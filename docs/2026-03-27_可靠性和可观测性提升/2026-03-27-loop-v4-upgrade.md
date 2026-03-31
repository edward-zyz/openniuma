# Loop v4 升级实现计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 为 dev-loop.sh 添加可视化看板、运行数据沉淀、异步通知、智能失败恢复和任务快捷入队五大能力。

**Architecture:** 新增 5 个独立 Shell 脚本（dashboard.sh, generate-progress.sh, stats.sh, notify.sh, add-task.sh），通过 stats.json 共享数据。dev-loop.sh 在会话前后埋点写入 stats.json、调用 notify.sh、替换现有失败处理逻辑。所有脚本延续 Shell + python3 内联的现有模式。

**Tech Stack:** Bash 4+, jq 1.7+, Python3 3.8+（均 macOS 自带）；可选 curl（飞书通知）、gh CLI（Issue 导入）

**设计文档:** `docs/plans/2026-03-27-loop-v4-upgrade-design.md`

---

## Task 1: .gitignore + 基础文件准备

**Files:**
- Modify: `.gitignore:21-27`

**Step 1: 添加新文件到 .gitignore**

在 `.gitignore` 的 loop 区域追加：

```
loop/stats.json
loop/PROGRESS.md
loop/.env
loop/workers/
loop/.locks/
```

**Step 2: 验证**

```bash
cat .gitignore | grep -A 15 'loop/'
```

Expected: 看到新增的 3 行（stats.json, PROGRESS.md, .env）

**Step 3: Commit**

```bash
git add .gitignore
git commit -m "chore: gitignore loop 新增产物文件"
```

---

## Task 2: dashboard.sh — 终端实时看板

**Files:**
- Create: `loop/dashboard.sh`

**Step 1: 创建 dashboard.sh**

完整脚本，功能区域：

```bash
#!/usr/bin/env bash
set -euo pipefail

# ── 参数解析 ──
WATCH=false
INTERVAL=5
for arg in "$@"; do
  case "$arg" in
    -w|--watch) WATCH=true ;;
    [0-9]*) INTERVAL="$arg" ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STATE_FILE="${SCRIPT_DIR}/loop-state.json"
LOG_DIR="${SCRIPT_DIR}/logs"
REVIEWS_DIR="${SCRIPT_DIR}/reviews"
WORKERS_DIR="${SCRIPT_DIR}/workers"
STATS_FILE="${SCRIPT_DIR}/stats.json"

# ── ANSI 颜色 ──
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
GRAY='\033[0;90m'
BOLD='\033[1m'
RESET='\033[0m'

# ── 阶段图标映射 ──
phase_icon() {
  case "$1" in
    INIT) echo "🚀" ;; DESIGN) echo "📐" ;; DESIGN_IMPLEMENT) echo "📐🔨" ;;
    IMPLEMENT) echo "🔨" ;; VERIFY) echo "🔍" ;; FIX) echo "🔧" ;;
    MERGE) echo "🔀" ;; MERGE_FIX) echo "🩹" ;; FINALIZE) echo "📦" ;;
    FAST_TRACK) echo "⚡" ;; CI_FIX) echo "🔧" ;; *) echo "❓" ;;
  esac
}

# ── 状态图标 ──
status_icon() {
  case "$1" in
    done) echo "✅" ;; in_progress|in-progress) echo "🔄" ;;
    pending) echo "⏳" ;; blocked) echo "🚫" ;; *) echo "·" ;;
  esac
}

# ── 进度条生成 ──
progress_bar() {
  local done=$1 total=$2 width=20
  if [ "$total" -eq 0 ]; then echo "░░░░░░░░░░░░░░░░░░░░ 0/0"; return; fi
  local filled=$(( done * width / total ))
  local empty=$(( width - filled ))
  local pct=$(( done * 100 / total ))
  printf '%s' "$(printf '█%.0s' $(seq 1 $filled 2>/dev/null) || true)"
  printf '%s' "$(printf '░%.0s' $(seq 1 $empty 2>/dev/null) || true)"
  printf ' %d%% (%d/%d)' "$pct" "$done" "$total"
}

# ── 主渲染函数 ──
render() {
  if [ ! -f "$STATE_FILE" ]; then
    echo "❌ loop-state.json 不存在: $STATE_FILE"
    return 1
  fi

  # 用 python3 一次性从 state 提取所有需要的数据（避免多次 jq 调用）
  eval "$(python3 -c "
import json, os
with open('$STATE_FILE') as f:
    s = json.load(f)
q = s.get('queue', [])
completed = s.get('completed', [])
blocked = s.get('blocked', [])
done_count = sum(1 for t in q if t.get('status') == 'done')
total = len(q)
phase = s.get('current_phase', 'UNKNOWN')
item_id = s.get('current_item_id', '')
dev_branch = s.get('dev_branch', '')
updated = s.get('updated_at', '')
branch = s.get('branch', '')
ip = s.get('implement_progress', {})
va = s.get('verify_attempts', 0)
mfa = s.get('merge_fix_attempts', 0)

# 当前任务名
task_name = ''
for t in q:
    if t.get('id') == item_id:
        task_name = t.get('name', '')
        break

print(f'DONE_COUNT={done_count}')
print(f'TOTAL={total}')
print(f'PHASE=\"{phase}\"')
print(f'ITEM_ID=\"{item_id}\"')
print(f'TASK_NAME=\"{task_name}\"')
print(f'DEV_BRANCH=\"{dev_branch}\"')
print(f'UPDATED=\"{updated}\"')
print(f'BRANCH=\"{branch}\"')
print(f'CHUNK=\"{ip.get(\"current_chunk\", 0)}\"')
print(f'TASK=\"{ip.get(\"current_task\", 0)}\"')
print(f'LAST_SHA=\"{ip.get(\"last_commit_sha\", \"\") or \"\"}\"')
print(f'VERIFY_ATTEMPTS={va}')
print(f'MERGE_FIX={mfa}')
")"

  local width=56
  local line
  line=$(printf '─%.0s' $(seq 1 $((width - 2))))

  # Header
  printf "${BOLD}┌─ Dev Loop Dashboard %s┐${RESET}\n" "$(printf '─%.0s' $(seq 1 $((width - 23))))"
  printf "│  分支: ${BOLD}%s${RESET}%*s│\n" "$DEV_BRANCH" $(( width - ${#DEV_BRANCH} - 9 )) ""
  local phase_str="$(phase_icon "$PHASE") $PHASE"
  printf "│  阶段: %s  |  任务: #%s %s%*s│\n" "$phase_str" "$ITEM_ID" "$TASK_NAME" $(( width - ${#phase_str} - ${#ITEM_ID} - ${#TASK_NAME} - 22 )) ""
  printf "│  更新: %s%*s│\n" "$UPDATED" $(( width - ${#UPDATED} - 9 )) ""

  # Progress
  printf "├%s┤\n" "$line"
  local pbar
  pbar=$(progress_bar "$DONE_COUNT" "$TOTAL")
  printf "│  进度: %s%*s│\n" "$pbar" $(( width - ${#pbar} - 9 )) ""

  # Task List
  printf "├%s┤\n" "$line"
  python3 -c "
import json
with open('$STATE_FILE') as f:
    q = json.load(f).get('queue', [])
icons = {'done': '✅', 'in_progress': '🔄', 'in-progress': '🔄', 'pending': '⏳', 'blocked': '🚫'}
colors = {'done': '\033[0;32m', 'in_progress': '\033[1;33m', 'in-progress': '\033[1;33m', 'pending': '\033[0;90m', 'blocked': '\033[0;31m'}
reset = '\033[0m'
for t in q:
    st = t.get('status', 'pending')
    icon = icons.get(st, '·')
    color = colors.get(st, '')
    name = t.get('name', '')[:30]
    cpx = t.get('complexity', '?')
    tid = str(t.get('id', ''))
    line = f'  #{tid:<4} {name:<32} {icon} {st:<12} {cpx}'
    padded = f'{color}│{line}\033[0m'
    print(padded)
"

  # Pipeline
  printf "├%s┤\n" "$line"
  # 根据当前任务的实际路径确定 pipeline
  python3 -c "
phase = '$PHASE'
# 确定流水线阶段列表
if phase in ('FAST_TRACK',):
    stages = ['FAST_TRACK', 'VERIFY', 'MERGE']
elif phase in ('DESIGN_IMPLEMENT',):
    stages = ['DESIGN_IMPLEMENT', 'VERIFY', 'MERGE']
elif phase in ('DESIGN', 'IMPLEMENT'):
    stages = ['DESIGN', 'IMPLEMENT', 'VERIFY', 'MERGE']
else:
    stages = ['DESIGN_IMPLEMENT', 'VERIFY', 'MERGE']

icons = {'INIT':'🚀','DESIGN':'📐','DESIGN_IMPLEMENT':'📐🔨','IMPLEMENT':'🔨','VERIFY':'🔍','FIX':'🔧','MERGE':'🔀','FAST_TRACK':'⚡','FINALIZE':'📦'}
# 确定当前和已过阶段
try:
    idx = stages.index(phase)
except ValueError:
    idx = -1

parts = []
for i, s in enumerate(stages):
    icon = icons.get(s, '?')
    label = s.replace('DESIGN_IMPLEMENT','D+I').replace('FAST_TRACK','FAST').replace('IMPLEMENT','IMPL')
    if i == idx:
        parts.append(f'\033[1;33m[{icon}{label}]\033[0m')
    elif i < idx:
        parts.append(f'\033[0;32m{icon}{label}\033[0m')
    else:
        parts.append(f'\033[0;90m{icon}{label}\033[0m')
print('│  流水线: ' + ' → '.join(parts))
"

  # Checkpoint
  printf "├%s┤\n" "$line"
  printf "│  实现: Chunk %s, Task %s" "$CHUNK" "$TASK"
  [ -n "$LAST_SHA" ] && printf "  sha: %.7s" "$LAST_SHA"
  printf "%*s│\n" 5 ""
  printf "│  重试: verify=%s, merge_fix=%s%*s│\n" "$VERIFY_ATTEMPTS" "$MERGE_FIX" $(( width - 35 )) ""

  # Recent Sessions
  printf "├%s┤\n" "$line"
  printf "│  ${BOLD}最近 Sessions:${RESET}%*s│\n" $(( width - 18 )) ""
  if [ -d "$LOG_DIR" ]; then
    # shellcheck disable=SC2012
    ls -t "$LOG_DIR"/session-*.log 2>/dev/null | head -5 | while read -r logfile; do
      fname=$(basename "$logfile" .log)
      # session-20260326-143000-IMPLEMENT → 14:30 IMPLEMENT
      ts=$(echo "$fname" | sed 's/session-[0-9]\{8\}-\([0-9]\{2\}\)\([0-9]\{2\}\)[0-9]\{2\}-\(.*\)/\1:\2 \3/')
      printf "│    %s%*s│\n" "$ts" $(( width - ${#ts} - 6 )) ""
    done
  fi

  # Recent Reviews
  printf "├%s┤\n" "$line"
  printf "│  ${BOLD}最近 Reviews:${RESET}%*s│\n" $(( width - 17 )) ""
  if [ -d "$REVIEWS_DIR" ]; then
    # shellcheck disable=SC2012
    ls -t "$REVIEWS_DIR"/*.md 2>/dev/null | head -3 | while read -r revfile; do
      fname=$(basename "$revfile" .md)
      verdict=$(grep -i 'verdict\|结论' "$revfile" 2>/dev/null | head -1 | sed 's/.*[：:]\s*//' | head -c 30)
      line_text="${fname}: ${verdict:-?}"
      printf "│    %s%*s│\n" "$line_text" $(( width - ${#line_text} - 6 )) ""
    done
  fi

  # Workers (并行模式)
  if ls "${WORKERS_DIR}"/*/pid 2>/dev/null | head -1 > /dev/null 2>&1; then
    printf "├%s┤\n" "$line"
    printf "│  ${BOLD}Workers:${RESET}%*s│\n" $(( width - 12 )) ""
    active=0
    for pidfile in "${WORKERS_DIR}"/*/pid; do
      [ -f "$pidfile" ] || continue
      wid=$(basename "$(dirname "$pidfile")")
      wpid=$(cat "$pidfile")
      wname=$(cat "${WORKERS_DIR}/${wid}/name" 2>/dev/null || echo "#${wid}")
      if kill -0 "$wpid" 2>/dev/null; then
        # 计算运行时间
        wstart=$(stat -f %m "$pidfile" 2>/dev/null || stat -c %Y "$pidfile" 2>/dev/null || echo 0)
        wnow=$(date +%s)
        wmins=$(( (wnow - wstart) / 60 ))
        wphase=$(python3 -c "
import json, os
sf = '${WORKERS_DIR}/${wid}/state.json'
if os.path.exists(sf):
    with open(sf) as f: print(json.load(f).get('current_phase', '?'))
else: print('?')
" 2>/dev/null || echo "?")
        printf "│    ${YELLOW}#%-4s %-20s %s  %dm${RESET}%*s│\n" "$wid" "$wname" "$(phase_icon "$wphase")" "$wmins" 3 ""
        active=$((active + 1))
      fi
    done
    printf "│    容量: %d active%*s│\n" "$active" $(( width - 18 )) ""
  fi

  # Stats 摘要（如果 stats.json 存在）
  if [ -f "$STATS_FILE" ]; then
    printf "├%s┤\n" "$line"
    stats_line=$(python3 -c "
import json
with open('$STATS_FILE') as f:
    data = json.load(f)
sessions = data.get('sessions', [])
total_dur = sum(s.get('duration_sec', 0) for s in sessions)
total_cost = sum(s.get('cost_usd', 0) or 0 for s in sessions)
tasks_done = len(data.get('tasks', []))
hours = total_dur / 3600
avg = hours / tasks_done if tasks_done > 0 else 0
print(f'💰 \${total_cost:.2f} | ⏱ {hours:.1f}h 总计 | 平均 {avg:.1f}h/任务')
" 2>/dev/null || echo "stats.json 解析失败")
    printf "│  %s%*s│\n" "$stats_line" $(( width - ${#stats_line} - 4 )) ""
  fi

  printf "└%s┘\n" "$line"
}

# ── 入口 ──
if [ "$WATCH" = true ]; then
  while true; do
    clear
    render
    echo ""
    printf "${GRAY}每 %ds 自动刷新 | Ctrl+C 退出${RESET}\n" "$INTERVAL"
    sleep "$INTERVAL"
  done
else
  render
fi
```

