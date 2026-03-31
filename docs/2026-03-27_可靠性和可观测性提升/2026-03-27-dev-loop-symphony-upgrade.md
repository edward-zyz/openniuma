# dev-loop Symphony 升级 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将 dev-loop.sh 的 19 个内联 Python 块提取为模块化的 `loop/lib/`，新增 `workflow.yaml` 配置外置 + prompt 模板化 + stall 检测 + 指数退避 + reconciliation + 状态汇总。

**Architecture:** Bash 保持入口（进程管理、worktree、claude CLI），新建 `loop/lib/` Python 模块承担所有 JSON/YAML/业务逻辑。`loop/workflow.yaml` 作为运行时配置（类似 Symphony 的 WORKFLOW.md），`loop/prompts/*.md` 作为 prompt 模板。

**Tech Stack:** Python 3.9+ stdlib + PyYAML, Bash 5, unittest

---

## Phase 1：基础设施

### Task 1: 创建目录结构 + __init__.py

**Files:**
- Create: `loop/lib/__init__.py`
- Create: `loop/prompts/.gitkeep`

**Step 1: 创建目录和文件**

```bash
mkdir -p loop/lib loop/prompts
touch loop/lib/__init__.py
touch loop/prompts/.gitkeep
```

**Step 2: Commit**

```bash
git add loop/lib/__init__.py loop/prompts/.gitkeep
git commit -m "chore: 创建 loop/lib/ 和 loop/prompts/ 目录结构"
```

---

### Task 2: 实现 config.py — YAML 配置加载 + shell 导出

**Files:**
- Create: `loop/lib/config.py`
- Create: `loop/workflow.yaml`
- Test: `loop/lib/test_config.py`

**Step 1: 写 workflow.yaml 配置文件**

```yaml
# loop/workflow.yaml — dev-loop 运行时配置，修改后下一轮 tick 自动生效

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

worktree:
  base_dir: .trees
  prefix: loop

prompts:
  dir: loop/prompts
  common_rules: loop/prompts/_common-rules.md
```

**Step 2: 写失败测试**

```python
# loop/lib/test_config.py
import unittest
import tempfile
import os
import json

class TestConfig(unittest.TestCase):

    def test_load_valid_yaml(self):
        """加载有效 YAML 返回完整配置"""
        from config import load_config
        cfg = load_config("loop/workflow.yaml")
        self.assertEqual(cfg["workers"]["max_concurrent"], 5)
        self.assertEqual(cfg["retry"]["base_delay_sec"], 10)

    def test_load_missing_file_returns_defaults(self):
        """文件不存在时返回默认值"""
        from config import load_config
        cfg = load_config("/nonexistent/path.yaml")
        self.assertEqual(cfg["workers"]["max_concurrent"], 5)

    def test_export_shell_format(self):
        """export-shell 输出合法的 shell 变量赋值"""
        from config import export_shell
        output = export_shell("loop/workflow.yaml")
        self.assertIn("MAX_WORKERS=5", output)
        self.assertIn("STALL_TIMEOUT_SEC=1800", output)
        self.assertIn("INBOX_POLL_INTERVAL=60", output)

    def test_hot_reload_detects_change(self):
        """文件修改后重新加载"""
        from config import load_config
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("workers:\n  max_concurrent: 3\n")
            f.flush()
            path = f.name
        try:
            cfg1 = load_config(path)
            self.assertEqual(cfg1["workers"]["max_concurrent"], 3)
            with open(path, "w") as f:
                f.write("workers:\n  max_concurrent: 8\n")
            cfg2 = load_config(path, force=True)
            self.assertEqual(cfg2["workers"]["max_concurrent"], 8)
        finally:
            os.unlink(path)

    def test_invalid_yaml_keeps_last_good(self):
        """无效 YAML 保留上次有效配置"""
        from config import ConfigLoader
        loader = ConfigLoader()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("workers:\n  max_concurrent: 7\n")
            path = f.name
        try:
            cfg1 = loader.load(path)
            self.assertEqual(cfg1["workers"]["max_concurrent"], 7)
            with open(path, "w") as f:
                f.write("workers:\n  max_concurrent: [invalid\n")
            cfg2 = loader.load(path)
            # 应保留 7，不崩溃
            self.assertEqual(cfg2["workers"]["max_concurrent"], 7)
        finally:
            os.unlink(path)
```

**Step 3: 运行测试确认失败**

```bash
cd loop/lib && python3 -m pytest test_config.py -v 2>&1 | head -20
```
Expected: FAIL — `ModuleNotFoundError: No module named 'config'`

**Step 4: 实现 config.py**

```python
#!/usr/bin/env python3
"""loop/lib/config.py — YAML 配置加载 + shell 导出 + prompt 渲染"""

import os
import sys
import re

try:
    import yaml
except ImportError:
    # macOS 可能没有 PyYAML，提供友好错误
    print("错误: 缺少 PyYAML，请执行 pip3 install pyyaml", file=sys.stderr)
    sys.exit(1)

DEFAULTS = {
    "polling": {"inbox_interval_sec": 60},
    "workers": {"max_concurrent": 5, "stall_timeout_sec": 1800, "max_consecutive_failures": 3},
    "retry": {"base_delay_sec": 10, "max_backoff_sec": 300, "rate_limit_default_wait_sec": 600},
    "worktree": {"base_dir": ".trees", "prefix": "loop"},
    "prompts": {"dir": "loop/prompts", "common_rules": "loop/prompts/_common-rules.md"},
}


def _deep_merge(base: dict, override: dict) -> dict:
    """递归合并，override 覆盖 base"""
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


class ConfigLoader:
    """带热重载的配置加载器"""

    def __init__(self):
        self._last_mtime = 0.0
        self._last_good: dict = dict(DEFAULTS)

    def load(self, path: str, force: bool = False) -> dict:
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            print(f"警告: 配置文件不存在 {path}，使用默认值", file=sys.stderr)
            return dict(self._last_good)

        if not force and mtime == self._last_mtime:
            return dict(self._last_good)

        try:
            with open(path) as f:
                raw = yaml.safe_load(f)
            if not isinstance(raw, dict):
                raise ValueError("YAML 根节点必须是 map")
            merged = _deep_merge(DEFAULTS, raw)
            self._last_good = merged
            self._last_mtime = mtime
            return dict(merged)
        except Exception as e:
            print(f"警告: 解析配置失败 ({e})，保留上次有效配置", file=sys.stderr)
            return dict(self._last_good)


# 全局单例（CLI 模式每次进程启动只加载一次，但支持 force 重载）
_loader = ConfigLoader()


def load_config(path: str, force: bool = False) -> dict:
    return _loader.load(path, force=force)


def export_shell(path: str) -> str:
    """输出 shell 变量赋值，供 bash eval 使用"""
    cfg = load_config(path)
    lines = [
        f'MAX_WORKERS={cfg["workers"]["max_concurrent"]}',
        f'STALL_TIMEOUT_SEC={cfg["workers"]["stall_timeout_sec"]}',
        f'MAX_CONSECUTIVE_FAILURES={cfg["workers"]["max_consecutive_failures"]}',
        f'INBOX_POLL_INTERVAL={cfg["polling"]["inbox_interval_sec"]}',
        f'RETRY_BASE_DELAY={cfg["retry"]["base_delay_sec"]}',
        f'RETRY_MAX_BACKOFF={cfg["retry"]["max_backoff_sec"]}',
        f'RATE_LIMIT_DEFAULT_WAIT={cfg["retry"]["rate_limit_default_wait_sec"]}',
        f'WORKTREE_BASE_DIR="{cfg["worktree"]["base_dir"]}"',
        f'WORKTREE_PREFIX="{cfg["worktree"]["prefix"]}"',
        f'PROMPTS_DIR="{cfg["prompts"]["dir"]}"',
        f'COMMON_RULES_FILE="{cfg["prompts"]["common_rules"]}"',
    ]
    return "\n".join(lines)


def render_prompt(phase: str, state_file: str, config_path: str, repo_dir: str) -> str:
    """渲染指定 phase 的 prompt 模板"""
    import json

    cfg = load_config(config_path)
    prompts_dir = os.path.join(repo_dir, cfg["prompts"]["dir"])
    common_rules_path = os.path.join(repo_dir, cfg["prompts"]["common_rules"])

    # phase 名转文件名: FAST_TRACK → fast-track.md
    filename = phase.lower().replace("_", "-") + ".md"
    template_path = os.path.join(prompts_dir, filename)

    if not os.path.exists(template_path):
        print(f"错误: prompt 模板不存在 {template_path}", file=sys.stderr)
        sys.exit(1)

    with open(template_path) as f:
        template = f.read()

    # 读取公共规则
    common_rules = ""
    if os.path.exists(common_rules_path):
        with open(common_rules_path) as f:
            common_rules = f.read().strip()

    # 从 state 文件提取变量
    variables = {"common_rules": common_rules}
    if os.path.exists(state_file):
        with open(state_file) as f:
            state = json.load(f)
        variables["dev_branch"] = state.get("dev_branch", "")
        variables["branch"] = state.get("branch", "")
        variables["spec_path"] = state.get("spec_path", "")
        variables["plan_path"] = state.get("plan_path", "")

        # 从 queue 中找 current_item 提取 slug
        item_id = state.get("current_item_id")
        slug = ""
        if item_id:
            for q in state.get("queue", []):
                if q["id"] == item_id:
                    dp = q.get("desc_path", "")
                    m = re.search(r"\d+-(.+?)_\d{2}-\d{2}_", dp)
                    slug = m.group(1) if m else re.sub(
                        r"[^\w-]", "-", q.get("name", str(item_id)).lower()
                    )[:40]
                    break
        variables["slug"] = slug

    # 替换 {{var}}，严格模式
    def replacer(match):
        var = match.group(1).strip()
        if var not in variables:
            print(f"错误: prompt 模板中引用了未知变量 {{{{{var}}}}}", file=sys.stderr)
            sys.exit(1)
        return variables[var]

    result = re.sub(r"\{\{(\w+)\}\}", replacer, template)
    return result


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: config.py <command> [args]", file=sys.stderr)
        print("  export-shell [config_path]", file=sys.stderr)
        print("  render-prompt <phase> <state_file> <config_path> <repo_dir>", file=sys.stderr)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "export-shell":
        path = sys.argv[2] if len(sys.argv) > 2 else "loop/workflow.yaml"
        print(export_shell(path))

    elif cmd == "render-prompt":
        if len(sys.argv) < 6:
            print("用法: config.py render-prompt <phase> <state_file> <config_path> <repo_dir>", file=sys.stderr)
            sys.exit(1)
        print(render_prompt(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]))

    else:
        print(f"未知命令: {cmd}", file=sys.stderr)
        sys.exit(1)
```

