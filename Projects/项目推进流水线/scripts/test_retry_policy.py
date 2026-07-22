#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_retry_policy.py — task 5.3 RetryPolicy 决策表 + task 5.6 独立预算单测。

覆盖 design L45-54 五决策的优先级与触发条件，以及 task 5.6 六维度独立预算耗尽。
七场景（task 5.7 的决策层）：transient resume / verifier-driven resume / alternative fork /
repeated-failure new session / missing session fallback / external-state block / exhausted budget。
AAA；模块零 SDK。跑：python3 -m pytest scripts/test_retry_policy.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import retry_policy as RP  # noqa: E402
from failure_analysis import FailureFingerprint, ProgressSignal  # noqa: E402
from session_meta import ExceptionClass as EC, ResultSubtype, SessionMeta  # noqa: E402

VS = RP.VerifierSignal


def _sess(**kw) -> SessionMeta:
    base = dict(iteration_id="i", session_id="s1", result_subtype=ResultSubtype.SUCCESS,
                exception_class=EC.NONE)
    base.update(kw)
    return SessionMeta(**base)


def _fresh_budget(**overrides) -> RP.BudgetState:
    lim_kw = dict(stop_continuations=3, sdk_retries=5, verify_iterations=2,
                  wall_clock_seconds=3600, turns_total=200, trusted_cost_usd=50.0)
    lim_kw.update(overrides)
    return RP.BudgetState(limits=RP.BudgetLimits(**lim_kw))


def _progress(changed=True, has_diff=True) -> ProgressSignal:
    return ProgressSignal(diff_changed=changed, test_changed=changed,
                          has_diff=has_diff, turns_delta=3 if changed else 0)


# ─── task 5.6：独立预算上限 ────────────────────────────────────────────────
def test_budget_not_exhausted_when_fresh():
    assert not _fresh_budget().exhausted


def test_budget_exhausted_per_independent_dimension():
    """六维度独立耗尽，任一触发即 stop（design risk#92 硬 kill 兜底）。"""
    cases = [
        ("stop_continuations", dict(stop_continuations_used=3)),
        ("sdk_retries", dict(sdk_retries_used=5)),
        ("verify_iterations", dict(verify_iterations_used=2)),
        ("wall_clock", dict(wall_clock_elapsed_s=3600)),
        ("turns", dict(turns_used=200)),
        ("trusted_cost", dict(trusted_cost_usd=50.0)),
    ]
    for name, kw in cases:
        b = RP.BudgetState(limits=RP.BudgetLimits(), **kw)
        assert b.exhausted, f"{name} 耗尽应触发 exhausted"
        assert name in b.exhaustion_reason


def test_budget_consume_is_immutable_and_accumulates():
    b0 = _fresh_budget()
    b1 = b0.consume(RP.BudgetDimension.SDK_RETRY)
    assert b0.sdk_retries_used == 0 and b1.sdk_retries_used == 1   # 不可变：原实例不变
    b2 = b1.consume(RP.BudgetDimension.SDK_RETRY, cost=1.5, turns=4, wall_s=60)
    assert b2.sdk_retries_used == 2 and b2.trusted_cost_usd == 1.5
    assert b2.turns_used == 4 and b2.wall_clock_elapsed_s == 60


def test_budget_partial_exhaustion_not_stopped():
    b = RP.BudgetState(limits=RP.BudgetLimits(), sdk_retries_used=4)   # 4/5 未耗尽
    assert not b.exhausted


# ─── task 5.3：决策优先级与五模式 ─────────────────────────────────────────
def test_stop_when_budget_exhausted_has_highest_priority():
    """预算耗尽优先于一切（即使 session 缺失/外部未知）→ STOP。"""
    b = RP.BudgetState(limits=RP.BudgetLimits(), sdk_retries_used=5)   # 耗尽
    d = RP.decide(budget=b, session=None, fingerprint=None, progress=None, external_known=False)
    assert d.mode is RP.RetryMode.STOP and not d.consumes_retry
    assert d.policy_version == RP.POLICY_VERSION


def test_block_when_external_source_unknown_does_not_consume_retry():
    """外部真源未知 → BLOCK，不消耗 retry（design L53；先 reconcile 再决策）。"""
    d = RP.decide(budget=_fresh_budget(), session=_sess(), fingerprint=None,
                  progress=_progress(), external_known=False)
    assert d.mode is RP.RetryMode.BLOCK and not d.consumes_retry
    assert d.budget_dimension is None


