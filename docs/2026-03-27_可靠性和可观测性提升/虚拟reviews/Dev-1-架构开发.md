# Dev-1 评审：代码架构与技术实现

> 评审对象：openNiuMa 合并升级设计（2026-03-27-loop-v4-merged-design.md + 关联的 Symphony/v4 设计文档）
> 评审视角：资深全栈工程师，关注代码架构合理性、模块职责划分、bash/python 交互效率、技术债务风险
> 参考基线：现有 dev-loop.sh（1676 行，含大量内联 Python 块）

---

## 🔴 严重问题

### S1. Bash→Python 每次调用均为独立进程，config.py 的"热重载"机制设计自相矛盾

**问题描述：**

设计声称 config.py 维护 `_last_mtime` 和 `_last_good_config` 两个内部状态实现热重载，mtime 未变则返回缓存，解析失败则降级到上次有效配置。但 Bash 每次调用 `python3 loop/lib/config.py ...` 都会 fork 全新 Python 进程——进程内存中不存在"上次"。这意味着：

1. 所谓的 mtime 缓存每次都是冷启动，必然触发 YAML 解析
2. "失败保留上次有效配置"在进程间完全不成立——YAML 被写坏时直接报错退出，没有任何降级能力
3. 主循环热路径上每轮至少 5-8 次 `python3 ...` 调用（export-env、render-prompt、read-phase、record-session 等），每次冷启动约 80-120ms（import yaml + IO），累计 400-960ms/轮

**技术影响：**
- 热重载机制完全失效，设计文档与实际行为不一致
- workflow.yaml 配置出错时编排器直接崩溃，无容错
- 进程启动开销在高频调度场景下显著拖慢吞吐

**建议修改：**

方案 A（推荐）：引入文件级缓存，真正实现跨进程降级
```python
# config.py — 解析结果缓存到 .cache/workflow.json
import json, os, time

CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", ".cache")
CACHE_PATH = os.path.join(CACHE_DIR, "workflow.json")

def load_config(yaml_path):
    yaml_mtime = os.path.getmtime(yaml_path)

    # 尝试命中磁盘缓存（JSON 解析比 YAML 快 10 倍+，且无 PyYAML 依赖）
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH) as f:
            cache = json.load(f)
        if cache.get("_source_mtime") == yaml_mtime:
            return cache  # 命中，跳过 YAML 解析

    # 缓存未命中或 mtime 变化，重新解析
    try:
        import yaml
        with open(yaml_path) as f:
            config = yaml.safe_load(f)
        config["_source_mtime"] = yaml_mtime
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(CACHE_PATH, "w") as f:
            json.dump(config, f)
        return config
    except Exception as e:
        # 降级到上次有效的缓存（真正的跨进程降级）
        if os.path.exists(CACHE_PATH):
            import sys
            print(f"[config] YAML 解析失败({e})，使用上次有效配置", file=sys.stderr)
            with open(CACHE_PATH) as f:
                return json.load(f)
        raise
```

方案 B（减少调用频次）：在 dev-loop.sh 每轮循环开头一次性 export 所有配置到 shell 变量
```bash
# 每轮循环只调用一次 config.py，而非散落在各处
eval "$(python3 loop/lib/config.py export-env workflow.yaml)"
# 后续直接使用 $CFG_GATE_COMMAND, $CFG_STALL_TIMEOUT 等变量
```

两个方案应同时采用——A 解决降级能力，B 解决调用频次。

### S2. state.py 文件锁方案未定义，5 worker 并行写 loop-state.json 必然出现数据竞争

**问题描述：**

设计只说"state.py 使用文件锁保护 loop-state.json 并发读写"，但未定义具体锁机制。当前 dev-loop.sh 中虽有 `LOCK_DIR` 变量，但实际并未使用。5 个 worker 进程 + 1 个主调度进程 + reconcile.py 都可能同时读写 loop-state.json，典型的读写竞争场景。

更关键的是：由于每次 state.py 调用都是独立进程，`read → modify → write` 不是原子的。即使用了文件锁，以下序列仍可能出问题：
```
Worker A: lock → read(state) → unlock → 计算 → lock → write(state) → unlock
Worker B:                        lock → read(state) → unlock → 计算 → lock → write(STALE state) → unlock
```

如果 state.py 的 `update` 操作不是 `lock → read → modify → write → unlock` 一气呵成，锁保护形同虚设。

**技术影响：**
- loop-state.json 被并发写入导致 JSON 损坏（进程被 kill 时写了一半）
- 状态丢失：Worker A 的更新被 Worker B 的 stale write 覆盖
- 调试极难：只在并行模式 + 高并发时偶现

