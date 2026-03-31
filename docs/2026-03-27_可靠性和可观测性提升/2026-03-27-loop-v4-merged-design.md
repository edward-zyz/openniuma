# openNiuMa — AI 自治研发编排器升级设计

> 原 dev-loop / Loop v4，正式命名为 **openNiuMa**（牛马）。
> 合并 Symphony 升级版（编排器内部质量）与 v4 升级版（操作者外部体验）的最优方案。
>
> **v2 — 评审修订版**：根据 9 人虚拟评审（3 QA + 3 PM + 3 Dev）的 120+ 条发现修订，
> 重点解决并发安全、跨平台兼容、进程模型效率三大系统性风险。

**来源：**
- `2026-03-27-dev-loop-symphony-upgrade-design.md` — 基础设施 + 可靠性 + 可配置性
- `2026-03-27-loop-v4-upgrade-design.md` — 可视化 + 数据沉淀 + 通知 + 失败分类 + 快捷入队
- `虚拟reviews/prd-review 结论.md` — 9 人评审结论

---

## 1. 合并原则

| 维度 | 采纳来源 | 理由 |
|------|---------|------|
| 代码架构 | Symphony | 消除 19 个内联 Python 块，模块可测试 |
| 配置管理 | Symphony | workflow.yaml + 热重载，prompt 模板化 |
| Stall 检测 + 取消 | Symphony | v4 缺失此能力 |
| 失败恢复 | **v4** | 6 类失败分类 >> Symphony 的统一重试 |
| 退避策略 | 合并 | Symphony 的指数退避公式 + v4 的按类型差异化 |
| 终端 Dashboard | **v4** | 更丰富（pipeline、颜色、workers 区域） |
| 运行数据沉淀 | **v4** | Symphony 缺失，量化优化效果的基础 |
| 异步通知 | **v4** | Symphony 缺失，无人值守的关键能力 |
| 快捷入队 | **v4** | Symphony 缺失，降低使用门槛 |
| Mermaid 进度报告 | **v4** | Symphony 缺失，可视化分享 |
| Worktree 保留复用 | Symphony | v4 缺失，减少重建开销 |
| 测试 | Symphony | v4 无测试 |

**关键合并点：** v4 的新脚本（dashboard.sh、stats.sh、notify.sh、add-task.sh）调用 `lib/` 模块而非内联 Python。

## 2. 目标架构

```
loop/
├── openniuma.sh                 # 统一 CLI 入口（路由子命令）[SF-3 提前]
├── dev-loop.sh                  # 核心编排器（openniuma.sh start 调用）
├── workflow.yaml                # 运行时配置（Symphony）
├── prompts/                     # Prompt 模板（Symphony）
│   ├── _common-rules.md
│   ├── _common-rules.md.template
│   ├── fast-track.md
│   ├── design-implement.md
│   ├── design.md
│   ├── implement.md
│   ├── verify.md
│   ├── fix.md
│   ├── merge.md
│   ├── merge-fix.md
│   ├── finalize.md
│   └── ci-fix.md
├── lib/                         # Python 模块（Symphony 基础 + v4 扩展）
│   ├── __init__.py
│   ├── config.py                # 配置加载 + 磁盘缓存 + export-env [MF-3]
│   ├── state.py                 # loop-state.json 原子读写 + 统一锁 [MF-1]
│   ├── compat.py                # 跨平台兼容层 [MF-2]
│   ├── json_store.py            # 通用 JSON 文件原子读写（state/stats 复用）[MF-1]
│   ├── inbox.py                 # inbox 扫描 + 任务入队
│   ├── backlog.py               # backlog.md 全量生成
│   ├── reconcile.py             # stall 检测 + 取消 + 孤儿回收
│   ├── retry.py                 # 指数退避 + 按类型差异化 + jitter [NH-7]
│   ├── failure.py               # 失败分类分析（分层匹配 + 置信度）[MF-7]
│   ├── stats.py                 # 运行数据采集 + 查询 + 轮转 [SF-1]
│   ├── notify.py                # 通知发送 + 抑制/聚合 [MF-5]
│   ├── status.py                # 状态汇总（Symphony 基础 + v4 丰富渲染）
│   ├── detect.py                # 项目技术栈自动探测
│   └── test_*.py                # 单元测试
├── stats.json                   # 运行数据（.gitignore）
├── .cache/                      # 配置磁盘缓存 [MF-3]
│   └── workflow.json
├── .env                         # 通知配置（.gitignore）
├── PROGRESS.md                  # 生成产物（.gitignore）
├── loop-state.json
├── inbox/
├── tasks/
├── logs/
└── workers/
```

### 2.1 统一 CLI 入口 [SF-3 提前到 Phase 0]

> **评审共识：** 7 个独立脚本认知负担大，从 Phase 0 提供统一入口。

`openniuma.sh` 是 20 行的路由脚本，所有独立脚本保留但不再是推荐入口：

```bash
#!/usr/bin/env bash
# openniuma.sh — openNiuMa 统一 CLI 入口
set -euo pipefail
LOOP_DIR="$(cd "$(dirname "$0")" && pwd)"

cmd="${1:-help}"; shift 2>/dev/null || true
case "$cmd" in
  start)     exec bash "$LOOP_DIR/dev-loop.sh" "$@" ;;
  init)      exec bash "$LOOP_DIR/init.sh" "$@" ;;
  status)    exec bash "$LOOP_DIR/status.sh" "$@" ;;
  dashboard) exec bash "$LOOP_DIR/dashboard.sh" "$@" ;;
  stats)     exec bash "$LOOP_DIR/stats.sh" "$@" ;;
  add)       exec bash "$LOOP_DIR/add-task.sh" "$@" ;;
  stop)      touch "$LOOP_DIR/inbox/STOP" && echo "⏹ STOP 信号已发送" ;;
  cancel)    touch "$LOOP_DIR/inbox/CANCEL-${1:?需要 task_id}" && echo "🚫 取消信号已发送: $1" ;;
  help|*)
    echo "用法: openniuma.sh <command> [args]"
    echo ""
    echo "Commands:"
    echo "  start [--workers=N]          启动编排器"
    echo "  init [--no-ai] [--dry-run]   初始化新项目"
    echo "  status [--format json]       查看状态"
    echo "  dashboard [-w [interval]]    终端实时看板"
    echo "  stats [--task N]             运行统计"
    echo "  add <description> [--complexity]  快捷入队"
    echo "  stop                         停止编排器"
    echo "  cancel <task_id>             取消任务"
    ;;
esac
```

## 3. 配置外置 + 热重载（from Symphony）

### 3.1 workflow.yaml

```yaml
# loop/workflow.yaml

polling:
  inbox_interval_sec: 60

workers:
  max_concurrent: 5
  stall_timeout_sec: 1800
  max_consecutive_failures: 3

retry:
  base_delay_sec: 10
  max_backoff_sec: 300
  rate_limit_default_wait_sec: 600

# 失败分类重试上限（from v4）
failure:
  max_retries_gate: 3           # 门禁失败
  max_retries_network: 2        # 网络/API 超时
  max_retries_context: 1        # 上下文耗尽
  max_retries_conflict: 2       # Git 冲突
  max_retries_permission: 1     # 权限阻塞
  skip_on_unknown: true         # 未知错误跳过（false=暂停）

worktree:
  base_dir: .trees
  prefix: loop

prompts:
  dir: loop/prompts
  common_rules: loop/prompts/_common-rules.md

# 通知配置（from v4 + 抑制机制 [MF-5]）
notify:
  level: info                   # debug|info|warn|critical
  macos: true                   # macOS 系统通知
  bell: true                    # 终端 Bell
  feishu_webhook: ""            # 飞书 Webhook URL（空=不启用）
  suppress_window_sec: 300      # 同类通知抑制窗口（默认 5 分钟）[MF-5]
  quiet_hours: ""               # 静默时段，如 "23:00-08:00"（空=不启用）[MF-5]
  aggregate_interval_sec: 300   # 非 critical 通知聚合间隔 [MF-5]
  feishu_rate_limit_per_min: 10 # 飞书渠道速率限制 [MF-5]

# 暂停恢复配置 [MF-8]
pause:
  auto_resume_sec: 3600         # 暂停后自动恢复超时（0=不自动恢复）
  partial: true                 # true=只暂停依赖链，独立任务继续
```

### 3.2 热重载机制（from Symphony，修订 [MF-3]）

> **评审问题：** 每次 `python3 config.py` 都是独立进程，热重载的"上次有效配置"在进程间不共享。

**修订方案：磁盘缓存 + export-env 模式**

1. `config.py` 解析 YAML 后将结果写入 `.cache/workflow.json`（JSON 解析比 YAML 快 ~10x）
2. 后续调用先检查缓存 mtime vs workflow.yaml mtime，未变则直接读缓存
3. YAML 解析失败时降级到缓存（而非丢失配置）
4. **export-env 模式**：dev-loop.sh 每轮开头一次性 `eval "$(python3 config.py export-env)"`，后续直接用 shell 变量，避免每轮 5-8 次 Python 进程启动

```bash
# dev-loop.sh 每轮调度循环开头（替代多次 python3 调用）
eval "$(python3 loop/lib/config.py export-env loop/workflow.yaml)"
# 产出 shell 变量：CONF_MAX_CONCURRENT, CONF_STALL_TIMEOUT, CONF_GATE_COMMAND, ...
# 后续直接 $CONF_xxx 引用，零 fork 开销
```

```python
# config.py export-env 输出示例
# CONF_MAX_CONCURRENT=5
# CONF_STALL_TIMEOUT=1800
# CONF_GATE_COMMAND='npm run lint && npm test && npm run build'
# ...
```

### 3.3 workflow.yaml Schema 校验 [SF-8]

