#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_sdk_contract.py — Claude Agent SDK 版本与字段契约哨兵（OpenSpec add-durable-loop-runtime task 1.2）。

本变更把 SDK 从「调用即忘」升级为「loop runtime 的恢复契约真源」：``session_id``、``stop_reason``、
``hooks``、``resume``/``fork_session`` 都成为持久化、重试与发布决策的依据。SDK 升级若改名/移除这些
字段，运行时会静默退化（design.md Risks 第 7 条「Hook/SDK 版本差异」）。本测试锁定 dev-agent.py
与后续 hook/retry/sandbox/telemetry 代码**实际依赖的 SDK 契约**——任一字段消失即 RED，强制在升级
时显式处理，而非运行中静默忽略（不支持的 hook 必须在启动 preflight 失败）。

只读类型与字段（不发起 query/subprocess），属 SDK 版本契约锁定，非 IO 触达。cron 隔离不变：
``run_daily.py`` 顶部仍不连带加载 SDK（由 ``test_dev_agent_source`` 守护），本测试独立 import 测契约。

跑：python3 -m pytest scripts/test_sdk_contract.py -q
AAA 结构（Arrange / Act / Assert）。
"""
from __future__ import annotations

import dataclasses
import importlib.metadata
import re
import typing
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def sdk():
    """模块级共享：import claude_agent_sdk 一次（本测试文件就是要触达 SDK 类型层）。"""
    import claude_agent_sdk as _sdk
    return _sdk


def _semver_tuple(v: str) -> tuple[int, ...]:
    """把 '0.2.121' → (0, 2, 121)，做可靠的版本序比较（字符串比较遇 '0.2.9' vs '0.2.121' 会错）。"""
    return tuple(int(x) for x in v.split("."))


# ─── A. dev-agent.py 顶层 import 的 SDK 名字必须仍可用 ──────────────────
def test_sdk_exports_dev_agent_imports(sdk):
    """dev-agent.py 顶部 ``from claude_agent_sdk import query, ClaudeAgentOptions, ...``。

    这些名字若在升级中被拆/改名，dev-agent 顶层 import 即崩 → cron dispatch 全线 fail。
    这是 SDK 与控制面执行器之间最浅、最致命的契约面。"""
    # Arrange
    needed = ["query", "ClaudeAgentOptions", "AssistantMessage", "UserMessage",
              "ResultMessage", "TextBlock", "ToolUseBlock", "ToolResultBlock",
              "PermissionResultAllow", "PermissionResultDeny"]
    # Act / Assert
    missing = [n for n in needed if not hasattr(sdk, n)]
    assert not missing, f"SDK 不再导出 dev-agent 顶层依赖的名字（import 即崩）: {missing}"


# ─── B. ResultMessage 必需字段（task 5.1 持久化 + retry 分类依据）──────────
def test_result_message_session_and_turn_contract():
    """ResultMessage 必填字段（无默认值）是「session 真源」契约——task 5.1 必须持久化这些。

    ``session_id`` 成为恢复契约、``num_turns`` 是分层限额（task 5.6）、``subtype`` 区分
    success/error_max_budget 等、``is_error`` 决定是否进 retry 评估。"""
    # Arrange
    from claude_agent_sdk.types import ResultMessage
    fields = {f.name for f in dataclasses.fields(ResultMessage)}
    required_no_default = {"subtype", "is_error", "num_turns", "session_id",
                           "duration_ms", "duration_api_ms"}
    # Act / Assert
    missing = required_no_default - fields
    assert not missing, f"ResultMessage 必填字段缺失（session/turn 持久化契约破损）: {missing}"


def test_result_message_retry_telemetry_fields():
    """ResultMessage 可选字段是 RetryPolicy 与 telemetry 的机械输入。

    ``stop_reason``/``usage``/``total_cost_usd`` 喂 retry 与成本指标；``result`` 是终态文本；
    ``api_error_status`` 见下一条独立测试。"""
    from claude_agent_sdk.types import ResultMessage
    fields = {f.name for f in dataclasses.fields(ResultMessage)}
    for f in ["stop_reason", "total_cost_usd", "usage", "result"]:
        assert f in fields, f"ResultMessage 缺 retry/telemetry 依赖字段: {f}"


def test_result_message_api_error_status_for_transient_classification():
    """``api_error_status``（HTTP 429/500/529）是 RetryPolicy 判「transient provider 错误」的机械依据
    （design 决策#3）；字段消失则 retry 退回文本解析 stop_reason = 不可解释的退化。"""
    from claude_agent_sdk.types import ResultMessage
    fields = {f.name for f in dataclasses.fields(ResultMessage)}
    assert "api_error_status" in fields


