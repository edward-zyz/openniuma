# SPDX-License-Identifier: MIT
"""StatsStore 单元测试。"""

import glob
import os
import shutil
import tempfile
import time
import unittest

from openniuma.core.stats import StatsStore


class TestStatsRecordSession(unittest.TestCase):
    """record_session 基本功能测试。"""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.path = os.path.join(self.tmpdir, "stats.json")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_record_first_session(self) -> None:
        """记录 1 条 session，sessions 长度应为 1。"""
        store = StatsStore(self.path)
        store.record_session({
            "task_id": "t-1",
            "task_name": "测试任务",
            "phase": "build",
            "duration_sec": 120,
            "exit_code": 0,
            "attempt": 1,
        })
        data = store.get_all()
        self.assertEqual(len(data["sessions"]), 1)
        session = data["sessions"][0]
        self.assertTrue(session["session_id"].startswith("s-"))
        self.assertIn("recorded_at", session)
        self.assertEqual(session["task_id"], "t-1")

    def test_record_multiple_sessions(self) -> None:
        """记录 3 条 session，sessions 长度应为 3。"""
        store = StatsStore(self.path)
        for i in range(3):
            store.record_session({
                "task_id": f"t-{i}",
                "task_name": f"任务{i}",
                "phase": "test",
                "duration_sec": 60,
                "exit_code": 0,
                "attempt": 1,
            })
            # 确保 session_id 不重复（基于 timestamp）
            time.sleep(0.01)
        data = store.get_all()
        self.assertEqual(len(data["sessions"]), 3)


class TestStatsRotation(unittest.TestCase):
    """轮转功能测试。"""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.path = os.path.join(self.tmpdir, "stats.json")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_rotation_triggered(self) -> None:
        """max_sessions=5，记录 10 条，sessions 应 <= 5，且有 archive 文件。"""
        store = StatsStore(self.path, max_sessions=5)
        for i in range(10):
            store.record_session({
                "task_id": f"t-{i}",
                "task_name": f"任务{i}",
                "phase": "build",
                "duration_sec": 30,
                "exit_code": 0,
                "attempt": 1,
            })
        data = store.get_all()
        self.assertLessEqual(len(data["sessions"]), 5)

        # 检查 archive 文件存在
        archives = glob.glob(os.path.join(self.tmpdir, "stats-archive-*.json"))
        self.assertGreater(len(archives), 0)


class TestStatsSummary(unittest.TestCase):
    """summary 查询测试。"""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.path = os.path.join(self.tmpdir, "stats.json")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_summary_empty(self) -> None:
        """空数据时 total_sessions 应为 0。"""
        store = StatsStore(self.path)
        result = store.summary()
        self.assertEqual(result["total_sessions"], 0)
        self.assertEqual(result["total_duration_sec"], 0)
        self.assertEqual(result["total_tasks"], 0)

    def test_summary_with_data(self) -> None:
        """2 条 session，total_sessions=2，total_duration_sec=300。"""
        store = StatsStore(self.path)
        store.record_session({
            "task_id": "t-1",
            "task_name": "任务A",
            "phase": "build",
            "duration_sec": 100,
            "exit_code": 0,
            "attempt": 1,
        })
        store.record_session({
            "task_id": "t-1",
            "task_name": "任务A",
            "phase": "test",
            "duration_sec": 200,
            "exit_code": 0,
            "attempt": 2,
        })
        result = store.summary()
        self.assertEqual(result["total_sessions"], 2)
        self.assertEqual(result["total_duration_sec"], 300)


if __name__ == "__main__":
    unittest.main()
