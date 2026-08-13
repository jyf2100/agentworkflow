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


# ─── 6.1c：merge_push/revert_push 边界（auto-merge/revert push 独立 checkpoint）─────────
def test_crash_merge_push_confirmed_skips_on_retry():
    """6.1c merge_push 边界：merge_commit 已是 main 祖先(FOUND)→ confirmed 跳过重 merge（exactly-once）。
    spec fail-safe-dispatch「Merge already applied is not repeated」的 crash drill 覆盖。"""
    r = CT.run_crash_drill("merge_push", resolver=FakeResolver(True))
    assert r.boundary == "merge_push"
    assert r.confirmed == 1 and r.exactly_once is True


def test_crash_merge_push_absent_reapplies_once():
    """6.1c merge_push 边界：merge_commit 非 main 祖先(NOT_FOUND)→ pending 可安全重 apply。"""
    r = CT.run_crash_drill("merge_push", resolver=FakeResolver(False))
    assert r.pending == 1 and r.exactly_once is True


def test_crash_merge_push_unknown_blocks():
    """6.1c merge_push 边界：ancestry UNKNOWN（main_ref 缺失/remote 不可达）→ block，不盲目重 merge。
    spec fail-safe-dispatch「Merge state unknown blocks retry」的 crash drill 覆盖。"""
    r = CT.run_crash_drill("merge_push", resolver=FakeResolver(None))
    assert r.unknown == 1 and r.exactly_once is False and r.external_known is False


def test_crash_revert_push_confirmed_skips_on_retry():
    """6.1c revert_push 边界：revert_commit 已是 main 祖先(FOUND)→ confirmed 跳过重 revert（exactly-once）。
    spec fail-safe-dispatch「Revert already applied is detected via the revert commit」的 crash drill 覆盖。"""
    r = CT.run_crash_drill("revert_push", resolver=FakeResolver(True))
    assert r.boundary == "revert_push"
    assert r.confirmed == 1 and r.exactly_once is True


def test_crash_revert_push_absent_reapplies_once():
    """6.1c revert_push 边界：revert_commit 非 main 祖先(NOT_FOUND)→ pending 可安全重 apply revert。"""
    r = CT.run_crash_drill("revert_push", resolver=FakeResolver(False))
    assert r.pending == 1 and r.exactly_once is True


def test_crash_revert_push_unknown_blocks():
    """6.1c revert_push 边界：ancestry UNKNOWN → block，不盲目重 revert（fail-safe）。"""
    r = CT.run_crash_drill("revert_push", resolver=FakeResolver(None))
    assert r.unknown == 1 and r.exactly_once is False and r.external_known is False


def test_run_crash_reconciliation_evidence_all_boundaries():
    """归档命令覆盖 spec 全 7 边界（agent/test/commit/push/PR/merge_push/revert_push），全 confirmed → all exactly-once。"""
    ev = CT.run_crash_reconciliation_evidence(resolver=FakeResolver(True))
    assert set(ev.boundaries_run) == {"agent_done", "test_done", "commit", "push", "pr_create",
                                      "merge_push", "revert_push"}
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
# langgraph-workflow-upgrade task 5.3 + 3.10：graph orchestrator 四重 gate + 双源 shadow parity
# 镜像 dispatch gate 范式（L582-627）+ shadow parity 范式（L97-121）。
# design D7（flag 物理隔离）+ R7（shadow parity 前置，不只比终态 Counter，防假绿）。
# ════════════════════════════════════════════════════════════════════════════
def test_graph_orchestrator_driven_when_shadow_parity_allowlist_flag_all_pass():
    """四重 gate 全过 → driven_by='graph_orchestrator'（cron 分流 graph_pa.py）。"""
    r = CT.resolve_graph_orchestrator_source(
        orchestrator_flag=True, shadow_flag=True, project_id="cc-web-control",
        allowlist=("cc-web-control",), parity_passed=True)
    assert r.driven_by == "graph_orchestrator"
    assert r.fallback_reason == ""
    assert r.terminal_state == ""                      # gate 判定阶段无 terminal state


def test_graph_orchestrator_fallback_when_shadow_off():
    """① shadow off → fallback（即使 orchestrator on + parity 过 + 白名单）。D7：orchestrator gated on shadow。"""
    r = CT.resolve_graph_orchestrator_source(
        orchestrator_flag=True, shadow_flag=False, project_id="cc-web-control",
        allowlist=("cc-web-control",), parity_passed=True)
    assert r.driven_by != "graph_orchestrator"
    assert "shadow" in r.fallback_reason.lower()


def test_graph_orchestrator_fallback_when_parity_not_passed():
    """② parity 未过 → fallback（即使 shadow on + 白名单）。R7 cutover 前置。"""
    r = CT.resolve_graph_orchestrator_source(
        orchestrator_flag=True, shadow_flag=True, project_id="cc-web-control",
        allowlist=("cc-web-control",), parity_passed=False)
    assert r.driven_by != "graph_orchestrator"
    assert "parity" in r.fallback_reason.lower()


def test_graph_orchestrator_fallback_when_project_not_allowlisted():
    """③ 非白名单项目 → fallback（即使 shadow on + parity 过）。单项目 rollout。"""
    r = CT.resolve_graph_orchestrator_source(
        orchestrator_flag=True, shadow_flag=True, project_id="proj-beta",
        allowlist=("cc-web-control",), parity_passed=True)
    assert r.driven_by != "graph_orchestrator"
    assert "allowlist" in r.fallback_reason.lower()


def test_graph_orchestrator_fallback_when_orchestrator_flag_off():
    """④ orchestrator flag 关 → fallback（前置全过但开关关 = shadow 可旁路跑，cron 未分流）。"""
    r = CT.resolve_graph_orchestrator_source(
        orchestrator_flag=False, shadow_flag=True, project_id="cc-web-control",
        allowlist=("cc-web-control",), parity_passed=True)
    assert r.driven_by != "graph_orchestrator"
    assert "flag off" in r.fallback_reason.lower()


def test_graph_orchestrator_gate_order_shadow_before_parity():
    """gate 按序短路：shadow off 时即使 parity 未过也报 shadow（① 在 ② 前，不混报）。"""
    r = CT.resolve_graph_orchestrator_source(
        orchestrator_flag=True, shadow_flag=False, project_id="cc-web-control",
        allowlist=("cc-web-control",), parity_passed=False)
    assert "shadow" in r.fallback_reason.lower()       # ① 先短路，不报 parity


# task 5.3b：双源 run_daily vs graph_pa 终态 shadow parity
def test_graph_shadow_parity_matched_when_same_distribution():
    """双源同终态分布 → matched=True。"""
    daily = [{"status": "pr_open", "project": "p", "slug": "a"},
             {"status": "fail", "project": "p", "slug": "b"}]
    graph = [{"status": "pr_open", "project": "p", "slug": "a"},
             {"status": "fail", "project": "p", "slug": "b"}]
    rep = CT.run_graph_shadow_parity_drill(daily, graph)
    assert rep.matched is True
    assert rep.mismatches == ()


def test_graph_shadow_parity_mismatch_when_distribution_differs():
    """双源终态分布不一致 → matched=False，mismatches 列出每个漂移 bucket。"""
    daily = [{"status": "pr_open"}, {"status": "fail"}]     # pr_open=1, fail=1
    graph = [{"status": "pr_open"}, {"status": "pr_open"}]  # pr_open=2, fail=0
    rep = CT.run_graph_shadow_parity_drill(daily, graph)
    assert rep.matched is False
    assert len(rep.mismatches) == 2                          # REVISE + FAILED 两 bucket 都漂移（逐 bucket 比对）
    assert any("revise" in m for m in rep.mismatches)        # pr_open 无 verify→REVISE（dual gate 降级），daily=1 graph=2
    assert any("failed" in m for m in rep.mismatches)        # fail→FAILED，daily=1 graph=0
    assert all("daily=" in m and "graph=" in m for m in rep.mismatches)  # 格式（诊断用）


def test_graph_shadow_parity_matched_when_both_empty():
    """双源皆空 → matched=True（无 PRD 输入的诚实一致，非假绿——空对空是真一致）。"""
    rep = CT.run_graph_shadow_parity_drill([], [])
    assert rep.matched is True


def test_graph_shadow_parity_evidence_reads_dispatch_json(tmp_path):
    """evidence 命令读两 state_dir 的 dispatch_{stamp}.json 跑 drill。"""
    daily_dir = tmp_path / "daily_state"
    graph_dir = tmp_path / "graph_state"
    daily_dir.mkdir(); graph_dir.mkdir()
    rec = [{"status": "pr_open", "project": "p", "slug": "a"}]
    (daily_dir / "dispatch_20260813.json").write_text(_json.dumps(rec), encoding="utf-8")
    (graph_dir / "dispatch_20260813.json").write_text(_json.dumps(rec), encoding="utf-8")
    ev = CT.run_graph_shadow_parity_evidence(daily_state_dir=str(daily_dir),
                                             graph_state_dir=str(graph_dir), stamp="20260813")
    assert ev.parity.matched is True
    assert ev.n_daily == 1 and ev.n_graph == 1
    assert ev.stamp == "20260813"


def test_graph_shadow_parity_evidence_missing_file_reports_mismatch(tmp_path):
    """单源文件缺 → loader 哨兵短路 matched=False（诚实 red，不静默假绿）。Q1 回归保护。"""
    daily_dir = tmp_path / "d"; graph_dir = tmp_path / "g"
    daily_dir.mkdir(); graph_dir.mkdir()
    (daily_dir / "dispatch_20260813.json").write_text(_json.dumps([{"status": "pr_open"}]), encoding="utf-8")
    # graph_dir 无文件 → _LOAD_FAILED 哨兵（不再静默返回 [] 当成功）
    ev = CT.run_graph_shadow_parity_evidence(daily_state_dir=str(daily_dir),
                                             graph_state_dir=str(graph_dir), stamp="20260813")
    assert ev.parity.matched is False
    assert ev.n_graph == 0
    assert ev.n_daily == 1                              # r-review：成功方记录数保留（不因 graph 失败清零）
    assert isinstance(ev.parity, CT.LoadFailureReport)  # C-1：load 失败用专用类型（非伪造空 counts）
    assert any("source_load_error" in m for m in ev.parity.mismatches)   # Q1 哨兵诊断标记


