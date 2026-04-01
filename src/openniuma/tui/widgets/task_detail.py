# SPDX-License-Identifier: MIT
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

    def __init__(self, **kwargs) -> None:
        super().__init__("选择一个任务查看详情", markup=True, **kwargs)

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

        if status == "released":
            completed_at = task.get("completed_at", "")
            lines.append(f"[green]★ 已发布[/]  {completed_at}")
        elif status == "done_in_dev":
            completed_at = task.get("completed_at", "")
            lines.append(f"[blue]↺ 已进 Dev[/]  {completed_at}")
        elif status == "dropped":
            completed_at = task.get("completed_at", "")
            lines.append(f"[magenta]↧ 已移除[/]  {completed_at}")
        elif status == "done":
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
            progress = worker_state.get("implement_progress") or {}
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
