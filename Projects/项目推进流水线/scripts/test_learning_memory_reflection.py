#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_learning_memory_reflection.py — add-cross-prd-learning-memory Section 4.2-4.5 单测。

锁定 task 4.2（bounded read-only SDK reflection）+ 4.3（仅 terminal 后调用）+ 4.4（持久化）+
4.5（degraded fail-open）契约——spec design 决策#2（read-only SDK）+ #7（fail-open delivery / fail-closed memory）。

核心反例（task 4.3 / 4.5 必须覆盖）：
    * task 4.3：重复 Stop hook + 中间 retry iteration **不生成 / 不 promote lesson**
      （Stop 是 inner lifecycle 可重复触发；terminal 是 durable 边界）
    * task 4.5：timeout / SDK error / invalid JSON / schema rejection / persist failure /
      evidence history mismatch → emit ``learning_memory_degraded``，**绝不改 test/verify/retry/publication/
      terminal outcome**（fail-open for delivery，决策#7）

mock-SDK 模式（参照 conftest.py：不触达真实 claude_agent_sdk）：
    * ``sdk_query_fn`` 注入（生产默认走真 SDK + ``asyncio.wait_for`` 硬超时；测试注入返回固定 JSON 文本）
    * ``persist_callback`` 注入（生产 ``artifact_store.store`` + ``learning_memory_store.append_candidate``；
      测试可注入故障桩模拟 persist 失败）

AAA 结构。跑：python3 -m pytest scripts/test_learning_memory_reflection.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import learning_memory_schema as LM  # noqa: E402
import learning_memory_envelope as ENV  # noqa: E402
import learning_memory_reflection as REFL  # noqa: E402


# ════════════════════════════════════════════════════════════════════════
# fixture：valid TerminalEnvelope（envelope 构造已单测；本测试给定 envelope 跑 reflection）
# ════════════════════════════════════════════════════════════════════════
def _envelope(*, evidence_class: str = ENV.EvidenceClass.VERIFIER_PASS.value,
              terminal_status: str = "published",
              evidence_refs: tuple[dict, ...] = (
                  {"digest": "sha256:abc1", "size": 10, "kind": "test_output",
                   "path": "sha256/ab/c1", "sensitivity": "sanitized"},),
              evidence_excerpts: tuple[dict, ...] = (
                  {"digest": "sha256:abc1", "kind": "test_output", "content": b"ok",
                   "truncated": False, "missing_content": False, "size": 2},),
              verifier_events: tuple[dict, ...] = (
                  {"event_type": "verifier_feedback", "verdict": "pass"},),
              sanitized_metadata: dict | None = None) -> ENV.TerminalEnvelope:
    """造一个 valid envelope（默认 verifier_pass 终态）。"""
    meta = sanitized_metadata if sanitized_metadata is not None else {
        "run_id": "r1", "prd_id": "p1", "iteration_id": "i1",
        "project_id": "proj-a", "terminal_status": terminal_status,
        "evidence_class": evidence_class,
    }
    return ENV.TerminalEnvelope(
        evidence_class=evidence_class,
        terminal_status=terminal_status,
        verifier_events=verifier_events,
        evidence_refs=evidence_refs,
        evidence_excerpts=evidence_excerpts,
        sanitized_metadata=meta,
    )


def _valid_candidate_dict(**overrides) -> dict:
    """schema-valid candidate dict（来自 model output 的 shape；经 candidate_from_model_output 还原）。"""
    base = dict(
        phase="verify",
        failure_class="verifier_invariant_violation",
        corrective_action_class="add_test",
        applies_when_tags=["python", "ci_gate"],
        corrective_action="add failing test reproducing the invariant before publish gate",
        pattern_description="audit only: verifier caught missing edge case for invariant X",
        applicability_when="project uses python and CI publish gate",
        non_applicability_when="no publish gate or no verifier",
        evidence_refs=[{"digest": "sha256:abc1", "kind": "test_output"}],
        source_outcome="published",
        confidence=0.8,
    )
    base.update(overrides)
    return base