**Step 5: 运行测试确认通过**

```bash
cd loop/lib && python3 -m pytest test_config.py -v
```
Expected: 5 passed

**Step 6: Commit**

```bash
git add loop/workflow.yaml loop/lib/config.py loop/lib/test_config.py
git commit -m "feat(loop): config.py — YAML 配置加载 + shell 导出 + 热重载"
```

---

### Task 3: 实现 state.py — 状态读写 + 文件锁

这是最大的模块，替代 dev-loop.sh 中 12 个内联 Python 块。

**Files:**
- Create: `loop/lib/state.py`
- Test: `loop/lib/test_state.py`

**Step 1: 写失败测试**

```python
# loop/lib/test_state.py
import unittest
import tempfile
import os
import json

class TestState(unittest.TestCase):

    def _write_state(self, state: dict) -> str:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        json.dump(state, f, ensure_ascii=False)
        f.close()
        return f.name

    def test_read_phase_default(self):
        from state import read_phase
        self.assertEqual(read_phase("/nonexistent"), "INIT")

    def test_read_phase_from_file(self):
        from state import read_phase
        path = self._write_state({"current_phase": "VERIFY"})
        try:
            self.assertEqual(read_phase(path), "VERIFY")
        finally:
            os.unlink(path)

    def test_read_field(self):
        from state import read_field
        path = self._write_state({"branch": "feat/test", "spec_path": None})
        try:
            self.assertEqual(read_field(path, "branch"), "feat/test")
            self.assertEqual(read_field(path, "spec_path"), "")
            self.assertEqual(read_field(path, "nonexistent"), "")
        finally:
            os.unlink(path)

    def test_read_slug_from_desc_path(self):
        from state import read_slug
        path = self._write_state({
            "current_item_id": 1,
            "queue": [{"id": 1, "name": "test", "desc_path": "loop/tasks/1-my-feature_03-26_10-00.md"}]
        })
        try:
            self.assertEqual(read_slug(path), "my-feature")
        finally:
            os.unlink(path)

    def test_find_ready_task_ids(self):
        from state import find_ready_task_ids
        path = self._write_state({
            "completed": [{"id": 1}],
            "queue": [
                {"id": 1, "status": "done", "depends_on": []},
                {"id": 2, "status": "pending", "depends_on": [1]},
                {"id": 3, "status": "pending", "depends_on": [99]},
            ]
        })
        try:
            ready = find_ready_task_ids(path)
            self.assertEqual(ready, [2])
        finally:
            os.unlink(path)

    def test_claim_task(self):
        from state import claim_task
        path = self._write_state({
            "queue": [{"id": 5, "status": "pending"}]
        })
        try:
            result = claim_task(path, 5)
            self.assertTrue(result)
            with open(path) as f:
                state = json.load(f)
            self.assertEqual(state["queue"][0]["status"], "in_progress")
        finally:
            os.unlink(path)

    def test_claim_task_already_claimed(self):
        from state import claim_task
        path = self._write_state({
            "queue": [{"id": 5, "status": "in_progress"}]
        })
        try:
            result = claim_task(path, 5)
            self.assertFalse(result)
        finally:
            os.unlink(path)

    def test_count_pending(self):
        from state import count_pending
        path = self._write_state({
            "queue": [
                {"id": 1, "status": "done"},
                {"id": 2, "status": "pending"},
                {"id": 3, "status": "pending"},
            ]
        })
        try:
            self.assertEqual(count_pending(path), 2)
        finally:
            os.unlink(path)

    def test_update_fields(self):
        from state import update_fields
        path = self._write_state({"current_phase": "INIT", "branch": None})
        try:
            update_fields(path, current_phase="DESIGN", branch="feat/x")
            with open(path) as f:
                state = json.load(f)
            self.assertEqual(state["current_phase"], "DESIGN")
            self.assertEqual(state["branch"], "feat/x")
        finally:
            os.unlink(path)
```

**Step 2: 运行测试确认失败**

```bash
cd loop/lib && python3 -m pytest test_state.py -v 2>&1 | head -20
```
Expected: FAIL — `ModuleNotFoundError`

**Step 3: 实现 state.py**

