#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_learning_memory_wiring.py — add-cross-prd-learning-memory Section 7.1 / 7.7 接线测试。

锁定 Section 7 三接线点 + spec tasks 7.1（shadow parity）+ 7.7（两级 rollback）契约：

    * **接线点 1（terminal reflection hook）**：dispatch 出口 → envelope + read-only SDK →
      rec["learning_memory"]。fail-open：timeout/sdk_error/invalid_json/schema_reject/persist_failure/
      evidence_mismatch → rec["learning_memory"]={reflection:"degraded"}，**不改 terminal outcome**。
    * **接线点 2（injection）**：dispatch-entry → catalog retrieval → lesson block → --lessons-artifact
      透传 dev-agent。fail-open：catalog 故障 / retrieval 空 → 不传 artifact（baseline prompt）。
    * **接线点 3（memory_mode report）**：per-record 合并 memory_mode 子字段（不改 status/success 语义）。

核心反例（spec task 7.1 shadow parity）：
    shadow on+ok reflection / shadow on+degraded reflection / shadow off 零调用——三种都断言 terminal
    outcome（status / verify / publish）byte-identical 不变（design 决策#7 fail-open by construction）。

核心反例（spec task 7.7 两级 rollback）：
    * level 1（disable injection, shadow on）：dev prompt 无 lesson block（byte-identical baseline），
      selected_lesson_ids=[]；reflection 仍跑（candidate generation 继续）。
    * level 2（disable shadow, both off）：reflection 完全不调（sdk_query_fn 调用计数=0）；catalog 重建
      可从 candidates+events+usage 还原（append-only，inert）。

mock-SDK / mock subprocess（cron 隔离 + 速度）：
    * reflection 测试注入 sdk_query_fn 桩（返固定 JSON）——**绝不**调真 SDK。
    * injection 测试桩 _run_dev_agent/dispatch_one 外层，只验证 lessons_artifact 透传 + lesson_block 内容
      + selected_lesson_ids 记录；不必真跑 dev-agent。

AAA 结构。跑：python3 -m pytest scripts/test_learning_memory_wiring.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import learning_memory_catalog as LMCat   # noqa: E402
import run_daily as RD                    # noqa: E402
from loop_state import JournalEvent       # noqa: E402
import journal as J                       # noqa: E402


def _seed_journal(state_dir: Path, proj: str, stamp: str, slug: str,
                  *, events: list[dict]) -> Path:
    """写一份合法 journal（每行带 JournalEvent 必填字段：schema_version/event_id/timestamp/iter/run/prd/type/payload）。

    dispatch_one 内 ShadowJournal.emit 已保证 schema；envelope 测试直接构造同样合法 shape。
    """
    jpath = state_dir / "runs" / proj / f"{stamp}_{slug}.journal.jsonl"
    jpath.parent.mkdir(parents=True, exist_ok=True)
    iter_id = "i1"
    run_id = stamp
    prd_id = "p1"
    for i, ev_spec in enumerate(events):
        je = JournalEvent(
            schema_version=1, event_id=f"e{i}", timestamp="2026-07-26T00:00:00Z",
            iteration_id=iter_id, run_id=run_id, prd_id=prd_id,
            event_type=ev_spec["event_type"],
            payload=ev_spec.get("payload", {}))
        J.append_event(jpath, je)
    return jpath


# ════════════════════════════════════════════════════════════════════════
# fixture helpers
# ════════════════════════════════════════════════════════════════════════
def _profile(*, learning_enabled: bool = True, name: str = "proj-a",
             parity_passed: bool = True, quality_passed: bool = True, **extra) -> dict:
    """造一个项目 profile（learning_memory.enabled 标记控制 V1 allowlist）。

    批次 2 升级 A：``parity_passed`` + ``quality_passed`` 是 injection 四重 gate 的 V1 evidence 信号
    （default True 便于多数 canary 测试；gate 失败的反例测试可显式传 False）。
    """
    prof = {"name": name, "admission": True, "dev_agent_ready": True, "type": "code",
            "repo": "/tmp/fake-repo", "default_branch": "main"}
    if learning_enabled:
        prof["learning_memory"] = {"enabled": True,
                                   "parity_passed": parity_passed,
                                   "quality_passed": quality_passed}
    prof.update(extra)
    return prof


def _entry(prd_path: str = "state/prd/proj-a/test-prd.md") -> dict:
    return {"project": "proj-a", "prd_path": prd_path, "source_path": ""}


def _set_flags(shadow: bool, injection: bool, monkeypatch) -> None:
    """环境变量设 flag（profile.loop 优先级的 fallback；测试用 env 直配简单）。"""
    if shadow:
        monkeypatch.setenv("PA_LEARNING_SHADOW", "1")
    else:
        monkeypatch.delenv("PA_LEARNING_SHADOW", raising=False)
    if injection:
        monkeypatch.setenv("PA_LEARNING_INJECTION", "1")
    else:
        monkeypatch.delenv("PA_LEARNING_INJECTION", raising=False)


