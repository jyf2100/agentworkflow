#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_learning_memory_schema.py — add-cross-prd-learning-memory Section 1 schema 单测。

锁定 task 1.1（versioned dataclass/enum）、task 2.3（机械 equivalence_key 派生函数）、
task 1.4（model-authored 字段在 schema 边界被 redact）。

核心断言（spec「Structured and evidence-grounded candidates」+ design 决策#4）：
    * schema-constrained enum 字段（phase/failure_class/corrective_action_class/applies_when_tags）+ bounded
      free-text corrective_action + applicability 边界 + evidence refs + source outcome + confidence；
    * schema MUST NOT accept model-authored pattern_key/equivalence_key（必须由 enum 字段机械派生）；
    * invariant_class（如有）是 audit-only，不进 equivalence_key、不触发 promotion；
    * any enum value of ``unknown`` 永不参与 equivalence/merge/promotion；
    * 缺证据 / 任务摘要无 reusable trigger / 无 executable corrective_action / 枚举超词表 / 字段超长 → 拒绝。

equivalence_key 公式（design 决策#4 + tasks 2.3）::

    project_id + ':' + sha256(json.dumps(
        (canonical(phase), canonical(failure_class), canonical(corrective_action_class),
         applicability_signature),
        separators=(',',':'), sort_keys=True))[:16]

    canonical(t) = lower(str(t)).replace('-', '_').strip()
    applicability_signature = sorted(set(canonical(t) for t in applies_when_tags)) or '__unscoped__'

纯 stdlib 新模块（learning_memory_schema.py），frozen dataclass + __test__=False，零 IO。

跑：python3 -m pytest scripts/test_learning_memory_schema.py -q
AAA 结构。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import learning_memory_schema as LM  # noqa: E402


# ════════════════════════════════════════════════════════════════════════
# task 1.1：受控枚举存在 + 含 UNKNOWN = "unknown"（永不参与 equivalence/合并/晋升）
# ════════════════════════════════════════════════════════════════════════
def test_phase_enum_vocabulary():
    """phase 枚举覆盖 dev lifecycle 阶段 + 含 UNKNOWN。"""
    values = {p.value for p in LM.Phase}
    assert "unknown" in values
    # 至少覆盖 implement / verify / publish / post_terminal 这几类生命周期阶段
    for needed in ("implement", "verify", "publish", "post_terminal"):
        assert needed in values, f"phase 枚举缺 {needed}"


def test_failure_class_enum_vocabulary():
    """failure_class 枚举对齐终态 evidence class + 含 UNKNOWN。"""
    values = {f.value for f in LM.FailureClass}
    assert "unknown" in values
    # 对齐 spec 终态分类（design 決策#1 表 + 终态集合）
    for needed in ("verifier_invariant_violation", "gate_blocked", "stalled",
                   "external_state_unknown", "sandbox_violation", "abort", "state_corrupt"):
        assert needed in values, f"FailureClass 缺 {needed}"


def test_corrective_action_class_enum_vocabulary():
    """corrective_action_class 枚举（参与 equivalence_key）+ 含 UNKNOWN。"""
    values = {c.value for c in LM.CorrectiveActionClass}
    assert "unknown" in values
    # 覆盖常见可执行 corrective step 大类
    for needed in ("add_test", "fix_pattern", "guard_boundary", "config_change"):
        assert needed in values, f"CorrectiveActionClass 缺 {needed}"


def test_applies_when_tag_enum_vocabulary():
    """AppliesWhenTag 是项目无关的技术标签 + 含 UNKNOWN。"""
    values = {t.value for t in LM.AppliesWhenTag}
    assert "unknown" in values
    # 至少有一些跨项目通用标签（语言无关、技术域无关）
    assert len(values) >= 4