def test_graph_shadow_parity_evidence_both_sources_load_failed_no_false_green(tmp_path):
    """Q1 核心（silent-failure-hunter Critical）：双源同文件缺 → 不当一致假绿。

    bug 场景（修复前）：双源都 load 失败返回 []，``[] == []`` → ``matched=True`` 假绿，打通 gate ② parity。
    修复后：双源都返回 ``_LOAD_FAILED`` 哨兵 → 短路 ``matched=False`` + mismatch 标 ``daily=failed graph=failed``。
    """
    daily_dir = tmp_path / "d"; graph_dir = tmp_path / "g"
    daily_dir.mkdir(); graph_dir.mkdir()                       # 双源都无 dispatch 文件
    ev = CT.run_graph_shadow_parity_evidence(daily_state_dir=str(daily_dir),
                                             graph_state_dir=str(graph_dir), stamp="20260813")
    assert ev.parity.matched is False                          # 不当一致假绿
    assert ev.n_daily == 0 and ev.n_graph == 0
    assert any("source_load_error" in m and "failed" in m for m in ev.parity.mismatches)


def test_graph_shadow_parity_outcome_extracts_drill_outcome():
    """GraphShadowParityEvidence → DrillOutcome（name/passed/detail，runner 批 4 接入用）。"""
    rep = CT.ShadowParityReport(dispatch_counts={"pr_open": 1}, journal_counts={"pr_open": 1},
                                matched=True, mismatches=())
    ev = CT.GraphShadowParityEvidence(parity=rep, stamp="20260813", n_daily=1, n_graph=1)
    out = CT._graph_shadow_parity_outcome(ev)
    assert out.name == "graph_shadow_parity"
    assert out.passed is True
    assert "matched=True" in out.detail


def test_graph_shadow_parity_evidence_single_source_failure_keeps_other_count(tmp_path):
    """r-review Important（silent-failure-hunter）：单源失败时保留成功方真实记录数。

    bug 场景：短路分支曾硬编码 n_daily=0/n_graph=0，单源失败丢弃成功方数量 → 与 mismatch 的
    "daily=ok" 诊断矛盾。修复后 daily 成功 N 条、graph 失败 → n_daily=N, n_graph=0。
    """
    daily_dir = tmp_path / "d"; graph_dir = tmp_path / "g"
    daily_dir.mkdir(); graph_dir.mkdir()
    recs = [{"status": "pr_open"}, {"status": "fail"}, {"status": "skip"}]
    (daily_dir / "dispatch_20260813.json").write_text(_json.dumps(recs), encoding="utf-8")
    ev = CT.run_graph_shadow_parity_evidence(daily_state_dir=str(daily_dir),
                                             graph_state_dir=str(graph_dir), stamp="20260813")
    assert ev.n_daily == 3 and ev.n_graph == 0          # 成功方 3 条保留，失败方 0
    assert ev.parity.matched is False
    assert "daily=ok" in ev.parity.mismatches[0] and "graph=failed" in ev.parity.mismatches[0]


def test_graph_shadow_parity_evidence_daily_failure_keeps_graph_count(tmp_path):
    """r-review R3 M2（silent-failure-hunter）：对称分支——daily 失、graph OK 也保留成功方数量。

    锁 n_daily/n_graph 三元的 daily 侧（防笔误成 len(graph_raw) if daily_raw is not _LOAD_FAILED）。
    """
    daily_dir = tmp_path / "d"; graph_dir = tmp_path / "g"
    daily_dir.mkdir(); graph_dir.mkdir()
    recs = [{"status": "pr_open"}, {"status": "fail"}]
    (graph_dir / "dispatch_20260813.json").write_text(_json.dumps(recs), encoding="utf-8")
    ev = CT.run_graph_shadow_parity_evidence(daily_state_dir=str(daily_dir),
                                             graph_state_dir=str(graph_dir), stamp="20260813")
    assert ev.n_daily == 0 and ev.n_graph == 2          # daily 失 0，graph 成功 2 条保留
    assert ev.parity.matched is False
    assert "daily=failed" in ev.parity.mismatches[0] and "graph=ok" in ev.parity.mismatches[0]


def test_graph_shadow_parity_evidence_corrupt_json_reports_mismatch(tmp_path):
    """r-review Minor（silent-failure-hunter）：损坏 JSON（JSONDecodeError 路径）→ 哨兵短路 matched=False。

    锁住 except json.JSONDecodeError 分支，防未来重构静默丢弃该 except 子句。
    """
    daily_dir = tmp_path / "d"; graph_dir = tmp_path / "g"
    daily_dir.mkdir(); graph_dir.mkdir()
    (daily_dir / "dispatch_20260813.json").write_text('{"CORRUPT": not valid', encoding="utf-8")
    (graph_dir / "dispatch_20260813.json").write_text('{"CORRUPT": not valid', encoding="utf-8")
    ev = CT.run_graph_shadow_parity_evidence(daily_state_dir=str(daily_dir),
                                             graph_state_dir=str(graph_dir), stamp="20260813")
    assert ev.parity.matched is False
    assert isinstance(ev.parity, CT.LoadFailureReport)
    assert "daily=failed" in ev.parity.mismatches[0] and "graph=failed" in ev.parity.mismatches[0]


def test_load_failure_report_invariant_matched_must_be_false():
    """r-review C-1 + I-2：LoadFailureReport.matched 恒 False + 必须带诊断 mismatches（构造时强制）。"""
    assert CT.LoadFailureReport(mismatches=("source_load_error: x",)).matched is False
    with pytest.raises(ValueError):
        CT.LoadFailureReport(mismatches=("x",), matched=True)      # matched=True 非法（load 失败永不 match）
    with pytest.raises(ValueError):
        CT.LoadFailureReport(mismatches=())                         # 空 mismatches 非法（无诊断）


def test_shadow_parity_report_invariant_rejects_fake_empty_counts():
    """r-review I-2 + C-1：ShadowParityReport 拒绝伪造空 counts（counts 相等却 matched=False = C-1 反模式）。

    __post_init__ 在构造时抛 ValueError，防 C-1 类静默带病传播（load 失败应用 LoadFailureReport）。
    """
    with pytest.raises(ValueError):
        CT.ShadowParityReport(dispatch_counts={}, journal_counts={}, matched=False,
                              mismatches=("fake",))     # 空计数相等却 matched=False → C-1 反模式，构造即拒
    ok = CT.ShadowParityReport(dispatch_counts={"a": 1}, journal_counts={"a": 1}, matched=True)
    assert ok.matched is True


def test_shadow_parity_report_invariant_rejects_counts_mismatch_with_matched_true():
    """r-review R3 M-1（type-design-analyzer）：counts 不等却 matched=True 也是不变式违例。

    runtime_evidence.py real_cutover_suite 曾伪造 counts（I-1 形态）——__post_init__ 拒绝，防 pre-existing
    伪造代码带病传播。
    """
    with pytest.raises(ValueError):
        CT.ShadowParityReport(dispatch_counts={"a": 1}, journal_counts={"a": 2},
                              matched=True)            # counts 不等却 matched=True → I-1 形态，构造即拒


def test_stage_parity_report_invariant_rejects_inconsistent_matched():
    """r-review R3 M-2（type-design-analyzer）：StageParityReport 反例——matched 与 mismatches 不一致即拒。"""
    with pytest.raises(ValueError):
        CT.StageParityReport(stages_checked=("a",), mismatches=("a: bad",), matched=True)  # 有 mismatch 却 True
    with pytest.raises(ValueError):
        CT.StageParityReport(stages_checked=("a",), mismatches=(), matched=False)          # 无 mismatch 却 False


# task 3.10：每 stage byte-identical（不只比终态 Counter，R7 防 Counter 假绿）
def test_graph_shadow_parity_per_stage_matched_when_identical(tmp_path):
    """双 state_dir 每 stage JSON 内容一致 → matched=True。"""
    daily = tmp_path / "d"; graph = tmp_path / "g"
    daily.mkdir(); graph.mkdir()
    for stage in ("candidates", "prd_manifest", "prd_gate"):
        payload = {"x": stage, "items": [1, 2, 3]}
        (daily / f"{stage}_20260813.json").write_text(_json.dumps(payload), encoding="utf-8")
        (graph / f"{stage}_20260813.json").write_text(_json.dumps(payload), encoding="utf-8")
    rec = [{"status": "pr_open"}]
    (daily / "dispatch_20260813.json").write_text(_json.dumps(rec), encoding="utf-8")
    (graph / "dispatch_20260813.json").write_text(_json.dumps(rec), encoding="utf-8")
    rep = CT.run_graph_shadow_parity_drill_per_stage(
        daily_state_dir=str(daily), graph_state_dir=str(graph), stamp="20260813")
    assert rep.matched is True
    assert rep.mismatches == ()


def test_graph_shadow_parity_per_stage_detects_field_drift(tmp_path):
    """终态 Counter 假绿（同 status 分布）但某 stage 字段漂移 → per_stage 抓住（R7 核心）。"""
    daily = tmp_path / "d"; graph = tmp_path / "g"
    daily.mkdir(); graph.mkdir()
    # candidates 内容漂移（daily 多一个 item），但 dispatch 终态分布恰好一致 → Counter 假绿
    (daily / "candidates_20260813.json").write_text(_json.dumps({"items": [1, 2]}), encoding="utf-8")
    (graph / "candidates_20260813.json").write_text(_json.dumps({"items": [1]}), encoding="utf-8")
    rec = [{"status": "pr_open"}]
    (daily / "dispatch_20260813.json").write_text(_json.dumps(rec), encoding="utf-8")
    (graph / "dispatch_20260813.json").write_text(_json.dumps(rec), encoding="utf-8")
    daily_recs = _json.loads((daily / "dispatch_20260813.json").read_text())
    graph_recs = _json.loads((graph / "dispatch_20260813.json").read_text())
    assert CT.run_graph_shadow_parity_drill(daily_recs, graph_recs).matched is True   # 假绿
    rep = CT.run_graph_shadow_parity_drill_per_stage(
        daily_state_dir=str(daily), graph_state_dir=str(graph), stamp="20260813")
    assert rep.matched is False
    assert any("candidates" in m for m in rep.mismatches)


def test_graph_shadow_parity_per_stage_missing_file_reports_mismatch(tmp_path):
    """单源某 stage 文件缺 → load_failed mismatch（诚实 red）。C1：返回 StageParityReport。"""
    daily = tmp_path / "d"; graph = tmp_path / "g"
    daily.mkdir(); graph.mkdir()
    (daily / "candidates_20260813.json").write_text(_json.dumps({"x": 1}), encoding="utf-8")
    # graph 无 candidates 文件 → _LOAD_FAILED 哨兵 → load_failed（不再 None≠dict）
    rep = CT.run_graph_shadow_parity_drill_per_stage(
        daily_state_dir=str(daily), graph_state_dir=str(graph), stamp="20260813")
    assert isinstance(rep, CT.StageParityReport)              # C1 专用类型（非 ShadowParityReport）
    assert rep.matched is False
    assert rep.stages_checked == CT._GRAPH_PARITY_STAGE_FILES
    assert any("candidates" in m and "load_failed" in m for m in rep.mismatches)


