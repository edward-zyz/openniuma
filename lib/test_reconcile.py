"""reconcile.py 单元测试。"""

import os
import tempfile
import time
import unittest

from .reconcile import (
    detect_cancel_signals,
    detect_resume_signals,
    detect_stalled_workers,
    reclaim_orphan_tasks,
)
from .state import LoopState


class TestStallDetection(unittest.TestCase):
    """detect_stalled_workers 测试。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.workers_dir = os.path.join(self.tmpdir, "workers")
        os.makedirs(self.workers_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _create_worker(self, task_id: int, log_age_sec: float = 0, pid: int | None = None):
        """创建模拟 worker 目录，包含 session.log 和可选 pid 文件。"""
        worker_dir = os.path.join(self.workers_dir, str(task_id))
        os.makedirs(worker_dir, exist_ok=True)

        log_path = os.path.join(worker_dir, "session.log")
        with open(log_path, "w") as f:
            f.write("log content")

        if log_age_sec > 0:
            old_time = time.time() - log_age_sec
            os.utime(log_path, (old_time, old_time))

        if pid is not None:
            pid_path = os.path.join(worker_dir, "pid")
            with open(pid_path, "w") as f:
                f.write(str(pid))

    def test_no_stall_within_timeout(self):
        """活跃 worker（日志刚更新），返回空列表。"""
        # 使用当前进程的 pid（保证 alive）
        self._create_worker(task_id=10, log_age_sec=0, pid=os.getpid())
        result = detect_stalled_workers(self.workers_dir, stall_timeout_sec=1800)
        self.assertEqual(result, [])

    def test_stall_detected_after_timeout(self):
        """日志 mtime 设为 2000 秒前，stall_timeout=1800，当前进程仍活着 → stalled。"""
        self._create_worker(task_id=20, log_age_sec=2000, pid=os.getpid())
        result = detect_stalled_workers(self.workers_dir, stall_timeout_sec=1800)
        self.assertEqual(result, [20])

    def test_empty_workers_dir(self):
        """空 workers_dir 返回空列表。"""
        result = detect_stalled_workers(self.workers_dir, stall_timeout_sec=1800)
        self.assertEqual(result, [])

    def test_nonexistent_workers_dir(self):
        """不存在的 workers_dir 返回空列表。"""
        result = detect_stalled_workers("/nonexistent/path", stall_timeout_sec=1800)
        self.assertEqual(result, [])


class TestCancelDetection(unittest.TestCase):
    """detect_cancel_signals 测试。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.inbox_dir = os.path.join(self.tmpdir, "inbox")
        os.makedirs(self.inbox_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_cancel_signal_detected(self):
        """创建 CANCEL-55 文件，返回 [55] 且文件被删除。"""
        signal_path = os.path.join(self.inbox_dir, "CANCEL-55")
        with open(signal_path, "w") as f:
            f.write("")

        result = detect_cancel_signals(self.inbox_dir)
        self.assertEqual(result, [55])
        self.assertFalse(os.path.exists(signal_path))

    def test_no_cancel_signal(self):
        """空目录返回空列表。"""
        result = detect_cancel_signals(self.inbox_dir)
        self.assertEqual(result, [])


class TestResumeSignal(unittest.TestCase):
    """detect_resume_signals 测试。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.inbox_dir = os.path.join(self.tmpdir, "inbox")
        os.makedirs(self.inbox_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_resume_specific_task(self):
        """创建 RESUME-55 文件，返回 [55] 且文件被删除。"""
        signal_path = os.path.join(self.inbox_dir, "RESUME-55")
        with open(signal_path, "w") as f:
            f.write("")

        result = detect_resume_signals(self.inbox_dir)
        self.assertEqual(result, [55])
        self.assertFalse(os.path.exists(signal_path))

    def test_resume_all(self):
        """创建 RESUME（无后缀）文件，返回 [-1] 且文件被删除。"""
        signal_path = os.path.join(self.inbox_dir, "RESUME")
        with open(signal_path, "w") as f:
            f.write("")

        result = detect_resume_signals(self.inbox_dir)
        self.assertEqual(result, [-1])
        self.assertFalse(os.path.exists(signal_path))


class TestOrphanReclaim(unittest.TestCase):
    """reclaim_orphan_tasks 测试。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.workers_dir = os.path.join(self.tmpdir, "workers")
        os.makedirs(self.workers_dir)
        self.state_path = os.path.join(self.tmpdir, "state.json")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_reclaim_orphan_with_no_pid_file(self):
        """in_progress 任务在 workers_dir 中无 pid 文件，应重置为 pending。"""
        state = LoopState(self.state_path)
        task_id = state.add_task("orphan-task", "medium")
        state.claim_task(task_id)

        # 确认任务为 in_progress
        task = state.get_task(task_id)
        self.assertEqual(task["status"], "in_progress")

        # workers_dir 中不创建 pid 文件 → 孤儿
        reclaim_orphan_tasks(state, self.workers_dir)

        # 任务应被重置为 pending
        task = state.get_task(task_id)
        self.assertEqual(task["status"], "pending")

    def test_no_reclaim_when_process_alive(self):
        """in_progress 任务且进程仍活着，不应被回收。"""
        state = LoopState(self.state_path)
        task_id = state.add_task("alive-task", "medium")
        state.claim_task(task_id)

        # 创建 pid 文件指向当前进程（活着的）
        worker_dir = os.path.join(self.workers_dir, str(task_id))
        os.makedirs(worker_dir)
        with open(os.path.join(worker_dir, "pid"), "w") as f:
            f.write(str(os.getpid()))

        reclaim_orphan_tasks(state, self.workers_dir)

        # 任务应保持 in_progress
        task = state.get_task(task_id)
        self.assertEqual(task["status"], "in_progress")


if __name__ == "__main__":
    unittest.main()