# ════════════════════════════════════════════════════════════════════════
# task 1.1：LessonCandidate frozen dataclass round-trip
# ════════════════════════════════════════════════════════════════════════
def _valid_candidate_kwargs(**overrides):
    """构造一份 schema-valid LessonCandidate 字段 dict（测试 baseline）。"""
    base = dict(
        project_id="proj-a",
        prd_id="prd-001",
        iteration_refs=("iter-1",),
        phase=LM.Phase.VERIFY,
        failure_class=LM.FailureClass.GATE_BLOCKED,
        corrective_action_class=LM.CorrectiveActionClass.ADD_TEST,
        applies_when_tags=(LM.AppliesWhenTag.PYTHON, LM.AppliesWhenTag.CI_GATE),
        corrective_action="add failing test reproducing the gate block before publish",
        pattern_description="dev bypassed test gate by passing --skip; pattern repeats across PRDs",
        applicability_when="applies when dispatch dispatches to a project with a CI test gate",
        non_applicability_when="does not apply when the project has no CI gate configured",
        evidence_refs=({"digest": "sha256:abc", "kind": "test_output", "path": "sha256/ab/c"},),
        source_outcome="verifier-revise-exhausted",
        confidence=0.7,
        schema_version=1,
    )
    base.update(overrides)
    return base


def test_lesson_candidate_construct_and_round_trip():
    """task 1.1：schema-valid candidate 构造 + 字段读取 round-trip。"""
    # Act
    cand = LM.LessonCandidate(**_valid_candidate_kwargs())
    # Assert
    assert cand.project_id == "proj-a"
    assert cand.prd_id == "prd-001"
    assert cand.phase == LM.Phase.VERIFY
    assert cand.failure_class == LM.FailureClass.GATE_BLOCKED
    assert cand.corrective_action_class == LM.CorrectiveActionClass.ADD_TEST
    assert cand.corrective_action.startswith("add failing test")
    assert cand.schema_version == 1
    assert cand.confidence == 0.7


def test_lesson_candidate_is_frozen():
    """task 1.1：frozen dataclass（同 loop_state 模式，调用方不得意外改写）。"""
    import dataclasses
    cand = LM.LessonCandidate(**_valid_candidate_kwargs())
    assert dataclasses.is_dataclass(cand)
    with pytest.raises(dataclasses.FrozenInstanceError):
        cand.project_id = "other"  # type: ignore[misc]


def test_lesson_candidate_class_not_collected_by_pytest():
    """task 1.1：LessonCandidate 有 ClassVar __test__=False——pytest 不收集为测试类（同 loop_state）。"""
    assert LM.LessonCandidate.__test__ is False


# ════════════════════════════════════════════════════════════════════════
# task 1.1：schema 拒绝（spec「MUST reject candidates that ...」）
# ════════════════════════════════════════════════════════════════════════
def test_reject_candidate_without_evidence_refs():
    """spec「MUST reject candidates that lack readable integrity-checked evidence」：evidence_refs=() → 拒。"""
    with pytest.raises(ValueError, match=r"evidence"):
        LM.LessonCandidate(**_valid_candidate_kwargs(evidence_refs=()))


def test_reject_candidate_with_empty_corrective_action():
    """spec「MUST reject ... do not prescribe an executable corrective action」：空 corrective_action → 拒。"""
    with pytest.raises(ValueError, match=r"corrective_action"):
        LM.LessonCandidate(**_valid_candidate_kwargs(corrective_action="   "))


def test_reject_candidate_with_task_summary_no_applicability():
    """spec「task-specific summaries without a reusable trigger」：空 applicability_when → 拒（无 reusable trigger）。"""
    with pytest.raises(ValueError, match=r"applicability"):
        LM.LessonCandidate(**_valid_candidate_kwargs(applicability_when=""))


def test_reject_candidate_with_unknown_enum_value():
    """spec「carry any enum value outside the controlled vocabulary」：unknown 枚举值 → 拒（绝不进 catalog）。"""
    with pytest.raises(ValueError, match=r"unknown"):
        LM.LessonCandidate(**_valid_candidate_kwargs(phase=LM.Phase.UNKNOWN))