```python
#!/usr/bin/env python3
"""loop/lib/state.py — loop-state.json 读写 + 文件锁"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone

LOCK_SUFFIX = ".lock"


def _load(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def _save(path: str, state: dict):
    state["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    with open(path, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _lock(path: str, timeout: float = 10.0):
    """基于 mkdir 的原子锁"""
    lock_dir = path + LOCK_SUFFIX
    start = time.monotonic()
    while True:
        try:
            os.makedirs(lock_dir)
            return
        except FileExistsError:
            if time.monotonic() - start > timeout:
                # 超时，强制清除可能的死锁
                try:
                    os.rmdir(lock_dir)
                except OSError:
                    pass
            time.sleep(0.05)


def _unlock(path: str):
    try:
        os.rmdir(path + LOCK_SUFFIX)
    except OSError:
        pass


def read_phase(path: str) -> str:
    state = _load(path)
    v = state.get("current_phase", "INIT")
    return "INIT" if v is None else v


def read_field(path: str, field: str) -> str:
    state = _load(path)
    v = state.get(field, "")
    return "" if v is None else str(v)


def read_slug(path: str) -> str:
    state = _load(path)
    item_id = state.get("current_item_id")
    if not item_id:
        return ""
    for q in state.get("queue", []):
        if q["id"] == item_id:
            dp = q.get("desc_path", "")
            m = re.search(r"\d+-(.+?)_\d{2}-\d{2}_", dp)
            if m:
                return m.group(1)
            slug = re.sub(r"[^\w-]", "-", q.get("name", str(item_id)).lower())[:40]
            return slug
    return ""


def find_ready_task_ids(path: str) -> list:
    state = _load(path)
    completed_ids = {c["id"] for c in state.get("completed", [])}
    ready = []
    for q in state.get("queue", []):
        if q.get("status") != "pending":
            continue
        deps = q.get("depends_on", [])
        if all(d in completed_ids for d in deps):
            ready.append(q["id"])
    return ready


def claim_task(path: str, task_id: int) -> bool:
    _lock(path)
    try:
        state = _load(path)
        for q in state.get("queue", []):
            if q["id"] == task_id and q.get("status") == "pending":
                q["status"] = "in_progress"
                _save(path, state)
                return True
        return False
    finally:
        _unlock(path)


def count_pending(path: str) -> int:
    state = _load(path)
    return sum(1 for q in state.get("queue", []) if q.get("status") == "pending")


def count_remaining(path: str) -> tuple:
    """返回 (total_pending, waiting_on_deps)"""
    state = _load(path)
    completed_ids = {c["id"] for c in state.get("completed", [])}
    pending = [q for q in state.get("queue", []) if q.get("status") in ("pending", "in_progress")]
    waiting = sum(1 for q in pending if not all(d in completed_ids for d in q.get("depends_on", [])))
    return len(pending), waiting


def get_task_name(path: str, task_id: int) -> str:
    state = _load(path)
    for q in state.get("queue", []):
        if q["id"] == task_id:
            return q.get("name", f"#{task_id}")
    return f"#{task_id}"


def create_worker_state(path: str, task_id: int, worker_dir: str, main_repo: str) -> str:
    """从主 state 提取单个任务，创建 worker state 文件，返回路径"""
    state = _load(path)
    task_info = None
    for q in state.get("queue", []):
        if q["id"] == task_id:
            task_info = dict(q)
            break
    if not task_info:
        raise ValueError(f"task {task_id} not found")

    os.makedirs(worker_dir, exist_ok=True)
    worker = {
        "dev_branch": state.get("dev_branch", ""),
        "current_item_id": task_id,
        "current_phase": "DESIGN_IMPLEMENT",
        "pr_number": None,
        "branch": None,
        "spec_path": None,
        "plan_path": None,
        "fix_list_path": None,
        "lock": {"session_id": None, "acquired_at": None},
        "stashed": False,
        "implement_progress": {
            "current_chunk": 0, "current_task": 0,
            "last_committed_task": None, "last_commit_sha": None,
            "current_step_attempts": 0,
        },
        "verify_attempts": 0,
        "merge_fix_attempts": 0,
        "completed": list(state.get("completed", [])),
        "blocked": list(state.get("blocked", [])),
        "queue": [task_info],
        "worktree_path": None,
        "main_repo_path": main_repo,
    }
    out = os.path.join(worker_dir, "state.json")
    with open(out, "w") as f:
        json.dump(worker, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return out


def sync_worker_result(path: str, task_id: int, worker_exit: int, worker_state_path: str):
    """同步 worker 结果回主 state"""
    _lock(path)
    try:
        state = _load(path)
        ws = {}
        if os.path.exists(worker_state_path):
            with open(worker_state_path) as f:
                ws = json.load(f)

        ws_phase = ws.get("current_phase", "")
        ws_queue = ws.get("queue", [])
        ws_task = ws_queue[0] if ws_queue else {}
        is_done = ws_task.get("status") == "done" or ws_phase in ("AWAITING_HUMAN_REVIEW", "FINALIZE")

        for q in state.get("queue", []):
            if q["id"] == task_id:
                if is_done and worker_exit == 0:
                    q["status"] = "done"
                    q["completed_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
                    q["spec_path"] = ws.get("spec_path") or ws_task.get("spec_path")
                    state.setdefault("completed", []).append({
                        "id": task_id, "name": q.get("name", "")
                    })
                else:
                    q["status"] = "pending"
                break

        # 依赖传播
        _propagate_blocked(state)
        _save(path, state)
    finally:
        _unlock(path)


def reset_orphan_tasks(path: str, active_task_ids: set):
    """重置孤儿任务（in_progress 但没有活跃 worker）"""
    _lock(path)
    try:
        state = _load(path)
        changed = False
        for q in state.get("queue", []):
            if q.get("status") == "in_progress" and str(q["id"]) not in active_task_ids:
                q["status"] = "pending"
                changed = True
        if changed:
            _save(path, state)
    finally:
        _unlock(path)


def select_next_task(path: str) -> bool:
    """选择下一个 pending 任务设为 current，返回是否找到"""
    _lock(path)
    try:
        state = _load(path)
        completed_ids = {c["id"] for c in state.get("completed", [])}
        for q in state.get("queue", []):
            if q.get("status") == "pending":
                deps = q.get("depends_on", [])
                if all(d in completed_ids for d in deps):
                    state["current_item_id"] = q["id"]
                    state["current_phase"] = "DESIGN_IMPLEMENT"
                    q["status"] = "in_progress"
                    _save(path, state)
                    return True
        return False
    finally:
        _unlock(path)


def reset_to_design_implement(path: str):
    """VERIFY/MERGE 失败时回退"""
    _lock(path)
    try:
        state = _load(path)
        state["current_phase"] = "DESIGN_IMPLEMENT"
        state["branch"] = None
        state["spec_path"] = None
        state["plan_path"] = None
        state["implement_progress"] = {
            "current_chunk": 0, "current_task": 0,
            "last_committed_task": None, "last_commit_sha": None,
            "current_step_attempts": 0,
        }
        _save(path, state)
    finally:
        _unlock(path)


def update_fields(path: str, **kwargs):
    """更新任意顶层字段"""
    _lock(path)
    try:
        state = _load(path)
        state.update(kwargs)
        _save(path, state)
    finally:
        _unlock(path)


def rebuild_from_tasks(state_file: str, tasks_dir: str, main_repo: str):
    """从 tasks/ 目录重建 loop-state.json（ensure_state_file 逻辑）"""
    from pathlib import Path

    task_files = sorted(Path(tasks_dir).glob("*.md"), key=lambda f: f.name)
    queue = []
    completed = []

    for f in task_files:
        m = re.match(r"(\d+)-(.+?)(?:_(\d{2}-\d{2}_\d{2}-\d{2}))?\.md$", f.name)
        if not m:
            continue
        item_id = int(m.group(1))
        slug = m.group(2)

        content = f.read_text()
        fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
        meta = {}
        if fm_match:
            for line in fm_match.group(1).split("\n"):
                if ":" in line:
                    key, val = line.split(":", 1)
                    meta[key.strip()] = val.strip().strip('"')

        status = meta.get("status", "open")
        is_done = status in ("done", "closed", "completed")

        item = {
            "id": item_id,
            "name": meta.get("name", slug.replace("-", " ")),
            "status": "done" if is_done else "pending",
            "depends_on": [],
            "complexity": meta.get("complexity", "中"),
            "created_at": meta.get("created_at", ""),
            "completed_at": None,
            "spec_path": None,
            "desc_path": f"loop/tasks/{f.name}",
        }
        queue.append(item)
        if is_done:
            completed.append({"id": item_id, "name": item["name"]})

    today = datetime.now().strftime("%Y-%m-%d")
    first_pending = next((q for q in queue if q["status"] == "pending"), None)

    state = {
        "dev_branch": f"dev/backlog-batch-{today}",
        "current_item_id": first_pending["id"] if first_pending else None,
        "current_phase": "DESIGN_IMPLEMENT" if first_pending else "AWAITING_HUMAN_REVIEW",
        "pr_number": None, "branch": None, "spec_path": None,
        "plan_path": None, "fix_list_path": None,
        "lock": {"session_id": None, "acquired_at": None},
        "stashed": False,
        "implement_progress": {
            "current_chunk": 0, "current_task": 0,
            "last_committed_task": None, "last_commit_sha": None,
            "current_step_attempts": 0,
        },
        "verify_attempts": 0, "merge_fix_attempts": 0,
        "completed": completed, "blocked": [], "queue": queue,
    }
    os.makedirs(os.path.dirname(state_file), exist_ok=True)
    _save(state_file, state)
    return len(queue), sum(1 for q in queue if q["status"] == "pending"), len(completed)


def _propagate_blocked(state: dict):
    """依赖传播：blocked 任务的下游也标记 blocked"""
    blocked_ids = {b["id"] for b in state.get("blocked", [])}
    changed = True
    while changed:
        changed = False
        for q in state.get("queue", []):
            if q.get("status") == "pending":
                for dep in q.get("depends_on", []):
                    if dep in blocked_ids:
                        q["status"] = "blocked"
                        state.setdefault("blocked", []).append({
                            "id": q["id"], "name": q.get("name", "")
                        })
                        blocked_ids.add(q["id"])
                        changed = True
                        break


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("用法: state.py <command> <state_file> [args]", file=sys.stderr)
        sys.exit(1)

    cmd, state_file = sys.argv[1], sys.argv[2]

    if cmd == "read-phase":
        print(read_phase(state_file))
    elif cmd == "read-field":
        print(read_field(state_file, sys.argv[3]))
    elif cmd == "read-slug":
        print(read_slug(state_file))
    elif cmd == "find-ready":
        for tid in find_ready_task_ids(state_file):
            print(tid)
    elif cmd == "claim":
        ok = claim_task(state_file, int(sys.argv[3]))
        print("ok" if ok else "")
    elif cmd == "count-pending":
        print(count_pending(state_file))
    elif cmd == "count-remaining":
        total, waiting = count_remaining(state_file)
        print(f"{total}:{waiting}")
    elif cmd == "get-name":
        print(get_task_name(state_file, int(sys.argv[3])))
    elif cmd == "create-worker":
        # state.py create-worker <state> <task_id> <worker_dir> <main_repo>
        out = create_worker_state(state_file, int(sys.argv[3]), sys.argv[4], sys.argv[5])
        print(out)
    elif cmd == "sync-result":
        # state.py sync-result <state> <task_id> <exit_code> <worker_state>
        sync_worker_result(state_file, int(sys.argv[3]), int(sys.argv[4]), sys.argv[5])
    elif cmd == "reset-orphans":
        # state.py reset-orphans <state> <active_ids_space_separated>
        active = set(sys.argv[3].split()) if len(sys.argv) > 3 else set()
        reset_orphan_tasks(state_file, active)
    elif cmd == "select-next":
        found = select_next_task(state_file)
        print("ok" if found else "")
    elif cmd == "reset-design":
        reset_to_design_implement(state_file)
    elif cmd == "update":
        # state.py update <state> key=value key=value ...
        kwargs = {}
        for arg in sys.argv[3:]:
            k, v = arg.split("=", 1)
            # 简单类型推断
            if v == "None" or v == "null":
                kwargs[k] = None
            elif v.isdigit():
                kwargs[k] = int(v)
            else:
                kwargs[k] = v
        update_fields(state_file, **kwargs)
    elif cmd == "rebuild":
        # state.py rebuild <state> <tasks_dir> <main_repo>
        total, pending, done = rebuild_from_tasks(state_file, sys.argv[3], sys.argv[4])
        print(f"{total}:{pending}:{done}")
    else:
        print(f"未知命令: {cmd}", file=sys.stderr)
        sys.exit(1)
```