`config.py` 加载配置时执行语义校验（不仅是 YAML 语法）：

```python
SCHEMA = {
    "workers.max_concurrent": (int, 1, 20),
    "workers.stall_timeout_sec": (int, 60, 7200),
    "retry.base_delay_sec": (int, 1, 600),
    "failure.max_retries_gate": (int, 0, 10),
    "notify.suppress_window_sec": (int, 0, 3600),
    # ...
}

def validate_config(config: dict) -> list[str]:
    """返回错误列表，空=通过"""
    errors = []
    for key, (typ, min_val, max_val) in SCHEMA.items():
        val = get_nested(config, key)
        if val is not None:
            if not isinstance(val, typ):
                errors.append(f"{key}: 期望 {typ.__name__}，实际 {type(val).__name__}")
            elif not (min_val <= val <= max_val):
                errors.append(f"{key}: {val} 不在范围 [{min_val}, {max_val}]")
    return errors
```

校验失败时 log warning + 降级到缓存，不阻塞运行。

### 3.4 Prompt 模板（from Symphony）

`prompts/*.md` 使用 `{{var}}` 占位符，`config.py render-prompt` 渲染。严格模式：未知变量报错退出。

## 4. 运行时可靠性

### 4.1 并发安全 — 统一 JsonFileStore [MF-1 致命]

> **评审问题：** loop-state.json 和 stats.json 是 5 个 worker + 1 个调度器 + reconcile.py 共同读写的共享状态。
> 现有 dev-loop.sh 用 mkdir 锁，新 state.py 用 Python flock，两套锁互不感知，迁移期间必然 lost update。

**方案：统一 `json_store.py` 模块，所有 JSON 文件操作走此模块，废弃 Bash mkdir 锁。**

```python
# lib/json_store.py — 原子 JSON 文件读写
import fcntl
import json
import os
import tempfile
import time

class JsonFileStore:
    """线程/进程安全的 JSON 文件读写。

    锁策略：独立 .lock 文件 + fcntl.flock（不锁数据文件本身）
    写入策略：临时文件写入 → fsync → os.replace 原子替换
    死锁恢复：锁文件记录持有者 PID，超时后检查 PID 存活性
    """

    LOCK_TIMEOUT_SEC = 10

    def __init__(self, path: str):
        self.path = path
        self.lock_path = path + ".lock"

    def read(self) -> dict:
        """读取 JSON 文件，文件不存在返回空 dict"""
        if not os.path.exists(self.path):
            return {}
        with open(self.lock_path, "w") as lock_fd:
            self._acquire_lock(lock_fd, blocking=True)
            try:
                with open(self.path, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, FileNotFoundError):
                return {}
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)

    def write(self, data: dict) -> None:
        """原子写入 JSON 文件"""
        dir_name = os.path.dirname(self.path) or "."
        with open(self.lock_path, "w") as lock_fd:
            self._acquire_lock(lock_fd, blocking=True)
            try:
                # 写入 PID 到 lock 文件（供死锁检测用）
                lock_fd.write(str(os.getpid()))
                lock_fd.flush()

                # 临时文件写入 → fsync → 原子替换
                fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
                try:
                    with os.fdopen(fd, "w") as tmp_f:
                        json.dump(data, tmp_f, indent=2, ensure_ascii=False)
                        tmp_f.flush()
                        os.fsync(tmp_f.fileno())
                    os.replace(tmp_path, self.path)
                except:
                    os.unlink(tmp_path) if os.path.exists(tmp_path) else None
                    raise
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)

    def update(self, fn) -> dict:
        """read-modify-write 原子操作：fn(data) -> modified_data"""
        with open(self.lock_path, "w") as lock_fd:
            self._acquire_lock(lock_fd, blocking=True)
            try:
                lock_fd.write(str(os.getpid()))
                lock_fd.flush()

                data = {}
                if os.path.exists(self.path):
                    with open(self.path, "r") as f:
                        data = json.load(f)

                data = fn(data)

                dir_name = os.path.dirname(self.path) or "."
                fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
                try:
                    with os.fdopen(fd, "w") as tmp_f:
                        json.dump(data, tmp_f, indent=2, ensure_ascii=False)
                        tmp_f.flush()
                        os.fsync(tmp_f.fileno())
                    os.replace(tmp_path, self.path)
                except:
                    os.unlink(tmp_path) if os.path.exists(tmp_path) else None
                    raise

                return data
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)

    def _acquire_lock(self, lock_fd, blocking: bool = True) -> None:
        """获取文件锁，支持超时和死进程回收"""
        deadline = time.monotonic() + self.LOCK_TIMEOUT_SEC
        while True:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return  # 成功获取
            except BlockingIOError:
                if not blocking:
                    raise
                # 检查持有者是否已死
                if self._is_holder_dead():
                    # 死进程，强制回收（lock 文件 truncate 后重试）
                    try:
                        os.truncate(self.lock_path, 0)
                    except OSError:
                        pass
                    continue
                if time.monotonic() > deadline:
                    raise TimeoutError(
                        f"获取锁超时 ({self.LOCK_TIMEOUT_SEC}s): {self.lock_path}"
                    )
                time.sleep(0.05)

    def _is_holder_dead(self) -> bool:
        """检查锁持有者 PID 是否存活"""
        try:
            with open(self.lock_path, "r") as f:
                pid_str = f.read().strip()
            if not pid_str:
                return False
            pid = int(pid_str)
            os.kill(pid, 0)  # 仅检查，不发信号
            return False  # 进程存活
        except (ValueError, FileNotFoundError):
            return False
        except ProcessLookupError:
            return True  # 进程已死
```

**state.py 和 stats.py 都基于 JsonFileStore：**

```python
# lib/state.py
from .json_store import JsonFileStore

class LoopState:
    def __init__(self, path: str = "loop/loop-state.json"):
        self._store = JsonFileStore(path)

    def get_task(self, task_id: int) -> dict | None:
        state = self._store.read()
        return next((t for t in state.get("tasks", []) if t["id"] == task_id), None)

    def update_task(self, task_id: int, updates: dict) -> None:
        """带版本号的原子更新 [MF-4 防止 kill 后重复执行]"""
        def modifier(state):
            for task in state.get("tasks", []):
                if task["id"] == task_id:
                    # 版本号递增，reconcile 只在版本未变时才重置
                    task["_version"] = task.get("_version", 0) + 1
                    task["_updated_at"] = time.time()
                    task.update(updates)
                    break
            return state
        self._store.update(modifier)
```

**dev-loop.sh 迁移：** Phase 1 必须同步将所有 Bash 中的 mkdir 锁 + jq 写入替换为 `python3 lib/state.py` 调用。两套锁不允许并存。

### 4.2 跨平台兼容层 [MF-2 严重]

> **评审问题：** 多处 macOS-only 代码（`sed -i ''`、`cp -Rc`、`timeout` 命令），Linux/WSL/Docker 直接不可用。

**方案：新增 `lib/compat.py`，所有 OS 相关操作走此模块。**

```python
# lib/compat.py — 跨平台兼容层
import os
import platform
import shutil
import subprocess
import sys

IS_MACOS = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"

def copy_tree(src: str, dst: str) -> None:
    """跨平台目录复制（macOS 优先 APFS clone，Linux 回退 cp -R）"""
    if IS_MACOS:
        result = subprocess.run(["cp", "-Rc", src, dst], capture_output=True)
        if result.returncode == 0:
            return
    # 通用回退
    shutil.copytree(src, dst, dirs_exist_ok=True)

def sed_inplace(filepath: str, pattern: str, replacement: str) -> None:
    """跨平台 sed -i（用 Python 替代，彻底消除平台差异）"""
    import re
    with open(filepath, "r") as f:
        content = f.read()
    content = re.sub(pattern, replacement, content)
    with open(filepath, "w") as f:
        f.write(content)

def run_with_timeout(cmd: list[str], timeout_sec: int, **kwargs) -> subprocess.CompletedProcess:
    """跨平台超时执行（用 Python subprocess.timeout，不依赖 coreutils timeout）"""
    return subprocess.run(cmd, timeout=timeout_sec, **kwargs)

def check_python_version() -> None:
    """检查 Python 版本 >= 3.9"""
    if sys.version_info < (3, 9):
        print(f"❌ 需要 Python >= 3.9，当前 {sys.version}", file=sys.stderr)
        sys.exit(1)

def check_yaml_available() -> bool:
    """检查 PyYAML 可用性，支持多种安装方式"""
    try:
        import yaml
        return True
    except ImportError:
        return False

def install_yaml() -> bool:
    """尝试安装 PyYAML，处理 PEP 668 限制 [MF-2]"""
    # 优先 pipx
    for cmd in [
        [sys.executable, "-m", "pip", "install", "--user", "pyyaml"],
        [sys.executable, "-m", "pip", "install", "pyyaml", "--break-system-packages"],
    ]:
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=30)
            if result.returncode == 0:
                return True
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue
    return False
```

**Bash 脚本中的兼容处理：** 不在 Bash 中重复做平台判断，凡需 OS 相关操作的地方调用 `python3 lib/compat.py <action>`。

**CI 增加 Linux 矩阵测试：**

```yaml
# .github/workflows/ci.yml 增加
strategy:
  matrix:
    os: [macos-latest, ubuntu-latest]
```

### 4.3 Stall 检测 + 任务取消 + 进程组管理（from Symphony，修订 [MF-4]）

> **评审问题：** `kill PID` 无法覆盖进程树，claude CLI 的子进程会成为 orphan。
> kill 后 worker 可能在退出前写入 state，reconcile 随后重置为 pending，导致任务重复执行。

**`reconcile.py`** 在每轮调度循环中执行：

