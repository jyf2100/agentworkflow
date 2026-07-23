"""test_coordinator.py — task 2.1 production runtime coordinator 回归测试。

design 决策#1：把 journal/artifacts/IDs/retry/hooks/sandbox/telemetry/reconciliation 收敛到一个
coordinator 边界，``dispatch_one`` 与 ``dev-agent`` 共用它——**一次解析所有 loop flag**，集中 own
运行时设施，不再散建（spec durable-runtime-integration「Production runtime coordinator」两个 scenario）。

本文件覆盖 task 2.1 的 coordinator **骨架**契约（flags 一次解析 + 稳定 IDs + own journal/artifact
+ lifecycle emit + iteration 衍生 + baseline 保留）；hooks/sandbox/telemetry adapter 的生产 wiring
由 task 2.3 / Section 5 / Section 6 在 coordinator 上挂载（从 ``coord.flags`` 读 flag，不再各自 resolve）。

纯逻辑层（DI ``tmp_path`` + 固定 ``stamp_fn``），零 SDK/零系统时间；AAA；跑：
``python3 -m pytest scripts/test_coordinator.py -q``
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import coordinator as CO  # noqa: E402
import ids as loop_ids  # noqa: E402
import journal as J  # noqa: E402
import trace_context as TC  # noqa: E402
from feature_flags import LoopFlags  # noqa: E402
from loop_runtime import ShadowJournal  # noqa: E402


_STAMP = "20260722"


def _build(tmp_path, *, profile=None, env=None):
    """造一个 coordinator（固定 stamp_fn，确定性）。"""
    return CO.build_coordinator(
        stamp=_STAMP, prd_path="prd/proj/x.md", proj="proj", slug="x",
        state_dir=tmp_path, profile=profile, env=env,
        stamp_fn=lambda: "2026-07-22T00:00:00Z",
    )


# ─── 一次解析 flag：env > profile > 默认；产出冻结快照 ─────────────────────────
def test_build_resolves_flags_once_as_immutable_snapshot(tmp_path):
    """design 决策#1「resolves flags once」：coordinator 是唯一 resolve 点。env 压 profile，
    产出 ``LoopFlags`` 冻结快照——后续 adapter 从 ``coord.flags`` 读，不再各自 ``resolve_flags``。"""
    # Arrange — profile 开 journal_shadow + lifecycle_hooks；env kill lifecycle_hooks（env 优先）
    prof = {"loop": {"journal_shadow": True, "lifecycle_hooks": True}}
    env = {"PA_LOOP_LIFECYCLE_HOOKS": "false"}

    # Act
    coord = _build(tmp_path, profile=prof, env=env)

    # Assert — env 压 profile；快照是 LoopFlags 冻结实例
    assert coord.flags.journal_shadow is True
    assert coord.flags.lifecycle_hooks is False     # env kill 压过 profile
    assert isinstance(coord.flags, LoopFlags)


# ─── 稳定确定性 ID（同输入→同 ID，来自 loop_ids 单一源头，前缀可辨）─────────────
def test_build_creates_stable_deterministic_ids(tmp_path):
    """spec「Enabled runtime uses coordinator ... creates stable run/PRD/iteration IDs」。
    ID 必须确定性（崩溃重放产同 id，reducer dedup 依据）且来自 ``loop_ids`` 单一源头。"""
    # Act
    c1 = _build(tmp_path)
    c2 = _build(tmp_path)

    # Assert — 确定性（同输入同 id）+ 等于 loop_ids + 前缀可辨
    assert c1.run_id == c2.run_id == loop_ids.run_id(_STAMP)
    assert c1.prd_id == c2.prd_id == loop_ids.prd_id("prd/proj/x.md")
    assert c1.iteration_id == loop_ids.iteration_id(c1.run_id, c1.prd_id, 0)
    assert c1.run_id.startswith("run_")
    assert c1.prd_id.startswith("prd_")
    assert c1.iteration_id.startswith("iter_")


# ─── own journal：ShadowJournal 实例，enabled 跟 journal_shadow flag，run_id 绑定 ─
def test_build_owns_journal_enabled_matches_shadow_flag(tmp_path):
    """coordinator own journal（``ShadowJournal``）；``enabled`` 跟 ``journal_shadow`` flag，
    ``run_id`` 绑定——替代 dispatch_one 散建的 ``_sj``。"""
    # Arrange — shadow 开 / 关
    c_on = _build(tmp_path, profile={"loop": {"journal_shadow": True}})
    c_off = _build(tmp_path)

    # Assert
    assert isinstance(c_on.journal, ShadowJournal)
    assert c_on.journal.enabled is True
    assert c_on.journal.run_id == c_on.run_id
    assert c_off.journal.enabled is False


# ─── enabled coordinator emit lifecycle 事件（在首个副作用前可观测）──────────────
def test_enabled_coordinator_emit_lifecycle_to_journal(tmp_path):
    """spec「emits lifecycle events before the first external side effect」——enabled 时
    ``coord.emit`` 委托 journal 落盘 lifecycle 事件（planned/running/...），返回 event_id。"""
    # Arrange
    coord = _build(tmp_path, profile={"loop": {"journal_shadow": True}})

    # Act
    eid = coord.emit("planned", payload={"base": "main"})
    coord.emit("running", payload={"round": 1})

    # Assert — 两条 lifecycle 事件落盘，首条 event_id 非空
    events = J.read_events(coord.journal.path)
    assert [e.event_type for e in events] == ["planned", "running"]
    assert eid is not None


# ─── disabled（baseline）：is_baseline、emit no-op、journal 文件不建（无 partial durable）
def test_disabled_coordinator_preserves_baseline(tmp_path):
    """spec「Disabled runtime preserves baseline ... does not silently invoke partial durable
    features」：flags 全关 → ``is_baseline`` True，emit no-op（journal 文件不建），dispatch
    first-phase 决策零变化。"""
    # Arrange — flags 全关（默认）
    coord = _build(tmp_path)

    # Act
    eid = coord.emit("running", payload={"round": 1})

    # Assert — baseline：no-op，不悄悄写 partial durable
    assert coord.is_baseline is True
    assert eid is None
    assert not Path(coord.journal.path).exists()


# ─── next_iteration：distinct + 引用 parent run/prd（确定性）────────────────────
def test_next_iteration_distinct_and_references_parent(tmp_path):
    """spec「Iteration identity ... distinct deterministic iteration ID while preserving a parent
    run/PRD identity」。``next_iteration(seq)`` 衍生 distinct id（task 3.3 revise/resume/fork 用），
    parent run/prd 一致，确定性。"""
    # Arrange
    coord = _build(tmp_path)

    # Act
    iter1 = coord.next_iteration(1)
    iter2 = coord.next_iteration(2)

    # Assert — distinct + 等于 loop_ids 单一源头（parent run/prd + seq）
    assert iter1 != iter2 != coord.iteration_id
    assert iter1 == loop_ids.iteration_id(coord.run_id, coord.prd_id, 1)
    assert iter2 == loop_ids.iteration_id(coord.run_id, coord.prd_id, 2)


# ─── artifact_root 在 state_dir/artifacts/<run_id>（内容寻址工件存储根）──────────
def test_artifact_root_keyed_by_run_under_state_dir(tmp_path):
    """coordinator own artifact store 根：``state_dir/artifacts/<run_id>``——per-run 隔离的
    内容寻址工件存储（feedback/snapshot/transcript 落盘处，task 3.2/4.3 用）。"""
    # Act
    coord = _build(tmp_path)

    # Assert
    assert coord.artifact_root == str(tmp_path / "artifacts" / coord.run_id)


# ════════════════════════════════════════════════════════════════════════════
# task 2.5：preflight 校验 loop flag 组合一致性（design 决策#1 防 impossible partial 组合）
# ════════════════════════════════════════════════════════════════════════════
def test_preflight_accepts_baseline_and_self_consistent_combos():
    """baseline（全关）+ 自洽组合（shadow 单开 / shadow+hooks / shadow+driven+hooks）→ ok。

    design 决策#1：散建 flag 允许 impossible 组合；preflight 一次校验所有依赖链。自洽组合放行。"""
    # Arrange — 自洽组合（依赖满足）
    ok_cases = [
        LoopFlags(),                                                          # baseline 全关
        LoopFlags(journal_shadow=True),                                       # shadow 单开
        LoopFlags(journal_shadow=True, lifecycle_hooks=True),                # shadow+hooks（hooks 依赖满足）
        LoopFlags(journal_shadow=True, journal_driven_dispatch=True,
                  lifecycle_hooks=True, session_aware_retry=True),           # shadow 驱动其余（全依赖满足）
    ]
    # Act + Assert
    for flags in ok_cases:
        r = CO.preflight(flags)
        assert r.is_ok, f"应放行自洽组合 {flags}"
        assert r.blocked is None