**Step 4: 运行测试确认通过**

```bash
cd loop/lib && python3 -m pytest test_state.py -v
```
Expected: 9 passed

**Step 5: Commit**

```bash
git add loop/lib/state.py loop/lib/test_state.py
git commit -m "feat(loop): state.py — 状态读写模块，替代 12 个内联 Python 块"
```

---

### Task 4: 提取 prompt 模板文件 + _common-rules.md

从 `dev-loop.sh` 的 `build_prompt()` 函数（L474-L733）和 `COMMON_RULES`/`COMMON_STATE_INSTRUCTIONS`/`COMMON_CHECKPOINT`（L432-L471）中提取。

**Files:**
- Create: `loop/prompts/_common-rules.md`
- Create: `loop/prompts/init.md`
- Create: `loop/prompts/fast-track.md`
- Create: `loop/prompts/design-implement.md`
- Create: `loop/prompts/design.md`
- Create: `loop/prompts/implement.md`
- Create: `loop/prompts/verify.md`
- Create: `loop/prompts/fix.md`
- Create: `loop/prompts/merge.md`
- Create: `loop/prompts/merge-fix.md`
- Create: `loop/prompts/finalize.md`
- Create: `loop/prompts/ci-fix.md`

**Step 1: 创建 _common-rules.md**

从 `COMMON_RULES`（L432-L456）+ `COMMON_STATE_INSTRUCTIONS`（L458-L464）+ `COMMON_CHECKPOINT`（L466-L471）提取：

```markdown
<!-- loop/prompts/_common-rules.md -->
## 硬性红线
- 禁止合并代码到 master（禁止 git merge/push/checkout master，禁止 gh pr merge）
- 禁止请求用户输入或等待用户操作（非交互模式）
- 后端 ESM：本地 import 必须用 .js 扩展名
- 切分支后必须 npm install
- 业务逻辑使用中文注释

## 硬性门禁（每个 Task 完成后必须全部 exit 0）
```bash
npm run lint && npm test && npm run build
npx tsc --noEmit -p frontend
```

## 时间格式
全栈统一 ISO 8601 字符串，禁止 Unix 时间戳（poi_cache.fetched_at 除外）。

## 移动端
App.tsx 中 PC/移动端是独立组件树。新 UI 功能必须同时实现两端。

## UI 规范
- 新代码用 Tailwind，用 cn() 合并 class，颜色取 tokens.json
- Modal: fixed inset-0 bg-black/60 z-50, rounded-2xl
- Button: 用 components/ui/button.tsx

## 状态文件操作
- 读取 loop-state.json 获取: dev_branch, current_item_id, branch, spec_path, plan_path
- 从 queue 数组中找到 id == current_item_id 的条目，读取其 desc_path 获取需求描述
- 每个 Phase 完成后必须更新 loop-state.json（先更新 phase，再做 git 操作）
- lock 字段：开始时写入 session_id + acquired_at，结束时清空

## Checkpoint（断点续传）
- 如果 branch 字段非空且分支已存在 → 从已有分支继续，不重新创建
- 查看 implement_progress 判断已完成的 Task，从下一个开始
- 每完成一个 Task：commit + 更新 implement_progress + 保存 loop-state.json
```

**Step 2: 逐个创建 phase prompt 文件**

每个文件内容从 `build_prompt()` 对应 case 分支提取，将硬编码变量替换为 `{{var}}` 占位符，将 `${COMMON_RULES}` 替换为 `{{common_rules}}`。

参考 `dev-loop.sh` 中的行号映射：
- INIT: L478-L490
- FAST_TRACK: L493-L517
- DESIGN_IMPLEMENT: L519-L555
- DESIGN: L557-L579
- IMPLEMENT: L581-L605
- VERIFY: L607-L647（注意其中有动态变量 `$spec_path`, `$dev_branch`, `$branch`）
- FIX: L650-L664
- MERGE: L666-L688
- MERGE_FIX: L690-L698
- FINALIZE: L700-L714
- CI_FIX: L715-L728

每个模板中：
- `{dev_branch}` → `{{dev_branch}}`
- `{slug}` → `{{slug}}`
- `{branch}` → `{{branch}}`
- `{date}` → `{{date}}`
- `${COMMON_RULES}` → `{{common_rules}}`
- `${COMMON_CHECKPOINT}` → 直接内联到 `_common-rules.md` 中（已包含）
- VERIFY 中的 `$spec_path`, `$dev_branch`, `$branch` → `{{spec_path}}`, `{{dev_branch}}`, `{{branch}}`

**Step 3: 删除 loop/prompts/.gitkeep**

不再需要，有实际文件了。

**Step 4: Commit**

```bash
git add loop/prompts/
git rm loop/prompts/.gitkeep 2>/dev/null || true
git commit -m "feat(loop): 提取 prompt 模板文件（12 个 phase + common rules）"
```

---

### Task 5: dev-loop.sh 接入 Python 模块（Phase 1 集成）

将 dev-loop.sh 中的内联 Python 调用替换为 `loop/lib/` 模块调用，保持行为完全不变。

**Files:**
- Modify: `loop/dev-loop.sh`

**Step 1: 替换配置区（L26-L38 的硬编码常量）**

替换前：
```bash
INBOX_POLL_INTERVAL=60
MAX_CONSECUTIVE_FAILURES=3
# ...
```

替换后：
```bash
# 从 workflow.yaml 加载配置
eval "$(python3 "${MAIN_REPO_DIR}/loop/lib/config.py" export-shell "${MAIN_REPO_DIR}/loop/workflow.yaml")"
```

保留不在配置中的路径变量（`STATE_FILE`, `INBOX_DIR` 等），它们依赖 `MAIN_REPO_DIR`。

**Step 2: 替换辅助函数（L45-L77）**

替换 `read_phase`, `read_field`, `read_slug` 函数体：

```bash
read_phase() {
  python3 "${MAIN_REPO_DIR}/loop/lib/state.py" read-phase "$STATE_FILE"
}
read_field() {
  python3 "${MAIN_REPO_DIR}/loop/lib/state.py" read-field "$STATE_FILE" "$1"
}
read_slug() {
  python3 "${MAIN_REPO_DIR}/loop/lib/state.py" read-slug "$STATE_FILE"
}
```

**Step 3: 替换 build_prompt() 函数（L474-L733）**

```bash
build_prompt() {
  local phase="$1"
  python3 "${MAIN_REPO_DIR}/loop/lib/config.py" render-prompt "$phase" "$STATE_FILE" "${MAIN_REPO_DIR}/loop/workflow.yaml" "$MAIN_REPO_DIR"
}
```

**Step 4: 替换并行模式中的内联 Python 调用**

逐个替换 `find_ready_task_ids`, `claim_task`, `create_worker_state`, `sync_worker_result`, 孤儿回收, 计数等函数中的 `python3 -c` 调用为 `python3 loop/lib/state.py <cmd>` 调用。

**Step 5: 替换 ensure_state_file 中的重建逻辑**

```bash
ensure_state_file() {
  [ -f "$STATE_FILE" ] && return 0
  log "⚠️ loop-state.json 不存在，从 tasks/ 目录重建..."
  result=$(python3 "${MAIN_REPO_DIR}/loop/lib/state.py" rebuild "$STATE_FILE" "$TASKS_DIR" "$MAIN_REPO_DIR")
  log "✅ loop-state.json 已重建 ($result)"
}
```

**Step 6: 删除不再需要的 bash 变量**

