#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_learning_memory_store.py — add-cross-prd-learning-memory Section 2.1/2.2/2.3/2.5 单测。

锁定 append-only candidate/event writer 契约 + 存储层 defense-in-depth 校验 + equivalence_key 派生 +
路径隔离反例（spec design 决策#3 存储布局 + 决策#7 fail-open delivery / fail-closed memory）。

核心断言：
    * task 2.1：per-project JSONL append-only writer（versioned + flock + fsync + O_APPEND 不撕裂）；
      并发 append 无丢失无交错；run_id/prd_id/iteration_refs 关联字段；
    * task 2.2：存储层 defense-in-depth 再校验（reusable trigger / executable corrective_action /
      applicability 边界 / identity / evidence_refs integrity shape）—— 防「绕过 schema 直接灌 JSONL」；
    * task 2.3：derive_equivalence_key（Section 1 函数）在写入时 stamp 到 JSONL 行；目录层兜底 reject
      out-of-vocabulary enum + redact model-authored pattern_key/equivalence_key/...；
    * task 2.5：所有 memory state 仅在 ``.project-auto/state/lessons/`` 下（控制面，.gitignore），
      永不进目标 worktree（ADR-0001）。

IO 测试用 pytest ``tmp_path`` 隔离（不在 cron/项目目录留痕）。模块仅依赖 Section 1 schema + 标准库。

跑：python3 -m pytest scripts/test_learning_memory_store.py -q
AAA 结构。
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import learning_memory_schema as LM  # noqa: E402
import learning_memory_store as LMS  # noqa: E402


# ════════════════════════════════════════════════════════════════════════
# fixture：构造 schema-valid LessonCandidate / LessonLifecycleEvent
# ════════════════════════════════════════════════════════════════════════
def _valid_candidate_kwargs(**overrides):
    """与 test_learning_memory_schema 同 baseline（schema-valid candidate 字段 dict）。"""
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


def _event(eid="evt-1", event_type="confirmed", lesson_id="lesson-1", **payload):
    return LM.LessonLifecycleEvent(
        event_id=eid, timestamp="2026-07-26T03:17:00Z",
        project_id="proj-a", lesson_id=lesson_id, event_type=event_type,
        payload=payload, schema_version=1)


def _usage(eid="u-1", lesson_id="lesson-1", prd_id="prd-001",
           action_observed=True, failure_recurred=False, outcome="followed"):
    """构造 schema-valid UsageOutcome（Section 6 fixture）。"""
    return LM.UsageOutcome(
        event_id=eid, timestamp="2026-07-26T03:17:00Z",
        project_id="proj-a", lesson_id=lesson_id, prd_id=prd_id,
        action_observed=action_observed, failure_recurred=failure_recurred,
        outcome=outcome, schema_version=1)


# ════════════════════════════════════════════════════════════════════════
# task 2.5：路径隔离（先跑——锁定 ADR-0001 边界）
# ════════════════════════════════════════════════════════════════════════
def test_state_paths_all_under_lessons(tmp_path):
    """task 2.5：所有 memory state 路径都在 ``state_dir/lessons/`` 下（design 决策#3）。"""
    state = tmp_path / "state"
    cand_p = LMS.candidate_path(str(state), "proj-a")
    evt_p = LMS.event_path(str(state), "proj-a")
    cat_p = LMS.catalog_path(str(state), "proj-a")
    for p in (cand_p, evt_p, cat_p):
        assert "lessons" in p.parts
        assert p.relative_to(state / "lessons")


def test_memory_state_never_written_to_target_worktree(tmp_path):
    """task 2.5 反例：append 只写 ``state_dir/lessons/``，绝不写目标 worktree（ADR-0001 控制面/目标面分离）。"""
    state = tmp_path / "state"
    target_worktree = tmp_path / "target-worktree"
    target_worktree.mkdir()
    LMS.append_candidate(str(state), "proj-a", _candidate(),
                         run_id="r-1", timestamp="2026-07-26T03:17:00Z")
    LMS.append_lifecycle_event(str(state), "proj-a", _event(),
                               run_id="r-1")
    # state 下有 lessons 文件
    assert (state / "lessons" / "candidates" / "proj-a.jsonl").exists()
    assert (state / "lessons" / "events" / "proj-a.jsonl").exists()
    # 目标 worktree 下零 memory 文件（反例：绝不污染目标面）
    assert not any(target_worktree.rglob("*.jsonl"))
    assert not any(target_worktree.rglob("lessons"))


