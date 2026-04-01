# SPDX-License-Identifier: MIT
"""基于 JsonFileStore 的循环状态管理。

提供任务队列的 CRUD、状态流转（pending → in_progress → done/blocked）、
依赖追踪、乐观锁版本控制等能力。
"""

import sys
import time
from datetime import datetime, timezone

from .json_store import JsonFileStore

# ── 辅助函数 ───────────────────────────────────────────

def _now_iso() -> str:
    """返回 UTC ISO 8601 时间字符串。"""
    return datetime.now(timezone.utc).isoformat()


_DEFAULT_STATE: dict = {
    "queue": [],
    "completed": [],
    "blocked": [],
    "dev_branch": None,
    "batch_branch": None,
    "batch_status": "active",
    "release_pr_number": None,
    "release_started_at": None,
    "current_phase": None,
}

_TERMINAL_TASK_STATUSES = {"done", "done_in_dev", "released", "dropped"}
_TERMINAL_WORKER_PHASES = {"AWAITING_HUMAN_REVIEW", "FINALIZE"}


def get_worker_worktree_slug(worker_state: dict) -> str | None:
    """从 worker state 提取 worktree slug（用于分支命名）。

    优先级：worktree_path 目录名 > desc_path 文件名 > task name。
    """
    import re

    # 1. 尝试从 worktree_path 提取
    wt_path = worker_state.get("worktree_path")
    if wt_path:
        dirname = wt_path.rstrip("/").rsplit("/", 1)[-1]
        # 去掉 "loop-" 前缀
        slug = re.sub(r"^loop-", "", dirname)
        return slug

    # 2. 尝试从当前任务的 desc_path 提取
    current_id = worker_state.get("current_item_id")
    if current_id is not None:
        for task in worker_state.get("queue", []):
            if task.get("id") == current_id:
                desc_path = task.get("desc_path")
                if desc_path:
                    filename = desc_path.rstrip("/").rsplit("/", 1)[-1]
                    # 去掉 .md 后缀
                    filename = re.sub(r"\.md$", "", filename)
                    # 格式: <id>-<slug>_<date>_<time>，提取 slug 部分
                    match = re.match(r"^\d+-(.+?)_\d{2}-\d{2}_\d{2}-\d{2}$", filename)
                    if match:
                        return match.group(1)
                # 3. 回退到 name
                name = task.get("name", "")
                if name:
                    # 替换中文标点和空格为连字符
                    slug = re.sub(r"[：:]+", "-", name)
                    slug = re.sub(r"\s+", "-", slug)
                    return slug

    return None


def is_worker_state_done(worker_state: dict, task_id: int) -> bool:
    """判断 worker state 是否已将指定任务推进到完成态。"""
    for task in worker_state.get("queue", []):
        if task.get("id") == task_id:
            return task.get("status") in _TERMINAL_TASK_STATUSES

    completed_ids = {item.get("id") for item in worker_state.get("completed", [])}
    if task_id in completed_ids:
        return True

    return worker_state.get("current_phase") in _TERMINAL_WORKER_PHASES


