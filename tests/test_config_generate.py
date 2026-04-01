# SPDX-License-Identifier: MIT
"""测试 workflow.yaml 生成。"""

import yaml

from openniuma.core.config import generate_workflow_yaml


def test_generate_minimal():
    content = generate_workflow_yaml(
        name="TestProject", main_branch="main", gate_command="pytest",
    )
    config = yaml.safe_load(content)
    assert config["project"]["name"] == "TestProject"
    assert config["project"]["main_branch"] == "main"
    assert config["project"]["gate_command"] == "pytest"
    assert config["workers"]["max_concurrent"] == 3
    assert config["models"]["default"] == "opus"
    assert config["schema_version"] == 1


def test_generate_with_hooks():
    content = generate_workflow_yaml(
        name="NodeProject", main_branch="master", gate_command="npm test",
        after_create="npm install", before_remove="echo cleanup",
    )
    config = yaml.safe_load(content)
    assert "npm install" in config["hooks"]["after_create"]
    assert "echo cleanup" in config["hooks"]["before_remove"]