1. **Stall 检测：** worker 日志 mtime 超过 `stall_timeout_sec` → kill 进程组 + 重试
2. **取消检测：** `inbox/CANCEL-{id}` → kill 进程组 + 标记 cancelled
3. **孤儿回收：** in_progress 无活跃 worker → 检查 state 版本号后重置 pending
4. **依赖传播：** blocked 级联

**进程组管理方案 [MF-4]：**

```bash
# worker 启动时：创建独立进程组
start_worker() {
  local task_id="$1" slug="$2"
  # setsid 创建新 session（进程组），使 kill 可以覆盖整个子进程树
  setsid bash -c "
    echo \$\$ > workers/${task_id}.pid
    exec claude ...
  " &
  local pid=$!
  echo "$pid" > "workers/${task_id}.pid"
}

# kill 时：杀整个进程组 + waitpid 确认 + 再修改 state
kill_worker() {
  local task_id="$1"
  local pid_file="workers/${task_id}.pid"
  [ -f "$pid_file" ] || return 0
  local pid
  pid=$(cat "$pid_file")

  # 1. 获取进程组 ID（PGID），杀整个组
  local pgid
  pgid=$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ')
  if [ -n "$pgid" ] && [ "$pgid" != "0" ]; then
    kill -- "-${pgid}" 2>/dev/null || true
  else
    kill "$pid" 2>/dev/null || true
  fi

  # 2. waitpid 确认退出（最多等 5 秒）
  local waited=0
  while kill -0 "$pid" 2>/dev/null && [ "$waited" -lt 5 ]; do
    sleep 1
    waited=$((waited + 1))
  done

  # 3. 强制 SIGKILL（如果还活着）
  kill -0 "$pid" 2>/dev/null && kill -9 -- "-${pgid}" 2>/dev/null || true

  # 4. 确认进程已死后，才修改 state
  rm -f "$pid_file"
}
```

**State 版本号防止重复执行 [MF-4]：**

```python
# reconcile.py 孤儿回收
def reclaim_orphan(state: LoopState, task_id: int):
    task = state.get_task(task_id)
    if not task:
        return
    # 只在版本号未变时才重置（防止 kill 后 worker 退出前的写入被覆盖）
    expected_version = task.get("_version", 0)

    def safe_reset(data):
        for t in data.get("tasks", []):
            if t["id"] == task_id and t.get("_version", 0) == expected_version:
                t["status"] = "pending"
                t["_version"] = expected_version + 1
                break
        return data

    state._store.update(safe_reset)
```

### 4.4 失败分类 + 智能恢复（from v4，修订 [MF-7]）

> **评审问题：** failure.py 仅扫描尾部 50 行，关键错误可能在 50 行之外、关键词可能出现在正常输出中。

**修订方案：分层匹配 + 置信度 + 优先级**

```python
# lib/failure.py — 失败分类分析
import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional

class FailureType(Enum):
    NETWORK = "network"       # 优先级 1（最高）
    CONTEXT = "context"       # 优先级 2
    PERMISSION = "permission" # 优先级 3
    CONFLICT = "conflict"     # 优先级 4
    GATE = "gate"             # 优先级 5
    UNKNOWN = "unknown"       # 优先级 6（最低）

# 优先级顺序：network > context > permission > conflict > gate > unknown
PRIORITY = [
    FailureType.NETWORK,
    FailureType.CONTEXT,
    FailureType.PERMISSION,
    FailureType.CONFLICT,
    FailureType.GATE,
]

@dataclass
class FailureResult:
    type: FailureType
    confidence: float        # 0.0 - 1.0
    evidence: str            # 匹配到的关键行
    line_number: int         # 在日志中的行号

def classify(log_path: str, exit_code: int) -> FailureResult:
    """分层匹配：exit code → 错误上下文行 → 关键词"""

    # 读取日志尾部 200 行 [MF-7: 从 50 行扩大到 200 行]
    lines = _read_tail(log_path, max_lines=200)

    # Layer 1: exit code 快速判定（高置信度）
    if exit_code == 137:  # SIGKILL / OOM
        return FailureResult(FailureType.NETWORK, 0.9, "exit code 137 (SIGKILL/OOM)", -1)

    # Layer 2: 在错误上下文中匹配（中高置信度）
    # 只在 stderr 行、Error/FATAL 标记行附近搜索关键词，排除代码注释和正常输出
    error_context_lines = _extract_error_context(lines)
    result = _match_in_lines(error_context_lines, confidence_base=0.8)
    if result and result.confidence >= 0.6:
        return result

    # Layer 3: 全文关键词搜索（较低置信度）
    result = _match_in_lines(lines, confidence_base=0.4)
    if result and result.confidence >= 0.3:
        return result

    # 低置信度走 unknown，不猜测 [MF-7]
    return FailureResult(FailureType.UNKNOWN, 0.0, "无法判定失败类型", -1)

def _extract_error_context(lines: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """提取错误上下文行：stderr 标记、Error/FATAL/WARN 行及前后 2 行"""
    error_indices = set()
    error_patterns = re.compile(
        r"(^(ERROR|FATAL|WARN|error|Error)\b|^E\s|stderr:|exit code [1-9])", re.MULTILINE
    )
    for i, (line_no, line) in enumerate(lines):
        if error_patterns.search(line):
            for j in range(max(0, i-2), min(len(lines), i+3)):
                error_indices.add(j)
    return [lines[i] for i in sorted(error_indices)]

# 匹配规则：每种类型的关键词 + 排除词
PATTERNS = {
    FailureType.NETWORK: {
        "keywords": [r"ETIMEDOUT", r"ECONNREFUSED", r"ECONNRESET", r"rate limit",
                     r"429\b", r"503\b", r"socket hang up", r"network error"],
        "excludes": [r"#.*ETIMEDOUT", r"//.*ETIMEDOUT"],  # 排除代码注释
    },
    FailureType.CONTEXT: {
        "keywords": [r"context window", r"token limit", r"max.turns",
                     r"conversation is too long", r"context length exceeded"],
        "excludes": [],
    },
    FailureType.PERMISSION: {
        "keywords": [r"permission denied", r"dangerously-skip", r"EACCES",
                     r"Operation not permitted"],
        "excludes": [r"chmod", r"# permission"],
    },
    FailureType.CONFLICT: {
        "keywords": [r"CONFLICT\s+\(", r"merge conflict", r"Merge conflict in"],
        "excludes": [r'["\']CONFLICT', r"#.*CONFLICT", r"//.*CONFLICT"],  # 排除字符串和注释
    },
    FailureType.GATE: {
        "keywords": [r"npm run (lint|test|build).*exit", r"npm ERR!",
                     r"FAIL\s+(src|test)/", r"eslint.*error", r"tsc.*error TS"],
        "excludes": [],
    },
}

def _match_in_lines(lines: list[tuple[int, str]], confidence_base: float) -> Optional[FailureResult]:
    """按优先级顺序匹配，返回最高优先级的结果"""
    for ftype in PRIORITY:
        patterns = PATTERNS[ftype]
        for line_no, line in reversed(lines):  # 从尾部向上搜索
            # 检查排除词
            if any(re.search(excl, line) for excl in patterns["excludes"]):
                continue
            for kw in patterns["keywords"]:
                if re.search(kw, line, re.IGNORECASE):
                    return FailureResult(ftype, confidence_base, line.strip()[:200], line_no)
    return None
```

**失败分类策略表（不变）：**

| 失败类型 | 策略 | 退避 |
|----------|------|------|
| `gate` | 重试，注入错误上下文 | 指数退避 |
| `context` | 清空进度，断点续传 | 立即 |
| `permission` | 修复参数后重试 | 立即 |
| `conflict` | 重试 MERGE，注入冲突提示 | 10s |
| `network` | 等待后重试 | 60s 固定 |
| `unknown` | 按配置：跳过或暂停（低置信度不猜测）| N/A |

**与 Symphony 退避的合并 + jitter [NH-7]：**
- `gate` 类型使用 Symphony 的指数退避：`min(base * 2^(n-1), max_backoff) + random(0, base)`
- 其他类型使用 v4 的固定退避 + `random(0, 5)` jitter
- `retry.py` 接受 `failure_type` 参数和 `confidence` 参数，低置信度时更保守

**重试时 Prompt 注入（from v4）：**

```
⚠️ 上次尝试失败 (第 {attempt}/{max_retries} 次)。
失败类型: {failure_type} (置信度: {confidence:.0%})
错误摘要: {session log 最后 10 行}
请避免相同错误，调整策略后重试。
```

**降级流程（修订 [MF-8]）：**

```
重试次数 >= 上限 ?
  ├── context → 清空 implement_progress，新会话断点续传
  ├── 无下游依赖 / skip_on_unknown=true → blocked + notify(warn) + 跳过
  └── 有下游依赖 →
        ├── partial=true [MF-8] → 只暂停该依赖链，独立任务继续
        │     ├── inbox/RESUME 信号 → 重置重试计数，恢复该链
        │     └── auto_resume_sec 超时 → 自动恢复（默认 1h）
        └── partial=false → notify(critical) + 全局暂停 loop
              └── 暂停期间 reconcile + inbox 扫描继续运行 [MF-8]
```

**RESUME 信号机制 [MF-8]：**

```bash
# 恢复被暂停的任务链
touch loop/inbox/RESUME-{task_id}     # 恢复单个任务
touch loop/inbox/RESUME                # 恢复所有暂停的任务

# inbox.py 扫描逻辑增加
# RESUME-{id} → 重置该任务的 retry 计数 + 状态改为 pending
# RESUME → 重置所有 blocked 任务
```

### 4.5 Worktree 保留复用（from Symphony）

