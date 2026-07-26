#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_learning_memory_envelope.py — add-cross-prd-learning-memory Section 4.1 单测。

锁定 task 4.1（terminal evidence envelope）契约——spec design 决策#1 硬约束：

**envelope 由 journal 实际 verifier transition history 选择，不是 terminal 状态标签**。

核心反例（task 4.1 必须覆盖）：
  * pre-verifier ``blocked_external_state``：terminal=EXTERNAL_BLOCKED + journal 无 verifier 事件
    → PRE_VERIFIER_SHORT_CIRCUIT
  * post-verifier ``blocked_external_state``：terminal=EXTERNAL_BLOCKED + journal 有 verifier pass
    → VERIFIER_PASS（post-publish reconcile UNKNOWN，验证器已过；同终态标签，不同 evidence class）
  * ``verifier-revise-exhausted``：terminal=FAILED/STALLED + journal verifier revise > 0 + 无 pass
    → VERIFIER_REVISE_EXHAUSTED（推断：revise count > 0 + no pass + terminal != PUBLISHED）
  * pre-verifier ``blocked_evidence``：terminal=BLOCKED_EVIDENCE + journal 无 verifier 事件
    → PRE_VERIFIER_SHORT_CIRCUIT
  * post-verifier ``blocked_evidence``：terminal=BLOCKED_EVIDENCE + journal 有 verifier 事件
    → 非PRE_VERIFIER_SHORT_CIRCUIT（同终态标签，不同 evidence class）

envelope 排除 raw secrets + unbounded transcripts（design 决策#2 + risks）：
  * artifact 内容经 ``artifact_store.redact_secrets`` 抹密钥
  * 每个 artifact 摘要截 MAX_ARTIFACT_EXCERPT_BYTES（防 unbounded transcripts 进 prompt）
  * evidence_refs 必带 digest（integrity-checked，spec「readable integrity-checked evidence」）

AAA 结构。跑：python3 -m pytest scripts/test_learning_memory_envelope.py -q
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import learning_memory_envelope as ENV  # noqa: E402


# ════════════════════════════════════════════════════════════════════════
# fixture：journal event 鸭子类型（最小契约：event_type + payload）
# ════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class _Ev:
    """最小 journal event 鸭子（生产 JournalEvent 也满足该契约）。"""
    event_type: str
    payload: dict
    event_id: str = ""
    iteration_id: str = "iter-1"
    run_id: str = "run-1"
    prd_id: str = "prd-1"


def _artifact_loader_factory(content_map: dict[str, bytes]):
    """造 mock artifact_loader：digest → bytes 内容（不触达真实 artifact_store）。"""
    def _load(ref: dict) -> bytes:
        # 生产 ref 是 ArtifactRef asdict（含 digest/path/kind/...）；loader 按 digest 取内容
        d = ref.get("digest", "") if isinstance(ref, dict) else ""
        return content_map.get(d, b"")
    return _load


def _make_artifact_ref(digest: str, *, kind: str = "diff") -> dict:
    """造 ArtifactRef-like dict（含 digest——integrity-checked 前提）。"""
    return {
        "digest": digest,
        "size": 100,
        "kind": kind,
        "path": f"sha256/{digest[7:9]}/{digest[9:21]}",
        "sensitivity": "sanitized",
    }


# ════════════════════════════════════════════════════════════════════════
# task 4.1 反例：terminal 标签相同，evidence_class 由 journal verifier history 决定
# ════════════════════════════════════════════════════════════════════════
class TestPreVsPostVerifierBlockedExternalState:
    """task 4.1 关键反例：blocked_external_state 同终态标签——pre vs post-verifier 走不同 evidence class。

    spec design 决策#1 表：「``blocked_external_state`` arises both before the verifier (an early
    remote-state UNKNOWN) and after it (verifier passed, then publication reconcile returned UNKNOWN)」。
    """

