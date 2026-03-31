# Dev-2 评审：Python 模块设计与实现质量

> 评审人：Dev-2（Python 资深开发工程师）
> 评审对象：openNiuMa 合并升级设计 — Python 模块部分（11 个模块）
> 评审日期：2026-03-27

---

## 🔴 严重问题

### S1. state.py 文件锁方案在 5 worker 并发下不可靠

**问题描述：** 设计中 `state.py` 使用文件锁保护 `loop-state.json`，5 个 worker 并发竞争同一 JSON 文件。Python 的 `fcntl.flock` 是 advisory lock，不阻止未持锁进程直接读写。更关键的是，每个 worker 是独立 `python3 lib/state.py` 进程调用（见 CLI 入口设计），读-改-写不是原子操作——即使加锁，在「读 -> JSON 反序列化 -> 修改 -> 序列化 -> 写」这个窗口中，另一个进程可能读到中间状态。

**技术影响：**
- 并发写导致 JSON 被截断或覆盖丢失更新（经典 lost update 问题）
- 5 worker 频繁更新 phase/progress 字段时，某个 worker 的状态变更被另一个覆盖
- 文件锁在 NFS 上行为未定义（虽然当前是本地 FS，但可移植性设计需考虑）

**建议修改：**

```python
import fcntl
import json
import os
import tempfile

class StateManager:
    def __init__(self, state_path: str):
        self.state_path = state_path
        self.lock_path = state_path + ".lock"

    def update(self, modifier_fn):
        """原子读-改-写：独立锁文件 + 临时文件写入 + rename"""
        with open(self.lock_path, "w") as lock_fd:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)  # 阻塞等锁
            try:
                # 读
                with open(self.state_path, "r") as f:
                    state = json.load(f)
                # 改
                modifier_fn(state)
                # 写到临时文件
                dir_name = os.path.dirname(self.state_path)
                fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
                try:
                    with os.fdopen(fd, "w") as tmp_f:
                        json.dump(state, tmp_f, indent=2, ensure_ascii=False)
                        tmp_f.flush()
                        os.fsync(tmp_f.fileno())
                    # 原子 rename
                    os.replace(tmp_path, self.state_path)
                except:
                    os.unlink(tmp_path)
                    raise
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
```

关键点：(1) 使用独立 `.lock` 文件而非锁数据文件本身；(2) 写入临时文件后 `os.replace` 原子替换；(3) `fsync` 确保数据落盘。

---

### S2. stats.py 无并发保护，与 state.py 存在同类问题

**问题描述：** 设计文档明确标注 stats.py「纯 JSON 文件存储，无并发保护」。但在多 worker 模式下，多个 worker 的 session 可能同时结束，并行调用 `stats.py record-session`，导致 `stats.json` 写入冲突。

**技术影响：**
- session 记录丢失，统计数据不准确
- JSON 文件损坏（部分写入）
- finalize-task 读到不完整数据，汇总结果错误

**建议修改：**

stats.py 应复用 state.py 的 `StateManager` 锁模式，或抽出一个通用的 `JsonFileStore` 类：

```python
class JsonFileStore:
    """带文件锁的 JSON 文件原子读写"""
    def __init__(self, path: str):
        self.path = path
        self.lock_path = path + ".lock"

    def read(self) -> dict:
        if not os.path.exists(self.path):
            return {"sessions": [], "tasks": []}
        with open(self.path) as f:
            return json.load(f)

    def update(self, modifier_fn):
        """与 StateManager.update 相同的锁 + 原子写逻辑"""
        ...  # 同 S1 建议
```

state.py 和 stats.py 都基于此类实现，避免重复且确保一致。

---

### S3. 每次 CLI 调用启动新 Python 进程，性能严重瓶颈

**问题描述：** 设计中 11 个模块全部通过 `python3 lib/xxx.py subcommand args` 调用，每次调用启动一个新 Python 进程。在编排器的 tick 循环中，单轮可能调用：`config.py get-value`（读配置）-> `state.py read`（读状态）-> `reconcile.py check`（stall 检测）-> `failure.py analyze`（失败分析）-> `retry.py delay`（计算退避）-> `notify.py send`（发通知）-> `stats.py record-session`（记录数据）-> `status.py render`（渲染状态）。