| Worker 结果 | Worktree | 数据库 |
|-------------|----------|--------|
| 成功 + 合入 dev | 清理 | 清理 |
| 失败 / 需要重试 | **保留** | 保留 |
| 手动取消 / STOP | 清理 | 清理 |

### 4.6 Shell 安全 [MF-6 严重]

> **评审问题：** `bash -c "$hook"` 从 YAML 读取的多行脚本经二次展开可能注入。
> `rm -rf "$wt_path"` 未做路径安全校验。

**修订方案：**

**1. Hook 通过临时文件执行（替代 `bash -c`）：**

```bash
execute_hook() {
  local hook_name="$1" hook_content="$2" timeout_sec="${3:-120}"
  [ -z "$hook_content" ] && return 0

  # 写入临时文件，避免引号嵌套/二次展开
  local hook_file
  hook_file=$(mktemp "${TMPDIR:-/tmp}/openniuma-hook-XXXXXX.sh")
  echo "$hook_content" > "$hook_file"
  chmod +x "$hook_file"

  # 通过文件执行（不是 bash -c）
  local result=0
  python3 -c "
import subprocess, sys
try:
    subprocess.run(['bash', '$hook_file'], timeout=$timeout_sec, check=True)
except subprocess.TimeoutExpired:
    print('❌ Hook 超时 (${timeout_sec}s)', file=sys.stderr)
    sys.exit(1)
except subprocess.CalledProcessError as e:
    sys.exit(e.returncode)
" || result=$?

  rm -f "$hook_file"
  return $result
}
```

**2. rm -rf 路径安全校验：**

```bash
safe_remove_worktree() {
  local wt_path="$1"

  # 路径必须在预期 base_dir 下
  local real_base real_wt
  real_base=$(cd "$WORKTREE_BASE_DIR" && pwd -P)
  real_wt=$(cd "$wt_path" 2>/dev/null && pwd -P || echo "$wt_path")

  if [[ "$real_wt" != "${real_base}/"* ]]; then
    log "❌ 安全检查失败：$wt_path 不在 $WORKTREE_BASE_DIR 下，拒绝删除"
    return 1
  fi

  git -C "$MAIN_REPO_DIR" worktree remove "$wt_path" --force 2>/dev/null || {
    rm -rf "$wt_path"
    git -C "$MAIN_REPO_DIR" worktree prune
  }
}
```

## 5. 可观测性

### 5.1 终端 Dashboard（from v4，调用 lib/）

```bash
bash loop/openniuma.sh dashboard         # 单次渲染
bash loop/openniuma.sh dashboard -w      # 每 5s 自动刷新
bash loop/openniuma.sh dashboard -w 10   # 每 10s 刷新
```

渲染区域（from v4 原样保留）：

```
┌─ Dev Loop Dashboard ──────────────────────────────┐
│  Header: 分支 / 阶段(emoji) / 当前任务 / 更新时间  │
├────────────────────────────────────────────────────┤
│  Progress: ██████████░░░░░░░░░░  2/4 (50%)        │
├────────────────────────────────────────────────────┤
│  Task List: 表格，每行一个任务                      │
│    #55 刷新热力图        ✅ DONE    低              │
│    #56 评分不一致调试     ✅ DONE    中              │
│    #57 移动训练入口       🔄 ACTIVE  低              │
│    #58 首页关键词搜索     ⏳ PENDING 中              │
├────────────────────────────────────────────────────┤
│  Pipeline: 当前任务的阶段流水线                      │
│    ⚡FAST → [🔨IMPL] → 🔍VERIFY → 🔀MERGE         │
├────────────────────────────────────────────────────┤
│  Checkpoint: chunk/task/commit 进度                  │
│  Retry: verify_attempts / merge_fix_attempts        │
├────────────────────────────────────────────────────┤
│  Workers (并行模式):                                 │
│    Worker #55  🔨 IMPLEMENT  PID:12345  运行 23m    │
│    Worker #57  🔍 VERIFY     PID:12347  运行 8m     │
│    容量: 2/5 active                                  │
├────────────────────────────────────────────────────┤
│  Recent Sessions: 最近 5 个 session 摘要             │
│  Stats: $8.45 | 3.1h/任务                           │
└────────────────────────────────────────────────────┘
```

**实现变化：** 原 v4 用 jq 直接解析 JSON，合并版改为调用 `python3 loop/lib/status.py --format dashboard`，Python 端负责数据聚合，bash 端负责 ANSI 渲染。

### 5.2 简洁状态查看（from Symphony）

```bash
bash loop/openniuma.sh status              # 快速查看（无颜色，适合管道）
bash loop/openniuma.sh status --format json  # JSON 输出
```

与 dashboard 的区别：status 是纯文本、无 ANSI、适合 `grep`/管道；dashboard 是彩色交互式。

### 5.3 运行数据沉淀（from v4，修订 [SF-1/SF-7/MF-3]）

**stats.json 结构（from v4，增加轮转机制 [SF-1]）：**

```jsonc
{
  "max_sessions": 500,          // [SF-1] 超出自动归档到 stats-archive-{date}.json
  "sessions": [
    {
      "session_id": "s-20260326-142000",  // [SF-2] 关联 ID
      "task_id": 55,
      "task_name": "刷新热力图",
      "phase": "FAST_TRACK",
      "started_at": "2026-03-26T14:20:00.000Z",
      "ended_at": "2026-03-26T14:43:22.000Z",
      "duration_sec": 1402,
      "exit_code": 0,
      "cost_usd": null,          // [SF-7] 见下文 cost 采集方案
      "failure_type": null,
      "failure_confidence": null, // [MF-7] 置信度
      "worker_id": null,
      "attempt": 1
    }
  ],
  "tasks": [
    {
      "id": 55,
      "name": "刷新热力图",
      "complexity": "低",
      "path": "FAST_TRACK",
      "total_sessions": 3,
      "total_duration_sec": 4200,
      "total_cost_usd": 1.23,
      "verify_attempts": 1,
      "verify_pass_first_try": true,
      "merge_fix_attempts": 0,
      "started_at": "2026-03-26T14:20:00.000Z",
      "completed_at": "2026-03-26T15:30:00.000Z"
    }
  ]
}
```

**stats.py 接口改用 JSON stdin [MF-3]：**

```bash
# 采集（旧：12 个位置参数 → 新：JSON stdin）
echo '{"task_id":55,"task_name":"刷新热力图","phase":"FAST_TRACK",...}' | \
  python3 loop/lib/stats.py record-session loop/stats.json

# 任务完成时汇总
python3 loop/lib/stats.py finalize-task loop/stats.json 55

# 查询
python3 loop/lib/stats.py summary loop/stats.json                   # 全量摘要
python3 loop/lib/stats.py summary loop/stats.json --task 55          # 单任务
python3 loop/lib/stats.py summary loop/stats.json --format json      # JSON 输出
```

**数据轮转 [SF-1]：** sessions 超过 `max_sessions`（默认 500）时，自动将最旧的一半归档到 `stats-archive-{date}.json`，主文件保持轻量。

**cost_usd 采集方案 [SF-7]：**
- Claude CLI 目前不直接输出 token 用量或费用
- 方案：从 session log 中提取 Claude 输出的 token 统计行（如果有），否则 `cost_usd = null`
- stats 展示时：有数据显示，null 显示 "N/A"（不显示 $0.00 以免误导）
- 后续 Claude CLI 支持 `--usage` 输出后再对接

### 5.4 结构化日志（from Symphony，增强 [SF-2]）

```
[2026-03-27 10:00:00] [scheduler] [sid:s-20260327-100000] 活跃 worker: 2/5 [#55 #58]
[2026-03-27 10:00:01] [worker:55] [sid:s-20260327-100001] [tid:55] 启动 (refresh-heatmap)
[2026-03-27 10:05:00] [reconcile] [sid:s-20260327-100000] [tid:58] Worker #58 卡死 1800s，终止
[2026-03-27 10:05:01] [failure]   [sid:s-20260327-100001] [tid:58] 分析: gate (置信度: 80%)
[2026-03-27 10:05:02] [retry]     [sid:s-20260327-100001] [tid:58] 第 2 次重试，等待 20s
```

**增强 [SF-2]：** 每行日志增加 `sid`（session_id）和 `tid`（task_id），多 worker 并行时可关联分析。

汇总日志写入 `logs/orchestrator.log`。后续迭代考虑改为 JSONL 格式支持机器解析。

### 5.5 Mermaid 进度报告（from v4）

```bash
bash loop/openniuma.sh stats --mermaid    # → loop/PROGRESS.md
```

内容结构与 v4 完全一致（概览、任务状态图、阶段流水线、Session 甘特图、审查记录）。新增：从 stats.json 读取统计数据展示。

## 6. 异步通知（from v4，增强 [MF-5]）

### 6.1 notify.py — 统一通知模块（含抑制/聚合）

> **评审问题：** 5 worker 并行场景下，同一 task_id + failure_type 可能在短时间内触发 10+ 条通知。