def test_pre_verifier_blocked_external_state_is_pre_verifier_short_circuit():
    """反例 1：pre-verifier blocked_external_state——journal 无 verifier 事件 → PRE_VERIFIER_SHORT_CIRCUIT。"""
    # Arrange: terminal=EXTERNAL_BLOCKED，但 journal 里**只有** admission 阶段 external_blocked 事件，
    # 没有任何 verifying/revise/verifier_feedback 事件（pre-verifier short-circuit）。
    events = [
        _Ev("planned", {"base": "main"}),
        _Ev("external_blocked", {"reason": "branch_protection_unknown",
                                  "query_state": "UNKNOWN"}),
    ]
    loader = _artifact_loader_factory({})
    # Act
    env = ENV.build_terminal_envelope(
        terminal_status="external_blocked",
        events=events, artifact_loader=loader,
        run_id="r1", prd_id="p1", iteration_id="i1", project_id="proj-a")
    # Assert
    assert env.evidence_class == ENV.EvidenceClass.PRE_VERIFIER_SHORT_CIRCUIT.value
    assert env.verifier_events == ()
    # matching mechanical evidence: external-state query 三态记录
    assert env.sanitized_metadata.get("external_state_query") == "UNKNOWN"


def test_post_verifier_blocked_external_state_is_verifier_pass():
    """反例 2：post-verifier blocked_external_state——verifier 已 pass，post-publish reconcile UNKNOWN。

    同终态标签 external_blocked，但 journal 有 verifier pass 事件 → VERIFIER_PASS（design 决策#1 表第 1 行）。
    matching evidence = verifier pass verdict + fresh-green TestEvidence + reconcile record。
    """
    # Arrange: terminal=EXTERNAL_BLOCKED，但 journal 走过 verifying → publish_ready → external_blocked
    test_ref = _make_artifact_ref("sha256:abcd0123", kind="test_output")
    verifier_ref = _make_artifact_ref("sha256:beef4567", kind="verifier_feedback")
    events = [
        _Ev("planned", {"base": "main"}),
        _Ev("running", {}),
        _Ev("agent_finished", {}),
        _Ev("test_blocked", {}),       # 一次 test 门拦
        _Ev("running", {}),            # retry
        _Ev("agent_finished", {}),
        _Ev("verifying", {"test_evidence_ref": test_ref}),
        _Ev("verifier_feedback", {"verdict": "pass", "artifact_ref": verifier_ref}),
        _Ev("publish_ready", {}),
        _Ev("external_blocked", {"reason": "reconcile_pr_unknown",
                                  "query_state": "UNKNOWN"}),   # post-publish reconcile UNKNOWN
    ]
    loader = _artifact_loader_factory({
        "sha256:abcd0123": b"PASSED 5 tests\n",
        "sha256:beef4567": b'{"verdict":"pass","notes":"ok"}\n',
    })
    # Act
    env = ENV.build_terminal_envelope(
        terminal_status="external_blocked",
        events=events, artifact_loader=loader,
        run_id="r1", prd_id="p1", iteration_id="i1", project_id="proj-a")
    # Assert
    assert env.evidence_class == ENV.EvidenceClass.VERIFIER_PASS.value
    # verifier_events 抽出至少 1 条（pass verdict）
    assert len(env.verifier_events) >= 1
    assert any(ev.get("verdict") == "pass" for ev in env.verifier_events)
    # evidence_refs 包含 verifier artifact + test artifact（integrity-checked digests）
    digests = {r["digest"] for r in env.evidence_refs}
    assert "sha256:abcd0123" in digests
    assert "sha256:beef4567" in digests


