#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_learning_memory_effectiveness.py — add-cross-prd-learning-memory Section 6.1/6.2/6.4 单测。

锁定 effectiveness 评估的机械契约（spec design 决策#6「Close the loop with evidence-derived
usage outcomes」）：

    * **classify_outcome 6 类映射**：followed / recurrence_prevented / contradicted /
      recurrence_observed / not_observed / unknown—— 特别验证 ``evidence_available=False → unknown``
      （spec「Absence of a detectable action is recorded as unknown, not automatically as
      disobedience」）+ ``has_explicit_prevention_evidence`` 区分 followed vs recurrence_prevented +
      failure_recurred 优先于 prevention 信号。
    * **detect_action_observed V1 保守机械判定**：显式 bool 字段优先 / token 匹配 / failure_class
      匹配；任何模糊 → evidence_available=False（→ classify → unknown）。
    * **build_usage_outcome schema 校验**：经 ``UsageOutcome.__post_init__`` enforce outcome 受控词表。
    * **bounded confidence 常量值断言**：CONFIDENCE_UP_BOUND=0.1 / CONFIDENCE_DOWN_BOUND=0.2 /
      CONTRADICTION_RETIRE_THRESHOLD=2（catalog _apply_usage_outcomes 也用，必须稳定）。
    * **build_memory_mode_record 纯函数**：task 6.4 dispatch/report 字段构造（不改现有 record）。

spec design 决策#6 关键不变式（**全部由机械判定 own**）：
    * absent evidence → unknown（绝不当 disobedience）；
    * contradicted loses confidence；repeated contradiction retires；
    * retirement only changes projection, never deletes source facts（catalog 层负责）；
    * semantic synthesis 是 follow-up（V1 不调 SDK）。

跑：python3 -m pytest scripts/test_learning_memory_effectiveness.py -q
AAA 结构。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import learning_memory_effectiveness as LME  # noqa: E402
import learning_memory_schema as LM  # noqa: E402


# ════════════════════════════════════════════════════════════════════════
# task 6.2 常量值断言（catalog _apply_usage_outcomes 依赖其稳定）
# ════════════════════════════════════════════════════════════════════════
def test_confidence_up_bound_is_stable_0_1():
    """CONFIDENCE_UP_BOUND 必须 == 0.1（design 决策#6 + catalog 依赖）。"""
    assert LME.CONFIDENCE_UP_BOUND == 0.1


def test_confidence_down_bound_is_stable_0_2():
    """CONFIDENCE_DOWN_BOUND 必须 == 0.2（下调比上调重——保守原则）。"""
    assert LME.CONFIDENCE_DOWN_BOUND == 0.2


def test_contradiction_retire_threshold_is_2():
    """CONTRADICTION_RETIRE_THRESHOLD == 2（与 promotion 的 ≥2 PRD 阈值对齐）。"""
    assert LME.CONTRADICTION_RETIRE_THRESHOLD == 2


# ════════════════════════════════════════════════════════════════════════
# task 6.1：classify_outcome 6 类映射
# ════════════════════════════════════════════════════════════════════════
def test_classify_outcome_unknown_when_no_evidence_action_true():
    """spec 硬约束：evidence_available=False → unknown（绝不当 disobedience）。

    场景：action 可能观察到了，但没有足够结构性证据确认 → 仍记 unknown。
    """
    outcome = LME.classify_outcome(
        action_observed=True, failure_recurred=False,
        evidence_available=False, has_explicit_prevention_evidence=True)
    assert outcome == "unknown"


def test_classify_outcome_unknown_when_no_evidence_failure_true():
    """absent evidence 即使 failure 可能复现也记 unknown（保守：不能凭 absence 定罪）。"""
    outcome = LME.classify_outcome(
        action_observed=False, failure_recurred=True,
        evidence_available=False)
    assert outcome == "unknown"


def test_classify_outcome_followed_when_action_no_failure_no_prevention_evidence():
    """action 发生 + failure 未复现 + 无显式 prevention 证据 → followed（中性正面）。"""
    outcome = LME.classify_outcome(
        action_observed=True, failure_recurred=False,
        evidence_available=True, has_explicit_prevention_evidence=False)
    assert outcome == "followed"


def test_classify_outcome_recurrence_prevented_when_explicit_prevention_evidence():
    """action 发生 + failure 未复现 + 有显式 prevention 证据 → recurrence_prevented（强正面）。"""
    outcome = LME.classify_outcome(
        action_observed=True, failure_recurred=False,
        evidence_available=True, has_explicit_prevention_evidence=True)
    assert outcome == "recurrence_prevented"