def _seed_catalog(state_dir: Path, project_id: str, lessons: int = 2) -> None:
    """seed catalog：写 N 个 active lesson entry（promoted，applies_when_tags=["python"]）。"""
    # 用 store.append_candidate + 直接 catalog projection（绕过 cross-PRD promotion 阈值——接线测试不验促销）
    cat_path = state_dir / "lessons" / "catalog" / f"{project_id}.json"
    cat_path.parent.mkdir(parents=True, exist_ok=True)
    entries = []
    for i in range(lessons):
        entries.append({
            "lesson_id": f"lesson_{i}",
            "project_id": project_id,
            "state": "active",
            "confidence": 0.7,
            "trigger": f"trigger condition {i}",
            "corrective_action": f"do action {i} to prevent recurrence",
            "non_applicability_when": "skip when stage=post_terminal",
            "applies_when_tags": ["python"],
            "verified_support_count": 2,
            "effectiveness_history": [],
            "last_outcome_ts": "",
            "source_candidate_ids": [],
            "equivalence_key": f"ek{i}",
            "supporting_prd_ids": ["prd_a", "prd_b"],
        })
    cat_path.write_text(json.dumps({"schema_version": 1, "project_id": project_id,
                                    "entries": entries, "rebuild_token": "t1"}), encoding="utf-8")


