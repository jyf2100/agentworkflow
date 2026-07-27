#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""prompt_stream.py — 把 string prompt 包成 SDK streaming 模式的 AsyncIterable（ADR-0006 #7 落地修正）。

why
---
vault dev-agent（ADR-0006 控制面标准执行器）用 ``ClaudeAgentOptions(can_use_tool=_can_use_tool)`` 挂 Bash
权限闸。但 SDK 0.2.x 的 ``can_use_tool`` 回调要求 **streaming 模式**——``_internal/client.py:103`` 见
``prompt`` 为 ``str`` 即 raise「can_use_tool callback requires streaming mode. Please provide prompt as an
AsyncIterable instead of a string」。dev loop 是单轮 prompt→多轮工具调用，本函数把单轮 string prompt 包成
单条 user 消息异步流即可满足 streaming 要求。

dict 结构对齐 SDK 字符串路径（``_internal/client.py:214-219``：``type``/``session_id``/``message``/
``parent_tool_use_id``），单 yield 即足（无中途追加 user 消息需求；如需中断/追加再扩为多 yield）。

零依赖模块（同 ``slug_utils``/``evidence``/``bash_allowlist``/``external_state`` 既定模式）→ 单测可零 SDK 导入
锁定 dict 结构，防 streaming 修复静默回归（2026-07-20 canary 实证此路径为真 SDK 入口）。
"""
from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator


async def prompt_stream(prompt: str) -> AsyncIterator[dict[str, Any]]:
    """yield 首条 user 消息后保持 pending，直到 stream_input 被取消（SDK streaming 模式）。

    产出 dict 结构对齐 SDK 字符串路径（``_internal/client.py:214-219``）。
    用法：``query(prompt=prompt_stream(p), options=options)``。

    yield 后 ``await asyncio.Event().wait()`` 永不返回（无 set），保持 input iterable
    pending 直到 query 结束、stream_input 任务被 cancel。这避免 SDK 0.2.121
    ``stream_input`` 在正常耗尽后调 ``wait_for_result_and_end_input()``——其保活条件
    ``sdk_mcp_servers or hooks`` 遗漏 ``can_use_tool``，无 hooks 时立即 ``end_input()``
    关 stdin，后续轮次 ``can_use_tool`` 的 permission response 写不回（``AbortError:
    Stream closed``）。见 change ``fix-dev-agent-stream-aclose-race``。
    """
    yield {
        "type": "user",
        "session_id": "",
        "message": {"role": "user", "content": prompt},
        "parent_tool_use_id": None,
    }
    # 保持 pending 到 result/cancel；stream_input cancel 时本 await 抛 CancelledError 正常退出
    await asyncio.Event().wait()
