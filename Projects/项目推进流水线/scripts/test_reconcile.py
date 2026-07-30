#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_reconcile.py — task 5.5 reconcile 驱动器 + task 5.7 recovery 端到端测试。

覆盖：
    - reconcile 谓词三态（confirmed/absent/unknown）+ 非法 kind fail-safe + idempotency key 稳定；
    - LocalGitResolver 真实 subprocess git（commit + 远端 push ls-remote）+ pr 返回 None；
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
import artifact_store  # noqa: E402
import journal as J  # noqa: E402
import loop_runtime as RT  # noqa: E402
import reconcile as RE  # noqa: E402
import ids as loop_ids  # noqa: E402
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
    """非法 kind（非 commit/push/pr/merge/revert）→ unknown（绝不构造非法幂等键，绝不盲目跳过/执行）。"""
    targets = [RE.SideEffectTarget("bogus_kind", "x")]
    report = RE.reconcile_side_effects(iteration_id="i", targets=targets,
                                       resolver=FakeResolver({}))
    assert report.unknown[0].state == "unknown" and report.unknown[0].key == ""
    assert report.external_known is False


def test_reconcile_test_evidence_kind_classified_three_states():
    """task 4.4：test evidence 幂等键纳入 reconcile 三态（confirmed=artifact 存在 / absent=缺失可重写 /
    unknown=查不到→BLOCK）。publication/retry 前对账 test evidence——unknown 不当 fresh green evidence。"""
    targets = [RE.SideEffectTarget("test", "sha256:abc")]
    # confirmed
    r1 = RE.reconcile_side_effects(iteration_id="iter_1", targets=targets,
                                   resolver=FakeResolver({("test", "sha256:abc"): True}))
    assert len(r1.confirmed) == 1 and r1.safe_to_retry
    # absent（pending）
    r2 = RE.reconcile_side_effects(iteration_id="iter_1", targets=targets,
                                   resolver=FakeResolver({("test", "sha256:abc"): False}))
    assert len(r2.pending) == 1 and r2.safe_to_retry
    # unknown → 不 safe（BLOCK）
    r3 = RE.reconcile_side_effects(iteration_id="iter_1", targets=targets,
                                   resolver=FakeResolver({("test", "sha256:abc"): None}))
    assert len(r3.unknown) == 1 and not r3.safe_to_retry


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


def test_local_git_resolver_commit_and_pr_local(tmp_path):
    """commit 查本地对象库（cat-file）；pr 本地查不到→None（交 GhPrResolver）。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    (repo / "f.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    r = RE.LocalGitResolver(repo)
    assert r.check("commit", "HEAD") is True            # commit 存在
    assert r.check("commit", "deadbeef") is False       # commit 不存在
    assert r.check("pr", "anything") is None            # pr 本地查不到（交 gh）


def test_local_git_resolver_push_uses_remote_truth(tmp_path):
    """P1-3：push resolver 查远端真源（ls-remote）——本地 branch 存在 ≠ 远端已 push。

    本地 bare repo 作 remote（origin）：feature 分支只在本地时远端无此 ref→absent(False)；
    push 到远端后→confirmed(True)；无 remote 的纯本地 repo→ls-remote 失败→unknown(None) fail-safe。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    bare = tmp_path / "remote.git"
    _git_init(repo)
    subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
    subprocess.run(["git", "remote", "add", "origin", str(bare)], cwd=repo, check=True)
    (repo / "f.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    subprocess.run(["git", "branch", "feature"], cwd=repo, check=True)
    r = RE.LocalGitResolver(repo)
    assert r.check("push", "feature") is False          # 只在本地、未 push → 远端无 → absent
    assert r.check("push", "nonexistent") is False      # 远端同样无 → absent
    subprocess.run(["git", "push", "-q", "origin", "feature"], cwd=repo, check=True)
    assert r.check("push", "feature") is True           # push 后远端有 → confirmed
    # 无 remote 的纯本地 repo → ls-remote 失败 → unknown（fail-safe，绝不盲目重放 push）
    noremote = tmp_path / "noremote"
    noremote.mkdir()
    _git_init(noremote)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "i"], cwd=noremote, check=True)
    subprocess.run(["git", "branch", "x"], cwd=noremote, check=True)
    assert RE.LocalGitResolver(noremote).check("push", "x") is None


def test_artifact_evidence_resolver_three_states(tmp_path):
    """task 4.4：ArtifactEvidenceResolver.check('test', digest) 真实查 artifact_store——artifact 存在且
    digest 匹配→confirmed(True)；缺失→absent(False)；digest 不匹配（篡改/损坏）→unknown(None, fail-safe)。
    非 test kind→None（交其他 resolver）。publication/retry 前 reconcile green evidence 是否仍在。"""
    root = tmp_path / "artifacts"
    ref = artifact_store.store(root, "all tests passed", kind="test_output", sensitivity="internal")
    res = RE.ArtifactEvidenceResolver(root)
    assert res.check("test", ref.digest) is True            # 存在 + digest 匹配 → confirmed
    assert res.check("test", "sha256:" + "0" * 64) is False  # 缺失 → absent（可重写）
    # 篡改内容致 digest 不匹配 → unknown（损坏 fail-safe，既不当 confirmed 也不当 absent）
    (root / artifact_store._bucketed_path(ref.digest)).write_text("TAMPERED", encoding="utf-8")
    assert res.check("test", ref.digest) is None
    assert res.check("commit", "x") is None                 # 非 test kind → None（不归本 resolver）