**Step 2: 设置可执行权限并测试**

```bash
chmod +x loop/dashboard.sh
bash loop/dashboard.sh
```

Expected: 如果 loop-state.json 不存在会显示错误提示；存在时渲染完整看板。

**Step 3: Commit**

```bash
git add loop/dashboard.sh
git commit -m "feat(loop): 终端实时看板 dashboard.sh"
```

---

## Task 3: generate-progress.sh — Mermaid 进度报告

**Files:**
- Create: `loop/generate-progress.sh`

**Step 1: 创建 generate-progress.sh**

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STATE_FILE="${SCRIPT_DIR}/loop-state.json"
LOG_DIR="${SCRIPT_DIR}/logs"
REVIEWS_DIR="${SCRIPT_DIR}/reviews"
STATS_FILE="${SCRIPT_DIR}/stats.json"
OUTPUT="${SCRIPT_DIR}/PROGRESS.md"

if [ ! -f "$STATE_FILE" ]; then
  echo "❌ loop-state.json 不存在" >&2
  exit 1
fi

python3 <<'PYEOF'
import json, os, re
from datetime import datetime
from pathlib import Path

state_file = os.environ.get("STATE_FILE", "") or "STATE_FILE_PLACEHOLDER"
log_dir = os.environ.get("LOG_DIR", "") or "LOG_DIR_PLACEHOLDER"
reviews_dir = os.environ.get("REVIEWS_DIR", "") or "REVIEWS_DIR_PLACEHOLDER"
stats_file = os.environ.get("STATS_FILE", "") or "STATS_FILE_PLACEHOLDER"
output = os.environ.get("OUTPUT", "") or "OUTPUT_PLACEHOLDER"
PYEOF

# 用 python3 生成完整 PROGRESS.md（避免 shell 拼 markdown 的转义问题）
STATE_FILE="$STATE_FILE" LOG_DIR="$LOG_DIR" REVIEWS_DIR="$REVIEWS_DIR" \
STATS_FILE="$STATS_FILE" OUTPUT="$OUTPUT" \
python3 <<'PYEOF'
import json, os, re
from datetime import datetime
from pathlib import Path

