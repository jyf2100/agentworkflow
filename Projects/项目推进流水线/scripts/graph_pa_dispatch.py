#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""graph_pa_dispatch.py — dispatch 子图组装（任务 3.5g）。

把 3.5a-3.5f 的节点串成完整 dispatch 子图（单 PRD 维度）。命令式真源是 ``dispatch_one``（run_daily L2029-2515）：
slot 准入 → admission 4 闸 → worktree → verify 闭环子图 → publication（reconcile + baseline/gates/merge）→
terminal_emit 统一收尾 → slot_release。各 terminal 出口（slot/admission/worktree/verify/publication）经条件边
机械路由到 terminal_emit（D6：读 state.terminal，不替判）。

拓扑（对齐 plan task 3.5）::

    START → slot_acquire ─(terminal)─→ terminal_emit
                          └─(pass)→ admission ─(terminal)→ terminal_emit
                                                 └─(pass)→ worktree ─(terminal)→ terminal_emit
                                                                     └─(pass)→ verify(子图)
         verify ─(terminal: interrupted_pr/blocked_test_gate/triaged/blocked_evidence)→ terminal_emit
                └─(pass)→ publication_reconcile ─(terminal unknown)→ terminal_emit
                                       └─(auto_merge off)→ publish_baseline → terminal_emit
                                       └─(auto_merge on)→ publish_gates ─(terminal: triaged/halted)→ terminal_emit
                                                                     └─(pass)→ publish_merge → terminal_emit
         terminal_emit → slot_release → END

baseline（flag 全 off）：slot_acquire/slot_release no-op pass；publication_reconcile no-op；publish_baseline 兜底开 PR。
auto_merge on：publication 走 publish_gates→publish_merge 真合 main（canary）。

