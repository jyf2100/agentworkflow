#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""graph_pa_critic.py — critic 子图（任务 3.3）：PersonaNode + revise 回环 round2。

design D3 L73：critic revise（prd↔critic）= graph 条件边 + state 轮次计数器 prd_round 判上限。
stage_critic L915-965 的命令式 revise 回环（verdict=revise → pa-prd round2 → 再 critic round2，
1 次修订机会，SPEC §4.3）迁为 LangGraph StateGraph 条件边回环。

子图拓扑：
    START → critic ─(verdict=revise 且 prd_round<MAX)→ revise → critic（round2）
                     └─(pass/drop 或 prd_round 用尽)→ END

critic = PersonaNode（expose_verdict=True，产 pass/revise/drop）；revise = PersonaNode（pa-prd round2）。
prd_round 上限 CRITIC_MAX_ROUNDS=2（round1 + 1 次 revise = round2，对齐 stage_critic 1 次修订）。
节点幂等靠 reconcile + idempotency_key（D3 三不变式，跨节点回环重入安全，R3）。
entries 累积每轮 critic 的 entry（对齐 stage_critic 返回 list[dict]）。

不用 Checkpointer（D2 journal 单写真源）；条件边路由是机械的（读 verdict + prd_round，不替判，D6）。
单 PRD 维度；多 PRD 遍历由上层 graph（task 3.9 拓扑）。
"""
from __future__ import annotations

from typing import TypedDict

from langgraph.graph import START, END, StateGraph

import graph_pa_nodes as GN

CRITIC_MAX_ROUNDS = 2   # round1 + 1 次 revise = round2（stage_critic 1 次修订机会，SPEC §4.3）


class CriticSubState(TypedDict, total=False):
    """critic 子图 state（单 PRD 维度）。path 字段是运行期状态（vault 相对路径），非 artifact 契约。"""
    # 标识 / 输入（str 经 _ni 转发；大对象直接读 state）
    run_id: str
    stamp: str
    config: dict
    _prd_path: str           # 待过闸 PRD（vault 相对路径）
    _source_path: str        # 信息源原文路径
    _project: str            # PRD 所属 project（label + revise key）
    _prof: dict              # 单 PRD 的 profile（critic_prompt 用）
    _profiles: dict          # 全量 profiles（revise 的 prd_prompt 用）
    # 轮次 / 产物
    prd_round: int           # 轮次计数器（1=首轮，2=revise 后；route 判上限，D3）
    _critic_verdict: str     # pass/revise/drop（critic node 写，route 读）
    _critic_payload: dict    # critic 完整 payload（含 revisions_needed 供 revise）
    _revised_prd: dict       # revise node 产物
    entries: list            # 累积 critic entries（对齐 stage_critic 返回 list[dict]）


def _critic_fn(state: dict) -> dict:
    """critic node：调 node_critic + 累积 entry（带 round/revised 标记）。"""
    update = GN.node_critic(state)                    # {obs_log, _critic_payload, _critic_verdict}
    rnd = state.get("prd_round", 1)
    payload = update.get("_critic_payload") or {}
    entry = dict(payload)
    entry.setdefault("round", rnd)
    entry.setdefault("revised", rnd > 1)             # round2+ 是 revise 后的再过闸
    update["entries"] = list(state.get("entries", [])) + [entry]
    return update


def _revise_fn(state: dict) -> dict:
    """revise node：调 pa-prd round2 + bump prd_round（→ 下一轮 critic 是 round2）。"""
    update = GN.node_prd_revise(state)               # {obs_log, _revised_prd}
    update["prd_round"] = 2                           # revise 后回 critic 是 round2（1 次修订上限）
    return update


def route_critic(state: dict) -> str:
    """条件边路由（机械，不替判，D6）：verdict=revise 且 prd_round<MAX → revise；否则 done。"""
    verdict = state.get("_critic_verdict")
    rnd = state.get("prd_round", 1)
    if verdict == "revise" and rnd < CRITIC_MAX_ROUNDS:
        return "revise"
    return "done"


def build_critic_subgraph():
    """构建 critic 子图（compiled StateGraph）。无 Checkpointer（D2 journal 单写真源）。"""
    g = StateGraph(CriticSubState)
    g.add_node("critic", _critic_fn)
    g.add_node("revise", _revise_fn)
    g.add_edge(START, "critic")
    g.add_conditional_edges("critic", route_critic, {"revise": "revise", "done": END})
    g.add_edge("revise", "critic")                    # 回环（revise → critic round2）
    return g.compile()
