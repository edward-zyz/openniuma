"""Stall 检测、取消/恢复信号处理、孤儿任务回收。

提供纯函数式的 reconciliation 能力：
- detect_stalled_workers: 检测超时但进程仍活着的 worker
- detect_cancel_signals: 扫描 inbox 中的 CANCEL 信号
- detect_resume_signals: 扫描 inbox 中的 RESUME 信号
- reclaim_orphan_tasks: 回收进程已死的 in_progress 任务
"""

import os
import re
import signal
import sys
import time

try:
    from .state import LoopState
except ImportError:
    from state import LoopState


# ── 辅助函数 ───────────────────────────────────────────

def _pid_is_alive(pid: int) -> bool:
    """检查指定 pid 的进程是否仍在运行。"""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _read_pid_file(pid_path: str) -> int | None:
    """读取 pid 文件，返回 pid 整数，失败返回 None。"""
    try:
        with open(pid_path, "r") as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError):
        return None


# ── 核心函数 ───────────────────────────────────────────

def detect_stalled_workers(workers_dir: str, stall_timeout_sec: int) -> list[int]:
    """扫描 workers_dir，返回日志超时且进程仍活着的 task_id 列表。

    每个子目录名视为 task_id，检查 session.log 的 mtime。
    如果 now - mtime > stall_timeout_sec 且 pid 文件中的进程仍在运行，
    视为 stalled。
    """
    if not os.path.isdir(workers_dir):
        return []

    stalled: list[int] = []
    now = time.time()

    for entry in os.listdir(workers_dir):
        entry_path = os.path.join(workers_dir, entry)
        if not os.path.isdir(entry_path):
            continue

        try:
            task_id = int(entry)
        except ValueError:
            continue

        log_path = os.path.join(entry_path, "session.log")
        pid_path = os.path.join(entry_path, "pid")

        if not os.path.isfile(log_path):
            continue

        mtime = os.path.getmtime(log_path)
        if now - mtime <= stall_timeout_sec:
            continue

        # 日志已超时，检查进程是否还活着
        pid = _read_pid_file(pid_path)
        if pid is not None and _pid_is_alive(pid):
            stalled.append(task_id)

    return stalled


def detect_cancel_signals(inbox_dir: str) -> list[int]:
    """扫描 inbox_dir 下 CANCEL-{id} 文件，返回 task_id 列表并删除信号文件。"""
    if not os.path.isdir(inbox_dir):
        return []

    cancel_ids: list[int] = []
    pattern = re.compile(r"^CANCEL-(\d+)$")

    for entry in os.listdir(inbox_dir):
        m = pattern.match(entry)
        if m:
            task_id = int(m.group(1))
            cancel_ids.append(task_id)
            os.remove(os.path.join(inbox_dir, entry))

    return cancel_ids


def detect_resume_signals(inbox_dir: str) -> list[int]:
    """扫描 inbox_dir 下 RESUME-{id} 和 RESUME 文件，返回 task_id 列表。

    RESUME-{id} → 对应 task_id
    RESUME（无后缀）→ 返回 [-1]（-1 表示恢复全部）
    删除已处理的信号文件。
    """
    if not os.path.isdir(inbox_dir):
        return []

    resume_ids: list[int] = []
    pattern = re.compile(r"^RESUME-(\d+)$")

    for entry in os.listdir(inbox_dir):
        filepath = os.path.join(inbox_dir, entry)

        if entry == "RESUME":
            resume_ids.append(-1)
            os.remove(filepath)
            continue

        m = pattern.match(entry)
        if m:
            task_id = int(m.group(1))
            resume_ids.append(task_id)
            os.remove(filepath)

    return resume_ids


def reclaim_orphan_tasks(state: LoopState, workers_dir: str) -> None:
    """回收孤儿任务：in_progress 但进程已不存在的任务重置为 pending。

    先检查 worker state：如果 worker 已将任务推进到完成态，即使进程死了也不回收，
    而是由 sync_worker_result 处理。
    """
    import json as _json
    data = state.get_all()
    queue = data.get("queue", [])

    for task in queue:
        if task.get("status") != "in_progress":
            continue

        task_id = task.get("id")
        version = task.get("_version", 1)
        worker_dir = os.path.join(workers_dir, str(task_id))
        pid_path = os.path.join(worker_dir, "pid")

        pid = _read_pid_file(pid_path)

        if pid is None or not _pid_is_alive(pid):
            # 检查 worker state 是否已完成
            ws_path = os.path.join(worker_dir, "state.json")
            if os.path.isfile(ws_path):
                try:
                    with open(ws_path) as f:
                        ws = _json.load(f)
                    from state import is_worker_state_done
                    if is_worker_state_done(ws, task_id):
                        continue
                except Exception:
                    pass
            state.reclaim_orphan(task_id, version)


# ── CLI 入口 ──────────────────────────────────────────

def _cli_main() -> None:
    import json as _json

    args = sys.argv[1:]
    if not args or args[0] != "run":
        print("用法: python3 reconcile.py run <state_path> <workers_dir> <inbox_dir> [stall_timeout]")
        sys.exit(1)

    if len(args) < 4:
        print("用法: python3 reconcile.py run <state_path> <workers_dir> <inbox_dir> [stall_timeout]")
        sys.exit(1)

    state_path = args[1]
    workers_dir = args[2]
    inbox_dir = args[3]
    stall_timeout = int(args[4]) if len(args) > 4 else 1800

    state = LoopState(state_path)

    # 1) Stall 检测
    stalled = detect_stalled_workers(workers_dir, stall_timeout)
    if stalled:
        print(f"stalled workers: {stalled}")

    # 2) 取消信号：更新 state 并输出机器可读行供 shell 层 kill worker
    cancels = detect_cancel_signals(inbox_dir)
    for cancel_id in cancels:
        state.cancel_task(cancel_id)
        print(f"CANCEL:{cancel_id}")

    # 3) 恢复信号
    resumes = detect_resume_signals(inbox_dir)
    if resumes:
        print(f"resume signals: {resumes}")

    # 4) 孤儿回收
    reclaim_orphan_tasks(state, workers_dir)
    print("orphan reclaim done")

    # 输出当前状态摘要
    data = state.get_all()
    summary = {}
    for task in data.get("queue", []):
        s = task.get("status", "unknown")
        summary[s] = summary.get(s, 0) + 1
    print(f"state summary: {_json.dumps(summary)}")


if __name__ == "__main__":
    _cli_main()
