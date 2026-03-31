"""通知发送 + 抑制/聚合 [MF-5]"""

import subprocess
import time
import json
from datetime import datetime


class NotifyManager:
    def __init__(self, config: dict):
        """config 是完整配置 dict（含 notify 键）"""
        self.config = config.get("notify", {})
        self.suppress_window = self.config.get("suppress_window_sec", 300)
        self.quiet_hours = self._parse_quiet_hours(self.config.get("quiet_hours", ""))
        self.feishu_rate_limit = self.config.get("feishu_rate_limit_per_min", 10)
        self.aggregate_interval = self.config.get("aggregate_interval_sec", 300)

        # 内部状态
        self._recent: dict[str, float] = {}  # dedup_key -> last_sent_time
        self._suppressed_counts: dict[str, int] = {}  # dedup_key -> count
        self._feishu_sent_times: list[float] = []
        self._sent_log: list[dict] = []  # 实际发送记录（用于测试）

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def send(
        self,
        level: str,
        title: str,
        body: str,
        task_id: str | None = None,
        failure_type: str | None = None,
    ) -> None:
        """发送通知，内部处理静默时段、去重、限流。"""
        # 1. 静默时段检查：非 critical 直接跳过
        if level != "critical" and self._in_quiet_hours():
            return

        # 2. 去重：窗口内重复 return
        dedup_key = f"{task_id}:{failure_type}:{level}"
        now = time.time()
        last_sent = self._recent.get(dedup_key)
        if last_sent is not None and (now - last_sent) < self.suppress_window:
            self._suppressed_counts[dedup_key] = (
                self._suppressed_counts.get(dedup_key, 0) + 1
            )
            return

        # 3. 发送
        self._recent[dedup_key] = now
        self._dispatch(level, title, body)

    # ------------------------------------------------------------------
    # 静默时段
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_quiet_hours(s: str) -> tuple[int, int, int, int] | None:
        """解析 'HH:MM-HH:MM' 格式，返回 (sh, sm, eh, em) 或 None。"""
        if not s or "-" not in s:
            return None
        try:
            start_str, end_str = s.split("-", 1)
            sh, sm = (int(x) for x in start_str.strip().split(":"))
            eh, em = (int(x) for x in end_str.strip().split(":"))
            return (sh, sm, eh, em)
        except (ValueError, TypeError):
            return None

    def _in_quiet_hours(self) -> bool:
        """检查当前时间是否在静默时段内。"""
        if self.quiet_hours is None:
            return False
        sh, sm, eh, em = self.quiet_hours
        now = datetime.now()
        cur = now.hour * 60 + now.minute
        start = sh * 60 + sm
        end = eh * 60 + em
        if start <= end:
            return start <= cur < end
        else:
            # 跨午夜，如 22:00-06:00
            return cur >= start or cur < end

    # ------------------------------------------------------------------
    # 分发
    # ------------------------------------------------------------------

    def _dispatch(self, level: str, title: str, body: str) -> None:
        """实际分发通知到各渠道。"""
        feishu_sent = False

        # bell
        if self.config.get("bell"):
            print("\a", end="", flush=True)

        # macOS 通知
        if self.config.get("macos"):
            self._send_macos_notification(title, body)

        # 飞书 webhook
        webhook = self.config.get("feishu_webhook", "")
        if webhook:
            if self._check_feishu_rate_limit():
                self._send_feishu(webhook, level, title, body)
                self._feishu_sent_times.append(time.time())
                feishu_sent = True

        self._sent_log.append(
            {
                "level": level,
                "title": title,
                "body": body,
                "feishu_sent": feishu_sent,
                "ts": time.time(),
            }
        )

    # ------------------------------------------------------------------
    # 飞书限流
    # ------------------------------------------------------------------

    def _check_feishu_rate_limit(self) -> bool:
        """清理 60 秒前的记录，检查是否超限。"""
        now = time.time()
        cutoff = now - 60
        self._feishu_sent_times = [
            t for t in self._feishu_sent_times if t > cutoff
        ]
        return len(self._feishu_sent_times) < self.feishu_rate_limit

    # ------------------------------------------------------------------
    # 渠道实现
    # ------------------------------------------------------------------

    @staticmethod
    def _send_macos_notification(title: str, body: str) -> None:
        """通过 osascript 发送 macOS 通知。"""
        try:
            escaped_title = title.replace('"', '\\"')
            escaped_body = body.replace('"', '\\"')
            subprocess.run(
                [
                    "osascript",
                    "-e",
                    f'display notification "{escaped_body}" with title "{escaped_title}"',
                ],
                capture_output=True,
                timeout=5,
            )
        except Exception:
            pass

    @staticmethod
    def _send_feishu(webhook: str, level: str, title: str, body: str) -> None:
        """通过 curl 发送飞书 webhook 消息。"""
        try:
            payload = json.dumps(
                {
                    "msg_type": "text",
                    "content": {"text": f"[{level}] {title}\n{body}"},
                }
            )
            subprocess.run(
                [
                    "curl",
                    "-s",
                    "-X",
                    "POST",
                    "-H",
                    "Content-Type: application/json",
                    "-d",
                    payload,
                    webhook,
                ],
                capture_output=True,
                timeout=10,
            )
        except Exception:
            pass
