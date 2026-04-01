# SPDX-License-Identifier: MIT
"""retry.py -- 重试策略模块，根据失败类型计算延迟和是否重试。

纯标准库，无外部依赖。
"""

from __future__ import annotations

import random
import sys

from .failure import FailureType


def compute_delay(
    failure_type: FailureType,
    attempt: int,
    base: float = 10,
    max_backoff: float = 300,
) -> float:
    """根据失败类型和尝试次数计算重试延迟（秒）。

    - GATE: 指数退避 + jitter
    - NETWORK: 固定 60s + 小抖动
    - CONTEXT: 立即（仅小抖动）
    - PERMISSION: 立即（仅小抖动）
    - CONFLICT: 短延迟 10s + 小抖动
    - UNKNOWN: 0（不重试）
    """
    if failure_type == FailureType.GATE:
        return min(base * 2 ** (attempt - 1), max_backoff) + random.uniform(0, base)
    elif failure_type == FailureType.NETWORK:
        return 60 + random.uniform(0, 5)
    elif failure_type == FailureType.CONTEXT:
        return random.uniform(0, 5)
    elif failure_type == FailureType.PERMISSION:
        return random.uniform(0, 5)
    elif failure_type == FailureType.CONFLICT:
        return 10 + random.uniform(0, 5)
    elif failure_type == FailureType.UNKNOWN:
        return 0
    else:
        return 0


def should_retry(
    failure_type: FailureType,
    attempt: int,
    max_retries: int,
) -> bool:
    """判断是否应该重试。

    - UNKNOWN: 永远不重试
    - 其他: attempt <= max_retries 时重试
    """
    if failure_type == FailureType.UNKNOWN:
        return False
    return attempt <= max_retries


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(
            f"Usage: python3 {sys.argv[0]} <failure_type> <attempt> [base] [max_backoff]",
            file=sys.stderr,
        )
        sys.exit(1)

    ft = FailureType(sys.argv[1])
    att = int(sys.argv[2])
    b = float(sys.argv[3]) if len(sys.argv) > 3 else 10
    mb = float(sys.argv[4]) if len(sys.argv) > 4 else 300

    delay = compute_delay(ft, att, base=b, max_backoff=mb)
    print(delay)
