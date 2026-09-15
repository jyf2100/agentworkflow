#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_persona_retry.py — pa-hardening P1-1：persona 瞬态退避重试单测。

覆盖矩阵（方案 §P1-1）：
  * flag 门控：PA_PERSONA_RETRY off（默认）= 首次瞬态失败即上抛，零重试零睡眠（baseline 零行为变化）；
  * flag on：瞬态→退避 60s（±20%）→重试成功（不记失败、不触发熔断记账）；
  * flag on：重试耗尽（3 次调用：1+2 重试）→ 上抛 + SB.record_failure **恰好 1 次**（重试与熔断
    职责分离：退避熬抖动、熔断裁决趋势——每次逻辑调用只记一败，防 3 连试被误计 3 连败提前熔断）；
  * 非瞬态（is_error/契约类 RuntimeError）即使 flag on 也不重试（重试=重复烧钱）；
  * TransientPersonaError 是 RuntimeError 子类（既有 except RuntimeError 捕获面不变）；
  * jitter ±20% 边界；第二次退避 300s ±20%；
  * SB.check 抛 CircuitBreakerTripped → 干净穿透（不记 persona 失败账）；
  * 成功 → CK.add_cost 收到 meta['cost']（P2-1 记账面不因重试丢失）。

睡眠注入：_persona_sleep monkeypatch 记录不真睡（确定性）。跑：python3 -m pytest scripts/test_persona_retry.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import run_daily  # noqa: E402
import stage_breaker as SB  # noqa: E402


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """退避睡眠 → 记录器（测试不真睡）。"""
    sleeps: list[float] = []
    monkeypatch.setattr(run_daily, "_persona_sleep", lambda s: sleeps.append(s))
    monkeypatch.delenv("PA_PERSONA_RETRY", raising=False)
    return sleeps


def _fake_persona(monkeypatch, outcomes, payload=None, meta=None):
    """按序吐结果的假 run_persona：outcomes 元素为异常实例（抛）或 None（成功）。返回 calls 记录。"""
    calls: list[tuple] = []
    ok_payload = payload or {"ok": 1}
    ok_meta = meta if meta is not None else {"cost": 0.01}

    def fake(name, prompt, stage, label, allowed_tools=None):
        calls.append({"name": name, "stage": stage, "label": label, "tools": allowed_tools})
        o = outcomes[min(len(calls) - 1, len(outcomes) - 1)]
        if o is not None:
            raise o
        return ok_payload, ok_meta

    monkeypatch.setattr(run_daily, "run_persona", fake)
    return calls


def _t(msg: str) -> Exception:
    """运行时解析 TransientPersonaError 构造瞬态异常——**不可**模块级绑定：test_fetch_deepresearch
    会 ``importlib.reload(run_daily)``（同 module 对象重执行，全局名全部换新类身份），import 期绑定的
    旧类会让 wrapper 的 except / pytest.raises 双双失配（隔离教训）。"""
    return run_daily.TransientPersonaError(msg)


# ─── flag 门控：off = 零重试零睡眠 ─────────────────────────────────────────
def test_flag_off_first_transient_raises_immediately(monkeypatch, _no_sleep):
    calls = _fake_persona(monkeypatch, [_t("超时")])
    with pytest.raises(run_daily.TransientPersonaError):
        run_daily.run_persona_with_backoff("pa-prd", "p", "prd", "prd")
    assert len(calls) == 1                    # 零重试
    assert _no_sleep == []                    # 零睡眠


def test_transient_is_runtimeerror_subclass():
    """既有 except RuntimeError 捕获面不变（_run_pipeline exit 1 路径兼容）。"""
    assert issubclass(run_daily.TransientPersonaError, RuntimeError)


# ─── flag on：退避重试 ──────────────────────────────────────────────────────
def test_flag_on_transient_then_success(monkeypatch, _no_sleep):
    monkeypatch.setenv("PA_PERSONA_RETRY", "1")
    calls = _fake_persona(monkeypatch, [_t("超时"), None])
    payload, meta = run_daily.run_persona_with_backoff("pa-prd", "p", "prd", "prd")
    assert payload == {"ok": 1} and meta["cost"] == 0.01
    assert len(calls) == 2                    # 1 败 + 1 重试成功
    assert len(_no_sleep) == 1
    assert 48.0 <= _no_sleep[0] <= 72.0       # 60s ± 20%


def test_flag_on_exhaustion_after_three_calls(monkeypatch, _no_sleep):
    monkeypatch.setenv("PA_PERSONA_RETRY", "1")
    calls = _fake_persona(monkeypatch, [_t("超时")])
    with pytest.raises(run_daily.TransientPersonaError):
        run_daily.run_persona_with_backoff("pa-prd", "p", "prd", "prd")
    assert len(calls) == 3                    # 1 + 2 重试
    assert len(_no_sleep) == 2
    assert 48.0 <= _no_sleep[0] <= 72.0       # 60s ± 20%
    assert 240.0 <= _no_sleep[1] <= 360.0     # 300s ± 20%


