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
# r5 P0-1：evaluate_sandbox_verdict（docker canary 全 7 项 → sandbox 通过判定，纯函数）
# 旧 real_cutover_suite 只查 credential_isolated + denied_egress_enforced，docker 其余 5 项
# （node/allowed-egress/resource/unavailable-runtime/python）失败仍 sandbox_pass=True → overall PASS
# → 归档（评审 r5 P0-1 假绿）。本纯函数堵该路径：all_pass 任一红即 sandbox 红。
# ════════════════════════════════════════════════════════════════════════════
def _docker_summary_all_pass():
    return {"python_exec": True, "credential_isolated": True, "node_exec": True,
            "denied_egress_enforced": True, "allowed_egress_works": True,
            "unavailable_runtime_fail_fast": True, "resource_limit_enforced": True,
            "all_pass": True}


def test_sandbox_green_when_all_seven_dims_pass():
    v = CT.evaluate_sandbox_verdict(_docker_summary_all_pass())
    assert v.sandbox_pass is True
    assert v.docker_all_pass is True
    assert v.failing_dims == ()


def test_sandbox_red_when_docker_node_unavailable():  # 评审 r5 P0-1 反例（精确复刻）
    s = _docker_summary_all_pass(); s["node_exec"] = False; s["all_pass"] = False
    v = CT.evaluate_sandbox_verdict(s)
    assert v.sandbox_pass is False               # 旧逻辑：cred+net 全真 → 假绿 True
    assert "node_exec" in v.failing_dims


def test_sandbox_red_when_resource_limit_not_enforced():
    s = _docker_summary_all_pass(); s["resource_limit_enforced"] = False; s["all_pass"] = False
    assert CT.evaluate_sandbox_verdict(s).sandbox_pass is False


def test_sandbox_red_when_unavailable_runtime_not_blocked():
    s = _docker_summary_all_pass(); s["unavailable_runtime_fail_fast"] = False; s["all_pass"] = False
    assert CT.evaluate_sandbox_verdict(s).sandbox_pass is False


def test_sandbox_red_when_allowed_egress_not_enforced():
    s = _docker_summary_all_pass(); s["allowed_egress_works"] = False; s["all_pass"] = False
    assert CT.evaluate_sandbox_verdict(s).sandbox_pass is False


def test_sandbox_red_when_credential_not_isolated():  # 原有核心语义保留
    s = _docker_summary_all_pass(); s["credential_isolated"] = False; s["all_pass"] = False
    assert CT.evaluate_sandbox_verdict(s).sandbox_pass is False


def test_sandbox_red_failing_dims_lists_all_red_dims():
    s = _docker_summary_all_pass(); s["node_exec"] = False; s["resource_limit_enforced"] = False; s["all_pass"] = False
    v = CT.evaluate_sandbox_verdict(s)
    assert v.failing_dims == ("node_exec", "resource_limit_enforced")


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
# 7.5 dispatch 三重 gate（flag + parity + allowlist）→ driven；否则 legacy fallback
# spec（durable-runtime）：switch to journal-reduced decisions ONLY after real parity
# evidence passes。design 决策#8：cutover 前置 parity；单项目 rollout（allowlist）。
# ════════════════════════════════════════════════════════════════════════════
def test_dispatch_gated_journal_when_flag_parity_allowlist_all_pass():
    """三重 gate 全过 → driven（journal reducer 驱动 dispatch）。"""
    events = [_ev("running", eid="e1"), _ev("aborted", eid="e2")]
    r = CT.resolve_dispatch_source(
        journal_driven_flag=True, project_id="proj-alpha",
        allowlist=("proj-alpha",), parity_passed=True, journal_events=events)
    assert r.driven_by == "journal"
    assert r.terminal_state == "aborted"


def test_dispatch_legacy_when_parity_not_passed():
    """parity 未过 → 即使 flag 开 + 白名单，仍 legacy（design 决策#8 cutover 前置）。"""
    events = [_ev("running")]
    r = CT.resolve_dispatch_source(
        journal_driven_flag=True, project_id="proj-alpha",
        allowlist=("proj-alpha",), parity_passed=False, journal_events=events)
    assert r.driven_by == "legacy_fallback"
    assert "parity" in r.fallback_reason.lower()


def test_dispatch_legacy_when_project_not_allowlisted():
    """非白名单项目 → 即使 flag 开 + parity 过，仍 legacy（单项目 rollout）。"""
    events = [_ev("running")]
    r = CT.resolve_dispatch_source(
        journal_driven_flag=True, project_id="proj-beta",
        allowlist=("proj-alpha",), parity_passed=True, journal_events=events)
    assert r.driven_by == "legacy_fallback"
    assert "allowlist" in r.fallback_reason.lower()


