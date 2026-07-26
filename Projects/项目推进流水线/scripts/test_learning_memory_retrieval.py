#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_learning_memory_retrieval.py — add-cross-prd-learning-memory Section 5 单测。

锁定 retrieval + prompt injection 的机械契约（spec design 决策#5「Retrieve with bounded deterministic
metadata matching」+ 决策#7「fail open for delivery, fail closed for memory」）：

    * **task 5.1 derive_task_metadata**：profile + prd → tags/categories/stage/paths 确定性派生（零 SDK）。
      保守原则：未列出 language / 不显式 has_ci / acceptance_criteria 不含 "test" → 不加 tag。
    * **task 5.2 retrieve_lessons**：项目本地 filter + rank + cap=5，stable tie-break by lesson_id ASC。
      反例全覆盖（task 5.5）：unrelated / conflicted / superseded / retired / cross-project /
      malformed / non_applicability 命中 → 不注入。
    * **task 5.3 render_lesson_block**：≤5 lessons 渲染为 markdown checklist；严格排除 evidence bodies
      与 historical narratives（design 决策#5）。空选集 → ``""``。
    * **task 5.4 inject_into_prompt**：纯字符串拼接；``lesson_block==""`` → identity（no-op）。
    * **task 5.5 load_catalog_for_retrieval + retrieve_from_source**：fail-open wrapper，catalog 故障
      → degraded_class + ``entries=()``，绝不抛主路径。

spec design 决策#5 关键不变式（**全部由机械判定 own**）：
    * retrieval = 确定性集合运算 + 排序（无 SDK / LLM / embedding）；
    * 至多 5 lessons 注入；
    * 仅 ``lesson_id``/``trigger``/``corrective_action``/``non_applicability_when`` 字段注入；
    * evidence_refs / pattern_description / effectiveness_history / source_candidate_ids 等绝不注入；
    * retrieval 故障 → 不注入 + degraded_class，**绝不阻断 dispatch**（delivery fail-open）。

跑：python3 -m pytest scripts/test_learning_memory_retrieval.py -q
AAA 结构 + 紧凑 ``a; b`` 写法（与 test_learning_memory_* 既定风格一致，ruff 仅开 E9+F）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import learning_memory_retrieval as LMR  # noqa: E402


# ════════════════════════════════════════════════════════════════════════
# fixtures / helpers
# ════════════════════════════════════════════════════════════════════════
def _entry(
    lesson_id="lesson_a",
    project_id="proj-x",
    state="active",
    confidence=0.5,
    applies_when_tags=("python",),
    verified_support_count=1,
    effectiveness_history=(),
    last_outcome_ts=None,
    trigger="when X",
    corrective_action="do Y",
    non_applicability_when="skip when stage=verify",
    **kw,
) -> dict:
    """构造 catalog entry dict（16 字段契约）；kw 用于覆盖任意字段。"""
    e = {
        "lesson_id": lesson_id,
        "project_id": project_id,
        "equivalence_key": f"{project_id}:abc",
        "source_candidate_ids": (),
        "supporting_prd_ids": (),
        "corrective_action": corrective_action,
        "trigger": trigger,
        "non_applicability_when": non_applicability_when,
        "state": state,
        "confidence": confidence,
        "schema_version": 1,
        "applies_when_tags": tuple(sorted(applies_when_tags)),
        "verified_support_count": verified_support_count,
        "effectiveness_history": tuple(effectiveness_history),
        "last_outcome_ts": last_outcome_ts,
        "usage_count": 0,
        "contradiction_count": 0,
    }
    e.update(kw)
    return e


def _task(project_id="proj-x", tags=None, **kw) -> dict:
    """构造 task_metadata dict（derive_task_metadata 返回 shape）。"""
    t = {
        "project_id": project_id,
        "tags": set(tags or {"python"}),
        "acceptance_categories": (),
        "lifecycle_stage": "",
        "declared_paths": (),
    }
    t.update(kw)
    return t


# ════════════════════════════════════════════════════════════════════════
# task 5.1：derive_task_metadata
# ════════════════════════════════════════════════════════════════════════
def test_derive_metadata_python_language():
    profile = {"language": "python"}
    md = LMR.derive_task_metadata(project_profile=profile, prd={}, project_id="proj-x")
    assert md["project_id"] == "proj-x"
    assert md["tags"] == {"python"}


def test_derive_metadata_typescript_via_primary_language():
    profile = {"primary_language": "typescript"}
    md = LMR.derive_task_metadata(project_profile=profile, prd={}, project_id="p")
    assert md["tags"] == {"typescript"}


def test_derive_metadata_golang_via_primary():
    profile = {"primary": "go"}
    md = LMR.derive_task_metadata(project_profile=profile, prd={}, project_id="p")
    assert md["tags"] == {"golang"}


