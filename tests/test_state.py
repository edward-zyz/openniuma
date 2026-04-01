# SPDX-License-Identifier: MIT
"""LoopState 单元测试。"""

import json
import os
import tempfile
import unittest

from openniuma.core.state import LoopState
from openniuma.core.state import is_worker_state_done


class TestLoopStateBasic(unittest.TestCase):
    """LoopState 基础功能测试，全部使用临时文件。"""

    def setUp(self) -> None:
        fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.unlink(self.path)  # 让 LoopState 自行创建

    def tearDown(self) -> None:
        for p in (self.path, self.path + ".lock"):
            try:
                os.unlink(p)
            except OSError:
                pass

    # ── 测试用例 ───────────────────────────────────────

    def test_init_empty_state(self) -> None:
        """get_all() 应包含 queue 列表。"""
        state = LoopState(self.path)
        data = state.get_all()
        self.assertIn("queue", data)
        self.assertIsInstance(data["queue"], list)
        self.assertIn("batch_branch", data)
        self.assertIn("batch_status", data)
        self.assertIn("release_pr_number", data)

    def test_init_migrates_null_batch_branch_from_dev_branch(self) -> None:
        """旧 state 中 batch_branch=null 时，应回填为 dev_branch。"""
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "queue": [],
                    "completed": [],
                    "blocked": [],
                    "dev_branch": "dev/backlog-batch-2026-03-28",
                    "batch_branch": None,
                },
                f,
                ensure_ascii=False,
            )

        state = LoopState(self.path)
        data = state.get_all()
        self.assertEqual(data["batch_branch"], "dev/backlog-batch-2026-03-28")

    def test_init_normalizes_completed_status_to_done_in_dev(self) -> None:
        """旧状态里的 completed 应迁移为 done_in_dev。"""
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "queue": [
                        {
                            "id": 14,
                            "name": "测试任务7：并行任务-B",
                            "status": "completed",
                            "depends_on": [],
                        }
                    ],
                    "completed": [{"id": 14, "name": "测试任务7：并行任务-B"}],
                    "blocked": [],
                },
                f,
                ensure_ascii=False,
            )

        state = LoopState(self.path)
        data = state.get_all()
        self.assertEqual(data["queue"][0]["status"], "done_in_dev")

    def test_worker_state_done_when_task_moved_to_completed(self) -> None:
        """worker queue 清空但 completed 已包含任务时，也应视为完成。"""
        worker_state = {
            "current_phase": "FAST_TRACK",
            "queue": [],
            "completed": [{"id": 9, "name": "测试任务2：更新 README 说明"}],
        }
        self.assertTrue(is_worker_state_done(worker_state, 9))

    def test_worker_state_not_done_for_in_progress_task(self) -> None:
        """仍在 queue 且 status=in_progress 的任务不应视为完成。"""
        worker_state = {
            "current_phase": "FAST_TRACK",
            "queue": [{"id": 9, "status": "in_progress"}],
            "completed": [],
        }
        self.assertFalse(is_worker_state_done(worker_state, 9))

    def test_add_task(self) -> None:
        """add_task 返回 id=1，get_task(1) 应有 name 和 status=pending。"""
        state = LoopState(self.path)
        tid = state.add_task("build-ui", "high", "/desc/ui.md")
        self.assertEqual(tid, 1)
        task = state.get_task(1)
        self.assertIsNotNone(task)
        self.assertEqual(task["name"], "build-ui")
        self.assertEqual(task["status"], "pending")

    def test_get_task_nonexistent_returns_none(self) -> None:
        """查询不存在的 task_id 应返回 None。"""
        state = LoopState(self.path)
        self.assertIsNone(state.get_task(999))

    def test_update_task_increments_version(self) -> None:
        """update_task 后 _version 应递增为 2。"""
        state = LoopState(self.path)
        state.add_task("refactor")
        state.update_task(1, {"complexity": "low"})
        task = state.get_task(1)
        self.assertEqual(task["_version"], 2)
        self.assertEqual(task["complexity"], "low")

    def test_claim_task(self) -> None:
        """claim_task 成功返回 True，status 变为 in_progress。"""
        state = LoopState(self.path)
        state.add_task("deploy")
        ok = state.claim_task(1)
        self.assertTrue(ok)
        task = state.get_task(1)
        self.assertEqual(task["status"], "in_progress")

    def test_claim_already_claimed_fails(self) -> None:
        """第二次 claim 同一任务应返回 False。"""
        state = LoopState(self.path)
        state.add_task("deploy")
        state.claim_task(1)
        ok = state.claim_task(1)
        self.assertFalse(ok)

    def test_complete_task(self) -> None:
        """complete_task 后 status=done_in_dev，completed_at 非空。"""
        state = LoopState(self.path)
        state.add_task("test")
        state.claim_task(1)
        state.complete_task(1)
        task = state.get_task(1)
        self.assertEqual(task["status"], "done_in_dev")
        self.assertIsNotNone(task["completed_at"])

    def test_find_ready_tasks(self) -> None:
        """t1(无依赖) 和 t3(无依赖) ready，t2(依赖 t1) 不 ready。"""
        state = LoopState(self.path)
        state.add_task("t1")                          # id=1, 无依赖
        state.add_task("t2", depends_on=[1])          # id=2, 依赖 t1
        state.add_task("t3")                          # id=3, 无依赖
        ready = state.find_ready_tasks()
        ready_ids = [t["id"] for t in ready]
        self.assertIn(1, ready_ids)
        self.assertIn(3, ready_ids)
        self.assertNotIn(2, ready_ids)

    def test_find_ready_after_dependency_done(self) -> None:
        """t1 进入 done_in_dev 后，t2 应变为 ready。"""
        state = LoopState(self.path)
        state.add_task("t1")                          # id=1
        state.add_task("t2", depends_on=[1])          # id=2
        # t1 完成前 t2 不 ready
        ready_ids = [t["id"] for t in state.find_ready_tasks()]
        self.assertNotIn(2, ready_ids)
        # 完成 t1
        state.claim_task(1)
        state.complete_task(1)
        # 现在 t2 应该 ready
        ready_ids = [t["id"] for t in state.find_ready_tasks()]
        self.assertIn(2, ready_ids)

    def test_find_ready_after_dependency_released(self) -> None:
        """released 任务也应满足依赖。"""
        state = LoopState(self.path)
        state.add_task("t1")
        state.add_task("t2", depends_on=[1])
        state.update_task(1, {"status": "released"})
        ready_ids = [t["id"] for t in state.find_ready_tasks()]
        self.assertIn(2, ready_ids)

    def test_version_prevents_stale_update(self) -> None:
        """两次更新后 version 应为 3，reclaim_orphan 用旧版本号应失败。"""
        state = LoopState(self.path)
        state.add_task("versioned")                   # _version=1
        state.update_task(1, {"note": "a"})           # _version=2
        state.update_task(1, {"note": "b"})           # _version=3
        task = state.get_task(1)
        self.assertEqual(task["_version"], 3)
        # 用过期版本号 reclaim 应失败
        ok = state.reclaim_orphan(1, expected_version=1)
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
