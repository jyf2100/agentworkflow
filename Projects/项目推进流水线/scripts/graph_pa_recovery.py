#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""graph_pa_recovery.py — graph 路径崩溃恢复（task 3.8，D2 journal 单写真源·恢复侧）。

cron 在某 node 中途崩溃后重启 → recover_iteration(journal) 判 external_known + RetryPolicy.decide
→ 重建 graph initial state → graph.invoke(state) 续跑（节点幂等靠 reconcile + idempotency_key，
D3 不变式 1；spec「崩溃恢复走 journal 重建 initial state」，specs/langgraph-workflow-orchestration/spec.md:97-119）。

设计决策：
- **D1 复用 recover_iteration**（reconcile.py:272），不在 graph 侧重写 reduce（守 D6 纯函数库无更改）。
- **D2 rebuild 分层**：shell 输入（_coord/_sj/_worktree_abs/...）不可序列化不入 journal，崩溃重启时从
  上游 stage 重算（调用方传 shell_inputs + coord）；journal 只经 plan.iteration_status 重建执行进度
  + recovery dict 供节点观测/追溯（消费端接线留 task 5.2）。
- **D3 续跑 = re-invoke from START**（无 Checkpointer，D2 既定）+ _apply_session_params 把 decision.mode
  翻译成 dev session 参数（RESUME→resume session / FORK→fork / NEW_SESSION→默认 new），dev-agent 据此
  续跑而非 fresh 重做（对齐 graph_pa_verify._redo_session_retry L131-142）。admission/worktree 幂等；
  publish 靠 reconcile external_known（publication_reconcile 节点已 fail-safe 阻断）。
- **D4 _decide_action** 综合 iteration_status（TERMINAL_STATUSES）+ decision.mode → 5 态 action。
- **D5 D6 技术债不阻断**：base 显式传 recover_iteration（不依赖 task 3.7 的 payload.base=""）；用
  plan.iteration_status（不读被 node_committed duplicate 污染的 last_transition_error 诊断字段）。

前置：coord 须 journal-driven（session_aware_retry on，retry_budget/session_store/resolver 非 None）——
崩溃恢复是 journal-driven 能力，baseline coord（flag 全关）走 legacy dispatch_one 不经此路。

范围（与 task 3.7 一致）：核心能力 + dispatch 子图级别验证；生产接线（run_daily graph 入口 + 主图
graph_pa.py + recovery_cli 续跑子命令）留 task 5.2 / Phase 3。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    import reconcile


# ── ResumeAction 常量（_decide_action 输出 5 态）─────────────────────────
COMPLETED = "completed"                # iteration 已 PUBLISHED，无需续跑
TERMINAL = "terminal"                  # iteration 已其他终态（FAILED/ABORTED/STALLED/...），不续跑
MANUAL_BLOCK = "manual_block"          # external_known=False / integrity / 无效 status，需运维 reconcile
BUDGET_EXHAUSTED = "budget_exhausted"  # 预算/轮次耗尽，需运维
RESUME = "resume"                      # 续跑（decision.mode ∈ RESUME/FORK/NEW_SESSION）


# dispatch_subgraph 全部节点所需的 shell 输入字段（对齐 test_graph_dispatch_e2e._BASE）。
# 缺任一字段 → 入口 fail-closed（防崩溃恢复用残缺 state 续跑，下游 dev_post/worktree 等 KeyError；
# r-review P0：fail-safe 入口校验，不静默用空串退化）。
_REQUIRED_SHELL = (
    "run_id", "stamp", "_project", "_slug", "_base",
    "_worktree_abs", "_owner_repo", "_prd_path", "_prd_abs",
    "_prof", "_src_abs", "_dev_log_file", "verify_round",
)


class _RecoveryCoord(Protocol):
    """recover_dispatch 对 Coordinator 的消费面（Structural typing，允 SimpleNamespace mock）。

    直接注解 concrete Coordinator 会与测试 SimpleNamespace mock 冲突；Protocol 只约束消费的 7 字段，
    既给静态检查又允 mock（r-review P1）。
    """
    prd_id: str
    iteration_id: str
    flags: Any
    journal: Any
    resolver: Any | None
    session_store: Any | None
    retry_budget: Any | None


@dataclass(frozen=True)
class ResumeDecision:
    """recover_dispatch 的输出：续跑 action + 重建的 state + RecoveryPlan + reason。"""
    action: str
    state: dict
    plan: "reconcile.RecoveryPlan"
    reason: str