**建议修改：**

state.py 必须提供原子的 read-modify-write 操作：
```python
import fcntl, json, os, tempfile

class StateManager:
    def __init__(self, state_path):
        self.state_path = state_path
        self.lock_path = state_path + ".lock"

    def update(self, modifier_fn):
        """原子性 read-modify-write，modifier_fn 接收当前 state 并返回修改后的 state"""
        with open(self.lock_path, 'w') as lock_fd:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)  # 阻塞式排他锁
            try:
                with open(self.state_path) as f:
                    state = json.load(f)

                state = modifier_fn(state)

                # 写临时文件 + rename（防止写到一半被 kill）
                dir_name = os.path.dirname(self.state_path)
                fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
                try:
                    with os.fdopen(fd, 'w') as f:
                        json.dump(state, f, indent=2, ensure_ascii=False)
                    os.replace(tmp_path, self.state_path)  # 原子替换
                except:
                    os.unlink(tmp_path)
                    raise
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
```

Bash 调用方式改为传入 JSON patch：
```bash
# 替代 state.py set-field + state.py set-status 的两次调用
python3 loop/lib/state.py update --patch '{"current_phase":"VERIFY","verify_attempts":1}'
```

### S3. stats.py record-session 用 10+ 个位置参数，Bash 调用极易出错且无法扩展

**问题描述：**
```
stats.py record-session <stats_file> <task_id> <task_name> <phase> <started_at> <ended_at> <exit_code> [cost_usd] [failure_type] [worker_id]
```

10 个位置参数中 task_name 可能包含中文、空格、引号、`&` 等特殊字符。可选参数用位置传递——如果 cost_usd 为空但 failure_type 有值，必须传空占位 `""`。

**技术影响：**
- task_name 如 `"修复「热力图」的 & 渲染问题"` 在 Bash 中引号处理稍有不慎就参数错位
- 可选参数的空占位极易遗忘，静默写入错误数据比报错更糟
- 未来新增字段（如 model_name）需要改所有调用点

**建议修改：**

改用 JSON stdin（彻底绕开 shell 转义问题）：
```bash
# Bash 端：用 jq 构造 JSON（jq 已是项目依赖）
jq -n --argjson task_id "$task_id" \
      --arg task_name "$task_name" \
      --arg phase "$phase" \
      --arg started_at "$started" \
      --arg ended_at "$ended" \
      --argjson exit_code "$exit_code" \
      '{task_id: $task_id, task_name: $task_name, phase: $phase,
        started_at: $started_at, ended_at: $ended_at, exit_code: $exit_code}' | \
  python3 loop/lib/stats.py record-session --stdin "$STATS_FILE"
```

或者用环境变量传递（简单且安全）：
```bash
STATS_TASK_ID="$task_id" \
STATS_TASK_NAME="$task_name" \
STATS_PHASE="$phase" \
STATS_STARTED="$started" \
STATS_ENDED="$ended" \
STATS_EXIT_CODE="$exit_code" \
  python3 loop/lib/stats.py record-session "$STATS_FILE"
```

---

## 🟡 中等问题

### M1. failure.py 正则匹配 50 行尾部过于脆弱，误分类风险高

**问题描述：**

失败分类靠正则匹配日志尾部 50 行中的关键词。但存在多重问题：
- Claude CLI 的错误输出格式不稳定，版本更新可能改变措辞
- 50 行可能不包含真正的错误原因——中间失败后可能有大段清理/回滚输出
- 代码注释中的 `// handle permission denied` 可能被误匹配
- `npm run lint/test/build` 如果当作一个正则 pattern，实际匹配的是 `lint` 或 `test` 或 `build` 之前有 `/` 的行，而非三条命令

**技术影响：**
- 误分类导致错误的重试策略——gate 误判为 network 会白等 60s，network 误判为 gate 会立即重试然后再次超时
- 随 Claude CLI 升级需要频繁维护正则列表

**建议修改：**

分层匹配策略，优先使用结构化信号：
```python
def classify_failure(log_path, exit_code):
    tail = read_tail(log_path, lines=200)

    # 第一层：exit code（最可靠）
    if exit_code == 137:
        return "resource"    # OOM / SIGKILL
    if exit_code == 124:
        return "timeout"     # timeout 命令超时

    # 第二层：提取错误上下文行（而非全文匹配）
    error_lines = [l for l in tail.split('\n')
                   if re.search(r'(?i)(error|fail|fatal|panic|CONFLICT)', l)]
    error_context = '\n'.join(error_lines[-30:])  # 只看最后 30 条错误行

    # 第三层：在错误上下文上做关键词匹配
    patterns = [
        ("gate",       [r"(?:npm|npx)\s+(?:run\s+)?(?:lint|test|build)", r"exited with code [1-9]"]),
        ("context",    [r"context.window", r"token.limit", r"max.turns"]),
        ("permission", [r"permission denied", r"dangerously"]),
        ("conflict",   [r"\bCONFLICT\b", r"merge conflict"]),
        ("network",    [r"ETIMEDOUT", r"ECONNREFUSED", r"rate.limit", r"429"]),
    ]
    for ftype, pats in patterns:
        if any(re.search(p, error_context, re.I) for p in pats):
            return ftype

    return "unknown"
```

