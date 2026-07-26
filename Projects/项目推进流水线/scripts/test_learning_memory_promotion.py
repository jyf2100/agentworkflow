#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_learning_memory_promotion.py — add-cross-prd-learning-memory Section 3 反例驱动单测。

锁定 cross-PRD promotion policy 的核心硬约束（spec design 决策#4）：

    * **3.1 普通 promotion 仅在等效 valid candidate 引用 ≥2 distinct PRD IDs（同项目）**；
      重复 iteration 计一次（supporting_prd_ids 是 set 去重）。
    * **3.2 merge 行为**：保留所有 source_candidate_ids + evidence lineage；冲突 corrective_action
      → state=conflicted，保持 inactive 直到 evidence 解决（spec「Conflicting corrective actions create
      a conflict event and remain inactive」）。
    * **3.3 反例证明单次出现永不 promotion**（核心反例任务）：即使 verifier 确认的 critical invariant
      violation、model self-labeled critical、或任何 unknown enum 值，单 PRD 出现也绝不 promotion
      （spec「even a verifier-confirmed critical invariant violation must recur across two distinct PRDs
      before promotion」）。
    * **3.4 active/conflicted/superseded/retired 作为 projection**：从 candidates + events replay 派生；
      retirement 只改 projection，candidates.jsonl / events.jsonl append-only 真源不变（spec「without
      deleting historical facts」）。

TDD discipline：先写反例 → 当前 Section 2 默认 active → 反例 RED → 实现 promotion policy → GREEN。
AAA 结构。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import learning_memory_catalog as LMC  # noqa: E402
import learning_memory_promotion as LMP  # noqa: E402
import learning_memory_schema as LM  # noqa: E402
import learning_memory_store as LMS  # noqa: E402


# ════════════════════════════════════════════════════════════════════════
# test helpers（与 test_learning_memory_catalog 对齐的 fixture）
# ════════════════════════════════════════════════════════════════════════
def _valid_candidate_kwargs(**overrides):
    base = dict(
        project_id="proj-a",
        prd_id="prd-001",
        iteration_refs=("iter-1",),
        phase=LM.Phase.VERIFY,
        failure_class=LM.FailureClass.GATE_BLOCKED,
        corrective_action_class=LM.CorrectiveActionClass.ADD_TEST,
        applies_when_tags=(LM.AppliesWhenTag.PYTHON, LM.AppliesWhenTag.CI_GATE),
        corrective_action="add failing test reproducing the gate block before publish",
        pattern_description="dev bypassed test gate; pattern repeats across PRDs",
        applicability_when="applies when dispatch dispatches to a project with a CI test gate",
        non_applicability_when="does not apply when the project has no CI gate configured",
        evidence_refs=({"digest": "sha256:abc", "kind": "test_output", "path": "sha256/ab/c"},),
        source_outcome="verifier-revise-exhausted",
        confidence=0.7,
        schema_version=1,
    )
    base.update(overrides)
    return base


def _candidate(**overrides):
    return LM.LessonCandidate(**_valid_candidate_kwargs(**overrides))


def _event(eid="evt-1", event_type="confirmed", lesson_id=None, **payload):
    return LM.LessonLifecycleEvent(
        event_id=eid, timestamp="2026-07-26T03:17:00Z",
        project_id="proj-a", lesson_id=lesson_id or "lesson-default",
        event_type=event_type, payload=payload, schema_version=1)


def _append_two_prd_candidates(state, project="proj-a", *,
                                corrective_action_a="add failing test for X",
                                corrective_action_b="add failing test for X"):
    """便捷：向 state 追加两个等效但来自不同 PRD 的 candidates（满足 cross-PRD recurrence）。"""
    c1 = _candidate(prd_id="prd-001", corrective_action=corrective_action_a)
    LMS.append_candidate(str(state), project, c1, run_id="r", timestamp="t1")
    # 第二条：不同 prd_id + 不同 evidence digest（避免 candidate_id 退化）
    c2 = _candidate(prd_id="prd-002", corrective_action=corrective_action_b,
                    evidence_refs=({"digest": "sha256:bb", "kind": "test_output", "path": "sha256/bb"},))
    LMS.append_candidate(str(state), project, c2, run_id="r", timestamp="t2")
    return c1, c2