删除 `COMMON_RULES`, `COMMON_STATE_INSTRUCTIONS`, `COMMON_CHECKPOINT` 变量（L432-L471），已迁移到 `_common-rules.md`。

**Step 7: 验证行为不变**

```bash
# 手动运行一轮确认不报错
cd loop && bash dev-loop.sh --verbose 2>&1 | head -30
# 检查 status 是否正常读取
python3 loop/lib/state.py read-phase loop/loop-state.json
```

**Step 8: Commit**

```bash
git add loop/dev-loop.sh
git commit -m "refactor(loop): dev-loop.sh 接入 Python 模块，消除内联 Python 代码"
```

---

## Phase 2：可靠性

### Task 6: 实现 retry.py — 指数退避

**Files:**
- Create: `loop/lib/retry.py`
- Test: `loop/lib/test_retry.py`

**Step 1: 写失败测试**

```python
# loop/lib/test_retry.py
import unittest

class TestRetry(unittest.TestCase):

    def test_backoff_attempt_1(self):
        from retry import backoff
        self.assertEqual(backoff(1, base=10, max_sec=300), 10)

    def test_backoff_attempt_2(self):
        from retry import backoff
        self.assertEqual(backoff(2, base=10, max_sec=300), 20)

    def test_backoff_attempt_3(self):
        from retry import backoff
        self.assertEqual(backoff(3, base=10, max_sec=300), 40)

    def test_backoff_capped(self):
        from retry import backoff
        self.assertEqual(backoff(10, base=10, max_sec=300), 300)

    def test_backoff_attempt_0_returns_base(self):
        from retry import backoff
        self.assertEqual(backoff(0, base=10, max_sec=300), 10)
```

**Step 2: 运行测试确认失败**

```bash
cd loop/lib && python3 -m pytest test_retry.py -v
```

**Step 3: 实现 retry.py**

```python
#!/usr/bin/env python3
"""loop/lib/retry.py — 指数退避计算"""
import sys


def backoff(attempt: int, base: int = 10, max_sec: int = 300) -> int:
    """min(base * 2^(attempt-1), max_sec)，attempt < 1 时返回 base"""
    exp = max(0, attempt - 1)
    delay = base * (2 ** exp)
    return min(delay, max_sec)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: retry.py backoff <attempt> [base] [max]", file=sys.stderr)
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "backoff":
        attempt = int(sys.argv[2])
        base = int(sys.argv[3]) if len(sys.argv) > 3 else 10
        max_sec = int(sys.argv[4]) if len(sys.argv) > 4 else 300
        print(backoff(attempt, base, max_sec))
    else:
        print(f"未知命令: {cmd}", file=sys.stderr)
        sys.exit(1)
```

**Step 4: 运行测试确认通过**

```bash
cd loop/lib && python3 -m pytest test_retry.py -v
```
Expected: 5 passed

**Step 5: Commit**

```bash
git add loop/lib/retry.py loop/lib/test_retry.py
git commit -m "feat(loop): retry.py — 指数退避计算"
```

---

### Task 7: 实现 reconcile.py — Stall 检测 + 取消 + 孤儿回收

**Files:**
- Create: `loop/lib/reconcile.py`
- Test: `loop/lib/test_reconcile.py`

**Step 1: 写失败测试**

```python
# loop/lib/test_reconcile.py
import unittest
import tempfile
import os
import json
import time

class TestReconcile(unittest.TestCase):

    def _make_dirs(self):
        base = tempfile.mkdtemp()
        workers = os.path.join(base, "workers")
        logs = os.path.join(base, "logs")
        inbox = os.path.join(base, "inbox")
        tasks = os.path.join(base, "tasks")
        os.makedirs(workers)
        os.makedirs(logs)
        os.makedirs(inbox)
        os.makedirs(tasks)
        return base, workers, logs, inbox, tasks

    def test_detect_stalled_worker(self):
        from reconcile import detect_stalled_workers
        base, workers, logs, _, _ = self._make_dirs()
        # 模拟一个 worker
        w1 = os.path.join(workers, "1")
        os.makedirs(w1)
        with open(os.path.join(w1, "pid"), "w") as f:
            f.write("99999")  # 不存在的 PID
        log_file = os.path.join(logs, "worker-1.log")
        with open(log_file, "w") as f:
            f.write("some log")
        # 把 mtime 设为 1 小时前
        old_time = time.time() - 3600
        os.utime(log_file, (old_time, old_time))

        stalled = detect_stalled_workers(workers, logs, stall_timeout_sec=1800)
        self.assertEqual(stalled, [1])

    def test_no_stall_when_recent(self):
        from reconcile import detect_stalled_workers
        base, workers, logs, _, _ = self._make_dirs()
        w1 = os.path.join(workers, "1")
        os.makedirs(w1)
        with open(os.path.join(w1, "pid"), "w") as f:
            f.write("99999")
        log_file = os.path.join(logs, "worker-1.log")
        with open(log_file, "w") as f:
            f.write("recent log")

        stalled = detect_stalled_workers(workers, logs, stall_timeout_sec=1800)
        self.assertEqual(stalled, [])

    def test_detect_cancel_signal(self):
        from reconcile import detect_cancellations
        base, _, _, inbox, _ = self._make_dirs()
        # 创建取消信号
        with open(os.path.join(inbox, "CANCEL-5"), "w") as f:
            f.write("")
        cancelled = detect_cancellations(inbox)
        self.assertEqual(cancelled, [5])
        # 信号文件应被删除
        self.assertFalse(os.path.exists(os.path.join(inbox, "CANCEL-5")))
```

**Step 2: 运行测试确认失败**

```bash
cd loop/lib && python3 -m pytest test_reconcile.py -v
```

**Step 3: 实现 reconcile.py**

```python
#!/usr/bin/env python3
"""loop/lib/reconcile.py — stall 检测 + 取消 + 孤儿回收"""
import json
import os
import re
import signal
import sys
import time


def detect_stalled_workers(workers_dir: str, logs_dir: str, stall_timeout_sec: int) -> list:
    """返回卡死的 task_id 列表"""
    stalled = []
    if not os.path.isdir(workers_dir):
        return stalled
    now = time.time()
    for entry in os.listdir(workers_dir):
        pid_file = os.path.join(workers_dir, entry, "pid")
        if not os.path.exists(pid_file):
            continue
        task_id = int(entry)

        # 最近活动时间 = 日志文件 mtime
        log_file = os.path.join(logs_dir, f"worker-{task_id}.log")
        if os.path.exists(log_file):
            last_activity = os.path.getmtime(log_file)
        else:
            # 无日志，用 pid 文件创建时间
            last_activity = os.path.getmtime(pid_file)

        elapsed = now - last_activity
        if elapsed > stall_timeout_sec:
            stalled.append(task_id)
    return stalled


def kill_worker(workers_dir: str, task_id: int):
    """终止 worker 进程及其子进程"""
    pid_file = os.path.join(workers_dir, str(task_id), "pid")
    if not os.path.exists(pid_file):
        return
    try:
        with open(pid_file) as f:
            pid = int(f.read().strip())
        os.kill(pid, signal.SIGTERM)
        # 也尝试杀子进程
        os.system(f"pkill -P {pid} 2>/dev/null")
    except (OSError, ValueError):
        pass


def detect_cancellations(inbox_dir: str) -> list:
    """扫描 inbox/CANCEL-{id} 文件，返回 task_id 列表，并删除信号文件"""
    cancelled = []
    if not os.path.isdir(inbox_dir):
        return cancelled
    for entry in os.listdir(inbox_dir):
        m = re.match(r"CANCEL-(\d+)$", entry)
        if m:
            task_id = int(m.group(1))
            cancelled.append(task_id)
            os.unlink(os.path.join(inbox_dir, entry))
    return cancelled


def run(workers_dir: str, logs_dir: str, inbox_dir: str, state_file: str,
        stall_timeout_sec: int) -> dict:
    """执行完整 reconciliation，返回动作摘要"""
    actions = {"stalled": [], "cancelled": [], "killed": []}

    # 1. Stall 检测
    stalled_ids = detect_stalled_workers(workers_dir, logs_dir, stall_timeout_sec)
    for tid in stalled_ids:
        kill_worker(workers_dir, tid)
        actions["stalled"].append(tid)
        actions["killed"].append(tid)

    # 2. 取消检测
    cancelled_ids = detect_cancellations(inbox_dir)
    for tid in cancelled_ids:
        kill_worker(workers_dir, tid)
        if tid not in actions["killed"]:
            actions["killed"].append(tid)
        actions["cancelled"].append(tid)

    # 3. 更新被取消任务的状态
    if cancelled_ids and os.path.exists(state_file):
        from state import _lock, _unlock, _load, _save
        _lock(state_file)
        try:
            state = _load(state_file)
            for q in state.get("queue", []):
                if q["id"] in cancelled_ids:
                    q["status"] = "cancelled"
            _save(state_file, state)
        finally:
            _unlock(state_file)

    return actions


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: reconcile.py run <workers_dir> <logs_dir> <inbox_dir> <state_file> <stall_timeout>",
              file=sys.stderr)
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "run":
        result = run(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5], int(sys.argv[6]))
        print(json.dumps(result))
    else:
        print(f"未知命令: {cmd}", file=sys.stderr)
        sys.exit(1)
```

