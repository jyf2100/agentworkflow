#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_prompt_stream.py — prompt_stream 单测（ADR-0006 #7 streaming 修复回归守卫）。

锁定 prompt_stream 产出的 dict 结构对齐 SDK 字符串路径（``_internal/client.py:214-219``），防 streaming
修复（string→AsyncIterable 包装）静默回归——该路径是 2026-07-20 canary 实证的真 SDK 入口，回归即崩。

跑：python3 -m pytest scripts/test_prompt_stream.py -q
AAA 结构（Arrange / Act / Assert）。零 SDK 依赖（prompt_stream 纯 stdlib）。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import prompt_stream  # noqa: E402


async def _collect(gen):
    return [m async for m in gen]


def test_single_yield_shape_matches_sdk_string_path():
    # Arrange
    p = "你是本仓的自治 dev agent…"
    # Act
    msgs = asyncio.run(_collect(prompt_stream.prompt_stream(p)))
    # Assert —— 单 yield，结构对齐 _internal/client.py:214-219
    assert len(msgs) == 1
    m = msgs[0]
    assert m["type"] == "user"
    assert m["session_id"] == ""
    assert m["message"]["role"] == "user"
    assert m["message"]["content"] == p
    assert m["parent_tool_use_id"] is None


def test_preserves_multiline_prompt_verbatim():
    # Arrange
    p = "line1\nline2\n## 任务\n- a\n- b"
    # Act
    m = asyncio.run(_collect(prompt_stream.prompt_stream(p)))[0]
    # Assert —— 多行 prompt 原样进 content（SDK 不截断/转义）
    assert m["message"]["content"] == p