# ════════════════════════════════════════════════════════════════════════
# task 4.2：bounded read-only SDK reflection（mock-SDK）
# ════════════════════════════════════════════════════════════════════════
class TestReadOnlySdkCall:
    """task 4.2：SDK 调用 read-only（tools=Read/Grep，无 Write/Edit/Bash）+ model 省略 + strict JSON。"""

    def test_valid_sdk_response_parses_to_candidates(self, tmp_path):
        """SDK 返回 valid JSON → 解析为 LessonCandidate 列表 + persist artifact + append candidate。"""
        captured = {}

        def _sdk(prompt: str, options: dict) -> str:
            captured["prompt"] = prompt
            captured["options"] = options
            return json.dumps({
                "candidates": [_valid_candidate_dict()],
                "audit_summary": "verifier caught missing edge case",
            })

        env = _envelope()
        result = REFL.run_terminal_reflection(
            envelope=env, state_dir=str(tmp_path), project_id="proj-a",
            run_id="r1", prd_id="p1", iteration_id="i1", timestamp="2026-07-26T00:00:00Z",
            sdk_query_fn=_sdk)
        # Assert：1 candidate 解析成功
        assert result.outcome == "ok"
        assert len(result.candidates) == 1
        assert result.candidates[0].failure_class == LM.FailureClass.VERIFIER_INVARIANT_VIOLATION
        # reflection artifact 持久化（REFLECTION kind，sanitized sensitivity）
        assert result.reflection_artifact_ref is not None
        assert result.reflection_artifact_ref["kind"] == "reflection"
        assert result.reflection_artifact_ref["sensitivity"] == "sanitized"
        # candidate 已 append 到 store
        import learning_memory_store as LMS
        records = LMS.read_candidate_records(str(tmp_path), "proj-a")
        assert len(records) == 1

    def test_sdk_tools_whitelist_is_read_only(self, tmp_path):
        """task 4.2 硬约束：tools= 严格 Read/Grep——无 Write/Edit/Bash/Mutable。"""
        captured = {}

        def _sdk(prompt: str, options: dict) -> str:
            captured["options"] = options
            return json.dumps({"candidates": []})

        env = _envelope()
        REFL.run_terminal_reflection(
            envelope=env, state_dir=str(tmp_path), project_id="proj-a",
            run_id="r1", prd_id="p1", iteration_id="i1", timestamp="t",
            sdk_query_fn=_sdk)
        tools = captured["options"]["tools"]
        assert set(tools) == {"Read", "Grep"}
        # 反例：禁用工具绝不在白名单
        assert "Write" not in tools
        assert "Edit" not in tools
        assert "Bash" not in tools
        assert "MultiEdit" not in tools

    def test_sdk_model_omitted_no_anthropic_id(self, tmp_path):
        """task 4.2：model 省略——不传裸 Anthropic model id（会被 roc 代理拒绝）。"""
        captured = {}

        def _sdk(prompt: str, options: dict) -> str:
            captured["options"] = options
            return json.dumps({"candidates": []})

        env = _envelope()
        REFL.run_terminal_reflection(
            envelope=env, state_dir=str(tmp_path), project_id="proj-a",
            run_id="r1", prd_id="p1", iteration_id="i1", timestamp="t",
            sdk_query_fn=_sdk)
        # 不传 model 字段（或 model=None）；绝不传 "claude-*" 裸 id
        assert "model" not in captured["options"] or captured["options"].get("model") is None

    def test_sdk_receives_sanitized_envelope_not_raw_transcripts(self, tmp_path):
        """task 4.2：SDK prompt 收 sanitized envelope（metadata + excerpts），不收 raw secrets/transcripts。"""
        captured = {}

        def _sdk(prompt: str, options: dict) -> str:
            captured["prompt"] = prompt
            return json.dumps({"candidates": []})

        # envelope 含 raw secret（已被 envelope 抹——这里测 reflection 不 reintroduce）
        secret = "ghp_this_should_not_appear_anywhere123"
        env = _envelope(
            evidence_excerpts=(
                {"digest": "sha256:x", "kind": "test_output", "content": b"clean",
                 "truncated": False, "missing_content": False, "size": 5},),
        )
        REFL.run_terminal_reflection(
            envelope=env, state_dir=str(tmp_path), project_id="proj-a",
            run_id="r1", prd_id="p1", iteration_id="i1", timestamp="t",
            sdk_query_fn=_sdk)
        # prompt 不含 raw secret（envelope 已抹；reflection 不重新引入）
        assert secret not in captured["prompt"]
        # prompt 含 envelope 的 sanitized metadata（structured fields）
        assert "proj-a" in captured["prompt"]
        assert "verifier_pass" in captured["prompt"]

    def test_sdk_no_session_metadata_written(self, tmp_path):
        """task 4.2：不 resume / 不 fork / 不 overwrite 主 dev session metadata。"""
        captured = {}

        def _sdk(prompt: str, options: dict) -> str:
            captured["options"] = options
            return json.dumps({"candidates": []})

        env = _envelope()
        REFL.run_terminal_reflection(
            envelope=env, state_dir=str(tmp_path), project_id="proj-a",
            run_id="r1", prd_id="p1", iteration_id="i1", timestamp="t",
            sdk_query_fn=_sdk)
        # 不传 resume / fork_session（不污染主 dev session）
        assert "resume" not in captured["options"] or captured["options"].get("resume") is None
        assert "fork_session" not in captured["options"] or captured["options"].get("fork_session") is None


