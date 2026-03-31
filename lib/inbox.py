"""inbox 扫描 + 任务入队 + slug 生成"""

import argparse
import hashlib
import os
import re
import sys

try:
    from .state import LoopState
except ImportError:
    from state import LoopState


# ── slug 生成 ─────────────────────────────────────────

def generate_slug(name: str) -> str:
    """中文名 -> 英文 slug。

    只保留 ASCII 字母数字和连字符，中文名则用 task-{hash[:8]}。
    """
    # 先尝试提取 ASCII 字母数字
    ascii_parts = re.findall(r"[a-zA-Z0-9]+", name)
    if ascii_parts:
        slug = "-".join(ascii_parts).lower()
        return slug[:64]  # 限制长度

    # 纯中文或无 ASCII 字符：使用哈希
    h = hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
    return f"task-{h}"


# ── frontmatter 解析 ──────────────────────────────────

def parse_frontmatter(content: str) -> tuple[dict, str]:
    """解析 --- 包围的 YAML frontmatter，返回 (metadata, body)。

    简单实现：只支持 key: value 单行格式和列表格式。
    """
    if not content.startswith("---"):
        return {}, content

    lines = content.split("\n")
    end_idx = -1
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break

    if end_idx == -1:
        return {}, content

    meta: dict = {}
    current_key = None
    current_list: list[str] | None = None

    for line in lines[1:end_idx]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # 检查是否是列表项（以 - 开头，属于上一个 key）
        if stripped.startswith("- ") and current_key and current_list is not None:
            current_list.append(stripped[2:].strip())
            meta[current_key] = current_list
            continue

        # key: value 格式
        if ":" in stripped:
            key, _, value = stripped.partition(":")
            key = key.strip()
            value = value.strip()
            if value:
                meta[key] = value
                current_key = key
                current_list = None
            else:
                # value 为空，可能下面跟列表
                current_key = key
                current_list = []
                meta[key] = current_list

    body = "\n".join(lines[end_idx + 1:]).strip()
    return meta, body


# ── inbox 扫描 ────────────────────────────────────────

def scan_inbox(inbox_dir: str) -> list[dict]:
    """扫描 *.md 文件，解析 frontmatter（name, complexity, depends_on），返回任务列表。"""
    tasks: list[dict] = []

    if not os.path.isdir(inbox_dir):
        return tasks

    for filename in sorted(os.listdir(inbox_dir)):
        if not filename.endswith(".md"):
            continue

        filepath = os.path.join(inbox_dir, filename)
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

        meta, body = parse_frontmatter(content)
        task = {
            "file": filename,
            "name": meta.get("name", filename.replace(".md", "")),
            "complexity": meta.get("complexity", "medium"),
            "depends_on": meta.get("depends_on", []),
            "body": body,
        }
        tasks.append(task)

    return tasks


def check_stop_signal(inbox_dir: str) -> bool:
    """检查 STOP 文件是否存在。"""
    return os.path.isfile(os.path.join(inbox_dir, "STOP"))


# ── CLI: add-task ─────────────────────────────────────

def _add_task_cli(args: argparse.Namespace) -> None:
    """CLI add-task 命令实现。"""
    description = args.description
    complexity = args.complexity or "medium"

    # 生成 slug 和任务文件
    slug = generate_slug(description)
    tasks_dir = args.tasks
    os.makedirs(tasks_dir, exist_ok=True)

    # 写入任务描述文件
    task_file = os.path.join(tasks_dir, f"{slug}.md")
    with open(task_file, "w", encoding="utf-8") as f:
        f.write(f"---\nname: {description}\ncomplexity: {complexity}\n---\n")

    # 添加到 state
    state = LoopState(args.state)
    tid = state.add_task(
        name=description,
        complexity=complexity,
        desc_path=task_file,
    )
    print(f"已入队: [{tid}] {description} (复杂度: {complexity})")
    print(f"任务文件: {task_file}")


# ── CLI 入口 ──────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="openNiuMa inbox 管理")
    sub = parser.add_subparsers(dest="command")

    add_parser = sub.add_parser("add-task", help="快捷入队任务")
    add_parser.add_argument("description", help="任务描述")
    add_parser.add_argument("--inbox", required=True, help="inbox 目录")
    add_parser.add_argument("--tasks", required=True, help="tasks 目录")
    add_parser.add_argument("--state", required=True, help="state.json 路径")
    add_parser.add_argument("--complexity", default="medium",
                            choices=["低", "中", "高", "low", "medium", "high"],
                            help="任务复杂度")

    args = parser.parse_args()

    if args.command == "add-task":
        _add_task_cli(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
