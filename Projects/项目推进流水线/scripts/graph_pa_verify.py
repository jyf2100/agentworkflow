#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""graph_pa_verify.py — verify 子图（任务 3.4 立 + 3.5c 升级为完整 dev↔verify 闭环）。

design D3 L73 + D5（撤 interrupt，升人工路径保持机械硬门）：verify 闭环（dev→独立验证→pa-verify 裁判；
判红保留分支+反馈进 PRD+增量重投；判绿 publish 接）迁为 LangGraph StateGraph 条件边回环。命令式真源是
dispatch_one L2163-2512（for-round：dev→independent_verify→pa-verify；revise→redo；pass→publish）。

子图拓扑（任务 3.5c 升级）：
    START → dev(DevLoopNode) ─(terminal=blocked/triaged)→ END
                              └─(ok)→ dev_post(Mech) ─(terminal=interrupted_pr/triaged/blocked)→ END
                                                       └─(ok)→ verify(PersonaNode pa-verify)
         verify ─(pass)→ END（publication 接）
                 ─(revise 且 verify_round<MAX)→ redo ─→ dev（round2 回环）
                 ─(用满 round2 仍 revise)→ terminal=interrupted_pr → END

dev = DevLoopNode（任务 3.5a，subprocess 调 dev-agent.py）；dev_post = MechanicalNode（任务 3.5b，
has_commits 三态 + independent_verify + green evidence）；verify = PersonaNode（expose_verdict，task 3.4）；
redo = MechanicalNode（任务 3.5c 真实：_append_verify_feedback + cur_base=branch + session retry + bump round）。
dev/dev_post terminal → END（条件边读 state.terminal 机械路由，D6）。

用满（round2 仍 revise）→ terminal=interrupted_pr（enum 终态，非 interrupt，D5 撤；对齐 L2510-2512
「对账降级 interrupted_pr，留 review」）。与 critic 子图关键差异：critic 用满→最后 verdict（上层处理）；
verify 用满→terminal=interrupted_pr（机械硬门升人工，不 drop 半成品）。

baseline 优先（D7）：redo 的 session retry 经 flag-gate（session_aware_retry off / 无 _coord → 走原增量
--base 重投，dispatch 决策零变化）；flag on → reconcile.recover_iteration 据 mode 设 session（对齐 L2476-2508）。
不用 Checkpointer（D2 journal 单写真源）；条件边路由机械（读 terminal + verdict + verify_round，不替判，D6）。
单项目维度；多项目遍历由上层 dispatch 子图（task 3.5g）。
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
    _prd_abs: str            # PRD 绝对路径（redo _append_verify_feedback 用；dispatch shell 注入）
    _project: str            # 项目 id（_ik 幂等键）
    _slug: str               # 项目 slug（label f"verify:{slug}:r{round}"，对齐 _pa_verify_round L1420）
    _prof: dict              # 单项目 profile（verify_prompt / independent_verify conda_env 用）
    _install_log: dict       # task 4.1 预留：install_log ArtifactHandle（传递通道；默认 None → byte-identical）
    # dispatch shell 注入（dev/dev_post/redo 用）
    _worktree_abs: str       # 目标仓 worktree 根（dev subprocess cwd / dev_post git -C / independent_verify repo）
    _owner_repo: str         # GitHub owner/repo（dev_post reconcile_pr 用）
    _dev_log_file: str       # dev log 路径（dev _run_capture + independent_verify test_log 派生）
    _base: str               # 初始 base（round1 = main；dev_post 写回 cur_base 喂 verify_prompt）
    _coord_flags: object     # CoordFlags（session_aware_retry/journal_driven_dispatch/single_flight_* flag 判）
    _coord: object           # Coord 对象（resolver/session_store/retry_budget；session retry flag-gate 用）
    _sj: object              # ShadowJournal（recover_iteration journal_path / _append_verify_feedback sj 用）
    _iter: str               # iteration_id（recover_iteration / _append_verify_feedback iter_id）
    _prd: str                # prd_id（recover_iteration / _append_verify_feedback prd_id）
    # dev node 写（任务 3.5a）
    _branch: str             # dev 分支（verify_prompt base..branch + redo cur_base=branch 增量重投）
    _dev_script: dict        # dev-agent JSON（test_cmd 喂 independent_verify）
    _dev_rc: int
    _dev_killed: bool
    _dev_off_track: bool
    _dev_stalled: bool
    _dev_run_log: str
    # dev_post 写（任务 3.5b）
    _verify_payload: dict    # independent_verify 产物 {test_rc, test_log, pass, evidence_ref, ...}
    _diff_path: str          # branch diff 文件（STATE_DIR/runs/<proj>/<stamp>_<slug>.r<round>.diff）
    _reconcile_status: str   # reconcile_pr 收尾 status（无产出时）
    _pr_url: str
    # redo 写（任务 3.5c）
    _cur_base: str           # round2 base = round1 dev 分支（增量重投，L2472）
    _redo_feedback: str      # redo 暂存 verify feedback（dev round2 注入用）
    _cur_resume_session: str  # session retry RESUME 模式（dev cmd --resume-session）
    _cur_fork_session: bool   # session retry FORK 模式（dev cmd --fork-session）
    _retry_mode: str          # session retry 决策 mode（观测）
    # 轮次 / 产物
    verify_round: int        # 轮次计数器（1=首轮，2=redo 后；route 判上限，D3）
    _verify_verdict: str     # pass/revise（verify node 写，route 读）
    _verify_result: dict     # verify 完整 payload（含 feedback_section 供 redo 注入 dev）
    entries: list            # 累积 verify entries（对齐 stage_dispatch rec verify 历史）
    terminal: str            # enum 终态（条件边机械路由，非空 → END）


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