def test_derive_metadata_ci_gate_from_has_ci_true():
    profile = {"has_ci": True}
    md = LMR.derive_task_metadata(project_profile=profile, prd={}, project_id="p")
    assert "ci_gate" in md["tags"]


def test_derive_metadata_ci_gate_from_ci_config_non_empty():
    profile = {"ci_config": {"workflow": "ci.yml"}}
    md = LMR.derive_task_metadata(project_profile=profile, prd={}, project_id="p")
    assert "ci_gate" in md["tags"]


def test_derive_metadata_dependency_mgmt_from_string():
    profile = {"dependency_management": "poetry"}
    md = LMR.derive_task_metadata(project_profile=profile, prd={}, project_id="p")
    assert "dependency_mgmt" in md["tags"]


def test_derive_metadata_dependency_mgmt_from_dict():
    profile = {"dependency_management": {"tool": "npm"}}
    md = LMR.derive_task_metadata(project_profile=profile, prd={}, project_id="p")
    assert "dependency_mgmt" in md["tags"]


def test_derive_metadata_test_infra_from_acceptance_criteria_substring():
    prd = {"acceptance_criteria": ["all tests pass", "lint clean"]}
    md = LMR.derive_task_metadata(project_profile={}, prd=prd, project_id="p")
    assert "test_infra" in md["tags"]


def test_derive_metadata_test_infra_from_dict_item_description():
    prd = {"acceptance_criteria": [{"category": "quality", "description": "unit test coverage 80%"}]}
    md = LMR.derive_task_metadata(project_profile={}, prd=prd, project_id="p")
    assert "test_infra" in md["tags"]


def test_derive_metadata_passes_through_acceptance_categories():
    prd = {"acceptance_categories": ["security", "perf"]}
    md = LMR.derive_task_metadata(project_profile={}, prd=prd, project_id="p")
    assert md["acceptance_categories"] == ("security", "perf")


def test_derive_metadata_passes_through_lifecycle_stage():
    prd = {"lifecycle_stage": "implement"}
    md = LMR.derive_task_metadata(project_profile={}, prd=prd, project_id="p")
    assert md["lifecycle_stage"] == "implement"


def test_derive_metadata_passes_through_declared_paths_prd_priority():
    prd = {"declared_paths": ["src/auth"]}
    profile = {"declared_paths": ["src/legacy"]}
    md = LMR.derive_task_metadata(project_profile=profile, prd=prd, project_id="p")
    assert md["declared_paths"] == ("src/auth",)


def test_derive_metadata_declared_paths_falls_back_to_profile():
    profile = {"declared_paths": ["src/legacy"]}
    md = LMR.derive_task_metadata(project_profile=profile, prd={}, project_id="p")
    assert md["declared_paths"] == ("src/legacy",)


def test_derive_metadata_conservative_no_fields_no_tags():
    """保守原则：profile/prd 无任何显式 hint → tags 为空（不猜，防假阳性注入）。"""
    md = LMR.derive_task_metadata(project_profile={}, prd={}, project_id="p")
    assert md["tags"] == set()


def test_derive_metadata_conservative_unknown_language_no_tag():
    """未知 language（如 "rust"）→ 不加 tag（V1 受控词表仅 python/typescript/golang）。"""
    profile = {"language": "rust"}
    md = LMR.derive_task_metadata(project_profile=profile, prd={}, project_id="p")
    assert md["tags"] == set()


def test_derive_metadata_conservative_empty_ci_config_no_tag():
    """空 ci_config dict（无实际配置）→ 不加 ci_gate tag（保守，防假阳性）。"""
    profile = {"ci_config": {}}
    md = LMR.derive_task_metadata(project_profile=profile, prd={}, project_id="p")
    assert "ci_gate" not in md["tags"]


def test_derive_metadata_conservative_unknown_dep_mgmt_tool_no_tag():
    """dependency_management 是未知工具名（如 "magic"）→ 不加 dependency_mgmt tag。"""
    profile = {"dependency_management": "magic"}
    md = LMR.derive_task_metadata(project_profile=profile, prd={}, project_id="p")
    assert "dependency_mgmt" not in md["tags"]


def test_derive_metadata_conservative_has_ci_false_no_tag():
    profile = {"has_ci": False}
    md = LMR.derive_task_metadata(project_profile=profile, prd={}, project_id="p")
    assert "ci_gate" not in md["tags"]


def test_derive_metadata_handles_non_dict_inputs_safely():
    """非 dict profile/prd → 不抛，按空 dict 处理（保守，对接线方容错）。"""
    md = LMR.derive_task_metadata(project_profile=None, prd="not-a-dict", project_id="p")
    assert md["tags"] == set()
    assert md["project_id"] == "p"


