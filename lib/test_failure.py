"""test_failure.py -- failure.py 的单元测试。"""

from __future__ import annotations

import os
import tempfile
import unittest

from openniuma.lib.failure import FailureType, classify


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _make_log(content: str) -> str:
    """创建临时 log 文件，返回路径。调用方负责清理。"""
    fd, path = tempfile.mkstemp(suffix=".log", prefix="failure_test_")
    os.write(fd, content.encode())
    os.close(fd)
    return path


# ---------------------------------------------------------------------------
# 正例测试 — Network (6 tests)
# ---------------------------------------------------------------------------

class TestFailureNetwork(unittest.TestCase):

    def _classify(self, content: str, exit_code: int = 1):
        path = _make_log(content)
        try:
            return classify(path, exit_code)
        finally:
            os.unlink(path)

    def test_etimedout(self):
        r = self._classify("ERROR: connect ETIMEDOUT 1.2.3.4:443")
        self.assertEqual(r.type, FailureType.NETWORK)
        self.assertGreaterEqual(r.confidence, 0.3)

    def test_econnrefused(self):
        r = self._classify("Error: connect ECONNREFUSED 127.0.0.1:3000")
        self.assertEqual(r.type, FailureType.NETWORK)

    def test_rate_limit(self):
        r = self._classify("ERROR: API rate limit exceeded, retry after 60s")
        self.assertEqual(r.type, FailureType.NETWORK)

    def test_429(self):
        r = self._classify("ERROR: HTTP 429 Too Many Requests")
        self.assertEqual(r.type, FailureType.NETWORK)

    def test_socket_hang_up(self):
        r = self._classify("Error: socket hang up at TLSWrap.onStreamRead")
        self.assertEqual(r.type, FailureType.NETWORK)

    def test_exit_code_137(self):
        r = self._classify("some normal output\nno errors here", exit_code=137)
        self.assertEqual(r.type, FailureType.NETWORK)
        self.assertAlmostEqual(r.confidence, 0.9, places=2)


# ---------------------------------------------------------------------------
# 正例测试 — Gate (5 tests)
# ---------------------------------------------------------------------------

class TestFailureGate(unittest.TestCase):

    def _classify(self, content: str, exit_code: int = 1):
        path = _make_log(content)
        try:
            return classify(path, exit_code)
        finally:
            os.unlink(path)

    def test_npm_err(self):
        r = self._classify("npm ERR! code ELIFECYCLE\nnpm ERR! errno 1")
        self.assertEqual(r.type, FailureType.GATE)

    def test_eslint_error(self):
        r = self._classify("  eslint found 3 error(s) in src/App.tsx")
        self.assertEqual(r.type, FailureType.GATE)

    def test_tsc_error(self):
        r = self._classify("src/index.ts(5,3): tsc reports error TS2304: Cannot find name 'foo'.")
        self.assertEqual(r.type, FailureType.GATE)

    def test_fail_src(self):
        r = self._classify("FAIL src/utils/keywords.test.ts\n  Test Suites: 1 failed")
        self.assertEqual(r.type, FailureType.GATE)

    def test_npm_run_lint_exit(self):
        r = self._classify("npm run lint exit code 1")
        self.assertEqual(r.type, FailureType.GATE)


# ---------------------------------------------------------------------------
# 正例测试 — Context (3 tests)
# ---------------------------------------------------------------------------

class TestFailureContext(unittest.TestCase):

    def _classify(self, content: str, exit_code: int = 1):
        path = _make_log(content)
        try:
            return classify(path, exit_code)
        finally:
            os.unlink(path)

    def test_context_window(self):
        r = self._classify("ERROR: context window exceeded, cannot continue")
        self.assertEqual(r.type, FailureType.CONTEXT)

    def test_token_limit(self):
        r = self._classify("Error: token limit reached for this conversation")
        self.assertEqual(r.type, FailureType.CONTEXT)

    def test_conversation_too_long(self):
        r = self._classify("WARN: conversation is too long, please start a new one")
        self.assertEqual(r.type, FailureType.CONTEXT)


# ---------------------------------------------------------------------------
# 正例测试 — Permission (2 tests)
# ---------------------------------------------------------------------------

