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
