# SPDX-License-Identifier: MIT
"""右栏日志面板 — 展示选中任务的 worker 日志。"""

from __future__ import annotations

from textual.widgets import RichLog


class LogViewerPanel(RichLog):
    """日志查看面板，支持追加和刷新。"""

    BORDER_TITLE = "Log"

    def __init__(self, **kwargs) -> None:
        super().__init__(highlight=True, markup=True, wrap=True, **kwargs)
        self._current_task_id: int | None = None
        self._line_count: int = 0

    def load_log(self, task_id: int, lines: list[str]) -> None:
        """加载指定任务的日志。"""
        self._current_task_id = task_id
        self._line_count = len(lines)
        self.clear()
        if not lines:
            self.write("[dim]无日志[/]")
            return
        for line in lines:
            self.write(line.rstrip())

    def append_lines(self, task_id: int, lines: list[str]) -> None:
        """追加新日志行（仅当 task_id 匹配当前展示的任务）。"""
        if task_id != self._current_task_id:
            return
        for line in lines:
            self.write(line.rstrip())
        self._line_count += len(lines)

    @property
    def current_task_id(self) -> int | None:
        return self._current_task_id