class LoopState:
    """基于 JsonFileStore 的任务状态管理器。"""

    def __init__(self, path: str) -> None:
        self._store = JsonFileStore(path)
        # 首次使用时初始化默认结构
        self._store.update(self._ensure_defaults)

    # ── 内部辅助 ───────────────────────────────────────

    @staticmethod
    def _ensure_defaults(data: dict) -> dict:
        """如果 data 为空或缺少关键字段，补充默认值。"""
        for key, default in _DEFAULT_STATE.items():
            if key not in data:
                if isinstance(default, list):
                    data[key] = list(default)  # 深拷贝列表
                else:
                    data[key] = default
        if not data.get("batch_branch") and data.get("dev_branch"):
            data["batch_branch"] = data["dev_branch"]
        for task in data.get("queue", []):
            if task.get("status") == "completed":
                task["status"] = "done_in_dev"
        return data

    # ── 读取 ───────────────────────────────────────────

    def get_all(self) -> dict:
        """读取完整状态。"""
        return self._store.read()

    def get_task(self, task_id: int) -> dict | None:
        """从 queue 中查找指定 id 的任务，不存在返回 None。"""
        data = self._store.read()
        for task in data.get("queue", []):
            if task.get("id") == task_id:
                return task
        return None

    # ── 写入 ───────────────────────────────────────────

    def add_task(
        self,
        name: str,
        complexity: str = "medium",
        desc_path: str | None = None,
        depends_on: list[int] | None = None,
    ) -> int:
        """添加任务到队列，返回自动分配的 ID。"""
        result: dict = {"id": 0}

        def _add(data: dict) -> dict:
            queue = data.get("queue", [])
            max_id = max((t.get("id", 0) for t in queue), default=0)
            new_id = max_id + 1
            task = {
                "id": new_id,
                "name": name,
                "complexity": complexity,
                "desc_path": desc_path,
                "depends_on": depends_on or [],
                "status": "pending",
                "block_reason": None,
                "completed_at": None,
                "created_at": _now_iso(),
                "_version": 1,
                "_updated_at": time.time(),
            }
            queue.append(task)
            data["queue"] = queue
            result["id"] = new_id
            return data

        self._store.update(_add)
        return result["id"]

    def update_task(self, task_id: int, updates: dict) -> None:
        """原子更新任务字段，自动递增 _version 和 _updated_at。"""

        def _update(data: dict) -> dict:
            for task in data.get("queue", []):
                if task.get("id") == task_id:
                    task.update(updates)
                    task["_version"] = task.get("_version", 1) + 1
                    task["_updated_at"] = time.time()
                    break
            return data

        self._store.update(_update)

    def claim_task(self, task_id: int) -> bool:
        """原子地将 pending 任务转为 in_progress，成功返回 True。"""
        result = {"ok": False}

        def _claim(data: dict) -> dict:
            for task in data.get("queue", []):
                if task.get("id") == task_id:
                    if task.get("status") != "pending":
                        return data
                    task["status"] = "in_progress"
                    task["_version"] = task.get("_version", 1) + 1
                    task["_updated_at"] = time.time()
                    result["ok"] = True
                    break
            return data

        self._store.update(_claim)
        return result["ok"]

    def complete_task(self, task_id: int) -> None:
        """将任务标记为 done_in_dev 并记录完成时间。"""

        def _complete(data: dict) -> dict:
            for task in data.get("queue", []):
                if task.get("id") == task_id:
                    task["status"] = "done_in_dev"
                    task["completed_at"] = _now_iso()
                    task["_version"] = task.get("_version", 1) + 1
                    task["_updated_at"] = time.time()
                    break
            return data

        self._store.update(_complete)

    def block_task(self, task_id: int, reason: str) -> None:
        """将任务标记为 blocked 并记录原因。"""

        def _block(data: dict) -> dict:
            for task in data.get("queue", []):
                if task.get("id") == task_id:
                    task["status"] = "blocked"
                    task["block_reason"] = reason
                    task["_version"] = task.get("_version", 1) + 1
                    task["_updated_at"] = time.time()
                    break
            return data

        self._store.update(_block)

    def cancel_task(self, task_id: int) -> bool:
        """将任务标记为 cancelled，仅对 pending/in_progress/blocked 任务有效。"""
        result = {"ok": False}

        def _cancel(data: dict) -> dict:
            for task in data.get("queue", []):
                if task.get("id") == task_id:
                    if task.get("status") in ("pending", "in_progress", "blocked"):
                        task["status"] = "cancelled"
                        task["_version"] = task.get("_version", 1) + 1
                        task["_updated_at"] = time.time()
                        result["ok"] = True
                    break
            return data

        self._store.update(_cancel)
        return result["ok"]

    def find_ready_tasks(self) -> list[dict]:
        """返回所有 pending 且依赖已全部满足的任务。"""
        data = self._store.read()
        queue = data.get("queue", [])
        ready_statuses = {"done", "done_in_dev", "released"}
        done_ids = {t["id"] for t in queue if t.get("status") in ready_statuses}

        ready = []
        for task in queue:
            if task.get("status") != "pending":
                continue
            deps = task.get("depends_on", [])
            if all(d in done_ids for d in deps):
                ready.append(task)
        return ready

    def reclaim_orphan(self, task_id: int, expected_version: int) -> bool:
        """只在版本号匹配时重置任务为 pending，返回是否成功。"""
        result = {"ok": False}

        def _reclaim(data: dict) -> dict:
            for task in data.get("queue", []):
                if task.get("id") == task_id:
                    if task.get("_version") != expected_version:
                        return data
                    task["status"] = "pending"
                    task["_version"] = task.get("_version", 1) + 1
                    task["_updated_at"] = time.time()
                    result["ok"] = True
                    break
            return data

        self._store.update(_reclaim)
        return result["ok"]

    def set_global(self, **kwargs) -> None:
        """更新全局字段（如 dev_branch, current_phase 等）。"""

        def _set(data: dict) -> dict:
            data.update(kwargs)
            return data

        self._store.update(_set)