# ════════════════════════════════════════════════════════════════════════
# task 3.3：反例证明单次出现永不 promotion（**核心反例任务**）
# ════════════════════════════════════════════════════════════════════════
def test_single_prd_never_promoted_even_with_verifier_confirmed_critical(tmp_path):
    """**反例 #1**：单 PRD 的 verifier-confirmed critical invariant violation 永不 promotion。

    spec design 决策#4：「No single-occurrence fast path exists in V1; even a verifier-confirmed
    critical invariant violation must recur across two distinct PRDs before promotion」。

    场景：一个 candidate，failure_class=VERIFIER_INVARIANT_VIOLATION（verifier 确认的 critical
    invariant violation），但只来自 1 个 PRD → **绝不**进 active catalog projection。
    """
    # Arrange：单 PRD candidate，verifier 确认的 critical invariant violation
    state = tmp_path / "s"
    c = _candidate(
        prd_id="prd-001",
        failure_class=LM.FailureClass.VERIFIER_INVARIANT_VIOLATION,
    )
    LMS.append_candidate(str(state), "proj-a", c, run_id="r", timestamp="t")

    # Act
    result = LMC.project_catalog(str(state), "proj-a")

    # Assert：<2 distinct PRD → 不进 active catalog projection（即使 verifier 确认 critical）
    assert result.ok
    assert result.snapshot.entries == (), (
        "单 PRD verifier-confirmed critical 不可 promotion（spec：必须 cross 2 distinct PRDs）")
    # promotion 判定 audit trail 记录拒绝理由
    decisions = result.snapshot.promotion_decisions
    assert len(decisions) == 1
    assert decisions[0]["promoted"] is False
    assert decisions[0]["distinct_prd_count"] == 1
    assert "insufficient_cross_prd_recurrence" in decisions[0]["reason"]


def test_single_prd_never_promoted_with_model_self_labeled_critical(tmp_path):
    """**反例 #2**：单 PRD + model self-labeled critical（invariant_class 字段）永不 promotion。

    spec design 决策#4：「An invariant_class asserted by the verifier is retained as an audit-only
    label for human triage and MUST NOT select equivalence or trigger promotion」。

    场景：一个 candidate 带 invariant_class="critical_model_label"（audit-only），只来自 1 个 PRD
    → **绝不** promotion。invariant_class 不驱动 promotion（只是 audit label）。
    """
    # Arrange：单 PRD candidate，model 自标 critical（invariant_class audit-only）
    state = tmp_path / "s"
    c = _candidate(
        prd_id="prd-001",
        invariant_class="critical_invariant_violation",
    )
    LMS.append_candidate(str(state), "proj-a", c, run_id="r", timestamp="t")

    # Act
    result = LMC.project_catalog(str(state), "proj-a")

    # Assert：invariant_class 不驱动 promotion；单 PRD 仍不 promote
    assert result.ok
    assert result.snapshot.entries == (), (
        "invariant_class audit-only label 不触发 promotion；单 PRD 仍需 cross-PRD recurrence")
    decisions = result.snapshot.promotion_decisions
    assert decisions[0]["promoted"] is False
    assert decisions[0]["distinct_prd_count"] == 1