**Step 4: 运行测试确认通过**

```bash
cd loop/lib && python3 -m pytest test_reconcile.py -v
```
Expected: 3 passed

**Step 5: Commit**

```bash
git add loop/lib/reconcile.py loop/lib/test_reconcile.py
git commit -m "feat(loop): reconcile.py — stall 检测 + 取消信号 + worker 终止"
```

---

### Task 8: dev-loop.sh 集成可靠性功能（Phase 2 集成）

**Files:**
- Modify: `loop/dev-loop.sh`

**Step 1: 在 parallel_main 的回收循环前插入 reconciliation 调用**

在 `parallel_main` 的 `while true` 循环中，在现有的 "回收已完成的 worker" 之前插入：

```bash
    # Reconciliation：stall 检测 + 取消
    reconcile_result=$(python3 "${MAIN_REPO_DIR}/loop/lib/reconcile.py" run \
      "$WORKERS_DIR" "$LOG_DIR" "$INBOX_DIR" "$STATE_FILE" "$STALL_TIMEOUT_SEC")
    stalled=$(echo "$reconcile_result" | python3 -c "import json,sys; print(' '.join(str(x) for x in json.load(sys.stdin).get('stalled',[])))")
    cancelled=$(echo "$reconcile_result" | python3 -c "import json,sys; print(' '.join(str(x) for x in json.load(sys.stdin).get('cancelled',[])))")
    [ -n "$stalled" ] && LOG_TAG=reconcile log "⚠️ 卡死 worker: $stalled"
    [ -n "$cancelled" ] && LOG_TAG=reconcile log "🚫 已取消: $cancelled"
```

**Step 2: 替换固定 30s 重试为指数退避**

找到 `sleep 30`（约 L1624），替换为：

```bash
    wait_sec=$(python3 "${MAIN_REPO_DIR}/loop/lib/retry.py" backoff "$consecutive_failures" "$RETRY_BASE_DELAY" "$RETRY_MAX_BACKOFF")
    log "等待 ${wait_sec}s 后重试 (attempt: $consecutive_failures)..."
    sleep "$wait_sec"
```

**Step 3: 修改 worktree 清理策略 — 失败时保留**

在 `parallel_main` 回收 worker 的逻辑中（约 L1241），将无条件 cleanup 改为条件判断：

```bash
        # 只在成功时清理 worktree（失败保留，下次复用）
        if [ "$exit_code" -eq 0 ] && [ -n "$wt_slug" ]; then
          cleanup_worktree "$wt_slug"
        fi
```

**Step 4: 升级 log() 函数支持 tag**

```bash
log() {
  local tag="${LOG_TAG:-scheduler}"
  echo "[$(timestamp)] [${tag}] $*"
  # 同时写入汇总日志
  echo "[$(timestamp)] [${tag}] $*" >> "$LOG_DIR/orchestrator.log" 2>/dev/null
}
```

**Step 5: 验证**

```bash
# 手动确认配置加载 + 退避计算
python3 loop/lib/retry.py backoff 1
python3 loop/lib/retry.py backoff 3
python3 loop/lib/reconcile.py run loop/workers loop/logs loop/inbox loop/loop-state.json 1800
```

**Step 6: Commit**

```bash
git add loop/dev-loop.sh
git commit -m "feat(loop): 集成 stall 检测 + 指数退避 + 条件清理 worktree"
```

---

## Phase 3：可观测性 + 收尾

### Task 9: 实现 status.py + status.sh

**Files:**
- Create: `loop/lib/status.py`
- Create: `loop/status.sh`
- Test: `loop/lib/test_status.py`

**Step 1: 写失败测试**

```python
# loop/lib/test_status.py
import unittest
import tempfile
import os
import json

class TestStatus(unittest.TestCase):

    def _write_state(self, state: dict) -> str:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        json.dump(state, f, ensure_ascii=False)
        f.close()
        return f.name

    def test_build_summary_json(self):
        from status import build_summary
        path = self._write_state({
            "dev_branch": "dev/test",
            "updated_at": "2026-03-27T10:00:00.000Z",
            "queue": [
                {"id": 1, "name": "feat-a", "status": "done"},
                {"id": 2, "name": "feat-b", "status": "in_progress"},
                {"id": 3, "name": "feat-c", "status": "pending", "complexity": "低"},
            ],
            "completed": [{"id": 1}],
        })
        try:
            summary = build_summary(path, workers_dir="/nonexistent", logs_dir="/nonexistent",
                                    stall_timeout_sec=1800)
            self.assertEqual(summary["counts"]["done"], 1)
            self.assertEqual(summary["counts"]["running"], 0)
            self.assertEqual(summary["counts"]["pending"], 1)
            self.assertEqual(summary["dev_branch"], "dev/test")
        finally:
            os.unlink(path)

    def test_format_text(self):
        from status import format_text
        summary = {
            "dev_branch": "dev/test",
            "updated_at": "2026-03-27T10:00:00.000Z",
            "counts": {"running": 0, "pending": 1, "done": 2, "blocked": 0},
            "running": [],
            "pending": [{"task_id": 3, "name": "feat-c", "complexity": "低"}],
            "done": [{"task_id": 1, "name": "feat-a"}, {"task_id": 2, "name": "feat-b"}],
            "alerts": [],
        }
        text = format_text(summary, max_workers=5)
        self.assertIn("dev/test", text)
        self.assertIn("feat-c", text)
```

**Step 2: 运行测试确认失败**

```bash
cd loop/lib && python3 -m pytest test_status.py -v
```

**Step 3: 实现 status.py**

