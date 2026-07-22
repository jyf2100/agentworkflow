#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_hook_bridge.py — task 2.3 hook_bridge：SDK HookInput(TypedDict) → HookAdapter → 真实 SDK SyncHookJSONOutput。

task 2.3「将协调器集成到 dev-agent.py 并用 pinned SDK API 注册真实 Claude Agent SDK lifecycle hooks」
的**可测核心**：hook_bridge 把 SDK 0.2.121 的 ``HookInput``（discriminated union，按 ``hook_event_name``
分发）桥接到 ``HookAdapter`` 方法，再把 ``HookOutcome`` 映射成**真实 SDK** ``SyncHookJSONOutput`` 格式。

关键对齐点（``hook_adapter.to_sdk_hook_specific_output`` 是 mock-SDK 时代契约，不对齐真实 SDK——输出
``continueActive``/``blockReason`` 这两个 SDK **不存在**的字段）：
  * 真实 SDK 只有 8 个 ``HookSpecificOutput``——**Stop / PreCompact / SubagentStop 没有 hookSpecificOutput**
    （SDK types.py:413-492 union 不含这三者）。
  * **Stop** 决策走顶层 ``continue_``（True=续命继续工作，False=允许停止）——不是 hookSpecificOutput。
  * **PreCompact** 阻断走顶层 ``decision:"block"`` + ``reason``——不是 hookSpecificOutput。
  * **PreToolUse** 走 ``hookSpecificOutput.permissionDecision``（allow/deny/ask/defer）+
    ``permissionDecisionReason``。
  * ``continue_``（非 ``continueActive``）是 SDK 真实字段名（types.py:544，下划线避关键字）。

纯逻辑层（fake HookAdapter + fake HookInput dict），零真实 SDK 运行；AAA。跑：
``python3 -m pytest scripts/test_hook_bridge.py -q``
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import hook_bridge  # noqa: E402
from hook_adapter import HookOutcome  # noqa: E402
from hook_policy import PermissionDecision  # noqa: E402


# ─── 测试替身：fake HookAdapter（记录调用 + 返回固定 outcome）──────────────────
class _FakeAdapter:
    """duck-type HookAdapter：记录 dispatch 透传的参数（验证 SDK→adapter 字段提取，不依赖 adapter 内部策略）。"""
    __test__ = False

    def __init__(self, outcome):
        self.calls = []
        self._outcome = outcome

    def on_pre_tool_use(self, iteration_id, tool_name, **kw):
        self.calls.append(("PreToolUse", iteration_id, tool_name, kw))
        return self._outcome

    def on_post_tool_use(self, iteration_id, **kw):
        self.calls.append(("PostToolUse", iteration_id, kw))
        return self._outcome

    def on_stop(self, iteration_id, **kw):
        self.calls.append(("Stop", iteration_id, kw))
        return self._outcome

    def on_pre_compact(self, iteration_id, **kw):
        self.calls.append(("PreCompact", iteration_id, kw))
        return self._outcome

    def on_subagent_start(self, iteration_id, agent_id, **kw):
        self.calls.append(("SubagentStart", iteration_id, agent_id, kw))
        return self._outcome

    def on_subagent_stop(self, iteration_id, agent_id, **kw):
        self.calls.append(("SubagentStop", iteration_id, agent_id, kw))
        return self._outcome


# ════════════════════════════════════════════════════════════════════════════
# Part 1：outcome_to_sync_output — HookOutcome → 真实 SDK SyncHookJSONOutput 格式
# ════════════════════════════════════════════════════════════════════════════
def test_outcome_pretooluse_allow_maps_permission_decision_allow():
    """PreToolUse ALLOW → hookSpecificOutput.permissionDecision="allow" + reason（SDK 策略回写通道）。"""
    out = HookOutcome(hook_event_name="PreToolUse",
                      permission_decision=PermissionDecision.ALLOW, permission_reason="path ok")
    sdk_out = hook_bridge.outcome_to_sync_output(out)
    spec = sdk_out["hookSpecificOutput"]
    assert spec["hookEventName"] == "PreToolUse"
    assert spec["permissionDecision"] == "allow"
    assert spec["permissionDecisionReason"] == "path ok"


