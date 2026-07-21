#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_shadow_parity.py — shadow mode parity 验证（OpenSpec add-durable-loop-runtime task 3.4）。

**spec 硬要求**：「prove journal-reduced terminal states match existing dispatch records in shadow mode」。
shadow journaling 旁路写 journal、不改 dispatch 决策（design 决策#8）——其**唯一价值**是：给定同一 run，
journal reducer 归约出的终态分布 == 历史 dispatch records 的终态分布。两份 ``Counter`` 相等 = parity 成立
（迁移没改变行为，可放心切 journal_driven_dispatch）。

本测试构造混合终态的 dispatch records（published/failed/blocked_external/blocked_test_gate/revise/aborted），
为每个 record 生成等价的合法 journal 迁移链（用 ``ShadowJournal.emit`` 写），然后断言：
``summarize_journal(events) == summarize_terminal(records)``。

这锁死「journal 端 summarize」与「dispatch 端 summarize」的对齐——任一端改了终态分类逻辑（如 compat 映射、
状态机迁移表）而另一端没跟上，parity 断裂即 RED。模块零 SDK；AAA 结构。

跑：python3 -m pytest scripts/test_shadow_parity.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import compat_readers as C  # noqa: E402
import loop_runtime as RT  # noqa: E402
import loop_state as L  # noqa: E402


def _stamp() -> str:
    return "2026-07-20T00:00:00Z"


# dispatch record → 等价合法 journal 迁移链（到同一 IterationStatus 终态）。
# 这是「shadow 接入」所做之事的逆映射：dispatch 决定 status，shadow 沿合法路径 emit 到等价终态。
_CHAINS = {
    "pr_open+pass":              ["planned", "running", "agent_finished", "verifying", "publish_ready", "published"],
    "pr_open+revise":            ["planned", "running", "agent_finished", "verifying", "revise"],
    "fail":                      ["planned", "running", "failed"],
    "blocked_external_state":    ["planned", "running", "external_blocked"],
    "blocked_test_gate":         ["planned", "running", "agent_finished", "test_blocked"],
    "skip":                      ["planned", "aborted"],
}


def _chain_for(record: dict) -> list[str]:
    """dispatch record → 等价 journal 迁移链。"""
    s = record.get("status")
    if s == "pr_open":
        verify = record.get("verify") or {}
        return _CHAINS["pr_open+pass"] if verify.get("pass") else _CHAINS["pr_open+revise"]
    return _CHAINS[s]


def test_shadow_parity_mixed_run(tmp_path):
    """**task 3.4 核心**：混合终态 run 的 journal-reduced 终态分布 == dispatch records 终态分布。

    场景：一次 cron run 投了 6 份 PRD——2 份交付（pr_open+pass）、1 份失败、1 份远程阻断、1 份测试门阻断、
    1 份验证红（pr_open+!pass→revise）。journal 旁路记录了等价轨迹，reducer 归约出的终态 Counter 必须
    与 compat 读 dispatch records 的 Counter 逐桶相等。"""
    # Arrange：dispatch 真相（6 条 record）
    records = [
        {"project": "a", "slug": "s1", "status": "pr_open", "verify": {"pass": True}},
        {"project": "a", "slug": "s2", "status": "pr_open", "verify": {"pass": True}},
        {"project": "b", "slug": "s3", "status": "fail"},
        {"project": "c", "slug": "s4", "status": "blocked_external_state"},
        {"project": "d", "slug": "s5", "status": "blocked_test_gate"},
        {"project": "e", "slug": "s6", "status": "pr_open", "verify": {"pass": False}},
    ]
    # 用 ShadowJournal 把每个 record 的等价链写进 journal（模拟 shadow 接入旁路记录）
    sj = RT.ShadowJournal(tmp_path / "run.jsonl", "run_1", _stamp, enabled=True)
    for i, r in enumerate(records):
        for et in _chain_for(r):
            sj.emit(et, iteration_id=f"iter_{i}", prd_id="prd", payload={"base": "abc"})
    # Act：两端各自 summarize
    from journal import read_events
    journal_counts = C.summarize_journal(read_events(tmp_path / "run.jsonl"))
    dispatch_counts = C.summarize_terminal(records)
    # Assert：逐桶相等 = parity 成立
    assert journal_counts == dispatch_counts, (
        f"shadow parity 断裂：journal {dict(journal_counts)} != dispatch {dict(dispatch_counts)}")
    # 关键桶显式核对（防 Counter == 但桶错位的弱断言）
    assert journal_counts[L.IterationStatus.PUBLISHED] == 2
    assert journal_counts[L.IterationStatus.FAILED] == 1
    assert journal_counts[L.IterationStatus.EXTERNAL_BLOCKED] == 1
    assert journal_counts[L.IterationStatus.TEST_BLOCKED] == 1
    assert journal_counts[L.IterationStatus.REVISE] == 1