```python
#!/usr/bin/env python3
"""loop/lib/status.py — 状态汇总输出（text / JSON）"""
import json
import os
import sys
import time
from datetime import datetime, timezone


def build_summary(state_file: str, workers_dir: str, logs_dir: str,
                  stall_timeout_sec: int) -> dict:
    """构建状态摘要"""
    if not os.path.exists(state_file):
        return {"error": "state file not found"}

    with open(state_file) as f:
        state = json.load(f)

    now = time.time()

    # 分类任务
    running_tasks = []
    pending_tasks = []
    done_tasks = []
    blocked_tasks = []

    for q in state.get("queue", []):
        status = q.get("status", "pending")
        entry = {"task_id": q["id"], "name": q.get("name", "")}

        if status == "in_progress":
            # 检查是否有活跃 worker
            pid_file = os.path.join(workers_dir, str(q["id"]), "pid")
            phase = ""
            elapsed_min = 0
            log_stale = False

            if os.path.exists(pid_file):
                # 从 worker state 读 phase
                ws_file = os.path.join(workers_dir, str(q["id"]), "state.json")
                if os.path.exists(ws_file):
                    with open(ws_file) as wf:
                        ws = json.load(wf)
                    phase = ws.get("current_phase", "")

                # 计算运行时间
                pid_mtime = os.path.getmtime(pid_file)
                elapsed_min = int((now - pid_mtime) / 60)

                # 日志是否过时
                log_file = os.path.join(logs_dir, f"worker-{q['id']}.log")
                if os.path.exists(log_file):
                    log_age = now - os.path.getmtime(log_file)
                    log_stale = log_age > stall_timeout_sec

            entry.update({"phase": phase, "elapsed_min": elapsed_min, "log_stale": log_stale})
            running_tasks.append(entry)

        elif status == "pending":
            entry["complexity"] = q.get("complexity", "中")
            entry["depends_on"] = q.get("depends_on", [])
            pending_tasks.append(entry)

        elif status == "done":
            done_tasks.append(entry)

        elif status in ("blocked", "cancelled"):
            entry["status"] = status
            blocked_tasks.append(entry)

    # 告警
    alerts = []
    for r in running_tasks:
        if r.get("log_stale"):
            alerts.append(f"Worker #{r['task_id']} 日志超时，可能卡死")

    return {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "dev_branch": state.get("dev_branch", ""),
        "updated_at": state.get("updated_at", ""),
        "counts": {
            "running": len(running_tasks),
            "pending": len(pending_tasks),
            "done": len(done_tasks),
            "blocked": len(blocked_tasks),
        },
        "running": running_tasks,
        "pending": pending_tasks,
        "done": done_tasks,
        "alerts": alerts,
    }


def format_text(summary: dict, max_workers: int = 5) -> str:
    """格式化为人类可读文本"""
    lines = []
    lines.append("═══ dev-loop 状态 ═══")
    lines.append(f"分支: {summary.get('dev_branch', '未知')}")

    updated = summary.get("updated_at", "")
    if updated:
        try:
            dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
            age_sec = (datetime.now(timezone.utc) - dt).total_seconds()
            if age_sec < 60:
                age_str = f"{int(age_sec)}s 前"
            elif age_sec < 3600:
                age_str = f"{int(age_sec/60)}m 前"
            else:
                age_str = f"{int(age_sec/3600)}h 前"
            lines.append(f"更新: {age_str}")
        except Exception:
            pass

    counts = summary.get("counts", {})
    lines.append("")

    # 运行中
    running = summary.get("running", [])
    lines.append(f"🔄 运行中 ({counts.get('running', 0)}/{max_workers})")
    if running:
        for r in running:
            stale = "  ⚠️ 日志过时" if r.get("log_stale") else "  日志活跃"
            lines.append(f"  #{r['task_id']} {r['name']}    {r.get('phase','')}  {r.get('elapsed_min',0)}m{stale}")
    else:
        lines.append("  （无）")

    # 待执行
    pending = summary.get("pending", [])
    lines.append(f"\n⏳ 待执行 ({counts.get('pending', 0)})")
    if pending:
        for p in pending:
            suffix = f" [{p.get('complexity', '中')}]"
            deps = p.get("depends_on", [])
            if deps:
                suffix += f"  ← blocked by #{', #'.join(str(d) for d in deps)}"
            lines.append(f"  #{p['task_id']} {p['name']}{suffix}")
    else:
        lines.append("  （无）")

    # 已完成
    done = summary.get("done", [])
    lines.append(f"\n✅ 已完成 ({counts.get('done', 0)})")
    if done:
        done_line = "  " + "  ".join(f"#{d['task_id']} {d['name']}" for d in done)
        lines.append(done_line)
    else:
        lines.append("  （无）")

    # 告警
    alerts = summary.get("alerts", [])
    lines.append(f"\n⚠️ 告警")
    if alerts:
        for a in alerts:
            lines.append(f"  {a}")
    else:
        lines.append("  （无）")

    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="dev-loop 状态汇总")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("--state", default=None, help="state 文件路径")
    parser.add_argument("--workers", default=None)
    parser.add_argument("--logs", default=None)
    parser.add_argument("--stall-timeout", type=int, default=1800)
    parser.add_argument("--max-workers", type=int, default=5)
    args = parser.parse_args()

    # 自动推断路径
    script_dir = os.path.dirname(os.path.abspath(__file__))
    loop_dir = os.path.dirname(script_dir)
    state_file = args.state or os.path.join(loop_dir, "loop-state.json")
    workers_dir = args.workers or os.path.join(loop_dir, "workers")
    logs_dir = args.logs or os.path.join(loop_dir, "logs")

    summary = build_summary(state_file, workers_dir, logs_dir, args.stall_timeout)

    if args.format == "json":
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        print(format_text(summary, max_workers=args.max_workers))
```

**Step 4: 运行测试确认通过**

```bash
cd loop/lib && python3 -m pytest test_status.py -v
```
Expected: 2 passed

**Step 5: 创建 status.sh**

```bash
#!/usr/bin/env bash
# loop/status.sh — 一键查看 dev-loop 运行状态
python3 "$(dirname "$0")/lib/status.py" "$@"
```

```bash
chmod +x loop/status.sh
```

**Step 6: Commit**

```bash
git add loop/lib/status.py loop/lib/test_status.py loop/status.sh
git commit -m "feat(loop): status.py + status.sh — 状态汇总面板"
```

---

### Task 10: 提取 inbox.py — inbox 处理模块

**Files:**
- Create: `loop/lib/inbox.py`
- Test: `loop/lib/test_inbox.py`

**Step 1: 写失败测试**

```python
# loop/lib/test_inbox.py
import unittest
import tempfile
import os
import json
import shutil

class TestInbox(unittest.TestCase):

    def _setup_dirs(self):
        base = tempfile.mkdtemp()
        inbox = os.path.join(base, "inbox")
        tasks = os.path.join(base, "tasks")
        os.makedirs(inbox)
        os.makedirs(tasks)
        state_file = os.path.join(base, "state.json")
        with open(state_file, "w") as f:
            json.dump({"queue": []}, f)
        return base, inbox, tasks, state_file

    def test_process_empty_inbox(self):
        from inbox import process_inbox
        base, inbox, tasks, state_file = self._setup_dirs()
        added = process_inbox(inbox, tasks, state_file)
        self.assertEqual(added, [])
        shutil.rmtree(base)

    def test_process_single_file(self):
        from inbox import process_inbox
        base, inbox, tasks, state_file = self._setup_dirs()
        # 创建 inbox 文件
        with open(os.path.join(inbox, "my-feature.md"), "w") as f:
            f.write("---\nname: My Feature\ncomplexity: 低\n---\n\n实现一个功能\n")
        added = process_inbox(inbox, tasks, state_file)
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0]["name"], "My Feature")
        # 文件应从 inbox 移到 tasks
        self.assertEqual(len(os.listdir(inbox)), 0)
        self.assertEqual(len(os.listdir(tasks)), 1)
        # state 应有新条目
        with open(state_file) as f:
            state = json.load(f)
        self.assertEqual(len(state["queue"]), 1)
        self.assertEqual(state["queue"][0]["name"], "My Feature")
        shutil.rmtree(base)

    def test_detect_stop_signal(self):
        from inbox import check_stop_signal
        base, inbox, _, _ = self._setup_dirs()
        with open(os.path.join(inbox, "STOP"), "w") as f:
            f.write("")
        self.assertTrue(check_stop_signal(inbox))
        # STOP 文件应被删除
        self.assertFalse(os.path.exists(os.path.join(inbox, "STOP")))
        shutil.rmtree(base)
```

**Step 2: 运行测试确认失败**

```bash
cd loop/lib && python3 -m pytest test_inbox.py -v
```

**Step 3: 实现 inbox.py**

从 `dev-loop.sh` L306-L416 的 `process_inbox` 内联 Python 提取，保持逻辑不变。

```python
#!/usr/bin/env python3
"""loop/lib/inbox.py — inbox 扫描 + 任务入队"""
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path


def parse_frontmatter(filepath: str) -> dict:
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


def inject_created_at(filepath: str, created_at_str: str):
    with open(filepath) as f:
        content = f.read()
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    if match:
        fm_text = match.group(1)
        if "created_at:" in fm_text:
            return
        new_fm = fm_text + f'\ncreated_at: "{created_at_str}"'
        content = f"---\n{new_fm}\n---\n" + content[match.end():]
    else:
        content = f'---\ncreated_at: "{created_at_str}"\n---\n\n' + content
    with open(filepath, "w") as f:
        f.write(content)


def check_stop_signal(inbox_dir: str) -> bool:
    stop_file = os.path.join(inbox_dir, "STOP")
    if os.path.exists(stop_file):
        os.unlink(stop_file)
        return True
    return False


def process_inbox(inbox_dir: str, tasks_dir: str, state_file: str) -> list:
    """处理 inbox 中的新任务文件，返回新增条目列表"""
    files = sorted(Path(inbox_dir).glob("*.md"), key=lambda f: f.stat().st_mtime)
    if not files:
        return []

    # 加载 state
    if os.path.exists(state_file):
        with open(state_file) as f:
            state = json.load(f)
    else:
        state = {"queue": []}

    # 计算 max_id
    max_id = 0
    for item in state.get("queue", []):
        max_id = max(max_id, item.get("id", 0))
    for f in Path(tasks_dir).glob("*.md"):
        m = re.match(r"(\d+)-", f.name)
        if m:
            max_id = max(max_id, int(m.group(1)))

    now = datetime.now()
    now_stamp = now.strftime("%m-%d_%H-%M")
    now_display = now.strftime("%Y-%m-%d %H:%M")

    added = []
    move_ops = []  # (src, dst, id, name)
    for f in files:
        meta = parse_frontmatter(str(f))
        slug = f.stem
        max_id += 1

        task_path = f"{tasks_dir}/{max_id}-{slug}_{now_stamp}.md"
        entry = {
            "id": max_id,
            "name": meta.get("name", slug),
            "status": "pending",
            "depends_on": meta.get("depends_on", []),
            "complexity": meta.get("complexity", "中"),
            "created_at": now_display,
            "completed_at": None,
            "spec_path": None,
            "desc_path": task_path,
        }
        state.setdefault("queue", []).append(entry)
        added.append(entry)
        move_ops.append((str(f), task_path, max_id, meta.get("name", slug)))

    # 持久化 state
    state["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    with open(state_file, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
        f.write("\n")

    # 注入 created_at + 移动文件
    os.makedirs(tasks_dir, exist_ok=True)
    for src, dst, item_id, name in move_ops:
        inject_created_at(src, now_display)
        shutil.move(src, dst)
        print(f"  ✅ #{item_id}: {name} → {dst}", file=sys.stderr)

    return added


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: inbox.py <command> [args]", file=sys.stderr)
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "check-stop":
        result = check_stop_signal(sys.argv[2])
        print("STOP" if result else "")
    elif cmd == "process":
        # inbox.py process <inbox_dir> <tasks_dir> <state_file>
        added = process_inbox(sys.argv[2], sys.argv[3], sys.argv[4])
        print(len(added))
    else:
        print(f"未知命令: {cmd}", file=sys.stderr)
        sys.exit(1)
```