state_file = os.environ["STATE_FILE"]
log_dir = os.environ["LOG_DIR"]
reviews_dir = os.environ["REVIEWS_DIR"]
stats_file = os.environ["STATS_FILE"]
output_file = os.environ["OUTPUT"]

with open(state_file) as f:
    state = json.load(f)

queue = state.get("queue", [])
completed = state.get("completed", [])
phase = state.get("current_phase", "UNKNOWN")
item_id = state.get("current_item_id")
dev_branch = state.get("dev_branch", "")
updated = state.get("updated_at", "")
ip = state.get("implement_progress", {})
va = state.get("verify_attempts", 0)
mfa = state.get("merge_fix_attempts", 0)

done_count = sum(1 for t in queue if t.get("status") == "done")
total = len(queue)
pct = (done_count * 100 // total) if total > 0 else 0

# 当前任务名
task_name = ""
for t in queue:
    if t.get("id") == item_id:
        task_name = t.get("name", "")
        break

# 阶段图标
ICONS = {
    "INIT": "🚀", "DESIGN": "📐", "DESIGN_IMPLEMENT": "📐🔨",
    "IMPLEMENT": "🔨", "VERIFY": "🔍", "FIX": "🔧",
    "MERGE": "🔀", "MERGE_FIX": "🩹", "FINALIZE": "📦",
    "FAST_TRACK": "⚡", "CI_FIX": "🔧",
    "AWAITING_HUMAN_REVIEW": "👀",
}
STATUS_ICONS = {"done": "✅", "in_progress": "🔄", "in-progress": "🔄", "pending": "⏳", "blocked": "🚫"}

lines = []
lines.append("# Dev Loop 进度报告")
lines.append("")
lines.append(f"> 自动生成于 {datetime.now().strftime('%Y-%m-%d %H:%M')}")
lines.append("")

# ── 概览 ──
lines.append("## 概览")
lines.append("")
lines.append("| 项目 | 值 |")
lines.append("|------|------|")
lines.append(f"| 集成分支 | `{dev_branch}` |")
lines.append(f"| 当前阶段 | {ICONS.get(phase, '?')} {phase} |")
lines.append(f"| 当前任务 | #{item_id} {task_name} |")
lines.append(f"| 总进度 | {done_count}/{total} ({pct}%) |")
lines.append(f"| 更新时间 | {updated} |")
lines.append("")

# 文本进度条
filled = done_count * 20 // total if total > 0 else 0
bar = "█" * filled + "░" * (20 - filled)
lines.append(f"`{bar}` {pct}% ({done_count}/{total})")
lines.append("")

# ── Mermaid: 任务状态图 ──
lines.append("## 任务状态图")
lines.append("")
lines.append("```mermaid")
lines.append("graph LR")
for t in queue:
    tid = t["id"]
    name = t.get("name", "")[:20]
    st = t.get("status", "pending")
    cls = {"done": "done", "in_progress": "active", "in-progress": "active", "pending": "pending", "blocked": "blocked"}.get(st, "pending")
    lines.append(f'  T{tid}["#{tid} {name}"]:::{cls}')
# 连接线：按顺序
ids = [t["id"] for t in queue]
if len(ids) > 1:
    chain = " --> ".join(f"T{i}" for i in ids)
    lines.append(f"  {chain}")
lines.append("")
lines.append("  classDef done fill:#d4edda,stroke:#28a745,color:#155724")
lines.append("  classDef active fill:#fff3cd,stroke:#ffc107,color:#856404")
lines.append("  classDef pending fill:#e2e3e5,stroke:#6c757d,color:#383d41")
lines.append("  classDef blocked fill:#f8d7da,stroke:#dc3545,color:#721c24")
lines.append("```")
lines.append("")

# ── Mermaid: 阶段流水线 ──
lines.append("## 当前任务阶段流水线")
lines.append("")
lines.append("```mermaid")
lines.append("graph LR")
if phase in ("FAST_TRACK",):
    stages = ["FAST_TRACK", "VERIFY", "MERGE"]
elif phase in ("DESIGN_IMPLEMENT",):
    stages = ["DESIGN_IMPLEMENT", "VERIFY", "MERGE"]
elif phase in ("DESIGN", "IMPLEMENT"):
    stages = ["DESIGN", "IMPLEMENT", "VERIFY", "MERGE"]
else:
    stages = ["DESIGN_IMPLEMENT", "VERIFY", "MERGE"]

try:
    phase_idx = stages.index(phase)
except ValueError:
    phase_idx = -1

for i, s in enumerate(stages):
    icon = ICONS.get(s, "?")
    label = s.replace("DESIGN_IMPLEMENT", "D+I").replace("FAST_TRACK", "FAST").replace("IMPLEMENT", "IMPL")
    node_id = f"S{i}"
    if i == phase_idx:
        lines.append(f'  {node_id}["{icon} {label}"]:::active')
    elif i < phase_idx:
        lines.append(f'  {node_id}["{icon} {label}"]:::done')
    else:
        lines.append(f'  {node_id}["{icon} {label}"]:::pending')
if len(stages) > 1:
    lines.append("  " + " --> ".join(f"S{i}" for i in range(len(stages))))
lines.append("")
lines.append("  classDef done fill:#d4edda,stroke:#28a745,color:#155724")
lines.append("  classDef active fill:#fff3cd,stroke:#ffc107,color:#856404")
lines.append("  classDef pending fill:#e2e3e5,stroke:#6c757d,color:#383d41")
lines.append("```")
lines.append("")

# ── 实施进度 ──
if ip.get("current_chunk", 0) > 0:
    lines.append("## 实施进度")
    lines.append("")
    lines.append("| 项目 | 值 |")
    lines.append("|------|------|")
    lines.append(f"| 当前 Chunk | {ip.get('current_chunk', 0)} |")
    lines.append(f"| 当前 Task | {ip.get('current_task', 0)} |")
    lines.append(f"| 最后提交 Task | {ip.get('last_committed_task', '-')} |")
    lines.append(f"| 最后 Commit | `{(ip.get('last_commit_sha') or '-')[:7]}` |")
    lines.append(f"| Verify 重试 | {va} |")
    lines.append(f"| Merge Fix 重试 | {mfa} |")
    lines.append("")

# ── 任务清单 ──
lines.append("## 任务清单")
lines.append("")
lines.append("| ID | 名称 | 复杂度 | 状态 | 创建时间 | 完成时间 |")
lines.append("|---:|------|--------|------|----------|----------|")
# 排序：completed → blocked → queue
sorted_tasks = sorted(queue, key=lambda t: {"done": 0, "blocked": 1, "in_progress": 2, "in-progress": 2, "pending": 3}.get(t.get("status", "pending"), 3))
for t in sorted_tasks:
    st = t.get("status", "pending")
    icon = STATUS_ICONS.get(st, "·")
    lines.append(f"| {t['id']} | {t.get('name', '')} | {t.get('complexity', '?')} | {icon} {st} | {t.get('created_at', '-')} | {t.get('completed_at', '-')} |")
lines.append("")

# ── Session 按日汇总 ──
lines.append("## Session 按日汇总")
lines.append("")
log_path = Path(log_dir)
if log_path.exists():
    # 收集 session 文件名信息
    day_phase = {}  # {date: {phase: count}}
    phase_set = set()
    for f in sorted(log_path.glob("session-*.log")):
        m = re.match(r"session-(\d{8})-\d{6}-(.+)\.log", f.name)
        if m:
            date_str = m.group(1)
            date_display = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
            p = m.group(2)
            phase_set.add(p)
            day_phase.setdefault(date_display, {})
            day_phase[date_display][p] = day_phase[date_display].get(p, 0) + 1

    if day_phase:
        all_phases = sorted(phase_set)
        header = "| 日期 | " + " | ".join(f"{ICONS.get(p, '?')} {p[:6]}" for p in all_phases) + " | 总计 |"
        sep = "|------|" + "|".join("---:" for _ in all_phases) + "|---:|"
        lines.append(header)
        lines.append(sep)
        for date in sorted(day_phase.keys()):
            counts = [str(day_phase[date].get(p, 0)) for p in all_phases]
            total_day = sum(day_phase[date].values())
            lines.append(f"| {date} | " + " | ".join(counts) + f" | {total_day} |")
        lines.append("")
    else:
        lines.append("暂无 session 日志。")
        lines.append("")
else:
    lines.append("暂无 session 日志。")
    lines.append("")

# ── Session 甘特时间线 ──
lines.append("## Session 时间线")
lines.append("")
if log_path.exists() and day_phase:
    lines.append("```mermaid")
    lines.append("gantt")
    lines.append("  title Session 时间线")
    lines.append("  dateFormat YYYY-MM-DD")
    lines.append("  axisFormat %m-%d")
    # 按阶段分 section，每个日期-阶段组合一条
    seen_sections = set()
    for f in sorted(log_path.glob("session-*.log")):
        m = re.match(r"session-(\d{8})-(\d{6})-(.+)\.log", f.name)
        if m:
            date_str = m.group(1)
            date_iso = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
            p = m.group(2)
            phase_name = m.group(3)
            key = f"{date_iso}-{phase_name}"
            if key not in seen_sections:
                seen_sections.add(key)
                if phase_name not in [s for s in seen_sections if not s.startswith("2")]:
                    lines.append(f"  section {phase_name}")
                lines.append(f"  {phase_name} ({date_iso}) : {date_iso}, 30m")
    lines.append("```")
    lines.append("")

# ── 审查记录 ──
lines.append("## 审查记录")
lines.append("")
rev_path = Path(reviews_dir)
if rev_path.exists():
    rev_files = sorted(rev_path.glob("*.md"), key=lambda f: f.stat().st_mtime, reverse=True)
    if rev_files:
        lines.append("| 文件 | 结论 |")
        lines.append("|------|------|")
        for rf in rev_files[:10]:
            content = rf.read_text()
            verdict = "?"
            for line in content.split("\n"):
                if re.search(r"verdict|结论|Verdict", line, re.IGNORECASE):
                    verdict = re.sub(r".*[：:]\s*", "", line).strip()[:40]
                    break
            # 映射 PASS/FAIL
            if "PASS" in verdict.upper():
                verdict = f"✅ {verdict}"
            elif "FAIL" in verdict.upper():
                verdict = f"❌ {verdict}"
            lines.append(f"| {rf.name} | {verdict} |")
        lines.append("")
    else:
        lines.append("暂无审查记录。")
        lines.append("")
else:
    lines.append("暂无审查记录。")
    lines.append("")

# ── 运行统计（如果 stats.json 存在）──
if os.path.exists(stats_file):
    try:
        with open(stats_file) as f:
            stats = json.load(f)
        sessions = stats.get("sessions", [])
        tasks_stats = stats.get("tasks", [])
        total_dur = sum(s.get("duration_sec", 0) for s in sessions)
        total_cost = sum(s.get("cost_usd", 0) or 0 for s in sessions)
        hours = total_dur / 3600
        avg_h = hours / len(tasks_stats) if tasks_stats else 0
        verify_first = sum(1 for t in tasks_stats if t.get("verify_pass_first_try"))
        verify_total = sum(1 for t in tasks_stats if t.get("verify_attempts", 0) > 0)

        lines.append("## 运行统计")
        lines.append("")
        lines.append("| 指标 | 值 |")
        lines.append("|------|------|")
        lines.append(f"| 总 Session | {len(sessions)} |")
        lines.append(f"| 总耗时 | {hours:.1f}h |")
        lines.append(f"| 总成本 | ${total_cost:.2f} |")
        lines.append(f"| 平均每任务 | {avg_h:.1f}h |")
        if verify_total > 0:
            lines.append(f"| VERIFY 首次通过率 | {verify_first}/{verify_total} ({verify_first*100//verify_total}%) |")
        lines.append("")
    except Exception:
        pass

with open(output_file, "w") as f:
    f.write("\n".join(lines))

print(f"✅ PROGRESS.md 已生成: {output_file}")
PYEOF
```

**Step 2: 测试**

```bash
chmod +x loop/generate-progress.sh
bash loop/generate-progress.sh
cat loop/PROGRESS.md | head -30
```

Expected: 生成 PROGRESS.md 文件，包含概览表格和 Mermaid 图表。

**Step 3: Commit**

```bash
git add loop/generate-progress.sh
git commit -m "feat(loop): Mermaid 进度报告生成器 generate-progress.sh"
```

---

## Task 4: dev-loop.sh 集成可视化

**Files:**
- Modify: `loop/dev-loop.sh:1658-1664` (串行模式: backlog 刷新后)
- Modify: `loop/dev-loop.sh:1246-1251` (并行模式: backlog 刷新后)

**Step 1: 串行模式 — backlog 刷新后生成 PROGRESS.md**

在 `dev-loop.sh` 第 1664 行 `log "📝 backlog.md 已刷新并 commit"` 之后追加：

```bash
  # 自动更新 PROGRESS.md
  bash "${MAIN_REPO_DIR}/loop/generate-progress.sh" 2>/dev/null &
```

**Step 2: 并行模式 — backlog 刷新后生成 PROGRESS.md**

在 `parallel_main()` 中第 1251 行 backlog commit 之后追加同样的调用。

**Step 3: 测试**

```bash
grep -n 'generate-progress' loop/dev-loop.sh
```

Expected: 两处调用点。

**Step 4: Commit**

```bash
git add loop/dev-loop.sh
git commit -m "feat(loop): 每轮 phase 后自动生成 PROGRESS.md"
```

---

## Task 5: stats.sh + stats.json 数据沉淀

**Files:**
- Create: `loop/stats.sh`

**Step 1: 创建 stats.sh**

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STATS_FILE="${SCRIPT_DIR}/stats.json"

# ── 参数解析 ──
MODE="summary"
TASK_ID=""
for arg in "$@"; do
  case "$arg" in
    --task=*) MODE="task"; TASK_ID="${arg#*=}" ;;
    --task) MODE="task" ;;
    --cost) MODE="cost" ;;
    --sessions) MODE="sessions" ;;
    *)
      case "${prev_arg:-}" in
        --task) TASK_ID="$arg" ;;
      esac
      ;;
  esac
  prev_arg="$arg"
done

if [ ! -f "$STATS_FILE" ]; then
  echo "❌ stats.json 不存在。运行 dev-loop.sh 后自动生成。"
  exit 1
fi

python3 - "$MODE" "$TASK_ID" "$STATS_FILE" <<'PYEOF'
import json, sys

mode = sys.argv[1]
task_id = sys.argv[2]
stats_file = sys.argv[3]

with open(stats_file) as f:
    data = json.load(f)

sessions = data.get("sessions", [])
tasks = data.get("tasks", [])

def fmt_duration(sec):
    h = sec // 3600
    m = (sec % 3600) // 60
    if h > 0:
        return f"{h}h{m}m"
    return f"{m}m"

def progress_bar(val, total, width=16):
    if total == 0:
        return "░" * width
    filled = int(val / total * width)
    return "█" * filled + "░" * (width - filled)

if mode == "summary":
    total_dur = sum(s.get("duration_sec", 0) for s in sessions)
    total_cost = sum(s.get("cost_usd", 0) or 0 for s in sessions)
    tasks_done = len(tasks)
    avg_dur = total_dur / tasks_done if tasks_done else 0
    avg_cost = total_cost / tasks_done if tasks_done else 0
    avg_sessions = len(sessions) / tasks_done if tasks_done else 0

    verify_first = sum(1 for t in tasks if t.get("verify_pass_first_try"))
    verify_total = sum(1 for t in tasks if t.get("verify_attempts", 0) > 0)

    print("📊 Dev Loop 运行统计")
    print("━" * 40)
    print(f"总任务: {tasks_done}  |  总 Session: {len(sessions)}")
    print(f"总耗时: {fmt_duration(total_dur)}  |  总成本: ${total_cost:.2f}")
    print()
    print(f"平均每任务: {fmt_duration(int(avg_dur))}, {avg_sessions:.1f} sessions, ${avg_cost:.2f}")
    print()
    if verify_total > 0:
        pct = verify_first * 100 // verify_total
        print(f"VERIFY 通过率: 首次 {pct}% ({verify_first}/{verify_total})")
    print()

    # 按复杂度
    by_cpx = {}
    for t in tasks:
        cpx = t.get("complexity", "?")
        by_cpx.setdefault(cpx, []).append(t)
    if by_cpx:
        print("按复杂度:")
        for cpx in sorted(by_cpx.keys()):
            ts = by_cpx[cpx]
            avg_d = sum(t.get("total_duration_sec", 0) for t in ts) / len(ts)
            avg_c = sum(t.get("total_cost_usd", 0) or 0 for t in ts) / len(ts)
            print(f"  {cpx}: {fmt_duration(int(avg_d))}/任务, ${avg_c:.2f}  ({len(ts)} 个)")
        print()

    # 按阶段耗时占比
    by_phase = {}
    for s in sessions:
        p = s.get("phase", "?")
        by_phase[p] = by_phase.get(p, 0) + s.get("duration_sec", 0)
    if by_phase and total_dur > 0:
        print("按阶段耗时占比:")
        for p, dur in sorted(by_phase.items(), key=lambda x: -x[1]):
            pct = dur * 100 // total_dur
            bar = progress_bar(dur, total_dur)
            print(f"  {p:<20} {pct:>3}% {bar}")

elif mode == "task":
    tid = int(task_id)
    task_info = None
    for t in tasks:
        if t.get("id") == tid:
            task_info = t
            break
    if not task_info:
        print(f"❌ 找不到任务 #{tid}")
        sys.exit(1)
    print(f"📋 任务 #{tid}: {task_info.get('name', '')}")
    print("━" * 40)
    print(f"复杂度: {task_info.get('complexity', '?')}")
    print(f"路径: {task_info.get('path', '?')}")
    print(f"总 Session: {task_info.get('total_sessions', 0)}")
    print(f"总耗时: {fmt_duration(task_info.get('total_duration_sec', 0))}")
    print(f"总成本: ${task_info.get('total_cost_usd', 0) or 0:.2f}")
    print(f"VERIFY 重试: {task_info.get('verify_attempts', 0)}")
    print(f"首次通过: {'✅' if task_info.get('verify_pass_first_try') else '❌'}")
    print(f"开始: {task_info.get('started_at', '-')}")
    print(f"完成: {task_info.get('completed_at', '-')}")
    print()
    # 该任务的 sessions
    task_sessions = [s for s in sessions if s.get("task_id") == tid]
    if task_sessions:
        print("Sessions:")
        for s in task_sessions:
            exit_icon = "✅" if s.get("exit_code", 1) == 0 else "❌"
            cost = f"${s['cost_usd']:.2f}" if s.get("cost_usd") else "-"
            print(f"  {exit_icon} {s.get('phase', '?'):<20} {fmt_duration(s.get('duration_sec', 0)):>6}  {cost}")

elif mode == "cost":
    if not tasks:
        print("暂无任务数据")
        sys.exit(0)
    print("💰 按成本排序")
    print("━" * 40)
    sorted_tasks = sorted(tasks, key=lambda t: t.get("total_cost_usd", 0) or 0, reverse=True)
    for t in sorted_tasks:
        cost = t.get("total_cost_usd", 0) or 0
        print(f"  #{t['id']:<4} ${cost:>6.2f}  {fmt_duration(t.get('total_duration_sec', 0)):>6}  {t.get('name', '')}")

elif mode == "sessions":
    if not sessions:
        print("暂无 session 数据")
        sys.exit(0)
    print("📋 全部 Sessions")
    print("━" * 50)
    for s in sessions:
        exit_icon = "✅" if s.get("exit_code", 1) == 0 else "❌"
        cost = f"${s['cost_usd']:.2f}" if s.get("cost_usd") else "   -"
        print(f"  {exit_icon} #{s.get('task_id', '?'):<4} {s.get('phase', '?'):<20} {fmt_duration(s.get('duration_sec', 0)):>6}  {cost}  {s.get('started_at', '')[:16]}")
PYEOF
```

**Step 2: 测试**

```bash
chmod +x loop/stats.sh
bash loop/stats.sh
```

Expected: 如果 stats.json 不存在，显示提示信息。

**Step 3: Commit**

```bash
git add loop/stats.sh
git commit -m "feat(loop): 运行统计查看命令 stats.sh"
```

---

## Task 6: dev-loop.sh 埋点写入 stats.json

**Files:**
- Modify: `loop/dev-loop.sh:36-38` (配置区: 新增 STATS_FILE)
- Modify: `loop/dev-loop.sh:1518-1554` (会话前后: 埋点)
- Modify: `loop/dev-loop.sh:975` (mark_task_done: 汇总)

**Step 1: 新增 STATS_FILE 配置**

在 `dev-loop.sh` 第 38 行 `LOCK_DIR=...` 之后追加：

```bash
STATS_FILE="${MAIN_REPO_DIR}/loop/stats.json"
```

**Step 2: 会话开始前记录 session**

在第 1519 行 `session_start=$(date +%s)` 之后追加：

```bash
  # 埋点：记录 session 开始
  task_id_for_stats=$(read_field current_item_id 2>/dev/null || echo "")
  task_name_for_stats=$(python3 -c "
import json
with open('$STATE_FILE') as f:
    s = json.load(f)
tid = s.get('current_item_id')
for q in s.get('queue', []):
    if q.get('id') == tid:
        print(q.get('name', '')); break
" 2>/dev/null || echo "")
  python3 -c "
import json, os
from datetime import datetime, timezone
sf = '${STATS_FILE}'
data = {'sessions': [], 'tasks': []}
if os.path.exists(sf):
    with open(sf) as f:
        data = json.load(f)
data['sessions'].append({
    'task_id': ${task_id_for_stats:-0},
    'task_name': '${task_name_for_stats}',
    'phase': '${phase}',
    'started_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z'),
    'ended_at': None,
    'duration_sec': 0,
    'exit_code': None,
    'cost_usd': None,
    'worker_id': $( [ -n \"$SINGLE_TASK\" ] && echo \"$SINGLE_TASK\" || echo 'None' ),
    'attempt': ${consecutive_failures:-0} + 1,
})
with open(sf, 'w') as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
    f.write('\n')
" 2>/dev/null || true
```

**Step 3: 会话结束后更新 session**

在第 1554 行 `log "会话结束，耗时..."` 之后追加：

```bash
  # 埋点：更新 session 结束数据
  python3 -c "
import json, os
from datetime import datetime, timezone
sf = '${STATS_FILE}'
if not os.path.exists(sf):
    raise SystemExit(0)
with open(sf) as f:
    data = json.load(f)
if data['sessions']:
    last = data['sessions'][-1]
    last['ended_at'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')
    last['duration_sec'] = ${session_duration}
    last['exit_code'] = ${claude_exit}
    with open(sf, 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write('\n')
" 2>/dev/null || true
```

**Step 4: verbose 模式解析 cost**

在 verbose 模式的 jq 解析块（第 1530-1538 行）之后，追加 cost 提取逻辑：

```bash
  # verbose 模式：从 session log 提取 cost
  if [ "$VERBOSE" = true ] && [ -f "$session_log" ]; then
    cost_usd=$(grep '"total_cost_usd"' "$session_log" | tail -1 | python3 -c "
import sys, json
for line in sys.stdin:
    try:
        obj = json.loads(line.strip())
        cost = obj.get('total_cost_usd')
        if cost is not None:
            print(cost)
    except: pass
" 2>/dev/null || echo "")
    if [ -n "$cost_usd" ]; then
      python3 -c "
import json, os
sf = '${STATS_FILE}'
if os.path.exists(sf):
    with open(sf) as f:
        data = json.load(f)
    if data['sessions']:
        data['sessions'][-1]['cost_usd'] = float('${cost_usd}')
        with open(sf, 'w') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write('\n')
" 2>/dev/null || true
    fi
  fi
```

**Step 5: mark_task_done 中汇总任务统计**

在 `mark_task_done()` 函数末尾（第 1101 行 `print(f"✅ 任务 #{task_id}...")` 之后），追加：

```python
# 汇总到 stats.json tasks[]
stats_file = os.path.join(main_repo, "loop", "stats.json")
if os.path.exists(stats_file):
    try:
        with open(stats_file) as sf:
            stats_data = json.load(sf)
        task_sessions = [s for s in stats_data.get("sessions", []) if s.get("task_id") == task_id]
        total_dur = sum(s.get("duration_sec", 0) for s in task_sessions)
        total_cost = sum(s.get("cost_usd", 0) or 0 for s in task_sessions)
        verify_sessions = [s for s in task_sessions if s.get("phase") == "VERIFY"]
        verify_attempts = len(verify_sessions)
        verify_first = verify_attempts <= 1 and all(s.get("exit_code", 1) == 0 for s in verify_sessions[:1])
        merge_fix_sessions = [s for s in task_sessions if s.get("phase") == "MERGE_FIX"]

        task_stat = {
            "id": task_id,
            "name": task_info.get("name", ""),
            "complexity": task_info.get("complexity", "?"),
            "path": task_sessions[0].get("phase", "?") if task_sessions else "?",
            "total_sessions": len(task_sessions),
            "total_duration_sec": total_dur,
            "total_cost_usd": round(total_cost, 2),
            "verify_attempts": verify_attempts,
            "verify_pass_first_try": verify_first,
            "merge_fix_attempts": len(merge_fix_sessions),
            "started_at": task_sessions[0].get("started_at") if task_sessions else None,
            "completed_at": now_iso,
        }
        # 更新或追加
        stats_data.setdefault("tasks", [])
        stats_data["tasks"] = [t for t in stats_data["tasks"] if t.get("id") != task_id]
        stats_data["tasks"].append(task_stat)
        with open(stats_file, "w") as sf:
            json.dump(stats_data, sf, indent=2, ensure_ascii=False)
            sf.write("\n")
    except Exception as e:
        print(f"⚠️ stats 汇总失败: {e}")
```

**Step 6: Commit**

```bash
git add loop/dev-loop.sh
git commit -m "feat(loop): dev-loop.sh 埋点写入 stats.json"
```

---

## Task 7: notify.sh — 统一通知

**Files:**
- Create: `loop/notify.sh`

**Step 1: 创建 notify.sh**

```bash
#!/usr/bin/env bash
set -euo pipefail

# ── 参数解析 ──
LEVEL="info"
TITLE=""
BODY=""
for arg in "$@"; do
  case "$arg" in
    --level=*) LEVEL="${arg#*=}" ;;
    --title=*) TITLE="${arg#*=}" ;;
    --body=*) BODY="${arg#*=}" ;;
    --level|--title|--body) ;; # 值在下一个 arg
    *)
      case "${prev_arg:-}" in
        --level) LEVEL="$arg" ;;
        --title) TITLE="$arg" ;;
        --body) BODY="$arg" ;;
      esac
      ;;
  esac
  prev_arg="$arg"
done

if [ -z "$TITLE" ] || [ -z "$BODY" ]; then
  echo "用法: notify.sh --level info|warn|critical --title '标题' --body '内容'" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"

# 加载配置
LOOP_FEISHU_WEBHOOK=""
LOOP_NOTIFY_LEVEL="info"
if [ -f "$ENV_FILE" ]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE"
fi

# 级别权重
level_weight() {
  case "$1" in
    debug) echo 0 ;; info) echo 1 ;; warn) echo 2 ;; critical) echo 3 ;; *) echo 1 ;;
  esac
}

current_weight=$(level_weight "$LEVEL")
threshold_weight=$(level_weight "$LOOP_NOTIFY_LEVEL")

# ── 渠道 1: 终端 Bell（始终） ──
printf '\a'

# ── 渠道 2: macOS 系统通知（始终，macOS only） ──
if command -v osascript >/dev/null 2>&1; then
  # 转义单引号
  escaped_body=$(echo "$BODY" | sed "s/'/\\\\'/g")
  escaped_title=$(echo "$TITLE" | sed "s/'/\\\\'/g")
  osascript -e "display notification '${escaped_body}' with title 'Dev Loop: ${escaped_title}'" 2>/dev/null || true
fi

# ── 渠道 3: 飞书 Webhook（可选，需配置） ──
if [ -n "$LOOP_FEISHU_WEBHOOK" ] && [ "$current_weight" -ge "$threshold_weight" ]; then
  # 颜色映射
  case "$LEVEL" in
    info) color="green" ;; warn) color="orange" ;; critical) color="red" ;; *) color="grey" ;;
  esac

  # 构建飞书卡片消息
  card_json=$(python3 -c "
import json
from datetime import datetime
card = {
    'msg_type': 'interactive',
    'card': {
        'header': {
            'title': {'tag': 'plain_text', 'content': 'Dev Loop: ${TITLE}'},
            'template': '${color}'
        },
        'elements': [
            {'tag': 'div', 'text': {'tag': 'plain_text', 'content': '${BODY}'}},
            {'tag': 'note', 'elements': [
                {'tag': 'plain_text', 'content': f'级别: ${LEVEL} | {datetime.now().strftime(\"%Y-%m-%d %H:%M:%S\")}'}
            ]}
        ]
    }
}
print(json.dumps(card, ensure_ascii=False))
" 2>/dev/null)

  if [ -n "$card_json" ]; then
    curl -s -X POST "$LOOP_FEISHU_WEBHOOK" \
      -H 'Content-Type: application/json' \
      -d "$card_json" >/dev/null 2>&1 || true
  fi
fi
```

**Step 2: 测试**

```bash
chmod +x loop/notify.sh
bash loop/notify.sh --level info --title "测试" --body "通知系统测试"
```

Expected: macOS 弹出系统通知。

**Step 3: Commit**

```bash
git add loop/notify.sh
git commit -m "feat(loop): 统一通知入口 notify.sh"
```

---

## Task 8: dev-loop.sh 集成通知

**Files:**
- Modify: `loop/dev-loop.sh` (多处插入 notify.sh 调用)

**Step 1: 定义 notify 辅助函数**

在 `dev-loop.sh` 的辅助函数区（第 40 行 `# ── 辅助函数 ──` 后）追加：

```bash
# 异步通知（不阻塞主流程）
notify() {
  local level="$1" title="$2" body="$3"
  bash "${MAIN_REPO_DIR}/loop/notify.sh" --level "$level" --title "$title" --body "$body" &
}
```

**Step 2: 在关键事件点调用**

在以下位置插入 notify 调用：

**a) 会话失败时**（第 1588 行 `log "⚠️ 会话异常退出"` 之后）：

