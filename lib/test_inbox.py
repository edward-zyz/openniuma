"""inbox.py 单元测试"""

import os
import tempfile
import unittest

from .inbox import generate_slug, scan_inbox, check_stop_signal, parse_frontmatter


class TestGenerateSlug(unittest.TestCase):

    def test_basic_slug(self):
        """generate_slug("支持自定义热力图半径") 返回非空、无空格、无特殊字符。"""
        slug = generate_slug("支持自定义热力图半径")
        self.assertTrue(len(slug) > 0)
        self.assertNotIn(" ", slug)
        # 只包含 ASCII 字母数字和连字符
        for ch in slug:
            self.assertTrue(ch.isalnum() or ch == "-", f"非法字符: {ch!r}")

    def test_ascii_slug(self):
        """英文名直接转为小写连字符格式。"""
        slug = generate_slug("Add Search Feature")
        self.assertEqual(slug, "add-search-feature")


class TestScanInbox(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_scan_md_files(self):
        """2 个 .md 文件 -> 2 个任务。"""
        for name in ["task-a.md", "task-b.md"]:
            with open(os.path.join(self.tmpdir, name), "w") as f:
                f.write(f"---\nname: {name}\ncomplexity: low\n---\nbody\n")

        tasks = scan_inbox(self.tmpdir)
        self.assertEqual(len(tasks), 2)

    def test_ignore_non_md_files(self):
        """.txt 文件不计入。"""
        with open(os.path.join(self.tmpdir, "notes.txt"), "w") as f:
            f.write("not a task")
        with open(os.path.join(self.tmpdir, "real.md"), "w") as f:
            f.write("---\nname: Real\n---\nbody")

        tasks = scan_inbox(self.tmpdir)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["name"], "Real")


class TestStopSignal(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_stop_signal_detected(self):
        """STOP 文件存在 -> True。"""
        with open(os.path.join(self.tmpdir, "STOP"), "w") as f:
            f.write("")
        self.assertTrue(check_stop_signal(self.tmpdir))

    def test_no_stop_signal(self):
        """空目录 -> False。"""
        self.assertFalse(check_stop_signal(self.tmpdir))


class TestParseFrontmatter(unittest.TestCase):

    def test_parse_frontmatter(self):
        """解析 '---\\nname: Test\\n---\\nbody' 正确。"""
        content = "---\nname: Test\n---\nbody"
        meta, body = parse_frontmatter(content)
        self.assertEqual(meta["name"], "Test")
        self.assertEqual(body, "body")

    def test_no_frontmatter(self):
        """没有 frontmatter 时返回空 dict 和完整内容。"""
        content = "just plain text"
        meta, body = parse_frontmatter(content)
        self.assertEqual(meta, {})
        self.assertEqual(body, content)


if __name__ == "__main__":
    unittest.main()
