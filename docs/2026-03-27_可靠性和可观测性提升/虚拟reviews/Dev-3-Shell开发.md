# Dev-3 评审：Shell 脚本与 CLI 设计

> 评审人：Dev-3（Shell/DevOps 工程师）
> 评审对象：openNiuMa 合并升级设计（2026-03-27-loop-v4-merged-design.md）
> 评审重点：Bash 脚本质量、git worktree 使用方式、进程生命周期管理、CLI 设计合理性

---

## 🔴 严重问题

### S1. `bash -c "$hook"` 存在注入和引用丢失风险

**问题描述：** `ensure_worktree()` 中通过 `bash -c "$hook"` 执行从 YAML 读取的多行 hook 脚本，hook 内容经 Python 输出到 stdout 再由 bash 变量捕获。这条链路中任何特殊字符（`$`、`` ` ``、`\`、`'`、`"`）都可能被二次展开。

**技术影响：**
- hook 中的 `$SLUG` 和 `$MAIN_REPO` 在 `bash -c` 执行时展开，这是预期行为，但 hook 中如果包含其他 `$VAR`（如 `$DB_NAME`）在赋值前就可能被外层 shell 展开为空。
- 更严重的是，如果 `python3 config.py get-hook` 的输出被 `$()` 捕获到变量中，多行脚本中的换行会保留，但如果变量没有双引号保护，就会被 word splitting 破坏。
- workflow.yaml 中 hook 内容如果包含单引号（如 `sed -i '' ...`），经过 Python stdout → bash 变量 → `bash -c` 的链路后，引号配对可能错乱。

**建议修改：** 将 hook 内容写到临时文件再执行，避免引号嵌套问题：

```bash
ensure_worktree() {
  local slug="$1" dev_branch="$2"
  local wt_path="${WORKTREE_BASE_DIR}/${WORKTREE_PREFIX}-${slug}"
  if [ -d "$wt_path" ]; then echo "$wt_path"; return 0; fi

  mkdir -p "$WORKTREE_BASE_DIR"
  git -C "$MAIN_REPO_DIR" worktree add --detach "$wt_path" "$dev_branch" || return 1

  local hook_file
  hook_file=$(mktemp "${TMPDIR:-/tmp}/niuma-hook.XXXXXX")
  trap "rm -f '$hook_file'" RETURN  # 函数返回时清理

  python3 "${MAIN_REPO_DIR}/loop/lib/config.py" get-hook after_create \
    "${MAIN_REPO_DIR}/loop/workflow.yaml" > "$hook_file"

  if [ -s "$hook_file" ]; then
    local timeout
    timeout=$(python3 "${MAIN_REPO_DIR}/loop/lib/config.py" get-value \
      hooks.timeout_sec "${MAIN_REPO_DIR}/loop/workflow.yaml")
    ( cd "$wt_path" && export SLUG="$slug" MAIN_REPO="$MAIN_REPO_DIR"
      timeout "${timeout:-120}" bash "$hook_file" ) || {
      log "after_create hook failed"
      rm -rf "$wt_path"
      git -C "$MAIN_REPO_DIR" worktree prune
      return 1
    }
  fi
  echo "$wt_path"
}
```

---

### S2. `cleanup_worktree()` 中 `rm -rf` 过于危险

**问题描述：** `git worktree remove --force` 失败后直接 `rm -rf "$wt_path"`。如果 `$wt_path` 变量因为某种原因为空或异常（比如 `WORKTREE_BASE_DIR` 未设置），`rm -rf` 可能删除非预期目录。

**技术影响：** 在 `set -u` 下空变量会报错退出，但如果 `WORKTREE_PREFIX` 为空而 `WORKTREE_BASE_DIR` 有效，`wt_path` 会变成 `/.trees/-<slug>` 这样的异常路径。虽然不太可能删到根目录，但在 worktree 内有未提交变更时，`--force` + `rm -rf` 会静默丢失工作内容，且无恢复手段。

**建议修改：**

