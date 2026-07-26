#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_learning_memory_canary.py — add-cross-prd-learning-memory Section 7 批次 2 测试。

锁定：
    * **升级 A**（injection 四重 gate）：parity/quality/allowlist/shadow gate 失败 → injection 降级；
      gate 全过 → injection_on=True。V1 fail-safe（profile 缺 parity/quality → False，绝不假阳）。
    * **升级 B**（Section 6 闭环 usage outcome 接线）：selected_lesson_ids + envelope → detect→classify→
      build→append。**红线**：unknown outcome 不持久化（Section 6 偏差 #3）。
    * **task 7.2 crash-recovery**：candidate append 前/后 + catalog replacement 前/后崩溃 → 幂等 replay +
      no duplicate promotion（append-only 真源；catalog 是 rebuildable projection）。
    * **task 7.3 正向 canary**：2 PRD → promotion → 第 3 PRD relevant injection → effectiveness feedback。
    * **task 7.4 负向 canaries**（6 反例）：1-PRD repeated / corrupt evidence / reflection outage /
      malformed catalog / irrelevant lesson / contradicted lesson。

mock-SDK + 直接驱动 store/catalog/effectiveness 状态机（不真跑 dispatch subprocess）。AAA 结构。
跑：python3 -m pytest scripts/test_learning_memory_canary.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import dataclasses
import learning_memory_schema as LM           # noqa: E402
import learning_memory_store as LMS           # noqa: E402
import learning_memory_catalog as LMCat       # noqa: E402
import learning_memory_retrieval as LMRet     # noqa: E402
import learning_memory_effectiveness as LMEff  # noqa: E402
import run_daily as RD                        # noqa: E402


# ════════════════════════════════════════════════════════════════════════
# fixture：schema-valid LessonCandidate / catalog seed
# ════════════════════════════════════════════════════════════════════════
def _cand_kwargs(**overrides):
    base = dict(
        project_id="proj-a",
        prd_id="prd-001",
        iteration_refs=("iter-1",),
        phase=LM.Phase.VERIFY,
        failure_class=LM.FailureClass.VERIFIER_INVARIANT_VIOLATION,
        corrective_action_class=LM.CorrectiveActionClass.ADD_TEST,
        applies_when_tags=(LM.AppliesWhenTag.PYTHON,),
        corrective_action="add failing test reproducing the invariant before publish gate",
        pattern_description="dev bypassed invariant; pattern repeats across PRDs",
        applicability_when="applies when dispatch to a python project with verifier gate",
        non_applicability_when="does not apply when no verifier configured",
        evidence_refs=({"digest": "sha256:abc", "kind": "test_output", "path": "sha256/ab/c"},),
        source_outcome="published",
        confidence=0.7,
        schema_version=1,
    )
    base.update(overrides)
    return base


def _candidate(**overrides):
    return LM.LessonCandidate(**_cand_kwargs(**overrides))


def _profile(*, name="proj-a", parity=True, quality=True, learning=True):
    prof = {"name": name, "admission": True, "language": "python"}
    if learning:
        prof["learning_memory"] = {"enabled": True, "parity_passed": parity, "quality_passed": quality}
    return prof


def _set_flags(shadow: bool, injection: bool, monkeypatch):
    if shadow:
        monkeypatch.setenv("PA_LEARNING_SHADOW", "1")
    else:
        monkeypatch.delenv("PA_LEARNING_SHADOW", raising=False)
    if injection:
        monkeypatch.setenv("PA_LEARNING_INJECTION", "1")
    else:
        monkeypatch.delenv("PA_LEARNING_INJECTION", raising=False)


