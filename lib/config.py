"""openniuma/lib/config.py — 配置加载 + 磁盘缓存 + export-env + schema 校验 + prompt 渲染"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any

# PyYAML 是可选依赖
try:
    import yaml  # type: ignore

    HAS_YAML = True
except ImportError:
    HAS_YAML = False

# ---------------------------------------------------------------------------
# 默认 YAML 路径（相对项目根）
# ---------------------------------------------------------------------------
_DEFAULT_YAML = "openniuma/workflow.yaml"

# ---------------------------------------------------------------------------
# Schema 定义：(type, min, max)
# ---------------------------------------------------------------------------
SCHEMA: dict[str, tuple[type, int | float, int | float]] = {
    "workers.max_concurrent": (int, 1, 20),
    "workers.stall_timeout_sec": (int, 60, 7200),
    "workers.max_consecutive_failures": (int, 1, 20),
    "retry.base_delay_sec": (int, 1, 600),
    "retry.max_backoff_sec": (int, 10, 3600),
    "retry.rate_limit_default_wait_sec": (int, 10, 7200),
    "failure.max_retries_gate": (int, 0, 10),
    "failure.max_retries_network": (int, 0, 10),
    "failure.max_retries_context": (int, 0, 10),
    "failure.max_retries_conflict": (int, 0, 10),
    "failure.max_retries_permission": (int, 0, 10),
    "polling.inbox_interval_sec": (int, 5, 3600),
    "hooks.timeout_sec": (int, 10, 600),
    "notify.suppress_window_sec": (int, 0, 3600),
    "notify.aggregate_interval_sec": (int, 0, 3600),
    "notify.feishu_rate_limit_per_min": (int, 1, 60),
    "pause.auto_resume_sec": (int, 0, 86400),
}

# export-env 映射：环境变量名 → 配置 key
_ENV_MAP: dict[str, str] = {
    "CONF_MAX_CONCURRENT": "workers.max_concurrent",
    "CONF_STALL_TIMEOUT": "workers.stall_timeout_sec",
    "CONF_MAX_CONSECUTIVE_FAILURES": "workers.max_consecutive_failures",
    "CONF_BASE_DELAY": "retry.base_delay_sec",
    "CONF_MAX_BACKOFF": "retry.max_backoff_sec",
    "CONF_RATE_LIMIT_WAIT": "retry.rate_limit_default_wait_sec",
    "CONF_GATE_COMMAND": "project.gate_command",
    "CONF_MAIN_BRANCH": "project.main_branch",
    "CONF_DEV_BRANCH_PREFIX": "project.dev_branch_prefix",
    "CONF_FEAT_BRANCH_PREFIX": "project.feat_branch_prefix",
    "CONF_SPEC_DIR": "project.spec_dir",
    "CONF_PLAN_DIR": "project.plan_dir",
    "CONF_WORKTREE_BASE": "worktree.base_dir",
    "CONF_WORKTREE_PREFIX": "worktree.prefix",
    "CONF_PROMPTS_DIR": "prompts.dir",
    "CONF_COMMON_RULES": "prompts.common_rules",
    "CONF_INBOX_INTERVAL": "polling.inbox_interval_sec",
    "CONF_HOOK_AFTER_CREATE": "hooks.after_create",
    "CONF_HOOK_BEFORE_REMOVE": "hooks.before_remove",
    "CONF_HOOK_TIMEOUT": "hooks.timeout_sec",
    "CONF_NOTIFY_LEVEL": "notify.level",
    "CONF_NOTIFY_MACOS": "notify.macos",
    "CONF_NOTIFY_BELL": "notify.bell",
    "CONF_NOTIFY_FEISHU_WEBHOOK": "notify.feishu_webhook",
    "CONF_MAX_RETRIES_GATE": "failure.max_retries_gate",
    "CONF_MAX_RETRIES_NETWORK": "failure.max_retries_network",
    "CONF_MAX_RETRIES_CONTEXT": "failure.max_retries_context",
    "CONF_MAX_RETRIES_CONFLICT": "failure.max_retries_conflict",
    "CONF_MAX_RETRIES_PERMISSION": "failure.max_retries_permission",
    "CONF_SKIP_ON_UNKNOWN": "failure.skip_on_unknown",
    "CONF_PAUSE_AUTO_RESUME": "pause.auto_resume_sec",
    "CONF_PAUSE_PARTIAL": "pause.partial",
    "CONF_MODEL_DEFAULT": "models.default",
}

# models 配置中有效的 phase 名
_VALID_PHASES = {
    "INIT", "FAST_TRACK", "DESIGN_IMPLEMENT", "DESIGN", "IMPLEMENT",
    "VERIFY", "FIX", "MERGE", "MERGE_FIX", "FINALIZE", "CI_FIX",
    "RELEASE_PREP", "RELEASE",
}


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def get_nested(config: dict[str, Any], key: str, default: Any = None) -> Any:
    """点号分隔嵌套取值，如 'workers.max_concurrent'"""
    parts = key.split(".")
    node: Any = config
    for part in parts:
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return default
    return node


def _cache_path(yaml_path: str, cache_dir: str | None) -> Path:
    """计算缓存文件路径"""
    if cache_dir is None:
        cache_dir = str(Path(yaml_path).parent / ".cache")
    p = Path(cache_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p / "workflow.json"


def load_config(yaml_path: str = _DEFAULT_YAML, cache_dir: str | None = None) -> dict[str, Any]:
    """加载 YAML 配置，带磁盘缓存。

    缓存逻辑：yaml mtime > cache mtime 则重解析+写缓存，否则读缓存。
    YAML 解析失败或 PyYAML 不可用时降级到缓存。
    """
    cache = _cache_path(yaml_path, cache_dir)
    yaml_file = Path(yaml_path)

    # 尝试读缓存
    def _read_cache() -> dict[str, Any] | None:
        if cache.exists():
            try:
                return json.loads(cache.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
            except (json.JSONDecodeError, OSError):
                return None
        return None

    # 缓存是否新鲜
    def _cache_fresh() -> bool:
        if not cache.exists() or not yaml_file.exists():
            return False
        return os.path.getmtime(str(cache)) >= os.path.getmtime(str(yaml_file))

    # 如果缓存新鲜，直接返回
    if _cache_fresh():
        cached = _read_cache()
        if cached is not None:
            return cached

    # 尝试解析 YAML
    if yaml_file.exists() and HAS_YAML:
        try:
            with open(yaml_file, encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
            # 写缓存
            try:
                cache.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
            except OSError:
                pass
            return config  # type: ignore[no-any-return]
        except Exception:
            # YAML 解析失败，降级到缓存
            cached = _read_cache()
            if cached is not None:
                return cached
            raise

    # PyYAML 不可用或 yaml 文件不存在，降级到缓存
    cached = _read_cache()
    if cached is not None:
        return cached

    if not yaml_file.exists():
        raise FileNotFoundError(f"配置文件不存在: {yaml_path}")
    raise ImportError("PyYAML 不可用且无缓存可用")


def validate_config(config: dict[str, Any]) -> list[str]:
    """Schema 校验，返回错误列表（空列表表示通过）"""
    errors: list[str] = []
    for key, (expected_type, min_val, max_val) in SCHEMA.items():
        val = get_nested(config, key)
        if val is None:
            continue  # 可选字段，缺失不报错
        if not isinstance(val, expected_type):
            errors.append(f"{key}: 期望类型 {expected_type.__name__}，实际为 {type(val).__name__} ({val!r})")
            continue
        if val < min_val or val > max_val:
            errors.append(f"{key}: 值 {val} 不在范围 [{min_val}, {max_val}]")
    return errors


def export_env(yaml_path: str = _DEFAULT_YAML, cache_dir: str | None = None) -> list[str]:
    """返回 shell 变量行列表，如 ['CONF_MAX_CONCURRENT=5', ...]"""
    config = load_config(yaml_path, cache_dir)
    lines: list[str] = []
    for env_name, config_key in _ENV_MAP.items():
        val = get_nested(config, config_key)
        if val is None:
            continue
        # 布尔值转 shell 友好格式
        if isinstance(val, bool):
            str_val = "true" if val else "false"
        else:
            str_val = str(val).strip()
        lines.append(f"{env_name}={shlex.quote(str_val)}")
    return lines


def resolve_model(phase: str, yaml_path: str = _DEFAULT_YAML, cache_dir: str | None = None) -> str:
    """解析指定 phase 应使用的模型。

    支持两种 models 配置格式：
      models: sonnet                  # 简写：所有 phase 用同一模型
      models:                          # 完整写法
        default: opus
        phases:
          VERIFY: sonnet
    返回模型名（如 'opus', 'sonnet', 'claude-sonnet-4-6'），无配置则返回空字符串。
    """
    config = load_config(yaml_path, cache_dir)
    models = config.get("models")
    if models is None:
        return ""
    # 简写格式：models: "sonnet"
    if isinstance(models, str):
        return models
    if not isinstance(models, dict):
        return ""
    # 完整格式：先查 phases 覆盖，再 fallback 到 default
    phase_upper = phase.upper()
    phases_map = models.get("phases") or {}
    if phase_upper in phases_map:
        return str(phases_map[phase_upper])
    return str(models.get("default", ""))


def render_prompt(template: str, variables: dict[str, str]) -> str:
    """用 re.sub 替换 {{var}}，未知变量 raise ValueError"""

    def _replacer(m: re.Match[str]) -> str:
        var_name = m.group(1).strip()
        if var_name not in variables:
            raise ValueError(f"未知模板变量: {{{{{var_name}}}}}")
        return variables[var_name]

    return re.sub(r"\{\{(\s*\w+\s*)\}\}", _replacer, template)


# ---------------------------------------------------------------------------
# generate-workflow: 生成 workflow.yaml 模板
# ---------------------------------------------------------------------------

_WORKFLOW_TEMPLATE = """\
# openniuma/workflow.yaml — openNiuMa 运行时配置

