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
    c = _candidate()
    LMS.append_candidate(str(state), "proj-a", c, run_id="r", timestamp="t")
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
    c = _candidate()
    LMS.append_candidate(str(state), "proj-a", c, run_id="r", timestamp="t")
    LMC.rebuild_catalog(str(state), "proj-a")
    cat = json.loads((state / "lessons" / "catalog" / "proj-a.json").read_text(encoding="utf-8"))
    entry = cat["entries"][0]
    for field in ("lesson_id", "project_id", "equivalence_key",
                  "source_candidate_ids", "supporting_prd_ids",
                  "corrective_action", "trigger", "non_applicability_when",
                  "state", "confidence", "schema_version"):
        assert field in entry, f"catalog entry 缺字段 {field}"