```python
# lib/notify.py — 通知发送 + 抑制/聚合
import time
import json
import os
from collections import defaultdict

class NotifyManager:
    """通知管理器，支持去重、聚合、速率限制"""

    def __init__(self, config: dict):
        self.config = config.get("notify", {})
        self.suppress_window = self.config.get("suppress_window_sec", 300)
        self.aggregate_interval = self.config.get("aggregate_interval_sec", 300)
        self.feishu_rate_limit = self.config.get("feishu_rate_limit_per_min", 10)
        self.quiet_hours = self._parse_quiet_hours(self.config.get("quiet_hours", ""))

        # 状态追踪（内存中，进程生命周期内有效）
        self._recent: dict[str, float] = {}       # dedup_key → last_sent_time
        self._pending: list[dict] = []             # 待聚合的非 critical 通知
        self._last_aggregate_flush: float = 0
        self._feishu_sent_times: list[float] = []  # 飞书速率限制追踪

    def send(self, level: str, title: str, body: str,
             task_id: int | None = None, failure_type: str | None = None) -> None:
        """发送通知，自动应用抑制/聚合规则"""

        # 静默时段检查（critical 除外）
        if level != "critical" and self._in_quiet_hours():
            return

        # 同类通知去重 [MF-5]：task_id + failure_type 在窗口内合并
        dedup_key = f"{task_id}:{failure_type}:{level}"
        now = time.time()
        if dedup_key in self._recent:
            elapsed = now - self._recent[dedup_key]
            if elapsed < self.suppress_window:
                # 抑制，但更新计数
                self._increment_suppressed(dedup_key)
                return
        self._recent[dedup_key] = now

        # critical 立即发送，其他进入聚合队列 [MF-5]
        if level == "critical":
            self._dispatch(level, title, body)
        else:
            self._pending.append({"level": level, "title": title, "body": body, "time": now})
            self._maybe_flush_aggregated()

    def _maybe_flush_aggregated(self) -> None:
        """定期刷新聚合队列"""
        now = time.time()
        if now - self._last_aggregate_flush < self.aggregate_interval:
            return
        if not self._pending:
            return

        self._last_aggregate_flush = now

        # 合并同级别通知为一条摘要
        if len(self._pending) == 1:
            msg = self._pending[0]
            self._dispatch(msg["level"], msg["title"], msg["body"])
        else:
            summary_lines = [f"  - {m['title']}" for m in self._pending[-10:]]
            count = len(self._pending)
            self._dispatch(
                "info",
                f"最近 {count} 条通知汇总",
                "\n".join(summary_lines)
            )
        self._pending.clear()

    def _dispatch(self, level: str, title: str, body: str) -> None:
        """实际发送到各渠道"""
        # 终端 Bell（始终）
        if self.config.get("bell", True):
            print("\a", end="", flush=True)

        # macOS 系统通知
        if self.config.get("macos", True):
            _send_macos_notification(title, body)

        # 飞书 Webhook（带速率限制 [MF-5]）
        webhook = self.config.get("feishu_webhook", "")
        if webhook and self._check_feishu_rate_limit():
            _send_feishu(webhook, level, title, body)

    def _check_feishu_rate_limit(self) -> bool:
        """飞书速率限制：每分钟最多 N 条"""
        now = time.time()
        self._feishu_sent_times = [t for t in self._feishu_sent_times if now - t < 60]
        if len(self._feishu_sent_times) >= self.feishu_rate_limit:
            return False
        self._feishu_sent_times.append(now)
        return True
```

### 6.2 触发时机（from v4，增加失败分类信息）

| 事件 | 级别 | 消息 |
|------|------|------|
| 任务完成 | info | `✅ #55 刷新热力图 完成 (1.8h, $1.20)` |
| 任务阻塞 | warn | `🚫 #58 首页搜索 被阻塞 (依赖 #55)` |
| 会话失败 | warn | `⚠️ #57 IMPLEMENT 失败 [gate: 门禁, 80%], 重试 2/3` |
| 连续失败停止 | critical | `🛑 #57 连续 3 次 [gate] 失败，依赖链已暂停` |
| **Stall 检测** | warn | `⏰ Worker #58 卡死 30m，已终止并重试` |
| **任务取消** | info | `🚫 #60 已取消 (CANCEL 信号)` |
| **暂停恢复** | info | `▶️ #57 已恢复 (RESUME 信号)` |
| 全部完成 | info | `🎉 4 个任务全部完成! 12.3h, $8.45` |

### 6.3 三层通知渠道（from v4）

1. **macOS 系统通知**（默认开启）— `osascript`
2. **飞书 Webhook**（可选）— `curl`，critical 级别附带错误日志，带速率限制
3. **终端 Bell**（始终）— `printf '\a'`

配置在 `workflow.yaml` 的 `notify` 节，也支持 `loop/.env` 覆盖。

## 7. 快捷入队（from v4，增强 [SF-5]）

### 7.1 add-task.sh

```bash
# 一句话入队
bash loop/openniuma.sh add "支持自定义热力图半径"

# 指定属性
bash loop/openniuma.sh add "支持自定义热力图半径" --type feat --complexity 低

# 从 GitHub Issue 导入
bash loop/openniuma.sh add --from-issue 42

# 批量导入
bash loop/openniuma.sh add --batch tasks.txt
```

**分级模板 [SF-5]：** 按 complexity 生成不同详细程度的模板：

| complexity | 模板内容 |
|-----------|---------|
| 低 | 标题 + 验收标准（2-3 行） |
| 中 | 标题 + 背景 + 验收标准 + 技术提示 |
| 高 | 标题 + 背景 + 方案要点 + 验收标准 + 风险提示 |

支持 `--ai` 标志，调用 claude 自动补充任务描述。

**实现变化 vs v4：** 调用 `python3 loop/lib/inbox.py add-task` 而非内联 Python。slug 生成、ID 分配、frontmatter 构造都在 `inbox.py` 中。

## 8. 可移植性设计 — 拷贝即用

### 8.1 问题

当前 dev-loop.sh 中有 **30+ 处 POI 项目硬编码**：

| 类别 | 数量 | 典型例子 |
|------|------|---------|
| CI 门禁命令 | 9 处 | `npm run lint && npm test && npm run build && npx tsc --noEmit -p frontend` |
| Worktree 初始化 | 5 处 | `npm install`、`createdb poi_dev_loop_*`、`.env` 配置 |
| Worktree 清理 | 2 处 | `dropdb poi_dev_loop_*` |
| 代码规范 | 5 处 | ESM .js 扩展名、Tailwind、tokens.json、cn()、button.tsx |
| 移动端规范 | 2 处 | App.tsx PC/移动端独立组件树 |
| 时间格式 | 1 处 | ISO 8601 + poi_cache 例外 |
| 输出路径 | 2 处 | `docs/superpowers/specs/`、`docs/superpowers/plans/` |
| 数据库凭据 | 1 处 | `postgresql://poi:poi_dev_password@...` |
| 主分支名 | 1 处 | `master`（有的项目是 `main`） |

把 loop 拷贝到另一个项目后，需要改 30+ 处才能跑——这不是"拷贝即用"。

### 8.2 设计原则

**引擎和项目配置彻底分离，借鉴 Symphony 的 WORKFLOW.md 模型：**

```
loop/                          ← 引擎（项目无关，可整目录拷贝）
├── dev-loop.sh                  不含任何项目特定内容
├── openniuma.sh                 统一 CLI 入口
├── lib/*.py                     通用编排逻辑
├── dashboard.sh, stats.sh ...   通用工具
└── prompts/
    ├── _common-rules.md.template  模板文件
    ├── fast-track.md            通用 phase 流程（引用 {{gate_command}}, {{common_rules}}）
    ├── design-implement.md
    └── ...

workflow.yaml                  ← 项目配置（项目专属，需要用户填写）
prompts/_common-rules.md       ← 项目规范（项目专属，需要用户填写）
```

### 8.3 workflow.yaml 扩展 — 项目配置区

在现有配置基础上新增 `project` 和 `hooks` 节：

```yaml
# workflow.yaml — 完整 schema

# ── 项目配置（每个项目必须填写）──
project:
  name: "Location Scout"               # 项目名，用于通知和日志
  main_branch: master                   # 主分支名（master / main）
  dev_branch_prefix: "dev/backlog-batch"  # dev 集成分支前缀
  feat_branch_prefix: "feat"            # 功能分支前缀
  gate_command: |                       # CI 门禁命令（必须全部 exit 0）
    npm run lint && npm test && npm run build
    npx tsc --noEmit -p frontend
  spec_dir: "docs/superpowers/specs"    # spec 输出目录
  plan_dir: "docs/superpowers/plans"    # plan 输出目录

# ── Worktree 生命周期 Hooks ──
hooks:
  after_create: |
    # Worktree 创建后执行（初始化依赖、数据库等）
    # cwd = worktree 目录，$SLUG 和 $MAIN_REPO 可用

    # 依赖安装：跨平台处理 [MF-2]
    if diff -q "$MAIN_REPO/package-lock.json" "package-lock.json" >/dev/null 2>&1; then
      python3 "$MAIN_REPO/loop/lib/compat.py" copy-tree "$MAIN_REPO/node_modules" node_modules || npm install --prefer-offline
      for ws in frontend backend; do
        [ -d "$MAIN_REPO/$ws/node_modules" ] && \
          python3 "$MAIN_REPO/loop/lib/compat.py" copy-tree "$MAIN_REPO/$ws/node_modules" "$ws/node_modules" || true
      done
    else
      npm install --prefer-offline
    fi

    # 独立数据库
    DB_NAME="poi_dev_loop_${SLUG//-/_}"
    createdb "$DB_NAME" 2>/dev/null || true

    # 配置 .env [MF-2: 用 Python sed 替代 sed -i '']
    if [ -f backend/.env.example ]; then
      cp backend/.env.example backend/.env
      python3 "$MAIN_REPO/loop/lib/compat.py" sed-inplace backend/.env \
        "poi_dev" "$DB_NAME"
    fi

  before_remove: |
    # Worktree 删除前执行（清理数据库等）
    DB_NAME="poi_dev_loop_${SLUG//-/_}"
    dropdb --if-exists "$DB_NAME" 2>/dev/null || true

  timeout_sec: 120                      # Hook 超时（防止 npm install 卡死）

# ── 编排器配置（通常不需要改）──
polling:
  inbox_interval_sec: 60

workers:
  max_concurrent: 5
  stall_timeout_sec: 1800
  max_consecutive_failures: 3

retry:
  base_delay_sec: 10
  max_backoff_sec: 300
  rate_limit_default_wait_sec: 600

failure:
  max_retries_gate: 3
  max_retries_network: 2
  max_retries_context: 1
  max_retries_conflict: 2
  max_retries_permission: 1
  skip_on_unknown: true

worktree:
  base_dir: .trees
  prefix: loop

prompts:
  dir: loop/prompts
  common_rules: loop/prompts/_common-rules.md

notify:
  level: info
  macos: true
  bell: true
  feishu_webhook: ""
  suppress_window_sec: 300
  quiet_hours: ""
  aggregate_interval_sec: 300
  feishu_rate_limit_per_min: 10

pause:
  auto_resume_sec: 3600
  partial: true
```