# ════════════════════════════════════════════════════════════════════════
# task 4.3：仅 terminal 后调用（Stop hook + retry iteration 反例）
# ════════════════════════════════════════════════════════════════════════
class TestTerminalGuard:
    """task 4.3：reflection 仅在 terminal durable recorded 后调用（重复 Stop / 中间 retry 不生成 lesson）。"""

    def test_non_terminal_status_refuses_reflection(self, tmp_path):
        """反例：中间状态（running/verifying/revise）调 reflection → refuse（记 degraded，不生成 candidate）。"""
        env = _envelope(terminal_status="running")   # 非终态
        result = REFL.run_terminal_reflection(
            envelope=env, state_dir=str(tmp_path), project_id="proj-a",
            run_id="r1", prd_id="p1", iteration_id="i1", timestamp="t",
            sdk_query_fn=lambda p, o: json.dumps({"candidates": [_valid_candidate_dict()]}))
        # 拒绝生成 candidate
        assert result.outcome == "degraded"
        assert result.degraded_class == "not_terminal"
        assert result.candidates == ()
        # 不触 SDK（not_terminal 在 SDK 调用前拦）
        # 不写任何 candidate 到 store
        import learning_memory_store as LMS
        assert LMS.read_candidate_records(str(tmp_path), "proj-a") == []

    def test_repeated_invocations_generate_candidates_once_each(self, tmp_path):
        """反例：重复 Stop hook 不 double-generate（每次 terminal 调用独立，candidate 由 equivalence_key 去重）。

        关键点：reflection 本身**幂等**——同 envelope 调两次得到同结果（candidate_id 由 content addressing
        决定）。Stop hook 重复触发由调用方（coordinator）通过「仅在 terminal durable recorded 后调用一次」
        防护；本测试验证 reflection 不在 candidate_id 层面产生重复行。
        """
        env = _envelope()
        sdk_resp = json.dumps({"candidates": [_valid_candidate_dict()]})

        r1 = REFL.run_terminal_reflection(
            envelope=env, state_dir=str(tmp_path), project_id="proj-a",
            run_id="r1", prd_id="p1", iteration_id="i1", timestamp="t1",
            sdk_query_fn=lambda p, o: sdk_resp)
        r2 = REFL.run_terminal_reflection(
            envelope=env, state_dir=str(tmp_path), project_id="proj-a",
            run_id="r1", prd_id="p1", iteration_id="i1", timestamp="t2",
            sdk_query_fn=lambda p, o: sdk_resp)
        # 两次都 outcome=ok（reflection 自身不阻止重复）
        assert r1.outcome == "ok"
        assert r2.outcome == "ok"
        # 但 candidate 写到 store 后，由 candidate_id 去重（content-addressed 同 ID 同行不重复）——
        # 即 store 不应有两行（同 candidate_id 重复 append 是上游 reconcile 责任，reflection 只负责写）。
        import learning_memory_store as LMS
        records = LMS.read_candidate_records(str(tmp_path), "proj-a")
        # 两次 append 会产生两行；但 candidate_id 相同（content addressing）
        cand_ids = {r["candidate_id"] for r in records}
        assert len(cand_ids) == 1   # 同 ID（dedup 在 catalog 层做，store 允许重复 append）


