#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_subgraph_to_record_mapping.py — _subgraph_result_to_record 字段映射测试（task 3.9）。

验证 dispatch 子图 state → dispatch_one rec schema 1:1（shadow parity 核心，R7）。dispatch_one rec 字段
= 初始 21 key（run_daily L2046-2056）+ 运行中动态 3 key（blocked_check/gate_status/gate_reason，stage_report
L3164-3167 消费）== _subgraph_result_to_record key 集合。任一字段漂移会让 shadow parity 报告（批 3
run_graph_shadow_parity_drill）失配，此测试作前置闸。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import graph_pa_aggregate as AGG


# dispatch_one rec 字段集（run_daily 真源，shadow parity 对照基准）：初始 21（L2046-2056）+ 运行中动态 3
# （blocked_check/gate_status/gate_reason，L2109/2219-2220 等 rec.update，stage_report L3164-3167 消费）。
_DISPATCH_ONE_REC_KEYS = {
    "project", "prd_path", "slug", "base", "status", "pr_url", "branch",
    "dev_killed", "stalled", "run_log", "dev_cost", "dev_turns", "verify",
    "skip_reason", "dev_test_cmd", "verify_verdict", "verify_round",
    "merge_commit", "reverted", "triage_reason", "post_merge_verdict",
    "blocked_check", "gate_status", "gate_reason",
}


def test_record_schema_matches_dispatch_one():
    """_subgraph_result_to_record key 集合 == dispatch_one rec 完整字段集（初始 21 + 动态 3，shadow parity 1:1）。"""
    result = {"_exit_status": "pr_open", "_pr_url": "https://x", "_branch": "pa-dev-x",
              "_dev_cost": 0.5, "_dev_turns": 12, "_verify_verdict": "pass",
              "_verify_payload": {"x": 1}, "verify_round": 1, "_slug": "x",
              "_base": "main", "_dev_test_cmd": "pytest", "_dev_run_log": "/l.log"}
    entry = {"prd_path": "state/prd/x/p.md", "source_path": "src.md"}
    prof = {"name": "proj", "default_branch": "main"}
    rec = AGG._subgraph_result_to_record(result, entry, prof)
    assert set(rec.keys()) == _DISPATCH_ONE_REC_KEYS
    assert len(rec) == 24                          # 显式 24 字段（21 初始 + 3 动态，防静默增减）


def test_field_mapping_state_to_rec():
    """子图 state 字段（_前缀）→ rec key（无前缀）映射精确。"""
    result = {"_exit_status": "interrupted_pr", "_pr_url": None, "_branch": "pa-dev-y",
              "_dev_killed": True, "_dev_stalled": False, "_dev_cost": 1.2,
              "_dev_turns": 8, "_verify_verdict": "revise", "_verify_payload": {"k": 2},
              "_skip_reason": None, "_dev_test_cmd": "tox", "_dev_run_log": "/r.log",
              "_merge_commit": "abc123", "_reverted": True, "_triage_reason": "gate",
              "_post_merge_verdict": "FAIL", "verify_round": 2, "_slug": "y", "_base": "master"}
    entry = {"prd_path": "p/y.md"}
    prof = {"name": "cc", "default_branch": "master"}
    rec = AGG._subgraph_result_to_record(result, entry, prof)
    assert rec["status"] == "interrupted_pr"       # _exit_status → status
    assert rec["pr_url"] is None
    assert rec["branch"] == "pa-dev-y"             # _branch → branch
    assert rec["dev_killed"] is True
    assert rec["stalled"] is False                 # _dev_stalled → stalled
    assert rec["run_log"] == "/r.log"              # _dev_run_log → run_log
    assert rec["dev_cost"] == 1.2
    assert rec["dev_turns"] == 8
    assert rec["verify"] == {"k": 2}               # _verify_payload → verify
    assert rec["dev_test_cmd"] == "tox"
    assert rec["verify_verdict"] == "revise"
    assert rec["verify_round"] == 2
    assert rec["merge_commit"] == "abc123"
    assert rec["reverted"] is True
    assert rec["triage_reason"] == "gate"
    assert rec["post_merge_verdict"] == "FAIL"
    assert rec["slug"] == "y"
    assert rec["base"] == "master"
    assert rec["project"] == "cc"
    assert rec["prd_path"] == "p/y.md"


def test_missing_fields_default_to_none_or_false():
    """缺失子图 state 字段 → rec 默认 None/False（对齐 dispatch_one rec 初始默认值）。"""
    rec = AGG._subgraph_result_to_record({}, {"prd_path": "p.md"}, {"name": "p"})
    assert rec["status"] == "fail"                 # 无 _exit_status/terminal → fail
    assert rec["dev_killed"] is False
    assert rec["reverted"] is False
    assert rec["dev_cost"] is None
    assert rec["pr_url"] is None
    assert rec["verify_round"] is None
    assert rec["slug"] == "p"                      # fallback Path("p.md").stem


def test_status_fallback_terminal_when_no_exit_status():
    """无 _exit_status 时 status fallback terminal enum（terminal_emit _GRAPH_TERMINAL_TO_EXIT 对齐）。"""
    rec = AGG._subgraph_result_to_record({"terminal": "blocked"}, {"prd_path": "p.md"}, {"name": "p"})
    assert rec["status"] == "blocked"


def test_build_dispatch_shell_has_required_shell_fields():
    """_build_dispatch_shell 产 13 字段 _REQUIRED_SHELL（对齐 graph_pa_recovery，崩溃恢复续跑前置）。"""
    import graph_pa_recovery as GR
    entry = {"prd_path": "state/prd/x/p.md", "source_path": "s.md"}
    prof = {"name": "proj", "repo": "/repo", "default_branch": "main"}
    shell = AGG._build_dispatch_shell(entry, prof, "20260812")
    missing = [k for k in GR._REQUIRED_SHELL if k not in shell]
    assert missing == [], f"shell 缺 _REQUIRED_SHELL 字段：{missing}"
    assert shell["verify_round"] == 1              # 每 PRD 重置（不跨 PRD 累加）
    assert shell["_worktree_abs"] == "/repo"       # 主仓路径（worktree node 覆盖成 detached wt）
    assert shell["_prd_path"] == "state/prd/x/p.md"
    assert shell["stamp"] == "20260812"
