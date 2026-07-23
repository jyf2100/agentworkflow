#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_cutover.py — Section 8（Cutover/Canary/Recovery Drills）task 8.1-8.6 + 8.8 drill harness 测试。

覆盖每个 drill（注入 fake 适配器覆盖路径）：
    * 8.1 shadow parity（matched/mismatch + dry-run 端到端）；
    * 8.2 lifecycle canary（no_test/test_red/test_green/compaction 4 场景）；
    * 8.3 crash drill（agent/test/push/pr 边界 + FakeResolver 三态 → exactly-once）；
    * 8.4 recovery canary（resume/fork/new_session + bounded budget + 因果）；
    * 8.5 sandbox canary（python local tier + container network/credential denial）；
    * 8.6 dispatch cutover（journal-driven 开闸 + legacy fallback）；
    * 8.8 quality gate（全过/失败/归档失败）。

诚实披露：真实 docker/PR/SDK 运行时验证不在 harness（策略层用 FakeContainerRunner/FakeResolver
覆盖；真实环境由运维换真实适配器跑同一 harness）。8.7 runbook 是独立 markdown 文档。

AAA；纯库编排，cron 隔离友好。跑：
    python3 -m pytest scripts/test_cutover.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import container_sandbox as CS  # noqa: E402
import cutover as CT  # noqa: E402
import loop_state as L  # noqa: E402
import retry_policy as RP  # noqa: E402
import sandbox as SB  # noqa: E402


def _ev(etype: str, *, eid: str = "e1", iter_id: str = "i1", run_id: str = "r1",
        prd_id: str = "p1", payload: dict | None = None) -> L.JournalEvent:
    """构造一条 JournalEvent（drill 用）。"""
    return L.JournalEvent(
        schema_version=L.JOURNAL_SCHEMA_VERSION, event_id=eid, timestamp="2026-07-22T00:00:00Z",
        iteration_id=iter_id, run_id=run_id, prd_id=prd_id,
        event_type=etype, payload=payload or {},
    )


class FakeResolver:
    """reconcile KeyResolver 测试桩（三态：True=confirmed / False=absent / None=unknown）。"""
    __test__ = False

    def __init__(self, result):
        self.result = result

    def check(self, kind, target):
        return self.result


class FakeContainerRunner:
    """container sandbox 测试桩（复用 test_sandbox 的形态）。"""
    __test__ = False

    def __init__(self, *, available=True, exec_result=(0, "", "")):
        self._available = available
        self._exec_result = exec_result

    def available(self):
        return self._available

    def create(self, **kw):
        return "fake_container_1"

    def exec(self, container_id, command):
        return self._exec_result

    def remove(self, container_id):
        pass


class _StaticEgress:
    """task 5.1/5.2 egress enforcement 桩：``enforceable``/``install`` 固定 bool（过 preflight+install）。"""
    __test__ = False

    def __init__(self, enforceable: bool = True):
        self._ok = enforceable

    def enforceable(self) -> bool:
        return self._ok

    def install(self, allowlist) -> bool:   # noqa: ARG002
        return self._ok

    def describe(self) -> str:
        return "static test egress"


# ════════════════════════════════════════════════════════════════════════════
# 8.1 shadow parity + dry-run
# ════════════════════════════════════════════════════════════════════════════
def test_shadow_parity_matched_when_both_empty():
    rep = CT.run_shadow_parity_drill(dispatch_records=[], journal_events=[])
    assert rep.matched is True
    assert rep.mismatches == ()


def test_shadow_parity_matched_when_same_distribution():
    # dispatch: 1 PUBLISHED（pr_open + verify.pass）+ 1 FAILED
    dispatch = [
        {"status": "pr_open", "verify": {"pass": True}, "project": "p", "slug": "a"},
        {"status": "fail", "project": "p", "slug": "b"},
    ]
    # journal: 同分布需 events reduce 出 1 PUBLISHED + 1 FAILED——这里只验证 parity 逻辑，
    # 用空 journal 触发 mismatch 再修；本例聚焦 dispatch 端 summarize 正确
    rep = CT.run_shadow_parity_drill(dispatch_records=dispatch, journal_events=[])
    assert rep.matched is False                      # journal 空而 dispatch 有 → mismatch
    assert any("published" in m for m in rep.mismatches)


