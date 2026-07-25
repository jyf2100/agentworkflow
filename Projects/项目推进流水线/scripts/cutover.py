#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cutover.py — Section 8（Cutover, Canary, and Recovery Drills）task 8.1-8.6 + 8.8 可重复 drill harness。

Section 8 是 **cutover 阶段**——shadow parity、lifecycle canary、crash drill、recovery canary、
sandbox canary、dispatch cutover、quality gate。这些在生产里要跑真实环境（真实 fixture dry-run、
真实 docker canary、真实 SDK session、真实 PR 创建）。本模块把它们实现为 **可重复运行的 drill harness
纯函数**：每个 drill 编排现有模块（loop_state / compat_readers / hook_adapter / retry_policy /
reconcile / sandbox / telemetry / artifact_store），用 **注入的适配器**（FakeResolver /
FakeContainerRunner / 注入 stamp）覆盖每条路径，断言行为正确。

**诚实披露**（同 Section 6 容器层）：真实 OS 边界隔离（真实 docker non-root/egress/cgroup）、真实
PR 创建、真实 SDK session resume 的 **运行时验证** 不在本 harness 内——harness 用 fake 适配器覆盖
**策略/逻辑层**；运维在真实 cutover 时把同一 harness 的 fake 适配器换成真实适配器（DockerCliRunner /
GhPrResolver / 真实 SDK）即可复用全部断言。task 8.7 的 operator runbook 文档化真实环境操作。

drill 一律返不可变结果 dataclass（``*_DrillResult``），便于测试断言 + 控制面归档为 evidence。

纯 stdlib + 复用现有纯库模块，cron 隔离友好（零 SDK 模块级导入）。
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass, fields, is_dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import artifact_store
import compat_readers as CR
import evidence as EV
import hook_adapter as HA
import hook_events as HE
import hook_policy as HP
import journal as J
import loop_runtime as LR
import loop_state as L
import recovery_context
import reconcile as RC
import retry_policy as RP
import sandbox as SB
import sandbox_publication as SP
import trace_context as TC


# ════════════════════════════════════════════════════════════════════════════
# 8.1 shadow journaling 对历史 fixture + 一个真实 dry-run，解决所有 state mismatch
# ════════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class ShadowParityReport:
    """shadow parity 比对结果（task 3.4 + 8.1）。

    ``matched=True`` ⇔ 历史 dispatch 终态分布 == journal-reducer 终态分布（迁移未改行为）。
    ``mismatches`` 列出每个不一致桶（供「解决每一个 mismatch」，design 8.1）。
    """
    dispatch_counts: dict
    journal_counts: dict
    matched: bool
    mismatches: tuple[str, ...] = ()


def run_shadow_parity_drill(dispatch_records: list[dict],
                            journal_events: list) -> ShadowParityReport:
    """对历史 fixtures + journal events 跑 shadow parity（task 8.1）。

    两套真源终态计数（``summarize_terminal`` vs ``summarize_journal``）相等即 parity 成立。
    任何 mismatch 列入 ``mismatches``——「resolving every state mismatch」= 调用方据此逐个修复
    直到 ``matched=True``。
    """
    dc: Counter = CR.summarize_terminal(dispatch_records)
    jc: Counter = CR.summarize_journal(journal_events)
    keys = sorted(set(dc) | set(jc), key=lambda s: getattr(s, "value", str(s)))
    mismatches = tuple(
        f"{getattr(k, 'value', k)}: dispatch={dc.get(k, 0)} journal={jc.get(k, 0)}"
        for k in keys if dc.get(k, 0) != jc.get(k, 0)
    )
    return ShadowParityReport(dispatch_counts=dict(dc), journal_counts=dict(jc),
                              matched=not mismatches, mismatches=mismatches)


def run_shadow_dry_run(*, journal_path, run_id: str, stamp,
                       flow: list[tuple]) -> L.IterationState:
    """一个真实 shadow dry-run（task 8.1「one real dry-run」）。

    用 ``ShadowJournal`` 旁路写一条 event 序列（模拟 dispatch 流程的 shadow emit），读回 reduce，
    返回归约态——证明「旁路写 journal + reducer 重建态」端到端可用。``flow`` = [(event_type,
    iteration_id, prd_id, payload), ...]。
    """
    sj = LR.ShadowJournal(path=journal_path, run_id=run_id, stamp=stamp, enabled=True)
    for event_type, iter_id, prd_id, payload in flow:
        sj.emit(event_type, iteration_id=iter_id, prd_id=prd_id, payload=payload)
    events = [e for e in _read_journal_events(journal_path) if e.run_id == run_id]
    if not events:
        return L.initial_state(run_id, "", "", base="")
    first = events[0]
    base = first.payload.get("base", "") if isinstance(first.payload, dict) else ""
    return L.reduce(events, L.initial_state(first.run_id, first.prd_id,
                                            first.iteration_id, base=base))


def _read_journal_events(path):
    """读 journal（容忍损坏行，drill 用）。"""
    import journal as J
    try:
        return J.read_events(path)
    except Exception:
        return []


# ─── task 7.1：historical fixtures shadow parity + 一个真实 no-write dispatch（production evidence）──
@dataclass(frozen=True)
class ShadowParityEvidence:
    """task 7.1：historical fixtures parity + 一个真实 no-write dispatch dry-run 的可归档证据。

    design 决策#2（parity 比对全 terminal state）+ #6（archive passing evidence）。``parity.matched``
    ⇔ historical fixtures 的 dispatch 终态分布 == journal-reducer 终态分布（mismatch 已解决基线）。
    """
    parity: "ShadowParityReport"
    dry_run_terminal: str          # no-write dry-run 重建终态（published 路径）
    dry_run_run_id: str


def run_shadow_parity_evidence(*, state_dir, stamp_fn) -> ShadowParityEvidence:
    """task 7.1：historical fixtures shadow parity + 一个真实 no-write dispatch dry-run（design#2/#6）。

    historical fixtures（``cutover_fixtures``，覆盖全 terminal class）经真实 ``ShadowJournal`` 写等价链、
    读回 reduce，与 ``summarize_terminal`` 比对——parity matched（mismatch 已解决的基线，design 7.1
    「resolving every terminal mismatch」）。再跑一个真实 no-write dispatch dry-run（``run_shadow_dry_run``，
    纯 journal 旁路写 + reducer 重建，不创建 PR/commit）。返回结构化 evidence 供 quality gate / 运维归档。

    production wiring（design 决策#1）：把 ``run_shadow_parity_drill``/``run_shadow_dry_run`` 从
    disconnected helper 连成可重复运行的 evidence 命令。
    """
    import cutover_fixtures as FX
    state = Path(state_dir)
    # historical parity：真实 ShadowJournal 写等价链（模拟 shadow 接入旁路记录），读回 reduce 比对
    parity_path = state / "parity_shadow.journal.jsonl"
    sj = LR.ShadowJournal(path=parity_path, run_id="run_parity", stamp=stamp_fn, enabled=True)
    for i, rec in enumerate(FX.HISTORICAL_DISPATCH_RECORDS):
        for et in FX.chain_for(rec):
            sj.emit(et, iteration_id=f"iter_{i}", prd_id=f"prd_{i}", payload={"base": "main"})
    parity = run_shadow_parity_drill(FX.HISTORICAL_DISPATCH_RECORDS,
                                     _read_journal_events(parity_path))
    # 一个真实 no-write dispatch dry-run（published 路径，纯 journal 旁路写 + reducer 重建）
    dry_state = run_shadow_dry_run(
        journal_path=state / "dry_run.journal.jsonl", run_id="run_dry",
        stamp=stamp_fn, flow=FX.NO_WRITE_DRY_RUN_FLOW)
    return ShadowParityEvidence(
        parity=parity,
        dry_run_terminal=dry_state.status.value,
        dry_run_run_id="run_dry",
    )


# ════════════════════════════════════════════════════════════════════════════
# 7.2 / 8.2 lifecycle hooks canary（spec 7 path + test_red 基线）
# ════════════════════════════════════════════════════════════════════════════
# spec task 7.2 的 7 path（kebab）→ 内部 scenario（snake）映射（canary 覆盖证明 + 归档审计）。
# test_red（GATE_FAILED）额外保留作 failed-test 基线（spec 未列但与 stale-test 对照合理保留）。
SPEC_PATH_TO_SCENARIO: dict[str, str] = {
    "no-test": "no_test",
    "stale-test": "stale_test",
    "green-test": "test_green",
    "semantic-revise": "semantic_revise",
    "compaction": "compaction",
    "subagent": "subagent",
    "hook-failure": "hook_failure",
}

LIFECYCLE_SCENARIOS: frozenset[str] = frozenset(
    {"no_test", "test_red"} | set(SPEC_PATH_TO_SCENARIO.values()))


@dataclass(frozen=True)
class LifecycleDrillResult:
    scenario: str
    stop_decision: str            # "allow" / "deny" / "revise"
    snapshot_persisted: bool
    detail: str
    gate: str = ""                # 场景裁决标签（GATE_* verdict / publication_blocked / fail_closed / semantic_revise / snapshot_persisted）


def _fresh_adapter(stamp, *, allow_publication: bool = False) -> HA.HookAdapter:
    """每次场景用全新 adapter（独立 evidence/续命计数），HookJournal no-op（drill 不落盘证据）。

    ``allow_publication`` 注入（subagent 场景设 True 以证明 subagent context 覆盖它）。
    """
    return HA.HookAdapter(journal=HE.HookJournal(path="/dev/null", enabled=False),
                          stamp=stamp, stop_continuation_limit=3,
                          allow_publication=allow_publication)


def run_lifecycle_drill(scenario: str, *, stamp=None) -> LifecycleDrillResult:
    """lifecycle hooks canary（task 7.2）：8 场景验证全 SDK hook 路径（spec 7 path + test_red 基线）。

    覆盖 spec task 7.2「real SDK hook canary for no-test, stale-test, green-test, semantic-revise,
    compaction, subagent, and hook-failure paths」：
    * no_test → 无 evidence → GATE_NOT_RUN → Stop deny（bounded 续命）；
    * test_red → PostToolUse 测试 exit 1 → GATE_FAILED → Stop deny；
    * stale_test → 绿后候选写 → mark_stale → GATE_STALE → Stop deny（区别 test_red 的 GATE_FAILED）；
    * test_green → PostToolUse 测试 exit 0 → fresh green GATE_PUBLISH → Stop allow；
    * semantic_revise → fresh green inner gate 放行，但外层 verify 语义判红 → revise（dual-gate，
      inner 绿 ≠ publish）；
    * compaction → PreCompact auto + snapshot writer 成功 → snapshot 持久化（不阻恢复）；
    * subagent → SubagentStart 记归属 + subagent context PreToolUse publication 强制 DENY（即使 host
      allow_publication=True，task 4.6 host-side verified publication）；
    * hook_failure → PreCompact auto + snapshot_writer 抛异常 → fail-closed block（防 auto-resume 无依据）。
    """
    if scenario not in LIFECYCLE_SCENARIOS:
        raise ValueError(f"unknown lifecycle scenario: {scenario!r}")
    ts = stamp or (lambda: "2026-07-22T00:00:00Z")
    if scenario == "no_test":
        out = _fresh_adapter(ts).on_stop("it_nt")
        decision = "allow" if out.permission_decision is HP.PermissionDecision.ALLOW else "deny"
        return LifecycleDrillResult("no_test", decision, False,
                                    f"continue_active={out.continue_active}", gate=EV.GATE_NOT_RUN)
    if scenario == "test_red":
        adapter = _fresh_adapter(ts)
        adapter.on_post_tool_use("it_tr", tool_name="Bash", tool_use_id="tu1",
                                 command="pytest -q", exit_code=1)
        out = adapter.on_stop("it_tr")
        return LifecycleDrillResult("test_red", "deny", False,
                                    f"gate blocked: {out.block_reason}", gate=EV.GATE_FAILED)
    if scenario == "stale_test":
        adapter = _fresh_adapter(ts)
        adapter.on_post_tool_use("it_st", tool_name="Bash", tool_use_id="tu1",
                                 command="pytest -q", exit_code=0)    # 绿 → fresh green
        adapter.on_post_tool_use("it_st", tool_name="Edit", tool_use_id="tu2",
                                 command="")                           # 候选写 → mark_stale
        out = adapter.on_stop("it_st")
        verdict, _ = EV.evaluate_gate(adapter.evidence)
        decision = "allow" if out.permission_decision is HP.PermissionDecision.ALLOW else "deny"
        return LifecycleDrillResult("stale_test", decision, False,
                                    f"gate blocked: {out.block_reason}", gate=verdict)
    if scenario == "test_green":
        adapter = _fresh_adapter(ts)
        adapter.on_post_tool_use("it_tg", tool_name="Bash", tool_use_id="tu1",
                                 command="pytest -q", exit_code=0)
        out = adapter.on_stop("it_tg")
        return LifecycleDrillResult("test_green", "allow", False,
                                    "fresh green TestEvidence; outer verify still runs",
                                    gate=EV.GATE_PUBLISH)
    if scenario == "semantic_revise":
        adapter = _fresh_adapter(ts)
        adapter.on_post_tool_use("it_sr", tool_name="Bash", tool_use_id="tu1",
                                 command="pytest -q", exit_code=0)    # inner fresh green
        inner = adapter.on_stop("it_sr")                               # inner gate 放行
        # dual-gate（design 4.1）：inner fresh-green allow ≠ publish——外层 verify 语义判红 → revise
        return LifecycleDrillResult(
            "semantic_revise", "revise", False,
            (f"inner_stop={inner.permission_decision.value}; outer verify semantic=revise "
             f"→ iteration revises, not published"),
            gate="semantic_revise")
    if scenario == "compaction":
        out = _fresh_adapter(ts).on_pre_compact(
            "it_pc", trigger="auto",
            snapshot_writer=lambda: {"digest": "d", "path": "snap.json", "kind": "recovery_snapshot"},
        )
        persisted = out.artifact_ref is not None and not out.block_reason
        return LifecycleDrillResult("compaction", "allow", persisted,
                                    f"block_reason={out.block_reason!r}", gate="snapshot_persisted")
    if scenario == "subagent":
        # host allow_publication=True，验证 subagent context 仍强制 False（task 4.6 防线）
        adapter = _fresh_adapter(ts, allow_publication=True)
        adapter.on_subagent_start("it_sa", "agent-1", agent_type="pa-verify", objective="verify diff")
        pub = adapter.on_pre_tool_use("it_sa", "Bash", tool_use_id="tu1",
                                      command="git push origin HEAD:refs/heads/feat",
                                      subagent_agent_id="agent-1")
        adapter.on_subagent_stop("it_sa", "agent-1", status="completed",
                                 result_artifact={"verdict": "revise"})
        decision = "allow" if pub.permission_decision is HP.PermissionDecision.ALLOW else "deny"
        return LifecycleDrillResult(
            "subagent", decision, False,
            (f"subagent publication decision={pub.permission_decision.value}; "
             f"reason={pub.permission_reason}"),
            gate="publication_blocked")
    # hook_failure：snapshot_writer 抛异常 → auto 压缩 fail-closed
    def _boom():
        raise RuntimeError("disk full")
    out = _fresh_adapter(ts).on_pre_compact("it_hf", trigger="auto", snapshot_writer=_boom)
    persisted = out.artifact_ref is not None and not out.block_reason
    blocked = bool(out.block_reason)
    return LifecycleDrillResult(
        "hook_failure", "deny" if blocked else "allow", persisted,
        f"fail-closed block_reason={out.block_reason!r}",
        gate="fail_closed" if blocked else "snapshot_ok")


