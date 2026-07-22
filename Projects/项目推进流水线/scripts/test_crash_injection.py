#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_crash_injection.py — task 3.5 副作用边界崩溃注入测试。

recovery driver（reconcile-before-retry 主循环）是 Section 5；本测试锁定其**契约基础**——
journal 是崩溃恢复真源（design 决策#1）：进程在副作用边界（commit/push/PR）崩溃后，重读 journal +
``loop_state.reduce`` 归约出崩溃前的精确迭代状态，recovery 据此决定 reconcile（查 GitHub 已开 PR 否）
再 retry，**绝不依赖运行时内存、绝不盲目重放副作用**（exactly-once effective，task 5.5/8.3 的前置）。

覆盖：
    - 崩溃在「开 PR 前」（publish_ready 已落盘、published 未到）→ 归约 PUBLISH_READY → recovery 须 reconcile；
    - 崩溃在「开 PR 后」（published 已落盘）→ 归约 PUBLISHED → exactly-once，不重试；
    - 末尾半行截断（append 中途崩溃）→ 容忍，从前一合法边界归约；
    - 中部损坏 → fail-closed（state_corrupt），不自动恢复（需运维）；
    - idempotency_id 跨重放确定性 → reconcile 据此跳过已执行副作用；
    - recovery 据归约态（非内存）决定 reconcile 路径。

模块零 SDK；AAA。跑：python3 -m pytest scripts/test_crash_injection.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import journal as J  # noqa: E402
import loop_runtime as RT  # noqa: E402
import loop_state as L  # noqa: E402
import ids  # noqa: E402


def _stamp() -> str:
    return "2026-07-21T00:00:00Z"


def _emit_seq(path, seq, run_id="run_1", iter_id="iter_1", prd_id="prd_1"):
    """用 ShadowJournal 顺序 emit 一串事件（模拟 dispatch 主路径旁路记录）。返回 journal path。"""
    sj = RT.ShadowJournal(path, run_id, _stamp, enabled=True)
    for et in seq:
        sj.emit(et, iter_id, prd_id, payload={"base": "abc"})
    return path


def _reduce(path, iter_id="iter_1"):
    return L.reduce(J.read_events(path),
                    initial=L.initial_state("run_1", "prd_1", iter_id, base="abc"))


# ─── 副作用边界崩溃：PR 前 / PR 后 ──────────────────────────────────────────
def test_crash_before_pr_recovers_to_publish_ready(tmp_path):
    """**task 3.5 核心**：崩溃在「开 PR 前」——publish_ready 已落盘、published 未 emit。

    重启后 read+reduce 归约出 PUBLISH_READY（PR 尚未确认开）→ recovery 须先 reconcile_pr（查 GitHub
    是否已有 PR）再决定补开，**绝不盲目重投 dev**（避免重复 dev 成本 / 重复 commit）。"""
    jf = _emit_seq(tmp_path / "j.jsonl",
                   ["planned", "running", "agent_finished", "verifying", "publish_ready"])
    # 模拟崩溃：进程在此终止（journal 末尾是 publish_ready，无 published）
    state = _reduce(jf)
    assert state.status is L.IterationStatus.PUBLISH_READY      # 非 PUBLISHED → PR 未确认开
    assert not L.is_terminal(state.status)                       # 非终态 → recovery 有工作（reconcile+publish）


def test_crash_after_pr_stays_published_no_retry(tmp_path):
    """崩溃在「开 PR 后」——published 已落盘。归约 PUBLISHED → exactly-once：recovery 不重试、不补开 PR。"""
    jf = _emit_seq(tmp_path / "j.jsonl",
                   ["planned", "running", "agent_finished", "verifying", "publish_ready", "published"])
    state = _reduce(jf)
    assert state.status is L.IterationStatus.PUBLISHED           # 已交付终态 → 无需 reconcile/retry
    assert L.is_terminal(state.status)


def test_crash_truncated_tail_recovers_from_prior_boundary(tmp_path):
    """副作用边界 append 中途崩溃 → 末尾半行。read_events 容忍半行（丢弃），从前一合法边界归约。

    场景：publish_ready 已完整落盘，published 写到一半进程被杀 → 半行被丢弃，归约停 publish_ready。"""
    jf = _emit_seq(tmp_path / "j.jsonl",
                   ["planned", "running", "agent_finished", "verifying", "publish_ready"])
    with open(jf, "a", encoding="utf-8") as f:        # 手工追加半截 published（模拟 append 中途崩溃）
        f.write('{"event_type":"published","ev')      # 半行 JSON（无换行、不完整）
    report = J.validate_journal(jf)
    assert report.tail_truncated is True              # 末尾半行被识别为截断
    assert not report.is_fail_closed                  # 非 fail-closed（中部无损坏）
    state = _reduce(jf)
    assert state.status is L.IterationStatus.PUBLISH_READY   # 从 publish_ready 恢复（半行丢弃）


