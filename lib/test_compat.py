"""compat.py 单元测试。"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from openniuma.lib.compat import (
    check_python_version,
    copy_tree,
    run_with_timeout,
    sed_inplace,
)


class TestSedInplace(unittest.TestCase):
    """sed_inplace 正则替换测试。"""

    def setUp(self):
        self.tmpfile = tempfile.NamedTemporaryFile(
            mode="w", suffix=".env", delete=False, encoding="utf-8"
        )
        self.tmpfile.close()

    def tearDown(self):
        os.unlink(self.tmpfile.name)

    def test_simple_replacement(self):
        """简单字符串替换 — DATABASE_URL 修改。"""
        Path(self.tmpfile.name).write_text(
            "DATABASE_URL=postgresql://localhost/old_db\nOTHER=keep\n",
            encoding="utf-8",
        )
        sed_inplace(self.tmpfile.name, "old_db", "new_db")
        content = Path(self.tmpfile.name).read_text(encoding="utf-8")
        self.assertIn("new_db", content)
        self.assertNotIn("old_db", content)
        self.assertIn("OTHER=keep", content)

    def test_regex_replacement(self):
        """正则替换 — PORT=数字。"""
        Path(self.tmpfile.name).write_text("PORT=3000\n", encoding="utf-8")
        sed_inplace(self.tmpfile.name, r"PORT=\d+", "PORT=4000")
        content = Path(self.tmpfile.name).read_text(encoding="utf-8")
        self.assertEqual(content.strip(), "PORT=4000")

    def test_no_match_leaves_file_unchanged(self):
        """无匹配时文件内容不变。"""
        original = "KEEP=this\nALSO=that\n"
        Path(self.tmpfile.name).write_text(original, encoding="utf-8")
        sed_inplace(self.tmpfile.name, "NONEXISTENT", "REPLACED")
        content = Path(self.tmpfile.name).read_text(encoding="utf-8")
        self.assertEqual(content, original)


class TestCopyTree(unittest.TestCase):
    """copy_tree 目录复制测试。"""

    def setUp(self):
        self.src_dir = tempfile.mkdtemp()
        self.dst_dir = os.path.join(tempfile.mkdtemp(), "output")

    def tearDown(self):
        import shutil

        shutil.rmtree(self.src_dir, ignore_errors=True)
        # dst_dir 的父目录
        parent = os.path.dirname(self.dst_dir)
        shutil.rmtree(parent, ignore_errors=True)

    def test_copy_directory(self):
        """复制含嵌套子目录的目录树。"""
        # 创建嵌套结构
        nested = os.path.join(self.src_dir, "sub", "deep")
        os.makedirs(nested)
        Path(os.path.join(self.src_dir, "root.txt")).write_text(
            "root", encoding="utf-8"
        )
        Path(os.path.join(nested, "leaf.txt")).write_text("leaf", encoding="utf-8")

        copy_tree(self.src_dir, self.dst_dir)

        self.assertTrue(os.path.isdir(self.dst_dir))
        self.assertEqual(
            Path(os.path.join(self.dst_dir, "root.txt")).read_text(encoding="utf-8"),
            "root",
        )
        self.assertEqual(
            Path(os.path.join(self.dst_dir, "sub", "deep", "leaf.txt")).read_text(
                encoding="utf-8"
            ),
            "leaf",
        )


class TestCheckPythonVersion(unittest.TestCase):
    """check_python_version 测试。"""

    def test_current_python_passes(self):
        """当前 Python 版本应满足 3.9 要求，不会 sys.exit。"""
        # 不抛异常即为通过
        check_python_version((3, 9))


class TestRunWithTimeout(unittest.TestCase):
    """run_with_timeout 测试。"""

    def test_quick_command_succeeds(self):
        """快速命令正常完成。"""
        result = run_with_timeout(
            ["echo", "hello"], timeout_sec=5, capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("hello", result.stdout)

    def test_timeout_raises(self):
        """超时命令抛出 TimeoutExpired。"""
        with self.assertRaises(subprocess.TimeoutExpired):
            run_with_timeout(["sleep", "10"], timeout_sec=1)


if __name__ == "__main__":
    unittest.main()
