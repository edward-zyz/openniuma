"""跨平台兼容层 — 消除 macOS-only 代码依赖。

提供文件操作、进程管理、环境检测等跨平台工具函数，
macOS 优先使用原生高效实现，自动回退到纯 Python 方案。
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 平台检测
# ---------------------------------------------------------------------------

IS_MACOS: bool = platform.system() == "Darwin"
IS_LINUX: bool = platform.system() == "Linux"

# ---------------------------------------------------------------------------
# 文件操作
# ---------------------------------------------------------------------------


def copy_tree(src: str | Path, dst: str | Path) -> None:
    """递归复制目录树。

    macOS 优先使用 ``cp -Rc``（利用 APFS clone 加速），
    失败时回退到 ``shutil.copytree``。
    """
    src, dst = str(src), str(dst)

    if IS_MACOS:
        try:
            subprocess.run(
                ["cp", "-Rc", src, dst],
                check=True,
                capture_output=True,
            )
            return
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass  # 回退到纯 Python 实现

    shutil.copytree(src, dst, dirs_exist_ok=True)


def sed_inplace(filepath: str | Path, pattern: str, replacement: str) -> None:
    """纯 Python 的 sed -i 替换，不依赖外部 sed 命令。

    对文件内容执行正则替换并原地写回。
    """
    path = Path(filepath)
    text = path.read_text(encoding="utf-8")
    new_text = re.sub(pattern, replacement, text)
    path.write_text(new_text, encoding="utf-8")


# ---------------------------------------------------------------------------
# 进程管理
# ---------------------------------------------------------------------------


def run_with_timeout(
    cmd: list[str] | str,
    timeout_sec: int,
    **kwargs,
) -> subprocess.CompletedProcess:
    """带超时的子进程执行，超时抛出 ``subprocess.TimeoutExpired``。"""
    return subprocess.run(cmd, timeout=timeout_sec, **kwargs)


# ---------------------------------------------------------------------------
# 环境检测
# ---------------------------------------------------------------------------


def check_python_version(min_version: tuple[int, int] = (3, 9)) -> None:
    """检查 Python 版本是否满足最低要求，不满足则退出。"""
    current = sys.version_info[:2]
    if current < min_version:
        min_str = ".".join(str(v) for v in min_version)
        cur_str = ".".join(str(v) for v in current)
        print(
            f"错误: 需要 Python >= {min_str}，当前版本 {cur_str}",
            file=sys.stderr,
        )
        sys.exit(1)
    print(f"Python {'.'.join(str(v) for v in current)} ✓")


def check_yaml_available() -> bool:
    """检查 PyYAML 是否可用。"""
    try:
        import yaml  # noqa: F401

        return True
    except ImportError:
        return False


def install_yaml() -> bool:
    """尝试安装 PyYAML，返回是否成功。"""
    # 第一次尝试: --user 安装
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--user", "pyyaml"],
            check=True,
            capture_output=True,
            timeout=30,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass

    # 第二次尝试: --break-system-packages（某些 Linux 发行版需要）
    try:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--break-system-packages",
                "pyyaml",
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass

    return False


# ---------------------------------------------------------------------------
# 进程组 / 会话管理
# ---------------------------------------------------------------------------


def setsid_exec(argv: list[str]) -> None:
    """创建新进程会话后 exec 指定命令（跨平台 setsid 替代）。

    调用 os.setsid() 使当前进程成为新会话的 leader，脱离父进程组，
    然后用 os.execvp() 将自身替换为目标程序。
    """
    if not argv:
        print("用法: compat.py setsid <cmd> [args...]", file=sys.stderr)
        sys.exit(1)
    os.setsid()
    os.execvp(argv[0], argv)


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------


def _cli() -> None:
    """命令行入口，支持子命令。"""
    if len(sys.argv) < 2:
        print(
            "用法: compat.py <子命令> [参数...]\n"
            "子命令:\n"
            "  copy-tree <src> <dst>                  递归复制目录\n"
            "  sed-inplace <file> <pattern> <replace>  原地正则替换\n"
            "  install-yaml                            安装 PyYAML\n"
            "  check-python [min_major.min_minor]      检查 Python 版本\n"
            "  setsid <cmd> [args...]                  创建新会话后执行命令",
            file=sys.stderr,
        )
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "copy-tree":
        if len(sys.argv) != 4:
            print("用法: compat.py copy-tree <src> <dst>", file=sys.stderr)
            sys.exit(1)
        copy_tree(sys.argv[2], sys.argv[3])

    elif cmd == "sed-inplace":
        if len(sys.argv) != 5:
            print(
                "用法: compat.py sed-inplace <file> <pattern> <replacement>",
                file=sys.stderr,
            )
            sys.exit(1)
        sed_inplace(sys.argv[2], sys.argv[3], sys.argv[4])

    elif cmd == "install-yaml":
        ok = install_yaml()
        if not ok:
            print("PyYAML 安装失败", file=sys.stderr)
            sys.exit(1)
        print("PyYAML 安装成功 ✓")

    elif cmd == "check-python":
        if len(sys.argv) >= 3:
            parts = sys.argv[2].split(".")
            min_ver = (int(parts[0]), int(parts[1]))
            check_python_version(min_ver)
        else:
            check_python_version()

    elif cmd == "setsid":
        setsid_exec(sys.argv[2:])

    else:
        print(f"未知子命令: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    _cli()