def test_outcome_pretooluse_deny_maps_permission_decision_deny():
    """PreToolUse DENY → permissionDecision="deny"（拦工具调用）。"""
    out = HookOutcome(hook_event_name="PreToolUse",
                      permission_decision=PermissionDecision.DENY, permission_reason=".git 写禁")
    sdk_out = hook_bridge.outcome_to_sync_output(out)
    assert sdk_out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_outcome_stop_continue_active_maps_continue_true():
    """Stop deny+续命 → 顶层 ``continue_=True``（SDK：agent 不停止，继续补测试）。

    **关键**：SDK Stop **无 hookSpecificOutput**（types.py SpecificOutput union 不含 Stop）——
    ``hook_adapter.to_sdk_hook_specific_output`` 的 ``continueActive`` 是错的，真实字段是顶层 ``continue_``。
    """
    out = HookOutcome(hook_event_name="Stop", permission_decision=PermissionDecision.DENY,
                      permission_reason="no green", continue_active=True, block_reason="no_fresh_green")
    sdk_out = hook_bridge.outcome_to_sync_output(out)
    assert sdk_out["continue_"] is True
    assert "hookSpecificOutput" not in sdk_out


def test_outcome_stop_allow_maps_continue_false():
    """Stop allow（fresh green / 预算耗尽放行）→ ``continue_=False``（允许停止）。"""
    out = HookOutcome(hook_event_name="Stop", permission_decision=PermissionDecision.ALLOW,
                      permission_reason="fresh green", continue_active=False)
    sdk_out = hook_bridge.outcome_to_sync_output(out)
    assert sdk_out["continue_"] is False
    assert "hookSpecificOutput" not in sdk_out


def test_outcome_precompact_block_maps_decision_block():
    """PreCompact 阻断自动恢复 → 顶层 ``decision="block"`` + ``reason``（SDK：阻止 compact/auto-resume）。

    **关键**：SDK PreCompact **无 hookSpecificOutput**——真实字段是顶层 ``decision``/``reason``，
    不是 ``hook_adapter`` 的 ``blockReason``。
    """
    out = HookOutcome(hook_event_name="PreCompact",
                      permission_reason="snapshot unpersistable", block_reason="recovery blocked")
    sdk_out = hook_bridge.outcome_to_sync_output(out)
    assert sdk_out["decision"] == "block"
    assert sdk_out["reason"] == "recovery blocked"
    assert "hookSpecificOutput" not in sdk_out


def test_outcome_precompact_no_block_maps_empty():
    """PreCompact 放行（snapshot 已持久化）→ 空 dict（不阻断，不 compact-control）。"""
    out = HookOutcome(hook_event_name="PreCompact", permission_reason="snapshot persisted")
    sdk_out = hook_bridge.outcome_to_sync_output(out)
    assert sdk_out == {}


def test_outcome_posttooluse_maps_hook_specific_output_without_permission():
    """PostToolUse → hookSpecificOutput（PostToolUseHookSpecificOutput，**无** permissionDecision 通道）。"""
    out = HookOutcome(hook_event_name="PostToolUse",
                      permission_reason="paired=True; evidence=fresh_green")
    sdk_out = hook_bridge.outcome_to_sync_output(out)
    spec = sdk_out["hookSpecificOutput"]
    assert spec["hookEventName"] == "PostToolUse"
    assert "permissionDecision" not in spec


def test_outcome_subagent_start_maps_hook_specific_output():
    """SubagentStart → hookSpecificOutput（SDK 有 SubagentStartHookSpecificOutput）。"""
    out = HookOutcome(hook_event_name="SubagentStart", permission_reason="ownership recorded")
    sdk_out = hook_bridge.outcome_to_sync_output(out)
    assert sdk_out["hookSpecificOutput"]["hookEventName"] == "SubagentStart"


