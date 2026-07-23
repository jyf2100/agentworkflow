#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""telemetry.py — task 7.1 metadata-only span/metric + 7.4 metrics + 7.5 field allowlist + 7.6 report 扩展。

design 决策（Section 7，L78-82 + L94）：
    * 每 PRD run 创建 root span；iteration/SDK session/tool/test/verify/reconcile/publish 为子 span；
    * resume/fork/跨进程 continuation 用 trace context + span links 表达因果（trace_context 模块）；
    * **属性只记** ID/状态/耗时/token/cost/文件计数/hash/错误分类——**绝不** prompt/源码/secret（L80）；
    * 低基数指标：成功率/blocked/failed/iteration 数/测试通过率/重复失败率/恢复成功率/成本/wall-clock（L82）；
    * 默认 metadata-only + 字段 allowlist + export 前 secret scanner（L94）。

本模块（7.1+7.4+7.5+7.6）：``Span`` 模型 + ``sanitize_attributes``（allowlist + secret scrub，7.5）+
``MetricSnapshot`` 聚合 + ``extend_report``（7.6，加 trace/tier/recovery/compaction/degradation 元数据，
不泄敏感）。trace-context 在 ``trace_context``（7.2），OTLP export 在 ``otlp_export``（7.3）。

纯 stdlib（hashlib/re），cron 隔离友好；复用 ``artifact_store.redact_secrets`` 抹密钥。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any

import artifact_store

# ── task 7.1 span 名约定（design L78：root span + 7 子 span）─────────────────
SPAN_RUN = "run"                       # root span（per PRD run）
SPAN_ITERATION = "iteration"
SPAN_SDK_SESSION = "sdk_session"
SPAN_TOOL = "tool"
SPAN_TEST = "test"
SPAN_VERIFY = "verify"
SPAN_RECONCILE = "reconcile"
SPAN_PUBLISH = "publish"
SPAN_NAMES: frozenset[str] = frozenset({
    SPAN_RUN, SPAN_ITERATION, SPAN_SDK_SESSION, SPAN_TOOL,
    SPAN_TEST, SPAN_VERIFY, SPAN_RECONCILE, SPAN_PUBLISH,
})

# ── task 7.5 属性白名单（design L80：只 ID/状态/耗时/token/cost/文件计数/hash/错误分类）──
# 任何不在此白名单的 key 一律丢弃（prompt/source/tool output 等永远进不来）。
ALLOWED_ATTR_KEYS: frozenset[str] = frozenset({
    # ID 类
    "run_id", "prd_id", "iteration_id", "session_id", "tool_use_id", "agent_id",
    "parent_iteration_id", "forked_from",
    # 状态/结果类
    "status", "exit_code", "error_class", "result_subtype", "stop_reason",
    "verifier_signal", "retry_mode", "budget_dimension",
    # 耗时类
    "duration_ms", "duration_api_ms", "wall_clock_ms",
    # token/cost 类（数值，非内容）
    "input_tokens", "output_tokens", "cache_read_tokens", "cost_usd", "trusted_cost_usd",
    # 文件计数/hash 类（内容地址，非内容本身）
    "file_count", "diff_hash", "test_signature", "artifact_digest", "turns",
    # recovery/sandbox 元数据（task 7.6 report 用）
    "assurance_tier", "recovery_mode", "compaction_count", "sandbox_tier",
})

# secret scrub 正则（复用 artifact_store.redact_secrets 的规则集，额外覆盖常见 env/cookie 形态）。
# 注意：白名单 key 本身不含敏感内容；此层是对 value 的兜底消毒。
_EXTRA_SECRET_RE = re.compile(
    r"(Authorization\s*:\s*\S+"            # Authorization header
    r"|Cookie\s*:\s*\S+"                   # Cookie header
    r"|Set-Cookie\s*:\s*\S+"               # Set-Cookie
    r"|[A-Za-z_][A-Za-z0-9_]*_(?:API_KEY|SECRET|TOKEN|PASSWORD)\s*=\s*\S+)",  # ENV=VAL
    re.IGNORECASE,
)


def _scrub_value(value: Any) -> Any:
    """对白名单 value 兜底消毒（抹 secret pattern）。非 str 原样返回。"""
    if not isinstance(value, str):
        return value
    scrubbed = artifact_store.redact_secrets(value)      # ghp_/Bearer/token=/basic-auth
    return _EXTRA_SECRET_RE.sub("***", scrubbed)          # env/cookie 额外形态


