#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""coordinator.py — task 2.1 production runtime coordinator（OpenSpec complete-durable-loop-runtime-integration）。

design 决策#1：把 journal/artifacts/IDs/retry/hooks/sandbox/telemetry/reconciliation 收敛到一个
coordinator 边界——``dispatch_one`` 与 ``dev-agent`` 共用，**一次解析所有 loop flag**，集中 own 运行时
设施，不再散建 ``_run``/``_prd``/``_iter``/``_sj``（spec durable-runtime-integration「Production runtime
coordinator」两个 scenario）。

职责分层：
  * **task 2.1 骨架（本文件）**：flags 一次解析（冻结快照）+ 稳定 IDs（``loop_ids`` 单一源头）+ own
    ``ShadowJournal`` + own artifact store 根 + lifecycle emit（委托 journal）+ iteration 衍生。
  * **后续 task 挂载**：hooks（task 2.3，从 ``coord.flags.lifecycle_hooks``）/ sandbox（Section 5）/
    telemetry（Section 6）/ retry·reconciliation（Section 3-4）的 adapter 都从 ``coord.flags`` 读 flag——
    coordinator 是唯一 resolve 点，adapter 不再各自 ``resolve_flags``（design「production code must not
    call them as disconnected helpers」）。

baseline 保留（spec「Disabled runtime preserves baseline」）：flags 全关 → ``is_baseline`` True，
``journal.enabled=False``（``ShadowJournal`` no-op），emit 不落盘、不悄悄触发任何 partial durable 功能，
dispatch first-phase 决策零变化。

DI（design 决策#6）：``stamp_fn``/``env``/``profile`` 注入，单测确定可复现；生产传 ``_now_iso`` /
``os.environ``。纯 stdlib，cron 隔离友好。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import ids as loop_ids
import otlp_export as OTLP
import telemetry as TE
import trace_context as TC
from artifact_store import compute_digest
from feature_flags import LoopFlags, resolve_flags
from loop_runtime import ShadowJournal
import retry_policy as RP            # task 2.1：coordinator own retry budget（session_aware_retry 开才构造）
from session_meta import SessionStore  # task 2.1：coordinator own SDK session 真源（resume/fork/new-session 决策消费）