def test_new_session_when_session_missing_fallback():
    """session metadata 缺失 → NEW_SESSION（5.7 missing session fallback 场景）。"""
    d = RP.decide(budget=_fresh_budget(), session=None, fingerprint=None, progress=None)
    assert d.mode is RP.RetryMode.NEW_SESSION
    assert "missing session" in d.reason


def test_new_session_when_context_corrupt():
    """上下文污染 → session 不可恢复 → NEW_SESSION（design L52/risk#91）。"""
    s = _sess(exception_class=EC.CONTEXT_CORRUPT)
    d = RP.decide(budget=_fresh_budget(), session=s, fingerprint=None, progress=_progress())
    assert d.mode is RP.RetryMode.NEW_SESSION
    assert "not resumable" in d.reason


def test_new_session_on_repeated_failure_with_no_progress():
    """重复相同失败指纹 + 停滞 → NEW_SESSION（5.7 repeated-failure 场景）。"""
    fp = FailureFingerprint.of(EC.UNKNOWN, "timeout", ["test_a"])
    stalled = ProgressSignal(diff_changed=False, test_changed=False, has_diff=True, turns_delta=0)
    d = RP.decide(budget=_fresh_budget(), session=_sess(), fingerprint=fp, progress=stalled,
                  failure_history=[fp])
    assert d.mode is RP.RetryMode.NEW_SESSION and "repeated failure" in d.reason


def test_fork_when_verifier_suggests_alternative():
    """verifier 建议换方案 → FORK（5.7 alternative fork 场景，design L51）。"""
    d = RP.decide(budget=_fresh_budget(), session=_sess(), fingerprint=None,
                  progress=_progress(), verifier_signal=VS.SUGGEST_ALTERNATIVE)
    assert d.mode is RP.RetryMode.FORK


def test_resume_on_transient_interruption():
    """临时 provider/transport 中断 + session 可用 → RESUME（5.7 transient resume 场景）。"""
    s = _sess(exception_class=EC.TRANSIENT)
    d = RP.decide(budget=_fresh_budget(), session=s, fingerprint=None, progress=_progress())
    assert d.mode is RP.RetryMode.RESUME and "transient" in d.reason


def test_resume_on_verifier_local_feedback_with_progress():
    """verifier 局部反馈 + 有进展 + session 可用 → RESUME（5.7 verifier-driven resume）。"""
    d = RP.decide(budget=_fresh_budget(), session=_sess(), fingerprint=None,
                  progress=_progress(), verifier_signal=VS.LOCAL_FEEDBACK)
    assert d.mode is RP.RetryMode.RESUME and "local feedback" in d.reason


def test_resume_default_when_session_healthy():
    """缺省：session 健康 + 无特殊信号 → RESUME（保守续跑）。"""
    d = RP.decide(budget=_fresh_budget(), session=_sess(), fingerprint=None, progress=_progress())
    assert d.mode is RP.RetryMode.RESUME and "default" in d.reason


def test_new_session_on_high_compaction_with_no_progress():
    """compaction 过高 + 停滞 → NEW_SESSION（保守防 compaction 固化错误上下文）。"""
    s = _sess(compaction_count=4)
    stalled = ProgressSignal(diff_changed=False, test_changed=False, has_diff=True, turns_delta=0)
    d = RP.decide(budget=_fresh_budget(), session=s, fingerprint=None, progress=stalled)
    assert d.mode is RP.RetryMode.NEW_SESSION and "compaction" in d.reason


# ─── decision 元数据：consumes_retry / budget_dimension（经 decide 真实产出）────────
def test_resume_fork_new_consume_sdk_retry_budget():
    """resume/fork/new_session 经 decide 产出 → consumes_retry=True + 扣 SDK_RETRY。"""
    d_resume = RP.decide(budget=_fresh_budget(), session=_sess(exception_class=EC.TRANSIENT),
                         fingerprint=None, progress=_progress())
    d_fork = RP.decide(budget=_fresh_budget(), session=_sess(), fingerprint=None,
                       progress=_progress(), verifier_signal=VS.SUGGEST_ALTERNATIVE)
    d_new = RP.decide(budget=_fresh_budget(), session=None, fingerprint=None, progress=None)
    for d in (d_resume, d_fork, d_new):
        assert d.consumes_retry is True and d.budget_dimension is RP.BudgetDimension.SDK_RETRY