```bash
cleanup_worktree() {
  local slug="$1"
  local wt_path="${WORKTREE_BASE_DIR}/${WORKTREE_PREFIX}-${slug}"

  # 安全检查：路径必须在预期的 base_dir 下
  [[ "$wt_path" == "${WORKTREE_BASE_DIR}/"* ]] || {
    log "ERROR: worktree path '$wt_path' outside base dir, refusing cleanup"
    return 1
  }
  [ -d "$wt_path" ] || return 0

  # before_remove hook（允许失败）
  local hook_file
  hook_file=$(mktemp "${TMPDIR:-/tmp}/niuma-hook.XXXXXX")
  python3 "${MAIN_REPO_DIR}/loop/lib/config.py" get-hook before_remove \
    "${MAIN_REPO_DIR}/loop/workflow.yaml" > "$hook_file" 2>/dev/null
  if [ -s "$hook_file" ]; then
    ( cd "$wt_path" && export SLUG="$slug" MAIN_REPO="$MAIN_REPO_DIR"
      bash "$hook_file" ) 2>/dev/null || true
  fi
  rm -f "$hook_file"

  # 先尝试安全移除，再强制
  git -C "$MAIN_REPO_DIR" worktree remove "$wt_path" 2>/dev/null || \
  git -C "$MAIN_REPO_DIR" worktree remove "$wt_path" --force 2>/dev/null || {
    log "WARN: git worktree remove failed, falling back to rm -rf"
    rm -rf "$wt_path"
    git -C "$MAIN_REPO_DIR" worktree prune
  }
}
```

---

### S3. init.sh 中 DETECT_RESULT 多次管道解析 JSON 极其脆弱

**问题描述：** `init.sh` 对 `detect.py` 的输出解析方式是将 JSON 存入变量，然后 6 次 `echo "$DETECT_RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin)['key'])"` 提取各字段。

**技术影响：**
- 6 次 Python 启动 = 6 次进程创建开销，每次约 50-80ms（macOS），总计约 0.5s 纯开销。
- 如果 `detect.py` 输出中包含非 ASCII 字符（如中文项目名），`echo` 在某些 locale 下可能出问题。
- 如果 `detect.py` 的 JSON 输出跨多行（pretty-print），管道传递可能被 shell 截断。
- 任何一次解析失败都会导致后续变量为空，但 `set -e` 已经开着，`python3 -c` 失败会直接退出脚本，用户看到的错误信息是 Python traceback 而非友好提示。

**建议修改：** 一次 Python 调用提取全部字段，使用 `eval` 或 `read`：

```bash
# 方案 A：python 输出 shell 赋值语句
eval "$(python3 "${LOOP_DIR}/lib/detect.py" --shell-export "$REPO_DIR")"
# detect.py --shell-export 输出：
# STACK='node'
# GATE='npm run lint && npm test'
# AFTER_CREATE='...'
# ...

# 方案 B：如果不想 eval，用临时文件
DETECT_FILE=$(mktemp)
python3 "${LOOP_DIR}/lib/detect.py" "$REPO_DIR" > "$DETECT_FILE"
STACK=$(python3 -c "import json; d=json.load(open('$DETECT_FILE')); print(d['stack'])")
GATE=$(python3 -c "import json; d=json.load(open('$DETECT_FILE')); print(d['gate_command'])")
# ... 至少减少到 1 次文件读取 vs 6 次管道
rm -f "$DETECT_FILE"

# 方案 C（最佳）：detect.py 直接输出各字段到独立行，bash 用 read 读取
python3 "${LOOP_DIR}/lib/detect.py" --fields "$REPO_DIR" | {
  IFS= read -r STACK
  IFS= read -r GATE
  IFS= read -r AFTER_CREATE
  IFS= read -r BEFORE_REMOVE
  IFS= read -r SPEC_DIR
  IFS= read -r PLAN_DIR
}
```

方案 A 最简洁，但 `eval` 有安全隐患（如果 detect.py 被篡改）。方案 C 对多行字段（如 `AFTER_CREATE`）处理困难。综合建议用 **方案 B**（临时文件 + 一次性解析），或让 `detect.py` 支持 `--shell-export` 带安全转义。

---

### S4. `sed -i ''` 仅适用 macOS，Linux 不兼容

**问题描述：** 设计文档的技术约束声明"兼容 macOS + Linux"，但 `sed -i ''`（BSD sed）在 GNU/Linux 上语法不同（GNU sed 的 `-i` 后直接跟表达式，`''` 会被当作备份后缀）。

