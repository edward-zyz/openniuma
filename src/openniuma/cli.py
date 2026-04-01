# SPDX-License-Identifier: MIT
"""openNiuMa CLI 入口。"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import click

from openniuma import __version__


def _find_runtime_dir() -> Path:
    """查找 .openniuma-runtime 目录。"""
    return Path.cwd() / ".openniuma-runtime"


def _find_workflow_yaml() -> Path:
    """查找 workflow.yaml。"""
    return Path.cwd() / "workflow.yaml"


@click.group()
@click.version_option(version=__version__, prog_name="openNiuMa")
def main():
    """openNiuMa — AI 自治研发编排器

    Put tasks in. AI does design → implement → test → review → PR. You grab coffee.
    """


@main.command()
@click.option("--workers", "-w", default=None, type=int, help="最大并行 Worker 数")
@click.option("--model", "-m", default=None, help="覆盖所有 phase 的模型")
@click.option("--detach", "-d", is_flag=True, help="后台运行")
def start(workers: int | None, model: str | None, detach: bool):
    """启动编排器。"""
    from openniuma.orchestrator import find_devloop_script

    try:
        script = find_devloop_script()
    except FileNotFoundError as e:
        click.echo(f"错误: {e}", err=True)
        raise SystemExit(1)

    cmd = ["bash", str(script)]
    if workers is not None:
        cmd.append(f"--workers={workers}")
    if model is not None:
        cmd.append(f"--model={model}")

    # 设置 Python 包路径
    env = os.environ.copy()
    core_dir = str(Path(__file__).parent / "core")
    env["OPENNIUMA_CORE_DIR"] = core_dir

    if detach:
        log_dir = _find_runtime_dir() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "orchestrator.log"
        click.echo(f"openNiuMa v{__version__} 已在后台启动，日志: {log_file}")
        with open(log_file, "a") as lf:
            subprocess.Popen(cmd, stdout=lf, stderr=lf, start_new_session=True, env=env)
    else:
        click.echo(f"openNiuMa v{__version__} | 前台模式 | Ctrl+C = 优雅停机")
        click.echo("─" * 50)
        try:
            subprocess.run(cmd, check=True, env=env)
        except KeyboardInterrupt:
            click.echo("\n正在优雅停机...")
        except subprocess.CalledProcessError as e:
            raise SystemExit(e.returncode)


@main.command()
@click.option("--no-ai", is_flag=True, help="跳过 AI 生成 _common-rules.md")
@click.option("--dry-run", is_flag=True, help="预览配置，不写入文件")
def init(no_ai: bool, dry_run: bool):
    """初始化新项目。"""
    from openniuma.core.detect import detect

    click.echo("初始化 openNiuMa...")

    repo_dir = Path.cwd()
    runtime_dir = repo_dir / ".openniuma-runtime"

    # 探测技术栈
    click.echo("探测项目配置...")
    result = detect(str(repo_dir))
    click.echo(f"  技术栈: {result.get('stack', 'unknown')}")
    click.echo(f"  Gate: {result.get('gate_command', 'echo TODO')}")

    # 探测 main branch
    try:
        out = subprocess.run(
            ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
            capture_output=True, text=True, check=True,
        )
        main_branch = out.stdout.strip().replace("refs/remotes/origin/", "")
    except (subprocess.CalledProcessError, FileNotFoundError):
        main_branch = "main"
    click.echo(f"  主分支: {main_branch}")

    # 探测项目名
    try:
        out = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, check=True,
        )
        project_name = Path(out.stdout.strip().removesuffix(".git")).name
    except (subprocess.CalledProcessError, FileNotFoundError):
        project_name = repo_dir.name
    click.echo(f"  项目名: {project_name}")

    if dry_run:
        click.echo("\nDry-run 模式 — 预览配置，不写入文件")
        click.echo(f"  workflow.yaml: project.name={project_name}, main_branch={main_branch}")
        click.echo(f"  gate_command: {result.get('gate_command', '')}")
        return

    # 创建运行时目录
    for sub in ["inbox", "tasks", "logs", "reviews", "workers", "drafts"]:
        (runtime_dir / sub).mkdir(parents=True, exist_ok=True)
    os.chmod(str(runtime_dir), 0o700)

    # 生成 workflow.yaml
    workflow_path = repo_dir / "workflow.yaml"
    if not workflow_path.exists():
        from openniuma.core.config import generate_workflow_yaml

        yaml_content = generate_workflow_yaml(
            name=project_name,
            main_branch=main_branch,
            gate_command=result.get("gate_command", "echo 'TODO: configure'"),
            after_create=result.get("after_create", ""),
            before_remove=result.get("before_remove", ""),
            spec_dir=result.get("spec_dir", "docs/specs"),
            plan_dir=result.get("plan_dir", "docs/plans"),
        )
        workflow_path.write_text(yaml_content, encoding="utf-8")
        click.echo("  workflow.yaml 已生成")
    else:
        click.echo("  workflow.yaml 已存在，跳过")

    # 创建 prompts 目录 + _common-rules.md
    prompts_dir = repo_dir / ".openniuma" / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)

    common_rules = prompts_dir / "_common-rules.md"
    if not common_rules.exists():
        if not no_ai and shutil.which("claude"):
            click.echo("调用 Claude 分析项目规范...")
            try:
                out = subprocess.run(
                    [
                        "claude", "-p",
                        "分析当前项目的 CLAUDE.md 和 README，生成 _common-rules.md。"
                        "这个文件注入到 AI 编码 agent 的 prompt 中。"
                        "gate_command 用 {{gate_command}} 变量。只输出文件内容。",
                        "--output-format", "text",
                    ],
                    capture_output=True, text=True, timeout=120,
                )
                if out.returncode == 0 and out.stdout.strip():
                    common_rules.write_text(out.stdout, encoding="utf-8")
                    click.echo("  _common-rules.md 已由 AI 生成")
                else:
                    _copy_common_rules_template(common_rules)
            except (subprocess.TimeoutExpired, FileNotFoundError):
                _copy_common_rules_template(common_rules)
        else:
            _copy_common_rules_template(common_rules)
    else:
        click.echo("  _common-rules.md 已存在，跳过")

    # .gitignore
    gitignore = repo_dir / ".gitignore"
    patterns = [".openniuma-runtime/", ".trees/"]
    existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    added = []
    for pat in patterns:
        if pat not in existing:
            added.append(pat)
    if added:
        with open(gitignore, "a", encoding="utf-8") as f:
            f.write("\n" + "\n".join(added) + "\n")
        click.echo("  .gitignore 已更新")

    click.echo("\n初始化完成！")
    click.echo("  openniuma add '你的第一个任务' --complexity 低")
    click.echo("  openniuma start")


def _copy_common_rules_template(dest: Path):
    """从内置模板复制 _common-rules.md。"""
    from openniuma.prompts import read_prompt

    try:
        content = read_prompt("_common-rules.md.template")
        dest.write_text(content, encoding="utf-8")
        click.echo("  _common-rules.md 使用默认模板")
    except FileNotFoundError:
        dest.write_text(
            "# 项目规范\n\n请在此文件中定义项目的编码规范和约定。\n", encoding="utf-8"
        )
        click.echo("  _common-rules.md 使用空模板")


@main.command()
@click.argument("description")
@click.option(
    "--complexity", "-c", default="低",
    type=click.Choice(["低", "中", "高"]), help="任务复杂度",
)
def add(description: str, complexity: str):
    """快捷入队新任务。"""
    runtime_dir = _find_runtime_dir()
    state_file = runtime_dir / "state.json"

    if not state_file.exists():
        click.echo(
            "错误: 未找到 .openniuma-runtime/state.json，请先运行 openniuma init", err=True
        )
        raise SystemExit(1)

    from openniuma.core.inbox import generate_slug
    from openniuma.core.state import LoopState

    slug = generate_slug(description)
    tasks_dir = runtime_dir / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)

    task_file = tasks_dir / f"{slug}.md"
    task_file.write_text(
        f"---\nname: {description}\ncomplexity: {complexity}\n---\n", encoding="utf-8"
    )

    state = LoopState(str(state_file))
    tid = state.add_task(name=description, complexity=complexity, desc_path=str(task_file))
    click.echo(f"任务已入队: #{tid} [{complexity}] {description}")


@main.command()
@click.option(
    "--format", "fmt", default="text",
    type=click.Choice(["text", "json", "dashboard"]), help="输出格式",
)
def status(fmt: str):
    """查看任务状态。"""
    runtime_dir = _find_runtime_dir()
    state_file = runtime_dir / "state.json"

    if not state_file.exists():
        click.echo("错误: 未找到状态文件，请先运行 openniuma init", err=True)
        raise SystemExit(1)

    from openniuma.core.state import LoopState
    from openniuma.core.status import render_status

    state = LoopState(str(state_file))
    output = render_status(
        state=state,
        fmt=fmt,
        stats_path=str(runtime_dir / "stats.json"),
        workers_dir=str(runtime_dir / "workers"),
    )
    click.echo(output)


@main.command()
def dashboard():
    """终端实时看板（需要 textual）。"""
    from openniuma.tui.deps import check_tui_deps

    if not check_tui_deps():
        click.echo("TUI 依赖未安装。运行: pip install openniuma[tui]", err=True)
        raise SystemExit(1)

    from openniuma.tui.app import OpenNiuMaApp

    runtime_dir = _find_runtime_dir()
    app = OpenNiuMaApp(runtime_dir=str(runtime_dir))
    app.run()


@main.command()
def stop():
    """停止编排器（发送 STOP 信号）。"""
    runtime_dir = _find_runtime_dir()
    inbox = runtime_dir / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "STOP").touch()
    click.echo("STOP 信号已发送")


@main.command()
@click.argument("task_id", type=int)
def cancel(task_id: int):
    """取消指定任务。"""
    runtime_dir = _find_runtime_dir()
    inbox = runtime_dir / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / f"CANCEL-{task_id}").touch()
    click.echo(f"取消信号已发送: {task_id}")


@main.command()
@click.option("--fix", is_flag=True, help="自动修复可修复的问题")
def doctor(fix: bool):
    """环境诊断。"""
    issues = []
    optional_issues = []

    # Python 版本
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if sys.version_info >= (3, 10):
        click.echo(f"  Python {py_ver}")
    else:
        click.echo(f"  Python {py_ver} (需要 >= 3.10)")
        issues.append("Python 版本过低")

    # Git
    if shutil.which("git"):
        out = subprocess.run(["git", "--version"], capture_output=True, text=True)
        ver = out.stdout.strip().replace("git version ", "")
        click.echo(f"  Git {ver}")
    else:
        click.echo("  Git 未安装")
        issues.append("Git 未安装")

    # Claude CLI
    if shutil.which("claude"):
        click.echo("  claude CLI found")
    else:
        click.echo("  claude CLI 未找到")
        issues.append("claude CLI 未安装 (https://claude.ai/code)")

    # workflow.yaml
    wf = _find_workflow_yaml()
    if wf.exists():
        click.echo("  workflow.yaml found")
        try:
            from openniuma.core.config import load_config

            config = load_config(str(wf))
            gate = config.get("project", {}).get("gate_command", "")
            if gate:
                click.echo(f"  gate_command: {gate.split(chr(10))[0].strip()}")
            else:
                click.echo("  gate_command 未配置")
                issues.append("workflow.yaml 缺少 gate_command")
        except Exception as e:
            click.echo(f"  workflow.yaml 解析失败: {e}")
            issues.append("workflow.yaml 格式错误")
    else:
        click.echo("  workflow.yaml 未找到（运行 openniuma init 创建）")
        optional_issues.append("workflow.yaml 不存在")

    # 可选依赖
    try:
        import textual  # noqa: F401

        click.echo("  textual (TUI dashboard)")
    except ImportError:
        click.echo("  textual 未安装 (pip install openniuma[tui] for dashboard)")
        optional_issues.append("textual 未安装")

    # 汇总
    click.echo()
    total = len(issues) + len(optional_issues)
    if issues:
        click.echo(f"{len(issues)} 个必要问题需要修复。")
    if optional_issues:
        click.echo(f"{len(optional_issues)} 个可选问题。")
    if total == 0:
        click.echo("环境检查通过！")


if __name__ == "__main__":
    main()
