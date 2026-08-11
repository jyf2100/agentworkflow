#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""graph_pa_state.py — LangGraph graph state（TypedDict，只持可序列化）。

langgraph-workflow-upgrade 任务 2.2 + design D3（轮次计数器）+ R8（state 可序列化可移植）。

不变式：
- **只持可序列化**（str / int / dict / list；ArtifactHandle 是 dict）。绝对路径**不入** state——
  node 内经 resolve_handle 即时解析（rel_path+store 可移植，R8/cross-machine bundle 友好）。
- **轮次计数器**（prd_round / verify_round）：跨节点回环（critic/verify revise）的条件边 router
  读它判上限（D3 不变式 2）；router 不碰复杂对象，只读 int。
- **terminal**：node 产的 enum 终态信号（interrupted_pr/triaged/blocked/halted/cooldown）。条件边
  机械路由（不替判），terminal 非空 → END（spec「升人工路径保持机械硬门」D5 撤）。
- **obs_log / side_effect_log**：Annotated[list, operator.add] 累加（langgraph reducer，图表达层非
  持久化）。obs_log 喂 report node 聚合为可查询 metrics（决策 M 路径 A）；side_effect_log 喂 reconcile。
- journal 单写真源（D2）：state 仅内存，不替代持久化；崩溃恢复走 recovery_cli 重建 initial state。

注：Annotated reducer 是 langgraph StateGraph 的 state 合并语义（图表达层），**不是** Checkpointer
持久化（D2 明确不用 Checkpointer）。
"""
from __future__ import annotations

import operator
from typing import Annotated, TypedDict

# ── 跨节点回环上限（D3 不变式 2 + spec「升人工路径保持机械硬门」）──────────────
# 用满 → enum 终态（非 interrupt）：verify → interrupted_pr、prd critic → triaged。
VERIFY_MAX_ROUNDS = 2   # pa-verify revise 闭环上限
PRD_MAX_ROUNDS = 2      # critic revise 回环上限


class GraphState(TypedDict, total=False):
    """pa 7 阶段 graph 的内存 state（可序列化）。绝对路径不入 state。"""
    # 标识
    run_id: str
    thread_id: str             # graph invoke thread_id（run_<stamp>）
    stamp: str                 # YYYYMMDD
    # 产物（ArtifactHandle as dict / payload dict / list，全可序列化）
    fetch_items: list[dict]    # fetch 各源 items（ArtifactHandle list）
    candidates: dict           # radar 产物 payload（{candidates, stats, ...}）
    prd_manifest: dict         # prd manifest payload（{prds:[...]}）
    critic_results: list[dict] # critic 逐份 verdict
    dispatch_results: list[dict]  # dispatch 逐项目结果
    report: dict               # report artifact handle
    # 轮次计数器（跨节点回环 router 读判上限，D3 不变式 2）
    prd_round: int             # critic revise 回环轮次
    verify_round: int          # verify revise 闭环轮次
    # 终态路由信号（条件边机械路由 enum 终态，不替判；非空 → END）
    terminal: str
    # 可观测性累加（report node 聚合 → 可查询 metrics，决策 M 路径 A）
    obs_log: Annotated[list[dict], operator.add]      # 每 node 追加 Obs
    side_effect_log: Annotated[list[dict], operator.add]  # 喂 reconcile
    # 恢复上下文（崩溃恢复：recovery_cli 重建 initial state，D2 / 任务 3.8）
    recovery: dict


def initial_state(*, run_id: str, thread_id: str, stamp: str) -> GraphState:
    """构造空 initial state（崩溃恢复经 recovery_cli 重建后也产此形态，再 graph.invoke 续跑）。"""
    return {'run_id': run_id, 'thread_id': thread_id, 'stamp': stamp,
            'prd_round': 0, 'verify_round': 0, 'obs_log': [], 'side_effect_log': []}


def is_terminal(state: dict) -> bool:
    """terminal 非空 str → 已进入 enum 终态（条件边据此路由到 END）。"""
    t = state.get('terminal')
    return isinstance(t, str) and t != ''


def mark_terminal(state: dict, status: str) -> None:
    """node 产 enum 终态时调用（升人工路径机械硬门，不替判，D5 撤）。"""
    state['terminal'] = status


def bump_round(state: dict, key: str) -> int:
    """轮次计数器 +1 并返回新值。key ∈ {'prd_round', 'verify_round'}（跨节点回环判上限，D3）。"""
    state[key] = state.get(key, 0) + 1
    return state[key]
