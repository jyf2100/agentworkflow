#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_graph_inject.py — node_inject MechanicalNode 配置实例测试（任务 3.2）。

stage_inject（run_daily.py L1102）是手动注入入口（--inject-prd md → manifest），替 radar→prd 自动路径。
纯机械活（零 LLM）→ MechanicalNode。op 复用 run_daily.stage_inject（零重写）。
mock stage_inject 验调用形态（args.inject_prd / profiles / stamp）+ manifest/actual_stamp 透传。
真实 stage_inject 落盘 + sys.exit 行为的 integration 测试留 Phase 2 拓扑接线（task 3.9）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import graph_pa_nodes as GN


def test_inject_node_config():
    assert GN.node_inject._kind is GN.KIND_MECHANICAL
    assert GN.node_inject._cfg == {"stage": "inject"}


def test_inject_call_shape(monkeypatch):
    captured = {}

    def fake_stage_inject(args, profiles, stamp):
        captured.update(inject_prd=args.inject_prd, profiles=profiles, stamp=stamp)
        return ({"prds": [{"project": "x", "path": "state/prd/x/20260811_foo.md"}], "skipped": []},
                stamp + "_m")                            # actual_stamp 自增（避碰，stage_inject L1136）

    import run_daily
    monkeypatch.setattr(run_daily, "stage_inject", fake_stage_inject)
    profiles = {"x": {"name": "x", "goal": "g"}}
    update = GN.node_inject({"run_id": "r1", "stamp": "20260811", "config": {},
                             "_inject_prd": "/tmp/hand.md", "_profiles": profiles})
    assert captured["inject_prd"] == "/tmp/hand.md"
    assert captured["profiles"] == profiles
    assert captured["stamp"] == "20260811"
    assert update["_inject_stamp"] == "20260811_m"       # actual_stamp 透传（下游对齐文件名）
    assert update["_prd_manifest"]["prds"][0]["project"] == "x"


def test_inject_no_verdict(monkeypatch):
    import run_daily
    monkeypatch.setattr(run_daily, "stage_inject",
                        lambda args, profiles, stamp: ({"prds": [], "skipped": []}, stamp))
    ni = {"run_id": "r9", "stamp": "s", "stage": "inject", "config": {}, "_inject_prd": "/tmp/x.md"}
    out, _ = GN.node_inject.invoke(ni, {"_profiles": {}})
    assert "verdict" not in out                          # MechanicalNode 不产 verdict（D3）
    assert out["status"] == "ok"
    assert out["idempotency_key"] == "r9:inject:"