def test_reject_candidate_failure_class_unknown():
    """task 1.1 + spec：FailureClass.UNKNOWN 不可构造为 candidate（永不参与 equivalence/merge/promotion）。"""
    with pytest.raises(ValueError):
        LM.LessonCandidate(**_valid_candidate_kwargs(failure_class=LM.FailureClass.UNKNOWN))


def test_reject_candidate_corrective_action_over_limit():
    """task 1.1：bounded field size——corrective_action 超长 → 拒（schema 长度上限）。"""
    overlimit = "x" * (LM.MAX_CORRECTIVE_ACTION_LEN + 1)
    with pytest.raises(ValueError, match=r"corrective_action"):
        LM.LessonCandidate(**_valid_candidate_kwargs(corrective_action=overlimit))


def test_reject_candidate_pattern_description_over_limit():
    """task 1.1：bounded field size——pattern_description 超长 → 拒。"""
    overlimit = "y" * (LM.MAX_PATTERN_DESCRIPTION_LEN + 1)
    with pytest.raises(ValueError, match=r"pattern_description"):
        LM.LessonCandidate(**_valid_candidate_kwargs(pattern_description=overlimit))


def test_reject_candidate_confidence_out_of_range():
    """task 1.1：confidence ∈ [0, 1]——越界 → 拒。"""
    with pytest.raises(ValueError, match=r"confidence"):
        LM.LessonCandidate(**_valid_candidate_kwargs(confidence=1.5))
    with pytest.raises(ValueError, match=r"confidence"):
        LM.LessonCandidate(**_valid_candidate_kwargs(confidence=-0.1))


def test_reject_candidate_with_unknown_applies_when_tag():
    """task 1.1：applies_when_tags 含 UNKNOWN → 拒（任何 unknown 值都不可入）。"""
    with pytest.raises(ValueError, match=r"unknown"):
        LM.LessonCandidate(**_valid_candidate_kwargs(
            applies_when_tags=(LM.AppliesWhenTag.PYTHON, LM.AppliesWhenTag.UNKNOWN)))


# ════════════════════════════════════════════════════════════════════════
# task 2.3：机械 equivalence_key 派生（公开放 schema 模块）
# ════════════════════════════════════════════════════════════════════════
def test_derive_equivalence_key_deterministic_within_project():
    """task 2.3：同 (project, phase, failure_class, corrective_action_class, applies_when_tags) → 同 key。"""
    cand = LM.LessonCandidate(**_valid_candidate_kwargs())
    k1 = LM.derive_equivalence_key(cand)
    k2 = LM.derive_equivalence_key(cand)
    assert k1 == k2
    assert k1.startswith("proj-a:")     # project scope 前缀


def test_derive_equivalence_key_byte_equal_for_equivalent_candidates():
    """spec「Two candidates are equivalent iff byte-equal equivalence_key」。

    跨 PRD 但同 enum 字段 + 同 applies_when_tags 的两 candidates → byte-equal key（promotion 前提）。"""
    # Arrange — 两份跨 PRD 的等价 candidate（仅 prd_id/iteration_refs/evidence 不同）
    c1 = LM.LessonCandidate(**_valid_candidate_kwargs(prd_id="prd-001"))
    c2 = LM.LessonCandidate(**_valid_candidate_kwargs(
        prd_id="prd-002",
        iteration_refs=("iter-other",),
        evidence_refs=({"digest": "sha256:other", "kind": "test_output", "path": "sha256/ot/her"},),
        corrective_action="different wording but same enum class",   # 文本不入 key
        pattern_description="different audit text",                   # 文本不入 key
    ))
    # Act
    k1 = LM.derive_equivalence_key(c1)
    k2 = LM.derive_equivalence_key(c2)
    # Assert — byte-equal（与 PRD/iter/evidence/文本字段无关）
    assert k1 == k2


