#!/usr/bin/env python3
"""openNiuMa Dashboard TUI — 基于 Textual 的交互式监控面板。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 依赖自检
NIUMA_DIR = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, os.path.join(NIUMA_DIR, "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from deps import ensure_deps  # noqa: E402
ensure_deps()

from textual.app import App, ComposeResult  # noqa: E402
from textual.binding import Binding  # noqa: E402
from textual.containers import Horizontal, Vertical  # noqa: E402
from textual.widgets import Footer, Header, Static  # noqa: E402
from textual.worker import Worker, get_current_worker  # noqa: E402

from data import (  # noqa: E402
    log_file_path,
    read_log_tail,
    read_state,
    read_stats_for_task,
    read_worker_state,
    watch_paths,
)
from widgets.task_list import TaskListPanel, TaskSelected  # noqa: E402
from widgets.task_detail import TaskDetailPanel  # noqa: E402
from widgets.log_viewer import LogViewerPanel  # noqa: E402


# ── 状态汇总用于 Header ────────────────────────────────

STATUS_ICONS = {
    "pending": ("○", "yellow"),
    "in_progress": ("●", "cyan"),
    "done_in_dev": ("↺", "blue"),
    "released": ("★", "green"),
    "dropped": ("↧", "magenta"),
    "done": ("✓", "green"),
    "blocked": ("✗", "red"),
}


def _build_header_stats(queue: list[dict]) -> str:
    counts: dict[str, int] = {}
    for t in queue:
        s = t.get("status", "pending")
        counts[s] = counts.get(s, 0) + 1
    parts = []
    for status, (icon, _color) in STATUS_ICONS.items():
        n = counts.get(status, 0)
        parts.append(f"{icon} {n}")
    return "  ".join(parts)


def _build_progress(queue: list[dict]) -> str:
    total = len(queue)
    done = sum(1 for t in queue if t.get("status") in {"done", "done_in_dev", "released"})
    if total == 0:
        return "无任务"
    pct = done * 100 // total
    bar_len = 20
    filled = done * bar_len // total
    bar = "█" * filled + "░" * (bar_len - filled)
    return f"[{bar}] {pct}% ({done}/{total})"


# ── App ──────────────────────────────────────────────

class DashboardApp(App):
    """openNiuMa Dashboard TUI 主应用。"""

    TITLE = "openNiuMa Dashboard"
    CSS_PATH = "styles.tcss"

    BINDINGS = [
        Binding("q", "quit", "退出"),
        Binding("r", "refresh", "刷新"),
        Binding("l", "toggle_log", "日志"),
        Binding("slash", "filter", "筛选"),
    ]

    def __init__(self, niuma_dir: str) -> None:
        super().__init__()
        self.niuma_dir = niuma_dir
        self._selected_task: dict | None = None
        self._log_visible = True

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main"):
            yield TaskListPanel(id="task-list")
            with Vertical(id="right-panel"):
                yield TaskDetailPanel(id="task-detail")
                yield LogViewerPanel(id="log-viewer")
        yield Footer()

    def on_mount(self) -> None:
        """启动时加载数据并开始 watch。"""
        self._do_refresh()
        self._start_watcher()
        self._start_log_tailer()

    # ── 数据刷新 ────────────────────────────────────────

    def _do_refresh(self) -> None:
        """同步刷新所有面板数据。"""
        state = read_state(self.niuma_dir)
        queue = state.get("queue", [])

        # 读取所有 in_progress worker 的阶段
        worker_phases: dict[int, str] = {}
        for task in queue:
            if task.get("status") == "in_progress":
                tid = task["id"]
                ws = read_worker_state(self.niuma_dir, tid)
                if ws:
                    worker_phases[tid] = ws.get("current_phase", "?")

        # 刷新左栏
        task_list = self.query_one("#task-list", TaskListPanel)
        task_list.refresh_tasks(queue, worker_phases)

        # 刷新 Header 统计
        stats_text = _build_header_stats(queue)
        progress_text = _build_progress(queue)
        self.sub_title = f"{stats_text}    {progress_text}"

        # 如果有选中任务，刷新右栏
        if self._selected_task:
            self._refresh_detail(self._selected_task)

    def _refresh_detail(self, task: dict) -> None:
        """刷新右栏详情和日志。"""
        tid = task.get("id")
        worker_state = read_worker_state(self.niuma_dir, tid)
        stats = read_stats_for_task(self.niuma_dir, tid)

        detail = self.query_one("#task-detail", TaskDetailPanel)
        detail.update_detail(task, worker_state, stats)

        log_viewer = self.query_one("#log-viewer", LogViewerPanel)
        if log_viewer.current_task_id != tid:
            lines = read_log_tail(self.niuma_dir, tid)
            log_viewer.load_log(tid, lines)

    # ── 事件处理 ────────────────────────────────────────

    def on_task_selected(self, event: TaskSelected) -> None:
        """左栏任务选中。"""
        self._selected_task = event.task
        self._refresh_detail(event.task)

    def action_refresh(self) -> None:
        """手动刷新。"""
        self._do_refresh()

    def action_toggle_log(self) -> None:
        """切换日志面板显示/隐藏。"""
        log_viewer = self.query_one("#log-viewer", LogViewerPanel)
        detail = self.query_one("#task-detail", TaskDetailPanel)
        self._log_visible = not self._log_visible
        log_viewer.display = self._log_visible
        if self._log_visible:
            detail.remove_class("full")
        else:
            detail.add_class("full")

    def action_filter(self) -> None:
        """按状态筛选（V2 实现，当前为 placeholder）。"""
        pass

    # ── 文件 watch ──────────────────────────────────────

    def _start_watcher(self) -> None:
        """启动文件变更监听 worker。"""
        self.run_worker(self._watch_files, thread=True)

    def _watch_files(self) -> None:
        """在后台线程中 watch 文件变更。"""
        import watchfiles

        paths = watch_paths(self.niuma_dir)
        if not paths:
            return

        worker = get_current_worker()
        for _changes in watchfiles.watch(*paths, step=1000):
            if worker.is_cancelled:
                break
            self.call_from_thread(self._do_refresh)

    # ── 日志 tail ───────────────────────────────────────

    def _start_log_tailer(self) -> None:
        """启动日志 tail worker。"""
        self.run_worker(self._tail_log, thread=True)

    def _tail_log(self) -> None:
        """在后台线程中 tail 当前选中任务的日志文件。"""
        import time

        worker = get_current_worker()
        last_size: int = 0
        last_task_id: int | None = None

        while not worker.is_cancelled:
            time.sleep(0.5)

            task = self._selected_task
            if not task:
                continue

            tid = task.get("id")
            path = log_file_path(self.niuma_dir, tid)

            # 切换任务时重置
            if tid != last_task_id:
                last_task_id = tid
                last_size = 0
                lines = read_log_tail(self.niuma_dir, tid)
                log_viewer = self.query_one("#log-viewer", LogViewerPanel)
                self.call_from_thread(log_viewer.load_log, tid, lines)
                try:
                    last_size = os.path.getsize(path)
                except OSError:
                    last_size = 0
                continue

            # 检查文件增长
            try:
                cur_size = os.path.getsize(path)
            except OSError:
                continue

            if cur_size > last_size:
                try:
                    with open(path, "r", encoding="utf-8", errors="replace") as f:
                        f.seek(last_size)
                        new_lines = f.readlines()
                    last_size = cur_size
                    if new_lines:
                        log_viewer = self.query_one("#log-viewer", LogViewerPanel)
                        self.call_from_thread(log_viewer.append_lines, tid, new_lines)
                except OSError:
                    pass


# ── 入口 ──────────────────────────────────────────────

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="openNiuMa Dashboard TUI")
    parser.add_argument("--dir", default=NIUMA_DIR, help="openniuma 目录路径")
    args = parser.parse_args()

    app = DashboardApp(niuma_dir=args.dir)
    app.run()


if __name__ == "__main__":
    main()
