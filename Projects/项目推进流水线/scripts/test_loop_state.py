#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_loop_state.py — 持久 loop 数据模型 + 迭代状态机单测（OpenSpec add-durable-loop-runtime task 2.1 + 2.6）。

第二阶段把「验证/重试分散在 JSON/日志/被追加的 PRD/git 分支」收敛为 **append-only journal + 显式状态机**
（design 决策#1/#2）。本测试锁定 **domain 真源**：

    task 2.1（数据模型）—— 版本化 journal event、iteration state、artifact ref、failure classification、
        recovery snapshot。所有模型 frozen + 可 JSON 序列化；``JOURNAL_SCHEMA_VERSION`` 文档化演化版本。
    task 2.6（状态机）—— ``validate_transition`` 显式迁移表，**拒绝非法/重复迁移**。spec 核心断言：
        SDK 成功（``agent_finished`` / is_error=False）只能停在 ``AGENT_FINISHED``，**绝不能直跳 published**
        （design 决策#2「SDK 成功 ≠ 已发布」——必须经 test 门 → verifying → publish_ready → published）。

纯逻辑零依赖模块（同 ``evidence``/``external_state`` 既定模式）：单测零 IO、零 SDK 导入，锁定数据模型与
状态机迁移表。reducer（``apply_event``/``reduce``）在本模块提供骨架（status 迁移 + dedup + reject invalid），
payload 字段合并在 task 3.2 增强、journal IO（append/read/损坏检测）在 ``journal`` 模块（task 2.2/2.3）。

