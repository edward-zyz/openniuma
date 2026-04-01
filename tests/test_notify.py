# SPDX-License-Identifier: MIT
"""NotifyManager 单元测试"""

import unittest
import time
from openniuma.notify.notify import NotifyManager


def _make_config(**overrides):
    """构建测试用配置，默认禁用 bell/macos 避免实际发送。"""
    notify = {
        "bell": False,
        "macos": False,
        "feishu_webhook": "",
        "suppress_window_sec": 300,
        "quiet_hours": "",
        "feishu_rate_limit_per_min": 10,
        "aggregate_interval_sec": 300,
    }
    notify.update(overrides)
    return {"notify": notify}


class TestNotifyBasic(unittest.TestCase):
    def test_send_records_to_log(self):
        """发 1 条通知，_sent_log 长度应为 1。"""
        mgr = NotifyManager(_make_config())
        mgr.send("info", "test title", "test body", task_id="t1")
        self.assertEqual(len(mgr._sent_log), 1)
        self.assertEqual(mgr._sent_log[0]["title"], "test title")


class TestNotifySuppression(unittest.TestCase):
    def test_duplicate_suppressed_within_window(self):
        """同 task_id + failure_type 发 3 次，_sent_log 长度应为 1。"""
        mgr = NotifyManager(_make_config(suppress_window_sec=300))
        for _ in range(3):
            mgr.send(
                "warn",
                "dup",
                "body",
                task_id="t1",
                failure_type="timeout",
            )
        self.assertEqual(len(mgr._sent_log), 1)

    def test_different_task_not_suppressed(self):
        """task_id=1 和 task_id=2 各发 1 次，_sent_log 长度应为 2。"""
        mgr = NotifyManager(_make_config(suppress_window_sec=300))
        mgr.send("warn", "a", "body", task_id="1", failure_type="err")
        mgr.send("warn", "b", "body", task_id="2", failure_type="err")
        self.assertEqual(len(mgr._sent_log), 2)


class TestNotifyQuietHours(unittest.TestCase):
    def test_quiet_hours_suppresses_non_critical(self):
        """quiet_hours 全天，info 级别应被抑制。"""
        mgr = NotifyManager(_make_config(quiet_hours="00:00-23:59"))
        mgr.send("info", "title", "body", task_id="t1")
        self.assertEqual(len(mgr._sent_log), 0)

    def test_critical_bypasses_quiet_hours(self):
        """quiet_hours 全天，critical 级别仍应发送。"""
        mgr = NotifyManager(_make_config(quiet_hours="00:00-23:59"))
        mgr.send("critical", "title", "body", task_id="t1")
        self.assertEqual(len(mgr._sent_log), 1)


class TestNotifyRateLimit(unittest.TestCase):
    def test_feishu_rate_limit(self):
        """feishu_webhook 非空，rate_limit=2，发 5 条 critical，feishu_sent 为 True 的 <= 2。"""
        mgr = NotifyManager(
            _make_config(
                feishu_webhook="https://example.com/hook",
                feishu_rate_limit_per_min=2,
            )
        )
        for i in range(5):
            mgr.send(
                "critical",
                f"title-{i}",
                "body",
                task_id=f"t{i}",
                failure_type="crash",
            )
        feishu_count = sum(1 for entry in mgr._sent_log if entry["feishu_sent"])
        self.assertLessEqual(feishu_count, 2)


if __name__ == "__main__":
    unittest.main()