### 8.4 _common-rules.md 模板

提供一个模板文件，用户拷贝后按自己项目填写：

```markdown
<!-- loop/prompts/_common-rules.md.template -->
<!-- 拷贝为 _common-rules.md，填入你的项目规范 -->

## 硬性红线
- 禁止合并代码到 {{main_branch}}
- 禁止请求用户输入或等待用户操作（非交互模式）
<!-- 在下面添加你的项目特定规则，例如：
- 后端 ESM：本地 import 必须用 .js 扩展名
- 业务逻辑使用中文注释
-->

## 硬性门禁
运行以下命令，必须全部 exit 0：
```bash
{{gate_command}}
```

<!-- 在下面添加你的项目特定规范，例如：
## 时间格式
## 移动端适配
## UI 规范
## 代码风格
-->
```

### 8.5 Prompt 模板中的项目引用

所有 prompt 模板中的项目特定内容通过变量注入，不硬编码：

| 变量 | 来源 | 用途 |
|------|------|------|
| `{{gate_command}}` | `project.gate_command` | CI 门禁命令 |
| `{{main_branch}}` | `project.main_branch` | 主分支引用 |
| `{{dev_branch}}` | `state.dev_branch` | dev 集成分支 |
| `{{feat_prefix}}` | `project.feat_branch_prefix` | 功能分支前缀 |
| `{{spec_dir}}` | `project.spec_dir` | spec 输出目录 |
| `{{plan_dir}}` | `project.plan_dir` | plan 输出目录 |
| `{{common_rules}}` | `_common-rules.md` 文件内容 | 项目规范注入 |
| `{{slug}}` | 从当前任务计算 | 分支/文件命名 |
| `{{branch}}` | `state.branch` | 当前功能分支 |

示例（fast-track.md 改造前后对比）：

```diff
- git checkout {dev_branch} && git pull && npm install
- git checkout -b feat/{slug}
+ git checkout {{dev_branch}} && git pull
+ git checkout -b {{feat_prefix}}/{{slug}}

- npm run lint && npm test && npm run build && npx tsc --noEmit -p frontend
+ {{gate_command}}
```

### 8.6 dev-loop.sh 中 Hooks 替代硬编码

**现状（ensure_worktree 函数 L89-L163）：** npm install、createdb、.env 全部硬编码。

**改造后：**

```bash
ensure_worktree() {
  local slug="$1" dev_branch="$2"
  local wt_path="${WORKTREE_BASE_DIR}/${WORKTREE_PREFIX}-${slug}"

  if [ -d "$wt_path" ]; then
    echo "$wt_path"
    return 0
  fi

  # 创建 worktree（通用逻辑，不含项目特定内容）
  mkdir -p "$WORKTREE_BASE_DIR"
  git -C "$MAIN_REPO_DIR" worktree add --detach "$wt_path" "$dev_branch" || return 1

  # 执行 after_create hook（项目特定初始化）[MF-6: 通过临时文件执行]
  local hook
  hook=$(python3 "${MAIN_REPO_DIR}/loop/lib/config.py" get-hook after_create)
  if [ -n "$hook" ]; then
    ( cd "$wt_path" && SLUG="$slug" MAIN_REPO="$MAIN_REPO_DIR" \
      execute_hook "after_create" "$hook" "$CONF_HOOK_TIMEOUT" ) || {
      log "❌ after_create hook 失败"
      safe_remove_worktree "$wt_path"
      return 1
    }
  fi

  echo "$wt_path"
}

cleanup_worktree() {
  local slug="$1"
  local wt_path="${WORKTREE_BASE_DIR}/${WORKTREE_PREFIX}-${slug}"
  [ -d "$wt_path" ] || return 0

  # 执行 before_remove hook [MF-6: 通过临时文件执行]
  local hook
  hook=$(python3 "${MAIN_REPO_DIR}/loop/lib/config.py" get-hook before_remove)
  if [ -n "$hook" ]; then
    ( cd "$wt_path" && SLUG="$slug" MAIN_REPO="$MAIN_REPO_DIR" \
      execute_hook "before_remove" "$hook" 30 ) 2>/dev/null || true
  fi

  safe_remove_worktree "$wt_path"
}
```

### 8.7 init.sh — 自动探测 + AI 生成，一条命令完成

**目标：** `bash loop/init.sh` 一条命令，不需要手动编辑任何文件。

**三层策略：**

```
Layer 1: 确定性探测（秒级，不依赖 AI）
  → 技术栈、主分支、CI 命令、依赖安装方式

Layer 2: AI 生成（30 秒，调用 claude）
  → 从 CLAUDE.md / README / CI 配置中提取项目规范 → _common-rules.md
  → 推断 worktree hooks（数据库、环境变量等非通用初始化）

Layer 3: 兜底默认值（无探测结果时）
  → 合理默认配置，确保至少能启动
```

**Layer 1 探测规则：**

| 探测对象 | 判定方式 | 输出 |
|----------|---------|------|
| 技术栈 | 文件存在检测 | `project.stack` |
| 主分支 | `git symbolic-ref refs/remotes/origin/HEAD` | `project.main_branch` |
| Gate 命令 | CI 配置 > package.json scripts > 技术栈默认 | `project.gate_command` |
| 依赖安装 | 技术栈 → 预设模板 | `hooks.after_create` |
| 项目名 | `basename $(git remote get-url origin)` 或目录名 | `project.name` |
| spec/plan 目录 | 扫描 `docs/` 下已有目录 | `project.spec_dir` |

**技术栈探测矩阵：**

| 文件 | 技术栈 | gate_command 默认 | after_create 默认 |
|------|--------|------------------|------------------|
| `package.json` | Node.js | 从 scripts 中拼装（见下文） | npm install |
| `go.mod` | Go | `go test ./... && go vet ./...` | `go mod download` |
| `Cargo.toml` | Rust | `cargo test && cargo clippy -- -D warnings` | `cargo fetch` |
| `pyproject.toml` / `requirements.txt` | Python | `pytest && ruff check .` | `pip install -r requirements.txt` |
| `Gemfile` | Ruby | `bundle exec rspec && bundle exec rubocop` | `bundle install` |
| `pom.xml` / `build.gradle` | Java | `./mvnw test` / `./gradlew test` | `./mvnw install -DskipTests` |

**Node.js gate 命令智能拼装：**

```python
# 读取 package.json scripts
scripts = json.load("package.json")["scripts"]
parts = []
if "lint" in scripts:     parts.append("npm run lint")
if "test" in scripts:     parts.append("npm test")
if "build" in scripts:    parts.append("npm run build")
if "typecheck" in scripts: parts.append("npm run typecheck")
# 检查是否有 TypeScript 前端
if exists("tsconfig.json") and exists("frontend/"):
    parts.append("npx tsc --noEmit -p frontend")
gate_command = " && ".join(parts)
```

**CI 配置探测（优先级最高）：**

```python
# 从已有 CI 配置中提取真实的 gate 命令
ci_files = [
    ".github/workflows/ci.yml",      # GitHub Actions
    ".github/workflows/test.yml",
    ".gitlab-ci.yml",                 # GitLab CI
    "Makefile",                       # make test
    "Justfile",                       # just test
]
# 解析 YAML/Makefile，提取 test/lint/build 步骤中的 run 命令
```

**完整 init.sh 流程（修订 [SF-4/SF-6]）：**