跑：python3 -m pytest scripts/test_loop_state.py -q
AAA 结构（Arrange / Act / Assert）。
"""
from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import loop_state as L  # noqa: E402  —— 故意后 sys.path 注入；RED 阶段 ModuleNotFoundError 即预期


# ─── 测试辅助：构造 event / initial state（字段多，集中造以免每用例重复）─────────
def _ev(event_type: str, event_id: str = "e1", **payload) -> L.JournalEvent:
    """造一个最小合法 JournalEvent（run/prd/iteration 固定，payload 透传）。"""
    return L.JournalEvent(
        schema_version=L.JOURNAL_SCHEMA_VERSION,
        event_id=event_id,
        timestamp="2026-07-20T00:00:00Z",
        iteration_id="iter-1",
        run_id="run-1",
        prd_id="prd-1",
        event_type=event_type,
        payload=payload,
    )


def _initial(status: L.IterationStatus = L.IterationStatus.PLANNED) -> L.IterationState:
    """造一个初始 IterationState（status 可调；run/prd/iteration 与 _ev 对齐）。"""
    return L.IterationState(
        iteration_id="iter-1",
        run_id="run-1",
        prd_id="prd-1",
        status=status,
        base="abc1234",
    )


# ════════════════════════════════════════════════════════════════════════
# A. task 2.1：版本化数据模型（字段 / frozen / 序列化 / schema 版本）
# ════════════════════════════════════════════════════════════════════════
def test_journal_schema_version_documented():
    """``JOURNAL_SCHEMA_VERSION`` 是 journal event 的演化版本号——读端按版本路由解析（不兼容时 fail-closed）。

    2.x 阶段锁定为 1；改这个数 = 契约升级，必须同步读端兼容逻辑。"""
    assert isinstance(L.JOURNAL_SCHEMA_VERSION, int)
    assert L.JOURNAL_SCHEMA_VERSION == 1


def test_journal_event_fields_and_frozen():
    """JournalEvent 是 design 决策#1 的「append-only 日志条目」契约：schema_version/event_id/timestamp/
    iteration_id/run_id/prd_id/event_type/payload。每条带稳定 ID（dedup 依据）+ iteration/run/prd 三级归属。"""
    # Arrange / Act
    ev = _ev("running", event_id="evt-42", note="x")
    # Assert：必填字段齐全
    for f in ["schema_version", "event_id", "timestamp", "iteration_id",
              "run_id", "prd_id", "event_type", "payload"]:
        assert hasattr(ev, f), f"JournalEvent 缺字段: {f}"
    assert ev.event_id == "evt-42"
    assert ev.event_type == "running"
    assert ev.payload == {"note": "x"}
    # frozen：调用方不得意外改写（与 evidence/external_state 一致）
    with pytest.raises(dataclasses.FrozenInstanceError):
        ev.event_type = "published"  # type: ignore[misc]


def test_journal_event_json_serializable():
    """journal 落盘是 JSONL——每条 event 必须可直接 ``json.dumps``（payload 是自由 dict，其余字段标量）。

    这是最朴素的「可持久化」契约：模型不带不可序列化成员（无 set/非 str Enum/对象）。"""
    ev = _ev("agent_finished", session_id="s-1", turns=7)
    # Act / Assert：能往返 JSON 而不丢字段
    blob = json.dumps(dataclasses.asdict(ev))
    restored = json.loads(blob)
    assert restored["event_type"] == "agent_finished"
    assert restored["payload"]["session_id"] == "s-1"
    assert restored["schema_version"] == 1


def test_artifact_ref_fields_and_frozen():
    """ArtifactRef 是内容寻址工件引用（task 2.4 工件存储落盘的 in-journal 指针）：digest/size/kind/path/sensitivity。

    ``digest``（sha256:hex）是真源——读端按 digest 校验内容完整性（task 2.5），不信任 path/sizo 元数据。"""
    ref = L.ArtifactRef(digest="sha256:abcd", size=128, kind="test_output",
                        path="artifacts/run-1/out.txt", sensitivity="sanitized")
    for f in ["digest", "size", "kind", "path", "sensitivity"]:
        assert hasattr(ref, f), f"ArtifactRef 缺字段: {f}"
    with pytest.raises(dataclasses.FrozenInstanceError):
        ref.digest = "sha256:0000"  # type: ignore[misc]


def test_failure_classification_fields():
    """FailureClassification 是 RetryPolicy 的机械输入（design 决策#3）：subtype/is_error/stop_reason/
    api_error_status/transient/fingerprint。``transient`` 决定 resume 候选；``fingerprint`` 做重复失败检测（task 5.2）。"""
    fc = L.FailureClassification(subtype="error_max_budget", is_error=True,
                                 stop_reason="max_turns", api_error_status=None,
                                 transient=False, fingerprint="budget:7")
    for f in ["subtype", "is_error", "stop_reason", "api_error_status", "transient", "fingerprint"]:
        assert hasattr(fc, f), f"FailureClassification 缺字段: {f}"


def test_session_run_meta_fields():
    """SessionRunMeta 持久化 SDK session 真源（task 5.1）：session_id/subtype/stop_reason/num_turns/usage/
    total_cost_usd/api_error_status/compaction_count。resume/fork 决策直接消费这些字段。"""
    meta = L.SessionRunMeta(session_id="s-1", subtype="success", stop_reason="end_turn",
                            num_turns=7, usage={"input": 100}, total_cost_usd=0.01,
                            api_error_status=None, compaction_count=0)
    for f in ["session_id", "subtype", "stop_reason", "num_turns", "usage",
              "total_cost_usd", "api_error_status", "compaction_count"]:
        assert hasattr(meta, f), f"SessionRunMeta 缺字段: {f}"


def test_subagent_record_fields():
    """SubagentRecord 记录子代理归属与产出（task 4.6）：agent_id/agent_type/objective/parent_iteration_id/
    tools/effort/status/result_ref。``result_ref`` 指向子代理产出工件（禁止子代理直接发布，只留证据）。"""
    rec = L.SubagentRecord(agent_id="a-1", agent_type="pa-verify", objective="审 PR",
                           parent_iteration_id="iter-1")
    for f in ["agent_id", "agent_type", "objective", "parent_iteration_id",
              "tools", "effort", "status", "result_ref"]:
        assert hasattr(rec, f), f"SubagentRecord 缺字段: {f}"
    assert rec.tools == () and rec.result_ref is None  # 默认空


def test_recovery_snapshot_fields():
    """RecoverySnapshot 是 PreCompact/重试的恢复上下文（task 4.5/5.4）：objective/acceptance_criteria/base/head/
    changed_files/decisions/test_evidence_ref/failures/next_action。由不可变 PRD 内容 + journal 工件合成。"""
    snap = L.RecoverySnapshot(objective="加 X", acceptance_criteria=("测绿",),
                              base="abc1234", head=None, changed_files=("a.py",),
                              decisions=("用方案1",), test_evidence_ref=None,
                              failures=(), next_action="补测试")
    for f in ["objective", "acceptance_criteria", "base", "head", "changed_files",
              "decisions", "test_evidence_ref", "failures", "next_action"]:
        assert hasattr(snap, f), f"RecoverySnapshot 缺字段: {f}"


def test_iteration_state_fields_and_frozen():
    """IterationState 是 reducer 归约出的不可变快照（design 决策#1「不原地覆写」）：status/base/head/session_meta/
    artifacts/test_evidence_ref/last_failure/subagents/recovery_snapshot/last_transition_error/applied_event_ids。

    ``applied_event_ids`` 是 dedup 依据；``last_transition_error`` 记录被拒迁移的原因（status 不变但可审计）。"""
    st = _initial()
    for f in ["iteration_id", "run_id", "prd_id", "status", "base",
              "head", "session_meta", "artifacts", "test_evidence_ref",
              "last_failure", "subagents", "recovery_snapshot",
              "last_transition_error", "applied_event_ids"]:
        assert hasattr(st, f), f"IterationState 缺字段: {f}"
    with pytest.raises(dataclasses.FrozenInstanceError):
        st.status = L.IterationStatus.PUBLISHED  # type: ignore[misc]


def test_iteration_status_enum_complete():
    """IterationStatus 覆盖 design 决策#2 的 11 个迭代态 + state_corrupt（损坏终态）。

    ``state_corrupt`` 是 journal 中部损坏时 reducer 落定的保守终态（spec「fail closed on malformed middle」）。"""
    expected = {"planned", "running", "agent_finished", "test_blocked", "verifying",
                "revise", "external_blocked", "publish_ready", "published",
                "aborted", "failed", "state_corrupt"}
    actual = {s.value for s in L.IterationStatus}
    assert actual == expected, f"IterationStatus 枚举值集合不符: 缺 {expected - actual}, 多 {actual - expected}"


def test_assurance_and_sensitivity_enums():
    """AssuranceTier（task 6.1/6.2 两 tier）+ Sensitivity（工件分层脱敏）+ ArtifactKind（工件类别）枚举稳定。

    改这些 value = 改对外契约（telemetry/sandbox/artifact store 都按字符串值路由）。"""
    assert {t.value for t in L.AssuranceTier} == {"local_worktree", "isolated_container"}
    assert {"public", "sanitized", "internal"} <= {s.value for s in L.Sensitivity}
    assert {"diff", "test_output", "verifier_feedback",
            "recovery_snapshot"} <= {k.value for k in L.ArtifactKind}


# ════════════════════════════════════════════════════════════════════════
# B. task 2.6：显式状态机（合法迁移 / 拒绝非法迁移 / 终态）
# ════════════════════════════════════════════════════════════════════════
def test_validate_transition_allows_legal_pipeline():
    """合法主路径：planned→running→agent_finished→verifying→publish_ready→published 全程放行。

    这是「绿路径」的机械骨架——每一跳都在迁移表里。"""
    chain = [
        (L.IterationStatus.PLANNED, L.IterationStatus.RUNNING),
        (L.IterationStatus.RUNNING, L.IterationStatus.AGENT_FINISHED),
        (L.IterationStatus.AGENT_FINISHED, L.IterationStatus.VERIFYING),
        (L.IterationStatus.VERIFYING, L.IterationStatus.PUBLISH_READY),
        (L.IterationStatus.PUBLISH_READY, L.IterationStatus.PUBLISHED),
    ]
    for cur, nxt in chain:
        ok, reason = L.validate_transition(cur, nxt)
        assert ok, f"合法迁移被拒 {cur}->{nxt}: {reason}"


def test_validate_transition_allows_planned_to_aborted():
    """planned→aborted 合法：dispatch_skip_dev（profile 不满足/dev 被跳过）时 agent 未跑即弃。

    shadow parity（task 3.4）暴露的缺口——状态机必须能表达「planning 阶段放弃」的真实 dispatch 路径，
    否则 skip 记录无法归约到 ABORTED，parity 在 skip 上结构性不成立。"""
    ok, reason = L.validate_transition(L.IterationStatus.PLANNED, L.IterationStatus.ABORTED)
    assert ok, f"planned→aborted 应合法（dispatch skip 路径）: {reason}"


def test_validate_transition_rejects_running_straight_to_published():
    """**spec 核心断言（design 决策#2）**：running→published 直跳必须被拒。

    SDK「成功完成」只是 agent_finished（代码跑完），不等于测试绿、不等于验证通过、不等于已发布。
    绕过 test 门 + verifying + publish_ready 直达 published = 跳过所有质量闸——状态机拦死。"""
    ok, reason = L.validate_transition(L.IterationStatus.RUNNING, L.IterationStatus.PUBLISHED)
    assert not ok, "running→published 直跳不应放行（SDK 成功≠已发布）"
    assert reason  # 带可审计原因


def test_validate_transition_rejects_agent_finished_to_published():
    """agent_finished→published 同理被拒：即便 SDK 跑完，仍须经 verifying→publish_ready 才能发布。"""
    ok, _ = L.validate_transition(L.IterationStatus.AGENT_FINISHED, L.IterationStatus.PUBLISHED)
    assert not ok


def test_validate_transition_rejects_terminal_outgoing():
    """终态（published/aborted/failed/state_corrupt）无任何出向迁移——已交付/已废弃/已损坏，不可复活。

    防止「published 后又改回 running」这类把已交付态抹掉的误操作。"""
    for terminal in [L.IterationStatus.PUBLISHED, L.IterationStatus.ABORTED,
                     L.IterationStatus.FAILED, L.IterationStatus.STATE_CORRUPT]:
        ok, _ = L.validate_transition(terminal, L.IterationStatus.RUNNING)
        assert not ok, f"终态 {terminal} 不应有出向迁移"


def test_is_terminal():
    """is_terminal 锁定 4 个终态；运行中状态（planned/running/verifying 等）非终态。"""
    for s in [L.IterationStatus.PUBLISHED, L.IterationStatus.ABORTED,
              L.IterationStatus.FAILED, L.IterationStatus.STATE_CORRUPT]:
        assert L.is_terminal(s) is True, f"{s} 应为终态"
    for s in [L.IterationStatus.PLANNED, L.IterationStatus.RUNNING,
              L.IterationStatus.VERIFYING, L.IterationStatus.PUBLISH_READY]:
        assert L.is_terminal(s) is False, f"{s} 不应为终态"


# ════════════════════════════════════════════════════════════════════════
# C. task 2.6：reducer（dedup 拒重复 / reject 非法迁移 / 绿路径归约）
# ════════════════════════════════════════════════════════════════════════
def test_reduce_legal_pipeline_reaches_published():
    """reduce 把一串合法 event 归约到 PUBLISHED 终态——reducer 是 journal→state 的本真源（design 决策#1）。"""
    # Arrange：planned 是声明性事件（不改 status，初始已 PLANNED），随后逐跳推进
    events = [
        _ev("planned", "e0"),
        _ev("running", "e1"),
        _ev("agent_finished", "e2"),
        _ev("verifying", "e3"),
        _ev("publish_ready", "e4"),
        _ev("published", "e5"),
    ]
    # Act
    state = L.reduce(events, initial=_initial())
    # Assert
    assert state.status is L.IterationStatus.PUBLISHED
    assert L.is_terminal(state.status)
    assert {e.event_id for e in events} <= state.applied_event_ids