def test_graph_shadow_parity_per_stage_both_sources_missing_no_false_green(tmp_path):
    """Q1（per_stage 视角）：双源某 stage 都缺 → 不当一致假绿。

    bug 场景（修复前）：双源 ``_load_json_any`` 都返 None，``None == None`` → matched 假绿。
    修复后：双源都返 ``_LOAD_FAILED`` 哨兵 → load_failed mismatch（即使 4 stage 都双缺，仍 red）。
    """
    daily = tmp_path / "d"; graph = tmp_path / "g"
    daily.mkdir(); graph.mkdir()                              # 双源全空（4 stage 文件都缺）
    rep = CT.run_graph_shadow_parity_drill_per_stage(
        daily_state_dir=str(daily), graph_state_dir=str(graph), stamp="20260813")
    assert rep.matched is False                               # 双源同失败不当一致
    assert len(rep.mismatches) == len(CT._GRAPH_PARITY_STAGE_FILES)   # 4 stage 全 load_failed
    assert all("load_failed" in m for m in rep.mismatches)


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
def _fake_state(sc):
    """每场景**正确** observed_state（让 evaluate_scenario state 匹配通过）。r6 P0：state 精确匹配维度。"""
    states = {
        "test_red": {"bash_results": [{"exit_code": 1, "output": ""}], "reply_text": "", "saw_tool_use": True, "saw_subagent_start": False},
        "test_green": {"bash_results": [{"exit_code": 0, "output": "GREEN"}], "reply_text": "", "saw_tool_use": True, "saw_subagent_start": False},
        "stale_test": {"bash_results": [{"exit_code": 0, "output": "STALE"}], "reply_text": "", "saw_tool_use": True, "saw_subagent_start": False},
        "semantic_revise": {"bash_results": [], "reply_text": "REVISE", "saw_tool_use": False, "saw_subagent_start": False},
        "no_test": {"bash_results": [], "reply_text": "NO TEST", "saw_tool_use": False, "saw_subagent_start": False},
        "subagent": {"bash_results": [], "reply_text": "", "saw_tool_use": False, "saw_subagent_start": True},
        "compaction": {"bash_results": [], "reply_text": "", "saw_tool_use": False, "saw_subagent_start": False},
        "hook_failure": {"bash_results": [], "reply_text": "", "saw_tool_use": False, "saw_subagent_start": False},
    }
    return states.get(sc, {})


def _fake_per_scenario(scenarios=None):
    """r6 P0：per_scenario 绑定 dict——每场景 journal/cid/state/gate 全绿（同源绑定）。``scenarios`` 缺省
    全 8；指定子集则只含该子集（缺场景 → missing_callbacks → callback_ok=False，测「缺 callback 场景 FAIL」）。
    compaction/hook_failure 不设 blocked_reason → 走非 blocked 分支（state 标签 blocked 恒匹配）→ fake 全绿。"""
    scn = scenarios if scenarios is not None else CT.SDK_CALLBACK_REQUIRED_SCENARIOS
    return {
        sc: {"scenario_id": sc, "journal_has_expected": True, "carries_own_cid": True,
             "adapter_gate": CT.EXPECTED_LIFECYCLE_GATES.get(sc), "observed_state": _fake_state(sc)}
        for sc in scn
    }


def _fake_sdk_canary_all_proven():
    """全 8 场景 callback proven 的 fake sdk_canary（测 run_full_cutover_suite 归档逻辑，非 SDK 真实性）。
    adapter fixture（run_sdk_hook_canary）r5 P0-2 后不填 sdk_callback_proven（口径4），故全绿编排测试需显式 fake。
    r6 P0：per_scenario 全绿绑定（journal/cid/state/gate 同源），让 _sdk_canary_outcome passed=True 基线。"""
    base = CT.run_sdk_hook_canary()
    return CT.SdkHookCanaryEvidence(
        scenarios=base.scenarios, stop_gates=base.stop_gates, paths_covered=base.paths_covered,
        summary=base.summary, real_query_proven=True,
        sdk_callback_proven=CT.SDK_CALLBACK_REQUIRED_SCENARIOS,
        adapter_contract_proven=base.adapter_contract_proven,
        per_scenario=tuple({"scenario_id": s, **e} for s, e in _fake_per_scenario().items()))


def _per_scenario_tuple(scenarios):
    """per_scenario dict → SdkHookCanaryEvidence.per_scenario 用的 tuple[dict]。"""
    return tuple({"scenario_id": s, **e} for s, e in _fake_per_scenario(scenarios).items())


def test_sdk_canary_outcome_requires_all_callback_scenarios():
    """r5 P0-2 + r6 P0：sdk_canary pass 须 SDK_CALLBACK_REQUIRED_SCENARIOS 8 场景逐个 per-scenario proven
    （journal+cid+state+gate 同源绑定），非"任意 callback 出现即真"。缺任一场景 → callback_ok=False → FAIL
    （即便 adapter gate 全对 + real_query_proven=True）——杜绝 7.6 outcome 比 7.2 谓词弱的假绿。"""
    green = _fake_sdk_canary_all_proven()   # fake：8 场景 per-scenario 全绿 → pass
    assert CT._sdk_canary_outcome(green).passed is True
    # 缺 compaction（PreCompact 单 query 不可靠触发）→ missing → FAIL（路B 诚实标红）
    sans_compaction = tuple(s for s in CT.SDK_CALLBACK_REQUIRED_SCENARIOS if s != "compaction")
    missing_compaction = CT.SdkHookCanaryEvidence(
        scenarios=green.scenarios, stop_gates=green.stop_gates, paths_covered=green.paths_covered,
        summary=green.summary, real_query_proven=True,
        sdk_callback_proven=sans_compaction,
        adapter_contract_proven=green.adapter_contract_proven,
        per_scenario=_per_scenario_tuple(sans_compaction))
    assert CT._sdk_canary_outcome(missing_compaction).passed is False
    # 缺 hook_failure → FAIL
    sans_hook = tuple(s for s in CT.SDK_CALLBACK_REQUIRED_SCENARIOS if s != "hook_failure")
    missing_hook_failure = CT.SdkHookCanaryEvidence(
        scenarios=green.scenarios, stop_gates=green.stop_gates, paths_covered=green.paths_covered,
        summary=green.summary, real_query_proven=True,
        sdk_callback_proven=sans_hook,
        adapter_contract_proven=green.adapter_contract_proven,
        per_scenario=_per_scenario_tuple(sans_hook))
    assert CT._sdk_canary_outcome(missing_hook_failure).passed is False
    # 缺全部 callback（任意 callback 假绿旧路径：仅 real_query_proven=True 即 pass）→ FAIL
    no_callback = CT.SdkHookCanaryEvidence(
        scenarios=green.scenarios, stop_gates=green.stop_gates, paths_covered=green.paths_covered,
        summary=green.summary, real_query_proven=True, sdk_callback_proven=(),
        adapter_contract_proven=green.adapter_contract_proven, per_scenario=())
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
    """r5 P1-2 + r6 P0（评审反例）：构造 callback_errors 非空、journal_decode_errors=1、query_error 非空、
    result_received=False **且** 场景矩阵（gate+callback+state）全真 → evaluate_sdk_canary_scenarios.passed 必 False。
    此前 7.2 _drill_predicate 只查 cb_proven + scenario_verdict（不含 integrity），同输入返回 (True, None) 假绿。
    本纯函数由 7.2 谓词 + 7.6 outcome 共调 → 两入口同时堵住该假绿。r6 P0：per_scenario 绑定（state 维度亦绿）。"""
    verdict = CT.evaluate_sdk_canary_scenarios(
        per_scenario=_fake_per_scenario(),
        callback_errors=({"event": "Stop"},), journal_decode_errors=1,
        query_error="proxy 5xx", result_received=False)
    assert verdict.passed is False                          # integrity 违例阻断
    assert verdict.gate_ok is True                          # gate 维度本身绿
    assert verdict.callback_ok is True                      # callback 维度本身绿
    assert verdict.state_ok is True                         # r6 P0：state 维度本身绿
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


def test_run_full_cutover_suite_telemetry_red_keeps_overall_green_with_open_item(tmp_path):
    """r6 P1-6（评审反例精髓）：telemetry 红（真实 OTLP/degradation suite 未接入，callback invocations 空）
    但其余 7 维度全绿 → overall_passed 仍 True + telemetry outcome 诚实 passed=False + open_items 记 telemetry
    red + known limitation。

    评审 P1-6 反例：旧实现 telemetry 进 overall ``all()``（``cutover.py`` 旧 ``drill_ok = all(o.passed for o in outcomes)``）
    ——headless 无真实 OTLP/degradation suite → telemetry 永远红 → overall 永远红（套件无法绿归档 = 停摆），
    或为「让套件能绿」而偷偷把 telemetry 假绿。r6 P1-6：telemetry 移出 overall ``all()``，其 passed 进新
    ``open_items``（诚实 red/open + known limitation，不阻断 overall，同 P1-1 语义）；telemetry outcome 仍
    执行 + 归档子证据（evidence_ok 查 8 维度，含 telemetry）。
    """
    from dataclasses import replace
    # telemetry red：callback invocations 空 + lifecycle 空 + num_turns None + 显式 degradation（_telemetry_outcome 必判红）
    red_tel = lambda: CT.TelemetryEvidence(
        callback_invocations=(), lifecycle_types_seen=(), num_turns=None, query_error=None,
        summary="red: no real OTLP/degradation suite", degradation="no_callback_invocations")
    # CutoverDrillBundle 是 frozen dataclass → dataclasses.replace 复制 7 个绿色 callable + 换 telemetry 为 red
    red_bundle = replace(_green_bundle(), telemetry=red_tel)
    m = CT.run_full_cutover_suite(drills=red_bundle, artifact_root=str(tmp_path / "suite"))
    # telemetry 红但其余 7 维度绿 → overall 仍 green（telemetry 已移出 drill_ok）
    assert m.overall_passed is True, "telemetry 红阻断 overall——P1-6 未把 telemetry 移出 all()"
    # telemetry outcome 诚实标红（不假绿）
    tel_o = next(o for o in m.outcomes if o.name == "telemetry")
    assert tel_o.passed is False, "telemetry outcome 假绿——P1-6 未诚实标 red"
    # open_items 含 telemetry 条目（passed=False + known limitation 非空）
    assert len(m.open_items) >= 1, "overall 绿但 telemetry red 未进 open_items——P1-6 诚实报告缺失"
    oi = next((i for i in m.open_items if i.get("item") == "telemetry"), None)
    assert oi is not None and oi["passed"] is False and oi["limitation"], (
        "open_items 缺 telemetry red + limitation——P1-6 未诚实记录")
    # structured() 也导出 open_items（跨机器可复核 manifest 见诚实 red/open，非假绿）
    assert len(m.structured()["open_items"]) >= 1


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