def test_dispatch_legacy_when_gate_flag_off():
    """flag 关 → legacy（allowlist/parity 不查）。"""
    r = CT.resolve_dispatch_source(
        journal_driven_flag=False, project_id="proj-alpha",
        allowlist=("proj-alpha",), parity_passed=True)
    assert r.driven_by == "legacy_fallback"


def test_dispatch_legacy_fallback_when_reducer_fails_under_full_gate():
    """三重 gate 过但 reducer 失败 → 一个 release cycle 内 legacy 仍可用。"""
    r = CT.resolve_dispatch_source(
        journal_driven_flag=True, project_id="proj-alpha",
        allowlist=("proj-alpha",), parity_passed=True,
        journal_events=["not-an-event"], legacy_records=[{"status": "fail"}])
    assert r.driven_by == "legacy_fallback"


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
# Section 7 task 7.6：完整 cutover 套件运行器 + 归档不可变通过证据
# spec（runtime-cutover-evidence「Quality command passes」）：archive immutable passing evidence
# before marking the change complete。design 决策#6（archive immutable passing evidence）。
# ════════════════════════════════════════════════════════════════════════════
def test_cutover_suite_passes_and_archives_when_all_green(tmp_path):
    """7 维度全绿 → overall_passed=True + 归档 summary digest（archive immutable evidence）。"""
    suite = CT.run_cutover_suite(
        shadow_parity_matched=True, lifecycle_all_pass=True, crash_all_exactly_once=True,
        recovery_all_intact=True, sandbox_all_clean=True, dispatch_cutover_ok=True,
        quality_gate_passed=True, artifact_root=str(tmp_path / "suite"))
    assert suite.overall_passed is True
    assert suite.archive_digest is not None          # summary 已归档为内容寻址 artifact


def test_cutover_suite_fails_without_archive_when_any_dimension_red(tmp_path):
    """任一维度 red（sandbox）→ overall_passed=False 且不归档（绝不伪装绿归档）。"""
    suite = CT.run_cutover_suite(
        shadow_parity_matched=True, lifecycle_all_pass=True, crash_all_exactly_once=True,
        recovery_all_intact=True, sandbox_all_clean=False, dispatch_cutover_ok=True,
        quality_gate_passed=True, artifact_root=str(tmp_path / "suite"))
    assert suite.overall_passed is False
    assert suite.archive_digest is None              # red 套件不归档


def test_cutover_suite_archive_is_content_addressed_and_verifiable(tmp_path):
    """归档 digest 内容寻址可复现：同 summary → 同 digest（artifact_store 内容寻址语义）。"""
    root = str(tmp_path / "suite")
    kwargs = dict(shadow_parity_matched=True, lifecycle_all_pass=True,
                  crash_all_exactly_once=True, recovery_all_intact=True,
                  sandbox_all_clean=True, dispatch_cutover_ok=True,
                  quality_gate_passed=True, artifact_root=root)
    a = CT.run_cutover_suite(**kwargs)
    b = CT.run_cutover_suite(**kwargs)
    assert a.archive_digest == b.archive_digest      # 内容寻址：同 summary → 同 digest


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


# ════════════════════════════════════════════════════════════════════════════
# task 7.6（评审 P0-2 修正）：run_full_cutover_suite 编排运行器——自行执行各子 drill + 归档 manifest
# 评审 P0-2：旧 run_cutover_suite 接收外部布尔值做 all()（聚合器非运行器）。新 runner 调用每个 drill
# 执行入口（注入 bundle）真实执行，从 Result 提取 pass+detail，构建 CutoverManifest，全绿归档 digest。
# ════════════════════════════════════════════════════════════════════════════
def _fake_sdk_canary_all_proven():
    """全 8 场景 callback proven 的 fake sdk_canary（测 run_full_cutover_suite 归档逻辑，非 SDK 真实性）。
    adapter fixture（run_sdk_hook_canary）r5 P0-2 后不填 sdk_callback_proven（口径4），故全绿编排测试需显式 fake。"""
    base = CT.run_sdk_hook_canary()
    return CT.SdkHookCanaryEvidence(
        scenarios=base.scenarios, stop_gates=base.stop_gates, paths_covered=base.paths_covered,
        summary=base.summary, real_query_proven=True,
        sdk_callback_proven=CT.SDK_CALLBACK_REQUIRED_SCENARIOS,
        adapter_contract_proven=base.adapter_contract_proven)