### M2. workflow.yaml 中 hooks.after_create 存储多行 Bash 脚本，调试和维护成本高

**问题描述：**

`hooks.after_create` 的值是一整段 Bash 脚本（含 if/else、变量、管道），存在以下风险：
- YAML 缩进敏感，用户编辑时缩进错一格整段脚本就断了
- `bash -c "$hook"` 执行时，hook 内容经历 YAML 解析 → Python 字符串 → shell 参数传递 → bash 解析，任一环节的引号/转义问题都难以排查
- hook 失败时只知道"after_create hook 失败"，不知道哪行出错

**技术影响：**
- 用户首次配置项目时极易踩坑
- hook 失败的调试体验很差

**建议修改：**

1. 支持 `@file:` 前缀引用外部脚本文件（大段脚本用独立文件更安全）：
```yaml
hooks:
  after_create: "@file:loop/hooks/after-create.sh"
  before_remove: |
    dropdb --if-exists "test_$SLUG" 2>/dev/null || true
```

2. 执行时写入临时文件再运行（避免 `bash -c` 的引号地狱），并开启调试追踪：
```bash
execute_hook() {
  local hook_content="$1" wt_path="$2" slug="$3" timeout_val="$4"
  local tmp_hook
  tmp_hook=$(mktemp "${TMPDIR:-/tmp}/loop-hook-XXXXXX.sh")
  echo "set -euo pipefail" > "$tmp_hook"
  echo "$hook_content" >> "$tmp_hook"

  ( cd "$wt_path" && SLUG="$slug" MAIN_REPO="$MAIN_REPO_DIR" \
    timeout "${timeout_val:-120}" bash "$tmp_hook" ) 2>&1 | \
    tee -a "$LOG_DIR/hooks.log"
  local rc=${PIPESTATUS[0]}
  rm -f "$tmp_hook"
  return $rc
}
```

### M3. 6 个 Shell 壳脚本与 Python 模块一一对应，引入了不必要的维护开销

**问题描述：**

dashboard.sh、stats.sh、notify.sh、add-task.sh、status.sh、generate-progress.sh 每个都只是简单的参数转发给对应 .py 模块。维护 12 个文件（6 sh + 6 py）比 6 个多一倍，且参数格式需要在两层之间保持一致。

**技术影响：**
- 用户需要记住用 `.sh` 调用还是直接 `python3 ... .py` 调用
- sh 层的参数解析和 py 层的参数解析可能不一致
- 新增子命令要改两个文件

**建议修改：**

用一个统一 CLI 入口替代所有壳脚本：
```bash
#!/usr/bin/env bash
# loop/niuma — 统一 CLI 入口
set -euo pipefail
LOOP_DIR="$(cd "$(dirname "$0")" && pwd)"
cmd="${1:-help}"; shift 2>/dev/null || true
case "$cmd" in
  start)      exec bash "$LOOP_DIR/dev-loop.sh" "$@" ;;
  init)       exec bash "$LOOP_DIR/init.sh" "$@" ;;
  dashboard)  exec python3 "$LOOP_DIR/lib/status.py" --format dashboard "$@" ;;
  status)     exec python3 "$LOOP_DIR/lib/status.py" "$@" ;;
  stats)      exec python3 "$LOOP_DIR/lib/stats.py" summary "$@" ;;
  notify)     exec python3 "$LOOP_DIR/lib/notify.py" send "$@" ;;
  add)        exec python3 "$LOOP_DIR/lib/inbox.py" add-task "$@" ;;
  progress)   exec bash "$LOOP_DIR/generate-progress.sh" "$@" ;;
  *)          echo "Usage: niuma {start|init|dashboard|status|stats|notify|add|progress}" ;;
esac
```

保留各 .sh 文件作为软链接或兼容别名即可，但新用户引导统一使用 `niuma <command>`。

### M4. PyYAML 依赖在新版 macOS 上会被 PEP 668 拒绝安装

**问题描述：**