@pytest.mark.parametrize("bad_flags,violated_substring", [
    (LoopFlags(journal_driven_dispatch=True), "journal_driven_dispatch requires journal_shadow"),
    (LoopFlags(session_aware_retry=True), "session_aware_retry requires journal_shadow"),
    (LoopFlags(lifecycle_hooks=True), "lifecycle_hooks requires journal_shadow"),
])
def test_preflight_rejects_flag_without_journal_dependency(bad_flags, violated_substring):
    """design 决策#1：driven/retry/hooks 开但 journal_shadow 关 = impossible partial 组合 → blocked。

    依赖链（design 决策#2 cutover + #8 渐进）：
        journal_driven_dispatch ⇒ journal_shadow（driven 必须先 shadow）
        session_aware_retry     ⇒ journal_shadow（retry 需 journal 持久化 session）
        lifecycle_hooks         ⇒ journal_shadow（hooks 需 journal 落盘事件）
    """
    # Act
    r = CO.preflight(bad_flags)
    # Assert — 结构化 blocked reason，含具体违规描述
    assert not r.is_ok
    assert r.blocked is not None
    assert any(violated_substring in v for v in r.blocked.violations)


def test_preflight_records_all_violations_structured():
    """task 2.5「record a structured blocked reason」：多条依赖同时违 → violations 全列出（不漏报）。"""
    # Arrange — driven + retry + hooks 全开但 shadow 关（3 条依赖链全违）
    flags = LoopFlags(journal_driven_dispatch=True, session_aware_retry=True,
                      lifecycle_hooks=True, journal_shadow=False)
    # Act
    r = CO.preflight(flags)
    # Assert — 3 条违规全记录，reason 含 journal_shadow
    assert not r.is_ok
    assert len(r.blocked.violations) == 3
    assert "journal_shadow" in r.blocked.reason