def _redo_session_retry(state: dict, branch: str) -> dict:
    """session-aware retry 决策（flag-gate，对齐 dispatch_one L2476-2508）。

    baseline（session_aware_retry off / 无 _coord / 无 _sj）→ 空更新（走原增量 --base 重投，决策零变化）。
    flag on → reconcile.recover_iteration 据 mode 设 _cur_resume_session/_cur_fork_session；
    BLOCK/STOP → terminal=triaged（升人工，对齐 L2491-2495 retry_blocked/budget_exhausted）。
    fail-open：recover_iteration 异常 → 空更新（不阻断 verify 闭环，走 baseline 增量重投）。
    """
    flags = state.get("_coord_flags")
    coord = state.get("_coord")
    sj = state.get("_sj")
    if not (flags and getattr(flags, "session_aware_retry", False) and coord and sj):
        return {}
    import reconcile
    import retry_policy as RP
    try:
        rplan = reconcile.recover_iteration(
            journal_path=sj.path, run_id=state.get("run_id", ""), prd_id=state.get("_prd", ""),
            iteration_id=state.get("_iter", ""), base=branch, prd_content=state.get("_prd_content", ""),
            targets=state.get("_retry_targets", []), resolver=coord.resolver,
            session_store=coord.session_store, budget=coord.retry_budget,
            verifier_signal=RP.VerifierSignal.LOCAL_FEEDBACK)   # revise=局部反馈，history 可信 → 偏 resume
    except Exception:
        return {}                                              # fail-open（决策异常不阻断）
    mode = rplan.decision.mode
    if mode in (RP.RetryMode.BLOCK, RP.RetryMode.STOP):
        return {"terminal": C.STATUS_TRIAGED, "_retry_mode": mode.value}
    out: dict = {"_retry_mode": mode.value}
    if mode is RP.RetryMode.RESUME:
        sess = coord.session_store.load(state.get("_iter", ""))
        out["_cur_resume_session"] = getattr(sess, "session_id", None) if sess else None
    elif mode is RP.RetryMode.FORK:
        out["_cur_fork_session"] = True
    # NEW_SESSION：不设（dev cmd 默认 new session）
    if rplan.decision.consumes_retry:
        coord.retry_budget.consume(RP.BudgetDimension.SDK_RETRY)
    return out