设计声称"零额外依赖（除 PyYAML）"，但 macOS Sonoma+ 的系统 Python 不再允许 `pip3 install` 直接安装包（PEP 668 externally-managed-environment 限制）。实际上 PyYAML 就是一个需要专门处理的外部依赖。

**技术影响：**
- 用户首次运行 init.sh 时大概率遇到 pip 报错
- 不同 Python 环境（系统/pyenv/conda/brew）导致"我这里能跑你那里不行"
- 破坏了"拷贝即用"的可移植性目标

**建议修改：**

方案优先级排序：
1. **首选：去掉 PyYAML，改用 JSON 配置** — workflow.json 对机器更友好，JSON 是 Python 标准库内置的。牺牲一点可读性（没有注释），但彻底零依赖
2. **次选：用 Python 3.11+ 的 tomllib** — TOML 支持注释且是标准库内置，但要求 Python 3.11+
3. **保底：自带 PyYAML** — 将 PyYAML 源码（单文件约 200KB）直接放入 `lib/vendor/yaml/` 目录，import 时优先从 vendor 加载。这是很多 CLI 工具的做法

### M5. reconcile.py stall 检测仅基于日志 mtime，可能误杀正常工作的 Claude 进程

**问题描述：**

设计中 stall 判定依据是"worker 日志文件的 mtime 超过 stall_timeout_sec"。但 Claude CLI 在长时间推理时可能数分钟不产生任何输出（尤其在 DESIGN phase 进行长思考链时），此时日志 mtime 不变，但进程完全正常。

**技术影响：**
- 误 kill 正在深度思考的 Claude 进程，浪费已消耗的 token 和费用
- 重试后大概率再次触发同一 stall 检测（因为同一任务的推理时间是相近的），形成"杀→重试→再杀"死循环

**建议修改：**

多信号综合判断，日志 mtime 只是必要条件之一：
```python
def is_stalled(worker_dir, timeout_sec):
    log_mtime = os.path.getmtime(log_file)
    log_stale = (time.time() - log_mtime) > timeout_sec

    if not log_stale:
        return False  # 日志有活动，肯定没 stall

    # 日志不活跃时，再检查进程 CPU
    pid = int(open(os.path.join(worker_dir, "pid")).read().strip())
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "%cpu="],
            capture_output=True, text=True, timeout=5
        )
        cpu = float(result.stdout.strip())
        return cpu < 0.5  # CPU 也几乎为零才判定 stall
    except Exception:
        return True  # 进程不存在，视为 stall
```

### M6. init.sh 中对同一 JSON 结果启动 6 个 Python 进程逐字段提取

**问题描述：**
```bash
STACK=$(echo "$DETECT_RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin)['stack'])")
GATE=$(echo "$DETECT_RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin)['gate_command'])")
AFTER_CREATE=$(echo "$DETECT_RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin)['after_create'])")
BEFORE_REMOVE=$(echo "$DETECT_RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin)['before_remove'])")
SPEC_DIR=$(echo "$DETECT_RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin)['spec_dir'])")
PLAN_DIR=$(echo "$DETECT_RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin)['plan_dir'])")
```

6 个 Python 进程做完全相同的 JSON 解析，只为提取不同字段。

**技术影响：**
- 6 次 Python 冷启动 ~500-700ms，而一次就够
- 如果 DETECT_RESULT 中某个字段含换行符，管道传递可能丢数据

**建议修改：**
```bash
eval "$(echo "$DETECT_RESULT" | python3 -c "
import json, sys, shlex
d = json.load(sys.stdin)
for k, v in d.items():
    print(f'{k.upper()}={shlex.quote(str(v))}')
")"
# 一次调用，$STACK, $GATE_COMMAND 等全部可用
```

---

## 🟢 建议

### G1. stats.json 应考虑数据轮转策略

stats.json 是 append-only 的 sessions 数组，长期运行的项目可能积累数千条记录。每次 record-session 需要全量读取 → 追加 → 全量写回，O(n) 复杂度随时间增长。

建议：
- 改用 JSONL（JSON Lines）格式，每行一条记录，append-only，无需全量读写
- 查询时用 Python 按需读取和聚合
- 定期归档：`stats-2026-03.jsonl` 按月分割

### G2. Prompt 模板的 `{{var}}` 语法存在冲突风险

`{{var}}` 与 Go template、Mustache、Jinja2 等主流模板引擎语法完全相同。如果用户的项目使用这些技术，prompt 模板中引用项目代码示例时会被误解析。

建议使用更独特的分隔符，如 `<<var>>` 或 `%%var%%`。或者在 config.py 的 render-prompt 中只替换已注册的变量名（白名单模式），遇到未注册的 `{{xxx}}` 保持原样而非报错。

