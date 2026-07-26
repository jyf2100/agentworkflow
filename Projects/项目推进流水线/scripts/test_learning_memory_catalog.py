#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_learning_memory_catalog.py — add-cross-prd-learning-memory Section 2.4 单测。

锁定 catalog projection + atomic replacement 的核心契约（spec design 决策#3「catalog 是 atomic,
rebuildable projection」+ 决策#7「fail-open delivery, fail-closed memory」）：

    * **deterministic replay**：相同 facts → byte-identical catalog（sorted keys / 稳定顺序）；
    * **duplicate-event idempotency**：相同 event_id 重放不重复应用；
    * **malformed-middle-record fail-closed**：中部损坏 → 整个 projection 失败，绝不部分信任；
    * **incomplete-trailing-record recovery**：末尾半行（崩溃写一半）→ 截断后正常 replay；
    * **atomic replacement**：temp + fsync + os.replace（crash 后旧 catalog 仍在，可重建）；
    * **fail-open delivery**：存储层 corruption 不抛主路径，返回 degraded CatalogResult。

task 2.4 的核心难点是「deterministic + fail-closed middle + recoverable trailing」三组合——
journal.py 的 _scan 模型直接复用：JSONDecodeError 在最后非空行=tail_truncated（容忍）；
非末尾=middle corruption（fail-closed）；complete-JSON-but-schema-invalid=始终 fail-closed（污染）。

跑：python3 -m pytest scripts/test_learning_memory_catalog.py -q
AAA 结构。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import learning_memory_catalog as LMC  # noqa: E402
import learning_memory_schema as LM  # noqa: E402
import learning_memory_store as LMS  # noqa: E402


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


def _usage(eid="u-1", lesson_id="lesson-1", prd_id="prd-x",
           action_observed=True, failure_recurred=False, outcome="followed",
           timestamp="2026-07-26T03:17:00Z"):
    """构造 schema-valid UsageOutcome（Section 6 fixture）。"""
    return LM.UsageOutcome(
        event_id=eid, timestamp=timestamp,
        project_id="proj-a", lesson_id=lesson_id, prd_id=prd_id,
        action_observed=action_observed, failure_recurred=failure_recurred,
        outcome=outcome, schema_version=1)


def _seed_promoted_lesson(state, project_id="proj-a", *, prd_a="prd-1", prd_b="prd-2",
                          candidate_kwargs=None):
    """种两条等效 candidate（不同 prd_id，达标 promotion ≥2 PRD）→ 返回 lesson_id。"""
    kw = candidate_kwargs or _valid_candidate_kwargs()
    c1 = LM.LessonCandidate(**{**kw, "prd_id": prd_a})
    c2 = LM.LessonCandidate(**{**kw, "prd_id": prd_b,
                               "evidence_refs": ({"digest": "sha256:bb", "kind": "test_output", "path": "sha256/bb"},)})
    LMS.append_candidate(str(state), project_id, c1, run_id="r", timestamp="t1")
    LMS.append_candidate(str(state), project_id, c2, run_id="r", timestamp="t2")
    return LMC.lesson_id_from_equivalence_key(LM.derive_equivalence_key(c1))


# ════════════════════════════════════════════════════════════════════════
# task 2.4：deterministic replay（相同 facts → byte-identical catalog）
# ════════════════════════════════════════════════════════════════════════
def test_catalog_replay_is_byte_identical_for_same_facts(tmp_path):
    """**spec 核心**：相同 facts（candidates + events）→ byte-identical catalog JSON。

    多次 rebuild 输出完全一致（sorted keys / 稳定顺序 / 确定性聚合）——catalog 是 projection，
    可随时从 facts rebuild，且 rebuild 结果与上次一致。"""
    state1 = tmp_path / "s1"
    state2 = tmp_path / "s2"
    for state in (state1, state2):
        LMS.append_candidate(str(state), "proj-a", _candidate(prd_id="prd-1"),
                             run_id="r", timestamp="t1")
        LMS.append_candidate(str(state), "proj-a",
                             _candidate(prd_id="prd-2",
                                        evidence_refs=({"digest": "sha256:bb", "kind": "test_output", "path": "sha256/bb"},)),
                             run_id="r", timestamp="t2")
        LMS.append_lifecycle_event(str(state), "proj-a",
                                   _event(eid="e1", lesson_id="lesson-1"),
                                   run_id="r")
    r1 = LMC.rebuild_catalog(str(state1), "proj-a")
    r2 = LMC.rebuild_catalog(str(state2), "proj-a")
    assert r1.ok and r2.ok
    bytes1 = (state1 / "lessons" / "catalog" / "proj-a.json").read_bytes()
    bytes2 = (state2 / "lessons" / "catalog" / "proj-a.json").read_bytes()
    assert bytes1 == bytes2, "相同 facts 的 catalog rebuild 必须 byte-identical"


def test_catalog_replay_order_independent(tmp_path):
    """facts 顺序不影响 catalog 输出——确定性聚合（spec「ordering is the only model-permitted freedom」
    在 catalog 层进一步稳定：append 顺序也不影响最终 projection）。"""
    state_a = tmp_path / "sa"
    state_b = tmp_path / "sb"
    # state_a: prd-1 first, then prd-2
    LMS.append_candidate(str(state_a), "proj-a", _candidate(prd_id="prd-1"),
                         run_id="r", timestamp="t1")
    LMS.append_candidate(str(state_a), "proj-a",
                         _candidate(prd_id="prd-2",
                                    evidence_refs=({"digest": "sha256:b2", "kind": "test_output", "path": "sha256/b2"},)),
                         run_id="r", timestamp="t2")
    # state_b: prd-2 first, then prd-1
    LMS.append_candidate(str(state_b), "proj-a",
                         _candidate(prd_id="prd-2",
                                    evidence_refs=({"digest": "sha256:b2", "kind": "test_output", "path": "sha256/b2"},)),
                         run_id="r", timestamp="t2")
    LMS.append_candidate(str(state_b), "proj-a", _candidate(prd_id="prd-1"),
                         run_id="r", timestamp="t1")
    LMC.rebuild_catalog(str(state_a), "proj-a")
    LMC.rebuild_catalog(str(state_b), "proj-a")
    bytes_a = (state_a / "lessons" / "catalog" / "proj-a.json").read_bytes()
    bytes_b = (state_b / "lessons" / "catalog" / "proj-a.json").read_bytes()
    assert bytes_a == bytes_b