def test_project_isolation_per_project_file(tmp_path):
    """task 2.5 + design 决策#3：不同 project 各自独立 JSONL（per-project 隔离，绝不混写）。"""
    state = tmp_path / "state"
    LMS.append_candidate(str(state), "proj-a", _candidate(project_id="proj-a"),
                         run_id="r-1", timestamp="t")
    LMS.append_candidate(str(state), "proj-b", _candidate(project_id="proj-b"),
                         run_id="r-2", timestamp="t")
    a = state / "lessons" / "candidates" / "proj-a.jsonl"
    b = state / "lessons" / "candidates" / "proj-b.jsonl"
    assert a.exists() and b.exists()
    a_rec = json.loads(a.read_text(encoding="utf-8").splitlines()[0])
    b_rec = json.loads(b.read_text(encoding="utf-8").splitlines()[0])
    assert a_rec["candidate"]["project_id"] == "proj-a"
    assert b_rec["candidate"]["project_id"] == "proj-b"


# ════════════════════════════════════════════════════════════════════════
# task 2.1：append-only candidate/event writers
# ════════════════════════════════════════════════════════════════════════
def test_append_candidate_creates_versioned_line(tmp_path):
    """task 2.1：append 一条 candidate → 文件含一行合法 JSON，schema_version + kind 字段在。"""
    state = tmp_path / "state"
    LMS.append_candidate(str(state), "proj-a", _candidate(),
                         run_id="r-1", timestamp="2026-07-26T03:17:00Z")
    lines = (state / "lessons" / "candidates" / "proj-a.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["schema_version"] == LMS.LESSONS_SCHEMA_VERSION
    assert rec["kind"] == "candidate"
    assert rec["run_id"] == "r-1"
    assert rec["timestamp"] == "2026-07-26T03:17:00Z"
    assert rec["candidate"]["project_id"] == "proj-a"
    assert rec["candidate_id"]   # candidate_id 非空


def test_append_candidate_does_not_overwrite(tmp_path):
    """task 2.1：O_APPEND——多次 append 累积，旧记录绝不丢（崩溃恢复前提）。"""
    state = tmp_path / "state"
    LMS.append_candidate(str(state), "proj-a", _candidate(prd_id="prd-1"),
                         run_id="r-1", timestamp="t1")
    LMS.append_candidate(str(state), "proj-a", _candidate(prd_id="prd-2"),
                         run_id="r-1", timestamp="t2")
    recs = LMS.read_candidate_records(str(state), "proj-a")
    assert len(recs) == 2
    assert {r["candidate"]["prd_id"] for r in recs} == {"prd-1", "prd-2"}


def test_append_candidate_calls_fsync(tmp_path, monkeypatch):
    """task 2.1：必须调 os.fsync（崩溃后已 append 的行落盘——design 决策#3 append-only 真源前提）。"""
    calls: list[int] = []
    monkeypatch.setattr(LMS.os, "fsync", lambda fd: calls.append(fd))
    LMS.append_candidate(str(tmp_path / "state"), "proj-a", _candidate(),
                         run_id="r-1", timestamp="t")
    assert len(calls) >= 1, "append_candidate 未调 os.fsync"


def test_append_lifecycle_event_creates_versioned_line(tmp_path):
    """task 2.1：append 一条 lifecycle event → events/<project>.jsonl 含一行合法 JSON。"""
    state = tmp_path / "state"
    LMS.append_lifecycle_event(str(state), "proj-a", _event(eid="e-1"),
                               run_id="r-1")
    lines = (state / "lessons" / "events" / "proj-a.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["kind"] == "event"
    assert rec["run_id"] == "r-1"
    assert rec["event"]["event_id"] == "e-1"


def test_append_candidate_correlates_run_prd_iteration(tmp_path):
    """task 2.1：record 含 run_id / prd_id / iteration_refs 关联字段（spec design 决策#3：
    'correlated with run_id, prd_id, and terminal journal evidence'）。"""
    state = tmp_path / "state"
    c = _candidate(prd_id="prd-42", iteration_refs=("iter-42a", "iter-42b"))
    LMS.append_candidate(str(state), "proj-a", c, run_id="run-xyz", timestamp="t")
    rec = LMS.read_candidate_records(str(state), "proj-a")[0]
    assert rec["run_id"] == "run-xyz"
    assert rec["candidate"]["prd_id"] == "prd-42"
    assert rec["candidate"]["iteration_refs"] == ["iter-42a", "iter-42b"]


def test_concurrent_append_preserves_all_records(tmp_path):
    """task 2.1：多线程并发 append 同一 project 文件——flock 保证无丢失、无交错（行不撕裂）。"""
    state = tmp_path / "state"
    N_THREADS = 8
    N_PER_THREAD = 10

    def worker(tid):
        for i in range(N_PER_THREAD):
            c = _candidate(prd_id=f"prd-{tid}-{i}")
            LMS.append_candidate(str(state), "proj-a", c,
                                 run_id=f"r-{tid}", timestamp=f"t-{tid}-{i}")

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(N_THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    recs = LMS.read_candidate_records(str(state), "proj-a")
    assert len(recs) == N_THREADS * N_PER_THREAD   # 无丢失
    # 每行都是完整合法 JSON（无撕裂）——read 已 json.loads 成功即证明
    prd_ids = {r["candidate"]["prd_id"] for r in recs}
    assert len(prd_ids) == N_THREADS * N_PER_THREAD   # 无覆盖


# ════════════════════════════════════════════════════════════════════════
# task 2.2：存储层 defense-in-depth 校验
# ════════════════════════════════════════════════════════════════════════
def test_append_candidate_rejects_empty_iteration_refs(tmp_path):
    """task 2.2：identity 校验——iteration_refs=() → 拒（无法回溯 evidence 真源）。"""
    state = tmp_path / "state"
    # 绕过 schema（schema 不强制 iteration_refs 非空），用 dataclasses.replace 改后冻检验验
    import dataclasses
    c = _candidate()
    c2 = dataclasses.replace(c, iteration_refs=())
    with pytest.raises(ValueError, match=r"iteration_refs"):
        LMS.append_candidate(str(state), "proj-a", c2, run_id="r-1", timestamp="t")


def test_append_candidate_rejects_empty_project_id(tmp_path):
    """task 2.2：identity 校验——project_id 空 → 拒（无 scope 隔离）。"""
    state = tmp_path / "state"
    import dataclasses
    c = dataclasses.replace(_candidate(), project_id="")
    with pytest.raises(ValueError, match=r"project_id"):
        LMS.append_candidate(str(state), "proj-a", c, run_id="r-1", timestamp="t")


def test_append_candidate_rejects_evidence_ref_missing_digest(tmp_path):
    """task 2.2：evidence_refs integrity shape——缺 digest → 拒（无可校验完整性）。"""
    state = tmp_path / "state"
    import dataclasses
    c = dataclasses.replace(
        _candidate(),
        evidence_refs=({"kind": "test_output", "path": "p"},),   # 缺 digest
    )
    with pytest.raises(ValueError, match=r"digest"):
        LMS.append_candidate(str(state), "proj-a", c, run_id="r-1", timestamp="t")


def test_append_candidate_rejects_bypassed_schema_via_raw_jsonl(tmp_path):
    """task 2.2 反例：手灌一行缺 reusable trigger 的 JSONL → read_candidate_records 拒（fail-closed）。

    防「绕过 schema 直接灌 JSONL」：存储层读端对每条记录再校验。"""
    state = tmp_path / "state"
    p = state / "lessons" / "candidates" / "proj-a.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    # 手写一行合法 JSON 但 applicability_when 空（无 reusable trigger，spec 禁）
    bad_line = json.dumps({
        "schema_version": LMS.LESSONS_SCHEMA_VERSION, "kind": "candidate",
        "candidate_id": "cand-bad", "run_id": "r-x", "timestamp": "t",
        "equivalence_key": "proj-a:x",
        "candidate": {**_valid_candidate_kwargs(), "applicability_when": ""},
    })
    p.write_text(bad_line + "\n", encoding="utf-8")
    with pytest.raises(LMS.LessonsCorruptionError):
        LMS.read_candidate_records(str(state), "proj-a")


# ════════════════════════════════════════════════════════════════════════
# task 2.3：equivalence_key 在写入时 stamp（目录层兜底）
# ════════════════════════════════════════════════════════════════════════
def test_append_candidate_stamps_derived_equivalence_key(tmp_path):
    """task 2.3：每条 candidate 行含 equivalence_key 字段（由 derive_equivalence_key 派生）。"""
    state = tmp_path / "state"
    c = _candidate()
    LMS.append_candidate(str(state), "proj-a", c, run_id="r-1", timestamp="t")
    rec = LMS.read_candidate_records(str(state), "proj-a")[0]
    expected_key = LM.derive_equivalence_key(c)
    assert rec["equivalence_key"] == expected_key


def test_append_candidate_equivalence_key_is_canonical_and_stable(tmp_path):
    """task 2.3：同一 candidate 写两次（不同 prd_id）——同 enum 字段 → 同 equivalence_key（byte-equal）。"""
    state = tmp_path / "state"
    c1 = _candidate(prd_id="prd-1", evidence_refs=(
        {"digest": "sha256:a1", "kind": "test_output", "path": "sha256/a1"},))
    c2 = _candidate(prd_id="prd-2", evidence_refs=(
        {"digest": "sha256:b2", "kind": "test_output", "path": "sha256/b2"},))
    LMS.append_candidate(str(state), "proj-a", c1, run_id="r", timestamp="t1")
    LMS.append_candidate(str(state), "proj-a", c2, run_id="r", timestamp="t2")
    recs = LMS.read_candidate_records(str(state), "proj-a")
    assert recs[0]["equivalence_key"] == recs[1]["equivalence_key"]   # byte-equal


def test_read_redacts_stored_model_authored_equivalence_key(tmp_path):
    """task 2.3 目录层兜底：JSONL 行含手灌的 model-authored equivalence_key → 读端用 enum 字段重算覆盖。

    防「绕过 schema 灌假 key」——equivalence_key 永远由 derive_equivalence_key 派生，存储值不被信任。"""
    state = tmp_path / "state"
    p = state / "lessons" / "candidates" / "proj-a.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    cand_kwargs = _valid_candidate_kwargs()
    fake_line = json.dumps({
        "schema_version": LMS.LESSONS_SCHEMA_VERSION, "kind": "candidate",
        "candidate_id": "cand-x", "run_id": "r-x", "timestamp": "t",
        "equivalence_key": "MODEL_LIES_KEY_SHOULD_BE_REDISTRIBUTED",   # model-authored，应被重算覆盖
        "candidate": cand_kwargs,
    })
    p.write_text(fake_line + "\n", encoding="utf-8")
    recs = LMS.read_candidate_records(str(state), "proj-a")
    expected = LM.derive_equivalence_key(LM.LessonCandidate(**cand_kwargs))
    assert recs[0]["equivalence_key"] == expected
    assert "MODEL_LIES" not in recs[0]["equivalence_key"]


def test_read_rejects_out_of_vocab_enum_in_stored_record(tmp_path):
    """task 2.3 目录层兜底：JSONL 行含超词表枚举值 → 读端拒（fail-closed，绝不放行污染）。"""
    state = tmp_path / "state"
    p = state / "lessons" / "candidates" / "proj-a.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    bad_kwargs = _valid_candidate_kwargs()
    bad_kwargs["phase"] = "totally_made_up_phase"   # 超词表
    bad_line = json.dumps({
        "schema_version": LMS.LESSONS_SCHEMA_VERSION, "kind": "candidate",
        "candidate_id": "cand-x", "run_id": "r-x", "timestamp": "t",
        "equivalence_key": "proj-a:x",
        "candidate": bad_kwargs,
    })
    p.write_text(bad_line + "\n", encoding="utf-8")
    with pytest.raises(LMS.LessonsCorruptionError):
        LMS.read_candidate_records(str(state), "proj-a")


# ════════════════════════════════════════════════════════════════════════
# task 2.1 + 2.4 共享：损坏容错（trailing recoverable / middle fail-closed）
# ════════════════════════════════════════════════════════════════════════
def test_read_tolerates_incomplete_trailing_candidate(tmp_path):
    """spec：末尾不完整（崩溃截断最后一条 append）→ 容忍丢弃，返回前面已提交记录。"""
    state = tmp_path / "state"
    LMS.append_candidate(str(state), "proj-a", _candidate(prd_id="prd-1"),
                         run_id="r", timestamp="t1")
    p = state / "lessons" / "candidates" / "proj-a.jsonl"
    with open(p, "a", encoding="utf-8") as f:
        f.write('{"schema_version": 1, "kind": "candidate", "candi')   # 截断半行
    recs = LMS.read_candidate_records(str(state), "proj-a")
    assert len(recs) == 1
    assert recs[0]["candidate"]["prd_id"] == "prd-1"


def test_read_fail_closed_on_middle_corruption(tmp_path):
    """spec：中部损坏 → fail-closed（绝不静默跳过坏行继续归约）。"""
    state = tmp_path / "state"
    p = state / "lessons" / "candidates" / "proj-a.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    # 合法 + 中部坏行 + 合法
    cand_kwargs = _valid_candidate_kwargs()
    good = json.dumps({
        "schema_version": LMS.LESSONS_SCHEMA_VERSION, "kind": "candidate",
        "candidate_id": "c1", "run_id": "r", "timestamp": "t",
        "equivalence_key": "proj-a:k1",
        "candidate": cand_kwargs,
    })
    p.write_text(good + "\n" + "GARBAGE_MIDDLE_LINE\n" + good + "\n", encoding="utf-8")
    with pytest.raises(LMS.LessonsCorruptionError) as ei:
        LMS.read_candidate_records(str(state), "proj-a")
    assert ei.value.line_number == 2   # 1-based 第 2 行


def test_validate_lessons_reports_tail_truncation(tmp_path):
    """validate_*（不 raise）——返回 CorruptionReport，调用方据 degraded 决策。"""
    state = tmp_path / "state"
    LMS.append_candidate(str(state), "proj-a", _candidate(),
                         run_id="r", timestamp="t")
    p = state / "lessons" / "candidates" / "proj-a.jsonl"
    with open(p, "a", encoding="utf-8") as f:
        f.write('{"truncated')
    report = LMS.validate_candidates(str(state), "proj-a")
    assert report.tail_truncated is True
    assert report.corrupted_line_numbers == ()
    assert report.is_fail_closed is False


# ════════════════════════════════════════════════════════════════════════
# Section 6 task 6.2：append-only usage outcome writer
# ════════════════════════════════════════════════════════════════════════
def test_usage_path_under_lessons_dir(tmp_path):
    """task 6.2 + design 决策#3：usage 文件在 ``state_dir/lessons/usage/`` 下。"""
    p = LMS.usage_path(str(tmp_path / "state"), "proj-a")
    assert "lessons" in p.parts
    assert p.name == "proj-a.jsonl"
    assert p.parent.name == "usage"


def test_append_usage_outcome_creates_versioned_line(tmp_path):
    """task 6.2：append 一条 usage outcome → usage/<project>.jsonl 含一行合法 JSON。"""
    state = tmp_path / "state"
    LMS.append_usage_outcome(str(state), "proj-a", _usage(eid="u-1"),
                             run_id="r-1")
    lines = (state / "lessons" / "usage" / "proj-a.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["schema_version"] == LMS.LESSONS_SCHEMA_VERSION
    assert rec["kind"] == "usage"
    assert rec["run_id"] == "r-1"
    assert rec["usage"]["event_id"] == "u-1"
    assert rec["usage"]["outcome"] == "followed"
    assert rec["usage"]["action_observed"] is True
    assert rec["usage"]["failure_recurred"] is False


def test_append_usage_outcome_does_not_overwrite(tmp_path):
    """task 6.2：O_APPEND——多次 append 累积（append-only facts，绝不丢已记录的 outcome）。"""
    state = tmp_path / "state"
    LMS.append_usage_outcome(str(state), "proj-a", _usage(eid="u-1", prd_id="prd-1"),
                             run_id="r")
    LMS.append_usage_outcome(str(state), "proj-a", _usage(eid="u-2", prd_id="prd-2"),
                             run_id="r")
    recs = LMS.read_usage_records(str(state), "proj-a")
    assert len(recs) == 2
    assert {r["usage"]["event_id"] for r in recs} == {"u-1", "u-2"}


def test_append_usage_outcome_calls_fsync(tmp_path, monkeypatch):
    """task 6.2：必须调 os.fsync（crash 后已 append 的 outcome 行落盘）。"""
    calls: list[int] = []
    monkeypatch.setattr(LMS.os, "fsync", lambda fd: calls.append(fd))
    LMS.append_usage_outcome(str(tmp_path / "state"), "proj-a", _usage(),
                             run_id="r-1")
    assert len(calls) >= 1, "append_usage_outcome 未调 os.fsync"


def test_append_usage_outcome_rejects_project_id_mismatch(tmp_path):
    """task 6.2：outcome.project_id != path project_id → ValueError（per-project 文件隔离）。"""
    state = tmp_path / "state"
    bad = LM.UsageOutcome(
        event_id="u-x", timestamp="t", project_id="proj-b",
        lesson_id="l", prd_id="prd-1",
        action_observed=True, failure_recurred=False,
        outcome="followed", schema_version=1)
    with pytest.raises(ValueError, match=r"project_id mismatch"):
        LMS.append_usage_outcome(str(state), "proj-a", bad, run_id="r")


def test_append_usage_outcome_rejects_empty_identity(tmp_path):
    """task 6.2 defense-in-depth：空 event_id / lesson_id / prd_id / timestamp → 拒。"""
    state = tmp_path / "state"
    # 用 dataclasses.replace 绕过 __init__ 改 frozen field
    import dataclasses
    base_u = _usage()
    for field in ("event_id", "lesson_id", "prd_id", "timestamp"):
        u_bad = dataclasses.replace(base_u, **{field: ""})
        with pytest.raises(ValueError, match=rf"{field}"):
            LMS.append_usage_outcome(str(state), "proj-a", u_bad, run_id="r")


def test_concurrent_append_usage_preserves_all_records(tmp_path):
    """task 6.2：多线程并发 append usage——flock 保证无丢失、无交错。"""
    state = tmp_path / "state"
    N_THREADS = 6
    N_PER_THREAD = 8

    def worker(tid):
        for i in range(N_PER_THREAD):
            u = _usage(eid=f"u-{tid}-{i}", prd_id=f"prd-{tid}-{i}")
            LMS.append_usage_outcome(str(state), "proj-a", u, run_id=f"r-{tid}")

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(N_THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    recs = LMS.read_usage_records(str(state), "proj-a")
    assert len(recs) == N_THREADS * N_PER_THREAD   # 无丢失
    eids = {r["usage"]["event_id"] for r in recs}
    assert len(eids) == N_THREADS * N_PER_THREAD   # 无覆盖


# ════════════════════════════════════════════════════════════════════════
# task 6.2：usage 损坏容错（trailing recoverable / middle fail-closed）
# ════════════════════════════════════════════════════════════════════════
def test_read_usage_tolerates_incomplete_trailing(tmp_path):
    """spec：末尾半行（crash 截断最后一条 append）→ 容忍丢弃。"""
    state = tmp_path / "state"
    LMS.append_usage_outcome(str(state), "proj-a", _usage(eid="u-1"),
                             run_id="r")
    p = state / "lessons" / "usage" / "proj-a.jsonl"
    with open(p, "a", encoding="utf-8") as f:
        f.write('{"schema_version": 1, "kind": "usage", "usa')   # 截断半行
    recs = LMS.read_usage_records(str(state), "proj-a")
    assert len(recs) == 1
    assert recs[0]["usage"]["event_id"] == "u-1"


def test_read_usage_fail_closed_on_middle_corruption(tmp_path):
    """spec design 决策#7：usage 中部损坏 → fail-closed（绝不静默跳过坏行）。"""
    state = tmp_path / "state"
    p = state / "lessons" / "usage" / "proj-a.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    good = json.dumps({
        "schema_version": LMS.LESSONS_SCHEMA_VERSION, "kind": "usage",
        "run_id": "r",
        "usage": {"event_id": "u-1", "timestamp": "t", "project_id": "proj-a",
                  "lesson_id": "l", "prd_id": "prd", "action_observed": True,
                  "failure_recurred": False, "outcome": "followed",
                  "evidence_refs": [], "schema_version": 1},
    })
    p.write_text(good + "\n" + "MIDDLE_GARBAGE_IN_USAGE\n" + good + "\n", encoding="utf-8")
    with pytest.raises(LMS.LessonsCorruptionError) as ei:
        LMS.read_usage_records(str(state), "proj-a")
    assert ei.value.line_number == 2


def test_read_usage_rejects_complete_but_invalid_outcome(tmp_path):
    """task 6.2 defense-in-depth：complete-JSON 但 outcome=unknown/超词表 → fail-closed。"""
    state = tmp_path / "state"
    p = state / "lessons" / "usage" / "proj-a.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    bad = json.dumps({
        "schema_version": LMS.LESSONS_SCHEMA_VERSION, "kind": "usage",
        "run_id": "r",
        "usage": {"event_id": "u-1", "timestamp": "t", "project_id": "proj-a",
                  "lesson_id": "l", "prd_id": "prd", "action_observed": True,
                  "failure_recurred": False, "outcome": "totally_made_up",
                  "evidence_refs": [], "schema_version": 1},
    })
    p.write_text(bad + "\n", encoding="utf-8")
    with pytest.raises(LMS.LessonsCorruptionError):
        LMS.read_usage_records(str(state), "proj-a")


def test_read_usage_rejects_wrong_kind_field(tmp_path):
    """task 6.2：kind != "usage" → fail-closed（防手灌 candidate/event 行进 usage 文件）。"""
    state = tmp_path / "state"
    p = state / "lessons" / "usage" / "proj-a.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    bad = json.dumps({
        "schema_version": LMS.LESSONS_SCHEMA_VERSION, "kind": "candidate",
        "run_id": "r",
        "usage": {"event_id": "u-1", "timestamp": "t", "project_id": "proj-a",
                  "lesson_id": "l", "prd_id": "prd", "action_observed": True,
                  "failure_recurred": False, "outcome": "followed",
                  "evidence_refs": [], "schema_version": 1},
    })
    p.write_text(bad + "\n", encoding="utf-8")
    with pytest.raises(LMS.LessonsCorruptionError):
        LMS.read_usage_records(str(state), "proj-a")