### G3. 测试策略中"Bash 脚本不测试"是显著的技术债务

dev-loop.sh 是编排核心（1676 行），worktree 管理、进程调度、信号处理、worker 生命周期等关键逻辑全在 Bash 中。"Python lib/ 用 unittest，Bash 不测试"意味着最核心、最难调试的部分没有任何自动化质量保障。

建议：
1. 至少用 bats-core 对 `ensure_worktree`、`cleanup_worktree`、`sync_worker_result` 等关键函数编写冒烟测试
2. 在 CI 中做端到端集成测试：`init.sh → 放入模拟任务 → 跑一轮循环 → 检查状态`
3. 长期方向：逐步将可测试的判断逻辑从 Bash 迁移到 Python（如状态转换、任务调度决策）

### G4. 通知渠道应设计为可插拔架构

当前 notify.py 硬编码支持 macOS + 飞书 + bell 三种渠道。建议用字典注册模式，新增渠道只需注册函数：

```python
CHANNELS = {}

def register_channel(name, handler):
    CHANNELS[name] = handler

def send(level, title, body, config):
    for name, handler in CHANNELS.items():
        if config.get(f"notify.{name}", False):
            try:
                handler(title, body, level, config)
            except Exception as e:
                print(f"[notify] {name} 发送失败: {e}", file=sys.stderr)

# 内置渠道
register_channel("bell", lambda t, b, l, c: print('\a', end=''))
register_channel("macos", send_macos_notification)
register_channel("feishu", send_feishu_webhook)
```

未来加 Slack、Telegram、Webhook 通用等只需几行代码。

### G5. dashboard.sh 中混合了大量内联 Python 用于渲染，与"消除内联 Python"的设计目标矛盾

v4 实现计划中 dashboard.sh 的代码（Task 2）包含了 5 块 `python3 -c "..."` 内联代码用于从 JSON 提取数据和渲染表格。合并设计虽然提到"改为调用 `lib/status.py --format dashboard`"，但 v4 实现计划中的代码与这一描述不一致。

建议在实现时严格遵循合并设计：dashboard.sh 只做 ANSI 彩色输出的壳，全部数据聚合和格式化由 `status.py` 完成并输出预格式化文本。

### G6. detect.py 从 CI 配置提取 gate 命令应更保守

当前设计会把所有包含 `test/lint/build` 关键词的 CI 步骤合并为 gate 命令，但 CI 中的 `npm ci`（如果步骤名为 "Install & build"）、Docker 相关步骤、条件分支步骤等都不应该进入 gate。

建议增加排除列表：
```python
EXCLUDE_KEYWORDS = {"install", "setup", "cache", "upload", "download", "deploy",
                    "docker", "checkout", "restore", "artifact", "publish"}
```

---

## 总体评价

**设计质量：7.5/10** — 方向完全正确，成功将 Symphony 的工程化基础设施与 v4 的用户体验能力整合为一个连贯方案。

**核心优点：**
1. **模块化提取方向正确**：将内联 Python 块提取到独立可测试模块，是当前最大的结构性改善
2. **配置与引擎分离**：workflow.yaml + hooks + prompt 模板的设计让引擎可移植性大幅提升
3. **失败分类的精细化**：6 种类型 + 差异化重试策略远优于原来的"连续 3 次就停"
4. **渐进式迁移**：按依赖关系分 Phase 落地，每步可独立验证，风险可控
5. **init.sh 三层探测**：确定性探测 → AI 生成 → 兜底默认，务实且降低了上手门槛

**主要风险（按优先级）：**
1. **Bash↔Python 进程间通信效率**（S1）是最大的架构瓶颈，每轮 5-8 次进程冷启动的累积开销不可忽视，建议在 Phase 1 就引入文件缓存 + 批量 export 方案
2. **并发写 state 的数据完整性**（S2）在多 worker 模式下是 P0 风险，必须在 Phase 2 之前解决
3. **stats.py 的 CLI 接口**（S3）上线后改接口成本高，建议在实现前就切换到 JSON stdin 或环境变量方案
4. **PyYAML 依赖**（M4）会在可移植性场景下频繁踩坑，建议评估去掉 YAML 改用 JSON 的可行性

**落地建议：**
- Phase 0（可移植性）和 Phase 1（基础设施）合并实施，因为 config.py 是所有模块的基础
- 在 Phase 1 中优先解决 S1（配置缓存）和 S2（文件锁），再进入 Phase 2
- S3（stats CLI 接口）必须在 Phase 2 开始前确定，避免上线后改接口
- M3（统一 CLI 入口）放到 Phase 4 尾部与品牌化命名一起做
