"""status.py 单元测试"""

import json
import os
import tempfile
import unittest

from .state import LoopState
from .status import render_status


class TestRenderStatus(unittest.TestCase):

    def _make_state(self) -> tuple[str, LoopState]:
        """创建临时 state 文件并返回 (路径, LoopState)。"""
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.unlink(path)  # LoopState 会自己创建
        state = LoopState(path)
        self.addCleanup(lambda: os.unlink(path) if os.path.exists(path) else None)
        return path, state

    def test_empty_state_renders(self):
        """空 state 能渲染不报错。"""
        _, state = self._make_state()
        result = render_status(state, fmt="text")
        self.assertIsInstance(result, str)
        self.assertIn("openNiuMa", result)

    def test_with_tasks_renders(self):
        """有任务时包含任务名和状态。"""
        _, state = self._make_state()
        state.add_task("实现搜索功能", complexity="medium")
        state.add_task("修复登录 Bug", complexity="low")

        result = render_status(state, fmt="text")
        self.assertIn("实现搜索功能", result)
        self.assertIn("修复登录 Bug", result)
        self.assertIn("Pending", result)

    def test_json_format(self):
        """json 输出能被 json.loads 解析，含 'tasks' 键。"""
        _, state = self._make_state()
        state.add_task("测试任务", complexity="high")

        result = render_status(state, fmt="json")
        parsed = json.loads(result)
        self.assertIn("tasks", parsed)
        self.assertIn("summary", parsed)
        self.assertEqual(len(parsed["tasks"]), 1)
        self.assertEqual(parsed["summary"]["total"], 1)

    def test_text_shows_batch_metadata(self):
        """文本状态页应展示批次分支和批次状态。"""
        _, state = self._make_state()
        state.set_global(batch_branch="dev/backlog-batch-2026-03-31", batch_status="active")

        result = render_status(state, fmt="text")
        self.assertIn("批次", result)
        self.assertIn("dev/backlog-batch-2026-03-31", result)
        self.assertIn("active", result)

    def test_json_summary_tracks_done_in_dev_released_and_dropped(self):
        """JSON summary 应区分 done_in_dev / released / dropped。"""
        _, state = self._make_state()
        state.add_task("任务 A", complexity="medium")
        state.add_task("任务 B", complexity="medium")
        state.add_task("任务 C", complexity="medium")
        state.update_task(1, {"status": "done_in_dev"})
        state.update_task(2, {"status": "released"})
        state.update_task(3, {"status": "dropped"})

        result = render_status(state, fmt="json")
        parsed = json.loads(result)
        self.assertEqual(parsed["summary"]["done_in_dev"], 1)
        self.assertEqual(parsed["summary"]["released"], 1)
        self.assertEqual(parsed["summary"]["dropped"], 1)


if __name__ == "__main__":
    unittest.main()