```bash
#!/usr/bin/env bash
set -euo pipefail

LOOP_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$LOOP_DIR")"
USE_AI=true
DRY_RUN=false
for arg in "$@"; do
  case "$arg" in
    --no-ai) USE_AI=false ;;
    --dry-run) DRY_RUN=true ;;  # [SF-4]
  esac
done

echo "🚀 初始化 dev-loop..."

# ── 0. Python 版本检查 [MF-2] ──
python3 -c "
import sys
if sys.version_info < (3, 9):
    print(f'❌ 需要 Python >= 3.9，当前 {sys.version}')
    sys.exit(1)
print(f'  ✅ Python {sys.version_info.major}.{sys.version_info.minor}')
"

# ── 1. 创建目录 ──
mkdir -p loop/inbox loop/tasks loop/logs loop/reviews loop/prompts loop/.cache

# ── 2. Layer 1: 确定性探测（一次调用 detect.py，输出全部结果）[SF-6] ──
echo "🔍 探测项目配置..."

# 主分支
MAIN_BRANCH=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's|refs/remotes/origin/||' || echo "main")
echo "  主分支: $MAIN_BRANCH"

# 项目名
PROJECT_NAME=$(basename "$(git remote get-url origin 2>/dev/null | sed 's/\.git$//')" 2>/dev/null || basename "$REPO_DIR")
echo "  项目名: $PROJECT_NAME"

# [SF-6] 一次性调用 detect.py，eval 所有变量（替代 6 次 python3 -c 管道解析）
eval "$(python3 "${LOOP_DIR}/lib/detect.py" "$REPO_DIR" --shell-vars)"
# 产出：DETECT_STACK, DETECT_GATE, DETECT_AFTER_CREATE, DETECT_BEFORE_REMOVE, DETECT_SPEC_DIR, DETECT_PLAN_DIR
echo "  技术栈: $DETECT_STACK"
echo "  Gate: $DETECT_GATE"

# ── 3. dry-run 验证 [SF-4] ──
if [ "$DRY_RUN" = true ]; then
  echo ""
  echo "🧪 Dry-run 验证："
  echo "  运行 gate_command 检查..."
  if eval "$DETECT_GATE" 2>&1 | tail -5; then
    echo "  ✅ Gate 命令通过"
  else
    echo "  ❌ Gate 命令失败（exit $?），请检查配置"
  fi
  echo ""
  echo "📋 将生成的配置预览（实际未写入文件）："
  echo "  workflow.yaml: project.name=$PROJECT_NAME, main_branch=$MAIN_BRANCH"
  echo "  gate_command: $DETECT_GATE"
  echo "  after_create hook: $(echo "$DETECT_AFTER_CREATE" | wc -l) 行"
  echo ""
  echo "  如确认无误，去掉 --dry-run 重新运行。"
  exit 0
fi

# ── 4. 生成 workflow.yaml ──
if [ ! -f loop/workflow.yaml ]; then
  python3 "${LOOP_DIR}/lib/config.py" generate-workflow \
    --name "$PROJECT_NAME" \
    --main-branch "$MAIN_BRANCH" \
    --gate-command "$DETECT_GATE" \
    --after-create "$DETECT_AFTER_CREATE" \
    --before-remove "$DETECT_BEFORE_REMOVE" \
    --spec-dir "$DETECT_SPEC_DIR" \
    --plan-dir "$DETECT_PLAN_DIR" \
    > loop/workflow.yaml
  echo "  ✅ workflow.yaml 已生成"
else
  echo "  ⏭ workflow.yaml 已存在，跳过"
fi

# ── 5. Layer 2: AI 生成 _common-rules.md ──
if [ ! -f loop/prompts/_common-rules.md ]; then
  if [ "$USE_AI" = true ] && command -v claude >/dev/null 2>&1; then
    echo "🤖 调用 Claude 分析项目规范..."
    claude -p "分析当前项目的 CLAUDE.md、README、lint 配置等，
生成 loop/prompts/_common-rules.md。
这个文件会注入到 AI 编码 agent 的 prompt 中作为项目规范。
格式参考 loop/prompts/_common-rules.md.template。
gate_command 用 {{gate_command}} 变量，不要硬编码。
只输出文件内容，不要额外解释。" \
      --output-format text > loop/prompts/_common-rules.md 2>/dev/null && {
      echo "  ✅ _common-rules.md 已由 AI 生成"
    } || {
      echo "  ⚠️ AI 生成失败，使用默认模板"
      cp "${LOOP_DIR}/prompts/_common-rules.md.template" loop/prompts/_common-rules.md
    }
  else
    echo "  ⏭ claude 不可用，使用默认模板（可稍后手动编辑）"
    cp "${LOOP_DIR}/prompts/_common-rules.md.template" loop/prompts/_common-rules.md
  fi
else
  echo "  ⏭ _common-rules.md 已存在，跳过"
fi

# ── 6. .gitignore ──
for pattern in "loop/stats.json" "loop/PROGRESS.md" "loop/.env" \
               "loop/loop-state.json" "loop/workers/" "loop/logs/" \
               "loop/.locks/" "loop/.cache/" ".trees/"; do
  grep -qF "$pattern" .gitignore 2>/dev/null || echo "$pattern" >> .gitignore
done
echo "  ✅ .gitignore 已更新"

# ── 7. 依赖检查 [MF-2: 处理 PEP 668] ──
echo ""
echo "📋 依赖检查："
python3 -c "import yaml" 2>/dev/null && echo "  ✅ PyYAML" || {
  echo "  📦 安装 PyYAML..."
  python3 "${LOOP_DIR}/lib/compat.py" install-yaml && echo "  ✅ PyYAML 已安装" || {
    echo "  ❌ 自动安装失败"
    echo "    请手动安装：pip3 install pyyaml"
    echo "    或：pip3 install --user pyyaml"
    echo "    或：pip3 install pyyaml --break-system-packages"
  }
}
command -v jq >/dev/null && echo "  ✅ jq" || echo "  ⚠️ jq（可选，brew install jq 或 apt install jq）"

# ── 8. 验证 ──
echo ""
echo "📋 生成结果验证："
echo "  workflow.yaml gate: $(head -1 <<< "$DETECT_GATE")"
echo "  common-rules: $(wc -l < loop/prompts/_common-rules.md) 行"

echo ""
echo "✅ 初始化完成！直接开始："
echo "  1. 在 loop/inbox/ 放入任务 .md 文件"
echo "  2. bash loop/openniuma.sh start"
echo ""
echo "  （可选）review 生成的配置："
echo "  cat loop/workflow.yaml"
echo "  cat loop/prompts/_common-rules.md"
```

### 8.8 detect.py — 项目探测模块（修订 [SF-6]）

> **评审问题：** 6 次 `python3 -c "import json,sys; print(...)"` 管道解析同一 JSON 极其脆弱。

**修订：增加 `--shell-vars` 模式，一次调用输出所有变量。**

```python
#!/usr/bin/env python3
"""loop/lib/detect.py — 项目技术栈和配置自动探测"""
import json
import os
import sys
import re
import shlex

def detect(repo_dir: str) -> dict:
    """探测项目配置，返回结构化结果"""
    result = {
        "stack": "unknown",
        "gate_command": "echo 'TODO: configure gate_command in workflow.yaml'",
        "after_create": "",
        "before_remove": "",
        "spec_dir": "docs/specs",
        "plan_dir": "docs/plans",
    }

    # ── 技术栈探测 ──
    if os.path.exists(os.path.join(repo_dir, "package.json")):
        result["stack"] = "node"
        result.update(_detect_node(repo_dir))
    elif os.path.exists(os.path.join(repo_dir, "go.mod")):
        result["stack"] = "go"
        result["gate_command"] = "go test ./... && go vet ./..."
        result["after_create"] = "go mod download"
    elif os.path.exists(os.path.join(repo_dir, "Cargo.toml")):
        result["stack"] = "rust"
        result["gate_command"] = "cargo test && cargo clippy -- -D warnings"
        result["after_create"] = "cargo fetch"
    elif os.path.exists(os.path.join(repo_dir, "pyproject.toml")) or \
         os.path.exists(os.path.join(repo_dir, "requirements.txt")):
        result["stack"] = "python"
        result.update(_detect_python(repo_dir))
    elif os.path.exists(os.path.join(repo_dir, "Gemfile")):
        result["stack"] = "ruby"
        result["gate_command"] = "bundle exec rspec && bundle exec rubocop"
        result["after_create"] = "bundle install"

    # ── CI 配置覆盖（优先级最高）──
    ci_gate = _detect_from_ci(repo_dir)
    if ci_gate:
        result["gate_command"] = ci_gate

    # ── 目录探测 ──
    for d in ["docs/specs", "docs/superpowers/specs", "specs"]:
        if os.path.isdir(os.path.join(repo_dir, d)):
            result["spec_dir"] = d
            break
    for d in ["docs/plans", "docs/superpowers/plans", "plans"]:
        if os.path.isdir(os.path.join(repo_dir, d)):
            result["plan_dir"] = d
            break

    return result


# ... _detect_node, _detect_python, _detect_from_ci 实现同前 ...
# （唯一变化：_detect_node 中 sed -i '' 改为 compat.py sed-inplace 调用）


if __name__ == "__main__":
    args = sys.argv[1:]
    shell_vars = "--shell-vars" in args
    args = [a for a in args if a != "--shell-vars"]
    repo = args[0] if args else "."

    result = detect(repo)

    if shell_vars:
        # [SF-6] 输出 shell 变量，一次 eval 即可
        print(f"DETECT_STACK={shlex.quote(result['stack'])}")
        print(f"DETECT_GATE={shlex.quote(result['gate_command'])}")
        print(f"DETECT_AFTER_CREATE={shlex.quote(result['after_create'])}")
        print(f"DETECT_BEFORE_REMOVE={shlex.quote(result['before_remove'])}")
        print(f"DETECT_SPEC_DIR={shlex.quote(result['spec_dir'])}")
        print(f"DETECT_PLAN_DIR={shlex.quote(result['plan_dir'])}")
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))
```

### 8.9 移植流程（最终版）

```bash
# 1. 拷贝 loop/ 到新项目
cp -r /path/to/loop-template/loop /new/project/loop

# 2. 一条命令初始化（可选 dry-run 先验证）
cd /new/project
bash loop/init.sh --dry-run    # [SF-4] 预览探测结果，不写入文件
bash loop/init.sh              # 实际初始化
```

**init.sh 自动完成的事：**

| 步骤 | 做什么 | 耗时 |
|------|--------|------|
| 0 | 检查 Python >= 3.9 | 瞬间 |
| 1 | 创建目录（inbox/tasks/logs/...） | 瞬间 |
| 2 | 探测技术栈 → 生成 workflow.yaml | 1 秒 |
| 3 | 从 CI 配置提取真实 gate 命令 | 1 秒 |
| 4 | 探测 monorepo/数据库 → 生成 hooks | 1 秒 |
| 5 | 调用 claude 分析 CLAUDE.md → 生成 _common-rules.md | 20 秒 |
| 6 | 安装 PyYAML（如缺失，处理 PEP 668）| 5 秒 |
| 7 | 更新 .gitignore | 瞬间 |

**用户需要做的：**