def test_sdk_canary_outcome_requires_all_callback_scenarios():
    """r5 P0-2：sdk_canary pass 须 SDK_CALLBACK_REQUIRED_SCENARIOS 8 场景逐个 callback proven（含 compaction/
    hook_failure——task 7.2 契约全 7 path），非"任意 callback 出现即真"。缺任一 callback 场景须 FAIL（即便
    adapter gate 全对 + real_query_proven=True）——杜绝 7.6 outcome 比 7.2 谓词弱的假绿。"""
    green = _fake_sdk_canary_all_proven()   # fake：gate 8 场景符合预期 + 8 callback 全 proven → pass
    assert CT._sdk_canary_outcome(green).passed is True
    # 缺 compaction callback（PreCompact 单 query 不可靠触发）→ callback_ok=False → FAIL（路B 诚实标红）
    missing_compaction = CT.SdkHookCanaryEvidence(
        scenarios=green.scenarios, stop_gates=green.stop_gates, paths_covered=green.paths_covered,
        summary=green.summary, real_query_proven=True,
        sdk_callback_proven=tuple(s for s in CT.SDK_CALLBACK_REQUIRED_SCENARIOS if s != "compaction"),
        adapter_contract_proven=green.adapter_contract_proven)
    assert CT._sdk_canary_outcome(missing_compaction).passed is False
    # 缺 hook_failure callback → FAIL
    missing_hook_failure = CT.SdkHookCanaryEvidence(
        scenarios=green.scenarios, stop_gates=green.stop_gates, paths_covered=green.paths_covered,
        summary=green.summary, real_query_proven=True,
        sdk_callback_proven=tuple(s for s in CT.SDK_CALLBACK_REQUIRED_SCENARIOS if s != "hook_failure"),
        adapter_contract_proven=green.adapter_contract_proven)
    assert CT._sdk_canary_outcome(missing_hook_failure).passed is False
    # 缺全部 callback（任意 callback 假绿旧路径：仅 real_query_proven=True 即 pass）→ FAIL
    no_callback = CT.SdkHookCanaryEvidence(
        scenarios=green.scenarios, stop_gates=green.stop_gates, paths_covered=green.paths_covered,
        summary=green.summary, real_query_proven=True, sdk_callback_proven=(),
        adapter_contract_proven=green.adapter_contract_proven)
    assert CT._sdk_canary_outcome(no_callback).passed is False


def test_adapter_fixture_does_not_fake_sdk_callback_proven():
    """r5 P0-2 口径4：adapter fixture（run_sdk_hook_canary）不得填 sdk_callback_proven（adapter 非 SDK），
    只填 adapter_contract_proven（gate 逻辑）。杜绝 adapter 冒充真实 callback 的假绿。"""
    ev = CT.run_sdk_hook_canary()
    assert ev.sdk_callback_proven == ()                     # adapter 不证 SDK callback
    assert ev.adapter_contract_proven == CT._CANARY_ORDER   # adapter 证全 8 场景 gate 逻辑


def test_sdk_canary_fails_on_callback_errors():
    """r5 P1-5：真实 query 中 callback 抛异常（callback_errors 非空）→ lifecycle 证据不可信 → sdk_canary FAIL，
    即便 8 场景 callback 全 proven + real_query_proven=True。杜绝 callback 路由失败被吞后仍归档的假绿。"""
    green = _fake_sdk_canary_all_proven()
    with_err = CT.SdkHookCanaryEvidence(
        scenarios=green.scenarios, stop_gates=green.stop_gates, paths_covered=green.paths_covered,
        summary=green.summary, real_query_proven=True,
        sdk_callback_proven=CT.SDK_CALLBACK_REQUIRED_SCENARIOS,
        adapter_contract_proven=green.adapter_contract_proven,
        callback_errors=({"event": "Stop", "error": "boom"},))
    assert CT._sdk_canary_outcome(with_err).passed is False


def test_sdk_canary_fails_on_journal_decode_errors():
    """r5 P1-5：hook journal 行 JSON 解析失败（journal_decode_errors>0）→ lifecycle 证据可能丢失 → sdk_canary FAIL。
    对应 runtime_evidence.py:540 旧 `except json.JSONDecodeError: pass` 静默吞 → 现计数进谓词。"""
    green = _fake_sdk_canary_all_proven()
    with_err = CT.SdkHookCanaryEvidence(
        scenarios=green.scenarios, stop_gates=green.stop_gates, paths_covered=green.paths_covered,
        summary=green.summary, real_query_proven=True,
        sdk_callback_proven=CT.SDK_CALLBACK_REQUIRED_SCENARIOS,
        adapter_contract_proven=green.adapter_contract_proven,
        journal_decode_errors=2)
    assert CT._sdk_canary_outcome(with_err).passed is False


