"""failure.py -- 失败分类分析模块，分层匹配 + 置信度。

纯标准库，无外部依赖。
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import List, Optional, Tuple

# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

class FailureType(Enum):
    """失败类型枚举，按优先级从高到低排列。"""
    NETWORK = "network"
    CONTEXT = "context"
    PERMISSION = "permission"
    CONFLICT = "conflict"
    GATE = "gate"
    UNKNOWN = "unknown"


# 优先级顺序（高 → 低），UNKNOWN 不参与匹配
PRIORITY: List[FailureType] = [
    FailureType.NETWORK,
    FailureType.CONTEXT,
    FailureType.PERMISSION,
    FailureType.CONFLICT,
    FailureType.GATE,
]


@dataclass
class FailureResult:
    type: FailureType
    confidence: float  # 0.0 – 1.0
    evidence: str
    line_number: int

    def to_dict(self) -> dict:
        d = asdict(self)
        d["type"] = self.type.value
        return d


# ---------------------------------------------------------------------------
# 匹配规则
# ---------------------------------------------------------------------------

PATTERNS = {
    FailureType.NETWORK: {
        "keywords": [
            r"ETIMEDOUT",
            r"ECONNREFUSED",
            r"ECONNRESET",
            r"rate limit",
            r"429\b",
            r"503\b",
            r"socket hang up",
            r"network error",
        ],
        "excludes": [r"#.*ETIMEDOUT", r"//.*ETIMEDOUT"],
    },
    FailureType.CONTEXT: {
        "keywords": [
            r"context window",
            r"token limit",
            r"max.turns",
            r"conversation is too long",
            r"context length exceeded",
        ],
        "excludes": [],
    },
    FailureType.PERMISSION: {
        "keywords": [
            r"permission denied",
            r"dangerously-skip",
            r"EACCES",
            r"Operation not permitted",
        ],
        "excludes": [r"chmod", r"# permission"],
    },
    FailureType.CONFLICT: {
        "keywords": [
            r"CONFLICT\s+\(",
            r"merge conflict",
            r"Merge conflict in",
        ],
        "excludes": [r'["\']CONFLICT', r"#.*CONFLICT", r"//.*CONFLICT"],
    },
    FailureType.GATE: {
        "keywords": [
            r"npm run (lint|test|build).*exit",
            r"npm ERR!",
            r"FAIL\s+(src|test)/",
            r"eslint.*error",
            r"tsc.*error TS",
            r"error TS\d+",
        ],
        "excludes": [],
    },
}


# ---------------------------------------------------------------------------
# 内部工具函数
# ---------------------------------------------------------------------------

def _read_tail(log_path: str | Path, max_lines: int = 200) -> List[Tuple[int, str]]:
    """读取文件尾部 *max_lines* 行，返回 [(行号, 行内容), ...]。"""
    path = Path(log_path)
    all_lines = path.read_text(errors="replace").splitlines()
    start = max(0, len(all_lines) - max_lines)
    return [(start + i + 1, line) for i, line in enumerate(all_lines[start:])]


def _extract_error_context(
    lines: List[Tuple[int, str]],
) -> List[Tuple[int, str]]:
    """提取含 ERROR/FATAL/WARN/stderr 的行及前后 2 行。"""
    error_pattern = re.compile(r"(ERROR|FATAL|WARN|stderr)", re.IGNORECASE)
    indices: set[int] = set()
    for idx, (_, text) in enumerate(lines):
        if error_pattern.search(text):
            for offset in range(-2, 3):
                pos = idx + offset
                if 0 <= pos < len(lines):
                    indices.add(pos)
    return [lines[i] for i in sorted(indices)]


def _match_in_lines(
    lines: List[Tuple[int, str]],
    confidence_base: float,
) -> Optional[FailureResult]:
    """按优先级顺序匹配，从尾部向上搜索。先检查排除词。"""
    # 从尾部向前搜索
    reversed_lines = list(reversed(lines))

    for ftype in PRIORITY:
        rules = PATTERNS[ftype]
        keywords = [re.compile(k, re.IGNORECASE) for k in rules["keywords"]]
        excludes = [re.compile(e, re.IGNORECASE) for e in rules["excludes"]]

        for line_no, text in reversed_lines:
            # 检查是否被排除
            excluded = any(ex.search(text) for ex in excludes)
            if excluded:
                continue
            # 检查关键词
            for kw in keywords:
                m = kw.search(text)
                if m:
                    return FailureResult(
                        type=ftype,
                        confidence=confidence_base,
                        evidence=text.strip()[:200],
                        line_number=line_no,
                    )
    return None


# ---------------------------------------------------------------------------
# 核心入口
# ---------------------------------------------------------------------------

def classify(log_path: str | Path, exit_code: int) -> FailureResult:
    """分类日志中的失败原因，返回 FailureResult。"""
    lines = _read_tail(log_path, max_lines=200)

    # Layer 1: exit_code == 137 → NETWORK 0.9
    if exit_code == 137:
        return FailureResult(
            type=FailureType.NETWORK,
            confidence=0.9,
            evidence=f"exit_code={exit_code} (OOM/killed, likely network timeout)",
            line_number=lines[-1][0] if lines else 0,
        )

    # Layer 2: 错误上下文匹配 (confidence_base=0.8, 阈值>=0.6)
    error_ctx = _extract_error_context(lines)
    if error_ctx:
        result = _match_in_lines(error_ctx, confidence_base=0.8)
        if result and result.confidence >= 0.6:
            return result

    # Layer 3: 全文搜索 (confidence_base=0.4, 阈值>=0.3)
    result = _match_in_lines(lines, confidence_base=0.4)
    if result and result.confidence >= 0.3:
        return result

    # 都不匹配 → UNKNOWN
    return FailureResult(
        type=FailureType.UNKNOWN,
        confidence=0.0,
        evidence="",
        line_number=0,
    )


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(f"Usage: python3 {sys.argv[0]} <log_path> <exit_code>", file=sys.stderr)
        sys.exit(1)

    log_file = sys.argv[1]
    code = int(sys.argv[2])
    result = classify(log_file, code)
    print(json.dumps(result.to_dict(), indent=2))
