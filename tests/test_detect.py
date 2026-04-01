# SPDX-License-Identifier: MIT
"""openniuma/lib/test_detect.py — detect.py 单元测试"""

from __future__ import annotations

import json
import os
import re
import tempfile
import unittest
from pathlib import Path

from openniuma.core.detect import detect, format_shell_vars


class TestDetectNode(unittest.TestCase):
    """Node.js 项目探测。"""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_node_project_detected(self) -> None:
        """创建 package.json，验证 stack=node, gate 包含 npm test + npm run lint。"""
        pkg = {
            "scripts": {
                "test": "jest",
                "lint": "eslint .",
                "build": "tsc",
            }
        }
        Path(self.tmpdir, "package.json").write_text(
            json.dumps(pkg), encoding="utf-8"
        )

        result = detect(self.tmpdir)

        self.assertEqual(result["stack"], "node")
        self.assertIn("npm test", result["gate_command"])
        self.assertIn("npm run lint", result["gate_command"])
        self.assertIn("npm run build", result["gate_command"])

    def test_node_monorepo_after_create(self) -> None:
        """monorepo (workspaces) → after_create 包含 MAIN_REPO。"""
        pkg = {
            "workspaces": ["frontend", "backend"],
            "scripts": {"test": "jest"},
        }
        Path(self.tmpdir, "package.json").write_text(
            json.dumps(pkg), encoding="utf-8"
        )

        result = detect(self.tmpdir)

        self.assertEqual(result["stack"], "node")
        self.assertIn("MAIN_REPO", result["after_create"])

    def test_node_frontend_tsconfig(self) -> None:
        """frontend/tsconfig.json 存在 → gate 追加 tsc --noEmit。"""
        pkg = {"scripts": {"test": "jest"}}
        Path(self.tmpdir, "package.json").write_text(
            json.dumps(pkg), encoding="utf-8"
        )
        frontend_dir = Path(self.tmpdir, "frontend")
        frontend_dir.mkdir()
        (frontend_dir / "tsconfig.json").write_text("{}", encoding="utf-8")

        result = detect(self.tmpdir)

        self.assertIn("npx tsc --noEmit -p frontend", result["gate_command"])

    def test_node_database_url_hooks(self) -> None:
        """检测 DATABASE_URL → after_create 含 createdb, before_remove 含 dropdb。"""
        pkg = {"scripts": {"test": "jest"}}
        Path(self.tmpdir, "package.json").write_text(
            json.dumps(pkg), encoding="utf-8"
        )
        backend_dir = Path(self.tmpdir, "backend")
        backend_dir.mkdir()
        (backend_dir / ".env.example").write_text(
            "DATABASE_URL=postgresql://localhost/test\nJWT_SECRET=abc",
            encoding="utf-8",
        )

        result = detect(self.tmpdir)

        self.assertIn("createdb", result["after_create"])
        self.assertIn("dropdb", result["before_remove"])


class TestDetectGo(unittest.TestCase):
    """Go 项目探测。"""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_go_project_detected(self) -> None:
        """创建 go.mod → stack=go, gate 包含 go test。"""
        Path(self.tmpdir, "go.mod").write_text(
            "module example.com/foo\n\ngo 1.21\n", encoding="utf-8"
        )

        result = detect(self.tmpdir)

        self.assertEqual(result["stack"], "go")
        self.assertIn("go test", result["gate_command"])


class TestDetectPython(unittest.TestCase):
    """Python 项目探测。"""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_python_project_detected(self) -> None:
        """创建 pyproject.toml 含 [tool.pytest] [tool.ruff] → stack=python, gate 包含 pytest。"""
        pyproject_content = """\
[build-system]
requires = ["setuptools"]

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.ruff]
line-length = 100
"""
        Path(self.tmpdir, "pyproject.toml").write_text(
            pyproject_content, encoding="utf-8"
        )

        result = detect(self.tmpdir)

        self.assertEqual(result["stack"], "python")
        self.assertIn("pytest", result["gate_command"])
        self.assertIn("ruff check", result["gate_command"])

    def test_python_with_mypy(self) -> None:
        """pyproject.toml 含 [tool.mypy] → gate 包含 mypy。"""
        content = """\
[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.mypy]
strict = true
"""
        Path(self.tmpdir, "pyproject.toml").write_text(content, encoding="utf-8")

        result = detect(self.tmpdir)

        self.assertEqual(result["stack"], "python")
        self.assertIn("mypy", result["gate_command"])