**技术影响：**
- Python 3 进程冷启动约 30-80ms（含解释器初始化 + import），PyYAML import 额外 15-25ms
- 每轮 tick 8 次调用 = 360-840ms 纯开销
- 5 worker 下每 60s 一轮 tick，每轮可能触发 10+ 次 Python 进程启动
- `config.py` 的热重载每次都要检查 mtime——但它自己每次都是新进程，内存中无缓存可言，所谓"热重载"实际上每次都是冷加载

**建议修改：**

方案 A（推荐，改动最小）：将高频调用合并为单次 Python 调用：

```python
# lib/tick.py — 单进程完成一轮 tick 的所有 Python 操作
"""
python3 lib/tick.py <workflow.yaml> <state.json> <stats.json>

一次进程调用完成：config 加载、state 读取、reconcile、failure 分析、retry 计算
输出 JSON 供 bash 消费
"""
import json
from config import load_config
from state import StateManager
from reconcile import check_all
from failure import analyze_log
from retry import compute_delay

def tick(config_path, state_path, stats_path):
    config = load_config(config_path)
    state = StateManager(state_path).read()

    result = {
        "config": config,
        "reconcile": check_all(state, config),
        "failures": {},
        "delays": {},
    }

    for worker in state.get("workers", {}).values():
        if worker.get("status") == "failed":
            ft = analyze_log(worker["log_path"])
            result["failures"][worker["id"]] = ft
            result["delays"][worker["id"]] = compute_delay(
                ft, worker.get("attempt", 1), config
            )

    print(json.dumps(result))
```

方案 B（更彻底）：让编排器主循环用 Python 而非 bash，bash 只做 `claude` CLI 调用和 git 操作。但这改动范围太大，建议作为后续迭代。

---

## 🟡 中等问题

### M1. failure.py 正则匹配 50 行日志的分类准确性不足

**问题描述：** 失败分类基于 session log 尾部 50 行的正则匹配。但实际场景中：
1. Claude 的输出可能在讨论错误时提到这些关键词（如"我看到了 CONFLICT 标记"），不代表当前失败原因
2. 50 行可能不够——如果 Claude 在失败前输出了大量调试信息
3. 多种失败模式可能同时出现（lint 失败 + context 耗尽），正则匹配取第一个命中的，优先级不清晰

**技术影响：**
- 错误分类导致错误的重试策略（如把 context 错分为 gate，导致无意义重试）
- 50 行截断可能丢失真正的错误信息

**建议修改：**

```python
import re
from typing import Tuple

# 按优先级排列：越严重/越确定的排在前面
PATTERNS = [
    # (类型, 正则列表, 权重)
    ("context", [
        re.compile(
            r"context window|token limit|max turns|conversation is too long",
            re.I,
        ),
    ], 100),  # context 耗尽是最确定的信号
    ("permission", [
        re.compile(r"permission denied|dangerously-skip|EPERM", re.I),
    ], 90),
    ("network", [
        re.compile(
            r"ETIMEDOUT|ECONNREFUSED|ECONNRESET|rate limit|429|503",
            re.I,
        ),
    ], 80),
    ("conflict", [
        re.compile(r"<<<<<<< |CONFLICT.*merge|merge conflict", re.I),
    ], 70),
    ("gate", [
        re.compile(
            r"exit code [1-9]|npm ERR!|FAIL\s|error TS\d|Error:|AssertionError",
            re.I,
        ),
    ], 60),
]

def classify(log_path: str, tail_lines: int = 100) -> Tuple[str, float]:
    """返回 (failure_type, confidence)"""
    lines = _read_tail(log_path, tail_lines)
    text = "\n".join(lines)

    scores = {}
    for ftype, patterns, weight in PATTERNS:
        match_count = sum(1 for p in patterns if p.search(text))
        if match_count > 0:
            scores[ftype] = weight * match_count

    if not scores:
        return ("unknown", 0.0)

    best = max(scores, key=scores.get)
    confidence = min(scores[best] / 100.0, 1.0)
    return (best, confidence)
```