class TestVerifierReviseExhausted:
    """task 4.1：verifier-revise-exhausted 推断——不新增 event_type，从 journal 推断。"""

    def test_revise_events_no_pass_terminal_failed_is_revise_exhausted(self):
        """反例 3：verifier revise > 0 + 无 pass + terminal != PUBLISHED → VERIFIER_REVISE_EXHAUSTED。

        spec design 决策#1 表第 2 行：matching evidence = verifier revise verdict 序列 +
        revise-exhaustion record + independent_verify test_log。
        """
        # Arrange: 终态 FAILED，2 轮 revise（无 pass）
        revise1_ref = _make_artifact_ref("sha256:dead0001", kind="verifier_feedback")
        revise2_ref = _make_artifact_ref("sha256:dead0002", kind="verifier_feedback")
        test_log_ref = _make_artifact_ref("sha256:fade0003", kind="test_output")
        events = [
            _Ev("planned", {}),
            _Ev("running", {}),
            _Ev("agent_finished", {}),
            _Ev("verifying", {"test_evidence_ref": test_log_ref}),
            _Ev("verifier_feedback", {"verdict": "revise", "artifact_ref": revise1_ref}),
            _Ev("revise", {}),
            _Ev("running", {}),
            _Ev("agent_finished", {}),
            _Ev("verifying", {}),
            _Ev("verifier_feedback", {"verdict": "revise", "artifact_ref": revise2_ref}),
            _Ev("revise", {}),
            _Ev("failed", {"reason": "revise_exhausted"}),    # 终态（重试耗尽）
        ]
        loader = _artifact_loader_factory({
            "sha256:dead0001": b'{"verdict":"revise","missing":"edge case X"}\n',
            "sha256:dead0002": b'{"verdict":"revise","missing":"edge case Y"}\n',
            "sha256:fade0003": b"FAILED 1 test\n",
        })
        # Act
        env = ENV.build_terminal_envelope(
            terminal_status="failed",
            events=events, artifact_loader=loader,
            run_id="r1", prd_id="p1", iteration_id="i1", project_id="proj-a")
        # Assert
        assert env.evidence_class == ENV.EvidenceClass.VERIFIER_REVISE_EXHAUSTED.value
        assert len(env.verifier_events) == 2     # 2 条 revise verdict
        assert all(ev.get("verdict") == "revise" for ev in env.verifier_events)
        # evidence_refs 含 revise verdicts + test_log
        digests = {r["digest"] for r in env.evidence_refs}
        assert "sha256:dead0001" in digests
        assert "sha256:dead0002" in digests
        assert "sha256:fade0003" in digests

    def test_revise_then_pass_terminal_published_is_verifier_pass(self):
        """对照：有 revise 但最终 pass + terminal=PUBLISHED → VERIFIER_PASS（不是 revise_exhausted）。"""
        revise_ref = _make_artifact_ref("sha256:dead0009", kind="verifier_feedback")
        pass_ref = _make_artifact_ref("sha256:cafe0001", kind="verifier_feedback")
        events = [
            _Ev("verifying", {}),
            _Ev("verifier_feedback", {"verdict": "revise", "artifact_ref": revise_ref}),
            _Ev("revise", {}),
            _Ev("running", {}),
            _Ev("agent_finished", {}),
            _Ev("verifying", {}),
            _Ev("verifier_feedback", {"verdict": "pass", "artifact_ref": pass_ref}),
            _Ev("publish_ready", {}),
            _Ev("published", {}),
        ]
        loader = _artifact_loader_factory({})
        env = ENV.build_terminal_envelope(
            terminal_status="published",
            events=events, artifact_loader=loader,
            run_id="r", prd_id="p", iteration_id="i", project_id="proj")
        assert env.evidence_class == ENV.EvidenceClass.VERIFIER_PASS.value

    def test_published_with_no_verifier_events_falls_back_to_short_circuit(self):
        """边界：terminal=PUBLISHED 但 journal 缺 verifier 事件（理论不应发生，state machine 保证）。

        防御性兜底：不假设 PUBLISHED 自动意味 verifier pass——journal 为准（spec 决策#1）。
        若 journal 真缺 verifier 事件 + terminal=PUBLISHED → PRE_VERIFIER_SHORT_CIRCUIT
        （诚实记录 mismatch，由 reflection 端的 evidence_history_mismatch 处理）。
        """
        events = [
            _Ev("planned", {}),
            _Ev("running", {}),
            _Ev("agent_finished", {}),
            _Ev("published", {}),    # 跳过了 verifying（state machine 实际会拒，但若 journal 已污染）
        ]
        loader = _artifact_loader_factory({})
        env = ENV.build_terminal_envelope(
            terminal_status="published",
            events=events, artifact_loader=loader,
            run_id="r", prd_id="p", iteration_id="i", project_id="proj")
        # journal 为准：缺 verifier 事件 → PRE_VERIFIER_SHORT_CIRCUIT（不靠 terminal 标签推断）
        assert env.evidence_class == ENV.EvidenceClass.PRE_VERIFIER_SHORT_CIRCUIT.value