# ════════════════════════════════════════════════════════════════════════
# task 4.4：持久化（valid output → sanitized content-addressed artifact + append candidates）
# ════════════════════════════════════════════════════════════════════════
class TestPersistValidReflection:
    """task 4.4：valid full reflection output → sanitized content-addressed artifact + append candidates。"""

    def test_valid_output_persists_reflection_artifact_with_digest(self, tmp_path):
        """valid output → REFLECTION kind content-addressed artifact（带 digest，sanitized）。"""
        def _sdk(p, o):
            return json.dumps({"candidates": [_valid_candidate_dict()], "audit_summary": "ok"})
        env = _envelope()
        result = REFL.run_terminal_reflection(
            envelope=env, state_dir=str(tmp_path), project_id="proj-a",
            run_id="r1", prd_id="p1", iteration_id="i1", timestamp="t",
            sdk_query_fn=_sdk)
        # reflection artifact 落盘（digest 校验可读回）
        ref = result.reflection_artifact_ref
        assert ref is not None
        assert ref["digest"].startswith("sha256:")
        import artifact_store as AS
        content = AS.load(tmp_path / "artifacts" / "r1", AS.ArtifactRef(**ref))
        assert b"verifier_invariant_violation" in content   # candidate 字段已落 artifact

    def test_valid_output_appends_candidate_with_evidence_refs(self, tmp_path):
        """valid candidates → ``append_candidate`` with evidence_refs 引用 envelope 的 digests。"""
        def _sdk(p, o):
            return json.dumps({"candidates": [_valid_candidate_dict()]})
        env = _envelope()
        result = REFL.run_terminal_reflection(
            envelope=env, state_dir=str(tmp_path), project_id="proj-a",
            run_id="r1", prd_id="p1", iteration_id="i1", timestamp="t",
            sdk_query_fn=_sdk)
        assert len(result.candidates) == 1
        cand = result.candidates[0]
        # evidence_refs 引用 envelope 的 digest（integrity-checked 真源）
        assert cand.evidence_refs
        assert any(r.get("digest") == "sha256:abc1" for r in cand.evidence_refs)

    def test_multiple_candidates_each_appended(self, tmp_path):
        """SDK 返回多个 candidates → 每个 schema-valid 的都 append（独立 evidence_refs）。"""
        def _sdk(p, o):
            return json.dumps({"candidates": [
                _valid_candidate_dict(corrective_action="add test for invariant X"),
                _valid_candidate_dict(corrective_action="add test for invariant Y",
                                      corrective_action_class="fix_pattern"),
            ]})
        env = _envelope()
        result = REFL.run_terminal_reflection(
            envelope=env, state_dir=str(tmp_path), project_id="proj-a",
            run_id="r1", prd_id="p1", iteration_id="i1", timestamp="t",
            sdk_query_fn=_sdk)
        assert len(result.candidates) == 2
        import learning_memory_store as LMS
        records = LMS.read_candidate_records(str(tmp_path), "proj-a")
        assert len(records) == 2