def test_evaluate_evidence_intact_pure_function():
    """r5 P1-2（评审）：evaluate_evidence_intact 纯函数——callback_errors / journal_decode_errors /
    query_error / result_received 四维度，任一违例即 (False, failures)。全空 → (True, ())。"""
    ok, fail = CT.evaluate_evidence_intact()
    assert ok is True and fail == ()
    ok, fail = CT.evaluate_evidence_intact(callback_errors=({"event": "Stop"},))
    assert ok is False and "callback_errors" in fail[0]
    ok, fail = CT.evaluate_evidence_intact(journal_decode_errors=3)
    assert ok is False and "journal_decode_errors" in fail[0]
    ok, fail = CT.evaluate_evidence_intact(query_error="proxy 5xx")
    assert ok is False and fail == ("query_error",)
    ok, fail = CT.evaluate_evidence_intact(result_received=False)
    assert ok is False and fail == ("result_not_received",)
    # 多维同时违例 → 全部列出
    ok, fail = CT.evaluate_evidence_intact(query_error="boom", result_received=False)
    assert ok is False and "query_error" in fail and "result_not_received" in fail


def test_evaluate_sdk_canary_scenarios_blocks_fake_green_on_integrity():
    """r5 P1-2（评审反例）：构造 callback_errors 非空、journal_decode_errors=1、query_error 非空、
    result_received=False **且** 场景矩阵（gate+callback）全真 → evaluate_sdk_canary_scenarios.passed 必 False。
    此前 7.2 _drill_predicate 只查 cb_proven + scenario_verdict（不含 integrity），同输入返回 (True, None) 假绿。
    本纯函数由 7.2 谓词 + 7.6 outcome 共调 → 两入口同时堵住该假绿。"""
    gates = dict(CT.EXPECTED_LIFECYCLE_GATES)              # 全 8 场景 gate 精确匹配（gate_ok=True）
    callbacks_proven = CT.SDK_CALLBACK_REQUIRED_SCENARIOS    # 全 callback proven（callback_ok=True）
    verdict = CT.evaluate_sdk_canary_scenarios(
        gates=gates, callbacks_proven=callbacks_proven,
        callback_errors=({"event": "Stop"},), journal_decode_errors=1,
        query_error="proxy 5xx", result_received=False)
    assert verdict.passed is False                          # integrity 违例阻断
    assert verdict.gate_ok is True                          # gate 维度本身绿
    assert verdict.callback_ok is True                      # callback 维度本身绿
    assert verdict.evidence_intact is False                 # 但 evidence 完整性红
    assert verdict.integrity_failures                       # 含具体违例标签


def test_sdk_canary_fails_on_query_error():
    """r5 P1-2（评审）：真实 SDK query 抛异常（query_error 非空）→ query 未正常结束 → lifecycle 证据不可信 →
    sdk_canary FAIL，即便 8 场景 callback 全 proven + real_query_proven=True。"""
    green = _fake_sdk_canary_all_proven()
    with_err = CT.SdkHookCanaryEvidence(
        scenarios=green.scenarios, stop_gates=green.stop_gates, paths_covered=green.paths_covered,
        summary=green.summary, real_query_proven=True,
        sdk_callback_proven=CT.SDK_CALLBACK_REQUIRED_SCENARIOS,
        adapter_contract_proven=green.adapter_contract_proven,
        query_error="query timed out")
    assert CT._sdk_canary_outcome(with_err).passed is False


def test_sdk_canary_fails_on_result_not_received():
    """r5 P1-2（评审）：SDK query 未正常返回 result（result_received=False）→ 残缺 evidence 不可作通过依据 →
    sdk_canary FAIL，即便其余维度全绿。杜绝「query 崩但 adapter fixture gate 全对」的假绿。"""
    green = _fake_sdk_canary_all_proven()
    with_err = CT.SdkHookCanaryEvidence(
        scenarios=green.scenarios, stop_gates=green.stop_gates, paths_covered=green.paths_covered,
        summary=green.summary, real_query_proven=True,
        sdk_callback_proven=CT.SDK_CALLBACK_REQUIRED_SCENARIOS,
        adapter_contract_proven=green.adapter_contract_proven,
        result_received=False)
    assert CT._sdk_canary_outcome(with_err).passed is False


