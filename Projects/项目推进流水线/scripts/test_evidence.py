#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_evidence.py — evidence 发布门单测（OpenSpec verified-dev-execution / tasks 2.1）。

覆盖 spec 四场景 + 边界：未跑测试 / 测试失败 / 绿且新鲜 / 绿后又改（过期）/ 过期后再绿刷新 /
历史绿后最新红 / None 透传。纯逻辑零 SDK 导入（evidence.py 无依赖）。

跑：python3 -m pytest scripts/test_evidence.py -q
AAA 结构（Arrange / Act / Assert）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from evidence import (  # noqa: E402
    GATE_FAILED, GATE_NOT_RUN, GATE_PUBLISH, GATE_STALE,
    TestEvidence, evaluate_gate, mark_stale,
)


def test_no_test_run_blocks():
    # Arrange: dev loop 结束无任何证据
    # Act / Assert
    assert evaluate_gate(None)[0] == GATE_NOT_RUN


def test_failed_test_blocks():
    ev = TestEvidence(command="pytest", exit_code=1, completed_at="20260720-1200", fresh=True)
    assert evaluate_gate(ev)[0] == GATE_FAILED


def test_green_fresh_allows_publish():
    ev = TestEvidence(command="pytest", exit_code=0, completed_at="20260720-1200", fresh=True)
    assert evaluate_gate(ev)[0] == GATE_PUBLISH


def test_green_then_write_blocks():
    # 绿后发生候选写 → mark_stale → 过期不放行
    ev = TestEvidence(command="pytest", exit_code=0, completed_at="20260720-1200", fresh=True)
    stale = mark_stale(ev)
    assert stale.fresh is False
    assert evaluate_gate(stale)[0] == GATE_STALE


def test_new_green_after_stale_refreshes():
    # 绿→写(过期)→再绿：新一轮干净测试整体替换为 fresh=True，重新放行
    ev = TestEvidence(command="pytest", exit_code=0, completed_at="20260720-1200", fresh=True)
    assert evaluate_gate(mark_stale(ev))[0] == GATE_STALE
    refreshed = TestEvidence(command="pytest", exit_code=0, completed_at="20260720-1201", fresh=True)
    assert evaluate_gate(refreshed)[0] == GATE_PUBLISH


def test_latest_red_after_green_blocks():
    # 绿→再测红（无写）：最新证据是红 → test_failed（不因历史绿放行）
    red = TestEvidence(command="pytest", exit_code=1, completed_at="20260720-1201", fresh=True)
    assert evaluate_gate(red)[0] == GATE_FAILED


def test_mark_stale_none_passthrough():
    assert mark_stale(None) is None


def test_original_evidence_immutable():
    # 不可变 dataclass：mark_stale 返回新副本，原证据 fresh 不被改
    ev = TestEvidence(command="pytest", exit_code=0, completed_at="20260720-1200", fresh=True)
    mark_stale(ev)
    assert ev.fresh is True