def test_non_transient_never_retries_even_flag_on(monkeypatch, _no_sleep):
    """is_error/契约类失败（裸 RuntimeError）重试=重复烧钱 → 不重试。"""
    monkeypatch.setenv("PA_PERSONA_RETRY", "1")
    calls = _fake_persona(monkeypatch, [RuntimeError("is_error 业务失败")])
    with pytest.raises(RuntimeError):
        run_daily.run_persona_with_backoff("pa-prd", "p", "prd", "prd")
    assert len(calls) == 1
    assert _no_sleep == []


def test_record_failure_exactly_once_per_logical_call(monkeypatch, _no_sleep):
    """重试耗尽 → SB.record_failure 恰 1 次（防 3 连试被计 3 连败提前熔断——职责分离关键不变式）。"""
    monkeypatch.setenv("PA_PERSONA_RETRY", "1")
    _fake_persona(monkeypatch, [_t("超时")])
    fails: list[dict] = []
    monkeypatch.setattr(run_daily.SB, "record_failure",
                        lambda *a, **k: fails.append({"args": a, "kwargs": k}))
    with pytest.raises(run_daily.TransientPersonaError):
        run_daily.run_persona_with_backoff("pa-prd", "p", "prd", "prd")
    assert len(fails) == 1
    assert fails[0]["args"][2] == "prd"        # stage（args: state_dir, stamp, stage, label, err_kind, ...）
    assert fails[0]["args"][4] == "transient"  # err_kind


def test_breaker_check_raises_propagates_cleanly(monkeypatch, _no_sleep):
    """SB.check（熔断快速失败/重试前复查）抛 CircuitBreakerTripped → 穿透不进 persona 失败记账。"""
    calls = _fake_persona(monkeypatch, [None])

    def _boom(*a, **k):
        raise SB.CircuitBreakerTripped("已熔断")

    monkeypatch.setattr(run_daily.SB, "check", _boom)
    fails: list[tuple] = []
    monkeypatch.setattr(run_daily.SB, "record_failure", lambda *a, **k: fails.append(a))
    with pytest.raises(SB.CircuitBreakerTripped):
        run_daily.run_persona_with_backoff("pa-prd", "p", "prd", "prd")
    assert calls == [] and fails == []        # 未起调、未记账


def test_success_adds_cost_from_meta(monkeypatch, _no_sleep):
    """成功 → CK.add_cost 收到 meta['cost']（P2-1 记账面经 wrapper 统一，不因重试丢失）。"""
    _fake_persona(monkeypatch, [None], meta={"cost": 0.42})
    costs: list[tuple] = []
    monkeypatch.setattr(run_daily.CK, "add_cost", lambda *a, **k: costs.append(a))
    run_daily.run_persona_with_backoff("pa-prd", "p", "prd", "prd")
    assert len(costs) == 1
    assert costs[0][2] == "prd" and costs[0][3] == 0.42   # (state_dir, stamp, stage, amount)


def test_success_feeds_both_cost_ledgers(monkeypatch, _no_sleep):
    """双账不变式：成功一次，CK.add_cost（报告账）与 SB.add_cost（熔丝2 账）**都**收到 cost——
    干跑①暴露的缺口：漏喂 SB.add_cost 时 PA_CHECKPOINT off 的成本熔丝失聪。"""
    _fake_persona(monkeypatch, [None], meta={"cost": 0.5})
    ck_costs, sb_costs = [], []
    monkeypatch.setattr(run_daily.CK, "add_cost", lambda *a, **k: ck_costs.append(a))
    monkeypatch.setattr(run_daily.SB, "add_cost", lambda *a, **k: sb_costs.append(a))
    run_daily.run_persona_with_backoff("pa-prd", "p", "prd", "prd")
    assert len(ck_costs) == 1 and len(sb_costs) == 1
    assert ck_costs[0][3] == 0.5 and sb_costs[0][2] == 0.5   # CK(state,stamp,stage,amount) / SB(state,stamp,amount)


def test_allowed_tools_passthrough_shapes(monkeypatch, _no_sleep):
    """allowed_tools=None → 4 参调用形状；带 list → 5 参透传（保直通语义 + 既有 fake 兼容）。"""
    calls = _fake_persona(monkeypatch, [None, None])
    run_daily.run_persona_with_backoff("pa-fetch-github-repo", "p", "fetch", "fetch-gh")
    run_daily.run_persona_with_backoff("pa-fetch-github-repo", "p", "fetch", "fetch-gh",
                                       allowed_tools=["Bash"])
    assert calls[0]["tools"] is None
    assert calls[1]["tools"] == ["Bash"]


def test_jitter_within_20_percent():
    for base in (60.0, 300.0, 5.0):
        for _ in range(200):
            j = run_daily._jitter(base)
            assert base * 0.8 <= j <= base * 1.2
