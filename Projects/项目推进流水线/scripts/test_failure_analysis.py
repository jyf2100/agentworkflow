#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_failure_analysis.py — task 5.2 failure fingerprint + progress signal 单测。

覆盖：error_pattern 去噪稳定性、test_signature 排序稳定性、FailureFingerprint.key 比对、
ProgressSignal 进展/停滞判定、diff_hash hunk 行号归一、is_repeated_failure 窗口判定。
AAA；模块零 SDK。跑：python3 -m pytest scripts/test_failure_analysis.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import failure_analysis as FA  # noqa: E402
from session_meta import ExceptionClass as EC  # noqa: E402

TRANS = EC.TRANSIENT
CORRUPT = EC.CONTEXT_CORRUPT


# ─── error_pattern：去噪稳定性（同根因→同 pattern）────────────────────────
def test_error_pattern_strips_paths_lines_numbers():
    """同根因、不同路径/行号/数字 → 同 pattern。"""
    a = FA.error_pattern("TypeError: 'NoneType' object at /a/b.py:42 got 99")
    b = FA.error_pattern("TypeError: 'NoneType' object at /c/d.py:99 got 7")
    assert a == b and a != "none"


def test_error_pattern_different_root_cause_differs():
    assert FA.error_pattern("KeyError: missing 'foo'") != FA.error_pattern("TimeoutError: dial tcp")


def test_error_pattern_empty_is_none():
    assert FA.error_pattern("") == "none"
    assert FA.error_pattern("   ") == "none"


def test_error_pattern_ignores_timestamps_and_hashes():
    a = FA.error_pattern("crash 2026-07-21T08:00:00 ref deadbeefcafebabe")
    b = FA.error_pattern("crash 2025-01-02T01:02:03 ref 1234567890abcdef")
    assert a == b   # 时间戳/hash（合法十六进制 8+）抹掉，根因词 "crash" 一致


# ─── test_signature：排序稳定性 ────────────────────────────────────────────
def test_test_signature_order_independent():
    assert FA.test_signature(["test_b", "test_a"]) == FA.test_signature(["test_a", "test_b"])


def test_test_signature_none_when_empty():
    assert FA.test_signature([]) is None
    assert FA.test_signature(None) is None
    assert FA.test_signature(["", "  "]) is None


def test_test_signature_differs_for_different_sets():
    assert FA.test_signature(["a", "b"]) != FA.test_signature(["a", "c"])


# ─── FailureFingerprint.key：重复失败比对键 ────────────────────────────────
def test_fingerprint_key_stable_across_noise():
    """同 exception class + 同根因（不同路径行号）+ 同失败测试 → 同 key = 重复失败。"""
    f1 = FA.FailureFingerprint.of(TRANS, "connection reset at /x/y.py:1", ["test_a", "test_b"])
    f2 = FA.FailureFingerprint.of(TRANS, "connection reset at /p/q.py:99", ["test_b", "test_a"])
    assert f1.key == f2.key


def test_fingerprint_key_differs_across_exception_class():
    a = FA.FailureFingerprint.of(TRANS, "timeout")
    b = FA.FailureFingerprint.of(CORRUPT, "timeout")
    assert a.key != b.key


# ─── ProgressSignal：进展 / 停滞判定 ───────────────────────────────────────
def test_progress_diff_change_is_progress():
    sig = FA.progress(prev_diff_hash="aaa", cur_diff_hash="bbb",
                      prev_test_sig=None, cur_test_sig=None, prev_turns=3, cur_turns=5)
    assert sig.diff_changed and sig.making_progress and not sig.stalled
    assert sig.turns_delta == 2


def test_progress_same_diff_same_test_is_stalled():
    sig = FA.progress(prev_diff_hash="aaa", cur_diff_hash="aaa",
                      prev_test_sig="t1", cur_test_sig="t1", prev_turns=5, cur_turns=5)
    assert sig.stalled and not sig.making_progress


def test_progress_test_change_without_diff_is_progress():
    """无新 diff 但测试签名变化（如修了测试本身）仍算进展。"""
    sig = FA.progress(prev_diff_hash=None, cur_diff_hash=None,
                      prev_test_sig="t1", cur_test_sig="t2", prev_turns=2, cur_turns=2)
    assert sig.making_progress and not sig.diff_changed


def test_progress_first_iteration_with_diff_is_progress():
    """首次 iteration（prev 全 None）有 diff 即进展。"""
    sig = FA.progress(prev_diff_hash=None, cur_diff_hash="new",
                      prev_test_sig=None, cur_test_sig=None, prev_turns=0, cur_turns=4)
    assert sig.has_diff and sig.making_progress


def test_progress_no_diff_no_test_is_stalled():
    sig = FA.progress(prev_diff_hash=None, cur_diff_hash=None,
                      prev_test_sig=None, cur_test_sig=None, prev_turns=0, cur_turns=0)
    assert sig.stalled


# ─── diff_hash：hunk 行号归一 ──────────────────────────────────────────────
def test_diff_hash_normalizes_hunk_headers():
    """同改动、不同上下文行号 → 同 diff hash（防假「diff 变化」）。"""
    d1 = "@@ -10,3 +10,3 @@\n-old\n+new\n"
    d2 = "@@ -500,3 +500,3 @@\n-old\n+new\n"
    assert FA.diff_hash(d1) == FA.diff_hash(d2)


def test_diff_hash_none_for_empty():
    assert FA.diff_hash(None) is None
    assert FA.diff_hash("") is None
    assert FA.diff_hash("   \n  ") is None


def test_diff_hash_differs_for_different_content():
    assert FA.diff_hash("+a\n") != FA.diff_hash("+b\n")


# ─── is_repeated_failure：窗口判定（new_session 触发）──────────────────────
def test_is_repeated_failure_detects_recent_repeat():
    cur = FA.FailureFingerprint.of(TRANS, "timeout")
    hist = [FA.FailureFingerprint.of(CORRUPT, "x"), cur]
    assert FA.is_repeated_failure(hist, cur, window=2) is True


def test_is_repeated_failure_outside_window_is_false():
    cur = FA.FailureFingerprint.of(TRANS, "timeout")
    hist = [cur, FA.FailureFingerprint.of(CORRUPT, "x"), FA.FailureFingerprint.of(CORRUPT, "y")]
    assert FA.is_repeated_failure(hist, cur, window=2) is False   # 超出窗口


def test_is_repeated_failure_empty_history_false():
    cur = FA.FailureFingerprint.of(TRANS, "timeout")
    assert FA.is_repeated_failure([], cur) is False
    assert FA.is_repeated_failure(None, cur) is False