@dataclass(frozen=True)
class ResumeOutcome:
    """resume_dispatch 的输出（r-review P1：返值契约对称化，消除 dict key 不对称陷阱）。

    所有路径都填 action/plan/reason；action=RESUME 时 result 含 dispatch_subgraph 续跑结果。
    调用方必须 branch on action；非 RESUME 表示需运维介入，不可视为成功。
    """
    action: str
    plan: "reconcile.RecoveryPlan"
    reason: str
    result: dict | None = None


def recover_dispatch(*, journal_path: str, shell_inputs: dict, coord: _RecoveryCoord,
                     targets: list, prd_content: str) -> ResumeDecision:
    """崩溃恢复主入口：recover_iteration 判 external_known + 重建 DispatchSubState + 续跑决策。

    流程（spec scenario L108-113）：recover_iteration(journal) → rebuild state → _decide_action。
    journal 中部损坏 → JournalCorruptionError 传播（fail-closed，需运维，不自动恢复，对齐 spec）。

    Args:
        journal_path: append-only journal 路径（coord.journal.path）。
        shell_inputs: dispatch shell 输入字段（上游 stage 重算，须含 _REQUIRED_SHELL 全部键；
            形态参考 test_graph_dispatch_e2e._BASE）。缺键 → ValueError fail-closed。
        coord: Coordinator（须 journal-driven：retry_budget/session_store/resolver 非 None）。
        targets: 副作用幂等键列表（push 分支 / pr owner:branch / test digest，**调用方须构造全** ——
            空 targets 会让 reconcile 报 external_known=True 假安全，违 D3 exactly-once）。
        prd_content: PRD 正文（喂 build_recovery_context）。

    Returns:
        ResumeDecision：action ∈ {COMPLETED, TERMINAL, MANUAL_BLOCK, BUDGET_EXHAUSTED, RESUME}；
        state 是重建的 DispatchSubState；plan 是 reconcile.RecoveryPlan；reason 是人类可读理由。
    """
    if coord.session_store is None or coord.resolver is None or coord.retry_budget is None:
        raise ValueError(
            "recover_dispatch 需 journal-driven coord（session_aware_retry on，retry_budget/"
            "session_store/resolver 非 None）；baseline coord 崩溃恢复走 legacy dispatch_one。"
        )
    missing = [k for k in _REQUIRED_SHELL if k not in shell_inputs]
    if missing:
        raise ValueError(f"recover_dispatch shell_inputs 缺必填字段：{missing}（对齐 _REQUIRED_SHELL）")
    import reconcile
    plan = reconcile.recover_iteration(
        journal_path=journal_path,
        run_id=shell_inputs["run_id"],
        prd_id=coord.prd_id,
        iteration_id=coord.iteration_id,
        base=shell_inputs["_base"],
        prd_content=prd_content,
        targets=targets,
        resolver=coord.resolver,
        session_store=coord.session_store,
        budget=coord.retry_budget,
    )
    state = _rebuild_dispatch_state(shell_inputs, coord, plan)
    action, reason = _decide_action(plan)
    return ResumeDecision(action=action, state=state, plan=plan, reason=reason)


def _rebuild_dispatch_state(shell_inputs: dict, coord: _RecoveryCoord,
                            plan: "reconcile.RecoveryPlan") -> dict:
    """组装 DispatchSubState（D2）：shell 输入（上游重算）+ coord 派生 + recovery 上下文。

    shell 输入字段（_coord/_sj/_worktree_abs/...）不可序列化不入 journal —— 崩溃重启时上游 stage
    重算传入。journal 经 plan.iteration_status 重建执行进度；recovery dict 供节点观测 + 续跑追溯
    （消费端 dispatch 入口节点接线留 task 5.2）。
    """
    state = dict(shell_inputs)
    state["_coord"] = coord
    state["_coord_flags"] = coord.flags
    state["_sj"] = coord.journal
    state["_iter"] = coord.iteration_id
    state["_prd"] = coord.prd_id
    state["_journal_path"] = coord.journal.path
    state["recovery"] = {  # TODO(task 5.2): dispatch 入口节点观测消费
        "iteration_status": plan.iteration_status,
        "decision_mode": plan.decision.mode.value,
        "external_known": plan.reconciliation.external_known,
        "reason": plan.decision.reason,
    }
    return state