def test_catalog_aggregates_candidates_by_equivalence_key(tmp_path):
    """task 2.4：replay 把 equivalence_key 相同的 candidates 聚合成一条 catalog entry
    （source_candidate_ids 全保留，spec「Merges preserve all source candidate IDs」）。"""
    state = tmp_path / "s"
    LMS.append_candidate(str(state), "proj-a", _candidate(prd_id="prd-1"),
                         run_id="r", timestamp="t1")
    LMS.append_candidate(str(state), "proj-a",
                         _candidate(prd_id="prd-2",
                                    evidence_refs=({"digest": "sha256:bb", "kind": "test_output", "path": "sha256/bb"},)),
                         run_id="r", timestamp="t2")
    result = LMC.rebuild_catalog(str(state), "proj-a")
    assert result.ok
    snap = result.snapshot
    assert snap is not None
    # 同 equivalence_key → 聚合为一条 entry
    assert len(snap.entries) == 1
    entry = snap.entries[0]
    assert len(entry["source_candidate_ids"]) == 2
    assert set(entry["supporting_prd_ids"]) == {"prd-1", "prd-2"}


def test_catalog_replay_includes_equivalence_key_in_entries(tmp_path):
    """task 2.4 + 2.3：catalog entry 含 equivalence_key（rebuildable，与 Section 1 派生值一致）。"""
    state = tmp_path / "s"
    c = _candidate(prd_id="prd-1")
    LMS.append_candidate(str(state), "proj-a", c, run_id="r", timestamp="t1")
    # Section 3：≥2 distinct PRD 才进 active catalog projection（单 PRD 被 promotion gate 过滤）
    c2 = _candidate(prd_id="prd-2",
                    evidence_refs=({"digest": "sha256:bb", "kind": "test_output", "path": "sha256/bb"},))
    LMS.append_candidate(str(state), "proj-a", c2, run_id="r", timestamp="t2")
    result = LMC.rebuild_catalog(str(state), "proj-a")
    snap = result.snapshot
    expected_key = LM.derive_equivalence_key(c)
    assert snap.entries[0]["equivalence_key"] == expected_key


# ════════════════════════════════════════════════════════════════════════
# task 2.4：duplicate-event idempotency
# ════════════════════════════════════════════════════════════════════════
def test_duplicate_event_id_is_idempotent(tmp_path):
    """**spec 核心**：相同 event_id 重放 → 不重复应用（dedupe by event_id）。

    场景：crash 后 reconcile 重放 events.jsonl，可能重复 append 同一 event_id——catalog projection
    必须幂等（同一 event_id 多次出现只应用一次）。"""
    state = tmp_path / "s"
    c = _candidate(prd_id="prd-1")
    LMS.append_candidate(str(state), "proj-a", c, run_id="r", timestamp="t")
    # event.lesson_id 必须对齐 candidate 派生的 lesson_id（catalog 才能匹配 entry）
    lesson_id = LMC.lesson_id_from_equivalence_key(LM.derive_equivalence_key(c))
    # 直接 append 同一 event_id 两次（模拟 reconcile 重放）
    LMS.append_lifecycle_event(str(state), "proj-a",
                               _event(eid="evt-dupe", lesson_id=lesson_id, event_type="confirmed"),
                               run_id="r")
    LMS.append_lifecycle_event(str(state), "proj-a",
                               _event(eid="evt-dupe", lesson_id=lesson_id, event_type="confirmed"),
                               run_id="r")
    result = LMC.rebuild_catalog(str(state), "proj-a")
    assert result.ok
    # snapshot 报告的去重后 event count = 1（不是 2）
    assert result.snapshot.source_event_count == 1


def test_different_event_ids_both_applied(tmp_path):
    """反例对照：不同 event_id 都应用（不是「所有 event 都丢」）。"""
    state = tmp_path / "s"
    c = _candidate(prd_id="prd-1")
    LMS.append_candidate(str(state), "proj-a", c, run_id="r", timestamp="t")
    lesson_id = LMC.lesson_id_from_equivalence_key(LM.derive_equivalence_key(c))
    LMS.append_lifecycle_event(str(state), "proj-a",
                               _event(eid="e-a", lesson_id=lesson_id, event_type="confirmed"),
                               run_id="r")
    LMS.append_lifecycle_event(str(state), "proj-a",
                               _event(eid="e-b", lesson_id=lesson_id, event_type="confidence_reduced"),
                               run_id="r")
    result = LMC.rebuild_catalog(str(state), "proj-a")
    assert result.snapshot.source_event_count == 2


# ════════════════════════════════════════════════════════════════════════
# task 2.4：malformed-middle-record fail-closed + incomplete-trailing recovery
# ════════════════════════════════════════════════════════════════════════
def test_malformed_middle_record_fails_closed(tmp_path):
    """**spec 硬约束**：JSONL 中部某行损坏 → fail-closed（绝不部分信任，绝不静默跳过）。

    catalog projection 必须整体失败，绝不基于残缺事实生成 partial catalog（design 决策#7）。"""
    state = tmp_path / "s"
    LMS.append_candidate(str(state), "proj-a", _candidate(prd_id="prd-1"),
                         run_id="r", timestamp="t")
    p = state / "lessons" / "candidates" / "proj-a.jsonl"
    # 在末尾追加：中部坏行 + 合法行（坏行非末尾 → middle corruption）
    cand_kwargs = _valid_candidate_kwargs(prd_id="prd-3")
    good = json.dumps({
        "schema_version": LMS.LESSONS_SCHEMA_VERSION, "kind": "candidate",
        "candidate_id": "c3", "run_id": "r", "timestamp": "t3",
        "equivalence_key": "proj-a:k3",
        "candidate": cand_kwargs,
    })
    with open(p, "a", encoding="utf-8") as f:
        f.write("GARBAGE_MIDDLE_LINE\n")
        f.write(good + "\n")
    result = LMC.rebuild_catalog(str(state), "proj-a")
    # fail-closed: 不生成 catalog（或 catalog 不变），返回 degraded
    assert result.ok is False
    assert result.degraded_class is not None
    assert "corruption" in result.degraded_class or "malformed" in result.degraded_class
    assert result.snapshot is None