# ─── task 3.1：immutable PRD content digest（content-addressed prd_id + planned event 真源）──
def test_build_with_prd_content_captures_digest_and_content_addresses_prd_id(tmp_path):
    """spec「Immutable new-run input」+ task 3.1：build_coordinator(prd_content=...) 在 dispatch entry
    捕获 PRD 内容 digest（artifact_store ``sha256:<hex>`` 格式），Coordinator.prd_digest 存真源，prd_id
    纳入 content_hash（content-addressed：PRD 改动→新 prd_id，不可变真源；ids.prd_id 已支持 content_hash）。
    """
    import artifact_store
    # Arrange
    content = "# PRD\n实现 X\n验收: tests green"
    expected_digest = artifact_store.compute_digest(content.encode("utf-8"))
    # Act
    coord = CO.build_coordinator(
        stamp=_STAMP, prd_path="prd/proj/x.md", proj="proj", slug="x",
        state_dir=tmp_path, env={}, stamp_fn=lambda: "T", prd_content=content,
    )
    # Assert — digest 捕获 + prd_id content-addressed（含 content_hash，≠ path-only）
    assert coord.prd_digest == expected_digest
    assert coord.prd_id == loop_ids.prd_id("prd/proj/x.md", expected_digest)
    assert coord.prd_id != loop_ids.prd_id("prd/proj/x.md")