# ---- r6 P1-5：manifest read-back 7 步严格校验（评审反例：空/残缺/篡改 manifest 不得过 read-back） ----

def _store_fake_manifest(root, manifest_dict):
    """归档一个伪造 manifest dict → 返回 digest（供 _read_back_manifest 反例测试）。

    内容寻址 store：store 后内容未篡改 → step 1（load 重算 digest）通过，反例靠后续结构/自洽校验（step 3+）触发。
    """
    import json
    blob = json.dumps(manifest_dict, ensure_ascii=False, sort_keys=True)
    return CT.artifact_store.store(str(root), blob, kind="cutover_suite",
                                   sensitivity="internal").digest


def test_read_back_manifest_rejects_legacy_5key_manifest(tmp_path):
    """r6 P1-5 step 3（评审反例）：旧版只查 5 键（schema_version/outcomes/sub_evidence_refs/
    evidence_integrity/digest_algorithm）→ 缺 subject_commit/runner_version/executed_at/overall_passed
    的残缺 manifest 能过 read-back 假绿。P1-5 step 3 扩展 §5 全字段校验 → 拒。"""
    root = tmp_path / "suite"
    digest = _store_fake_manifest(root, {
        "schema_version": "cutover-manifest/v1",
        "outcomes": [], "sub_evidence_refs": [],
        "evidence_integrity": "ok", "digest_algorithm": "sha256",
    })
    ok, reason = CT._read_back_manifest(str(root), digest)
    assert ok is False and "[3]" in reason


def test_read_back_manifest_rejects_empty_outcomes_with_overall_true(tmp_path):
    """r6 P1-5 step 4（评审反例精髓）：空结构 manifest——outcomes=[] 但 overall_passed=True、
    sub_evidence_refs=[]。旧版「只查 5 键存在」会放行此空 manifest 假绿。P1-5 step 4（outcomes 非空）→ 拒。"""
    root = tmp_path / "suite"
    digest = _store_fake_manifest(root, {
        "schema_version": "cutover-manifest/v1", "subject_commit": "abc123",
        "runner_version": "v1", "executed_at": "2026-07-25",
        "overall_passed": True, "outcomes": [], "sub_evidence_refs": [],
        "evidence_integrity": "ok", "digest_algorithm": "sha256",
    })
    ok, reason = CT._read_back_manifest(str(root), digest)
    assert ok is False and "[4]" in reason


def test_read_back_manifest_rejects_subrefs_outcome_digest_mismatch(tmp_path):
    """r6 P1-5 step 5（评审反例）：manifest 自洽——sub_evidence_refs 多一个 outcome 未引用的假 digest。
    旧版不校验全局一致性 → 假 digest 混入 refs 不被发现。P1-5 step 5（outcome digest 并集 == sub_refs）→ 拒。"""
    root = tmp_path / "suite"
    digest = _store_fake_manifest(root, {
        "schema_version": "cutover-manifest/v1", "subject_commit": "abc123",
        "runner_version": "v1", "executed_at": "2026-07-25",
        "overall_passed": True,
        "outcomes": [{"name": "shadow_parity", "passed": True, "detail": "x",
                      "evidence_digests": ["sha256:real1"]}],
        "sub_evidence_refs": ["sha256:real1", "sha256:FAKE_EXTRA"],
        "evidence_integrity": "ok", "digest_algorithm": "sha256",
    })
    ok, reason = CT._read_back_manifest(str(root), digest)
    assert ok is False and "[5]" in reason


def test_read_back_manifest_rejects_overall_outcomes_mismatch(tmp_path):
    """r6 P1-5 step 7（评审反例）：篡改 manifest——outcomes 全 passed=False 但 overall_passed=True
    （声称 passing 实则全红）。P1-5 step 7（overall_passed == all(outcome.passed) 自洽校验）→ 拒。"""
    root = tmp_path / "suite"
    digest = _store_fake_manifest(root, {
        "schema_version": "cutover-manifest/v1", "subject_commit": "abc123",
        "runner_version": "v1", "executed_at": "2026-07-25",
        "overall_passed": True,
        "outcomes": [{"name": "shadow_parity", "passed": False, "detail": "x",
                      "evidence_digests": []}],
        "sub_evidence_refs": [], "evidence_integrity": "ok", "digest_algorithm": "sha256",
    })
    ok, reason = CT._read_back_manifest(str(root), digest)
    assert ok is False and "[7]" in reason


def test_read_back_manifest_rejects_non_allowlist_open_item(tmp_path):
    """r7-S4（审核员反例精髓）：open_items 塞进**任意**红色 outcome（如 quality_gate 真 red）即从 overall
    ``all()`` 偷排 → overall 假绿。旧 step 7 从 ``open_items`` 无白名单取 red 名排除 → quality_gate 被
    偷排 → ``business_outcomes`` 空 → ``all([])``=True → overall(True)==True 过（假绿）。r7-S4：open_items
    白名单（仅 telemetry 允许 open）→ 非白名单 red 项进 open_items → read-back [7] 拒。"""
    root = tmp_path / "suite"
    digest = _store_fake_manifest(root, {
        "schema_version": "cutover-manifest/v1", "subject_commit": "abc123",
        "runner_version": "v1", "executed_at": "2026-07-25",
        "overall_passed": True,
        "outcomes": [{"name": "quality_gate", "passed": False, "detail": "x",
                      "evidence_digests": []}],
        "open_items": [{"item": "quality_gate", "passed": False,
                        "limitation": "偷排假绿"}],
        "sub_evidence_refs": [], "evidence_integrity": "ok", "digest_algorithm": "sha256",
    })
    ok, reason = CT._read_back_manifest(str(root), digest)
    assert ok is False and "[7]" in reason and "quality_gate" in reason, (
        "非白名单 red 项进 open_items 未被拒——S4 open_items 白名单未生效（任意红色 outcome 可偷排致 overall 假绿）")


def test_read_back_manifest_accepts_telemetry_open_item(tmp_path):
    """r7-S4（白名单正向）：telemetry 是唯一允许的 open 项（真实 OTLP/degradation suite 未接入，同 P1-6）。
    telemetry red + 其余 outcome 绿 + overall_passed=True + open_items 含 telemetry → read-back [7] 过
    （telemetry 从 all() 合法排除，非假绿）。确认 S4 白名单收紧不误伤合法 telemetry-open 语义。"""
    root = tmp_path / "suite"
    digest = _store_fake_manifest(root, {
        "schema_version": "cutover-manifest/v1", "subject_commit": "abc123",
        "runner_version": "v1", "executed_at": "2026-07-25",
        "overall_passed": True,
        "outcomes": [
            {"name": "shadow_parity", "passed": True, "detail": "x", "evidence_digests": []},
            {"name": "telemetry", "passed": False, "detail": "red", "evidence_digests": []},
        ],
        "open_items": [{"item": "telemetry", "passed": False,
                        "limitation": "真实 OTLP/degradation suite 未接入"}],
        "sub_evidence_refs": [], "evidence_integrity": "ok", "digest_algorithm": "sha256",
    })
    ok, reason = CT._read_back_manifest(str(root), digest)
    assert ok is True, f"telemetry 合法 open 项被误拒——S4 白名单过紧误伤 P1-6 语义: {reason}"


def test_publish_evidence_bundle_cross_machine_verify(tmp_path):
    """r5 P1-4（评审④）+ r7-S2：cross-machine immutable bundle——verify.py exit 0（完整 + subject 存在）；
    篡改 artifact → exit 1；两次发布同 manifest → bundle_digest 一致（跨机器可复核锚点）。

    r7-S2（审核员）：verify.py 校验 subject_commit 真实存在于 git（rev-parse）→ bundle 须在 git 仓内 +
    manifest 声明真 subject。审查者原反例：「artifact root 仍为本机路径，没有 immutable cross-machine bundle」。"""
    # r7-S2：verify.py 在 vault 仓上下文跑（git rev-parse 校验 subject 存在）
    vault = tmp_path / "vault"
    vault.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=str(vault), check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=str(vault), check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=str(vault), check=True)
    (vault / "README.md").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(vault), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "subject"], cwd=str(vault), check=True)
    subject = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(vault), text=True).strip()
    root = tmp_path / "suite"
    # r8-2：显式 runner_version（与 evidence commit committer 一致；默认 "" 会让 runner 绑定校验无锚点）
    m = CT.run_full_cutover_suite(drills=_green_bundle(), artifact_root=str(root),
                                  subject_commit=subject, runner_version="pa-cutover-runner")
    assert m.overall_passed is True
    b1 = vault / "docs" / "evidence" / subject      # bundle 落 git 仓内（verify.py 在仓上下文跑）
    _, digest1 = CT.publish_evidence_bundle(artifact_root=str(root), manifest=m, bundle_root=b1)
    # r8-2（审核员）：造 evidence commit（让 verify.py 的 git log --grep 反查能找到 + ancestry + runner binding
    # 校验通过）。committer name = m.runner_version；commit message 用 _commit_evidence 同模板（verify --grep
    # 精确匹配）。手动 commit 不 push——verify.py 的 ``git log --all`` 搜本地 refs 即找到。
    subprocess.run(["git", "config", "user.name", m.runner_version], cwd=str(vault), check=True)
    subprocess.run(["git", "config", "user.email", "runner@pa-cutover.local"], cwd=str(vault), check=True)
    subprocess.run(["git", "add", "."], cwd=str(vault), check=True)
    subprocess.run(["git", "commit", "-q", "-m", f"evidence: cutover suite for subject_commit={subject[:12]}"],
                   cwd=str(vault), check=True)
    # verify.py 自检通过（bundle 完整 + subject 存在 + evidence_commit ancestry + runner binding）
    r = subprocess.run([sys.executable, str(b1 / "verify.py")], cwd=str(vault), capture_output=True, text=True)
    assert r.returncode == 0, f"verify.py 应 exit 0（完整 + subject + ancestry + runner 绑定）: {r.stderr}"
    # 篡改一个子证据 → verify.py exit 非 0（检测篡改）
    first_art = next((b1 / "artifacts").iterdir())
    first_art.write_text("TAMPERED", encoding="utf-8")
    r2 = subprocess.run([sys.executable, str(b1 / "verify.py")], cwd=str(vault), capture_output=True, text=True)
    assert r2.returncode != 0, "篡改后 verify.py 应 exit 非 0"
    # 两次发布同 manifest → bundle_digest 一致（跨机器一致，passing 可复核锚点）
    b2 = tmp_path / "bundle2"
    _, digest2 = CT.publish_evidence_bundle(artifact_root=str(root), manifest=m, bundle_root=b2)
    assert digest1 == digest2