# ── CLI 入口 ──────────────────────────────────────────

def _cli_main() -> None:
    import json as _json

    args = sys.argv[1:]
    if not args:
        print("用法: python -m openniuma.lib.state <command> [args]")
        print("  dump [state_path]")
        print("  ready [state_path]")
        print("  claim <state_path> <task_id>")
        print("  complete <state_path> <task_id>")
        print("  add <state_path> <name> [complexity] [desc_path]")
        print("  get-field <state_path> <field>")
        print("  set-field <state_path> <field> <value_json>")
        print("  find-ready-ids <state_path>")
        sys.exit(1)

    cmd = args[0]

    if cmd == "dump":
        path = args[1] if len(args) > 1 else "state.json"
        state = LoopState(path)
        print(_json.dumps(state.get_all(), ensure_ascii=False, indent=2))

    elif cmd == "ready":
        path = args[1] if len(args) > 1 else "state.json"
        state = LoopState(path)
        tasks = state.find_ready_tasks()
        print(_json.dumps(tasks, ensure_ascii=False, indent=2))

    elif cmd == "claim":
        if len(args) < 3:
            print("用法: claim <state_path> <task_id>")
            sys.exit(1)
        state = LoopState(args[1])
        ok = state.claim_task(int(args[2]))
        print("claimed" if ok else "failed")

    elif cmd == "complete":
        if len(args) < 3:
            print("用法: complete <state_path> <task_id>")
            sys.exit(1)
        state = LoopState(args[1])
        state.complete_task(int(args[2]))
        print("completed")

    elif cmd == "add":
        if len(args) < 3:
            print("用法: add <state_path> <name> [complexity] [desc_path]")
            sys.exit(1)
        path = args[1]
        name = args[2]
        complexity = args[3] if len(args) > 3 else "medium"
        desc_path = args[4] if len(args) > 4 else None
        state = LoopState(path)
        tid = state.add_task(name, complexity, desc_path)
        print(f"added task {tid}")

    elif cmd == "get-field":
        if len(args) < 3:
            print("用法: get-field <state_path> <field>")
            sys.exit(1)
        path = args[1]
        field = args[2]
        state = LoopState(path)
        data = state.get_all()
        val = data.get(field, "")
        if isinstance(val, (dict, list)):
            print(_json.dumps(val, ensure_ascii=False))
        else:
            print(val if val is not None else "")

    elif cmd == "set-field":
        if len(args) < 4:
            print("用法: set-field <state_path> <field> <value_json>")
            sys.exit(1)
        path = args[1]
        field = args[2]
        raw_value = args[3]
        # 尝试解析为 JSON（支持 dict/list/number/bool/null），失败则作为字符串
        try:
            value = _json.loads(raw_value)
        except (_json.JSONDecodeError, ValueError):
            value = raw_value
        state = LoopState(path)
        state.set_global(**{field: value})
        print("ok")

    elif cmd == "find-ready-ids":
        path = args[1] if len(args) > 1 else "state.json"
        state = LoopState(path)
        tasks = state.find_ready_tasks()
        for t in tasks:
            print(t["id"])

    else:
        print(f"未知命令: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    _cli_main()
