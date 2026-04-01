# SPDX-License-Identifier: MIT
"""backlog.md 渲染辅助。"""

from __future__ import annotations

import re
from pathlib import Path


def _read_body(filepath: str) -> str:
    content = Path(filepath).read_text(encoding="utf-8")
    match = re.match(r"^---\s*\n.*?\n---\s*\n", content, re.DOTALL)
    if match:
        return content[match.end():].strip()
    return content.strip()


def render_backlog(state: dict) -> str:
    sections = {
        "in_progress": [],
        "pending": [],
        "done_in_dev": [],
        "released": [],
        "done": [],
        "blocked": [],
    }

    for item in state.get("queue", []):
        status = item.get("status", "pending")
        if status in sections:
            sections[status].append(item)
        else:
            sections["pending"].append(item)

    lines = [
        "# Product Backlog",
        "",
        "> 自动生成，请勿手工编辑。任务通过 inbox/ 目录添加。",
        "",
    ]

    section_titles = [
        ("in_progress", "进行中"),
        ("pending", "待开发"),
        ("done_in_dev", "已进 Dev"),
        ("released", "已发布"),
        ("done", "已完成"),
        ("blocked", "已阻塞"),
    ]

    for key, title in section_titles:
        items = sections[key]
        lines.append(f"## {title}")
        lines.append("")
        if not items:
            lines.append("（无）")
            lines.append("")
            continue
        for item in items:
            lines.append(f"- [{item.get('id', '?')}] {item.get('name', '未命名')}")
            desc_path = item.get("desc_path")
            if desc_path and Path(desc_path).exists():
                body = _read_body(desc_path)
                if body:
                    lines.append(f"  {body.splitlines()[0]}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