# ─── C. ClaudeAgentOptions 支持 hooks + can_use_tool（Phase 4 前提）──────
def test_options_hook_and_permission_fields():
    """``hooks`` 字段存在 → Phase 4 lifecycle hooks 可原生接入（无需 CLI settings hook 绕路）；
    ``can_use_tool`` 是 dev-agent 现用闸；``include_hook_events`` 让 hook 事件流入 message stream。"""
    from claude_agent_sdk.types import ClaudeAgentOptions
    fields = {f.name for f in dataclasses.fields(ClaudeAgentOptions)}
    assert "hooks" in fields, "SDK 不支持 hooks 字段 → Phase 4 lifecycle hooks 无法落地"
    assert "can_use_tool" in fields, "dev-agent 现用的 can_use_tool 闸契约破损"
    assert "include_hook_events" in fields


# ─── D. HookEvent 覆盖 loop runtime 需要的 6 个生命周期事件 ───────────────
def test_hook_events_cover_loop_lifecycle():
    """task 4.1 依赖 PreToolUse/PostToolUse/Stop/PreCompact/SubagentStart/SubagentStop 全部可用。
    任一缺失 → hook adapter 在 preflight 即 fail（而非运行中静默漏采证据）。"""
    from claude_agent_sdk.types import HookEvent
    # HookEvent 是 Literal["A"] | Literal["B"] | ... 的 union；typing.get_args 只剥一层，
    # 需对每个 union 分支再 get_args 取出字面量字符串。
    events: set[str] = set()
    for union_arg in typing.get_args(HookEvent):
        events.update(typing.get_args(union_arg))
    needed = {"PreToolUse", "PostToolUse", "Stop", "PreCompact",
              "SubagentStart", "SubagentStop"}
    missing = needed - events
    assert not missing, f"SDK HookEvent 缺失生命周期事件（task 4.1 依赖）: {missing}"


# ─── E. resume / fork_session / session_id（Phase 5 RetryPolicy 前提）─────
def test_options_supports_resume_fork_session():
    """RetryPolicy 的 resume/fork 决策（task 5.3）直接映射到 SDK 的 resume + fork_session；
    session_id 用于 new_session 定锚。三者缺一 → retry 分流无法机械执行。"""
    from claude_agent_sdk.types import ClaudeAgentOptions
    fields = {f.name for f in dataclasses.fields(ClaudeAgentOptions)}
    assert "resume" in fields, "SDK 不支持 resume → RetryPolicy resume 决策无法落地"
    assert "fork_session" in fields, "SDK 不支持 fork_session → RetryPolicy fork 决策无法落地"
    assert "session_id" in fields


# ─── F. PermissionResult 契约（dev-agent _can_use_tool 已用）─────────────
def test_permission_result_allow_deny_contract(sdk):
    """dev-agent 的 ``_can_use_tool`` 用 ``PermissionResultAllow(updated_input=...)`` 放行、
    ``PermissionResultDeny(message=...)`` 拒绝并回写原因给 dev。锁定这两个构造签名。"""
    # Arrange / Act
    allow = sdk.PermissionResultAllow(updated_input={"command": "ls"})
    deny = sdk.PermissionResultDeny(message="命中拒绝规则（网络/破坏性）")
    # Assert
    assert allow.behavior == "allow"
    assert allow.updated_input == {"command": "ls"}
    assert deny.behavior == "deny"
    assert deny.message == "命中拒绝规则（网络/破坏性）"


# ─── G. Hook 输入/输出形状（Phase 4 hook adapter 实现依据）────────────────
def test_pretooluse_hook_specific_output_shape():
    """PreToolUse hook 经 ``hookSpecificOutput.permissionDecision``（allow/deny/ask/defer）+
    ``permissionDecisionReason`` 回写策略裁定——这是 Phase 4 确定性策略闸的回写通道。"""
    from claude_agent_sdk.types import PreToolUseHookSpecificOutput
    keys = set(PreToolUseHookSpecificOutput.__annotations__)
    for k in ["hookEventName", "permissionDecision", "permissionDecisionReason"]:
        assert k in keys, f"PreToolUseHookSpecificOutput 缺键（策略闸回写通道破损）: {k}"


