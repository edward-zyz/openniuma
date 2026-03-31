"""openniuma/lib/detect.py — 项目技术栈自动探测

无外部依赖，纯标准库（yaml 可选）。

CLI 用法:
    python3 detect.py <repo_dir>               # JSON 输出
    python3 detect.py <repo_dir> --shell-vars   # shell 变量输出
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# 默认值
# ---------------------------------------------------------------------------

_DEFAULTS: dict[str, str] = {
    "stack": "unknown",
    "gate_command": "echo 'TODO: configure'",
    "after_create": "",
    "before_remove": "",
    "spec_dir": "docs/specs",
    "plan_dir": "docs/plans",
}

# spec_dir 候选路径（按优先级）
_SPEC_DIR_CANDIDATES = [
    "docs/specs",
    "docs/superpowers/specs",
    "specs",
]

# plan_dir 候选路径
_PLAN_DIR_CANDIDATES = [
    "docs/plans",
    "docs/superpowers/plans",
    "plans",
]


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> dict[str, Any] | None:
    """安全读取 JSON 文件，失败返回 None。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _detect_spec_dir(repo: Path) -> str:
    """扫描常见 spec 目录，返回第一个存在的。"""
    for candidate in _SPEC_DIR_CANDIDATES:
        if (repo / candidate).is_dir():
            return candidate
    return _DEFAULTS["spec_dir"]


def _detect_plan_dir(repo: Path) -> str:
    """扫描常见 plan 目录，返回第一个存在的。"""
    for candidate in _PLAN_DIR_CANDIDATES:
        if (repo / candidate).is_dir():
            return candidate
    return _DEFAULTS["plan_dir"]


def _has_database_url(repo: Path) -> bool:
    """检查 .env.example 中是否包含 DATABASE_URL。"""
    env_example = repo / "backend" / ".env.example"
    if not env_example.exists():
        env_example = repo / ".env.example"
    if not env_example.exists():
        return False
    try:
        text = env_example.read_text(encoding="utf-8")
        return "DATABASE_URL" in text
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Node.js 探测
# ---------------------------------------------------------------------------


def _detect_node(repo: Path) -> dict[str, str] | None:
    """探测 Node.js 项目。"""
    pkg_path = repo / "package.json"
    pkg = _read_json(pkg_path)
    if pkg is None:
        return None

    scripts = pkg.get("scripts", {})
    workspaces = pkg.get("workspaces", [])

    # 构建 gate_command
    gate_parts: list[str] = []
    if "lint" in scripts:
        gate_parts.append("npm run lint")
    if "test" in scripts:
        gate_parts.append("npm test")
    if "build" in scripts:
        gate_parts.append("npm run build")
    if "typecheck" in scripts:
        gate_parts.append("npm run typecheck")

    # 检测前端 TypeScript: frontend/tsconfig.json 存在则追加 tsc --noEmit
    if (repo / "frontend" / "tsconfig.json").exists():
        gate_parts.append("npx tsc --noEmit -p frontend")

    gate_command = " && ".join(gate_parts) if gate_parts else _DEFAULTS["gate_command"]

    # after_create
    if workspaces:
        # monorepo: 生成 APFS clone 脚本
        after_create_lines = [
            'MAIN_REPO="$(cd "$(dirname "$0")/.." && pwd)"',
            'WORKTREE_DIR="$1"',
            "# APFS clone for monorepo node_modules",
            'if command -v cp >/dev/null 2>&1; then',
            '  cp -c -r "$MAIN_REPO/node_modules" "$WORKTREE_DIR/node_modules" 2>/dev/null'
            " || npm install",
            "fi",
        ]
        # 为每个 workspace 也 clone node_modules
        for ws in workspaces:
            after_create_lines.append(
                f'if [ -d "$MAIN_REPO/{ws}/node_modules" ]; then'
            )
            after_create_lines.append(
                f'  cp -c -r "$MAIN_REPO/{ws}/node_modules"'
                f' "$WORKTREE_DIR/{ws}/node_modules" 2>/dev/null || true'
            )
            after_create_lines.append("fi")
        after_create = "\n".join(after_create_lines)
    else:
        after_create = "npm install"

    # before_remove
    before_remove = ""

    # DATABASE_URL → 追加 createdb/dropdb hooks
    if _has_database_url(repo):
        db_name_var = 'poi_dev_$(basename "$WORKTREE_DIR")'
        after_create += f"\ncreatedb {db_name_var} 2>/dev/null || true"
        before_remove = f"dropdb {db_name_var} 2>/dev/null || true"

    return {
        "stack": "node",
        "gate_command": gate_command,
        "after_create": after_create,
        "before_remove": before_remove,
    }


# ---------------------------------------------------------------------------
# Go 探测
# ---------------------------------------------------------------------------


def _detect_go(repo: Path) -> dict[str, str] | None:
    """探测 Go 项目。"""
    if not (repo / "go.mod").exists():
        return None

    gate_parts = ["go test ./..."]
    if (repo / ".golangci.yml").exists() or (repo / ".golangci.yaml").exists():
        gate_parts.insert(0, "golangci-lint run")

    return {
        "stack": "go",
        "gate_command": " && ".join(gate_parts),
        "after_create": "go mod download",
        "before_remove": "",
    }


# ---------------------------------------------------------------------------
# Rust 探测
# ---------------------------------------------------------------------------


def _detect_rust(repo: Path) -> dict[str, str] | None:
    """探测 Rust 项目。"""
    if not (repo / "Cargo.toml").exists():
        return None

    return {
        "stack": "rust",
        "gate_command": "cargo fmt --check && cargo clippy -- -D warnings && cargo test",
        "after_create": "cargo fetch",
        "before_remove": "",
    }


