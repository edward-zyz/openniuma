# SPDX-License-Identifier: MIT
"""分支命名规范验证测试。

验证任务创建时分支命名符合规范：
- 格式: <type>/<slug>
- type: feat/fix/docs/chore/refactor
"""

import re
import unittest

from openniuma.core.state import get_worker_worktree_slug

# 合法的分支类型
VALID_BRANCH_TYPES = frozenset(["feat", "fix", "docs", "chore", "refactor"])

# 分支命名正则: <type>/<slug>
# slug: 小写字母、数字、连字符、下划线，长度 1-100
BRANCH_PATTERN = re.compile(r"^(feat|fix|docs|chore|refactor)/[\w-]{1,100}$")


def validate_branch_name(branch_name: str | None) -> tuple[bool, str]:
    """验证分支名称是否符合 <type>/<slug> 规范。

    Returns:
        (is_valid, reason)
    """
    if not branch_name:
        return False, "分支名为空"

    if not BRANCH_PATTERN.match(branch_name):
        # 提供更详细的错误信息
        if "/" not in branch_name:
            return False, f"分支名缺少类型分隔符 '/'，实际: {branch_name}"
        parts = branch_name.split("/", 1)
        if len(parts) == 2:
            branch_type, slug = parts
            if branch_type not in VALID_BRANCH_TYPES:
                return False, f"非法分支类型 '{branch_type}'，期望: {', '.join(sorted(VALID_BRANCH_TYPES))}"
            if not re.match(r"^[\w-]{1,100}$", slug):
                return False, f"非法 slug '{slug}'，slug 只允许字母、数字、连字符、下划线"
        return False, f"分支名格式不合法: {branch_name}"

    return True, "OK"


def build_branch_name(branch_type: str, worker_state: dict) -> str | None:
    """根据分支类型和 worker state 构建分支名。

    Returns:
        分支名 (如 "feat/branch-naming")，无法构建时返回 None
    """
    if branch_type not in VALID_BRANCH_TYPES:
        return None
    slug = get_worker_worktree_slug(worker_state)
    if not slug:
        return None
    return f"{branch_type}/{slug}"