def _decide_action(plan: "reconcile.RecoveryPlan") -> tuple[str, str]:
    """D4：综合 iteration_status（TERMINAL_STATUSES）+ decision.mode → (ResumeAction, reason)。

    PUBLISHED → completed；其他终态 → terminal；非终态 BLOCK→manual_block / STOP→budget_exhausted /
    RESUME|FORK|NEW_SESSION → resume。**无效 iteration_status（非 enum 值，暗示 reducer/journal 损坏）
    → MANUAL_BLOCK fail-closed**（r-review P0：不静默续跑脏 status，需运维 triage）。
    """
    import loop_state as L
    import retry_policy as RP
    try:
        status = L.IterationStatus(plan.iteration_status)
    except ValueError:
        return (MANUAL_BLOCK,
                f"invalid iteration_status from reducer: {plan.iteration_status!r}; operator triage")
    if status in L.TERMINAL_STATUSES:
        if status is L.IterationStatus.PUBLISHED:
            return (COMPLETED, "iteration 已发布终态（published），无需续跑")
        return (TERMINAL, f"iteration 已终态（{status.value}），不续跑")
    mode = plan.decision.mode
    if mode is RP.RetryMode.BLOCK:
        return (MANUAL_BLOCK, plan.decision.reason or "external 真源未知或 integrity 阻断，需运维 reconcile")
    if mode is RP.RetryMode.STOP:
        return (BUDGET_EXHAUSTED, plan.decision.reason or "预算/轮次耗尽")
    return (RESUME, plan.decision.reason or f"续跑（mode={mode.value}）")


def _apply_session_params(state: dict, plan: "reconcile.RecoveryPlan",
                          coord: _RecoveryCoord) -> dict:
    """resume 时据 decision.mode 设 dev session 参数（对齐 _redo_session_retry L131-142，D3）。

    **返新 dict，不 mutate 入参**（r-review P0：守 immutability + 不穿透 ResumeDecision frozen）。
    RESUME → _cur_resume_session（session_store.load().session_id；**二次 load 若 None 或无 session_id
    → RuntimeError fail-closed**，r-review P0：retry_policy.decide 时 session 非 None，apply 时 None =
    TOCTOU/IO 损坏，不静默降级 NEW_SESSION 丢 dev 进度）；FORK → _cur_fork_session=True；
    NEW_SESSION → 不设（dev cmd 默认 new session）。consumes_retry → coord.retry_budget.consume。
    """
    import retry_policy as RP
    out = dict(state)  # shallow copy，不污染入参（immutability）
    out["_retry_mode"] = plan.decision.mode.value
    mode = plan.decision.mode
    if mode is RP.RetryMode.RESUME:
        sess = coord.session_store.load(coord.iteration_id)
        sid = sess.session_id if sess else None
        if not sid:
            # decide 时 session 非 None（retry_policy L177），apply 时 None → TOCTOU/IO 损坏；
            # 静默降级 NEW_SESSION 会丢 dev 进度，显式 fail-closed 需运维 triage。
            raise RuntimeError(
                f"RESUME decided but session_store.load({coord.iteration_id!r}) returned "
                f"{sess!r} (no session_id); likely TOCTOU/IO corruption; operator triage before retry")
        out["_cur_resume_session"] = sid
    elif mode is RP.RetryMode.FORK:
        out["_cur_fork_session"] = True
    # NEW_SESSION：不设（dev cmd 默认 new session）
    if plan.decision.consumes_retry:
        # ⚠️ 已知限制（r-review P2 follow-up，独立 spec change）：BudgetState frozen，consume 返新实例，
        # 但 coord.retry_budget 是 frozen Coordinator 字段，此处调用结果未回写 → sdk_retries_used 不递增。
        # 同模式见 graph_pa_verify._redo_session_retry L141-142 + run_daily L2508（跨 3 模块既有）。
        # 根治需 Coordinator 持 mutable budget holder；本期标记，独立 change 修 + 改 3 处测试 mock。
        coord.retry_budget.consume(RP.BudgetDimension.SDK_RETRY)
    return out


def resume_dispatch(*, journal_path: str, shell_inputs: dict, coord: _RecoveryCoord,
                    targets: list, prd_content: str) -> ResumeOutcome:
    """崩溃恢复端到端：recover_dispatch → 据 action 续跑或返信号。

    completed/terminal/manual_block/budget_exhausted → 不 invoke，返 ResumeOutcome（result=None）。
    resume → _apply_session_params + build_dispatch_subgraph().invoke(state)（D3 re-invoke from START）。

    Returns:
        ResumeOutcome：所有路径填 action/plan/reason；action=RESUME 时 result 含 dispatch_subgraph
        续跑结果。调用方必须 branch on action；非 RESUME 表示需运维介入，不可视为成功。
    """
    import graph_pa_dispatch as GD
    decision = recover_dispatch(journal_path=journal_path, shell_inputs=shell_inputs,
                                coord=coord, targets=targets, prd_content=prd_content)
    if decision.action != RESUME:
        return ResumeOutcome(action=decision.action, plan=decision.plan, reason=decision.reason)
    state = _apply_session_params(decision.state, decision.plan, coord)
    result = GD.build_dispatch_subgraph().invoke(state)
    return ResumeOutcome(action=RESUME, plan=decision.plan,
                         reason=decision.reason, result=result)
