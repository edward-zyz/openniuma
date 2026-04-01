# SPDX-License-Identifier: MIT
"""openniuma/lib/test_config.py — config.py 单元测试"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from openniuma.core.config import (
    export_env,
    get_nested,
    load_config,
    render_prompt,
    validate_config,
)

MINIMAL_YAML = """
polling:
  inbox_interval_sec: 30
workers:
  max_concurrent: 3
  stall_timeout_sec: 900
  max_consecutive_failures: 2
retry:
  base_delay_sec: 5
  max_backoff_sec: 120
  rate_limit_default_wait_sec: 300
failure:
  max_retries_gate: 2
  max_retries_network: 1
  max_retries_context: 1
  max_retries_conflict: 1
  max_retries_permission: 1
  skip_on_unknown: false
project:
  name: TestProject
  main_branch: main
  gate_command: "echo ok"
"""


class TestConfigLoad(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.yaml_path = os.path.join(self.tmpdir, "workflow.yaml")
        self.cache_dir = os.path.join(self.tmpdir, ".cache")
        Path(self.yaml_path).write_text(MINIMAL_YAML, encoding="utf-8")

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_load_basic(self) -> None:
        config = load_config(self.yaml_path, self.cache_dir)
        self.assertEqual(config["workers"]["max_concurrent"], 3)
        self.assertEqual(config["project"]["name"], "TestProject")
        self.assertEqual(config["retry"]["base_delay_sec"], 5)

    def test_get_nested(self) -> None:
        config = load_config(self.yaml_path, self.cache_dir)
        self.assertEqual(get_nested(config, "workers.max_concurrent"), 3)
        self.assertEqual(get_nested(config, "project.main_branch"), "main")
        self.assertIsNone(get_nested(config, "nonexistent.key"))
        self.assertEqual(get_nested(config, "nonexistent.key", 42), 42)

    def test_cache_created(self) -> None:
        load_config(self.yaml_path, self.cache_dir)
        cache_file = Path(self.cache_dir) / "workflow.json"
        self.assertTrue(cache_file.exists())
        cached = json.loads(cache_file.read_text(encoding="utf-8"))
        self.assertEqual(cached["workers"]["max_concurrent"], 3)

    def test_fallback_to_cache_when_yaml_missing(self) -> None:
        # 先加载一次以创建缓存
        load_config(self.yaml_path, self.cache_dir)
        # 删除 YAML 文件
        os.remove(self.yaml_path)
        # 应降级到缓存
        config = load_config(self.yaml_path, self.cache_dir)
        self.assertEqual(config["workers"]["max_concurrent"], 3)


class TestConfigExportEnv(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.yaml_path = os.path.join(self.tmpdir, "workflow.yaml")
        self.cache_dir = os.path.join(self.tmpdir, ".cache")
        Path(self.yaml_path).write_text(MINIMAL_YAML, encoding="utf-8")

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_export_env_format(self) -> None:
        lines = export_env(self.yaml_path, self.cache_dir)
        # 应该是 KEY=VALUE 格式
        for line in lines:
            self.assertRegex(line, r"^CONF_\w+=.+")
        # 检查关键变量存在
        keys = [line.split("=", 1)[0] for line in lines]
        self.assertIn("CONF_MAX_CONCURRENT", keys)
        self.assertIn("CONF_STALL_TIMEOUT", keys)
        self.assertIn("CONF_GATE_COMMAND", keys)

    def test_export_env_values_correct(self) -> None:
        lines = export_env(self.yaml_path, self.cache_dir)
        env_dict = {}
        for line in lines:
            k, v = line.split("=", 1)
            env_dict[k] = v
        # shlex.quote 会给纯数字加引号或保持原样
        self.assertIn("3", env_dict["CONF_MAX_CONCURRENT"])
        self.assertIn("900", env_dict["CONF_STALL_TIMEOUT"])
        self.assertIn("main", env_dict["CONF_MAIN_BRANCH"])


class TestConfigValidation(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.yaml_path = os.path.join(self.tmpdir, "workflow.yaml")
        self.cache_dir = os.path.join(self.tmpdir, ".cache")
        Path(self.yaml_path).write_text(MINIMAL_YAML, encoding="utf-8")

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_valid_config_no_errors(self) -> None:
        config = load_config(self.yaml_path, self.cache_dir)
        errors = validate_config(config)
        self.assertEqual(errors, [])

    def test_out_of_range_detected(self) -> None:
        config = load_config(self.yaml_path, self.cache_dir)
        config["workers"]["max_concurrent"] = 100
        errors = validate_config(config)
        self.assertTrue(any("max_concurrent" in e for e in errors))

    def test_wrong_type_detected(self) -> None:
        config = load_config(self.yaml_path, self.cache_dir)
        config["workers"]["max_concurrent"] = "not_a_number"
        errors = validate_config(config)
        self.assertTrue(any("max_concurrent" in e for e in errors))


class TestPromptRender(unittest.TestCase):
    def test_render_basic(self) -> None:
        template = "Hello {{name}}, your project is {{project}}."
        result = render_prompt(template, {"name": "Alice", "project": "POI"})
        self.assertEqual(result, "Hello Alice, your project is POI.")

    def test_render_unknown_var_raises(self) -> None:
        template = "Hello {{unknown_var}}!"
        with self.assertRaises(ValueError) as ctx:
            render_prompt(template, {"name": "Alice"})
        self.assertIn("unknown_var", str(ctx.exception))

    def test_render_preserves_non_template_braces(self) -> None:
        template = "JSON: {key: value}, template: {{name}}"
        result = render_prompt(template, {"name": "Bob"})
        self.assertEqual(result, "JSON: {key: value}, template: Bob")


if __name__ == "__main__":
    unittest.main()
