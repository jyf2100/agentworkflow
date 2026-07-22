#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hook_bridge.py — task 2.3 SDK HookInput(TypedDict) → HookAdapter → 真实 SDK SyncHookJSONOutput 桥接。

design 决策#1「通过一个运行时协调器进行集成」的 SDK hook 接入层：把 pinned ``claude_agent_sdk`` 0.2.121
的 ``HookInput``（discriminated union，按 ``hook_event_name`` 分发）桥接到 **SDK-free** 的 ``HookAdapter``
（task 4.1），再把 ``HookOutcome`` 映射成**真实 SDK** ``SyncHookJSONOutput`` 格式，供
``ClaudeAgentOptions.hooks`` 注册真实生命周期 hook（task 2.3 后半：dev-agent wiring）。

**为什么需要这一层**（``hook_adapter.to_sdk_hook_specific_output`` 不够）：后者是 mock-SDK 时代契约，
输出 ``continueActive``/``blockReason`` 两个 SDK **不存在**的字段，且把 Stop/PreCompact/SubagentStop 塞进
``hookSpecificOutput``——而真实 SDK 0.2.121（``types.py:413-492`` SpecificOutput union）**只有 8 个
HookSpecificOutput，不含 Stop/PreCompact/SubagentStop**。本层用真实 SDK 字段名做正确映射：

  * **PreToolUse** → ``hookSpecificOutput.{hookEventName, permissionDecision(allow/deny/ask/defer),
    permissionDecisionReason}``（SDK 策略回写通道，types.py:413）。
  * **Stop** → 顶层 ``continue_``（True=续命继续工作，False=允许停止）——SDK Stop 无 hookSpecificOutput。
    （``continue_`` 带 underscore 是 SDK 真实字段，types.py:544，自动转 CLI ``continue``。）
  * **PreCompact** → 顶层 ``decision:"block"`` + ``reason``（阻断 auto-resume）——SDK PreCompact 无
    hookSpecificOutput；放行则空 dict。
  * **PostToolUse / SubagentStart** → ``hookSpecificOutput.{hookEventName}``（SDK 有对应 SpecificOutput）。
  * **SubagentStop** → 空 dict（SDK 无 SubagentStopHookSpecificOutput）。

**SDK 解耦**（同 ``hook_adapter``）：核心三函数（``outcome_to_sync_output`` / ``dispatch_hook_event`` /
``make_hook_callback``）**零 SDK 导入**——顶层 import 不触 ``claude_agent_sdk``，纯逻辑可测、cron 隔离友好。
``build_hook_matchers`` 是控制面 wiring helper，函数内延迟 import ``HookMatcher``（仅真实 SDK 接入时）。

纯 stdlib + 复用 hook_adapter/hook_policy，TDD（test_hook_bridge.py）覆盖全分支。
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from hook_adapter import HookOutcome

# 写类工具：PreToolUse ``write`` 判定（与 ``hook_adapter._WRITE_TOOLS`` 对齐——策略层不重复判写）。
_WRITE_TOOLS: frozenset[str] = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})

# loop 注册的 6 个 SDK lifecycle 事件（对齐 SDK ``HookEvent`` literal；task 2.3 注册完整 lifecycle）。
LOOP_HOOK_EVENTS: tuple[str, ...] = (
    "PreToolUse", "PostToolUse", "Stop", "PreCompact", "SubagentStart", "SubagentStop",
)


def outcome_to_sync_output(outcome: HookOutcome) -> dict[str, Any]:
    """HookOutcome → 真实 SDK 0.2.121 ``SyncHookJSONOutput`` dict（对齐 ``types.py:517``）。

    按 hook 事件分流到 SDK 真实回写通道（hookSpecificOutput 仅 PreToolUse/PostToolUse/SubagentStart 有；
    Stop 走顶层 ``continue_``；PreCompact 走顶层 ``decision``）。
    """
    name = outcome.hook_event_name
    if name == "PreToolUse":
        spec: dict[str, Any] = {"hookEventName": "PreToolUse"}
        if outcome.permission_decision is not None:
            spec["permissionDecision"] = outcome.permission_decision.value
            spec["permissionDecisionReason"] = outcome.permission_reason
        return {"hookSpecificOutput": spec}
    if name == "PostToolUse":
        # SDK PostToolUseHookSpecificOutput：仅 hookEventName（additionalContext 可选，本层最小映射）
        return {"hookSpecificOutput": {"hookEventName": "PostToolUse"}}
    if name == "SubagentStart":
        return {"hookSpecificOutput": {"hookEventName": "SubagentStart"}}
    if name == "Stop":
        # SDK Stop **无 hookSpecificOutput**：continue_=True 让 agent 续命补测试，False 允许停止
        return {"continue_": bool(outcome.continue_active)}
    if name == "PreCompact":
        # SDK PreCompact **无 hookSpecificOutput**：block_reason 非空 → 顶层 decision="block" 阻 compact/auto-resume
        if outcome.block_reason:
            return {"decision": "block", "reason": outcome.block_reason}
        return {}
    if name == "SubagentStop":
        return {}                       # SDK 无 SubagentStopHookSpecificOutput
    return {}