class TestPreVsPostVerifierBlockedEvidence:
    """task 4.1 反例 4：blocked_evidence 同终态标签——pre vs post-verifier 走不同 evidence class。"""

    def test_pre_verifier_blocked_evidence_is_pre_verifier_short_circuit(self):
        """terminal=BLOCKED_EVIDENCE + journal 无 verifier 事件 → PRE_VERIFIER_SHORT_CIRCUIT。"""
        events = [
            _Ev("planned", {}),
            _Ev("running", {}),
            _Ev("agent_finished", {}),
            _Ev("test_blocked", {"reason": "test_not_run"}),
            _Ev("blocked_evidence", {"reason": "evidence_persist_failed"}),
        ]
        loader = _artifact_loader_factory({})
        env = ENV.build_terminal_envelope(
            terminal_status="blocked_evidence",
            events=events, artifact_loader=loader,
            run_id="r", prd_id="p", iteration_id="i", project_id="proj")
        assert env.evidence_class == ENV.EvidenceClass.PRE_VERIFIER_SHORT_CIRCUIT.value

    def test_post_verifier_blocked_evidence_is_verifier_pass(self):
        """terminal=BLOCKED_EVIDENCE + journal 有 verifier pass → VERIFIER_PASS。"""
        test_ref = _make_artifact_ref("sha256:feed1111", kind="test_output")
        verifier_ref = _make_artifact_ref("sha256:feed2222", kind="verifier_feedback")
        events = [
            _Ev("verifying", {"test_evidence_ref": test_ref}),
            _Ev("verifier_feedback", {"verdict": "pass", "artifact_ref": verifier_ref}),
            _Ev("publish_ready", {}),
            _Ev("blocked_evidence", {"reason": "publish_evidence_persist_failed"}),
        ]
        loader = _artifact_loader_factory({})
        env = ENV.build_terminal_envelope(
            terminal_status="blocked_evidence",
            events=events, artifact_loader=loader,
            run_id="r", prd_id="p", iteration_id="i", project_id="proj")
        assert env.evidence_class == ENV.EvidenceClass.VERIFIER_PASS.value


class TestPreVerifierShortCircuitVariants:
    """task 4.1：pre-verifier short-circuit 全覆盖（design 决策#1 表第 3 行）。"""

    @pytest.mark.parametrize("terminal_status,reason", [
        ("stalled", "no_progress_dev_loop"),
        ("aborted", "admission_skip"),
        ("sandbox_blocked", "sandbox_violation"),
        ("state_corrupt", "journal_middle_corrupt"),
        ("test_blocked", "test_gate_blocked"),   # 注意：test_blocked 在 state machine 内可复活，但作为终态也合理
    ])
    def test_no_verifier_events_pre_verifier_short_circuit(self, terminal_status, reason):
        """无 verifier 事件的终态 → PRE_VERIFIER_SHORT_CIRCUIT。"""
        events = [
            _Ev("planned", {}),
            _Ev(terminal_status, {"reason": reason}),
        ]
        loader = _artifact_loader_factory({})
        env = ENV.build_terminal_envelope(
            terminal_status=terminal_status,
            events=events, artifact_loader=loader,
            run_id="r", prd_id="p", iteration_id="i", project_id="proj")
        assert env.evidence_class == ENV.EvidenceClass.PRE_VERIFIER_SHORT_CIRCUIT.value


