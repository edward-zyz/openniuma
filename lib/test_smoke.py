"""冒烟测试 — 验证测试基础设施正常工作"""
import unittest
import os
from .conftest import make_temp_json, make_temp_dir

class TestSmoke(unittest.TestCase):
    def test_import_lib(self):
        from . import __version__
        self.assertTrue(__version__)

    def test_make_temp_json(self):
        path = make_temp_json({"key": "value"})
        self.assertTrue(os.path.exists(path))
        os.unlink(path)

    def test_make_temp_dir(self):
        d = make_temp_dir()
        self.assertTrue(os.path.isdir(d))
        os.rmdir(d)

if __name__ == "__main__":
    unittest.main()