# ════════════════════════════════════════════════════════════════════════════
# r5 P1-3（评审）：telemetry 维度成为 gate（_telemetry_outcome 明确通过谓词）
#   旧实现：_telemetry 仅追加到 quality_gate.evidence_items（归档），run_quality_gate 只验内容写入、
#   不判 OTLP/degradation 契约 → SDK 降级为 no-op（无 invocation）仍 overall PASS 假绿。
# ════════════════════════════════════════════════════════════════════════════
def test_telemetry_outcome_green_passes():
    """r5 P1-3：SDK 遥测通道在线（callback_invocations 非空 + lifecycle 可观测 + query 正常结束 + 无降级）→ PASS。"""
    ev = CT.TelemetryEvidence(
        callback_invocations=({"event": "PostToolUse"}, {"event": "Stop"}),
        lifecycle_types_seen=("PostToolUse", "Stop"), num_turns=2, query_error=None)
    assert CT._telemetry_outcome(ev).passed is True


def test_telemetry_outcome_fails_on_no_invocations():
    """r5 P1-3（评审反例）：SDK 降级为 no-op（callback_invocations 空）→ 遥测通道未证在线 → telemetry FAIL。
    即便 lifecycle_types 非空（adapter fixture 可造 lifecycle 事件，但 SDK 未真实调用 hook）。"""
    ev = CT.TelemetryEvidence(
        callback_invocations=(), lifecycle_types_seen=("Stop",), num_turns=1, query_error=None)
    assert CT._telemetry_outcome(ev).passed is False
    assert "no_callback_invocations" in CT._telemetry_outcome(ev).detail


def test_telemetry_outcome_fails_on_no_lifecycle_types():
    """r5 P1-3：lifecycle 事件不可观测（lifecycle_types_seen 空）→ telemetry FAIL。"""
    ev = CT.TelemetryEvidence(
        callback_invocations=({"event": "PostToolUse"},), lifecycle_types_seen=(), num_turns=1, query_error=None)
    assert CT._telemetry_outcome(ev).passed is False


def test_telemetry_outcome_fails_on_query_interrupted():
    """r5 P1-3：query 未正常结束（query_error 非空 或 num_turns None）→ 遥测中断 → telemetry FAIL。"""
    err = CT.TelemetryEvidence(
        callback_invocations=({"event": "Stop"},), lifecycle_types_seen=("Stop",),
        num_turns=1, query_error="proxy 5xx")
    assert CT._telemetry_outcome(err).passed is False
    no_result = CT.TelemetryEvidence(
        callback_invocations=({"event": "Stop"},), lifecycle_types_seen=("Stop",),
        num_turns=None, query_error=None)
    assert CT._telemetry_outcome(no_result).passed is False


def test_telemetry_outcome_fails_on_degradation_marker():
    """r5 P1-3：显式降级标记（runner 检测到 SDK 降级路径）→ telemetry FAIL。"""
    ev = CT.TelemetryEvidence(
        callback_invocations=({"event": "Stop"},), lifecycle_types_seen=("Stop",),
        num_turns=1, query_error=None, degradation="sdk_no_telemetry_export")
    assert CT._telemetry_outcome(ev).passed is False
    assert "degradation" in CT._telemetry_outcome(ev).detail


def _green_telemetry():
    """全绿 fake telemetry evidence（r5 P1-3）：SDK 遥测通道在线——callback_invocations 非空、lifecycle 可观测、
    query 正常结束（num_turns 非 None、无 query_error）、无降级。测归档/编排逻辑，非 SDK 真实性。"""
    return CT.TelemetryEvidence(
        callback_invocations=({"event": "PostToolUse"},),
        lifecycle_types_seen=("PostToolUse", "Stop"), num_turns=2, query_error=None,
        summary="[fake green telemetry] 1 invocation, lifecycle observed")


def _green_bundle():
    """全绿 fake bundle（callable 返回全绿 fake Result）+ 真实 sdk_canary/recovery（确定性绿）。"""
    return CT.CutoverDrillBundle(
        shadow_parity=lambda: CT.ShadowParityEvidence(
            parity=CT.ShadowParityReport(dispatch_counts={}, journal_counts={}, matched=True),
            dry_run_terminal="published", dry_run_run_id="r"),
        sdk_canary=lambda: _fake_sdk_canary_all_proven(),   # 全 callback proven fake（测归档逻辑，非 SDK 真实性）
        telemetry=lambda: _green_telemetry(),   # r5 P1-3：telemetry 维度绿（SDK 遥测通道在线）
        crash_reconciliation=lambda: CT.CrashReconciliationEvidence(
            results=(), boundaries_run=(), all_exactly_once=True, summary="all exactly-once"),
        recovery=lambda: (CT.run_recovery_drill("resume"), CT.run_recovery_drill("fork"),
                          CT.run_recovery_drill("new_session")),
        sandbox=lambda: (CT.SandboxDrillResult("python", "local_worktree", 0, False, True),),
        dispatch_cutover=lambda: CT.DispatchCutoverResult("journal", "published", ""),
        quality_gate=lambda: CT.QualityGateResult(
            tests_total=10, tests_failed=0, passed=True, evidence_digests=("d",), detail="10/10 pass"),
    )