建议：(1) 增加到 100 行或可配置；(2) 引入置信度，低置信度时标记为 unknown 而非猜测；(3) 按优先级打分而非 first-match。

---

### M2. config.py 热重载在进程模型下形同虚设

**问题描述：** 设计说「config.py 每次被调用时检查文件 mtime，变化则重新解析，失败则保留上次有效配置」。但由于每次调用都是新进程（见 S3），进程内无「上次有效配置」可保留——每次都是从零解析。

**技术影响：**
- 「失败保留上次配置」的容错能力完全失效：如果 workflow.yaml 被写坏，每次调用都会失败
- mtime 检查毫无意义，因为不存在「上次检查时间」

**建议修改：**

在进程模型不变的前提下，用文件缓存实现容错：

```python
import hashlib
import json
import os

CACHE_PATH = os.path.join(
    os.path.dirname(__file__), "..", ".config_cache.json"
)

def load_config(yaml_path: str) -> dict:
    try:
        with open(yaml_path) as f:
            raw = f.read()
        config = yaml.safe_load(raw)
        _validate(config)
        # 成功：更新缓存
        with open(CACHE_PATH, "w") as f:
            json.dump(config, f)
        return config
    except Exception as e:
        # 失败：尝试读缓存
        if os.path.exists(CACHE_PATH):
            import sys
            print(
                f"[config] YAML 解析失败 ({e})，使用缓存配置",
                file=sys.stderr,
            )
            with open(CACHE_PATH) as f:
                return json.load(f)
        raise  # 缓存也没有，只能报错
```

或者如果采纳 S3 的方案 A（tick.py 合并调用），那么 config 可以在进程生命周期内缓存，热重载才真正有意义。

---

### M3. stats.py CLI 参数设计：12 个位置参数是维护噩梦

**问题描述：** `stats.py record-session` 接受 12 个位置参数：`<stats_file> <task_id> <task_name> <phase> <started_at> <ended_at> <exit_code> [cost_usd] [failure_type] [worker_id] [attempt]`。位置参数超过 4 个时极易出错——调用方必须记住精确顺序，参数缺失或错位不会报错（Python 只会把值赋给错误的字段）。

**技术影响：**
- bash 调用方传参顺序出错导致数据污染（如 task_name 传到 phase 位置），且不会有明确报错
- 新增字段需要修改所有调用点的参数顺序
- 可选参数在 bash 中难以优雅跳过（需要传空字符串占位）

**建议修改：**

使用 `argparse` 命名参数：

```python
import argparse

def build_parser():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")

    record = sub.add_parser("record-session")
    record.add_argument("--file", required=True, help="stats.json 路径")
    record.add_argument("--task-id", type=int, required=True)
    record.add_argument("--task-name", required=True)
    record.add_argument("--phase", required=True)
    record.add_argument("--started-at", required=True)
    record.add_argument("--ended-at", required=True)
    record.add_argument("--exit-code", type=int, required=True)
    record.add_argument("--cost-usd", type=float, default=None)
    record.add_argument("--failure-type", default=None)
    record.add_argument("--worker-id", default=None)
    record.add_argument("--attempt", type=int, default=1)

    return parser
```

bash 调用方式改为：
```bash
python3 lib/stats.py record-session \
  --file "$STATS_FILE" \
  --task-id "$TASK_ID" \
  --task-name "$TASK_NAME" \
  --phase "$PHASE" \
  --started-at "$STARTED_AT" \
  --ended-at "$ENDED_AT" \
  --exit-code "$EXIT_CODE" \
  --failure-type "$FAILURE_TYPE"
```

虽然更长，但自文档化、不怕参数错位、可选参数可省略。所有 11 个模块的 CLI 入口都应统一使用 argparse。

---

### M4. detect.py 异常处理过于宽泛

**问题描述：** `_parse_github_actions` 使用裸 `except Exception: return ""`，吞掉所有错误包括 `yaml.YAMLError`、`KeyError`、`FileNotFoundError`。`_detect_node` 中 `json.load(pkg)` 对 malformed JSON 也会抛异常但未捕获。两种处理方式不一致。

