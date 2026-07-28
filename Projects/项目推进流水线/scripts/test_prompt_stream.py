#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_prompt_stream.py — prompt_stream 单测（ADR-0006 #7 streaming 修复回归守卫）。

锁定 prompt_stream 产出首条 user 消息的 dict 结构对齐 SDK 字符串路径
（``_internal/client.py:214-219``），并锁单 yield 后正常耗尽的最小 AsyncIterable 契约
（migrate-dev-agent-streaming-with-1106-patch D4：Event.wait 输入侧冗余对冲已 conscious
移除——早关根因由 sdk_compat_patch ast 变异根治，输入 pending 是虚假对冲）。

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
    """取首条消息（prompt_stream 单 yield 后即耗尽）。"""
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


def test_single_yield_then_stopasynciteration():
    # Arrange / Act / Assert —— 单 yield 后正常耗尽（D4：Event.wait 已 conscious 移除，
    # 回归最小 AsyncIterable；第二条 __anext__ 应抛 StopAsyncIteration 而非挂起）
    async def run() -> None:
        gen = prompt_stream.prompt_stream("x")
        first = await gen.__anext__()
        assert first["type"] == "user"
        with pytest.raises(StopAsyncIteration):
            await gen.__anext__()

    asyncio.run(run())