def test_unknown_enum_never_promoted(tmp_path):
    """**反例 #3**：任何 unknown enum 值的 candidate 永不 promotion。

    spec design 决策#4：「Any enum value of unknown keeps the candidate unpromoted regardless of
    recurrence, preventing a model from batching uncertain classifications into promotion」。

    多层 defense-in-depth：
        1. schema 边界（LessonCandidate.__post_init__）拒 unknown enum → candidate 不可构造；
        2. candidate_from_model_output 边界拒（_coerce_enum）→ 不可从 raw dict 还原；
        3. 存储层读端 defense-in-depth（_validate_candidate_record）→ 手灌 JSONL 中的 unknown enum
           → LessonsCorruptionError → catalog replay fail-closed（绝不部分信任）。
    因此 unknown enum candidate **结构上不可能**到达 promotion 层。
    """
    # 1. schema 边界：LessonCandidate 直接构造 unknown enum → ValueError
    with pytest.raises(ValueError, match="unknown"):
        LM.LessonCandidate(**_valid_candidate_kwargs(phase=LM.Phase.UNKNOWN))
    with pytest.raises(ValueError, match="unknown"):
        LM.LessonCandidate(**_valid_candidate_kwargs(
            failure_class=LM.FailureClass.UNKNOWN))

    # 2. candidate_from_model_output 边界：raw dict 带 unknown enum → ValueError
    with pytest.raises(ValueError, match="unknown"):
        LM.candidate_from_model_output(
            _valid_candidate_kwargs(phase="unknown"),
            project_id="proj-a", prd_id="prd-001", iteration_refs=("iter-1",))

    # 3. 存储层读端 defense-in-depth：手灌 JSONL 含 unknown enum → fail-closed（绝不 promotion）
    state = tmp_path / "s"
    cand_kwargs = _valid_candidate_kwargs(prd_id="prd-001")
    cand_kwargs["phase"] = "unknown"   # 手灌污染（绕过 schema 写入）
    bad_line = json.dumps({
        "schema_version": LMS.LESSONS_SCHEMA_VERSION, "kind": "candidate",
        "candidate_id": "c-bad", "run_id": "r", "timestamp": "t",
        "equivalence_key": "proj-a:k-bad",
        "candidate": cand_kwargs,
    })
    p = state / "lessons" / "candidates" / "proj-a.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(bad_line + "\n", encoding="utf-8")
    # catalog replay → fail-closed（unknown enum candidate 绝不进 projection）
    result = LMC.project_catalog(str(state), "proj-a")
    assert result.ok is False, "unknown enum candidate 必须被 fail-closed 拒（绝不部分信任）"
    assert result.degraded_class is not None


# ════════════════════════════════════════════════════════════════════════
# task 3.1：普通 promotion 仅在 ≥2 distinct PRD IDs
# ════════════════════════════════════════════════════════════════════════
def test_two_distinct_prds_promotes_to_active(tmp_path):
    """**正例**：两个等效 valid candidate 来自 2 distinct PRD IDs → promotion to active。

    spec design 决策#4：「Promotion requires equivalence_key-equal candidates supported by at least
    two distinct PRD IDs in the same project」。
    """
    # Arrange：两个等效 candidate，不同 prd_id
    state = tmp_path / "s"
    _append_two_prd_candidates(state)

    # Act
    result = LMC.project_catalog(str(state), "proj-a")

    # Assert：1 条 active entry，supporting_prd_ids 含两个 PRD
    assert result.ok
    assert len(result.snapshot.entries) == 1
    entry = result.snapshot.entries[0]
    assert entry["state"] == "active"
    assert set(entry["supporting_prd_ids"]) == {"prd-001", "prd-002"}
    assert len(entry["source_candidate_ids"]) == 2


def test_same_prd_repeated_iterations_counts_once(tmp_path):
    """3.1：同一 PRD 多次 iteration 只算一个 distinct PRD（supporting_prd_ids 是 set 去重）。

    spec design 决策#4：「Repeated iterations of one PRD count once」。

    场景：3 个等效 candidate，都来自 prd-001（不同 iteration_refs）→ 只有 1 distinct PRD → 不 promote。
    """
    # Arrange：3 个等效 candidate，同一 prd_id，不同 iteration_refs
    state = tmp_path / "s"
    for i, ev_digest in enumerate(["sha256:a1", "sha256:a2", "sha256:a3"], start=1):
        c = _candidate(
            prd_id="prd-001",   # 同一 PRD
            iteration_refs=(f"iter-{i}",),
            evidence_refs=({"digest": ev_digest, "kind": "test_output", "path": f"sha256/a{i}"},),
        )
        LMS.append_candidate(str(state), "proj-a", c, run_id="r", timestamp=f"t{i}")

    # Act
    result = LMC.project_catalog(str(state), "proj-a")

    # Assert：3 个 candidate 都在 candidates.jsonl（facts 保留），但只有 1 distinct PRD → 不 promote
    assert result.ok
    assert result.snapshot.entries == (), (
        "同 PRD 重复 iteration 计一次；3 个 candidate 仍只算 1 PRD，不达 ≥2 阈值")
    # candidates.jsonl 仍有全部 3 条 facts（未删，spec「without deleting historical facts」）
    cand_records = LMS.read_candidate_records(str(state), "proj-a")
    assert len(cand_records) == 3
    # promotion_decisions 记录拒绝（distinct_prd_count=1）
    decisions = result.snapshot.promotion_decisions
    assert len(decisions) == 1
    assert decisions[0]["distinct_prd_count"] == 1


