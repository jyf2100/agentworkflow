#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_check_boundary.py — check_boundary lint 规则测试（任务 2.4，D3/R6）。"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_boundary as CB


# ── verdict_boundary 规则 ────────────────────────────────────────────
def test_persona_factory_verdict_allowed():
    src = "def make_persona_node():\n    out['verdict'] = {'value': 'pass'}\n"
    assert CB.check_source(src) == []


def test_mechanical_factory_verdict_rejected():
    src = "def make_mechanical_node():\n    out['verdict'] = {'value': 'pass'}\n"
    vs = CB.check_source(src, "f.py")
    assert len(vs) == 1 and vs[0].rule == "verdict_boundary" and vs[0].line == 2


def test_gateway_factory_verdict_rejected():
    src = "def make_gateway_node():\n    return {'verdict': v}\n"
    vs = CB.check_source(src)
    assert len(vs) == 1 and vs[0].rule == "verdict_boundary"


def test_devloop_factory_verdict_rejected():
    src = "def make_devloop_node():\n    x = dict(verdict='drop')\n"
    vs = CB.check_source(src)
    assert len(vs) == 1 and vs[0].rule == "verdict_boundary"


def test_non_factory_function_verdict_not_flagged():
    # 普通函数（非 *node 工厂）写 verdict 不在本 lint 范围（运行时 commit_node 守）
    src = "def helper():\n    out['verdict'] = x\n"
    assert CB.check_source(src) == []


# ── no_bare_path 规则 ────────────────────────────────────────────────
def test_bare_path_in_typeddict_rejected():
    src = "class NodeOutput(TypedDict):\n    path: str\n"
    vs = CB.check_source(src)
    assert len(vs) == 1 and vs[0].rule == "no_bare_path"


def test_rel_path_is_allowed():
    # ArtifactHandle.rel_path 是合法字段（store+rel_path 可移植），不触发
    src = "class ArtifactHandle(TypedDict):\n    rel_path: str\n"
    assert CB.check_source(src) == []


def test_artifact_handle_field_not_bare_str_when_typed():
    src = "class NodeOutput(TypedDict):\n    artifacts: list\n"
    assert CB.check_source(src) == []


# ── 真实编排层源码干净（回归守门）────────────────────────────────────
def test_real_graph_pa_nodes_clean():
    vs = CB.check_file(os.path.join(os.path.dirname(__file__), "graph_pa_nodes.py"))
    assert vs == [], "graph_pa_nodes.py 不应有边界违规: " + str(vs)


def test_real_graph_pa_contracts_clean():
    vs = CB.check_file(os.path.join(os.path.dirname(__file__), "graph_pa_contracts.py"))
    assert vs == [], "graph_pa_contracts.py 不应有边界违规: " + str(vs)


# ── CLI main exit code ──────────────────────────────────────────────
def test_main_exit_code_clean_vs_dirty():
    clean = "def make_persona_node():\n    pass\n"
    dirty = "def make_mechanical_node():\n    out['verdict'] = 1\n"
    with tempfile.TemporaryDirectory() as d:
        cp = os.path.join(d, "graph_pa_clean.py"); open(cp, "w").write(clean)
        dp = os.path.join(d, "graph_pa_dirty.py"); open(dp, "w").write(dirty)
        assert CB.main(["x", cp]) == 0
        assert CB.main(["x", dp]) == 1
