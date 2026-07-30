#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_merge_phase.py — single-flight-auto-merge task 3.1：rebase 三态判定单测。

验证 spec「Three-state rebase safety before merge」契约（D2 / D7）：
  - ``CLEAN`` 须**正向证据**聚合：fetch 成功 + rebase exit0 + 干净工作树 + 无冲突标记 + 未超时；
    缺任一正向证据 → ``UNKNOWN``（spec scenario「No positive evidence is not clean」）。
  - ``CONFLICT``：rebase 在 fetched main 上**明确报告冲突**（fetch 成功 + 非超时 + 冲突标记存在）
    → triage（rebase_conflict）。
  - ``UNKNOWN``：fetch 失败 / exit≠0 / 工作树脏 / 超时 / 缺证 / 超时残留冲突标记
    → triage（rebase_unknown），**不当代干净**（fail-safe-dispatch 不变式：UNKNOWN=阻断，绝不静默当 CLEAN）。

task 3.1 只交付**纯机械判定层**（确定性、零 git/SDK）：给定 rebase 执行证据 → 判三态。执行（fetch+rebase）
经 dev-agent.py 在目标仓内跑（D6：控制面只发 cmd，不直接持 git 写句柄）——执行接线属 task 3.2（merge 闭环）。

AAA 结构；注入 ``RebaseEvidence`` 保测试确定性（不触系统时间/git）。跑：python -m pytest scripts/test_merge_phase.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import merge_phase as MP  # noqa: E402  (RED：模块尚未实现)
from merge_phase import RebaseOutcome  # noqa: E402


def _ev(**kw) -> MP.RebaseEvidence:
    """构造 RebaseEvidence，默认全正向（CLEAN 候选），测试只覆写被测字段。"""
    base = dict(fetch_ok=True, rebase_rc=0, worktree_clean=True,
                conflict_files=0, timed_out=False)
    base.update(kw)
    return MP.RebaseEvidence(**base)


# ─── CLEAN：须全正向证据齐（spec「asserted only on positive evidence」）────────
def test_clean_when_all_positive_evidence():
    # 全正向证据齐 → CLEAN
    assert MP.classify_rebase(_ev()) is RebaseOutcome.CLEAN


@pytest.mark.parametrize("field,value,why", [
    ("fetch_ok", False, "fetch 失败 → 缺正向证据 → UNKNOWN"),
    ("rebase_rc", 1, "rebase exit≠0 → UNKNOWN"),
    ("rebase_rc", None, "rebase_rc 缺（未跑/超时被杀未取到）→ UNKNOWN"),
    ("worktree_clean", False, "工作树脏（rebase 残留）→ UNKNOWN"),
    ("timed_out", True, "超时杀留半完成状态 → UNKNOWN（spec scenario「No positive evidence」）"),
])
def test_not_clean_when_any_positive_evidence_missing(field, value, why):
    # 缺任一正向证据 → UNKNOWN（非 CLEAN）—— spec「absence of positive evidence MUST yield UNKNOWN」
    assert MP.classify_rebase(_ev(**{field: value})) is RebaseOutcome.UNKNOWN, why


def test_not_clean_when_rebase_rc_zero_but_timed_out():
    # 即使 rebase_rc 巧合为 0，timeout 已破坏正向证据 → UNKNOWN（timeout 优先于 rc）
    assert MP.classify_rebase(_ev(rebase_rc=0, timed_out=True)) is RebaseOutcome.UNKNOWN


# ─── CONFLICT：rebase 明确报告冲突 ──────────────────────────────────────────
def test_conflict_when_markers_present_on_fetched_main():
    # fetch 成功 + 非超时 + 冲突标记存在 = rebase 明确报告冲突 → CONFLICT（spec scenario「Rebase conflict」）
    # 注：此时 worktree 必然脏（冲突未解决）、rebase_rc 必非 0 —— CONFLICT 优先于这些
    assert MP.classify_rebase(_ev(conflict_files=2, rebase_rc=1, worktree_clean=False)) is RebaseOutcome.CONFLICT