无 Checkpointer（D2 journal 单写真源）；条件边机械路由（D6）；DispatchSubState 是 VerifySubState 超集
（verify 子图作 node 嵌入，schema 兼容）。
"""
from __future__ import annotations

from langgraph.graph import START, END, StateGraph

import graph_pa_nodes as GN
import graph_pa_verify as GV
from graph_pa_verify import VerifySubState


class DispatchSubState(VerifySubState, total=False):
    """dispatch 子图 state（单 PRD 维度）。VerifySubState 超集 + dispatch shell 字段。

    dispatch shell 注入（admission/worktree 读）+ 各节点写（slot/publication/terminal_emit）。
    terminal/_exit_status 是条件边机械路由锚点（D6）。
    """
    # admission/worktree 读
    _repo: str               # 目标仓路径（_post_merge_test_cmd / publish_baseline rec）
    _max_inflight: int       # admission 准入上限（serial_shadow→1 / prof.max_prs_in_flight）
    _admission_inflight: int  # admission 当前 inflight 计数（观测）
    # slot lifecycle（slot_acquire/release/halt 写）
    _slot_handle: object     # SF.SlotHandle（acquired→release/halt；None=baseline 无 slot）
    _slot_released: bool
    # publication / terminal_emit 写
    _exit_status: str        # 原始 dispatch status（terminal_emit _sj_terminal 词汇；优先于 terminal enum）
    _triage_reason: str
    _skip_reason: str
    _blocked_check: str
    _dev_off_track: bool     # admission/publish_baseline rec 读
    _dev_stalled: bool
    _dev_killed: bool
    _merge_commit: str       # publish_merge 合并 commit
    _revert_commit: str
    _reverted: bool
    _post_merge_verdict: str
    _publication_reconciliation: dict
    _terminal_emitted: bool


# ── 条件边路由（机械，D6：读 state.terminal，不替判）──────────────────────
def route_slot_acquire(state: dict) -> str:
    """slot_acquire 出边：terminal（blocked/skip）→ terminal_emit；否则 admission。"""
    return "terminal_emit" if state.get("terminal") else "admission"


def route_admission(state: dict) -> str:
    """admission 出边：terminal（blocked/skip）→ terminal_emit；否则 worktree。"""
    return "terminal_emit" if state.get("terminal") else "worktree"


def route_worktree(state: dict) -> str:
    """worktree 出边：terminal（fail）→ terminal_emit；否则 verify 闭环子图。"""
    return "terminal_emit" if state.get("terminal") else "verify"


def route_after_verify(state: dict) -> str:
    """verify 子图出边：terminal（interrupted_pr/blocked_test_gate/triaged/blocked_evidence）→ terminal_emit；否则 publication_reconcile。"""
    return "terminal_emit" if state.get("terminal") else "publication_reconcile"


def route_after_reconcile(state: dict) -> str:
    """publication_reconcile 出边（机械+flag，D6）：terminal（unknown blocked）→ terminal_emit；
    auto_merge on → publish_gates；off → publish_baseline（baseline 兜底开 PR）。"""
    if state.get("terminal"):
        return "terminal_emit"
    flags = state.get("_coord_flags")
    if flags and getattr(flags, "single_flight_auto_merge", False):
        return "publish_gates"
    return "publish_baseline"


def route_publish_gates(state: dict) -> str:
    """publish_gates 出边：terminal（cooldown triaged/open_intent halted）→ terminal_emit；否则 publish_merge。"""
    return "terminal_emit" if state.get("terminal") else "publish_merge"


def build_dispatch_subgraph():
    """构建 dispatch 子图（compiled StateGraph，单 PRD 维度）。无 Checkpointer（D2 journal 单写真源）。

    10 节点（slot_acquire/admission/worktree/verify 子图/publication_reconcile/publish_gates/
    publish_merge/publish_baseline/terminal_emit/slot_release）+ 条件边机械路由。各 terminal 出口 →
    terminal_emit 汇聚；terminal_emit → slot_release → END。
    """
    g = StateGraph(DispatchSubState)
    g.add_node("slot_acquire", GN.node_slot_acquire)
    g.add_node("admission", GN.node_admission)
    g.add_node("worktree", GN.node_worktree)
    g.add_node("verify", GV.build_verify_subgraph())              # verify 闭环子图作 node 嵌入
    g.add_node("publication_reconcile", GN.node_publication_reconcile)
    g.add_node("publish_gates", GN.node_publish_gates)
    g.add_node("publish_merge", GN.node_publish_merge)
    g.add_node("publish_baseline", GN.node_publish_baseline)
    g.add_node("terminal_emit", GN.node_terminal_emit)
    g.add_node("slot_release", GN.node_slot_release)
    # START → slot_acquire（serial_shadow on 先占 slot；off no-op pass）
    g.add_edge(START, "slot_acquire")
    # 准入/worktree 链：terminal → terminal_emit；否则下一闸
    g.add_conditional_edges("slot_acquire", route_slot_acquire,
                            {"terminal_emit": "terminal_emit", "admission": "admission"})
    g.add_conditional_edges("admission", route_admission,
                            {"terminal_emit": "terminal_emit", "worktree": "worktree"})
    g.add_conditional_edges("worktree", route_worktree,
                            {"terminal_emit": "terminal_emit", "verify": "verify"})
    # verify 闭环 → terminal（各出口）→ terminal_emit；pass → publication_reconcile
    g.add_conditional_edges("verify", route_after_verify,
                            {"terminal_emit": "terminal_emit", "publication_reconcile": "publication_reconcile"})
    # publication：reconcile（flag-gate）→ terminal / auto_merge 分支
    g.add_conditional_edges("publication_reconcile", route_after_reconcile,
                            {"terminal_emit": "terminal_emit", "publish_gates": "publish_gates",
                             "publish_baseline": "publish_baseline"})
    g.add_conditional_edges("publish_gates", route_publish_gates,
                            {"terminal_emit": "terminal_emit", "publish_merge": "publish_merge"})
    # publish_baseline / publish_merge → terminal_emit（固定；_exit_status 喂 emit）
    g.add_edge("publish_baseline", "terminal_emit")
    g.add_edge("publish_merge", "terminal_emit")
    # 统一收尾：terminal_emit → slot_release → END
    g.add_edge("terminal_emit", "slot_release")
    g.add_edge("slot_release", END)
    return g.compile()