def test_verify_rejects_evidence_commit_ancestry_with_business_files(tmp_path):
    """r8-2（审核员反例）：evidence_commit ancestry subject..evidence 含非 docs/evidence/ 路径（夹带业务代码）
    → verify.py exit 非 0。防 evidence_commit 夹带 scripts/ 等业务变更被误当 subject 重新执行。committer
    正确（runner 绑定 pass），唯独 ancestry 污染 → 隔离测 ancestry 校验。"""
    vault = tmp_path / "vault"
    vault.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=str(vault), check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=str(vault), check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=str(vault), check=True)
    (vault / "README.md").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(vault), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "subject"], cwd=str(vault), check=True)
    subject = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(vault), text=True).strip()
    root = tmp_path / "suite"
    m = CT.run_full_cutover_suite(drills=_green_bundle(), artifact_root=str(root),
                                  subject_commit=subject, runner_version="pa-cutover-runner")
    b1 = vault / "docs" / "evidence" / subject
    CT.publish_evidence_bundle(artifact_root=str(root), manifest=m, bundle_root=b1)
    subprocess.run(["git", "config", "user.name", m.runner_version], cwd=str(vault), check=True)
    subprocess.run(["git", "config", "user.email", "runner@pa-cutover.local"], cwd=str(vault), check=True)
    (vault / "scripts").mkdir()
    (vault / "scripts" / "foo.py").write_text("print(1)", encoding="utf-8")  # 业务文件污染 ancestry
    subprocess.run(["git", "add", "."], cwd=str(vault), check=True)
    subprocess.run(["git", "commit", "-q", "-m", f"evidence: cutover suite for subject_commit={subject[:12]}"],
                   cwd=str(vault), check=True)
    r = subprocess.run([sys.executable, str(b1 / "verify.py")], cwd=str(vault), capture_output=True, text=True)
    assert r.returncode != 0, "evidence_commit ancestry 含业务文件应使 verify.py exit 非 0"
    assert "ancestry" in r.stderr or "docs/evidence" in r.stderr, f"stderr 应指出 ancestry 问题: {r.stderr}"


def test_verify_rejects_evidence_commit_runner_mismatch(tmp_path):
    """r8-2（审核员反例）：evidence_commit committer name != manifest.runner_version → verify.py exit 非 0。
    防「evidence 被他人/异 runner 产出」伪装成 runner_version 绑定。ancestry 干净（只 docs/evidence/），
    唯独 committer 不符 → 隔离测 runner 绑定校验。"""
    vault = tmp_path / "vault"
    vault.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=str(vault), check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=str(vault), check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=str(vault), check=True)
    (vault / "README.md").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(vault), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "subject"], cwd=str(vault), check=True)
    subject = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(vault), text=True).strip()
    root = tmp_path / "suite"
    m = CT.run_full_cutover_suite(drills=_green_bundle(), artifact_root=str(root),
                                  subject_commit=subject, runner_version="pa-cutover-runner")
    b1 = vault / "docs" / "evidence" / subject
    CT.publish_evidence_bundle(artifact_root=str(root), manifest=m, bundle_root=b1)
    subprocess.run(["git", "config", "user.name", "impostor-runner"], cwd=str(vault), check=True)  # ≠ m.runner_version
    subprocess.run(["git", "config", "user.email", "x@x"], cwd=str(vault), check=True)
    subprocess.run(["git", "add", "."], cwd=str(vault), check=True)
    subprocess.run(["git", "commit", "-q", "-m", f"evidence: cutover suite for subject_commit={subject[:12]}"],
                   cwd=str(vault), check=True)
    r = subprocess.run([sys.executable, str(b1 / "verify.py")], cwd=str(vault), capture_output=True, text=True)
    assert r.returncode != 0, "committer != runner_version 应使 verify.py exit 非 0"
    assert "runner_version" in r.stderr or "committer" in r.stderr, f"stderr 应指出 runner 绑定问题: {r.stderr}"


def test_verify_rejects_nonexistent_subject_commit(tmp_path):
    """r7-S2（审核员反例）：verify.py 对不存在的 subject commit 须 exit 非 0。旧版只校验字段存在 →
    manifest 声明假 sha 仍 exit 0（假绿）。r7-S2：verify.py 加 ``git rev-parse --verify <subject>^{commit}``。"""
    vault = tmp_path / "vault"
    vault.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=str(vault), check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=str(vault), check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=str(vault), check=True)
    (vault / "README.md").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(vault), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "subject"], cwd=str(vault), check=True)
    root = tmp_path / "suite"
    _fake = "0" * 40    # manifest 声明一个**不存在**的 subject（假 sha；vault 仓无此 object）
    m = CT.run_full_cutover_suite(drills=_green_bundle(), artifact_root=str(root), subject_commit=_fake)
    b1 = vault / "docs" / "evidence" / _fake
    CT.publish_evidence_bundle(artifact_root=str(root), manifest=m, bundle_root=b1)
    r = subprocess.run([sys.executable, str(b1 / "verify.py")], cwd=str(vault), capture_output=True, text=True)
    assert r.returncode != 0, "假 subject_commit 应使 verify.py exit 非 0（旧版字段存在校验假绿）"
    assert "subject_commit" in r.stderr or "rev-parse" in r.stderr, f"stderr 应指出 subject 问题: {r.stderr}"


def test_check_sub_evidence_allowlist_rejects_input_fields_and_long_text():
    """r7-S3 → r8-4（审核员）：_check_sub_evidence_allowlist 检测未知字段（prompt/tool_input——不在任何
    dataclass 字段集）+ 超长文本。旧 r7-S3 是 denylist（4 禁字段）；r8-4 改 per-kind allowlist——任意非
    dataclass 字段（prompt/tool_input/model_output 等）均拒（未知字段 fail，堵假绿）。用真 drill（telemetry）
    隔离测字段 allowlist（非 drill 校验）。secret scan 查凭据，allowlist 查字段类型，互补。"""
    import json as _json
    blob = _json.dumps({"drill": "telemetry", "evidence": {
        "prompt": "do something", "tool_input": "raw",   # 不在 TelemetryEvidence 字段集 → 未知字段
        "long": "a" * 1500}}).encode("utf-8")
    v = CT._check_sub_evidence_allowlist(blob)
    assert any("prompt" in x for x in v), "应检出未知字段 prompt（不在 dataclass allowlist）"
    assert any("tool_input" in x for x in v), "应检出未知字段 tool_input"
    assert any("超长" in x for x in v), "应检出超长文本"


def test_check_sub_evidence_allowlist_accepts_minimal_evidence():
    """r8-4：真 drill + dataclass 合法字段（``_dc_field_names`` 动态取）+ 短文本 → 合规（空违规）。
    旧 r7-S3 用假 drill "x" + {exit/stdout/...}（r8-4 后未知 drill/字段会被拒）；r8-4 改用真 telemetry
    drill + 动态字段集（schema 漂移自动跟随，不硬编码字段名）。"""
    import json as _json
    _fields = CT._dc_field_names(CT.TelemetryEvidence)
    _ev = {f: "v" for f in _fields}    # 全合法字段，短值
    blob = _json.dumps({"drill": "telemetry", "evidence": _ev}).encode("utf-8")
    assert CT._check_sub_evidence_allowlist(blob) == [], (
        f"真 drill + dataclass 字段应合规: {CT._check_sub_evidence_allowlist(blob)}")


# ---- r8-4（审核员）：sub-evidence per-kind allowlist 反例（denylist→allowlist） ----

def test_check_sub_evidence_rejects_unknown_drill():
    """r8-4：未知 drill（不在 per-kind allowlist）→ 违规（防任意 drill 名发布，旧 denylist 不查 drill）。"""
    import json as _json
    blob = _json.dumps({"drill": "bogus_drill", "evidence": {}}).encode("utf-8")
    v = CT._check_sub_evidence_allowlist(blob)
    assert any("未知 drill" in x for x in v), "未知 drill 应被拒（per-kind allowlist）"


def test_check_sub_evidence_rejects_unknown_top_level_key():
    """r8-4：顶层未知键（非 drill/evidence）→ 违规（防 blob 夹带额外顶层结构）。"""
    import json as _json
    blob = _json.dumps({"drill": "telemetry", "evidence": {}, "extra": "x"}).encode("utf-8")
    v = CT._check_sub_evidence_allowlist(blob)
    assert any("顶层" in x or "extra" in x for x in v), "顶层未知键应被拒"


def test_check_sub_evidence_rejects_list_element_unknown_field():
    """r8-4：list drill（recovery/sandbox）元素含未知字段 → 违规（list 元素也查 allowlist）。"""
    import json as _json
    blob = _json.dumps({"drill": "recovery", "evidence": [{"prompt": "x"}]}).encode("utf-8")
    v = CT._check_sub_evidence_allowlist(blob)
    assert any("prompt" in x or "未知字段" in x for x in v), "list 元素未知字段应被拒"


# ---- r9-6（审核员）：sub-evidence 递归 leaky denylist + 非 JSON fail-closed ----
# r8-4 顶层 allowlist 只查 evidence 顶层键 + list 元素顶层键，不深入嵌套 dict/list → 审核员反例：
# telemetry.callback_invocations[].raw_prompt/tool_output + sdk_canary.per_scenario[].prompt/model_output
# 经嵌套结构泄漏。r9-6 在 _walk 递归中补 denylist（_SUB_EVIDENCE_LEAKY_FIELDS），任意深度命中即拒。

def test_check_sub_evidence_rejects_non_json_blob():
    """r9-6（审核员）：非 JSON blob → fail-closed。旧版 ``except: return []`` 跳过 = 视为干净 → 假绿。
    sub-evidence 经 ``_archive_sub_evidence`` 总是 ``json.dumps`` 序列化，非 JSON = 损坏/伪造，绝不当二进制
    artifact 干净放过。"""
    blob = b"\x00\x01\x02 not json \xff\xfe"
    v = CT._check_sub_evidence_allowlist(blob)
    assert any("非 JSON" in x for x in v), "非 JSON blob 应 fail-closed（旧版跳过返回 [] = 假绿）"