def dispatch_hook_event(adapter: Any, hook_input: dict[str, Any], *,
                        iteration_id: str) -> HookOutcome:
    """SDK ``HookInput``（TypedDict dict）→ ``HookAdapter`` 方法 + 字段提取。

    按 ``hook_input["hook_event_name"]`` 分发到对应 adapter 方法，从 ``tool_input`` 提取
    command/path/url（PreToolUse/PostToolUse）+ ``write`` 判定 + subagent ``agent_id`` 透传。
    未知事件 → ``ValueError``（fail-loud：防 SDK 新增 lifecycle 事件静默漏处理）。
    """
    name = hook_input.get("hook_event_name", "")
    if name == "PreToolUse":
        ti = hook_input.get("tool_input") or {}
        return adapter.on_pre_tool_use(
            iteration_id, hook_input.get("tool_name", ""),
            tool_use_id=hook_input.get("tool_use_id"),
            command=ti.get("command", ""),
            path=ti.get("file_path") or ti.get("path") or "",
            write=hook_input.get("tool_name", "") in _WRITE_TOOLS,
            url=ti.get("url", ""),
            subagent_agent_id=hook_input.get("agent_id"),
        )
    if name == "PostToolUse":
        ti = hook_input.get("tool_input") or {}
        return adapter.on_post_tool_use(
            iteration_id,
            tool_use_id=hook_input.get("tool_use_id"),
            tool_name=hook_input.get("tool_name", ""),
            command=ti.get("command", ""),
            subagent_agent_id=hook_input.get("agent_id"),
        )
    if name == "Stop":
        return adapter.on_stop(
            iteration_id, stop_hook_active=bool(hook_input.get("stop_hook_active", False)))
    if name == "PreCompact":
        return adapter.on_pre_compact(
            iteration_id, trigger=hook_input.get("trigger", "auto"))
    if name == "SubagentStart":
        return adapter.on_subagent_start(
            iteration_id, hook_input.get("agent_id", ""),
            agent_type=hook_input.get("agent_type", ""),
        )
    if name == "SubagentStop":
        return adapter.on_subagent_stop(
            iteration_id, hook_input.get("agent_id", ""))
    raise ValueError(
        f"unknown hook_event_name: {name!r} "
        "(hook_bridge only dispatches loop lifecycle events)")


def make_hook_callback(adapter: Any, *, iteration_id: str) -> Callable[..., Awaitable[dict]]:
    """构造 async ``HookCallback``（签名对齐 SDK ``Callable[[HookInput, str|None, HookContext],
    Awaitable[HookJSONOutput]]``）。

    控制面（dev-agent wiring，task 2.3 后半）把此 callback 注册进 ``ClaudeAgentOptions.hooks`` 的
    ``HookMatcher.hooks``；SDK 触发生命周期事件时回调 → ``dispatch_hook_event`` +
    ``outcome_to_sync_output`` → SDK 格式回写。零 SDK 导入（鸭子类型匹配 HookCallback）。
    """

    async def _callback(hook_input: dict[str, Any], tool_use_id: str | None,
                        context: dict[str, Any]) -> dict[str, Any]:
        outcome = dispatch_hook_event(adapter, hook_input, iteration_id=iteration_id)
        return outcome_to_sync_output(outcome)

    return _callback


def build_hook_matchers(adapter: Any, *, iteration_id: str) -> dict[str, list]:
    """构造 ``dict[HookEvent, list[HookMatcher]]`` 供 ``ClaudeAgentOptions.hooks`` 注册（task 2.3 wiring）。

    SDK ``ClaudeAgentOptions.hooks: dict[HookEvent, list[HookMatcher]]``（types.py:1913）；本函数覆盖
    loop 6 个 lifecycle 事件，每事件一个 ``HookMatcher(hooks=[callback])``（``matcher=None`` 不按工具
    名过滤——loop 监控所有工具）。延迟 import ``HookMatcher``：仅控制面真实 SDK 接入时触依赖，核心层零 SDK。
    """
    from claude_agent_sdk import HookMatcher   # 延迟 import：保核心层 SDK-free（types.py:585）
    callback = make_hook_callback(adapter, iteration_id=iteration_id)
    return {event: [HookMatcher(hooks=[callback])] for event in LOOP_HOOK_EVENTS}
