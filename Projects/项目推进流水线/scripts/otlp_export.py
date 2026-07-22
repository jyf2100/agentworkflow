#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""otlp_export.py — task 7.3 OTLP export + bounded timeout + observability-degradation event。

design 决策（Section 7，L82 + L117）：
    * Telemetry 后端不可用时**本地执行继续**，但记录一次可见的 **observability degradation event**
      （不转失败——telemetry 是观测层，outage 不得拖垮被观测的 dispatch，同 shadow 契约#3）；
    * OTLP-compatible export（collector/Jaeger 由部署环境决定，规范只要求 OTLP 兼容）。

**解耦真实后端**：``OtlpExporter`` Protocol 注入——真实用 ``HttpOtlpExporter``（urllib，运行时 bounded
timeout），测试用 ``FakeExporter``。``TelemetrySink`` 收集 span/metric → flush 时 export；backend 不可用
或超时 → 记 degradation（不抛、不伪装绿）。

``telemetry_export`` flag 关（默认）→ ``TelemetrySink`` no-op（design 决策#8 渐进启用）。

纯 stdlib（urllib 仅在 HttpOtlpExporter 运行时），cron 隔离友好。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

from telemetry import MetricSnapshot, Span


@dataclass(frozen=True)
class ExportResult:
    """一次 flush 的结果。``degraded=True`` 表示 backend 不可用/超时（已记 degradation，非失败）。"""
    exported_spans: int = 0
    exported_metrics: int = 0
    degraded: bool = False
    error: str = ""


class OtlpExporter(Protocol):
    """OTLP exporter 抽象（注入式）。真实 ``HttpOtlpExporter`` / 测试 ``FakeExporter``。"""
    def available(self) -> bool: ...
    def export(self, spans: list[Span], metrics: MetricSnapshot) -> ExportResult: ...


class HttpOtlpExporter:
    """真实 OTLP/HTTP exporter（urllib，运行时 bounded timeout）。

    export 把 span/metric 序列化为 OTLP-ish JSON POST 到 endpoint；超时/不可达 → ``ExportResult(degraded)``，
    **绝不抛异常**（telemetry outage 不拖垮 dispatch）。生产可换 opentelemetry-sdk，本实现满足「OTLP-compatible」。
    """

    def __init__(self, endpoint: str, *, timeout: float = 5.0):
        self.endpoint = endpoint
        self.timeout = timeout

    def available(self) -> bool:
        """探测 endpoint 可达性（短超时）。不可达 → False（flush 时据此记 degradation）。"""
        import urllib.request
        try:
            req = urllib.request.Request(self.endpoint, method="HEAD")
            urllib.request.urlopen(req, timeout=min(self.timeout, 2.0))
            return True
        except Exception:
            return False

    def export(self, spans, metrics):
        import urllib.request
        payload = json.dumps({
            "spans": [_span_payload(s) for s in spans],
            "metrics": _metric_payload(metrics),
        }).encode("utf-8")
        try:
            req = urllib.request.Request(
                self.endpoint, data=payload,
                headers={"Content-Type": "application/json"}, method="POST",
            )
            urllib.request.urlopen(req, timeout=self.timeout)
            return ExportResult(exported_spans=len(spans), exported_metrics=1)
        except Exception as e:
            # backend 不可用/超时 → degraded（不抛，dispatch 继续；上层记 degradation event）
            return ExportResult(degraded=True, error=str(e))


def _span_payload(span: Span) -> dict:
    return {
        "name": span.name, "trace_id": span.trace_id, "span_id": span.span_id,
        "parent_span_id": span.parent_span_id, "links": list(span.links),
        "start_ms": span.start_ms, "end_ms": span.end_ms,
        "duration_ms": span.duration_ms, "status": span.status,
        "attributes": span.attributes,
    }


def _metric_payload(metrics: MetricSnapshot) -> dict:
    return {
        "runs_total": metrics.runs_total, "successes": metrics.successes,
        "blocked": metrics.blocked, "failed": metrics.failed,
        "iterations_total": metrics.iterations_total,
        "tests_total": metrics.tests_total, "tests_passed": metrics.tests_passed,
        "success_rate": metrics.success_rate, "test_pass_rate": metrics.test_pass_rate,
        "recovery_success_rate": metrics.recovery_success_rate,
        "cost_usd_total": metrics.cost_usd_total,
        "wall_clock_ms_total": metrics.wall_clock_ms_total,
    }


@dataclass
class DegradationRecord:
    """observability degradation 记录（backend 不可用时产，落 journal 可见事件，design L82）。"""
    __test__ = False
    reason: str
    at_flush: int = 0
    error: str = ""


class TelemetrySink:
    """telemetry 汇聚 + export。flag 关 → no-op；backend 不可用 → 记 degradation（不转失败）。

    ``degradation_callback``：backend 不可用时由控制面落 observability-degradation journal event
    （design L82「记录一次可见的 degradation event」）。callback 异常吞掉（telemetry 自身故障不拖垮 dispatch）。
    """
    __test__ = False

    def __init__(self, exporter: OtlpExporter | None, *, enabled: bool,
                 degradation_callback=None):
        self.exporter = exporter
        self.enabled = bool(enabled)
        self.degradation_callback = degradation_callback
        self._spans: list[Span] = []
        self._metrics = MetricSnapshot()
        self._degradations: list[DegradationRecord] = []

    @property
    def metrics(self) -> MetricSnapshot:
        return self._metrics

    @property
    def degraded(self) -> bool:
        """是否发生过 observability degradation（report 扩展用，task 7.6）。"""
        return bool(self._degradations)

    def emit_span(self, span: Span) -> None:
        """收集一条 span（flag 关 → no-op）。"""
        if not self.enabled:
            return
        self._spans.append(span)

    def set_metrics(self, metrics: MetricSnapshot) -> None:
        self._metrics = metrics

    def flush(self) -> ExportResult:
        """export 收集的 span/metric。backend 不可用/超时 → degraded + 记 degradation event（不抛）。"""
        if not self.enabled:
            return ExportResult()                       # no-op
        if self.exporter is None:
            return ExportResult()                       # 无 exporter 配置 = no-op（不记 degradation）
        try:
            result = self.exporter.export(self._spans, self._metrics)
        except Exception as e:
            # exporter 自身抛（不应发生，HttpOtlpExporter 内部已吞）→ degraded
            result = ExportResult(degraded=True, error=f"exporter raised: {e}")
        if result.degraded:
            rec = DegradationRecord(
                reason="telemetry backend unavailable or timed out",
                at_flush=len(self._spans), error=result.error,
            )
            self._degradations.append(rec)
            if self.degradation_callback is not None:
                try:
                    self.degradation_callback(rec)
                except Exception:
                    pass                # callback 失败吞掉（telemetry 不拖垮 dispatch）
        else:
            self._spans.clear()         # export 成功才清空（失败保留待下次重试）
        return result
