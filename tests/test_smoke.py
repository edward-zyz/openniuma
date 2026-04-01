# SPDX-License-Identifier: MIT
"""冒烟测试 — 验证测试基础设施正常工作"""
import json
import os
import tempfile
import unittest

import openniuma


def make_temp_json(data: dict, suffix: str = ".json") -> str:
    """创建临时 JSON 文件，返回路径"""
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    return path


def make_temp_dir() -> str:
    """创建临时目录，返回路径"""
    return tempfile.mkdtemp(prefix="openniuma-test-")


class TestSmoke(unittest.TestCase):
    def test_import_lib(self):
        self.assertTrue(openniuma.__version__)

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