def test_build_without_prd_content_baseline_has_no_digest_path_only_prd_id(tmp_path):
    """baseline：prd_content=None → prd_digest=None（无 content 捕获），prd_id path-only（向后兼容：
    test_build_creates_stable_deterministic_ids 仍 path-only；旧调用方不传 prd_content 不破）。"""
    coord = CO.build_coordinator(
        stamp=_STAMP, prd_path="prd/proj/x.md", proj="proj", slug="x",
        state_dir=tmp_path, env={}, stamp_fn=lambda: "T",
    )
    assert coord.prd_digest is None
    assert coord.prd_id == loop_ids.prd_id("prd/proj/x.md")     # path-only，向后兼容


def test_prd_digest_differs_when_prd_content_changes(tmp_path):
    """不可变真源：PRD 内容改 → digest 变 → prd_id 变（每个 PRD 版本独立 content-addressed id；
    spec「original PRD remains byte-for-byte unchanged」由 digest 锚定具体内容版本）。"""
    c1 = CO.build_coordinator(stamp=_STAMP, prd_path="p.md", proj="p", slug="s",
                              state_dir=tmp_path, env={}, stamp_fn=lambda: "T", prd_content="v1")
    c2 = CO.build_coordinator(stamp=_STAMP, prd_path="p.md", proj="p", slug="s",
                              state_dir=tmp_path, env={}, stamp_fn=lambda: "T", prd_content="v2")
    assert c1.prd_digest != c2.prd_digest
    assert c1.prd_id != c2.prd_id


# ════════════════════════════════════════════════════════════════════════════
# Section 6 task 6.1：coordinator own root trace + propagation through operations
# design 决策#1：telemetry 从 coord 派生，非 disconnected helper。trace_context 已实现 generic
# root + child；6.1 把它连到 coordinator——per PRD run 建 root trace + 为子 operations 派生子 span。
# ════════════════════════════════════════════════════════════════════════════
def test_coordinator_owns_root_trace_per_run(tmp_path):
    """6.1：build_coordinator → coord.trace 是 root TraceContext（trace_id 由 run_id 派生，无 parent）。"""
    coord = _build(tmp_path)
    assert coord.trace.parent_span_id is None
    assert coord.trace.trace_id == TC.new_trace_id(coord.run_id)


def test_coordinator_root_trace_stable_for_same_run(tmp_path):
    """6.1：同 run → 同 root trace（确定性，跨进程/重放稳定，design 决策#1 稳定 ID）。"""
    c1 = _build(tmp_path)
    c2 = _build(tmp_path)
    assert c1.trace.trace_id == c2.trace.trace_id


def test_coordinator_child_span_propagates_trace(tmp_path):
    """6.1：child_span 派生子 span——trace_id 不变 + parent 指向 root span（propagation）。"""
    coord = _build(tmp_path)
    child = coord.child_span("test")
    assert child.trace_id == coord.trace.trace_id
    assert child.parent_span_id == coord.trace.span_id
    assert child.span_id != coord.trace.span_id


def test_coordinator_child_span_covers_seven_operations(tmp_path):
    """6.1：7 子 operation（iteration/sdk_session/tool/test/verify/reconcile/publish）都能派生 child span。"""
    coord = _build(tmp_path)
    for op in ("iteration", "sdk_session", "tool", "test", "verify", "reconcile", "publish"):
        child = coord.child_span(op)
        assert child.trace_id == coord.trace.trace_id
        assert child.parent_span_id == coord.trace.span_id


