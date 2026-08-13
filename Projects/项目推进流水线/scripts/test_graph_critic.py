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
from argparse import Namespace

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


# ── task 5.7：critic 切子图边界（revise 异常 / path 同步 / 聚合层三边界）──────────
STAMP_AGG = "20260815"


def test_subgraph_revise_exception_drops(monkeypatch):
    """revise 异常 → drop entry + _critic_verdict=drop + _revise_failed（route done，保 round1，task 5.7）。"""
    import run_daily
    def fake(agent, *a, **kw):
        if agent == "pa-prd-critic":
            return ({"verdict": "revise", "summary": "rev", "issues": [],
                     "revisions_needed": ["x"], "round": 1, "revised": False}, {"cost": 0, "turns": 1})
        raise RuntimeError("pa-prd revise 爆炸")           # pa-prd 异常
    monkeypatch.setattr(run_daily, "run_persona", fake)
    sg = GC.build_critic_subgraph()
    result = sg.invoke({"run_id": "r", "stamp": "s", "config": {},
                        "_prd_path": "p.md", "_project": "x", "_prof": {}, "_profiles": {}})
    assert result["_critic_verdict"] == "drop"
    assert result.get("_revise_failed") is True
    assert len(result["entries"]) == 2                    # round1 revise entry + revise 失败 drop entry
    assert result["entries"][-1]["verdict"] == "drop" and result["entries"][-1]["revised"] is True


def test_subgraph_revise_syncs_prd_path(monkeypatch):
    """revise 后 PRD 写新 path → _prd_path 更新（critic round2 读新 path，task 5.7 对齐 stage_critic L953-954）。"""
    import run_daily
    def fake(agent, prompt, stage, label, allowed_tools=None):
        if agent == "pa-prd-critic":
            return ({"verdict": "revise", "summary": "rev", "issues": [],
                     "revisions_needed": ["x"], "round": 1, "revised": False}, {"cost": 0, "turns": 1})
        return ({"prds": [{"path": "p-new.md", "project": "x"}], "skipped": []}, {"cost": 0, "turns": 1})
    monkeypatch.setattr(run_daily, "run_persona", fake)
    sg = GC.build_critic_subgraph()
    result = sg.invoke({"run_id": "r", "stamp": "s", "config": {},
                        "_prd_path": "p-old.md", "_project": "x", "_prof": {}, "_profiles": {}})
    assert result["_prd_path"] == "p-new.md"             # revise 同步新 path（round2 critic 读这个）


def _agg_state(monkeypatch, tmp_path, prds, *, skip_critic=False, force=False):
    """聚合层测试 state + 隔离 STATE_DIR（守 pa-test-no-dirty-data）。"""
    import run_daily
    sd = tmp_path / "state"; sd.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(run_daily, "STATE_DIR", sd)
    return {
        "run_id": "r1", "stamp": STAMP_AGG, "config": {},
        "prd_manifest": {"prds": prds},
        "_profiles": {"proj-x": {"name": "proj-x", "repo": "", "type": "code"}},
        "_args": Namespace(stamp=STAMP_AGG, force=force, skip_critic=skip_critic),
        "obs_log": [], "side_effect_log": [],
    }


def test_aggregate_missing_path_drops(monkeypatch, tmp_path):
    """边界①：prd 缺 path → drop entry（镜像 stage_critic L934-937），不调 persona。"""
    import run_daily
    import graph_pa_aggregate as AGG
    called = []
    monkeypatch.setattr(run_daily, "run_persona", lambda *a, **kw: called.append(a) or ({}, {}))
    prds = [{"project": "proj-x", "source_path": "s.md"}]   # 缺 path
    rec = AGG.node_critic_main(_agg_state(monkeypatch, tmp_path, prds))
    gate = rec["critic_results"]
    assert len(gate) == 1 and gate[0]["verdict"] == "drop"
    assert not called                                       # 缺 path 短路，不调 persona