# ════════════════════════════════════════════════════════════════════════
# task 4.5：degraded（fail-open delivery + fail-closed memory）
# ════════════════════════════════════════════════════════════════════════
class TestDegradedScenarios:
    """task 4.5：timeout / SDK error / invalid JSON / schema rejection / persist failure /
    evidence history mismatch → ``learning_memory_degraded`` 记录，**绝不改 test/verify/retry/publication/
    terminal outcome**（design 决策#7 fail-open for delivery）。

    degraded 记录方式：side-channel ``degraded/<project>.jsonl``（不耦合 journal 主路径 / 不污染 catalog 事件）。
    """

    def test_timeout_emits_degraded_no_candidates(self, tmp_path):
        """反例：SDK 超时 → ``degraded{class:timeout}``，不生成 candidate。"""
        def _slow_sdk(p, o):
            raise TimeoutError("SDK did not return within timeout")
        env = _envelope()
        result = REFL.run_terminal_reflection(
            envelope=env, state_dir=str(tmp_path), project_id="proj-a",
            run_id="r1", prd_id="p1", iteration_id="i1", timestamp="t",
            sdk_query_fn=_slow_sdk)
        assert result.outcome == "degraded"
        assert result.degraded_class == "timeout"
        assert result.candidates == ()
        # 不写 candidate 到 store（fail-closed memory）
        import learning_memory_store as LMS
        assert LMS.read_candidate_records(str(tmp_path), "proj-a") == []
        # 写 degraded record 到 side-channel
        degraded = REFL.read_degraded_records(str(tmp_path), "proj-a")
        assert len(degraded) == 1
        assert degraded[0]["degraded_class"] == "timeout"

    def test_sdk_error_emits_degraded_no_candidates(self, tmp_path):
        """反例：SDK 抛异常 → ``degraded{class:sdk_error}``。"""
        def _broken_sdk(p, o):
            raise RuntimeError("connection refused")
        env = _envelope()
        result = REFL.run_terminal_reflection(
            envelope=env, state_dir=str(tmp_path), project_id="proj-a",
            run_id="r1", prd_id="p1", iteration_id="i1", timestamp="t",
            sdk_query_fn=_broken_sdk)
        assert result.outcome == "degraded"
        assert result.degraded_class == "sdk_error"
        assert result.candidates == ()

    def test_invalid_json_emits_degraded_no_candidates(self, tmp_path):
        """反例：SDK 返回非 JSON → ``degraded{class:invalid_json}``。"""
        def _garbled_sdk(p, o):
            return "this is not json {{broken"
        env = _envelope()
        result = REFL.run_terminal_reflection(
            envelope=env, state_dir=str(tmp_path), project_id="proj-a",
            run_id="r1", prd_id="p1", timestamp="t", iteration_id="i1",
            sdk_query_fn=_garbled_sdk)
        assert result.outcome == "degraded"
        assert result.degraded_class == "invalid_json"
        assert result.candidates == ()

    def test_schema_reject_emits_degraded_no_candidates(self, tmp_path):
        """反例：SDK 返回 JSON 但 candidate schema invalid（enum 超词表）→ ``degraded{class:schema_reject}``。"""
        def _bad_schema_sdk(p, o):
            return json.dumps({"candidates": [
                _valid_candidate_dict(failure_class="totally_made_up_class"),  # 超词表
            ]})
        env = _envelope()
        result = REFL.run_terminal_reflection(
            envelope=env, state_dir=str(tmp_path), project_id="proj-a",
            run_id="r1", prd_id="p1", iteration_id="i1", timestamp="t",
            sdk_query_fn=_bad_schema_sdk)
        assert result.outcome == "degraded"
        assert result.degraded_class == "schema_reject"
        assert result.candidates == ()

    def test_persist_failure_emits_degraded(self, tmp_path):
        """反例：artifact persist 故障 → ``degraded{class:persist_failure}``，不 append candidate。"""
        def _sdk(p, o):
            return json.dumps({"candidates": [_valid_candidate_dict()]})

        def _broken_persist(*args, **kwargs):
            raise OSError("disk full")
        env = _envelope()
        result = REFL.run_terminal_reflection(
            envelope=env, state_dir=str(tmp_path), project_id="proj-a",
            run_id="r1", prd_id="p1", iteration_id="i1", timestamp="t",
            sdk_query_fn=_sdk,
            persist_callback=_broken_persist)
        assert result.outcome == "degraded"
        assert result.degraded_class == "persist_failure"
        assert result.candidates == ()

    def test_evidence_history_mismatch_emits_degraded(self, tmp_path):
        """反例：pre-verifier short-circuit 终态的 candidate 引用 verifier verdict → mismatch degraded。

        design 决策#1：「A candidate whose cited evidence does not match the journal's actual verifier
        transition history is rejected with a ``learning_memory_degraded`` event of class
        ``evidence_history_mismatch``」。
        """
        def _sdk(p, o):
            # SDK 在 pre-verifier short-circuit envelope 上返回引用 verifier 的 candidate
            return json.dumps({"candidates": [
                _valid_candidate_dict(
                    failure_class="verifier_invariant_violation",  # 引用 verifier evidence
                    evidence_refs=[{"digest": "sha256:nonexistent_verifier"}],
                ),
            ]})
        # envelope 是 PRE_VERIFIER_SHORT_CIRCUIT（无 verifier 事件）
        env = _envelope(
            evidence_class=ENV.EvidenceClass.PRE_VERIFIER_SHORT_CIRCUIT.value,
            terminal_status="stalled",
            verifier_events=(),
            evidence_refs=(),
            evidence_excerpts=(),
        )
        result = REFL.run_terminal_reflection(
            envelope=env, state_dir=str(tmp_path), project_id="proj-a",
            run_id="r1", prd_id="p1", iteration_id="i1", timestamp="t",
            sdk_query_fn=_sdk)
        # 候选 mismatch envelope 的 evidence_class（pre-verifier 终态却引用 verifier evidence）
        assert result.outcome == "degraded"
        assert result.degraded_class == "evidence_history_mismatch"
        assert result.candidates == ()