# ---------------------------------------------------------------------------
# Python 探测
# ---------------------------------------------------------------------------


def _detect_python(repo: Path) -> dict[str, str] | None:
    """探测 Python 项目。"""
    pyproject = repo / "pyproject.toml"
    requirements = repo / "requirements.txt"

    if not pyproject.exists() and not requirements.exists():
        return None

    gate_parts: list[str] = []

    # 尝试读 pyproject.toml 检测工具
    if pyproject.exists():
        try:
            text = pyproject.read_text(encoding="utf-8")
        except OSError:
            text = ""

        if "[tool.ruff]" in text or "ruff" in text.lower().split("\n")[0:5]:
            gate_parts.append("ruff check .")
        if "[tool.mypy]" in text:
            gate_parts.append("mypy .")
        if "[tool.pytest" in text or "pytest" in text:
            gate_parts.append("pytest")

    if not gate_parts:
        gate_parts.append("pytest")

    # after_create
    if pyproject.exists():
        after_create = "pip install -e '.[dev]' 2>/dev/null || pip install -e ."
    else:
        after_create = "pip install -r requirements.txt"

    return {
        "stack": "python",
        "gate_command": " && ".join(gate_parts),
        "after_create": after_create,
        "before_remove": "",
    }


# ---------------------------------------------------------------------------
# Ruby 探测
# ---------------------------------------------------------------------------


def _detect_ruby(repo: Path) -> dict[str, str] | None:
    """探测 Ruby 项目。"""
    if not (repo / "Gemfile").exists():
        return None

    return {
        "stack": "ruby",
        "gate_command": "bundle exec rake test",
        "after_create": "bundle install",
        "before_remove": "",
    }


# ---------------------------------------------------------------------------
# CI 配置探测（补充）
# ---------------------------------------------------------------------------


def _detect_from_ci(repo: Path) -> dict[str, str]:
    """扫描 .github/workflows/ 提取 gate 命令（辅助信息）。"""
    result: dict[str, str] = {}
    workflows_dir = repo / ".github" / "workflows"
    if not workflows_dir.is_dir():
        return result

    ci_files = [
        f
        for f in workflows_dir.iterdir()
        if f.suffix in (".yml", ".yaml")
        and any(kw in f.stem for kw in ("ci", "test", "check", "lint"))
    ]

    if not ci_files:
        return result

    # 尝试用 yaml 解析
    yaml_mod = None
    try:
        import yaml as yaml_mod  # type: ignore[no-redef]
    except ImportError:
        pass

    ci_commands: list[str] = []

    for ci_file in ci_files:
        try:
            text = ci_file.read_text(encoding="utf-8")
        except OSError:
            continue

        if yaml_mod is not None:
            try:
                data = yaml_mod.safe_load(text)
                if isinstance(data, dict) and "jobs" in data:
                    for _job_name, job in data["jobs"].items():
                        if not isinstance(job, dict):
                            continue
                        for step in job.get("steps", []):
                            if not isinstance(step, dict):
                                continue
                            run_cmd = step.get("run", "")
                            if isinstance(run_cmd, str):
                                for line in run_cmd.splitlines():
                                    line = line.strip()
                                    if any(
                                        kw in line
                                        for kw in ("test", "lint", "build", "tsc")
                                    ):
                                        ci_commands.append(line)
            except Exception:
                pass
        else:
            # 正则回退：提取 run: 后面的命令
            for match in re.finditer(r"run:\s*[|]?\s*\n?([\s\S]*?)(?=\n\s*-|\n\S|\Z)", text):
                block = match.group(1)
                for line in block.splitlines():
                    line = line.strip()
                    if any(kw in line for kw in ("test", "lint", "build", "tsc")):
                        ci_commands.append(line)

    if ci_commands:
        result["ci_commands"] = ci_commands  # type: ignore[assignment]

    return result


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def detect(repo_dir: str) -> dict[str, str]:
    """探测项目技术栈，返回标准化结果字典。"""
    repo = Path(repo_dir).resolve()

    # 按优先级依次探测
    detectors = [
        _detect_node,
        _detect_go,
        _detect_rust,
        _detect_python,
        _detect_ruby,
    ]

    result: dict[str, str] = dict(_DEFAULTS)

    for detector in detectors:
        detected = detector(repo)
        if detected is not None:
            result.update(detected)
            break

    # 目录探测
    result["spec_dir"] = _detect_spec_dir(repo)
    result["plan_dir"] = _detect_plan_dir(repo)

    # CI 补充信息（仅作参考，不覆盖已有 gate）
    _detect_from_ci(repo)

    return result


# ---------------------------------------------------------------------------
# Shell 变量输出
# ---------------------------------------------------------------------------

_SHELL_KEY_MAP = {
    "stack": "DETECT_STACK",
    "gate_command": "DETECT_GATE",
    "after_create": "DETECT_AFTER_CREATE",
    "before_remove": "DETECT_BEFORE_REMOVE",
    "spec_dir": "DETECT_SPEC_DIR",
    "plan_dir": "DETECT_PLAN_DIR",
}


def format_shell_vars(result: dict[str, str]) -> list[str]:
    """将探测结果转为 shell 变量赋值行。"""
    lines: list[str] = []
    for key, shell_name in _SHELL_KEY_MAP.items():
        value = result.get(key, "")
        lines.append(f"{shell_name}={shlex.quote(value)}")
    return lines


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI 入口。"""
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <repo_dir> [--shell-vars]", file=sys.stderr)
        sys.exit(1)

    repo_dir = sys.argv[1]
    shell_vars = "--shell-vars" in sys.argv

    result = detect(repo_dir)

    if shell_vars:
        for line in format_shell_vars(result):
            print(line)
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