def test_derive_metadata_tags_are_valid_applies_vocab():
    """所有派生 tag 必须在受控 AppliesWhenTag 词表内（防超词表污染 overlap 计算）。"""
    profile = {"language": "python", "has_ci": True, "dependency_management": "npm"}
    prd = {"acceptance_criteria": ["tests pass"]}
    md = LMR.derive_task_metadata(project_profile=profile, prd=prd, project_id="p")
    valid = {"python", "typescript", "golang", "ci_gate", "test_infra", "dependency_mgmt"}
    assert md["tags"].issubset(valid)


# ════════════════════════════════════════════════════════════════════════
# task 5.2 + 5.5：retrieve_lessons filter 反例
# ════════════════════════════════════════════════════════════════════════
def test_retrieve_includes_matching_active_lesson():
    entries = [_entry()]
    r = LMR.retrieve_lessons(entries, _task())
    assert r.selected_lesson_ids == ("lesson_a",)


def test_retrieve_excludes_unrelated_no_tag_overlap():
    """task 5.5 反例：applies_when_tags 与 task_tags 无 overlap → 不注入。"""
    entries = [_entry(applies_when_tags=("golang",))]
    r = LMR.retrieve_lessons(entries, _task(tags={"python"}))
    assert r.selected == ()


def test_retrieve_keeps_unscoped_entry_empty_applies_when_tags():
    """task 5.5 反例覆盖：applies_when_tags 为空（__unscoped__）→ 保留（不因 tag 缺失排除）。

    spec 决策#4 applicability_signature 兜底 ``__unscoped__``——空 tag 是合法的通用 lesson。
    """
    entries = [_entry(applies_when_tags=())]
    r = LMR.retrieve_lessons(entries, _task(tags={"python"}))
    assert r.selected_lesson_ids == ("lesson_a",)


def test_retrieve_excludes_conflicted():
    """task 5.5 反例：state=conflicted → 不注入。"""
    entries = [_entry(state="conflicted")]
    r = LMR.retrieve_lessons(entries, _task())
    assert r.selected == ()


def test_retrieve_excludes_superseded():
    """task 5.5 反例：state=superseded → 不注入。"""
    entries = [_entry(state="superseded")]
    r = LMR.retrieve_lessons(entries, _task())
    assert r.selected == ()


def test_retrieve_excludes_retired():
    """task 5.5 反例：state=retired → 不注入（design「retired lessons remain replayable but excluded
    from retrieval」）。"""
    entries = [_entry(state="retired")]
    r = LMR.retrieve_lessons(entries, _task())
    assert r.selected == ()


def test_retrieve_excludes_cross_project():
    """task 5.5 反例：project_id 不匹配 → 不注入（V1 项目内 scope）。"""
    entries = [_entry(project_id="other-proj")]
    r = LMR.retrieve_lessons(entries, _task(project_id="proj-x"))
    assert r.selected == ()


def test_retrieve_excludes_non_applicability_match_lifecycle_stage():
    """task 5.5 反例：non_applicability_when 命中 lifecycle_stage → 排除（boundary 命中）。"""
    entries = [_entry(non_applicability_when="skip when stage=post_terminal")]
    r = LMR.retrieve_lessons(entries, _task(lifecycle_stage="post_terminal"))
    assert r.selected == ()


def test_retrieve_excludes_non_applicability_match_acceptance_category():
    entries = [_entry(non_applicability_when="skip for security work")]
    r = LMR.retrieve_lessons(entries, _task(acceptance_categories=("security",)))
    assert r.selected == ()


def test_retrieve_excludes_non_applicability_match_declared_path():
    entries = [_entry(non_applicability_when="skip when touching src/legacy")]
    r = LMR.retrieve_lessons(entries, _task(declared_paths=("src/legacy",)))
    assert r.selected == ()


def test_retrieve_excludes_malformed_missing_lesson_id():
    """task 5.5 反例：缺 lesson_id → malformed → 排除。"""
    entries = [{"project_id": "proj-x", "state": "active", "confidence": 0.5}]
    r = LMR.retrieve_lessons(entries, _task())
    assert r.selected == ()


def test_retrieve_excludes_malformed_missing_project_id():
    entries = [{"lesson_id": "x", "state": "active", "confidence": 0.5}]
    r = LMR.retrieve_lessons(entries, _task())
    assert r.selected == ()


def test_retrieve_excludes_malformed_missing_state():
    entries = [{"lesson_id": "x", "project_id": "proj-x", "confidence": 0.5}]
    r = LMR.retrieve_lessons(entries, _task())
    assert r.selected == ()


def test_retrieve_excludes_malformed_confidence_above_range():
    entries = [_entry(confidence=1.5)]
    r = LMR.retrieve_lessons(entries, _task())
    assert r.selected == ()


def test_retrieve_excludes_malformed_confidence_below_range():
    entries = [_entry(confidence=-0.1)]
    r = LMR.retrieve_lessons(entries, _task())
    assert r.selected == ()


