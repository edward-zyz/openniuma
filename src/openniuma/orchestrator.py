# SPDX-License-Identifier: MIT
"""dev-loop.sh 的 Python 薄壳。

Phase 0 阶段，Python 仅作为 dev-loop.sh 的启动器。
Phase 1.5 将逐步用 Python 替代 Bash 核心。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def find_devloop_script() -> Path:
    """定位 dev-loop.sh 脚本。

    查找顺序：
    1. 环境变量 OPENNIUMA_DEVLOOP
    2. 包内 scripts/dev-loop.sh
    3. 当前目录的 scripts/dev-loop.sh
    """
    env_path = os.environ.get("OPENNIUMA_DEVLOOP")
    if env_path:
        p = Path(env_path)
        if p.exists():
            return p

    # 包内 scripts/
    pkg_script = Path(__file__).parent / "scripts" / "dev-loop.sh"
    if pkg_script.exists():
        return pkg_script

    # 当前目录
    local = Path.cwd() / "scripts" / "dev-loop.sh"
    if local.exists():
        return local

    raise FileNotFoundError(
        "找不到 dev-loop.sh。"
        "设置环境变量 OPENNIUMA_DEVLOOP 指向脚本路径，"
        "或确认 openniuma 已正确安装。"
    )


def run_bash_engine(
    workers: int | None = None,
    model: str | None = None,
    single_task: int | None = None,
    verbose: bool = False,
) -> int:
    """启动 Bash 编排引擎。"""
    script = find_devloop_script()
    cmd = ["bash", str(script)]

    if workers is not None:
        cmd.append(f"--workers={workers}")
    if model is not None:
        cmd.append(f"--model={model}")
    if single_task is not None:
        cmd.append(f"--single-task={single_task}")
    if verbose:
        cmd.append("--verbose")

    env = os.environ.copy()
    core_dir = str(Path(__file__).parent / "core")
    env["OPENNIUMA_CORE_DIR"] = core_dir

    result = subprocess.run(cmd, env=env)
    return result.returncode