def sanitize_attributes(attrs) -> dict:
    """task 7.5 field allowlist + secret scrub。

    1. key 不在 ``ALLOWED_ATTR_KEYS`` → 整个 field 丢弃（prompt/source/tool output 永远进不来）；
    2. 白名单 key 的 value → ``_scrub_value`` 兜底抹 secret pattern。
    返回消毒后的新 dict（不可变输入不被改）。
    """
    if not isinstance(attrs, dict):
        return {}
    out: dict = {}
    for k, v in attrs.items():
        if k not in ALLOWED_ATTR_KEYS:
            continue                      # 非 allowlist → 丢（7.5 主防线）
        out[k] = _scrub_value(v)
    return out


@dataclass(frozen=True)
class Span:
    """一条 telemetry span（metadata-only）。属性经 ``sanitize_attributes`` 消毒。"""
    name: str                              # SPAN_NAMES 之一
    trace_id: str                          # 32 hex（trace_context 派生）
    span_id: str                           # 16 hex
    parent_span_id: str | None = None
    links: tuple[str, ...] = ()            # 关联 span_id（resume/fork 因果，task 7.2）
    start_ms: int = 0
    end_ms: int = 0
    status: str = "ok"                     # ok / error
    attributes: dict = field(default_factory=dict)

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)


def make_span(name: str, *, trace_id: str, span_id: str,
              parent_span_id: str | None = None, links: tuple[str, ...] = (),
              start_ms: int = 0, end_ms: int = 0, status: str = "ok",
              attributes=None) -> Span:
    """构造 Span（强制 name ∈ SPAN_NAMES + attributes 经 sanitize）。name 非法 → ValueError。"""
    if name not in SPAN_NAMES:
        raise ValueError(f"illegal span name (allowed: {sorted(SPAN_NAMES)}): {name!r}")
    return Span(
        name=name, trace_id=trace_id, span_id=span_id,
        parent_span_id=parent_span_id, links=tuple(links),
        start_ms=start_ms, end_ms=end_ms, status=status,
        attributes=sanitize_attributes(attributes or {}),
    )


# ════════════════════════════════════════════════════════════════════════════
# task 7.4 低基数指标（design L82）
# ════════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class MetricSnapshot:
    """operational metrics 快照（低基数，metadata-only）。从 journal events 聚合。"""
    runs_total: int = 0
    successes: int = 0                     # published（唯一成功交付终态）
    blocked: int = 0                       # external_blocked / test_blocked
    failed: int = 0                        # failed 终态
    iterations_total: int = 0
    tests_total: int = 0
    tests_passed: int = 0
    repeated_failures: int = 0
    recovery_attempts: int = 0
    recovery_successes: int = 0
    cost_usd_total: float = 0.0
    wall_clock_ms_total: int = 0

    @property
    def success_rate(self) -> float:
        return self.successes / self.runs_total if self.runs_total else 0.0

    @property
    def test_pass_rate(self) -> float:
        return self.tests_passed / self.tests_total if self.tests_total else 0.0

    @property
    def repeated_failure_rate(self) -> float:
        return self.repeated_failures / self.iterations_total if self.iterations_total else 0.0

    @property
    def recovery_success_rate(self) -> float:
        return (self.recovery_successes / self.recovery_attempts
                if self.recovery_attempts else 0.0)


# journal event_type → metric delta 映射（design L82 低基数）
_EVENT_METRIC_MAP = {
    "running": lambda s, p: replace(s, iterations_total=s.iterations_total + 1),
    "published": lambda s, p: replace(s, runs_total=s.runs_total + 1, successes=s.successes + 1),
    "failed": lambda s, p: replace(s, runs_total=s.runs_total + 1, failed=s.failed + 1),
    "external_blocked": lambda s, p: replace(s, blocked=s.blocked + 1),
    "test_blocked": lambda s, p: replace(s, blocked=s.blocked + 1),
    "aborted": lambda s, p: replace(s, runs_total=s.runs_total + 1),
    "test": lambda s, p: _record_test(s, p),
    "agent_finished": lambda s, p: _record_cost(s, p),
}


def _record_test(snapshot: MetricSnapshot, payload: dict) -> MetricSnapshot:
    exit_code = payload.get("exit_code") if isinstance(payload, dict) else None
    passed = exit_code == 0
    return replace(
        snapshot,
        tests_total=snapshot.tests_total + 1,
        tests_passed=snapshot.tests_passed + (1 if passed else 0),
    )


