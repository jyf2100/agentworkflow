#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""failure_analysis.py — task 5.2 normalized failure fingerprint + progress signal。

RetryPolicy（task 5.3）的两大结构化输入（design 决策#3「策略只消费结构化失败分类」）：

    - **FailureFingerprint**：跨 iteration 稳定的失败指纹（exception class + normalized
      error pattern + 失败测试签名）。RetryPolicy 用它判「重复相同失败」→ new_session
      （design risk#91「Session resume 固化错误上下文」）。
    - **ProgressSignal**：本次 vs 上次的 diff/test 变化信号。有进展→可 resume/fork；
      原地打转 + 重复失败 → new_session。

指纹刻意抹掉易变噪声（文件路径、行号、时间戳、token 数），只留根因骨架，保证「同根因→同指纹」。
纯函数、纯 stdlib（cron 隔离友好）。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from session_meta import ExceptionClass

# ─── 归一化：抹掉易变噪声，只留根因骨架 ────────────────────────────────────
_PATH_RE = re.compile(r"(?:[/\\][\w.\-]+)+")          # 文件路径
_LINE_RE = re.compile(r":\d+", )                       # 行号 :42
_NUM_RE = re.compile(r"\b\d{2,}\b")                    # 2 位以上数字（保留 <10 的小枚举）
_HEX_RE = re.compile(r"\b[0-9a-f]{8,}\b")              # hash/十六进制
_TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}")  # 时间戳（兼容 lower 后的 t）
_WS_RE = re.compile(r"\s+")
# 只保留这些「根因词」类别——其余 token 丢弃，使指纹稳定
_KEEP_TOKEN_RE = re.compile(r"[a-z_][a-z_0-9]{2,}")
_STOPWORDS = frozenset({
    "the", "and", "for", "with", "from", "this", "that", "was", "were", "has",
    "have", "not", "but", "you", "are", "line", "file", "at", "in", "on", "to",
    "of", "an", "is", "be", "by", "object", "value", "string", "error",   # error 太泛，丢
})


def _short(digest_input: str, n: int = 12) -> str:
    return hashlib.sha256(digest_input.encode("utf-8")).hexdigest()[:n]


def error_pattern(message: str) -> str:
    """normalized error signature：去路径/行号/数字/时间戳/hash，留根因词，排序去重后短摘要。

    例：``TypeError: 'NoneType' at /a/b.py:42`` 与 ``TypeError: NoneType at /c/d.py:99``
    → 同 pattern（去路径行号后根因词一致）。"""
    if not message:
        return "none"
    s = message.lower()
    s = _TS_RE.sub(" ", s)
    s = _PATH_RE.sub(" ", s)
    s = _LINE_RE.sub(" ", s)
    s = _HEX_RE.sub(" ", s)
    s = _NUM_RE.sub(" ", s)
    tokens = {t for t in _KEEP_TOKEN_RE.findall(s) if t not in _STOPWORDS}
    return _short(" ".join(sorted(tokens))) if tokens else "none"


def test_signature(failing_test_names) -> str | None:
    """失败测试集合的稳定签名（排序去重 + sha256）。无失败测试→None。"""
    names = sorted({str(n).strip() for n in (failing_test_names or []) if str(n).strip()})
    if not names:
        return None
    return _short(":".join(names))


@dataclass(frozen=True)
class FailureFingerprint:
    """跨 iteration 稳定的失败指纹——RetryPolicy 判「重复相同失败」的键。"""
    exception_class: ExceptionClass
    error_pattern: str                          # normalized error signature
    test_failure_signature: str | None          # 失败测试签名；None=无测试失败

    @property
    def key(self) -> str:
        """稳定比对键：同 key = 同根因重复失败。"""
        return f"{self.exception_class.value}|{self.error_pattern}|{self.test_failure_signature or 'none'}"

    @classmethod
    def of(cls, exception_class: ExceptionClass, error_message: str = "",
           failing_test_names=None) -> "FailureFingerprint":
        return cls(exception_class=exception_class,
                   error_pattern=error_pattern(error_message),
                   test_failure_signature=test_signature(failing_test_names))


@dataclass(frozen=True)
class ProgressSignal:
    """本次 iteration vs 上次的进展信号（diff/test/turns 变化）。"""
    diff_changed: bool                          # diff hash 变化（有新改动）
    test_changed: bool                          # 测试结果签名变化
    has_diff: bool                              # 是否产出非空 diff
    turns_delta: int                            # 轮次增量 vs 上次（>0=有新工作）

    @property
    def making_progress(self) -> bool:
        """有进展：diff 变化 或 测试签名变化（非原地打转）。无 diff 且无变化→停滞。

        RetryPolicy：停滞 + 重复失败指纹 → new_session（design risk#91）。"""
        return self.diff_changed or self.test_changed

    @property
    def stalled(self) -> bool:
        """原地打转：有 iteration 但无 diff 进展且无测试变化。"""
        return not self.making_progress


def diff_hash(diff_content: str | None) -> str | None:
    """diff 内容的稳定 hash（None/空→None，表示无 diff）。喂 progress 比对。"""
    if not diff_content or not diff_content.strip():
        return None
    # 去除 hunk 行号（@@ -a,b +c,d @@）使「同改动不同上下文位置」归同 hash
    cleaned = re.sub(r"^@@[^@]+@@", "@@hunk@@", diff_content, flags=re.M)
    return _short(cleaned)


def progress(prev_diff_hash: str | None, cur_diff_hash: str | None,
             prev_test_sig: str | None, cur_test_sig: str | None,
             prev_turns: int, cur_turns: int) -> ProgressSignal:
    """从前后两次 diff/test/turns 算 ProgressSignal。纯函数。

    首次 iteration（prev 全 None）视为有进展（has_diff 即进展）。"""
    has_diff = cur_diff_hash is not None
    diff_changed = bool(cur_diff_hash) and cur_diff_hash != prev_diff_hash
    # 测试签名变化：任一非 None 且不同（首次 None→None 视为未变）
    test_changed = (cur_test_sig != prev_test_sig) and (cur_test_sig is not None or prev_test_sig is not None)
    return ProgressSignal(diff_changed=diff_changed, test_changed=test_changed,
                          has_diff=has_diff, turns_delta=max(0, cur_turns - prev_turns))


def is_repeated_failure(history, current: FailureFingerprint, *, window: int = 2) -> bool:
    """当前指纹是否在最近 ``window`` 次历史中重复出现（design risk#91 切 new_session 触发）。

    ``history``：按时间序的 FailureFingerprint 列表（旧→新）。重复 = 最近 window 次含与
    current.key 相同的指纹。"""
    if window < 1:
        return False
    recent = list(history or [])[-window:]
    return any(fp.key == current.key for fp in recent)
