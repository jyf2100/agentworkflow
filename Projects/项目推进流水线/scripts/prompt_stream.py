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
    """单 yield 的 user 消息异步流（SDK streaming 模式）。

    产出一个 dict，结构对齐 SDK 字符串路径（``_internal/client.py:214-219``）。
    用法：``query(prompt=prompt_stream(p), options=options)``。
    """
    yield {
        "type": "user",
        "session_id": "",
        "message": {"role": "user", "content": prompt},
        "parent_tool_use_id": None,
    }