def test_classify_outcome_recurrence_prevented_default_no_prevention_evidence():
    """has_explicit_prevention_evidence 默认 False → action+!failure → followed（非 prevented）。"""
    outcome = LME.classify_outcome(
        action_observed=True, failure_recurred=False,
        evidence_available=True)   # 默认 has_prevention=False
    assert outcome == "followed"


def test_classify_outcome_contradicted_when_action_and_failure():
    """action 发生但 failure 仍复现 → contradicted（prevention 失败）。"""
    outcome = LME.classify_outcome(
        action_observed=True, failure_recurred=True,
        evidence_available=True, has_explicit_prevention_evidence=True)
    assert outcome == "contradicted"


def test_classify_outcome_failure_recurred_prioritized_over_prevention_evidence():
    """failure_recurred=True 优先于 has_prevention_evidence：action+failure+prevention → contradicted。

    语义：若 failure 真复现，prevention 并未实际 prevent，应记 contradicted 而非 recurrence_prevented。
    """
    outcome = LME.classify_outcome(
        action_observed=True, failure_recurred=True,
        evidence_available=True, has_explicit_prevention_evidence=True)
    assert outcome == "contradicted"


def test_classify_outcome_recurrence_observed_when_no_action_and_failure():
    """action 未观察 + failure 复现 → recurrence_observed（强负面，但是 observation 不是 disobedience）。"""
    outcome = LME.classify_outcome(
        action_observed=False, failure_recurred=True,
        evidence_available=True)
    assert outcome == "recurrence_observed"


def test_classify_outcome_not_observed_when_no_action_no_failure():
    """action 未观察 + failure 未复现 → not_observed（中性）。"""
    outcome = LME.classify_outcome(
        action_observed=False, failure_recurred=False,
        evidence_available=True)
    assert outcome == "not_observed"


def test_classify_outcome_all_six_kinds_covered():
    """classify_outcome 必须能返回 UsageOutcomeKind 的所有 6 个合法 value（覆盖性断言）。"""
    seen = set()
    seen.add(LME.classify_outcome(action_observed=True, failure_recurred=False,
                                  evidence_available=True, has_explicit_prevention_evidence=True))
    seen.add(LME.classify_outcome(action_observed=True, failure_recurred=False,
                                  evidence_available=True, has_explicit_prevention_evidence=False))
    seen.add(LME.classify_outcome(action_observed=True, failure_recurred=True,
                                  evidence_available=True))
    seen.add(LME.classify_outcome(action_observed=False, failure_recurred=True,
                                  evidence_available=True))
    seen.add(LME.classify_outcome(action_observed=False, failure_recurred=False,
                                  evidence_available=True))
    seen.add(LME.classify_outcome(action_observed=True, failure_recurred=False,
                                  evidence_available=False))
    assert seen == {"recurrence_prevented", "followed", "contradicted",
                    "recurrence_observed", "not_observed", "unknown"}


def test_classify_outcome_returns_value_string_in_vocab():
    """所有返回值必须在 ``_VALID_USAGE_OUTCOMES | {"unknown"}`` 受控词表内。"""
    valid = set(LM._VALID_USAGE_OUTCOMES) | {"unknown"}
    for action in (True, False):
        for failure in (True, False):
            for ev in (True, False):
                for prev in (True, False):
                    out = LME.classify_outcome(
                        action_observed=action, failure_recurred=failure,
                        evidence_available=ev, has_explicit_prevention_evidence=prev)
                    assert out in valid, f"非法 outcome {out!r} for {(action, failure, ev, prev)}"


# ════════════════════════════════════════════════════════════════════════
# task 6.1：detect_action_observed（V1 保守机械判定）
# ════════════════════════════════════════════════════════════════════════
def test_detect_action_observed_explicit_bool_fields_win():
    """显式 ``action_observed`` / ``failure_recurred`` bool 字段优先于 token 启发式。"""
    lesson = {"corrective_action": "add failing test", "failure_class": "gate_blocked"}
    evidence = {"action_observed": True, "failure_recurred": False}
    action, failure, ev = LME.detect_action_observed(lesson, evidence)
    assert action is True
    assert failure is False
    assert ev is True   # 任一 True → evidence_available=True


def test_detect_action_observed_no_evidence_when_all_empty():
    """保守：空 terminal_evidence → 全 False → evidence_available=False → classify → unknown。"""
    lesson = {"corrective_action": "add failing test", "failure_class": "gate_blocked"}
    action, failure, ev = LME.detect_action_observed(lesson, {})
    assert action is False
    assert failure is False
    assert ev is False   # 关键：absent evidence → evidence_available=False


