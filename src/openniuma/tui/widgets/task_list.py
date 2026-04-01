# SPDX-License-Identifier: MIT
"""左栏任务列表 widget。"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.message import Message
from textual.widgets import ListView, ListItem, Static


STATUS_ICONS = {
    "pending": ("○", "yellow"),
    "in_progress": ("●", "cyan"),
    "done_in_dev": ("↺", "blue"),
    "released": ("★", "green"),
    "dropped": ("↧", "magenta"),
    "done": ("✓", "green"),
    "blocked": ("✗", "red"),
}


class TaskSelected(Message):
    """任务选中事件。"""

    def __init__(self, task: dict) -> None:
        super().__init__()
        self.task = task


class TaskListItem(ListItem):
    """单个任务列表项。"""

    def __init__(self, task_data: dict, phase: str | None = None) -> None:
        super().__init__()
        self.task_data = task_data
        self._phase = phase

    def compose(self) -> ComposeResult:
        status = self.task_data.get("status", "pending")
        icon, color = STATUS_ICONS.get(status, ("?", "white"))
        tid = self.task_data.get("id", "?")
        name = self.task_data.get("name", "未命名")

        phase_tag = ""
        if self._phase and status == "in_progress":
            phase_tag = f" {self._phase}"

        yield Static(
            f"[{color}]{icon}[/] [{tid}]{phase_tag} {name}",
            markup=True,
        )


class TaskListPanel(ListView):
    """任务列表面板，支持键盘导航和选中事件。"""

    BORDER_TITLE = "Tasks"

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, TaskListItem):
            self.post_message(TaskSelected(item.task_data))

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        item = event.item
        if isinstance(item, TaskListItem):
            self.post_message(TaskSelected(item.task_data))

    def refresh_tasks(self, tasks: list[dict], worker_phases: dict[int, str]) -> None:
        current_index = self.index or 0
        self.clear()
        for task in tasks:
            tid = task.get("id")
            phase = worker_phases.get(tid)
            self.append(TaskListItem(task, phase))
        if self.children and current_index < len(self.children):
            self.index = current_index
