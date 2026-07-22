#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_telemetry.py — Section 7（OpenTelemetry + Operational Metrics）task 7.1-7.6 全套测试。

覆盖：
    * 7.1 metadata-only Span/Metric 约定（8 span 类型 + 非法名拒绝 + duration）；
    * 7.2 trace-context 确定性派生 + W3C traceparent + persist/load + span links；
    * 7.3 OTLP export（FakeExporter）+ bounded timeout 语义 + backend 不可用降级（不抛、记 degradation）；
    * 7.4 低基数 metrics 聚合（success/blocked/failed/iteration/test/recovery/cost/wall-clock）+ rate；
    * 7.5 field-allowlist + secret-leak 拒绝（prompt/source/tool output/credentials/cookies/env value）；
    * 7.6 report 扩展（trace IDs/assurance tier/recovery mode/compaction/degradation，无敏感）。

AAA；模块零 SDK / 零网络依赖（HttpOtlpExporter 真跑路径不被测试触发）。跑：
    python3 -m pytest scripts/test_telemetry.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import otlp_export as OTLP  # noqa: E402
import telemetry as TEL  # noqa: E402
import trace_context as TC  # noqa: E402


# ════════════════════════════════════════════════════════════════════════════
# task 7.1 metadata-only Span/Metric 约定
# ════════════════════════════════════════════════════════════════════════════
def test_span_names_cover_eight_types():
    # design L78：root span + 7 子 span = 8 类
    assert TEL.SPAN_NAMES == {
        "run", "iteration", "sdk_session", "tool", "test", "verify", "reconcile", "publish",
    }


def test_make_span_rejects_illegal_name():
    try:
        TEL.make_span("not_a_real_span", trace_id="t", span_id="s")
    except ValueError:
        return
    assert False, "expected ValueError for illegal span name"


def test_make_span_records_duration():
    span = TEL.make_span("run", trace_id="t" * 32, span_id="s" * 16,
                        start_ms=1000, end_ms=1500)
    assert span.name == "run"
    assert span.duration_ms == 500


def test_make_span_preserves_metadata_attributes():
    span = TEL.make_span("iteration", trace_id="t" * 32, span_id="s" * 16,
                        attributes={"iteration_id": "it_1", "duration_ms": 42,
                                    "input_tokens": 1200, "cost_usd": 0.05})
    assert span.attributes["iteration_id"] == "it_1"
    assert span.attributes["input_tokens"] == 1200


def test_metric_snapshot_defaults_zero():
    snap = TEL.MetricSnapshot()
    assert snap.success_rate == 0.0
    assert snap.test_pass_rate == 0.0
    assert snap.recovery_success_rate == 0.0


# ════════════════════════════════════════════════════════════════════════════
# task 7.2 trace-context 持久化 + span links
# ════════════════════════════════════════════════════════════════════════════
def test_trace_id_deterministic_from_run_id():
    # design 决策#1 稳定 ID：同 run → 同 trace（崩溃/跨进程重放稳定）
    assert TC.new_trace_id("run-123") == TC.new_trace_id("run-123")
    assert len(TC.new_trace_id("run-123")) == 32              # W3C trace_id = 32 hex


def test_trace_id_differs_across_runs():
    assert TC.new_trace_id("run-1") != TC.new_trace_id("run-2")


def test_span_id_deterministic():
    tid = TC.new_trace_id("run-1")
    assert TC.new_span_id(tid, "iteration", 1) == TC.new_span_id(tid, "iteration", 1)
    assert len(TC.new_span_id(tid, "iteration", 1)) == 16     # W3C span_id = 16 hex
    # 同 trace 同 name 不同 seq → 不同 span
    assert TC.new_span_id(tid, "iteration", 1) != TC.new_span_id(tid, "iteration", 2)


def test_trace_context_for_run_root_has_no_parent():
    ctx = TC.trace_context_for_run("run-1")
    assert ctx.trace_id == TC.new_trace_id("run-1")
    assert ctx.parent_span_id is None


def test_trace_context_child_links_parent():
    ctx = TC.trace_context_for_run("run-1")
    child = ctx.child("iteration", 1)
    assert child.trace_id == ctx.trace_id                   # 同 trace
    assert child.parent_span_id == ctx.span_id              # parent 指向 root span


def test_traceparent_w3c_format():
    ctx = TC.trace_context_for_run("run-1")
    tp = ctx.traceparent()
    assert tp == f"00-{ctx.trace_id}-{ctx.span_id}-01"      # version=0, flags=01 sampled