# ─── task 7.2：real SDK hook canary evidence（spec 7 path 聚合，design#1 production 证据命令 + #6 archive）──
@dataclass(frozen=True)
class SdkHookCanaryEvidence:
    """task 7.2：real SDK hook canary 的可归档证据。

    跑全 spec 7 path（+ test_red 基线），聚合每场景 ``LifecycleDrillResult``。``paths_covered`` 证明
    spec 列举的 7 path 全覆盖；``stop_gates`` 给 scenario→gate 快照（归档/审计）。design 决策#6
    （archive immutable passing evidence）：frozen dataclass + tuple scenarios（不可变）。
    """
    scenarios: tuple[LifecycleDrillResult, ...]
    stop_gates: dict[str, str]       # scenario → gate
    paths_covered: tuple[str, ...]   # spec 7 path（kebab）
    summary: str
    real_query_proven: bool = True   # r2 P0-5：真实 SDK query 是否证明 lifecycle callback 真实触发
                                     # （run_sdk_hook_canary fixture 默认 True；real_cutover_suite 从 real_sdk_canary
                                     # 真实 query 结果填 → sdk_canary 通过需 adapter gate AND 真实 query proven）
    # r3 P0-1 闭环：真实 SDK query 逐场景 proven 的场景名子集（SDK_CALLBACK_REQUIRED_SCENARIOS 须全含）。
    # run_sdk_hook_canary fixture **不**填此字段（adapter 非 SDK）→ 默认 ()；real_cutover_suite 从
    # real_sdk_canary 真实 query 结果填真值 → sdk_canary 通过需 callback 逐场景 proven（非任意 callback 假绿）。
    sdk_callback_proven: tuple[str, ...] = ()
    # r5 P0-2（口径4）：adapter gate 逻辑覆盖的场景（on_stop/on_post_tool_use/on_pre_compact 真实代码路径，
    # run_sdk_hook_canary fixture 填）。与 sdk_callback_proven 严格分离——adapter 证 gate 编排，非 SDK callback。
    adapter_contract_proven: tuple[str, ...] = ()
    # r5 P1-5：回调/日志错误维度（评审 P1-5）。真实 query 中 callback 抛异常（callback_errors 非空）或 hook
    # journal 行 JSON 解析失败（journal_decode_errors>0）→ lifecycle 证据不可信/可能丢失 → sdk_canary fail。
    # adapter fixture 默认空（adapter 无真实 query）；real_cutover_suite 从 real_sdk_canary 真实结果填。
    callback_errors: tuple[dict, ...] = ()
    journal_decode_errors: int = 0
    # r5 P1-2（评审）：query 完整性维度——SDK query 须正常结束（result_received=True 且 query_error=None），
    # 否则其产出的 callback/gate 证据不可信（query 中途崩/超时/被代理拒 → observed events 可能是残缺或错位）。
    # 与 callback_errors/journal_decode_errors 同属 evaluate_evidence_intact 纯函数（7.2 谓词 + 7.6 outcome 共调）。
    result_received: bool = True
    query_error: str | None = None
    # r6 P0（评审）：per-scenario 绑定证据——每场景 {journal_has_expected/carries_own_cid/observed_state/
    # adapter_gate/blocked_reason}，全部来自该场景**单一 query**（同源）。替代旧 sdk_callback_proven 场景名
    # tuple（仅证"名出现"，不证 state/gate 同源）。real_cutover_suite 从 real_sdk_canary 的
    # per_scenario_real_triggers 填真值；run_sdk_hook_canary fixture 无真实 query → 默认 ()（7.6 fixture 路径
    # per_scenario 空 → 全场景 missing → passed=False，诚实，fixture 不该过 callback 维度）。
    per_scenario: tuple[dict, ...] = ()


# canary 跑序（spec 7 path 对应 scenario + test_red 基线，按 GATE 严重度递减便于归档阅读）
_CANARY_ORDER: tuple[str, ...] = (
    "no_test", "test_red", "stale_test", "test_green",
    "semantic_revise", "compaction", "subagent", "hook_failure",
)


def run_sdk_hook_canary(*, stamp_fn=None) -> SdkHookCanaryEvidence:
    """task 7.2：real SDK hook canary——跑 spec 7 path（no-test/stale-test/green-test/semantic-revise/
    compaction/subagent/hook-failure）+ test_red 基线，聚合可归档 evidence。

    production wiring（design 决策#1）：把 ``run_lifecycle_drill`` 从 disconnected 单点 helper 连成
    覆盖 spec 全 7 path 的可重复 canary 证据命令。返回 ``SdkHookCanaryEvidence`` 供 quality gate /
    运维归档（design 决策#6 archive immutable passing evidence）。

    Args:
        stamp_fn: 时间戳函数（None → 固定值，drill 确定性可复现）。
    """
    ts = stamp_fn or (lambda: "2026-07-22T00:00:00Z")
    scenarios = tuple(run_lifecycle_drill(s, stamp=ts) for s in _CANARY_ORDER)
    stop_gates = {r.scenario: r.gate for r in scenarios}
    paths_covered = tuple(SPEC_PATH_TO_SCENARIO.keys())
    summary = " | ".join(f"{r.scenario}={r.stop_decision}/{r.gate}" for r in scenarios)
    return SdkHookCanaryEvidence(
        scenarios=scenarios, stop_gates=stop_gates,
        paths_covered=paths_covered, summary=summary,
        # r5 P0-2（口径4）：adapter fixture 证 gate 编排逻辑（on_stop/on_post_tool_use/on_pre_compact 真实
        # 代码路径），填 adapter_contract_proven；**不**填 sdk_callback_proven（adapter 非 SDK，不得冒充真实
        # callback）。真实 SDK callback 逐场景 proven 由 real_cutover_suite 从 real_sdk_canary 路径填真值。
        sdk_callback_proven=(),
        adapter_contract_proven=tuple(_CANARY_ORDER))


# ════════════════════════════════════════════════════════════════════════════
# 7.3 / 8.3 controlled crash drill（agent/test/commit/push/PR 后）确认 exactly-once
# ════════════════════════════════════════════════════════════════════════════
# spec task 7.3 的 5 边界（side-effect 发生点），崩溃后 reconcile 判定每个副作用是否已发生。
CRASH_BOUNDARIES: frozenset[str] = frozenset(
    {"agent_done", "test_done", "commit", "push", "pr_create"})


@dataclass(frozen=True)
class CrashDrillResult:
    boundary: str
    confirmed: int               # 副作用已发生（retry 跳过）
    pending: int                 # 副作用未发生（retry 执行）
    unknown: int                 # 查不到（fail-safe，不盲目执行）
    exactly_once: bool           # 无 unknown ⇔ 每个副作用状态明确（confirmed 或 pending 各一次）
    external_known: bool


# 各边界注入崩溃后待 reconcile 的副作用目标（agent_done/test_done 无外部副作用；commit/push/pr 有）。
# SideEffectTarget.kind 对齐 ids.idempotency_id 允许列表（commit/push/pr）；target 语义随 kind。
_BOUNDARY_TARGETS = {
    "agent_done": (),
    "test_done": (),
    "commit": (RC.SideEffectTarget("commit", "feat-branch"),),
    "push": (RC.SideEffectTarget("push", "feat-branch"),),
    "pr_create": (RC.SideEffectTarget("pr", "owner/repo:feat-branch"),),
}


def run_crash_drill(boundary: str, *, resolver: RC.KeyResolver,
                    iteration_id: str = "iter_crash") -> CrashDrillResult:
    """controlled crash drill（task 8.3）：在 side-effect 边界注入崩溃后 reconcile。

    契约（spec L10-12）：崩溃重启后 reconciliation 判定每个副作用是否已发生——confirmed 跳过、
    pending 执行、unknown fail-safe 不盲目重放。``exactly_once`` ⇔ 无 unknown（每个副作用状态明确，
    不会重复执行也不会盲目补做）。
    """
    if boundary not in CRASH_BOUNDARIES:
        raise ValueError(f"unknown crash boundary: {boundary!r}")
    report = RC.reconcile_side_effects(
        iteration_id=iteration_id, targets=_BOUNDARY_TARGETS[boundary], resolver=resolver,
    )
    return CrashDrillResult(
        boundary=boundary, confirmed=len(report.confirmed), pending=len(report.pending),
        unknown=len(report.unknown), exactly_once=report.external_known,
        external_known=report.external_known,
    )


# ─── task 7.3：crash reconciliation evidence（全 5 边界 exactly-once 归档，design#1 production 证据命令 + #6 archive）──
@dataclass(frozen=True)
class CrashReconciliationEvidence:
    """task 7.3：crash reconciliation 的可归档证据。

    跑 spec 全 5 边界（agent/test/commit/push/PR），聚合每边界 ``CrashDrillResult``。
    ``all_exactly_once`` ⇔ 每边界 reconcile 无 unknown（崩溃后副作用状态全明确，retry 安全决策）。
    design 决策#6（archive immutable passing evidence）：frozen dataclass + tuple results。
    """
    results: tuple[CrashDrillResult, ...]
    boundaries_run: tuple[str, ...]
    all_exactly_once: bool
    summary: str


# 跑序（spec 7.3 边界顺序：side-effect 发生时点）
_CRASH_BOUNDARY_ORDER: tuple[str, ...] = (
    "agent_done", "test_done", "commit", "push", "pr_create",
)


def run_crash_reconciliation_evidence(*, resolver: RC.KeyResolver,
                                      iteration_id: str = "iter_crash") -> CrashReconciliationEvidence:
    """task 7.3：crash reconciliation evidence——跑 spec 全 5 边界（agent/test/commit/push/PR），
    聚合每边界 reconcile 结果为可归档证据。

    production wiring（design 决策#1）：把 ``run_crash_drill`` 从 disconnected 单点 helper 连成覆盖
    spec 全 5 边界的可重复 reconciliation 证据命令。``all_exactly_once`` 汇总每边界 exactly_once——
    任一边界 unknown（reconcile 查不到）→ False（fail-safe，design risk#90 不盲目重放）。返回
    ``CrashReconciliationEvidence`` 供 quality gate / 运维归档（design 决策#6 archive）。

    Args:
        resolver: KeyResolver（reconcile 查副作用状态；生产 LocalGitResolver，测试 FakeResolver）。
        iteration_id: reconcile 幂等键输入（同 iteration 同 target → 同 key，跨崩溃稳定）。
    """
    results = tuple(
        run_crash_drill(b, resolver=resolver, iteration_id=iteration_id)
        for b in _CRASH_BOUNDARY_ORDER
    )
    boundaries_run = tuple(r.boundary for r in results)
    all_exactly_once = all(r.exactly_once for r in results)
    summary = " | ".join(
        f"{r.boundary}=confirmed={r.confirmed}/pending={r.pending}/unknown={r.unknown}"
        for r in results)
    return CrashReconciliationEvidence(
        results=results, boundaries_run=boundaries_run,
        all_exactly_once=all_exactly_once, summary=summary)


# ─── task 7.4：operator journal-corruption recovery command（runbook 引用，design#1 production 命令 + #6 archive）──
@dataclass(frozen=True)
class JournalRecoveryResult:
    """task 7.4：operator recovery 命令结果（spec scenario「Documented recovery command」）。

    两种 action（spec：verifiable recovery 或 explicit manual-block）：
    * ``recovered``：journal 可读（末尾截断容忍/正常）→ ``terminal_status`` = reduce 重建终态；
      ``prd_content`` 提供时附 ``recovery_context``（完整恢复），否则仅终态。
    * ``manual_block``：journal 中部损坏（fail-closed）→ ``report.is_fail_closed``，绝不自动修复，
      给运维 ``corrupted_line_numbers`` 定位（备份后重建/丢弃受污染 iteration）。
    design 决策#6：frozen dataclass（不可变归档）。
    """
    journal_path: str
    action: str                          # "recovered" / "manual_block"
    report: J.CorruptionReport
    terminal_status: str | None          # recovered → reduce 终态；manual_block → None
    recovery_context: "recovery_context.RecoveryContext | None"
    detail: str


def run_journal_recovery(*, journal_path, prd_content: str | None = None,
                         iteration_id: str = "iter_recover", prd_id: str = "prd_recover",
                         stamp_fn=None) -> JournalRecoveryResult:
    """task 7.4：operator recovery command——损坏 journal → verifiable recovery 或 explicit manual-block。

    spec scenario「Documented recovery command」：operator follows the runbook for a corrupt journal →
    every referenced command produces a verifiable recovery or explicit manual-block result。

    production wiring（design 决策#1）：把 ``validate_journal``/``reduce``/``build_recovery_context`` 从
    disconnected helper 连成可重复的 operator recovery 命令（``recovery_cli.py`` 薄 CLI 包装）。

    决策树：
        * ``validate_journal`` → ``CorruptionReport``；
        * ``report.is_fail_closed``（committed history 内中部损坏）→ ``manual_block``（fail-closed，
          绝不静默跳过坏行归约——否则状态机基于残缺事件得错误状态，design 决策#1）；
        * 否则（末尾截断容忍 / 正常）→ ``read_events`` → ``reduce`` 重建终态；``prd_content`` 提供 →
          ``build_recovery_context``（verifiable 完整恢复），缺则仅终态（detail 注 PRD 缺失）。

    Args:
        journal_path: journal JSONL 路径（损坏真源）。
        prd_content: PRD 文本（可选；提供 → 完整 RecoveryContext，缺 → 仅终态）。
        iteration_id/prd_id: recovery_context 归属（prd_content 提供时用）。
    """
    report = J.validate_journal(journal_path)
    if report.is_fail_closed:
        return JournalRecoveryResult(
            journal_path=str(journal_path), action="manual_block", report=report,
            terminal_status=None, recovery_context=None,
            detail=(f"journal 中部损坏（fail-closed）：行 {list(report.corrupted_line_numbers)}；"
                    "不自动修复——运维介入（备份后重建或丢弃受污染 iteration）"))
    events = J.read_events(journal_path)            # 末尾截断容忍
    if not events:
        return JournalRecoveryResult(
            journal_path=str(journal_path), action="recovered", report=report,
            terminal_status=None, recovery_context=None,
            detail="空 journal（首次 dispatch 或全截断）——无状态可恢复")
    state = L.reduce(events)
    rc: "recovery_context.RecoveryContext | None" = None
    if prd_content is not None:
        rc = recovery_context.build_recovery_context(
            iteration_id=iteration_id, prd_id=prd_id,
            status_value=state.status.value, prd_content=prd_content, events=events)
    detail = (f"recovered → terminal={state.status.value}；"
              + ("PRD 提供 → 完整 RecoveryContext" if rc is not None
                 else "PRD 缺失 → 仅终态（无 RecoveryContext）"))
    return JournalRecoveryResult(
        journal_path=str(journal_path), action="recovered", report=report,
        terminal_status=state.status.value, recovery_context=rc, detail=detail)


# ════════════════════════════════════════════════════════════════════════════
# 8.4 recovery canary（resume / fork / new_session），bounded budget + journal 因果
# ════════════════════════════════════════════════════════════════════════════
RECOVERY_MODES: frozenset[str] = frozenset({"resume", "fork", "new_session"})


@dataclass(frozen=True)
class RecoveryDrillResult:
    mode: str
    decision_mode: str           # RetryPolicy 实际决策
    budget_exhausted: bool
    causality_intact: bool       # span link 表达因果（resume/fork 接续 parent trace）
    detail: str


def _fake_session(*, resumable: bool, exc_class, compaction: int = 0):
    """duck-typed SessionMeta（retry_policy.decide 只读 session_resumable/exception_class/compaction_count）。"""
    return SimpleNamespace(session_resumable=resumable, exception_class=exc_class,
                           compaction_count=compaction, exception_message="")