def test_derive_equivalence_key_differs_across_projects():
    """spec「under a ``project_id`` scope」：跨项目同模式 → 不同 key（V1 项目内 promotion）。"""
    c1 = LM.LessonCandidate(**_valid_candidate_kwargs(project_id="proj-a"))
    c2 = LM.LessonCandidate(**_valid_candidate_kwargs(project_id="proj-b"))
    assert LM.derive_equivalence_key(c1) != LM.derive_equivalence_key(c2)


def test_derive_equivalence_key_differs_when_failure_class_differs():
    """failure_class 是 key 输入——不同 failure_class → 不同 key。"""
    c1 = LM.LessonCandidate(**_valid_candidate_kwargs(failure_class=LM.FailureClass.GATE_BLOCKED))
    c2 = LM.LessonCandidate(**_valid_candidate_kwargs(failure_class=LM.FailureClass.STALLED))
    assert LM.derive_equivalence_key(c1) != LM.derive_equivalence_key(c2)


def test_derive_equivalence_key_differs_when_corrective_action_class_differs():
    """corrective_action_class 参与 key（spec「enum participates only in the equivalence key」）。"""
    c1 = LM.LessonCandidate(**_valid_candidate_kwargs(
        corrective_action_class=LM.CorrectiveActionClass.ADD_TEST))
    c2 = LM.LessonCandidate(**_valid_candidate_kwargs(
        corrective_action_class=LM.CorrectiveActionClass.FIX_PATTERN))
    assert LM.derive_equivalence_key(c1) != LM.derive_equivalence_key(c2)


def test_derive_equivalence_key_invariant_when_tags_reordered():
    """spec「ordering of applies_when_tags is the only model-permitted freedom」——重排 → 同 key。"""
    c1 = LM.LessonCandidate(**_valid_candidate_kwargs(
        applies_when_tags=(LM.AppliesWhenTag.PYTHON, LM.AppliesWhenTag.CI_GATE)))
    c2 = LM.LessonCandidate(**_valid_candidate_kwargs(
        applies_when_tags=(LM.AppliesWhenTag.CI_GATE, LM.AppliesWhenTag.PYTHON)))
    assert LM.derive_equivalence_key(c1) == LM.derive_equivalence_key(c2)


def test_derive_equivalence_key_unscoped_fallback():
    """spec「applicability_signature = sorted(...) or '__unscoped__'」——空 tags → __unscoped__ 占位。

    注意：默认 valid candidate 必须有 applicability_when（reusable trigger），但 applies_when_tags 是
    枚举标签集合（可为空，由 __unscoped__ 兜底）。这里直接调内部 canonical 逻辑验证 fallback。"""
    # 用空 tags 构造（applies_when_tags 与 applicability_when 文本字段是两个独立概念）
    cand = LM.LessonCandidate(**_valid_candidate_kwargs(applies_when_tags=()))
    key = LM.derive_equivalence_key(cand)
    # 只断言它能算出 key（不崩），unscoped 是 key hash 输入，不直接出现在 key 字符串里
    assert key.startswith("proj-a:") and ":" in key


def test_derive_equivalence_key_hex_length_is_16():
    """task 2.3 公式：``[:16]`` 取 sha256 hex 前 16 字符。"""
    cand = LM.LessonCandidate(**_valid_candidate_kwargs())
    key = LM.derive_equivalence_key(cand)
    # project_id + ':' + 16 hex chars
    assert len(key) == len("proj-a") + 1 + 16


