#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_loop_runtime.py — shadow journal 写入运行时单测（OpenSpec add-durable-loop-runtime task 3.2）。

把 journal 接入 dispatch 流程的「写入器」：每个决策点旁路写一条 ``JournalEvent``。**shadow mode**（design 决策#8）：

    * ``enabled=False``（``journal_shadow`` flag 默认关）→ ``emit`` 完全 no-op，dispatch 行为 **零变化**
      （baseline 不变——flag 关即回到第一阶段）；
    * ``enabled=True`` → 旁路写 journal，**不改 dispatch 决策**（emit 只观测、不返回影响流程的值）；
    * **emit 内部吞所有异常**——journal 是观测层，写失败（盘满/权限/损坏）绝不得让 dispatch 崩
      （spec：shadow 不改决策 = 不改控制流，含「不因自身故障改控制流」）。

本测试锁定这三条硬契约 + event_id 唯一性 + 全 event_type 覆盖 + 与 reducer 串通。
模块依赖 ``journal``/``loop_state``/标准库，不触 SDK——cron 隔离不变。

跑：python3 -m pytest scripts/test_loop_runtime.py -q。AAA 结构。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import loop_runtime as RT  # noqa: E402
import journal as J  # noqa: E402
import loop_state as L  # noqa: E402


def _fixed_stamp() -> str:
    """确定性时间戳（不触系统时间——可测、可重放）。"""
    return "2026-07-20T00:00:00Z"


# ─── 契约 1：flag 关 → emit 完全 no-op（baseline 不变）────────────────────
def test_emit_noop_when_disabled(tmp_path):
    """enabled=False → emit 不写文件、返回 None（flag 关即第一阶段行为，design 决策#8）。"""
    sj = RT.ShadowJournal(path=tmp_path / "j.jsonl", run_id="run_1",
                          stamp=_fixed_stamp, enabled=False)
    # Act
    ret = sj.emit("running", iteration_id="iter_1", prd_id="prd_1", payload={"base": "abc"})
    # Assert：文件未创建，返回 None
    assert ret is None
    assert not (tmp_path / "j.jsonl").exists()


def test_emit_writes_event_when_enabled(tmp_path):
    """enabled=True → emit 写一条 JournalEvent 到 journal，返回 event_id。"""
    sj = RT.ShadowJournal(path=tmp_path / "j.jsonl", run_id="run_1",
                          stamp=_fixed_stamp, enabled=True)
    eid = sj.emit("running", iteration_id="iter_1", prd_id="prd_1", payload={"base": "abc"})
    # Assert
    assert eid is not None
    events = J.read_events(tmp_path / "j.jsonl")
    assert len(events) == 1
    assert events[0].event_type == "running"
    assert events[0].run_id == "run_1"
    assert events[0].payload == {"base": "abc"}


# ─── 契约 2：event_id 唯一（reducer dedup 依据，同 journal 内不得重号）─────
def test_emit_produces_unique_event_ids(tmp_path):
    """多次 emit → event_id 各异（reducer dedup 按 event_id，重号会被误判重复跳过）。"""
    sj = RT.ShadowJournal(path=tmp_path / "j.jsonl", run_id="run_1",
                          stamp=_fixed_stamp, enabled=True)
    ids = [sj.emit("running", "iter_1", "prd_1") for _ in range(5)]
    assert len(set(ids)) == 5    # 全唯一


# ─── 契约 3：emit 异常吞掉（shadow 绝不影响 dispatch 控制流）──────────────
def test_emit_swallows_write_failure(tmp_path):
    """**spec 硬契约**：journal 写失败（盘满/权限/path 非法）→ emit 不 raise，dispatch 照常继续。

    shadow 是观测层——它自己坏掉绝不能拖垮被观测的 dispatch（否则观测反成单点故障源）。"""
    # Arrange：path 指向不存在且无法创建的父目录（/proc 之类只读路径模拟写失败）
    sj = RT.ShadowJournal(path="/proc/cannot/write/here.jsonl", run_id="run_1",
                          stamp=_fixed_stamp, enabled=True)
    # Act / Assert：不 raise 即过
    sj.emit("running", "iter_1", "prd_1")


def test_emit_with_none_path_is_noop(tmp_path):
    """path=None（历史 run 无 journal 目录兼容）→ emit no-op，不崩。"""
    sj = RT.ShadowJournal(path=None, run_id="run_1", stamp=_fixed_stamp, enabled=True)
    assert sj.emit("running", "iter_1", "prd_1") is None


# ─── 全 event_type 覆盖（dispatch 每个决策点都能 emit）──────────────────
@pytest.mark.parametrize("event_type", [
    "planned", "running", "agent_finished", "test_blocked",
    "verifying", "revise", "external_blocked",
    "publish_ready", "published", "aborted", "failed",
])
def test_emit_covers_all_dispatch_decision_events(tmp_path, event_type):
    """dispatch 的每个状态机决策点都能被 emit 记录——shadow 必须**完整**覆盖迁移路径。

    缺任一 event_type → reducer 归约出的终态会与真实 dispatch 不符（shadow parity 假象）。"""
    sj = RT.ShadowJournal(path=tmp_path / "j.jsonl", run_id="run_1",
                          stamp=_fixed_stamp, enabled=True)
    sj.emit(event_type, iteration_id="iter_1", prd_id="prd_1")
    events = J.read_events(tmp_path / "j.jsonl")
    assert events[-1].event_type == event_type


# ─── shadow 写入的 journal 可被 reducer 归约（端到端真源链路）─────────────
def test_shadow_journal_feeds_reducer_to_published(tmp_path):
    """完整绿路径 emit → read → reduce 归约到 PUBLISHED（shadow 写的 journal 是 reducer 合法输入）。"""
    sj = RT.ShadowJournal(path=tmp_path / "j.jsonl", run_id="run_1",
                          stamp=_fixed_stamp, enabled=True)
    for et in ["planned", "running", "agent_finished", "verifying", "publish_ready", "published"]:
        sj.emit(et, iteration_id="iter_1", prd_id="prd_1")
    # Act
    state = L.reduce(J.read_events(tmp_path / "j.jsonl"),
                     initial=L.initial_state("run_1", "prd_1", "iter_1", base="abc"))
    # Assert
    assert state.status is L.IterationStatus.PUBLISHED
