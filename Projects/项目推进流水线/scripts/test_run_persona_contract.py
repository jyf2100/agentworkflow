#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_run_persona_contract.py — run_persona 语义契约接线单测（change 2026-07-28 Phase 2）。

验证 run_persona 在语法 parse 成功后接入 stage_contracts：
    - 契约违反（critic 缺 verdict）+ 有预算 → 带诊断重试 → 第二次合规则返回合规 payload
    - 契约违反 + 预算用尽 → fail-open 降级返回现状 payload（不 raise，不改 stage 终态）

mock subprocess.run 返回构造的两层 JSON 信封（绕过真实 claude CLI）；AAA 结构。
跑：python3 -m pytest scripts/test_run_persona_contract.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent))
import run_daily  # noqa: E402


def _envelope(payload: dict) -> str:
    """构造 claude --output-format json 的两层信封：outer.result = json(inner payload)。"""
    return json.dumps({"result": json.dumps(payload, ensure_ascii=False), "total_cost_usd": 0,
                       "num_turns": 1, "is_error": False}, ensure_ascii=False)


def _mock_run(stdout_seq):
    """假 subprocess.run：按序吐 stdout（pop 模拟多次调用，耗尽则返空信封）。"""
    seq = list(stdout_seq)
    def _fake(*a, **k):
        return SimpleNamespace(returncode=0, stdout=seq.pop(0) if seq else _envelope({}), stderr="")
    return _fake


def test_run_persona_contract_violation_retries_then_succeeds(monkeypatch):
    """critic 第一次缺 verdict（契约违反）→ 带诊断重试；第二次合规 → 返回合规 payload。"""
    monkeypatch.setattr(run_daily.subprocess, "run",
                        _mock_run([_envelope({"prd_path": "a.md", "project": "p1"}),          # 缺 verdict
                                   _envelope({"verdict": "pass", "prd_path": "a.md"})]))        # 合规
    payload, _meta = run_daily.run_persona("pa-prd-critic", "prompt", "critic", "t1")
    assert payload["verdict"] == "pass"                       # 重试后拿到合规 payload
    assert payload["prd_path"] == "a.md"


def test_run_persona_contract_violation_budget_exhausted_failopen(monkeypatch):
    """critic 连续缺 verdict → 重试预算用尽 → fail-open 降级返回现状 payload，不 raise。"""
    bad = _envelope({"prd_path": "a.md", "project": "p1"})     # 持续缺 verdict
    monkeypatch.setattr(run_daily.subprocess, "run", _mock_run([bad, bad]))
    payload, _meta = run_daily.run_persona("pa-prd-critic", "prompt", "critic", "t2")
    assert payload["prd_path"] == "a.md"                      # 降级返回现状 payload，未 raise