def test_coordinator_child_span_rejects_run_and_unknown(tmp_path):
    """6.1：run 是 root（不作为 child）+ 未知 operation → ValueError。"""
    coord = _build(tmp_path)
    with pytest.raises(ValueError):
        coord.child_span("run")
    with pytest.raises(ValueError):
        coord.child_span("bogus")


def test_coordinator_trace_present_in_baseline(tmp_path):
    """6.1：baseline（telemetry flag 关）coord.trace 仍建（metadata-only，无 export 副作用，不依赖 flag）。"""
    coord = _build(tmp_path)
    assert coord.is_baseline
    assert coord.trace is not None
    assert coord.trace.parent_span_id is None


# ════════════════════════════════════════════════════════════════════════════
# Section 6 task 6.2：connect OTLP export + degradation journaling to coordinator
# design 决策#1：coordinator own telemetry sink（从 flags.telemetry_export 读 enabled，非 disconnected
# helper）。bounded timeout（HttpOtlpExporter.timeout）+ metadata-only（make_span 内 sanitize_attributes）+
# backend 不可用 → 落 journal 'telemetry_degraded' event（design L82 可见，不拖垮 dispatch）。
# TelemetrySink/exporter/degradation 语义已在 test_telemetry 覆盖；此处只验 coordinator wiring。
# ════════════════════════════════════════════════════════════════════════════
class _FakeExporter:
    """6.2 测试桩 OTLP exporter（可控 fail，记录 export 调用，同 test_telemetry.FakeExporter 形态）。"""
    __test__ = False

    def __init__(self, *, fail=False):
        self._fail = fail
        self.exported: list = []

    def available(self):
        return not self._fail

    def export(self, spans, metrics):   # noqa: ARG002
        import otlp_export as OTLP
        self.exported.append((list(spans), metrics))
        if self._fail:
            return OTLP.ExportResult(degraded=True, error="backend down (test)")
        return OTLP.ExportResult(exported_spans=len(spans), exported_metrics=1)


def _telem(tmp_path, *, exporter=None, journal=False):
    """造 telemetry_export 开的 coordinator（DI exporter；可选 journal_shadow 同开）。"""
    prof = {"loop": {"telemetry_export": True}}
    if journal:
        prof["loop"]["journal_shadow"] = True
    return CO.build_coordinator(
        stamp=_STAMP, prd_path="prd/proj/x.md", proj="proj", slug="x",
        state_dir=tmp_path, profile=prof, env={},
        stamp_fn=lambda: "2026-07-22T00:00:00Z",
        telemetry_exporter=exporter,
    )


def test_coordinator_owns_telemetry_sink_enabled_when_flag_on(tmp_path):
    """6.2：telemetry_export flag 开 → coord.telemetry_sink 是 TelemetrySink，enabled=True（coord own）。"""
    import otlp_export as OTLP
    coord = _telem(tmp_path, exporter=_FakeExporter())
    assert isinstance(coord.telemetry_sink, OTLP.TelemetrySink)
    assert coord.telemetry_sink.enabled is True


def test_coordinator_telemetry_sink_disabled_in_baseline(tmp_path):
    """6.2：baseline（telemetry_export 关）→ sink.enabled=False，flush no-op（flag off → no-op，design 决策#8）。"""
    coord = _build(tmp_path)
    assert coord.is_baseline
    assert coord.telemetry_sink.enabled is False
    res = coord.flush_telemetry()
    assert res.exported_spans == 0
    assert res.degraded is False