**技术影响：** 在 Linux 上执行 after_create hook 中的 `sed -i '' "s|..."` 会生成一个名为 `''` 的备份文件，或直接报错。

**建议修改：**

```bash
# 跨平台 sed in-place 辅助函数（放入 dev-loop.sh 或 lib/helpers.sh）
sed_inplace() {
  if sed --version 2>/dev/null | grep -q GNU; then
    sed -i "$@"
  else
    sed -i '' "$@"
  fi
}

# 或者直接用 perl（macOS 和 Linux 都自带）
perl -pi -e "s|/poi_dev|/$DB_NAME|g" backend/.env
```

更干净的方案是让 `detect.py` 生成的 hook 脚本自带跨平台处理，或者在 `config.py` 中提供一个 `sed_inplace` 的 shell 函数定义，自动注入到 hook 脚本前面。

---

## 🟡 中等问题

### M1. 每次调用 python3 lib/xxx.py 的进程启动开销

**问题描述：** dev-loop.sh 主循环中几乎每个操作都 fork 一个 `python3` 进程：读配置、读状态、写状态、渲染 prompt、分析失败、记录统计、发通知。设计文档的调用约定显示一轮循环可能调用 10+ 次 `python3`。

**技术影响：** Python 进程冷启动（含 import）在 macOS 上约 50-100ms。一轮循环 10 次 = 0.5-1s 纯开销。如果 `import yaml` 较慢（PyYAML C extension 未安装时 ~200ms），开销更大。在高频轮询场景（dashboard.sh -w 5）下，5 秒刷新间隔中有显著比例消耗在进程启动上。

**建议修改：**
1. **短期：** 合并可以合并的调用。例如 `config.py export-shell` 一次导出所有配置变量，而非分别调用 `get-value` 取各字段。
2. **中期：** 考虑让 `lib/` 模块支持一种"批量命令"模式：

```bash
# 单次 Python 启动完成多步操作
python3 loop/lib/batch.py <<'EOF'
config.export-shell workflow.yaml
state.read-phase
state.read-field branch
config.render-prompt IMPLEMENT
EOF
```

3. **长期（如果性能成为瓶颈）：** 用 Unix socket 做一个轻量 daemon，bash 通过 `socat` 或命名管道通信。

---

### M2. dashboard.sh 的 `-w` 参数实时刷新方式未明确

**问题描述：** 设计只说"每 5s 自动刷新"，没有说明具体的终端控制方式。如果是简单的 `while true; do clear; render; sleep 5; done`，在 clear 和 render 之间会有闪烁。

**技术影响：** 用户体验差，尤其是 SSH 远程连接时 clear + redraw 的延迟会导致明显闪烁。

**建议修改：** 使用 ANSI 光标控制实现无闪烁刷新：

```bash
# 首次渲染
tput clear
render_dashboard

# 后续刷新：光标归零而非 clear
while true; do
  sleep "${interval:-5}"
  tput home         # 光标移到左上角
  render_dashboard  # 覆盖写入（不清屏）
  tput ed           # 清除光标以下残余内容（内容变短时需要）
done
```

同时需要处理 `SIGINT`/`SIGTERM` 来恢复终端状态（`tput cnorm` 显示光标等），以及 `SIGWINCH` 在终端大小变化时重新渲染。

---

### M3. Stall 检测基于日志 mtime 不够可靠

**问题描述：** `reconcile.py` 通过检查 worker 日志文件的 mtime 来判断是否卡死。如果 Claude CLI 正在思考（长时间无输出但进程活跃），mtime 不会更新，会被误判为 stall。

**技术影响：** 复杂任务的 DESIGN 或 IMPLEMENT 阶段，Claude 可能在内部推理 2-3 分钟才有输出。`stall_timeout_sec: 1800`（30 分钟）对这种场景够用，但如果用户调低到 5 分钟，就可能误杀正常工作的 worker。

**建议修改：** 多信号联合判断：