def test_detect_action_observed_failure_verdict_indicates_recurrence():
    """verifier_verdict=test_fail → failure_recurred=True（机械保守判定）。"""
    lesson = {"corrective_action": "add test", "failure_class": "verifier_invariant_violation"}
    evidence = {"verifier_verdict": "test_fail"}
    _, failure, _ = LME.detect_action_observed(lesson, evidence)
    assert failure is True


def test_detect_action_observed_failure_class_token_in_skip_reason():
    """failure_class token 出现在 skip_reason → failure_recurred=True。"""
    lesson = {"corrective_action": "x", "failure_class": "gate_blocked"}
    evidence = {"skip_reason": "deploy aborted: gate_blocked at publish step"}
    _, failure, _ = LME.detect_action_observed(lesson, evidence)
    assert failure is True


def test_detect_action_observed_action_tokens_in_diff():
    """corrective_action 显著 token 多数出现在 diff+test_log → action_observed=True。"""
    lesson = {"corrective_action": "add failing reproducing gate block"}
    # 4 tokens (add/failing/reproducing/gate/block - len>=4: failing/reproducing/gate/block = 4 tokens)
    # 多数 = >=3 命中
    evidence = {"diff": "gate block test added", "test_log": "failing reproducing assertion"}
    action, _, _ = LME.detect_action_observed(lesson, evidence)
    assert action is True


def test_detect_action_observed_conservative_when_token_minority():
    """保守：corrective_action token 仅少数命中 → action_observed=False。"""
    lesson = {"corrective_action": "completely unrelated different strange wording"}
    evidence = {"diff": "completely", "test_log": ""}   # 只命中 1/4
    action, _, _ = LME.detect_action_observed(lesson, evidence)
    assert action is False


def test_detect_action_observed_non_dict_inputs_return_all_false():
    """非 dict 输入 → (False, False, False)（防 TypeError）。"""
    assert LME.detect_action_observed(None, {}) == (False, False, False)
    assert LME.detect_action_observed({}, None) == (False, False, False)
    assert LME.detect_action_observed("x", "y") == (False, False, False)


def test_detect_action_observed_has_structured_evidence_flag():
    """显式 has_structured_evidence=True → evidence_available=True（即便 action/failure 都 False）。

    场景：调用方有结构性证据但本 V1 helper 看不懂——允许调用方显式标记有证据可用。
    """
    lesson = {"corrective_action": "x", "failure_class": "y"}
    evidence = {"has_structured_evidence": True}
    action, failure, ev = LME.detect_action_observed(lesson, evidence)
    assert action is False
    assert failure is False
    assert ev is True   # 显式标记 → evidence 可用（调用方再用别的方式判定 action/failure）


# ════════════════════════════════════════════════════════════════════════
# task 6.2：build_usage_outcome schema 校验
# ════════════════════════════════════════════════════════════════════════
def test_build_usage_outcome_constructs_schema_valid_record():
    """build_usage_outcome → UsageOutcome 实例（schema-valid，outcome 在受控词表）。"""
    u = LME.build_usage_outcome(
        event_id="evt-1", timestamp="2026-07-26T03:17:00Z",
        project_id="proj-a", lesson_id="lesson-xyz", prd_id="prd-001",
        action_observed=True, failure_recurred=False,
        outcome="followed",
        evidence_refs=({"digest": "sha256:a1", "kind": "verifier_verdict", "path": "p"},))
    assert isinstance(u, LM.UsageOutcome)
    assert u.event_id == "evt-1"
    assert u.outcome == "followed"
    assert u.action_observed is True
    assert u.failure_recurred is False
    assert len(u.evidence_refs) == 1


def test_build_usage_outcome_rejects_out_of_vocab_outcome():
    """outcome 不在受控词表 → schema __post_init__ raise（防手灌非法 outcome）。"""
    with pytest.raises(ValueError, match=r"outcome"):
        LME.build_usage_outcome(
            event_id="evt-1", timestamp="t", project_id="p", lesson_id="l", prd_id="prd",
            action_observed=True, failure_recurred=False,
            outcome="totally_made_up_outcome")


def test_build_usage_outcome_unknown_outcome_rejected_by_schema():
    """outcome="unknown" 也是非法（UsageOutcomeKind.UNKNOWN 不在 _VALID_USAGE_OUTCOMES）。

    UsageOutcome record 是「已判定」的事实——存进 storage 时必须已有合法 outcome。
    unknown 是 classify_outcome 的兜底返回值，但**不能直接灌进 storage**（调用方需先经
    classify_outcome 走完判定，再 build_usage_outcome；若仍是 unknown 也可写——只是
    outcome 字段必须是 ``UsageOutcomeKind.value``，而 ``"unknown"`` 在 ``_VALID_USAGE_OUTCOMES``
    外，会被 schema 拒）。
    """
    with pytest.raises(ValueError, match=r"outcome"):
        LME.build_usage_outcome(
            event_id="evt-1", timestamp="t", project_id="p", lesson_id="l", prd_id="prd",
            action_observed=False, failure_recurred=False,
            outcome="unknown")