class TestBranchNamingConvention(unittest.TestCase):
    """分支命名规范测试。"""

    # ── validate_branch_name ─────────────────────────────────

    def test_valid_feat_branch(self) -> None:
        valid, reason = validate_branch_name("feat/branch-naming")
        self.assertTrue(valid, reason)

    def test_valid_fix_branch(self) -> None:
        valid, reason = validate_branch_name("fix/ios-safari")
        self.assertTrue(valid, reason)

    def test_valid_docs_branch(self) -> None:
        valid, reason = validate_branch_name("docs/api-changes")
        self.assertTrue(valid, reason)

    def test_valid_chore_branch(self) -> None:
        valid, reason = validate_branch_name("chore/update-deps")
        self.assertTrue(valid, reason)

    def test_valid_refactor_branch(self) -> None:
        valid, reason = validate_branch_name("refactor/auth-module")
        self.assertTrue(valid, reason)

    def test_invalid_empty_branch(self) -> None:
        valid, reason = validate_branch_name("")
        self.assertFalse(valid)
        self.assertIn("空", reason)

    def test_invalid_none_branch(self) -> None:
        valid, reason = validate_branch_name(None)
        self.assertFalse(valid)
        self.assertIn("空", reason)

    def test_invalid_missing_slash(self) -> None:
        """没有 '/' 分隔符的分支名应被拒绝。"""
        valid, reason = validate_branch_name("feat_branch-name")
        self.assertFalse(valid)
        self.assertIn("缺少类型分隔符", reason)

    def test_invalid_unknown_type(self) -> None:
        """未知类型的分支名应被拒绝。"""
        valid, reason = validate_branch_name("feature/branch-name")
        self.assertFalse(valid)
        self.assertIn("非法分支类型", reason)

    def test_invalid_type_capitalized(self) -> None:
        """大写类型的分支名应被拒绝（必须小写）。"""
        valid, reason = validate_branch_name("Feat/branch-name")
        self.assertFalse(valid)
        self.assertIn("非法分支类型", reason)

    def test_invalid_type_uppercase(self) -> None:
        valid, reason = validate_branch_name("FIX/branch-name")
        self.assertFalse(valid)
        self.assertIn("非法分支类型", reason)

    def test_invalid_slug_with_spaces(self) -> None:
        """slug 包含空格应被拒绝。"""
        valid, reason = validate_branch_name("feat/branch naming")
        self.assertFalse(valid)
        self.assertIn("非法 slug", reason)

    def test_invalid_slug_with_dots(self) -> None:
        """slug 包含非法字符（如点号）应被拒绝。"""
        valid, reason = validate_branch_name("feat/branch.naming")
        self.assertFalse(valid)
        self.assertIn("非法 slug", reason)

    def test_valid_slug_with_underscore(self) -> None:
        """slug 可以包含下划线。"""
        valid, reason = validate_branch_name("feat/branch_naming")
        self.assertTrue(valid, reason)

    def test_valid_slug_with_numbers(self) -> None:
        """slug 可以包含数字。"""
        valid, reason = validate_branch_name("feat/t01-add-format")
        self.assertTrue(valid, reason)

    def test_valid_slug_chinese_name(self) -> None:
        """slug 可以包含中文字符（Unicode letters）。"""
        # get_worker_worktree_slug 返回的 slug 可能包含中文字符
        valid, reason = validate_branch_name("feat/分支命名")
        self.assertTrue(valid, reason)

    # ── build_branch_name ────────────────────────────────────

    def test_build_branch_name_with_worktree_path(self) -> None:
        """有 worktree_path 时，分支名应基于目录名构建。"""
        worker_state = {
            "current_item_id": 5,
            "worktree_path": "/tmp/.trees/loop-005-branch-naming",
            "queue": [
                {
                    "id": 5,
                    "name": "测试任务：分支命名规范",
                    "desc_path": "/tmp/tasks/5-005-branch-naming_03-31_18-33.md",
                }
            ],
        }
        branch = build_branch_name("feat", worker_state)
        self.assertEqual(branch, "feat/005-branch-naming")

    def test_build_branch_name_with_desc_path(self) -> None:
        """无 worktree_path 但有 desc_path 时，分支名应从文件名提取。"""
        worker_state = {
            "current_item_id": 27,
            "queue": [
                {
                    "id": 27,
                    "name": "测试任务：Worktree 生命周期",
                    "desc_path": "/tmp/tasks/27-020-worktree-lifecycle_03-31_16-56.md",
                }
            ],
        }
        branch = build_branch_name("feat", worker_state)
        self.assertEqual(branch, "feat/020-worktree-lifecycle")

    def test_build_branch_name_with_name_fallback(self) -> None:
        """无 worktree_path 和 desc_path 时，分支名应从 name 构建。"""
        worker_state = {
            "current_item_id": 30,
            "queue": [{"id": 30, "name": "测试任务：模型选择验证"}],
        }
        branch = build_branch_name("fix", worker_state)
        self.assertEqual(branch, "fix/测试任务-模型选择验证")

    def test_build_branch_name_empty_state(self) -> None:
        """空 worker_state 无法构建分支名。"""
        branch = build_branch_name("feat", {})
        self.assertIsNone(branch)

    def test_build_branch_name_invalid_type(self) -> None:
        """非法 type 无法构建分支名。"""
        worker_state = {
            "current_item_id": 1,
            "queue": [{"id": 1, "name": "测试", "desc_path": "/tmp/t/1-001-test_03-31_18-33.md"}],
        }
        branch = build_branch_name("feature", worker_state)
        self.assertIsNone(branch)

    def test_build_all_valid_types(self) -> None:
        """所有合法 type 都应能构建分支名。"""
        worker_state = {
            "current_item_id": 1,
            "queue": [{"id": 1, "name": "测试", "desc_path": "/tmp/t/1-001-test_03-31_18-33.md"}],
        }
        for branch_type in VALID_BRANCH_TYPES:
            with self.subTest(branch_type=branch_type):
                branch = build_branch_name(branch_type, worker_state)
                self.assertIsNotNone(branch)
                valid, reason = validate_branch_name(branch)
                self.assertTrue(valid, reason)

    def test_branch_in_worktree_created_with_correct_name(self) -> None:
        """实际验证：openNiuMa 创建的分支应符合规范。

        验证模式：遍历 openNiuMa 已创建的功能分支，检查是否全部符合 <type>/<slug> 格式。
        """
        import subprocess
        from pathlib import Path

        # 当前仓库根目录
        _repo_dir = Path(__file__).resolve().parent.parent

        # 读取仓库的分支列表
        try:
            result = subprocess.run(
                ["git", "branch", "-a", "--format=%(refname:short)"],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=str(_repo_dir),
            )
            branches = [b.strip() for b in result.stdout.splitlines() if b.strip()]
        except Exception as e:
            self.skipTest(f"无法读取 git 分支: {e}")
            return

        # 筛选功能分支（feat/xxx, fix/xxx, docs/xxx, chore/xxx, refactor/xxx）
        feature_branches = [
            b for b in branches
            if any(b.startswith(f"{t}/") for t in VALID_BRANCH_TYPES)
        ]

        if len(feature_branches) == 0:
            self.skipTest("仓库中无功能分支，跳过分支命名规范检查")

        violations = []
        for branch in feature_branches:
            valid, reason = validate_branch_name(branch)
            if not valid:
                violations.append(f"  {branch}: {reason}")

        self.assertEqual(
            violations, [],
            f"发现 {len(violations)} 条违规分支名:\n" + "\n".join(violations)
        )


if __name__ == "__main__":
    unittest.main()