```bash
# 1. 日志 mtime 超时（一级信号）
# 2. 进程 CPU 时间是否在增长（二级信号，排除僵死进程）
# 3. /proc/$pid/fd 是否有活跃文件描述符（Linux）或 lsof（macOS）

is_stalled() {
  local pid=$1 log_file=$2 timeout=$3
  local log_age=$(( $(date +%s) - $(stat -f %m "$log_file" 2>/dev/null || echo 0) ))

  if [ "$log_age" -lt "$timeout" ]; then
    return 1  # 日志近期有更新，没卡
  fi

  # 日志超时了，再检查进程是否仍在消耗 CPU
  if ps -p "$pid" -o cputime= 2>/dev/null | grep -q '^[0-9]'; then
    # 记录上次 CPU 时间，下次检查对比
    local cpu_now
    cpu_now=$(ps -p "$pid" -o cputime= 2>/dev/null | tr -d ' ')
    local cpu_prev="${STALL_CPU_CACHE[$pid]:-}"
    STALL_CPU_CACHE[$pid]="$cpu_now"
    if [ -n "$cpu_prev" ] && [ "$cpu_now" != "$cpu_prev" ]; then
      return 1  # CPU 时间在增长，进程活跃
    fi
  fi

  return 0  # 真的卡了
}
```

不过考虑到实现复杂度，建议至少在设计文档中明确：stall_timeout_sec 应设置为远大于单次 Claude 推理时间的值（建议最低 10 分钟），并在日志中记录"疑似 stall，等待确认"的中间状态。

---

### M4. 信号文件 `CANCEL-{id}` 的竞态风险

**问题描述：** 取消机制依赖 `inbox/CANCEL-{id}` 文件。`reconcile.py` 扫描到该文件后 kill worker 并标记 cancelled，但如果 reconcile 和 worker 的状态更新之间有时间差，worker 可能在被 kill 前刚好完成任务并写入 success 状态。

**技术影响：** 取消生效但任务实际已完成，状态不一致。或者更糟：kill 发生在 worker 正在写 loop-state.json 的过程中，导致 JSON 文件损坏。

**建议修改：**
1. `state.py` 写入时使用原子操作（write to tmpfile + rename），设计中提到了文件锁但需要确保锁粒度覆盖到整个 read-modify-write 周期。
2. Cancel 流程增加状态前置检查：

```python
# reconcile.py cancel 逻辑
def cancel_task(task_id):
    with state_lock():
        state = read_state()
        task = find_task(state, task_id)
        if task["status"] in ("done", "cancelled"):
            # 已完成或已取消，清理信号文件即可
            remove_cancel_signal(task_id)
            return
        task["status"] = "cancelled"
        write_state(state)
    # 锁释放后再 kill（kill 是幂等的）
    kill_worker(task_id)
    remove_cancel_signal(task_id)
```

---

### M5. `timeout` 命令在 macOS 上不自带

**问题描述：** `ensure_worktree()` 使用 `timeout` 命令限制 hook 执行时间，但 macOS 原生不带 `timeout`，需要 `brew install coreutils`（提供 `gtimeout`）。

**技术影响：** 在纯净 macOS 环境上首次运行会直接失败：`timeout: command not found`，由于 `set -e`，整个脚本退出。

**建议修改：**

```bash
# 在脚本顶部定义跨平台 timeout
if command -v timeout >/dev/null 2>&1; then
  TIMEOUT_CMD="timeout"
elif command -v gtimeout >/dev/null 2>&1; then
  TIMEOUT_CMD="gtimeout"
else
  # 降级：用 bash 内建的后台进程 + sleep 模拟
  TIMEOUT_CMD=""
  run_with_timeout() {
    local secs=$1; shift
    "$@" &
    local pid=$!
    ( sleep "$secs" && kill -TERM "$pid" 2>/dev/null ) &
    local watcher=$!
    wait "$pid" 2>/dev/null
    local rc=$?
    kill "$watcher" 2>/dev/null 2>&1; wait "$watcher" 2>/dev/null
    return $rc
  }
fi

# 使用
if [ -n "$TIMEOUT_CMD" ]; then
  ( cd "$wt_path" && ... "$TIMEOUT_CMD" "${timeout:-120}" bash "$hook_file" )
else
  ( cd "$wt_path" && ... run_with_timeout "${timeout:-120}" bash "$hook_file" )
fi
```

或者在依赖检查阶段（init.sh 的第 6 步）就检测 timeout 可用性并提示安装 coreutils。

---

### M6. Phase 5 CLI 统一入口的子命令路由缺少 help 和错误处理