def test_reduce_rejects_duplicate_event_id():
    """**task 2.6「reject duplicate transitions」**：同一 event_id 第二次出现 → 跳过（status 不变）+
    ``last_transition_error`` 记 duplicate。append-only journal 重放/恢复时可能重读同一条——dedup 保证幂等。"""
    # Arrange：两条 event 同 event_id（模拟恢复时重放）
    dup = [_ev("running", "e1"), _ev("running", "e1")]  # 同 id
    # Act
    state = L.reduce(dup, initial=_initial())
    # Assert：只应用一次，status=RUNNING，且记录了 duplicate
    assert state.status is L.IterationStatus.RUNNING
    assert state.last_transition_error is not None
    assert "e1" in state.last_transition_error or "duplicate" in state.last_transition_error.lower()


def test_reduce_rejects_invalid_transition_keeps_status():
    """非法迁移（如 running 上直接发 published event）→ status 不变 + ``last_transition_error`` 记原因。

    reducer 遇非法迁移**绝不静默推进**——状态机是质量闸的最后一道机械防线（spec：SDK 成功不能直跳发布）。"""
    # Arrange：初始 RUNNING，来一条直跳 published 的 event
    state = L.reduce([_ev("published", "e1")], initial=_initial(L.IterationStatus.RUNNING))
    # Assert：status 卡在 RUNNING，没被推进到 published
    assert state.status is L.IterationStatus.RUNNING
    assert state.last_transition_error is not None


def test_reduce_sdk_success_stops_at_agent_finished():
    """SDK 成功（is_error=False 的 agent_finished event）只把状态推到 AGENT_FINISHED——
    不自动进 publish_ready/published。验证「SDK 成功≠已发布」在 reducer 层也成立（非仅 validate_transition）。"""
    state = L.reduce([_ev("agent_finished", "e1", is_error=False, session_id="s-1")],
                     initial=_initial(L.IterationStatus.RUNNING))
    assert state.status is L.IterationStatus.AGENT_FINISHED
    assert state.status is not L.IterationStatus.PUBLISHED


def test_reduce_dedup_does_not_swallow_distinct_events():
    """dedup 只针对同 event_id；不同 event_id 的合法事件即使 event_type 相同也要各自应用。

    防过度 dedup：合法重试（两次 running，不同 id）不该被当成重复丢弃。"""
    events = [
        _ev("running", "e1"),
        _ev("external_blocked", "e2"),   # 阻断后
        _ev("running", "e3"),            # reconciled 重投（新 id）
    ]
    state = L.reduce(events, initial=_initial())
    assert state.status is L.IterationStatus.RUNNING  # e3 生效，非被 dedup 丢弃
    assert {"e1", "e2", "e3"} <= state.applied_event_ids
