"""backlog.py 回归测试。"""

import tempfile
import unittest
from pathlib import Path

from .backlog import render_backlog


class TestRenderBacklog(unittest.TestCase):
    def test_render_backlog_groups_in_progress_and_done_in_dev(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            task_path = tasks_dir / "1-demo_03-31_13-00.md"
            task_path.write_text("---\nname: Demo\n---\n任务正文。\n", encoding="utf-8")

            state = {
                "queue": [
                    {
                        "id": 1,
                        "name": "进行中任务",
                        "status": "in_progress",
                        "desc_path": str(task_path),
                    },
                    {
                        "id": 2,
                        "name": "已进 Dev 任务",
                        "status": "done_in_dev",
                        "desc_path": str(task_path),
                    },
                ]
            }

            text = render_backlog(state)
            self.assertIn("## 进行中", text)
            self.assertIn("## 已进 Dev", text)
            self.assertIn("进行中任务", text)
            self.assertIn("已进 Dev 任务", text)


if __name__ == "__main__":
    unittest.main()