def test_outcome_subagent_stop_maps_empty():
    """SubagentStop → 空 dict（SDK **无** SubagentStopHookSpecificOutput）。"""
    out = HookOutcome(hook_event_name="SubagentStop",
                      permission_reason="agent stopped: completed")
    sdk_out = hook_bridge.outcome_to_sync_output(out)
    assert sdk_out == {}


# ════════════════════════════════════════════════════════════════════════════
# Part 2：dispatch_hook_event — SDK HookInput(TypedDict) → adapter 方法 + 字段提取
# ════════════════════════════════════════════════════════════════════════════
def test_dispatch_pretooluse_extracts_command_and_subagent_context():
    """PreToolUse：从 tool_input 提 command + mixin agent_id（subagent context）透传 adapter。"""
    fa = _FakeAdapter(HookOutcome(hook_event_name="PreToolUse", permission_decision=PermissionDecision.ALLOW))
    hook_input = {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                  "tool_input": {"command": "pytest -q"}, "tool_use_id": "tu_1",
                  "agent_id": "sub_1"}
    hook_bridge.dispatch_hook_event(fa, hook_input, iteration_id="iter_1")
    name, iid, tool, kw = fa.calls[0]
    assert (name, iid, tool) == ("PreToolUse", "iter_1", "Bash")
    assert kw["tool_use_id"] == "tu_1"
    assert kw["command"] == "pytest -q"
    assert kw["subagent_agent_id"] == "sub_1"
    assert kw["write"] is False            # Bash 非写类


def test_dispatch_edit_marks_write_and_extracts_path():
    """Edit：write=True（写类工具）+ 从 file_path 提 path。"""
    fa = _FakeAdapter(HookOutcome(hook_event_name="PreToolUse"))
    hook_input = {"hook_event_name": "PreToolUse", "tool_name": "Edit",
                  "tool_input": {"file_path": "src/x.py", "old_string": "a", "new_string": "b"},
                  "tool_use_id": "tu_2"}
    hook_bridge.dispatch_hook_event(fa, hook_input, iteration_id="iter_1")
    _, _, _, kw = fa.calls[0]
    assert kw["write"] is True
    assert kw["path"] == "src/x.py"


def test_dispatch_stop_passes_stop_hook_active():
    """Stop：透传 stop_hook_active。"""
    fa = _FakeAdapter(HookOutcome(hook_event_name="Stop"))
    hook_bridge.dispatch_hook_event(fa, {"hook_event_name": "Stop", "stop_hook_active": True},
                                    iteration_id="iter_1")
    _, _, kw = fa.calls[0]
    assert kw == {"stop_hook_active": True}


def test_dispatch_precompact_passes_trigger():
    """PreCompact：透传 trigger（manual/auto）。"""
    fa = _FakeAdapter(HookOutcome(hook_event_name="PreCompact"))
    hook_bridge.dispatch_hook_event(
        fa, {"hook_event_name": "PreCompact", "trigger": "auto", "custom_instructions": None},
        iteration_id="iter_1")
    _, _, kw = fa.calls[0]
    assert kw["trigger"] == "auto"


def test_dispatch_subagent_start_passes_agent_id_and_type():
    """SubagentStart：agent_id 位置参 + agent_type kw。"""
    fa = _FakeAdapter(HookOutcome(hook_event_name="SubagentStart"))
    hook_bridge.dispatch_hook_event(
        fa, {"hook_event_name": "SubagentStart", "agent_id": "a1", "agent_type": "general-purpose"},
        iteration_id="iter_1")
    name, iid, agent_id, kw = fa.calls[0]
    assert (name, iid, agent_id) == ("SubagentStart", "iter_1", "a1")
    assert kw["agent_type"] == "general-purpose"


def test_dispatch_subagent_stop_passes_agent_id():
    """SubagentStop：agent_id 位置参。"""
    fa = _FakeAdapter(HookOutcome(hook_event_name="SubagentStop"))
    hook_bridge.dispatch_hook_event(
        fa, {"hook_event_name": "SubagentStop", "agent_id": "a1", "agent_type": "general-purpose",
             "stop_hook_active": False, "agent_transcript_path": "/t"},
        iteration_id="iter_1")
    name, iid, agent_id, _kw = fa.calls[0]
    assert (name, iid, agent_id) == ("SubagentStop", "iter_1", "a1")