def test_incomplete_trailing_record_recoverable(tmp_path):
    """**spec 硬约束**：JSONL 末尾半行（crash 写一半）→ 截断后正常 replay（trailing 可恢复）。

    关键区分：trailing 可恢复（崩溃只可能截断最后一条 append），middle 不可恢复（committed history 污染）。
    journal.py 已建立此模型；catalog 复用同一判定。"""
    state = tmp_path / "s"
    LMS.append_candidate(str(state), "proj-a", _candidate(prd_id="prd-1"),
                         run_id="r", timestamp="t1")
    LMS.append_candidate(str(state), "proj-a",
                         _candidate(prd_id="prd-2",
                                    evidence_refs=({"digest": "sha256:b2", "kind": "test_output", "path": "sha256/b2"},)),
                         run_id="r", timestamp="t2")
    # 末尾追加半行（crash 截断第三条 append）
    p = state / "lessons" / "candidates" / "proj-a.jsonl"
    with open(p, "a", encoding="utf-8") as f:
        f.write('{"schema_version": 1, "kind": "candidate", "candi')   # 半行
    result = LMC.rebuild_catalog(str(state), "proj-a")
    assert result.ok is True
    assert result.snapshot.tail_truncated_candidates is True
    # 截断的第三条不进 catalog；前两条聚合为一条 entry
    assert len(result.snapshot.entries) == 1
    assert set(result.snapshot.entries[0]["supporting_prd_ids"]) == {"prd-1", "prd-2"}


def test_malformed_middle_in_events_also_fails_closed(tmp_path):
    """events.jsonl 中部损坏同样 fail-closed（events 也是 committed history）。"""
    state = tmp_path / "s"
    LMS.append_candidate(str(state), "proj-a", _candidate(prd_id="prd-1"),
                         run_id="r", timestamp="t")
    LMS.append_lifecycle_event(str(state), "proj-a",
                               _event(eid="e-1", lesson_id="lesson-1"),
                               run_id="r")
    p = state / "lessons" / "events" / "proj-a.jsonl"
    with open(p, "a", encoding="utf-8") as f:
        f.write("MIDDLE_GARBAGE_IN_EVENTS\n")
        f.write(json.dumps({
            "schema_version": LMS.LESSONS_SCHEMA_VERSION, "kind": "event",
            "run_id": "r",
            "event": {"event_id": "e-2", "timestamp": "t", "project_id": "proj-a",
                      "lesson_id": "lesson-1", "event_type": "confirmed",
                      "payload": {}, "schema_version": 1},
        }) + "\n")
    result = LMC.rebuild_catalog(str(state), "proj-a")
    assert result.ok is False
    assert result.degraded_class is not None


# ════════════════════════════════════════════════════════════════════════
# task 2.4：atomic replacement + crash recovery
# ════════════════════════════════════════════════════════════════════════
def test_catalog_atomic_replacement_overwrites_old(tmp_path):
    """task 2.4：rebuild 用 temp+fsync+os.replace 原子替换旧 catalog（design 决策#3 atomic projection）。"""
    state = tmp_path / "s"
    cat_p = state / "lessons" / "catalog" / "proj-a.json"
    cat_p.parent.mkdir(parents=True, exist_ok=True)
    cat_p.write_text('{"old": "stale catalog that should be replaced"}', encoding="utf-8")
    LMS.append_candidate(str(state), "proj-a", _candidate(prd_id="prd-1"),
                         run_id="r", timestamp="t")
    LMC.rebuild_catalog(str(state), "proj-a")
    new_content = cat_p.read_text(encoding="utf-8")
    assert "old" not in new_content or "stale" not in new_content
    parsed = json.loads(new_content)
    assert parsed["project_id"] == "proj-a"


def test_catalog_rebuild_idempotent_after_repeated_calls(tmp_path):
    """task 2.4：多次 rebuild 不产生副作用（catalog 是纯 projection，幂等）。"""
    state = tmp_path / "s"
    LMS.append_candidate(str(state), "proj-a", _candidate(),
                         run_id="r", timestamp="t")
    r1 = LMC.rebuild_catalog(str(state), "proj-a")
    bytes1 = (state / "lessons" / "catalog" / "proj-a.json").read_bytes()
    r2 = LMC.rebuild_catalog(str(state), "proj-a")
    bytes2 = (state / "lessons" / "catalog" / "proj-a.json").read_bytes()
    assert r1.ok and r2.ok
    assert bytes1 == bytes2


def test_catalog_uses_temp_then_rename_for_atomicity(tmp_path, monkeypatch):
    """task 2.4：rebuild 必须用 os.replace 完成 atomic replacement（POSIX rename 原子保证）。

    crash 在写 catalog 中途不应留下半写的 catalog——temp 写完整 + fsync + rename 才原子。"""
    replaced: list[str] = []
    real_replace = LMC.os.replace
    def spy_replace(src, dst):
        replaced.append(str(src))
        # temp 文件确实存在于同目录
        assert Path(src).exists()
        return real_replace(src, dst)
    monkeypatch.setattr(LMC.os, "replace", spy_replace)
    state = tmp_path / "s"
    LMS.append_candidate(str(state), "proj-a", _candidate(),
                         run_id="r", timestamp="t")
    LMC.rebuild_catalog(str(state), "proj-a")
    assert len(replaced) == 1, "rebuild_catalog 未走 os.replace 原子替换"


# ════════════════════════════════════════════════════════════════════════
# task 2.4：fail-open delivery（design 决策#7）
# ════════════════════════════════════════════════════════════════════════
def test_project_catalog_fail_open_returns_degraded_not_raise(tmp_path):
    """**design 决策#7**：存储层 corruption → 不抛主路径，返回 degraded CatalogResult。

    memory 故障不改 PRD 结果——调用方拿 degraded_class 记 learning_memory_degraded 继续跑。"""
    state = tmp_path / "s"
    LMS.append_candidate(str(state), "proj-a", _candidate(prd_id="prd-1"),
                         run_id="r", timestamp="t")
    p = state / "lessons" / "candidates" / "proj-a.jsonl"
    with open(p, "a", encoding="utf-8") as f:
        f.write("MIDDLE_GARBAGE\n")
        f.write('{"schema_version": 1, "kind": "candidate", "candidate_id": "c2", '
                '"run_id": "r", "timestamp": "t", "equivalence_key": "proj-a:k2", '
                '"candidate": ' + json.dumps(_valid_candidate_kwargs(prd_id="prd-2")) + '}\n')
    # 不 raise——返回 degraded
    result = LMC.project_catalog(str(state), "proj-a")
    assert result.ok is False
    assert result.degraded_class is not None
    assert result.snapshot is None