```bash
    notify warn "会话失败" "⚠️ #${task_id_for_stats} ${phase} 失败 (exit ${claude_exit}), 第 ${consecutive_failures}/${MAX_CONSECUTIVE_FAILURES} 次"
```

**b) 连续失败停止时**（第 1668 行 `log "❌ 连续..."` 之后）：

```bash
    notify critical "Loop 停止" "🛑 #${task_id_for_stats} 连续 ${MAX_CONSECUTIVE_FAILURES} 次失败"
```

**c) 任务完成时**（第 1639 行 `mark_task_done` 之后）：

```bash
      notify info "任务完成" "✅ #${current_item} 完成"
```

**d) 并行模式 — 全部任务完成时**（第 1339 行 `log "🎉 所有任务完成！"` 之后）：

```bash
        notify info "全部完成" "🎉 所有任务完成！"
```

**e) 并行模式 — Worker 异常退出**（第 1228 行 `log "⚠️ Worker..."` 之后）：

```bash
          notify warn "Worker 异常" "⚠️ Worker #${task_id}（${task_name}）异常退出"
```

**Step 3: Commit**

```bash
git add loop/dev-loop.sh
git commit -m "feat(loop): dev-loop.sh 集成通知推送"
```

---

## Task 9: 智能失败恢复 — 失败分类函数