def test_unknown_when_markers_but_fetch_failed():
    # fetch 失败 + 冲突标记：base 过时不可信，冲突标记不算「在当前 main 上明确报告」→ UNKNOWN
    assert MP.classify_rebase(_ev(fetch_ok=False, conflict_files=2)) is RebaseOutcome.UNKNOWN


def test_unknown_when_markers_but_timed_out():
    # 超时下的冲突标记 = 半完成残留，非明确报告 → UNKNOWN（不算 CONFLICT）
    assert MP.classify_rebase(_ev(conflict_files=3, timed_out=True)) is RebaseOutcome.UNKNOWN


# ─── 三态互斥 / 边界 ─────────────────────────────────────────────────────────
def test_zero_conflict_explicit_clean():
    # 显式 conflict_files=0 + 全正向 → CLEAN（conflict_files=0 是 CLEAN 正向前提之一）
    assert MP.classify_rebase(_ev(conflict_files=0)) is RebaseOutcome.CLEAN


def test_clean_requires_no_markers():
    # 全正向但 conflict_files=1（非超时）→ CONFLICT 而非 CLEAN（冲突标记优先）
    assert MP.classify_rebase(_ev(conflict_files=1)) is RebaseOutcome.CONFLICT


# ─── MergeResult（task 3.2：merge phase 整体结果 + triage 路由）─────────────────
# dev-agent merge phase 执行后吐 JSON，控制面 parse 成 MergeResult；merged/triage_reason 驱动 dispatch 收尾。
def _result(**kw) -> MP.MergeResult:
    base = dict(rebase_outcome=RebaseOutcome.CLEAN, merge_commit="abc123",
                push_failed=False, evidence=MP.RebaseEvidence(
                    fetch_ok=True, rebase_rc=0, worktree_clean=True,
                    conflict_files=0, timed_out=False))
    base.update(kw)
    return MP.MergeResult(**base)


def test_merge_result_merged_when_clean_and_pushed():
    assert _result().merged is True
    assert _result().triage_reason is None


def test_merge_result_not_merged_when_no_commit():
    # rebase CLEAN 但 merge_commit 缺（未真合）→ 未合 main
    assert _result(merge_commit=None).merged is False


def test_merge_result_push_failed_routes_triage():
    # spec「Push fails → main unchanged」：rebase CLEAN + 本地 merge 成功但 push reject → 未合 main，triage push_failed
    assert _result(push_failed=True).merged is False
    assert _result(push_failed=True).triage_reason == "push_failed"


def test_merge_result_triage_conflict():
    r = _result(rebase_outcome=RebaseOutcome.CONFLICT, merge_commit=None,
                evidence=MP.RebaseEvidence(True, 1, False, 2, False))
    assert r.merged is False and r.triage_reason == "rebase_conflict"


def test_merge_result_triage_unknown():
    r = _result(rebase_outcome=RebaseOutcome.UNKNOWN, merge_commit=None,
                evidence=MP.RebaseEvidence(False, None, False, 0, False))
    assert r.triage_reason == "rebase_unknown"


# ─── parse_merge_result（dev-agent JSON → MergeResult，fail-safe 坏→UNKNOWN）────
def _payload(**kw) -> dict:
    base = {"phase": "merge", "rebase_outcome": "clean", "merge_commit": "deadbeef",
            "push_failed": False,
            "rebase": {"fetch_ok": True, "rebase_rc": 0, "worktree_clean": True,
                       "conflict_files": 0, "timed_out": False}}
    base.update(kw)
    return base


def test_parse_merge_result_clean():
    r = MP.parse_merge_result(_payload())
    assert r.merged and r.merge_commit == "deadbeef"


def test_parse_merge_result_push_failed():
    # rebase_outcome=clean + push_failed=True → 未合 main（main unchanged），triage push_failed
    r = MP.parse_merge_result(_payload(rebase_outcome="clean", merge_commit=None, push_failed=True))
    assert not r.merged and r.triage_reason == "push_failed"


def test_parse_merge_result_conflict():
    r = MP.parse_merge_result(_payload(rebase_outcome="conflict", merge_commit=None,
                                       rebase={"fetch_ok": True, "rebase_rc": 1,
                                               "worktree_clean": False, "conflict_files": 2,
                                               "timed_out": False}))
    assert r.triage_reason == "rebase_conflict"