project:
  name: "{name}"
  main_branch: {main_branch}
  dev_branch_prefix: "dev/backlog-batch"
  feat_branch_prefix: "feat"
  gate_command: |
    {gate_command}
  spec_dir: "docs/superpowers/specs"
  plan_dir: "docs/superpowers/plans"

hooks:
  after_create: |
    echo "TODO: customize after_create hook"
  before_remove: |
    echo "TODO: customize before_remove hook"
  timeout_sec: 120

polling:
  inbox_interval_sec: 60
workers:
  max_concurrent: 5
  stall_timeout_sec: 1800
  max_consecutive_failures: 3
retry:
  base_delay_sec: 10
  max_backoff_sec: 300
  rate_limit_default_wait_sec: 600
failure:
  max_retries_gate: 3
  max_retries_network: 2
  max_retries_context: 1
  max_retries_conflict: 2
  max_retries_permission: 1
  skip_on_unknown: true
worktree:
  base_dir: .trees
  prefix: loop
prompts:
  dir: openniuma/prompts
  common_rules: openniuma/prompts/_common-rules.md
notify:
  level: info
  macos: true
  bell: true
  feishu_webhook: ""
  suppress_window_sec: 300
  quiet_hours: ""
  aggregate_interval_sec: 300
  feishu_rate_limit_per_min: 10