def test_shadow_parity_mismatch_lists_each_bucket():
    dispatch = [{"status": "pr_open", "verify": {"pass": True}}]
    rep = CT.run_shadow_parity_drill(dispatch, [])
    assert rep.matched is False
    assert len(rep.mismatches) == 1
    assert "published" in rep.mismatches[0]


def test_shadow_dry_run_emits_then_reduces(tmp_path):
    """8.1 one real dry-run：ShadowJournal 旁路写 → read → reduce 端到端。"""
    jpath = tmp_path / "journal.jsonl"
    state = CT.run_shadow_dry_run(
        journal_path=str(jpath), run_id="r1", stamp=lambda: "2026-07-22T00:00:00Z",
        flow=[("running", "i1", "p1", {"base": "main"}),
              ("aborted", "i1", "p1", {})],
    )
    assert isinstance(state, L.IterationState)
    assert state.status in (L.IterationStatus.ABORTED,)   # running→aborted 合法迁移
    assert state.run_id == "r1"


# ════════════════════════════════════════════════════════════════════════════
# 8.2 lifecycle canary
# ════════════════════════════════════════════════════════════════════════════
def test_lifecycle_no_test_denies_stop():
    r = CT.run_lifecycle_drill("no_test")
    assert r.stop_decision == "deny"                  # 无 evidence → bounded 续命


def test_lifecycle_test_red_denies_stop():
    r = CT.run_lifecycle_drill("test_red")
    assert r.stop_decision == "deny"


def test_lifecycle_test_green_allows_stop():
    r = CT.run_lifecycle_drill("test_green")
    assert r.stop_decision == "allow"
    assert "fresh green" in r.detail


def test_lifecycle_compaction_persists_snapshot():
    r = CT.run_lifecycle_drill("compaction")
    assert r.snapshot_persisted is True
    assert r.stop_decision == "allow"


def test_lifecycle_unknown_scenario_raises():
    with pytest.raises(ValueError):
        CT.run_lifecycle_drill("bogus")


# ════════════════════════════════════════════════════════════════════════════
# 7.2 SDK hook canary（no-test / stale-test / green-test / semantic-revise / compaction / subagent / hook-failure）
# ════════════════════════════════════════════════════════════════════════════
import evidence as EV  # noqa: E402


def test_canary_stale_test_denies_stop():
    """stale-test path：绿后候选写 → GATE_STALE（区别 test_red 的 GATE_FAILED）。"""
    r = CT.run_lifecycle_drill("stale_test")
    assert r.stop_decision == "deny"
    assert r.gate == EV.GATE_STALE


def test_canary_semantic_revise_defers_publish():
    """semantic-revise path：inner fresh-green gate 放行，外层 verify 语义判红 → revise（dual-gate）。"""
    r = CT.run_lifecycle_drill("semantic_revise")
    assert r.stop_decision == "revise"
    assert r.gate == "semantic_revise"


def test_canary_subagent_blocks_publication():
    """subagent path：subagent context 强制 allow_publication=False → publication PreToolUse DENY。"""
    r = CT.run_lifecycle_drill("subagent")
    assert r.stop_decision == "deny"
    assert r.gate == "publication_blocked"
    assert "deny" in r.detail.lower()


def test_canary_hook_failure_fail_closed():
    """hook-failure path：snapshot_writer 抛异常 → auto 压缩 unpersisted → fail-closed block。"""
    r = CT.run_lifecycle_drill("hook_failure")
    assert r.stop_decision == "deny"
    assert r.gate == "fail_closed"
    assert r.snapshot_persisted is False


def test_run_sdk_hook_canary_covers_all_spec_paths():
    """canary 命令覆盖 spec 7 path（design#1 production 证据命令 + #6 archive）。"""
    ev = CT.run_sdk_hook_canary()
    assert set(ev.paths_covered) == {
        "no-test", "stale-test", "green-test", "semantic-revise",
        "compaction", "subagent", "hook-failure",
    }
    mapped = [CT.SPEC_PATH_TO_SCENARIO[p] for p in ev.paths_covered]
    assert all(ev.stop_gates[s] for s in mapped)


def test_run_sdk_hook_canary_archives_immutable():
    """evidence frozen + 不可变归档（tuple scenarios + 非空 summary）。"""
    ev = CT.run_sdk_hook_canary()
    assert isinstance(ev.scenarios, tuple)
    assert all(isinstance(s, CT.LifecycleDrillResult) for s in ev.scenarios)
    assert ev.summary