**问题描述：** `niuma.sh <command>` 设计了 8 个子命令，但没有说明无参数/未知命令时的行为，也没有 `--help`/`-h` 设计。

**建议修改：**

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

show_help() {
  cat <<'EOF'
Usage: niuma <command> [options]

Commands:
  start      启动编排器
  init       初始化新项目
  status     查看状态
  dashboard  终端实时看板
  stats      运行统计
  add        快捷入队
  stop       停止编排器
  cancel     取消任务

Run 'niuma <command> --help' for command-specific help.
EOF
}

case "${1:-}" in
  start)     shift; exec bash "$SCRIPT_DIR/dev-loop.sh" "$@" ;;
  init)      shift; exec bash "$SCRIPT_DIR/init.sh" "$@" ;;
  status)    shift; exec bash "$SCRIPT_DIR/status.sh" "$@" ;;
  dashboard) shift; exec bash "$SCRIPT_DIR/dashboard.sh" "$@" ;;
  stats)     shift; exec bash "$SCRIPT_DIR/stats.sh" "$@" ;;
  add)       shift; exec bash "$SCRIPT_DIR/add-task.sh" "$@" ;;
  stop)      touch "${SCRIPT_DIR}/inbox/STOP"; echo "STOP signal sent" ;;
  cancel)
    [ -n "${2:-}" ] || { echo "Usage: niuma cancel <task_id>" >&2; exit 1; }
    touch "${SCRIPT_DIR}/inbox/CANCEL-$2"; echo "CANCEL signal sent for task #$2"
    ;;
  -h|--help|help|"") show_help ;;
  *) echo "Unknown command: $1" >&2; show_help >&2; exit 1 ;;