def test_block_and_stop_do_not_consume_retry():
    """block/stop 经 decide 产出 → consumes_retry=False + 不扣预算维度。"""
    d_block = RP.decide(budget=_fresh_budget(), session=_sess(), fingerprint=None,
                        progress=_progress(), external_known=False)
    b_exhaust = RP.BudgetState(limits=RP.BudgetLimits(), sdk_retries_used=5)
    d_stop = RP.decide(budget=b_exhaust, session=_sess(), fingerprint=None, progress=None)
    for d in (d_block, d_stop):
        assert not d.consumes_retry and d.budget_dimension is None


# ─── 优先级顺序总览（STOP > BLOCK > session health > repeated > fork > transient > …）────
def test_priority_stop_beats_block_beats_new_session():
    """预算耗尽 > 外部未知 > session 缺失，逐层短路。"""
    b_exhaust = RP.BudgetState(limits=RP.BudgetLimits(), sdk_retries_used=5)
    assert RP.decide(budget=b_exhaust, session=None, fingerprint=None,
                     progress=None, external_known=False).mode is RP.RetryMode.STOP
    # 预算未耗尽 + 外部未知 → BLOCK（即使 session 缺失也先 BLOCK）
    assert RP.decide(budget=_fresh_budget(), session=None, fingerprint=None,
                     progress=None, external_known=False).mode is RP.RetryMode.BLOCK
    # 预算未耗尽 + 外部已知 + session 缺失 → NEW_SESSION
    assert RP.decide(budget=_fresh_budget(), session=None, fingerprint=None,
                     progress=None, external_known=True).mode is RP.RetryMode.NEW_SESSION


# ─── task 4.3：evidence/journal-integrity 阻塞显式输入 ──────────────────────
def test_block_when_evidence_integrity_block_does_not_consume_retry():
    """task 4.3：evidence-integrity 阻塞（green test evidence artifact 无法持久化/校验）
    → BLOCK，不消耗 retry（spec verified-publication「Test artifact write fails」：记
    integrity-block reason，不当 complete fresh evidence）。优先于 session/verifier。"""
    d = RP.decide(budget=_fresh_budget(), session=_sess(), fingerprint=None,
                  progress=_progress(), external_known=True, integrity_block="evidence_integrity")
    assert d.mode is RP.RetryMode.BLOCK and not d.consumes_retry
    assert d.budget_dimension is None
    assert "evidence" in d.reason


def test_block_when_journal_integrity_block_does_not_consume_retry():
    """task 4.3：journal-integrity 阻塞（malformed tail / reducer failure fail-closed）
    → BLOCK，不消耗 retry（spec「Complete malformed journal tail」+ durable-runtime
    「Reducer failure during driven mode」：fail-closed blocked，需运维 triage）。reason 含 journal。"""
    d = RP.decide(budget=_fresh_budget(), session=_sess(), fingerprint=None,
                  progress=_progress(), external_known=True, integrity_block="journal_integrity")
    assert d.mode is RP.RetryMode.BLOCK and not d.consumes_retry
    assert "journal" in d.reason


def test_integrity_block_priority_over_session_and_external():
    """task 4.3 优先级：预算耗尽 > integrity_block（BLOCK）> 外部未知 > session 健康。
    即使 session 可用 + external_known=True + verifier 建议换方案，integrity_block 仍 BLOCK——
    证据/日志不可信时 resume/fork/new 都无意义（喂进去的 recovery context 本身不可信）。"""
    # 预算耗尽仍最高（硬 kill）
    b_exhaust = RP.BudgetState(limits=RP.BudgetLimits(), sdk_retries_used=5)
    assert RP.decide(budget=b_exhaust, session=_sess(), fingerprint=None,
                     progress=_progress(), integrity_block="evidence_integrity").mode is RP.RetryMode.STOP
    # 预算未耗尽 + integrity_block → BLOCK（即使 session 健康 + external 已知 + 建议换方案）
    d = RP.decide(budget=_fresh_budget(), session=_sess(), fingerprint=None,
                  progress=_progress(), external_known=True,
                  verifier_signal=VS.SUGGEST_ALTERNATIVE, integrity_block="journal_integrity")
    assert d.mode is RP.RetryMode.BLOCK and not d.consumes_retry


def test_no_integrity_block_preserves_existing_decisions():
    """task 4.3 回归：integrity_block=None（默认）不影响原决策表（resume/fork/new/block/stop 不变）。"""
    d = RP.decide(budget=_fresh_budget(), session=_sess(exception_class=EC.TRANSIENT),
                  fingerprint=None, progress=_progress())
    assert d.mode is RP.RetryMode.RESUME