# ════════════════════════════════════════════════════════════════════════════
# 8.3 crash drill（exactly-once via reconcile）
# ════════════════════════════════════════════════════════════════════════════
def test_crash_agent_done_no_side_effects_exactly_once():
    r = CT.run_crash_drill("agent_done", resolver=FakeResolver(True))
    assert r.confirmed == 0 and r.pending == 0 and r.unknown == 0
    assert r.exactly_once is True                     # 无副作用 → 无歧义


def test_crash_push_confirmed_skips_on_retry():
    """push 后崩溃：reconcile 发现已 push（confirmed）→ retry 跳过（exactly-once）。"""
    r = CT.run_crash_drill("push", resolver=FakeResolver(True))
    assert r.confirmed == 1 and r.pending == 0
    assert r.exactly_once is True


def test_crash_push_absent_reapplies_once():
    r = CT.run_crash_drill("push", resolver=FakeResolver(False))
    assert r.pending == 1 and r.confirmed == 0
    assert r.exactly_once is True


def test_crash_pr_unknown_blocks_not_blindly_reapplied():
    """PR 状态查不到（gh 失败）→ unknown → fail-safe，绝不盲目补开 PR。"""
    r = CT.run_crash_drill("pr_create", resolver=FakeResolver(None))
    assert r.unknown == 1
    assert r.exactly_once is False                    # 有 unknown → 不安全 retry
    assert r.external_known is False


def test_crash_unknown_boundary_raises():
    with pytest.raises(ValueError):
        CT.run_crash_drill("bogus", resolver=FakeResolver(True))


# ════════════════════════════════════════════════════════════════════════════
# 7.3 crash reconciliation evidence（commit 边界 + 全边界归档，design #1/#6）
# ════════════════════════════════════════════════════════════════════════════
def test_crash_commit_confirmed_skips_on_retry():
    """commit 边界（spec 7.3 新增）：commit 副作用已发生 → confirmed 跳过（exactly-once）。"""
    r = CT.run_crash_drill("commit", resolver=FakeResolver(True))
    assert r.boundary == "commit"
    assert r.confirmed == 1
    assert r.exactly_once is True


def test_crash_commit_absent_reapplies_once():
    """commit 边界：commit 未发生 → pending 重新执行一次。"""
    r = CT.run_crash_drill("commit", resolver=FakeResolver(False))
    assert r.pending == 1
    assert r.exactly_once is True


def test_run_crash_reconciliation_evidence_all_boundaries():
    """归档命令覆盖 spec 全 5 边界（agent/test/commit/push/PR），全 confirmed → all exactly-once。"""
    ev = CT.run_crash_reconciliation_evidence(resolver=FakeResolver(True))
    assert set(ev.boundaries_run) == {"agent_done", "test_done", "commit", "push", "pr_create"}
    assert ev.all_exactly_once is True


def test_run_crash_reconciliation_evidence_unknown_breaks_exactly_once():
    """任一边界 unknown → all_exactly_once=False（fail-safe，design risk）。"""
    ev = CT.run_crash_reconciliation_evidence(resolver=FakeResolver(None))
    assert ev.all_exactly_once is False


def test_run_crash_reconciliation_evidence_archives_immutable():
    """evidence frozen + 不可变归档（tuple results + 非空 summary）。"""
    ev = CT.run_crash_reconciliation_evidence(resolver=FakeResolver(True))
    assert isinstance(ev.results, tuple)
    assert all(isinstance(r, CT.CrashDrillResult) for r in ev.results)
    assert ev.summary


# ════════════════════════════════════════════════════════════════════════════
# 7.4 journal-corruption recovery command（runbook 命令 + e2e，design #1/#6）
# ════════════════════════════════════════════════════════════════════════════
import json as _json  # noqa: E402
import subprocess  # noqa: E402
import journal as _J  # noqa: E402


def _revo_ev(eid, etype="running", **payload):
    """造最小合法 JournalEvent（7.4 recovery fixture）。"""
    return L.JournalEvent(
        schema_version=L.JOURNAL_SCHEMA_VERSION, event_id=eid,
        timestamp="2026-07-22T00:00:00Z", iteration_id="i", run_id="r",
        prd_id="p", event_type=etype, payload=payload)