def test_retrieve_excludes_malformed_invalid_state_value():
    """state 非 active/conflicted/superseded/retired（如 "draft"）→ malformed → 排除。"""
    entries = [_entry(state="draft")]  # _entry 不校验，直接灌
    r = LMR.retrieve_lessons(entries, _task())
    assert r.selected == ()


def test_retrieve_excludes_malformed_non_dict_entry():
    entries = ["not-a-dict", None, 42]
    r = LMR.retrieve_lessons(entries, _task())
    assert r.selected == ()


def test_retrieve_filtered_count_excludes_filtered_only():
    """filtered_count = filter 后 cap 前的候选数（监控用，区分「无候选」与「命中 cap」）。"""
    entries = [
        _entry(lesson_id="a"),
        _entry(lesson_id="b", state="retired"),    # filtered out
        _entry(lesson_id="c", project_id="other"),  # filtered out
    ]
    r = LMR.retrieve_lessons(entries, _task())
    assert r.filtered_count == 1
    assert r.selected_lesson_ids == ("a",)


def test_retrieve_degraded_class_always_none_for_in_memory_call():
    """retrieve_lessons 自身永不 degrade；上游 catalog 故障经 retrieve_from_source 透传。"""
    r = LMR.retrieve_lessons([_entry()], _task())
    assert r.degraded_class is None


def test_retrieve_empty_entries_returns_empty():
    r = LMR.retrieve_lessons([], _task())
    assert r.selected == () and r.selected_lesson_ids == ()
    assert r.filtered_count == 0


# ════════════════════════════════════════════════════════════════════════
# task 5.2：rank（design 决策#5 顺序 + stable tie-break）
# ════════════════════════════════════════════════════════════════════════
def test_retrieve_caps_at_five_when_six_candidates():
    """task 5.3 cap：6 个 active 候选只取 5（design「At most five lessons」）。"""
    entries = [_entry(lesson_id=f"lesson_{i}") for i in range(6)]
    r = LMR.retrieve_lessons(entries, _task())
    assert len(r.selected) == 5
    assert len(r.selected_lesson_ids) == 5


def test_retrieve_caps_at_max_lessons_design_hard_limit():
    """调用方传 max_lessons=10 也 cap 到 5（design 硬上限，防 accidentally bypass）。"""
    entries = [_entry(lesson_id=f"lesson_{i}") for i in range(8)]
    r = LMR.retrieve_lessons(entries, _task(), max_lessons=10)
    assert len(r.selected) == 5


def test_retrieve_respects_smaller_max_lessons():
    entries = [_entry(lesson_id=f"lesson_{i}") for i in range(3)]
    r = LMR.retrieve_lessons(entries, _task(), max_lessons=2)
    assert len(r.selected) == 2


def test_retrieve_stable_tiebreak_lesson_id_asc():
    """相同 ranking 维度 → lesson_id 升序（byte-stable，两次 retrieve 同输入 → 同顺序）。"""
    entries = [
        _entry(lesson_id="charlie", applies_when_tags=("python", "ci_gate")),
        _entry(lesson_id="alpha", applies_when_tags=("python", "ci_gate")),
        _entry(lesson_id="bravo", applies_when_tags=("python", "ci_gate")),
    ]
    r1 = LMR.retrieve_lessons(list(entries), _task(tags={"python", "ci_gate"}))
    r2 = LMR.retrieve_lessons(list(reversed(entries)), _task(tags={"python", "ci_gate"}))  # 输入逆序
    assert r1.selected_lesson_ids == ("alpha", "bravo", "charlie")
    assert r1.selected_lesson_ids == r2.selected_lesson_ids  # 输入顺序无关 → 同样输出（stable）


def test_retrieve_ranks_by_applicability_overlap_desc():
    """tag 重叠多者优先（design「ranked by applicability overlap」首位）。"""
    entries = [
        _entry(lesson_id="one_tag", applies_when_tags=("python",)),
        _entry(lesson_id="two_tags", applies_when_tags=("python", "ci_gate")),
    ]
    r = LMR.retrieve_lessons(entries, _task(tags={"python", "ci_gate"}))
    assert r.selected_lesson_ids == ("two_tags", "one_tag")


def test_retrieve_ranks_by_verified_support_count_desc_on_tie():
    """overlap 相同时 verified_support_count 多者优先。"""
    entries = [
        _entry(lesson_id="low_support", verified_support_count=2),
        _entry(lesson_id="high_support", verified_support_count=5),
    ]
    r = LMR.retrieve_lessons(entries, _task())
    assert r.selected_lesson_ids == ("high_support", "low_support")


def test_retrieve_ranks_by_effectiveness_score_desc_on_tie():
    """overlap + verified_support 相同时 effectiveness_score 高者优先。

    方向：followed/recurrence_prevented +1；contradicted/recurrence_observed -1。
    """
    entries = [
        _entry(lesson_id="negative", effectiveness_history=[
            {"outcome": "contradicted"}, {"outcome": "followed"}]),   # 1 - 1 = 0
        _entry(lesson_id="positive", effectiveness_history=[
            {"outcome": "followed"}, {"outcome": "recurrence_prevented"}]),   # +2
    ]
    r = LMR.retrieve_lessons(entries, _task())
    assert r.selected_lesson_ids == ("positive", "negative")