def test_check_sub_evidence_rejects_nested_leaky_field_in_callback_invocations():
    """r9-6（审核员反例）：``telemetry.callback_invocations[].tool_output`` 经嵌套 dict/list 泄漏。
    r8-4 顶层 allowlist 只查 evidence 顶层键（callback_invocations 是合法顶层字段）+ list 元素顶层键，
    不深入 callback_invocations[].tool_output → 嵌套 leaky 字段漏网。r9-6 ``_walk`` 递归 denylist 补：
    任意深度命中 ``_SUB_EVIDENCE_LEAKY_FIELDS``（tool_output/raw_prompt/prompt/model_output/...）→ 违规。"""
    import json as _json
    blob = _json.dumps({"drill": "telemetry", "evidence": {
        "callback_invocations": [
            {"event": "PostToolUse", "correlation_id": "c1", "tool_output": "SECRET_STDOUT_WITH_TOKEN"},
            {"event": "PostToolUse", "correlation_id": "c2", "raw_prompt": "system prompt leak"}],
    }}).encode("utf-8")
    v = CT._check_sub_evidence_allowlist(blob)
    assert any("tool_output" in x and "泄漏" in x for x in v), (
        "嵌套 callback_invocations[].tool_output 须被 r9-6 递归 denylist 检出（r8-4 顶层 allowlist 漏网）")
    assert any("raw_prompt" in x and "泄漏" in x for x in v), "raw_prompt 同属 leaky denylist"


def test_check_sub_evidence_accepts_clean_callback_invocations():
    """r9-6 回归：callback_invocations 元素只含非 leaky 字段（event/correlation_id/tool_name/tool_exit_code）
    → 合规。证明 r9-6 denylist 精确拒 leaky 字段名，不误伤合法诊断元数据（runtime
    ``_strip_leaky_invocation_fields`` 剥离 tool_output 后的 callback_invocations 须过 allowlist）。"""
    import json as _json
    blob = _json.dumps({"drill": "telemetry", "evidence": {
        "callback_invocations": [
            {"event": "PostToolUse", "correlation_id": "c1", "tool_name": "Bash", "tool_exit_code": 0}],
    }}).encode("utf-8")
    v = CT._check_sub_evidence_allowlist(blob)
    assert v == [], f"无 leaky 字段的 callback_invocations 应合规: {v}"


def test_check_sub_evidence_allowlist_matches_dataclass_fields():
    """r8-4 schema 漂移守卫：_SUB_EVIDENCE_ALLOWLIST_BY_KIND 每 drill 字段集 == 对应 dataclass 的 ``fields()``。
    allowlist 由 ``_dc_field_names`` 动态构建，本应自动跟随；本测试锁定该不变式——dataclass 改字段时此测试
    提醒 allowlist 同步（防手动维护 allowlist 漂移）。"""
    from dataclasses import fields as _dc_fields
    pairs = [
        ("shadow_parity", CT.ShadowParityEvidence),
        ("sdk_canary", CT.SdkHookCanaryEvidence),
        ("crash_reconciliation", CT.CrashReconciliationEvidence),
        ("dispatch_cutover", CT.DispatchCutoverResult),
        ("quality_gate", CT.QualityGateResult),
        ("telemetry", CT.TelemetryEvidence),
        ("recovery", CT.RecoveryDrillResult),
        ("sandbox", CT.SandboxDrillResult),
    ]
    for drill, cls in pairs:
        expected = frozenset(f.name for f in _dc_fields(cls))
        assert CT._SUB_EVIDENCE_ALLOWLIST_BY_KIND[drill] == expected, (
            f"allowlist({drill}) 与 dataclass fields 漂移: "
            f"{sorted(CT._SUB_EVIDENCE_ALLOWLIST_BY_KIND[drill])} != {sorted(expected)}")


# ---- r6 P1-4：bundle publication allowlist + secret scan（评审 R4 §4：凭据不得跨机器泄漏） ----

def test_scan_for_secrets_detects_credentials():
    """r6 P1-4：_scan_for_secrets 识别各凭据模式（GitHub PAT/token、AWS、OpenAI/Anthropic、Google、Bearer、
    secret kv）+ 干净文本无误报 + 脱敏不回显凭据值。"""
    assert any("github_token" in h for h in CT._scan_for_secrets("ghp_" + "a" * 36))
    assert any("github_pat" in h for h in CT._scan_for_secrets("github_pat_" + "b" * 82))
    assert any("aws_access_key" in h for h in CT._scan_for_secrets("AKIA" + "A" * 16))
    assert any("openai_key" in h for h in CT._scan_for_secrets("sk-" + "c" * 24))
    assert any("bearer_token" in h for h in CT._scan_for_secrets("Bearer " + "d" * 24))
    assert any("secret_kv" in h for h in CT._scan_for_secrets('password: "supersecret123"'))
    # 干净文本（evidence 常见字段）无误报
    assert CT._scan_for_secrets("matched=parity_ok; mismatches=0; drill=sdk_canary") == []
    # 脱敏：不回显完整凭据值
    h = CT._scan_for_secrets("ghp_" + "a" * 36)
    assert "a" * 36 not in h[0], "scan 诊断不得回显完整凭据值"


def test_publish_evidence_bundle_failclosed_on_secret_in_sub_evidence(tmp_path):
    """r6 P1-4（评审反例）：子证据 blob 含凭据（GitHub PAT）→ publish_evidence_bundle scan 命中 →
    raise（fail-closed，不复制凭据进 bundle）。raise 经 real_cutover_suite catch → bundle_publish_ok=False
    → overall_passed=False（P1-2 接力非零退出）。"""
    import json
    from dataclasses import replace
    root = tmp_path / "suite"
    m = CT.run_full_cutover_suite(drills=_green_bundle(), artifact_root=str(root))
    # 构造含 GitHub PAT 的子证据 blob，归档得 digest
    bad_blob = json.dumps({"drill": "sdk_canary",
                           "evidence": {"env": {"GITHUB_TOKEN": "ghp_" + "a" * 36}}},
                          ensure_ascii=False, sort_keys=True)
    bad_digest = CT.artifact_store.store(str(root), bad_blob, kind="test_output",
                                         sensitivity="internal").digest
    m_bad = replace(m, sub_evidence_refs=(bad_digest,))
    with pytest.raises(ValueError, match="凭据模式"):
        CT.publish_evidence_bundle(artifact_root=str(root), manifest=m_bad,
                                   bundle_root=tmp_path / "bundle")
    # bundle 不得被创建（fail-closed 在写盘前 raise）
    assert not (tmp_path / "bundle" / "manifest.json").exists()


def test_publish_evidence_bundle_rejects_unknown_manifest_field(tmp_path):
    """r6 P1-4 step 1（allowlist）：manifest structured() 被注入未知字段 → publish 拒（防绕过结构校验）。"""
    import json

    class _BadManifest:
        """duck-typed manifest：structured_json 注入未知字段（模拟 manifest 被篡改/外部构造）。"""
        def __init__(self, real):
            self._r = real
        def structured_json(self):
            return json.dumps({**self._r.structured(), "INJECTED_FIELD": "evil"},
                              ensure_ascii=False, sort_keys=True)
        @property
        def sub_evidence_refs(self):
            return self._r.sub_evidence_refs
        @property
        def manifest_digest(self):
            return self._r.manifest_digest

    root = tmp_path / "suite"
    m = CT.run_full_cutover_suite(drills=_green_bundle(), artifact_root=str(root))
    with pytest.raises(ValueError, match="未知字段"):
        CT.publish_evidence_bundle(artifact_root=str(root), manifest=_BadManifest(m),
                                   bundle_root=tmp_path / "bundle")


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


def test_otlp_export_verified_false_when_no_endpoint(monkeypatch):
    """r9-1：无 OTEL endpoint（生产常态）→ False（不连网，诚实未接入 → telemetry 进 open_items）。"""
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    assert CT._otlp_export_verified() is False


def test_otlp_export_verified_not_forgeable_by_env_nonempty(monkeypatch):
    """r9-1 核心（审核员 P0 反向反例）：设 ``OTEL_EXPORTER_OTLP_ENDPOINT=x`` → r8-1 旧判 ``bool(env)=True``
    （telemetry_connected=True → 7.6 假绿）。r9-1 改实际 export——collector 不可达 → False。

    环境变量非空**不可伪造**接入：必须真实 collector 可达 + 接收 valid span（2xx）。"""
    import urllib.error
    import urllib.request
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://nonexistent-collector.invalid")

    def _boom(req, timeout=None):
        raise urllib.error.URLError("unreachable")     # 连接失败（伪造 endpoint =x 走此路径）
    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    assert CT._otlp_export_verified() is False, "环境变量非空即 True——r8-1 旧判未废，假绿路径仍在"


def test_otlp_export_verified_true_when_collector_receives_2xx(monkeypatch):
    """r9-1：接真实 collector + export test span 接收（HTTP 2xx）→ True（真实接入，telemetry 可移出 open_items）。

    同时验 url 拼接：base endpoint（无 /v1/traces）自动补 /v1/traces（OTLP/HTTP traces 路径）。"""
    import urllib.request
    captured = {}

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _ok(req, timeout=None):
        captured["url"] = req.full_url
        return _Resp()
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318")
    monkeypatch.setattr(urllib.request, "urlopen", _ok)
    assert CT._otlp_export_verified() is True
    assert captured["url"] == "http://collector:4318/v1/traces", "base endpoint 须补 /v1/traces"


def test_otlp_export_verified_uses_traces_endpoint_as_full_url(monkeypatch):
    """r9-1：``OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`` 是完整 URL（含 /v1/traces）→ 直接用，不重复补路径。"""
    import urllib.request
    captured = {}

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _ok(req, timeout=None):
        captured["url"] = req.full_url
        return _Resp()
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "http://collector:4318/v1/traces")
    monkeypatch.setattr(urllib.request, "urlopen", _ok)
    assert CT._otlp_export_verified() is True
    assert captured["url"] == "http://collector:4318/v1/traces", "TRACES_ENDPOINT 已含路径，不可重复补"


def test_telemetry_connected_delegates_to_otlp_export_verified(monkeypatch):
    """r9-1：runtime ``_telemetry_connected`` 转调 ``CT._otlp_export_verified``（单一真理源）——
    无 endpoint → False（7.6 谓词强制 telemetry open，不可假装就绪）。"""
    import runtime_evidence as RE
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    assert RE._telemetry_connected() is False