def test_project_catalog_missing_files_returns_empty_ok(tmp_path):
    """无 facts 文件（首次运行，未 reflect 过）→ 空快照 + ok=True（非 degraded）。"""
    state = tmp_path / "s"
    result = LMC.project_catalog(str(state), "proj-a")
    assert result.ok is True
    assert result.snapshot is not None
    assert result.snapshot.entries == ()


def test_catalog_rebuild_on_corruption_leaves_old_catalog_intact(tmp_path):
    """task 2.4 + 决策#7：corruption 时 rebuild 不写 partial catalog——旧 catalog 文件保持原样。

    catalog 是 projection：rebuild 失败绝不留下「半信任」的新 catalog；旧 catalog 可继续被读（虽然 stale），
    或调用方记 degraded 跳过。"""
    state = tmp_path / "s"
    cat_p = state / "lessons" / "catalog" / "proj-a.json"
    cat_p.parent.mkdir(parents=True, exist_ok=True)
    cat_p.write_text('{"project_id": "proj-a", "preserved": true}', encoding="utf-8")
    LMS.append_candidate(str(state), "proj-a", _candidate(prd_id="prd-1"),
                         run_id="r", timestamp="t")
    # 制造真正的中部损坏：GARBAGE 后再追一条合法行，让 GARBAGE 不在末尾（否则被当 tail_truncated 容忍）
    p = state / "lessons" / "candidates" / "proj-a.jsonl"
    cand_kwargs = _valid_candidate_kwargs(prd_id="prd-2")
    good_after = json.dumps({
        "schema_version": LMS.LESSONS_SCHEMA_VERSION, "kind": "candidate",
        "candidate_id": "c2", "run_id": "r", "timestamp": "t",
        "equivalence_key": "proj-a:k2",
        "candidate": cand_kwargs,
    })
    with open(p, "a", encoding="utf-8") as f:
        f.write("MIDDLE_GARBAGE\n")
        f.write(good_after + "\n")
    result = LMC.rebuild_catalog(str(state), "proj-a")
    assert result.ok is False
    # 旧 catalog 未被覆盖（rebuild 失败时不写）
    assert cat_p.read_text(encoding="utf-8") == '{"project_id": "proj-a", "preserved": true}'


# ════════════════════════════════════════════════════════════════════════
# task 2.4：catalog 输出格式（rebuildable + 结构稳定）
# ════════════════════════════════════════════════════════════════════════
def test_catalog_file_is_sorted_json_with_schema_version(tmp_path):
    """task 2.4：catalog 文件是合法 JSON，schema_version + project_id 字段在，keys 排序（byte-stable）。"""
    state = tmp_path / "s"
    LMS.append_candidate(str(state), "proj-a", _candidate(),
                         run_id="r", timestamp="t")
    LMC.rebuild_catalog(str(state), "proj-a")
    cat = json.loads((state / "lessons" / "catalog" / "proj-a.json").read_text(encoding="utf-8"))
    assert cat["schema_version"] == LMC.CATALOG_SCHEMA_VERSION
    assert cat["project_id"] == "proj-a"
    assert "entries" in cat
    # entries 按 lesson_id 排序（稳定）
    if len(cat["entries"]) > 1:
        ids = [e["lesson_id"] for e in cat["entries"]]
        assert ids == sorted(ids)


def test_catalog_entry_has_required_projection_fields(tmp_path):
    """task 2.4：catalog entry 含 ActiveCatalogEntry 必填字段（lesson_id/equivalence_key/source_candidate_ids/...）."""
    state = tmp_path / "s"
    c = _candidate(prd_id="prd-1")
    LMS.append_candidate(str(state), "proj-a", c, run_id="r", timestamp="t1")
    # Section 3：≥2 distinct PRD 才进 active catalog projection（单 PRD 被 promotion gate 过滤）
    c2 = _candidate(prd_id="prd-2",
                    evidence_refs=({"digest": "sha256:bb", "kind": "test_output", "path": "sha256/bb"},))
    LMS.append_candidate(str(state), "proj-a", c2, run_id="r", timestamp="t2")
    LMC.rebuild_catalog(str(state), "proj-a")
    cat = json.loads((state / "lessons" / "catalog" / "proj-a.json").read_text(encoding="utf-8"))
    entry = cat["entries"][0]
    for field in ("lesson_id", "project_id", "equivalence_key",
                  "source_candidate_ids", "supporting_prd_ids",
                  "corrective_action", "trigger", "non_applicability_when",
                  "state", "confidence", "schema_version"):
        assert field in entry, f"catalog entry 缺字段 {field}"


# ════════════════════════════════════════════════════════════════════════
# Section 6 task A：catalog entry 字段扩展（Section 5 retrieval ranking 维度）
# ════════════════════════════════════════════════════════════════════════
def test_catalog_entry_includes_applies_when_tags_sorted_tuple(tmp_path):
    """task A：catalog entry 含 applies_when_tags（sorted union tuple，from candidates）。

    注：equivalence_key 由 (phase, failure_class, corrective_action_class, applicability_signature)
    派生，故两条 candidate 同 equivalence_key ⟺ 同 applicability_signature ⟺ 同 canonical tag set。
    即「union」在 cross-candidate 聚合时其实必然恒等——本测试主要验：sorted tuple 形态 +
    candidate 自身的 applies_when_tags 透传到 catalog entry。
    """
    state = tmp_path / "s"
    # 两条 candidate 同 enum 字段（含 applies_when_tags 顺序不同——canonical 归一）→ 同 equivalence_key
    kw = _valid_candidate_kwargs(applies_when_tags=(LM.AppliesWhenTag.PYTHON, LM.AppliesWhenTag.CI_GATE))
    c1 = LM.LessonCandidate(**{**kw, "prd_id": "prd-1"})
    # tag 顺序不同，canonical 后等价 → 同 equivalence_key
    kw2 = _valid_candidate_kwargs(applies_when_tags=(LM.AppliesWhenTag.CI_GATE, LM.AppliesWhenTag.PYTHON))
    c2 = LM.LessonCandidate(**{**kw2, "prd_id": "prd-2",
                                "evidence_refs": ({"digest": "sha256:bb", "kind": "test_output", "path": "p"},)})
    LMS.append_candidate(str(state), "proj-a", c1, run_id="r", timestamp="t1")
    LMS.append_candidate(str(state), "proj-a", c2, run_id="r", timestamp="t2")
    result = LMC.rebuild_catalog(str(state), "proj-a")
    assert result.ok
    assert len(result.snapshot.entries) == 1   # 聚合为一条
    entry = result.snapshot.entries[0]
    assert "applies_when_tags" in entry
    # sorted tuple（canonical union = {ci_gate, python}）
    assert entry["applies_when_tags"] == ("ci_gate", "python")