# ════════════════════════════════════════════════════════════════════════
# task 3.2：merge 行为 + 冲突 corrective_action
# ════════════════════════════════════════════════════════════════════════
def test_conflicting_corrective_actions_become_inactive_conflict(tmp_path):
    """3.2：同 equivalence_key 但 corrective_action 文本不同 → state=conflicted（inactive）。

    spec design 决策#4：「Conflicting corrective actions create a conflict event and remain inactive
    until evidence resolves them」+ design Risks：「Require applicability boundaries, retain source
    lineages, and keep conflicting actions inactive」。

    场景：2 个等效 candidate（同 eq key，2 distinct PRD），但 corrective_action 文本不同
    → entry 进 catalog（≥2 PRD 满足 recurrence）但 state=conflicted（inactive，不可注入）。
    """
    # Arrange：两个等效 candidate，不同 corrective_action 文本
    state = tmp_path / "s"
    _append_two_prd_candidates(
        state,
        corrective_action_a="add failing test reproducing the gate block",
        corrective_action_b="fix the CI workflow to enforce test gate before publish",  # 不同文本
    )

    # Act
    result = LMC.project_catalog(str(state), "proj-a")

    # Assert：1 条 entry（≥2 PRD 满足 recurrence）但 state=conflicted
    assert result.ok
    assert len(result.snapshot.entries) == 1
    entry = result.snapshot.entries[0]
    assert entry["state"] == "conflicted"
    # source lineages 保留（spec「Merges preserve all source candidate IDs」）
    assert len(entry["source_candidate_ids"]) == 2
    # promotion_decisions 记录冲突
    decisions = result.snapshot.promotion_decisions
    assert decisions[0]["has_corrective_action_conflict"] is True
    assert decisions[0]["promoted"] is False   # conflict → 不 promote to active


def test_merge_preserves_all_source_candidate_ids_and_lineage(tmp_path):
    """3.2：merge 保留所有 source_candidate_ids（spec「Merges preserve all source candidate IDs and
    evidence lineages」）。两条等效 candidate 合并后 entry 的 source_candidate_ids 含两条。"""
    # Arrange
    state = tmp_path / "s"
    c1, c2 = _append_two_prd_candidates(state)

    # Act
    result = LMC.project_catalog(str(state), "proj-a")

    # Assert
    entry = result.snapshot.entries[0]
    # 两条 candidate_id 都保留（set 去重，sorted tuple 输出）
    assert len(entry["source_candidate_ids"]) == 2
    # evidence 可从 candidates.jsonl 完整回溯（facts 未删）
    records = LMS.read_candidate_records(str(state), "proj-a")
    assert len(records) == 2


