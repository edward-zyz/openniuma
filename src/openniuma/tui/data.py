# SPDX-License-Identifier: MIT
"""TUI 数据读取层 — 聚合 state / worker / stats / log 的读取逻辑。"""

from __future__ import annotations

import os

from openniuma.core.json_store import JsonFileStore
from openniuma.core.stats import StatsStore


def read_state(niuma_dir: str) -> dict:
    """读取主 state.json，返回完整 dict。"""
    store = JsonFileStore(os.path.join(niuma_dir, "state.json"))
    return store.read()


def read_worker_state(niuma_dir: str, task_id: int) -> dict | None:
    """读取 workers/<task_id>/state.json，不存在返回 None。"""
    path = os.path.join(niuma_dir, "workers", str(task_id), "state.json")
    if not os.path.exists(path):
        return None
    store = JsonFileStore(path)
    data = store.read()
    return data if data else None


def read_stats_for_task(niuma_dir: str, task_id: int) -> dict:
    """从 stats.json 聚合指定 task 的统计数据。

    返回 {"total_sessions": N, "total_duration_sec": N, "last_failure": str|None}
    """
    stats_path = os.path.join(niuma_dir, "stats.json")
    if not os.path.exists(stats_path):
        return {"total_sessions": 0, "total_duration_sec": 0, "last_failure": None}

    store = StatsStore(stats_path)
    data = store.get_all()
    sessions = [
        s for s in data.get("sessions", [])
        if s.get("task_id") == task_id
    ]
    total_dur = sum(s.get("duration_sec", 0) for s in sessions)

    # 最近一次失败
    last_failure = None
    for s in reversed(sessions):
        if s.get("exit_code", 0) != 0:
            last_failure = s.get("failure_type", "unknown")
            break

    return {
        "total_sessions": len(sessions),
        "total_duration_sec": total_dur,
        "last_failure": last_failure,
    }


def read_log_tail(niuma_dir: str, task_id: int, max_lines: int = 50) -> list[str]:
    """读取 worker 日志文件的最后 N 行。"""
    path = os.path.join(niuma_dir, "logs", f"worker-{task_id}.log")
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return lines[-max_lines:]
    except OSError:
        return []


def log_file_path(niuma_dir: str, task_id: int) -> str:
    """返回 worker 日志文件路径。"""
    return os.path.join(niuma_dir, "logs", f"worker-{task_id}.log")


def watch_paths(niuma_dir: str) -> list[str]:
    """返回需要 watch 的文件/目录路径列表。"""
    paths = [
        os.path.join(niuma_dir, "state.json"),
        os.path.join(niuma_dir, "stats.json"),
        os.path.join(niuma_dir, "workers"),
        os.path.join(niuma_dir, "logs"),
    ]
    return [p for p in paths if os.path.exists(p)]
