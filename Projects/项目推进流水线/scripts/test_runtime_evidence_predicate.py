#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""r3 P0-1 闭环：7.2 ``_drill_predicate`` 谓词单元测试。

runtime_evidence.py 作为 CLI 历来靠真实 ``--drill`` 运行验证（无单测传统，见 test_runtime_evidence_index.py
先例）。本文件专测 r3 P0-1 闭环加固的 7.2 谓词——直接构造 ``per_scenario_real_triggers`` 矩阵，断言谓词在
wrong gate（MEDIUM-1）/ 缺 callback 场景（HIGH-1）/ 缺场景条目时判 False，杜绝假绿。

注：``_drill_predicate`` 为模块私有函数，但它是验收关键判定（决定 drill 是否归档 passing evidence），
测试须直连断言其行为（pytest 访问私有成员惯例；ruff E9+F 规则集不禁止）。
"""
import cutover as CT
import runtime_evidence as RE

# 真实 adapter gate（run_sdk_hook_canary fixture 的 8 场景 gate，确定性符合 EXPECTED_LIFECYCLE_GATES）。
_ADAPTER_GATES = dict(CT.run_sdk_hook_canary().stop_gates)
_REQUIRED = CT.SDK_CALLBACK_REQUIRED_SCENARIOS


def _green_per_scenario() -> dict:
    """全绿 per_scenario 矩阵：6 必须场景 sdk_callback_real_proven + 全 8 场景 adapter_gate_outcome 精确匹配。"""
    per: dict = {}
    for sc, gate in CT.EXPECTED_LIFECYCLE_GATES.items():
        per[sc] = {
            "expected_event": "Stop",
            "sdk_callback_real_proven": sc in _REQUIRED,   # 6 必须场景 proven；PreCompact 两场景诚实 blocked
            "adapter_gate_outcome": _ADAPTER_GATES.get(sc, gate),
        }
    return per


def _res(per=None, cb_proven=True) -> dict:
    return {
        "lifecycle_callback_proven": cb_proven,
        "per_scenario_real_triggers": _green_per_scenario() if per is None else per,
        "blocked_scenarios": [],
    }


def test_drill_predicate_7_2_green_passes():
    """全绿：6 callback 场景 proven + 8 场景 gate 精确匹配 + lifecycle_cb → 7.2 谓词 True。"""
    ok, reason = RE._drill_predicate("7.2_sdk_canary", _res())
    assert ok is True and reason is None


def test_drill_predicate_7_2_wrong_gate_rejected():
    """r3 P0-1 闭环 MEDIUM-1：adapter gate 非空但错（test_green 应 PUBLISH 实给错值）→ False。
    旧 truthy 判定会假绿（错值非空→True）；exact-match 必须拒。"""
    per = _green_per_scenario()
    per["test_green"] = {**per["test_green"], "adapter_gate_outcome": "WRONG_NONEMPTY_GATE"}
    ok, reason = RE._drill_predicate("7.2_sdk_canary", _res(per))
    assert ok is False and reason is not None


def test_drill_predicate_7_2_missing_callback_scenario_rejected():
    """r3 P0-1 闭环 HIGH-1（谓词侧）：缺一个必须 callback 场景的 proven → sdk_cb_ok False → False。"""
    per = _green_per_scenario()
    per["subagent"] = {**per["subagent"], "sdk_callback_real_proven": False}
    ok, reason = RE._drill_predicate("7.2_sdk_canary", _res(per))
    assert ok is False and reason is not None


def test_drill_predicate_7_2_missing_scenario_entry_rejected():
    """r3 P0-1 闭环 MEDIUM-1：场景条目数 < 8（缺一个场景）→ gate_ok False（exact-match 需全 8 场景对齐）。"""
    per = _green_per_scenario()
    del per["hook_failure"]
    ok, reason = RE._drill_predicate("7.2_sdk_canary", _res(per))
    assert ok is False and reason is not None