# ════════════════════════════════════════════════════════════════════════
# task 3.4：active/conflicted/superseded/retired 作为 projection（永不删 source facts）
# ════════════════════════════════════════════════════════════════════════
def test_retired_lesson_excluded_from_active_projection_but_facts_retained(tmp_path):
    """3.4：retired entry 仍在 catalog（state=retired，auditable）但不进 active injection；
    candidates.jsonl append-only 真源不变（spec「without deleting historical facts」）。

    spec design 决策#6：「Retirement only changes the projection and never deletes source facts」。
    """
    # Arrange：两个等效 candidate（满足 cross-PRD recurrence）+ retired lifecycle event
    state = tmp_path / "s"
    c1, c2 = _append_two_prd_candidates(state)
    lesson_id = LMC.lesson_id_from_equivalence_key(LM.derive_equivalence_key(c1))
    LMS.append_lifecycle_event(
        str(state), "proj-a",
        _event(eid="e-retire", event_type="retired", lesson_id=lesson_id),
        run_id="r")
    # 记录 candidates.jsonl 原始内容（验证 facts 不被删）
    cand_path = state / "lessons" / "candidates" / "proj-a.jsonl"
    original_cand_bytes = cand_path.read_bytes()

    # Act
    result = LMC.project_catalog(str(state), "proj-a")

    # Assert：entry 在 catalog（projection 含 retired 状态，auditable）
    assert result.ok
    assert len(result.snapshot.entries) == 1
    entry = result.snapshot.entries[0]
    assert entry["state"] == "retired"
    # retired != active → excluded from active injection（projection 含但 state 非 active）
    assert entry["state"] != "active"
    # candidates.jsonl append-only 真源未变（spec「without deleting historical facts」）
    assert cand_path.read_bytes() == original_cand_bytes, (
        "retirement 只改 projection，不可删 candidates.jsonl 真源事实")


def test_superseded_lesson_retained_in_catalog_with_state(tmp_path):
    """3.4：superseded entry 仍在 catalog（projection 含 superseded 状态，auditable）。"""
    # Arrange
    state = tmp_path / "s"
    c1, c2 = _append_two_prd_candidates(state)
    lesson_id = LMC.lesson_id_from_equivalence_key(LM.derive_equivalence_key(c1))
    LMS.append_lifecycle_event(
        str(state), "proj-a",
        _event(eid="e-super", event_type="superseded", lesson_id=lesson_id),
        run_id="r")

    # Act
    result = LMC.project_catalog(str(state), "proj-a")

    # Assert
    assert result.ok
    entry = result.snapshot.entries[0]
    assert entry["state"] == "superseded"


def test_active_conflicted_superseded_retired_all_derived_from_projection(tmp_path):
    """3.4：四种状态都是 projection（从 candidates + events replay 派生），rebuild 幂等。

    spec design 决策#3 + #6：「catalog is an atomic, rebuildable projection」+「Retirement only
    changes the projection and never deletes source facts」。
    """
    # Arrange：两个项目，分别 active / retired
    state = tmp_path / "s"
    c1, c2 = _append_two_prd_candidates(state)
    lesson_id = LMC.lesson_id_from_equivalence_key(LM.derive_equivalence_key(c1))
    # 第一组：active（无 lifecycle override）
    # 第二组：retired
    LMS.append_lifecycle_event(
        str(state), "proj-a",
        _event(eid="e-retire", event_type="retired", lesson_id=lesson_id),
        run_id="r")

    # Act：两次 rebuild，验证 projection 稳定 + 可重建
    r1 = LMC.rebuild_catalog(str(state), "proj-a")
    bytes1 = (state / "lessons" / "catalog" / "proj-a.json").read_bytes()
    r2 = LMC.rebuild_catalog(str(state), "proj-a")
    bytes2 = (state / "lessons" / "catalog" / "proj-a.json").read_bytes()

    # Assert：projection 是确定性 replay（相同 facts → byte-identical）
    assert r1.ok and r2.ok
    assert bytes1 == bytes2, "projection 必须可确定性重建（spec：rebuildable）"
    # state 是 retired（projection 从 events 派生，非删 source）
    assert r1.snapshot.entries[0]["state"] == "retired"


# ════════════════════════════════════════════════════════════════════════
# promotion policy 纯函数单测（defense-in-depth + audit trail）
# ════════════════════════════════════════════════════════════════════════
def test_evaluate_promotion_insufficient_prds_returns_decision():
    """单测：evaluate_promotion 对 <2 PRD 返回 promoted=False + reason。"""
    entry = {
        "lesson_id": "lesson_x",
        "equivalence_key": "proj-a:abc",
        "supporting_prd_ids": {"prd-001"},
        "_audit_corrective_actions": {"add test"},
    }
    d = LMP.evaluate_promotion(entry)
    assert d.promoted is False
    assert d.distinct_prd_count == 1
    assert "insufficient_cross_prd_recurrence" in d.reason