class TestFailurePermission(unittest.TestCase):

    def _classify(self, content: str, exit_code: int = 1):
        path = _make_log(content)
        try:
            return classify(path, exit_code)
        finally:
            os.unlink(path)

    def test_permission_denied(self):
        r = self._classify("ERROR: permission denied, open '/etc/shadow'")
        self.assertEqual(r.type, FailureType.PERMISSION)

    def test_eacces(self):
        r = self._classify("Error: EACCES: permission denied, mkdir '/usr/local/lib'")
        self.assertEqual(r.type, FailureType.PERMISSION)


# ---------------------------------------------------------------------------
# 正例测试 — Conflict (1 test)
# ---------------------------------------------------------------------------

class TestFailureConflict(unittest.TestCase):

    def _classify(self, content: str, exit_code: int = 1):
        path = _make_log(content)
        try:
            return classify(path, exit_code)
        finally:
            os.unlink(path)

    def test_merge_conflict(self):
        r = self._classify("CONFLICT (content): Merge conflict in src/App.tsx\nAutomatic merge failed")
        self.assertEqual(r.type, FailureType.CONFLICT)

    def test_string_conflict_no_trigger(self):
        """字符串中的 "CONFLICT" 不应触发 conflict 类型。"""
        r = self._classify('const msg = "CONFLICT happened"\nconsole.log(msg)')
        self.assertNotEqual(r.type, FailureType.CONFLICT)


# ---------------------------------------------------------------------------
# 反例测试 — False Positives
# ---------------------------------------------------------------------------

class TestFailureNetworkFalsePositive(unittest.TestCase):

    def _classify(self, content: str, exit_code: int = 1):
        path = _make_log(content)
        try:
            return classify(path, exit_code)
        finally:
            os.unlink(path)

    def test_comment_etimedout(self):
        """注释中的 ETIMEDOUT 不应匹配 network。"""
        r = self._classify("# ETIMEDOUT is a common error\nnpm ERR! code ELIFECYCLE")
        self.assertNotEqual(r.type, FailureType.NETWORK)

    def test_js_comment_etimedout(self):
        """JS 注释中的 ETIMEDOUT 不应匹配 network。"""
        r = self._classify("// ETIMEDOUT can happen when server is down\nnpm ERR! errno 1")
        self.assertNotEqual(r.type, FailureType.NETWORK)


# ---------------------------------------------------------------------------
# Unknown 测试 (3 tests)
# ---------------------------------------------------------------------------

class TestFailureUnknown(unittest.TestCase):

    def _classify(self, content: str, exit_code: int = 1):
        path = _make_log(content)
        try:
            return classify(path, exit_code)
        finally:
            os.unlink(path)

    def test_empty_log(self):
        r = self._classify("")
        self.assertEqual(r.type, FailureType.UNKNOWN)
        self.assertLess(r.confidence, 0.3)

    def test_irrelevant_content(self):
        r = self._classify("Hello world\nEverything is fine\nAll good")
        self.assertEqual(r.type, FailureType.UNKNOWN)
        self.assertLess(r.confidence, 0.3)

    def test_normal_output(self):
        r = self._classify("Starting server...\nListening on port 4000\nReady.")
        self.assertEqual(r.type, FailureType.UNKNOWN)
        self.assertLess(r.confidence, 0.3)


# ---------------------------------------------------------------------------
# 优先级测试 (2 tests)
# ---------------------------------------------------------------------------

class TestFailurePriority(unittest.TestCase):

    def _classify(self, content: str, exit_code: int = 1):
        path = _make_log(content)
        try:
            return classify(path, exit_code)
        finally:
            os.unlink(path)

    def test_network_over_gate(self):
        """当 network 和 gate 同时出现时，network 优先。"""
        content = "npm ERR! code ELIFECYCLE\nERROR: connect ETIMEDOUT 1.2.3.4:443"
        r = self._classify(content)
        self.assertEqual(r.type, FailureType.NETWORK)

    def test_error_context_confidence(self):
        """错误上下文行中的匹配应该有 >= 0.6 置信度。"""
        content = "INFO: starting build\nERROR: connect ECONNREFUSED 127.0.0.1:5432\nINFO: done"
        r = self._classify(content)
        self.assertGreaterEqual(r.confidence, 0.6)


if __name__ == "__main__":
    unittest.main()