def test_build_usage_outcome_default_evidence_refs_is_empty_tuple():
    """evidence_refs 默认 () （empty tuple，非 None）。"""
    u = LME.build_usage_outcome(
        event_id="e", timestamp="t", project_id="p", lesson_id="l", prd_id="prd",
        action_observed=True, failure_recurred=False, outcome="followed")
    assert u.evidence_refs == ()


def test_build_usage_outcome_classify_integration():
    """集成：detect_action_observed → classify_outcome → build_usage_outcome 端到端契约。"""
    lesson = {"corrective_action": "add failing test reproducing gate block"}
    evidence = {
        "verifier_verdict": "test_pass",
        "diff": "added gate block reproducing test",
        "test_log": "failing assertion added",
        "has_structured_evidence": True,
    }
    action, failure, ev = LME.detect_action_observed(lesson, evidence)
    outcome = LME.classify_outcome(
        action_observed=action, failure_recurred=failure,
        evidence_available=ev, has_explicit_prevention_evidence=True)
    u = LME.build_usage_outcome(
        event_id="evt-int", timestamp="2026-07-26T03:17:00Z",
        project_id="proj-a", lesson_id="lesson-1", prd_id="prd-001",
        action_observed=action, failure_recurred=failure, outcome=outcome)
    assert u.outcome in LM._VALID_USAGE_OUTCOMES
    assert u.action_observed == action
    assert u.failure_recurred == failure


# ════════════════════════════════════════════════════════════════════════
# task 6.4：build_memory_mode_record 纯函数
# ════════════════════════════════════════════════════════════════════════
def test_build_memory_mode_record_shadow_off_injection_off_baseline():
    """baseline：两个 flag 都 off → 全部字段 default/empty（无 memory 活动）。"""
    rec = LME.build_memory_mode_record(shadow_on=False, injection_on=False)
    assert rec["shadow_on"] is False
    assert rec["injection_on"] is False
    assert rec["selected_lesson_ids"] == ()
    assert rec["candidate_count"] == 0
    assert rec["promotion_count"] == 0
    assert rec["degraded_status"] is None


def test_build_memory_mode_record_shadow_on_injection_off_shadow_mode():
    """shadow mode：shadow_on=True, injection_on=False（只观察不改 prompt）。"""
    rec = LME.build_memory_mode_record(
        shadow_on=True, injection_on=False,
        candidate_count=3, promotion_count=1)
    assert rec["shadow_on"] is True
    assert rec["injection_on"] is False
    assert rec["candidate_count"] == 3
    assert rec["promotion_count"] == 1


def test_build_memory_mode_record_full_injection_mode():
    """injection mode：两 flag on + selected_lesson_ids（dispatch 注入了 lesson）。"""
    rec = LME.build_memory_mode_record(
        shadow_on=True, injection_on=True,
        selected_lesson_ids=("lesson-a", "lesson-b"),
        candidate_count=5, promotion_count=2)
    assert rec["shadow_on"] is True
    assert rec["injection_on"] is True
    assert rec["selected_lesson_ids"] == ("lesson-a", "lesson-b")
    assert rec["candidate_count"] == 5
    assert rec["promotion_count"] == 2


def test_build_memory_mode_record_with_degraded_status():
    """degraded_status 非 None 时透传（coordinator 同步记 learning_memory_degraded）。"""
    rec = LME.build_memory_mode_record(
        shadow_on=True, injection_on=False,
        degraded_status="middle_corruption")
    assert rec["degraded_status"] == "middle_corruption"


def test_build_memory_mode_record_returns_json_serializable():
    """返回 dict 全字段 JSON-serializable（dispatch/report record 落盘前提）。"""
    import json
    rec = LME.build_memory_mode_record(
        shadow_on=True, injection_on=True,
        selected_lesson_ids=("l1", "l2"),
        candidate_count=2, promotion_count=1,
        degraded_status="reflection_timeout")
    s = json.dumps(rec)   # 不 raise 即 OK
    assert "shadow_on" in s


def test_build_memory_mode_record_does_not_force_other_flags():
    """task 6.4「keep existing success/failure semantics unchanged」—— 纯函数无副作用。

    返回 dict 只含约定的 6 字段；不附带任何「memory 状态影响 delivery」的信号。
    """
    rec = LME.build_memory_mode_record(shadow_on=True, injection_on=False)
    assert set(rec.keys()) == {"shadow_on", "injection_on", "selected_lesson_ids",
                               "candidate_count", "promotion_count", "degraded_status"}