**Files:**
- Modify: `loop/dev-loop.sh` (辅助函数区 + 主循环失败处理)

**Step 1: 新增配置项**

在 `dev-loop.sh` 配置区（第 36 行 `MAX_CONSECUTIVE_FAILURES=3` 之后）追加：

```bash
MAX_RETRIES_GATE=3
MAX_RETRIES_NETWORK=2
MAX_RETRIES_CONTEXT=1
SKIP_ON_UNKNOWN=true
```

**Step 2: 新增失败分类函数**

在辅助函数区追加：

```bash
# 分析 session log 判定失败类型
# 输出: gate|context|permission|conflict|network|unknown
classify_failure() {
  local log_file="$1"
  [ -f "$log_file" ] || { echo "unknown"; return; }
  local tail_content
  tail_content=$(tail -50 "$log_file" 2>/dev/null || true)

  if echo "$tail_content" | grep -qi "context.window\|token.limit\|max.turns\|conversation is too long"; then
    echo "context"
  elif echo "$tail_content" | grep -qi "permission.denied\|permission.blocked\|not.allowed\|dangerously-skip"; then
    echo "permission"
  elif echo "$tail_content" | grep -qi "CONFLICT\|merge.conflict"; then
    echo "conflict"
  elif echo "$tail_content" | grep -qi "ETIMEDOUT\|ECONNREFUSED\|rate.limit\|hit.your.limit"; then
    echo "network"
  elif echo "$tail_content" | grep -qi "npm.run.lint\|npm.test\|npm.run.build\|exit.code.*[1-9]"; then
    echo "gate"
  else
    echo "unknown"
  fi
}

# 获取失败类型对应的最大重试次数
max_retries_for() {
  case "$1" in
    gate) echo "$MAX_RETRIES_GATE" ;;
    network) echo "$MAX_RETRIES_NETWORK" ;;
    context) echo "$MAX_RETRIES_CONTEXT" ;;
    conflict) echo 2 ;;
    permission) echo 1 ;;
    unknown) echo 0 ;;
    *) echo 0 ;;
  esac
}

# 获取退避等待时间（秒）
backoff_seconds() {
  local failure_type="$1" attempt="$2"
  case "$failure_type" in
    network) echo 60 ;;
    *)
      case "$attempt" in
        1) echo 0 ;; 2) echo 30 ;; *) echo 60 ;;
      esac
      ;;
  esac
}
```