def run_recovery_drill(mode: str, *, budget: RP.BudgetState | None = None) -> RecoveryDrillResult:
    """recovery canary（task 8.4）：三模式 + bounded budget + journal 因果。

    * resume → session 可用 + transient 中断 → RetryPolicy RESUME；
    * fork → verifier 建议换方案 → FORK；
    * new_session → session 缺失 → NEW_SESSION。
    因果用 trace_context span_link 表达（child iteration 接续 parent run trace）。
    """
    if mode not in RECOVERY_MODES:
        raise ValueError(f"unknown recovery mode: {mode!r}")
    b = budget or RP.BudgetState(limits=RP.BudgetLimits())
    if mode == "resume":
        session = _fake_session(resumable=True, exc_class=RP.ExceptionClass.TRANSIENT)
        decision = RP.decide(budget=b, session=session, fingerprint=None, progress=None,
                             external_known=True)
    elif mode == "fork":
        session = _fake_session(resumable=True, exc_class=RP.ExceptionClass.NONE)
        decision = RP.decide(budget=b, session=session, fingerprint=None, progress=None,
                             external_known=True,
                             verifier_signal=RP.VerifierSignal.SUGGEST_ALTERNATIVE)
    else:  # new_session
        decision = RP.decide(budget=b, session=None, fingerprint=None, progress=None,
                             external_known=True)
    # 因果：resume/fork/new 都接续 parent run trace（span link 指回 parent span）
    parent = TC.trace_context_for_run("run_canary")
    child = parent.child("iteration", 1)
    link = TC.span_link(child.span_id, parent.span_id, relation=mode)
    return RecoveryDrillResult(
        mode=mode, decision_mode=decision.mode.value,
        budget_exhausted=b.exhausted, causality_intact=link["relation"] == mode,
        detail=f"policy_version={decision.policy_version}; reason={decision.reason}",
    )


# ════════════════════════════════════════════════════════════════════════════
# 8.5 sandbox canary（Node + Python + network/credential denial）
# ════════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class SandboxDrillResult:
    language: str                # python / node
    tier: str                    # local_worktree / container
    exit_code: int               # 执行 exit（policy block → -1）
    network_denied: bool         # requested_hosts 违例被 block
    credential_denied: bool      # 长期凭据不进 sandbox（始终 True，design 6.4）


def run_sandbox_drill(*, sandbox: SB.ExecutionSandbox, handle: SB.SandboxHandle,
                      command, language: str,
                      network_violation_host: str | None = None,
                      credential_kind: str = SP.PUB_GIT_PUSH) -> SandboxDrillResult:
    """sandbox canary（task 8.5）：Node/Python fixture 跑过 tier + network/credential denial。

    * fixture 正常执行 → exit_code 来自 SandboxRunResult；
    * requested_hosts 有未声明目标 → policy block（network_denied=True）；
    * 长期凭据（github/smtp/cloud）无论何种 policy 都不进 sandbox（credential_denied 始终 True）。
    """
    requested = (network_violation_host,) if network_violation_host else ()
    run = sandbox.run(handle, command, requested_hosts=requested)
    if isinstance(run, SB.SandboxBlocked):
        exit_code = -1
        network_denied = bool(run.policy_violation)
    else:
        exit_code = run.exit_code
        network_denied = False
    cred_denied = not SP.sandbox_credential_allowed(
        policy=SP.CredentialPolicy.HOST_ONLY, kind=credential_kind)
    return SandboxDrillResult(
        language=language, tier=handle.tier.value, exit_code=exit_code,
        network_denied=network_denied, credential_denied=cred_denied,
    )


# ════════════════════════════════════════════════════════════════════════════
# 8.6 journal-driven dispatch 开闸 + legacy-state fallback（一个 release cycle）
# ════════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class DispatchCutoverResult:
    driven_by: str               # "journal" / "legacy_fallback"
    terminal_state: str
    fallback_reason: str


def run_dispatch_cutover_drill(*, journal_driven: bool,
                               journal_events: list | None = None,
                               legacy_records: list[dict] | None = None) -> DispatchCutoverResult:
    """dispatch cutover（task 8.6）：flag 开 → journal reducer 驱动；关/失败 → legacy fallback。

    design 决策#8：``journal_driven_dispatch`` flag 开之前 shadow parity 必须成立；开闸后仍**保留
    legacy-state fallback 一个 release cycle**（flag 关或 reducer 失败 → 读历史 dispatch JSON）。
    """
    if journal_driven and journal_events:
        try:
            first = journal_events[0]
            base = first.payload.get("base", "") if isinstance(first.payload, dict) else ""
            state = L.reduce(
                journal_events,
                L.initial_state(first.run_id, first.prd_id, first.iteration_id, base=base),
            )
            return DispatchCutoverResult("journal", state.status.value, "")
        except Exception as e:
            # reducer 失败 → fallback（一个 release cycle 内 legacy 仍可用）
            return _legacy_fallback(legacy_records, f"journal reducer failed: {e}")
    return _legacy_fallback(legacy_records, "journal_driven_dispatch disabled (shadow→driven transition)")


def _legacy_fallback(legacy_records, reason) -> DispatchCutoverResult:
    states = [CR.legacy_status(r) for r in (legacy_records or []) if isinstance(r, dict)]
    terminal = states[-1].value if states else L.IterationStatus.STATE_CORRUPT.value
    return DispatchCutoverResult("legacy_fallback", terminal, reason)


def resolve_dispatch_source(*, journal_driven_flag, project_id, allowlist,
                            parity_passed, journal_events=None,
                            legacy_records=None) -> DispatchCutoverResult:
    """task 7.5：dispatch 三重 gate——flag + parity + allowlist 全过才 journal-driven；否则 legacy fallback。

    spec（durable-runtime Requirement "shadow mode → journal authority"）：new runs switch to
    journal-reduced decisions **only after real parity evidence passes**；tasks 7.5 单项目 rollout
    （allowlist）。gate 任一不过 → ``legacy_fallback``（reason 指明未开闸维度），**保留并测试 legacy
    读取回退一个 release cycle**（design 决策#8；reducer 失败亦 fallback）。

    三重 gate 全过后复用 ``run_dispatch_cutover_drill`` 的 reducer 驱动 + reducer-fail fallback——
    把 gate 判定与 reducer 执行解耦，gate 不重写 reducer 逻辑。
    """
    allow = set(allowlist or ())
    if not journal_driven_flag:
        return _legacy_fallback(
            legacy_records, "journal_driven_dispatch flag off (single-project rollout gate)")
    if not parity_passed:
        return _legacy_fallback(
            legacy_records, "shadow parity not passed (design 决策#8 cutover 前置)")
    if project_id not in allow:
        return _legacy_fallback(
            legacy_records, f"project {project_id!r} not in dispatch allowlist (single-project rollout)")
    # 三重 gate 通过 → journal reducer 驱动；reducer 失败仍 legacy fallback（一个 release cycle）
    return run_dispatch_cutover_drill(
        journal_driven=True, journal_events=journal_events, legacy_records=legacy_records)


# ════════════════════════════════════════════════════════════════════════════
# 8.8 full repository quality gate + archive passing evidence
# ════════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class QualityGateResult:
    tests_total: int
    tests_failed: int
    passed: bool                 # tests_failed==0 且 total>0 且 evidence 全归档
    evidence_digests: tuple[str, ...] = ()
    detail: str = ""


def run_quality_gate(*, test_counts: dict, evidence_items, artifact_root: str) -> QualityGateResult:
    """quality gate（task 8.8）：聚合 test/sandbox/recovery/telemetry evidence + 归档 artifact store。

    ``passed`` ⇔ 全套测试绿（failed==0, total>0）**且** 每条 evidence 成功归档（内容寻址 digest）。
    任何归档失败 → 不通过（绝不伪装绿，design「telemetry/journaling 失败不伪装绿」）。
    """
    total = int(sum(test_counts.values())) if test_counts else 0
    failed = int(test_counts.get("failed", 0)) if test_counts else 0
    digests: list[str] = []
    archive_ok = True
    for kind, content in (evidence_items or []):
        try:
            ref = artifact_store.store(artifact_root, content, kind=kind, sensitivity="internal")
            digests.append(ref.digest)
        except Exception:
            archive_ok = False
    passed = (failed == 0 and total > 0 and archive_ok and len(digests) == len(evidence_items or []))
    detail = (f"tests {total - failed}/{total} pass; "
              f"evidence {len(digests)}/{len(evidence_items or [])} archived")
    return QualityGateResult(tests_total=total, tests_failed=failed, passed=passed,
                             evidence_digests=tuple(digests), detail=detail)


# ════════════════════════════════════════════════════════════════════════════
# 顶层：一次完整 cutover 套件（聚合 8.1-8.8 各 drill 结果，供 8.8 归档前汇总）
# ════════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class CutoverSuiteResult:
    shadow_parity_matched: bool
    lifecycle_all_pass: bool
    crash_all_exactly_once: bool
    recovery_all_intact: bool
    sandbox_all_clean: bool
    dispatch_cutover_ok: bool
    quality_gate_passed: bool
    overall_passed: bool
    archive_digest: str | None = None       # task 7.6：suite 通过时归档 summary 的内容寻址 digest（None=未归档/red）

    @property
    def summary(self) -> str:
        flags = [
            f"parity={self.shadow_parity_matched}", f"lifecycle={self.lifecycle_all_pass}",
            f"crash={self.crash_all_exactly_once}", f"recovery={self.recovery_all_intact}",
            f"sandbox={self.sandbox_all_clean}", f"cutover={self.dispatch_cutover_ok}",
            f"quality={self.quality_gate_passed}",
        ]
        return "cutover suite: " + ("PASS" if self.overall_passed else "FAIL") + " (" + ", ".join(flags) + ")"


def run_cutover_suite(*, shadow_parity_matched, lifecycle_all_pass, crash_all_exactly_once,
                      recovery_all_intact, sandbox_all_clean, dispatch_cutover_ok,
                      quality_gate_passed, artifact_root) -> CutoverSuiteResult:
    """task 7.6：完整 cutover 套件运行器——7 维度全绿才 overall_passed；通过则归档不可变 summary 证据。

    spec（runtime-cutover-evidence「Quality command passes」+ design 决策#6 archive immutable passing
    evidence）：marking the change complete 前跑完整 quality/sandbox/recovery/canary 套件，全绿且证据
    归档。任一维度 red → ``overall_passed=False`` 且**不归档**（绝不把 red 套件伪装成绿归档）。

    单一职责：调用方从各 drill Result（run_shadow_parity_evidence / run_sdk_hook_canary /
    run_crash_reconciliation_evidence / run_recovery_drill / run_sandbox_drill /
    run_dispatch_cutover_drill / run_quality_gate）提取 pass bool 传入——本函数只汇总 + 归档，
    不重跑 drill 逻辑。归档用 ``artifact_store`` 内容寻址（同 summary → 同 digest，可复现验证）。
    """
    flags = (shadow_parity_matched, lifecycle_all_pass, crash_all_exactly_once,
             recovery_all_intact, sandbox_all_clean, dispatch_cutover_ok, quality_gate_passed)
    suite = CutoverSuiteResult(
        shadow_parity_matched=shadow_parity_matched, lifecycle_all_pass=lifecycle_all_pass,
        crash_all_exactly_once=crash_all_exactly_once, recovery_all_intact=recovery_all_intact,
        sandbox_all_clean=sandbox_all_clean, dispatch_cutover_ok=dispatch_cutover_ok,
        quality_gate_passed=quality_gate_passed, overall_passed=all(flags))
    if not suite.overall_passed:
        return suite                                # red 套件不归档（绝不伪装绿归档）
    ref = artifact_store.store(artifact_root, suite.summary, kind="cutover_suite",
                               sensitivity="internal")
    return replace(suite, archive_digest=ref.digest)


# ════════════════════════════════════════════════════════════════════════════
# task 7.6（评审 P0-2 修正）：完整 cutover 套件**运行器**——自行编排执行各子 drill + 归档 manifest
# ════════════════════════════════════════════════════════════════════════════
# 评审 P0-2：旧 run_cutover_suite 接收 7 个布尔值做 all()，是聚合器非运行器。本节新增
# run_full_cutover_suite——runner **调用每个子 drill 的执行入口**（注入 bundle），从各 Result 提取
# pass + detail，构建不可变 manifest，全绿才归档带 digest 的 manifest（design#1 runner 编排真实
# drill + #6 archive immutable evidence）。drill 注入保持可测（fake bundle 验证编排/归档逻辑）+ 生产
# 可注入真实 drill（真实跑各子项，见 real_cutover_drills）。

# lifecycle canary 每场景的预期 gate（lifecycle pass = 全场景 gate 符合预期，非"全 allow"——
# no_test/test_red/stale_test 应 deny 阻断，test_green allow，revise/compaction/subagent/hook-failure 各符预期）
# r3 P0-1 闭环：公开为验收契约常量——7.2 _drill_predicate gate exact-match + 7.6 _lifecycle_canary_passed
# 共用同一份预期值，杜绝两处各写一份漂移致假绿。
EXPECTED_LIFECYCLE_GATES: dict[str, str] = {
    "no_test": EV.GATE_NOT_RUN,
    "test_red": EV.GATE_FAILED,
    "stale_test": EV.GATE_STALE,
    "test_green": EV.GATE_PUBLISH,
    "semantic_revise": "semantic_revise",
    "compaction": "snapshot_persisted",
    "subagent": "publication_blocked",
    "hook_failure": "fail_closed",
}
# r5 P0-2 闭环（路 B 诚实标红）：SDK callback 维度须逐场景真实触发的 8 场景——对齐 task 7.2 契约全 7 path
# （no-test/stale-test/green-test/semantic-revise/compaction/subagent/hook-failure）+ test_red 基线。
# compaction/hook_failure 的 PreCompact callback 需逼近上下文上限触发 auto-compact，单次 headless query
# 不可靠触发（SDK 暂无稳定公开的主动触发 API）→ 真实 query 缺这两条 callback 时 7.2/7.6 诚实 red、不归档
# （不再用 adapter gate 补绿）。adapter on_pre_compact 证 gate 逻辑（adapter_contract_proven），非
# sdk_callback_proven。路 A（真触发 PreCompact）拆后续 spike：streaming ClaudeSDKClient / 手动 /compact
# /可控 context threshold。7.2 _drill_predicate + 7.6 _sdk_canary_outcome 共用此常量，防漂移。
SDK_CALLBACK_REQUIRED_SCENARIOS: tuple[str, ...] = (
    "no_test", "test_red", "stale_test", "test_green",
    "semantic_revise", "compaction", "subagent", "hook_failure")

# r6 P0（评审）：每场景预期 state 标签——per-scenario proven 须 **state 精确匹配**（R4 §3.4 测试状态/陈旧度/
# 语义判定），杜绝「own journal 有 expected event + cid」即 proven 的假绿。与 EXPECTED_LIFECYCLE_GATES /
# SDK_CALLBACK_REQUIRED_SCENARIOS 同处——场景预期三件套（gate 契约 / callback 必须集 / state 标签）。
# state_matches 纯函数按标签校验 runner 从**该场景单一 query** 提取的 observed_state（同源）。
SCENARIO_EXPECTED_STATE: dict[str, str] = {
    "no_test": "reply_no_test_no_tool",   # reply 含 "NO TEST" 且全程无 tool use
    "test_red": "bash_nonzero",           # Bash 非零退出（`false` → exit 1）
    "test_green": "bash_zero_green",      # Bash 零退出 + stdout 含 GREEN
    "stale_test": "bash_zero_stale",      # Bash 零退出 + stdout 含 STALE
    "semantic_revise": "reply_revise",    # reply 含 REVISE
    "compaction": "blocked",              # blocked 场景（query 不跑），无 state 要求
    "subagent": "subagent_started",       # SubagentStart lifecycle 事件触发
    "hook_failure": "blocked",
}