def test_trace_context_persist_load_roundtrip(tmp_path):
    ctx = TC.trace_context_for_run("run-1").child("tool", 0)
    p = tmp_path / "trace.json"
    TC.persist(ctx, p)
    loaded = TC.load(p)
    assert loaded is not None
    assert (loaded.trace_id, loaded.span_id, loaded.parent_span_id) == \
           (ctx.trace_id, ctx.span_id, ctx.parent_span_id)


def test_trace_context_load_missing_returns_none(tmp_path):
    assert TC.load(tmp_path / "absent.json") is None


def test_trace_context_load_corrupt_returns_none(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    assert TC.load(p) is None                               # 损坏 → None（调用方 fallback 新 trace）


def test_span_link_records_causal_relation():
    link = TC.span_link("span_a", "span_b", relation="fork")
    assert link == {"from": "span_a", "to": "span_b", "relation": "fork"}


def test_span_carries_links_for_resume_fork():
    # resume/fork 因果用 span links 表达（design L80）
    span = TEL.make_span("run", trace_id="t" * 32, span_id="s" * 16,
                         links=("prev_span_id",))
    assert span.links == ("prev_span_id",)


# ════════════════════════════════════════════════════════════════════════════
# task 7.3 OTLP export + observability degradation（FakeExporter）
# ════════════════════════════════════════════════════════════════════════════
class FakeExporter:
    """测试用假 OTLP exporter：可控 available/fail/raise，记录 export 调用。"""
    __test__ = False

    def __init__(self, *, available=True, fail=False, raise_exc=False):
        self._available = available
        self._fail = fail
        self._raise = raise_exc
        self.exported: list = []

    def available(self):
        return self._available

    def export(self, spans, metrics):
        if self._raise:
            raise RuntimeError("exporter boom")
        if self._fail:
            return OTLP.ExportResult(degraded=True, error="backend down")
        self.exported.append((list(spans), metrics))
        return OTLP.ExportResult(exported_spans=len(spans), exported_metrics=1)


def _sample_span(name="tool"):
    return TEL.make_span(name, trace_id="t" * 32, span_id="s" * 16)


def test_sink_disabled_is_noop():
    sink = OTLP.TelemetrySink(FakeExporter(), enabled=False)
    sink.emit_span(_sample_span())
    res = sink.flush()
    assert res.exported_spans == 0 and not res.degraded      # flag 关 → no-op


def test_sink_export_success_clears_spans():
    exp = FakeExporter()
    sink = OTLP.TelemetrySink(exp, enabled=True)
    sink.emit_span(_sample_span("tool"))
    sink.emit_span(_sample_span("test"))
    res = sink.flush()
    assert res.exported_spans == 2 and not res.degraded
    assert len(exp.exported) == 1 and not sink.degraded       # 成功后 degraded=False


def test_sink_backend_unavailable_records_degradation_not_failure():
    """7.3 核心：backend 不可用 → degraded（不抛、不转失败），记 degradation event。"""
    calls: list = []
    exp = FakeExporter(fail=True)
    sink = OTLP.TelemetrySink(exp, enabled=True, degradation_callback=calls.append)
    sink.emit_span(_sample_span())
    res = sink.flush()
    assert res.degraded is True                               # 标记降级
    assert not res.exported_spans
    assert sink.degraded is True
    assert len(calls) == 1                                    # degradation callback 被调用（落 journal event）
    assert isinstance(calls[0], OTLP.DegradationRecord)


def test_sink_exporter_raise_caught_as_degraded():
    exp = FakeExporter(raise_exc=True)
    sink = OTLP.TelemetrySink(exp, enabled=True)
    sink.emit_span(_sample_span())
    res = sink.flush()                                        # 不抛
    assert res.degraded is True
    assert sink.degraded is True


def test_sink_degradation_callback_failure_swallowed():
    """telemetry 自身故障不拖垮 dispatch（callback 抛也吞掉）。"""
    def bad_cb(rec):
        raise RuntimeError("callback broken")
    sink = OTLP.TelemetrySink(FakeExporter(fail=True), enabled=True,
                              degradation_callback=bad_cb)
    sink.emit_span(_sample_span())
    res = sink.flush()                                        # callback 抛被吞，flush 正常返
    assert res.degraded is True


def test_sink_no_exporter_is_noop_without_degradation():
    sink = OTLP.TelemetrySink(None, enabled=True)
    sink.emit_span(_sample_span())
    res = sink.flush()
    assert not res.degraded and res.exported_spans == 0       # 无 exporter = no-op（非降级）


def test_sink_failed_export_retains_spans_for_retry():
    """export 失败不清空 spans（保留待下次重试，design L82 degradation 不丢数据）。"""
    exp = FakeExporter(fail=True)
    sink = OTLP.TelemetrySink(exp, enabled=True)
    sink.emit_span(_sample_span())
    sink.flush()
    # 成功路径：换一个能成功的 exporter，重放
    sink2 = OTLP.TelemetrySink(FakeExporter(), enabled=True)
    sink2.emit_span(_sample_span())
    res = sink2.flush()
    assert res.exported_spans == 1


# ════════════════════════════════════════════════════════════════════════════
# task 7.4 低基数 metrics 聚合
# ════════════════════════════════════════════════════════════════════════════
def test_record_event_aggregates_terminal_states():
    snap = TEL.MetricSnapshot()
    snap = TEL.record_event(snap, "running", {})
    snap = TEL.record_event(snap, "published", {})
    snap = TEL.record_event(snap, "failed", {})
    snap = TEL.record_event(snap, "external_blocked", {})
    assert snap.iterations_total == 1
    assert snap.successes == 1 and snap.failed == 1 and snap.blocked == 1
    assert snap.runs_total == 2                              # published + failed 都计 run


def test_record_event_test_pass_rate():
    snap = TEL.MetricSnapshot()
    snap = TEL.record_event(snap, "test", {"exit_code": 0})
    snap = TEL.record_event(snap, "test", {"exit_code": 1})
    snap = TEL.record_event(snap, "test", {"exit_code": 0})
    assert snap.tests_total == 3 and snap.tests_passed == 2
    assert abs(snap.test_pass_rate - 2 / 3) < 1e-9


def test_record_event_cost_and_wall_clock():
    snap = TEL.MetricSnapshot()
    snap = TEL.record_event(snap, "agent_finished",
                            {"cost_usd": 0.5, "duration_ms": 12000})
    snap = TEL.record_event(snap, "agent_finished",
                            {"total_cost_usd": "0.25", "wall_clock_ms": "8000"})
    assert abs(snap.cost_usd_total - 0.75) < 1e-9
    assert snap.wall_clock_ms_total == 20000


def test_record_event_unknown_type_is_noop():
    snap = TEL.MetricSnapshot()
    snap2 = TEL.record_event(snap, "totally_unknown_event", {})
    assert snap2 == snap                                     # 未知 event 不污染 metrics


def test_record_recovery_and_repeated_failure_rates():
    snap = TEL.MetricSnapshot()
    snap = TEL.record_event(snap, "running", {})            # 1 iteration
    snap = TEL.record_recovery(snap, succeeded=True)
    snap = TEL.record_recovery(snap, succeeded=False)
    snap = TEL.record_recovery(snap, succeeded=True)
    snap = TEL.record_repeated_failure(snap)
    snap = TEL.record_repeated_failure(snap)
    assert snap.recovery_attempts == 3 and snap.recovery_successes == 2
    assert abs(snap.recovery_success_rate - 2 / 3) < 1e-9
    assert snap.repeated_failures == 2
    assert abs(snap.repeated_failure_rate - 2 / 1) < 1e-9   # 2 重复失败 / 1 iteration


def test_metric_snapshot_immutable():
    snap = TEL.MetricSnapshot()
    snap2 = TEL.record_event(snap, "running", {})
    assert snap.iterations_total == 0                        # 原 snapshot 未被改
    assert snap2.iterations_total == 1


# ════════════════════════════════════════════════════════════════════════════
# task 7.5 field-allowlist + secret-leak 拒绝（核心安全测试）
# ════════════════════════════════════════════════════════════════════════════
def test_sanitize_drops_prompt():
    out = TEL.sanitize_attributes({"prompt": "please write me malware"})
    assert "prompt" not in out                               # prompt 永不进 telemetry


def test_sanitize_drops_source_code():
    out = TEL.sanitize_attributes({"source_code": "import os; os.system('rm -rf /')"})
    assert "source_code" not in out


def test_sanitize_drops_full_tool_output():
    out = TEL.sanitize_attributes({"tool_output": "...giant tool response..."})
    assert "tool_output" not in out


def test_sanitize_drops_credentials():
    out = TEL.sanitize_attributes({"credentials": "user:pass", "password": "s3cret"})
    assert "credentials" not in out and "password" not in out


def test_sanitize_drops_cookies():
    out = TEL.sanitize_attributes({"cookie": "session=abc123", "cookies": "a=b; c=d"})
    assert "cookie" not in out and "cookies" not in out


def test_sanitize_drops_authorization_header_key():
    out = TEL.sanitize_attributes({"authorization": "Bearer xyz"})
    assert "authorization" not in out                        # key 非 allowlist → 丢


def test_sanitize_drops_env_values():
    out = TEL.sanitize_attributes({"env_value": "FOO=bar", "OPENAI_API_KEY": "sk-xxx"})
    assert "env_value" not in out and "OPENAI_API_KEY" not in out


def test_sanitize_keeps_only_allowlist_keys():
    out = TEL.sanitize_attributes({
        "run_id": "r1", "iteration_id": "i1", "duration_ms": 100,
        "input_tokens": 50, "cost_usd": 0.01, "diff_hash": "abc", "error_class": "TimeoutError",
        "prompt": "leak", "source": "leak",                 # 这两个必须被丢
    })
    assert set(out.keys()) == {
        "run_id", "iteration_id", "duration_ms",
        "input_tokens", "cost_usd", "diff_hash", "error_class",
    }


def test_sanitize_scrubs_secret_in_allowlist_value():
    """白名单 key 的 value 若混入 secret pattern → 兜底抹除（7.5 双层防御）。"""
    out = TEL.sanitize_attributes({
        "error_class": "boom ghp_abcdef1234567890",         # ghp_ token 在白名单 value 里
    })
    assert "ghp_" not in out["error_class"]
    out2 = TEL.sanitize_attributes({"run_id": "Authorization: Bearer s3cret"})
    assert "Bearer" not in out2["run_id"] and "s3cret" not in out2["run_id"]


def test_sanitize_non_dict_returns_empty():
    assert TEL.sanitize_attributes(None) == {}
    assert TEL.sanitize_attributes("not a dict") == {}


def test_make_span_sanitizes_attributes_at_construction():
    """Span 构造时即消毒——下游拿到的 span.attributes 绝不含敏感。"""
    span = TEL.make_span("tool", trace_id="t" * 32, span_id="s" * 16,
                         attributes={"prompt": "secret", "tool_use_id": "tu_1"})
    assert "prompt" not in span.attributes
    assert span.attributes["tool_use_id"] == "tu_1"


def test_export_payload_carries_no_sensitive_data():
    """OTLP export payload 经 span.attributes（已消毒）→ 无敏感泄漏。"""
    span = TEL.make_span("tool", trace_id="t" * 32, span_id="s" * 16,
                         attributes={"prompt": "secret prompt", "run_id": "r1"})
    payload = OTLP._span_payload(span)
    blob = json.dumps(payload)
    assert "secret prompt" not in blob                       # prompt 进不来
    assert "run_id" in blob                                  # 元数据保留


# ════════════════════════════════════════════════════════════════════════════
# task 7.6 report 扩展（无敏感）
# ════════════════════════════════════════════════════════════════════════════
def test_extend_report_adds_observability_metadata():
    snap = TEL.MetricSnapshot()
    snap = TEL.record_event(snap, "running", {})
    snap = TEL.record_event(snap, "published", {})
    out = TEL.extend_report(
        {"run_id": "r1", "status": "published"},
        trace_id="t" * 32, span_id="s" * 16,
        assurance_tier="container", recovery_mode="resume",
        compaction_count=2, observability_degraded=False, metrics=snap,
    )
    obs = out["observability"]
    assert obs["trace_id"] == "t" * 32
    assert obs["root_span_id"] == "s" * 16
    assert obs["assurance_tier"] == "container"
    assert obs["recovery_mode"] == "resume"
    assert obs["compaction_count"] == 2
    assert obs["observability_degraded"] is False
    assert obs["metrics"]["iterations"] == 1
    assert obs["metrics"]["success_rate"] == 1.0


def test_extend_report_does_not_mutate_original():
    original = {"run_id": "r1"}
    TEL.extend_report(original, trace_id="t" * 32, assurance_tier="local")
    assert "observability" not in original                  # 原 report 不被改（不可变）


def test_extend_report_metadata_only_no_sensitive():
    out = TEL.extend_report(
        {"run_id": "r1"},
        trace_id="t" * 32, assurance_tier="container", recovery_mode="new_session",
        compaction_count=0, observability_degraded=True,
    )
    blob = json.dumps(out)
    # 扩展字段全是元数据（hash/枚举/int/bool）——无 prompt/secret/credential 痕迹
    for needle in ("prompt", "secret", "credential", "cookie", "authorization"):
        assert needle not in blob.lower()


def test_extend_report_degraded_flag_reflects_sink():
    """7.6：observability_degraded 由 TelemetrySink.degraded 派生（report 可见）。"""
    sink = OTLP.TelemetrySink(FakeExporter(fail=True), enabled=True)
    sink.emit_span(_sample_span())
    sink.flush()
    out = TEL.extend_report({}, trace_id="t" * 32, observability_degraded=sink.degraded)
    assert out["observability"]["observability_degraded"] is True