**Step 3: 替换主循环的失败处理逻辑**

替换第 1587-1671 行的失败处理段（从 `if [ "$claude_exit" -ne 0 ]` 到 `安全阀` 结束）为新的智能恢复逻辑：

```bash
  # ── 智能失败恢复 ──
  new_phase=$(read_phase)
  if [ "$new_phase" != "$phase" ]; then
    # Phase 推进成功
    log "Phase 推进: ${phase} → ${new_phase}"
    consecutive_failures=0
    retry_hint=""

    # 功能完成后标记任务文件 + 清理 worktree
    if [ "$phase" = "MERGE" ]; then
      current_item=$(read_field current_item_id 2>/dev/null || true)
      if [ -n "$current_item" ]; then
        mark_task_done "$current_item"
        notify info "任务完成" "✅ #${current_item} 完成"
      fi
    fi
    if [ "$phase" = "MERGE" ] && [ -n "$current_wt_slug" ]; then
      cleanup_worktree "$current_wt_slug"
      current_wt_path=""
      current_wt_slug=""
    fi
  else
    # Phase 未推进 = 失败
    consecutive_failures=$((consecutive_failures + 1))

    # 分类失败
    failure_type=$(classify_failure "$session_log")
    max_retry=$(max_retries_for "$failure_type")
    log "⚠️ Phase 未变化（${phase}），失败类型: ${failure_type}，第 ${consecutive_failures}/${max_retry} 次"

    if [ "$consecutive_failures" -le "$max_retry" ]; then
      # 可重试
      wait_sec=$(backoff_seconds "$failure_type" "$consecutive_failures")
      if [ "$wait_sec" -gt 0 ]; then
        log "⏳ 退避等待 ${wait_sec}s..."
        sleep "$wait_sec"
      fi

      # 构建重试提示
      last_error=$(tail -10 "$session_log" 2>/dev/null | head -c 500 || echo "无法读取日志")
      retry_hint="⚠️ 上次尝试失败 (第 ${consecutive_failures}/${max_retry} 次)。
失败类型: ${failure_type}
错误摘要:
${last_error}

请避免相同错误，调整策略后重试。"

      # 特殊处理：上下文耗尽 → 清空进度，断点续传
      if [ "$failure_type" = "context" ]; then
        log "🔄 上下文耗尽，清空进度断点续传..."
        python3 -c "
import json
with open('$STATE_FILE') as f:
    state = json.load(f)
state['implement_progress'] = {'current_chunk': 0, 'current_task': 0, 'last_committed_task': None, 'last_commit_sha': None, 'current_step_attempts': 0}
with open('$STATE_FILE', 'w') as f:
    json.dump(state, f, ensure_ascii=False, indent=2)
    f.write('\n')
" 2>/dev/null || true
      fi

      notify warn "会话失败" "⚠️ #${task_id_for_stats:-?} ${phase} (${failure_type}), 重试 ${consecutive_failures}/${max_retry}"
    else
      # 不可重试或重试次数用完
      log "❌ 失败类型 ${failure_type} 已达最大重试次数"

      if [ "$SKIP_ON_UNKNOWN" = true ] || [ "$failure_type" != "unknown" ]; then
        # 跳过：标记 blocked，继续下一个任务
        log "⏭️ 跳过当前任务，标记为 blocked"
        python3 -c "
import json
from datetime import datetime, timezone
with open('$STATE_FILE') as f:
    state = json.load(f)
item_id = state.get('current_item_id')
for q in state.get('queue', []):
    if q.get('id') == item_id:
        q['status'] = 'blocked'
        state.setdefault('blocked', []).append({'id': item_id, 'name': q.get('name', ''), 'reason': '${failure_type}'})
        break
# 传播 blocked 给下游依赖
blocked_ids = {b['id'] for b in state.get('blocked', [])}
changed = True
while changed:
    changed = False
    for q in state.get('queue', []):
        if q.get('status') == 'pending':
            for dep in q.get('depends_on', []):
                if dep in blocked_ids:
                    q['status'] = 'blocked'
                    state.setdefault('blocked', []).append({'id': q['id'], 'name': q.get('name', '')})
                    blocked_ids.add(q['id'])
                    changed = True
                    break
# 找下一个 pending 任务
next_found = False
completed_ids = {c['id'] for c in state.get('completed', [])}
for q in state.get('queue', []):
    if q.get('status') == 'pending':
        deps = q.get('depends_on', [])
        if all(d in completed_ids for d in deps):
            state['current_item_id'] = q['id']
            state['current_phase'] = 'DESIGN_IMPLEMENT'
            q['status'] = 'in_progress'
            next_found = True
            break
if not next_found:
    state['current_phase'] = 'AWAITING_HUMAN_REVIEW'
state['branch'] = None
state['spec_path'] = None
state['plan_path'] = None
state['implement_progress'] = {'current_chunk': 0, 'current_task': 0, 'last_committed_task': None, 'last_commit_sha': None, 'current_step_attempts': 0}
state['updated_at'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')
with open('$STATE_FILE', 'w') as f:
    json.dump(state, f, ensure_ascii=False, indent=2)
    f.write('\n')
" 2>/dev/null || true

        # 清理 worktree
        if [ -n "$current_wt_slug" ]; then
          cleanup_worktree "$current_wt_slug"
          current_wt_path=""
          current_wt_slug=""
        fi

        consecutive_failures=0
        retry_hint=""
        notify warn "任务跳过" "⏭️ #${task_id_for_stats:-?} 已跳过 (${failure_type})"
      else
        # 不可跳过：停止 loop
        notify critical "Loop 停止" "🛑 #${task_id_for_stats:-?} ${failure_type} 不可恢复"
        log "最后的会话日志: $session_log"
        break
      fi
    fi
  fi
```

