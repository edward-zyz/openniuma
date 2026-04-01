# SPDX-License-Identifier: MIT
"""test_retry.py -- retry 模块单元测试。"""

import unittest

from openniuma.core.failure import FailureType
from openniuma.core.retry import compute_delay, should_retry


class TestRetryDelay(unittest.TestCase):
    """compute_delay 各失败类型的延迟范围测试。"""

    def test_gate_exponential_backoff(self):
        # attempt=1: base * 2^0 = 10, + jitter [0, 10] → [10, 20]
        delay = compute_delay(FailureType.GATE, attempt=1)
        self.assertGreaterEqual(delay, 10)
        self.assertLessEqual(delay, 20)

        # attempt=3: base * 2^2 = 40, + jitter [0, 10] → [40, 50]
        delay = compute_delay(FailureType.GATE, attempt=3)
        self.assertGreaterEqual(delay, 40)
        self.assertLessEqual(delay, 50)

    def test_gate_capped_at_max(self):
        # attempt=20 远超 max_backoff=300, 结果应 <= 300 + 10 = 310
        delay = compute_delay(FailureType.GATE, attempt=20, max_backoff=300)
        self.assertLessEqual(delay, 310)

    def test_network_fixed_delay(self):
        delay = compute_delay(FailureType.NETWORK, attempt=1)
        self.assertGreaterEqual(delay, 60)
        self.assertLessEqual(delay, 65)

    def test_context_immediate(self):
        delay = compute_delay(FailureType.CONTEXT, attempt=1)
        self.assertLessEqual(delay, 5)

    def test_permission_immediate(self):
        delay = compute_delay(FailureType.PERMISSION, attempt=1)
        self.assertLessEqual(delay, 5)

    def test_conflict_short_delay(self):
        delay = compute_delay(FailureType.CONFLICT, attempt=1)
        self.assertGreaterEqual(delay, 10)
        self.assertLessEqual(delay, 15)

    def test_unknown_returns_zero(self):
        delay = compute_delay(FailureType.UNKNOWN, attempt=1)
        self.assertEqual(delay, 0)

    def test_jitter_is_random(self):
        # 10 次调用 GATE 应产生不同结果（jitter 随机性）
        results = {compute_delay(FailureType.GATE, attempt=1) for _ in range(10)}
        self.assertGreater(len(results), 1)


class TestShouldRetry(unittest.TestCase):
    """should_retry 重试判断测试。"""

    def test_within_limit(self):
        self.assertTrue(should_retry(FailureType.GATE, attempt=1, max_retries=3))
        self.assertTrue(should_retry(FailureType.GATE, attempt=3, max_retries=3))

    def test_exceeded_limit(self):
        self.assertFalse(should_retry(FailureType.GATE, attempt=4, max_retries=3))

    def test_unknown_never_retries(self):
        self.assertFalse(should_retry(FailureType.UNKNOWN, attempt=1, max_retries=3))


if __name__ == "__main__":
    unittest.main()