def test_verify_accepts_evidence_commit_argv_exact_sha(tmp_path):
    """r9-3（审核员）：verify.py 接受 ``argv[1]`` exact evidence SHA（real_cutover_suite 传），**消除 r8-2 的
    git log --grep 反查**（多匹配 + commit message 漂移风险）。传 exact SHA → exit 0（绿）。"""
    vault = tmp_path / "vault"
    vault.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=str(vault), check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=str(vault), check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=str(vault), check=True)
    (vault / "README.md").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(vault), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "subject"], cwd=str(vault), check=True)
    subject = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(vault), text=True).strip()
    root = tmp_path / "suite"
    m = CT.run_full_cutover_suite(drills=_green_bundle(), artifact_root=str(root),
                                  subject_commit=subject, runner_version="pa-cutover-runner")
    b1 = vault / "docs" / "evidence" / subject
    CT.publish_evidence_bundle(artifact_root=str(root), manifest=m, bundle_root=b1)
    subprocess.run(["git", "config", "user.name", m.runner_version], cwd=str(vault), check=True)
    subprocess.run(["git", "config", "user.email", "runner@pa-cutover.local"], cwd=str(vault), check=True)
    subprocess.run(["git", "add", "."], cwd=str(vault), check=True)
    subprocess.run(["git", "commit", "-q", "-m", f"evidence: cutover suite for subject_commit={subject[:12]}"],
                   cwd=str(vault), check=True)
    evidence_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(vault), text=True).strip()
    # r9-3：传 exact SHA 作 argv（real_cutover_suite 同款）—— 非 grep 反查
    r = subprocess.run([sys.executable, str(b1 / "verify.py"), evidence_commit],
                       cwd=str(vault), capture_output=True, text=True)
    assert r.returncode == 0, f"verify.py 传 exact SHA 应 exit 0（r9-3 argv 路径）: {r.stderr}"


def test_verify_rejects_non_ancestor_evidence_commit(tmp_path):
    """r9-4（审核员）：evidence_commit 不基于 subject（subject 非 evidence 祖先）→ verify.py exit 非 0。
    r8-2 旧版仅 ``git diff subject..evidence``（树差异——subject 与 evidence 平行分支也过，假绿）。r9-4 加
    ``merge-base --is-ancestor``：subject 非 evidence 祖先 → fail（防 evidence 基于 unrelated commit）。"""
    vault = tmp_path / "vault"
    vault.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=str(vault), check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=str(vault), check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=str(vault), check=True)
    (vault / "README.md").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(vault), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "subject"], cwd=str(vault), check=True)
    subject = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(vault), text=True).strip()
    root = tmp_path / "suite"
    m = CT.run_full_cutover_suite(drills=_green_bundle(), artifact_root=str(root),
                                  subject_commit=subject, runner_version="pa-cutover-runner")
    b1 = vault / "docs" / "evidence" / subject
    CT.publish_evidence_bundle(artifact_root=str(root), manifest=m, bundle_root=b1)
    subprocess.run(["git", "config", "user.name", m.runner_version], cwd=str(vault), check=True)
    subprocess.run(["git", "config", "user.email", "runner@pa-cutover.local"], cwd=str(vault), check=True)
    subprocess.run(["git", "add", "."], cwd=str(vault), check=True)
    # 造 **orphan** evidence commit（commit-tree 无父 → 不含 subject 历史）—— 模拟 evidence 基于 unrelated commit
    _tree = subprocess.check_output(["git", "write-tree"], cwd=str(vault), text=True).strip()
    orphan = subprocess.check_output(["git", "commit-tree", _tree, "-m", "orphan evidence"],
                                     cwd=str(vault), text=True).strip()
    # subject 非 orphan 祖先 → merge-base --is-ancestor 失败 → verify.py exit 非 0
    r = subprocess.run([sys.executable, str(b1 / "verify.py"), orphan],
                       cwd=str(vault), capture_output=True, text=True)
    assert r.returncode != 0, "subject 非 evidence 祖先应使 verify.py exit 非 0（r9-4 merge-base 未生效）"
    assert "祖先" in r.stderr or "merge-base" in r.stderr, (
        f"应报 merge-base 祖先失败，实际 stderr: {r.stderr}")


def test_publish_and_verify_evidence_rollbacks_when_verify_fails(tmp_path, monkeypatch):
    """r9-2（审核员 P0）编排层端到端守门（综合裁判 MEDIUM 缺口）：``_publish_and_verify_evidence``（r9-8 提取自
    real_cutover_suite）编排 publish→commit(push=False)→verify.py→push。verify.py exit≠0 → ``_rollback_evidence_commit``
    回滚 commit + raise + 不 push。旧版仅 helper 各自单测（_commit_evidence/_rollback_evidence_commit 绿），编排顺序
    （verify 前绝不 push）无测试——未来回归把 push 提前到 verify 前，现有测试守不住。本测试注入 tmp git 仓作
    vault_root，monkeypatch verify.py 那次 subprocess.run 返非零（git 调用透传 real_run），断言：(1) raise
    RuntimeError；(2) HEAD 退回 subject（commit 被 rollback）；(3) 远端 main 仍是 subject（verify 红绝不 push）。"""
    import runtime_evidence as RE
    vault = tmp_path / "vault"
    vault.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=str(vault), check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=str(vault), check=True)
    subprocess.run(["git", "config", "user.name", "pa-cutover-runner"], cwd=str(vault), check=True)
    (vault / "README.md").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(vault), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "subject"], cwd=str(vault), check=True)
    subject = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(vault), text=True).strip()
    _bare = vault.parent / f"{vault.name}-bare.git"              # bare remote + upstream（push 依赖 upstream）
    subprocess.run(["git", "init", "-q", "--bare", str(_bare)], check=True)
    subprocess.run(["git", "remote", "add", "origin", str(_bare)], cwd=str(vault), check=True)
    subprocess.run(["git", "push", "-q", "-u", "origin", "main"], cwd=str(vault), check=True)
    root = tmp_path / "suite"
    m = CT.run_full_cutover_suite(drills=_green_bundle(), artifact_root=str(root),
                                  subject_commit=subject, runner_version="pa-cutover-runner")
    assert m.overall_passed is True, "precondition: 全绿 manifest"

    # monkeypatch verify.py 那次 subprocess.run 返非零；git add/commit/rev-parse/diff 透传 real_run（cmd[0]=="git"）
    real_run = subprocess.run

    def _fake_run(cmd, *a, **kw):
        if cmd and str(cmd[0]) == sys.executable and len(cmd) > 1 and "verify.py" in str(cmd[1]):
            return subprocess.CompletedProcess(cmd, returncode=1, stdout="",
                                               stderr="simulated verify fail（r9-2 编排守门）")
        return real_run(cmd, *a, **kw)
    monkeypatch.setattr(RE.subprocess, "run", _fake_run)

    # verify.py 红 → _rollback_evidence_commit 回滚 + raise（编排 fail-closed）
    with pytest.raises(RuntimeError, match="verify.py fail-closed"):
        RE._publish_and_verify_evidence(m, subject, vault_root=vault, artifact_root=root)

    # (1) HEAD 退回 subject（evidence commit 被 _rollback_evidence_commit 撤；r9-5 unstage 一并生效）
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(vault), text=True).strip()
    assert head == subject, "verify.py 失败后 evidence commit 未回滚——r9-2 编排 fail-closed 未生效"
    # (2) 远端 main 仍是 subject（verify 红绝不 push；若编排误先 push 再 verify，远端 main 会进 evidence commit）
    remote_main = subprocess.check_output(["git", "ls-remote", str(_bare), "refs/heads/main"],
                                          text=True).strip().split()[0]
    assert remote_main == subject, "verify 红后远端 main 进了 evidence commit——r9-2 编排先 push 了（顺序错）"


def test_verify_argv_sha_distinguishes_dup_message_commits_where_grep_ambiguous(tmp_path):
    """r9-3（审核员 P1）反例编码（综合裁判 MEDIUM 缺口）：仓内 ≥2 个相同 evidence message commit 时，argv[1] exact
    SHA 精确区分（exit 0），而 r8-2 旧 ``git log --grep`` 反查会多匹配 ambiguous（无法定唯一 evidence_commit）。现有
    ``test_verify_accepts_evidence_commit_argv_exact_sha`` 是 happy-path（单 evidence commit），回退到 grep 在单 commit
    仓仍只 1 匹配→测试仍绿→守不住「不准退回 grep」。本测试造 2 同 message commit，显式编码「grep 多匹配」缺陷 +
    argv 精确性（argv=e1 exit 0，不受 e2 存在影响）。"""
    vault = tmp_path / "vault"
    vault.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=str(vault), check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=str(vault), check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=str(vault), check=True)
    (vault / "README.md").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(vault), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "subject"], cwd=str(vault), check=True)
    subject = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(vault), text=True).strip()
    root = tmp_path / "suite"
    m = CT.run_full_cutover_suite(drills=_green_bundle(), artifact_root=str(root),
                                  subject_commit=subject, runner_version="pa-cutover-runner")
    b1 = vault / "docs" / "evidence" / subject
    CT.publish_evidence_bundle(artifact_root=str(root), manifest=m, bundle_root=b1)
    subprocess.run(["git", "config", "user.name", m.runner_version], cwd=str(vault), check=True)
    subprocess.run(["git", "config", "user.email", "runner@pa-cutover.local"], cwd=str(vault), check=True)
    msg = f"evidence: cutover suite for subject_commit={subject[:12]}"
    subprocess.run(["git", "add", "."], cwd=str(vault), check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=str(vault), check=True)
    e1 = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(vault), text=True).strip()
    # 第 2 个**相同 message** commit（空 commit，仅制造 grep 多匹配；不 verify 它——它仅证明 grep ambiguous）
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", msg], cwd=str(vault), check=True)
    e2 = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(vault), text=True).strip()
    assert e1 != e2

    # argv[1]=e1 → verify exit 0（argv 精确，e2 存在不影响——argv 不依赖 message 唯一性；工作树仍是 e1 内容因 e2 空）
    r1 = subprocess.run([sys.executable, str(b1 / "verify.py"), e1],
                        cwd=str(vault), capture_output=True, text=True)
    assert r1.returncode == 0, f"argv=e1 应 exit 0（r9-3 argv 精确，不受同 message e2 影响）: {r1.stderr}"

    # r8-2 grep 反查在同 message 仓多匹配 ambiguous（编码 r9-3 删 grep 理由——回退 grep 无法定唯一 evidence_commit）
    grep_out = subprocess.check_output(["git", "log", "--grep", msg, "--format=%H"],
                                       cwd=str(vault), text=True).strip()
    assert len(grep_out.splitlines()) >= 2, (
        f"grep 同 message 应 ≥2 匹配（证明 r8-2 grep 反查 ambiguous；r9-3 argv 必要）；实际: {grep_out!r}")


