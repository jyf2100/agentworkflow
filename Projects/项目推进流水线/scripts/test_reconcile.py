#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_reconcile.py — task 5.5 reconcile 驱动器 + task 5.7 recovery 端到端测试。

覆盖：
    - reconcile 谓词三态（confirmed/absent/unknown）+ 非法 kind fail-safe + idempotency key 稳定；
    - LocalGitResolver 真实 subprocess git（commit/branch）+ pr 返回 None；
    - CompositeResolver 首 non-None 胜出；
    - recover_iteration 端到端（reconcile→policy→RecoveryPlan）：PR 前 crash 对账 pending、
      unknown→BLOCK、session 缺失→NEW_SESSION、预算耗尽→STOP、中部损坏→corruption 传播。
AAA；模块零 SDK。跑：python3 -m pytest scripts/test_reconcile.py -q
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import journal as J  # noqa: E402
import loop_runtime as RT  # noqa: E402
import reconcile as RE  # noqa: E402
import retry_policy as RP  # noqa: E402
from session_meta import ResultSubtype, SessionMeta, SessionStore  # noqa: E402


class FakeResolver:
    """测试用 resolver：{(kind,target): True/False/None}。"""
    def __init__(self, mapping):
        self.m = dict(mapping)

    def check(self, kind, target):
        return self.m.get((kind, target))


def _stamp() -> str:
    return "2026-07-21T00:00:00Z"


def _emit(path, seq, run="run_1", iter_id="iter_1", prd="prd_1"):
    sj = RT.ShadowJournal(path, run, _stamp, enabled=True)
    for et in seq:
        sj.emit(et, iter_id, prd, payload={"base": "abc"})
    return path


def _healthy_session() -> SessionMeta:
    return SessionMeta(iteration_id="iter_1", session_id="s1", result_subtype=ResultSubtype.SUCCESS)


def _plan(tmp_path, resolver, *, session=None, budget=None,
          seq=("planned", "running", "agent_finished", "verifying", "publish_ready"),
          targets=None):
    """端到端 recover_iteration helper。"""
    jf = _emit(tmp_path / "j.jsonl", list(seq))
    store = SessionStore(tmp_path / "sess")
    if session is not None:
        store.save(session)
    return RE.recover_iteration(
        journal_path=jf, run_id="run_1", prd_id="prd_1", iteration_id="iter_1", base="abc",
        prd_content="# 目标\n\n## 验收标准\n- 条件\n",
        targets=targets or [RE.SideEffectTarget("pr", "owner/repo:auto/b")],
        resolver=resolver, session_store=store,
        budget=budget or RP.BudgetState(limits=RP.BudgetLimits()))


# ─── reconcile 谓词三态 ──────────────────────────────────────────────────
def test_reconcile_confirmed_absent_unknown_classification():
    targets = [
        RE.SideEffectTarget("commit", "abc123"),
        RE.SideEffectTarget("push", "feature"),
        RE.SideEffectTarget("pr", "owner/repo:auto/b"),
    ]
    resolver = FakeResolver({("commit", "abc123"): True, ("push", "feature"): False,
                             ("pr", "owner/repo:auto/b"): None})
    report = RE.reconcile_side_effects(iteration_id="iter_1", targets=targets, resolver=resolver)
    assert len(report.confirmed) == 1 and report.confirmed[0].kind == "commit"
    assert len(report.pending) == 1 and report.pending[0].kind == "push"
    assert len(report.unknown) == 1 and report.unknown[0].kind == "pr"
    assert report.external_known is False and report.safe_to_retry is False


def test_reconcile_all_known_is_safe_to_retry():
    targets = [RE.SideEffectTarget("pr", "r:b")]
    report = RE.reconcile_side_effects(iteration_id="i", targets=targets,
                                       resolver=FakeResolver({("pr", "r:b"): False}))
    assert report.external_known is True and report.safe_to_retry is True
    assert report.pending[0].state == "absent"


def test_reconcile_illegal_kind_is_unknown_fail_safe():
    """非法 kind（非 commit/push/pr）→ unknown（绝不构造非法幂等键，绝不盲目跳过/执行）。"""
    targets = [RE.SideEffectTarget("merge", "x")]
    report = RE.reconcile_side_effects(iteration_id="i", targets=targets,
                                       resolver=FakeResolver({}))
    assert report.unknown[0].state == "unknown" and report.unknown[0].key == ""
    assert report.external_known is False


def test_reconcile_idempotency_key_stable_across_replay():
    """同 (kind, iteration, target) → 同 key（exactly-once 比对键，跨重放稳定）。"""
    t = [RE.SideEffectTarget("pr", "owner/repo:auto/b")]
    r1 = RE.reconcile_side_effects(iteration_id="iter_1", targets=t, resolver=FakeResolver({("pr", "owner/repo:auto/b"): False}))
    r2 = RE.reconcile_side_effects(iteration_id="iter_1", targets=t, resolver=FakeResolver({("pr", "owner/repo:auto/b"): False}))
    assert r1.pending[0].key == r2.pending[0].key and r1.pending[0].key.startswith("idem_")


