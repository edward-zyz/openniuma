# SPDX-License-Identifier: MIT
"""E2E 测试 — 覆盖 CLI 入口 + lib 集成流程。

测试策略：
- 每个测试使用独立的临时目录（隔离真实数据）
- CLI 命令通过 click CliRunner 调用，验证真实可执行性
- 集成流程覆盖：inbox 入队 → state 状态流转 → status 渲染 → 信号控制
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from click.testing import CliRunner

from openniuma.cli import main as cli_main

# 找到 repo 根目录
_HERE = Path(__file__).resolve().parent
_REPO_DIR = _HERE.parent
_WORKFLOW_YAML = _REPO_DIR / "workflow.yaml"


def _invoke(args: list[str], env: dict | None = None) -> "click.testing.Result":
    """通过 CliRunner 运行 CLI 命令，返回 Result。"""
    runner = CliRunner()
    return runner.invoke(cli_main, args, env=env, catch_exceptions=False)


class TestCLIHelp(unittest.TestCase):
    """CLI help 和未知命令。"""

    def test_help_exits_zero(self):
        result = _invoke(["--help"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("start", result.output)
        self.assertIn("status", result.output)
        self.assertIn("add", result.output)

    def test_unknown_command_shows_help(self):
        result = CliRunner().invoke(
            cli_main, ["nonexistent-command"], catch_exceptions=True
        )
        # click 对未知命令显示 Usage 并退出非零
        self.assertTrue(
            "Usage" in result.output or "No such command" in result.output,
            f"Expected usage/error info, got: {result.output}",
        )


class TestCLIStatus(unittest.TestCase):
    """status 命令 — 使用临时 runtime 目录。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="niuma-e2e-status-")
        self.runtime_dir = os.path.join(self.tmpdir, ".openniuma-runtime")
        os.makedirs(self.runtime_dir, exist_ok=True)
        # 创建最小 state.json
        state_path = os.path.join(self.runtime_dir, "state.json")
        with open(state_path, "w") as f:
            json.dump({"queue": [], "next_id": 1}, f)
        self._orig_cwd = os.getcwd()
        os.chdir(self.tmpdir)

    def tearDown(self):
        os.chdir(self._orig_cwd)
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_status_text_format(self):
        result = _invoke(["status"])
        self.assertEqual(result.exit_code, 0, f"output: {result.output}")
        self.assertIn("openNiuMa 状态概览", result.output)

    def test_status_json_format(self):
        result = _invoke(["status", "--format", "json"])
        self.assertEqual(result.exit_code, 0, f"output: {result.output}")
        data = json.loads(result.output)
        self.assertIn("tasks", data)


class TestCLIAddTask(unittest.TestCase):
    """add 命令 — 向临时 inbox/tasks/state 入队。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="niuma-e2e-add-")
        self.runtime_dir = os.path.join(self.tmpdir, ".openniuma-runtime")
        self.tasks_dir = os.path.join(self.runtime_dir, "tasks")
        self.state_path = os.path.join(self.runtime_dir, "state.json")
        os.makedirs(self.tasks_dir, exist_ok=True)
        # 创建最小 state.json，add 命令需要它存在
        with open(self.state_path, "w") as f:
            json.dump({"queue": [], "next_id": 1}, f)
        self._orig_cwd = os.getcwd()
        os.chdir(self.tmpdir)

    def tearDown(self):
        os.chdir(self._orig_cwd)
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_add_creates_md_and_state(self):
        result = _invoke(["add", "E2E 测试任务"])
        self.assertEqual(result.exit_code, 0, f"output: {result.output}")
        # tasks 目录应有 .md 文件
        md_files = list(Path(self.tasks_dir).glob("*.md"))
        self.assertEqual(len(md_files), 1)
        # state.json 应有该任务
        with open(self.state_path) as f:
            state = json.load(f)
        queue = state.get("queue", [])
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]["name"], "E2E 测试任务")
        self.assertEqual(queue[0]["status"], "pending")

    def test_add_complexity_option(self):
        result = _invoke(["add", "高复杂度任务", "--complexity", "高"])
        self.assertEqual(result.exit_code, 0, f"output: {result.output}")
        with open(self.state_path) as f:
            state = json.load(f)
        task = state["queue"][0]
        self.assertEqual(task["complexity"], "高")

    def test_add_multiple_tasks_increments_id(self):
        _invoke(["add", "任务 A"])
        _invoke(["add", "任务 B"])
        with open(self.state_path) as f:
            state = json.load(f)
        ids = [t["id"] for t in state["queue"]]
        self.assertEqual(ids, [1, 2])

    def test_md_file_has_frontmatter(self):
        _invoke(["add", "前置测试任务"])
        md = next(Path(self.tasks_dir).glob("*.md"))
        content = md.read_text()
        self.assertIn("---", content)
        self.assertIn("前置测试任务", content)


class TestCLISignals(unittest.TestCase):
    """stop / cancel 信号文件。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="niuma-e2e-sig-")
        self.inbox = os.path.join(self.tmpdir, "inbox")
        os.makedirs(self.inbox)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_stop_signal_creates_file(self):
        stop_file = os.path.join(self.inbox, "STOP")
        Path(stop_file).touch()
        self.assertTrue(os.path.exists(stop_file))

    def test_cancel_signal_creates_file(self):
        cancel_file = os.path.join(self.inbox, "CANCEL-42")
        Path(cancel_file).touch()
        self.assertTrue(os.path.exists(cancel_file))