def _real_stamp() -> str:
    """默认时间戳函数（生产用法）：UTC ISO8601（``Z`` 结尾）。测试注入固定函数免系统时间耦合。"""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class Coordinator:
    """task 2.1：一次 dispatch 的运行时协调器边界（design 决策#1）。

    集中 own：flags（一次解析快照）/ IDs（run·prd·iteration）/ journal（``ShadowJournal``）/
    artifact_root（内容寻址工件存储根）/ ``prd_digest``（task 3.1 dispatch entry 捕获的 PRD 内容 digest）。lifecycle 经 ``emit`` 委托 journal 落盘；新 iteration 经
    ``next_iteration`` 衍生（parent run/prd + seq，task 3.3 revise/resume/fork 用）。

    flags 全关 → ``is_baseline`` True，``emit`` no-op，dispatch first-phase 决策零变化。
    """
    __test__ = False

    flags: LoopFlags
    run_id: str
    prd_id: str
    iteration_id: str               # 初始 iteration（seq=0）；next_iteration(seq) 衍生后续
    journal: ShadowJournal
    artifact_root: str
    trace: TC.TraceContext               # task 6.1：root trace（per PRD run，trace_id 由 run_id 派生，metadata-only）
    telemetry_sink: OTLP.TelemetrySink   # task 6.2：OTLP export + degradation journaling（enabled 从 telemetry_export flag）
    prd_digest: str | None = None        # task 3.1：dispatch entry PRD 内容 digest（``sha256:<hex>``；None=未捕获）
    circuit_key: str = ""                # task 4.4 fix：跨 cron 稳定熔断键 = prd_id(stable_slug, prd_digest)（slug-based，
                                         #   非 path-based prd_id——prd_path 含 stamp 跨 cron 变，旧键致熔断跨 cron 失效）
    # task 2.1：own retry·session·reconciliation（design 决策#1：coordinator 集中 own 所有运行时关注点，
    #   production code 不再 disconnected helper 式各自 resolve）。评审 P0-1：曾散建、coordinator 未持有
    #   retry/session/reconcile，导致 recover_iteration / publication 前对账无法从 coordinator 单点派生。
    #   ``session_aware_retry`` 开 → 构造 budget/session_store（resume/fork/new-session 决策消费）；
    #   关 → None（baseline 零变化，dispatch 决策不受影响）。``resolver`` 始终注入持有（被动对象，持有无害；
    #   publication/retry 前对账才查），满足 design「coordinator 是 reconcile 唯一 resolve 点」。
    retry_budget: RP.BudgetState | None = None     # retry 预算（sdk_retries_used 计数 + limits；session_aware_retry 开构造）
    session_store: SessionStore | None = None      # SDK session 真源（resume/fork/new-session 决策 + dev-agent 持久化消费）
    resolver: object | None = None                 # reconcile KeyResolver（publication/retry 前对账 commit/push/pr/test 幂等键）

    @property
    def is_baseline(self) -> bool:
        """flags 全关 → True（第一阶段 baseline，dispatch 决策零变化；spec「Disabled runtime」）。

        add-cross-prd-learning-memory task 1.3a/1.3b：cross_prd_learning_shadow / cross_prd_learning_injection
        开 → is_baseline=False（learning memory shadow/injection 是非常规 baseline；shadow 开后 terminal 学习
        步骤会跑 read-only reflection + 投射 catalog）。
        """
        return not any((
            self.flags.journal_shadow, self.flags.journal_driven_dispatch,
            self.flags.session_aware_retry, self.flags.lifecycle_hooks,
            self.flags.container_sandbox, self.flags.telemetry_export,
            self.flags.cross_prd_learning_shadow, self.flags.cross_prd_learning_injection,
        ))

    @property
    def assurance_tier(self) -> str:
        """task 6.3：configured assurance tier（``flags.container_sandbox`` → 'container' else 'local'）。

        report 元数据用 configured intent（实际 used tier 经 sandbox preflight，task 5.1）；coordinator 从
        flags 派生 configured 值（design 决策#1：coord 是元数据唯一来源）。
        """
        return "container" if self.flags.container_sandbox else "local"

    @property
    def journal_authority(self) -> str:
        """task 6.3：journal 权威阶段（decision#2 cutover）：
        ``journal_driven_dispatch`` → 'driven'；``journal_shadow`` → 'shadow'；baseline → 'legacy'。
        """
        if self.flags.journal_driven_dispatch:
            return "driven"
        if self.flags.journal_shadow:
            return "shadow"
        return "legacy"

    def emit(self, event_type: str, payload: dict | None = None) -> str | None:
        """委托 journal emit lifecycle 事件（shadow 语义：flag 关 no-op，不改决策；返回值不得驱动控制流）。

        用 coordinator 的 ``iteration_id``/``prd_id``——调用方无需再传（消除散建
        ``_sj.emit(type, _iter, _prd, ...)``，统一从 coordinator 发）。
        """
        return self.journal.emit(event_type, self.iteration_id, self.prd_id, payload=payload)

    def next_iteration(self, seq: int) -> str:
        """衍生 distinct deterministic iteration ID（parent run/prd + seq）。

        task 3.3「distinct deterministic iteration ID for every revise/resume/fork/new-session」的入口；
        每次 verify revise/recovery 用新 seq 调此方法得新 iteration，引用 parent run/prd。
        """
        return loop_ids.iteration_id(self.run_id, self.prd_id, seq)

    def child_span(self, operation: str, seq: int = 0) -> TC.TraceContext:
        """task 6.1：派生 operation 子 span context（propagation through operations）。

        design 决策#1：telemetry 从 coordinator 派生，非 disconnected helper。``operation`` ∈ 子 span 名
        （iteration/sdk_session/tool/test/verify/reconcile/publish）；``run`` 是 root（不作为 child）；
        非法 → ValueError。trace_id 不变（同 run 同 trace），parent_span_id 指向 root span。

        生产 dispatch/dev-agent 各 operation（iteration/SDK session/tool/test/verify/reconcile/publish）
        调此方法得子 span context，记录 telemetry（task 6.2 export 经 TelemetrySink）。
        """
        if operation not in _CHILD_SPAN_OPS:
            raise ValueError(
                f"illegal child span operation (allowed: {sorted(_CHILD_SPAN_OPS)}): {operation!r}")
        return self.trace.child(operation, seq)

    def emit_telemetry_span(self, operation: str, *, seq: int = 0,
                            attributes=None, start_ms: int = 0, end_ms: int = 0,
                            status: str = "ok", links: tuple[str, ...] = ()) -> None:
        """task 6.2：派生 child span → emit 到 sink（metadata-only attributes）。

        design 决策#1：telemetry 从 coord 发，非 disconnected helper。``operation`` 经 ``child_span`` 派生
        （propagation：trace_id 不变，parent 指向 root span）；attributes 经 ``make_span`` 内
        ``sanitize_attributes`` 仅留 metadata（allowlist key + secret value 抹，design L80 绝不记
        prompt/source/secret）。flag 关 → ``sink.emit_span`` no-op。

        生产 dispatch/dev-agent 各 operation 完成后调此方法记录 span；``seq`` 区分同 operation 多次
        （多次 test → 不同 span_id）。
        """
        child = self.child_span(operation, seq=seq)     # propagation（非法 operation → ValueError）
        span = TE.make_span(
            operation, trace_id=child.trace_id, span_id=child.span_id,
            parent_span_id=child.parent_span_id, links=links,
            start_ms=start_ms, end_ms=end_ms, status=status, attributes=attributes)
        self.telemetry_sink.emit_span(span)

    def flush_telemetry(self) -> OTLP.ExportResult:
        """task 6.2：flush 收集的 span/metric 到 OTLP backend（bounded timeout 由 exporter 持有）。

        flag 关 → no-op（``ExportResult()``）；backend 不可用/超时 → ``degraded=True``（callback 落 journal
        ``telemetry_degraded`` event，design L82 可见），**绝不抛**——telemetry 是观测层，outage 不得拖垮
        被观测的 dispatch（design L82 + shadow 契约#3）。失败时 span 保留待下次重试。
        """
        return self.telemetry_sink.flush()

    def build_report(self, base_report, *, semantic_verdict: str | None = None,
                     evidence_integrity: str | None = None,
                     recovery_mode: str | None = None,
                     compaction_count: int | None = None) -> dict:
        """task 6.3：扩展报告加可观测元数据（design 决策#1：coordinator own report 元数据源头）。

        coord own 的元数据（``trace_id``/``span_id``/``assurance_tier``/``journal_authority``/
        ``observability_degraded``）直接从 coord 读——coordinator 是这些元数据的唯一来源；运行时业务态
        （``semantic_verdict``/``evidence_integrity``/``recovery_mode``/``compaction_count``）由调用方传。
        全部 metadata-only（经 ``extend_report``，绝不泄敏感）。
        """
        return TE.extend_report(
            base_report,
            trace_id=self.trace.trace_id,
            span_id=self.trace.span_id,
            assurance_tier=self.assurance_tier,
            recovery_mode=recovery_mode,
            compaction_count=compaction_count,
            observability_degraded=self.telemetry_sink.degraded,
            journal_authority=self.journal_authority,
            semantic_verdict=semantic_verdict,
            evidence_integrity=evidence_integrity,
        )


