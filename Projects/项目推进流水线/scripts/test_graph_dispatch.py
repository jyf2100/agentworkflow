#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_graph_dispatch.py — dispatch 子图组装测试（任务 3.5g）。

验证：
① build_dispatch_subgraph 编译成功（add_node/add_edge/add_conditional_edges 引用合法 = 拓扑自洽）
② 10 节点齐（slot_acquire/admission/worktree/verify 子图/publication_reconcile/publish_gates/
   publish_merge/publish_baseline/terminal_emit/slot_release）
③ 条件边路由机械正确（terminal→terminal_emit；auto_merge on→publish_gates/off→publish_baseline）
④ DispatchSubState 是 VerifySubState 超集（verify 子图作 node 嵌入 schema 兼容）
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import graph_pa_dispatch as GD
import graph_pa_contracts as C
import graph_pa_verify as GV


_NODES = ["slot_acquire", "admission", "worktree", "verify", "publication_reconcile",
          "publish_gates", "publish_merge", "publish_baseline", "terminal_emit", "slot_release"]


# ── 编译 + 拓扑 ───────────────────────────────────────────────────────
def test_dispatch_subgraph_compiles():
    """build 不抛 = 节点/边引用全合法（拓扑自洽）。"""
    g = GD.build_dispatch_subgraph()
    assert g is not None


def test_dispatch_nodes_present():
    """10 节点齐（含 verify 子图作 node 嵌入）。"""
    g = GD.build_dispatch_subgraph()
    names = set(getattr(g, "nodes", {}) or {})
    # compiled graph.nodes 可能不含 START/END；至少 10 个业务节点在
    missing = [n for n in _NODES if n not in names]
    assert not missing, f"缺失节点: {missing}"


def test_dispatch_substate_superset_of_verify():
    """DispatchSubState 是 VerifySubState 超集（verify 子图嵌入 schema 兼容）。"""
    verify_keys = set(GV.VerifySubState.__annotations__)
    dispatch_keys = set(GD.DispatchSubState.__annotations__)
    assert verify_keys <= dispatch_keys                    # dispatch 含 verify 全部字段
    # dispatch 独有字段（shell + publication）
    assert "_slot_handle" in dispatch_keys - verify_keys
    assert "_exit_status" in dispatch_keys - verify_keys


# ── 条件边路由（机械，D6）──────────────────────────────────────────────
def test_route_slot_acquire():
    assert GD.route_slot_acquire({"terminal": "skip"}) == "terminal_emit"
    assert GD.route_slot_acquire({}) == "admission"


def test_route_admission():
    assert GD.route_admission({"terminal": C.STATUS_BLOCKED}) == "terminal_emit"
    assert GD.route_admission({}) == "worktree"


def test_route_worktree():
    assert GD.route_worktree({"terminal": "fail"}) == "terminal_emit"
    assert GD.route_worktree({}) == "verify"


def test_route_after_verify():
    """verify 子图 terminal（interrupted_pr/blocked/triaged）→ terminal_emit；pass → publication_reconcile。"""
    assert GD.route_after_verify({"terminal": C.STATUS_INTERRUPTED}) == "terminal_emit"
    assert GD.route_after_verify({"terminal": C.STATUS_TRIAGED}) == "terminal_emit"
    assert GD.route_after_verify({}) == "publication_reconcile"


def test_route_after_reconcile_terminal():
    """publication_reconcile unknown blocked → terminal_emit（fail-safe 不盲目 publish）。"""
    assert GD.route_after_reconcile({"terminal": C.STATUS_BLOCKED}) == "terminal_emit"


def test_route_after_reconcile_auto_merge_on():
    """auto_merge on + 无 terminal → publish_gates（真合 main 路）。"""
    flags = types.SimpleNamespace(single_flight_auto_merge=True)
    assert GD.route_after_reconcile({"_coord_flags": flags}) == "publish_gates"


def test_route_after_reconcile_baseline():
    """auto_merge off + 无 terminal → publish_baseline（兜底开 PR，baseline 路）。"""
    flags = types.SimpleNamespace(single_flight_auto_merge=False)
    assert GD.route_after_reconcile({"_coord_flags": flags}) == "publish_baseline"
    assert GD.route_after_reconcile({}) == "publish_baseline"   # 无 flags = baseline


def test_route_publish_gates():
    """publish_gates terminal（cooldown triaged/open_intent halted）→ terminal_emit；pass → publish_merge。"""
    assert GD.route_publish_gates({"terminal": C.STATUS_TRIAGED}) == "terminal_emit"
    assert GD.route_publish_gates({"terminal": C.STATUS_HALTED}) == "terminal_emit"
    assert GD.route_publish_gates({}) == "publish_merge"