def _record_cost(snapshot: MetricSnapshot, payload: dict) -> MetricSnapshot:
    if not isinstance(payload, dict):
        return snapshot
    cost = payload.get("cost_usd") or payload.get("total_cost_usd")
    try:
        cost_f = float(cost) if cost is not None else 0.0
    except (TypeError, ValueError):
        cost_f = 0.0
    wall = payload.get("duration_ms") or payload.get("wall_clock_ms") or 0
    try:
        wall_i = int(wall)
    except (TypeError, ValueError):
        wall_i = 0
    return replace(
        snapshot,
        cost_usd_total=round(snapshot.cost_usd_total + cost_f, 6),
        wall_clock_ms_total=snapshot.wall_clock_ms_total + wall_i,
    )


def record_event(snapshot: MetricSnapshot, event_type: str,
                 payload: dict | None = None) -> MetricSnapshot:
    """从一条 journal event 聚合 metric（不可变，返新 snapshot）。未知 event_type → 原样。"""
    fn = _EVENT_METRIC_MAP.get(event_type)
    if fn is None:
        return snapshot
    return fn(snapshot, payload or {})


def record_recovery(snapshot: MetricSnapshot, *, succeeded: bool) -> MetricSnapshot:
    """记录一次 recovery 尝试（resume/fork/new_session）的成功/失败（design L82 恢复成功率）。"""
    return replace(
        snapshot,
        recovery_attempts=snapshot.recovery_attempts + 1,
        recovery_successes=snapshot.recovery_successes + (1 if succeeded else 0),
    )


def record_repeated_failure(snapshot: MetricSnapshot) -> MetricSnapshot:
    """记录一次重复失败（is_repeated_failure，design L82 重复失败率）。"""
    return replace(snapshot, repeated_failures=snapshot.repeated_failures + 1)


# ════════════════════════════════════════════════════════════════════════════
# task 7.6 report 扩展（design L82：trace IDs/assurance tier/recovery mode/
# compaction count/observability degradation，不泄敏感）
# ════════════════════════════════════════════════════════════════════════════
def extend_report(report: dict, *, trace_id: str | None = None, span_id: str | None = None,
                  assurance_tier: str | None = None, recovery_mode: str | None = None,
                  compaction_count: int | None = None,
                  observability_degraded: bool | None = None,
                  journal_authority: str | None = None,
                  semantic_verdict: str | None = None,
                  evidence_integrity: str | None = None,
                  metrics: MetricSnapshot | None = None) -> dict:
    """扩展 dispatch report 加可观测元数据（task 7.6）。

    只加 metadata-only 字段（trace ID 是 hash、tier/recovery_mode 是枚举值、count 是 int、degraded 是
    bool）——**绝不**把敏感数据塞进 report。原 report 经拷贝，不被改。
    """
    out = dict(report) if isinstance(report, dict) else {}
    ext = out.setdefault("observability", {})
    if trace_id is not None:
        ext["trace_id"] = trace_id          # 32 hex hash，非敏感
    if span_id is not None:
        ext["root_span_id"] = span_id
    if assurance_tier is not None:
        ext["assurance_tier"] = assurance_tier
    if recovery_mode is not None:
        ext["recovery_mode"] = recovery_mode
    if compaction_count is not None:
        ext["compaction_count"] = int(compaction_count)
    if observability_degraded is not None:
        ext["observability_degraded"] = bool(observability_degraded)
    if journal_authority is not None:
        ext["journal_authority"] = journal_authority          # task 6.3：driven/shadow/legacy（decision#2）
    if semantic_verdict is not None:
        ext["semantic_verdict"] = semantic_verdict            # task 6.3：verify 语义判定（与 mechanical test 分开，decision#3）
    if evidence_integrity is not None:
        ext["evidence_integrity"] = evidence_integrity        # task 6.3：ok/blocked_evidence/state_corrupt（不伪装绿）
    if metrics is not None:
        ext["metrics"] = {
            "success_rate": round(metrics.success_rate, 4),
            "test_pass_rate": round(metrics.test_pass_rate, 4),
            "iterations": metrics.iterations_total,
            "recovery_success_rate": round(metrics.recovery_success_rate, 4),
            "cost_usd": round(metrics.cost_usd_total, 6),
            "wall_clock_ms": metrics.wall_clock_ms_total,
        }
    return out
