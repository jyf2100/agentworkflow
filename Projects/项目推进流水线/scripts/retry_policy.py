#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""retry_policy.py — task 5.3 RetryPolicy 五决策 + task 5.6 独立预算上限。

只消费结构化失败分类（design 决策#3），绝不依赖模型自述。决策表（design L45-54）：

    - 预算/轮次用尽 → ``stop``
    - 外部真源未知 → ``block``（不消耗 retry，先 reconcile）
    - session 缺失/损坏、上下文污染、重复相同失败 → ``new_session``
    - 临时 provider/transport 中断、verifier 局部反馈 + 有进展 → ``resume``
    - verifier 建议换方案 / 保留原历史比较 → ``fork``

每次决策带 ``policy_version`` + ``reason``（调用方据此写 journal event，design L56）。
预算独立计数（task 5.6）：Stop 续跑 / SDK retry / 外层 verify 迭代 / wall-clock / turns /
trusted cost，任一耗尽即 ``stop``（design risk#92「Stop hook 形成无限内循环」的硬 kill 兜底）。

纯函数、纯 stdlib（cron 隔离友好）。
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

from failure_analysis import FailureFingerprint, ProgressSignal, is_repeated_failure
from session_meta import ExceptionClass, SessionMeta

POLICY_VERSION = "1"   # design L56「每次决策写 journal 含 policy version」


class RetryMode(str, Enum):
    RESUME = "resume"                 # 同 session 续跑（临时中断 / verifier 局部反馈）
    FORK = "fork"                     # 保留原历史，另起方案比较
    NEW_SESSION = "new_session"       # 全新 session（污染 / session 缺失 / 重复失败）
    BLOCK = "block"                   # 外部真源未知，不消耗 retry，先 reconcile
    STOP = "stop"                     # 预算 / 轮次用尽


class VerifierSignal(str, Enum):
    """verifier 反馈类别——驱动 resume vs fork（design L50-51）。"""
    NONE = "none"
    LOCAL_FEEDBACK = "local_feedback"               # 局部可修，历史仍可信 → resume
    SUGGEST_ALTERNATIVE = "suggest_alternative"     # 建议换方案比较 → fork


class BudgetDimension(str, Enum):
    """task 5.6 独立预算维度（design risk#92 硬 kill 兜底）。"""
    STOP_CONTINUATION = "stop_continuation"   # Stop hook 内循环续跑
    SDK_RETRY = "sdk_retry"                   # SDK session retry（resume/fork/new）
    VERIFY_ITERATION = "verify_iteration"     # 外层 verify 增量重做（第一阶段 ≤2）
    WALL_CLOCK = "wall_clock"                 # 总墙钟
    TURNS = "turns"                           # 总轮次
    TRUSTED_COST = "trusted_cost"             # trusted cost 上限


# task 5.6：各维度独立上限
@dataclass(frozen=True)
class BudgetLimits:
    stop_continuations: int = 3        # design open-question：Stop 续跑 2-3 次
    sdk_retries: int = 5
    verify_iterations: int = 2         # 第一阶段最多 2 轮增量重做（hardened pipeline 契约）
    wall_clock_seconds: int = 3600
    turns_total: int = 200
    trusted_cost_usd: float = 50.0


@dataclass(frozen=True)
class BudgetState:
    """不可变预算账本。``consume`` 返回新实例（immutability）。"""
    limits: BudgetLimits
    stop_continuations_used: int = 0
    sdk_retries_used: int = 0
    verify_iterations_used: int = 0
    wall_clock_elapsed_s: int = 0
    turns_used: int = 0
    trusted_cost_usd: float = 0.0

    def _violations(self) -> list[str]:
        lim = self.limits
        v: list[str] = []
        if self.stop_continuations_used >= lim.stop_continuations:
            v.append(f"stop_continuations {self.stop_continuations_used}/{lim.stop_continuations}")
        if self.sdk_retries_used >= lim.sdk_retries:
            v.append(f"sdk_retries {self.sdk_retries_used}/{lim.sdk_retries}")
        if self.verify_iterations_used >= lim.verify_iterations:
            v.append(f"verify_iterations {self.verify_iterations_used}/{lim.verify_iterations}")
        if self.wall_clock_elapsed_s >= lim.wall_clock_seconds:
            v.append(f"wall_clock {self.wall_clock_elapsed_s}/{lim.wall_clock_seconds}s")
        if self.turns_used >= lim.turns_total:
            v.append(f"turns {self.turns_used}/{lim.turns_total}")
        if self.trusted_cost_usd >= lim.trusted_cost_usd:
            v.append(f"trusted_cost {self.trusted_cost_usd:.2f}/{lim.trusted_cost_usd:.2f}usd")
        return v

    @property
    def exhausted(self) -> bool:
        """任一维度耗尽 → True（task 5.6：独立上限，design risk#92 硬 kill）。"""
        return bool(self._violations())

    @property
    def exhaustion_reason(self) -> str:
        v = self._violations()
        return "budget exhausted: " + ", ".join(v) if v else ""

    def consume(self, dim: BudgetDimension, *, amount: int | float = 1,
                cost: float = 0.0, turns: int = 0, wall_s: int = 0) -> "BudgetState":
        """扣减指定维度（不可变，返回新实例）。BLOCK 不调用此方法（不消耗 retry）。"""
        kw = {}
        if dim is BudgetDimension.STOP_CONTINUATION:
            kw["stop_continuations_used"] = self.stop_continuations_used + amount
        elif dim is BudgetDimension.SDK_RETRY:
            kw["sdk_retries_used"] = self.sdk_retries_used + amount
        elif dim is BudgetDimension.VERIFY_ITERATION:
            kw["verify_iterations_used"] = self.verify_iterations_used + amount
        # wall_clock/turns/cost 是累加型，任何 retry 都累加实际消耗
        if wall_s:
            kw["wall_clock_elapsed_s"] = self.wall_clock_elapsed_s + wall_s
        if turns:
            kw["turns_used"] = self.turns_used + turns
        if cost:
            kw["trusted_cost_usd"] = self.trusted_cost_usd + cost
        return replace(self, **kw) if kw else self


