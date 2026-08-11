#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""graph_pa_verify.py — verify 子图（任务 3.4）：PersonaNode revise 闭环 + interrupted_pr enum 终态。

design D3 L73 + D5（撤 interrupt，升人工路径保持机械硬门）：verify revise 闭环（verify↔dev-redo）
= graph 条件边 + state 轮次计数器 verify_round 判上限。stage_dispatch verify 闭环（L2163-2512 命令式
for-round：dev→独立验证→pa-verify 裁判；判红保留分支+反馈进 PRD+增量重投；判绿兜底开 PR）迁为
LangGraph StateGraph 条件边回环。dev loop / independent_verify / reconcile 是 dispatch 子图（task 3.5），
本子图只做 verify PersonaNode + revise 闭环结构 + interrupted_pr 终态（spec「升人工路径保持机械硬门」）。

子图拓扑：
    START → verify ─(verdict=revise 且 verify_round<MAX)→ redo → verify（round2）
                     └─(pass / verify_round 用满 / 异常)→ END

verify = PersonaNode（expose_verdict=True，产 pass/revise）；redo = MechanicalNode stub（task 3.5 接
DevLoopNode）。verify_round 上限 VERIFY_MAX_ROUNDS=2（round1 + 1 次 redo = round2，对齐 stage_dispatch
VERIFY_MAX_ROUNDS L118）。用满（round2 仍 revise）→ terminal=interrupted_pr（enum 终态，非 interrupt，
D5 撤）；上层 dispatch 子图读 terminal 判终态（条件边机械路由，不替判 D6）。
entries 累积每轮 verify 的 entry（对齐 stage_dispatch rec verify 历史）。

install_log 接入（task 4.1 预留）：state._install_log（ArtifactHandle 形态）经 node_verify build_prompt
暴露给 pa-verify（传递通道）；默认 None → byte-identical。resolve 绝对 + must_exist fail-closed + 上游
independent_verify 实装留 task 4.1（graph state 不持绝对路径 R8）。
不用 Checkpointer（D2 journal 单写真源）；条件边路由机械（读 verdict + verify_round，不替判，D6）。
单项目维度；多项目遍历由上层 dispatch 子图（task 3.5）。

与 graph_pa_critic.py 的关键差异：critic 用满（round2 仍 revise）→ 最后 verdict（上层处理，不 mark
terminal）；verify 用满 → terminal=interrupted_pr（机械硬门升人工，不 drop 半成品，对齐 stage_dispatch
L2510-2512「对账降级 interrupted_pr，留 review」）。
"""
from __future__ import annotations

from typing import TypedDict

from langgraph.graph import START, END, StateGraph

import graph_pa_contracts as C
import graph_pa_nodes as GN
from graph_pa_state import VERIFY_MAX_ROUNDS


class VerifySubState(TypedDict, total=False):
    """verify 子图 state（单项目维度）。path 字段是运行期状态（vault/state 相对），非 artifact 契约。"""
    # 标识 / 输入（str 经 _ni 转发；大对象直接读 state）
    run_id: str
    stamp: str
    config: dict
    _prd_path: str           # 待验 PRD（vault 相对路径，verify_prompt 用）
    _project: str            # 项目 id（_ik 幂等键）
    _slug: str               # 项目 slug（label f"verify:{slug}:r{round}"，对齐 _pa_verify_round L1420）
    _branch: str             # dev 分支（verify_prompt base..branch）
    _base: str               # cur_base（round≥2 为上次 dev 分支，增量重投，stage_dispatch L2472）
    _diff_path: str          # branch diff 文件（STATE_DIR/runs/<proj>/<stamp>_<slug>.r<round>.diff）
    _prof: dict              # 单项目 profile（verify_prompt 用）
    _verify_payload: dict    # independent_verify 产物 {test_rc, test_log, pass, ...}（dispatch 子图 task 3.5 填）
    _install_log: dict       # task 4.1 预留：install_log ArtifactHandle（传递通道；默认 None → byte-identical）
    # 轮次 / 产物
    verify_round: int        # 轮次计数器（1=首轮，2=redo 后；route 判上限，D3）
    _verify_verdict: str     # pass/revise（verify node 写，route 读）
    _verify_result: dict     # verify 完整 payload（含 feedback_section 供 redo 注入 dev）
    _redo_feedback: str      # redo stub 暂存（task 3.5 dev 注入用）
    entries: list            # 累积 verify entries（对齐 stage_dispatch rec verify 历史）
    terminal: str            # enum 终态（用满 → interrupted_pr；条件边机械路由，非空 → END）


def _verify_fn(state: dict) -> dict:
    """verify node：调 node_verify + 累积 entry + 用满 → interrupted_pr enum 终态。"""
    update = GN.node_verify(state)                    # {obs_log, _verify_verdict, _verify_result}
    rnd = state.get("verify_round", 1)
    verdict = update.get("_verify_verdict")
    # 用满（verify_round>=MAX 仍 revise）→ interrupted_pr enum 终态（机械硬门升人工，非 interrupt，D5 撤）。
    # 不 drop 半成品——对齐 stage_dispatch L2510-2512「判红用满/异常/无产出 → 对账降级 interrupted_pr」。
    if verdict == "revise" and rnd >= VERIFY_MAX_ROUNDS:
        update["terminal"] = C.STATUS_INTERRUPTED
    payload = update.get("_verify_result") or {}
    entry = dict(payload)
    entry.setdefault("round", rnd)
    update["entries"] = list(state.get("entries", [])) + [entry]
    return update


def _redo_fn(state: dict) -> dict:
    """redo node：调 dev redo stub（task 3.5 接 DevLoopNode）+ bump verify_round（→ 下轮 verify 是 round2）。"""
    update = GN.node_dev_redo(state)                  # {obs_log, _redo_feedback}
    update["verify_round"] = state.get("verify_round", 1) + 1
    return update


def route_verify(state: dict) -> str:
    """条件边路由（机械，不替判，D6）：verdict=revise 且 verify_round<MAX → redo；否则 done。

    done 涵盖 pass（成功）/ 用满（terminal=interrupted_pr，上层读）/ 异常（verdict 缺失 → 上层 triaged，
    边界债 task 4.2「critic 漏吐 verdict → triaged」同源）。
    """
    verdict = state.get("_verify_verdict")
    rnd = state.get("verify_round", 1)
    if verdict == "revise" and rnd < VERIFY_MAX_ROUNDS:
        return "redo"
    return "done"


def build_verify_subgraph():
    """构建 verify 子图（compiled StateGraph）。无 Checkpointer（D2 journal 单写真源）。"""
    g = StateGraph(VerifySubState)
    g.add_node("verify", _verify_fn)
    g.add_node("redo", _redo_fn)
    g.add_edge(START, "verify")
    g.add_conditional_edges("verify", route_verify, {"redo": "redo", "done": END})
    g.add_edge("redo", "verify")                      # 回环（redo → verify round2）
    return g.compile()
