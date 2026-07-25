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


def _state_for(sc):
    """每场景正确 observed_state（r6 P0：state 精确匹配，让 evaluate_scenario 通过）。"""
    return {
        "test_red": {"bash_results": [{"exit_code": 1, "output": ""}], "reply_text": "", "saw_tool_use": True, "saw_subagent_start": False},
        "test_green": {"bash_results": [{"exit_code": 0, "output": "GREEN"}], "reply_text": "", "saw_tool_use": True, "saw_subagent_start": False},
        "stale_test": {"bash_results": [{"exit_code": 0, "output": "STALE"}], "reply_text": "", "saw_tool_use": True, "saw_subagent_start": False},
        "semantic_revise": {"bash_results": [], "reply_text": "REVISE", "saw_tool_use": False, "saw_subagent_start": False},
        "no_test": {"bash_results": [], "reply_text": "NO TEST", "saw_tool_use": False, "saw_subagent_start": False},
        "subagent": {"bash_results": [], "reply_text": "", "saw_tool_use": False, "saw_subagent_start": True},
        "compaction": {"bash_results": [], "reply_text": "", "saw_tool_use": False, "saw_subagent_start": False},
        "hook_failure": {"bash_results": [], "reply_text": "", "saw_tool_use": False, "saw_subagent_start": False},
    }.get(sc, {"bash_results": [], "reply_text": "", "saw_tool_use": False, "saw_subagent_start": False})


def _green_per_scenario() -> dict:
    """全绿 per_scenario 矩阵：8 场景 journal+cid+state+gate 全绑定（r6 P0：同源 + state 精确匹配）。"""
    per: dict = {}
    for sc, gate in CT.EXPECTED_LIFECYCLE_GATES.items():
        per[sc] = {
            "expected_event": "Stop",
            "sdk_callback_real_proven": True,                       # 向后兼容（旧字段）
            "adapter_gate_outcome": _ADAPTER_GATES.get(sc, gate),   # 向后兼容
            # r6 P0：per-scenario 绑定字段（evaluate_sdk_canary_scenarios 消费）
            "journal_has_expected": True,
            "carries_own_cid": True,
            "adapter_gate": _ADAPTER_GATES.get(sc, gate),
            "observed_state": _state_for(sc),
        }
    return per


def _res(per=None, cb_proven=True, **integrity) -> dict:
    base = {
        "lifecycle_callback_proven": cb_proven,
        "per_scenario_real_triggers": _green_per_scenario() if per is None else per,
        "blocked_scenarios": [],
        # r5 P1-2（评审）：绿 query 须正常结束——result_received=True、query_error=None、无 callback 异常、
        # journal 可解析。谓词经 evaluate_sdk_canary_scenarios 消费这些维度（integrity 违例即证据不可信）。
        "result_received": True,
        "query_error": None,
        "callback_errors": [],
        "journal_decode_errors": 0,
    }
    base.update(integrity)
    return base


def test_drill_predicate_7_2_green_passes():
    """全绿：6 callback 场景 proven + 8 场景 gate 精确匹配 + lifecycle_cb + query 正常结束 → 7.2 谓词 True。"""
    ok, reason = RE._drill_predicate("7.2_sdk_canary", _res())
    assert ok is True and reason is None


def test_drill_predicate_7_2_integrity_violation_rejected():
    """r5 P1-2（评审反例，7.2 入口）：构造 callback_errors 非空 + journal_decode_errors>0 + query_error 非空 +
    result_received=False **且** 场景矩阵（gate+callback）全真 → 7.2 谓词必 False。此前谓词只查 cb_proven +
    scenario_verdict（不含 integrity），同输入返回 (True, None) 假绿。integrity 现经 evaluate_sdk_canary_scenarios
    （7.2 + 7.6 共调纯函数）堵住。"""
    res = _res(callback_errors=[{"event": "Stop"}], journal_decode_errors=1,
               query_error="proxy 5xx", result_received=False)
    ok, reason = RE._drill_predicate("7.2_sdk_canary", res)
    assert ok is False and reason is not None
    assert "evidence_intact=False" in reason


