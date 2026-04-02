#!/usr/bin/env bash
set -euo pipefail

# ── 确保 PATH 包含 Homebrew（macOS 子进程可能缺少） ──
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:${PATH}"

# ── 参数解析 ──
VERBOSE=false
MAX_WORKERS=5
SINGLE_TASK=""  # 内部模式：只处理指定任务
CLI_MODEL=""    # --model 覆盖：优先级高于 workflow.yaml
for arg in "$@"; do
  case "$arg" in
    -v|--verbose) VERBOSE=true ;;
    --workers=*) MAX_WORKERS="${arg#*=}" ;;
    --workers) ;; # 下一个 arg 是值，见下方
    --single-task=*) SINGLE_TASK="${arg#*=}" ;;
    --single-task) ;; # 下一个 arg 是值
    --model=*) CLI_MODEL="${arg#*=}" ;;
    --model) ;; # 下一个 arg 是值
    *)
      # 处理 --workers N / --single-task N / --model X（值在下一个 arg）
      case "${prev_arg:-}" in
        --workers) MAX_WORKERS="$arg" ;;
        --single-task) SINGLE_TASK="$arg" ;;
        --model) CLI_MODEL="$arg" ;;
      esac
      ;;
  esac
  prev_arg="$arg"
done

# ── 配置 ──
NIUMA_DIR="$(cd "$(dirname "$0")" && pwd)"          # openniuma 代码目录绝对路径
MAIN_REPO_DIR="$(dirname "$NIUMA_DIR")"             # 主仓库绝对路径
RUNTIME_DIR="${MAIN_REPO_DIR}/.openniuma-runtime"    # 运行时数据目录
STATE_FILE="${RUNTIME_DIR}/state.json"                 # 全部自包含在 openniuma/ 下
INBOX_DIR="${RUNTIME_DIR}/inbox"
TASKS_DIR="${RUNTIME_DIR}/tasks"
BACKLOG_FILE="${RUNTIME_DIR}/backlog.md"
LOG_DIR="${RUNTIME_DIR}/logs"
STATS_FILE="${RUNTIME_DIR}/stats.json"
WORKTREE_PREFIX="loop"                              # worktree 目录前缀
WORKTREE_BASE_DIR="${MAIN_REPO_DIR}/.trees"         # worktree 父目录
INBOX_POLL_INTERVAL=60
MAX_CONSECUTIVE_FAILURES=3
WORKERS_DIR="${RUNTIME_DIR}/workers"

# ── 辅助函数 ──
timestamp() { date "+%Y-%m-%d %H:%M:%S"; }

log() { echo "[$(timestamp)] $*"; }

read_phase() {
  if [ ! -f "$STATE_FILE" ]; then
    echo "INIT"
  else
    local val
    val=$(python3 "$NIUMA_DIR/lib/state.py" get-field "$STATE_FILE" current_phase)
    echo "${val:-INIT}"
  fi
}

read_field() {
  python3 "$NIUMA_DIR/lib/state.py" get-field "$STATE_FILE" "$1"
}

# 获取当前任务的 slug（用于 worktree 目录名）
read_slug() {
  python3 -c "
import json, re
with open('$STATE_FILE') as f:
    state = json.load(f)
item_id = state.get('current_item_id')
if item_id:
    for q in state.get('queue', []):
        if q['id'] == item_id:
            # 从 desc_path 提取 slug，或从 name 生成
            dp = q.get('desc_path', '')
            m = re.search(r'\d+-(.+?)_\d{2}-\d{2}_', dp)
            if m:
                print(m.group(1))
            else:
                slug = re.sub(r'[^\w-]', '-', q.get('name', str(item_id)).lower())[:40]
                print(slug)
            break
" 2>/dev/null
}

# 判断 phase 是否需要 worktree
needs_worktree() {
  case "$1" in
    FAST_TRACK|DESIGN_IMPLEMENT|DESIGN|IMPLEMENT|VERIFY|FIX|MERGE|MERGE_FIX|FINALIZE) return 0 ;;
    *) return 1 ;;
  esac
}

# 安全执行 hook：写入临时文件执行，替代 bash -c "$hook"
execute_hook() {
  local hook_name="$1" hook_content="$2" timeout_sec="${3:-120}"
  [ -z "$hook_content" ] && return 0

  local hook_file _tmpdir
  _tmpdir="${TMPDIR:-/tmp}"; _tmpdir="${_tmpdir%/}"
  hook_file=$(mktemp "${_tmpdir}/openniuma-hook-XXXXXX")
  printf '%s\n' "$hook_content" > "$hook_file"
  chmod +x "$hook_file"

  local result=0
  if command -v timeout >/dev/null 2>&1; then
    timeout "$timeout_sec" bash "$hook_file" || result=$?
  else
    # macOS 没有 timeout，用 Python 超时
    python3 -c "
import subprocess, sys
try:
    subprocess.run(['bash', '$hook_file'], timeout=$timeout_sec, check=True)
except subprocess.TimeoutExpired:
    print('Hook 超时 (${timeout_sec}s)', file=sys.stderr)
    sys.exit(124)
except subprocess.CalledProcessError as e:
    sys.exit(e.returncode)
" || result=$?
  fi

  rm -f "$hook_file"
  return $result
}

# 通知发送封装（通过 notify.py 的 NotifyManager）
send_notify() {
  local level="$1" title="$2" body="${3:-}"
  python3 - "$NIUMA_DIR" "$level" "$title" "$body" <<'PYEOF' 2>/dev/null || true
import sys, os
niuma_dir, level, title, body = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
sys.path.insert(0, niuma_dir)
from lib.notify import NotifyManager
try:
    import yaml
    with open(os.path.join(niuma_dir, 'workflow.yaml')) as f:
        config = yaml.safe_load(f) or {}
except Exception:
    config = {}
mgr = NotifyManager(config)
mgr.send(level, title, body)
PYEOF
}

# 安全删除 worktree：路径必须在 WORKTREE_BASE_DIR 下
safe_remove_worktree() {
  local wt_path="$1"
  [ -d "$wt_path" ] || return 0

  # 路径安全校验：必须在 WORKTREE_BASE_DIR 下
  local real_base real_wt
  real_base=$(cd "$WORKTREE_BASE_DIR" 2>/dev/null && pwd -P || echo "$WORKTREE_BASE_DIR")
  real_wt=$(cd "$wt_path" 2>/dev/null && pwd -P || echo "$wt_path")

  if [[ "$real_wt" != "${real_base}/"* ]]; then
    log "安全检查失败：$wt_path 不在 $WORKTREE_BASE_DIR 下，拒绝删除"
    return 1
  fi

  git -C "$MAIN_REPO_DIR" worktree remove "$wt_path" --force 2>/dev/null || {
    rm -rf "$wt_path"
    git -C "$MAIN_REPO_DIR" worktree prune
  }
}

# 创建 worktree + 执行 after_create hook 初始化环境
# 输出 worktree 绝对路径到 stdout
ensure_worktree() {
  local slug="$1" dev_branch="$2"
  local wt_path="${WORKTREE_BASE_DIR}/${WORKTREE_PREFIX}-${slug}"

  if [ -d "$wt_path" ]; then
    # 复用已有 worktree，但校验关键环境文件
    # 如果 .env 缺失或 DATABASE_URL 指向主库，重新执行 after_create hook 修复
    if [ -f "$wt_path/backend/.env.example" ] && \
       { [ ! -f "$wt_path/backend/.env" ] || grep -q "poi_dev$" "$wt_path/backend/.env" 2>/dev/null; }; then
      log "⚠️ Worktree .env 缺失或指向主库，重新执行 after_create hook" >&2
      local hook
      hook=$(python3 "$NIUMA_DIR/lib/config.py" get-hook after_create "$NIUMA_DIR/workflow.yaml" 2>/dev/null || true)
      if [ -n "$hook" ]; then
        ( cd "$wt_path" && SLUG="$slug" MAIN_REPO="$MAIN_REPO_DIR" \
          execute_hook "after_create" "$hook" "${CONF_HOOK_TIMEOUT:-120}" ) >&2 2>&2 || true
      fi
    fi
    echo "$wt_path"
    return 0
  fi

  # ⚠️ 本函数被 $() 调用，只有最后的 echo "$wt_path" 可以输出到 stdout
  # 其余所有输出必须走 fd3→stderr，否则会污染返回值
  exec 3>&2  # fd3 指向 stderr

  mkdir -p "$WORKTREE_BASE_DIR"

  log "🔧 创建 Worktree: $wt_path (基于 $dev_branch)" >&3

  # 确保 dev 分支本地存在并更新
  git -C "$MAIN_REPO_DIR" fetch origin "$dev_branch" >/dev/null 2>&1 || true
  if ! git -C "$MAIN_REPO_DIR" rev-parse --verify "$dev_branch" >/dev/null 2>&1; then
    git -C "$MAIN_REPO_DIR" branch "$dev_branch" "origin/$dev_branch" >/dev/null 2>&1 || true
  fi

  # 创建 worktree（并发重试 3 次，防止 git lock 竞争）
  local wt_created=false
  for attempt in 1 2 3; do
    if git -C "$MAIN_REPO_DIR" worktree add --detach "$wt_path" "$dev_branch" >/dev/null 2>&1; then
      wt_created=true; break
    fi
    [ -d "$wt_path/.git" ] || [ -f "$wt_path/.git" ] && { wt_created=true; break; }
    log "⚠️ git worktree add 失败，第 ${attempt}/3 次重试..." >&3
    sleep "$((attempt * 2))"
  done
  if [ "$wt_created" = false ]; then
    log "❌ git worktree add 3 次重试均失败" >&3
    exec 3>&-
    return 1
  fi

  # 执行 after_create hook（替代硬编码的项目初始化）
  local hook
  hook=$(python3 "$NIUMA_DIR/lib/config.py" get-hook after_create "$NIUMA_DIR/workflow.yaml" 2>/dev/null || true)
  if [ -n "$hook" ]; then
    log "🔧 执行 after_create hook..." >&3
    ( cd "$wt_path" && SLUG="$slug" MAIN_REPO="$MAIN_REPO_DIR" \
      execute_hook "after_create" "$hook" "${CONF_HOOK_TIMEOUT:-120}" ) >&3 2>&3 || {
      log "❌ after_create hook 失败" >&3
      safe_remove_worktree "$wt_path"
      exec 3>&-
      return 1
    }
  fi

  exec 3>&-  # 关闭 fd3
  echo "$wt_path"
}

