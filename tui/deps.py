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