@dataclass(frozen=True)
class RetryDecision:
    """RetryPolicy 决策输出——调用方据此写 journal event + 执行 retry mode。"""
    mode: RetryMode
    reason: str
    policy_version: str = POLICY_VERSION
    consumes_retry: bool = True        # BLOCK=False（design L53：block 不消耗 retry）

    @property
    def budget_dimension(self) -> BudgetDimension | None:
        """该决策执行时应扣的预算维度（BLOCK/STOP=None）。"""
        return {
            RetryMode.RESUME: BudgetDimension.SDK_RETRY,
            RetryMode.FORK: BudgetDimension.SDK_RETRY,
            RetryMode.NEW_SESSION: BudgetDimension.SDK_RETRY,
            RetryMode.BLOCK: None,
            RetryMode.STOP: None,
        }[self.mode]


def decide(*,
           budget: BudgetState,
           session: SessionMeta | None,
           fingerprint: FailureFingerprint | None,
           progress: ProgressSignal | None,
           external_known: bool = True,
           verifier_signal: VerifierSignal = VerifierSignal.NONE,
           failure_history=None,
           integrity_block: str | None = None) -> RetryDecision:
    """RetryPolicy 主决策——纯函数，对齐 design L45-54 决策表（优先级自上而下）。

    前置门（停机/integrity/外部/session 健康）→ 失败分类驱动 resume/fork/new。绝不依赖模型自述。

    task 4.3：``integrity_block``（``"evidence_integrity"`` | ``"journal_integrity"`` | None）显式
    表达证据/日志完整性阻塞——喂进 retry/fork/new 的 recovery context 本身不可信时，重试无意义，
    一律 BLOCK 不消耗 retry（spec verified-publication「Test artifact write fails」+ durable-runtime
    「Reducer failure during driven mode」的 fail-closed 阻塞，需运维 triage）。
    """
    # 1. 预算耗尽 → STOP（design L54 / risk#92 硬 kill，最高优先级）
    if budget.exhausted:
        return RetryDecision(RetryMode.STOP, reason=budget.exhaustion_reason, consumes_retry=False)
    # 1.5 task 4.3：evidence/journal-integrity 阻塞 → BLOCK，不消耗 retry
    #    （证据/日志不可信时 retry/fork/new 都无意义——喂进去的 context 本身不可信；spec fail-closed）
    if integrity_block:
        return RetryDecision(RetryMode.BLOCK,
                             reason=f"{integrity_block}: integrity block; operator triage before retry",
                             consumes_retry=False)
    # 2. 外部真源未知 → BLOCK，不消耗 retry（design L53；先 reconcile 再决策）
    if not external_known:
        return RetryDecision(RetryMode.BLOCK,
                             reason="external source of truth unknown; reconcile before retry",
                             consumes_retry=False)
    # 3. session 缺失/损坏 → NEW_SESSION（design L52「session 缺失/损坏」）
    if session is None or not session.session_resumable:
        why = "missing session metadata" if session is None else \
              f"session not resumable ({session.exception_class.value})"
        return RetryDecision(RetryMode.NEW_SESSION, reason=why)
    # 4. 重复相同失败 + 停滞 → NEW_SESSION（design L52/risk#91：固化错误上下文）
    if fingerprint is not None and is_repeated_failure(failure_history, fingerprint) \
            and (progress is None or progress.stalled):
        return RetryDecision(RetryMode.NEW_SESSION,
                             reason=f"repeated failure fingerprint ({fingerprint.key}) with no progress")
    # 5. verifier 建议换方案 → FORK（design L51：比较另一方案/保留原历史）
    if verifier_signal is VerifierSignal.SUGGEST_ALTERNATIVE:
        return RetryDecision(RetryMode.FORK, reason="verifier suggests alternative approach")
    # 6. 临时 provider/transport 中断 + session 可用 → RESUME（design L49）
    if session.exception_class is ExceptionClass.TRANSIENT:
        return RetryDecision(RetryMode.RESUME, reason="transient provider/transport interruption")
    # 7. verifier 局部反馈 + 有进展 + session 可用 → RESUME（design L50）
    if verifier_signal is VerifierSignal.LOCAL_FEEDBACK and (progress is None or progress.making_progress):
        return RetryDecision(RetryMode.RESUME, reason="verifier local feedback, history still trusted")
    # 8. compaction 过高 + 停滞 → NEW_SESSION（保守：防 compaction 固化错误上下文）
    if session.compaction_count >= 3 and progress is not None and progress.stalled:
        return RetryDecision(RetryMode.NEW_SESSION,
                             reason=f"high compaction ({session.compaction_count}) with no progress")
    # 9. 缺省 → RESUME（保守续跑；有 session 且未触发 new/fork/block/stop）
    return RetryDecision(RetryMode.RESUME, reason="default retry; session healthy")