def test_catalog_entry_includes_verified_support_count(tmp_path):
    """task A：catalog entry.verified_support_count == len(supporting_prd_ids)。"""
    state = tmp_path / "s"
    _seed_promoted_lesson(state)
    result = LMC.rebuild_catalog(str(state), "proj-a")
    entry = result.snapshot.entries[0]
    assert entry["verified_support_count"] == 2   # prd-1 + prd-2
    assert entry["verified_support_count"] == len(entry["supporting_prd_ids"])


def test_catalog_entry_includes_empty_effectiveness_fields_when_no_usage(tmp_path):
    """task A：无 usage outcome 时 effectiveness_history=() / usage_count=0 / contradiction_count=0 / last_outcome_ts=None。"""
    state = tmp_path / "s"
    _seed_promoted_lesson(state)
    result = LMC.rebuild_catalog(str(state), "proj-a")
    entry = result.snapshot.entries[0]
    assert entry["effectiveness_history"] == ()
    assert entry["usage_count"] == 0
    assert entry["contradiction_count"] == 0
    assert entry["last_outcome_ts"] is None


# ════════════════════════════════════════════════════════════════════════
# Section 6 task 6.2：bounded deterministic confidence update
# ════════════════════════════════════════════════════════════════════════
def test_apply_usage_outcomes_confidence_up_on_followed(tmp_path):
    """task 6.2：每个 followed → confidence += CONFIDENCE_UP_BOUND (0.1)，cap 1.0。"""
    state = tmp_path / "s"
    lesson_id = _seed_promoted_lesson(state, candidate_kwargs=_valid_candidate_kwargs(confidence=0.5))
    LMS.append_usage_outcome(str(state), "proj-a",
                             _usage(eid="u-1", lesson_id=lesson_id, prd_id="prd-3",
                                    action_observed=True, failure_recurred=False, outcome="followed"),
                             run_id="r")
    result = LMC.rebuild_catalog(str(state), "proj-a")
    entry = result.snapshot.entries[0]
    assert entry["confidence"] == pytest.approx(0.6, abs=1e-9)
    assert entry["usage_count"] == 1


def test_apply_usage_outcomes_confidence_caps_at_1(tmp_path):
    """task 6.2：多次 followed → confidence cap 在 1.0（bounded update）。"""
    state = tmp_path / "s"
    lesson_id = _seed_promoted_lesson(state, candidate_kwargs=_valid_candidate_kwargs(confidence=0.9))
    for i in range(5):
        LMS.append_usage_outcome(str(state), "proj-a",
                                 _usage(eid=f"u-{i}", lesson_id=lesson_id, prd_id=f"prd-{i+3}",
                                        outcome="followed", timestamp=f"2026-07-2{i+1}T03:00:00Z"),
                                 run_id="r")
    result = LMC.rebuild_catalog(str(state), "proj-a")
    entry = result.snapshot.entries[0]
    assert entry["confidence"] == 1.0   # 0.9 + 5*0.1 = 1.4 → cap 1.0
    assert entry["usage_count"] == 5


def test_apply_usage_outcomes_confidence_down_on_contradicted(tmp_path):
    """task 6.2：每个 contradicted → confidence -= CONFIDENCE_DOWN_BOUND (0.2)，floor 0.0。"""
    state = tmp_path / "s"
    lesson_id = _seed_promoted_lesson(state, candidate_kwargs=_valid_candidate_kwargs(confidence=0.7))
    LMS.append_usage_outcome(str(state), "proj-a",
                             _usage(eid="u-1", lesson_id=lesson_id, prd_id="prd-3",
                                    action_observed=True, failure_recurred=True, outcome="contradicted"),
                             run_id="r")
    result = LMC.rebuild_catalog(str(state), "proj-a")
    entry = result.snapshot.entries[0]
    assert entry["confidence"] == pytest.approx(0.5, abs=1e-9)   # 0.7 - 0.2 = 0.5


def test_apply_usage_outcomes_confidence_floors_at_0(tmp_path):
    """task 6.2：多次 contradicted → confidence floor 在 0.0（bounded update）。"""
    state = tmp_path / "s"
    lesson_id = _seed_promoted_lesson(state, candidate_kwargs=_valid_candidate_kwargs(confidence=0.3))
    for i in range(5):
        LMS.append_usage_outcome(str(state), "proj-a",
                                 _usage(eid=f"u-{i}", lesson_id=lesson_id, prd_id=f"prd-{i+3}",
                                        outcome="contradicted", timestamp=f"2026-07-2{i+1}T03:00:00Z"),
                                 run_id="r")
    result = LMC.rebuild_catalog(str(state), "proj-a")
    entry = result.snapshot.entries[0]
    assert entry["confidence"] == 0.0   # 0.3 - 5*0.2 = -0.7 → floor 0.0
    assert entry["contradiction_count"] == 5


def test_apply_usage_outcomes_recurrence_prevented_counts_as_up(tmp_path):
    """task 6.2：recurrence_prevented 也走 +CONFIDENCE_UP_BOUND（与 followed 同）。"""
    state = tmp_path / "s"
    lesson_id = _seed_promoted_lesson(state, candidate_kwargs=_valid_candidate_kwargs(confidence=0.5))
    LMS.append_usage_outcome(str(state), "proj-a",
                             _usage(eid="u-1", lesson_id=lesson_id, prd_id="prd-3",
                                    action_observed=True, failure_recurred=False,
                                    outcome="recurrence_prevented"),
                             run_id="r")
    result = LMC.rebuild_catalog(str(state), "proj-a")
    entry = result.snapshot.entries[0]
    assert entry["confidence"] == pytest.approx(0.6, abs=1e-9)


