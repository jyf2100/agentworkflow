#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_graph_state_serialize.py — GraphState 序列化 + 轮次计数器测试（task 3.9, R8）。

state 可序列化可移植（R8：绝对路径不入 state，cross-machine bundle 友好）；轮次计数器（prd_round/
verify_round）跨节点回环判上限（D3）；_journal_path 提升到 GraphState 后仍可序列化（task 3.7 r2 P2④）。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import graph_pa_state as GS


def test_initial_state_json_serializable():
    """initial_state 可 JSON 序列化（R8：state 可序列化可移植）。"""
    s = GS.initial_state(run_id="r", thread_id="t", stamp="20260812")
    blob = json.dumps(s, ensure_ascii=False)          # 不抛 = 全字段可序列化
    restored = json.loads(blob)
    assert restored["run_id"] == "r"
    assert restored["thread_id"] == "t"
    assert restored["stamp"] == "20260812"
    assert restored["prd_round"] == 0
    assert restored["verify_round"] == 0
    assert restored["obs_log"] == []


def test_round_counters_bump():
    """prd_round/verify_round bump +1（D3 跨节点回环判上限）。"""
    s = GS.initial_state(run_id="r", thread_id="t", stamp="s")
    assert GS.bump_round(s, "prd_round") == 1
    assert GS.bump_round(s, "prd_round") == 2
    assert GS.bump_round(s, "verify_round") == 1
    assert s["prd_round"] == 2 and s["verify_round"] == 1


def test_round_constants_match_design():
    """轮次上限常量（D3 不变式 2：verify 2 轮 / prd critic 2 轮）。"""
    assert GS.VERIFY_MAX_ROUNDS == 2
    assert GS.PRD_MAX_ROUNDS == 2


def test_terminal_helpers():
    """is_terminal/mark_terminal（enum 终态机械硬门，D5/D6）。"""
    s = GS.initial_state(run_id="r", thread_id="t", stamp="s")
    assert not GS.is_terminal(s)
    GS.mark_terminal(s, "interrupted_pr")
    assert GS.is_terminal(s)
    assert s["terminal"] == "interrupted_pr"


def test_state_serializable_with_journal_path():
    """_journal_path 提升到 GraphState 后仍可序列化（task 3.7 r2 P2④）。"""
    s = GS.initial_state(run_id="r", thread_id="t", stamp="s")
    s["_journal_path"] = "/tmp/j.jsonl"
    blob = json.dumps(s, ensure_ascii=False)          # 不抛
    assert "/tmp/j.jsonl" in blob


def test_full_pipeline_state_serializable():
    """GraphState 产物字段（各 stage 写回的 payload）可序列化（R8）。

    r-review I2：生产 invoke 输入 dict（graph_pa.py _run_pipeline_graph）含运行期 _args/_sources/_profiles
    （Namespace，非 GraphState TypedDict 字段，违 R8 但只 node 内读不经 reducer + 崩溃恢复不重建——见
    graph_pa_aggregate 模块头）。本测试验证 GraphState 产物字段可序列化；_args 等运行期字段不进 GraphState，
    故不在此序列化范畴（崩溃恢复 task 3.8 不重建 _args，op 从 state 重读）。
    """
    s = GS.initial_state(run_id="r", thread_id="t", stamp="s")
    # 模拟各 stage 产物（ArtifactHandle as dict / payload，全可序列化）
    s["fetch_items"] = [{"rel_path": "a.md", "store": "vault", "digest": "sha256:x"}]
    s["candidates"] = {"candidates": [{"sig": "x"}], "stats": {"n": 1}}
    s["prd_manifest"] = {"prds": [{"path": "p.md", "project": "proj"}]}
    s["critic_results"] = [{"verdict": "pass"}]
    s["dispatch_results"] = [{"status": "pr_open"}]
    s["report"] = {"path": "report.md"}
    blob = json.dumps(s, ensure_ascii=False)          # 不抛 = GraphState 产物字段全可序列化
    assert json.loads(blob)["candidates"]["candidates"][0]["sig"] == "x"
