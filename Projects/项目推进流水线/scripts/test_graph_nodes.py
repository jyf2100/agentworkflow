#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_graph_nodes.py — 4 类 node 工厂 + verdict 边界守 + node_radar 测试（任务 2.3/2.5）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import graph_pa_contracts as C
import graph_pa_nodes as GN


# ── verdict 边界运行时守（D3/R6）─────────────────────────────────────
def test_commit_node_rejects_verdict_from_non_persona():
    ni = {"run_id": "r", "stage": "s", "config": {}}
    out = {"status": "ok", "obs": {}, "idempotency_key": "k",
           "verdict": {"value": "pass", "reason": "r"}}
    for kind in (GN.KIND_MECHANICAL, GN.KIND_GATEWAY, GN.KIND_DEVLOOP):
        try:
            GN.commit_node(kind, ni, out); assert False, f"{kind.name} 不应写 verdict"
        except C.ContractError:
            pass
    # PersonaNode 允许写 verdict
    GN.commit_node(GN.KIND_PERSONA, ni, out)


def test_commit_node_validates_input_output():
    ni_bad = {"stage": "s", "config": {}}   # 缺 run_id
    try:
        GN.commit_node(GN.KIND_PERSONA, ni_bad, {"status": "ok", "obs": {}, "idempotency_key": "k"})
        assert False
    except C.ContractError:
        pass
    try:
        GN.commit_node(GN.KIND_PERSONA, {"run_id": "r", "stage": "s", "config": {}},
                       {"status": "bogus", "obs": {}, "idempotency_key": "k"})
        assert False
    except C.ContractError:
        pass


# ── node_radar 配置实例（任务 2.5）───────────────────────────────────
def test_node_radar_is_persona_instance():
    assert GN.node_radar._kind is GN.KIND_PERSONA
    assert GN.node_radar._cfg["agent_name"] == "pa-radar"
    assert GN.node_radar._cfg["stage"] == "radar"


def test_node_radar_invoke_calls_run_persona_and_emits_obs(monkeypatch):
    import run_daily
    captured = {}

    def fake(name, prompt, stage, label, allowed_tools=None):
        captured.update(name=name, stage=stage, label=label, prompt=prompt)
        return ({"candidates": [{"project": "p", "relevance": 0.9}], "stats": {"signals_extracted": 1}},
                {"cost": 0.12, "turns": 3, "duration_ms": 500, "model": {"glm-5.2": {"input": 100, "output": 50}}})
    monkeypatch.setattr(run_daily, "run_persona", fake)

    ni = {"run_id": "r1", "stage": "radar", "config": {}, "_project": "p", "stamp": "20260811"}
    out, payload = GN.node_radar.invoke(ni, "prompt-x")

    assert captured["name"] == "pa-radar" and captured["stage"] == "radar"
    assert out["status"] == "ok"
    assert out["idempotency_key"] == "r1:radar:p"
    assert out["obs"]["cost"] == 0.12 and out["obs"]["model"] == "glm-5.2"
    assert out["artifacts"] == [{"kind": "candidates", "store": "tmp",
                                 "rel_path": "candidates_20260811.json"}]
    assert "verdict" not in out                       # radar 不产 verdict（expose_verdict=False）
    assert payload["candidates"][0]["project"] == "p"


def test_node_radar_to_state_stores_payload(monkeypatch):
    import run_daily
    monkeypatch.setattr(run_daily, "run_persona",
                        lambda *a, **k: ({"candidates": []}, {"cost": 0, "turns": 0}))
    state = {"run_id": "r", "stage": "radar", "config": {}, "_project": "p",
             "_today_new": [], "_profiles": {"p": {}}}
    update = GN.node_radar(state)
    assert update["obs_log"][0]["cost"] == 0
    assert update["_radar_payload"] == {"candidates": []}


# ── PersonaNode expose_verdict（critic/progress 用）──────────────────
def test_persona_node_expose_verdict(monkeypatch):
    import run_daily
    monkeypatch.setattr(run_daily, "run_persona",
                        lambda *a, **k: ({"verdict": "revise", "reason": "缺验收标准",
                                          "feedback": "补 AC"}, {"cost": 0, "turns": 1}))
    critic = GN.make_persona_node(agent_name="pa-prd-critic", stage="critic", label="critic",
                                  build_prompt=lambda s: "p", expose_verdict=True)
    out, _ = critic.invoke({"run_id": "r", "stage": "critic", "config": {}}, "p")
    assert out["verdict"] == {"value": "revise", "reason": "缺验收标准", "feedback": "补 AC"}


# ── MechanicalNode（零 LLM，不产 verdict）────────────────────────────
def test_mechanical_node():
    m = GN.make_mechanical_node(
        stage="report",
        op=lambda ni, state: ([{"kind": "metrics", "store": "vault", "rel_path": "m.json",
                                "digest": "sha256:x"}], {"report": {"done": True}}, {"turns": 0}))
    out, extra = m.invoke({"run_id": "r", "stage": "report", "config": {}}, {})
    assert out["status"] == "ok" and extra == {"report": {"done": True}}
    assert out["artifacts"][0]["kind"] == "metrics"


def test_mechanical_node_cannot_produce_verdict():
    # op 不构造 verdict（工厂不暴露）；直接验证 commit_node 对 mechanical+verdict raise
    try:
        GN.commit_node(GN.KIND_MECHANICAL, {"run_id": "r", "stage": "s", "config": {}},
                       {"status": "ok", "obs": {}, "idempotency_key": "k",
                        "verdict": {"value": "pass", "reason": "r"}})
        assert False
    except C.ContractError:
        pass


# ── GatewayNode（fail-safe 门，UNKNOWN→blocked）──────────────────────
def test_gateway_node_pass():
    gw = GN.make_gateway_node(stage="pre_dispatch", check=lambda ni: (True, None))
    out = gw.invoke({"run_id": "r", "stage": "pre_dispatch", "config": {}})
    assert out["status"] == "ok"


def test_gateway_node_blocked_yields_terminal():
    gw = GN.make_gateway_node(stage="pre_dispatch", check=lambda ni: (False, None))
    update = gw({"run_id": "r", "stage": "pre_dispatch", "config": {}})
    assert update["terminal"] == "blocked"


# ── parse_dev_exit（exit code→terminal 机械映射；DevLoopNode 完整实装见 test_graph_devloop.py）──
def test_parse_dev_exit_mapping():
    assert GN.parse_dev_exit(0) == (None, None)
    assert GN.parse_dev_exit(14) == (C.STATUS_BLOCKED, C.ERR_TEST_GATE)
    assert GN.parse_dev_exit(15) == (C.STATUS_TRIAGED, C.ERR_CONTRACT_VIOLATION)
    assert GN.parse_dev_exit(12) == (C.STATUS_TRIAGED, C.ERR_CONTRACT_VIOLATION)
    term, code = GN.parse_dev_exit(99)          # 未知非 0 → triaged（升人工，不替判死）
    assert term == C.STATUS_TRIAGED and code == C.ERR_PERSONA_CRASH