# ════════════════════════════════════════════════════════════════════════
# 升级 A：injection 四重 gate
# ════════════════════════════════════════════════════════════════════════
class TestFourGateInjection:
    """spec task 1.3b：injection gated on shadow + parity + quality + allowlist。"""

    def test_all_gates_pass_injection_on(self, monkeypatch):
        _set_flags(shadow=True, injection=True, monkeypatch=monkeypatch)
        s, i, d = RD._resolve_learning_enabled(_profile(parity=True, quality=True))
        assert (s, i, d) == (True, True, None)

    def test_shadow_off_injection_on_degraded_not_gated(self, monkeypatch):
        """invalid 组合（injection=on, shadow=off）→ injection_not_gated 降级。"""
        _set_flags(shadow=False, injection=True, monkeypatch=monkeypatch)
        s, i, d = RD._resolve_learning_enabled(_profile())
        assert (s, i, d) == (False, False, "injection_not_gated")

    def test_parity_not_passed_degraded(self, monkeypatch):
        """parity_passed=False → injection_parity_failed 降级（绝不假阳 injection=on）。"""
        _set_flags(shadow=True, injection=True, monkeypatch=monkeypatch)
        s, i, d = RD._resolve_learning_enabled(_profile(parity=False, quality=True))
        assert (s, i, d) == (True, False, "injection_parity_failed")

    def test_quality_not_passed_degraded(self, monkeypatch):
        """quality_passed=False → injection_quality_failed 降级。"""
        _set_flags(shadow=True, injection=True, monkeypatch=monkeypatch)
        s, i, d = RD._resolve_learning_enabled(_profile(parity=True, quality=False))
        assert (s, i, d) == (True, False, "injection_quality_failed")

    def test_injection_flag_off_no_degraded(self, monkeypatch):
        """injection flag off（shadow 仍 on）→ 非 degraded（normal "off" 状态）。"""
        _set_flags(shadow=True, injection=False, monkeypatch=monkeypatch)
        s, i, d = RD._resolve_learning_enabled(_profile(parity=False, quality=False))
        # injection flag off → 短路，不调 gate resolver；shadow 仍可独立开
        assert (s, i, d) == (True, False, None)

    def test_default_no_evidence_flow_is_fail_safe(self, monkeypatch):
        """profile 无 parity/quality_passed 字段（V1 无 evidence 流）→ fail-safe injection off。"""
        _set_flags(shadow=True, injection=True, monkeypatch=monkeypatch)
        prof = {"name": "proj-a", "learning_memory": {"enabled": True}}  # 无 parity/quality
        s, i, d = RD._resolve_learning_enabled(prof)
        assert (s, i, d) == (True, False, "injection_parity_failed")


