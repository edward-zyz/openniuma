# SPDX-License-Identifier: MIT
"""共享 pytest fixtures。"""

import json
from pathlib import Path

import pytest


@pytest.fixture
def tmp_dir(tmp_path: Path) -> Path:
    """提供一个临时目录。"""
    return tmp_path


@pytest.fixture
def state_file(tmp_path: Path) -> Path:
    """提供一个带初始状态的临时 state.json。"""
    state = {
        "queue": [],
        "completed": [],
        "blocked": [],
        "dev_branch": None,
        "batch_branch": None,
        "batch_status": "active",
        "current_phase": None,
    }
    p = tmp_path / "state.json"
    p.write_text(json.dumps(state), encoding="utf-8")
    return p


@pytest.fixture
def runtime_dir(tmp_path: Path) -> Path:
    """提供一个带完整结构的临时运行时目录。"""
    runtime = tmp_path / ".openniuma-runtime"
    for sub in ["inbox", "tasks", "logs", "reviews", "workers", "drafts"]:
        (runtime / sub).mkdir(parents=True)
    state = {
        "queue": [],
        "completed": [],
        "blocked": [],
        "dev_branch": None,
        "batch_branch": None,
        "batch_status": "active",
        "current_phase": None,
    }
    (runtime / "state.json").write_text(json.dumps(state), encoding="utf-8")
    return runtime