# ════════════════════════════════════════════════════════════════════════
# task 4.1 反例：raw secrets + unbounded transcripts 必须 excluded
# ════════════════════════════════════════════════════════════════════════
class TestEnvelopeExcludesSecretsAndUnboundedTranscripts:
    """design 决策#2 + risks「Evidence contains secrets or excessive transcripts」。"""

    def test_raw_secrets_in_artifact_content_are_redacted(self):
        """artifact 内容含 GitHub PAT / Bearer token → envelope excerpts 必抹密钥（spec risks）。"""
        # Arrange: 一个 verifier_feedback artifact，内容含 raw GitHub PAT
        secret_pat = "ghp_abcdefghijklmnopqrstuvwxyz0123"
        raw_content = f'{{"verdict":"revise","error":"git push failed with token={secret_pat}"}}\n'.encode()
        artifact_ref = _make_artifact_ref("sha256:secr0001", kind="verifier_feedback")
        events = [
            _Ev("verifier_feedback", {"verdict": "revise", "artifact_ref": artifact_ref}),
        ]
        loader = _artifact_loader_factory({"sha256:secr0001": raw_content})
        # Act
        env = ENV.build_terminal_envelope(
            terminal_status="failed",
            events=events, artifact_loader=loader,
            run_id="r", prd_id="p", iteration_id="i", project_id="proj")
        # Assert：excerpt 必不含 raw PAT
        excerpts = env.evidence_excerpts
        assert len(excerpts) == 1
        assert secret_pat not in excerpts[0]["content"].decode("utf-8")
        assert "***" in excerpts[0]["content"].decode("utf-8")    # 已抹

    def test_unbounded_transcript_truncated_to_max_bytes(self):
        """artifact 内容超 MAX_ARTIFACT_EXCERPT_BYTES → 截断（防 unbounded transcripts 进 prompt）。"""
        huge_content = b"x" * (ENV.MAX_ARTIFACT_EXCERPT_BYTES + 5000)
        artifact_ref = _make_artifact_ref("sha256:huge0001", kind="transcript")
        events = [
            _Ev("verifier_feedback", {"verdict": "pass", "artifact_ref": artifact_ref}),
        ]
        loader = _artifact_loader_factory({"sha256:huge0001": huge_content})
        env = ENV.build_terminal_envelope(
            terminal_status="published",
            events=events, artifact_loader=loader,
            run_id="r", prd_id="p", iteration_id="i", project_id="proj")
        # Assert：excerpt 截到上限
        excerpts = env.evidence_excerpts
        assert len(excerpts) == 1
        assert excerpts[0]["truncated"] is True
        assert len(excerpts[0]["content"]) <= ENV.MAX_ARTIFACT_EXCERPT_BYTES

    def test_unbounded_secret_in_sanitized_metadata_redacted(self):
        """sanitized_metadata 中 external_state 等结构化字段也抹密钥。"""
        secret_pat = "gho_abcdefghijklmnopqrstuvwxyz0123"
        events = [
            _Ev("external_blocked", {"reason": "idempotency_unknown",
                                     "query_state": "UNKNOWN",
                                     "raw_error": f"token={secret_pat}"}),
        ]
        loader = _artifact_loader_factory({})
        env = ENV.build_terminal_envelope(
            terminal_status="external_blocked",
            events=events, artifact_loader=loader,
            run_id="r", prd_id="p", iteration_id="i", project_id="proj")
        # sanitized_metadata 不含 raw PAT
        meta_json = str(env.sanitized_metadata)
        assert secret_pat not in meta_json
        assert "***" in meta_json


