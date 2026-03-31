# Dashboard TUI Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将 openNiuMa dashboard 从简单 ANSI 打印升级为基于 Textual 的交互式 TUI，支持主从分栏、任务详情和实时日志。

**Architecture:** Textual App 作为主入口，左栏 ListView 展示任务列表，右栏上部 Static 展示选中任务的结构化详情，右栏下部 RichLog 展示实时日志 tail。数据层直接复用现有 `lib/state.py`、`lib/stats.py`、`lib/json_store.py`。通过 Textual Worker 异步 watch 文件变更并推送更新。

**Tech Stack:** Python 3.8+, Textual (TUI), watchfiles (文件监听)

---

### Task 1: 依赖自检与安装

**Files:**
- Create: `openniuma/tui/__init__.py`
- Create: `openniuma/tui/deps.py`
- Modify: `openniuma/init.sh:90-93`

**Step 1: 创建 `tui/` 包和依赖自检模块**

```bash
mkdir -p openniuma/tui/widgets
touch openniuma/tui/__init__.py
touch openniuma/tui/widgets/__init__.py
```

**Step 2: 编写 `tui/deps.py`**

```python
"""依赖自检，缺失时自动安装。"""

import subprocess
import sys


def ensure_deps() -> None:
    """检查 textual 和 watchfiles，缺失则 pip install。"""
    missing = []
    for pkg in ("textual", "watchfiles"):
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)

    if missing:
        print(f"正在安装依赖: {', '.join(missing)} ...")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--user", *missing],
            stdout=subprocess.DEVNULL,
        )
```

**Step 3: 在 `init.sh` 依赖检查段添加 textual 检测**

在 `init.sh` 第 92 行 PyYAML 检测之后添加：

```bash
python3 -c "import textual" 2>/dev/null && echo "  ✅ textual" || echo "  ⚠️ textual 未安装 (pip3 install textual watchfiles)"
```

**Step 4: Commit**

```bash
git add openniuma/tui/ openniuma/init.sh
git commit -m "feat(dashboard): 添加 TUI 依赖自检模块"
```

---

### Task 2: 数据读取层 — `tui/data.py`

**Files:**
- Create: `openniuma/tui/data.py`

**背景:** 所有 widget 需要一个统一的数据读取接口，封装对 state.json、worker state、stats.json、log 文件的读取。不是新数据层，只是把散落在多个模块的读取逻辑聚合为一组纯函数。

**Step 1: 编写 `tui/data.py`**

```python
"""TUI 数据读取层 — 聚合 state / worker / stats / log 的读取逻辑。"""

from __future__ import annotations

import os
from pathlib import Path

# 支持包内和直接运行两种 import 方式
try:
    from ..lib.json_store import JsonFileStore
    from ..lib.stats import StatsStore
except ImportError:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
    from json_store import JsonFileStore
    from stats import StatsStore


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
```

**Step 2: Commit**

```bash
git add openniuma/tui/data.py
git commit -m "feat(dashboard): TUI 数据读取层"
```

---

### Task 3: 左栏 — `widgets/task_list.py`

**Files:**
- Create: `openniuma/tui/widgets/task_list.py`

**Step 1: 编写 TaskList widget**

使用 Textual 的 `ListView` + `ListItem`，每个 item 展示状态图标+ID+阶段+名称。

```python
"""左栏任务列表 widget。"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.message import Message
from textual.widgets import ListView, ListItem, Static


STATUS_ICONS = {
    "pending": ("○", "yellow"),
    "in_progress": ("●", "cyan"),
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

    def __init__(self, task: dict, phase: str | None = None) -> None:
        super().__init__()
        self.task = task
        self._phase = phase

    def compose(self) -> ComposeResult:
        status = self.task.get("status", "pending")
        icon, color = STATUS_ICONS.get(status, ("?", "white"))
        tid = self.task.get("id", "?")
        name = self.task.get("name", "未命名")

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
        """选中某一行时发布 TaskSelected 消息。"""
        item = event.item
        if isinstance(item, TaskListItem):
            self.post_message(TaskSelected(item.task))

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        """高亮变化时也发布 TaskSelected，实现实时跟随。"""
        item = event.item
        if isinstance(item, TaskListItem):
            self.post_message(TaskSelected(item.task))

    def refresh_tasks(self, tasks: list[dict], worker_phases: dict[int, str]) -> None:
        """用新数据刷新列表。保持当前选中位置。"""
        current_index = self.index or 0
        self.clear()
        for task in tasks:
            tid = task.get("id")
            phase = worker_phases.get(tid)
            self.append(TaskListItem(task, phase))
        # 恢复选中位置
        if self.children and current_index < len(self.children):
            self.index = current_index
```

**Step 2: Commit**

```bash
git add openniuma/tui/widgets/task_list.py
git commit -m "feat(dashboard): 左栏 TaskList widget"
```

---

### Task 4: 右栏上部 — `widgets/task_detail.py`

**Files:**
- Create: `openniuma/tui/widgets/task_detail.py`