models: sonnet
# models:                              # 或完整写法：
#   default: opus
#   phases:
#     VERIFY: sonnet
#     MERGE: sonnet
pause:
  auto_resume_sec: 3600
  partial: true
"""


def _generate_workflow(name: str, main_branch: str, gate_command: str) -> str:
    """生成 workflow.yaml 模板内容"""
    return _WORKFLOW_TEMPLATE.format(
        name=name,
        main_branch=main_branch,
        gate_command=gate_command,
    )


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------


def _cli(args: list[str] | None = None) -> None:
    """命令行入口"""
    if args is None:
        args = sys.argv[1:]

    if not args:
        print("用法: config.py <command> [args...]", file=sys.stderr)
        print("  export-env [yaml_path]", file=sys.stderr)
        print("  get-value <key> [yaml_path]", file=sys.stderr)
        print("  get-hook <hook_name> [yaml_path]", file=sys.stderr)
        print("  resolve-model <phase> [yaml_path]", file=sys.stderr)
        print("  render-prompt <prompt_path> [yaml_path]", file=sys.stderr)
        print("  validate [yaml_path]", file=sys.stderr)
        print("  generate-workflow --name X --main-branch X --gate-command X", file=sys.stderr)
        sys.exit(1)

    cmd = args[0]

    if cmd == "export-env":
        yaml_path = args[1] if len(args) > 1 else _DEFAULT_YAML
        for line in export_env(yaml_path):
            print(line)

    elif cmd == "get-value":
        if len(args) < 2:
            print("用法: config.py get-value <key> [yaml_path]", file=sys.stderr)
            sys.exit(1)
        key = args[1]
        yaml_path = args[2] if len(args) > 2 else _DEFAULT_YAML
        config = load_config(yaml_path)
        val = get_nested(config, key)
        if val is None:
            sys.exit(1)
        print(val)

    elif cmd == "get-hook":
        if len(args) < 2:
            print("用法: config.py get-hook <hook_name> [yaml_path]", file=sys.stderr)
            sys.exit(1)
        hook_name = args[1]
        yaml_path = args[2] if len(args) > 2 else _DEFAULT_YAML
        config = load_config(yaml_path)
        hook = get_nested(config, f"hooks.{hook_name}")
        if hook is None:
            print(f"Hook 不存在: {hook_name}", file=sys.stderr)
            sys.exit(1)
        print(hook, end="")

    elif cmd == "resolve-model":
        if len(args) < 2:
            print("用法: config.py resolve-model <phase> [yaml_path]", file=sys.stderr)
            sys.exit(1)
        phase = args[1]
        yaml_path = args[2] if len(args) > 2 else _DEFAULT_YAML
        model = resolve_model(phase, yaml_path)
        if model:
            print(model)

    elif cmd == "render-prompt":
        if len(args) < 2:
            print("用法: config.py render-prompt <prompt_path> [yaml_path]", file=sys.stderr)
            sys.exit(1)
        prompt_path = args[1]
        yaml_path = args[2] if len(args) > 2 else _DEFAULT_YAML
        config = load_config(yaml_path)
        template = Path(prompt_path).read_text(encoding="utf-8")
        # 从配置中提取常用变量
        variables: dict[str, str] = {}
        for env_name, config_key in _ENV_MAP.items():
            val = get_nested(config, config_key)
            if val is not None:
                variables[env_name] = str(val).strip()
        # 也加入项目级变量
        project = config.get("project", {})
        for k, v in project.items():
            variables[k] = str(v).strip() if v is not None else ""
        # 加载 common_rules 文件内容（如果配置了路径）
        common_rules_path = get_nested(config, "prompts.common_rules")
        if common_rules_path:
            cr_path = Path(common_rules_path)
            if cr_path.exists():
                # 先渲染 common_rules 自身的变量，再注入
                cr_template = cr_path.read_text(encoding="utf-8")
                variables["common_rules"] = render_prompt(cr_template, variables)
        print(render_prompt(template, variables))

    elif cmd == "validate":
        yaml_path = args[1] if len(args) > 1 else _DEFAULT_YAML
        config = load_config(yaml_path)
        errors = validate_config(config)
        if errors:
            print("配置校验失败:", file=sys.stderr)
            for e in errors:
                print(f"  - {e}", file=sys.stderr)
            sys.exit(1)
        else:
            print("配置校验通过")

    elif cmd == "generate-workflow":
        # 解析 --name, --main-branch, --gate-command
        name = "MyProject"
        main_branch = "main"
        gate_command = "echo ok"
        i = 1
        while i < len(args):
            if args[i] == "--name" and i + 1 < len(args):
                name = args[i + 1]
                i += 2
            elif args[i] == "--main-branch" and i + 1 < len(args):
                main_branch = args[i + 1]
                i += 2
            elif args[i] == "--gate-command" and i + 1 < len(args):
                gate_command = args[i + 1]
                i += 2
            else:
                i += 1
        print(_generate_workflow(name, main_branch, gate_command))

    else:
        print(f"未知命令: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    _cli()