# ════════════════════════════════════════════════════════════════════════
# task 4.1：evidence_refs integrity（digest 必备）
# ════════════════════════════════════════════════════════════════════════
class TestEvidenceRefsIntegrity:
    """evidence_refs 必带 digest（spec「readable integrity-checked evidence」）。"""

    def test_each_evidence_ref_has_digest(self):
        """每条 evidence_ref 必带 digest（integrity-checked 前提；envelope 不接受无 digest 的 ref）。"""
        test_ref = _make_artifact_ref("sha256:di100001", kind="test_output")
        verifier_ref = _make_artifact_ref("sha256:di200002", kind="verifier_feedback")
        events = [
            _Ev("verifying", {"test_evidence_ref": test_ref}),
            _Ev("verifier_feedback", {"verdict": "pass", "artifact_ref": verifier_ref}),
        ]
        loader = _artifact_loader_factory({})
        env = ENV.build_terminal_envelope(
            terminal_status="published",
            events=events, artifact_loader=loader,
            run_id="r", prd_id="p", iteration_id="i", project_id="proj")
        assert env.evidence_refs
        for ref in env.evidence_refs:
            assert "digest" in ref and ref["digest"].startswith("sha256:")

    def test_artifact_loader_failure_skips_excerpt_but_keeps_digest(self):
        """artifact_loader 返回空（IO 故障/digest 不存在）→ excerpt 缺，但 evidence_ref 仍保留 digest。

        design 决策#2「integrity-checked artifact excerpts」：digest 是真源；内容加载失败不可静默，
        但 envelope 容忍（reflection 时若需要 excerpt 会感知 missing_content 字段）。
        """
        artifact_ref = _make_artifact_ref("sha256:miss0001", kind="verifier_feedback")
        events = [
            _Ev("verifier_feedback", {"verdict": "pass", "artifact_ref": artifact_ref}),
        ]
        # loader 返回空 bytes（模拟加载失败）
        loader = _artifact_loader_factory({})
        env = ENV.build_terminal_envelope(
            terminal_status="published",
            events=events, artifact_loader=loader,
            run_id="r", prd_id="p", iteration_id="i", project_id="proj")
        # evidence_refs 仍带 digest（integrity 引用保留）
        assert any(r["digest"] == "sha256:miss0001" for r in env.evidence_refs)
        # excerpt 标 missing_content（不静默丢，但 reflection 感知）
        excerpts = env.evidence_excerpts
        assert len(excerpts) == 1
        assert excerpts[0]["missing_content"] is True


# ════════════════════════════════════════════════════════════════════════
# task 4.1：sanitized_metadata 边界（design「safe structured fields」）
# ════════════════════════════════════════════════════════════════════════
class TestSanitizedMetadata:
    """envelope 的 sanitized_metadata 仅留 schema-constrained 安全字段（不收 raw evidence）。"""

    def test_metadata_includes_safe_structured_fields(self):
        """metadata 含 run_id/prd_id/iteration_id/project_id/terminal_status/evidence_class——可定位，不含 raw。"""
        events = [_Ev("published", {})]
        loader = _artifact_loader_factory({})
        env = ENV.build_terminal_envelope(
            terminal_status="published",
            events=events, artifact_loader=loader,
            run_id="run-aaa", prd_id="prd-bb", iteration_id="iter-cc",
            project_id="proj-xx")
        m = env.sanitized_metadata
        assert m["run_id"] == "run-aaa"
        assert m["prd_id"] == "prd-bb"
        assert m["iteration_id"] == "iter-cc"
        assert m["project_id"] == "proj-xx"
        assert m["terminal_status"] == "published"
        assert m["evidence_class"] == ENV.EvidenceClass.PRE_VERIFIER_SHORT_CIRCUIT.value

    def test_metadata_records_revise_count_when_revise_exhausted(self):
        """evidence_class=REVISE_EXHAUSTED 时 metadata 记 revise_count（audit 用，机械可重放）。"""
        r1 = _make_artifact_ref("sha256:rv001aaa", kind="verifier_feedback")
        r2 = _make_artifact_ref("sha256:rv002bbb", kind="verifier_feedback")
        events = [
            _Ev("verifier_feedback", {"verdict": "revise", "artifact_ref": r1}),
            _Ev("revise", {}),
            _Ev("verifier_feedback", {"verdict": "revise", "artifact_ref": r2}),
            _Ev("revise", {}),
            _Ev("failed", {"reason": "revise_exhausted"}),
        ]
        loader = _artifact_loader_factory({})
        env = ENV.build_terminal_envelope(
            terminal_status="failed",
            events=events, artifact_loader=loader,
            run_id="r", prd_id="p", iteration_id="i", project_id="proj")
        assert env.evidence_class == ENV.EvidenceClass.VERIFIER_REVISE_EXHAUSTED.value
        assert env.sanitized_metadata.get("revise_count") == 2