# ════════════════════════════════════════════════════════════════════════
# task 1.4：schema 边界 redaction（model-authored 字段被丢弃，equivalence 不受影响）
# ════════════════════════════════════════════════════════════════════════
def test_candidate_from_model_output_drops_pattern_key():
    """spec「MUST NOT accept a model-authored pattern_key or equivalence_key」——构造时丢弃。"""
    raw = {
        "phase": "verify",
        "failure_class": "gate_blocked",
        "corrective_action_class": "add_test",
        "applies_when_tags": ["python", "ci_gate"],
        "corrective_action": "add failing test",
        "pattern_description": "bypass",
        "applicability_when": "ci gate",
        "non_applicability_when": "no ci gate",
        "evidence_refs": [{"digest": "sha256:abc", "kind": "test_output", "path": "sha256/ab/c"}],
        "source_outcome": "verifier-revise-exhausted",
        "confidence": 0.6,
        "pattern_key": "MODEL_AUTHORED_KEY_SHOULD_BE_IGNORED",   # model-authored，应被丢弃
    }
    cand = LM.candidate_from_model_output(raw, project_id="proj-a", prd_id="prd-001",
                                          iteration_refs=("iter-1",))
    assert not hasattr(cand, "pattern_key") or getattr(cand, "pattern_key", None) is None


def test_candidate_from_model_output_drops_equivalence_key():
    """model 灌的 equivalence_key 被丢弃——key 由机械派生函数算出。"""
    raw = {
        "phase": "verify",
        "failure_class": "gate_blocked",
        "corrective_action_class": "add_test",
        "applies_when_tags": ["python"],
        "corrective_action": "add test",
        "pattern_description": "x",
        "applicability_when": "ci",
        "non_applicability_when": "no ci",
        "evidence_refs": [{"digest": "sha256:abc", "kind": "test_output", "path": "sha256/ab/c"}],
        "source_outcome": "failed",
        "confidence": 0.5,
        "equivalence_key": "MODEL_LIES_ABOUT_KEY",
    }
    cand = LM.candidate_from_model_output(raw, project_id="proj-a", prd_id="prd-001",
                                          iteration_refs=("iter-1",))
    derived = LM.derive_equivalence_key(cand)
    assert "MODEL_LIES_ABOUT_KEY" not in derived
    assert derived.startswith("proj-a:")


def test_candidate_from_model_output_drops_promotion_count_and_storage_path():
    """spec task 1.4：promotion_count / storage_path 是 system 字段，model 输出含也丢弃。"""
    raw = {
        "phase": "implement",
        "failure_class": "stalled",
        "corrective_action_class": "fix_pattern",
        "applies_when_tags": ["python"],
        "corrective_action": "fix",
        "pattern_description": "x",
        "applicability_when": "ci",
        "non_applicability_when": "no ci",
        "evidence_refs": [{"digest": "sha256:abc", "kind": "diff", "path": "sha256/ab/c"}],
        "source_outcome": "stalled",
        "confidence": 0.4,
        "promotion_count": 999,         # system 字段，不可由 model 灌
        "storage_path": "/some/path",   # system 字段
    }
    cand = LM.candidate_from_model_output(raw, project_id="proj-a", prd_id="prd-001",
                                          iteration_refs=("iter-1",))
    assert not hasattr(cand, "promotion_count")
    assert not hasattr(cand, "storage_path")


def test_candidate_from_model_output_drops_model_project_id_uses_injected():
    """spec task 1.4：project_id 从外部注入，model 灌的 project_id 被丢弃（防串项目注入）。"""
    raw = {
        "phase": "implement",
        "failure_class": "stalled",
        "corrective_action_class": "fix_pattern",
        "applies_when_tags": ["python"],
        "corrective_action": "fix",
        "pattern_description": "x",
        "applicability_when": "ci",
        "non_applicability_when": "no ci",
        "evidence_refs": [{"digest": "sha256:abc", "kind": "diff", "path": "sha256/ab/c"}],
        "source_outcome": "stalled",
        "confidence": 0.4,
        "project_id": "ATTACKER_PROJECT",   # model 试图串项目
    }
    cand = LM.candidate_from_model_output(raw, project_id="proj-a", prd_id="prd-001",
                                          iteration_refs=("iter-1",))
    assert cand.project_id == "proj-a"   # 外部注入赢
    assert cand.project_id != "ATTACKER_PROJECT"