# ════════════════════════════════════════════════════════════════════════
# 接线点 1：terminal reflection hook（fail-open by construction）
# ════════════════════════════════════════════════════════════════════════
class TestTerminalReflectionWiring:
    """spec task 7.1 shadow parity + task 4.5 fail-open：reflection 是 read-only 副作用，不渗入决策路径。"""

    def test_shadow_off_zero_side_effects(self, tmp_path, monkeypatch):
        """shadow=off → 不构造 envelope、不调 SDK、零 reflection 副作用（design 决策#8）。"""
        _set_flags(shadow=False, injection=False, monkeypatch=monkeypatch)
        monkeypatch.setattr(RD, "STATE_DIR", tmp_path)
        rec = {"status": "published", "verify": {"pass": True}, "pr_url": "https://x"}
        sdk_calls = []

        def _sdk(prompt, options):
            sdk_calls.append((prompt, options))
            return "{}"

        RD._attach_learning_memory(rec, _profile(learning_enabled=True), _entry(),
                                   stamp="20260726-0000", sdk_query_fn=_sdk)
        # shadow off → SDK 零调用
        assert sdk_calls == [], f"shadow=off 仍调 SDK: {sdk_calls}"
        # memory_mode 字段附加（不改 status / verify / pr_url）
        assert rec["status"] == "published"
        assert rec["verify"] == {"pass": True}
        assert rec["pr_url"] == "https://x"
        mm = rec["memory_mode"]
        assert mm == {"shadow_on": False, "injection_on": False, "selected_lesson_ids": (),
                      "candidate_count": 0, "promotion_count": 0, "degraded_status": None}
        # 无 learning_memory 子字段（shadow off 完全不构造 envelope）
        assert "learning_memory" not in rec

    def test_shadow_on_ok_reflection_attaches_and_preserves_outcome(self, tmp_path, monkeypatch):
        """shadow=on + SDK 返 valid JSON → rec["learning_memory"]={reflection:"ok"} + terminal outcome 不变。"""
        _set_flags(shadow=True, injection=False, monkeypatch=monkeypatch)
        monkeypatch.setattr(RD, "STATE_DIR", tmp_path)

        # seed artifact store（verifier pass evidence excerpt）—— 先 store 拿真实 digest，再写 journal
        import artifact_store
        ref = artifact_store.store(str(tmp_path / "artifacts" / "r1"), "OK green",
                                   kind="test_output", sensitivity="sanitized")
        # seed journal：verifier_feedback{verdict:pass, artifact_ref.digest=store 返的真实 digest}
        # → envelope _extract_artifact_refs 收 + _has_verifier_pass → evidence_class=VERIFIER_PASS
        _seed_journal(tmp_path, "proj-a", "20260726-0000", "test-prd",
                      events=[{"event_type": "verifier_feedback",
                               "payload": {"verdict": "pass", "round": 1,
                                           "artifact_ref": {"digest": ref.digest, "kind": "test_output",
                                                            "path": ref.path, "size": ref.size,
                                                            "sensitivity": "sanitized"}}}])

        # SDK 桩返 valid JSON（1 candidate）
        def _valid_cand():
            return {
                "phase": "verify", "failure_class": "verifier_invariant_violation",
                "corrective_action_class": "add_test",
                "applies_when_tags": ["python"],
                "corrective_action": "add test that reproduces the invariant violation",
                "pattern_description": "audit only: verifier caught invariant violation on edge case",
                "applicability_when": "verifier gate is active",
                "non_applicability_when": "no verifier",
                "evidence_refs": [{"digest": ref.digest, "kind": "test_output"}],
                "source_outcome": "published", "confidence": 0.8,
            }

        def _sdk(prompt, options):
            return json.dumps({"candidates": [_valid_cand()], "audit_summary": "ok"})

        rec = {"status": "published", "verify": {"pass": True}, "pr_url": "https://x",
               "_learning_selected_ids": ()}
        RD._attach_learning_memory(rec, _profile(learning_enabled=True), _entry(),
                                   stamp="20260726-0000", sdk_query_fn=_sdk)
        # reflection ok → learning_memory 子字段
        lm_rec = rec["learning_memory"]
        assert lm_rec["reflection"] == "ok"
        assert lm_rec["class"] is None
        assert lm_rec["candidate_count"] == 1
        # terminal outcome 字节级不变（fail-open by construction）
        assert rec["status"] == "published"
        assert rec["verify"] == {"pass": True}
        assert rec["pr_url"] == "https://x"
        # memory_mode：shadow on，injection off（环境变量只 shadow）
        assert rec["memory_mode"]["shadow_on"] is True
        assert rec["memory_mode"]["injection_on"] is False

    def test_shadow_on_sdk_timeout_degraded_preserves_outcome(self, tmp_path, monkeypatch):
        """shadow=on + SDK timeout → rec["learning_memory"]={reflection:"degraded", class:"timeout"} + outcome 不变。"""
        _set_flags(shadow=True, injection=False, monkeypatch=monkeypatch)
        monkeypatch.setattr(RD, "STATE_DIR", tmp_path)
        # seed 一个 published 终态 journal
        _seed_journal(tmp_path, "proj-a", "20260726-0000", "test-prd",
                      events=[{"event_type": "verifier_feedback", "payload": {"verdict": "pass"}}])

        def _timeout_sdk(prompt, options):
            raise TimeoutError("simulated SDK timeout")

        rec = {"status": "published", "verify": {"pass": True}, "pr_url": "https://x/timeout"}
        RD._attach_learning_memory(rec, _profile(learning_enabled=True), _entry(),
                                   stamp="20260726-0000", sdk_query_fn=_timeout_sdk)
        # reflection degraded{class:timeout}
        assert rec["learning_memory"]["reflection"] == "degraded"
        assert rec["learning_memory"]["class"] == "timeout"
        # fail-open：terminal outcome 字节不变
        assert rec["status"] == "published"
        assert rec["verify"] == {"pass": True}
        assert rec["pr_url"] == "https://x/timeout"

    def test_shadow_on_sdk_error_degraded_preserves_outcome(self, tmp_path, monkeypatch):
        """shadow=on + SDK 抛 RuntimeError → degraded{class:sdk_error} + outcome 不变。"""
        _set_flags(shadow=True, injection=False, monkeypatch=monkeypatch)
        monkeypatch.setattr(RD, "STATE_DIR", tmp_path)
        _seed_journal(tmp_path, "proj-a", "20260726-0000", "test-prd",
                      events=[{"event_type": "verifier_feedback", "payload": {"verdict": "pass"}}])

        def _broken_sdk(prompt, options):
            raise RuntimeError("simulated SDK exception")

        rec = {"status": "failed", "skip_reason": "dev crashed"}
        RD._attach_learning_memory(rec, _profile(learning_enabled=True), _entry(),
                                   stamp="20260726-0000", sdk_query_fn=_broken_sdk)
        assert rec["learning_memory"]["reflection"] == "degraded"
        assert rec["learning_memory"]["class"] == "sdk_error"
        assert rec["status"] == "failed"
        assert rec["skip_reason"] == "dev crashed"

    def test_shadow_on_invalid_json_degraded_preserves_outcome(self, tmp_path, monkeypatch):
        """shadow=on + SDK 返非 JSON → degraded{class:invalid_json} + outcome 不变。"""
        _set_flags(shadow=True, injection=False, monkeypatch=monkeypatch)
        monkeypatch.setattr(RD, "STATE_DIR", tmp_path)
        _seed_journal(tmp_path, "proj-a", "20260726-0000", "test-prd",
                      events=[{"event_type": "verifier_feedback", "payload": {"verdict": "pass"}}])

        def _garbled_sdk(prompt, options):
            return "not json at all"

        rec = {"status": "aborted", "skip_reason": "x"}
        RD._attach_learning_memory(rec, _profile(learning_enabled=True), _entry(),
                                   stamp="20260726-0000", sdk_query_fn=_garbled_sdk)
        assert rec["learning_memory"]["reflection"] == "degraded"
        assert rec["learning_memory"]["class"] == "invalid_json"
        assert rec["status"] == "aborted"

    def test_non_allowlisted_project_zero_side_effects(self, tmp_path, monkeypatch):
        """V1 allowlist（prof.learning_memory.enabled）未启用 → 零副作用（spec「V1 project-only scope」）。"""
        _set_flags(shadow=True, injection=True, monkeypatch=monkeypatch)  # 用户误开 flag
        monkeypatch.setattr(RD, "STATE_DIR", tmp_path)
        sdk_calls = []

        rec = {"status": "published"}
        RD._attach_learning_memory(rec, _profile(learning_enabled=False), _entry(),
                                   stamp="20260726-0000",
                                   sdk_query_fn=lambda p, o: sdk_calls.append((p, o)) or "{}")
        # 非 allowlist → 整个 learning 子系统零副作用
        assert sdk_calls == []
        assert "learning_memory" not in rec
        assert rec["memory_mode"]["shadow_on"] is False
        assert rec["memory_mode"]["injection_on"] is False