def test_apply_usage_outcomes_recurrence_observed_counts_as_down(tmp_path):
    """task 6.2：recurrence_observed 走 -CONFIDENCE_DOWN_BOUND（与 contradicted 同）。"""
    state = tmp_path / "s"
    lesson_id = _seed_promoted_lesson(state, candidate_kwargs=_valid_candidate_kwargs(confidence=0.7))
    LMS.append_usage_outcome(str(state), "proj-a",
                             _usage(eid="u-1", lesson_id=lesson_id, prd_id="prd-3",
                                    action_observed=False, failure_recurred=True,
                                    outcome="recurrence_observed"),
                             run_id="r")
    result = LMC.rebuild_catalog(str(state), "proj-a")
    entry = result.snapshot.entries[0]
    assert entry["confidence"] == pytest.approx(0.5, abs=1e-9)


def test_apply_usage_outcomes_not_observed_does_not_change_confidence(tmp_path):
    """task 6.2：not_observed → confidence 不变（absent evidence ≠ disobedience）。"""
    state = tmp_path / "s"
    lesson_id = _seed_promoted_lesson(state, candidate_kwargs=_valid_candidate_kwargs(confidence=0.5))
    LMS.append_usage_outcome(str(state), "proj-a",
                             _usage(eid="u-1", lesson_id=lesson_id, prd_id="prd-3",
                                    action_observed=False, failure_recurred=False,
                                    outcome="not_observed"),
                             run_id="r")
    result = LMC.rebuild_catalog(str(state), "proj-a")
    entry = result.snapshot.entries[0]
    assert entry["confidence"] == 0.5   # 不变
    assert entry["usage_count"] == 1    # usage 仍记录


# ════════════════════════════════════════════════════════════════════════
# Section 6 task 6.2：effectiveness_history / last_outcome_ts derivation
# ════════════════════════════════════════════════════════════════════════
def test_apply_usage_outcomes_derives_effectiveness_history(tmp_path):
    """task 6.2：每条 usage → effectiveness_history 一条（含 outcome/prd_id/timestamp/action/failure）。"""
    state = tmp_path / "s"
    lesson_id = _seed_promoted_lesson(state, candidate_kwargs=_valid_candidate_kwargs(confidence=0.5))
    LMS.append_usage_outcome(str(state), "proj-a",
                             _usage(eid="u-1", lesson_id=lesson_id, prd_id="prd-3",
                                    outcome="followed", timestamp="2026-07-26T01:00:00Z"),
                             run_id="r")
    LMS.append_usage_outcome(str(state), "proj-a",
                             _usage(eid="u-2", lesson_id=lesson_id, prd_id="prd-4",
                                    outcome="contradicted", timestamp="2026-07-27T01:00:00Z"),
                             run_id="r")
    result = LMC.rebuild_catalog(str(state), "proj-a")
    entry = result.snapshot.entries[0]
    assert len(entry["effectiveness_history"]) == 2
    # 按 timestamp 排序（deterministic）
    assert entry["effectiveness_history"][0]["timestamp"] == "2026-07-26T01:00:00Z"
    assert entry["effectiveness_history"][0]["outcome"] == "followed"
    assert entry["effectiveness_history"][1]["outcome"] == "contradicted"
    assert entry["last_outcome_ts"] == "2026-07-27T01:00:00Z"
    assert entry["usage_count"] == 2
    assert entry["contradiction_count"] == 1


def test_apply_usage_outcomes_dedupes_by_event_id(tmp_path):
    """task 6.2 idempotency：相同 event_id 重放 → 只算一次（crash-recovery task 7.2）。"""
    state = tmp_path / "s"
    lesson_id = _seed_promoted_lesson(state, candidate_kwargs=_valid_candidate_kwargs(confidence=0.5))
    # 直接 append 同一 event_id 两次（模拟 reconcile 重放）
    LMS.append_usage_outcome(str(state), "proj-a",
                             _usage(eid="u-dupe", lesson_id=lesson_id, prd_id="prd-3",
                                    outcome="followed"),
                             run_id="r")
    LMS.append_usage_outcome(str(state), "proj-a",
                             _usage(eid="u-dupe", lesson_id=lesson_id, prd_id="prd-3",
                                    outcome="followed"),
                             run_id="r")
    result = LMC.rebuild_catalog(str(state), "proj-a")
    entry = result.snapshot.entries[0]
    assert entry["usage_count"] == 1   # dedupe
    assert entry["confidence"] == pytest.approx(0.6, abs=1e-9)   # 只 +0.1 一次


# ════════════════════════════════════════════════════════════════════════
# Section 6 task 6.3：contradiction-driven retire + terminal stickiness
# ════════════════════════════════════════════════════════════════════════
def test_apply_usage_outcomes_retires_on_repeated_contradiction(tmp_path):
    """task 6.3：contradiction_count >= 2 → state=retired（active entry）。"""
    state = tmp_path / "s"
    lesson_id = _seed_promoted_lesson(state, candidate_kwargs=_valid_candidate_kwargs(confidence=0.7))
    # 两次 contradicted（不同 PRD）
    LMS.append_usage_outcome(str(state), "proj-a",
                             _usage(eid="u-1", lesson_id=lesson_id, prd_id="prd-3",
                                    outcome="contradicted", timestamp="2026-07-26T01:00:00Z"),
                             run_id="r")
    LMS.append_usage_outcome(str(state), "proj-a",
                             _usage(eid="u-2", lesson_id=lesson_id, prd_id="prd-4",
                                    outcome="contradicted", timestamp="2026-07-27T01:00:00Z"),
                             run_id="r")
    result = LMC.rebuild_catalog(str(state), "proj-a")
    entry = result.snapshot.entries[0]
    assert entry["state"] == "retired"
    assert entry["contradiction_count"] == 2


def test_apply_usage_outcomes_single_contradiction_does_not_retire(tmp_path):
    """task 6.3 反例：单次 contradicted (<2) → 不 retire（state 仍 active）。"""
    state = tmp_path / "s"
    lesson_id = _seed_promoted_lesson(state, candidate_kwargs=_valid_candidate_kwargs(confidence=0.7))
    LMS.append_usage_outcome(str(state), "proj-a",
                             _usage(eid="u-1", lesson_id=lesson_id, prd_id="prd-3",
                                    outcome="contradicted"),
                             run_id="r")
    result = LMC.rebuild_catalog(str(state), "proj-a")
    entry = result.snapshot.entries[0]
    assert entry["state"] == "active"   # <2 不 retire
    assert entry["contradiction_count"] == 1