# ════════════════════════════════════════════════════════════════════════
# 升级 B：usage outcome 接线（Section 6 闭环）
# ════════════════════════════════════════════════════════════════════════
class TestUsageOutcomeWiring:
    """spec task 6.1/6.2 接线：detect→classify→build→append；unknown 红线跳过。"""

    def _envelope_with_test_log(self, test_log: str, verdict: str = "pass",
                                skip_reason: str = ""):
        """造一个最小 envelope（envelope schema 在 test_learning_memory_envelope 单测；此处只喂 effectiveness）。

        ``skip_reason`` 喂 ``_detect_failure_recurred``（failure_class 子串命中 → failure_recurred=T）。
        """
        class _FakeEnv:
            sanitized_metadata = {"terminal_reason": skip_reason}
            verifier_events = ({"event_type": "verifier_feedback", "verdict": verdict},)
            evidence_excerpts = ({"kind": "test_output", "content": test_log.encode("utf-8")},) if test_log else ()
        return _FakeEnv()

    def test_unknown_outcome_not_persisted(self, tmp_path, monkeypatch):
        """红线（Section 6 偏差 #3）：unknown outcome 不 build/append（UsageOutcome.__post_init__ 拒绝）。"""
        monkeypatch.setattr(RD, "STATE_DIR", tmp_path)
        # 构造一个 catalog entry + 一个匹配的 selected_id；但 terminal_evidence 全空 → evidence_available=False
        # → classify_outcome 返 unknown → 不持久化
        _seed_active_lesson_catalog(tmp_path, "proj-a", lesson_id="lesson_x",
                                    corrective_action="add test for X",
                                    failure_class_value=LM.FailureClass.GATE_BLOCKED.value)
        env = self._envelope_with_test_log(test_log="", verdict="")  # 无任何证据
        recorded = RD._record_usage_outcomes(
            state_dir=str(tmp_path), project_id="proj-a", run_id="r1", prd_id="p1",
            selected_ids=("lesson_x",), envelope=env, timestamp="2026-07-26T00:00:00Z")
        assert recorded == []
        # usage file 不存在或为空
        usage_path = tmp_path / "lessons" / "usage" / "proj-a.jsonl"
        assert not usage_path.exists() or usage_path.read_text(encoding="utf-8").strip() == ""

    def test_followed_outcome_appended(self, tmp_path, monkeypatch):
        """action 观察到 + failure 未复现 → followed → 持久化（catalog 后续 _apply_usage_outcomes +0.1）。"""
        monkeypatch.setattr(RD, "STATE_DIR", tmp_path)
        # corrective_action="add python test for invariant X" → token 命中 test_log
        _seed_active_lesson_catalog(tmp_path, "proj-a", lesson_id="lesson_followed",
                                    corrective_action="python invariant test pattern",
                                    failure_class_value=LM.FailureClass.VERIFIER_INVARIANT_VIOLATION.value)
        env = self._envelope_with_test_log(test_log="ok green python invariant test pattern ran", verdict="pass")
        recorded = RD._record_usage_outcomes(
            state_dir=str(tmp_path), project_id="proj-a", run_id="r1", prd_id="p1",
            selected_ids=("lesson_followed",), envelope=env, timestamp="2026-07-26T00:00:00Z")
        assert len(recorded) == 1
        assert recorded[0][0] == "lesson_followed"
        assert recorded[0][1] == "followed"
        # 持久化到 usage/<project>.jsonl（record shape: {schema_version, kind:"usage", run_id, usage:{...}}）
        usage_path = tmp_path / "lessons" / "usage" / "proj-a.jsonl"
        assert usage_path.exists()
        line = json.loads(usage_path.read_text(encoding="utf-8").strip().splitlines()[-1])
        assert line["usage"]["outcome"] == "followed"
        assert line["usage"]["lesson_id"] == "lesson_followed"

    def test_contradicted_outcome_appended(self, tmp_path, monkeypatch):
        """action 观察到 + failure 复现 → contradicted（后续 catalog -0.2 confidence）。"""
        monkeypatch.setattr(RD, "STATE_DIR", tmp_path)
        _seed_active_lesson_catalog(tmp_path, "proj-a", lesson_id="lesson_contra",
                                    corrective_action="add python test for invariant",
                                    failure_class_value=LM.FailureClass.VERIFIER_INVARIANT_VIOLATION.value)
        # failure_class token 在 skip_reason（"verifier_invariant_violation" 子串命中 → failure=T）；
        # corrective_action token 命中 test_log → action=T → contradicted（failure 优先于 prevention）
        env = self._envelope_with_test_log(
            test_log="FAIL python test invariant add test",
            verdict="revise",
            skip_reason="recurred: verifier_invariant_violation")
        recorded = RD._record_usage_outcomes(
            state_dir=str(tmp_path), project_id="proj-a", run_id="r1", prd_id="p1",
            selected_ids=("lesson_contra",), envelope=env, timestamp="2026-07-26T00:00:00Z")
        assert recorded == [("lesson_contra", "contradicted")]

    def test_missing_lesson_skipped_fail_open(self, tmp_path, monkeypatch):
        """lesson_id 不在 catalog（已 retire / 跨 project）→ 跳过该 lesson（不崩）。"""
        monkeypatch.setattr(RD, "STATE_DIR", tmp_path)
        _seed_active_lesson_catalog(tmp_path, "proj-a", lesson_id="lesson_real",
                                    corrective_action="add python test pattern",
                                    failure_class_value="gate_blocked")
        env = self._envelope_with_test_log(test_log="ok green python test pattern", verdict="pass")
        recorded = RD._record_usage_outcomes(
            state_dir=str(tmp_path), project_id="proj-a", run_id="r1", prd_id="p1",
            selected_ids=("lesson_real", "lesson_missing"), envelope=env,
            timestamp="2026-07-26T00:00:00Z")
        # lesson_real 评估（followed）；lesson_missing 跳过（不在 catalog）
        assert any(lid == "lesson_real" for lid, _ in recorded)
        assert not any(lid == "lesson_missing" for lid, _ in recorded)

    def test_catalog_unreachable_returns_empty(self, tmp_path, monkeypatch):
        """catalog 不存在 → fail-open 返空（不改 terminal outcome）。"""
        monkeypatch.setattr(RD, "STATE_DIR", tmp_path)
        env = self._envelope_with_test_log(test_log="ok", verdict="pass")
        recorded = RD._record_usage_outcomes(
            state_dir=str(tmp_path), project_id="proj-a", run_id="r1", prd_id="p1",
            selected_ids=("lesson_x",), envelope=env, timestamp="2026-07-26T00:00:00Z")
        assert recorded == []

    def test_empty_selected_ids_returns_empty(self, tmp_path, monkeypatch):
        """无注入（selected_ids=()）→ 零 usage outcome 写入（design「injection off → 零评估」）。"""
        monkeypatch.setattr(RD, "STATE_DIR", tmp_path)
        env = self._envelope_with_test_log(test_log="ok", verdict="pass")
        recorded = RD._record_usage_outcomes(
            state_dir=str(tmp_path), project_id="proj-a", run_id="r1", prd_id="p1",
            selected_ids=(), envelope=env, timestamp="2026-07-26T00:00:00Z")
        assert recorded == []