def test_parse_merge_result_malformed_is_unknown():
    # 坏/缺字段 payload → fail-safe UNKNOWN（绝不误判 merged）
    assert MP.parse_merge_result({"bad": "payload"}).merged is False
    assert MP.parse_merge_result(None).rebase_outcome is RebaseOutcome.UNKNOWN   # 非 dict


# ─── build_merge_cmd（控制面构造 dev-agent --phase merge 命令）──────────────────
def test_build_merge_cmd_shape(tmp_path):
    cmd = MP.build_merge_cmd(python="python3", dev_agent_py=tmp_path / "dev-agent.py",
                             branch="auto/x", main_ref="main", prd_id="prd-1",
                             state_dir=str(tmp_path))
    assert cmd[0] == "python3"
    assert "--phase" in cmd and cmd[cmd.index("--phase") + 1] == "merge"
    assert "--branch" in cmd and "auto/x" in cmd
    assert "--prd-id" in cmd and "prd-1" in cmd
    assert "--main" in cmd
    assert "--state-dir" in cmd     # 传了 state_dir → 带


def test_build_merge_cmd_state_dir_optional(tmp_path):
    cmd = MP.build_merge_cmd(python="python3", dev_agent_py=tmp_path / "dev-agent.py",
                             branch="auto/x", main_ref="main", prd_id="p", state_dir=None)
    assert "--state-dir" not in cmd


# ─── build_classify_cmd（task 7.1a shadow：控制面构造 dev-agent --phase merge --classify-only）─────
def test_build_classify_cmd_carries_classify_only_flag(tmp_path):
    cmd = MP.build_classify_cmd(python="python3", dev_agent_py=tmp_path / "dev-agent.py",
                                branch="auto/x", main_ref="main", prd_id="prd-1",
                                state_dir=str(tmp_path))
    assert cmd[0] == "python3"
    assert "--phase" in cmd and cmd[cmd.index("--phase") + 1] == "merge"
    assert "--classify-only" in cmd, "shadow classify-only 须透传 --classify-only（dev-agent CLEAN 短路开关）"
    assert "--branch" in cmd and "auto/x" in cmd
    assert "--prd-id" in cmd and "prd-1" in cmd
    assert "--main" in cmd
    assert "--state-dir" in cmd


def test_build_classify_cmd_state_dir_optional(tmp_path):
    cmd = MP.build_classify_cmd(python="python3", dev_agent_py=tmp_path / "dev-agent.py",
                                branch="auto/x", main_ref="main", prd_id="p", state_dir=None)
    assert "--state-dir" not in cmd


# ═══ task 4.1 / 4.2：post-merge main 全量测试三态判定（spec「Post-merge main verification」）═══
# D8：post-merge 跑的是**集成后 main 全量 suite**（基线=main，≠ verify 的 candidate branch）——覆盖面与基线都
# 不同，故非 verify 的重复。结果三态：PASS 保留+放行；FAIL→revert(4.3)；UNKNOWN→keep+halt+CRITICAL（不 auto-revert）。
# fail-safe 判定（同 classify_rebase 结构）：PASS 须**全正向证据**（确实跑过 + exit0 + 未超时）；缺证=UNKNOWN。
def _pm(**kw) -> MP.PostMergeEvidence:
    """构造 PostMergeEvidence，默认全正向（PASS 候选），测试只覆写被测字段。"""
    base = dict(ran=True, test_rc=0, timed_out=False)
    base.update(kw)
    return MP.PostMergeEvidence(**base)


def test_post_merge_pass_when_all_positive_evidence():
    # 确实跑过 + exit0 + 未超时 → PASS（spec scenario「Main stays green after merge」）
    assert MP.classify_post_merge(_pm()) is MP.PostMergeVerdict.PASS


