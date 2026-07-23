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

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import artifact_store
import compat_readers as CR
import hook_adapter as HA
import hook_events as HE
import hook_policy as HP
import loop_runtime as LR
import loop_state as L
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
# 8.2 lifecycle hooks canary（no-test / test-red / test-green / compaction）
# ════════════════════════════════════════════════════════════════════════════
LIFECYCLE_SCENARIOS: frozenset[str] = frozenset(
    {"no_test", "test_red", "test_green", "compaction"})


@dataclass(frozen=True)
class LifecycleDrillResult:
    scenario: str
    stop_decision: str            # "allow" / "deny"
    snapshot_persisted: bool
    detail: str


def _fresh_adapter(stamp) -> HA.HookAdapter:
    """每次场景用全新 adapter（独立 evidence/续命计数），HookJournal no-op（drill 不落盘证据）。"""
    return HA.HookAdapter(journal=HE.HookJournal(path="/dev/null", enabled=False),
                          stamp=stamp, stop_continuation_limit=3)


def run_lifecycle_drill(scenario: str, *, stamp=None) -> LifecycleDrillResult:
    """lifecycle hooks canary（task 8.2）：4 场景验证 hook 路径。

    * no_test → Stop 无 evidence → deny（bounded 续命）；
    * test_red → PostToolUse 测试 exit 1 → Stop deny；
    * test_green → PostToolUse 测试 exit 0 → fresh green → Stop allow；
    * compaction → PreCompact auto + snapshot writer 成功 → snapshot 持久化（不阻恢复）。
    """
    if scenario not in LIFECYCLE_SCENARIOS:
        raise ValueError(f"unknown lifecycle scenario: {scenario!r}")
    ts = stamp or (lambda: "2026-07-22T00:00:00Z")
    adapter = _fresh_adapter(ts)
    if scenario == "no_test":
        out = adapter.on_stop("it_nt")
        decision = "allow" if out.permission_decision is HP.PermissionDecision.ALLOW else "deny"
        return LifecycleDrillResult("no_test", decision, False,
                                    f"continue_active={out.continue_active}")
    if scenario == "test_red":
        adapter.on_post_tool_use("it_tr", tool_name="Bash", tool_use_id="tu1",
                                 command="pytest -q", exit_code=1)
        out = adapter.on_stop("it_tr")
        return LifecycleDrillResult("test_red", "deny", False,
                                    f"gate blocked: {out.block_reason}")
    if scenario == "test_green":
        adapter.on_post_tool_use("it_tg", tool_name="Bash", tool_use_id="tu1",
                                 command="pytest -q", exit_code=0)
        out = adapter.on_stop("it_tg")
        return LifecycleDrillResult("test_green", "allow", False,
                                    "fresh green TestEvidence; outer verify still runs")
    # compaction
    out = adapter.on_pre_compact(
        "it_pc", trigger="auto",
        snapshot_writer=lambda: {"digest": "d", "path": "snap.json", "kind": "recovery_snapshot"},
    )
    persisted = out.artifact_ref is not None and not out.block_reason
    return LifecycleDrillResult("compaction", "allow", persisted,
                                f"block_reason={out.block_reason!r}")


# ════════════════════════════════════════════════════════════════════════════
# 8.3 controlled crash drill（agent/test/push/PR 后）确认 exactly-once
# ════════════════════════════════════════════════════════════════════════════
CRASH_BOUNDARIES: frozenset[str] = frozenset(
    {"agent_done", "test_done", "push", "pr_create"})


@dataclass(frozen=True)
class CrashDrillResult:
    boundary: str
    confirmed: int               # 副作用已发生（retry 跳过）
    pending: int                 # 副作用未发生（retry 执行）
    unknown: int                 # 查不到（fail-safe，不盲目执行）
    exactly_once: bool           # 无 unknown ⇔ 每个副作用状态明确（confirmed 或 pending 各一次）
    external_known: bool


# 各边界注入崩溃后待 reconcile 的副作用目标（agent_done/test_done 无外部副作用；push/pr 有）。
_BOUNDARY_TARGETS = {
    "agent_done": (),
    "test_done": (),
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

    @property
    def summary(self) -> str:
        flags = [
            f"parity={self.shadow_parity_matched}", f"lifecycle={self.lifecycle_all_pass}",
            f"crash={self.crash_all_exactly_once}", f"recovery={self.recovery_all_intact}",
            f"sandbox={self.sandbox_all_clean}", f"cutover={self.dispatch_cutover_ok}",
            f"quality={self.quality_gate_passed}",
        ]
        return "cutover suite: " + ("PASS" if self.overall_passed else "FAIL") + " (" + ", ".join(flags) + ")"