**技术影响：**
- CI 配置解析出错时静默返回空，用户不知道探测失败，拿到的是次优默认值
- package.json 格式错误时进程 crash，init.sh 中断
- 调试困难——探测结果不符合预期时无日志可追

**建议修改：**

```python
import logging
import sys

logger = logging.getLogger("detect")

def _parse_github_actions(path: str) -> str:
    """从 GitHub Actions YAML 提取 run 命令"""
    try:
        import yaml
    except ImportError:
        logger.warning("PyYAML 未安装，跳过 CI 配置解析")
        return ""

    try:
        with open(path) as f:
            ci = yaml.safe_load(f)
    except yaml.YAMLError as e:
        logger.warning(f"CI 配置 YAML 解析失败 ({path}): {e}")
        return ""

    if not isinstance(ci, dict) or "jobs" not in ci:
        logger.info(f"CI 配置无 jobs 节: {path}")
        return ""

    commands = []
    for job_name, job in ci["jobs"].items():
        if not isinstance(job, dict):
            continue
        for step in job.get("steps") or []:
            run = step.get("run", "")
            if run and any(
                kw in run for kw in ["test", "lint", "build", "check", "tsc"]
            ):
                for line in run.strip().split("\n"):
                    line = line.strip()
                    if line and not line.startswith("#"):
                        commands.append(line)

    result = " && ".join(commands)
    logger.info(f"从 {path} 提取 gate 命令: {result}")
    return result


def _detect_node(repo_dir: str) -> dict:
    pkg_path = os.path.join(repo_dir, "package.json")
    try:
        with open(pkg_path) as f:
            pkg = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"package.json 读取失败: {e}")
        return {
            "gate_command": "npm test",
            "after_create": "npm install",
            "before_remove": "",
        }
    # ... 后续逻辑
```

原则：(1) 区分不同异常类型，给出有意义的 warning；(2) 有合理的 fallback；(3) 所有探测结果写入 stderr 日志，方便用户 review。

---

### M5. retry.py 指数退避缺少 jitter，多 worker 雷同重试

**问题描述：** gate 类型退避公式 `min(base * 2^(n-1), max_backoff)` 没有 jitter（随机抖动）。当多个 worker 同时失败（如 CI 服务短暂不可用），它们会在完全相同的时间点重试，造成 thundering herd。

**技术影响：**
- 5 个 worker 同时重试 gate，同时启动 `npm test`，CI 资源瞬间打满
- network 类型 60s 固定退避更严重——所有 worker 精确同步重试

**建议修改：**

```python
import random

def compute_delay(
    failure_type: str, attempt: int, config: dict
) -> float:
    base = config.get("retry", {}).get("base_delay_sec", 10)
    max_backoff = config.get("retry", {}).get("max_backoff_sec", 300)

    if failure_type == "gate":
        delay = min(base * (2 ** (attempt - 1)), max_backoff)
    elif failure_type == "network":
        delay = 60.0
    elif failure_type == "conflict":
        delay = 10.0
    elif failure_type in ("context", "permission"):
        delay = 0.0
    else:
        delay = base

    # Full jitter: [0.5 * delay, delay]，避免 thundering herd
    if delay > 0:
        delay = random.uniform(delay * 0.5, delay * 1.0)

    return round(delay, 1)
```

采用 AWS 推荐的 Full Jitter 或 Equal Jitter 策略。`[0.5 * delay, delay]` 范围比 `[0, delay]` 更保守但仍有效分散重试时间。

---

### M6. notify.py 对外部命令调用缺乏超时和错误隔离

**问题描述：** 通知通过 `osascript`（macOS）和 `curl`（飞书 webhook）发送。如果飞书 webhook 不通，curl 会默认等待很长时间（300s）。osascript 在 SSH session 中会失败。这些外部调用的失败不应阻塞编排器主流程。

**技术影响：**
- curl 超时阻塞编排器 tick 循环
- osascript 在无 GUI 环境中报错
- 通知失败导致整个 stats 记录或 reconcile 流程中断

**建议修改：**