**Step 1: 编写 TaskDetail widget**

使用 Textual `Static` 渲染结构化的 Rich markup。

```python
"""右栏 Info 面板 — 展示选中任务的详细状态。"""

from __future__ import annotations

from textual.widgets import Static


def _format_duration(seconds: int) -> str:
    """秒数格式化为 Xh Ym Zs。"""
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m{secs:02d}s"
    hours, mins = divmod(minutes, 60)
    return f"{hours}h{mins:02d}m"


def _progress_bar(current: int, total: int, width: int = 20) -> str:
    """生成文本进度条。"""
    if total <= 0:
        return ""
    filled = current * width // total
    bar = "█" * filled + "░" * (width - filled)
    return f"[{bar}] {current}/{total}"


class TaskDetailPanel(Static):
    """任务详情面板。"""

    BORDER_TITLE = "Detail"

    def __init__(self) -> None:
        super().__init__("选择一个任务查看详情", markup=True)

    def update_detail(
        self,
        task: dict,
        worker_state: dict | None,
        stats: dict,
    ) -> None:
        """根据数据刷新面板内容。"""
        lines: list[str] = []
        status = task.get("status", "pending")
        name = task.get("name", "未命名")
        tid = task.get("id", "?")

        lines.append(f"[bold]\\[{tid}] {name}[/]")
        lines.append("")

        if status == "done":
            completed_at = task.get("completed_at", "")
            lines.append(f"[green]✓ 已完成[/]  {completed_at}")
        elif status == "blocked":
            reason = task.get("block_reason", "未知原因")
            lines.append(f"[red]✗ 阻塞:[/] {reason}")
        elif status == "pending":
            lines.append("[yellow]○ 待处理[/]")
        elif status == "in_progress" and worker_state:
            phase = worker_state.get("current_phase", "?")
            lines.append(f"[cyan]● 阶段:[/] {phase}")

            # 分支
            branch = worker_state.get("branch", "")
            if branch:
                lines.append(f"[cyan]  分支:[/] {branch}")

            # 实现进度（仅 DESIGN_IMPLEMENT 阶段）
            progress = worker_state.get("implement_progress", {})
            if phase == "DESIGN_IMPLEMENT" and progress:
                cur = progress.get("current_task", 0)
                total = progress.get("current_chunk", 0)
                if total > 0:
                    bar = _progress_bar(cur, total)
                    lines.append(f"[cyan]  进度:[/] {bar}")

            # 最近 commit
            last_task = progress.get("last_committed_task", "")
            last_sha = progress.get("last_commit_sha", "")
            if last_task:
                lines.append(f"[cyan]  最近 commit:[/] {last_sha[:7]} {last_task}")

            # 重试次数
            verify = worker_state.get("verify_attempts", 0)
            merge_fix = worker_state.get("merge_fix_attempts", 0)
            if verify > 0 or merge_fix > 0:
                lines.append(f"[cyan]  重试:[/] verify={verify}/3  merge_fix={merge_fix}/3")

        # 统计（来自 stats.json）
        total_dur = stats.get("total_duration_sec", 0)
        total_sess = stats.get("total_sessions", 0)
        if total_sess > 0:
            lines.append("")
            lines.append(f"[dim]累计:[/] {_format_duration(total_dur)}  ({total_sess} sessions)")

        last_fail = stats.get("last_failure")
        if last_fail:
            lines.append(f"[red]最近失败:[/] {last_fail}")

        self.update("\n".join(lines))
```

**Step 2: Commit**

```bash
git add openniuma/tui/widgets/task_detail.py
git commit -m "feat(dashboard): 右栏 TaskDetail widget"
```

---

### Task 5: 右栏下部 — `widgets/log_viewer.py`

**Files:**
- Create: `openniuma/tui/widgets/log_viewer.py`

**Step 1: 编写 LogViewer widget**

使用 Textual `RichLog` 展示日志内容，支持 tail 追加。

```python
"""右栏日志面板 — 展示选中任务的 worker 日志。"""

from __future__ import annotations

from textual.widgets import RichLog


class LogViewerPanel(RichLog):
    """日志查看面板，支持追加和刷新。"""

    BORDER_TITLE = "Log"

    def __init__(self) -> None:
        super().__init__(highlight=True, markup=True, wrap=True)
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
```

**Step 2: Commit**

```bash
git add openniuma/tui/widgets/log_viewer.py
git commit -m "feat(dashboard): 右栏 LogViewer widget"
```

---

### Task 6: 样式文件 — `tui/styles.tcss`

**Files:**
- Create: `openniuma/tui/styles.tcss`

**Step 1: 编写 Textual CSS**