# ════════════════════════════════════════════════════════════════════════
# 接线点 2：injection（dispatch-entry catalog retrieval → lesson block artifact）
# ════════════════════════════════════════════════════════════════════════
class TestInjectionWiring:
    """spec task 5 接线 + fail-open（catalog 故障 → 不传 artifact，baseline prompt）。"""

    def test_injection_off_no_artifact_no_catalog_read(self, tmp_path, monkeypatch):
        """injection=off → 完全不读 catalog、retrieve 零调用（spec design 决策#8）。"""
        monkeypatch.setattr(RD, "STATE_DIR", tmp_path)
        pkg = RD._build_lessons_pkg(_profile(learning_enabled=True), "/tmp/prd.md",
                                    project_id="proj-a", run_id="r1",
                                    timestamp="2026-07-26T00:00:00Z", injection_on=False)
        assert pkg["artifact_path"] is None
        assert pkg["selected_lesson_ids"] == ()
        assert pkg["degraded_class"] is None
        assert pkg["candidate_count"] == 0

    def test_injection_on_with_seeded_catalog_builds_artifact(self, tmp_path, monkeypatch):
        """injection=on + catalog seeded → lesson_block 写成 content-addressed artifact + selected IDs。"""
        monkeypatch.setattr(RD, "STATE_DIR", tmp_path)
        _seed_catalog(tmp_path, "proj-a", lessons=2)
        # PRD 文件（derive_task_metadata 读 acceptance_criteria）
        prd_path = tmp_path / "prd.md"
        prd_path.write_text("# PRD\n\nacceptance: add python test for invariant X\n", encoding="utf-8")

        pkg = RD._build_lessons_pkg(_profile(learning_enabled=True, language="python"),
                                    str(prd_path), project_id="proj-a", run_id="r1",
                                    timestamp="2026-07-26T00:00:00Z", injection_on=True)
        # artifact path 写出（content-addressed；sanitized；lessons_block kind）
        assert pkg["artifact_path"] is not None
        assert pkg["selected_lesson_ids"]   # 2 个 active lesson，都匹配 python tag
        # artifact 文件可读，内容是 lesson_block markdown checklist
        block_text = Path(pkg["artifact_path"]).read_text(encoding="utf-8")
        assert "## Applicable lessons from prior PRDs" in block_text
        for lid in pkg["selected_lesson_ids"]:
            assert f"**{lid}**" in block_text
        # 严格不含 evidence / 叙事（design 决策#5）
        assert "evidence_refs" not in block_text
        assert "pattern_description" not in block_text
        assert "source_candidate_ids" not in block_text

    def test_injection_on_empty_catalog_no_artifact(self, tmp_path, monkeypatch):
        """injection=on + catalog 不存在 → 不传 artifact（dev-agent baseline prompt）+ degraded_class。"""
        monkeypatch.setattr(RD, "STATE_DIR", tmp_path)
        pkg = RD._build_lessons_pkg(_profile(learning_enabled=True), "/tmp/prd.md",
                                    project_id="proj-a", run_id="r1",
                                    timestamp="2026-07-26T00:00:00Z", injection_on=True)
        assert pkg["artifact_path"] is None
        assert pkg["selected_lesson_ids"] == ()
        # catalog 缺 → degraded_class=catalog_unavailable（load_catalog_for_retrieval 既定）
        assert pkg["degraded_class"] == "catalog_unavailable"

    def test_injection_on_no_applicable_lessons(self, tmp_path, monkeypatch):
        """injection=on + catalog 有 lessons 但 applies_when_tags 不 overlap → 无 artifact（baseline）。"""
        monkeypatch.setattr(RD, "STATE_DIR", tmp_path)
        # catalog 里 lessons 是 python tag，但 task_metadata 是 typescript（不 overlap）
        _seed_catalog(tmp_path, "proj-a", lessons=2)
        pkg = RD._build_lessons_pkg(_profile(learning_enabled=True, language="typescript"),
                                    "/tmp/nonexistent.md", project_id="proj-a", run_id="r1",
                                    timestamp="2026-07-26T00:00:00Z", injection_on=True)
        assert pkg["artifact_path"] is None
        assert pkg["selected_lesson_ids"] == ()
        # retrieval 自身不 degraded（catalog 读了但 filter 后空）；non-degraded
        assert pkg["degraded_class"] is None


# ════════════════════════════════════════════════════════════════════════
# 接线点 3：memory_mode report（per-record 附加字段，不改 status/success 语义）
# ════════════════════════════════════════════════════════════════════════
class TestMemoryModeReport:
    """spec task 6.4：memory_mode 是附加字段，**绝不改** status/success/failure 语义。"""

    def test_shadow_off_baseline_memory_mode(self, tmp_path, monkeypatch):
        _set_flags(shadow=False, injection=False, monkeypatch=monkeypatch)
        monkeypatch.setattr(RD, "STATE_DIR", tmp_path)
        rec = {"status": "skip", "skip_reason": "x"}
        RD._attach_learning_memory(rec, _profile(learning_enabled=True), _entry(),
                                   stamp="20260726-0000")
        # status / skip_reason 完全不变
        assert rec["status"] == "skip"
        assert rec["skip_reason"] == "x"
        # memory_mode 是附加字段
        assert rec["memory_mode"]["shadow_on"] is False
        assert rec["memory_mode"]["injection_on"] is False
        assert rec["memory_mode"]["selected_lesson_ids"] == ()

    def test_injection_on_selected_ids_propagated_to_memory_mode(self, tmp_path, monkeypatch):
        """injection 注入的 selected_lesson_ids 经 _learning_selected_ids 桥接到 memory_mode。"""
        _set_flags(shadow=True, injection=True, monkeypatch=monkeypatch)
        monkeypatch.setattr(RD, "STATE_DIR", tmp_path)
        rec = {"status": "published",
               "_learning_selected_ids": ("lesson_a", "lesson_b"),
               "_learning_candidate_count": 5,
               "_learning_promotion_count": 3}
        # shadow on 但 mock sdk_query_fn 不返回 valid JSON → degraded；memory_mode 仍记录 selected IDs
        _seed_journal(tmp_path, "proj-a", "20260726-0000", "test-prd",
                      events=[{"event_type": "verifier_feedback", "payload": {"verdict": "pass"}}])
        RD._attach_learning_memory(rec, _profile(learning_enabled=True), _entry(),
                                   stamp="20260726-0000",
                                   sdk_query_fn=lambda p, o: "garbage")
        assert rec["memory_mode"]["shadow_on"] is True
        assert rec["memory_mode"]["injection_on"] is True
        assert rec["memory_mode"]["selected_lesson_ids"] == ("lesson_a", "lesson_b")
        assert rec["memory_mode"]["candidate_count"] == 5
        assert rec["memory_mode"]["promotion_count"] == 3
        # status / skip_reason 等字段不变（不存在则不存在）
        assert rec["status"] == "published"

    def test_invalid_combination_emits_injection_not_gated(self, tmp_path, monkeypatch):
        """injection=on, shadow=off → memory_mode.degraded_status=injection_not_gated（design 决策#8）。"""
        _set_flags(shadow=False, injection=True, monkeypatch=monkeypatch)
        monkeypatch.setattr(RD, "STATE_DIR", tmp_path)
        rec = {"status": "published"}
        RD._attach_learning_memory(rec, _profile(learning_enabled=True), _entry(),
                                   stamp="20260726-0000",
                                   sdk_query_fn=lambda p, o: "{}")
        # invalid 组合 → shadow off 零 SDK 调用 + injection 降级标记
        assert "learning_memory" not in rec
        assert rec["memory_mode"]["shadow_on"] is False
        assert rec["memory_mode"]["injection_on"] is False
        assert rec["memory_mode"]["degraded_status"] == "injection_not_gated"


