#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_graph_critic.py — critic PersonaNode + revise 子图测试（任务 3.3）。

验证：
① node_critic 配置（KIND_PERSONA, expose_verdict=True 的 verdict 提取）
② node_critic 调用形态 == _critic_one L970（agent/stage/label per-prd/allowed_tools/prompt 同源）
③ node_prd_revise 调用形态 == stage_critic L951-952（revise 参数 + label per-project）
④ critic 子图拓扑：pass→END / revise→revise→critic round2 / revise 用尽（round2 仍 revise）→END
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import graph_pa_nodes as GN
import graph_pa_critic as GC


# ── node_critic 配置 + verdict 提取 ──────────────────────────────────
def test_critic_node_config():
    assert GN.node_critic._kind is GN.KIND_PERSONA
    assert GN.node_critic._cfg["agent_name"] == "pa-prd-critic"
    assert GN.node_critic._cfg["stage"] == "critic"


def _capture_persona(monkeypatch, captured, payload_override=None):
    import run_daily
    default_payload = {"verdict": "pass", "issues": [], "summary": "ok",
                       "revisions_needed": [], "round": 1, "revised": False}
    payload = payload_override or default_payload
    monkeypatch.setattr(run_daily, "run_persona",
                        lambda agent, prompt, stage, label, allowed_tools=None:
                        (captured.update(agent=agent, prompt=prompt, stage=stage,
                                         label=label, tools=allowed_tools) or
                         (dict(payload), {"cost": 0.1, "turns": 3})))


def test_critic_call_shape(monkeypatch):
    captured = {}
    _capture_persona(monkeypatch, captured)
    import run_daily
    prof = {"name": "x", "goal": "g", "match_surface": {"one_liner": "ol"}}
    GN.node_critic({"run_id": "r1", "config": {}, "_prd_path": "state/prd/x/20260811_foo.md",
                    "_source_path": "src.md", "_project": "x", "_prof": prof})
    assert captured["agent"] == "pa-prd-critic"
    assert captured["stage"] == "critic"
    assert captured["label"] == "critic:20260811_foo"  # per-prd label（Path.stem 含日期前缀，对齐 _critic_one L969）
    assert captured["tools"] is None                   # critic 不限工具（_critic_one 没传 allowed_tools）
    assert captured["prompt"] == run_daily.critic_prompt(
        "state/prd/x/20260811_foo.md", "src.md", prof)


def test_critic_exposes_verdict(monkeypatch):
    """critic 是首个 expose_verdict=True 的 PersonaNode：payload.verdict 提取到 out['verdict']。"""
    captured = {}
    _capture_persona(monkeypatch, captured,
                     {"verdict": "revise", "issues": ["i"], "summary": "需修订",
                      "revisions_needed": ["补验收标准"], "round": 1, "revised": False})
    ni = {"run_id": "r9", "stage": "critic", "config": {}, "_prd_path": "p.md", "_prof": {}}
    out, _ = GN.node_critic.invoke(ni, "prompt")
    assert out["verdict"]["value"] == "revise"          # expose_verdict=True 提取（条件边 route_critic 用）


# ── node_prd_revise 调用形态 ─────────────────────────────────────────
def test_prd_revise_call_shape(monkeypatch):
    captured = {}
    _capture_persona(monkeypatch, captured)
    import run_daily
    profiles = {"x": {"name": "x", "goal": "g"}}
    GN.node_prd_revise({"run_id": "r", "stamp": "20260811", "config": {},
                        "_prd_path": "state/prd/x/p.md", "_project": "x", "_profiles": profiles,
                        "_critic_payload": {"revisions_needed": ["补验收标准"]}})
    assert captured["agent"] == "pa-prd"
    assert captured["stage"] == "prd"
    assert captured["label"] == "prd-revise:x"          # 对齐 stage_critic L952 f"prd-revise:{proj}"
    rev = {"prd_path": "state/prd/x/p.md", "revisions_needed": ["补验收标准"]}
    assert captured["prompt"] == run_daily.prd_prompt([], profiles, "20260811", revise=rev)


# ── critic 子图拓扑（revise 回环 + round 上限）────────────────────────
def _persona_seq(monkeypatch, verdicts):
    """critic 调用按序消费 verdicts；revise（pa-prd）调用返固定 prds。"""
    import run_daily
    it = iter(verdicts)

    def fake(agent, prompt, stage, label, allowed_tools=None):
        if agent == "pa-prd-critic":
            v = next(it)
            return ({"verdict": v, "issues": [], "summary": v,
                     "revisions_needed": ["fix"], "round": 1, "revised": False},
                    {"cost": 0.1, "turns": 3})
        return ({"prds": [{"path": "p.md", "project": "x"}], "skipped": []},   # pa-prd revise
                {"cost": 0.2, "turns": 5})
    monkeypatch.setattr(run_daily, "run_persona", fake)


def test_subgraph_pass_terminates(monkeypatch):
    _persona_seq(monkeypatch, ["pass"])
    sg = GC.build_critic_subgraph()
    result = sg.invoke({"run_id": "r", "stamp": "s", "config": {},
                        "_prd_path": "p.md", "_prof": {}, "_profiles": {}})
    assert result["_critic_verdict"] == "pass"
    assert len(result["entries"]) == 1                  # 单轮 critic，无 revise


def test_subgraph_revise_loop_round2(monkeypatch):
    """critic revise → revise → critic round2 pass（1 次 revise 回环）。"""
    _persona_seq(monkeypatch, ["revise", "pass"])
    sg = GC.build_critic_subgraph()
    result = sg.invoke({"run_id": "r", "stamp": "s", "config": {},
                        "_prd_path": "p.md", "_project": "x", "_prof": {}, "_profiles": {}})
    assert result["prd_round"] == 2                     # revise bump 了 round
    assert result["_critic_verdict"] == "pass"
    assert len(result["entries"]) == 2                  # round1 revise entry + round2 pass entry


def test_subgraph_revise_exhausted_terminates(monkeypatch):
    """round1 revise → revise → round2 仍 revise，但 prd_round=2=MAX → done（不再 revise）。"""
    _persona_seq(monkeypatch, ["revise", "revise"])
    sg = GC.build_critic_subgraph()
    result = sg.invoke({"run_id": "r", "stamp": "s", "config": {},
                        "_prd_path": "p.md", "_project": "x", "_prof": {}, "_profiles": {}})
    assert result["prd_round"] == 2
    assert result["_critic_verdict"] == "revise"        # round2 仍 revise
    assert len(result["entries"]) == 2                  # 但不再 revise（用尽，CRITIC_MAX_ROUNDS=2）


def test_subgraph_drop_terminates(monkeypatch):
    _persona_seq(monkeypatch, ["drop"])
    sg = GC.build_critic_subgraph()
    result = sg.invoke({"run_id": "r", "stamp": "s", "config": {},
                        "_prd_path": "p.md", "_prof": {}, "_profiles": {}})
    assert result["_critic_verdict"] == "drop"
    assert result.get("prd_round", 1) == 1              # 无 revise，prd_round 未 bump