def test_reconcile_resolver_exception_is_unknown_fail_safe():
    class Boom:
        def check(self, k, t): raise RuntimeError("gh exploded")
    report = RE.reconcile_side_effects(iteration_id="i",
                                       targets=[RE.SideEffectTarget("pr", "r:b")], resolver=Boom())
    assert report.unknown and report.external_known is False


# ─── LocalGitResolver：真实 subprocess git ─────────────────────────────────
def _git_init(repo):
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)


def test_local_git_resolver_branch_and_commit(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    (repo / "f.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    subprocess.run(["git", "branch", "feature"], cwd=repo, check=True)
    r = RE.LocalGitResolver(repo)
    assert r.check("push", "feature") is True          # branch 存在
    assert r.check("push", "nonexistent") is False      # branch 不存在
    assert r.check("commit", "HEAD") is True            # commit 存在
    assert r.check("pr", "anything") is None            # pr 本地查不到（交 gh）


def test_composite_resolver_first_non_none_wins():
    class A:
        def check(self, k, t): return None if k == "pr" else True
    class B:
        def check(self, k, t): return False
    c = RE.CompositeResolver([A(), B()])
    assert c.check("commit", "x") is True     # A 非 None 胜出
    assert c.check("pr", "y") is False         # A None → B False


# ─── recover_iteration 端到端（task 5.7 七场景的 driver 层）─────────────────
def test_recover_pr_before_crash_reconciles_pr_as_pending(tmp_path):
    """5.5 核心：PR 前 crash（publish_ready 已落盘）→ reconcile 知 PR 未开（pending）→ safe retry。"""
    plan = _plan(tmp_path, FakeResolver({("pr", "owner/repo:auto/b"): False}), session=_healthy_session())
    assert plan.iteration_status == "publish_ready"
    assert plan.reconciliation.external_known is True
    assert plan.reconciliation.pending[0].kind == "pr"     # PR 未开 → 待执行（不重复开已开的）
    assert plan.decision.mode is RP.RetryMode.RESUME        # session 健康 + external known
    assert plan.context.objective == "目标"
    assert "publish" in plan.context.suggested_next_step.lower()


def test_recover_unknown_external_state_blocks(tmp_path):
    """5.7 external-state block：reconcile 查不到（unknown）→ BLOCK，不消耗 retry。"""
    plan = _plan(tmp_path, FakeResolver({("pr", "owner/repo:auto/b"): None}), session=_healthy_session())
    assert plan.decision.mode is RP.RetryMode.BLOCK
    assert plan.reconciliation.external_known is False and not plan.decision.consumes_retry


def test_recover_missing_session_falls_back_new_session(tmp_path):
    """5.7 missing session fallback：session metadata 缺失 → NEW_SESSION。"""
    plan = _plan(tmp_path, FakeResolver({("pr", "owner/repo:auto/b"): False}), session=None)
    assert plan.decision.mode is RP.RetryMode.NEW_SESSION
    assert "missing session" in plan.decision.reason


def test_recover_exhausted_budget_stops(tmp_path):
    """5.7 exhausted budget：SDK retry 预算耗尽 → STOP（优先于 session/reconcile）。"""
    b = RP.BudgetState(limits=RP.BudgetLimits(), sdk_retries_used=5)
    plan = _plan(tmp_path, FakeResolver({("pr", "owner/repo:auto/b"): None}),
                 session=_healthy_session(), budget=b)
    assert plan.decision.mode is RP.RetryMode.STOP


def test_recover_context_carries_prd_and_status(tmp_path):
    """recovery context 从 immutable PRD + 归约态派生（不改 PRD）。"""
    plan = _plan(tmp_path, FakeResolver({("pr", "owner/repo:auto/b"): False}), session=_healthy_session())
    assert plan.context.status == "publish_ready"
    assert plan.context.acceptance_criteria == ("条件",)
    assert plan.context.iteration_id == "iter_1"


def test_recover_middle_corruption_propagates(tmp_path):
    """journal 中部损坏 → JournalCorruptionError 传播（state_corrupt 需运维，不自动恢复）。"""
    jf = _emit(tmp_path / "j.jsonl", ["planned", "running"])
    lines = jf.read_text(encoding="utf-8").splitlines()
    lines.insert(1, "{坏行 NOT JSON}")            # 中部损坏
    jf.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(J.JournalCorruptionError):
        RE.recover_iteration(journal_path=jf, run_id="run_1", prd_id="prd_1",
                             iteration_id="iter_1", base="abc", prd_content="# T",
                             targets=[RE.SideEffectTarget("pr", "r:b")],
                             resolver=FakeResolver({}), session_store=SessionStore(tmp_path / "s"),
                             budget=RP.BudgetState(limits=RP.BudgetLimits()))


def test_recover_published_with_pr_confirmed_skips_side_effect(tmp_path):
    """PR 已开（confirmed）→ 不在 pending（retry 时跳过，exactly-once effective）。"""
    plan = _plan(tmp_path, FakeResolver({("pr", "owner/repo:auto/b"): True}),
                 session=_healthy_session(),
                 seq=("planned", "running", "agent_finished", "verifying", "publish_ready"))
    assert plan.reconciliation.confirmed[0].kind == "pr"
    assert plan.reconciliation.pending == ()      # 无 pending（PR 已开，不重复）