def test_coordinator_emit_telemetry_span_is_metadata_only(tmp_path):
    """6.2：emit_telemetry_span 的 attributes 经 sanitize——非白名单 key（prompt/source）丢、白名单 key 的
    secret value 抹（metadata-only，design L80 绝不记 prompt/source/secret）。"""
    exp = _FakeExporter()
    coord = _telem(tmp_path, exporter=exp)
    coord.emit_telemetry_span(
        "test", attributes={
            "status": "ok",                         # 白名单
            "input_tokens": 100,                    # 白名单（数值）
            "prompt": "leak me",                    # 非白名单 → 整个 field 丢
            "source": "def f(): ...",               # 非白名单 → 丢
            "error_class": "Bearer ghp_leaked123",  # 白名单 key + secret value → value 抹
        })
    res = coord.flush_telemetry()
    assert res.exported_spans == 1
    span = exp.exported[0][0][0]
    assert "prompt" not in span.attributes                # 非白名单丢
    assert "source" not in span.attributes
    assert span.attributes["status"] == "ok"
    assert span.attributes["input_tokens"] == 100
    assert "ghp_leaked123" not in span.attributes["error_class"]   # secret value 抹
    assert span.attributes["error_class"] != "Bearer ghp_leaked123"


def test_coordinator_emit_telemetry_span_rejects_run_and_unknown(tmp_path):
    """6.2：emit_telemetry_span('run')/未知 operation → ValueError（run 是 root，6.1 已立 propagation 边界）。"""
    coord = _telem(tmp_path, exporter=_FakeExporter())
    with pytest.raises(ValueError):
        coord.emit_telemetry_span("run")
    with pytest.raises(ValueError):
        coord.emit_telemetry_span("bogus")


def test_coordinator_emit_telemetry_span_propagates_trace(tmp_path):
    """6.2：emit 的 span trace_id == coord root trace，parent 指向 root span（propagation，6.1 延续）。"""
    exp = _FakeExporter()
    coord = _telem(tmp_path, exporter=exp)
    coord.emit_telemetry_span("verify")
    coord.flush_telemetry()
    span = exp.exported[0][0][0]
    assert span.trace_id == coord.trace.trace_id
    assert span.parent_span_id == coord.trace.span_id


def test_coordinator_flush_exports_collected_spans_with_bounded_exporter(tmp_path):
    """6.2：emit 多 span → flush 经 DI exporter export（bounded timeout 由 exporter 持有；coord 只 connect）。"""
    exp = _FakeExporter()
    coord = _telem(tmp_path, exporter=exp)
    coord.emit_telemetry_span("iteration")
    coord.emit_telemetry_span("test", attributes={"status": "ok"})
    res = coord.flush_telemetry()
    assert res.exported_spans == 2
    assert len(exp.exported) == 1                         # 一次 flush 一次 export
    assert {s.name for s in exp.exported[0][0]} == {"iteration", "test"}


def test_coordinator_degradation_journaled_on_backend_unavailable(tmp_path):
    """6.2：backend 不可用 → flush 返 degraded（不抛）+ 落 journal 'telemetry_degraded' event（design L82 可见）。

    telemetry outage 不拖垮 dispatch，但记一次可见事件（journal enabled 时落盘 + sink.degraded 给 report 6.3）。
    """
    exp = _FakeExporter(fail=True)
    coord = _telem(tmp_path, exporter=exp, journal=True)
    coord.emit_telemetry_span("test")
    res = coord.flush_telemetry()                         # 不抛
    assert res.degraded is True
    events = J.read_events(coord.journal.path)
    assert any(e.event_type == "telemetry_degraded" for e in events)
    assert coord.telemetry_sink.degraded is True


def test_coordinator_degradation_does_not_crash_when_journal_disabled(tmp_path):
    """6.2：telemetry 开但 journal 关 → backend 不可用时 flush 仍不抛（callback 落 disabled journal = no-op）。

    baseline 保留：telemetry_export 不强依赖 journal_shadow（preflight 无此依赖链）；可见性经 sink.degraded
    给 report 6.3，journal event 是 bonus。"""
    exp = _FakeExporter(fail=True)
    coord = _telem(tmp_path, exporter=exp, journal=False)
    coord.emit_telemetry_span("test")
    res = coord.flush_telemetry()                         # 不抛
    assert res.degraded is True
    assert coord.telemetry_sink.degraded is True
    assert not Path(coord.journal.path).exists()          # journal 关 → 文件不建