# 删除 worktree + 执行 before_remove hook 清理资源
cleanup_worktree() {
  local slug="$1"
  local wt_path="${WORKTREE_BASE_DIR}/${WORKTREE_PREFIX}-${slug}"

  [ -d "$wt_path" ] || return 0

  log "🧹 清理 Worktree: $wt_path"

  # 执行 before_remove hook（替代硬编码的资源清理）
  local hook
  hook=$(python3 "$NIUMA_DIR/lib/config.py" get-hook before_remove "$NIUMA_DIR/workflow.yaml" 2>/dev/null || true)
  if [ -n "$hook" ]; then
    ( cd "$wt_path" && SLUG="$slug" MAIN_REPO="$MAIN_REPO_DIR" \
      execute_hook "before_remove" "$hook" 30 ) 2>/dev/null || true
  fi

  safe_remove_worktree "$wt_path"
}

# 启动时清理残留的 loop worktree（上次异常退出后的残留）
cleanup_stale_worktrees() {
  # 只收集有活跃进程的 worker 的 worktree slug（已死/已完成的 worker 不保护）
  local active_slugs=""
  for wstate in "${WORKERS_DIR}"/*/state.json; do
    [ -f "$wstate" ] || continue
    local wdir; wdir="$(dirname "$wstate")"
    local pidfile="${wdir}/pid"
    [ -f "$pidfile" ] || continue
    local wpid; wpid=$(cat "$pidfile" 2>/dev/null)
    kill -0 "$wpid" 2>/dev/null || continue
    local slug
    # Bug fix: 使用 current_item_id + desc_path 提取 slug，与 read_slug() 逻辑一致
    # 原来用 queue[0].name 会取到已完成任务的名称，导致当前活跃 worktree 不在保护名单里被误删
    slug=$(python3 -c "
import json, re, sys
try:
    s = json.load(open('$wstate'))
    item_id = s.get('current_item_id')
    if item_id:
        for q in s.get('queue', []):
            if q['id'] == item_id:
                dp = q.get('desc_path', '')
                m = re.search(r'\d+-(.+?)_\d{2}-\d{2}_', dp)
                if m:
                    print(m.group(1))
                else:
                    slug = re.sub(r'[^\w-]', '-', q.get('name', str(item_id)).lower())[:40]
                    print(slug)
                break
except: pass
" 2>/dev/null)
    [ -n "$slug" ] && active_slugs="$active_slugs $slug"
  done

  for wt in "${WORKTREE_BASE_DIR}/${WORKTREE_PREFIX}"-*; do
    [ -d "$wt" ] || continue
    local wt_slug="${wt##*${WORKTREE_PREFIX}-}"
    # 跳过所有活跃 worker 正在使用的 worktree
    local in_use=false
    for aslug in $active_slugs; do
      if [ "$wt_slug" = "$aslug" ]; then
        in_use=true
        break
      fi
    done
    if [ "$in_use" = false ]; then
      log "🧹 清理残留 worktree: $wt"
      cleanup_worktree "$wt_slug"
    fi
  done
}

# 终止 worker 进程：PGID kill + waitpid 确认
kill_worker() {
  local task_id="$1"
  local pid_file="$WORKERS_DIR/${task_id}/pid"
  [ -f "$pid_file" ] || return 0
  local pid
  pid=$(cat "$pid_file")

  # 获取进程组 ID，杀整个组
  local pgid
  pgid=$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ')
  if [ -n "$pgid" ] && [ "$pgid" != "0" ]; then
    kill -- "-${pgid}" 2>/dev/null || true
  else
    kill "$pid" 2>/dev/null || true
  fi

  # waitpid 确认退出（最多等 5 秒）
  local waited=0
  while kill -0 "$pid" 2>/dev/null && [ "$waited" -lt 5 ]; do
    sleep 1
    waited=$((waited + 1))
  done

  # 强制 SIGKILL
  if kill -0 "$pid" 2>/dev/null; then
    if [ -n "$pgid" ] && [ "$pgid" != "0" ]; then
      kill -9 -- "-${pgid}" 2>/dev/null || true
    else
      kill -9 "$pid" 2>/dev/null || true
    fi
  fi

  rm -f "$pid_file"
}

# 从 state.json 全量重新生成 backlog.md
refresh_backlog() {
  [ -f "$STATE_FILE" ] || return
  python3 <<PYEOF
import json, re, os
from pathlib import Path

state_file = "${STATE_FILE}"
backlog_file = "${BACKLOG_FILE}"

def read_body(filepath):
    with open(filepath) as f:
        content = f.read()
    match = re.match(r"^---\s*\n.*?\n---\s*\n", content, re.DOTALL)
    if match:
        return content[match.end():].strip()
    return content.strip()

with open(state_file) as f:
    state = json.load(f)

sections = {"done": [], "in-progress": [], "pending": [], "blocked": []}
for item in state.get("queue", []):
    status = item.get("status", "pending")
    if status == "done":
        sections["done"].append(item)
    elif status == "in-progress":
        sections["in-progress"].append(item)
    elif status == "blocked":
        sections["blocked"].append(item)
    else:
        sections["pending"].append(item)

lines = [
    "# Product Backlog",
    "",
    "> 自动生成，请勿手工编辑。任务通过 inbox/ 目录添加。",
    "",
]

section_titles = [
    ("in-progress", "进行中"),
    ("pending", "待开发"),
    ("done", "已完成"),
    ("blocked", "已阻塞"),
]

for key, title in section_titles:
    items = sections[key]
    lines.append(f"## {title}")
    lines.append("")
    if not items:
        lines.append("（无）")
        lines.append("")
        continue
    for item in items:
        complexity = item.get("complexity", "中")
        desc_path = item.get("desc_path")
        lines.append(f"### #{item['id']} {item['name']} [{complexity}]")
        meta_parts = []
        created = item.get("created_at")
        completed = item.get("completed_at")
        spec = item.get("spec_path")
        if created:
            short_created = created[5:] if len(created) > 5 else created
            time_str = f"📅 {short_created}"
            if completed:
                short_completed = completed[5:] if len(completed) > 5 else completed
                time_str += f" → {short_completed}"
            meta_parts.append(time_str)
        if spec:
            meta_parts.append(f"📄 [spec]({spec})")
        if meta_parts:
            lines.append(f"> {' | '.join(meta_parts)}")
        if desc_path and os.path.exists(desc_path):
            body = read_body(desc_path)
            if body:
                for bline in body.split("\n"):
                    lines.append(bline)
        lines.append("")

with open(backlog_file, "w") as f:
    f.write("\n".join(lines))
PYEOF
}

# 处理 inbox 目录中的新任务文件
# 返回 "STOP" 表示检测到停止信号，调用方应 break
process_inbox() {
  # 0. 检查 STOP 哨兵
  if [ -f "$INBOX_DIR/STOP" ]; then
    rm "$INBOX_DIR/STOP"
    echo "STOP"
    return
  fi

  # 检查是否有待处理文件
  local inbox_files
  inbox_files=$(find "$INBOX_DIR" -maxdepth 1 -type f -name "*.md" 2>/dev/null | sort)
  if [ -z "$inbox_files" ]; then
    return
  fi

  log "📥 发现 inbox 中有新任务，开始处理..."

  python3 <<PYEOF
import json, re, os, sys, shutil
from pathlib import Path
from datetime import datetime, timezone

inbox_dir = "${INBOX_DIR}"
tasks_dir = "${TASKS_DIR}"
state_file = "${STATE_FILE}"

# ── 辅助函数 ──
def parse_frontmatter(filepath):
    with open(filepath) as f:
        content = f.read()
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    if not match:
        return {"name": Path(filepath).stem.replace("-", " ")}
    meta = {}
    for line in match.group(1).split("\n"):
        if ":" in line:
            key, val = line.split(":", 1)
            key, val = key.strip(), val.strip()
            if key == "depends_on":
                try:
                    meta[key] = json.loads(val) if val else []
                except json.JSONDecodeError:
                    meta[key] = []
            else:
                meta[key] = val
    return meta

def inject_created_at(filepath, created_at_str):
    """在文件的 frontmatter 中注入 created_at 字段（如果不存在）"""
    with open(filepath) as f:
        content = f.read()
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    if match:
        fm_text = match.group(1)
        if "created_at:" in fm_text:
            return  # 已有，跳过
        new_fm = fm_text + f"\ncreated_at: \"{created_at_str}\""
        content = f"---\n{new_fm}\n---\n" + content[match.end():]
    else:
        content = f"---\ncreated_at: \"{created_at_str}\"\n---\n\n" + content
    with open(filepath, "w") as f:
        f.write(content)

# ── 主逻辑 ──

# 扫描 inbox/*.md，按修改时间排序（先进先出）
files = sorted(
    [f for f in Path(inbox_dir).glob("*.md")],
    key=lambda f: f.stat().st_mtime
)
if not files:
    sys.exit(0)

# 加载 state
if os.path.exists(state_file):
    with open(state_file) as f:
        state = json.load(f)
else:
    state = {"queue": []}

# 计算 max_id：同时扫描 queue 和 tasks/ 目录（防止中断后 ID 冲突）
max_id = 0
for item in state.get("queue", []):
    max_id = max(max_id, item.get("id", 0))
for f in Path(tasks_dir).glob("*.md"):
    m = re.match(r"(\d+)-", f.name)
    if m:
        max_id = max(max_id, int(m.group(1)))

now = datetime.now()
now_stamp = now.strftime("%m-%d_%H-%M")       # 文件名后缀: 03-23_11-56
now_display = now.strftime("%Y-%m-%d %H:%M")   # frontmatter: 2026-03-23 11:56

added = []
for f in files:
    meta = parse_frontmatter(str(f))
    slug = f.stem  # invite-bug
    max_id += 1

    task_path = f"{tasks_dir}/{max_id}-{slug}_{now_stamp}.md"

    state.setdefault("queue", []).append({
        "id": max_id,
        "name": meta.get("name", slug),
        "status": "pending",
        "depends_on": meta.get("depends_on", []),
        "complexity": meta.get("complexity", "中"),
        "created_at": now_display,
        "completed_at": None,
        "spec_path": None,
        "desc_path": task_path,
    })
    added.append((str(f), task_path, max_id, meta.get("name", slug)))

# 先持久化 state（确保 queue 写入成功）
state["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
with open(state_file, "w") as f:
    json.dump(state, f, indent=2, ensure_ascii=False)
    f.write("\n")

# 注入 created_at 到 frontmatter，再移动文件到 tasks/
os.makedirs(tasks_dir, exist_ok=True)
for src, dst, item_id, name in added:
    inject_created_at(src, now_display)
    shutil.move(src, dst)
    print(f"  ✅ #{item_id}: {name} → {dst}")

PYEOF

  # 全量生成 backlog.md（复用独立函数）
  refresh_backlog
  log "📝 backlog.md 已重新生成"

  # commit tasks/ 和 backlog.md 的变更
  if [ -n "$(git -C "$MAIN_REPO_DIR" status --porcelain .openniuma-runtime/tasks/ .openniuma-runtime/backlog.md 2>/dev/null)" ]; then
    git -C "$MAIN_REPO_DIR" add .openniuma-runtime/tasks/ .openniuma-runtime/backlog.md
    git -C "$MAIN_REPO_DIR" commit -m "docs: inbox 新任务入队"
    log "📥 tasks/ + backlog.md 已 commit"
  fi
  log "📥 inbox 处理完成"
}

# ── Prompt 映射 ──
build_prompt() {
  local phase="$1"

  # phase 名到文件名的映射（大写转小写+连字符）
  local prompt_file
  case "$phase" in
    FAST_TRACK)       prompt_file="fast-track.md" ;;
    DESIGN_IMPLEMENT) prompt_file="design-implement.md" ;;
    DESIGN)           prompt_file="design.md" ;;
    IMPLEMENT)        prompt_file="implement.md" ;;
    VERIFY)           prompt_file="verify.md" ;;
    FIX)              prompt_file="fix.md" ;;
    MERGE)            prompt_file="merge.md" ;;
    MERGE_FIX)        prompt_file="merge-fix.md" ;;
    FINALIZE)         prompt_file="finalize.md" ;;
    CI_FIX)           prompt_file="ci-fix.md" ;;
    INIT)
      # INIT 保持内联（无对应模板文件）
      cat <<PROMPT
# INIT：初始化 dev 集成分支

⚠️ 你在主仓库目录执行。**禁止 git checkout / git merge**，只允许 fetch/branch/push。
使用以下命令创建 dev 分支（不影响当前工作目录）：
\`\`\`bash
git fetch origin master
git branch dev/backlog-batch-{date} origin/master  # 如已存在则跳过
git push -u origin dev/backlog-batch-{date}
\`\`\`
更新 state.json: dev_branch 字段。
然后将 current_phase 更新为第一个 pending 任务对应的路径（FAST_TRACK / DESIGN_IMPLEMENT / DESIGN）。
全程自主工作，不要问我问题。
PROMPT
      return
      ;;
    *)
      log "未知 phase: $phase"
      return 1
      ;;
  esac

  local full_path="$NIUMA_DIR/prompts/$prompt_file"
  if [ ! -f "$full_path" ]; then
    log "Prompt 文件不存在: $full_path"
    return 1
  fi

  # 渲染模板变量（config-time 变量由 config.py 处理，{task_id} 在此注入）
  python3 "$NIUMA_DIR/lib/config.py" render-prompt "$full_path" "$NIUMA_DIR/workflow.yaml" | \
    sed "s|{task_id}|${SINGLE_TASK:-?}|g"
}

# ── 自检：确保 state.json 存在 ──
ensure_state_file() {
  [ -f "$STATE_FILE" ] && return 0

  log "⚠️ state.json 不存在，从 tasks/ 目录重建..."

  python3 <<PYEOF
import json, re, os
from pathlib import Path
from datetime import datetime, timezone

tasks_dir = "${TASKS_DIR}"
state_file = "${STATE_FILE}"
main_repo = "${MAIN_REPO_DIR}"

# 扫描 tasks/ 目录中的 .md 文件（忽略 .gitkeep）
task_files = sorted(
    [f for f in Path(tasks_dir).glob("*.md")],
    key=lambda f: f.name
)

if not task_files:
    print("  ⚠️ tasks/ 目录为空，生成空白 state")

queue = []
completed = []

for f in task_files:
    # 从文件名提取 ID: "55-55-refresh-heatmap-on-space-switch_03-26_09-53.md" → 55
    m = re.match(r"(\d+)-(.+?)(?:_(\d{2}-\d{2}_\d{2}-\d{2}))?\.md$", f.name)
    if not m:
        continue
    item_id = int(m.group(1))
    slug = m.group(2)

    # 解析 frontmatter
    content = f.read_text()
    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    meta = {}
    if fm_match:
        for line in fm_match.group(1).split("\n"):
            if ":" in line:
                key, val = line.split(":", 1)
                meta[key.strip()] = val.strip().strip('"')

    created_at = meta.get("created_at", "")
    status = meta.get("status", "open")
    complexity = meta.get("complexity", "中")
    name = meta.get("name", slug.replace("-", " "))

    # 推断完成状态：frontmatter 中 status=done/closed 视为已完成
    is_done = status in ("done", "closed", "completed")

    item = {
        "id": item_id,
        "name": name,
        "status": "done" if is_done else "pending",
        "depends_on": [],
        "complexity": complexity,
        "created_at": created_at,
        "completed_at": None,
        "spec_path": None,
        "desc_path": f"{tasks_dir}/{f.name}",
    }
    queue.append(item)
    if is_done:
        completed.append({"id": item_id, "name": name})

# 确定 dev_branch：使用当天日期
today = datetime.now().strftime("%Y-%m-%d")
dev_branch = f"dev/backlog-batch-{today}"

# 找第一个 pending 任务作为 current_item_id
first_pending = next((q for q in queue if q["status"] == "pending"), None)

state = {
    "dev_branch": dev_branch,
    "current_item_id": first_pending["id"] if first_pending else None,
    "current_phase": "DESIGN_IMPLEMENT" if first_pending else "AWAITING_HUMAN_REVIEW",
    "pr_number": None,
    "branch": None,
    "spec_path": None,
    "plan_path": None,
    "fix_list_path": None,
    "lock": {"session_id": None, "acquired_at": None},
    "stashed": False,
    "implement_progress": {
        "current_chunk": 0,
        "current_task": 0,
        "last_committed_task": None,
        "last_commit_sha": None,
        "current_step_attempts": 0,
    },
    "verify_attempts": 0,
    "merge_fix_attempts": 0,
    "completed": completed,
    "blocked": [],
    "queue": queue,
    "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
}

os.makedirs(os.path.dirname(state_file), exist_ok=True)
with open(state_file, "w") as f:
    json.dump(state, f, indent=2, ensure_ascii=False)
    f.write("\n")

pending_count = sum(1 for q in queue if q["status"] == "pending")
print(f"  ✅ 重建完成: {len(queue)} 个任务 ({pending_count} pending, {len(completed)} done)")
if first_pending:
    print(f"  ▶️ 下一个任务: #{first_pending['id']} {first_pending['name']}")
PYEOF

  if [ $? -ne 0 ]; then
    log "❌ 重建 state.json 失败"
    return 1
  fi

  log "✅ state.json 已重建"
}

ensure_state_file

# ── 并行模式：Worker 管理 ──
# 注意：锁由 state.py (JsonFileStore) 的 fcntl 实现，不再需要 mkdir 锁

# 查找所有可执行任务 ID（pending + 依赖已满足 + 未被其他 worker 占用）
find_ready_task_ids() {
  python3 "$NIUMA_DIR/lib/state.py" find-ready-ids "$STATE_FILE"
}

# 原子认领一个任务：将其 status 改为 in_progress，返回 "claimed" 或 "failed"
claim_task() {
  local target_id="$1"
  local result
  result=$(python3 "$NIUMA_DIR/lib/state.py" claim "$STATE_FILE" "$target_id")
  # state.py claim 输出 "claimed" 或 "failed"，转换为旧接口的 "ok"
  if [ "$result" = "claimed" ]; then
    echo "ok"
  fi
}

# 为 worker 创建独立 state 文件（从主 state 提取单个任务）
# Bug fix: 如果 worker state 已存在且 current_item_id 匹配，保留已有进度（不覆盖）
create_worker_state() {
  local task_id=$1
  local worker_dir="${WORKERS_DIR}/${task_id}"
  local worker_state="${worker_dir}/state.json"
  mkdir -p "$worker_dir"

  # 如果 worker state 已存在，直接保留（保持已有进度）
  # 目录名即 task_id（workers/8/），无需再做 ID 匹配
  # Bug fix: 之前做 ID 匹配时，Claude 把 current_item_id 写成 null 导致匹配失败 → 重建 → 进度丢失
  if [ -f "$worker_state" ] && [ -s "$worker_state" ]; then
    # Bug fix: log 必须写 stderr（>&2），否则会被 $() 捕获污染 STATE_FILE
    log "Worker #${task_id} state 已存在（phase: $(python3 -c "import json; print(json.load(open('$worker_state')).get('current_phase','?'))" 2>/dev/null)），跳过重建" >&2
    echo "$worker_state"
    return
  fi

  python3 -c "
import json
from datetime import datetime, timezone
with open('$STATE_FILE') as f:
    state = json.load(f)
task_info = None
for q in state.get('queue', []):
    if q['id'] == $task_id:
        task_info = dict(q)
        break
if not task_info:
    raise SystemExit('task not found')
worker = {
    'dev_branch': state.get('dev_branch', ''),
    'current_item_id': $task_id,
    'current_phase': 'DESIGN_IMPLEMENT',
    'pr_number': None,
    'branch': None,
    'spec_path': None,
    'plan_path': None,
    'fix_list_path': None,
    'lock': {'session_id': None, 'acquired_at': None},
    'stashed': False,
    'implement_progress': {
        'current_chunk': 0, 'current_task': 0,
        'last_committed_task': None, 'last_commit_sha': None,
        'current_step_attempts': 0,
    },
    'verify_attempts': 0,
    'merge_fix_attempts': 0,
    'completed': list(state.get('completed', [])),
    'blocked': list(state.get('blocked', [])),
    'queue': [task_info],
    'updated_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z'),
    'worktree_path': None,
    'main_repo_path': '${MAIN_REPO_DIR}',
}
out = '${worker_dir}/state.json'
with open(out, 'w') as f:
    json.dump(worker, f, indent=2, ensure_ascii=False)
    f.write('\n')
"
  echo "${worker_dir}/state.json"
}

# 标记任务文件为已完成（更新 frontmatter + 追加完成总结）
mark_task_done() {
  local task_id=$1
  local worker_state="${2:-}"  # 可选：worker state 文件路径

  python3 <<PYEOF
import json, re, os, subprocess
from datetime import datetime, timezone

state_file = "${STATE_FILE}"
worker_state_path = "${worker_state}"
task_id = $task_id
main_repo = "${MAIN_REPO_DIR}"
trees_dir = "${WORKTREE_BASE_DIR}"

# 从主 state 或 worker state 获取任务信息
task_info = None
spec_path = None
branch = None
for sf in [worker_state_path, state_file]:
    if not sf or not os.path.exists(sf):
        continue
    with open(sf) as f:
        s = json.load(f)
    spec_path = spec_path or s.get('spec_path')
    branch = branch or s.get('branch')
    for q in s.get('queue', []):
        if q['id'] == task_id:
            task_info = q
            break
    if task_info:
        break

if not task_info:
    print(f"⚠️ 找不到任务 #{task_id}")
    raise SystemExit(0)

desc_path = task_info.get('desc_path', '')
if not desc_path:
    raise SystemExit(0)

# desc_path 可能是相对路径
abs_desc = os.path.join(main_repo, desc_path) if not os.path.isabs(desc_path) else desc_path
if not os.path.exists(abs_desc):
    print(f"⚠️ 任务文件不存在: {abs_desc}")
    raise SystemExit(0)

with open(abs_desc) as f:
    content = f.read()

now = datetime.now(timezone.utc)
now_iso = now.strftime('%Y-%m-%dT%H:%M:%S.000Z')
now_display = now.strftime('%Y-%m-%d %H:%M')
created_at = task_info.get('created_at', '未知')

# 1. 更新 frontmatter
fm_match = re.match(r'^---\s*\n(.*?)\n---\s*\n', content, re.DOTALL)
if fm_match:
    fm_text = fm_match.group(1)
    # 更新 status
    if 'status:' in fm_text:
        fm_text = re.sub(r'status:\s*\S+', 'status: done', fm_text)
    else:
        fm_text += '\nstatus: done'
    # 添加 completed_at
    if 'completed_at:' not in fm_text:
        fm_text += f'\ncompleted_at: "{now_display}"'
    body = content[fm_match.end():]
    content = f"---\n{fm_text}\n---\n{body}"
else:
    content = f"---\nstatus: done\ncompleted_at: \"{now_display}\"\n---\n\n{content}"

# 2. 标题加 [Done]
content = re.sub(r'^(# .+?)(\s*\n)', r'\1 [Done]\2', content, count=1, flags=re.MULTILINE)

# 3. 收集完成总结信息
summary_lines = [
    "",
    "---",
    "",
    "## 完成总结",
    "",
    f"- **开始时间**: {created_at}",
    f"- **完成时间**: {now_display} (UTC)",
]

if spec_path:
    summary_lines.append(f"- **设计文档**: [{os.path.basename(spec_path)}]({spec_path})")

if branch:
    summary_lines.append(f"- **功能分支**: `{branch}`")
    # 从 git log 获取 commit 摘要（尝试在 worktree 或主仓库中查找）
    try:
        dev_branch = task_info.get('dev_branch', '')
        if not dev_branch:
            with open(state_file) as f:
                dev_branch = json.load(f).get('dev_branch', '')
        if dev_branch:
            # 尝试多个可能的仓库路径
            for repo in [main_repo] + [os.path.join(trees_dir, d) for d in os.listdir(trees_dir) if os.path.isdir(os.path.join(trees_dir, d))]:
                try:
                    result = subprocess.run(
                        ['git', '-C', repo, 'log', f'{dev_branch}..{branch}', '--oneline', '--no-decorate'],
                        capture_output=True, text=True, timeout=5
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        commits = result.stdout.strip().split('\n')
                        summary_lines.append(f"- **提交数**: {len(commits)}")
                        summary_lines.append("")
                        summary_lines.append("### Commits")
                        summary_lines.append("")
                        for c in commits[:20]:  # 最多显示 20 条
                            summary_lines.append(f"- {c}")
                        break
                except Exception:
                    continue
    except Exception:
        pass

summary_lines.append("")

# 追加总结到文件末尾
content = content.rstrip() + "\n" + "\n".join(summary_lines)

with open(abs_desc, 'w') as f:
    f.write(content)

print(f"✅ 任务 #{task_id} 文件已标记完成: {abs_desc}")
PYEOF
}

# Worker 完成后：读取 worker state，同步结果回主 state
# 注意：state.py 的 JsonFileStore 提供 fcntl 锁，无需外部加锁
sync_worker_result() {
  local task_id=$1 worker_exit=$2
  local worker_state="${WORKERS_DIR}/${task_id}/state.json"

  python3 -c "
import json, os, sys
sys.path.insert(0, '$NIUMA_DIR')
from lib.json_store import JsonFileStore
from datetime import datetime, timezone

state_file = '$STATE_FILE'
worker_state_path = '${worker_state}'
task_id = $task_id
worker_exit = $worker_exit

store = JsonFileStore(state_file)

def _sync(state):
    # 读 worker state 获取结果
    ws = {}
    if os.path.exists(worker_state_path):
        with open(worker_state_path) as wf:
            ws = json.load(wf)

    ws_phase = ws.get('current_phase', '')
    ws_queue = ws.get('queue', [])
    ws_task = ws_queue[0] if ws_queue else {}
    is_done = ws_task.get('status') == 'done' or ws_phase in ('AWAITING_HUMAN_REVIEW', 'FINALIZE')

    for q in state.get('queue', []):
        if q['id'] == task_id:
            # Bug fix: AWAITING_HUMAN_REVIEW 表示任务已完成，即使 worker 被 kill（exit!=0）也标记 done
            # 否则被杀的已完成任务会被重置为 pending → 重新 spawn → 无限循环
            if is_done:
                q['status'] = 'done'
                q['completed_at'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')
                q['spec_path'] = ws.get('spec_path') or ws_task.get('spec_path')
                completed_ids = {c['id'] for c in state.get('completed', [])}
                if task_id not in completed_ids:
                    state.setdefault('completed', []).append({
                        'id': task_id, 'name': q.get('name', '')
                    })
            elif worker_exit == 0:
                # worker 正常退出但任务未完成（不应发生，但保险起见保持 in_progress）
                pass
            else:
                q['status'] = 'pending'
            break

    # 传播 BLOCKED 状态
    blocked_ids = {b['id'] for b in state.get('blocked', [])}
    changed = True
    while changed:
        changed = False
        for q in state.get('queue', []):
            if q.get('status') == 'pending':
                for dep in q.get('depends_on', []):
                    if dep in blocked_ids:
                        q['status'] = 'blocked'
                        state.setdefault('blocked', []).append({
                            'id': q['id'], 'name': q.get('name', '')
                        })
                        blocked_ids.add(q['id'])
                        changed = True
                        break

    state['updated_at'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')
    return state

store.update(_sync)
"
}

# 并行调度器主函数（兼容 bash 3.x，用文件追踪 worker）
parallel_main() {
  mkdir -p "$LOG_DIR" "$WORKERS_DIR"

  log "🔀 并行模式启动：最大 ${MAX_WORKERS} 个 worker"
  log "主仓库: $MAIN_REPO_DIR"

  # 信号处理：Ctrl+C / kill 时自动终止所有 worker 子进程
  cleanup_all_workers() {
    log "🛑 收到终止信号，清理所有 worker..."
    for pidfile in "${WORKERS_DIR}"/*/pid; do
      [ -f "$pidfile" ] || continue
      local task_id
      task_id=$(basename "$(dirname "$pidfile")")
      kill_worker "$task_id"
    done
    wait 2>/dev/null
    log "清理完成，退出"
    exit 0
  }
  trap cleanup_all_workers INT TERM HUP

  # 清理上次残留的 PID 文件（旧进程 PID 会导致 wait 失败）
  # Bug fix: 只清理 pid/name 文件，保留 state.json（否则重启会丢失 VERIFY/MERGE 等进度）
  if [ -d "$WORKERS_DIR" ]; then
    log "🧹 清理残留 worker PID..."
    for _wd in "${WORKERS_DIR}"/*/; do
      [ -d "$_wd" ] || continue
      rm -f "${_wd}pid" "${_wd}name"
    done
  fi

  # 注意：并行模式不清理 worktree，让新 worker 直接复用已有的 worktree
  # （ensure_worktree 检测到目录存在会直接复用）

  while true; do
    # 从 workflow.yaml 加载配置到 shell 变量
    eval "$(python3 "$NIUMA_DIR/lib/config.py" export-env "$NIUMA_DIR/workflow.yaml")"

    # 每轮执行 reconciliation（stall 检测、取消信号、孤儿回收）
    reconcile_out=$(python3 "$NIUMA_DIR/lib/reconcile.py" run "$STATE_FILE" "$WORKERS_DIR" "$INBOX_DIR" "${CONF_STALL_TIMEOUT:-1800}" 2>/dev/null || true)

    # 处理取消信号：kill 对应 worker 进程
    while IFS= read -r _line; do
      if [[ "$_line" == CANCEL:* ]]; then
        _cancel_id="${_line#CANCEL:}"
        _task_name="$(cat "${WORKERS_DIR}/${_cancel_id}/name" 2>/dev/null || echo "#${_cancel_id}")"
        log "🚫 取消任务 #${_cancel_id}（${_task_name}）"
        kill_worker "$_cancel_id"
        rm -f "${WORKERS_DIR}/${_cancel_id}/pid" "${WORKERS_DIR}/${_cancel_id}/name"
        send_notify "info" "任务 #${_cancel_id} 已取消" "$_task_name"
      fi
    done <<< "$reconcile_out"

    # 处理 inbox
    result=$(process_inbox)
    if [ "$result" = "STOP" ]; then
      log "🛑 检测到 STOP 信号，终止所有 worker"
      for pidfile in "${WORKERS_DIR}"/*/pid; do
        [ -f "$pidfile" ] || continue
        local task_id
        task_id=$(basename "$(dirname "$pidfile")")
        kill_worker "$task_id"
      done
      wait 2>/dev/null
      break
    fi

    # 回收已完成的 worker
    for pidfile in "${WORKERS_DIR}"/*/pid; do
      [ -f "$pidfile" ] || continue
      pid=$(cat "$pidfile")
      task_id=$(basename "$(dirname "$pidfile")")
      if ! kill -0 "$pid" 2>/dev/null; then
        exit_code=0
        wait "$pid" 2>/dev/null || exit_code=$?
        task_name="$(cat "${WORKERS_DIR}/${task_id}/name" 2>/dev/null || echo "#${task_id}")"
        if [ "$exit_code" -eq 0 ]; then
          log "✅ Worker #${task_id}（${task_name}）完成"
          mark_task_done "$task_id" "${WORKERS_DIR}/${task_id}/state.json"
          send_notify "info" "任务 #${task_id} 完成" "$task_name"
        else
          log "⚠️ Worker #${task_id}（${task_name}）异常退出 (exit: $exit_code)"
        fi
        sync_worker_result "$task_id" "$exit_code"
        # 清理 worker worktree
        wt_slug=$(python3 -c "
import json, re
with open('${WORKERS_DIR}/${task_id}/state.json') as f:
    ws = json.load(f)
for q in ws.get('queue', []):
    dp = q.get('desc_path', '')
    m = re.search(r'\d+-(.+?)_\d{2}-\d{2}_', dp)
    if m: print(m.group(1)); break
" 2>/dev/null || true)
        [ -n "$wt_slug" ] && cleanup_worktree "$wt_slug"
        # Bug fix: 只在任务完成时删除整个 worker 目录；异常退出只清理 pid/name，保留 state.json
        if [ "$exit_code" -eq 0 ]; then
          rm -rf "${WORKERS_DIR}/${task_id}"
        else
          rm -f "${WORKERS_DIR}/${task_id}/pid" "${WORKERS_DIR}/${task_id}/name"
        fi
      fi
    done

    # 刷新 backlog
    refresh_backlog
    if [ -n "$(git -C "$MAIN_REPO_DIR" status --porcelain .openniuma-runtime/backlog.md 2>/dev/null)" ]; then
      git -C "$MAIN_REPO_DIR" add .openniuma-runtime/backlog.md
      git -C "$MAIN_REPO_DIR" commit -m "docs: 刷新 backlog.md" 2>/dev/null || true
    fi

    # 重置孤儿任务：in_progress 但没有活跃 worker 的任务重置为 pending
    active_task_ids=""
    for pidfile in "${WORKERS_DIR}"/*/pid; do
      [ -f "$pidfile" ] || continue
      active_task_ids="${active_task_ids} $(basename "$(dirname "$pidfile")")"
    done
    python3 -c "