注意：这段替换了原来的 `if [ "$claude_exit" -ne 0 ]` 到 `if [ "$consecutive_failures" -ge "$MAX_CONSECUTIVE_FAILURES" ]` 整个块。需要保留限流处理逻辑（在 classify_failure 之前先检查 rate_limited）。

**Step 4: 保留限流处理**

在新的失败恢复逻辑之前（`new_phase=$(read_phase)` 之前），保留现有的限流检查逻辑（第 1556-1621 行），限流走 continue 不进入失败分类。

**Step 5: 测试**

```bash
# 验证语法
bash -n loop/dev-loop.sh
echo $?
```

Expected: 0（无语法错误）

**Step 6: Commit**

```bash
git add loop/dev-loop.sh
git commit -m "feat(loop): 智能失败恢复 — 6类失败分级处理 + 降级/跳过"
```

---

## Task 10: add-task.sh — 任务快捷入队

**Files:**
- Create: `loop/add-task.sh`

**Step 1: 创建 add-task.sh**

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INBOX_DIR="${SCRIPT_DIR}/inbox"
TASKS_DIR="${SCRIPT_DIR}/tasks"

# ── 参数解析 ──
DESCRIPTION=""
TYPE="feature"
PRIORITY="medium"
FROM_ISSUE=""
BATCH_FILE=""

