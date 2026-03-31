"""测试工具函数 — 临时目录、临时 JSON 文件等"""
import json
import os
import tempfile

def make_temp_json(data: dict, suffix: str = ".json") -> str:
    """创建临时 JSON 文件，返回路径"""
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    return path

def make_temp_dir() -> str:
    """创建临时目录，返回路径"""
    return tempfile.mkdtemp(prefix="openniuma-test-")