| 步骤 | 做什么 | 耗时 |
|------|--------|------|
| 1 | `bash loop/init.sh` | 30 秒 |
| 2 | （可选）review 生成的配置 | 1 分钟 |
| 3 | 放任务到 inbox/ → `bash loop/openniuma.sh start` | 开始 |

**不需要手动编辑任何配置文件。** `init.sh` 的探测结果足够大多数项目直接使用。如果需要调整，编辑 `workflow.yaml` 即可——但这是可选的，不是必须的。

**降级模式（无 claude CLI）：** `bash loop/init.sh --no-ai`，跳过 AI 生成，_common-rules.md 使用通用模板（只含硬性红线和门禁），后续 AI agent 运行时会自动参考 CLAUDE.md。

### 8.10 不同技术栈的 workflow.yaml 示例

**Python + Django 项目：**
```yaml
project:
  gate_command: |
    python -m pytest
    python -m mypy .
    python -m ruff check .
hooks:
  after_create: |
    python -m venv .venv
    .venv/bin/pip install -r requirements.txt
    .venv/bin/python manage.py migrate --database=test_$SLUG
  before_remove: |
    dropdb "test_$SLUG" 2>/dev/null || true
```

**Go 项目：**
```yaml
project:
  gate_command: |
    go test ./...
    go vet ./...
    golangci-lint run
hooks:
  after_create: |
    go mod download
  before_remove: ""
```

**Rust 项目：**
```yaml
project:
  gate_command: |
    cargo test
    cargo clippy -- -D warnings
hooks:
  after_create: |
    cargo fetch
  before_remove: ""
```

## 9. 迁移策略

> **评审建议 [NH-1]：** 考虑压缩 6 Phase 为更少的阶段。
> 采纳部分：将 Phase 0 和 Phase 1 合并，Phase 3 和 Phase 4 合并，Phase 5 保持独立。

### Phase 1：架构基础 + 可移植性（合并原 Phase 0 + Phase 1）
- `lib/json_store.py`（原子读写 + 统一锁）[MF-1]
- `lib/compat.py`（跨平台兼容层）[MF-2]
- `lib/config.py`（配置加载 + 磁盘缓存 + export-env + schema 校验）[MF-3, SF-8]
- `lib/state.py`（基于 JsonFileStore）[MF-1]
- `workflow.yaml` 扩展（project + hooks + notify + pause 节）
- `prompts/*.md` + `_common-rules.md.template`
- `openniuma.sh`（统一 CLI 入口）[SF-3]
- `init.sh`（含 --dry-run）[SF-4]
- `lib/detect.py`（含 --shell-vars）[SF-6]
- dev-loop.sh：废弃 mkdir 锁、接入 config.py export-env、hook 临时文件执行 [MF-6]
- dev-loop.sh：ensure_worktree/cleanup_worktree 改用 hooks + safe_remove_worktree
- dev-loop.sh：worker 启动改用 setsid [MF-4]

### Phase 2：可靠性 + 数据沉淀（合并原 Phase 2 + Phase 3）
- `lib/reconcile.py`（stall 检测 + 取消 + 进程组 kill + 孤儿回收 + 版本号检查）[MF-4]
- `lib/retry.py`（指数退避 + 按类型差异化 + jitter）[NH-7]
- `lib/failure.py`（分层匹配 + 置信度 + 200 行扫描）[MF-7]
- `lib/stats.py`（JSON stdin + 数据轮转）[MF-3, SF-1]
- `lib/notify.py`（含抑制/聚合/速率限制）[MF-5]
- 降级流程：部分暂停 + RESUME 信号 + auto_resume [MF-8]
- Worktree 保留策略
- dev-loop.sh 集成失败分类 + 重试 + reconciliation + 埋点 + 通知

### Phase 3：可观测性 + 工具（原 Phase 4）
- `lib/status.py`（合并两版渲染逻辑）
- `dashboard.sh`（v4 丰富渲染，调用 lib/）
- `stats.sh`（调用 lib/stats.py）
- `generate-progress.sh`（Mermaid 报告）
- `lib/inbox.py` + `lib/backlog.py`（从内联提取）
- `add-task.sh`（快捷入队，分级模板）[SF-5]
- 结构化日志增加 session_id/task_id 关联 [SF-2]
- 清理 dev-loop.sh 残留内联代码

### Phase 4：品牌化改名 — loop → openniuma
- 目录重命名：`loop/` → `openniuma/`（或保持 `loop/` 为符号链接）
- 脚本重命名：`dev-loop.sh` → `openniuma.sh start` 的实际处理逻辑
- 状态文件：`loop-state.json` → `openniuma-state.json`
- 日志/workers 目录名同步更新
- CLAUDE.md 中所有 `loop/` 引用更新
- README.md 品牌介绍 + 使用说明
- 向后兼容：如果检测到旧 `loop/` 目录结构，自动迁移

**改名范围清单：**

| 旧名 | 新名 |
|------|------|
| `loop/` | `openniuma/` |
| `loop/dev-loop.sh` | `openniuma/openniuma.sh start` |
| `loop/loop-state.json` | `openniuma/openniuma-state.json` |
| `loop/workflow.yaml` | `openniuma/workflow.yaml`（不改） |
| `loop/lib/` | `openniuma/lib/` |
| `loop/prompts/` | `openniuma/prompts/` |
| `loop/inbox/` | `openniuma/inbox/` |
| `loop/tasks/` | `openniuma/tasks/` |
| `loop/logs/` | `openniuma/logs/` |
| `loop/workers/` | `openniuma/workers/` |
| `.trees/loop-*` | `.trees/openniuma-*` |

每个 Phase 独立可交付。Phase 1-2 是核心改造（~2 周），Phase 3 是体验增强（~1 周），Phase 4 是品牌化收尾（~数天）。

## 10. 依赖

- **Python 3.9+**（非 macOS 自带，明确为前置依赖 [MF-2]，init.sh 检查版本）
- **PyYAML**（`pip3 install pyyaml`，init.sh 处理 PEP 668 限制 [MF-2]）
- jq 1.7+（可选，dashboard.sh 辅助渲染用）
- gh CLI（可选，GitHub Issue 导入）
- curl（可选，飞书通知）

## 11. 测试策略

Python 模块 unittest 覆盖：
- `json_store.py`：原子读写 + 锁竞争 + 死进程回收 + 并发压力测试 [MF-1]
- `compat.py`：各平台行为一致性（CI Linux 矩阵）[MF-2]
- `config.py`：YAML 解析、磁盘缓存、export-env、schema 校验、热重载 [MF-3, SF-8]
- `state.py`：读写 + 版本号 + 重建 [MF-1, MF-4]
- `failure.py`：6 种失败类型 x **每种至少 5 正例 + 3 反例** [MF-7]、分层匹配优先级、置信度阈值
- `reconcile.py`：stall 阈值、取消逻辑、孤儿回收、版本号检查 [MF-4]
- `retry.py`：指数退避 + 按类型退避 + jitter 范围
- `stats.py`：session 记录（JSON stdin）+ 任务汇总 + 查询 + 轮转
- `notify.py`：级别过滤、抑制窗口、聚合、速率限制（mock 实际发送）[MF-5]
- `inbox.py`：入队 + STOP 信号 + RESUME 信号 + slug 生成 [MF-8]
- `backlog.py`：全量生成
- `detect.py`：各技术栈探测 + --shell-vars 输出 [SF-6]

**集成测试 [QA-2 建议]：** 后续迭代增加多 worker 并发场景端到端测试（Phase 3）。

Bash 脚本（dashboard.sh、stats.sh 等）不写单元测试，通过手动验证覆盖。

## 12. .gitignore 新增

```
loop/stats.json
loop/stats-archive-*.json
loop/PROGRESS.md
loop/.env
loop/loop-state.json
loop/.cache/
loop/.locks/
```

---

## 附录 A：评审修订追踪

| 评审编号 | 严重度 | 修订位置 | 状态 |
|---------|--------|---------|------|
| MF-1 并发安全 | 致命 | §4.1 JsonFileStore + state.py | ✅ 已补充完整方案 |
| MF-2 跨平台兼容 | 严重 | §4.2 compat.py + §8.7 init.sh | ✅ 已补充完整方案 |
| MF-3 进程模型效率 | 严重 | §3.2 export-env + §5.3 JSON stdin | ✅ 已修订 |
| MF-4 进程组管理 | 严重 | §4.3 setsid + PGID + 版本号 | ✅ 已补充完整方案 |
| MF-5 通知抑制 | 高 | §3.1 notify 配置 + §6.1 NotifyManager | ✅ 已补充完整方案 |
| MF-6 Shell 安全 | 严重 | §4.6 hook 临时文件 + safe_remove | ✅ 已补充完整方案 |
| MF-7 失败分类准确性 | 高 | §4.4 分层匹配 + 置信度 | ✅ 已修订 |
| MF-8 暂停恢复 | 高 | §4.4 降级流程 + RESUME | ✅ 已补充完整方案 |
| SF-1 数据轮转 | - | §5.3 max_sessions + 归档 | ✅ 已纳入 |
| SF-2 日志关联 | - | §5.4 session_id/task_id | ✅ 已纳入 |
| SF-3 统一 CLI | - | §2.1 openniuma.sh 提前到 Phase 0 | ✅ 已纳入 |
| SF-4 dry-run | - | §8.7 init.sh --dry-run | ✅ 已纳入 |
| SF-5 分级模板 | - | §7.1 按 complexity 生成 | ✅ 已纳入 |
| SF-6 JSON 解析合并 | - | §8.8 detect.py --shell-vars | ✅ 已纳入 |
| SF-7 cost 采集 | - | §5.3 cost_usd 方案说明 | ✅ 已纳入 |
| SF-8 schema 校验 | - | §3.3 validate_config | ✅ 已纳入 |