def test_read_usage_rejects_non_bool_action_observed(tmp_path):
    """task 6.2：action_observed 非 bool（如字符串 "true"）→ fail-closed（防 type confusion）。"""
    state = tmp_path / "state"
    p = state / "lessons" / "usage" / "proj-a.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    bad = json.dumps({
        "schema_version": LMS.LESSONS_SCHEMA_VERSION, "kind": "usage",
        "run_id": "r",
        "usage": {"event_id": "u-1", "timestamp": "t", "project_id": "proj-a",
                  "lesson_id": "l", "prd_id": "prd", "action_observed": "true",
                  "failure_recurred": False, "outcome": "followed",
                  "evidence_refs": [], "schema_version": 1},
    })
    p.write_text(bad + "\n", encoding="utf-8")
    with pytest.raises(LMS.LessonsCorruptionError):
        LMS.read_usage_records(str(state), "proj-a")


def test_validate_usage_reports_tail_truncation(tmp_path):
    """validate_usage（不 raise）——返回 CorruptionReport。"""
    state = tmp_path / "state"
    LMS.append_usage_outcome(str(state), "proj-a", _usage(), run_id="r")
    p = state / "lessons" / "usage" / "proj-a.jsonl"
    with open(p, "a", encoding="utf-8") as f:
        f.write('{"truncated')
    report = LMS.validate_usage(str(state), "proj-a")
    assert report.tail_truncated is True
    assert report.corrupted_line_numbers == ()


def test_read_usage_missing_file_returns_empty(tmp_path):
    """首次运行未注入过 lesson → usage 文件不存在 → 空列表（正常态）。"""
    state = tmp_path / "state"
    recs = LMS.read_usage_records(str(state), "proj-a")
    assert recs == []