# ─── 6.1b：merge/revert 种 + ancestry resolver（D12 exactly-once reconcile）─────────
def test_merge_revert_kinds_in_allowed_lists():
    """6.1b：merge/revert 纳入幂等种白名单（ids + reconcile.ALLOWED_KINDS），不再是 illegal kind。"""
    assert "merge" in RE.ALLOWED_KINDS and "revert" in RE.ALLOWED_KINDS
    assert "merge" in loop_ids._IDEMPOTENCY_KINDS and "revert" in loop_ids._IDEMPOTENCY_KINDS


def test_idempotency_id_accepts_merge_revert():
    """merge/revert kind 可构造幂等键（不再 raise ValueError）。"""
    assert loop_ids.idempotency_id("merge", "iter_1", "abc1234").startswith("idem_")
    assert loop_ids.idempotency_id("revert", "iter_1", "def5678").startswith("idem_")


def test_local_git_resolver_merge_ancestry(tmp_path):
    """6.1b：merge 种 ancestry check——merge_commit 是否 main 祖先（git merge-base --is-ancestor）。
    是祖先→True（已合，skip）；非祖先→False（未合，可重 apply）。spec「Merge already applied is not repeated」。"""
    repo = tmp_path / "repo"; repo.mkdir(); _git_init(repo)
    subprocess.run(["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd=repo, check=True)   # 默认 branch=main
    (repo / "a.txt").write_text("a", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "A"], cwd=repo, check=True)           # A
    a_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
    (repo / "b.txt").write_text("b", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "B"], cwd=repo, check=True)           # B = main HEAD
    r = RE.LocalGitResolver(repo, main_ref="refs/heads/main")
    assert r.check("merge", a_sha) is True              # A 是 main 祖先 → 已合 → confirmed（skip）
    # 非祖先：side 分支上的 C（父=B；main 只到 B，C 不是 main 祖先；C 在 repo 对象库内可被 merge-base 判定）
    subprocess.run(["git", "checkout", "-q", "-b", "side"], cwd=repo, check=True)
    (repo / "c.txt").write_text("c", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "C"], cwd=repo, check=True)
    c_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
    subprocess.run(["git", "checkout", "-q", "main"], cwd=repo, check=True)
    assert r.check("merge", c_sha) is False             # C 不是 main 祖先 → 未合 → absent（可重 apply）


def test_local_git_resolver_revert_ancestry(tmp_path):
    """6.1b：revert 种 ancestry check——revert_commit（git revert 产的新 commit）是否 main 祖先。
    ⚠️ 不查 merge_commit：git revert 不删原 merge commit，revert 后原 merge commit 仍是 main 祖先
    （spec v2.1）。查 revert_commit ancestry 才正确反映 revert 是否已发生。"""
    repo = tmp_path / "repo"; repo.mkdir(); _git_init(repo)
    subprocess.run(["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd=repo, check=True)   # 默认 branch=main
    (repo / "a.txt").write_text("1", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "A"], cwd=repo, check=True)
    # 非空 M：让 a.txt 从 "1" 变 "2" 再提交（git revert 拒绝还原空 commit，故 M 必须带 diff）
    (repo / "a.txt").write_text("2", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "M"], cwd=repo, check=True)                     # M 模拟 merge commit（非空）
    subprocess.run(["git", "revert", "--no-edit", "HEAD"], cwd=repo, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)                          # revert M → revert_commit（-q 在 git 2.34 不支持→exit129）
    rev_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
    r = RE.LocalGitResolver(repo, main_ref="refs/heads/main")
    assert r.check("revert", rev_sha) is True           # revert_commit 是 main 祖先 → 已 revert → confirmed（skip）


def test_local_git_resolver_merge_unknown_when_main_ref_missing(tmp_path):
    """main_ref 不存在（未 fetch / ref 缺失）→ merge-base 失败 → None（fail-safe unknown，block，不重 apply）。
    spec「Merge state unknown blocks retry」。"""
    repo = tmp_path / "repo"; repo.mkdir(); _git_init(repo)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "A"], cwd=repo, check=True)
    a_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
    r = RE.LocalGitResolver(repo, main_ref="refs/remotes/origin/nope")   # 不存在的 ref
    assert r.check("merge", a_sha) is None              # ref 缺失 → merge-base 失败 → unknown → block


def test_reconcile_merge_found_is_confirmed_skip():
    """spec fail-safe-dispatch「Merge already applied is not repeated」：merge ancestry FOUND→confirmed（skip re-merge）。"""
    targets = [RE.SideEffectTarget("merge", "abc1234")]
    report = RE.reconcile_side_effects(iteration_id="iter_1", targets=targets,
                                       resolver=FakeResolver({("merge", "abc1234"): True}))
    assert len(report.confirmed) == 1 and report.confirmed[0].kind == "merge"
    assert report.external_known is True and report.safe_to_retry is True


def test_reconcile_merge_unknown_blocks():
    """spec fail-safe-dispatch「Merge state unknown blocks retry」：merge ancestry UNKNOWN→unknown（block，不重 apply）。"""
    targets = [RE.SideEffectTarget("merge", "abc1234")]
    report = RE.reconcile_side_effects(iteration_id="iter_1", targets=targets,
                                       resolver=FakeResolver({("merge", "abc1234"): None}))
    assert len(report.unknown) == 1 and not report.safe_to_retry


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
