# SPDX-License-Identifier: MIT
"""内置 prompt 模板资源访问。"""

from importlib import resources
from pathlib import Path


def get_prompt_path(name: str) -> Path:
    """获取内置 prompt 模板的路径。"""
    return resources.files("openniuma.prompts").joinpath(name)  # type: ignore[return-value]


def read_prompt(name: str) -> str:
    """读取内置 prompt 模板内容。"""
    ref = resources.files("openniuma.prompts").joinpath(name)
    return ref.read_text(encoding="utf-8")


def list_prompts() -> list[str]:
    """列出所有内置 prompt 模板文件名。"""
    prompts_dir = resources.files("openniuma.prompts")
    return sorted(
        f.name
        for f in prompts_dir.iterdir()  # type: ignore[union-attr]
        if f.name.endswith(".md") and not f.name.startswith("__")
    )