# ════════════════════════════════════════════════════════════════════════
# spec task 7.1 shadow parity：terminal outcome byte-identical（shadow on vs off）
# ════════════════════════════════════════════════════════════════════════
class TestShadowParity:
    """spec task 7.1：shadow on（含 reflection 副作用）vs off——terminal outcome 字节相同（read-only 副作用）。"""

    def _baseline_rec(self) -> dict:
        return {"status": "published", "verify": {"pass": True}, "pr_url": "https://x/123",
                "verify_verdict": "pass", "verify_round": 1,
                "dev_cost": 0.01, "dev_turns": 5, "branch": "auto/x", "slug": "test-prd"}

    def test_outcome_identical_shadow_off_vs_shadow_ok(self, tmp_path, monkeypatch):
        """shadow=off 与 shadow=on+ok reflection 的 terminal outcome（status/verify/pr_url/branch）相同。"""
        # shadow off 基线
        _set_flags(shadow=False, injection=False, monkeypatch=monkeypatch)
        monkeypatch.setattr(RD, "STATE_DIR", tmp_path)
        rec_off = self._baseline_rec()
        RD._attach_learning_memory(rec_off, _profile(learning_enabled=True), _entry(),
                                   stamp="20260726-0000")

        # shadow on + ok reflection
        _set_flags(shadow=True, injection=False, monkeypatch=monkeypatch)
        rec_on = self._baseline_rec()
        # seed journal（published verifier pass）
        _seed_journal(tmp_path, "proj-a", "20260726-0000", "test-prd",
                      events=[{"event_type": "verifier_feedback", "payload": {"verdict": "pass"}}])
        RD._attach_learning_memory(rec_on, _profile(learning_enabled=True), _entry(),
                                   stamp="20260726-0000",
                                   sdk_query_fn=lambda p, o: json.dumps({"candidates": [], "audit_summary": ""}))
        # terminal outcome 字段 byte-identical
        for key in ("status", "verify", "pr_url", "verify_verdict", "verify_round",
                    "dev_cost", "dev_turns", "branch", "slug"):
            assert rec_off[key] == rec_on[key], f"terminal outcome drift on {key}"

    def test_outcome_identical_shadow_off_vs_shadow_degraded(self, tmp_path, monkeypatch):
        """shadow=off vs shadow=on+degraded（timeout）——terminal outcome 字节相同（fail-open by construction）。"""
        _set_flags(shadow=False, injection=False, monkeypatch=monkeypatch)
        monkeypatch.setattr(RD, "STATE_DIR", tmp_path)
        rec_off = self._baseline_rec()
        RD._attach_learning_memory(rec_off, _profile(learning_enabled=True), _entry(),
                                   stamp="20260726-0000")

        _set_flags(shadow=True, injection=False, monkeypatch=monkeypatch)
        rec_on = self._baseline_rec()
        _seed_journal(tmp_path, "proj-a", "20260726-0000", "test-prd",
                      events=[{"event_type": "verifier_feedback", "payload": {"verdict": "pass"}}])
        RD._attach_learning_memory(rec_on, _profile(learning_enabled=True), _entry(),
                                   stamp="20260726-0000",
                                   sdk_query_fn=lambda p, o: (_ for _ in ()).throw(TimeoutError("t")))
        for key in ("status", "verify", "pr_url", "verify_verdict", "verify_round",
                    "dev_cost", "dev_turns", "branch", "slug"):
            assert rec_off[key] == rec_on[key], f"terminal outcome drift on {key} (degraded)"
        # 唯一区别：shadow on 多了 learning_memory（degraded） + memory_mode.shadow_on=True
        assert rec_on["learning_memory"]["reflection"] == "degraded"
        assert rec_on["memory_mode"]["shadow_on"] is True


