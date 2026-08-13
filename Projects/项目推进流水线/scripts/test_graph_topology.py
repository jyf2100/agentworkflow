#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_graph_topology.py — 主图拓扑测试（task 3.9）。

验证 build_main_graph 编译期条件加 node + 线性边 + inject 条件/插入位置 + lo/hi 窗口 + 无 Checkpointer。
主图严格线性（无条件分支，terminal 不短路主图——全在 dispatch 子图内，D5/D6）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import graph_pa


def _nodes(g):
    """compiled graph 的业务 node 名（排除 __start__/__end__）。"""
    return sorted(n for n in g.get_graph().nodes if n not in ("__start__", "__end__"))


def _topo_nodes(g):
    """compiled graph 业务 node 按 add_node 插入顺序（= 拓扑顺序，验证线性链顺序）。"""
    return [n for n in g.get_graph().nodes if n not in ("__start__", "__end__")]


def test_full_graph_6_stages_no_inject():
    """lo=0,hi=6 无 inject → 6 node（fetch/radar/prd/critic/dispatch/report）。"""
    g = graph_pa.build_main_graph({"lo": 0, "hi": 6})
    assert _nodes(g) == ["critic", "dispatch", "fetch", "prd", "radar", "report"]


def test_full_graph_7_stages_with_inject():
    """inject_prd 非空 → +inject（7 node）。"""
    g = graph_pa.build_main_graph({"lo": 0, "hi": 6, "inject_prd": "x.md"})
    assert _nodes(g) == ["critic", "dispatch", "fetch", "inject", "prd", "radar", "report"]


def test_inject_only_when_inject_prd_set():
    """inject_prd=None → 无 inject node；非空 → 有（编译期条件，对齐 _run_pipeline L3395）。"""
    assert "inject" not in _nodes(graph_pa.build_main_graph({"lo": 0, "hi": 6}))
    assert "inject" in _nodes(graph_pa.build_main_graph({"lo": 0, "hi": 6, "inject_prd": "x.md"}))


def test_from_stage_window():
    """lo/hi 控制哪些 node 进拓扑（对齐 _run_pipeline if lo<=N<=hi）。"""
    assert _nodes(graph_pa.build_main_graph({"lo": 5, "hi": 5})) == ["dispatch"]
    assert _nodes(graph_pa.build_main_graph({"lo": 0, "hi": 1})) == ["fetch", "radar"]
    assert _nodes(graph_pa.build_main_graph({"lo": 2, "hi": 4})) == ["critic", "prd"]


def test_inject_inserted_after_prd_before_critic():
    """inject 在 prd 和 critic 之间（prd→inject→critic，非 prd→critic 直连）。

    inject 包装 op 写回 manifest+stamp 覆盖 prd 产物（对齐 _run_pipeline L3396）。
    """
    g = graph_pa.build_main_graph({"lo": 0, "hi": 6, "inject_prd": "x.md"})
    ns = _topo_nodes(g)            # 插入顺序 = 拓扑顺序（sorted 字母序无法判位）
    assert ns.index("prd") < ns.index("inject") < ns.index("critic")


def test_no_inject_when_window_excludes_3():
    """lo/hi 窗口不含 stage 3 → 即使 inject_prd 非空也不挂 inject。"""
    g = graph_pa.build_main_graph({"lo": 0, "hi": 2, "inject_prd": "x.md"})
    assert "inject" not in _nodes(g)           # hi=2（prd）不含 stage 3（inject）
    assert _nodes(g) == ["fetch", "prd", "radar"]


def test_no_checkpointer():
    """主图无 Checkpointer（D2 journal 单写真源）。compiled graph.checkpointer 应为 falsy。"""
    g = graph_pa.build_main_graph({"lo": 0, "hi": 6})
    assert not g.checkpointer, f"主图不应有 checkpointer，实际 {g.checkpointer!r}"


def test_empty_window_start_to_end():
    """窗口空（lo>hi 全跳过）→ START→END 直通（防御性，不崩）。"""
    g = graph_pa.build_main_graph({"lo": 7, "hi": 0})
    assert _nodes(g) == []


def test_linear_edges_chained():
    """全图 edges 是严格线性链（START→fetch→radar→prd→critic→dispatch→report→END，无跳连/分支）。

    r-review I3：原有测试只验 node 插入顺序（_topo_nodes index），不验 edges——add_edge 漏连/跳连
    不会被 _nodes/_topo_nodes 抓住。本测试断言 edges 精确集合（D8 严格线性，terminal 不短路主图）。
    """
    g = graph_pa.build_main_graph({"lo": 0, "hi": 6})
    edges = {(e[0], e[1]) for e in g.get_graph().edges}   # langgraph MultiDiGraph edges 含 key（3-tuple），取前 2
    chain = ["__start__", "fetch", "radar", "prd", "critic", "dispatch", "report", "__end__"]
    expected = set(zip(chain, chain[1:]))
    assert edges == expected, f"edges 漂移（应有线性链）：{edges ^ expected}"


def test_linear_edges_with_inject():
    """inject 挂入时 edges 含 prd→inject→critic（非 prd→critic 直连，inject 覆盖 manifest）。"""
    g = graph_pa.build_main_graph({"lo": 0, "hi": 6, "inject_prd": "x.md"})
    edges = {(e[0], e[1]) for e in g.get_graph().edges}   # langgraph MultiDiGraph edges 含 key（3-tuple），取前 2
    assert ("prd", "inject") in edges and ("inject", "critic") in edges   # inject 正确串入
    assert ("prd", "critic") not in edges                                # 非直连