def test_retrieve_ranks_by_confidence_desc_on_tie():
    """overlap + verified + effectiveness 相同时 confidence 高者优先。"""
    entries = [
        _entry(lesson_id="low_conf", confidence=0.3),
        _entry(lesson_id="high_conf", confidence=0.9),
    ]
    r = LMR.retrieve_lessons(entries, _task())
    assert r.selected_lesson_ids == ("high_conf", "low_conf")


def test_retrieve_ranks_by_recency_desc_on_tie():
    """overlap + verified + effectiveness + confidence 相同时 recency 新者优先。

    recency_rank = last_outcome_ts（ISO8601 字典序 = 时间序）；None → 最低。
    """
    entries = [
        _entry(lesson_id="older", last_outcome_ts="2026-01-01T00:00:00Z"),
        _entry(lesson_id="newer", last_outcome_ts="2026-07-15T12:00:00Z"),
        _entry(lesson_id="never", last_outcome_ts=None),
    ]
    r = LMR.retrieve_lessons(entries, _task())
    assert r.selected_lesson_ids == ("newer", "older", "never")


def test_retrieve_effectiveness_score_positive_outcomes_plus_one():
    """effectiveness_score 方向：followed/recurrence_prevented 各 +1。"""
    from learning_memory_retrieval import _effectiveness_score
    e = _entry(effectiveness_history=[
        {"outcome": "followed"}, {"outcome": "recurrence_prevented"}])
    assert _effectiveness_score(e) == 2


def test_retrieve_effectiveness_score_negative_outcomes_minus_one():
    """effectiveness_score 方向：contradicted/recurrence_observed 各 -1。"""
    from learning_memory_retrieval import _effectiveness_score
    e = _entry(effectiveness_history=[
        {"outcome": "contradicted"}, {"outcome": "recurrence_observed"}])
    assert _effectiveness_score(e) == -2


def test_retrieve_effectiveness_score_neutral_outcomes_zero():
    """effectiveness_score 方向：not_observed/unknown 各 0（与 catalog「absent evidence ≠ disobedience」对齐）。"""
    from learning_memory_retrieval import _effectiveness_score
    e = _entry(effectiveness_history=[
        {"outcome": "not_observed"}, {"outcome": "unknown"}])
    assert _effectiveness_score(e) == 0


def test_retrieve_effectiveness_score_mixed_balance():
    """混合 +1/-1/0 三类相加（验证同向叠加正确）。"""
    from learning_memory_retrieval import _effectiveness_score
    e = _entry(effectiveness_history=[
        {"outcome": "followed"},               # +1
        {"outcome": "contradicted"},           # -1
        {"outcome": "not_observed"},           # 0
        {"outcome": "recurrence_prevented"},   # +1
        {"outcome": "recurrence_observed"},    # -1
        {"outcome": "unknown"},                # 0
    ])
    assert _effectiveness_score(e) == 0


def test_retrieve_effectiveness_score_handles_non_dict_history():
    """非 dict 元素 / 非正常 history → 视为 0（防御 malformed catalog）。"""
    from learning_memory_retrieval import _effectiveness_score
    assert _effectiveness_score({"effectiveness_history": "not-a-list"}) == 0
    assert _effectiveness_score({"effectiveness_history": [{"outcome": "followed"}, "junk"]}) == 1


# ════════════════════════════════════════════════════════════════════════
# task 5.3：render_lesson_block
# ════════════════════════════════════════════════════════════════════════
def test_render_empty_selection_returns_empty_string():
    assert LMR.render_lesson_block(()) == ""
    assert LMR.render_lesson_block([]) == ""


def test_render_single_lesson_format():
    entries = (_entry(
        lesson_id="lesson_py_test",
        trigger="when adding new test infrastructure",
        corrective_action="prefer existing pytest fixtures",
        non_applicability_when="skip when stage=post_terminal"),)
    out = LMR.render_lesson_block(entries)
    assert "## Applicable lessons from prior PRDs (apply where relevant)" in out
    assert "**lesson_py_test**" in out
    assert "trigger: when adding new test infrastructure" in out
    assert "action: prefer existing pytest fixtures" in out
    assert "skip when: skip when stage=post_terminal" in out


