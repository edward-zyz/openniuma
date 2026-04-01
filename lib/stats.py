"""运行数据采集、查询与轮转模块。

基于 JsonFileStore 实现 session 记录、task 汇总、
超量自动归档等统计功能。
"""

import json
import os
import sys
import time
from datetime import datetime, timezone

try:
    from .json_store import JsonFileStore
except ImportError:
    from json_store import JsonFileStore


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_shape(data: dict, max_sessions: int) -> dict:
    """确保 data 包含必要字段。"""
    data.setdefault("sessions", [])
    data.setdefault("tasks", [])
    data.setdefault("max_sessions", max_sessions)
    return data


class StatsStore:
    """运行统计存储，支持 session 记录、task 汇总、自动轮转。"""

    def __init__(self, path: str, max_sessions: int = 500) -> None:
        self.store = JsonFileStore(path)
        self.max_sessions = max_sessions
        # 初始化文件结构（如文件不存在或为空）
        self.store.update(lambda d: _ensure_shape(d, max_sessions))

    # ── 记录 session ──────────────────────────────────────

    def record_session(self, session_data: dict) -> dict:
        """添加一条 session 记录，返回完整 session 对象。

        session_data 应包含 task_id, task_name, phase,
        duration_sec, exit_code, attempt 等字段。
        自动添加 session_id 和 recorded_at。
        """
        ts = int(time.time())
        session = {
            "session_id": f"s-{ts}",
            "recorded_at": _now_iso(),
            **session_data,
        }

        def _append(data: dict) -> dict:
            data = _ensure_shape(data, self.max_sessions)
            data["sessions"].append(session)
            return data

        self.store.update(_append)
        self._maybe_rotate()
        return session

    # ── 汇总 task ─────────────────────────────────────────

    def finalize_task(self, task_id: str) -> dict:
        """从 sessions 中汇总 task_id 对应的统计数据，写入 tasks 列表。

        返回汇总后的 task 记录。
        """
        result = {}

        def _finalize(data: dict) -> dict:
            data = _ensure_shape(data, self.max_sessions)
            matched = [s for s in data["sessions"] if s.get("task_id") == task_id]
            total_duration = sum(s.get("duration_sec", 0) for s in matched)
            task_record = {
                "task_id": task_id,
                "total_sessions": len(matched),
                "total_duration_sec": total_duration,
                "finalized_at": _now_iso(),
            }
            # 取第一条的 task_name 作为名称
            if matched:
                task_record["task_name"] = matched[0].get("task_name", "")
            data["tasks"].append(task_record)
            result.update(task_record)
            return data

        self.store.update(_finalize)
        return result

    # ── 查询 ──────────────────────────────────────────────

    def summary(self, task_id: str | None = None, state_path: str | None = None) -> dict:
        """返回统计摘要。

        如果指定 task_id，只统计该 task 的 sessions；
        否则统计全部。state_path 用于获取当前并行任务数。
        """
        data = self.get_all()
        sessions = data.get("sessions", [])
        tasks = data.get("tasks", [])

        if task_id is not None:
            sessions = [s for s in sessions if s.get("task_id") == task_id]
            tasks = [t for t in tasks if t.get("task_id") == task_id]

        total_duration = sum(s.get("duration_sec", 0) for s in sessions)
        succeeded = sum(1 for s in sessions if s.get("exit_code") == 0)
        failed = sum(1 for s in sessions if s.get("exit_code", 0) != 0)
        unique_tasks = len({s.get("task_id") for s in sessions})
        avg_duration = total_duration / len(sessions) if sessions else 0

        # 当前并行数：从 state.json 读取 in_progress 任务数
        active_tasks = self._count_active_tasks(state_path) if state_path else 0

        return {
            "total_sessions": len(sessions),
            "total_duration_sec": total_duration,
            "total_tasks": len(tasks),
            "unique_tasks": unique_tasks,
            "succeeded": succeeded,
            "failed": failed,
            "avg_duration_sec": round(avg_duration, 1),
            "active_tasks": active_tasks,
        }

    @staticmethod
    def _count_active_tasks(state_path: str) -> int:
        """从 state.json 读取当前 in_progress 任务数。"""
        try:
            state_store = JsonFileStore(state_path)
            state_data = state_store.read()
            queue = state_data.get("queue", [])
            return sum(1 for t in queue if t.get("status") == "in_progress")
        except Exception:
            return 0

    def get_all(self) -> dict:
        """返回完整数据。"""
        data = self.store.read()
        return _ensure_shape(data, self.max_sessions)

    # ── 轮转 ──────────────────────────────────────────────

    def _maybe_rotate(self) -> None:
        """sessions 超过 max_sessions 时，将最旧的一半归档。"""
        data = self.store.read()
        data = _ensure_shape(data, self.max_sessions)
        sessions = data["sessions"]

        if len(sessions) <= self.max_sessions:
            return

        # 归档最旧的一半
        half = len(sessions) // 2
        to_archive = sessions[:half]
        to_keep = sessions[half:]

        # 写归档文件
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        archive_dir = os.path.dirname(self.store.path)
        archive_name = f"stats-archive-{date_str}.json"
        archive_path = os.path.join(archive_dir, archive_name)
        archive_store = JsonFileStore(archive_path)

        def _append_archive(d: dict) -> dict:
            d.setdefault("archived_sessions", [])
            d["archived_sessions"].extend(to_archive)
            return d

        archive_store.update(_append_archive)

        # 更新主文件，只保留较新的一半
        def _trim(d: dict) -> dict:
            d = _ensure_shape(d, self.max_sessions)
            d["sessions"] = to_keep
            return d

        self.store.update(_trim)


# ── CLI 入口 ──────────────────────────────────────────────


def main() -> None:
    """CLI 入口，支持 record-session / finalize-task / summary。"""
    if len(sys.argv) < 3:
        print("用法: stats.py <command> <stats_path> [args...]", file=sys.stderr)
        sys.exit(1)

    cmd = sys.argv[1]
    stats_path = sys.argv[2]

    if cmd == "record-session":
        store = StatsStore(stats_path)
        raw = sys.stdin.read()
        session_data = json.loads(raw)
        result = store.record_session(session_data)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif cmd == "finalize-task":
        if len(sys.argv) < 4:
            print("用法: stats.py finalize-task <path> <task_id>", file=sys.stderr)
            sys.exit(1)
        task_id = sys.argv[3]
        store = StatsStore(stats_path)
        result = store.finalize_task(task_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif cmd == "summary":
        task_id = None
        state_path = None
        fmt = "text"
        i = 3
        while i < len(sys.argv):
            if sys.argv[i] == "--task" and i + 1 < len(sys.argv):
                task_id = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--state" and i + 1 < len(sys.argv):
                state_path = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--format" and i + 1 < len(sys.argv):
                fmt = sys.argv[i + 1]
                i += 2
            else:
                i += 1

        store = StatsStore(stats_path)
        result = store.summary(task_id=task_id, state_path=state_path)

        if fmt == "json":
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            for k, v in result.items():
                print(f"{k}: {v}")
    else:
        print(f"未知命令: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