# task 6.1：coordinator root trace 的子 operation（``run`` 是 root，7 子 operation 来自 telemetry.SPAN_NAMES）
_CHILD_SPAN_OPS: frozenset[str] = TE.SPAN_NAMES - {TE.SPAN_RUN}


# ─── task 6.2：telemetry sink 构造（coordinator own；enabled 从 flags.telemetry_export）──────────
def _make_degradation_callback(journal: ShadowJournal, iteration_id: str, prd_id: str):
    """造 degradation callback：telemetry backend 不可用时落 ``telemetry_degraded`` lifecycle event。

    design L82「记录一次可见的 degradation event」——callback 经 ``journal.emit`` 落盘（journal enabled 时）；
    journal disabled 时 emit no-op，可见性兜底由 ``sink.degraded`` 给 report（task 6.3）。``error`` 截断
    200 字防长 stack/敏感泄漏。
    """
    def _cb(rec: OTLP.DegradationRecord) -> None:
        journal.emit("telemetry_degraded", iteration_id, prd_id, payload={
            "reason": rec.reason,
            "at_flush": rec.at_flush,
            "error": (rec.error or "")[:200],
        })
    return _cb


def _build_telemetry_sink(*, flags: LoopFlags, journal: ShadowJournal, iteration_id: str,
                          prd_id: str, env: dict | None, telemetry_exporter,
                          otlp_timeout: float) -> OTLP.TelemetrySink:
    """建 coordinator own 的 ``TelemetrySink``（design 决策#1：telemetry 从 coord 派生）。

    exporter 解析（DI 优先）：
      * ``telemetry_exporter`` 注入（测试 ``FakeExporter``）→ 直接用；
      * flag 开但无注入 → ``PA_OTLP_ENDPOINT`` 配则 ``HttpOtlpExporter(endpoint, timeout)``（bounded
        timeout），未配则 ``None``（sink enabled 但无 exporter = flush no-op，otlp_export L152）；
      * flag 关 → ``None``（sink disabled = 全 no-op，design 决策#8 渐进启用）。
    degradation callback 始终接（flag 关时 sink 不 flush，callback 不触发）。
    """
    enabled = flags.telemetry_export
    if telemetry_exporter is not None:
        exporter = telemetry_exporter
    elif enabled:
        env_dict = env if env is not None else os.environ
        endpoint = env_dict.get("PA_OTLP_ENDPOINT")
        exporter = OTLP.HttpOtlpExporter(endpoint, timeout=otlp_timeout) if endpoint else None
    else:
        exporter = None
    return OTLP.TelemetrySink(
        exporter, enabled=enabled,
        degradation_callback=_make_degradation_callback(journal, iteration_id, prd_id))