def test_render_full_output_sample_matches_docstring_contract():
    """task 5.3 docstring 给的样例格式作为契约对齐——逐行断言。"""
    entries = (
        _entry(lesson_id="lesson_a",
               trigger="when X happens",
               corrective_action="do Y to prevent recurrence",
               non_applicability_when="stage=post_terminal"),
        _entry(lesson_id="lesson_b",
               trigger="when Z happens",
               corrective_action="do W",
               non_applicability_when="path=src/legacy"),
    )
    out = LMR.render_lesson_block(entries)
    expected = (
        "## Applicable lessons from prior PRDs (apply where relevant)\n"
        "- **lesson_a** — trigger: when X happens\n"
        "  - action: do Y to prevent recurrence\n"
        "  - skip when: stage=post_terminal\n"
        "- **lesson_b** — trigger: when Z happens\n"
        "  - action: do W\n"
        "  - skip when: path=src/legacy"
    )
    assert out == expected


def test_render_excludes_evidence_refs():
    """design「Evidence bodies and historical narratives are not injected」—— evidence_refs 不出现。"""
    e = _entry()
    e["evidence_refs"] = ({"artifact": "secret-artifact", "digest": "abc123"},)
    out = LMR.render_lesson_block((e,))
    assert "evidence_refs" not in out
    assert "secret-artifact" not in out
    assert "abc123" not in out


def test_render_excludes_pattern_description():
    """historical narratives（pattern_description）绝不注入。"""
    e = _entry()
    e["pattern_description"] = "once upon a time in a legacy code review..."
    out = LMR.render_lesson_block((e,))
    assert "pattern_description" not in out
    assert "once upon a time" not in out


def test_render_excludes_effectiveness_history():
    e = _entry()
    e["effectiveness_history"] = ({"outcome": "followed", "prd_id": "prd-old"},)
    out = LMR.render_lesson_block((e,))
    assert "effectiveness_history" not in out
    assert "prd-old" not in out


def test_render_excludes_source_candidate_ids():
    e = _entry()
    e["source_candidate_ids"] = ("cand-1", "cand-2")
    out = LMR.render_lesson_block((e,))
    assert "source_candidate_ids" not in out
    assert "cand-1" not in out
    assert "cand-2" not in out


def test_render_excludes_supporting_prd_ids():
    e = _entry()
    e["supporting_prd_ids"] = ("prd-foo", "prd-bar")
    out = LMR.render_lesson_block((e,))
    assert "supporting_prd_ids" not in out
    assert "prd-foo" not in out


def test_render_excludes_equivalence_key():
    e = _entry()
    e["equivalence_key"] = "proj-x:deadbeefdeadbeef"
    out = LMR.render_lesson_block((e,))
    assert "equivalence_key" not in out
    assert "deadbeef" not in out


def test_render_excludes_confidence_and_numeric_audit_fields():
    e = _entry()
    e["confidence"] = 0.873
    e["usage_count"] = 42
    e["contradiction_count"] = 3
    e["verified_support_count"] = 5
    out = LMR.render_lesson_block((e,))
    assert "0.873" not in out
    assert "usage_count" not in out
    assert "contradiction_count" not in out
    assert "verified_support_count" not in out


def test_render_only_injects_four_canonical_fields():
    """每条只含 lesson_id/trigger/corrective_action/non_applicability_when（design 决策#5）。"""
    e = _entry(lesson_id="L", trigger="T", corrective_action="A", non_applicability_when="N")
    out = LMR.render_lesson_block((e,))
    # 四个字段都应出现
    for token in ("L", "T", "A", "N"):
        assert token in out


def test_render_skips_non_dict_entries_safely():
    """selected 含非 dict 元素（理论上不会发生，但防御 malformed 上游）→ 跳过，不抛。"""
    entries = (_entry(lesson_id="ok"), "junk", None)
    out = LMR.render_lesson_block(entries)
    assert "**ok**" in out
    assert "junk" not in out


def test_render_caps_at_five_via_retrieve_pipeline():
    """端到端：6 候选 → retrieve cap 5 → render 输出 5 条 ``- **``。"""
    entries = [_entry(lesson_id=f"lesson_{i}") for i in range(6)]
    r = LMR.retrieve_lessons(entries, _task())
    out = LMR.render_lesson_block(r.selected)
    # 5 lessons → 5 行以 ``- **`` 开头（header 不含）
    assert out.count("\n- **") == 5


# ════════════════════════════════════════════════════════════════════════
# task 5.4：inject_into_prompt
# ════════════════════════════════════════════════════════════════════════
def test_inject_empty_block_identity_noop():
    """lesson_block=="" → 原样返回 dev_prompt（fail-open delivery：retrieval 故障 → 不注入）。"""
    prompt = "Implement feature X with tests."
    assert LMR.inject_into_prompt(prompt, "") == prompt


def test_inject_whitespace_only_block_identity_noop():
    """lesson_block 仅空白 → 视为空（no-op）。"""
    prompt = "Implement feature X."
    assert LMR.inject_into_prompt(prompt, "   \n  ") == prompt


def test_inject_non_empty_appends_with_separator():
    """非空 lesson_block → append 到 dev_prompt 末尾，用 \\n\\n 分隔。"""
    prompt = "Implement feature X."
    block = "## Applicable lessons from prior PRDs (apply where relevant)\n- **L** — trigger: T"
    out = LMR.inject_into_prompt(prompt, block)
    assert out == f"{prompt}\n\n{block}"