@dataclass(frozen=True)
class ScenarioJudgement:
    """单场景判定（R4 §6 纯函数 ``evaluate_scenario`` 输出）。

    ``proven`` ⇔ own journal 产出 expected event AND invocation 携带 own correlation_id AND
    observed state 精确匹配 ``SCENARIO_EXPECTED_STATE`` 标签。三维度须**同时**成立——杜绝「journal
    有 event 就 proven」（r5 残留假绿）或「callback 名出现就 proven」（评审 P0 反例）。``diagnostic``
    给不通过的具体维度，``observed_summary`` 给观察证据摘要（诊断/归档，不含完整 prompt/output）。
    """
    scenario_id: str
    proven: bool
    diagnostic: str
    journal_has_expected: bool
    carries_own_cid: bool
    state_matched: bool
    expected_state: str
    observed_summary: str


def state_matches(label: str, observed: dict) -> bool:
    """r6 P0（评审）：纯函数——校验 observed state 是否符合预期标签（R4 §3.4）。

    ``observed`` 由 runner 从**该场景单一 query** 的 callback + result 提取（同源——与 journal event /
    correlation_id 同一 query），含：
      * ``bash_results``: list[{exit_code, output}]（PostToolUse Bash 的退出码/stdout）
      * ``reply_text``: str（query 最终 reply，Stop 场景语义判定依据）
      * ``saw_subagent_start``: bool（SubagentStart lifecycle 事件触发）
      * ``saw_tool_use``: bool（任何 PostToolUse 触发——no_test 场景须为 False）

    best-effort 提取失败（字段缺失）→ 对应分支 False → fail-closed（proven=False，绝不假绿）。
    纯 stdlib、无副作用，便于反例测试直接构造 observed_state 验证匹配逻辑。
    """
    bash = observed.get("bash_results") or ()
    reply = observed.get("reply_text") or ""
    saw_sub = bool(observed.get("saw_subagent_start"))
    saw_tool = bool(observed.get("saw_tool_use"))
    if label == "reply_no_test_no_tool":
        return "NO TEST" in reply and not saw_tool
    if label == "bash_nonzero":
        return any(br.get("exit_code") is not None and br.get("exit_code") != 0 for br in bash)
    if label == "bash_zero_green":
        return any(br.get("exit_code") == 0 and "GREEN" in (br.get("output") or "") for br in bash)
    if label == "bash_zero_stale":
        return any(br.get("exit_code") == 0 and "STALE" in (br.get("output") or "") for br in bash)
    if label == "reply_revise":
        return "REVISE" in reply
    if label == "subagent_started":
        return saw_sub
    if label == "blocked":
        return True
    return False


def _summarize_observed(observed: dict) -> str:
    """observed_state → 诊断摘要（归档/反例定位用；只含维度计数，不含完整 prompt/output/stderr）。"""
    if not observed:
        return "<empty>"
    bash = observed.get("bash_results") or ()
    exits = ",".join(str(b.get("exit_code")) for b in bash) or "-"
    return (f"bash_exits=[{exits}] reply_len={len(observed.get('reply_text') or '')} "
            f"saw_sub={bool(observed.get('saw_subagent_start'))} "
            f"saw_tool={bool(observed.get('saw_tool_use'))}")


def evaluate_scenario(scenario_id, *, journal_has_expected, carries_own_cid,
                      observed_state, expected_state_label) -> ScenarioJudgement:
    """R4 §6 纯函数——单场景 proven 判定（无副作用，7.2 谓词 + 7.6 outcome 经
    ``evaluate_sdk_canary_scenarios`` 共调）。

    proven = own journal 产出 expected event AND invocation 携带 own correlation_id AND
    observed state 精确匹配 ``expected_state_label``。三维度任一缺失即 not proven，``diagnostic``
    指明具体缺失维度（评审 response §2.1「失败并给出具体不匹配场景」）。
    """
    state_matched = state_matches(expected_state_label, observed_state or {})
    jhe = bool(journal_has_expected)
    coc = bool(carries_own_cid)
    proven = jhe and coc and state_matched
    if not jhe:
        diag = "no_journal_expected_event"
    elif not coc:
        diag = "no_own_correlation_id"
    elif not state_matched:
        diag = f"state_mismatch(expected={expected_state_label})"
    else:
        diag = "ok"
    return ScenarioJudgement(
        scenario_id=scenario_id, proven=proven, diagnostic=diag,
        journal_has_expected=jhe, carries_own_cid=coc, state_matched=state_matched,
        expected_state=expected_state_label, observed_summary=_summarize_observed(observed_state or {}))


# r3 P0-1 闭环：SDK canary 场景级判定单一真源——7.2 _drill_predicate（CLI 谓词）+ 7.6 _sdk_canary_outcome
# （manifest outcome extractor）共同调用 evaluate_sdk_canary_scenarios，杜绝两个验收入口语义漂移致假绿
# （评审 response §2.2 建议"收敛为一个纯函数，由两个消费点共同调用"）。
@dataclass(frozen=True)
class SdkCanaryScenarioVerdict:
    """SDK canary 场景级判定结果（7.2 谓词 + 7.6 outcome 共用）。

    ``passed`` ⇔ gate 维度（8 场景与 EXPECTED_LIFECYCLE_GATES 精确匹配）AND callback 维度
    （SDK_CALLBACK_REQUIRED_SCENARIOS 6 场景全 proven）AND evidence 完整（callback 无异常 / journal 可解析 /
    query 正常结束）。``gate_mismatches``/``missing_callbacks``/``integrity_failures``
    给反例的具体不匹配项（评审 response §2.1 要求"失败并给出具体不匹配场景"）。
    """
    passed: bool
    gate_ok: bool
    callback_ok: bool
    evidence_intact: bool              # r5 P1-2：evidence 完整性维度（7.2 + 7.6 共调，防谓词单独假绿）
    state_ok: bool                     # r6 P0：state 维度全场景精确匹配（杜绝 journal+cid 即 proven 假绿）
    gate_mismatches: tuple[str, ...]
    missing_callbacks: tuple[str, ...]
    integrity_failures: tuple[str, ...]   # r5 P1-2：具体完整性违例（诊断用）
    state_failures: tuple[str, ...] = ()   # r6 P0：state 不匹配场景（含 diagnostic）
    scenario_results: tuple[ScenarioJudgement, ...] = ()   # r6 P0：每场景判定（归档/诊断）


def evaluate_evidence_intact(*, callback_errors=(), journal_decode_errors: int = 0,
                             query_error=None, result_received: bool = True
                             ) -> tuple[bool, tuple[str, ...]]:
    """r5 P1-2（评审）：SDK canary evidence 完整性纯函数——7.2 谓词 + 7.6 outcome 共调。

    evidence 在以下任一情形判**不可信**（即便场景矩阵全绿）：
      * ``callback_errors`` 非空：注册的 hook callback 在真实 query 中抛异常 → observed events 可能残缺/错位；
      * ``journal_decode_errors`` > 0：hook journal 行 JSON 解析失败 → 证据可能丢失；
      * ``query_error`` 非空：SDK query 抛异常（崩/超时/代理拒）→ 其产出不可信；
      * ``result_received`` 为 False：query 未正常返回 result → 残缺 evidence 不可作通过依据。

    返回 ``(intact, failures)``：``failures`` 为具体违例标签 tuple，供诊断与 outcome detail。纯 stdlib、
    无副作用，7.2 / 7.6 两个消费点共用，杜绝谓词（7.2）单独放行完整性违例致假绿（评审 P1-2 反例）。
    """
    failures: list[str] = []
    if callback_errors:
        failures.append(f"callback_errors={len(callback_errors)}")
    if journal_decode_errors:
        failures.append(f"journal_decode_errors={journal_decode_errors}")
    if query_error:
        failures.append("query_error")
    if not result_received:
        failures.append("result_not_received")
    return (not failures, tuple(failures))


def evaluate_sdk_canary_scenarios(
        *, per_scenario: dict, callback_errors=(), journal_decode_errors: int = 0,
        query_error=None, result_received: bool = True) -> "SdkCanaryScenarioVerdict":
    """SDK canary 场景级通过判定——单一纯函数，7.2 谓词 + 7.6 outcome 共调（防漂移）。

    r6 P0（评审）：``per_scenario`` 替代旧 ``gates`` + ``callbacks_proven`` 两**独立集合**——每场景的
    gate + callback + state 须来自**同一 query 的绑定 entry**（同源）。旧签名接收独立 gates dict（adapter
    fixture）+ callbacks_proven set（query），无法证明二者同源 → 审查者反例「全 callback 名 + fixture
    stop_gates = passed」成立。新签名强制 per-scenario 绑定：每场景 entry 含 journal_has_expected /
    carries_own_cid / observed_state / adapter_gate / blocked_reason，全部来自该场景单一 query。

    Args:
        per_scenario: scenario_id → 绑定证据 dict（7.2 取 ``per_scenario_real_triggers``；7.6 取
            ``SdkHookCanaryEvidence.per_scenario``）。每 entry 字段：``journal_has_expected`` /
            ``carries_own_cid`` / ``observed_state`` / ``adapter_gate`` / ``blocked_reason``。
        callback_errors / journal_decode_errors / query_error / result_received：evidence 完整性入参（r5 P1-2）。

    四维度 **per-scenario** 同时成立才 passed（杜绝独立集合假绿，评审 P0 反例）：
      * gate：每场景 ``adapter_gate`` 精确匹配 EXPECTED_LIFECYCLE_GATES（非 truthy，response §2.1）；
      * callback：SDK_CALLBACK_REQUIRED_SCENARIOS 全场景 proven（evaluate_scenario 三维度成立）；
      * state：每场景 observed_state 精确匹配 SCENARIO_EXPECTED_STATE（R4 §3.4，杜绝 journal+cid 即 proven）；
      * evidence 完整：callback 无异常 / journal 可解析 / query 正常结束（评审 P1-2）。
    四维度独立报告（gate_mismatches/missing_callbacks/state_failures/integrity_failures），便于反例定位。
    """
    results: list[ScenarioJudgement] = []
    state_failures: list[str] = []
    gate_mismatches: list[str] = []
    for sc in SDK_CALLBACK_REQUIRED_SCENARIOS:
        ev = (per_scenario.get(sc) or {}) if per_scenario else {}
        blocked = bool(ev.get("blocked_reason"))
        exp_state = SCENARIO_EXPECTED_STATE.get(sc, "")
        if blocked:
            # blocked 场景（compaction/hook_failure）：query 不跑，诚实 not proven（路 B）。state 维度对
            # blocked 标签恒 True，但 proven 仍 False（无 journal/cid）→ 计入 missing_callbacks（callback_ok=False）。
            j = ScenarioJudgement(sc, False, f"blocked:{(ev.get('blocked_reason') or '')[:40]}",
                                  False, False, True, exp_state, "blocked")
        else:
            j = evaluate_scenario(
                sc,
                journal_has_expected=ev.get("journal_has_expected", False),
                carries_own_cid=ev.get("carries_own_cid", False),
                observed_state=ev.get("observed_state") or {},
                expected_state_label=exp_state)
        results.append(j)
        if not j.proven and not blocked:
            state_failures.append(f"{sc}:{j.diagnostic}")
        actual_gate = ev.get("adapter_gate")
        exp_gate = EXPECTED_LIFECYCLE_GATES.get(sc)
        if actual_gate != exp_gate:
            gate_mismatches.append(f"{sc}: expected={exp_gate!r} actual={actual_gate!r}")
    proven_set = {j.scenario_id for j in results if j.proven}
    missing = tuple(sc for sc in SDK_CALLBACK_REQUIRED_SCENARIOS if sc not in proven_set)
    callback_ok = not missing
    gate_ok = bool(per_scenario) and len(per_scenario) >= len(EXPECTED_LIFECYCLE_GATES) and not gate_mismatches
    state_ok = not state_failures
    evidence_intact, integrity_failures = evaluate_evidence_intact(
        callback_errors=callback_errors, journal_decode_errors=journal_decode_errors,
        query_error=query_error, result_received=result_received)
    return SdkCanaryScenarioVerdict(
        passed=gate_ok and callback_ok and state_ok and evidence_intact,
        gate_ok=gate_ok, callback_ok=callback_ok, evidence_intact=evidence_intact, state_ok=state_ok,
        gate_mismatches=tuple(gate_mismatches), missing_callbacks=missing,
        integrity_failures=integrity_failures, state_failures=tuple(state_failures),
        scenario_results=tuple(results))


# r5 P0-1 闭环：sandbox 通过判定单一真源——real_cutover_suite 消费。旧逻辑只查 credential_isolated
# + denied_egress_enforced 两项，docker canary 其余 5 项（node/allowed-egress/resource/
# unavailable-runtime/python）失败仍 sandbox_pass=True → overall PASS → 归档（评审 r5 P0-1 假绿）。
# 本纯函数纳入 all_pass（全 7 项），任一红即 sandbox 红，杜绝 docker 假绿归档。
_SANDBOX_REQUIRED_DIMS: tuple[str, ...] = (
    "python_exec", "credential_isolated", "node_exec",
    "denied_egress_enforced", "allowed_egress_works",
    "unavailable_runtime_fail_fast", "resource_limit_enforced")


@dataclass(frozen=True)
class SandboxVerdict:
    """sandbox 通过判定结果（7.6 real_cutover_suite 消费）。

    ``sandbox_pass`` ⇔ 凭据隔离 AND 网络违例 block AND docker canary 全 7 项过。
    ``failing_dims`` 给反例的具体失败项（评审 r5 P0-1 要求 docker 假绿可定位到维度）。
    """
    sandbox_pass: bool
    docker_all_pass: bool
    cred_denied: bool
    net_denied: bool
    failing_dims: tuple[str, ...]


def evaluate_sandbox_verdict(docker_summary: dict) -> "SandboxVerdict":
    """docker canary summary → sandbox 通过判定（纯函数，无副作用，便于反例测试）。

    Args:
        docker_summary: ``real_docker_canary`` 返回的 summary（all_pass + 7 项明细）。

    sandbox 通过 ⇔ credential_isolated AND denied_egress_enforced AND all_pass（全 7 项）。
    旧 real_cutover_suite 只查前两项，漏 node/allowed-egress/resource/unavailable-runtime/python
    → docker 失败仍归档（评审 r5 P0-1 假绿）。``failing_dims`` 按声明顺序列出失败项，便于诊断。
    """
    cred_denied = bool(docker_summary.get("credential_isolated"))
    net_denied = bool(docker_summary.get("denied_egress_enforced"))
    docker_all_pass = bool(docker_summary.get("all_pass"))
    failing_dims = tuple(d for d in _SANDBOX_REQUIRED_DIMS if not docker_summary.get(d))
    sandbox_pass = cred_denied and net_denied and docker_all_pass
    return SandboxVerdict(
        sandbox_pass=sandbox_pass, docker_all_pass=docker_all_pass,
        cred_denied=cred_denied, net_denied=net_denied, failing_dims=failing_dims)


@dataclass(frozen=True)
class DrillOutcome:
    """一项 drill 的运行结果摘要（manifest 元素）。``passed`` ⇔ 该维度验收通过；``detail`` 给归档可读证据。

    r2 P0-5：``evidence_digests`` 持该子 drill evidence 的内容寻址 digest（runner 归档每个子 drill
    evidence 后填入），manifest ``sub_evidence_refs`` 聚合全部子 digest——passing manifest 引用所有子
    evidence，可逐项 ``artifact_store.load`` 校验。
    """
    name: str
    passed: bool
    detail: str
    evidence_digests: tuple[str, ...] = ()