# ════════════════════════════════════════════════════════════════════════════
# Section 6 task 6.3：coordinator own report 元数据（build_report + 派生 property）
# spec task 6.3：「Extend reports with journal authority, trace ID, assurance tier, recovery mode,
# semantic verdict, evidence integrity, compaction count, and observability degradation.」
# design 决策#1：coordinator 是元数据唯一来源（trace/flags/sink.degraded）→ own build_report；
# 运行时业务态（semantic_verdict/evidence_integrity/recovery_mode/compaction_count）由调用方传。
# extend_report 补 journal_authority/semantic_verdict/evidence_integrity（见 test_telemetry）。
# ════════════════════════════════════════════════════════════════════════════
def test_coordinator_assurance_tier_from_container_flag(tmp_path):
    """6.3：container_sandbox 开 → assurance_tier='container'；关 → 'local'（configured intent，coord 从 flags 派生）。"""
    c_on = _build(tmp_path, profile={"loop": {"container_sandbox": True}})
    c_off = _build(tmp_path)
    assert c_on.assurance_tier == "container"
    assert c_off.assurance_tier == "local"


def test_coordinator_journal_authority_from_flags(tmp_path):
    """6.3：journal authority 从 flags 派生（decision#2 cutover 阶段）：
    driven→'driven'；shadow→'shadow'；baseline→'legacy'。"""
    c_driven = _build(tmp_path, profile={"loop": {"journal_driven_dispatch": True, "journal_shadow": True}})
    c_shadow = _build(tmp_path, profile={"loop": {"journal_shadow": True}})
    c_base = _build(tmp_path)
    assert c_driven.journal_authority == "driven"
    assert c_shadow.journal_authority == "shadow"
    assert c_base.journal_authority == "legacy"


def test_coordinator_build_report_includes_coord_owned_metadata(tmp_path):
    """6.3：build_report 把 coord own 的元数据（trace_id/span_id/tier/journal_authority/degraded）写进 observability。"""
    coord = _build(tmp_path, profile={"loop": {"journal_shadow": True}})
    out = coord.build_report({"run_id": "r1", "status": "published"})
    obs = out["observability"]
    assert obs["trace_id"] == coord.trace.trace_id
    assert obs["root_span_id"] == coord.trace.span_id
    assert obs["assurance_tier"] == coord.assurance_tier
    assert obs["journal_authority"] == coord.journal_authority
    assert obs["observability_degraded"] is False


def test_coordinator_build_report_passes_runtime_fields(tmp_path):
    """6.3：调用方传运行时业务态（semantic_verdict/evidence_integrity/recovery_mode/compaction_count）。"""
    coord = _build(tmp_path)
    out = coord.build_report(
        {"run_id": "r1"}, semantic_verdict="pass", evidence_integrity="ok",
        recovery_mode="resume", compaction_count=3)
    obs = out["observability"]
    assert obs["semantic_verdict"] == "pass"
    assert obs["evidence_integrity"] == "ok"
    assert obs["recovery_mode"] == "resume"
    assert obs["compaction_count"] == 3


def test_coordinator_build_report_degradation_reflected(tmp_path):
    """6.3：telemetry backend 不可用 → build_report observability_degraded=True（coord own sink.degraded）。"""
    exp = _FakeExporter(fail=True)
    coord = _telem(tmp_path, exporter=exp, journal=True)
    coord.emit_telemetry_span("test")
    coord.flush_telemetry()
    out = coord.build_report({"run_id": "r1"})
    assert out["observability"]["observability_degraded"] is True


def test_coordinator_build_report_does_not_mutate_base(tmp_path):
    """6.3：build_report 不改 base report（不可变；extend_report 拷贝）。"""
    coord = _build(tmp_path)
    base = {"run_id": "r1"}
    coord.build_report(base)
    assert "observability" not in base

