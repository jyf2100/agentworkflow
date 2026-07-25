#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""r7-S1（审核员）：SDK ``ResultMessage`` 文本字段是 ``.result``，非 ``.text``。

旧 ``_run_scenario_query`` 读 ``getattr(result_msg, "text", None)``——``ResultMessage`` dataclass **无**
``.text`` 字段（实测字段：result/num_turns/total_cost_usd/is_error/...）→ 恒 None → ``reply_text`` 恒空 →
``semantic_revise``（reply 含 REVISE）/``no_test``（reply 含 NO TEST）场景靠 reply 文本的 state 匹配恒红
（被 fixture gate 补绿掩盖，P0-2 待 spike）。

r7-S1 修复：抽 ``_extract_reply`` 纯函数读 ``.result``。本文件回归固化字段选择，防回退到 ``.text``。
旁证：``dev-agent.py:470/476`` 自身就用 ``result_msg.result``。
"""
import types

import runtime_evidence as RE


def test_extract_reply_uses_result_field_not_text():
    """取 ``.result``（真实字段）；即便对象上存在 ``.text``（旧 bug 字段）也不取——防回退。"""
    msg = types.SimpleNamespace(result="NO TEST", text="LEAK_IF_USED",
                                num_turns=1, total_cost_usd=0.0, is_error=False)
    assert RE._extract_reply(msg) == "NO TEST"


def test_extract_reply_missing_result_returns_empty():
    """无 ``.result`` 字段 → 空串（不崩；fail-closed：state 匹配 reply 分支 False → 诚实红）。"""
    msg = types.SimpleNamespace(num_turns=1, total_cost_usd=0.0, is_error=False)
    assert RE._extract_reply(msg) == ""


def test_extract_reply_none_msg_returns_empty():
    """result_msg=None（query 未返回 result）→ 空串，不崩。"""
    assert RE._extract_reply(None) == ""