def test_drill_predicate_7_2_wrong_gate_rejected():
    """r3 P0-1 闭环 MEDIUM-1：adapter gate 非空但错（test_green 应 PUBLISH 实给错值）→ False。
    旧 truthy 判定会假绿（错值非空→True）；exact-match 必须拒。"""
    per = _green_per_scenario()
    per["test_green"] = {**per["test_green"], "adapter_gate": "WRONG_NONEMPTY_GATE"}
    ok, reason = RE._drill_predicate("7.2_sdk_canary", _res(per))
    assert ok is False and reason is not None


def test_drill_predicate_7_2_missing_callback_scenario_rejected():
    """r3 P0-1 闭环 HIGH-1（谓词侧）：缺一个必须 callback 场景的 proven → sdk_cb_ok False → False。"""
    per = _green_per_scenario()
    per["subagent"] = {**per["subagent"], "journal_has_expected": False, "carries_own_cid": False}
    ok, reason = RE._drill_predicate("7.2_sdk_canary", _res(per))
    assert ok is False and reason is not None


def test_drill_predicate_7_2_missing_scenario_entry_rejected():
    """r3 P0-1 闭环 MEDIUM-1：场景条目数 < 8（缺一个场景）→ gate_ok False（exact-match 需全 8 场景对齐）。"""
    per = _green_per_scenario()
    del per["hook_failure"]
    ok, reason = RE._drill_predicate("7.2_sdk_canary", _res(per))
    assert ok is False and reason is not None


# ---- r6 P1-2：bundle publication fail-closed（7.6 入口） ----

def test_drill_predicate_7_6_bundle_publish_failure_rejected():
    """r6 P1-2（评审反例，7.6 入口）：overall_passed=True 但 bundle_publish_ok=False + digest=None
    → 谓词必 False。旧谓词只查 overall_passed，同输入返回 (True, None) 假绿——manifest 全绿但 bundle
    publish 抛异常（写盘/scan/digest 失败）仍声称 passing。P1-2：bundle publication fail-closed。"""
    res = {
        "overall_passed": True,        # manifest 维度全绿
        "bundle_publish_ok": False,    # 但 publish 失败
        "bundle_digest": None,         # 无 digest
    }
    ok, reason = RE._drill_predicate("7.6_cutover_suite", res)
    assert ok is False and reason is not None
    assert "bundle_publish_ok=False" in reason


def test_drill_predicate_7_6_bundle_digest_missing_rejected():
    """r6 P1-2 另一面：overall_passed=True + bundle_publish_ok=True 但 bundle_digest=None（publish 写盘
    但未产出 digest）→ 谓词 False。digest 是跨机器可复核锚点，缺失即不可复核 → fail-closed。"""
    res = {"overall_passed": True, "bundle_publish_ok": True, "bundle_digest": None}
    ok, reason = RE._drill_predicate("7.6_cutover_suite", res)
    assert ok is False and reason is not None
    assert "bundle_digest=None" in reason


def test_drill_predicate_7_6_green_passes():
    """r6 P1-2 + P1-3 正向：overall_passed + bundle_publish_ok + bundle_digest + evidence_commit 四者全真 → 谓词 True。"""
    res = {"overall_passed": True, "bundle_publish_ok": True,
           "bundle_digest": "sha256:abc", "evidence_commit": "deadbeef"}
    ok, reason = RE._drill_predicate("7.6_cutover_suite", res)
    assert ok is True and reason is None


def test_drill_predicate_7_6_missing_evidence_commit_rejected():
    """r6 P1-3（R4 §2.2）：evidence_commit 缺失（subject 阻断/git 不可用/ancestry 失败）→ 谓词 False。
    overall_passed + bundle 全真但 evidence_commit=None → 仍红（证据未绑定 git ancestry，不可独立验收）。"""
    res = {"overall_passed": True, "bundle_publish_ok": True,
           "bundle_digest": "sha256:abc", "evidence_commit": None}
    ok, reason = RE._drill_predicate("7.6_cutover_suite", res)
    assert ok is False and reason is not None
    assert "evidence_commit=None" in reason
