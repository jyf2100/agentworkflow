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

from typing import Any, AsyncIterator


async def prompt_stream(prompt: str) -> AsyncIterator[dict[str, Any]]:
    """yield 单条 user 消息后正常耗尽（SDK streaming 模式的最小 AsyncIterable）。

    产出 dict 结构对齐 SDK 字符串路径（``_internal/client.py:214-219``）。
    用法：``query(prompt=prompt_stream(p), options=options)``。

    单 yield 后 generator 正常结束（StopAsyncIteration）。历史上此处曾用
    ``await asyncio.Event().wait()`` 保持 pending 作为「输入侧冗余对冲」，防 prompt 耗尽后
    SDK ``stream_input`` 触发 ``wait_for_result_and_end_input()`` 早关 stdin。但 RCA 实证
    该早关根因在 SDK 方法侧（保活条件遗漏 ``can_use_tool``），输入侧 pending 救不了次要
    关闭路径——是虚假对冲，给假绿温床。故 conscious 移除，accept false-hedge loss；真实
    对冲交给三重机制：(1) ``sdk_compat_patch.apply()`` ast 变异根治 #1105；(2) detection
    fail-safe；(3) dev-agent 测试门 fail-closed + canary 发布门。详见 change
    ``migrate-dev-agent-streaming-with-1106-patch``。
    """
    yield {
        "type": "user",
        "session_id": "",
        "message": {"role": "user", "content": prompt},
        "parent_tool_use_id": None,
    }