def test_pretooluse_hook_input_carries_tool_use_id():
    """PostToolUse 配对（task 4.3）依赖 tool_use_id 串联 tool 调用与结果。"""
    from claude_agent_sdk.types import PreToolUseHookInput, PostToolUseHookInput
    assert "tool_use_id" in PreToolUseHookInput.__annotations__
    assert "tool_use_id" in PostToolUseHookInput.__annotations__
    assert "tool_response" in PostToolUseHookInput.__annotations__


def test_stop_hook_input_has_stop_hook_active():
    """``stop_hook_active`` 是 Stop hook「再给一次机会」续命机制（task 5.6 bounded continuation）的判位。"""
    from claude_agent_sdk.types import StopHookInput
    assert "stop_hook_active" in StopHookInput.__annotations__


def test_precompact_hook_input_has_trigger():
    """PreCompact 的 ``trigger``（manual/auto）区分主动/自动压缩（task 4.5 recovery snapshot）。"""
    from claude_agent_sdk.types import PreCompactHookInput
    assert "trigger" in PreCompactHookInput.__annotations__


def test_subagent_hook_inputs_carry_agent_attribution():
    """SubagentStart/Stop 的 agent_id/agent_type 是 task 4.6 记录父子因果与禁止 subagent 发布的依据。"""
    from claude_agent_sdk.types import SubagentStartHookInput, SubagentStopHookInput
    assert "agent_id" in SubagentStartHookInput.__annotations__
    assert "agent_type" in SubagentStartHookInput.__annotations__
    assert "agent_id" in SubagentStopHookInput.__annotations__


# ─── H. ToolResultBlock.is_error（dev-agent classify_test_exit 依赖）─────
def test_tool_result_block_fields(sdk):
    """dev-agent ``classify_test_exit`` 优先读 ``is_error``（=exit!=0）判测试红绿；
    ``tool_use_id`` 用于 pending_test_ids 配对。"""
    fields = {f.name for f in dataclasses.fields(sdk.ToolResultBlock)}
    for f in ["is_error", "tool_use_id", "content"]:
        assert f in fields, f"ToolResultBlock 缺字段: {f}"


# ─── I. SDK 版本 pin 文档化（task 1.2：pin + document）──────────────────
def test_sdk_version_pinned_with_upper_bound():
    """pyproject.toml 必须把 claude-agent-sdk pin 在带上界的范围。

    上界理由（ADR-0006 / pyproject 注释）：0.2.123 起 can_use_tool 回调要求 streaming 模式，
    本执行器用 string-prompt query() 会触 ``can_use_tool callback requires streaming mode``。
    松开上界 → 升级即崩。本测试挡住「误删上界」（design Risks 第 7 条的硬落地）。"""
    # Arrange
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    # Act
    m = re.search(r'"claude-agent-sdk\s*([^"]+)"', text)
    # Assert
    assert m, "pyproject 未声明 claude-agent-sdk 依赖"
    spec = m.group(1)
    assert (">=" in spec) or ("==" in spec), f"SDK 未 pin 下界: {spec}"
    assert "<" in spec, f"SDK 未 pin 上界（防 streaming-mode 回归）: {spec}"


def test_installed_sdk_version_within_pinned_range():
    """当前装的 SDK 版本必须落在 pyproject pin 范围内（环境一致性哨兵）。

    CI / 开发机若误装超范围版本（如 0.2.123+），dev-agent 一调 can_use_tool 即崩——
    本测试在 quality 阶段就挡住，而非等到 dispatch 运行时崩。"""
    # Arrange
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    m = re.search(r'"claude-agent-sdk\s*>=\s*([\d.]+)\s*,\s*<\s*([\d.]+)"', text)
    assert m, "pyproject SDK pin 非预期的 >=x,<y 形态（见 ADR-0006 streaming 限制）"
    lo, hi = m.group(1), m.group(2)
    installed = importlib.metadata.version("claude-agent-sdk")
    # Act / Assert
    assert _semver_tuple(lo) <= _semver_tuple(installed) < _semver_tuple(hi), (
        f"装的 claude-agent-sdk {installed} 不在 pyproject pin [{lo}, {hi}) 内")