def _redo_fn(state: dict) -> dict:
    """redo：verify revise → 注入反馈 + 设 round2 base + session retry 决策 + bump verify_round → dev(round2)。

    对齐 dispatch_one L2463-2508（判红未用满：保留分支做下次 base + 反馈追加 PRD + 增量 --base 重投）。
    """
    import run_daily
    vinfo = state.get("_verify_result") or {}
    feedback = vinfo.get("feedback_section") or vinfo.get("feedback") or ""
    round_n = state.get("verify_round", 1)
    branch = state.get("_branch")
    update: dict = {"_redo_feedback": feedback, "_cur_base": branch,   # round2 base = round1 dev 分支
                    "verify_round": round_n + 1}
    # ① _append_verify_feedback（PRD 追加反馈节，baseline；driven flag 摘 PRD 追加，对齐 L2466-2471）
    prd_abs = state.get("_prd_abs") or state.get("_prd_path")
    if prd_abs:
        flags = state.get("_coord_flags")
        driven = bool(flags and getattr(flags, "journal_driven_dispatch", False))
        try:
            run_daily._append_verify_feedback(prd_abs, feedback, round_n, driven=driven)
        except Exception:
            pass   # fail-open：反馈追加失败不阻断 verify 闭环（_append_verify_feedback shadow 契约）
    # ② session retry flag-gate（对齐 L2476-2508；flag on + _coord → recover_iteration 据 mode 设 session）
    update.update(_redo_session_retry(state, branch))
    return update


def route_dev(state: dict) -> str:
    """dev 出边（机械，D6）：terminal（blocked_test_gate/off_track/timeout/no_dev_agent）→ done；否则 dev_post。"""
    return "done" if state.get("terminal") else "dev_post"


def route_dev_post(state: dict) -> str:
    """dev_post 出边（机械，D6）：terminal（interrupted_pr/triaged/blocked_evidence/blocked_external）→ done；否则 verify。"""
    return "done" if state.get("terminal") else "verify"


def route_verify(state: dict) -> str:
    """条件边路由（机械，不替判，D6）：verdict=revise 且 verify_round<MAX → redo；否则 done。

    done 涵盖 pass（成功，publication 接）/ 用满（terminal=interrupted_pr）/ 异常（verdict 缺失 → 上层
    triaged，边界债 task 4.2「critic 漏吐 verdict → triaged」同源）。
    """
    verdict = state.get("_verify_verdict")
    rnd = state.get("verify_round", 1)
    if verdict == "revise" and rnd < VERIFY_MAX_ROUNDS:
        return "redo"
    return "done"


def route_redo(state: dict) -> str:
    """redo 出边（机械，D6）：terminal（session retry BLOCK/STOP → triaged）→ done；否则 dev（round2 回环）。"""
    return "done" if state.get("terminal") else "dev"


def build_verify_subgraph():
    """构建 verify 子图（compiled StateGraph）。无 Checkpointer（D2 journal 单写真源）。

    dev/dev_post/verify/redo 四节点 + 条件边回环。dev/dev_post terminal → END；verify revise&round<MAX → redo
    → dev（round2）；用满 → terminal=interrupted_pr → END。
    """
    g = StateGraph(VerifySubState)
    g.add_node("dev", GN.node_dev)
    g.add_node("dev_post", GN.node_dev_post)
    g.add_node("verify", _verify_fn)
    g.add_node("redo", _redo_fn)
    g.add_edge(START, "dev")
    g.add_conditional_edges("dev", route_dev, {"dev_post": "dev_post", "done": END})
    g.add_conditional_edges("dev_post", route_dev_post, {"verify": "verify", "done": END})
    g.add_conditional_edges("verify", route_verify, {"redo": "redo", "done": END})
    g.add_conditional_edges("redo", route_redo, {"dev": "dev", "done": END})   # 回环（terminal → END，否则 dev round2）
    return g.compile()