def test_middle_corruption_blocks_automatic_recovery(tmp_path):
    """中部损坏（committed history 内夹坏行）→ fail-closed：state_corrupt，recovery 不自动恢复（需运维）。

    spec「fail closed on malformed middle」——绝不基于残缺事件归约出错误状态后盲目重试。"""
    jf = _emit_seq(tmp_path / "j.jsonl", ["planned", "running"])
    lines = jf.read_text(encoding="utf-8").splitlines()
    lines.insert(1, "{这是坏行 NOT JSON}")            # 中部插入坏行（非末尾 → 中部损坏）
    jf.write_text("\n".join(lines) + "\n", encoding="utf-8")
    report = J.validate_journal(jf)
    assert report.is_fail_closed                      # 中部损坏 → fail-closed
    with pytest.raises(J.JournalCorruptionError):
        J.read_events(jf)                             # read_events 对中部损坏 raise（不静默跳过）


# ─── 防重放：idempotency_id 跨重放确定性 ─────────────────────────────────────
def test_idempotency_key_stable_across_replay():
    """**task 3.5 防重放契约**：崩溃重放产同 idempotency_id → reconcile 据此跳过已执行副作用。

    相同 (kind, iteration, target) → 同 key（确定性 sha256）；不同 target → 不同 key。
    恢复时 reconcile 见同 key 的副作用已执行（GitHub 有 PR）→ 跳过，实现 exactly-once effective。"""
    iter_id = ids.iteration_id("run_1", "prd_1", 0)
    k1 = ids.idempotency_id("pr", iter_id, "owner/repo:auto/branch-x")
    k2 = ids.idempotency_id("pr", iter_id, "owner/repo:auto/branch-x")   # 重放：同输入 → 同 key
    k3 = ids.idempotency_id("pr", iter_id, "owner/repo:auto/branch-y")   # 不同 target → 不同 key
    assert k1 == k2 and k1 != k3
    assert k1.startswith("idem_")


def test_idempotency_rejects_illegal_kind():
    """idempotency kind 必须在 commit/push/pr 允许列表（防构造非法幂等键混入 reconcile 逻辑）。"""
    iter_id = ids.iteration_id("run_1", "prd_1", 0)
    with pytest.raises(ValueError):
        ids.idempotency_id("merge", iter_id, "owner/repo:auto/b")   # merge 不在允许列表（永不 auto-merge 契约）


# ─── recovery 据归约态（非内存）决定 reconcile 路径 ──────────────────────────
def test_recovery_decides_reconcile_path_from_journal_state(tmp_path):
    """recovery 据 reduce 归约态决定路径（不依赖运行时内存）：
    PUBLISH_READY → 须 reconcile_pr（非终态）；PUBLISHED → 终态跳过；EXTERNAL_BLOCKED → 不消耗 retry。"""
    # A：PR 前 crash → PUBLISH_READY（非终态）→ recovery 须 reconcile_pr
    sa = _reduce(_emit_seq(tmp_path / "a.jsonl",
                           ["planned", "running", "agent_finished", "verifying", "publish_ready"]), "iter_a")
    assert sa.status is L.IterationStatus.PUBLISH_READY and not L.is_terminal(sa.status)
    # B：PR 后 crash → PUBLISHED（终态）→ exactly-once，recovery 无动作
    sb = _reduce(_emit_seq(tmp_path / "b.jsonl",
                           ["planned", "running", "agent_finished", "verifying", "publish_ready", "published"]),
                 "iter_b")
    assert sb.status is L.IterationStatus.PUBLISHED and L.is_terminal(sb.status)
    # C：远程态阻断 → EXTERNAL_BLOCKED（非终态但 retry policy 决策 block，不消耗 retry 预算，design 决策#3）
    sc = _reduce(_emit_seq(tmp_path / "c.jsonl",
                           ["planned", "running", "external_blocked"]), "iter_c")
    assert sc.status is L.IterationStatus.EXTERNAL_BLOCKED and not L.is_terminal(sc.status)


def test_full_green_chain_recovers_to_published_after_simulated_restart(tmp_path):
    """端到端：dispatch 主路径完整 emit → 模拟进程重启（重新 read+reduce，无内存）→ 归约 PUBLISHED。

    锁定「journal 单调记录 + reducer 确定性归约 = 重启后状态可重建」的真源契约（task 3.5 副作用边界
    recovery 的地基）。"""
    jf = _emit_seq(tmp_path / "green.jsonl",
                   ["planned", "running", "agent_finished", "verifying", "publish_ready", "published"])
    # 模拟重启：全新进程，只读 journal（无运行时内存继承）
    state = L.reduce(J.read_events(jf),
                     initial=L.initial_state("run_1", "prd_1", "iter_1", base="abc"))
    assert state.status is L.IterationStatus.PUBLISHED
    assert state.applied_event_ids                       # 6 条事件全部 dedup 累积
