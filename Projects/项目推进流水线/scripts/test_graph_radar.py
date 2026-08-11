#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_graph_radar.py — node_radar 调用形态 == stage_radar 断言（任务 2.6）。

stage_radar（run_daily.py L848-850）调 run_persona 的形态：
    run_persona("pa-radar", radar_prompt(proj, flat, profiles[proj], dedup.get(proj, [])),
                "radar", f"radar-{proj}")
node_radar 必须对齐此形态（agent_name/stage/label/radar_prompt 入参顺序/allowed_tools）。
byte-identical 真实 payload 对比留给 spike 1.4（需真实 run）；此处用 monkeypatch 断言调用形态。
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import graph_pa_nodes as GN


def test_node_radar_call_shape_matches_stage_radar(monkeypatch):
    """node_radar 调 run_persona + radar_prompt 的形态 == stage_radar L848-850。"""
    import run_daily

    persona_calls = []
    monkeypatch.setattr(run_daily, "run_persona",
                        lambda name, prompt, stage, label, allowed_tools=None:
                        (persona_calls.append(dict(name=name, prompt=prompt, stage=stage,
                                                   label=label, allowed_tools=allowed_tools)) or
                         ({"candidates": []}, {"cost": 0, "turns": 0})))
    radar_calls = []
    monkeypatch.setattr(run_daily, "radar_prompt",
                        lambda proj, files, profile, dedup:
                        radar_calls.append(dict(proj=proj, files=files, profile=profile, dedup=dedup)) or "PROMPT")

    proj = "aichat"
    flat = [Path("/v/Knowledge/x.md")]
    profile = {"name": "aichat"}
    state = {"run_id": "r1", "thread_id": "t", "stamp": "s", "config": {},
             "_project": proj, "_today_new": flat, "_profiles": {proj: profile}, "_dedup": []}
    GN.node_radar(state)

    # radar_prompt 入参顺序 == stage_radar L849: radar_prompt(proj, flat, profiles[proj], dedup.get(proj, []))
    assert radar_calls == [{"proj": proj, "files": flat, "profile": profile, "dedup": []}]
    # run_persona 调用形态 == stage_radar L848-850（label=f"radar-{proj}" 对齐）
    assert persona_calls == [{"name": "pa-radar", "prompt": "PROMPT", "stage": "radar",
                              "label": f"radar-{proj}", "allowed_tools": None}]


def test_node_radar_label_per_project(monkeypatch):
    """label 运行期带 _project（对齐 stage_radar f"radar-{proj}"）——不同项目不同 label。"""
    import run_daily
    labels = []
    monkeypatch.setattr(run_daily, "run_persona",
                        lambda name, prompt, stage, label, allowed_tools=None:
                        (labels.append(label), ({"candidates": []}, {"cost": 0, "turns": 0}))[1])
    monkeypatch.setattr(run_daily, "radar_prompt", lambda *a, **k: "p")
    base = {"run_id": "r", "config": {}, "_today_new": [], "_profiles": {"x": {}, "y": {}}, "_dedup": []}
    GN.node_radar({**base, "_project": "x"})
    GN.node_radar({**base, "_project": "y"})
    assert labels == ["radar-x", "radar-y"]