def test_inject_preserves_dev_prompt_content():
    """注入不破坏 dev_prompt 原内容（控制面构造的字符串语义不动）。"""
    prompt = "## Task\nImplement Y.\n\n## Constraints\nNo regex for HTML."
    block = "## Applicable lessons from prior PRDs (apply where relevant)\n- **L** — trigger: T"
    out = LMR.inject_into_prompt(prompt, block)
    assert out.startswith(prompt)
    assert out.endswith(block)


def test_inject_handles_non_string_dev_prompt_safely():
    """dev_prompt 非 str（接线异常）→ 视为空 str，不抛。"""
    out = LMR.inject_into_prompt(None, "## lessons")  # type: ignore[arg-type]
    assert out == "\n\n## lessons"


# ════════════════════════════════════════════════════════════════════════
# task 5.5：load_catalog_for_retrieval（fail-open wrapper）
# ════════════════════════════════════════════════════════════════════════
def test_load_catalog_unavailable_when_file_missing(tmp_path):
    """catalog 不存在（首跑 / 项目无 lessons）→ degraded_class=catalog_unavailable + entries=()。"""
    src = LMR.load_catalog_for_retrieval(tmp_path / "state", "proj-x")
    assert src.entries == ()
    assert src.degraded_class == "catalog_unavailable"


def test_load_catalog_read_error_on_corrupt_json(tmp_path):
    """catalog 文件 corrupted（非法 JSON）→ degraded_class=catalog_read_error + entries=()。"""
    state = tmp_path / "state"
    (state / "lessons" / "catalog").mkdir(parents=True)
    (state / "lessons" / "catalog" / "proj-x.json").write_text("{not valid json}", encoding="utf-8")
    src = LMR.load_catalog_for_retrieval(state, "proj-x")
    assert src.entries == ()
    assert src.degraded_class == "catalog_read_error"


def test_load_catalog_read_error_when_entries_field_missing(tmp_path):
    """catalog JSON 合法但缺 entries 字段 → 视为 malformed → catalog_read_error。"""
    state = tmp_path / "state"
    (state / "lessons" / "catalog").mkdir(parents=True)
    payload = json.dumps({"schema_version": 1, "project_id": "proj-x"})  # 无 entries
    (state / "lessons" / "catalog" / "proj-x.json").write_text(payload, encoding="utf-8")
    src = LMR.load_catalog_for_retrieval(state, "proj-x")
    assert src.entries == ()
    assert src.degraded_class == "catalog_read_error"


def test_load_catalog_read_error_when_entries_not_list(tmp_path):
    """entries 字段类型错（如 dict 而非 list）→ catalog_read_error（绝不部分信任）。"""
    state = tmp_path / "state"
    (state / "lessons" / "catalog").mkdir(parents=True)
    payload = json.dumps({"schema_version": 1, "project_id": "proj-x", "entries": {"not": "a list"}})
    (state / "lessons" / "catalog" / "proj-x.json").write_text(payload, encoding="utf-8")
    src = LMR.load_catalog_for_retrieval(state, "proj-x")
    assert src.degraded_class == "catalog_read_error"


def test_load_catalog_normal_returns_entries(tmp_path):
    """正常 catalog → entries tuple + degraded_class=None。"""
    state = tmp_path / "state"
    (state / "lessons" / "catalog").mkdir(parents=True)
    e = _entry(lesson_id="lesson_a")
    payload = json.dumps({"schema_version": 1, "project_id": "proj-x", "entries": [e]})
    (state / "lessons" / "catalog" / "proj-x.json").write_text(payload, encoding="utf-8")
    src = LMR.load_catalog_for_retrieval(state, "proj-x")
    assert src.degraded_class is None
    assert len(src.entries) == 1
    assert src.entries[0]["lesson_id"] == "lesson_a"


# ════════════════════════════════════════════════════════════════════════
# task 5.5：retrieve_from_source（fail-open 串联）
# ════════════════════════════════════════════════════════════════════════
def test_retrieve_from_source_unavailable_returns_empty_with_degraded():
    src = LMR.RetrievalSource(entries=(), degraded_class="catalog_unavailable")
    r = LMR.retrieve_from_source(src, _task())
    assert r.selected == ()
    assert r.selected_lesson_ids == ()
    assert r.degraded_class == "catalog_unavailable"


def test_retrieve_from_source_read_error_returns_empty_with_degraded():
    src = LMR.RetrievalSource(entries=(), degraded_class="catalog_read_error")
    r = LMR.retrieve_from_source(src, _task())
    assert r.selected == ()
    assert r.degraded_class == "catalog_read_error"


