#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_graph_report.py — report node 测试（任务 3.6）。

report node（MechanicalNode）机械聚合 state["obs_log"]（每 node 追加的 Obs）→ 标准化可查询
metrics 文件（决策 M 路径 A / spec「report_node 机械聚合所有 node 的 obs」）。obs_log 经
Annotated[list, operator.add] reducer 自动累加所有 node 的 obs（4 类 node 工厂都 update={"obs_log":[...]}）。

覆盖：
① _aggregate_obs 纯函数：totals 求和 / by_model 分组 / 空 / drop-None 容错 / token 合计 / nodes 明细
② node_report：写 metrics_<stamp>.json + ArtifactHandle（store=vault digest 强制 OQ3）+ state 更新
③ rel_path 可移植（相对 vault_root，非绝对，R8）
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import graph_pa_nodes as GN
import graph_pa_contracts as C


# ── _aggregate_obs 纯函数 ─────────────────────────────────────────────
def test_aggregate_obs_totals():
    """3 node obs → sum cost/turns/duration_ms + token 合计。"""
    obs_log = [
        {"cost": 0.1, "turns": 3, "duration_ms": 1000, "model": "glm-5.2",
         "token_usage": {"input": 100, "output": 50}},
        {"cost": 0.2, "turns": 5, "duration_ms": 2000, "model": "glm-5.2",
         "token_usage": {"input": 200, "output": 80}},
        {"cost": 0.05, "turns": 1, "duration_ms": 500, "model": "glm-5.1",
         "token_usage": {"input": 50, "output": 20}},
    ]
    m = GN._aggregate_obs(obs_log, run_id="r", stamp="20260812")
    assert m["node_count"] == 3
    assert m["totals"]["cost"] == pytest.approx(0.35)   # 浮点累加精度（IEEE754）
    assert m["totals"]["turns"] == 9
    assert m["totals"]["duration_ms"] == 3500
    assert m["totals"]["input_tokens"] == 350
    assert m["totals"]["output_tokens"] == 150


def test_aggregate_obs_by_model():
    """多 model → group by model 合计 calls/cost/token。"""
    obs_log = [
        {"cost": 0.1, "model": "glm-5.2", "token_usage": {"input": 100, "output": 50}},
        {"cost": 0.2, "model": "glm-5.2", "token_usage": {"input": 200, "output": 80}},
        {"cost": 0.05, "model": "glm-5.1", "token_usage": {"input": 50, "output": 20}},
    ]
    m = GN._aggregate_obs(obs_log, run_id="r", stamp="s")
    assert m["by_model"]["glm-5.2"]["calls"] == 2
    assert m["by_model"]["glm-5.2"]["cost"] == pytest.approx(0.3)   # 浮点累加精度（IEEE754）
    assert m["by_model"]["glm-5.2"]["input"] == 300
    assert m["by_model"]["glm-5.1"]["calls"] == 1


def test_aggregate_obs_empty():
    """空 obs_log → node_count=0, totals 全 0, by_model 空。"""
    m = GN._aggregate_obs([], run_id="r", stamp="s")
    assert m["node_count"] == 0
    assert m["totals"]["cost"] == 0.0
    assert m["totals"]["turns"] == 0
    assert m["totals"]["duration_ms"] == 0
    assert m["by_model"] == {}
    assert m["nodes"] == []


def test_aggregate_obs_partial_drop_none():
    """drop-None obs（缺 cost/model/turns）容错：.get(,0) 不崩；model 缺 → 'unknown' 桶。"""
    obs_log = [
        {"turns": 2, "duration_ms": 800},    # 缺 cost/model/token_usage
        {"cost": 0.3, "model": "glm-5.2"},   # 缺 turns/duration/token
    ]
    m = GN._aggregate_obs(obs_log, run_id="r", stamp="s")
    assert m["node_count"] == 2
    assert m["totals"]["cost"] == 0.3
    assert m["totals"]["turns"] == 2
    assert m["by_model"]["unknown"]["calls"] == 1      # 缺 model → unknown 桶
    assert m["by_model"]["glm-5.2"]["calls"] == 1


def test_aggregate_obs_nodes_detail():
    """nodes 字段保留 obs_log 原样明细（逐 node，可 grep，决策 M「可查询」）。"""
    obs_log = [{"cost": 0.1, "model": "glm-5.2"}, {"cost": 0.2, "model": "glm-5.1"}]
    m = GN._aggregate_obs(obs_log, run_id="r", stamp="s")
    assert m["nodes"] == obs_log


# ── node_report（MechanicalNode）写 metrics 文件 + ArtifactHandle ────────
def _state(obs_log, tmp_path, monkeypatch):
    import run_daily
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(run_daily, "VAULT_ROOT", tmp_path)
    (tmp_path / "state").mkdir()
    return {"obs_log": obs_log, "run_id": "r1", "stamp": "20260812", "config": {}}


def test_node_report_writes_metrics_file(monkeypatch, tmp_path):
    """node_report 跑 → metrics_<stamp>.json 落盘 + state[report_metrics] 含聚合。"""
    s = _state([{"cost": 0.1, "turns": 3, "model": "glm-5.2",
                 "token_usage": {"input": 100, "output": 50}}], tmp_path, monkeypatch)
    upd = GN.node_report(s)
    metrics_path = tmp_path / "state" / "metrics_20260812.json"
    assert metrics_path.exists()                       # 固定名可查询（决策 M 核心）
    m = json.loads(metrics_path.read_text(encoding="utf-8"))
    assert m["node_count"] == 1
    assert m["totals"]["cost"] == 0.1
    assert upd["report_metrics"]["totals"]["cost"] == 0.1


def test_node_report_handle_store_vault_digest(monkeypatch, tmp_path):
    """store=vault → digest 强制 sha256:...（OQ3）；handle 过 validate_artifact_handle 严格校验。"""
    s = _state([{"cost": 0.1, "model": "glm-5.2"}], tmp_path, monkeypatch)
    upd = GN.node_report(s)
    h = upd["report"]
    C.validate_artifact_handle(h)                      # 严格校验通过（不 raise）
    assert h["store"] == C.STORE_VAULT
    assert h["digest"].startswith(C.DIGEST_PREFIX)
    assert h["must_exist"] is True
    assert h["kind"] == "metrics"


def test_node_report_rel_path_portable(monkeypatch, tmp_path):
    """rel_path 相对 vault_root（非绝对），可移植 R8。"""
    s = _state([{"cost": 0.1}], tmp_path, monkeypatch)
    upd = GN.node_report(s)
    rel = upd["report"]["rel_path"]
    assert not os.path.isabs(rel)                       # 相对路径（跨机 bundle 友好）
    assert "metrics_20260812.json" in rel


def test_node_report_obs_emitted(monkeypatch, tmp_path):
    """node_report 自身也吐 obs（mechanical 工厂 update={"obs_log":[out["obs"]]}）；含 node 标记。"""
    s = _state([{"cost": 0.1}], tmp_path, monkeypatch)
    upd = GN.node_report(s)
    assert "obs_log" in upd                             # mechanical node 工厂总会带 obs_log
    assert upd["obs_log"][0]["node"] == "report"
    assert upd["obs_log"][0]["node_count"] == 1