def _seed_active_lesson_catalog(state_dir: Path, project_id: str, *, lesson_id: str,
                                corrective_action: str, failure_class_value: str,
                                applies_when_tags=("python",), confidence: float = 0.7,
                                verified_support_count: int = 2):
    """写 catalog：1 个 active lesson（直接 projection；不经过 promotion policy）。"""
    cat_path = state_dir / "lessons" / "catalog" / f"{project_id}.json"
    cat_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "lesson_id": lesson_id, "project_id": project_id, "state": "active",
        "confidence": confidence, "trigger": f"trigger for {lesson_id}",
        "corrective_action": corrective_action, "failure_class": failure_class_value,
        "non_applicability_when": "skip when stage=post_terminal",
        "applies_when_tags": list(applies_when_tags),
        "verified_support_count": verified_support_count,
        "effectiveness_history": [], "last_outcome_ts": "",
        "source_candidate_ids": [], "equivalence_key": f"ek_{lesson_id}",
        "supporting_prd_ids": ["prd_a", "prd_b"],
    }
    cat_path.write_text(json.dumps({"schema_version": 1, "project_id": project_id,
                                    "entries": [entry], "rebuild_token": "t1"}), encoding="utf-8")


# ════════════════════════════════════════════════════════════════════════
# task 7.2 crash-recovery：append-only 幂等 replay + no duplicate promotion
# ════════════════════════════════════════════════════════════════════════
class TestCrashRecovery:
    """spec task 7.2：crash 在 candidate append 前/后 + catalog replacement 前/后 → 幂等 replay。"""

    def test_replay_after_clean_state_rebuilds_identical_catalog(self, tmp_path):
        """clean candidates+events+usage → replay 两次得 byte-identical catalog（幂等）。"""
        state = tmp_path / "state"
        # seed 2 个等价 candidates（同 equivalence_key，不同 prd_id）
        c1 = _candidate(prd_id="prd-001", evidence_refs=({"digest": "sha256:a1", "kind": "test_output", "path": "p"},))
        c2 = _candidate(prd_id="prd-002", evidence_refs=({"digest": "sha256:a2", "kind": "test_output", "path": "p"},))
        LMS.append_candidate(str(state), "proj-a", c1, run_id="r1", timestamp="2026-07-26T00:00:00Z")
        LMS.append_candidate(str(state), "proj-a", c2, run_id="r2", timestamp="2026-07-26T01:00:00Z")
        # replay 两次（同一 state_dir + project_id）→ 幂等（dataclasses.asdict 后字节相同）
        snap1 = LMCat._replay(str(state), "proj-a")
        snap2 = LMCat._replay(str(state), "proj-a")
        assert snap1 is not None and snap2 is not None
        d1 = dataclasses.asdict(snap1)
        d2 = dataclasses.asdict(snap2)
        assert json.dumps(d1, sort_keys=True, default=str) == json.dumps(d2, sort_keys=True, default=str)

    def test_replay_promotes_only_when_two_distinct_prds(self, tmp_path):
        """2 个等价 candidate 来自不同 PRD → promote；replay 不重复 promote。"""
        state = tmp_path / "state"
        c1 = _candidate(prd_id="prd-001", evidence_refs=({"digest": "sha256:a1", "kind": "test_output", "path": "p"},))
        c2 = _candidate(prd_id="prd-002", evidence_refs=({"digest": "sha256:a2", "kind": "test_output", "path": "p"},))
        LMS.append_candidate(str(state), "proj-a", c1, run_id="r1", timestamp="2026-07-26T00:00:00Z")
        LMS.append_candidate(str(state), "proj-a", c2, run_id="r2", timestamp="2026-07-26T01:00:00Z")
        snap = LMCat._replay(str(state), "proj-a")
        active = [e for e in snap.entries if isinstance(e, dict) and e.get("state") == "active"]
        assert len(active) >= 1   # 至少 1 个 active（cross-PRD 满足 promotion）
        # replay 不重复 promote：再 replay 一次仍是同样数量 active
        snap2 = LMCat._replay(str(state), "proj-a")
        active2 = [e for e in snap2.entries if isinstance(e, dict) and e.get("state") == "active"]
        assert len(active2) == len(active)

    def test_catalog_replacement_crash_keeps_old_catalog(self, tmp_path):
        """catalog replacement 中途崩溃 → rebuild_catalog fail-closed（旧 catalog 未被覆盖）。

        反例构造「中部损坏」：必须前后各有一条合法 record——若 malformed 是末行会被当 tail_truncated
        容忍（spec：崩溃只截断最后一条 append）。本测试在两条合法 candidate 中间塞一行 malformed。
        """
        state = tmp_path / "state"
        # 先写一个合法 catalog projection（旧版本，应被保留）
        _seed_active_lesson_catalog(state, "proj-a", lesson_id="l_old",
                                    corrective_action="old action",
                                    failure_class_value="gate_blocked")
        old_catalog_text = (state / "lessons" / "catalog" / "proj-a.json").read_text(encoding="utf-8")
        # 写两条合法 candidate，中间塞一行 malformed（中部损坏，非末尾截断）
        cand_path = state / "lessons" / "candidates" / "proj-a.jsonl"
        cand_path.parent.mkdir(parents=True, exist_ok=True)
        c1 = _candidate(prd_id="prd-001", evidence_refs=({"digest": "sha256:a1", "kind": "test_output", "path": "p"},))
        LMS.append_candidate(str(state), "proj-a", c1, run_id="r1", timestamp="2026-07-26T00:00:00Z")
        c2 = _candidate(prd_id="prd-002", evidence_refs=({"digest": "sha256:a2", "kind": "test_output", "path": "p"},))
        LMS.append_candidate(str(state), "proj-a", c2, run_id="r2", timestamp="2026-07-26T01:00:00Z")
        lines_after = cand_path.read_text(encoding="utf-8").splitlines()
        # 在两条合法中间塞一行 malformed
        with open(cand_path, "w", encoding="utf-8") as f:
            f.write(lines_after[0] + "\n")            # 第一条合法
            f.write("{not valid json middle corruption}\n")   # 中部 malformed
            for extra in lines_after[1:]:
                f.write(extra + "\n")                 # 后续合法
        result = LMCat.rebuild_catalog(str(state), "proj-a")
        # 中部损坏 → fail-closed（绝不部分信任）；旧 catalog 文件未被覆盖
        assert result.ok is False
        new_catalog_text = (state / "lessons" / "catalog" / "proj-a.json").read_text(encoding="utf-8")
        assert new_catalog_text == old_catalog_text

    def test_trailing_truncated_record_tolerated(self, tmp_path):
        """候选文件末尾半行（崩溃截断最后一条 append）→ tail_truncated 容忍，前面合法 records 仍 replay。"""
        state = tmp_path / "state"
        c1 = _candidate(prd_id="prd-001", evidence_refs=({"digest": "sha256:a1", "kind": "test_output", "path": "p"},))
        LMS.append_candidate(str(state), "proj-a", c1, run_id="r1", timestamp="2026-07-26T00:00:00Z")
        # 追加半行（崩溃截断）
        cand_path = state / "lessons" / "candidates" / "proj-a.jsonl"
        with open(cand_path, "a", encoding="utf-8") as f:
            f.write('{"schema_version":1,"candidate":')   # 截断的 JSON
        # _scan 容忍末尾截断；replay 应成功（前面的合法 record 仍归约）
        snap = LMCat._replay(str(state), "proj-a")
        assert snap is not None