```python
import subprocess

def send_feishu(webhook_url: str, title: str, body: str):
    subprocess.run(
        [
            "curl", "-s", "-m", "10",  # 10 秒超时
            "-H", "Content-Type: application/json",
            "-d", json.dumps({
                "msg_type": "text",
                "content": {"text": f"{title}\n{body}"},
            }),
            webhook_url,
        ],
        capture_output=True,
        timeout=15,  # Python 层再加一层超时
    )

def send_macos(title: str, body: str):
    try:
        subprocess.run(
            [
                "osascript", "-e",
                f'display notification "{body}" with title "{title}"',
            ],
            capture_output=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass  # 无 GUI 环境静默跳过
```

因为每次调用是独立进程（见 S3），在 Python 内做 threading 意义不大。更实际的做法是在 bash 调用方用后台进程：

```bash
python3 lib/notify.py send "$level" "$title" "$body" &  # 后台运行，不阻塞
```

---

## 🟢 建议

### L1. 统一所有模块的 CLI 入口模式

**问题描述：** 11 个模块各自实现 `if __name__ == "__main__":` CLI 入口，格式不统一。有的用位置参数（stats.py），有的用子命令（config.py），有的混合使用。

**建议：** 抽出统一的 CLI 框架：

```python
# lib/_cli.py
import argparse
import json
import sys

def run_cli(description: str, setup_fn):
    """统一 CLI 入口模板"""
    parser = argparse.ArgumentParser(description=description)
    setup_fn(parser)
    args = parser.parse_args()

    try:
        result = args.func(args)
        if result is not None:
            if isinstance(result, (dict, list)):
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print(result)
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)
```

每个模块复用此模板，确保：(1) 错误输出格式统一（JSON to stderr）；(2) 退出码一致；(3) `--help` 自动可用。

---

### L2. detect.py 探测逻辑应支持多技术栈混合项目

**问题描述：** 当前探测逻辑是 `if/elif` 链，只返回第一个命中的技术栈。但 monorepo 可能同时包含 Node.js 前端和 Python 后端（或 Go 微服务）。

**建议：**

```python
def detect(repo_dir: str) -> dict:
    stacks = []
    if os.path.exists(os.path.join(repo_dir, "package.json")):
        stacks.append(("node", _detect_node(repo_dir)))
    if os.path.exists(os.path.join(repo_dir, "pyproject.toml")):
        stacks.append(("python", _detect_python(repo_dir)))
    # ...

    if not stacks:
        return DEFAULT_RESULT

    # 合并 gate 命令
    result = stacks[0][1]
    result["stack"] = "+".join(s[0] for s in stacks)  # "node+python"
    if len(stacks) > 1:
        gates = [s[1]["gate_command"] for s in stacks]
        result["gate_command"] = " && ".join(gates)

    return result
```

---

### L3. 模块依赖 PyYAML 应声明为可选并优雅降级

**问题描述：** `config.py` 硬依赖 PyYAML，但 PyYAML 不是 Python 标准库。init.sh 虽然有安装逻辑，但如果 `pip3 install` 失败（权限、网络），所有模块全部不可用。

**建议：**

```python
try:
    import yaml
except ImportError:
    yaml = None

def load_config(yaml_path: str) -> dict:
    if yaml is None:
        # 降级：尝试读取 JSON 缓存
        cache = _try_load_cache(yaml_path)
        if cache:
            return cache
        raise ImportError(
            "PyYAML 未安装且无配置缓存。请执行: pip3 install pyyaml\n"
            "或使用 pip3 install --user pyyaml 避免权限问题"
        )
    # ... 正常 YAML 加载
```

---

### L4. 建议为所有模块添加类型注解和 dataclass

**问题描述：** 设计中模块间传递的数据结构（config dict、state dict、session record、failure result）全部用裸 dict。11 个模块间的数据契约不清晰，修改一个字段名需要全局搜索。

**建议：**