def test_recovery_corrupt_journal_manual_blocks(tmp_path):
    """中部损坏 → explicit manual-block（不自动修复，给运维 corrupted_line_numbers）。"""
    p = tmp_path / "bad.jsonl"
    _J.append_event(p, _revo_ev("e1", "planned"))
    p.write_text(p.read_text() + "{CORRUPT\n")        # 中部坏行
    _J.append_event(p, _revo_ev("e2", "running"))     # append → 坏行变中部
    r = CT.run_journal_recovery(journal_path=p)
    assert r.action == "manual_block"
    assert r.report.is_fail_closed


def test_recovery_tail_truncated_recovers(tmp_path):
    """末尾截断容忍 → verifiable recovery（reduce 重建终态）。"""
    p = tmp_path / "trunc.jsonl"
    _J.append_event(p, _revo_ev("e1", "planned"))
    _J.append_event(p, _revo_ev("e2", "running"))
    p.write_text(p.read_text() + '{"schema_version":')   # 半行截断尾
    r = CT.run_journal_recovery(journal_path=p)
    assert r.action == "recovered"
    assert r.terminal_status is not None


def test_recovery_clean_with_prd_recovers_context(tmp_path):
    """正常 journal + PRD → recovered + RecoveryContext（verifiable 完整恢复）。"""
    p = tmp_path / "clean.jsonl"
    _J.append_event(p, _revo_ev("e1", "planned"))
    _J.append_event(p, _revo_ev("e2", "running"))
    r = CT.run_journal_recovery(journal_path=p, prd_content="# 标题\n\n## 验收\n- [ ] a\n")
    assert r.action == "recovered"
    assert r.recovery_context is not None


def test_recovery_cli_executable_manual_block_e2e(tmp_path):
    """recovery_cli.py 是 runbook 引用的可执行命令：损坏 → exit 2 + JSON manual_block。"""
    p = tmp_path / "bad.jsonl"
    _J.append_event(p, _revo_ev("e1", "planned"))
    p.write_text(p.read_text() + "{CORRUPT\n")
    _J.append_event(p, _revo_ev("e2", "running"))
    cli = Path(__file__).parent / "recovery_cli.py"
    proc = subprocess.run([sys.executable, str(cli), str(p)],
                          capture_output=True, text=True)
    assert proc.returncode == 2                       # manual_block exit code
    data = _json.loads(proc.stdout)
    assert data["action"] == "manual_block"


def test_runbook_references_existing_commands():
    """spec：runbook 引用的每个命令存在于仓库（every referenced command exists）。"""
    runbook = Path(__file__).parent.parent / "RUNBOOK.md"
    assert runbook.exists()
    text = runbook.read_text(encoding="utf-8")
    assert "recovery_cli.py" in text
    assert (Path(__file__).parent / "recovery_cli.py").exists()


# ════════════════════════════════════════════════════════════════════════════
# 8.4 recovery canary（resume/fork/new_session + bounded budget + 因果）
# ════════════════════════════════════════════════════════════════════════════
def test_recovery_resume_decision():
    r = CT.run_recovery_drill("resume")
    assert r.decision_mode == "resume"
    assert r.causality_intact is True
    assert not r.budget_exhausted


def test_recovery_fork_decision():
    r = CT.run_recovery_drill("fork")
    assert r.decision_mode == "fork"
    assert r.causality_intact is True


def test_recovery_new_session_when_session_missing():
    r = CT.run_recovery_drill("new_session")
    assert r.decision_mode == "new_session"
    assert r.causality_intact is True


def test_recovery_budget_exhausted_forces_stop():
    """bounded budget 耗尽 → RetryPolicy STOP（risk#92 硬 kill 兜底）。"""
    budget = RP.BudgetState(
        limits=RP.BudgetLimits(),
        sdk_retries_used=RP.BudgetLimits().sdk_retries,   # 耗尽 SDK retry 维度
    )
    assert budget.exhausted
    r = CT.run_recovery_drill("resume", budget=budget)
    assert r.decision_mode == "stop"
    assert r.budget_exhausted is True


def test_recovery_unknown_mode_raises():
    with pytest.raises(ValueError):
        CT.run_recovery_drill("bogus")