def test_candidate_from_model_output_invariant_class_is_audit_only():
    """spec「An invariant_class field, if present, is an audit-only label ... MUST NOT drive promotion」。

    invariant_class 保留为 audit-only 字段，但**不进 equivalence_key**——同 invariant_class 不同 enum 字段
    的两 candidates 不应被等效；不同 invariant_class 同 enum 字段的 candidates 应被等效。"""
    # 同 enum 字段，仅 invariant_class 不同
    raw1 = {"phase": "verify", "failure_class": "verifier_invariant_violation",
            "corrective_action_class": "guard_boundary", "applies_when_tags": ["python"],
            "corrective_action": "guard", "pattern_description": "x",
            "applicability_when": "ci", "non_applicability_when": "no ci",
            "evidence_refs": [{"digest": "sha256:a1", "kind": "verifier_feedback", "path": "sha256/a1"}],
            "source_outcome": "verifier-revise-exhausted", "confidence": 0.8,
            "invariant_class": "INV_A"}
    raw2 = {**raw1, "invariant_class": "INV_B",
            "evidence_refs": [{"digest": "sha256:b2", "kind": "verifier_feedback", "path": "sha256/b2"}]}
    c1 = LM.candidate_from_model_output(raw1, project_id="proj-a", prd_id="prd-001",
                                        iteration_refs=("i1",))
    c2 = LM.candidate_from_model_output(raw2, project_id="proj-a", prd_id="prd-002",
                                        iteration_refs=("i2",))
    # invariant_class 不同，但 enum 字段相同 → key byte-equal（invariant_class 不进 key）
    assert LM.derive_equivalence_key(c1) == LM.derive_equivalence_key(c2)
    # invariant_class 被保留为 audit-only（字段可访问）
    assert getattr(c1, "invariant_class", None) == "INV_A"
    assert getattr(c2, "invariant_class", None) == "INV_B"


def test_candidate_from_model_output_canonicalizes_enum_string_values():
    """task 1.4：raw model 输出的 enum 字段是 kebab/lowercase string → 接受 + canonical 等价。"""
    raw = {
        "phase": "verify",
        "failure_class": "gate-blocked",       # kebab-case
        "corrective_action_class": "add-test",  # kebab-case
        "applies_when_tags": ["python", "ci-gate"],
        "corrective_action": "x",
        "pattern_description": "y",
        "applicability_when": "ci",
        "non_applicability_when": "no ci",
        "evidence_refs": [{"digest": "sha256:abc", "kind": "diff", "path": "sha256/ab/c"}],
        "source_outcome": "failed",
        "confidence": 0.5,
    }
    cand = LM.candidate_from_model_output(raw, project_id="p", prd_id="prd-1",
                                          iteration_refs=("i",))
    # canonical 公式：lower().replace('-','_').strip() → 同枚举值 byte-equal
    assert LM.derive_equivalence_key(cand) == LM.derive_equivalence_key(
        LM.LessonCandidate(**_valid_candidate_kwargs(project_id="p", prd_id="prd-1")))


def test_candidate_from_model_output_rejects_unknown_enum_string():
    """task 1.4：raw model 输出含超词表枚举值 → 拒（绝不让 unknown 混入）。"""
    raw = {
        "phase": "verify",
        "failure_class": "totally_made_up_class",   # 不在词表
        "corrective_action_class": "add-test",
        "applies_when_tags": ["python"],
        "corrective_action": "x",
        "pattern_description": "y",
        "applicability_when": "ci",
        "non_applicability_when": "no ci",
        "evidence_refs": [{"digest": "sha256:abc", "kind": "diff", "path": "sha256/ab/c"}],
        "source_outcome": "failed",
        "confidence": 0.5,
    }
    with pytest.raises(ValueError):
        LM.candidate_from_model_output(raw, project_id="p", prd_id="prd-1",
                                        iteration_refs=("i",))