# ════════════════════════════════════════════════════════════════════════
# spec task 7.7 两级 rollback
# ════════════════════════════════════════════════════════════════════════
class TestTwoLevelRollback:
    """spec task 7.7：disable injection（level 1）→ prompt baseline；disable shadow（level 2）→ reflection 停。"""

    def test_level1_disable_injection_keeps_shadow_candidate_generation(self, tmp_path, monkeypatch):
        """level 1：injection=off, shadow=on → dev prompt byte-identical baseline + reflection 仍跑。"""
        # seed catalog（candidate generation 读它）
        monkeypatch.setattr(RD, "STATE_DIR", tmp_path)
        _seed_catalog(tmp_path, "proj-a", lessons=2)
        # injection=off → lessons_pkg 完全空（不读 catalog，artifact_path=None）
        pkg_off = RD._build_lessons_pkg(_profile(learning_enabled=True, language="python"),
                                        "/tmp/prd.md", project_id="proj-a", run_id="r1",
                                        timestamp="2026-07-26T00:00:00Z", injection_on=False)
        assert pkg_off["artifact_path"] is None
        assert pkg_off["selected_lesson_ids"] == ()
        # 即便 catalog 存在，injection=off 也不读——candidate generation（reflection）在 shadow 维度仍开
        # 模拟「reflection 跑」（验证 shadow 维度不被 injection 关闭影响）
        _set_flags(shadow=True, injection=False, monkeypatch=monkeypatch)
        shadow_on, injection_on, degraded = RD._resolve_learning_enabled(_profile(learning_enabled=True))
        assert shadow_on is True              # candidate generation 继续
        assert injection_on is False          # prompt 不改
        assert degraded is None               # 非 invalid 组合（injection 主动关，不是降级）

    def test_level2_disable_shadow_stops_reflection_entirely(self, tmp_path, monkeypatch):
        """level 2：shadow=off（both off）→ reflection 完全不调（sdk_query_fn 调用计数=0）。"""
        _set_flags(shadow=False, injection=False, monkeypatch=monkeypatch)
        monkeypatch.setattr(RD, "STATE_DIR", tmp_path)
        sdk_calls = []
        rec = {"status": "published"}
        RD._attach_learning_memory(rec, _profile(learning_enabled=True), _entry(),
                                   stamp="20260726-0000",
                                   sdk_query_fn=lambda p, o: sdk_calls.append((p, o)) or "{}")
        assert sdk_calls == [], f"shadow=off 仍调 SDK: {len(sdk_calls)} 次"
        # candidate facts 若存在（之前 shadow=on 跑过）仍 inert——不参与 retrieval（injection=off）
        # 也不参与 catalog 重建（append-only，replay 可重建，但本测试不验 store 层，已在 Section 2 测）

    def test_level2_candidate_facts_remain_inert_and_rebuildable(self, tmp_path, monkeypatch):
        """level 2：existing candidate facts 保留（append-only）+ catalog 可经 _replay 重建（不依赖 shadow）。"""
        monkeypatch.setattr(RD, "STATE_DIR", tmp_path)
        # seed catalog（前 shadow=on 阶段产出的）+ candidates（append-only 真源）
        _seed_catalog(tmp_path, "proj-a", lessons=2)
        cand_path = tmp_path / "lessons" / "candidates" / "proj-a.jsonl"
        cand_path.parent.mkdir(parents=True, exist_ok=True)
        cand_path.write_text(json.dumps({"lesson_id": "lesson_0", "schema_version": 1}) + "\n",
                             encoding="utf-8")
        events_path = tmp_path / "lessons" / "events" / "proj-a.jsonl"
        events_path.parent.mkdir(parents=True, exist_ok=True)
        events_path.write_text("", encoding="utf-8")

        # shadow off 状态下 retrieval 不跑（injection off）；但 catalog 重建（_replay）不依赖 shadow flag
        catalog = LMCat.load_catalog_file(str(tmp_path), "proj-a")
        assert isinstance(catalog, dict)
        assert len(catalog.get("entries", [])) == 2   # 原 entries 仍可读

        # injection=off 时 retrieval 完全不调
        pkg = RD._build_lessons_pkg(_profile(learning_enabled=True), "/tmp/prd.md",
                                    project_id="proj-a", run_id="r1",
                                    timestamp="2026-07-26T00:00:00Z", injection_on=False)
        assert pkg["artifact_path"] is None
        assert pkg["selected_lesson_ids"] == ()
        # candidate facts inert（不进 prompt）+ 可重建（store 层 _replay 已单测；本测试只验 retrieval 不读）