```css
/* openNiuMa Dashboard TUI 样式 */

Screen {
    layout: grid;
    grid-size: 1;
    grid-rows: 3 1fr 3;
}

#header {
    dock: top;
    height: 3;
    background: $primary-background;
    color: $text;
    content-align: center middle;
    text-style: bold;
}

#footer {
    dock: bottom;
    height: 3;
    background: $primary-background;
    color: $text-muted;
    content-align: center middle;
}

#main {
    layout: horizontal;
}

#task-list {
    width: 30%;
    min-width: 25;
    border: solid $primary;
    padding: 0 1;
}

#right-panel {
    width: 70%;
    layout: vertical;
}

#task-detail {
    height: 50%;
    border: solid $secondary;
    padding: 1 2;
    overflow-y: auto;
}

#log-viewer {
    height: 50%;
    border: solid $accent;
    padding: 0 1;
}

/* 隐藏日志时详情占满 */
#task-detail.full {
    height: 100%;
}

TaskListItem {
    height: 1;
    padding: 0 1;
}

TaskListItem:hover {
    background: $primary 20%;
}
```

**Step 2: Commit**

```bash
git add openniuma/tui/styles.tcss
git commit -m "feat(dashboard): TUI 样式文件"
```

---

### Task 7: 主入口 — `tui/app.py`

**Files:**
- Create: `openniuma/tui/app.py`

**背景:** 这是核心文件，组装所有 widget，处理文件 watch、按键绑定、数据刷新。

**Step 1: 编写 `app.py`**

```python
#!/usr/bin/env python3
"""openNiuMa Dashboard TUI — 基于 Textual 的交互式监控面板。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 依赖自检
NIUMA_DIR = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, os.path.join(NIUMA_DIR, "lib"))

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
    done = sum(1 for t in queue if t.get("status") == "done")
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
```

**Step 2: Commit**

```bash
git add openniuma/tui/app.py
git commit -m "feat(dashboard): TUI 主入口 app.py"
```

---

### Task 8: 更新 `dashboard.sh` 入口

**Files:**
- Modify: `openniuma/dashboard.sh`

**Step 1: 改写 `dashboard.sh`，默认启动 TUI，保留 `--format` 兼容**

```bash
#!/usr/bin/env bash
set -euo pipefail
NIUMA_DIR="$(cd "$(dirname "$0")" && pwd)"

# 检测是否请求旧的文本/JSON格式
FORMAT=""
WATCH=false
INTERVAL=5
ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --format) FORMAT="$2"; shift 2 ;;
    -w) WATCH=true; shift
        if [[ $# -gt 0 && "$1" =~ ^[0-9]+$ ]]; then
          INTERVAL="$1"; shift
        fi ;;
    *) ARGS+=("$1"); shift ;;
  esac
done

# 非 TUI 格式走旧的 status.py
if [ -n "$FORMAT" ] && [ "$FORMAT" != "tui" ]; then
  if [ "$WATCH" = true ]; then
    while true; do
      clear
      python3 "$NIUMA_DIR/lib/status.py" --state "$NIUMA_DIR/state.json" --format "$FORMAT" "${ARGS[@]}"
      sleep "$INTERVAL"
    done
  else
    python3 "$NIUMA_DIR/lib/status.py" --state "$NIUMA_DIR/state.json" --format "$FORMAT" "${ARGS[@]}"
  fi
  exit 0
fi

# 默认启动 TUI
exec python3 "$NIUMA_DIR/tui/app.py" --dir "$NIUMA_DIR" "${ARGS[@]}"
```

**Step 2: Commit**

```bash
git add openniuma/dashboard.sh
git commit -m "feat(dashboard): dashboard.sh 默认启动 TUI，保留 --format 兼容"
```

---

### Task 9: 冒烟测试

**Files:** 无新文件

**Step 1: 验证依赖自检**

```bash
cd /Users/zhangyingze/Documents/AI/POI
python3 -c "import textual; print(textual.__version__)"
python3 -c "import watchfiles; print('ok')"
```

如果缺失会自动安装。

**Step 2: 验证 TUI 能启动**

```bash
cd /Users/zhangyingze/Documents/AI/POI
python3 openniuma/tui/app.py --dir openniuma
```

预期：TUI 启动，显示左栏 2 个任务（task 2 in_progress, task 3 done），右栏详情面板。按 `q` 退出。

**Step 3: 验证旧格式兼容**

```bash
bash openniuma/dashboard.sh --format text
bash openniuma/dashboard.sh --format json
```

预期：分别输出纯文本和 JSON 格式，与改造前一致。

**Step 4: Commit（如有修复）**

```bash
git add -A
git commit -m "fix(dashboard): TUI 冒烟测试修复"
```

---

### Task 10: init.sh 依赖检测 + 最终整理

**Files:**
- Modify: `openniuma/init.sh:90-93`

**Step 1: 在 init.sh 依赖检查段添加 textual 检测**

在 `python3 -c "import yaml"` 行之后添加：

```bash
python3 -c "import textual" 2>/dev/null && echo "  ✅ textual (dashboard TUI)" || echo "  ⚠️ textual 未安装 (pip3 install textual watchfiles)"
```

**Step 2: Commit**

```bash
git add openniuma/init.sh
git commit -m "chore(dashboard): init.sh 添加 textual 依赖检测"
```
