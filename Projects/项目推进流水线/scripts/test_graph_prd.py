#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_graph_prd.py — node_prd PersonaNode 配置实例测试（任务 3.2）。

stage_prd（run_daily.py L907）调 run_persona 形态：
    run_persona("pa-prd", prd_prompt(candidates, profiles, stamp), "prd", "prd")
node_prd 对齐此形态（agent=pa-prd / stage=prd / label 固定"prd" / allowed_tools=None / prompt 同源）。
manifest 落盘留 MechanicalNode（Phase 2 后续）；critic revise 回环留 critic 子图（task 3.3）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import graph_pa_nodes as GN


def test_prd_node_config():
    assert GN.node_prd._kind is GN.KIND_PERSONA
    assert GN.node_prd._cfg == {"agent_name": "pa-prd", "stage": "prd", "label": "prd"}


def _capture_persona(monkeypatch, captured):
    import run_daily
    monkeypatch.setattr(run_daily, "run_persona",
                        lambda agent, prompt, stage, label, allowed_tools=None:
                        (captured.update(agent=agent, prompt=prompt, stage=stage,
                                         label=label, tools=allowed_tools) or
                         ({"prds": [{"project": "x", "path": "p.md"}], "skipped": []},
                          {"cost": 0.2, "turns": 5})))


def test_prd_call_shape(monkeypatch):
    captured = {}
    _capture_persona(monkeypatch, captured)
    import run_daily
    cands = [{"project": "x", "signal": "s", "score": 0.9}]
    profiles = {"x": {"name": "x", "goal": "g", "tech_stack": ["py"]}}
    update = GN.node_prd({"run_id": "r1", "stamp": "20260811", "config": {},
                          "_candidates": cands, "_profiles": profiles})
    assert captured["agent"] == "pa-prd"
    assert captured["stage"] == "prd"
    assert captured["label"] == "prd"                    # 固定 label（batch，对齐 stage_prd L907 第 4 参）
    assert captured["tools"] is None                     # pa-prd 不限工具（stage_prd 没传 allowed_tools）
    assert captured["prompt"] == run_daily.prd_prompt(cands, profiles, "20260811")
    assert update["_prd_manifest"]["prds"][0]["project"] == "x"


def test_prd_no_verdict(monkeypatch):
    captured = {}
    _capture_persona(monkeypatch, captured)
    ni = {"run_id": "r9", "stage": "prd", "config": {}, "stamp": "s",
          "_candidates": [], "_profiles": {}}
    out, _ = GN.node_prd.invoke(ni, "prompt")
    assert "verdict" not in out                          # prd 不产 verdict（critic 才产）
    assert out["status"] == "ok"
    assert out["idempotency_key"] == "r9:prd:"           # _project 空（prd batch，非 per-project）