def test_evaluate_promotion_conflict_returns_decision():
    """单测：evaluate_promotion 对 corrective_action 冲突返回 promoted=False + conflict 标记。"""
    entry = {
        "lesson_id": "lesson_y",
        "equivalence_key": "proj-a:def",
        "supporting_prd_ids": {"prd-001", "prd-002"},
        "_audit_corrective_actions": {"add test for X", "fix pattern for Y"},   # 2 distinct → conflict
    }
    d = LMP.evaluate_promotion(entry)
    assert d.promoted is False
    assert d.has_corrective_action_conflict is True
    assert d.distinct_prd_count == 2
    assert "conflict" in d.reason.lower()


def test_evaluate_promotion_two_prds_no_conflict_promotes():
    """单测：≥2 PRD + 无冲突 → promoted=True。"""
    entry = {
        "lesson_id": "lesson_z",
        "equivalence_key": "proj-a:ghi",
        "supporting_prd_ids": {"prd-001", "prd-002"},
        "_audit_corrective_actions": {"add test for X"},   # 1 distinct → no conflict
    }
    d = LMP.evaluate_promotion(entry)
    assert d.promoted is True
    assert d.has_corrective_action_conflict is False


def test_apply_promotion_policy_filters_sub_threshold_keeps_conflict():
    """单测：apply_promotion_policy 过滤 <2 PRD entry，保留 conflict entry（state 标 conflicted）。"""
    grouped = {
        "proj-a:k1": {
            "lesson_id": "lesson_k1", "equivalence_key": "proj-a:k1",
            "project_id": "proj-a", "source_candidate_ids": {"c1"},
            "supporting_prd_ids": {"prd-001"},   # <2 → 过滤
            "_audit_corrective_actions": {"action"},
            "corrective_action": "action", "trigger": "t", "non_applicability_when": "n",
            "state": "active", "confidence": 0.5, "schema_version": 1,
        },
        "proj-a:k2": {
            "lesson_id": "lesson_k2", "equivalence_key": "proj-a:k2",
            "project_id": "proj-a", "source_candidate_ids": {"c2", "c3"},
            "supporting_prd_ids": {"prd-001", "prd-002"},   # ≥2 + 冲突 → 保留为 conflicted
            "_audit_corrective_actions": {"action_a", "action_b"},
            "corrective_action": "action_a", "trigger": "t", "non_applicability_when": "n",
            "state": "active", "confidence": 0.5, "schema_version": 1,
        },
    }
    filtered, decisions = LMP.apply_promotion_policy(grouped)
    # k1 被过滤（<2 PRD）
    assert "proj-a:k1" not in filtered
    # k2 保留为 conflicted
    assert "proj-a:k2" in filtered
    assert filtered["proj-a:k2"]["state"] == "conflicted"
    # decisions 记录两者
    assert len(decisions) == 2
    by_key = {d.equivalence_key: d for d in decisions}
    assert by_key["proj-a:k1"].promoted is False
    assert by_key["proj-a:k2"].has_corrective_action_conflict is True


def test_apply_promotion_policy_does_not_override_retired_with_conflict():
    """单测：已被 lifecycle 标 retired/superseded 的 entry，promotion policy 不覆盖为 conflicted。"""
    grouped = {
        "proj-a:k1": {
            "lesson_id": "lesson_k1", "equivalence_key": "proj-a:k1",
            "project_id": "proj-a", "source_candidate_ids": {"c1", "c2"},
            "supporting_prd_ids": {"prd-001", "prd-002"},
            "_audit_corrective_actions": {"a", "b"},   # 有冲突
            "corrective_action": "a", "trigger": "t", "non_applicability_when": "n",
            "state": "retired",   # lifecycle 已标 retired
            "confidence": 0.5, "schema_version": 1,
        },
    }
    filtered, decisions = LMP.apply_promotion_policy(grouped)
    # retired 优先于 conflict 标记（lifecycle 显式状态胜出）
    assert filtered["proj-a:k1"]["state"] == "retired"
    # decisions 仍记录冲突（audit）
    assert decisions[0].has_corrective_action_conflict is True