```python
from dataclasses import dataclass, asdict
from typing import Optional

@dataclass
class SessionRecord:
    task_id: int
    task_name: str
    phase: str
    started_at: str
    ended_at: str
    exit_code: int
    cost_usd: Optional[float] = None
    failure_type: Optional[str] = None
    worker_id: Optional[str] = None
    attempt: int = 1
    duration_sec: Optional[int] = None

    def __post_init__(self):
        if self.duration_sec is None and self.started_at and self.ended_at:
            from datetime import datetime
            start = datetime.fromisoformat(
                self.started_at.replace("Z", "+00:00")
            )
            end = datetime.fromisoformat(
                self.ended_at.replace("Z", "+00:00")
            )
            self.duration_sec = int((end - start).total_seconds())

@dataclass
class FailureResult:
    failure_type: str   # gate|context|permission|conflict|network|unknown
    confidence: float   # 0.0-1.0
    summary: str        # 错误摘要（最后 10 行）
```

dataclass 让每个字段都有明确类型，IDE 自动补全，序列化用 `asdict()`，兼顾简洁和类型安全。

---

### L5. 测试策略建议补充集成测试和 fixture

**问题描述：** 设计只提到 unittest 单元测试覆盖各模块。但 state.py + stats.py 的并发问题、config.py 的热重载、reconcile.py 的 stall 检测都需要集成测试验证端到端行为。

**建议：**

```python
# tests/test_integration.py
import subprocess
import tempfile
import os
import json
import concurrent.futures

class TestConcurrentStateUpdate:
    def test_5_workers_concurrent_write(self):
        """模拟 5 worker 并发更新 state，验证无丢失"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump({"workers": {}, "counter": 0}, f)
            state_path = f.name

        def worker_update(worker_id):
            result = subprocess.run(
                [
                    "python3", "lib/state.py",
                    "increment-counter", state_path,
                ],
                capture_output=True, text=True,
            )
            return result.returncode

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            futures = [pool.submit(worker_update, i) for i in range(50)]
            results = [f.result() for f in futures]

        with open(state_path) as f:
            final = json.load(f)

        assert final["counter"] == 50, (
            f"Expected 50, got {final['counter']} (lost updates!)"
        )
        os.unlink(state_path)
```

建议在 `tests/` 目录下分层：`test_unit_*.py`（纯逻辑）、`test_integration_*.py`（文件 I/O + 并发）、`conftest.py`（共享 fixture）。

---

### L6. reconcile.py 依赖传播应防环

**问题描述：** 设计提到「依赖传播：blocked 级联」。如果任务依赖图中存在环（A -> B -> C -> A），级联逻辑会无限递归或死循环。

**建议：**

```python
def propagate_blocked(
    state: dict, failed_task_id: int, visited: set = None
):
    if visited is None:
        visited = set()
    if failed_task_id in visited:
        logger.error(
            f"依赖环检测: {failed_task_id} 已在遍历链中 {visited}"
        )
        return
    visited.add(failed_task_id)

    for task in state.get("tasks", []):
        deps = task.get("depends_on", [])
        if failed_task_id in deps and task["status"] == "pending":
            task["status"] = "blocked"
            task["blocked_by"] = failed_task_id
            propagate_blocked(state, task["id"], visited)
```

---

## 总体评价

### 优点
1. **模块化方向正确**：从 19 个内联 Python 块提取到 11 个独立模块，可测试性大幅提升
2. **失败分类 + 差异化退避**：比统一重试精细很多，gate/context/network 区分处理是实用设计
3. **配置外置**：workflow.yaml 集中管理，比散落在 bash 各处的魔法数字好维护
4. **可移植性设计**：init.sh 三层探测 + AI 生成是有创意的方案，降低了新项目接入门槛

### 核心风险
1. **并发安全是最大隐患**：5 worker 竞争 JSON 文件，state.py 和 stats.py 都需要原子读写保护，当前设计不足
2. **进程模型的性能税**：每次 CLI 调用启动新 Python 进程，热重载形同虚设，累积开销不可忽视
3. **12 参数位置传递**：bash -> python CLI 接口不够健壮，一个参数错位就是静默数据污染

### 建议优先级
1. **必须解决**：S1（state 并发）、S2（stats 并发）— 数据正确性的基本保障
2. **强烈建议**：M3（argparse 命名参数）、M4（异常处理）— 可维护性和调试效率
3. **后续迭代**：S3（进程合并 tick.py）、M5（jitter）— 性能优化，不阻塞首版交付