# ════════════════════════════════════════════════════════════════════════
# task 7.3 正向 canary（端到端 V1 演进）
# ════════════════════════════════════════════════════════════════════════
class TestPositiveCanary:
    """spec task 7.3：2 PRD → promotion → 第 3 PRD relevant injection → effectiveness feedback。"""

    def test_full_canary_evolution(self, tmp_path):
        """端到端 V1 演进（直接驱动 store/catalog/retrieval/effectiveness 模拟 3 PRD）。"""
        state = tmp_path / "state"

        # ── PRD 1：terminal → 1 个 evidence-backed candidate（无 promotion：仅 1 PRD）──
        c1 = _candidate(prd_id="prd-001",
                        evidence_refs=({"digest": "sha256:a1", "kind": "test_output", "path": "p"},))
        LMS.append_candidate(str(state), "proj-a", c1,
                             run_id="r1", timestamp="2026-07-26T00:00:00Z")
        snap1 = LMCat._replay(str(state), "proj-a")
        active1 = [e for e in snap1.entries if isinstance(e, dict) and e.get("state") == "active"]
        assert len(active1) == 0, "1 PRD 不应 promote（spec Section 3.1：需 ≥2 distinct PRD）"

        # ── PRD 2：等价 candidate（同 equivalence_key 来自同 schema 字段，不同 prd_id）→ promotion ──
        c2 = _candidate(prd_id="prd-002",
                        evidence_refs=({"digest": "sha256:a2", "kind": "test_output", "path": "p"},))
        LMS.append_candidate(str(state), "proj-a", c2,
                             run_id="r2", timestamp="2026-07-26T01:00:00Z")
        snap2 = LMCat._replay(str(state), "proj-a")
        active2 = [e for e in snap2.entries if isinstance(e, dict) and e.get("state") == "active"]
        assert len(active2) >= 1, "2 distinct PRD 等价 candidate → 至少 1 promote"

        # 把 promoted catalog 落盘（production：rebuild_catalog；这里直接写 projection）
        LMCat.rebuild_catalog(str(state), "proj-a")

        # ── PRD 3：dispatch-entry → retrieve 命中 promoted active lesson → inject ──
        source = LMRet.load_catalog_for_retrieval(str(state), "proj-a")
        assert source.degraded_class is None
        assert len(source.entries) >= 1
        tm = LMRet.derive_task_metadata(
            project_profile={"language": "python"},
            prd={"acceptance_criteria": ["add python test"]},
            project_id="proj-a")
        result = LMRet.retrieve_from_source(source, tm)
        assert len(result.selected_lesson_ids) >= 1, "promoted active lesson 应被 retrieve 命中"
        block = LMRet.render_lesson_block(result.selected)
        assert "Applicable lessons from prior PRDs" in block
        for lid in result.selected_lesson_ids:
            assert f"**{lid}**" in block

        # ── PRD 3 terminal → effectiveness feedback（followed → 持久化 usage outcome）──
        lesson_entry = result.selected[0]
        terminal_ev = {
            "verifier_verdict": "pass",
            "test_log": "ok green " + lesson_entry.get("corrective_action", ""),
            "skip_reason": "",
        }
        action, failure, evidence_avail = LMEff.detect_action_observed(lesson_entry, terminal_ev)
        outcome = LMEff.classify_outcome(
            action_observed=action, failure_recurred=failure, evidence_available=evidence_avail)
        # action token 命中 → followed（不 unknown）
        assert outcome == "followed", f"expected followed, got {outcome}"
        if outcome != "unknown":   # 红线 guard（虽然此处不应触发）
            usage = LMEff.build_usage_outcome(
                event_id="usage_r3", timestamp="2026-07-26T02:00:00Z",
                project_id="proj-a", lesson_id=lesson_entry["lesson_id"], prd_id="prd-003",
                action_observed=action, failure_recurred=failure, outcome=outcome)
            LMS.append_usage_outcome(str(state), "proj-a", usage, run_id="r3")
        # usage 已持久化（record shape: {schema_version, kind:"usage", run_id, usage:{outcome}})
        usage_records = LMS.read_usage_records(str(state), "proj-a")
        assert any(r.get("usage", {}).get("outcome") == "followed" for r in usage_records)