**Step 4: 运行测试确认通过**

```bash
cd loop/lib && python3 -m pytest test_inbox.py -v
```
Expected: 3 passed

**Step 5: Commit**

```bash
git add loop/lib/inbox.py loop/lib/test_inbox.py
git commit -m "feat(loop): inbox.py — inbox 处理模块，从内联代码提取"
```

---

### Task 11: 提取 backlog.py — backlog.md 生成模块

**Files:**
- Create: `loop/lib/backlog.py`
- Test: `loop/lib/test_backlog.py`

**Step 1: 写失败测试**

```python
# loop/lib/test_backlog.py
import unittest
import tempfile
import os
import json

class TestBacklog(unittest.TestCase):

    def test_refresh_basic(self):
        from backlog import refresh_backlog
        base = tempfile.mkdtemp()
        state_file = os.path.join(base, "state.json")
        backlog_file = os.path.join(base, "backlog.md")

        with open(state_file, "w") as f:
            json.dump({
                "queue": [
                    {"id": 1, "name": "feat-a", "status": "done", "complexity": "低",
                     "created_at": "03-26 10:00", "completed_at": "03-26 12:00"},
                    {"id": 2, "name": "feat-b", "status": "pending", "complexity": "中",
                     "created_at": "03-26 11:00"},
                ]
            }, f)

        refresh_backlog(state_file, backlog_file)
        self.assertTrue(os.path.exists(backlog_file))
        content = open(backlog_file).read()
        self.assertIn("feat-a", content)
        self.assertIn("feat-b", content)
        self.assertIn("已完成", content)
        self.assertIn("待开发", content)
```

**Step 2: 运行测试确认失败**

```bash
cd loop/lib && python3 -m pytest test_backlog.py -v
```

**Step 3: 实现 backlog.py**

从 `dev-loop.sh` L204-L284 的 `refresh_backlog` 内联 Python 提取。

```python
#!/usr/bin/env python3
"""loop/lib/backlog.py — backlog.md 全量生成"""
import json
import os
import re
import sys


def read_body(filepath: str) -> str:
    if not os.path.exists(filepath):
        return ""
    with open(filepath) as f:
        content = f.read()
    match = re.match(r"^---\s*\n.*?\n---\s*\n", content, re.DOTALL)
    if match:
        return content[match.end():].strip()
    return content.strip()


def refresh_backlog(state_file: str, backlog_file: str):
    """从 state 文件全量重新生成 backlog.md"""
    if not os.path.exists(state_file):
        return

    with open(state_file) as f:
        state = json.load(f)

    sections = {"done": [], "in-progress": [], "pending": [], "blocked": []}
    for item in state.get("queue", []):
        status = item.get("status", "pending")
        if status == "done":
            sections["done"].append(item)
        elif status == "in_progress" or status == "in-progress":
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
            desc_path = item.get("desc_path")
            if desc_path and os.path.exists(desc_path):
                body = read_body(desc_path)
                if body:
                    for bline in body.split("\n"):
                        lines.append(bline)
            lines.append("")

    with open(backlog_file, "w") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("用法: backlog.py refresh <state_file> <backlog_file>", file=sys.stderr)
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "refresh":
        refresh_backlog(sys.argv[2], sys.argv[3])
    else:
        print(f"未知命令: {cmd}", file=sys.stderr)
        sys.exit(1)
```

**Step 4: 运行测试确认通过**

```bash
cd loop/lib && python3 -m pytest test_backlog.py -v
```
Expected: 1 passed

**Step 5: Commit**

```bash
git add loop/lib/backlog.py loop/lib/test_backlog.py
git commit -m "feat(loop): backlog.py — backlog.md 生成模块，从内联代码提取"
```

---

### Task 12: dev-loop.sh 最终集成 — 替换剩余内联代码 + 日志升级

**Files:**
- Modify: `loop/dev-loop.sh`

**Step 1: 替换 process_inbox 函数**

```bash
process_inbox() {
  # 检查 STOP 信号
  result=$(python3 "${MAIN_REPO_DIR}/loop/lib/inbox.py" check-stop "$INBOX_DIR")
  if [ "$result" = "STOP" ]; then
    echo "STOP"
    return
  fi
  # 处理 inbox 文件
  added=$(python3 "${MAIN_REPO_DIR}/loop/lib/inbox.py" process "$INBOX_DIR" "$TASKS_DIR" "$STATE_FILE")
  if [ "$added" -gt 0 ] 2>/dev/null; then
    log "📥 新增 $added 个任务"
    # 刷新 backlog
    python3 "${MAIN_REPO_DIR}/loop/lib/backlog.py" refresh "$STATE_FILE" "$BACKLOG_FILE"
    log "📝 backlog.md 已重新生成"
    # commit
    if [ -n "$(git -C "$MAIN_REPO_DIR" status --porcelain loop/tasks/ loop/backlog.md 2>/dev/null)" ]; then
      git -C "$MAIN_REPO_DIR" add loop/tasks/ loop/backlog.md
      git -C "$MAIN_REPO_DIR" commit -m "docs: inbox 新任务入队"
    fi
  fi
}
```

**Step 2: 替换 refresh_backlog 函数**

```bash
refresh_backlog() {
  python3 "${MAIN_REPO_DIR}/loop/lib/backlog.py" refresh "$STATE_FILE" "$BACKLOG_FILE"
}
```

**Step 3: 替换 mark_task_done 中的内联 Python**

`mark_task_done` 是 L979-L1102 的大块 Python（收集 git log、更新 frontmatter、追加完成总结）。由于它涉及 git 操作和文件修改，提取到 `state.py` 中新增 `mark_done` 子命令：

在 `state.py` 中新增函数 `mark_task_done(state_file, task_id, worker_state, main_repo, trees_dir)`，逻辑与现有内联代码完全一致。

bash 侧改为：
```bash
mark_task_done() {
  local task_id=$1 worker_state="${2:-}"
  python3 "${MAIN_REPO_DIR}/loop/lib/state.py" mark-done "$STATE_FILE" "$task_id" \
    "$worker_state" "$MAIN_REPO_DIR" "$WORKTREE_BASE_DIR"
}
```

**Step 4: 删除不再使用的 bash 变量和函数**

删除：
- `COMMON_RULES` 变量 (L432-L456)
- `COMMON_STATE_INSTRUCTIONS` 变量 (L458-L464)
- `COMMON_CHECKPOINT` 变量 (L466-L471)
- `locked_state_op` 函数（锁逻辑已在 state.py 中）
- `lock_state` / `unlock_state` 函数

**Step 5: 验证全部测试通过**

```bash
cd loop/lib && python3 -m pytest test_*.py -v
```
Expected: 全部通过（约 23 个测试）

**Step 6: 手动端到端验证**

```bash
# 查看状态
./loop/status.sh

# 确认配置加载
python3 loop/lib/config.py export-shell loop/workflow.yaml

# 确认 prompt 渲染（需要有 state 文件和模板文件）
python3 loop/lib/config.py render-prompt FAST_TRACK loop/loop-state.json loop/workflow.yaml "$(pwd)"
```

**Step 7: Commit**

```bash
git add loop/dev-loop.sh loop/lib/state.py
git commit -m "refactor(loop): 最终集成 — 消除全部内联 Python，接入 inbox/backlog/status 模块"
```

---

## 完成验收清单

- [ ] `loop/lib/` 目录包含 7 个 Python 模块 + 5 个测试文件
- [ ] `loop/workflow.yaml` 配置文件可用，修改后下一轮自动生效
- [ ] `loop/prompts/` 包含 12 个 prompt 模板 + 1 个 common rules
- [ ] `loop/status.sh` 可以一键查看运行状态
- [ ] dev-loop.sh 中不再有 `python3 -c` 或 `python3 <<PYEOF` 内联代码
- [ ] Stall 检测在并行模式中每轮执行
- [ ] 指数退避替代固定 30s 等待
- [ ] 失败时 worktree 保留复用
- [ ] 取消信号 `inbox/CANCEL-{id}` 可终止运行中的 worker
- [ ] 全部 Python 测试通过：`cd loop/lib && python3 -m pytest test_*.py -v`