def build_coordinator(*, stamp: str, prd_path: str, proj: str, slug: str,
                      state_dir, profile: dict | None = None, env: dict | None = None,
                      stamp_fn: Callable[[], str] | None = None,
                      prd_content: str | None = None,
                      telemetry_exporter=None,
                      otlp_timeout: float = 5.0,
                      resolver: object | None = None,
                      stable_slug: str = "") -> Coordinator:
    """dispatch/dev-agent 入口：一次解析 flag + 建 IDs/journal/artifact_root，返回 ``Coordinator``。

    替代 ``dispatch_one`` 散建的 ``_run``/``_prd``/``_iter``/``_sj``。所有 adapter 从返回的
    ``coord.flags`` 读 flag（design 决策#1：production code 不再 disconnected helper 式各自 resolve）。

    Args:
        stamp: cron run 时间戳（``run_id`` 输入，同 run→同 id）。
        prd_path: PRD 相对路径（``prd_id`` 输入）。
        proj: 项目名（journal/artifact 路径分段）。
        slug: PRD slug（journal 文件名分段，同 dispatch_one 的 ``{stamp}_{slug}``）。
        state_dir: 运行时 state 根（journal 落 ``runs/<proj>/``，artifact 落 ``artifacts/<run>/``）。
        profile: 项目 profile（``profile["loop"][flag]`` per-project canary）。
        env: 环境变量字典（None 读 ``os.environ``，运维 kill switch 压 profile）；测试传 ``{}`` 隔离。
        stamp_fn: 时间戳函数（None → ``_real_stamp`` 调系统时间；测试注入固定函数）。
        prd_content: PRD 文本内容（task 3.1 dispatch entry 捕获）——非 None → 算 ``sha256:<hex>`` digest 存
            ``coord.prd_digest`` + content-addressed ``prd_id``；None → baseline（path-only prd_id，无 digest）。
            调用方（dispatch_one / dev-agent）读 PRD 文件后注入（IO 在边界，本函数纯）。
    """
    flags = resolve_flags(env=env, profile=profile)
    run = loop_ids.run_id(stamp)
    prd_digest = compute_digest(prd_content.encode("utf-8")) if prd_content is not None else None
    prd = loop_ids.prd_id(prd_path, prd_digest)        # task 3.1：content-addressed（PRD 改→新 prd_id）
    # task 4.4 fix：circuit_key 跨 cron 稳定——prd_path 含 stamp（{stamp}_{slug}.md）跨 cron 变，致 path-based
    #   prd_id 跨 cron 变 → is_in_cooldown 跨 cron 不命中 → "branch 绿 main 红 PRD 夜夜复发"防不住。改用
    #   prd_id(stable_slug, prd_digest)：stable_slug（frontmatter 语义 slug，跨 cron 稳定）+ 内容 digest。
    #   stable_slug 空（无 frontmatter / baseline）→ fallback path-based prd（向后兼容）。
    circuit_key = loop_ids.prd_id(stable_slug or prd_path, prd_digest)
    iteration = loop_ids.iteration_id(run, prd, 0)
    journal_path = Path(state_dir) / "runs" / proj / f"{stamp}_{slug}.journal.jsonl"
    journal = ShadowJournal(journal_path, run, stamp_fn or _real_stamp,
                            enabled=flags.journal_shadow)
    artifact_root = str(Path(state_dir) / "artifacts" / run)
    trace = TC.trace_context_for_run(run)            # task 6.1：per-PRD-run root trace（metadata-only）
    telemetry_sink = _build_telemetry_sink(          # task 6.2：OTLP export + degradation journaling
        flags=flags, journal=journal, iteration_id=iteration, prd_id=prd,
        env=env, telemetry_exporter=telemetry_exporter, otlp_timeout=otlp_timeout)
    # task 2.1：session_aware_retry 开 → own session_store + retry_budget（design 决策#1；评审 P0-1 曾散建、
    #   coordinator 未持有 retry/session/reconcile，致 recover_iteration / publication 前对账无法单点派生）。
    #   关 → None（baseline 零变化）。resolver 始终注入持有（被动 KeyResolver，持有无害；publication/retry
    #   前对账才查 commit/push/pr/test 幂等键）。
    if flags.session_aware_retry:
        session_store = SessionStore(Path(state_dir) / "sessions")
        retry_budget = RP.BudgetState(limits=RP.BudgetLimits())
    else:
        session_store = None
        retry_budget = None
    return Coordinator(flags=flags, run_id=run, prd_id=prd, iteration_id=iteration,
                       journal=journal, artifact_root=artifact_root, trace=trace,
                       telemetry_sink=telemetry_sink, prd_digest=prd_digest, circuit_key=circuit_key,
                       retry_budget=retry_budget, session_store=session_store, resolver=resolver)