def test_retrieve_from_source_normal_proceeds_to_retrieve_lessons():
    """catalog 正常（degraded_class=None + entries 非空）→ 走 retrieve_lessons。"""
    e = _entry(lesson_id="lesson_a")
    src = LMR.RetrievalSource(entries=(e,), degraded_class=None)
    r = LMR.retrieve_from_source(src, _task())
    assert r.degraded_class is None
    assert r.selected_lesson_ids == ("lesson_a",)


def test_retrieve_from_source_empty_entries_short_circuits():
    """entries 空（即使 degraded_class=None，如合法空 catalog）→ 直接返回空 selected。"""
    src = LMR.RetrievalSource(entries=(), degraded_class=None)
    r = LMR.retrieve_from_source(src, _task())
    assert r.selected == ()
    assert r.degraded_class is None


# ════════════════════════════════════════════════════════════════════════
# 端到端：derive → load → retrieve → render → inject（fail-open pipeline）
# ════════════════════════════════════════════════════════════════════════
def test_pipeline_normal_dispatch_injection(tmp_path):
    """端到端 happy path：profile + prd → metadata → catalog load → retrieve → render → inject。"""
    state = tmp_path / "state"
    (state / "lessons" / "catalog").mkdir(parents=True)
    e_active = _entry(
        lesson_id="lesson_py_test", project_id="pa",
        applies_when_tags=("python", "test_infra"),
        verified_support_count=3,
        trigger="when adding pytest fixtures",
        corrective_action="reuse conftest.py fixtures",
        non_applicability_when="skip when stage=post_terminal")
    e_retired = _entry(lesson_id="lesson_old", project_id="pa", state="retired",
                       applies_when_tags=("python",))   # filtered (retired)
    e_unrelated = _entry(lesson_id="lesson_go", project_id="pa",
                         applies_when_tags=("golang",))  # filtered (no overlap)
    payload = json.dumps({
        "schema_version": 1, "project_id": "pa",
        "entries": [e_active, e_retired, e_unrelated],
    })
    (state / "lessons" / "catalog" / "pa.json").write_text(payload, encoding="utf-8")

    profile = {"language": "python", "has_ci": True, "dependency_management": "poetry"}
    prd = {"acceptance_criteria": ["all tests green", "lint clean"]}
    md = LMR.derive_task_metadata(project_profile=profile, prd=prd, project_id="pa")
    assert md["tags"] == {"python", "ci_gate", "dependency_mgmt", "test_infra"}

    src = LMR.load_catalog_for_retrieval(state, "pa")
    assert src.degraded_class is None

    r = LMR.retrieve_from_source(src, md)
    assert r.degraded_class is None
    assert r.selected_lesson_ids == ("lesson_py_test",)   # 仅 active + overlap

    block = LMR.render_lesson_block(r.selected)
    assert "lesson_py_test" in block
    assert "reuse conftest.py fixtures" in block

    prompt = "Implement feature Z."
    injected = LMR.inject_into_prompt(prompt, block)
    assert injected.startswith(prompt)
    assert "lesson_py_test" in injected


def test_pipeline_fail_open_on_missing_catalog_does_not_block_dispatch(tmp_path):
    """design 决策#7：retrieval 故障 → 不注入 + degraded，绝不阻断 dispatch。

    场景：catalog 不存在（首跑）→ degraded_class=catalog_unavailable → inject no-op →
    dev prompt 原样，dispatch 正常跑。
    """
    state = tmp_path / "state"   # 无 catalog 文件
    profile = {"language": "python"}
    md = LMR.derive_task_metadata(project_profile=profile, prd={}, project_id="pa")
    src = LMR.load_catalog_for_retrieval(state, "pa")
    assert src.degraded_class == "catalog_unavailable"

    r = LMR.retrieve_from_source(src, md)
    assert r.degraded_class == "catalog_unavailable"
    assert r.selected == ()

    block = LMR.render_lesson_block(r.selected)
    assert block == ""

    prompt = "Implement feature W."
    injected = LMR.inject_into_prompt(prompt, block)
    assert injected == prompt   # identity（no-op）—— dispatch 不被阻断


def test_pipeline_fail_open_on_corrupt_catalog_does_not_block_dispatch(tmp_path):
    """corrupt catalog → degraded_class=catalog_read_error → inject no-op → dispatch 继续。"""
    state = tmp_path / "state"
    (state / "lessons" / "catalog").mkdir(parents=True)
    (state / "lessons" / "catalog" / "pa.json").write_text("{corrupt", encoding="utf-8")

    md = LMR.derive_task_metadata(project_profile={}, prd={}, project_id="pa")
    src = LMR.load_catalog_for_retrieval(state, "pa")
    assert src.degraded_class == "catalog_read_error"

    r = LMR.retrieve_from_source(src, md)
    assert r.selected == ()

    block = LMR.render_lesson_block(r.selected)
    assert block == ""

    injected = LMR.inject_into_prompt("do work", block)
    assert injected == "do work"