# ════════════════════════════════════════════════════════════════════════
# task 1.1：lifecycle/catalog/usage dataclass round-trip（spec「Lesson effectiveness feedback and lifecycle」）
# ════════════════════════════════════════════════════════════════════════
def test_lesson_lifecycle_event_construct():
    """task 1.1：LessonLifecycleEvent dataclass 可构造（spec「confirmation, confidence reduction,
    supersession, and retirement」—— lifecycle 事件类型受控）。"""
    ev = LM.LessonLifecycleEvent(
        event_id="evt-1", timestamp="2026-07-26T00:00:00Z",
        project_id="proj-a", lesson_id="lesson-1", event_type="confirmed",
        payload={"evidence_ref": {"digest": "sha256:abc"}}, schema_version=1)
    assert ev.event_type == "confirmed"
    assert ev.lesson_id == "lesson-1"
    assert ev.__test__ is False


def test_lesson_lifecycle_event_rejects_unknown_event_type():
    """task 1.1：event_type 超词表 → 拒（lifecycle 状态机受控）。"""
    with pytest.raises(ValueError):
        LM.LessonLifecycleEvent(
            event_id="e", timestamp="t", project_id="p", lesson_id="l",
            event_type="totally_made_up", payload={}, schema_version=1)


def test_active_catalog_entry_construct():
    """task 1.1：ActiveCatalogEntry dataclass 可构造（spec「active lesson」projection entry）。"""
    entry = LM.ActiveCatalogEntry(
        lesson_id="lesson-1", project_id="proj-a",
        equivalence_key="proj-a:deadbeefdeadbeef",
        source_candidate_ids=("cand-1", "cand-2"),
        supporting_prd_ids=("prd-001", "prd-002"),
        corrective_action="add test", trigger="ci gate",
        non_applicability_when="no ci",
        state="active", confidence=0.7, schema_version=1)
    assert entry.state == "active"
    assert len(entry.supporting_prd_ids) == 2
    assert entry.__test__ is False


def test_evidence_lineage_construct():
    """task 1.1：EvidenceLineage dataclass 可构造（spec「Merges preserve all source candidate IDs and
    evidence lineages」）。"""
    lin = LM.EvidenceLineage(
        lesson_id="lesson-1", candidate_id="cand-1", prd_id="prd-001",
        iteration_id="iter-1", evidence_refs=(
            {"digest": "sha256:abc", "kind": "test_output", "path": "sha256/ab/c"},), schema_version=1)
    assert lin.candidate_id == "cand-1"
    assert len(lin.evidence_refs) == 1


def test_usage_outcome_construct():
    """task 1.1：UsageOutcome dataclass 可构造（spec「record whether the development run exhibited the
    prescribed action and whether the associated failure pattern recurred」）。"""
    out = LM.UsageOutcome(
        event_id="u-1", timestamp="2026-07-26T00:00:00Z",
        project_id="proj-a", lesson_id="lesson-1", prd_id="prd-003",
        action_observed=True, failure_recurred=False, outcome="recurrence_prevented",
        evidence_refs=({"digest": "sha256:o1", "kind": "test_output", "path": "sha256/o1"},),
        schema_version=1)
    assert out.outcome == "recurrence_prevented"
    assert out.action_observed is True


def test_usage_outcome_rejects_unknown_outcome():
    """task 1.1：UsageOutcome outcome 超词表 → 拒（spec 受控：followed/not_observed/recurrence_prevented/
    recurrence_observed/contradicted/unknown）。"""
    with pytest.raises(ValueError):
        LM.UsageOutcome(
            event_id="u", timestamp="t", project_id="p", lesson_id="l", prd_id="prd",
            action_observed=False, failure_recurred=False, outcome="totally_made_up",
            evidence_refs=(), schema_version=1)