def test_shadow_parity_each_status_individually(tmp_path):
    """逐 status parity：每种 dispatch 终态，其等价 journal 链 reduce 必须落到同一 IterationStatus。

    细粒度锁定——任一 status 的链/reducer/compat 映射漂移即 RED（不靠混合 Counter 掩盖单点错）。"""
    cases = [
        ("pr_open", {"pass": True}, L.IterationStatus.PUBLISHED),
        ("pr_open", {"pass": False}, L.IterationStatus.REVISE),
        ("fail", None, L.IterationStatus.FAILED),
        ("blocked_external_state", None, L.IterationStatus.EXTERNAL_BLOCKED),
        ("blocked_test_gate", None, L.IterationStatus.TEST_BLOCKED),
        ("skip", None, L.IterationStatus.ABORTED),
    ]
    for idx, (status, verify, expected) in enumerate(cases):
        record = {"project": "x", "slug": "s", "status": status}
        if verify is not None:
            record["verify"] = verify
        # dispatch 端
        assert C.legacy_status(record) is expected, f"compat {status} 映射错"
        # journal 端：等价链 reduce 到同一终态（每个 case 唯一文件，防 pr_open 两次 case append 污染）
        jpath = tmp_path / f"{status}_{idx}.jsonl"
        sj = RT.ShadowJournal(jpath, "run_1", _stamp, enabled=True)
        for et in _chain_for(record):
            sj.emit(et, iteration_id="iter_0", prd_id="prd")
        from journal import read_events
        counts = C.summarize_journal(read_events(jpath))
        assert counts[expected] == 1, f"journal 链 for {status} 未归约到 {expected}: {dict(counts)}"


def test_shadow_parity_detects_drift(tmp_path):
    """parity 机制的负向断言：若 dispatch 端与 journal 端终态分类漂移，Counter 不等（机制能发现漂移）。

    构造一个「故意错配」的 record（compat 判 REVISE，但 journal 链指向 published）→ Counter 不等，
    证明 parity 检查不是恒真——它能真正捕获不一致。"""
    # dispatch record：pr_open + verify not pass → compat REVISE
    record = {"project": "x", "slug": "s", "status": "pr_open", "verify": {"pass": False}}
    # journal 端故意写「直跳 published」的错配链（违背状态机——running→published 应被 reducer 拒）
    sj = RT.ShadowJournal(tmp_path / "drift.jsonl", "run_1", _stamp, enabled=True)
    for et in ["planned", "running", "published"]:   # 非法链：running→published 状态机拒
        sj.emit(et, iteration_id="iter_0", prd_id="prd")
    from journal import read_events
    journal_counts = C.summarize_journal(read_events(tmp_path / "drift.jsonl"))
    dispatch_counts = C.summarize_terminal([record])
    # reducer 拒了非法迁移 → journal 停在 RUNNING（非 published），与 dispatch REVISE 不等 → parity 正确报断裂
    assert journal_counts != dispatch_counts