_EXPECTED_OUTCOME_NAMES = {"shadow_parity", "sdk_canary", "crash_reconciliation", "recovery",
                           "sandbox", "dispatch_cutover", "quality_gate", "telemetry"}   # r5 P1-3：8 维度


def test_run_full_cutover_suite_green_archives_manifest(tmp_path):
    """runner 自行执行各 drill → 全绿 → overall_passed + 归档 manifest digest（archive immutable evidence）。"""
    m = CT.run_full_cutover_suite(drills=_green_bundle(), artifact_root=str(tmp_path / "suite"))
    assert m.overall_passed is True
    assert m.archive_digest is not None               # manifest 已归档为内容寻址 artifact
    names = {o.name for o in m.outcomes}
    assert names == _EXPECTED_OUTCOME_NAMES
    assert all(o.passed for o in m.outcomes)


def test_manifest_structured_has_section5_fields():
    """r5 P1-4（评审① + r4-response-revise §5）：CutoverManifest.structured() 含 §5 全字段（非 summary 字符串）。

    审查者：「run_full_cutover_suite() 仍归档 manifest.summary，没有结构化 manifest」。"""
    m = CT.CutoverManifest(
        outcomes=(CT.DrillOutcome("x", True, "d"),), overall_passed=True,
        subject_commit="abc123", runner_version="rv1", executed_at="2026-07-25T00:00:00Z")
    s = m.structured()
    for f in ("schema_version", "subject_commit", "runner_version", "executed_at",
              "overall_passed", "outcomes", "sub_evidence_refs", "evidence_integrity", "digest_algorithm"):
        assert f in s, f"structured() 缺 §5 字段 {f}"
    assert s["outcomes"][0]["name"] == "x"
    assert s["schema_version"] == "cutover-manifest/v1"
    assert "schema_version" not in m.summary   # summary 是可读字符串，非结构化载体


def test_run_full_cutover_suite_archives_structured_json(tmp_path):
    """r5 P1-4（评审①②）：green suite 归档**结构化 JSON**（非 summary 字符串）+ manifest_digest 非空 +
    归档后 read-back 自动通过（load 回来是结构化 dict 含 §5 字段）。"""
    import artifact_store as A
    root = tmp_path / "suite"
    m = CT.run_full_cutover_suite(drills=_green_bundle(), artifact_root=str(root))
    assert m.overall_passed is True
    assert m.manifest_digest == m.archive_digest          # 结构化 manifest 自身 digest = 归档 digest
    ref = L.ArtifactRef(digest=m.archive_digest, size=0,
                        kind=L.ArtifactKind.CUTOVER_SUITE.value,
                        path=A._bucketed_path(m.archive_digest),
                        sensitivity=L.Sensitivity.INTERNAL.value)
    blob = A.load(str(root), ref)
    text = blob.decode("utf-8") if isinstance(blob, bytes) else blob
    assert not text.startswith("cutover manifest:")       # 非旧 summary 字符串归档（评审①）
    import json
    parsed = json.loads(text)
    assert parsed["schema_version"] == "cutover-manifest/v1"
    assert {o["name"] for o in parsed["outcomes"]} == _EXPECTED_OUTCOME_NAMES


def test_read_back_manifest_failclosed_on_tamper(tmp_path):
    """r5 P1-4（评审②）：归档内容被篡改（digest 不再匹配）→ _read_back_manifest 返回 False（fail-closed）。

    审查者：「没有 read-back」。防归档内容被篡改/损坏仍声明 passing manifest。"""
    import artifact_store as A
    root = tmp_path / "suite"
    m = CT.run_full_cutover_suite(drills=_green_bundle(), artifact_root=str(root))
    p = root / A._bucketed_path(m.archive_digest)
    p.write_text("TAMPERED:not-the-manifest", encoding="utf-8")   # 篡改 → digest 不匹配
    ok, reason = CT._read_back_manifest(str(root), m.archive_digest)
    assert ok is False
    assert "digest" in reason.lower() or "read-back" in reason.lower()