def test_dispatch_posttooluse_extracts_command_for_evidence():
    """PostToolUse：从 tool_input 提 command（adapter 识别测试命令→fresh green TestEvidence）。"""
    fa = _FakeAdapter(HookOutcome(hook_event_name="PostToolUse"))
    hook_input = {"hook_event_name": "PostToolUse", "tool_name": "Bash",
                  "tool_input": {"command": "pytest -q"},
                  "tool_response": {"stdout": "ok", "interrupted": False},
                  "tool_use_id": "tu_1", "agent_id": None}
    hook_bridge.dispatch_hook_event(fa, hook_input, iteration_id="iter_1")
    _, _, kw = fa.calls[0]
    assert kw["tool_use_id"] == "tu_1"
    assert kw["tool_name"] == "Bash"
    assert kw["command"] == "pytest -q"


def test_dispatch_unknown_event_raises():
    """未知 hook_event_name → ValueError（fail-loud：防 SDK 新增事件静默漏处理）。"""
    with pytest.raises(ValueError):
        hook_bridge.dispatch_hook_event(
            _FakeAdapter(HookOutcome(hook_event_name="X")),
            {"hook_event_name": "Unknown"}, iteration_id="iter_1")


# ════════════════════════════════════════════════════════════════════════════
# Part 3：make_hook_callback / build_hook_matchers — 供 ClaudeAgentOptions.hooks 注册
# ════════════════════════════════════════════════════════════════════════════
def test_make_hook_callback_is_async_and_emits_sdk_output():
    """make_hook_callback → async HookCallback(input, tool_use_id, ctx)：dispatch + outcome_to_sync_output → SDK 格式。"""
    fa = _FakeAdapter(HookOutcome(hook_event_name="PreToolUse",
                                  permission_decision=PermissionDecision.DENY, permission_reason=".git 写禁"))
    cb = hook_bridge.make_hook_callback(fa, iteration_id="iter_1")
    assert asyncio.iscoroutinefunction(cb)        # SDK HookCallback 必须 async（Awaitable 返回）
    hook_input = {"hook_event_name": "PreToolUse", "tool_name": "Edit",
                  "tool_input": {"file_path": ".git/config"}, "tool_use_id": "tu_1"}
    sdk_out = asyncio.run(cb(hook_input, "tu_1", {"signal": None}))
    assert sdk_out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_make_hook_callback_stop_emits_continue_true():
    """callback 对 Stop 续命 outcome → SDK 顶层 ``continue_=True``。"""
    fa = _FakeAdapter(HookOutcome(hook_event_name="Stop", continue_active=True,
                                  permission_reason="no green"))
    cb = hook_bridge.make_hook_callback(fa, iteration_id="iter_1")
    sdk_out = asyncio.run(cb({"hook_event_name": "Stop", "stop_hook_active": True},
                             None, {"signal": None}))
    assert sdk_out["continue_"] is True


def test_build_hook_matchers_covers_six_loop_lifecycle_events():
    """build_hook_matchers → dict[HookEvent, list[HookMatcher]] 供 ``ClaudeAgentOptions.hooks`` 注册。

    SDK ``ClaudeAgentOptions.hooks: dict[HookEvent, list[HookMatcher]]``——hook_bridge 须覆盖 loop
    用到的 6 个生命周期事件（task 2.3 注册完整 lifecycle）。每个 event 至少一个 HookMatcher。
    """
    fa = _FakeAdapter(HookOutcome(hook_event_name="Stop"))
    matchers = hook_bridge.build_hook_matchers(fa, iteration_id="iter_1")
    assert set(matchers.keys()) == {
        "PreToolUse", "PostToolUse", "Stop", "PreCompact", "SubagentStart", "SubagentStop"}
    for ev, ms in matchers.items():
        assert len(ms) >= 1, f"{ev} 须至少一个 HookMatcher"
        # 每个 matcher 至少含一个 async hook callback
        assert any(asyncio.iscoroutinefunction(h) for m in ms for h in m.hooks)