def test_apply_usage_outcomes_terminal_stickiness_lifecycle_retired(tmp_path):
    """task 6.3 反例：lifecycle 显式 retired 优先——usage 不重激活（即使 followed 多次）。

    spec design 决策#6：「Retirement only changes the projection and never deletes source facts」
    + 决策#3：terminal 状态粘性。"""
    state = tmp_path / "s"
    lesson_id = _seed_promoted_lesson(state, candidate_kwargs=_valid_candidate_kwargs(confidence=0.5))
    # 显式 retired lifecycle event
    LMS.append_lifecycle_event(str(state), "proj-a",
                               _event(eid="e-retire", lesson_id=lesson_id, event_type="retired"),
                               run_id="r")
    # usage 多次 followed（本应让 confidence 上升）+ contradiction 2 次（本应 retire）
    for i in range(3):
        LMS.append_usage_outcome(str(state), "proj-a",
                                 _usage(eid=f"u-{i}", lesson_id=lesson_id, prd_id=f"prd-{i+3}",
                                        outcome="followed", timestamp=f"2026-07-2{i+1}T00:00:00Z"),
                                 run_id="r")
    result = LMC.rebuild_catalog(str(state), "proj-a")
    entry = result.snapshot.entries[0]
    # terminal stickiness：state 保持 retired；confidence 不被 usage 改（lifecycle 优先）
    assert entry["state"] == "retired"
    assert entry["confidence"] == 0.5   # 不变（terminal 粘性）
    # effectiveness_history 仍记录（observation facts 永远在）
    assert entry["usage_count"] == 3


def test_apply_usage_outcomes_terminal_stickiness_lifecycle_superseded(tmp_path):
    """task 6.3 反例：lifecycle 显式 superseded 优先——usage 不改 state/confidence。"""
    state = tmp_path / "s"
    lesson_id = _seed_promoted_lesson(state, candidate_kwargs=_valid_candidate_kwargs(confidence=0.5))
    LMS.append_lifecycle_event(str(state), "proj-a",
                               _event(eid="e-super", lesson_id=lesson_id, event_type="superseded"),
                               run_id="r")
    LMS.append_usage_outcome(str(state), "proj-a",
                             _usage(eid="u-1", lesson_id=lesson_id, prd_id="prd-3",
                                    outcome="contradicted"),
                             run_id="r")
    LMS.append_usage_outcome(str(state), "proj-a",
                             _usage(eid="u-2", lesson_id=lesson_id, prd_id="prd-4",
                                    outcome="contradicted"),
                             run_id="r")
    result = LMC.rebuild_catalog(str(state), "proj-a")
    entry = result.snapshot.entries[0]
    assert entry["state"] == "superseded"   # 不被 usage 重写为 retired
    assert entry["confidence"] == 0.5       # terminal 粘性：usage 不改 confidence
    # effectiveness_history 仍记录
    assert entry["contradiction_count"] == 2


def test_retired_lesson_remains_in_catalog_with_full_facts(tmp_path):
    """task 6.3 反例：retired lesson 仍在 projection（state=retired）；source facts 不删。

    spec design 决策#3：「retirement only changes the projection and never deletes source facts」
    + 决策#6：「Retirement only changes the projection and never deletes source facts」。
    """
    state = tmp_path / "s"
    lesson_id = _seed_promoted_lesson(state, candidate_kwargs=_valid_candidate_kwargs(confidence=0.7))
    # 触发 retire
    LMS.append_usage_outcome(str(state), "proj-a",
                             _usage(eid="u-1", lesson_id=lesson_id, prd_id="prd-3",
                                    outcome="contradicted", timestamp="2026-07-26T00:00:00Z"),
                             run_id="r")
    LMS.append_usage_outcome(str(state), "proj-a",
                             _usage(eid="u-2", lesson_id=lesson_id, prd_id="prd-4",
                                    outcome="contradicted", timestamp="2026-07-27T00:00:00Z"),
                             run_id="r")
    result = LMC.rebuild_catalog(str(state), "proj-a")
    assert result.ok
    # retired lesson 仍在 catalog（不删）
    assert len(result.snapshot.entries) == 1
    entry = result.snapshot.entries[0]
    assert entry["state"] == "retired"
    # source facts 完整保留（spec：never deletes source facts）
    assert len(entry["source_candidate_ids"]) == 2
    assert len(entry["supporting_prd_ids"]) == 2


# ════════════════════════════════════════════════════════════════════════
# Section 6 task 7.2：idempotency（crash-recovery）
# ════════════════════════════════════════════════════════════════════════
def test_apply_usage_outcomes_replay_is_byte_identical(tmp_path):
    """task 7.2：相同 facts（含 usage）→ byte-identical catalog replay。

    crash-recovery 要求：reconcile 重放同一 usage records，catalog projection 必须幂等。
    """
    state1 = tmp_path / "s1"
    state2 = tmp_path / "s2"
    for state in (state1, state2):
        lesson_id = _seed_promoted_lesson(state, candidate_kwargs=_valid_candidate_kwargs(confidence=0.5))
        LMS.append_usage_outcome(str(state), "proj-a",
                                 _usage(eid="u-1", lesson_id=lesson_id, prd_id="prd-3",
                                        outcome="followed", timestamp="2026-07-26T00:00:00Z"),
                                 run_id="r")
        LMS.append_usage_outcome(str(state), "proj-a",
                                 _usage(eid="u-2", lesson_id=lesson_id, prd_id="prd-4",
                                        outcome="contradicted", timestamp="2026-07-27T00:00:00Z"),
                                 run_id="r")
    LMC.rebuild_catalog(str(state1), "proj-a")
    LMC.rebuild_catalog(str(state2), "proj-a")
    bytes1 = (state1 / "lessons" / "catalog" / "proj-a.json").read_bytes()
    bytes2 = (state2 / "lessons" / "catalog" / "proj-a.json").read_bytes()
    assert bytes1 == bytes2, "相同 facts（含 usage）→ catalog 必须 byte-identical"