esac
```

注意用 `exec` 替代直接调用子脚本，这样子命令的进程替换当前 shell，不会多一层进程嵌套，信号传递也更直接。

---

### M7. Worker 进程的 PID 文件管理缺乏安全保障

**问题描述：** 设计中 worker 进程通过 `workers/*/pid` 文件追踪。但没有说明：(1) PID 文件写入是否原子；(2) PID 复用场景如何处理（kill 掉旧进程后 OS 分配了相同 PID 给新进程）。

**技术影响：** PID 复用在长时间运行的系统上有真实风险。一个 worker crash 后，其 PID 被 OS 分配给一个无关进程，reconcile 模块 kill 这个 PID 会误杀其他进程。

**建议修改：**

```bash
# 写 PID 文件时同时记录进程启动时间
echo "$pid $(date +%s)" > "$worker_dir/pid"

# kill 前验证进程身份
safe_kill_worker() {
  local pid_file=$1
  read -r pid start_ts < "$pid_file"
  # 检查进程是否存在且创建时间匹配
  local proc_start
  proc_start=$(ps -p "$pid" -o lstart= 2>/dev/null) || return 0  # 进程已退出
  # 额外检查：进程的 command line 是否包含 claude
  if ps -p "$pid" -o command= 2>/dev/null | grep -q "claude"; then
    kill -TERM "$pid" 2>/dev/null
    # 给 10 秒优雅退出
    for i in $(seq 1 10); do
      kill -0 "$pid" 2>/dev/null || return 0
      sleep 1
    done
    kill -KILL "$pid" 2>/dev/null
  fi
}
```

---

### M8. `cp -Rc` APFS clone 在非 APFS 文件系统上静默失败可能不够

**问题描述：** after_create hook 中使用 `cp -Rc` 进行 APFS copy-on-write clone。设计中有 `|| npm install --prefer-offline` 兜底，但兜底在两处不一致：

- `[ -d node_modules ] || npm install --prefer-offline`：仅在 cp 完全失败时兜底
- 如果 `cp -Rc` 部分成功（只拷贝了部分 workspace 的 node_modules），不会触发 npm install，导致残缺的 node_modules

**技术影响：** 在非 macOS 或非 APFS 分区上可能产生不完整依赖，后续构建/测试出现莫名其妙的 module not found 错误。

**建议修改：**

```bash
# 拷贝后做完整性校验
if diff -q "$MAIN_REPO/package-lock.json" "package-lock.json" >/dev/null 2>&1; then
  cp -Rc "$MAIN_REPO/node_modules" node_modules 2>/dev/null && \
  for ws in frontend backend; do
    [ -d "$MAIN_REPO/$ws/node_modules" ] && \
      cp -Rc "$MAIN_REPO/$ws/node_modules" "$ws/node_modules" 2>/dev/null
  done
  # 校验：关键 binary 是否存在
  if [ ! -x "node_modules/.bin/eslint" ] 2>/dev/null; then
    rm -rf node_modules frontend/node_modules backend/node_modules
    npm install --prefer-offline
  fi
else
  npm install --prefer-offline
fi
```

---

## 🟢 建议

### G1. 为所有脚本添加 `set -euo pipefail` 并统一错误处理

**问题描述：** 设计文档在技术约束中提到"所有脚本 `set -euo pipefail`"，但 6 个独立脚本（dashboard.sh、stats.sh 等）作为薄 wrapper 只调用 python3，如果 Python 进程返回非零，`set -e` 会让脚本直接退出，没有友好错误信息。

**建议修改：** 提供统一的错误处理函数：

```bash
# lib/helpers.sh —— source 到每个脚本
die() { echo "ERROR: $*" >&2; exit 1; }

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "'$1' not found. $2"
}

require_file() {
  [ -f "$1" ] || die "File not found: $1"
}

# 使用示例
source "$(dirname "$0")/lib/helpers.sh"
require_cmd python3 "Please install Python 3.9+"
require_file "$STATE_FILE" "Run 'niuma init' first."
```

---

### G2. init.sh 中 `.gitignore` 追加逻辑可以更健壮

**问题描述：** 当前用 `grep -qF "$pattern" .gitignore || echo "$pattern" >> .gitignore` 逐行追加。如果 `.gitignore` 文件不存在会 grep 报错（虽然 `||` 会兜住），且如果 `.gitignore` 最后一行没有换行符，追加的内容会连在最后一行后面。

**建议修改：**

```bash
touch .gitignore
# 确保文件以换行符结尾
[ -z "$(tail -c 1 .gitignore)" ] || echo >> .gitignore

for pattern in "loop/stats.json" "loop/PROGRESS.md" "loop/.env" \
               "loop/loop-state.json" "loop/workers/" "loop/logs/" \
               "loop/.locks/" ".trees/"; do
  grep -qxF "$pattern" .gitignore 2>/dev/null || echo "$pattern" >> .gitignore
done
```

注意用 `-qxF`（完整行匹配）而非 `-qF`（子串匹配），避免 `loop/logs/` 误匹配到 `# loop/logs/ is excluded` 这样的注释行。

---

### G3. 考虑 `workflow.yaml` 的 schema 校验

**问题描述：** `config.py` 做热重载时如果 YAML 格式错误会保留上次有效配置（设计中提到），但如果是语义错误（比如 `stall_timeout_sec: "abc"` 或缺少必填字段），行为未定义。

**建议修改：** 在 `config.py` 中增加简单的 schema 校验：

```python
REQUIRED_FIELDS = {
    "workers.max_concurrent": int,
    "workers.stall_timeout_sec": int,
    "retry.base_delay_sec": int,
}

def validate_config(cfg: dict) -> list[str]:
    errors = []
    for path, expected_type in REQUIRED_FIELDS.items():
        keys = path.split(".")
        val = cfg
        for k in keys:
            val = val.get(k) if isinstance(val, dict) else None
        if val is None:
            errors.append(f"Missing required field: {path}")
        elif not isinstance(val, expected_type):
            errors.append(f"{path}: expected {expected_type.__name__}, got {type(val).__name__}")
    return errors
```

---

### G4. `generate-progress.sh` 后台执行需要保护

**问题描述：** 设计中 `bash loop/generate-progress.sh 2>/dev/null &` 后台运行不阻塞。但如果主进程退出时后台进程还在写文件，可能产生不完整的 PROGRESS.md。

**建议修改：** 使用原子写入（写临时文件再 rename）：

```bash
# generate-progress.sh 内部
PROGRESS_TMP=$(mktemp "${LOOP_DIR}/PROGRESS.md.XXXXXX")
# ... 生成内容写入 $PROGRESS_TMP ...
mv "$PROGRESS_TMP" "${LOOP_DIR}/PROGRESS.md"
```

---

### G5. 通知脚本的飞书 Webhook 安全性

**问题描述：** 飞书 Webhook URL 存储在 `workflow.yaml` 或 `loop/.env` 中。`workflow.yaml` 如果不在 `.gitignore` 中（设计看起来它应该被提交），Webhook URL 会进入 git 历史。

**建议修改：** `workflow.yaml` 中的 `feishu_webhook` 默认值应该是空字符串或占位符，实际 URL 只通过 `.env` 文件或环境变量注入：

```yaml
notify:
  feishu_webhook: ""  # 通过 LOOP_FEISHU_WEBHOOK 环境变量配置
```

确保 `notify.py` 优先读取环境变量 `LOOP_FEISHU_WEBHOOK`，`workflow.yaml` 中的值仅作为 fallback。

---

### G6. `detect.py` 中 CI 解析可能引入不期望的命令

**问题描述：** `_parse_github_actions` 从 CI 配置中提取所有包含 `test`/`lint`/`build`/`check` 关键词的 `run` 命令，并用 `&&` 连接。但 CI 中可能有条件判断、设置环境变量、下载依赖等不适合作为 gate 命令的步骤，如果它们碰巧包含这些关键词就会被误收。

**技术影响：** gate 命令可能包含 `docker pull test-image`、`curl https://...check` 这样的无关命令。

**建议修改：** 只提取 step name 明确包含 "lint"/"test"/"build"/"typecheck" 的步骤，且跳过 setup 类步骤：

```python
SKIP_PATTERNS = ["setup", "install", "cache", "checkout", "upload", "download", "deploy"]

for step in job.get("steps") or []:
    step_name = (step.get("name") or "").lower()
    # 跳过 setup 类步骤
    if any(p in step_name for p in SKIP_PATTERNS):
        continue
    run = step.get("run", "")
    # 只在 step name（而非 run 内容）中匹配关键词
    if run and any(kw in step_name for kw in ["test", "lint", "build", "check", "typecheck"]):
        commands.append(run.strip())
```

---

## 总体评价

### 优点

1. **Shell + Python 职责分离合理**：Bash 负责进程管理、信号处理、git/worktree 操作（这些是 shell 的强项），Python 负责 JSON/YAML/业务逻辑（避免了 bash 处理结构化数据的痛苦）。这种分层设计是务实的选择。

2. **配置外置 + 热重载是正确方向**：将 30+ 处硬编码提取到 workflow.yaml，配合 hooks 机制实现可移植性。init.sh 的三层探测策略（确定性探测 → AI 生成 → 兜底默认）设计思路优秀。

3. **Worktree 保留复用策略合理**：失败时保留 worktree 避免重建开销，成功后清理。这符合 git worktree 的最佳实践。

4. **Phase 5 CLI 统一入口（niuma.sh）是正确的演进路径**：6 个独立脚本 → 单一入口 + 子命令，符合 Unix CLI 工具的标准模式（类似 git、docker 的子命令结构）。

### 需要重点关注的风险

1. **Shell 引号嵌套**（S1）是最大的稳定性风险，hook 内容从 YAML → Python → bash 变量 → `bash -c` 的链路太长，应尽早改为临时文件方案。

2. **跨平台兼容性**（S4, M5）在声明支持 Linux 的前提下，`sed -i ''` 和 `timeout` 都需要处理。如果实际只在 macOS 上用，应在文档中明确。

3. **Python 进程启动开销**（M1）在当前规模下可接受，但随着调用点增多需要关注，建议在实现时就做好批量调用的接口设计。

### 评分

| 维度 | 评分 | 说明 |
|------|------|------|
| 脚本质量 | 7/10 | 整体结构清晰，但引号安全和跨平台兼容需要加固 |
| Git worktree 使用 | 8/10 | `--detach` + 保留复用策略合理，cleanup 安全性需加强 |
| 进程管理 | 7/10 | PID 追踪 + stall 检测思路正确，细节（PID 复用、信号竞态）需打磨 |
| CLI 设计 | 8/10 | 子命令结构合理，缺 help/错误处理细节 |
| 可移植性 | 8/10 | hooks + workflow.yaml 分离方案优秀，init.sh 探测设计出色 |
| **综合** | **7.5/10** | 架构设计扎实，实现细节需要在编码阶段补强 |

建议在实现阶段优先解决 S1-S4 四个严重问题，它们是生产环境稳定运行的前提。
