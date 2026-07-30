#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_spec_durable_merge_revert.py — task 6.2（fail-safe-dispatch delta）：Exactly-once reconciliation of
merge/revert side effects 4 scenarios 的覆盖锁。

方案 C（task 6.1d）已落地 dispatch 级 crash 安全门，覆盖 scenario「Crash between intent and confirm」的安全
不变式（不 double-apply、不丢副作用 → has_open_intent True → halt+CRITICAL）。ancestry reconciler（scenarios
「Merge already applied」/「Revert via revert commit」/「Merge state unknown blocks」）由 task 6.1b 落地：
reconcile.ALLOWED_KINDS 已含 merge/revert 种 + LocalGitResolver ancestry check（merge-base --is-ancestor 三态）。
故 3 个 ancestry scenario 此处用真实 git repo 端到端断言（经 reconcile_side_effects + LocalGitResolver，非 FakeResolver）。

其余 spec scenarios（single-flight-auto-merge 13 + verified-dev-execution delta 5 + fail-safe-dispatch
admission 4）已被 task 2-5 既有测试覆盖（test_single_flight*.py / test_merge_phase.py / test_dev_agent_merge.py
/ test_report.py / test_circuit_breaker.py / test_critical_alert.py / test_main_status.py），见 tasks.md 6.2 映射注。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import merge_loop as ML  # noqa: E402
import reconcile  # noqa: E402


def _stamp():
    return "2026-07-30T00:00:00"


def test_scenario_crash_between_intent_and_confirm_is_resolvable(tmp_path):
    """spec fail-safe-dispatch「Crash between intent and confirm is resolvable」。

    方案 C（task 6.1d）落地语义：crash 在 intent(merge_started/revert_started) 后、confirm 前写发生 → 下轮
    has_open_intent→True→dispatch halt+CRITICAL。满足 spec 安全不变式「does not silently apply twice nor lose
    the side effect」——halt 不重 merge/revert（不 double-apply）+ journal 已记 intent（不丢副作用）。
    ancestry auto-resolve（reconciler 查 main 祖先 complete/block）属 6.1b follow-up；方案 C 的 halt 是安全
    退路：人工查 main_status.py 判 main 真实状态后手动 resume。
    """
    # Arrange: merge intent 已记，confirm 未写（crash 在 merge phase push 后、confirm 前）
    ML.record_event(tmp_path, "o/r", "prd1", "merge_started", stamp_fn=_stamp, branch="b", main_ref="main")
    # Act + Assert: 下轮 dispatch 安全门查 has_open_intent → True → halt（不重 merge，不丢副作用）
    assert ML.has_open_intent(tmp_path, "o/r", "prd1") is True
    # revert 闭环同理（crash 在 revert push 后、confirm 前）
    ML.record_event(tmp_path, "o/r", "prd2", "revert_started", stamp_fn=_stamp, merge_commit="abc1234")
    assert ML.has_open_intent(tmp_path, "o/r", "prd2") is True
    # 闭合后（confirm 写入）→ resolvable（非 open）→ 放行（人工查证后或正常闭环）
    ML.record_event(tmp_path, "o/r", "prd1", "merge_completed", stamp_fn=_stamp, merge_commit="abc1234")
    assert ML.has_open_intent(tmp_path, "o/r", "prd1") is False


def _git_init_main(repo):
    """初始化 repo，默认分支=main（symbolic-ref 显式建，避免 git init 默认分支随版本漂移）+ 配 user。"""
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd=repo, check=True)


def _head_sha(repo):
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


def test_scenario_merge_already_applied_not_repeated(tmp_path):
    """spec fail-safe-dispatch「Merge already applied is not repeated」。

    reconciler 重放 merge intent：target(merge_commit) 已是 main 祖先 → FOUND → confirmed（skip re-merge）。
    端到端经 reconcile_side_effects + LocalGitResolver（真实 git ancestry check），非 FakeResolver。"""
    repo = tmp_path / "repo"; repo.mkdir(); _git_init_main(repo)
    (repo / "a.txt").write_text("a", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "A"], cwd=repo, check=True)
    a_sha = _head_sha(repo)
    # main 继续推进（A 已是 main 祖先 = 模拟 merge_commit 已进 main 后 main 又前进）
    (repo / "b.txt").write_text("b", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "B"], cwd=repo, check=True)
    resolver = reconcile.LocalGitResolver(repo, main_ref="refs/heads/main")
    report = reconcile.reconcile_side_effects(iteration_id="iter_1",
                                               targets=[reconcile.SideEffectTarget("merge", a_sha)],
                                               resolver=resolver)
    assert len(report.confirmed) == 1 and report.confirmed[0].kind == "merge"   # 已进 main → skip re-merge
    assert report.external_known is True


def test_scenario_revert_detected_via_revert_commit(tmp_path):
    """spec fail-safe-dispatch「Revert already applied is detected via the revert commit」。

    ⚠️ git revert 不删原 merge commit——revert 后原 merge_commit 仍是 main 祖先，故查 merge_commit ancestry
    会误判 confirmed（以为还要 skip，实则 revert 已生效）。必须查 revert_commit ancestry 才正确反映 revert 已发生。
    端到端：revert_commit 是 main 祖先 → confirmed（skip re-revert）；并断言原 merge_commit 仍为祖先（spec v2.1）。"""
    repo = tmp_path / "repo"; repo.mkdir(); _git_init_main(repo)
    (repo / "a.txt").write_text("1", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "A"], cwd=repo, check=True)
    (repo / "a.txt").write_text("2", encoding="utf-8")          # 非空 M（git revert 拒绝还原空 commit）
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "M"], cwd=repo, check=True)   # M = 模拟 merge_commit
    m_sha = _head_sha(repo)
    subprocess.run(["git", "revert", "--no-edit", "HEAD"], cwd=repo, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)        # revert M → revert_commit
    rev_sha = _head_sha(repo)
    resolver = reconcile.LocalGitResolver(repo, main_ref="refs/heads/main")
    # 关键：查 revert_commit → confirmed（revert 已发生 → skip re-revert）
    report = reconcile.reconcile_side_effects(iteration_id="iter_1",
                                               targets=[reconcile.SideEffectTarget("revert", rev_sha)],
                                               resolver=resolver)
    assert len(report.confirmed) == 1 and report.confirmed[0].kind == "revert"
    # spec v2.1 不变量：原 merge_commit 仍是 main 祖先（git revert 不删它）——证明不可由 merge_commit 推断 revert 状态
    assert resolver.check("merge", m_sha) is True


def test_scenario_merge_state_unknown_blocks_retry(tmp_path):
    """spec fail-safe-dispatch「Merge state unknown blocks retry」。

    main_ref 缺失（未 fetch / remote unreachable）→ ancestry check UNKNOWN → reconcile unknown → block（不重 apply）。
    端到端经 reconcile_side_effects + LocalGitResolver（真实 git merge-base 失败）。"""
    repo = tmp_path / "repo"; repo.mkdir(); _git_init_main(repo)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "A"], cwd=repo, check=True)
    a_sha = _head_sha(repo)
    resolver = reconcile.LocalGitResolver(repo, main_ref="refs/remotes/origin/nope")   # 不存在的 ref → merge-base 失败
    report = reconcile.reconcile_side_effects(iteration_id="iter_1",
                                               targets=[reconcile.SideEffectTarget("merge", a_sha)],
                                               resolver=resolver)
    assert len(report.unknown) == 1 and not report.safe_to_retry   # UNKNOWN → block（不重 apply）
