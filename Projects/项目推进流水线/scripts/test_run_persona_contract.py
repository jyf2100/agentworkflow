#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_run_persona_contract.py — run_persona 语义契约接线单测（change 2026-07-28 Phase 2）。

验证 run_persona 在语法 parse 成功后接入 stage_contracts：
    - 契约违反（critic 缺 verdict）+ 有预算 → 带诊断重试 → 第二次合规则返回合规 payload
    - 契约违反 + 预算用尽 → fail-open 降级返回现状 payload（不 raise，不改 stage 终态）

mock subprocess.run 返回构造的两层 JSON 信封（绕过真实 claude CLI）；AAA 结构。
add-per-agent-model-routing：twin 镜像守（run_daily 端 env→--model，与 persona_call 行为对齐）。
跑：python3 -m pytest scripts/test_run_persona_contract.py -q
"""
from __future__ import annotations

import json
import os
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


# ─── per-agent model routing：twin 镜像守（add-per-agent-model-routing）─────────
# review HIGH（3 人共识）：persona_call 端有 env 注入测试，run_daily 端（7 persona）零守护。
# 此处补 twin 守护——捕获 run_persona 拼的 cmd，断言 equals 形式注入 / 不设则无。
def _cmd_capturing_run(captured: dict):
    """记下每次 subprocess.run 收到的 cmd（位置参 args[0]），再返空信封。"""
    def _fake(*a, **k):
        captured["cmds"].append(list(a[0]))
        return SimpleNamespace(returncode=0, stdout=_envelope({"ok": True}), stderr="")
    return _fake


def test_run_persona_model_env_injects_equals_form(monkeypatch):
    """设 PA_PERSONA_MODEL_PA_RADAR=sonnet → run_daily 拼的 cmd 含 '--model=sonnet'（twin 镜像守）。"""
    monkeypatch.setenv("PA_PERSONA_MODEL_PA_RADAR", "sonnet")
    captured: dict = {"cmds": []}
    monkeypatch.setattr(run_daily.subprocess, "run", _cmd_capturing_run(captured))
    run_daily.run_persona("pa-radar", "P", "radar", "t-radar")
    assert any(x == "--model=sonnet" for x in captured["cmds"][0])


def test_run_persona_no_model_env_omits_flag(monkeypatch):
    """不设任何 PA_PERSONA_MODEL_* → run_daily 拼的 cmd 无任何 --model*（baseline byte-identical）。"""
    for k in [k for k in os.environ if k.startswith("PA_PERSONA_MODEL_")]:
        monkeypatch.delenv(k, raising=False)
    captured: dict = {"cmds": []}
    monkeypatch.setattr(run_daily.subprocess, "run", _cmd_capturing_run(captured))
    run_daily.run_persona("pa-radar", "P", "radar", "t-radar2")
    assert not any(x.startswith("--model") for x in captured["cmds"][0])