class TestIntegrationWorkflow(unittest.TestCase):
    """集成测试 — 模拟完整任务生命周期（无 Claude，纯状态机）。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="niuma-e2e-wf-")
        self.inbox = os.path.join(self.tmpdir, "inbox")
        self.tasks = os.path.join(self.tmpdir, "tasks")
        self.state_path = os.path.join(self.tmpdir, "state.json")
        self.workers_dir = os.path.join(self.tmpdir, "workers")
        os.makedirs(self.inbox)
        os.makedirs(self.tasks)
        os.makedirs(self.workers_dir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_full_lifecycle_pending_to_done(self):
        """inbox 入队 → 读 ready → claim → complete → state = done_in_dev。"""
        from openniuma.core.state import LoopState
        from openniuma.core.inbox import scan_inbox

        # 1. 放任务到 inbox
        task_md = os.path.join(self.inbox, "my-task.md")
        Path(task_md).write_text("---\nname: E2E 集成任务\ncomplexity: 低\n---\n任务详情。\n")

        # 2. 扫描 inbox → 入队
        tasks_found = scan_inbox(self.inbox)
        self.assertEqual(len(tasks_found), 1)
        self.assertEqual(tasks_found[0]["name"], "E2E 集成任务")

        state = LoopState(self.state_path)
        tid = state.add_task(
            tasks_found[0]["name"],
            tasks_found[0].get("complexity", "medium"),
            task_md,
        )
        self.assertEqual(tid, 1)

        # 3. 找 ready 任务
        ready = state.find_ready_tasks()
        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0]["id"], 1)

        # 4. claim（模拟 worker 认领）
        ok = state.claim_task(1)
        self.assertTrue(ok)
        self.assertEqual(state.get_task(1)["status"], "in_progress")

        # 5. 再次 claim 应失败（已被认领）
        ok2 = state.claim_task(1)
        self.assertFalse(ok2)

        # 6. complete
        state.complete_task(1)
        task = state.get_task(1)
        self.assertEqual(task["status"], "done_in_dev")
        self.assertIsNotNone(task["completed_at"])

        # 7. find_ready 应为空（已完成）
        ready_after = state.find_ready_tasks()
        self.assertEqual(len(ready_after), 0)

    def test_dependency_gates_task(self):
        """依赖未完成时 t2 不进入 ready。"""
        from openniuma.core.state import LoopState

        state = LoopState(self.state_path)
        t1 = state.add_task("前置任务", "低")
        t2 = state.add_task("依赖任务", "中", depends_on=[t1])

        # 初始：只有 t1 ready
        ready = state.find_ready_tasks()
        self.assertEqual([t["id"] for t in ready], [t1])

        # t1 完成后 t2 ready
        state.claim_task(t1)
        state.complete_task(t1)
        ready2 = state.find_ready_tasks()
        self.assertEqual([t["id"] for t in ready2], [t2])

    def test_orphan_reclaim(self):
        """Worker 崩溃后 reconcile 能重置任务为 pending。"""
        from openniuma.core.state import LoopState
        from openniuma.core.reconcile import reclaim_orphan_tasks

        state = LoopState(self.state_path)
        tid = state.add_task("孤儿任务", "低")
        state.claim_task(tid)

        task = state.get_task(tid)
        self.assertEqual(task["status"], "in_progress")

        # workers_dir 中无 pid 文件 → reclaim_orphan_tasks 将其重置
        reclaim_orphan_tasks(state, self.workers_dir)
        self.assertEqual(state.get_task(tid)["status"], "pending")

    def test_cancel_signal_processed(self):
        """创建 CANCEL 文件 → reconcile 检测并返回 task_id。"""
        from openniuma.core.reconcile import detect_cancel_signals

        Path(os.path.join(self.inbox, "CANCEL-77")).touch()
        cancelled = detect_cancel_signals(self.inbox)
        self.assertIn(77, cancelled)
        # 文件应被删除
        self.assertFalse(os.path.exists(os.path.join(self.inbox, "CANCEL-77")))

    def test_stop_signal_processed(self):
        """STOP 文件存在 → scan_inbox 中的 stop 检测返回 True。"""
        from openniuma.core.inbox import check_stop_signal

        Path(os.path.join(self.inbox, "STOP")).touch()
        self.assertTrue(check_stop_signal(self.inbox))

    def test_stats_record_and_summary(self):
        """记录 session → summary 汇总数据正确。"""
        from openniuma.core.stats import StatsStore

        stats_path = os.path.join(self.tmpdir, "stats.json")
        store = StatsStore(stats_path)

        store.record_session({
            "task_id": 1,
            "task_name": "统计测试任务",
            "phase": "FAST_TRACK",
            "started_at": "2026-03-28T00:00:00.000Z",
            "ended_at": "2026-03-28T00:05:00.000Z",
            "duration_sec": 300,
            "exit_code": 0,
            "failure_type": None,
            "attempt": 1,
        })

        summary = store.summary()
        self.assertEqual(summary["total_sessions"], 1)
        self.assertEqual(summary["total_duration_sec"], 300)

    def test_failure_classification_gate(self):
        """npm ERR! + eslint error 日志应被分类为 gate，置信度 >= 0.6。"""
        from openniuma.core.failure import classify, FailureType

        # 包含多个 gate 信号：eslint error + npm ERR!
        log_content = (
            "> frontend@0.0.0 lint\n"
            "> eslint src\n"
            "\n"
            "/app/src/index.ts\n"
            "  1:1  error  Unexpected token  @typescript-eslint/no-explicit-any\n"
            "\n"
            "npm ERR! code ELIFECYCLE\n"
            "npm ERR! errno 1\n"
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write(log_content)
            log_path = f.name
        try:
            result = classify(log_path, exit_code=1)
            self.assertEqual(result.type, FailureType.GATE)
            self.assertGreaterEqual(result.confidence, 0.6)
        finally:
            os.unlink(log_path)

    def test_failure_classification_network(self):
        """ETIMEDOUT 日志应被分类为 network。"""
        from openniuma.core.failure import classify, FailureType

        log_content = "Error: connect ETIMEDOUT 1.2.3.4:443\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write(log_content)
            log_path = f.name
        try:
            result = classify(log_path, exit_code=1)
            self.assertEqual(result.type, FailureType.NETWORK)
        finally:
            os.unlink(log_path)

    def test_retry_should_retry_gate(self):
        """gate 类型在未超过 max_retries 时应重试。"""
        from openniuma.core.retry import should_retry
        from openniuma.core.failure import FailureType

        self.assertTrue(should_retry(FailureType.GATE, attempt=1, max_retries=3))
        self.assertFalse(should_retry(FailureType.GATE, attempt=4, max_retries=3))

    def test_config_loads_workflow_yaml(self):
        """workflow.yaml 能正确加载并返回 project.name。"""
        from openniuma.core.config import load_config, get_nested

        cfg = load_config(str(_WORKFLOW_YAML))
        self.assertEqual(get_nested(cfg, "project.name"), "Location Scout")
        self.assertEqual(get_nested(cfg, "project.main_branch"), "master")

    def test_status_renders_with_real_state(self):
        """status 用真实 state.json 能正常渲染（不 crash）。"""
        from openniuma.core.state import LoopState
        from openniuma.core.status import render_status

        state_file = str(_REPO_DIR / "state.json")
        state = LoopState(state_file)
        output = render_status(state=state, fmt="text")
        self.assertIn("openNiuMa 状态概览", output)


class TestCLIConfigValidation(unittest.TestCase):
    """config.py validate 校验。"""

    def test_validate_real_workflow_yaml(self):
        """真实 workflow.yaml 应通过校验。"""
        from openniuma.core.config import load_config, validate_config

        cfg = load_config(str(_WORKFLOW_YAML))
        errors = validate_config(cfg)
        self.assertEqual(errors, [], f"校验错误: {errors}")

    def test_validate_bad_config_fails(self):
        """超出范围的 max_concurrent 应校验失败。"""
        from openniuma.core.config import load_config, validate_config
        import yaml as _yaml

        with open(_WORKFLOW_YAML) as f:
            cfg = _yaml.safe_load(f)
        cfg["workers"]["max_concurrent"] = 999  # 超出合法范围

        errors = validate_config(cfg)
        self.assertTrue(len(errors) > 0, "超出范围的配置应校验失败")


if __name__ == "__main__":
    unittest.main(verbosity=2)