# ════════════════════════════════════════════════════════════════════════
# dev-agent build_prompt：lesson_block 注入 byte-identical baseline（injection=off）
# ════════════════════════════════════════════════════════════════════════
class TestDevAgentBuildPromptInjection:
    """dev-agent.build_prompt：lessons_artifact 缺 → baseline byte-identical；有 → lesson_block append。"""

    @classmethod
    def _import_dev_agent(cls):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "dev_agent_test", Path(RD.__file__).parent / "dev-agent.py")
        da = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(da)
        return da

    def test_no_lessons_artifact_baseline_prompt(self, tmp_path):
        """无 --lessons-artifact → build_prompt 返 baseline（与未加 learning memory 时 byte-identical）。"""
        da = self._import_dev_agent()
        prd = "# PRD\n\ndo X\n"
        args_off = {"base": "main", "dry_run": True, "source": None, "feedback_artifact": None,
                    "lessons_artifact": None}
        args_empty = {"base": "main", "dry_run": True, "source": None, "feedback_artifact": None,
                      "lessons_artifact": None}
        prompt_off = da.build_prompt(args_off, prd, None)
        prompt_empty = da.build_prompt(args_empty, prd, None)
        # 两次 baseline prompt byte-identical（无 lesson_block 痕迹）
        assert prompt_off == prompt_empty
        assert "Applicable lessons from prior PRDs" not in prompt_off

    def test_lessons_artifact_passed_through_to_prompt(self, tmp_path):
        """有 --lessons-artifact → build_prompt 读 artifact + lesson_block append 到 prompt 末尾。"""
        da = self._import_dev_agent()
        # 写 lesson_block artifact
        artifact_path = tmp_path / "lessons.txt"
        artifact_path.write_text(
            "## Applicable lessons from prior PRDs (apply where relevant)\n"
            "- **lesson_a** — trigger: when X happens\n"
            "  - action: do Y to prevent recurrence\n"
            "  - skip when: stage=post_terminal\n",
            encoding="utf-8")
        prd = "# PRD\n\ndo X\n"
        args_on = {"base": "main", "dry_run": True, "source": None, "feedback_artifact": None,
                   "lessons_artifact": str(artifact_path)}
        args_off = {"base": "main", "dry_run": True, "source": None, "feedback_artifact": None,
                    "lessons_artifact": None}
        prompt_on = da.build_prompt(args_on, prd, None)
        prompt_off = da.build_prompt(args_off, prd, None)
        # 注入版 == baseline + "\n\n" + lesson_block
        assert prompt_on == prompt_off + "\n\n" + artifact_path.read_text(encoding="utf-8")
        # baseline 不含 lesson_block
        assert "Applicable lessons from prior PRDs" not in prompt_off
        assert "Applicable lessons from prior PRDs" in prompt_on

    def test_lessons_artifact_missing_file_baseline_noop(self, tmp_path):
        """--lessons-artifact 指向不存在文件 → read_text 返 None → inject_into_prompt no-op（baseline）。"""
        da = self._import_dev_agent()
        prd = "# PRD\n"
        args = {"base": "main", "dry_run": True, "source": None, "feedback_artifact": None,
                "lessons_artifact": "/nonexistent/lessons.txt"}
        prompt = da.build_prompt(args, prd, None)
        baseline = da.build_prompt({**args, "lessons_artifact": None}, prd, None)
        # 读不到 → lessons_block="" → inject_into_prompt no-op → baseline
        assert prompt == baseline

    def test_dev_cmd_passes_lessons_artifact_flag(self, tmp_path, monkeypatch):
        """_dev_cmd 收 lessons_artifact 参数 → 透传 --lessons-artifact <path> 到 dev-agent CLI。"""
        monkeypatch.setattr(RD, "DEV_AGENT_PY", tmp_path / "fake-dev-agent.py")
        (tmp_path / "fake-dev-agent.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
        cmd_off = RD._dev_cmd(_profile(), "/tmp/prd.md", "main", "/tmp/src.md")
        assert not any(a == "--lessons-artifact" for a in cmd_off)
        cmd_on = RD._dev_cmd(_profile(), "/tmp/prd.md", "main", "/tmp/src.md",
                             lessons_artifact="/tmp/lb.txt")
        # --lessons-artifact 出现在 cmd 里，后跟 path
        idx = cmd_on.index("--lessons-artifact")
        assert cmd_on[idx + 1] == "/tmp/lb.txt"


# ════════════════════════════════════════════════════════════════════════
# 外部评审 P1 #1 复现：dispatch status → envelope.terminal_status 映射
# ════════════════════════════════════════════════════════════════════════
class TestEnvelopeTerminalStatusMapping:
    """spec P1 #1 修复：dispatch rec.status 词汇（pr_open/fail/skip/...）与
    ``loop_state.IterationStatus`` 受控 value 集合不一致 → envelope.terminal_status 直传会让
    ``learning_memory_reflection.py`` L362 ``IterationStatus(...)`` 抛 ValueError →
    degraded{class:not_terminal}（**不生成候选**）。

    修复：run_daily 加 ``_dispatch_status_to_envelope_terminal`` 做映射，**不改** reflection.py 的
    ``IterationStatus()`` 守护契约。映射表对齐 ``_SJ_TERMINAL_MAP`` + ``_sj_terminal`` 双门分流逻辑。

    测试策略：纯函数单测（不进 envelope 构造）+ 1 个 integration 验证完整链路（rec → envelope →
    reflection）——避免依赖 SDK 桩被调用（not_terminal 时 SDK 不调）。
    """

    @pytest.mark.parametrize("status, verify_pass, verify_verdict, expected", [
        # identity pass-through：已是合法 IterationStatus.value（loop_state 终态名/中间态名直传场景）
        ("published", True, "pass", "published"),
        ("failed", None, None, "failed"),
        ("aborted", None, None, "aborted"),
        ("stalled", None, None, "stalled"),
        ("blocked_evidence", None, None, "blocked_evidence"),
        ("external_blocked", None, None, "external_blocked"),
        ("sandbox_blocked", None, None, "sandbox_blocked"),
        # 直接映射表（对齐 _SJ_TERMINAL_MAP）
        ("skip", None, None, "aborted"),
        ("blocked_external_state", None, None, "external_blocked"),
        ("blocked_test_gate", None, None, "test_blocked"),
        ("fail", None, None, "failed"),
        # pr_open/interrupted_pr 双门分流（与 _sj_terminal L1665-1685 同款）
        ("pr_open", True, "pass", "published"),         # 机械绿 + 语义 pass → published
        ("pr_open", True, "revise", "revise"),          # 机械绿但语义红 → revise
        ("pr_open", False, "pass", "revise"),           # 机械不绿（即使语义 pass）→ revise
        ("interrupted_pr", True, "pass", "published"),
        ("interrupted_pr", True, "revise", "revise"),
        # 其他 pr_* 前缀（pr_merged/pr_closed/...）
        ("pr_merged", None, None, "published"),         # merged → published（reconcile 看到 merged 即等价成功交付）
        ("pr_closed", None, None, "revise"),            # 其余保守 revise
        # 未知/空 → ""（fail-open：让 reflection 走 not_terminal degraded，不崩 envelope 构造）
        ("some_unmapped_status", None, None, ""),
        ("", None, None, ""),
    ])
    def test_dispatch_status_maps_to_iteration_status_value(self, status, verify_pass,
                                                             verify_verdict, expected):
        """_dispatch_status_to_envelope_terminal(rec) → 返回合法 IterationStatus.value（或 ""）。

        覆盖：identity pass-through / 直接映射表 / pr_* 双门分流 / pr_merged / fail-open 兜底。
        """
        rec = {"status": status}
        if verify_pass is not None:
            rec["verify"] = {"pass": verify_pass}
        if verify_verdict is not None:
            rec["verify_verdict"] = verify_verdict
        assert RD._dispatch_status_to_envelope_terminal(rec) == expected

    def test_envelope_link_does_not_degrade_on_pr_open_double_green(self, tmp_path, monkeypatch):
        """integration：dispatch rec.status=pr_open + verify 双绿 → envelope.terminal_status=published
        → reflection 进 SDK 路径（**未**在 not_terminal degraded）→ reflection ok。

        bug 复现（修复前）：pr_open 直传 → IterationStatus("pr_open") ValueError → SDK 不调 →
        rec["learning_memory"].reflection=degraded{not_terminal}。
        """
        _set_flags(shadow=True, injection=False, monkeypatch=monkeypatch)
        monkeypatch.setattr(RD, "STATE_DIR", tmp_path)
        _seed_journal(tmp_path, "proj-a", "20260726-0000", "test-prd",
                      events=[{"event_type": "verifier_feedback", "payload": {"verdict": "pass"}}])
        sdk_calls = []

        def _sdk(prompt, options):
            sdk_calls.append(prompt)
            # 反向断言：prompt 内 metadata.terminal_status 已映射为 published
            payload = json.loads(prompt)
            assert payload["metadata"]["terminal_status"] == "published"
            return json.dumps({"candidates": [], "audit_summary": ""})

        rec = {"status": "pr_open", "verify": {"pass": True}, "verify_verdict": "pass",
               "pr_url": "https://x"}
        RD._attach_learning_memory(rec, _profile(learning_enabled=True), _entry(),
                                   stamp="20260726-0000", sdk_query_fn=_sdk)
        # SDK 被调（envelope.terminal_status=published → reflection 进 SDK 路径，未在 not_terminal degraded）
        assert len(sdk_calls) == 1, "envelope.terminal_status 非法 → reflection 在 not_terminal degraded 不调 SDK"
        assert rec["learning_memory"]["reflection"] == "ok"
        assert rec["learning_memory"].get("class") is None    # not_terminal 不发生
        # fail-open：terminal outcome 不变
        assert rec["status"] == "pr_open"
        assert rec["pr_url"] == "https://x"

    def test_envelope_link_fail_open_on_unmapped_status(self, tmp_path, monkeypatch):
        """integration：未知 dispatch status → envelope.terminal_status="" → reflection degraded{not_terminal}
        → **不改** terminal outcome（fail-open by construction）。

        rec 保留 dispatch 设置的全部字段；只 reflection 失效（degraded side-channel）。
        """
        _set_flags(shadow=True, injection=False, monkeypatch=monkeypatch)
        monkeypatch.setattr(RD, "STATE_DIR", tmp_path)
        _seed_journal(tmp_path, "proj-a", "20260726-0000", "test-prd",
                      events=[{"event_type": "verifier_feedback", "payload": {"verdict": "pass"}}])
        sdk_calls = []

        def _sdk(prompt, options):
            sdk_calls.append(prompt)
            return "{}"

        rec = {"status": "some_unmapped_status", "skip_reason": "??",
               "verify": {"pass": True}, "pr_url": "https://x"}
        RD._attach_learning_memory(rec, _profile(learning_enabled=True), _entry(),
                                   stamp="20260726-0000", sdk_query_fn=_sdk)
        # SDK 不被调（not_terminal degraded 在调 SDK 前返回）
        assert sdk_calls == []
        assert rec["learning_memory"]["reflection"] == "degraded"
        assert rec["learning_memory"]["class"] == "not_terminal"
        # fail-open：terminal outcome 字段全部保留（dispatch 字段不被 envelope/reflection 污染）
        assert rec["status"] == "some_unmapped_status"
        assert rec["skip_reason"] == "??"
        assert rec["pr_url"] == "https://x"