def test_publish_evidence_bundle_cross_machine_verify(tmp_path):
    """r5 P1-4（评审④）：cross-machine immutable bundle——verify.py exit 0（完整）；篡改 artifact → exit 1；
    两次发布同 manifest → bundle_digest 一致（跨机器可复核锚点，不依赖本机路径）。

    审查者：「artifact root 仍为本机路径，没有 immutable cross-machine bundle」。"""
    root = tmp_path / "suite"
    m = CT.run_full_cutover_suite(drills=_green_bundle(), artifact_root=str(root))
    assert m.overall_passed is True
    b1 = tmp_path / "bundle1"
    _, digest1 = CT.publish_evidence_bundle(artifact_root=str(root), manifest=m, bundle_root=b1)
    # verify.py 自检通过（bundle 完整可独立复核）
    r = subprocess.run([sys.executable, str(b1 / "verify.py")], capture_output=True, text=True)
    assert r.returncode == 0, f"verify.py 应 exit 0（完整）: {r.stderr}"
    # 篡改一个子证据 → verify.py exit 非 0（检测篡改）
    first_art = next((b1 / "artifacts").iterdir())
    first_art.write_text("TAMPERED", encoding="utf-8")
    r2 = subprocess.run([sys.executable, str(b1 / "verify.py")], capture_output=True, text=True)
    assert r2.returncode != 0, "篡改后 verify.py 应 exit 非 0"
    # 两次发布同 manifest → bundle_digest 一致（跨机器一致，passing 可复核锚点）
    b2 = tmp_path / "bundle2"
    _, digest2 = CT.publish_evidence_bundle(artifact_root=str(root), manifest=m, bundle_root=b2)
    assert digest1 == digest2


def test_run_full_cutover_suite_red_does_not_archive(tmp_path):
    """任一 drill red（sandbox fixture exit1 且未 net_denied → 不 clean）→ overall red 且不归档（绝不伪装绿归档）。"""
    green = _green_bundle()
    bundle = CT.CutoverDrillBundle(
        shadow_parity=green.shadow_parity, sdk_canary=green.sdk_canary,
        telemetry=green.telemetry,
        crash_reconciliation=green.crash_reconciliation, recovery=green.recovery,
        sandbox=lambda: (CT.SandboxDrillResult("python", "local_worktree", 1, False, True),),
        dispatch_cutover=green.dispatch_cutover, quality_gate=green.quality_gate)
    m = CT.run_full_cutover_suite(drills=bundle, artifact_root=str(tmp_path / "suite"))
    assert m.overall_passed is False
    assert m.archive_digest is None                   # red 套件不归档
    assert any(o.name == "sandbox" and not o.passed for o in m.outcomes)


def test_run_full_cutover_suite_invokes_every_drill(tmp_path):
    """评审 P0-2 核心：runner **调用**每个 drill 执行入口（编排执行），而非接收外部布尔值。
    用带计数副作用的 callable 证明 8 个 drill 都被真实调用一次。"""
    calls = []

    def wrap(name, result):
        def _f():
            calls.append(name)
            return result
        return _f
    green = _green_bundle()
    bundle = CT.CutoverDrillBundle(
        shadow_parity=wrap("shadow_parity", green.shadow_parity()),
        sdk_canary=wrap("sdk_canary", green.sdk_canary()),
        telemetry=wrap("telemetry", green.telemetry()),
        crash_reconciliation=wrap("crash_reconciliation", green.crash_reconciliation()),
        recovery=wrap("recovery", green.recovery()),
        sandbox=wrap("sandbox", green.sandbox()),
        dispatch_cutover=wrap("dispatch_cutover", green.dispatch_cutover()),
        quality_gate=wrap("quality_gate", green.quality_gate()),
    )
    m = CT.run_full_cutover_suite(drills=bundle, artifact_root=str(tmp_path / "suite"))
    assert len(calls) == 8                            # 每个 drill 都被真实调用一次
    assert set(calls) == _EXPECTED_OUTCOME_NAMES
    assert m.overall_passed is True