# ─── task 2.5：preflight 校验 loop flag 组合一致性（design 决策#1 防 impossible partial 组合）──
@dataclass(frozen=True)
class PreflightBlocked:
    """task 2.5：invalid partial feature 组合 → 结构化 blocked reason。"""
    reason: str
    violations: tuple[str, ...]      # 每条违规组合描述（dispatch 记录后返回，不起 dev loop）


@dataclass(frozen=True)
class PreflightResult:
    """preflight 校验结果。``ok=True`` 可继续 dispatch；``ok=False`` 含 blocked 详情。"""
    ok: bool
    blocked: PreflightBlocked | None = None

    @property
    def is_ok(self) -> bool:
        return self.ok


# flag 依赖链（design 决策#1 防 impossible partial 组合 + 决策#2 cutover）：
#   journal_driven_dispatch ⇒ journal_shadow   driven 必须先 shadow（cutover 前置 shadow parity）
#   session_aware_retry     ⇒ journal_shadow   retry 需 journal 持久化 session（无 journal = 无 session 可 resume）
#   lifecycle_hooks         ⇒ journal_shadow   hooks 需 journal 落盘事件（无 journal = hook 事件丢失）
#   single_flight_auto_merge⇒ single_flight_serial_shadow  真合 main 必须先串行单飞准入（ADR-0008 护栏#7：
#                                shadow→drill→canary→全量；无串行 slot = 并发同仓 merge = chaos；7.1a shadow gate）
# 注：cross_prd_learning_injection **不进**此硬依赖链——injection 对 shadow 的依赖是 advisory（provenance
# 安全策略），不是功能硬依赖（shadow off 时 injection 仍可基于历史 catalog 工作）。invalid 组合
# injection=on, shadow=off 走**运行时降级**：resolve_learning_injections_source 返 fallback
# （driven_by='learning_injection_shadow_off'），调用方 emit learning_memory_degraded{class:injection_not_gated}
# （接线 section 4/5），dispatch 继续不阻断——design 决策#7 fail-open for delivery + 决策#8 读时降级语义。
# 形式：(flag, depends_on, violation_desc)
_FLAG_DEPENDENCIES: tuple[tuple[str, str, str], ...] = (
    ("journal_driven_dispatch", "journal_shadow",
     "journal_driven_dispatch requires journal_shadow (driven cutover needs shadow parity first)"),
    ("session_aware_retry", "journal_shadow",
     "session_aware_retry requires journal_shadow (retry needs journal-persisted session)"),
    ("lifecycle_hooks", "journal_shadow",
     "lifecycle_hooks requires journal_shadow (hooks must persist events to journal)"),
    ("single_flight_auto_merge", "single_flight_serial_shadow",
     "single_flight_auto_merge requires single_flight_serial_shadow (auto-merge needs serial single-flight admission first)"),
)


def preflight(flags: LoopFlags) -> PreflightResult:
    """校验 loop flag 组合一致性（task 2.5；design 决策#1）。

    散建 flag 允许 impossible partial 组合（如 hooks 无 journal、retry 无 session 持久化、driven 无
    shadow）。本函数在 dispatch preflight 阶段一次性校验所有依赖链，违规 → 结构化 blocked reason
    （dispatch 记录后返回，不起 dev loop；design 决策#1「permits impossible combinations」的反制）。

    Args:
        flags: 已解析的 ``LoopFlags``（coordinator 一次解析的快照）。
    Returns:
        ``PreflightResult``：``ok=True`` 可继续 dispatch；``ok=False`` 含违规列表。
    """
    violations: list[str] = []
    for flag, dep, desc in _FLAG_DEPENDENCIES:
        if getattr(flags, flag) and not getattr(flags, dep):
            violations.append(desc)
    if not violations:
        return PreflightResult(ok=True)
    return PreflightResult(
        ok=False,
        blocked=PreflightBlocked(
            reason=f"invalid loop flag combination: {len(violations)} dependency violation(s)",
            violations=tuple(violations),
        ),
    )
