"""状态汇总渲染 — 供 status.sh / dashboard.sh / openniuma.sh status 调用"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

try:
    from .state import LoopState
except ImportError:
    from state import LoopState


# ── 状态符号映射 ───────────────────────────────────────

_STATUS_ICONS = {
    "pending": "○",
    "in_progress": "●",
    "done": "✓",
    "done_in_dev": "↺",
    "released": "★",
    "dropped": "↧",
    "blocked": "⊘",
    "cancelled": "✗",
}

_STATUS_LABELS = {
    "pending": "Pending",
    "in_progress": "Running",
    "done": "Done",
    "done_in_dev": "In Dev",
    "released": "Released",
    "dropped": "Dropped",
    "blocked": "Blocked",
    "cancelled": "Cancelled",
}

_ANSI_COLORS = {
    "pending": "\033[33m",      # 黄色
    "in_progress": "\033[36m",  # 青色
    "done": "\033[32m",         # 绿色
    "done_in_dev": "\033[34m",  # 蓝色
    "released": "\033[32m",     # 绿色
    "dropped": "\033[35m",      # 品红
    "blocked": "\033[31m",      # 红色
    "cancelled": "\033[35m",    # 紫色/品红
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
}

# phase 标准显示顺序
_PHASE_DISPLAY_ORDER = [
    "DESIGN", "FAST_TRACK", "DESIGN_IMPLEMENT",
    "VERIFY", "FIX", "MERGE_FIX", "MERGE", "RELEASE_PREP", "RELEASE", "CI_FIX", "FINALIZE",
]


# ── 时间工具 ──────────────────────────────────────────

def _fmt_duration(seconds: int) -> str:
    """将秒数格式化为 Xh Ym Zs。"""
    if seconds < 0:
        seconds = 0
    if seconds < 60:
        return f"{seconds}s"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"


def _parse_iso(ts: str) -> datetime | None:
    """解析 ISO 8601 字符串为 datetime，失败返回 None。"""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


# ── 数据加载辅助 ──────────────────────────────────────

def _load_phase_timings(stats_path: str) -> dict[int, list[dict]]:
    """从 stats.json 加载每个 task 的 session 记录，按 task_id 分组。"""
    if not stats_path or not os.path.exists(stats_path):
        return {}
    try:
        with open(stats_path) as f:
            data = json.load(f)
    except Exception:
        return {}

    result: dict[int, list[dict]] = {}
    for s in data.get("sessions", []):
        tid = s.get("task_id")
        if tid is None:
            continue
        if tid not in result:
            result[tid] = []
        result[tid].append(s)
    return result


def _load_worker_phases(workers_dir: str) -> dict[int, str]:
    """从 workers/{id}/state.json 读取当前 phase。"""
    if not workers_dir or not os.path.isdir(workers_dir):
        return {}
    result: dict[int, str] = {}
    try:
        for entry in os.listdir(workers_dir):
            worker_state_path = os.path.join(workers_dir, entry, "state.json")
            if not os.path.isfile(worker_state_path):
                continue
            try:
                task_id = int(entry)
            except ValueError:
                continue
            try:
                with open(worker_state_path) as f:
                    ws = json.load(f)
                phase = ws.get("current_phase")
                if phase:
                    result[task_id] = phase
            except Exception:
                pass
    except Exception:
        pass
    return result


def _get_current_phase_elapsed(task_id: int, phase_timings: dict) -> int:
    """估算当前正在运行的 phase 已执行秒数（距最后一条 session 结束时间到现在）。"""
    sessions = phase_timings.get(task_id, [])
    if not sessions:
        return 0
    now = datetime.now(timezone.utc)
    last_ended = None
    for s in sessions:
        dt = _parse_iso(s.get("ended_at", ""))
        if dt and (last_ended is None or dt > last_ended):
            last_ended = dt
    if last_ended:
        return max(0, int((now - last_ended).total_seconds()))
    return 0


def _get_phase_rows(task_id: int, phase_timings: dict, worker_phases: dict, status: str) -> list[tuple[str, int, bool]]:
    """返回 [(phase, total_duration_sec, is_current)] 保持首次出现顺序。

    is_current 表示该 phase 正在执行（in_progress 任务的当前 phase）。
    对于尚无完成 session 的当前 phase，追加到列表末尾并标记 is_current=True。
    """
    sessions = phase_timings.get(task_id, [])
    current_phase = worker_phases.get(task_id) if status == "in_progress" else None

    phase_durations: dict[str, int] = {}
    phase_order: list[str] = []
    for s in sessions:
        phase = s.get("phase", "?")
        dur = int(s.get("duration_sec", 0))
        if phase not in phase_durations:
            phase_durations[phase] = 0
            phase_order.append(phase)
        phase_durations[phase] += dur

    # 若当前 phase 尚无完成 session，追加到列表（显示已运行时间）
    if current_phase and current_phase not in phase_durations:
        elapsed = _get_current_phase_elapsed(task_id, phase_timings)
        phase_order.append(current_phase)
        phase_durations[current_phase] = elapsed

    result = []
    for phase in phase_order:
        is_current = phase == current_phase
        result.append((phase, phase_durations[phase], is_current))
    return result


def _get_elapsed_sec(task_id: int, phase_timings: dict, task: dict) -> int:
    """返回任务从首次执行到现在的秒数（用于 in_progress 任务）。"""
    sessions = phase_timings.get(task_id, [])
    now = datetime.now(timezone.utc)

    if sessions:
        first_started = None
        for s in sessions:
            dt = _parse_iso(s.get("started_at", ""))
            if dt and (first_started is None or dt < first_started):
                first_started = dt
        if first_started:
            return int((now - first_started).total_seconds())

    # fallback：用 _updated_at（任务被 claim 的时间）
    updated = task.get("_updated_at", 0)
    if updated:
        return int(time.time() - updated)
    return 0


def _get_total_sec(task_id: int, phase_timings: dict) -> int:
    """返回任务所有 session 的总耗时（用于 done/cancelled 任务）。"""
    sessions = phase_timings.get(task_id, [])
    return sum(int(s.get("duration_sec", 0)) for s in sessions)


# ── 统计摘要 ──────────────────────────────────────────

def _build_summary(queue: list[dict]) -> dict:
    """从任务队列构建统计摘要。"""
    total = len(queue)
    by_status: dict[str, int] = {}
    for task in queue:
        s = task.get("status", "pending")
        by_status[s] = by_status.get(s, 0) + 1
    return {
        "total": total,
        "pending": by_status.get("pending", 0),
        "in_progress": by_status.get("in_progress", 0),
        "done": by_status.get("done", 0),
        "done_in_dev": by_status.get("done_in_dev", 0),
        "released": by_status.get("released", 0),
        "dropped": by_status.get("dropped", 0),
        "blocked": by_status.get("blocked", 0),
        "cancelled": by_status.get("cancelled", 0),
    }


# ── 渲染函数 ──────────────────────────────────────────

def render_status(
    state: LoopState,
    fmt: str = "text",
    stats_path: str = "",
    workers_dir: str = "",
) -> str:
    """根据格式渲染状态信息。

    fmt="text":      纯文本表格（任务列表 + 进度 + 统计）
    fmt="json":      JSON 输出 {"tasks": [...], "summary": {...}}
    fmt="dashboard": ANSI 彩色看板（含 phase 耗时详情）
    """
    data = state.get_all()
    queue = data.get("queue", [])

    if fmt == "json":
        return _render_json(queue)
    elif fmt == "dashboard":
        phase_timings = _load_phase_timings(stats_path)
        worker_phases = _load_worker_phases(workers_dir)
        return _render_dashboard(queue, data, phase_timings, worker_phases)
    else:
        phase_timings = _load_phase_timings(stats_path)
        worker_phases = _load_worker_phases(workers_dir)
        return _render_text(queue, data, phase_timings, worker_phases)


def _render_json(queue: list[dict]) -> str:
    """JSON 格式输出。"""
    summary = _build_summary(queue)
    return json.dumps({"tasks": queue, "summary": summary}, ensure_ascii=False, indent=2)


def _render_text(
    queue: list[dict],
    data: dict,
    phase_timings: dict,
    worker_phases: dict,
) -> str:
    """纯文本表格输出。"""
    lines: list[str] = []
    summary = _build_summary(queue)

    lines.append("=" * 70)
    lines.append("openNiuMa 状态概览")
    lines.append("=" * 70)

    branch = data.get("batch_branch") or data.get("dev_branch")
    batch_status = data.get("batch_status")
    phase = data.get("current_phase")
    if branch:
        lines.append(f"批次: {branch}")
    if batch_status:
        lines.append(f"批次状态: {batch_status}")
    if phase:
        lines.append(f"阶段: {phase}")
    lines.append("")

    parts = [
        f"总计: {summary['total']}",
        f"待处理: {summary['pending']}",
        f"进行中: {summary['in_progress']}",
        f"已进 Dev: {summary['done_in_dev']}",
        f"已发布: {summary['released']}",
    ]
    if summary["dropped"]:
        parts.append(f"已移除: {summary['dropped']}")
    if summary["done"]:
        parts.append(f"旧完成态: {summary['done']}")
    if summary["blocked"]:
        parts.append(f"阻塞: {summary['blocked']}")
    if summary["cancelled"]:
        parts.append(f"已取消: {summary['cancelled']}")
    lines.append("  ".join(parts))
    lines.append("-" * 70)

    if not queue:
        lines.append("（无任务）")
    else:
        lines.append(f"{'ID':<5} {'状态':<12} {'复杂度':<6} {'名称'}")
        lines.append("-" * 70)
        for task in queue:
            tid = task.get("id", "?")
            status = task.get("status", "pending")
            icon = _STATUS_ICONS.get(status, "?")
            label = _STATUS_LABELS.get(status, status)
            complexity = task.get("complexity", "-")
            name = task.get("name", "未命名")

            # 状态附加时间信息
            if status == "in_progress":
                elapsed = _get_elapsed_sec(tid, phase_timings, task)
                cur_phase = worker_phases.get(tid, "")
                time_str = f" {_fmt_duration(elapsed)}"
                if cur_phase:
                    time_str += f" [{cur_phase}]"
                label += time_str
            elif status in ("done", "done_in_dev", "released", "dropped", "cancelled"):
                total = _get_total_sec(tid, phase_timings)
                if total:
                    label += f" {_fmt_duration(total)}"

            lines.append(f"{tid:<5} {icon} {label:<22} {complexity:<6} {name}")

            # phase 明细（缩进显示）
            phase_rows = _get_phase_rows(tid, phase_timings, worker_phases, status)
            for ph_name, ph_dur, ph_current in phase_rows:
                suffix = " ←" if ph_current else ""
                lines.append(f"      {ph_name:<22} {_fmt_duration(ph_dur)}{suffix}")

    lines.append("=" * 70)
    return "\n".join(lines)


def _render_dashboard(
    queue: list[dict],
    data: dict,
    phase_timings: dict,
    worker_phases: dict,
) -> str:
    """ANSI 彩色看板，含 phase 耗时详情。"""
    c = _ANSI_COLORS
    lines: list[str] = []
    summary = _build_summary(queue)

    lines.append(f"{c['bold']}╔══════════════════════════════════════╗{c['reset']}")
    lines.append(f"{c['bold']}║     openNiuMa Dashboard              ║{c['reset']}")
    lines.append(f"{c['bold']}╚══════════════════════════════════════╝{c['reset']}")
    lines.append("")

    # 进度条（计 released + done_in_dev + done，不计 cancelled）
    total = summary["total"]
    done = summary["released"] + summary["done_in_dev"] + summary["done"]
    if total > 0:
        pct = done * 100 // total
        bar_len = 30
        filled = done * bar_len // total
        bar = "█" * filled + "░" * (bar_len - filled)
        lines.append(f"  进度: [{bar}] {pct}% ({done}/{total})")
    else:
        lines.append("  进度: [无任务]")
    lines.append("")

    # 状态统计行
    stat_parts = [
        f"{c['pending']}○ 待处理: {summary['pending']}{c['reset']}",
        f"{c['in_progress']}● 进行中: {summary['in_progress']}{c['reset']}",
        f"{c['done_in_dev']}↺ 已进 Dev: {summary['done_in_dev']}{c['reset']}",
        f"{c['released']}★ 已发布: {summary['released']}{c['reset']}",
    ]
    if summary["dropped"]:
        stat_parts.append(f"{c['dropped']}↧ 已移除: {summary['dropped']}{c['reset']}")
    if summary["done"]:
        stat_parts.append(f"{c['done']}✓ 旧完成态: {summary['done']}{c['reset']}")
    if summary["blocked"]:
        stat_parts.append(f"{c['blocked']}⊘ 阻塞: {summary['blocked']}{c['reset']}")
    if summary["cancelled"]:
        stat_parts.append(f"{c['cancelled']}✗ 已取消: {summary['cancelled']}{c['reset']}")
    lines.append("  " + "  ".join(stat_parts))
    lines.append("")

    # 任务列表（含 phase 明细）
    for task in queue:
        status = task.get("status", "pending")
        color = c.get(status, "")
        icon = _STATUS_ICONS.get(status, "?")
        label = _STATUS_LABELS.get(status, status)
        name = task.get("name", "未命名")
        tid = task.get("id", "?")
        complexity = task.get("complexity", "")
        complexity_str = f"[{complexity}] " if complexity else ""

        phase_rows = _get_phase_rows(tid, phase_timings, worker_phases, status)

        if status == "in_progress":
            elapsed = _get_elapsed_sec(tid, phase_timings, task)
            cur_phase = worker_phases.get(tid, "")
            phase_tag = f"  {c['dim']}│ {cur_phase}{c['reset']}" if cur_phase else ""
            lines.append(
                f"  {color}{icon} [{tid}] {name}{c['reset']}"
                f"  {c['bold']}{label}  {_fmt_duration(elapsed)}{c['reset']}{phase_tag}"
            )
            # phase 明细
            for ph_name, ph_dur, ph_current in phase_rows:
                if ph_current:
                    # 当前 phase：显示累计时间 + 标记
                    lines.append(
                        f"      {c['in_progress']}{ph_name:<22} {_fmt_duration(ph_dur)} ▶{c['reset']}"
                    )
                else:
                    lines.append(
                        f"      {c['dim']}{ph_name:<22} {_fmt_duration(ph_dur)} ✓{c['reset']}"
                    )

        elif status in ("done", "done_in_dev", "released", "dropped", "cancelled"):
            total_sec = _get_total_sec(tid, phase_timings)
            time_str = f"  {_fmt_duration(total_sec)}" if total_sec else ""
            lines.append(
                f"  {color}{icon} [{tid}] {name}{c['reset']}"
                f"  {color}{label}{time_str}{c['reset']}"
            )
            # phase 明细
            for ph_name, ph_dur, _ in phase_rows:
                lines.append(
                    f"      {c['dim']}{ph_name:<22} {_fmt_duration(ph_dur)}{c['reset']}"
                )

        else:
            # pending / blocked
            block_reason = task.get("block_reason", "")
            reason_str = f"  {c['dim']}{block_reason}{c['reset']}" if block_reason else ""
            lines.append(
                f"  {color}{icon} [{tid}] {complexity_str}{name}{c['reset']}{reason_str}"
            )

        lines.append("")  # 任务间空行

    return "\n".join(lines)


# ── CLI 入口 ──────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="openNiuMa 状态查看")
    parser.add_argument("--state", required=True, help="state.json 路径")
    parser.add_argument("--stats", default="", help="stats.json 路径（可选，用于 phase 耗时）")
    parser.add_argument("--workers", default="", help="workers 目录路径（可选，用于当前 phase）")
    parser.add_argument("--format", default="text", choices=["text", "json", "dashboard"],
                        help="输出格式")
    parser.add_argument("-w", "--watch", metavar="INTERVAL", nargs="?", const=5, type=int,
                        help="自动刷新间隔（秒，默认 5），仅 dashboard 格式有效")
    args = parser.parse_args()

    state = LoopState(args.state)

    if args.watch and args.format == "dashboard":
        try:
            while True:
                print("\033[2J\033[H", end="")
                print(render_status(state, fmt="dashboard",
                                    stats_path=args.stats, workers_dir=args.workers))
                print(f"\n  每 {args.watch}s 刷新 — Ctrl+C 退出")
                time.sleep(args.watch)
        except KeyboardInterrupt:
            pass
    else:
        print(render_status(state, fmt=args.format,
                            stats_path=args.stats, workers_dir=args.workers))


if __name__ == "__main__":
    main()
