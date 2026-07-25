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
    """r6 P1-2 + P1-3 + r7-S5 正向：overall + bundle 三件全真 + telemetry_connected=True（已接入，无 open）
    → 谓词 True。r7-S5：telemetry 接入状态须显式声明（connected=True + open_items 空 = 诚实全绿）。"""
    res = {"overall_passed": True, "bundle_publish_ok": True,
           "bundle_digest": "sha256:abc", "evidence_commit": "deadbeef",
           "telemetry_connected": True, "open_items": []}
    ok, reason = RE._drill_predicate("7.6_cutover_suite", res)
    assert ok is True and reason is None


# ---- r7-S5（审核员）：telemetry 未接入时 7.6 不可返回无条件 success（强制接入状态显式诚实声明） ----

def test_drill_predicate_7_6_telemetry_status_must_be_declared():
    """r7-S5（审核员反例精髓）：res 缺 ``telemetry_connected``（接入状态未声明）→ 即便 overall+bundle 全绿，
    7.6 也不可返回无条件 success。旧谓词只查 overall_passed → telemetry 未接入仍声称成功（假绿）。S5 强制：
    base_ok 时 telemetry_connected 必须显式声明（不可假装 telemetry 就绪）。"""
    res = {"overall_passed": True, "bundle_publish_ok": True,
           "bundle_digest": "sha256:abc", "evidence_commit": "deadbeef"}   # 无 telemetry_connected
    ok, reason = RE._drill_predicate("7.6_cutover_suite", res)
    assert ok is False and reason is not None and "telemetry_connected" in reason, (
        "res 缺 telemetry_connected 仍返回 success——S5 未强制 telemetry 接入状态显式声明（假绿）")


def test_drill_predicate_7_6_telemetry_not_connected_is_runtime_red():
    """r8-1（审核员 P0 反向修复）：telemetry_connected=False（真实 OTLP suite 未接入，生产常态）+
    open_items 含 telemetry red（manifest 诚实 open）+ overall+bundle 全绿 → **7.6 runtime 层 ok=False**。

    旧 S5「一致规则」在此输入（connected=False + telemetry 在 open_items）落不到任何 fail 分支 → ok=True 假绿
    （审核员抓的反向：未接入时 7.6 通过）。r8-1 改 connected 驱动：未接入即 runtime 红，接受红色直到
    OTEL_EXPORTER_OTLP_ENDPOINT 接入。与 P1-6 协调：manifest 归档层 overall 仍可绿（drill_ok 排除 telemetry），
    但 runtime main 层 7.6 drill 断言红——两层独立，杜绝「未接入仍声称 runtime 成功」假绿。"""
    res = {"overall_passed": True, "bundle_publish_ok": True,
           "bundle_digest": "sha256:abc", "evidence_commit": "deadbeef",
           "telemetry_connected": False,
           "open_items": [{"item": "telemetry", "passed": False,
                           "limitation": "真实 OTLP/degradation suite 未接入"}]}
    ok, reason = RE._drill_predicate("7.6_cutover_suite", res)
    assert ok is False and reason is not None and "telemetry_connected=False" in reason, (
        "telemetry_connected=False 仍返回 runtime success——r8-1 未修正反向（未接入应 runtime 红）")


def test_drill_predicate_7_6_two_layers_independent_manifest_green_runtime_red():
    """r8-1（审核员 P0 + P1-6 协调）：manifest 归档层 overall_passed=True（P1-6 drill_ok 排除 telemetry，
    套件仍归档绿）与 runtime main 层 7.6 drill 断言 ok=False（connected=False → 红）**两层独立**。

    这是 r8-1 的核心不变式：manifest 可诚实归档绿（open_items 标 telemetry open），但 runtime 7.6 不可因此
    声称成功。旧 S5 把两层耦合（open_items 有 telemetry 就让 7.6 绿）→ 假绿；r8-1 解耦：runtime 只看 connected。"""
    # manifest 维度：overall 绿（P1-6 不阻断）+ open_items 诚实标 telemetry open
    res = {"overall_passed": True, "bundle_publish_ok": True,
           "bundle_digest": "sha256:abc", "evidence_commit": "deadbeef",
           "telemetry_connected": False,                # runtime 维度：OTLP 未接入
           "open_items": [{"item": "telemetry", "passed": False,
                           "limitation": "真实 OTLP/degradation suite 未接入"}]}
    ok, reason = RE._drill_predicate("7.6_cutover_suite", res)
    # manifest overall 绿，但 runtime 7.6 断言红（两层独立）
    assert ok is False, "manifest overall 绿不应让 runtime 7.6 假绿——r8-1 两层解耦未生效"
    assert res["overall_passed"] is True, "manifest 归档层 overall 应保持绿（P1-6 不阻断）"


def test_drill_predicate_7_6_telemetry_not_connected_runtime_red_regardless_of_open_items():
    """r8-1（审核员 P0）：telemetry_connected=False → 7.6 runtime 红，**不管** open_items 是否含 telemetry。

    旧 S5 在此（connected=False + 空 open_items）命中「未进 open_items」分支拒，但那是错误的一致规则（耦合两层）；
    r8-1 改 connected 驱动后，未接入即红（open_items 有无不影响 runtime 判定——open_items 是 manifest 归档层
    诚实标记，runtime 层只看 connected）。与 telemetry_in_open 版（#is_runtime_red）共同覆盖两种 open_items 输入。"""
    res = {"overall_passed": True, "bundle_publish_ok": True,
           "bundle_digest": "sha256:abc", "evidence_commit": "deadbeef",
           "telemetry_connected": False, "open_items": []}   # 未接入且 open_items 空
    ok, reason = RE._drill_predicate("7.6_cutover_suite", res)
    assert ok is False and reason is not None and "telemetry_connected=False" in reason, (
        "telemetry_connected=False（空 open_items）仍 runtime success——r8-1 未改 connected 驱动")


def test_drill_predicate_7_6_telemetry_connected_but_open_item_contradiction_rejected():
    """r7-S5（堵矛盾声明）：telemetry_connected=True（声称已接入）但 open_items 仍含 telemetry red → 自相矛盾
    （接入不应 red）→ 不诚实假绿。7.6 拒。"""
    res = {"overall_passed": True, "bundle_publish_ok": True,
           "bundle_digest": "sha256:abc", "evidence_commit": "deadbeef",
           "telemetry_connected": True,
           "open_items": [{"item": "telemetry", "passed": False, "limitation": "矛盾"}]}
    ok, reason = RE._drill_predicate("7.6_cutover_suite", res)
    assert ok is False and reason is not None and "矛盾" in reason, (
        "telemetry_connected=True 但 open_items 含 telemetry red 的矛盾声明未拒——S5 未堵矛盾假绿")


def test_drill_predicate_7_6_missing_evidence_commit_rejected():
    """r6 P1-3（R4 §2.2）：evidence_commit 缺失（subject 阻断/git 不可用/ancestry 失败）→ 谓词 False。
    overall_passed + bundle 全真但 evidence_commit=None → 仍红（证据未绑定 git ancestry，不可独立验收）。"""
    res = {"overall_passed": True, "bundle_publish_ok": True,
           "bundle_digest": "sha256:abc", "evidence_commit": None}
    ok, reason = RE._drill_predicate("7.6_cutover_suite", res)
    assert ok is False and reason is not None
    assert "evidence_commit=None" in reason