# ════════════════════════════════════════════════════════════════════════
# 外部评审 P1 #2 复现：生产 emit 的 ``verifier`` event（带 verdict）必须被识别
# ════════════════════════════════════════════════════════════════════════
class TestProductionVerifierEmitCollected:
    """spec P1 #2 修复：envelope 必须收生产 emit 的 ``verifier`` event（带 verdict），不能只认
    ``verifier_feedback``。

    生产 emit（run_daily.py）：
        * L1208 ``sj.emit("verifier_feedback", ..., payload={"round", "digest", "path", "size"})``
          —— 反馈**内容**事件，**无 verdict 字段**
        * L1948 ``_sj.emit("verifier", ..., payload={"round", "verdict"})`` —— 判决**观测**事件，
          **有 verdict**

    bug 复现（修复前）：``_VERIFIER_EVENT_TYPES`` 只含 ``"verifier_feedback"`` →
    ``_collect_verifier_history`` 收到的 verifier_feedback 事件 payload 无 verdict → entry verdict="none" →
    ``_has_verifier_pass`` / ``_count_revise_verdicts`` 永远查不到 pass / revise verdict →
    evidence_class 误判为 PRE_VERIFIER_SHORT_CIRCUIT（即使 journal 实际走过 verifier pass）。

    修复后：``_VERIFIER_EVENT_TYPES = frozenset({"verifier_feedback", "verifier"})``；verifier 事件
    的 verdict 字段被正确抽出来参与 evidence_class 决策。
    """

    def test_verifier_event_with_pass_verdict_recognized_as_verifier_pass(self):
        """生产 emit ``verifier{round:1, verdict:"pass"}`` —— envelope 必须识别为 pass。

        修复前 bug：verifier event 不在 _VERIFIER_EVENT_TYPES → history 空（只有 verifying 这种 transition
        也被忽略）→ _has_verifier_pass=False → 即使 terminal=published 也判 PRE_VERIFIER_SHORT_CIRCUIT
        （违反 state machine 不变量：published 必经 verifier pass）。
        """
        # Arrange: 模拟生产 emit 序列（verifying → verifier{verdict:pass} → publish_ready → published）
        events = [
            _Ev("verifying", {"round": 1}),
            _Ev("verifier", {"round": 1, "verdict": "pass"}),    # 生产真实 shape（run_daily L1948）
            _Ev("publish_ready", {"round": 1}),
            _Ev("published", {}),
        ]
        loader = _artifact_loader_factory({})
        # Act
        env = ENV.build_terminal_envelope(
            terminal_status="published",
            events=events, artifact_loader=loader,
            run_id="r", prd_id="p", iteration_id="i", project_id="proj")
        # Assert：verifier event 的 pass verdict 被识别 → VERIFIER_PASS
        assert env.evidence_class == ENV.EvidenceClass.VERIFIER_PASS.value
        # verifier_events 收到至少 1 条带 verdict=pass 的 entry
        assert any(ev.get("verdict") == "pass" for ev in env.verifier_events), \
            f"verifier pass 事件未被收集: {env.verifier_events}"

    def test_verifier_event_revise_counted_for_revise_exhausted(self):
        """生产 emit ``verifier{verdict:"revise"}`` 多次 + 无 pass + terminal=failed →
        evidence_class=VERIFIER_REVISE_EXHAUSTED，revise_count 正确。

        修复前 bug：verifier 事件被忽略 → revise_count=0 → 落 PRE_VERIFIER_SHORT_CIRCUIT（错判）。
        """
        events = [
            _Ev("verifying", {"round": 1}),
            _Ev("verifier", {"round": 1, "verdict": "revise"}),
            _Ev("revise", {}),
            _Ev("verifying", {"round": 2}),
            _Ev("verifier", {"round": 2, "verdict": "revise"}),
            _Ev("revise", {}),
            _Ev("failed", {"reason": "revise_exhausted"}),
        ]
        loader = _artifact_loader_factory({})
        env = ENV.build_terminal_envelope(
            terminal_status="failed",
            events=events, artifact_loader=loader,
            run_id="r", prd_id="p", iteration_id="i", project_id="proj")
        assert env.evidence_class == ENV.EvidenceClass.VERIFIER_REVISE_EXHAUSTED.value
        assert env.sanitized_metadata.get("revise_count") == 2

    def test_verifier_event_pass_prevents_short_circuit_on_terminal_failed(self):
        """反例：terminal=failed 但 journal 有 verifier pass（罕见，post-pass reconcile failed 等）→
        VERIFIER_PASS（不是 PRE_VERIFIER_SHORT_CIRCUIT；verifier pass 是权威证据）。

        修复前 bug：verifier event 被忽略 → 即使有 verifier{verdict:pass} 也判 PRE_VERIFIER_SHORT_CIRCUIT。
        """
        events = [
            _Ev("verifying", {"round": 1}),
            _Ev("verifier", {"round": 1, "verdict": "pass"}),
            _Ev("publish_ready", {}),
            _Ev("failed", {"reason": "post_pass_reconcile_failed"}),
        ]
        loader = _artifact_loader_factory({})
        env = ENV.build_terminal_envelope(
            terminal_status="failed",
            events=events, artifact_loader=loader,
            run_id="r", prd_id="p", iteration_id="i", project_id="proj")
        assert env.evidence_class == ENV.EvidenceClass.VERIFIER_PASS.value

    def test_mixed_verifier_and_verifier_feedback_both_collected(self):
        """verifier（verdict）+ verifier_feedback（digest/path）并存 → 都进 verifier_history。

        生产 emit 同时有：
            * ``verifier`` 带 verdict（判决观测）
            * ``verifier_feedback`` 带 digest/path（反馈内容工件）
        envelope 应同时收两者——verifier 用于 verdict 判定，verifier_feedback 用于 evidence_refs（如果
        带 ArtifactRef-like dict；生产 L1208 emit 用裸 digest/path/size——不在 _extract_artifact_refs 范围
        内，与本测试无关，本测试只验 history 收集）。
        """
        events = [
            _Ev("verifier_feedback", {"round": 1, "digest": "sha256:abc", "path": "p", "size": 10}),
            _Ev("verifier", {"round": 1, "verdict": "pass"}),
            _Ev("published", {}),
        ]
        loader = _artifact_loader_factory({})
        env = ENV.build_terminal_envelope(
            terminal_status="published",
            events=events, artifact_loader=loader,
            run_id="r", prd_id="p", iteration_id="i", project_id="proj")
        # 两类事件都进 verifier_history（保时序）
        etypes = [ev.get("event_type") for ev in env.verifier_events]
        assert "verifier_feedback" in etypes
        assert "verifier" in etypes
        # verifier 事件带 verdict=pass
        assert any(ev.get("event_type") == "verifier" and ev.get("verdict") == "pass"
                   for ev in env.verifier_events)