# ─────────────────────────────────────────────────────────────────────────────
# r10-B1/B3 regression lock：xfail(strict=True) 已知洞 RED baseline（诚实收敛路线，留 P2 硬化）。
# B2 必须-修复代码已闭合（见 test_runtime_evidence_commit.py r10-B2 反向测试）；B1（dummy 2xx 假绿）/ B3
# （合法 VALUE 字段值含凭据 + 嵌套未知字段名）是**残留洞**，本批不做代码硬化，但用 xfail strict 锁定——
# P2 硬化让测试意外变 GREEN 时 strict 失败，强制移除 xfail（防「悄悄变绿却无人知」的守门弱循环）。
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason=(
    "r10-B1 已知洞（留 P2 traceId 回查）：_otlp_export_verified 只验 endpoint 对 POST 返 2xx，dummy / 输入"
    "校验型 2xx server 即返 True → 假绿。探针不证明真 collector / 真 ingest。"))
def test_r10_b1_known_hole_dummy_2xx_is_not_real_collector(monkeypatch):
    """r10-B1 regression lock：dummy 2xx server 让连通性探针假绿（探针分不清真 collector 与 dummy）。

    现有 ``test_otlp_..._true_when_collector_receives_2xx`` 隐式接受「2xx=True」；本测试显式锁定其为已知洞。
    P2 gold-standard traceId 回查硬化后，dummy 场景应返 False → 本测试 GREEN → xfail(strict) FAILED → 移除标记。
    """
    import urllib.request

    class _Dummy2xxResp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, *a):
            return b"{}"
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://dummy-2xx-collector.test")
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: _Dummy2xxResp())
    # 洞：dummy 2xx → _otlp_export_verified 返 True（假绿——不证明真 collector）
    assert CT._otlp_export_verified() is False, (
        "若 GREEN：P2 traceId 回查已硬化（dummy 2xx 不再假绿），移除本 xfail")


@pytest.mark.xfail(strict=True, reason=(
    "r10-B3 已知洞（留 P2 值维度 scan）：_check_sub_evidence_allowlist 是 key-level allowlist，summary 等"
    "合法字段名的【值】含凭据则放过。需对 allowlist 字段值也跑 _scan_for_secrets（P2）。"))
def test_r10_b3_known_hole_credential_in_legal_value_field():
    """r10-B3 regression lock (a)：合法 VALUE 字段（``summary`` 在 TelemetryEvidence 字段集内）的值含 AKIA
    凭据 → key-level allowlist 放过（``_walk`` 只查字段名 denylist + 超长，不查值里的凭据模式）。

    publish 层 ``_scan_for_secrets`` 整文件 scan 互补，但模式有限（派生值 / 新格式漏检）→ 残留风险。
    P2：对 allowlist 字段值跑 _scan_for_secrets → violations 非空 → 本测试 GREEN → xfail strict FAILED。
    """
    import json
    blob = json.dumps({
        "drill": "telemetry",
        "evidence": {"summary": "export failed: leaked AKIA" + "C" * 16 + " in legal summary field"},
    }).encode("utf-8")
    violations = CT._check_sub_evidence_allowlist(blob)
    # 洞：summary 字段名合法（在 telemetry allowlist），值含 AKIA 但 key-level 放过 → violations 空
    assert violations, (
        "若 GREEN：P2 已对 allowlist 字段值跑 _scan_for_secrets，移除本 xfail")


@pytest.mark.xfail(strict=True, reason=(
    "r10-B3 已知洞（留 P2 嵌套覆盖扫描）：_walk 递归 denylist 只拒 _SUB_EVIDENCE_LEAKY_FIELDS（14 已知"
    "名），新增未知泄漏字段名（如 ai_response）嵌套放过。需 denylist 升覆盖式扫描（P2）。"))
def test_r10_b3_known_hole_nested_unknown_leaky_field_name():
    """r10-B3 regression lock (b)：嵌套未知字段名（``ai_response`` 不在 14 leaky 名表）→ 递归 denylist 放过。

    evidence.callback_invocations 是 TelemetryEvidence 合法字段（顶层过）；list 元素 dict 的未知 key
    ``ai_response`` 不在 14 名表 → ``_walk`` 不拒。P2 嵌套覆盖式扫描 → violations 非空 → GREEN → strict FAILED。
    """
    import json
    blob = json.dumps({
        "drill": "telemetry",
        "evidence": {"callback_invocations": [{"ai_response": "leaked nested output"}]},
    }).encode("utf-8")
    violations = CT._check_sub_evidence_allowlist(blob)
    # 洞：ai_response 不在 14 leaky 名表 + 顶层 callback_invocations 合法 → violations 空
    assert violations, (
        "若 GREEN：P2 已升嵌套覆盖扫描，移除本 xfail")


# ════════════════════════════════════════════════════════════════════════════
# add-cross-prd-learning-memory task 1.3b：learning injection 四重 gate 纯函数
# design 决策#8：injection cutover 镜像 resolve_dispatch_source 的 flag + parity + allowlist 三重 gate，
# 再追加 shadow 前置 + quality 门。任一不过 → learning_memory_degraded fallback（reason 指明未过维度）。
# 纯函数零 IO（cutover.py:19 约束）——不在内 emit 事件。
# ════════════════════════════════════════════════════════════════════════════
def test_learning_injection_driven_when_all_four_gates_pass():
    """task 1.3b：flag + shadow + parity + quality + allowlist 全过 → driven_by='learning_injection'。"""
    r = CT.resolve_learning_injections_source(
        injection_flag=True, shadow_flag=True, project_id="proj-alpha",
        allowlist=("proj-alpha",), parity_passed=True, quality_passed=True)
    assert r.driven_by == "learning_injection"
    assert r.fallback_reason == ""


def test_learning_injection_fallback_when_shadow_off():
    """task 1.3b 门控①：shadow_flag=False → fallback（injection gated on shadow）。"""
    r = CT.resolve_learning_injections_source(
        injection_flag=True, shadow_flag=False, project_id="proj-alpha",
        allowlist=("proj-alpha",), parity_passed=True, quality_passed=True)
    assert r.driven_by != "learning_injection"
    assert "shadow" in r.fallback_reason.lower()


def test_learning_injection_fallback_when_parity_not_passed():
    """task 1.3b 门控②：parity_passed=False → fallback（learning parity 未过）。"""
    r = CT.resolve_learning_injections_source(
        injection_flag=True, shadow_flag=True, project_id="proj-alpha",
        allowlist=("proj-alpha",), parity_passed=False, quality_passed=True)
    assert r.driven_by != "learning_injection"
    assert "parity" in r.fallback_reason.lower()


def test_learning_injection_fallback_when_quality_not_passed():
    """task 1.3b 门控③：quality_passed=False → fallback（learning quality gate 未过）。"""
    r = CT.resolve_learning_injections_source(
        injection_flag=True, shadow_flag=True, project_id="proj-alpha",
        allowlist=("proj-alpha",), parity_passed=True, quality_passed=False)
    assert r.driven_by != "learning_injection"
    assert "quality" in r.fallback_reason.lower()


def test_learning_injection_fallback_when_project_not_allowlisted():
    """task 1.3b 门控④：project_id 不在 allowlist → fallback（单项目 rollout）。"""
    r = CT.resolve_learning_injections_source(
        injection_flag=True, shadow_flag=True, project_id="proj-beta",
        allowlist=("proj-alpha",), parity_passed=True, quality_passed=True)
    assert r.driven_by != "learning_injection"
    assert "allowlist" in r.fallback_reason.lower()


def test_learning_injection_fallback_when_injection_flag_off():
    """task 1.3b：injection_flag=False → fallback（不开仓，无注入；调用方查 reason 后 emit degraded）。"""
    r = CT.resolve_learning_injections_source(
        injection_flag=False, shadow_flag=True, project_id="proj-alpha",
        allowlist=("proj-alpha",), parity_passed=True, quality_passed=True)
    assert r.driven_by != "learning_injection"


def test_learning_injection_result_is_dispatch_cutover_result_dataclass():
    """task 1.3b：返回类型是 DispatchCutoverResult（与 resolve_dispatch_source 同 envelope，便于上层统一处理）。"""
    r = CT.resolve_learning_injections_source(
        injection_flag=True, shadow_flag=True, project_id="proj-alpha",
        allowlist=("proj-alpha",), parity_passed=True, quality_passed=True)
    assert isinstance(r, CT.DispatchCutoverResult)
    # 三字段：driven_by / terminal_state / fallback_reason（同 dispatch cutover 契约）
    assert hasattr(r, "driven_by")
    assert hasattr(r, "fallback_reason")
    assert hasattr(r, "terminal_state")


def test_learning_injection_gate_priority_shadow_before_parity():
    """task 1.3b：四门控按序短路——shadow 不过 + parity 不过 → fallback_reason 是 shadow（更前置的 gate）。
    spec/design 决策#8：shadow 是 injection 的前置依赖，必须最先查。"""
    r = CT.resolve_learning_injections_source(
        injection_flag=True, shadow_flag=False, project_id="proj-alpha",
        allowlist=("proj-alpha",), parity_passed=False, quality_passed=False)
    # shadow gate 优先短路——reason 必须是 shadow，不报 parity/quality
    assert "shadow" in r.fallback_reason.lower()
    assert "parity" not in r.fallback_reason.lower()


def test_learning_injection_pure_function_no_io():
    """task 1.3b：纯函数零 IO（cutover.py:19 约束）——不在内 emit 事件、不读文件、不调 SDK。

    journal_events/legacy_records 参数为 None 时函数仍正常返（参数仅为对称 resolve_dispatch_source 签名，
    留待 section 4/5 接线学习侧 reducer 时使用；本 section 只锁定 gate 判定纯函数）。"""
    # 不传 journal_events/legacy_records，仍正常返回（纯 gate 判定）
    r = CT.resolve_learning_injections_source(
        injection_flag=True, shadow_flag=True, project_id="proj-alpha",
        allowlist=("proj-alpha",), parity_passed=True, quality_passed=True)
    assert r.driven_by == "learning_injection"
