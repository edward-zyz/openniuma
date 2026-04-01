# SPDX-License-Identifier: MIT
"""测试 prompt 资源访问。"""

from openniuma.prompts import get_prompt_path, list_prompts, read_prompt


def test_list_prompts_returns_all_templates():
    prompts = list_prompts()
    assert "fast-track.md" in prompts
    assert "design.md" in prompts
    assert "verify.md" in prompts


def test_read_prompt_fast_track():
    content = read_prompt("fast-track.md")
    assert len(content) > 100


def test_get_prompt_path_exists():
    path = get_prompt_path("design.md")
    assert str(path).endswith("design.md")