@pytest.mark.parametrize("field,value,why", [
    ("ran", False, "测试未跑/环境失败（无 exit code 可信）→ UNKNOWN（非 PASS）"),
    ("test_rc", None, "未取到 exit code（超时被杀/异常）→ UNKNOWN"),
    ("timed_out", True, "超时 → 无法判定（半跑完）→ UNKNOWN（spec scenario「Post-merge test result unknown」）"),
])
def test_post_merge_unknown_when_any_positive_evidence_missing(field, value, why):
    # 缺任一正向证据 → UNKNOWN（绝不误判 PASS——否则烂代码留 main 不触发 revert，安全网失效）
    assert MP.classify_post_merge(_pm(**{field: value})) is MP.PostMergeVerdict.UNKNOWN, why


def test_post_merge_fail_when_ran_and_nonzero_exit():
    # 确实跑过 + 非0退出（非超时）= 明确测试失败 → FAIL（spec scenario「Main goes red」触发 revert）
    assert MP.classify_post_merge(_pm(test_rc=1)) is MP.PostMergeVerdict.FAIL
    assert MP.classify_post_merge(_pm(test_rc=130)) is MP.PostMergeVerdict.FAIL   # SIGTERM-like


def test_post_merge_timeout_overrides_exit_zero():
    # 即使 test_rc 巧合为 0，超时已破坏正向证据 → UNKNOWN（timeout 优先于 rc；非 PASS）
    assert MP.classify_post_merge(_pm(test_rc=0, timed_out=True)) is MP.PostMergeVerdict.UNKNOWN


def test_post_merge_not_run_is_unknown_even_if_rc_zero():
    # ran=False + rc=0（命令根本没执行，rc 无意义）→ UNKNOWN（fail-safe：不当代绿）
    assert MP.classify_post_merge(_pm(ran=False, test_rc=0)) is MP.PostMergeVerdict.UNKNOWN


# ─── parse_post_merge_result（dev-agent JSON → PostMergeResult，fail-safe 坏→UNKNOWN）─────
def _pm_payload(**kw) -> dict:
    base = {"phase": "post-merge-test", "verdict": "pass",
            "ran": True, "test_rc": 0, "timed_out": False}
    base.update(kw)
    return base


def test_parse_post_merge_pass():
    r = MP.parse_post_merge_result(_pm_payload())
    assert r.verdict is MP.PostMergeVerdict.PASS


def test_parse_post_merge_fail():
    r = MP.parse_post_merge_result(_pm_payload(verdict="fail", test_rc=1))
    assert r.verdict is MP.PostMergeVerdict.FAIL


def test_parse_post_merge_malformed_is_unknown():
    # 坏/缺字段 payload → fail-safe UNKNOWN（绝不误判 PASS——否则不 revert）
    assert MP.parse_post_merge_result({"bad": "payload"}).verdict is MP.PostMergeVerdict.UNKNOWN
    assert MP.parse_post_merge_result(None).verdict is MP.PostMergeVerdict.UNKNOWN   # 非 dict


# ═══ task 4.3：auto-revert 三态判定（spec「Post-merge ... revert itself SHALL be three-state」）═══
# D3 / D7：post-merge FAIL → revert 本次自动合入产出的单一 merge commit（``git revert -m 1``，journal 记其 sha）。
# revert 本身三态：REVERTED→triage(post_merge_red_reverted)+放行；CONFLICT/UNKNOWN→halt 整仓+CRITICAL
# （**不 continue，不强改 main**——spec scenario「Revert itself fails halts the queue」）。
# fail-safe（同 classify_rebase/post_merge）：REVERTED 须**全正向证据**；push reject=UNKNOWN（远端 main 仍红）。
def _rv(**kw) -> MP.RevertEvidence:
    """构造 RevertEvidence，默认全正向（REVERTED 候选），测试只覆写被测字段。"""
    base = dict(revert_rc=0, conflict_files=0, push_failed=False, timed_out=False)
    base.update(kw)
    return MP.RevertEvidence(**base)


def test_revert_reverted_when_all_positive_evidence():
    # rc0 + 无冲突 + push 成功 + 未超时 → REVERTED（spec scenario「revert succeeds」）
    assert MP.classify_revert(_rv()) is MP.RevertOutcome.REVERTED


