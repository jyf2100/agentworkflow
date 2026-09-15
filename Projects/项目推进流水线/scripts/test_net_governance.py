#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_net_governance.py — pa-hardening P1-2：对账网络调用统一治理单测。

覆盖矩阵（方案 §P1-2）：
  * P1-2a 信号量：PA_NET_CONCURRENCY unset/非法/<1 → None=不限流（baseline 零行为变化，与方案
    「默认 2」的差异见实施报告——任务红线 flag off=零变化优先）；合法值缓存（同值同实例、换值换新）；
    _net_gate 并发上限真压测（6 线程 × sem=2 → 峰值并发 == 2）；
  * P1-2b UNKNOWN 退避重试：PA_NET_STRICT off=单查零睡眠；on → UNKNOWN 睡 5s 重查、再 UNKNOWN 睡
    15s 三查；重试后 FOUND（瞬态自愈）→ 返回成功结果（不误阻断）；非 UNKNOWN（NOT_FOUND）不重试；
    耗尽仍 UNKNOWN → 原样返回（交准入既有 blocked_external_state——「UNKNOWN≠成功」不变式）；
  * _env_truthy 真值集合。

睡眠注入：_net_sleep monkeypatch 记录不真睡（确定性；并发压测的 fn 内小睡是真睡 80ms，量级可控）。
跑：python3 -m pytest scripts/test_net_governance.py -q
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import run_daily  # noqa: E402
from external_state import ExtState, found, not_found, unknown  # noqa: E402


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """P1-2b 查询退避 → 记录器（测试不真睡）+ flag 隔离。"""
    sleeps: list[float] = []
    monkeypatch.setattr(run_daily, "_net_sleep", lambda s: sleeps.append(s))
    monkeypatch.delenv("PA_NET_STRICT", raising=False)
    monkeypatch.delenv("PA_NET_CONCURRENCY", raising=False)
    return sleeps


# ─── P1-2a：信号量 ─────────────────────────────────────────────────────────
def test_semaphore_unset_returns_none():
    assert run_daily._net_semaphore() is None          # baseline：不限流


def test_semaphore_invalid_values(monkeypatch):
    for bad in ("", "0", "-1", "abc", "1.5"):
        monkeypatch.setenv("PA_NET_CONCURRENCY", bad)
        assert run_daily._net_semaphore() is None, bad


def test_semaphore_cached_per_value(monkeypatch):
    monkeypatch.setenv("PA_NET_CONCURRENCY", "2")
    s2a = run_daily._net_semaphore()
    assert s2a is run_daily._net_semaphore()           # 同值同实例
    monkeypatch.setenv("PA_NET_CONCURRENCY", "3")
    s3 = run_daily._net_semaphore()
    assert s3 is not s2a and s3 is run_daily._net_semaphore()
    monkeypatch.delenv("PA_NET_CONCURRENCY")
    assert run_daily._net_semaphore() is None          # 摘 flag 即回落不限流


def test_net_gate_passthrough_without_flag():
    """unset → fn 直调（零额外开销零行为变化）。"""
    calls = []
    assert run_daily._net_gate(lambda: calls.append(1) or "r", "t") == "r"
    assert calls == [1]


def test_net_gate_enforces_concurrency_cap():
    """6 线程 × sem=2 → 峰值并发恰 2（真线程压测，fn 内 80ms 持锁窗口）。"""
    import os
    os.environ["PA_NET_CONCURRENCY"] = "2"
    try:
        active = peak = 0
        lock = threading.Lock()

        def fn():
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.08)
            with lock:
                active -= 1

        threads = [threading.Thread(target=lambda: run_daily._net_gate(fn, "t")) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert peak == 2                               # 压到上限但不破
    finally:
        os.environ.pop("PA_NET_CONCURRENCY", None)


# ─── P1-2b：UNKNOWN 退避重试 ────────────────────────────────────────────────
def test_retry_unknown_flag_off_single_call(_no_sleep):
    calls = []
    res = run_daily._retry_unknown(lambda: calls.append(1) or unknown("rc=1"), "inflight")
    assert res.state is ExtState.UNKNOWN
    assert len(calls) == 1 and _no_sleep == []         # 零重试零睡眠


def test_retry_unknown_recovers_after_backoff(monkeypatch, _no_sleep):
    """瞬态自愈：UNKNOWN → 睡 5s → FOUND（不误阻断——重试后成功不跳过）。"""
    monkeypatch.setenv("PA_NET_STRICT", "1")
    calls = []

    def fn():
        calls.append(1)
        return unknown("rc=1") if len(calls) == 1 else found(3)

    res = run_daily._retry_unknown(fn, "inflight")
    assert res.state is ExtState.FOUND and res.value == 3
    assert len(calls) == 2
    assert _no_sleep == [5.0]


def test_retry_unknown_exhausted_returns_unknown(monkeypatch, _no_sleep):
    """重试耗尽（1+2 查）→ 原样 UNKNOWN（交准入既有 blocked_external_state，UNKNOWN≠成功不变式）。"""
    monkeypatch.setenv("PA_NET_STRICT", "1")
    calls = []

    def fn():
        calls.append(1)
        return unknown(f"rc={len(calls)}")

    res = run_daily._retry_unknown(fn, "branch_protection")
    assert res.state is ExtState.UNKNOWN and "rc=3" in res.reason   # 最后一次的结果
    assert len(calls) == 3
    assert _no_sleep == [5.0, 15.0]


def test_retry_unknown_non_unknown_no_retry(monkeypatch, _no_sleep):
    """可决断态（NOT_FOUND）不重试——重试只滤「态不明」，不碰明确结论。"""
    monkeypatch.setenv("PA_NET_STRICT", "1")
    calls = []
    res = run_daily._retry_unknown(lambda: calls.append(1) or not_found("未保护"), "branch_protection")
    assert res.state is ExtState.NOT_FOUND
    assert len(calls) == 1 and _no_sleep == []


# ─── env 真值 ───────────────────────────────────────────────────────────────
def test_env_truthy_values(monkeypatch):
    for on_v in ("1", "true", "YES", " on "):
        monkeypatch.setenv("X_TEST_FLAG", on_v)
        assert run_daily._env_truthy("X_TEST_FLAG") is True, on_v
    for off_v in ("", "0", "false", "no", "off"):
        monkeypatch.setenv("X_TEST_FLAG", off_v)
        assert run_daily._env_truthy("X_TEST_FLAG") is False, off_v
    monkeypatch.delenv("X_TEST_FLAG", raising=False)
    assert run_daily._env_truthy("X_TEST_FLAG") is False