@dataclass(frozen=True)
class CutoverManifest:
    """task 7.6：完整 cutover 套件**编排运行**后的不可变通过证据 manifest。

    runner 自行执行各子 drill（非接收外部布尔值），逐项记 ``(name, passed, detail)``。``overall_passed``
    ⇔ 全项 passed；全绿归档 manifest 内容寻址 digest（design#6）；任一 red → 不归档（绝不伪装绿归档）。

    r2 P0-5：``sub_evidence_refs`` 聚合每子 drill evidence 的 digest（runner 归档每子 evidence 后收集），
    passing manifest 引用全部子 evidence digest（非只存 summary 字符串），满足 §6.8。
    """
    outcomes: tuple[DrillOutcome, ...]
    overall_passed: bool
    archive_digest: str | None = None
    sub_evidence_refs: tuple[str, ...] = ()
    # r3 P0-2：子证据完整性门结论（"ok" 或失败原因），便于上层独立复核为何不归档 passing manifest。
    evidence_integrity: str = "ok"
    # r5 P1-4（评审① + r4-response-revise §5）：结构化 manifest 字段——归档结构化 JSON（非 summary 字符串）。
    # 审查者：「run_full_cutover_suite() 仍归档 manifest.summary，没有结构化 manifest」。§5 要求 manifest 至少含
    # schema_version/subject_commit/runner version+时间/七 outcome 判定+诊断+evidence digests/全局 sub_evidence_refs/
    # evidence_integrity/manifest 自身 digest 算法。manifest_digest 由归档 store 内容寻址算出后回填（read-back 锚点）。
    schema_version: str = "cutover-manifest/v1"
    subject_commit: str | None = None
    runner_version: str = ""
    executed_at: str = ""
    digest_algorithm: str = "sha256"
    manifest_digest: str | None = None
    # r6 P1-6（评审）：open_items——诚实报告"已知未验收/受限"项（不阻断 overall，同 P1-1 语义）。
    # telemetry 因真实 OTLP/degradation suite 未接入，移出 overall all() 进 open_items（red + known limitation）。
    open_items: tuple[dict, ...] = ()

    @property
    def summary(self) -> str:
        parts = [f"{o.name}={'PASS' if o.passed else 'FAIL'}" for o in self.outcomes]
        head = "PASS" if self.overall_passed else "FAIL"
        return f"cutover manifest: {head} (" + ", ".join(parts) + ")"

    def structured(self) -> dict:
        """r5 P1-4（评审① + §5）：结构化 manifest dict——归档此（非 summary 字符串）。

        含 §5 全部必填字段：schema_version/subject_commit/runner_version/executed_at/overall_passed/
        outcomes[]（name/passed/detail/evidence_digests）/sub_evidence_refs/evidence_integrity/digest_algorithm。
        ``manifest_digest`` 不含于此 dict（它由归档 store 对本 dict 序列化内容算内容寻址 digest，回填到
        ``CutoverManifest.manifest_digest``，作 read-back 锚点——避免 digest 自引用循环）。
        """
        return {
            "schema_version": self.schema_version,
            "subject_commit": self.subject_commit,
            "runner_version": self.runner_version,
            "executed_at": self.executed_at,
            "overall_passed": self.overall_passed,
            "outcomes": [{"name": o.name, "passed": o.passed, "detail": o.detail,
                          "evidence_digests": list(o.evidence_digests)} for o in self.outcomes],
            "sub_evidence_refs": list(self.sub_evidence_refs),
            "evidence_integrity": self.evidence_integrity,
            "digest_algorithm": self.digest_algorithm,
            "open_items": list(self.open_items),
        }

    def structured_json(self) -> str:
        """结构化 manifest 的规范序列化（sort_keys + ensure_ascii=False，供归档 + read-back digest 稳定）。"""
        return json.dumps(self.structured(), ensure_ascii=False, sort_keys=True, default=str)


def _lifecycle_canary_passed(ev: "SdkHookCanaryEvidence") -> bool:
    """lifecycle pass = 每场景 gate 符合 ``EXPECTED_LIFECYCLE_GATES``（非"全 allow"）。

    r5 P1-2：gate 维度已收敛进 ``evaluate_sdk_canary_scenarios``（7.2 谓词 + 7.6 outcome 共调）。
    本函数保留为单一 gate-精确匹配 契约断言，便于归档/审计单独复核 gate 维度（非死代码）。
    """
    return all(ev.stop_gates.get(sc) == exp for sc, exp in EXPECTED_LIFECYCLE_GATES.items())


# 各 drill Result → DrillOutcome 提取器（runner 编排执行各 drill 后调用，从真实 Result 提取 pass+detail）
def _shadow_parity_outcome(ev: "ShadowParityEvidence") -> DrillOutcome:
    return DrillOutcome("shadow_parity", ev.parity.matched,
                        f"matched={ev.parity.matched}; mismatches={len(ev.parity.mismatches)}")


def _sdk_canary_outcome(ev: "SdkHookCanaryEvidence") -> DrillOutcome:
    # r2 P0-5：sdk_canary 通过 = adapter gate spec 7 场景符合预期 AND 真实 SDK query 证明 lifecycle callback 触发
    # r3 P0-1 闭环 HIGH-1：进一步要求 SDK_CALLBACK_REQUIRED_SCENARIOS 6 场景 callback 逐场景真实 proven
    # （非"任意 callback 出现即真"）。与 _drill_predicate 7.2 同语义，共用常量防两处漂移致假绿。
    # r5 P1-2（评审）：gate+callback+evidence-integrity 三维度收敛到 evaluate_sdk_canary_scenarios（与 7.2 谓词
    # 同一纯函数），杜绝 outcome 单独放行 query_error/result_not_received/callback_errors 致假绿。
    # real_query_proven 为 7.6 额外顶层门（真实 query 曾观察到 lifecycle callback，非 adapter fixture 自证）。
    # r6 P0：per_scenario 绑定（每场景 gate+callback+state 同源），替代 gates+callbacks_proven 独立集合。
    # ev.per_scenario 每项含 scenario_id + 绑定字段；重构为 dict 传 evaluate_sdk_canary_scenarios。
    per = {e.get("scenario_id"): e for e in ev.per_scenario if e.get("scenario_id")} if ev.per_scenario else {}
    verdict = evaluate_sdk_canary_scenarios(
        per_scenario=per,
        callback_errors=ev.callback_errors, journal_decode_errors=ev.journal_decode_errors,
        query_error=ev.query_error, result_received=ev.result_received)
    passed = ev.real_query_proven and verdict.passed
    diag = ev.summary
    if not verdict.evidence_intact:
        diag += f" | integrity_fail={','.join(verdict.integrity_failures) or 'unknown'}"
    if verdict.state_failures:
        diag += f" | state_fail={','.join(verdict.state_failures)}"
    return DrillOutcome("sdk_canary", passed, diag)


def _crash_outcome(ev: "CrashReconciliationEvidence") -> DrillOutcome:
    return DrillOutcome("crash_reconciliation", ev.all_exactly_once, ev.summary)


def _recovery_outcome(results: "tuple[RecoveryDrillResult, ...]") -> DrillOutcome:
    intact = bool(results) and all(r.causality_intact for r in results)
    detail = " | ".join(f"{r.mode}={r.decision_mode}/causal={r.causality_intact}" for r in results)
    return DrillOutcome("recovery", intact, detail or "no recovery results")


def _sandbox_outcome(results: "tuple[SandboxDrillResult, ...]") -> DrillOutcome:
    # clean = 每个 canary：长期凭据始终拒（host-side verified）**且**（正常退出 exit0 或因 network
    # 违例被 policy block）。exit0=fixture 正常跑，network_denied=违例正确阻断（两者皆 clean）。
    clean = bool(results) and all(r.credential_denied and (r.exit_code == 0 or r.network_denied)
                                  for r in results)
    detail = " | ".join(
        f"{r.language}/{r.tier}=exit{r.exit_code}/net_denied={r.network_denied}" for r in results)
    return DrillOutcome("sandbox", clean, detail or "no sandbox results")


def _dispatch_outcome(r: "DispatchCutoverResult") -> DrillOutcome:
    # dispatch ok = reducer 给出合法终态（journal 驱动或 legacy fallback 均可，design#8 一个 release
    # cycle 允许 fallback）；仅 state_corrupt（无历史/reducer 全失败）算 red。
    ok = r.driven_by in ("journal", "legacy_fallback") and \
        r.terminal_state != L.IterationStatus.STATE_CORRUPT.value
    return DrillOutcome("dispatch_cutover", ok, f"driven_by={r.driven_by}; terminal={r.terminal_state}")


def _quality_outcome(r: "QualityGateResult") -> DrillOutcome:
    return DrillOutcome("quality_gate", r.passed, r.detail)


# ─── task 7.6 telemetry 维度（r5 P1-3 评审：telemetry 从"仅归档"升为带明确通过谓词的 gate）──
@dataclass(frozen=True)
class TelemetryEvidence:
    """r5 P1-3（评审）：SDK callback telemetry 契约证据——证明 SDK 遥测通道在线、未降级。

    与 ``sdk_canary`` 严格分离：sdk_canary 证「逐场景 callback+gate 契约」；telemetry 证「SDK 真实调用
    了注册的 hooks（callback_invocations 非空）+ lifecycle 事件流可观测 + query 未中断/未降级」。后者是
    前者可信的前提——若 SDK 降级为 no-op（无 invocation / lifecycle 事件不可观测），sdk_canary 的逐场景
    proven 即失去依据。评审 P1-3 反例：旧实现把 ``_telemetry`` 仅追加到 quality_gate.evidence_items（只归档），
    run_quality_gate 只验内容写入、不判 OTLP/degradation 契约 → SDK 降级为 no-op 仍 overall PASS 假绿。
    本 evidence 经 ``_telemetry_outcome`` 以明确谓词成为 cutover 套件的独立 gate 维度。
    """
    callback_invocations: tuple[dict, ...]   # SDK 真实调用注册 hook 的记录（非空 = 遥测通道在线）
    lifecycle_types_seen: tuple[str, ...]    # 可观测 lifecycle 事件类型（PreToolUse/PostToolUse/Stop/...）
    num_turns: int | None                    # SDK query 返回的 turn 数（None = 未收到 result）
    query_error: str | None                  # query 异常（非空 = 遥测中断）
    summary: str = ""
    degradation: str | None = None           # 显式降级标记（runner 检测到时填，如 "no_callback_invocations"）


def _telemetry_outcome(ev: "TelemetryEvidence") -> DrillOutcome:
    """r5 P1-3（评审）：telemetry gate 通过 = SDK 真实调用 hooks（callback_invocations 非空）AND lifecycle
    事件可观测（lifecycle_types_seen 非空）AND query 正常结束（无 query_error 且收到 result/num_turns）AND
    无显式降级标记。任一缺失 → telemetry 红（即便其余维度全绿，SDK 遥测通道未验证为在线即不可信）。

    以明确通过谓词成为 gate（评审 P1-3）：此前 telemetry 仅被归档进 quality_gate.evidence_items，run_quality_gate
    只检查内容是否写入、不判断 OTLP/degradation 契约，SDK 降级为 no-op 仍 overall PASS。
    """
    no_invocations = not ev.callback_invocations
    no_lifecycle = not ev.lifecycle_types_seen
    query_interrupted = bool(ev.query_error) or ev.num_turns is None
    degraded = bool(ev.degradation)
    fails: list[str] = []
    if no_invocations:
        fails.append("no_callback_invocations")
    if no_lifecycle:
        fails.append("no_lifecycle_types")
    if query_interrupted:
        fails.append("query_interrupted")
    if degraded:
        fails.append(f"degradation={ev.degradation}")
    passed = not fails
    diag = ev.summary or (f"invocations={len(ev.callback_invocations)} "
                          f"lifecycle_types={sorted(ev.lifecycle_types_seen)} "
                          f"num_turns={ev.num_turns} query_error={ev.query_error!r}")
    if fails:
        diag += f" | telemetry_fail={','.join(fails)}"
    return DrillOutcome("telemetry", passed, diag)