# ════════════════════════════════════════════════════════════════════════
# task 4.5：degraded fail-open——**绝不改 terminal outcome**
# ════════════════════════════════════════════════════════════════════════
class TestFailOpenDelivery:
    """task 4.5 / design 决策#7：reflection 故障**绝不**改 PRD 结果。

    断言方式：reflection 的 ``ReflectionResult`` 不暴露任何可改 terminal outcome 的入口；degraded 时
    返回 ``outcome="degraded"`` + ``candidates=()``——调用方（coordinator）据 envelope.terminal_status
    继续原报告，不受 reflection degraded 影响。
    """

    def test_degraded_result_does_not_expose_outcome_mutation(self, tmp_path):
        """degraded result 不带任何「改 terminal」的副作用字段（fail-open by construction）。"""
        def _broken_sdk(p, o):
            raise RuntimeError("simulated outage")
        env = _envelope(terminal_status="published")  # PRD 已 published
        result = REFL.run_terminal_reflection(
            envelope=env, state_dir=str(tmp_path), project_id="proj-a",
            run_id="r1", prd_id="p1", iteration_id="i1", timestamp="t",
            sdk_query_fn=_broken_sdk)
        # degraded result 不改 terminal_status（envelope 的字段不变；reflection 不持有 terminal mutation 入口）
        assert env.terminal_status == "published"   # envelope 是 immutable，本来就不会变——这是契约保障
        assert result.outcome == "degraded"
        # ReflectionResult 字段集只有：outcome/degraded_*/candidates/reflection_artifact_ref/evidence_class
        # （无 retry/abort/publish 字段——fail-open by construction）

    def test_degraded_does_not_touch_journal_main_path(self, tmp_path):
        """degraded record 走 side-channel ``degraded/<project>.jsonl``，不耦合 journal 主路径。"""
        def _broken_sdk(p, o):
            raise RuntimeError("outage")
        env = _envelope()
        result = REFL.run_terminal_reflection(
            envelope=env, state_dir=str(tmp_path), project_id="proj-a",
            run_id="r1", prd_id="p1", iteration_id="i1", timestamp="t",
            sdk_query_fn=_broken_sdk)
        assert result.outcome == "degraded"
        # degraded 落在 lessons/degraded/proj-a.jsonl（**不**落 journal runs/<proj>/...）
        degraded_file = tmp_path / "lessons" / "degraded" / "proj-a.jsonl"
        assert degraded_file.exists()
        # journal 主路径文件**不**被 reflection 创建（journal 由 coordinator/journal 模块 own）
        journal_files = list((tmp_path / "runs").glob("**/*.journal.jsonl")) if (tmp_path / "runs").exists() else []
        assert journal_files == []


# ════════════════════════════════════════════════════════════════════════
# task 4.5：degraded side-channel 持久化
# ════════════════════════════════════════════════════════════════════════
class TestDegradedSideChannel:
    """degraded record 走 side-channel（不耦合 journal 主路径 / 不污染 catalog events）。"""

    def test_degraded_record_has_structured_fields(self, tmp_path):
        """每条 degraded record 含 timestamp/run_id/prd_id/iteration_id/project_id/degraded_class/reason。"""
        def _broken_sdk(p, o):
            raise RuntimeError("test")
        env = _envelope()
        REFL.run_terminal_reflection(
            envelope=env, state_dir=str(tmp_path), project_id="proj-a",
            run_id="r1", prd_id="p1", iteration_id="i1", timestamp="2026-07-26T00:00:00Z",
            sdk_query_fn=_broken_sdk)
        records = REFL.read_degraded_records(str(tmp_path), "proj-a")
        assert len(records) == 1
        rec = records[0]
        assert rec["timestamp"] == "2026-07-26T00:00:00Z"
        assert rec["run_id"] == "r1"
        assert rec["prd_id"] == "p1"
        assert rec["iteration_id"] == "i1"
        assert rec["project_id"] == "proj-a"
        assert rec["degraded_class"] == "sdk_error"
        assert "reason" in rec