def test_aggregate_missing_verdict_drops(monkeypatch, tmp_path):
    """边界②：critic 漏吐 verdict → drop 后处理（镜像 stage_critic L940-943）。"""
    import run_daily
    import graph_pa_aggregate as AGG
    monkeypatch.setattr(run_daily, "run_persona",
                        lambda *a, **kw: ({"summary": "无 verdict"}, {"cost": 0, "turns": 1}))   # 缺 verdict
    prds = [{"project": "proj-x", "path": "state/prd/proj-x/p.md", "source_path": "s.md"}]
    rec = AGG.node_critic_main(_agg_state(monkeypatch, tmp_path, prds))
    assert rec["critic_results"][-1]["verdict"] == "drop"   # 漏吐 → 降级 drop


def test_aggregate_revise_exception_drops(monkeypatch, tmp_path):
    """边界③：revise 异常 → drop（保 round1 entry，镜像 stage_critic L959-962）。"""
    import run_daily
    import graph_pa_aggregate as AGG
    def fake(agent, *a, **kw):
        if agent == "pa-prd-critic":
            return ({"verdict": "revise", "summary": "需修订", "issues": [],
                     "revisions_needed": ["x"], "round": 1, "revised": False}, {"cost": 0, "turns": 1})
        raise RuntimeError("pa-prd revise 爆炸")
    monkeypatch.setattr(run_daily, "run_persona", fake)
    prds = [{"project": "proj-x", "path": "state/prd/proj-x/p.md", "source_path": "s.md"}]
    rec = AGG.node_critic_main(_agg_state(monkeypatch, tmp_path, prds))
    gate = rec["critic_results"]
    assert len(gate) == 2                                   # round1 revise + revise 失败 drop
    assert gate[0]["verdict"] == "revise"                   # round1 entry 保
    assert gate[1]["verdict"] == "drop" and gate[1]["revised"] is True


def test_aggregate_skip_critic_all_pass(monkeypatch, tmp_path):
    """skip_critic → 全 pass（canary/演练），不调 persona。"""
    import run_daily
    import graph_pa_aggregate as AGG
    called = []
    monkeypatch.setattr(run_daily, "run_persona", lambda *a, **kw: called.append(a) or ({}, {}))
    prds = [{"project": "proj-x", "path": "p.md"}, {"project": "proj-y", "path": "q.md"}]
    rec = AGG.node_critic_main(_agg_state(monkeypatch, tmp_path, prds, skip_critic=True))
    gate = rec["critic_results"]
    assert len(gate) == 2 and all(e["verdict"] == "pass" for e in gate)
    assert not called


def test_aggregate_reuse_gate(monkeypatch, tmp_path):
    """复用门：prd_gate_{stamp}.json 已存在 + force=False → 直接复用（镜像 stage_critic L916-919）。"""
    import json
    import run_daily
    import graph_pa_aggregate as AGG
    sd = tmp_path / "state"; sd.mkdir(parents=True, exist_ok=True)
    cached = [{"prd_path": "old.md", "project": "proj-x", "verdict": "pass", "round": 1}]
    (sd / f"prd_gate_{STAMP_AGG}.json").write_text(json.dumps(cached), encoding="utf-8")
    monkeypatch.setattr(run_daily, "STATE_DIR", sd)
    called = []
    monkeypatch.setattr(run_daily, "run_persona", lambda *a, **kw: called.append(a) or ({}, {}))
    state = {
        "run_id": "r1", "stamp": STAMP_AGG, "config": {},
        "prd_manifest": {"prds": [{"project": "proj-x", "path": "new.md"}]},
        "_profiles": {}, "_args": Namespace(stamp=STAMP_AGG, force=False, skip_critic=False),
        "obs_log": [], "side_effect_log": [],
    }
    rec = AGG.node_critic_main(state)
    assert rec["critic_results"] == cached                  # 复用旧 gate，不重跑
    assert not called