class TestDetectRust(unittest.TestCase):
    """Rust 项目探测。"""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_rust_project_detected(self) -> None:
        """创建 Cargo.toml → stack=rust。"""
        Path(self.tmpdir, "Cargo.toml").write_text(
            '[package]\nname = "foo"\nversion = "0.1.0"\n', encoding="utf-8"
        )

        result = detect(self.tmpdir)

        self.assertEqual(result["stack"], "rust")
        self.assertIn("cargo test", result["gate_command"])


class TestDetectRuby(unittest.TestCase):
    """Ruby 项目探测。"""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_ruby_project_detected(self) -> None:
        """创建 Gemfile → stack=ruby。"""
        Path(self.tmpdir, "Gemfile").write_text(
            'source "https://rubygems.org"\ngem "rails"\n', encoding="utf-8"
        )

        result = detect(self.tmpdir)

        self.assertEqual(result["stack"], "ruby")
        self.assertIn("bundle exec rake test", result["gate_command"])


class TestDetectShellVars(unittest.TestCase):
    """format_shell_vars 输出格式。"""

    def test_shell_vars_output(self) -> None:
        """每行匹配 DETECT_\\w+=。"""
        result = {
            "stack": "node",
            "gate_command": "npm test && npm run lint",
            "after_create": "npm install",
            "before_remove": "",
            "spec_dir": "docs/specs",
            "plan_dir": "docs/plans",
        }
        lines = format_shell_vars(result)

        self.assertTrue(len(lines) > 0)
        for line in lines:
            self.assertRegex(line, r"^DETECT_\w+=")

    def test_shell_vars_are_quoted(self) -> None:
        """值包含特殊字符时正确 shlex.quote。"""
        result = {
            "stack": "node",
            "gate_command": "npm test && npm run lint",
            "after_create": 'echo "hello world"',
            "before_remove": "",
            "spec_dir": "docs/specs",
            "plan_dir": "docs/plans",
        }
        lines = format_shell_vars(result)
        gate_line = [l for l in lines if l.startswith("DETECT_GATE=")][0]
        # shlex.quote 应包裹整个值
        self.assertIn("'", gate_line)


class TestDetectUnknown(unittest.TestCase):
    """空目录 → unknown。"""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_empty_dir_returns_unknown(self) -> None:
        """空目录 → stack=unknown。"""
        result = detect(self.tmpdir)

        self.assertEqual(result["stack"], "unknown")
        self.assertEqual(result["gate_command"], "echo 'TODO: configure'")


class TestDetectSpecDir(unittest.TestCase):
    """spec_dir / plan_dir 目录探测。"""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_superpowers_specs_detected(self) -> None:
        """docs/superpowers/specs 存在 → 优先于默认值。"""
        spec_path = Path(self.tmpdir, "docs", "superpowers", "specs")
        spec_path.mkdir(parents=True)

        result = detect(self.tmpdir)

        self.assertEqual(result["spec_dir"], "docs/superpowers/specs")

    def test_default_spec_dir(self) -> None:
        """无 spec 目录 → 使用默认 docs/specs。"""
        result = detect(self.tmpdir)

        self.assertEqual(result["spec_dir"], "docs/specs")

    def test_docs_specs_takes_priority(self) -> None:
        """docs/specs 和 docs/superpowers/specs 都存在 → docs/specs 优先。"""
        Path(self.tmpdir, "docs", "specs").mkdir(parents=True)
        Path(self.tmpdir, "docs", "superpowers", "specs").mkdir(parents=True)

        result = detect(self.tmpdir)

        self.assertEqual(result["spec_dir"], "docs/specs")


if __name__ == "__main__":
    unittest.main()