# ════════════════════════════════════════════════════════════════════════
# task 7.4 负向 canaries（6 反例）
# ════════════════════════════════════════════════════════════════════════
class TestNegativeCanaries:
    """spec task 7.4：6 个反例证明 V1 安全不变量（不 promote / 不 inject / 不污染 catalog）。"""

    def test_canary_1_prd_repeated_no_promotion(self, tmp_path):
        """反例 1：同一 PRD 多 iteration → 只算 1 distinct PRD → 不 promote（Section 3.1）。"""
        state = tmp_path / "state"
        # 同 PRD 2 个 candidate（不同 iteration_refs，但 prd_id 同）
        c1 = _candidate(prd_id="prd-001", iteration_refs=("iter-1",),
                        evidence_refs=({"digest": "sha256:a1", "kind": "test_output", "path": "p"},))
        c2 = _candidate(prd_id="prd-001", iteration_refs=("iter-2",),
                        evidence_refs=({"digest": "sha256:a2", "kind": "test_output", "path": "p"},))
        LMS.append_candidate(str(state), "proj-a", c1, run_id="r1", timestamp="2026-07-26T00:00:00Z")
        LMS.append_candidate(str(state), "proj-a", c2, run_id="r1", timestamp="2026-07-26T00:30:00Z")
        snap = LMCat._replay(str(state), "proj-a")
        entries = snap.entries if hasattr(snap, "entries") else snap.get("entries", [])
        active = [e for e in entries if isinstance(e, dict) and e.get("state") == "active"]
        assert len(active) == 0, "同 PRD 重复 → 不 promote（distinct PRD 计数 = 1）"

    def test_canary_corrupt_evidence_no_promotion(self, tmp_path):
        """反例 2：candidate evidence_refs 缺 digest / 损坏 → schema reject（append 时即拒）。"""
        state = tmp_path / "state"
        # evidence_refs 缺 digest → schema 校验在 append_candidate 阶段 raise
        bad = _candidate(evidence_refs=({"kind": "test_output"},))   # 无 digest
        try:
            LMS.append_candidate(str(state), "proj-a", bad,
                                 run_id="r1", timestamp="2026-07-26T00:00:00Z")
            assert False, "schema reject 应抛"
        except (ValueError, TypeError):
            pass
        # candidate 未持久化
        records = LMS.read_candidate_records(str(state), "proj-a")
        assert len(records) == 0

    def test_canary_reflection_outage_degraded_no_terminal_mutation(self, tmp_path, monkeypatch):
        """反例 3：SDK timeout/error/invalid_json → degraded，不改 terminal outcome，不写 candidate。"""
        monkeypatch.setattr(RD, "STATE_DIR", tmp_path)
        monkeypatch.setenv("PA_LEARNING_SHADOW", "1")
        monkeypatch.delenv("PA_LEARNING_INJECTION", raising=False)
        # seed journal（published verifier pass）
        jpath = tmp_path / "runs" / "proj-a" / "20260726-0000_test-prd.journal.jsonl"
        jpath.parent.mkdir(parents=True, exist_ok=True)
        from loop_state import JournalEvent
        import journal as J
        J.append_event(jpath, JournalEvent(
            schema_version=1, event_id="e1", timestamp="2026-07-26T00:00:00Z",
            iteration_id="i1", run_id="r1", prd_id="p1",
            event_type="verifier_feedback", payload={"verdict": "pass"}))
        rec = {"status": "published", "verify": {"pass": True}, "pr_url": "https://x"}

        def _timeout_sdk(p, o):
            raise TimeoutError("simulated SDK timeout")

        RD._attach_learning_memory(rec, _profile(learning=True), {"prd_path": "state/prd/proj-a/test-prd.md"},
                                   stamp="20260726-0000", sdk_query_fn=_timeout_sdk)
        # degraded 但 terminal outcome 字节不变
        assert rec["learning_memory"]["reflection"] == "degraded"
        assert rec["learning_memory"]["class"] == "timeout"
        assert rec["status"] == "published"
        assert rec["verify"] == {"pass": True}
        # candidate 文件未写（fail-closed for memory）
        cand_path = tmp_path / "lessons" / "candidates" / "proj-a.jsonl"
        assert not cand_path.exists() or cand_path.read_text(encoding="utf-8").strip() == ""

    def test_canary_malformed_catalog_retrieval_degraded(self, tmp_path, monkeypatch):
        """反例 4：catalog 中间 record 损坏 → retrieval fail-open（degraded_class + 空 entries）。"""
        monkeypatch.setattr(RD, "STATE_DIR", tmp_path)
        # 写一份 corrupted catalog JSON（entries 非 list）
        cat_path = tmp_path / "lessons" / "catalog" / "proj-a.json"
        cat_path.parent.mkdir(parents=True, exist_ok=True)
        cat_path.write_text('{"schema_version": 1, "entries": "not_a_list"}', encoding="utf-8")
        source = LMRet.load_catalog_for_retrieval(str(tmp_path), "proj-a")
        # entries 非 list → degraded_class=catalog_read_error（绝不部分信任 corrupted catalog）
        assert source.degraded_class == "catalog_read_error"
        assert source.entries == ()
        # retrieval 经 retrieve_from_source 透传 degraded（不 inject）
        result = LMRet.retrieve_from_source(source, {"project_id": "proj-a", "tags": {"python"}})
        assert result.selected_lesson_ids == ()
        assert result.degraded_class == "catalog_read_error"

    def test_canary_irrelevant_lesson_not_injected(self, tmp_path, monkeypatch):
        """反例 5：promoted lesson 的 applies_when_tags 与 task 不 overlap → retrieve 过滤掉。"""
        monkeypatch.setattr(RD, "STATE_DIR", tmp_path)
        _seed_active_lesson_catalog(tmp_path, "proj-a", lesson_id="lesson_python",
                                    corrective_action="python-specific action",
                                    failure_class_value="gate_blocked",
                                    applies_when_tags=("python",))
        # task_metadata 是 typescript（不 overlap）→ retrieve 过滤
        source = LMRet.load_catalog_for_retrieval(str(tmp_path), "proj-a")
        tm = LMRet.derive_task_metadata(
            project_profile={"language": "typescript"},
            prd={}, project_id="proj-a")
        result = LMRet.retrieve_from_source(source, tm)
        assert result.selected_lesson_ids == (), "irrelevant lesson 不应被 inject"

    def test_canary_contradicted_lesson_retired_not_retrieved(self, tmp_path, monkeypatch):
        """反例 6：2 次 contradicted → state=retired；retired 不被 retrieve（即使 applicability 匹配）。"""
        monkeypatch.setattr(RD, "STATE_DIR", tmp_path)
        # 直接写一个 retired lesson 到 catalog projection（绕过 2 次 contradicted 的真实流程——
        # retire 流程在 catalog 单测已验；本测只确认 retrieval 排除 retired）
        cat_path = tmp_path / "lessons" / "catalog" / "proj-a.json"
        cat_path.parent.mkdir(parents=True, exist_ok=True)
        entries = [{
            "lesson_id": "lesson_retired", "project_id": "proj-a", "state": "retired",
            "confidence": 0.0, "trigger": "t", "corrective_action": "a",
            "failure_class": "verifier_invariant_violation",
            "non_applicability_when": "", "applies_when_tags": ["python"],
            "verified_support_count": 2, "effectiveness_history": [
                {"outcome": "contradicted"}, {"outcome": "contradicted"}],
            "last_outcome_ts": "", "source_candidate_ids": [],
            "equivalence_key": "ek_r", "supporting_prd_ids": ["prd_a", "prd_b"],
        }]
        cat_path.write_text(json.dumps({"schema_version": 1, "project_id": "proj-a",
                                        "entries": entries, "rebuild_token": "t"}), encoding="utf-8")
        source = LMRet.load_catalog_for_retrieval(str(tmp_path), "proj-a")
        tm = LMRet.derive_task_metadata(
            project_profile={"language": "python"},
            prd={"acceptance_criteria": ["python test"]},
            project_id="proj-a")
        result = LMRet.retrieve_from_source(source, tm)
        # retired lesson 被 filter 掉（design 决策#7「retired excluded from retrieval」）
        assert "lesson_retired" not in result.selected_lesson_ids
        assert result.selected_lesson_ids == ()