@pytest.mark.parametrize("field,value,why", [
    ("timed_out", True, "revert 超时 → 无法判定 → UNKNOWN（halt，不 continue）"),
    ("revert_rc", None, "未取到 revert exit code → UNKNOWN"),
    ("push_failed", True, "revert 本地成功但 push reject → 远端 main 仍红 → UNKNOWN（halt）"),
])
def test_revert_unknown_when_any_positive_evidence_missing(field, value, why):
    # push reject / 超时 / 无 rc → UNKNOWN（绝不误判 REVERTED 放行——否则烂代码留 main + 队列续跑叠加）
    assert MP.classify_revert(_rv(**{field: value})) is MP.RevertOutcome.UNKNOWN, why


def test_revert_conflict_when_markers_present():
    # revert 产生冲突标记（非超时）→ CONFLICT（revert --abort；spec「revert fails halts」）
    assert MP.classify_revert(_rv(conflict_files=2, revert_rc=1)) is MP.RevertOutcome.CONFLICT


def test_revert_push_failed_overrides_clean_local():
    # 本地 revert 干净（rc0+无冲突）但 push reject → 远端未 revert → UNKNOWN（halt，非 REVERTED）
    assert MP.classify_revert(_rv(push_failed=True)) is MP.RevertOutcome.UNKNOWN


def test_revert_timeout_overrides_exit_zero():
    # 即使 revert_rc 巧合 0，超时已破坏正向证据 → UNKNOWN（非 REVERTED）
    assert MP.classify_revert(_rv(revert_rc=0, timed_out=True)) is MP.RevertOutcome.UNKNOWN


# ─── parse_revert_result（dev-agent JSON → RevertResult，fail-safe 坏→UNKNOWN；记 revert_commit）──
def _rv_payload(**kw) -> dict:
    base = {"phase": "revert", "outcome": "reverted", "revert_commit": "face0ff",
            "revert_rc": 0, "conflict_files": 0, "push_failed": False, "timed_out": False}
    base.update(kw)
    return base


def test_parse_revert_reverted_records_commit():
    r = MP.parse_revert_result(_rv_payload())
    assert r.outcome is MP.RevertOutcome.REVERTED
    assert r.revert_commit == "face0ff"   # 记 revert_commit sha 供 exactly-once reconcile（D12 / task 6.1b）


def test_parse_revert_conflict():
    r = MP.parse_revert_result(_rv_payload(outcome="conflict", revert_commit=None,
                                           conflict_files=1, revert_rc=1))
    assert r.outcome is MP.RevertOutcome.CONFLICT


def test_parse_revert_malformed_is_unknown():
    # 坏 payload → fail-safe UNKNOWN（绝不误判 REVERTED——否则不 halt）
    assert MP.parse_revert_result({"bad": "payload"}).outcome is MP.RevertOutcome.UNKNOWN
    assert MP.parse_revert_result(None).outcome is MP.RevertOutcome.UNKNOWN


# ─── build_revert_cmd / build_post_merge_cmd（控制面构造 dev-agent --phase 命令）─────────
def test_build_revert_cmd_shape(tmp_path):
    cmd = MP.build_revert_cmd(python="python3", dev_agent_py=tmp_path / "dev-agent.py",
                              merge_commit="abc123", main_ref="main", prd_id="prd-1",
                              state_dir=str(tmp_path))
    assert cmd[0] == "python3"
    assert "--phase" in cmd and cmd[cmd.index("--phase") + 1] == "revert"
    assert "--merge-commit" in cmd and "abc123" in cmd
    assert "--main" in cmd and "--prd-id" in cmd and "--state-dir" in cmd


def test_build_post_merge_cmd_shape(tmp_path):
    cmd = MP.build_post_merge_cmd(python="python3", dev_agent_py=tmp_path / "dev-agent.py",
                                  test_cmd="npm test", main_ref="main", prd_id="prd-1",
                                  state_dir=str(tmp_path), timeout=1800)
    assert "--phase" in cmd and cmd[cmd.index("--phase") + 1] == "post-merge-test"
    assert "--test-cmd" in cmd and "npm test" in cmd
    assert "--timeout" in cmd   # D10 post-merge test wall-clock 上界透传