import sys
sys.path.insert(0, '$NIUMA_DIR')
from lib.json_store import JsonFileStore

active = set('${active_task_ids}'.split())
store = JsonFileStore('$STATE_FILE')

def _reset_orphans(state):
    changed = False
    for q in state.get('queue', []):
        if q.get('status') == 'in_progress' and str(q['id']) not in active:
            q['status'] = 'pending'
            changed = True
    return state if changed else state

store.update(_reset_orphans)
" 2>/dev/null

    # 清理已完成任务的残留 worker 目录
    for wdir in "${WORKERS_DIR}"/*/; do
      [ -d "$wdir" ] || continue
      local wtid; wtid=$(basename "$wdir")
      [ -f "${wdir}pid" ] && kill -0 "$(cat "${wdir}pid" 2>/dev/null)" 2>/dev/null && continue
      local task_status; task_status=$(python3 -c "
import json
for q in json.load(open('$STATE_FILE')).get('queue', []):
    if q['id'] == $wtid: print(q.get('status', '')); break
" 2>/dev/null)
      case "$task_status" in done|done_in_dev|released|cancelled|dropped)
        rm -rf "$wdir"; log "🧹 清理已完成任务 #${wtid} 的 worker 目录" ;; esac
    done

    # 统计当前活跃 worker 数
    active_count=0
    active_list=""
    for pidfile in "${WORKERS_DIR}"/*/pid; do
      [ -f "$pidfile" ] || continue
      active_count=$((active_count + 1))
      active_list="${active_list} #$(basename "$(dirname "$pidfile")")"
    done

    # 有空闲 slot 时认领新任务
    ready_ids=$(find_ready_task_ids)
    for task_id in $ready_ids; do
      if [ "$active_count" -ge "$MAX_WORKERS" ]; then
        break
      fi
      # 跳过已在运行的
      [ -f "${WORKERS_DIR}/${task_id}/pid" ] && continue
      # 认领
      claimed=$(claim_task "$task_id")
      [ "$claimed" != "ok" ] && continue
      # 获取任务名
      task_name=$(python3 -c "
import json
with open('$STATE_FILE') as f:
    state = json.load(f)
for q in state.get('queue', []):
    if q['id'] == $task_id:
        print(q.get('name', '#$task_id'))
        break
" 2>/dev/null || echo "#${task_id}")

      log "🚀 启动 Worker #${task_id}（${task_name}）"
      mkdir -p "${WORKERS_DIR}/${task_id}"
      echo "$task_name" > "${WORKERS_DIR}/${task_id}/name"
      # 构建子进程参数，setsid 创建独立进程组使 kill 可覆盖整个子进程树
      # 使用 compat.py setsid 跨平台替代 Linux setsid 命令（macOS 无内置 setsid）
      worker_args="--single-task $task_id"
      [ "$VERBOSE" = true ] && worker_args="$worker_args --verbose"
      [ -n "$CLI_MODEL" ] && worker_args="$worker_args --model $CLI_MODEL"
      python3 "$NIUMA_DIR/lib/compat.py" setsid bash "$0" $worker_args >>"$LOG_DIR/worker-${task_id}.log" 2>&1 &
      echo "$!" > "${WORKERS_DIR}/${task_id}/pid"
      active_count=$((active_count + 1))
      active_list="${active_list} #${task_id}"
      log "  PID: $!, 日志: $LOG_DIR/worker-${task_id}.log"
    done

    # 状态汇总
    if [ "$active_count" -gt 0 ]; then
      log "📊 活跃 worker: ${active_count}/${MAX_WORKERS} [${active_list# }]"
      sleep 10
    else
      # 无活跃 worker，检查是否还有任务
      remaining=$(python3 -c "
import json
with open('$STATE_FILE') as f:
    state = json.load(f)
queue = state.get('queue', [])
total = len(queue)
completed_ids = {c['id'] for c in state.get('completed', [])}
pending = [q for q in queue if q.get('status') in ('pending', 'in_progress')]
waiting = sum(1 for q in pending if not all(d in completed_ids for d in q.get('depends_on', [])))
print('%d:%d:%d' % (total, len(pending), waiting))
" 2>/dev/null || echo "0:0:0")
      total_tasks="${remaining%%:*}"
      rest="${remaining#*:}"
      total_pending="${rest%%:*}"
      total_waiting="${rest##*:}"

      if [ "$total_pending" -eq 0 ]; then
        # 无待处理任务（空队列或全部完成）→ 等待新任务
        if [ "$total_tasks" -gt 0 ]; then
          log "🎉 当前任务全部完成，等待新任务... (${INBOX_POLL_INTERVAL}s)"
        else
          log "📭 暂无任务，等待 inbox... (${INBOX_POLL_INTERVAL}s)"
        fi
        sleep "$INBOX_POLL_INTERVAL"
      elif [ "$total_waiting" -eq "$total_pending" ]; then
        # 全部任务在等依赖 → 等待
        log "⏳ ${total_waiting} 个任务等待依赖解除... (${INBOX_POLL_INTERVAL}s)"
        sleep "$INBOX_POLL_INTERVAL"
      else
        sleep 2
      fi
    fi
  done

  log "并行调度器退出"
}

# ── 入口路由 ──

# --single-task 模式：使用独立 worker state 文件
if [ -n "$SINGLE_TASK" ]; then
  worker_state_file=$(create_worker_state "$SINGLE_TASK")
  STATE_FILE="$worker_state_file"
  log "Worker #${SINGLE_TASK} 启动，state: $STATE_FILE"
fi

# 并行模式（workers > 1 且非 single-task）直接进入调度器
if [ "$MAX_WORKERS" -gt 1 ] && [ -z "$SINGLE_TASK" ]; then
  parallel_main
  exit $?
fi

# ── 主循环（串行模式 或 --single-task 模式）──

# 信号处理：Ctrl+C / kill 时终止当前 claude 子进程
cleanup_on_exit() {
  log "🛑 收到终止信号，退出"
  # 分支守卫：退出前检查主仓库分支是否被污染
  local _exit_dev_branch _exit_current_branch
  _exit_dev_branch=$(python3 "$NIUMA_DIR/lib/state.py" get-field "$STATE_FILE" dev_branch 2>/dev/null || echo "")
  _exit_current_branch=$(git -C "$MAIN_REPO_DIR" branch --show-current 2>/dev/null || echo "")
  if [ -n "$_exit_dev_branch" ] && [ -n "$_exit_current_branch" ] && [ "$_exit_current_branch" != "$_exit_dev_branch" ]; then
    log "⚠️ [分支守卫] 主仓库分支被污染（$_exit_current_branch → $_exit_dev_branch），自动重置"
    git -C "$MAIN_REPO_DIR" checkout "$_exit_dev_branch" 2>/dev/null || log "❌ [分支守卫] 重置失败，请人工介入"
  fi
  kill 0 2>/dev/null
  exit 0
}
trap cleanup_on_exit INT TERM HUP

mkdir -p "$LOG_DIR"
consecutive_failures=0
retry_hint=""
current_wt_path=""   # 当前 worktree 路径（空 = 在主仓库执行）
current_wt_slug=""   # 当前 worktree 对应的 slug

log "🚀 自治研发循环启动（worktree 隔离模式）"
log "主仓库: $MAIN_REPO_DIR"

# 启动时清理残留 worktree（仅 orchestrator，单任务 worker 不清理避免误删其他 worker 的 worktree）
if [ -z "$SINGLE_TASK" ]; then
  cleanup_stale_worktrees
fi

while true; do
  # 从 workflow.yaml 加载配置到 shell 变量
  eval "$(python3 "$NIUMA_DIR/lib/config.py" export-env "$NIUMA_DIR/workflow.yaml")"

  # 每轮循环开始前：处理 inbox（single-task 模式跳过，由调度器处理）
  if [ -z "$SINGLE_TASK" ]; then
    result=$(process_inbox)
    if [ "$result" = "STOP" ]; then
      log "🛑 检测到 STOP 信号，停止循环"
      break
    fi
  fi

  phase=$(read_phase)
  log "当前 Phase: $phase"

  # 守卫：single-task worker 不应进入 INIT 或无效 phase（如 DONE）
  if [ -n "$SINGLE_TASK" ]; then
    case "$phase" in
      INIT|DONE|RELEASE_PREP|RELEASE)
        log "⚠️ Worker 遇到无效 phase '$phase'，标记任务完成并退出"
        sync_worker_result "$SINGLE_TASK" 0
        break
        ;;
    esac
  fi

  # Bug#1 fix: 每轮循环开始时重置 worktree 路径，避免 VERIFY 检查误用上一轮的旧路径
  current_wt_path=""
  current_wt_slug=""

  # --single-task 模式：任务完成时退出（FINALIZE 需先执行再退出）
  if [ -n "$SINGLE_TASK" ] && [ "$phase" = "AWAITING_HUMAN_REVIEW" ]; then
    log "Worker #${SINGLE_TASK} 任务完成 (phase: $phase)"
    break
  fi

  # 等待新任务或人工审核
  if [ "$phase" = "AWAITING_HUMAN_REVIEW" ]; then
    # process_inbox() 已在循环顶部执行，检查 queue 中是否有 pending 任务
    pending_count=$(python3 -c "
import json
with open('$STATE_FILE') as f:
    state = json.load(f)
print(sum(1 for q in state.get('queue', []) if q.get('status') == 'pending'))
" 2>/dev/null || echo "0")
    if [ "$pending_count" -gt 0 ]; then
      log "📥 发现 ${pending_count} 个待处理任务，启动新任务..."
      # 找到第一个 pending 任务，默认进入 DESIGN_IMPLEMENT（由 Claude 自主判定复杂度）
      python3 -c "
import sys
sys.path.insert(0, '$NIUMA_DIR')
from lib.json_store import JsonFileStore

store = JsonFileStore('$STATE_FILE')

def _pick_next(state):
    completed_ids = {c['id'] for c in state.get('completed', [])}
    for q in state.get('queue', []):
        if q.get('status') == 'pending':
            deps = q.get('depends_on', [])
            if all(d in completed_ids for d in deps):
                state['current_item_id'] = q['id']
                state['current_phase'] = 'DESIGN_IMPLEMENT'
                q['status'] = 'in_progress'
                break
    return state

store.update(_pick_next)
"
      continue
    fi
    log "⏳ 等待新任务... (${INBOX_POLL_INTERVAL}s)"
    sleep "$INBOX_POLL_INTERVAL"
    continue
  fi

  # ── 产出校验：VERIFY 前检查 feat 分支是否有新 commit ──
  # 注意：不在 MERGE 阶段检查，避免 MERGE_FIX 后 feat 已合并导致误回退
  if echo "VERIFY" | grep -qw "$phase"; then
    local_branch=$(read_field branch)
    local_dev_branch=$(read_field dev_branch)
    if [ -n "$local_branch" ] && [ -n "$local_dev_branch" ]; then
      # Bug fix: current_wt_path 在每轮循环开头被重置为空，此处从 state.json 的 worktree_path 字段
      # 读取实际 worktree 路径，避免误用主仓库路径导致跨 worktree 分支不可见
      _verify_wt=$(python3 -c "
import json, os, sys
try:
    d = json.load(open('$STATE_FILE'))
    p = d.get('worktree_path', '')
    print(p if p and os.path.isdir(p) else '')
except: print('')
" 2>/dev/null || echo "")
      git_dir="${current_wt_path:-${_verify_wt:-$MAIN_REPO_DIR}}"
      new_commits=$(git -C "$git_dir" log "${local_dev_branch}..${local_branch}" --oneline 2>/dev/null | wc -l | tr -d ' ')
      # feat 已合并进 dev 时 new_commits=0 属正常，跳过回退
      # Bug fix: 使用完整分支名匹配而非 ##*/ 截断，避免同名分支误匹配
      already_merged=$(git -C "$git_dir" branch --merged "${local_dev_branch}" 2>/dev/null | grep -qxF "  ${local_branch}" && echo "yes" || echo "no")
      if [ "$new_commits" -eq 0 ] && [ "$already_merged" != "yes" ]; then
        log "⚠️ feat 分支 ${local_branch} 没有新 commit（实现阶段可能失败），回退到 DESIGN_IMPLEMENT"
        python3 -c "
import sys
sys.path.insert(0, '$NIUMA_DIR')
from lib.json_store import JsonFileStore

store = JsonFileStore('$STATE_FILE')

def _rollback(state):
    state['current_phase'] = 'DESIGN_IMPLEMENT'
    state['branch'] = None
    state['spec_path'] = None
    state['plan_path'] = None
    state['implement_progress'] = {'current_chunk': 0, 'current_task': 0, 'last_committed_task': None, 'last_commit_sha': None, 'current_step_attempts': 0}
    return state

store.update(_rollback)
"
        continue
      fi
    fi
  fi

  prompt=$(build_prompt "$phase")
  if [ -z "$prompt" ]; then
    log "❌ 未知 Phase: ${phase}，停止循环"
    break
  fi

  # 如果是重试，在 prompt 前追加断点续传提示
  if [ -n "$retry_hint" ]; then
    prompt="${retry_hint}

${prompt}"
    log "📎 已追加断点续传提示"
    retry_hint=""
  fi

  # ── Worktree 处理 ──
  current_wt_path=""
  current_wt_slug=""
  if needs_worktree "$phase"; then
    current_wt_slug=$(read_slug)
    # FINALIZE 特殊处理：即使 current_item_id 为空，也必须在 worktree 中运行（防止 git checkout 污染主仓库）
    if [ -z "$current_wt_slug" ] && [ "$phase" = "FINALIZE" ]; then
      current_wt_slug="finalize-$(date +%Y%m%d)"
      log "⚠️ FINALIZE: current_item_id 为空，使用临时 worktree slug: $current_wt_slug"
    fi
    if [ -n "$current_wt_slug" ]; then
      local_dev_branch=$(read_field dev_branch)
      current_wt_path=$(ensure_worktree "$current_wt_slug" "$local_dev_branch")
      if [ $? -ne 0 ] || [ -z "$current_wt_path" ]; then
        log "❌ Worktree 创建失败，停止循环"
        break
      fi
      # 更新 state 记录 worktree 路径
      python3 -c "
import sys
sys.path.insert(0, '$NIUMA_DIR')
from lib.json_store import JsonFileStore

store = JsonFileStore('$STATE_FILE')

def _set_wt(state):
    state['worktree_path'] = '$current_wt_path'
    state['main_repo_path'] = '$MAIN_REPO_DIR'
    return state

store.update(_set_wt)
"
      # 在 prompt 前注入 worktree 上下文
      worktree_hint="⚠️ 你在 git worktree 中工作（隔离模式）。
- 代码修改和 git 操作在当前目录执行: ${current_wt_path}
- state.json 在 .openniuma-runtime/ 目录，必须使用绝对路径读写: ${STATE_FILE}
- task 描述文件在 .openniuma-runtime/tasks/: ${TASKS_DIR}/
- 禁止在 worktree 中创建或修改 .openniuma-runtime/ 目录下的状态文件
"
      prompt="${worktree_hint}

${prompt}"
      log "📂 Worktree: $current_wt_path"
    fi
  fi

  # 记录会话日志和计时
  task_label="${SINGLE_TASK:+task${SINGLE_TASK}-}"
  session_log="$LOG_DIR/session-$(date +%Y%m%d-%H%M%S)-${task_label}${phase}.log"
  session_start=$(date +%s)
  session_start_iso=$(date -u +%Y-%m-%dT%H:%M:%S.000Z)
  log "启动 Claude Code 会话 → $session_log"

  # Bug#3 fix: 记录 VERIFY / MERGE_FIX 进入次数，供 TUI 展示
  if [ "$phase" = "VERIFY" ] || [ "$phase" = "MERGE_FIX" ]; then
    _attempts_field="verify_attempts"
    [ "$phase" = "MERGE_FIX" ] && _attempts_field="merge_fix_attempts"
    python3 -c "
import sys
sys.path.insert(0, '$NIUMA_DIR')
from lib.json_store import JsonFileStore
store = JsonFileStore('$STATE_FILE')
def _inc(state):
    state['$_attempts_field'] = state.get('$_attempts_field', 0) + 1
    return state
store.update(_inc)
" 2>/dev/null || true
  fi

  # 解析当前 phase 对应的模型（--model CLI 参数优先于 workflow.yaml）
  if [ -n "$CLI_MODEL" ]; then
    phase_model="$CLI_MODEL"
  else
    phase_model=$(python3 "$NIUMA_DIR/lib/config.py" resolve-model "$phase" "$NIUMA_DIR/workflow.yaml" 2>/dev/null || echo "")
  fi
  if [ -n "$phase_model" ]; then
    log "🤖 模型: $phase_model"
  fi

  # 调用 claude CLI（非交互模式，跳过权限审批）
  if [ "$VERBOSE" = true ]; then
    # verbose 模式：stream-json + jq 实时解析，同时保存原始 JSON 到日志
    claude_cmd=(claude -p "$prompt" ${phase_model:+--model "$phase_model"} --output-format stream-json --verbose --dangerously-skip-permissions)
    log "🔍 verbose 模式：实时显示对话过程"
    set +e
    work_dir="${current_wt_path:-$MAIN_REPO_DIR}"
    ( cd "$work_dir" && "${claude_cmd[@]}" </dev/null 2>&1 ) | tee "$session_log" | jq -r --unbuffered '
      if .type == "assistant" then
        (.message.content[]? | if .type == "text" then "💬 " + .text
         elif .type == "tool_use" then "🔧 " + .name + ": " + (.input | tostring | .[0:200])
         else empty end)
      elif .type == "result" then
        "✅ 完成 (" + (.duration_ms / 1000 | tostring) + "s, $" + (.total_cost_usd | tostring) + ")"
      else empty end
    ' 2>/dev/null
  else
    claude_cmd=(claude -p "$prompt" ${phase_model:+--model "$phase_model"} --output-format text --dangerously-skip-permissions)
    set +e
    if [ -n "$current_wt_path" ]; then
      ( cd "$current_wt_path" && set -o pipefail && "${claude_cmd[@]}" </dev/null 2>&1 | tee "$session_log" )
    else
      ( cd "$MAIN_REPO_DIR" && set -o pipefail && "${claude_cmd[@]}" </dev/null 2>&1 | tee "$session_log" )
    fi
  fi
  claude_exit=$?

  # === session 记录（最高优先级，不受 set -e 影响） ===
  # 必须在分支守卫和其他后处理之前执行，防止任何错误导致统计丢失
  session_end=$(date +%s)
  session_end_iso=$(date -u +%Y-%m-%dT%H:%M:%S.000Z)
  session_duration=$(( session_end - session_start ))
  log "会话结束，耗时 $((session_duration / 60))m$((session_duration % 60))s"

  # 失败分类分析（仅在失败时执行）
  failure_type=""
  failure_confidence=""
  if [ "$claude_exit" -ne 0 ]; then
    failure_json=$(python3 "$NIUMA_DIR/lib/failure.py" "$session_log" "$claude_exit" 2>/dev/null || echo '{"type":"unknown","confidence":0.0}')
    failure_type=$(echo "$failure_json" | python3 -c "import json,sys; print(json.load(sys.stdin).get('type','unknown'))" 2>/dev/null || echo "unknown")
    failure_confidence=$(echo "$failure_json" | python3 -c "import json,sys; print(json.load(sys.stdin).get('confidence',0))" 2>/dev/null || echo "0")
    log "失败分类: $failure_type (置信度: $failure_confidence)"
  fi

  # 记录 session 统计（stats.py 埋点）
  current_id=$(read_field current_item_id 2>/dev/null || echo "0")
  task_name=$(python3 -c "
import json
with open('$STATE_FILE') as f:
    state = json.load(f)
for q in state.get('queue', []):
    if q['id'] == int('${current_id}' or '0'):
        print(q.get('name', '')); break
else:
    print('')
" 2>/dev/null || echo "")
  echo "{\"task_id\":${current_id:-0},\"task_name\":\"${task_name}\",\"phase\":\"${phase}\",\"started_at\":\"${session_start_iso}\",\"ended_at\":\"${session_end_iso}\",\"duration_sec\":${session_duration},\"exit_code\":${claude_exit},\"attempt\":${consecutive_failures},\"failure_type\":$([ -n "${failure_type}" ] && echo "\"$failure_type\"" || echo "null")}" | \
    python3 "$NIUMA_DIR/lib/stats.py" record-session "$STATS_FILE" 2>/dev/null || true

  set -e

  # 🔒 分支守卫：session 结束后检查并重置主仓库分支，防止 FINALIZE/CI_FIX 污染
  _guard_dev_branch=$(read_field dev_branch 2>/dev/null || echo "")
  _guard_main_branch=$(git -C "$MAIN_REPO_DIR" branch --show-current 2>/dev/null || echo "")
  if [ -n "$_guard_dev_branch" ] && [ -n "$_guard_main_branch" ] && [ "$_guard_main_branch" != "$_guard_dev_branch" ]; then
    log "⚠️ [分支守卫] 主仓库分支被污染（$_guard_main_branch → $_guard_dev_branch），自动重置"
    git -C "$MAIN_REPO_DIR" checkout "$_guard_dev_branch" 2>/dev/null || log "❌ [分支守卫] 重置失败，请人工介入"
  fi

  # 检测 API 限流（日志中含 "hit your limit" 或 "rate limit"）
  rate_limited=false
  if [ -f "$session_log" ] && grep -qi "hit your limit\|rate.limit\|resets.*[ap]m" "$session_log"; then
    rate_limited=true
  fi

  # 检测权限阻塞（不可重试错误，直接停止）
  # 注意：只检测 claude CLI 自身的错误输出，不检测代码内容（避免误匹配用户协议等业务文本）
  if [ -f "$session_log" ]; then
    permission_blocked=false
    # 非 verbose 模式：日志是纯文本，可直接 grep
    # verbose 模式：日志是 stream-json，只检查 error 类型的消息
    if [ "$VERBOSE" = true ]; then
      # 只在 error/result 类型的 JSON 行中检查
      if grep -q '"is_error":true' "$session_log" && grep -qi "permission.*denied\|permission.*blocked\|not allowed" "$session_log"; then
        permission_blocked=true
      fi
    else
      # 纯文本模式：用更严格的匹配（要求在同一行内）
      if grep -qiE "^(Error|错误|❌|🔒).*权限|permission (denied|blocked)|tool.*not allowed" "$session_log"; then
        permission_blocked=true
      fi
    fi
    if [ "$permission_blocked" = true ]; then
      log "🔒 检测到权限阻塞错误（不可通过重试解决），停止循环"
      log "请检查 claude 权限设置或 --dangerously-skip-permissions 标志"
      log "会话日志: $session_log"
      break
    fi
  fi

  if [ "$claude_exit" -ne 0 ]; then
    log "⚠️ 会话异常退出 (exit code: ${claude_exit})"
    if [ "$rate_limited" = true ]; then
      # 限流：计算到重置时间的等待秒数，不消耗重试次数
      reset_info=$(grep -oi "resets [0-9]\+[ap]m" "$session_log" | tail -1)
      log "🚫 API 配额耗尽（${reset_info:-未知重置时间}），进入长等待..."
      # 尝试解析重置时间，计算等待秒数
      reset_hour=$(echo "$reset_info" | grep -o '[0-9]\+')
      reset_ampm=$(echo "$reset_info" | grep -oi '[ap]m')
      wait_seconds=600  # 默认 10 分钟
      if [ -n "$reset_hour" ] && [ -n "$reset_ampm" ]; then
        # 转换为 24 小时制
        if [ "$(echo "$reset_ampm" | tr '[:upper:]' '[:lower:]')" = "pm" ] && [ "$reset_hour" -ne 12 ]; then
          reset_hour=$((reset_hour + 12))
        elif [ "$(echo "$reset_ampm" | tr '[:upper:]' '[:lower:]')" = "am" ] && [ "$reset_hour" -eq 12 ]; then
          reset_hour=0
        fi
        current_epoch=$(date +%s)
        # 构造今天的重置时间戳
        reset_epoch=$(date -j -f "%Y-%m-%d %H:%M:%S" "$(date +%Y-%m-%d) ${reset_hour}:00:00" +%s 2>/dev/null || echo "")
        if [ -n "$reset_epoch" ]; then
          # 如果重置时间已过（跨天），加 24 小时
          if [ "$reset_epoch" -le "$current_epoch" ]; then
            reset_epoch=$((reset_epoch + 86400))
          fi
          wait_seconds=$((reset_epoch - current_epoch + 60))  # 多等 1 分钟缓冲
          if [ "$wait_seconds" -lt 60 ]; then wait_seconds=60; fi
          if [ "$wait_seconds" -gt 43200 ]; then wait_seconds=600; fi  # 上限 12h，超过则回退默认
        fi
      fi
      log "⏳ 等待 $((wait_seconds / 60)) 分钟后恢复..."
      sleep "$wait_seconds"
      # 限流不算失败次数，直接继续下一轮
      log "---"
      continue
    else
      # 普通错误：用 retry.py 计算自适应延迟
      retry_delay=$(python3 "$NIUMA_DIR/lib/retry.py" "${failure_type:-unknown}" "$((consecutive_failures + 1))" "${CONF_BASE_DELAY:-10}" "${CONF_MAX_BACKOFF:-300}" 2>/dev/null || echo "30")
      # 取整（retry.py 输出可能含小数）
      retry_delay=$(printf "%.0f" "$retry_delay" 2>/dev/null || echo "30")
      if [ "$retry_delay" -gt 0 ] 2>/dev/null; then
        log "等待 ${retry_delay}s 后重试（失败类型: ${failure_type:-unknown}）..."
        sleep "$retry_delay"
      fi
    fi
  fi

  # 判定本轮是否成功：以 phase 是否推进为准（而非 claude 退出码）
  # 因为 claude 正常退出但没完成工作（如上下文耗尽）也算失败
  new_phase=$(read_phase)

  # Bug fix: FINALIZE/MERGE 后 phase 不得回退到 DESIGN_IMPLEMENT（Claude 误写防护）
  if [ "$phase" = "FINALIZE" ] || [ "$phase" = "MERGE" ]; then
    if [ "$new_phase" = "DESIGN_IMPLEMENT" ]; then
      log "⚠️ [Phase 防护] ${phase} 后出现 DESIGN_IMPLEMENT 回退，强制修正为 AWAITING_HUMAN_REVIEW"
      python3 -c "
import sys
sys.path.insert(0, '$NIUMA_DIR')
from lib.json_store import JsonFileStore
store = JsonFileStore('$STATE_FILE')
store.update(lambda s: dict(s, current_phase='AWAITING_HUMAN_REVIEW'))
"
      new_phase="AWAITING_HUMAN_REVIEW"
    fi
  fi

  if [ "$new_phase" != "$phase" ]; then
    log "Phase 推进: ${phase} → ${new_phase}"
    consecutive_failures=0

    # 功能完成后标记任务文件 + 清理 worktree（MERGE 成功推进 = 功能已合入 dev）
    if [ "$phase" = "MERGE" ]; then
      current_item=$(read_field current_item_id 2>/dev/null || true)
      if [ -n "$current_item" ]; then
        mark_task_done "$current_item"
        send_notify "info" "任务 #${current_item} 完成" "${task_name}"
      fi
    fi
    if [ "$phase" = "MERGE" ] && [ -n "$current_wt_slug" ]; then
      cleanup_worktree "$current_wt_slug"
      current_wt_path=""
      current_wt_slug=""
    fi
  else
    consecutive_failures=$((consecutive_failures + 1))
    log "⚠️ Phase 未变化（仍为 ${phase}），第 ${consecutive_failures}/${MAX_CONSECUTIVE_FAILURES} 次"
    # 设置下一轮的断点续传提示
    retry_hint="⚠️ 上一个会话可能中途结束，这是第 ${consecutive_failures} 次重试。
请先检查已有产出物再决定从哪里继续：
- 检查 state.json 当前状态
- 检查是否已有 review 文件、commit 记录等产出
- 如果核心工作已完成只差更新 state.json，直接更新 phase 并结束
- 不要重做已完成的工作"
  fi

  # 每轮会话结束后刷新 backlog.md（反映 state.json 的最新状态）
  refresh_backlog
  if [ -n "$(git -C "$MAIN_REPO_DIR" status --porcelain .openniuma-runtime/backlog.md 2>/dev/null)" ]; then
    git -C "$MAIN_REPO_DIR" add .openniuma-runtime/backlog.md
    git -C "$MAIN_REPO_DIR" commit -m "docs: 刷新 backlog.md"
    log "📝 backlog.md 已刷新并 commit"
  fi

  # 安全阀：连续失败过多则停止
  if [ "$consecutive_failures" -ge "$MAX_CONSECUTIVE_FAILURES" ]; then
    log "❌ 连续 ${MAX_CONSECUTIVE_FAILURES} 次 Phase 未推进，停止循环，等待人工排查"
    log "最后的会话日志: $session_log"
    send_notify "critical" "Loop 停止" "连续 ${consecutive_failures} 次失败，phase: ${phase}"
    break
  fi

  log "---"
done

log "循环结束"
