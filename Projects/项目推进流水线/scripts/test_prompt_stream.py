#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_prompt_stream.py — prompt_stream 单测（ADR-0006 #7 streaming 修复回归守卫）。

锁定 prompt_stream 产出首条 user 消息的 dict 结构对齐 SDK 字符串路径
（``_internal/client.py:214-219``），并锁 yield 后保持 pending 的生命周期行为
（fix-dev-agent-stream-aclose-race 方案 A：避免 SDK 0.2.121 在正常耗尽后立即
``end_input()`` 关闭 ``can_use_tool`` permission 通道）。

跑：python3 -m pytest scripts/test_prompt_stream.py -q
AAA 结构（Arrange / Act / Assert）。零 SDK 依赖（prompt_stream 纯 stdlib）。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import prompt_stream  # noqa: E402


async def _first(gen):
    """取首条消息（不耗尽 generator——yield 后 prompt_stream 保持 pending）。"""
    return await gen.__anext__()


def test_first_message_shape_matches_sdk_string_path():
    # Arrange
    p = "你是本仓的自治 dev agent…"
    # Act
    m = asyncio.run(_first(prompt_stream.prompt_stream(p)))
    # Assert —— 首条 user 消息结构对齐 _internal/client.py:214-219
    assert m["type"] == "user"
    assert m["session_id"] == ""
    assert m["message"]["role"] == "user"
    assert m["message"]["content"] == p
    assert m["parent_tool_use_id"] is None


def test_preserves_multiline_prompt_verbatim():
    # Arrange
    p = "line1\nline2\n## 任务\n- a\n- b"
    # Act
    m = asyncio.run(_first(prompt_stream.prompt_stream(p)))
    # Assert —— 多行 prompt 原样进 content（SDK 不截断/转义）
    assert m["message"]["content"] == p


def test_stays_pending_after_first_yield():
    # Arrange / Act / Assert —— yield 首条后应保持 pending，不立即 StopAsyncIteration
    # （fix-dev-agent-stream-aclose-race 方案 A；第二条 __anext__ 应挂起而非抛 StopAsyncIteration）
    async def run() -> None:
        gen = prompt_stream.prompt_stream("x")
        first = await gen.__anext__()
        assert first["type"] == "user"
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(gen.__anext__(), timeout=0.05)
        await gen.aclose()

    asyncio.run(run())
