#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_graph_fetch.py — 3 个 fetch PersonaNode 配置实例测试（任务 3.1）。

stage_fetch（run_daily.py L765-766）调 run_persona 的形态：
    run_persona(cfg["agent"], cfg["prompt"](src), "fetch", f"fetch-{src['name']}", allowed_tools=cfg["tools"])
3 个 fetch node 必须对齐此形态（agent_name/stage/label per-source/allowed_tools/prompt 同源）。
落盘 + items 拆分留 MechanicalNode（Phase 2 后续）；此处只验调用形态 + payload 暂存。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import graph_pa_nodes as GN


# ── 配置实例类型 + 工具白名单一致性（防漂移）─────────────────────────
def test_fetch_nodes_are_persona_instances():
    for n in (GN.node_fetch_deepresearch, GN.node_fetch_wechat, GN.node_fetch_github):
        assert n._kind is GN.KIND_PERSONA
        assert n._cfg["stage"] == "fetch"
    assert GN.node_fetch_deepresearch._cfg["agent_name"] == "pa-fetch-deepresearch"
    assert GN.node_fetch_wechat._cfg["agent_name"] == "pa-fetch-wechat-url"
    assert GN.node_fetch_github._cfg["agent_name"] == "pa-fetch-github-repo"


def test_fetch_tools_mirror_fetch_config():
    """工具白名单与 run_daily.FETCH_CONFIG 一致（防 graph_pa_nodes 模块级常量漂移）。"""
    import run_daily
    for kind in ("agent-deepresearch", "wechat-url", "github-repo"):
        assert GN._FETCH_TOOLS[kind] == run_daily.FETCH_CONFIG[kind]["tools"], f"{kind} 工具白名单漂移"


# ── 调用形态 == stage_fetch L765-766 ─────────────────────────────────
def _capture_persona(monkeypatch, captured):
    import run_daily
    monkeypatch.setattr(run_daily, "run_persona",
                        lambda agent, prompt, stage, label, allowed_tools=None:
                        (captured.update(agent=agent, prompt=prompt, stage=stage,
                                         label=label, tools=allowed_tools) or
                         ({"title": "T", "markdown": "M", "sources_count": 3, "items": []},
                          {"cost": 0.1, "turns": 2})))


def test_fetch_deepresearch_call_shape(monkeypatch):
    captured = {}
    _capture_persona(monkeypatch, captured)
    import run_daily
    src = {"name": "ai-frontier", "kind": "agent-deepresearch", "params": {"prompts": ["LLM agents"]}}
    state = {"run_id": "r1", "thread_id": "t", "stamp": "s", "config": {},
             "_src": src, "_src_name": src["name"]}
    update = GN.node_fetch_deepresearch(state)
    assert captured["agent"] == "pa-fetch-deepresearch"
    assert captured["stage"] == "fetch"
    assert captured["label"] == "fetch-ai-frontier"           # per-source label（对齐 stage_fetch L766）
    assert captured["tools"] == run_daily.FETCH_ALLOWED_TOOLS
    assert captured["prompt"] == run_daily.fetch_prompt(src)  # prompt 同源（fetch_prompt）
    assert update["_fetch_payload"]["title"] == "T"


def test_fetch_wechat_call_shape(monkeypatch):
    captured = {}
    _capture_persona(monkeypatch, captured)
    import run_daily
    src = {"name": "wx-tech", "kind": "wechat-url", "params": {"urls": ["https://mp.weixin.qq.com/s/x"]}}
    GN.node_fetch_wechat({"run_id": "r", "config": {}, "_src": src, "_src_name": src["name"]})
    assert captured["agent"] == "pa-fetch-wechat-url"
    assert captured["label"] == "fetch-wx-tech"
    assert captured["tools"] == run_daily.FETCH_CONFIG["wechat-url"]["tools"]
    assert captured["prompt"] == run_daily.wechat_url_prompt(src)


def test_fetch_github_call_shape(monkeypatch):
    captured = {}
    _capture_persona(monkeypatch, captured)
    import run_daily
    src = {"name": "gh-ai", "kind": "github-repo", "params": {"repos": ["o/r"], "window": "7d"}}
    GN.node_fetch_github({"run_id": "r", "config": {}, "_src": src, "_src_name": src["name"]})
    assert captured["agent"] == "pa-fetch-github-repo"
    assert captured["label"] == "fetch-gh-ai"
    assert captured["tools"] == ["Bash"]
    assert captured["prompt"] == run_daily.github_repo_prompt(src)


# ── verdict 缺席（fetch 不产 verdict）+ idempotency_key ───────────────
def test_fetch_no_verdict_and_idempotency(monkeypatch):
    captured = {}
    _capture_persona(monkeypatch, captured)
    ni = {"run_id": "r9", "stage": "fetch", "config": {}, "_src_name": "src-x"}
    out, _ = GN.node_fetch_github.invoke(ni, "prompt")
    assert "verdict" not in out                            # fetch 不产 verdict（expose_verdict=False）
    assert out["status"] == "ok"
    assert out["idempotency_key"] == "r9:fetch:"           # _project 空（fetch 按 source，非 project）