def test_apply_usage_outcomes_repeated_rebuild_byte_identical(tmp_path):
    """task 7.2：同一 state 多次 rebuild → byte-identical（_apply_usage_outcomes 幂等）。"""
    state = tmp_path / "s"
    lesson_id = _seed_promoted_lesson(state, candidate_kwargs=_valid_candidate_kwargs(confidence=0.5))
    LMS.append_usage_outcome(str(state), "proj-a",
                             _usage(eid="u-1", lesson_id=lesson_id, prd_id="prd-3",
                                    outcome="followed"),
                             run_id="r")
    LMC.rebuild_catalog(str(state), "proj-a")
    bytes1 = (state / "lessons" / "catalog" / "proj-a.json").read_bytes()
    LMC.rebuild_catalog(str(state), "proj-a")
    bytes2 = (state / "lessons" / "catalog" / "proj-a.json").read_bytes()
    assert bytes1 == bytes2


# ════════════════════════════════════════════════════════════════════════
# Section 6 task 6.2：fail-closed for memory（usage middle corruption）
# ════════════════════════════════════════════════════════════════════════
def test_project_catalog_degraded_on_usage_middle_corruption(tmp_path):
    """task 6.2 + design 决策#7：usage.jsonl 中部损坏 → project_catalog 返回 degraded_class
    = ``middle_corruption``（fail-closed for memory；fail-open delivery）。"""
    state = tmp_path / "s"
    lesson_id = _seed_promoted_lesson(state, candidate_kwargs=_valid_candidate_kwargs(confidence=0.5))
    LMS.append_usage_outcome(str(state), "proj-a",
                             _usage(eid="u-1", lesson_id=lesson_id, prd_id="prd-3",
                                    outcome="followed"),
                             run_id="r")
    # 在 usage.jsonl 末尾追加：中部坏行 + 合法行（坏行非末尾 → middle corruption）
    p = state / "lessons" / "usage" / "proj-a.jsonl"
    good = json.dumps({
        "schema_version": LMS.LESSONS_SCHEMA_VERSION, "kind": "usage", "run_id": "r",
        "usage": {"event_id": "u-2", "timestamp": "t2", "project_id": "proj-a",
                  "lesson_id": lesson_id, "prd_id": "prd-4",
                  "action_observed": True, "failure_recurred": False,
                  "outcome": "followed", "evidence_refs": [], "schema_version": 1},
    })
    with open(p, "a", encoding="utf-8") as f:
        f.write("MIDDLE_GARBAGE_IN_USAGE\n")
        f.write(good + "\n")
    result = LMC.project_catalog(str(state), "proj-a")
    assert result.ok is False
    assert result.degraded_class == "middle_corruption"
    assert result.snapshot is None


def test_rebuild_catalog_on_usage_corruption_leaves_old_catalog_intact(tmp_path):
    """task 6.2 + 决策#7：usage 损坏时 rebuild 不写 partial catalog（旧 catalog 保持原样）。"""
    state = tmp_path / "s"
    cat_p = state / "lessons" / "catalog" / "proj-a.json"
    cat_p.parent.mkdir(parents=True, exist_ok=True)
    cat_p.write_text('{"project_id": "proj-a", "preserved": true}', encoding="utf-8")
    lesson_id = _seed_promoted_lesson(state, candidate_kwargs=_valid_candidate_kwargs(confidence=0.5))
    LMS.append_usage_outcome(str(state), "proj-a",
                             _usage(eid="u-1", lesson_id=lesson_id, prd_id="prd-3",
                                    outcome="followed"),
                             run_id="r")
    # 制造真正的中部损坏
    p = state / "lessons" / "usage" / "proj-a.jsonl"
    good_after = json.dumps({
        "schema_version": LMS.LESSONS_SCHEMA_VERSION, "kind": "usage", "run_id": "r",
        "usage": {"event_id": "u-2", "timestamp": "t2", "project_id": "proj-a",
                  "lesson_id": lesson_id, "prd_id": "prd-4",
                  "action_observed": True, "failure_recurred": False,
                  "outcome": "followed", "evidence_refs": [], "schema_version": 1},
    })
    with open(p, "a", encoding="utf-8") as f:
        f.write("MIDDLE_GARBAGE\n")
        f.write(good_after + "\n")
    result = LMC.rebuild_catalog(str(state), "proj-a")
    assert result.ok is False
    # 旧 catalog 未被覆盖
    assert cat_p.read_text(encoding="utf-8") == '{"project_id": "proj-a", "preserved": true}'


def test_project_catalog_no_usage_file_returns_ok(tmp_path):
    """task 6.2：无 usage 文件（未注入过 lesson）→ 正常 ok=True（不是 degraded）。"""
    state = tmp_path / "s"
    _seed_promoted_lesson(state)
    result = LMC.project_catalog(str(state), "proj-a")
    assert result.ok is True
    assert result.snapshot is not None


# ════════════════════════════════════════════════════════════════════════
# Section 6 task 6.4：catalog 输出含 effectiveness 字段（rebuildable + byte-stable）
# ════════════════════════════════════════════════════════════════════════
def test_catalog_file_entries_contain_all_section6_fields(tmp_path):
    """task A/6.2/6.3：catalog JSON entries 含全部 Section 6 扩展字段。"""
    state = tmp_path / "s"
    lesson_id = _seed_promoted_lesson(state, candidate_kwargs=_valid_candidate_kwargs(confidence=0.5))
    LMS.append_usage_outcome(str(state), "proj-a",
                             _usage(eid="u-1", lesson_id=lesson_id, prd_id="prd-3",
                                    outcome="followed"),
                             run_id="r")
    LMC.rebuild_catalog(str(state), "proj-a")
    cat = json.loads((state / "lessons" / "catalog" / "proj-a.json").read_text(encoding="utf-8"))
    entry = cat["entries"][0]
    for field in ("applies_when_tags", "verified_support_count",
                  "effectiveness_history", "last_outcome_ts",
                  "usage_count", "contradiction_count"):
        assert field in entry, f"catalog entry 缺 Section 6 字段 {field}"
    # applies_when_tags 必须是 list（JSON 序列化的 sorted tuple）
    assert isinstance(entry["applies_when_tags"], list)
    assert isinstance(entry["effectiveness_history"], list)
    assert isinstance(entry["usage_count"], int)