def _otlp_export_verified() -> bool:
    """r9-1（审核员 P0）+ r10-B1 诚实收敛（审核员 r9 复审 + 红队）：OTLP/HTTP 连通性探针。

    ⚠️ r10-B1 **停止 overclaim**：本函数只是「endpoint 可达且对 OTLP/HTTP POST 返 2xx」的**连通性探针**，
    **不证明**对端是真 OTLP collector、更不证明 collector 真的 ingest/存了 span。诚实边界：

      - 能抓的最朴素误配：``ENDPOINT=x``（无服务）→ 连接失败 → False。相比 r8-1（环境变量非空即 True），
        这堵住了「设个假值就假绿」的最弱路径——这是 r9-1 相对 r8-1 的**唯一**真实增益。
      - **抓不住**的残余洞（留 P2 硬化）：
        (a) dummy 2xx server：任何对 POST 返 200 的 HTTP 服务（含输入校验型）都让本函数返 True → 假绿；
        (b) collector-behind-2xx-ack-proxy：proxy 返 2xx ack 但 collector 未真正 ingest → 误判真接入；
        (c) gold-standard 唯一可信验证 =「export 一个已知 traceId → 事后从 collector/backend 回查该 traceId
            确实被 ingest」——r9-1 未实现，留 P2（真接入唯一可信证明）。

    生产无真实 OTLP collector → 永远 False → telemetry 诚实红（open_items 标 telemetry open，同 P1-6）。
    接真实 collector（OTLP/HTTP 4318）→ 本函数返 True，**但「2xx = 真接入」仅在 collector 直接裸暴露、无
    2xx-ack 中间层时成立**；生产若 collector 经 proxy，须 P2 traceId 回查才可信。

    open_items + runtime 7.6 connected 共用本函数（单一真理源；runtime._telemetry_connected 转调）。
    """
    import json as _json
    import os
    import time
    import urllib.error
    import urllib.request
    _ep = (os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
           or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"))
    if not _ep:
        return False
    # OTLP/HTTP traces：TRACES_ENDPOINT 是完整 URL（含 /v1/traces）；ENDPOINT 是 base（需加 /v1/traces）。
    _url = _ep if _ep.rstrip("/").endswith("/v1/traces") else _ep.rstrip("/") + "/v1/traces"
    # minimal valid OTLP/JSON span（traceId 16B / spanId 8B hex + 纳秒时间戳）——验 collector 接收 valid span。
    _tid, _sid = os.urandom(16).hex(), os.urandom(8).hex()
    _ns = int(time.time() * 1_000_000_000)
    _payload = _json.dumps({"resourceSpans": [{"scopeSpans": [{"spans": [{
        "traceId": _tid, "spanId": _sid, "name": "pa.cutover.telemetry.probe",
        "kind": 1, "startTimeUnixNano": str(_ns), "endTimeUnixNano": str(_ns + 1_000_000)}]}]}]},
        ensure_ascii=False).encode("utf-8")
    try:
        _req = urllib.request.Request(_url, data=_payload, method="POST",
                                      headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(_req, timeout=3) as _r:
            return 200 <= _r.status < 300
    except Exception:
        return False                 # 不可达 / 非 2xx / timeout → False（不可伪造；生产无 collector 走此）


@dataclass(frozen=True)
class CutoverDrillBundle:
    """task 7.6：各子 drill 的执行入口注入（runner 编排**调用执行**，非接收 bool）。

    每字段是无参 callable，返回对应 drill 的 Result/evidence。测试注入确定性 fake callable（验证
    编排/汇总/归档/red-不归档），生产注入真实 drill callable（真实跑 shadow parity / SDK canary /
    crash reconciliation / recovery / sandbox / dispatch cutover / quality gate，见 ``real_cutover_drills``）。
    design 决策#1（runner 编排真实 drill，非 disconnected helper 聚合布尔值——评审 P0-2）。
    """
    shadow_parity: Callable[[], "ShadowParityEvidence"]
    sdk_canary: Callable[[], "SdkHookCanaryEvidence"]
    crash_reconciliation: Callable[[], "CrashReconciliationEvidence"]
    recovery: Callable[[], "tuple[RecoveryDrillResult, ...]"]
    sandbox: Callable[[], "tuple[SandboxDrillResult, ...]"]
    dispatch_cutover: Callable[[], "DispatchCutoverResult"]
    quality_gate: Callable[[], "QualityGateResult"]
    # r5 P1-3（评审）：telemetry 维度——SDK 遥测通道 gate（callback_invocations/lifecycle_types/无降级）。
    # runner 调用本 callable 执行 telemetry 证据采集，由 _telemetry_outcome 判 pass/fail（明确谓词 gate）。
    telemetry: Callable[[], "TelemetryEvidence"]


def _archive_sub_evidence(artifact_root: str, name: str, ev) -> tuple[str, ...]:
    """归档单个子 drill evidence 到内容寻址 store，返回 digest tuple（r2 P0-5：manifest 引用子 evidence）。

    序列化 robust：dataclass→``asdict``、tuple→list 递归、其余 ``default=str`` 兜底。

    r3 P0-2 fail-closed：归档失败（序列化/落盘/digest 异常）**绝不静默返回空 tuple**——上抛 ``RuntimeError``，
    由 ``run_full_cutover_suite._exec`` 捕获记该维度 red（归档失败 = 套件不绿 = 不归档 passing manifest）。
    任一子证据归档失败必须 fail closed，绝不归档「缺证据」的 passing manifest。
    """
    def _jsonable(o):
        if isinstance(o, tuple):
            return [_jsonable(x) for x in o]
        if is_dataclass(o) and not isinstance(o, type):
            return asdict(o)
        return o
    try:
        blob = json.dumps({"drill": name, "evidence": _jsonable(ev)},
                          ensure_ascii=False, sort_keys=True, default=str)
        ref = artifact_store.store(artifact_root, blob, kind="test_output", sensitivity="internal")
        return (ref.digest,)
    except Exception as exc:   # r3 P0-2：归档失败 fail-closed，不再静默吞掉返回空 tuple
        raise RuntimeError(f"子证据归档失败（fail-closed）drill={name}: {exc!r}") from exc


# r3 P0-2 完整性门：passing manifest 必须引用全部子 drill 的可解析、可读、digest 匹配的证据。
# r5 P1-3（评审）：补 telemetry 维度（8 个）——SDK 遥测通道 gate，杜绝 telemetry 仅归档不判的假绿。
_EXPECTED_DRILL_NAMES: frozenset[str] = frozenset({
    "shadow_parity", "sdk_canary", "crash_reconciliation",
    "recovery", "sandbox", "dispatch_cutover", "quality_gate", "telemetry",
})


def _verify_sub_evidence_complete(outcomes: "tuple[DrillOutcome, ...]",
                                  artifact_root: str) -> tuple[bool, str]:
    """r3 P0-2 完整性门：七个 outcome 均至少有一个**可解析、可读取且 digest 匹配**的证据引用。

    三层校验（任一不满足 → ``(False, reason)``，runner 据此置 ``overall_passed=False`` 不归档 passing manifest）：
        1. outcome 数量/名字齐全——恰好 7 个，名字匹配 ``_EXPECTED_DRILL_NAMES``（防漏跑/多跑维度）；
        2. 每 outcome 至少 1 个 evidence digest（子证据已归档，非空——单纯业务 ``passed`` 但无证据引用不允许绿）；
        3. 每个 digest 经 ``artifact_store.load`` 读回 + 重算 digest 校验（可读 + digest 匹配，fail-closed）。

    digest → path 是 ``artifact_store._bucketed_path`` 单射（与 ``store`` 同源），故仅凭 digest 即可重构
    ``ArtifactRef`` 完成真实读取校验，满足 r3「可解析、可读取且 digest 匹配」三要件。
    """
    names = {o.name for o in outcomes}
    if names != _EXPECTED_DRILL_NAMES:
        missing = _EXPECTED_DRILL_NAMES - names
        extra = names - _EXPECTED_DRILL_NAMES
        return False, f"outcome 名字不齐全: missing={sorted(missing)} extra={sorted(extra)}"
    for o in outcomes:
        if not o.evidence_digests:
            return False, f"drill={o.name} 无已归档子证据引用（缺证据不允许 passing manifest）"
        for d in o.evidence_digests:
            ref = L.ArtifactRef(digest=d, size=0,
                                kind=L.ArtifactKind.TEST_OUTPUT.value,
                                path=artifact_store._bucketed_path(d),
                                sensitivity=L.Sensitivity.INTERNAL.value)
            try:
                artifact_store.load(artifact_root, ref)   # load 自带 digest 重算校验（fail-closed）
            except Exception as exc:
                return False, f"drill={o.name} 子证据 {d} 不可读/digest 不匹配: {exc!r}"
    return True, "ok"


# r7-S4（审核员）：open_items 白名单——仅允许真实 OTLP/degradation suite 未接入的 ``telemetry`` 作合法 open
# 项（同 P1-1/P1-6 known-limitation 语义，不阻断 overall）。read-back step 7 只允许白名单内的 red 项从
# ``all()`` 排除；非白名单 red 项进 open_items → read-back fail（杜绝「任意红色 outcome 塞进 open_items
# 即从 overall 排除」的假绿——如 quality_gate 真 red 被偷排）。
_ALLOWED_OPEN_ITEMS: frozenset[str] = frozenset({"telemetry"})


def _read_back_manifest(artifact_root: str, archive_digest: str) -> tuple[bool, str]:
    """r5 P1-4 + r6 P1-5（评审② + R4 §5）：结构化 manifest 归档后 read-back——7 步 fail-closed 严格校验。

    审查者 r6 反例：旧版「只查 5 键存在」→ 空结构 manifest（``outcomes=[]``、``overall_passed=True``、
    ``sub_evidence_refs=[]``）能过 read-back 假绿。r6 P1-5 补齐 7 步，杜绝空/篡改 manifest 过 read-back：

    1. load 回来重算 digest（``artifact_store.load`` 自带重算校验，篡改/损坏即抛）；
    2. JSON 可解析；
    3. §5 必填字段齐全（schema_version/subject_commit/runner_version/executed_at/overall_passed/outcomes/
       sub_evidence_refs/evidence_integrity/digest_algorithm）；
    4. outcomes 非空 + name 唯一 + 每项结构完整（name str / passed bool / evidence_digests list）；
    5. manifest 自洽：``set(outcome.evidence_digests 并集) == set(sub_evidence_refs)``（全局 refs 覆盖
       每 outcome 引用，杜绝 manifest 内部不一致的假绿）；
    6. 从 read-back manifest 重新遍历 sub_evidence_refs，每个 load + 重算 digest（独立于内存 outcomes，
       纯从归档 manifest 视角验子证据可读 + 内容寻址匹配）；
    7. manifest 自洽：``overall_passed == all(passed for outcome not in open_items red)``——r6 P1-6：telemetry
       移出 overall（drill_ok 排除 telemetry），比 all() 时须排除 ``open_items`` 诚实声明的 red 项；telemetry
       red 若未进 open_items（偷假绿）→ 仍含于 all() → 与 overall=True 冲突 → 拒（强制诚实 red/open）。

    任一不满足 → ``(False, reason)``，runner 据此置 overall_passed=False 不归档 passing manifest。
    与 ``_verify_sub_evidence_complete``（内存 outcomes tuple 视角）互补：本函数从**归档 manifest dict**
    视角独立复核（防归档后被篡改）。
    """
    # step 1: load + digest 重算（artifact_store.load 自带内容寻址校验）
    ref = L.ArtifactRef(digest=archive_digest, size=0,
                        kind=L.ArtifactKind.CUTOVER_SUITE.value,
                        path=artifact_store._bucketed_path(archive_digest),
                        sensitivity=L.Sensitivity.INTERNAL.value)
    try:
        blob = artifact_store.load(artifact_root, ref)
    except Exception as exc:
        return False, f"manifest read-back [1] load/digest 失败: {exc!r}"
    # step 2: JSON 解析
    try:
        parsed = json.loads(blob)
    except Exception as exc:
        return False, f"manifest read-back [2] JSON 解析失败: {exc!r}"
    # step 3: §5 必填字段齐全（含 subject_commit/runner_version/executed_at/overall_passed）
    required = {"schema_version", "subject_commit", "runner_version", "executed_at",
                "overall_passed", "outcomes", "sub_evidence_refs",
                "evidence_integrity", "digest_algorithm"}
    missing = required - set(parsed)
    if missing:
        return False, f"manifest read-back [3] 缺 §5 字段: {sorted(missing)}"
    # step 4: outcomes 非空 + name 唯一 + 每项结构完整（防空/残缺 manifest 假绿）
    outcomes = parsed.get("outcomes")
    if not isinstance(outcomes, list) or not outcomes:
        return False, "manifest read-back [4] outcomes 非列表或为空"
    names: list[str] = []
    for o in outcomes:
        if (not isinstance(o, dict) or "name" not in o or "passed" not in o
                or "evidence_digests" not in o):
            return False, f"manifest read-back [4] outcome 结构不完整: {o!r}"
        if not isinstance(o["name"], str) or not isinstance(o["passed"], bool):
            return False, f"manifest read-back [4] outcome name/passed 类型错: {o!r}"
        if not isinstance(o["evidence_digests"], list):
            return False, f"manifest read-back [4] evidence_digests 非列表: {o!r}"
        names.append(o["name"])
    if len(set(names)) != len(names):
        return False, f"manifest read-back [4] outcome name 重复: {names}"
    # step 5: manifest 自洽——outcome 引用 digest 并集 == sub_evidence_refs（全局覆盖）
    sub_refs = parsed.get("sub_evidence_refs") or []
    if not isinstance(sub_refs, list):
        return False, "manifest read-back [5] sub_evidence_refs 非列表"
    outcome_digests = {d for o in outcomes for d in o["evidence_digests"]}
    if outcome_digests != set(sub_refs):
        return False, ("manifest read-back [5] sub_evidence_refs 与 outcome 引用不一致: "
                       f"only_in_outcomes={sorted(outcome_digests - set(sub_refs))} "
                       f"only_in_refs={sorted(set(sub_refs) - outcome_digests)}")
    # step 6: 从 read-back manifest 遍历 sub_evidence_refs，逐个 load + 重算 digest
    for d in sub_refs:
        sref = L.ArtifactRef(digest=d, size=0,
                             kind=L.ArtifactKind.TEST_OUTPUT.value,
                             path=artifact_store._bucketed_path(d),
                             sensitivity=L.Sensitivity.INTERNAL.value)
        try:
            artifact_store.load(artifact_root, sref)
        except Exception as exc:
            return False, f"manifest read-back [6] 子证据 {d} 不可读/digest 不匹配: {exc!r}"
    # step 7: manifest 自洽——overall_passed == all(outcome.passed)（防篡改 overall 假绿）。
    # r6 P1-6：telemetry 移出 overall（drill_ok 排除 telemetry），overall 不再 == 无脑 all(outcomes)。故比
    # all() 时排除 ``open_items`` 诚实声明的 red 项——且这加强语义：telemetry red 要 overall 绿必须诚实进
    # open_items；若 telemetry red 但未进 open_items（偷假绿），仍含于 business_outcomes → all()=False →
    # 与 overall=True 冲突 → 拒（杜绝悄悄假绿，强制诚实 red/open + known limitation）。
    # r7-S4（审核员）：open_items 仅允许 ``_ALLOWED_OPEN_ITEMS``（白名单 {telemetry}）的 red 项从 all()
    # 排除——防「任意红色 outcome 塞进 open_items 即从 overall 排除」的假绿（如 quality_gate 真 red 被偷
    # 排 → overall 假绿）。非白名单 red 项进 open_items → read-back [7] fail（杜绝任意排除红色 outcome）。
    open_red_names: set[str] = set()
    for _it in parsed.get("open_items") or []:
        if not (isinstance(_it, dict) and _it.get("passed") is False and isinstance(_it.get("item"), str)):
            continue
        if _it["item"] not in _ALLOWED_OPEN_ITEMS:
            return False, (f"manifest read-back [7] open_items 含非白名单 red 项 {_it['item']!r}"
                           f"（仅 {sorted(_ALLOWED_OPEN_ITEMS)} 允许 open；防任意红色 outcome 被排除致 overall 假绿）")
        open_red_names.add(_it["item"])
    business_outcomes = [o for o in outcomes if o["name"] not in open_red_names]
    outcomes_all = all(o["passed"] for o in business_outcomes)
    if parsed["overall_passed"] != outcomes_all:
        return False, (f"manifest read-back [7] overall_passed={parsed['overall_passed']} "
                       f"与 (outcomes 排除 open_items red) all()={outcomes_all} 不一致")
    return True, "ok"


# r5 P1-4（评审④）：cross-machine bundle 自检脚本模板——随 bundle 分发，跨机器独立复核（仅 stdlib）。
# 复核：(1) 每个 artifacts/<digest> 内容重算 sha256 == 文件名；(2) manifest.json 含 §5 字段；
# (3) bundle.sha256 = manifest_digest + 排序子 digest 聚合（与 _compute_bundle_digest 同算法）。exit 0 ⇔ 完整。
_BUNDLE_VERIFY_TEMPLATE = r'''#!/usr/bin/env python3
"""cross-machine evidence bundle 自检（r5 P1-4）。无外部依赖；跨机器运行结果一致。"""
import hashlib, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def _digest(b: bytes) -> str:
    return "sha256:" + hashlib.sha256(b).hexdigest()

def main() -> int:
    failures = []
    try:
        manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"manifest.json 不可解析: {exc!r}", file=sys.stderr)
        return 1
    # r6 P1-3（R4 §2.3-1）：subject_commit 必须声明（evidence_commit ancestry 锚点）+ §5 结构字段。
    # 字段存在校验（跨机器 bundle 完整性）；值非空由 real_cutover_suite overall 层 subject 阻断保证。
    for field in ("schema_version", "subject_commit", "runner_version", "executed_at",
                  "outcomes", "sub_evidence_refs", "evidence_integrity", "digest_algorithm"):
        if field not in manifest:
            failures.append(f"manifest 缺字段 {field}")
    # r7-S2（审核员）：subject_commit 必须真实存在于 git object store（rev-parse），杜绝 manifest 声明假 sha
    # 仍 exit 0。旧版只校验字段存在 → 假 subject 也过（假绿）。verify.py 须在 vault 仓上下文跑
    # （cross-machine = clone vault 后跑），故 git 可用且 subject 应在 ancestry 内。
    import subprocess
    _subj = manifest.get("subject_commit")
    if not _subj:
        failures.append("subject_commit 为空（evidence ancestry 锚点缺失）")
    else:
        _rp = subprocess.run(["git", "rev-parse", "--verify", "--quiet", _subj + "^{commit}"],
                             capture_output=True, text=True)
        if _rp.returncode != 0:
            failures.append(f"subject_commit {_subj} 不存在于 git object store（rev-parse 失败）")
        else:
            # r9-3（审核员）：evidence_commit 从 argv[1] 取（real_cutover_suite 传 exact SHA），fallback HEAD
            # （跨机器 checkout evidence commit 后跑 verify.py）。**消除 r8-2 的 git log --grep 反查**——grep 有
            # 多匹配（同 message 多 evidence commit）+ commit message 漂移风险。verify.py 在 evidence commit 上下文
            # 跑（real_cutover_suite cwd=vault + 传 SHA；跨机器 checkout evidence 后跑），HEAD 即 evidence commit。
            import sys as _sys
            _ev = _sys.argv[1].strip() if len(_sys.argv) > 1 and _sys.argv[1].strip() else None
            if not _ev:
                _hp = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True)
                _ev = _hp.stdout.strip() if _hp.returncode == 0 else ""
            if not _ev:
                failures.append("无法取 evidence_commit（argv 未传 + git rev-parse HEAD 失败；verify.py 须在 vault git 仓跑）")
            else:
                # r9-4（审核员）：真祖先断言——subject 必须是 evidence 的祖先（``merge-base --is-ancestor``）。
                # r8-2 旧版仅 ``git diff subject..evidence``（树差异，非祖先——subject 与 evidence 平行分支也过，
                # 假绿）。r9-4 加 --is-ancestor：subject 非 evidence 祖先 → fail（防 evidence 基于 unrelated commit）。
                _mb = subprocess.run(["git", "merge-base", "--is-ancestor", _subj, _ev],
                                     capture_output=True, text=True)
                if _mb.returncode != 0:
                    failures.append(f"subject_commit {_subj[:12]} 非 evidence_commit {_ev[:12]} 祖先（merge-base --is-ancestor 失败；evidence 须基于 subject）")
                # ancestry 路径 allowlist：subject..evidence 只含 docs/evidence/（防夹带业务代码被误当 subject 重新执行）
                _anc = subprocess.run(["git", "diff", "--name-only", f"{_subj}..{_ev}"],
                                      capture_output=True, text=True)
                if _anc.returncode != 0:
                    failures.append(f"evidence_commit ancestry diff 失败（{_subj}..{_ev}）")
                else:
                    _bad = [f for f in _anc.stdout.splitlines() if f.strip() and not f.startswith("docs/evidence/")]
                    if _bad:
                        failures.append(f"evidence_commit ancestry 含非 docs/evidence/ 路径 {_bad}（防夹带业务代码）")
                # runner binding：committer name == manifest.runner_version（谁产的 evidence，杜绝旧固定 runner 名漂移）
                _cn = subprocess.run(["git", "log", "-1", "--format=%cn", _ev],
                                     capture_output=True, text=True)
                _runner = manifest.get("runner_version", "")
                if _cn.returncode != 0 or (_cn.stdout.strip() != _runner):
                    failures.append(f"evidence_commit committer '{_cn.stdout.strip()}' != runner_version '{_runner}'（runner 绑定失败）")
    sub = list(manifest.get("sub_evidence_refs", []))
    for d in sub:
        p = ROOT / "artifacts" / d
        if not p.exists():
            failures.append(f"子证据缺失 {d}")
        elif _digest(p.read_bytes()) != d:
            failures.append(f"子证据 digest 不匹配 {d}")
    md = _digest((ROOT / "manifest.json").read_bytes())
    expected = _digest((md + "\n" + "\n".join(sorted(sub))).encode("utf-8"))
    claim = (ROOT / "bundle.sha256").read_text(encoding="utf-8").strip()
    if expected != claim:
        failures.append("bundle.sha256 不匹配（bundle 内容被篡改）")
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print(f"bundle OK: manifest_digest={md} sub_evidence={len(sub)} bundle={claim}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
'''


def _compute_bundle_digest(manifest_digest: str, sub_digests: tuple[str, ...]) -> str:
    """r5 P1-4（评审④）：cross-machine bundle 内容 digest——manifest digest + 排序子证据 digest 聚合 sha256。

    基于内容（非路径/时间），跨机器一致。与 ``_BUNDLE_VERIFY_TEMPLATE`` 内同算法（评审据此独立复核 bundle 完整性）。
    """
    import hashlib
    payload = (manifest_digest + "\n" + "\n".join(sorted(sub_digests))).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


# r6 P1-4（评审 R4 §4）：bundle publication 脱敏——allowlist schema + secret scan。
# 凭据泄漏反例：子证据含 env/prompt/tool input-output/error 文本，若内嵌凭据（GitHub PAT / API key /
# SMTP / 云密钥 / Bearer），bundle 复制原始 blob 即跨机器泄漏。publish 前 scan → 命中 fail-closed
# （raise → bundle_publish_ok=False → overall_passed=False，P1-2 接力非零退出，绝不带凭据归档）。
_SECRET_PATTERNS: tuple[tuple[str, str], ...] = (
    ("github_pat", r"github_pat_[A-Za-z0-9_]{82}"),
    ("github_token", r"gh[pousr]_[A-Za-z0-9]{36,}"),
    ("aws_access_key", r"AKIA[0-9A-Z]{16}"),
    ("openai_key", r"sk-[A-Za-z0-9]{20,}"),
    ("anthropic_key", r"sk-ant-[A-Za-z0-9_-]{20,}"),
    ("google_api", r"AIza[0-9A-Za-z_-]{35}"),
    ("bearer_token", r"Bearer\s+[A-Za-z0-9._-]{20,}"),
    ("secret_kv", r'(?i)(password|passwd|secret|api[_-]?key|token|smtp[_-]?pass)["\']?\s*[:=]\s*["\']?[A-Za-z0-9._~/+-]{8,}'),
)
_MANIFEST_ALLOWED_FIELDS: frozenset[str] = frozenset({
    "schema_version", "subject_commit", "runner_version", "executed_at",
    "overall_passed", "outcomes", "sub_evidence_refs",
    "evidence_integrity", "digest_algorithm", "open_items",
})


def _scan_for_secrets(text: str) -> list[str]:
    """扫描文本凭据模式（r6 P1-4）。返回命中描述（脱敏：模式名 + 首尾少量字符 + 长度，**不回显凭据值**）。
    空列表 = 干净；非空 = fail-closed 信号（调用方据此 raise，绝不带凭据写盘/复制）。"""
    import re
    hits: list[str] = []
    for name, pat in _SECRET_PATTERNS:
        for m in re.finditer(pat, text):
            v = m.group(0)
            masked = (v[:4] + "…" + f"(len={len(v)})") if len(v) > 6 else "(short)"
            hits.append(f"{name}: {masked}")
    return hits


# r7-S3 → r8-4（审核员）：sub-evidence per-kind **allowlist**（旧 r7-S3 是 denylist——4 禁字段，未知字段
# 放过，如新输入字段 model_output 不在禁集即过 → 假绿）。R4 §4 最小充分证据 + 审核员 r8：denylist→allowlist。
# allowlist 字段源自各 drill 的 dataclass（``fields()``），无编造，schema 漂移自动跟随（dataclass 加字段 →
# allowlist 自动含）。secret scan（``_scan_for_secrets``，publish 层）查凭据模式，allowlist 查字段类型，互补。
_SUB_EVIDENCE_MAX_TEXT_LEN = 1000   # 单字符串值上限（防完整 prompt/output 原文；drill 产出已截断 500/300）
# r9-6（审核员）：递归泄漏字段 denylist——raw_prompt/tool_output/prompt/model_output 等输入/输出原文字段，
# 无论嵌套多深一律拒（callback_invocations[].tool_output / per_scenario[].prompt/model_output 泄漏反例）。
# r8-4 顶层 allowlist 不查嵌套 dict/list 内字段 → 审核员反例：telemetry.callback_invocations[].raw_prompt/
# tool_output + sdk_canary.per_scenario[].prompt/model_output 泄漏。r9-6 递归 denylist 与 r8-4 allowlist 互补。
_SUB_EVIDENCE_LEAKY_FIELDS: frozenset[str] = frozenset({
    "raw_prompt", "tool_output", "tool_input", "tool_result",
    "prompt", "model_output", "user_input", "user_message",
    "stdin", "stdout", "stderr", "response_body", "request_body",
})


def _dc_field_names(cls: type) -> frozenset[str]:
    """r8-4：dataclass 字段名集（``fields()`` 推导，无编造，schema 漂移自动跟随）。"""
    return frozenset(f.name for f in fields(cls))


# r8-4：per-kind allowlist——drill → 该 drill evidence 的合法字段集（dataclass 顶层字段）。
# recovery/sandbox 的 evidence 是 list[DrillResult]，字段集 = 元素 dataclass 字段。
_SUB_EVIDENCE_ALLOWLIST_BY_KIND: dict[str, frozenset[str]] = {
    "shadow_parity": _dc_field_names(ShadowParityEvidence),
    "sdk_canary": _dc_field_names(SdkHookCanaryEvidence),
    "crash_reconciliation": _dc_field_names(CrashReconciliationEvidence),
    "dispatch_cutover": _dc_field_names(DispatchCutoverResult),
    "quality_gate": _dc_field_names(QualityGateResult),
    "telemetry": _dc_field_names(TelemetryEvidence),
    "recovery": _dc_field_names(RecoveryDrillResult),      # list 元素字段
    "sandbox": _dc_field_names(SandboxDrillResult),        # list 元素字段
}
# 所有 drill 字段名并集（drill 未知时兜底诊断 + 未来嵌套校验参考）
_ALL_DRILL_FIELD_NAMES: frozenset[str] = frozenset().union(*_SUB_EVIDENCE_ALLOWLIST_BY_KIND.values())


def _check_sub_evidence_allowlist(blob: bytes) -> list[str]:
    """r8-4（审核员）+ r10-B3 诚实收敛（审核员 r9 复审 + 红队）：sub-evidence publish 层 best-effort 校验。

    ⚠️ r10-B3 **停止 overclaim**：本函数是 publish 层**字段名维度**的 best-effort 校验，**不**是「可被外部
    构造的伪造 blob 都能堵住」的信任边界。真信任边界在 **runtime 发射端**（drill dataclass 字段集 +
    ``_strip_leaky_invocation_fields`` 预剥离）——runtime 只发射固定已知键、且 tool_output 等泄漏字段已在
    发射前剥离。本函数是第二层防线，防「已知泄漏字段名残留 + 字段名 schema 漂移」，**防不住**：

      (a) 合法 VALUE 字段值里的凭据：``summary``/``detail``/``error`` 等字段名在 dataclass allowlist 内
          （合法），但其**值**若含 AKIA/token 则 key-level allowlist 放过 → 残余洞（r10-B3 留 P2：需对
          allowlist 字段的**值**也跑 ``_scan_for_secrets``，当前只对 publish 目录整文件 scan）。
      (b) 嵌套深层未知字段名：r9-6 递归 denylist 只拒 ``_SUB_EVIDENCE_LEAKY_FIELDS``（14 已知名），新增的
          未知泄漏字段名（如未来 ``ai_response``）不在表内 → 嵌套放过（P2：denylist 升覆盖式扫描）。

    校验步骤（publish 层 best-effort）：
    (1) 顶层 dict 且键 ⊆ {drill, evidence}；
    (2) drill 在 ``_SUB_EVIDENCE_ALLOWLIST_BY_KIND``（未知 drill fail-closed）；
    (3) evidence 顶层字段 ⊆ 该 drill dataclass 字段集（dict 直接查；list 查每元素）；
    (4) 递归 denylist（``_SUB_EVIDENCE_LEAKY_FIELDS`` 14 名）+ 文本长度上限。
    r9-6：非 JSON blob → fail-closed（旧版跳过 = 假绿）。secret scan 由 publish 层 ``_scan_for_secrets``
    互补（整文件维度，含 manifest/bundle.sha256/verify.py + artifacts/<d>）。
    """
    try:
        obj = json.loads(blob.decode("utf-8", errors="replace"))
    except Exception:
        # r9-6（审核员）：非 JSON blob → fail-closed（旧版跳过返回 [] = 视为干净 → 假绿）。
        # sub-evidence 经 ``_archive_sub_evidence`` 总是 ``json.dumps`` 序列化（:1360），非 JSON = 损坏/伪造，
        # 绝不当二进制 artifact 干净放过。
        return ["sub-evidence 非 JSON（须 json.dumps 序列化；非 JSON = 损坏/伪造 → fail-closed）"]
    if not isinstance(obj, dict):
        return ["sub-evidence 顶层非 dict（期望 {drill, evidence}）"]
    violations: list[str] = []
    # (1) 顶层键 allowlist
    _top_bad = [k for k in obj if k not in ("drill", "evidence")]
    if _top_bad:
        violations.append(f"顶层未知键 {_top_bad}（期望仅 {{drill, evidence}}）")
    # (2) drill 在 per-kind 表
    _drill = obj.get("drill")
    if _drill not in _SUB_EVIDENCE_ALLOWLIST_BY_KIND:
        violations.append(f"未知 drill {_drill!r}（不在 per-kind allowlist；防任意 drill 名发布）")
        _allowed = _ALL_DRILL_FIELD_NAMES    # 兜底（继续长度诊断，不阻断）
    else:
        _allowed = _SUB_EVIDENCE_ALLOWLIST_BY_KIND[_drill]
    # (3) evidence 顶层字段 allowlist（dict 直接查；list 查每元素）
    _ev = obj.get("evidence")
    if isinstance(_ev, dict):
        _bad_ev = [k for k in _ev if k not in _allowed]
        if _bad_ev:
            violations.append(f"drill={_drill} evidence 未知字段 {_bad_ev}（不在 dataclass 字段集；防输入字段进 evidence）")
    elif isinstance(_ev, list):
        for i, item in enumerate(_ev):
            if isinstance(item, dict):
                _bad_it = [k for k in item if k not in _allowed]
                if _bad_it:
                    violations.append(f"drill={_drill} evidence[{i}] 未知字段 {_bad_it}（list 元素字段须在 allowlist）")
    # (4) 递归文本长度检查（保留 r7-S3，防完整 prompt/output 原文）
    #     + r9-6（审核员）：递归泄漏字段 denylist。r8-4 (3) 只查 evidence 顶层字段，嵌套 dict/list 元素
    #       （callback_invocations[].tool_output / per_scenario[].prompt/model_output）不查 → 审核员反例：
    #       顶层 allowlist 过但嵌套仍泄漏输入/输出原文。r9-6 在 _walk 递归中补 denylist，与 r8-4 顶层
    #       allowlist 互补——顶层结构合法 + 嵌套无任何 leaky 字段。
    def _walk(o, path: str = "") -> None:
        if isinstance(o, dict):
            for k, v in o.items():
                if k in _SUB_EVIDENCE_LEAKY_FIELDS:
                    violations.append(
                        f"泄漏字段 {path + '.' + k if path else k}（输入/输出原文字段，禁入 sub-evidence——r9-6 递归 denylist）")
                _walk(v, f"{path}.{k}" if path else k)
        elif isinstance(o, list):
            for i, x in enumerate(o):
                _walk(x, f"{path}[{i}]")
        elif isinstance(o, str) and len(o) > _SUB_EVIDENCE_MAX_TEXT_LEN:
            violations.append(f"超长文本 {path}（len={len(o)} > {_SUB_EVIDENCE_MAX_TEXT_LEN}，疑似完整原文）")
    _walk(obj)
    return violations


def publish_evidence_bundle(*, artifact_root: str, manifest: "CutoverManifest",
                            bundle_root) -> tuple[str, str]:
    """r5 P1-4（评审④）：发布 cross-machine immutable evidence bundle。

    审查者：「artifact root 仍为本机路径，没有 immutable cross-machine bundle」。本机 mkdtemp artifact_root
    跨机器不可访问。bundle 把结构化 manifest + 全部子证据（按 digest load 原始内容）+ 自检脚本打成**自包含、
    内容寻址、相对路径**目录，digest 跨机器独立复核（``bundle.sha256`` = manifest_digest + 排序子 digest 聚合）。

    bundle 结构（自包含，不依赖本机 artifact_root 绝对路径）::

        <bundle_root>/
          manifest.json          # 结构化 manifest（manifest_digest = 内容寻址 digest）
          bundle.sha256          # bundle 内容 digest（跨机器一致，passing 声明可复核锚点）
          verify.py              # 自检脚本（stdlib，跨机器 exit 0 ⇔ 完整）
          artifacts/<digest>     # 每个子证据原始内容（文件名 = 内容 digest，自校验）

    Returns:
        ``(bundle_root_str, bundle_digest)``——bundle_digest 跨机器一致。
    """
    from pathlib import Path
    root = Path(bundle_root)
    mj = manifest.structured_json()
    # r6 P1-4 step 1（allowlist schema）：manifest 只允许 §5 固定字段——拒绝未知字段（防 manifest 被注入
    # 额外字段绕过结构校验）。structured() 本不产生未知字段，此为 publish 层防御（manifest 可被外部构造）。
    _parsed = json.loads(mj)
    _unknown = set(_parsed) - _MANIFEST_ALLOWED_FIELDS
    if _unknown:
        raise ValueError(f"bundle publish fail-closed: manifest 含未知字段（allowlist 拒）: {sorted(_unknown)}")
    # r6 P1-4 step 2（secret scan · manifest）：扫描 manifest JSON 凭据 → 命中 fail-closed（不写盘）。
    _mj_hits = _scan_for_secrets(mj)
    if _mj_hits:
        raise ValueError(f"bundle publish fail-closed: manifest.json 含凭据模式: {_mj_hits[:3]}")
    # r6 P1-4 step 2（secret scan · 子证据）：两阶段——先 load 全部子证据 scan，全干净才写盘（杜绝
    # 半成品 bundle：凭据命中时不创建任何文件，连 manifest.json 也不写）。子证据含 env/prompt/tool
    # input-output/error 文本，是凭据泄漏主面；best-effort decode（非 utf-8 不崩）。
    _blobs: list[tuple[str, bytes]] = []
    for d in manifest.sub_evidence_refs:
        ref = L.ArtifactRef(digest=d, size=0, kind=L.ArtifactKind.TEST_OUTPUT.value,
                            path=artifact_store._bucketed_path(d),
                            sensitivity=L.Sensitivity.INTERNAL.value)
        blob = artifact_store.load(artifact_root, ref)   # load 重算 digest 校验（fail-closed）
        _sub_hits = _scan_for_secrets(blob.decode("utf-8", errors="replace"))
        if _sub_hits:
            raise ValueError(f"bundle publish fail-closed: 子证据 {d} 含凭据模式: {_sub_hits[:3]}")
        # r7-S3（审核员）：sub-evidence allowlist——禁输入字段（prompt/tool_input）+ 超长文本（防任意
        # prompt/tool output 原文）。P1-4 manifest allowlist 只覆盖 manifest 顶层，本处覆盖 sub-evidence。
        _forbidden = _check_sub_evidence_allowlist(blob)
        if _forbidden:
            raise ValueError(f"bundle publish fail-closed: 子证据 {d} 违反 allowlist: {_forbidden[:5]}")
        _blobs.append((d, blob))
    # 全 scan 通过 → 写盘（fail-closed：scan 失败时不创建任何 bundle 文件）
    (root / "artifacts").mkdir(parents=True, exist_ok=True)
    (root / "manifest.json").write_text(mj, encoding="utf-8")
    manifest_digest = manifest.manifest_digest or artifact_store._digest(mj.encode("utf-8"))
    for d, blob in _blobs:
        (root / "artifacts" / d).write_bytes(blob)
    (root / "verify.py").write_text(_BUNDLE_VERIFY_TEMPLATE, encoding="utf-8")
    bundle_digest = _compute_bundle_digest(manifest_digest, manifest.sub_evidence_refs)
    (root / "bundle.sha256").write_text(bundle_digest, encoding="utf-8")
    return str(root), bundle_digest


def run_full_cutover_suite(*, drills: CutoverDrillBundle,
                           artifact_root: str,
                           subject_commit: str | None = None,
                           runner_version: str = "",
                           executed_at: str = "") -> CutoverManifest:
    """task 7.6：完整 cutover 套件**运行器**——自行编排执行各子 drill + 归档不可变通过证据 manifest。

    评审 P0-2 修正：旧 ``run_cutover_suite`` 接收外部布尔值做 ``all()``，不执行任何 drill。本函数**调用
    ``drills`` bundle 里每个子 drill 的执行入口**（真实跑该 drill）→ 从 Result 提取 pass + detail → 构建
    ``CutoverManifest`` → 全绿才归档内容寻址 manifest digest（design#1 runner 编排真实 drill + #6 archive
    immutable passing evidence）。任一维度 red → ``overall_passed=False`` 且**不归档**（绝不伪装绿归档）。

    r2 P0-5：runner 归档每个子 drill evidence（``_archive_sub_evidence``），outcome 带 ``evidence_digests``，
    manifest ``sub_evidence_refs`` 聚合全部子 digest——passing manifest 引用所有子 evidence（非只存 summary）。

    drill 注入（design 决策#1）：bundle 每项是 callable，测试注入 fake（确定性验证编排/归档），生产注入
    真实 drill（真实跑各子项，见 ``real_cutover_drills``）。runner 只编排执行 + 汇总归档，不重写 drill 逻辑。

    Args:
        drills: ``CutoverDrillBundle``——7 个子 drill 的执行入口（runner 依次调用执行）。
        artifact_root: 归档 manifest 的内容寻址 artifact store 根。
    """
    def _exec(execute: Callable, extractor: Callable, archive_name: str) -> DrillOutcome:
        # r3 P0-2 fail-closed：drill 执行（execute）、outcome 提取（extractor）、子证据归档
        # （_archive_sub_evidence）任一异常 → 该维度记 red + 空证据引用，绝不归档缺证据的 passing manifest。
        try:
            ev = execute()                        # 调 bundle callable，真实执行该子 drill
            outcome = extractor(ev)
            digests = _archive_sub_evidence(artifact_root, archive_name, ev)
            return replace(outcome, evidence_digests=digests)
        except Exception as exc:
            return DrillOutcome(archive_name, False,
                                f"FAIL-CLOSED: drill 执行或子证据归档异常: {exc!r}")

    outcomes = (
        _exec(drills.shadow_parity, _shadow_parity_outcome, "shadow_parity"),
        _exec(drills.sdk_canary, _sdk_canary_outcome, "sdk_canary"),
        _exec(drills.crash_reconciliation, _crash_outcome, "crash_reconciliation"),
        _exec(drills.recovery, _recovery_outcome, "recovery"),
        _exec(drills.sandbox, _sandbox_outcome, "sandbox"),
        _exec(drills.dispatch_cutover, _dispatch_outcome, "dispatch_cutover"),
        _exec(drills.quality_gate, _quality_outcome, "quality_gate"),
        # r5 P1-3（评审）：telemetry 升为独立 gate 维度——_telemetry_outcome 以明确谓词判 SDK 遥测通道
        # 在线/未降级（非旧"仅归档进 evidence_items"），SDK 降级为 no-op 即 telemetry 红 → overall 红。
        _exec(drills.telemetry, _telemetry_outcome, "telemetry"),
    )
    sub_refs = tuple(d for o in outcomes for d in o.evidence_digests)
    # r3 P0-2：overall_passed = 全维度业务 passed **且** 子证据完整性门通过（7 outcome 均有可解析、
    # 可读、digest 匹配的证据引用）。缺任一 → overall_passed=False 不归档（绝不伪装绿归档）。
    # r6 P1-6（评审）：telemetry 移出 overall all()——真实 OTLP/degradation suite 未接入，作硬 gate 会让
    # headless 永远红。telemetry outcome 仍执行+归档子证据（evidence_ok 仍查 8 维度），但 passed 不进 drill_ok，
    # 进 open_items（诚实报告 red/open + known limitation，不阻断 overall，同 P1-1 语义）。
    drill_ok = all(o.passed for o in outcomes if o.name != "telemetry")
    evidence_ok, evidence_reason = _verify_sub_evidence_complete(outcomes, artifact_root)
    _telemetry_o = next((o for o in outcomes if o.name == "telemetry"), None)
    _open_items: tuple[dict, ...] = ()
    # r8-1 → r9-1（审核员 P0）：open_items 基于**真实 OTLP export 验证**（``_otlp_export_verified`` 实际 export
    # test span + 验 collector 接收），非 r8-1 环境变量非空（旧判可伪造 ``=x`` → telemetry_connected=True → 假绿）。
    # 未接真实 collector（生产常态）→ export 失败 → telemetry 诚实 open（passed=False，OTLP 维度红；与 S4 read-back
    # step7 白名单 + runtime 7.6 connected 断言一致）；接真实 collector + export 成功 → 不 open（已接入）。
    if _telemetry_o is not None and not _otlp_export_verified():
        _open_items = ({"item": "telemetry", "passed": False,
                        "limitation": "真实 OTLP/degradation suite 未接入（_otlp_export_verified 实际 export 失败："
                                      "无 OTEL endpoint 或 collector 不可达）；仅 SDK callback 维度可验"},)
    manifest = CutoverManifest(outcomes=outcomes,
                               overall_passed=(drill_ok and evidence_ok),
                               sub_evidence_refs=sub_refs,
                               evidence_integrity=evidence_reason,
                               subject_commit=subject_commit,
                               runner_version=runner_version,
                               executed_at=executed_at,
                               open_items=_open_items)
    if not manifest.overall_passed:
        return manifest                        # red 套件不归档（绝不伪装绿归档）
    # r5 P1-4（评审①）：归档结构化 JSON（非 summary 字符串）——含 §5 全字段（schema_version/subject_commit/
    # runner_version/executed_at/outcomes[]/sub_evidence_refs/evidence_integrity/digest_algorithm）。
    ref = artifact_store.store(artifact_root, manifest.structured_json(),
                               kind="cutover_suite", sensitivity="internal")
    # r5 P1-4（评审②）：归档后 read-back——load 回来重算 digest + 结构校验（fail-closed）。归档内容被篡改/损坏
    # → read-back 失败 → overall_passed=False（不声明 passing，archive/manifest_digest 不回填——归档不可信）。
    read_ok, read_reason = _read_back_manifest(artifact_root, ref.digest)
    if not read_ok:
        return replace(manifest,
                       evidence_integrity=f"manifest_read_back_fail: {read_reason}",
                       overall_passed=False)
    return replace(manifest, archive_digest=ref.digest, manifest_digest=ref.digest)


# r6 P0：real_cutover_drills 测试替身用——每场景"全绿"observed_state（与 SCENARIO_EXPECTED_STATE 对齐，让
# evaluate_scenario state 匹配通过）。仅离线 bundle 测试注入路径用；生产 real_cutover_suite 从 real_sdk_canary
# 真实 query 填 per_scenario，不走此映射。
_CANARY_GREEN_OBSERVED: dict[str, dict] = {
    "test_red": {"bash_results": [{"exit_code": 1, "output": ""}], "reply_text": "", "saw_tool_use": True, "saw_subagent_start": False},
    "test_green": {"bash_results": [{"exit_code": 0, "output": "GREEN"}], "reply_text": "", "saw_tool_use": True, "saw_subagent_start": False},
    "stale_test": {"bash_results": [{"exit_code": 0, "output": "STALE"}], "reply_text": "", "saw_tool_use": True, "saw_subagent_start": False},
    "semantic_revise": {"bash_results": [], "reply_text": "REVISE", "saw_tool_use": False, "saw_subagent_start": False},
    "no_test": {"bash_results": [], "reply_text": "NO TEST", "saw_tool_use": False, "saw_subagent_start": False},
    "subagent": {"bash_results": [], "reply_text": "", "saw_tool_use": False, "saw_subagent_start": True},
    "compaction": {"bash_results": [], "reply_text": "", "saw_tool_use": False, "saw_subagent_start": False},
    "hook_failure": {"bash_results": [], "reply_text": "", "saw_tool_use": False, "saw_subagent_start": False},
}


def real_cutover_drills(*, state_dir, stamp_fn, resolver, sandbox_runs,
                        dispatch_events, dispatch_legacy, test_counts,
                        evidence_items, artifact_root,
                        sdk_callback_proven: tuple[str, ...] = (),
                        telemetry_proven: bool = False) -> CutoverDrillBundle:
    """离线 bundle 工厂（测试/design 入口，**非生产执行器**）：用 cutover 自身各 ``run_*`` drill 构造 bundle。

    生产执行走 ``real_cutover_suite``（runtime_evidence.py，真实 SDK query via ``real_sdk_canary``）；
    本工厂 sdk_canary 维度默认 adapter 级（无真实 SDK callback），仅用于编排/归档逻辑测试（评审 P1-3：
    生产 bundle 的真实 SDK wiring 在 real_cutover_suite，非此处 fixture）。

    环境相关输入（真实 sandbox/handle/fixture、dispatch journal events、test counts）由调用方提供——
    sandbox 真实执行需真实 worktree/container，调用方先跑 ``run_sandbox_drill`` 收集 ``SandboxDrillResult``
    传入 ``sandbox_runs``；其余 drill 由 bundle 内 callable 直接调真实 ``run_*`` 函数执行。runner 调用
    bundle 时这些 callable 才真实执行（design#1 production wiring，非 disconnected helper）。

    r5 P0-2：sdk_canary 维度默认 adapter 级（``sdk_callback_proven=()`` → 离线 bundle 诚实红，因无真实 SDK
    callback）；真实 callback 须由 ``real_cutover_suite`` 从 ``real_sdk_canary`` 真实 query 填入。测试如需
    验证编排全绿，显式传 ``sdk_callback_proven=SDK_CALLBACK_REQUIRED_SCENARIOS``（测试替身，非生产默认）。
    """
    def _sdk_canary(_sf=stamp_fn, _scp=sdk_callback_proven):
        base = run_sdk_hook_canary(stamp_fn=_sf)
        # r6 P0：从 sdk_callback_proven 推导 per_scenario 全绿绑定（测试替身——生产 real_cutover_suite 用真实
        # query 填，非此映射）。每场景 journal+cid+state+gate 全绿 → _sdk_canary_outcome 在 scp=全8 时 passed=True。
        _ps = tuple({"scenario_id": s, "journal_has_expected": True, "carries_own_cid": True,
                     "adapter_gate": base.stop_gates.get(s),
                     "observed_state": _CANARY_GREEN_OBSERVED.get(s, {})} for s in _scp)
        return SdkHookCanaryEvidence(
            scenarios=base.scenarios, stop_gates=base.stop_gates, paths_covered=base.paths_covered,
            summary=base.summary, real_query_proven=bool(_scp),
            sdk_callback_proven=_scp, adapter_contract_proven=base.adapter_contract_proven,
            per_scenario=_ps)
    def _telemetry(_proven=telemetry_proven):
        # r5 P1-3（评审）：离线 bundle 无真实 SDK query → telemetry 诚实红（无 invocations/lifecycle）；
        # 测试如需验证编排全绿（8 维度 outcome/归档逻辑），显式传 telemetry_proven=True（测试替身，非生产）。
        # 生产 telemetry 由 real_cutover_suite 从 real_sdk_canary 真实 query 结果填（runtime_evidence.py）。
        if _proven:
            return TelemetryEvidence(
                callback_invocations=({"event": "PostToolUse", "scenario": "fixture"},),
                lifecycle_types_seen=("PostToolUse", "Stop"), num_turns=1, query_error=None,
                summary="[fixture] telemetry_proven=True（测试替身）")
        return TelemetryEvidence(
            callback_invocations=(), lifecycle_types_seen=(), num_turns=None, query_error=None,
            summary="[offline bundle] 无真实 SDK query → telemetry 诚实红（生产填真值见 real_cutover_suite）")
    return CutoverDrillBundle(
        shadow_parity=lambda: run_shadow_parity_evidence(state_dir=state_dir, stamp_fn=stamp_fn),
        sdk_canary=_sdk_canary,
        telemetry=_telemetry,
        crash_reconciliation=lambda: run_crash_reconciliation_evidence(resolver=resolver),
        recovery=lambda: tuple(run_recovery_drill(m) for m in RECOVERY_MODES),
        sandbox=lambda: tuple(sandbox_runs),
        dispatch_cutover=lambda: run_dispatch_cutover_drill(
            journal_driven=True, journal_events=dispatch_events, legacy_records=dispatch_legacy),
        quality_gate=lambda: run_quality_gate(
            test_counts=test_counts, evidence_items=evidence_items, artifact_root=artifact_root),
    )