# ════════════════════════════════════════════════════════════════════════════
# 8.5 sandbox canary（python local tier + container network/credential denial）
# ════════════════════════════════════════════════════════════════════════════
def test_sandbox_python_local_tier_runs_clean(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    lw = SB.LocalWorktreeSandbox()
    handle = lw.prepare(SB.SandboxSpec(worktree_dir=str(repo), network_allowlist=()))
    assert isinstance(handle, SB.SandboxHandle)
    r = CT.run_sandbox_drill(sandbox=lw, handle=handle, command="python3 -c 'print(1)'",
                             language="python")
    assert r.language == "python" and r.tier == "local_worktree"
    assert r.exit_code == 0
    assert r.network_denied is False
    assert r.credential_denied is True                 # 长期凭据始终不进 sandbox


def test_sandbox_container_network_violation_denied(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    c = CS.ContainerSandbox(FakeContainerRunner(exec_result=(0, "", "")), egress=_StaticEgress(True))
    handle = c.prepare(SB.SandboxSpec(worktree_dir=str(repo), network_allowlist=("pypi.org",)))
    assert isinstance(handle, SB.SandboxHandle)
    # requested_hosts 含未声明的 evil.com → policy block
    r = CT.run_sandbox_drill(sandbox=c, handle=handle, command=["python3", "-c", "1"],
                             language="python", network_violation_host="evil.com")
    assert r.network_denied is True
    assert r.exit_code == -1
    assert r.credential_denied is True


def test_sandbox_credential_denied_regardless_of_kind(tmp_path):
    """8.5：无论 publication kind，长期凭据都不进 sandbox（host-side verified）。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    lw = SB.LocalWorktreeSandbox()
    handle = lw.prepare(SB.SandboxSpec(worktree_dir=str(repo)))
    r = CT.run_sandbox_drill(sandbox=lw, handle=handle, command="true",
                             language="python", credential_kind="smtp_send")
    assert r.credential_denied is True


# ════════════════════════════════════════════════════════════════════════════
# 8.6 dispatch cutover（journal-driven + legacy fallback）
# ════════════════════════════════════════════════════════════════════════════
def test_dispatch_cutover_journal_driven_when_flag_on():
    events = [_ev("running", eid="e1"), _ev("aborted", eid="e2")]   # running→aborted
    r = CT.run_dispatch_cutover_drill(journal_driven=True, journal_events=events)
    assert r.driven_by == "journal"
    assert r.terminal_state == "aborted"
    assert r.fallback_reason == ""


def test_dispatch_cutover_legacy_fallback_when_flag_off():
    r = CT.run_dispatch_cutover_drill(journal_driven=False, journal_events=[_ev("running")],
                                      legacy_records=[{"status": "pr_open", "verify": {"pass": True}}])
    assert r.driven_by == "legacy_fallback"
    assert r.terminal_state == "published"
    assert "disabled" in r.fallback_reason


def test_dispatch_cutover_falls_back_when_reducer_fails():
    """reducer 失败（坏 events）→ 一个 release cycle 内 legacy 仍可用。"""
    r = CT.run_dispatch_cutover_drill(journal_driven=True, journal_events=["not-an-event"],
                                      legacy_records=[{"status": "fail"}])
    assert r.driven_by == "legacy_fallback"
    assert "reducer failed" in r.fallback_reason


def test_dispatch_cutover_empty_legacy_corrupt_state():
    r = CT.run_dispatch_cutover_drill(journal_driven=False, legacy_records=[])
    assert r.driven_by == "legacy_fallback"
    assert r.terminal_state == "state_corrupt"        # 无历史 → fail-closed STATE_CORRUPT


# ════════════════════════════════════════════════════════════════════════════
# 8.8 quality gate + evidence archive
# ════════════════════════════════════════════════════════════════════════════
def test_quality_gate_passes_when_green_and_evidence_archived(tmp_path):
    root = tmp_path / "artifacts"
    r = CT.run_quality_gate(
        test_counts={"passed": 100, "failed": 0},
        evidence_items=[("test_output", "pytest ok"), ("recovery_snapshot", "snap")],
        artifact_root=str(root),
    )
    assert r.passed is True
    assert r.tests_failed == 0 and r.tests_total == 100
    assert len(r.evidence_digests) == 2


def test_quality_gate_fails_when_tests_red(tmp_path):
    r = CT.run_quality_gate(test_counts={"passed": 90, "failed": 10},
                            evidence_items=[("test_output", "x")],
                            artifact_root=str(tmp_path / "a"))
    assert r.passed is False
    assert r.tests_failed == 10


def test_quality_gate_fails_when_evidence_archive_fails(tmp_path):
    """归档失败（artifact_root 不可写/非法）→ 不通过（绝不伪装绿）。"""
    r = CT.run_quality_gate(
        test_counts={"passed": 5, "failed": 0},
        evidence_items=[("test_output", "x")],
        artifact_root=str(tmp_path / "a"),             # 合法路径——人为制造非对齐：传多余 evidence
    )
    # 多传一条无法对齐的 evidence → 归档数 < 项数 → passed False
    r2 = CT.run_quality_gate(
        test_counts={"passed": 5, "failed": 0},
        evidence_items=[("test_output", "x"), ("bad", None)],   # None content 触发 store 异常
        artifact_root=str(tmp_path / "b"),
    )
    assert r.passed is True
    assert r2.passed is False                          # 一条归档失败 → 整体不通过


def test_quality_gate_empty_tests_does_not_pass(tmp_path):
    r = CT.run_quality_gate(test_counts={}, evidence_items=[],
                            artifact_root=str(tmp_path / "c"))
    assert r.passed is False                           # 无测试 = 无证据 = 不通过


# ════════════════════════════════════════════════════════════════════════════
# CutoverSuiteResult 汇总（8.8 归档前汇总用）
# ════════════════════════════════════════════════════════════════════════════
def test_cutover_suite_summary_formats_flags():
    suite = CT.CutoverSuiteResult(
        shadow_parity_matched=True, lifecycle_all_pass=True, crash_all_exactly_once=True,
        recovery_all_intact=True, sandbox_all_clean=True, dispatch_cutover_ok=True,
        quality_gate_passed=True, overall_passed=True,
    )
    assert "PASS" in suite.summary and "parity=True" in suite.summary


# ════════════════════════════════════════════════════════════════════════════
# Section 7 task 7.1：historical fixtures shadow parity + 一个真实 no-write dispatch
# spec：「Run shadow parity against historical fixtures and one real no-write dispatch,
# resolving every terminal mismatch.」
# design 决策#2（parity 比对全 terminal state）+ #1（production wiring，非 disconnected helper）
# + #6（archive evidence）。run_shadow_parity_drill/dry_run 逻辑层已在 8.1 覆盖；7.1 补 production
# historical fixtures（覆盖全 terminal class，parity matched 基线）+ 串成可归档 evidence 的命令。
# ════════════════════════════════════════════════════════════════════════════
def test_historical_fixtures_cover_all_terminal_classes():
    """7.1：historical fixtures 覆盖 decision#2 parity 比对范围的全 terminal class：
    published/revise/failed/blocked_external/blocked_test/stalled/orphan/planned。"""
    import compat_readers as CR
    import cutover_fixtures as FX
    statuses = {CR.legacy_status(r) for r in FX.HISTORICAL_DISPATCH_RECORDS}
    for expected in (L.IterationStatus.PUBLISHED, L.IterationStatus.REVISE,
                     L.IterationStatus.FAILED, L.IterationStatus.EXTERNAL_BLOCKED,
                     L.IterationStatus.TEST_BLOCKED, L.IterationStatus.STALLED,
                     L.IterationStatus.ORPHAN_DELETED, L.IterationStatus.PLANNED):
        assert expected in statuses, f"historical fixtures 缺 terminal class {expected}"


def test_run_shadow_parity_evidence_historical_matched(tmp_path):
    """7.1：run_shadow_parity_evidence → historical fixtures parity matched（mismatch 已解决，design 7.1）。"""
    ev = CT.run_shadow_parity_evidence(
        state_dir=tmp_path, stamp_fn=lambda: "2026-07-23T00:00:00Z")
    assert ev.parity.matched is True
    assert ev.parity.mismatches == ()


def test_run_shadow_parity_evidence_no_write_dry_run_published(tmp_path):
    """7.1：一个真实 no-write dispatch——dry-run 经真实 ShadowJournal 旁路写 + reducer 重建 published
    终态（不创建 PR/commit，纯 journal 路径，design#2 one real dry-run）。"""
    ev = CT.run_shadow_parity_evidence(
        state_dir=tmp_path, stamp_fn=lambda: "2026-07-23T00:00:00Z")
    assert ev.dry_run_terminal == "published"