for arg in "$@"; do
  case "$arg" in
    --type=*) TYPE="${arg#*=}" ;;
    --priority=*) PRIORITY="${arg#*=}" ;;
    --from-issue=*) FROM_ISSUE="${arg#*=}" ;;
    --from-issue) ;; # 值在下一个 arg
    --batch=*) BATCH_FILE="${arg#*=}" ;;
    --batch) ;; # 值在下一个 arg
    --type|--priority) ;; # 值在下一个 arg
    -h|--help)
      echo "用法: add-task.sh [描述] [选项]"
      echo ""
      echo "选项:"
      echo "  --type=feat|bug|refactor    任务类型 (默认: feature)"
      echo "  --priority=high|medium|low  优先级 (默认: medium)"
      echo "  --from-issue=<number>       从 GitHub Issue 导入"
      echo "  --batch=<file>              批量导入（一行一个描述）"
      echo ""
      echo "示例:"
      echo "  add-task.sh '支持自定义热力图半径'"
      echo "  add-task.sh '修复登录bug' --type bug --priority high"
      echo "  add-task.sh --from-issue 42"
      exit 0
      ;;
    *)
      case "${prev_arg:-}" in
        --type) TYPE="$arg" ;;
        --priority) PRIORITY="$arg" ;;
        --from-issue) FROM_ISSUE="$arg" ;;
        --batch) BATCH_FILE="$arg" ;;
        *) [ -z "$DESCRIPTION" ] && DESCRIPTION="$arg" ;;
      esac
      ;;
  esac
  prev_arg="$arg"
done

# 类型别名标准化
case "$TYPE" in
  feat|feature) TYPE="feature" ;;
  bug|fix) TYPE="bug" ;;
  refactor) TYPE="refactor" ;;
esac

# ── 生成单个任务文件 ──
create_task() {
  local desc="$1" task_type="$2" task_priority="$3"

  # 计算下一个 ID
  local max_id=0
  for f in "${TASKS_DIR}"/*.md "${INBOX_DIR}"/*.md; do
    [ -f "$f" ] || continue
    local fid
    fid=$(basename "$f" | grep -o '^[0-9]\+' || true)
    if [ -n "$fid" ] && [ "$fid" -gt "$max_id" ]; then
      max_id=$fid
    fi
  done
  local next_id=$((max_id + 1))

  # 生成 slug
  local slug
  slug=$(python3 -c "
import re
desc = '''${desc}'''
# 中文转拼音风格 slug（简单截取）
slug = re.sub(r'[^\w\u4e00-\u9fff-]', '-', desc.lower())
slug = re.sub(r'-+', '-', slug).strip('-')[:40]
print(slug)
" 2>/dev/null || echo "task-${next_id}")

  local timestamp
  timestamp=$(date +%m-%d_%H-%M)
  local created
  created=$(date +"%Y-%m-%d %H:%M")
  local filename="${next_id}-${slug}_${timestamp}.md"

  mkdir -p "$INBOX_DIR"
  cat > "${INBOX_DIR}/${filename}" <<EOF
---
created_at: "${created}"
type: ${task_type}
priority: ${task_priority}
status: open
---
# ${desc}

## 需求描述
${desc}
EOF

  echo "✅ 任务 #${next_id} 已入队 → inbox/${filename}"
}

# ── 从 GitHub Issue 导入 ──
import_from_issue() {
  local issue_num="$1"
  if ! command -v gh >/dev/null 2>&1; then
    echo "❌ 需要安装 gh CLI: brew install gh" >&2
    exit 1
  fi

  local issue_json
  issue_json=$(gh issue view "$issue_num" --json title,body,labels 2>/dev/null)
  if [ -z "$issue_json" ]; then
    echo "❌ 无法获取 Issue #${issue_num}" >&2
    exit 1
  fi

  local title body issue_type
  title=$(echo "$issue_json" | python3 -c "import json,sys; print(json.load(sys.stdin)['title'])")
  body=$(echo "$issue_json" | python3 -c "import json,sys; print(json.load(sys.stdin).get('body', '') or '')")
  issue_type=$(echo "$issue_json" | python3 -c "
import json, sys
labels = [l['name'].lower() for l in json.load(sys.stdin).get('labels', [])]
if 'bug' in labels: print('bug')
elif 'enhancement' in labels or 'feature' in labels: print('feature')
else: print('feature')
")

  # 用 Issue body 替换默认描述
  local max_id=0
  for f in "${TASKS_DIR}"/*.md "${INBOX_DIR}"/*.md; do
    [ -f "$f" ] || continue
    local fid
    fid=$(basename "$f" | grep -o '^[0-9]\+' || true)
    if [ -n "$fid" ] && [ "$fid" -gt "$max_id" ]; then
      max_id=$fid
    fi
  done
  local next_id=$((max_id + 1))
  local slug
  slug=$(python3 -c "
import re
slug = re.sub(r'[^\w-]', '-', '''${title}'''.lower())[:40].strip('-')
print(slug)
" 2>/dev/null || echo "issue-${issue_num}")

  local timestamp
  timestamp=$(date +%m-%d_%H-%M)
  local created
  created=$(date +"%Y-%m-%d %H:%M")
  local filename="${next_id}-${slug}_${timestamp}.md"

  mkdir -p "$INBOX_DIR"
  cat > "${INBOX_DIR}/${filename}" <<EOF
---
created_at: "${created}"
type: ${issue_type}
priority: medium
status: open
source: "github#${issue_num}"
---
# ${title}

## 需求描述
${body:-${title}}
EOF

  echo "✅ 从 Issue #${issue_num} 导入 → 任务 #${next_id} inbox/${filename}"
}

# ── 入口 ──

# GitHub Issue 导入
if [ -n "$FROM_ISSUE" ]; then
  import_from_issue "$FROM_ISSUE"
  exit 0
fi

# 批量导入
if [ -n "$BATCH_FILE" ]; then
  if [ ! -f "$BATCH_FILE" ]; then
    echo "❌ 文件不存在: $BATCH_FILE" >&2
    exit 1
  fi
  count=0
  while IFS= read -r line; do
    line=$(echo "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    [ -z "$line" ] && continue
    [ "${line:0:1}" = "#" ] && continue
    create_task "$line" "$TYPE" "$PRIORITY"
    count=$((count + 1))
  done < "$BATCH_FILE"
  echo "📦 批量导入完成: ${count} 个任务"
  exit 0
fi

# 交互模式（无参数）
if [ -z "$DESCRIPTION" ]; then
  printf "📝 任务描述: "
  read -r DESCRIPTION
  if [ -z "$DESCRIPTION" ]; then
    echo "❌ 描述不能为空" >&2
    exit 1
  fi
  printf "📦 类型 (feature/bug/refactor) [feature]: "
  read -r input_type
  [ -n "$input_type" ] && TYPE="$input_type"
  printf "🔺 优先级 (high/medium/low) [medium]: "
  read -r input_priority
  [ -n "$input_priority" ] && PRIORITY="$input_priority"
fi

create_task "$DESCRIPTION" "$TYPE" "$PRIORITY"
```

**Step 2: 测试**

```bash
chmod +x loop/add-task.sh
bash loop/add-task.sh --help
bash loop/add-task.sh "测试任务入队"
ls loop/inbox/
```

Expected: 显示帮助信息；创建任务文件到 inbox/。

**Step 3: 清理测试文件**

```bash
rm -f loop/inbox/*测试*
```

**Step 4: Commit**

```bash
git add loop/add-task.sh
git commit -m "feat(loop): 任务快捷入队 CLI add-task.sh"
```

---

## Task 11: 最终验证 + .gitignore 确认

**Step 1: 验证所有新脚本可执行**

```bash
ls -la loop/dashboard.sh loop/generate-progress.sh loop/stats.sh loop/notify.sh loop/add-task.sh
```

Expected: 全部有 x 权限。

**Step 2: 验证 dev-loop.sh 语法**

```bash
bash -n loop/dev-loop.sh && echo "✅ 语法正确"
```

**Step 3: 验证 .gitignore**

```bash
grep -E 'stats\.json|PROGRESS\.md|loop/\.env' .gitignore
```

Expected: 三行都存在。

**Step 4: 验证文件结构**

```bash
ls loop/*.sh
```

Expected: dev-loop.sh, dashboard.sh, generate-progress.sh, stats.sh, notify.sh, add-task.sh

**Step 5: 最终 Commit（如有遗漏）**

```bash
git status
# 如有未提交的改动：
git add -A && git commit -m "chore(loop): v4 升级完成 — 可视化/统计/通知/恢复/入队"
```