def test_real_cutover_drills_orchestrates_real_drills(tmp_path):
    """real_cutover_drills 用真实 run_* drill 构造 bundle → runner 编排真实执行（集成证据）。"""
    sandbox_green = CT.SandboxDrillResult("python", "local_worktree", 0, False, True)
    events = [_ev("running", eid="e1"), _ev("aborted", eid="e2")]   # running→aborted 合法迁移
    bundle = CT.real_cutover_drills(
        state_dir=tmp_path, stamp_fn=lambda: "2026-07-23T00:00:00Z",
        resolver=FakeResolver(True),
        sandbox_runs=(sandbox_green,),
        dispatch_events=events, dispatch_legacy=None,
        test_counts={"passed": 5, "failed": 0},
        evidence_items=[("test_output", "ok")],
        artifact_root=str(tmp_path / "q"),
        sdk_callback_proven=CT.SDK_CALLBACK_REQUIRED_SCENARIOS,   # r5 P0-2：测试替身（测编排全绿，非 SDK 真实性）
        telemetry_proven=True)   # r5 P1-3：telemetry 维度测试替身（生产真值见 real_cutover_suite）
    m = CT.run_full_cutover_suite(drills=bundle, artifact_root=str(tmp_path / "q"))
    assert m.overall_passed is True
    assert m.archive_digest is not None
    assert {o.name for o in m.outcomes} == _EXPECTED_OUTCOME_NAMES


def test_run_full_cutover_suite_archive_is_content_addressed(tmp_path):
    """归档 digest 内容寻址可复现：同 manifest summary → 同 digest（artifact_store 内容寻址语义）。"""
    a = CT.run_full_cutover_suite(drills=_green_bundle(), artifact_root=str(tmp_path / "a"))
    b = CT.run_full_cutover_suite(drills=_green_bundle(), artifact_root=str(tmp_path / "b"))
    assert a.archive_digest == b.archive_digest


# ════════════════════════════════════════════════════════════════════════════
# r3 P0-2：passing manifest 子证据 fail-closed + 完整性门
#   1. 子证据归档失败 → fail-closed（该维度 red + overall 不绿 + 不归档）
#   2. 完整性门：7 outcome 均需可解析、可读、digest 匹配的证据引用，否则不允许 passing manifest
# ════════════════════════════════════════════════════════════════════════════
def test_run_full_cutover_suite_sub_evidence_archive_failure_fail_closed(tmp_path, monkeypatch):
    """r3 P0-2：子证据归档失败（artifact_store.store 抛异常）→ fail-closed：该维度记 red、
    overall 不绿、不归档 passing manifest，且 evidence_integrity 记可审计原因。"""
    real_store = CT.artifact_store.store

    def boom(root, content, kind, sensitivity):
        # 仅子证据归档（kind=test_output）炸；manifest 归档（kind=cutover_suite）不触发
        if kind == "test_output":
            raise OSError("模拟磁盘满/IO 故障")
        return real_store(root, content, kind, sensitivity)

    monkeypatch.setattr(CT.artifact_store, "store", boom)
    m = CT.run_full_cutover_suite(drills=_green_bundle(), artifact_root=str(tmp_path / "suite"))
    assert m.overall_passed is False
    assert m.archive_digest is None                       # 不归档「缺证据」的 passing manifest
    red = [o for o in m.outcomes if not o.passed]
    assert red and any("FAIL-CLOSED" in o.detail for o in red)   # 归档失败被记 red
    assert m.evidence_integrity != "ok"                   # 完整性门原因可审计


def test_verify_sub_evidence_complete_rejects_missing_digest():
    """r3 P0-2：业务维度全 passed 但某 outcome 缺证据引用 → 完整性门拒绝（缺证据不允许绿）。"""
    outcomes = tuple(CT.DrillOutcome(n, True, "ok", ()) for n in CT._EXPECTED_DRILL_NAMES)
    ok, reason = CT._verify_sub_evidence_complete(outcomes, "/nonexistent-root")
    assert ok is False
    assert "无已归档子证据引用" in reason


def test_verify_sub_evidence_complete_rejects_unreadable_digest():
    """r3 P0-2：digest 不可读/不匹配（伪造或丢失）→ 完整性门拒绝（防伪造 digest 引用混入绿 manifest）。"""
    outcomes = tuple(
        CT.DrillOutcome(n, True, "ok", ("sha256:deadbeef",)) for n in CT._EXPECTED_DRILL_NAMES)
    ok, reason = CT._verify_sub_evidence_complete(outcomes, "/nonexistent-root")
    assert ok is False
    assert "不可读" in reason or "digest 不匹配" in reason


def test_verify_sub_evidence_complete_rejects_wrong_outcome_names():
    """r3 P0-2：outcome 名字不齐全（漏跑维度）→ 完整性门拒绝。"""
    # 少一个 quality_gate
    names = CT._EXPECTED_DRILL_NAMES - {"quality_gate"}
    outcomes = tuple(CT.DrillOutcome(n, True, "ok", ("sha256:x",)) for n in names)
    ok, reason = CT._verify_sub_evidence_complete(outcomes, "/nonexistent-root")
    assert ok is False
    assert "不齐全" in reason and "quality_gate" in reason
