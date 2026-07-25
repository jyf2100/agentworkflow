#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""r5 P0（评审 #1）：real_sdk_canary 逐场景独立 query + runner-owned correlation 测试。

评审 P0 反例：旧 real_sdk_canary 跑 base+subagent 两条 query 汇总 ``real_triggered``，把一个 PostToolUse
批量映射给 test_red/stale_test/test_green——「一旦出现 PostToolUse，三场景全 proven」的假绿；同理一个
PreCompact 批量证明 compaction+hook_failure。r5 P0 改为每场景跑**独立** SDK query（独立 hook journal +
runner-owned correlation_id），杜绝跨场景批量补绿。

real_sdk_canary 调真实 ``claude_agent_sdk.query``（仅 ``--drill all`` 真跑，pytest 不触发）。本文件 monkeypatch
``_run_scenario_query`` 注入受控 per-scenario 结果，验证聚合逻辑的逐场景隔离——无需真实 SDK query 成本。
"""
import os

import pytest

import runtime_evidence as RE


@pytest.fixture(autouse=True)
def _restore_loop_env(monkeypatch):
    """real_sdk_canary 函数体用 ``os.environ["..."] = "1"`` **直接设** PA_LOOP_LIFECYCLE_HOOKS /
    PA_LOOP_JOURNAL_SHADOW（非 monkeypatch，无 teardown 清理）。本模块首次在 pytest 中调用 real_sdk_canary
    → 这两 env 泄漏到后续 ``test_shadow_dispatch``::test_dispatch_one_preflight_blocks_invalid_flag_combo
    （dispatch_one admission 门顺序受这两个 env 影响）致其假失败。预占这两个 key 让 monkeypatch teardown
    恢复测试前值（产品代码逻辑不动——生产需这些 env 控制 HookAdapter）。"""
    for _k in ("PA_LOOP_LIFECYCLE_HOOKS", "PA_LOOP_JOURNAL_SHADOW"):
        monkeypatch.setenv(_k, os.environ.get(_k, ""))
    yield


def _fake_runner(proven_map):
    """构造受控 ``_run_scenario_query`` 替身：按 ``spec.id`` 决定该场景独立 journal 是否产出 expected event。

    ``proven_map[spec.id]=True`` → 该场景独立 journal 含 expected event + invocation 带 own correlation_id
    （``sdk_callback_real_proven=True``）；``False`` → 空 journal（proven=False，即便别处同 event 类型也不补绿）。
    """
    def fake(spec, *, workdir, stamp):
        cid = f"{stamp}:{spec.id}"   # runner-owned（r4 §3：runner 生成、closure 捕获，非模型回传）
        proven = proven_map.get(spec.id, False)
        invs = ({"event": spec.expected_event, "correlation_id": cid},) if proven else ()
        return {
            "per_scenario_entry": {
                "expected_event": spec.expected_event, "correlation_id": cid,
                "query_ran": True, "result_received": True, "query_error": None,
                "journal_path": f"/tmp/fake_{spec.id}.hooks.jsonl",
                "observed_event_types": [spec.expected_event] if proven else [],
                "invocation_count": 1 if proven else 0,
                "invocation_carries_own_cid": proven,
                "sdk_callback_real_proven": proven, "real_proven": proven,
            },
            "callback_invocations": invs,
            "callback_errors": [],
            "sdk_types": [spec.expected_event] if proven else [],
            "observed_lifecycle_types": {spec.expected_event} if proven else set(),
            "journal_decode_errors": 0, "query_error": None, "result_received": True,
            "num_turns": 1, "cost_usd": 0.0, "saw_lifecycle_event": proven,
        }
    return fake


def test_real_sdk_canary_posttooluse_not_batched_across_scenarios(monkeypatch, tmp_path):
    """r5 P0 核心：test_red 的 PostToolUse 不可批量补绿 test_green/stale_test（共享 event 类型但独立 journal）。

    旧实现：test_red/green/stale_test expected_event 均为 PostToolUse，任一触发 → real_triggered 含
    PostToolUse → 三者 sdk_cb 全 True（批量假绿）。r5 P0：每场景独立 query+journal+correlation_id，
    scenario proven 只看自己 journal → test_green/stale_test 即使 test_red proven 也保持 False。"""
    proven = {"test_red": True, "test_green": False, "stale_test": False,
              "no_test": True, "semantic_revise": True, "subagent": True}
    monkeypatch.setattr(RE, "_run_scenario_query", _fake_runner(proven))
    res = RE.real_sdk_canary(tmp_path)
    per = res["per_scenario_real_triggers"]
    assert per["test_red"]["sdk_callback_real_proven"] is True
    assert per["test_green"]["sdk_callback_real_proven"] is False, (
        "test_green 被 test_red 的 PostToolUse 批量补绿——P0 批量证明漏洞未修复")
    assert per["stale_test"]["sdk_callback_real_proven"] is False, (
        "stale_test 被 test_red 的 PostToolUse 批量补绿——P0 批量证明漏洞未修复")
    cids = [per[s]["correlation_id"] for s in ("test_red", "test_green", "stale_test")]
    assert len(set(cids)) == 3


def test_real_sdk_canary_blocked_scenarios_have_independent_correlation(monkeypatch, tmp_path):
    """r5 P0：compaction/hook_failure（PreCompact）headless 不可靠触发 → 各持独立 correlation_id 诚实 blocked。

    评审 P0 反例：旧实现一个 PreCompact 批量证明 compaction+hook_failure 两场景。r5 P0：两场景 query 不跑，
    各持独立 correlation_id，``sdk_callback_real_proven=False``——别处 PreCompact 无法批量补绿。即便
    ``_fake_runner`` 让其余 6 场景全 proven，这两条仍 blocked。"""
    proven = {"no_test": True, "test_red": True, "test_green": True, "stale_test": True,
              "semantic_revise": True, "subagent": True}
    monkeypatch.setattr(RE, "_run_scenario_query", _fake_runner(proven))
    res = RE.real_sdk_canary(tmp_path)
    per = res["per_scenario_real_triggers"]
    assert per["compaction"]["sdk_callback_real_proven"] is False
    assert per["compaction"]["query_ran"] is False
    assert per["hook_failure"]["sdk_callback_real_proven"] is False
    assert per["hook_failure"]["query_ran"] is False
    # 独立 correlation_id（即便同为 PreCompact 也不共享）
    assert per["compaction"]["correlation_id"] != per["hook_failure"]["correlation_id"]
    # journal_paths 只含真正跑 query 的 6 场景（compaction/hook_failure 无 journal）
    assert len(res["our_hook_journal_paths"]) == 6


def test_real_sdk_canary_correlation_id_runner_owned(monkeypatch, tmp_path):
    """r5 P0（r4 §3 不可信边界）：correlation_id 由 runner 生成（``f"{stamp}:{scenario_id}"``），经 closure 注入
    callback 记录，**不**依赖模型在 prompt/tool args/文本输出回传。验证 correlation_id 形态（runner 控制）+
    invocation 携带 own cid。"""
    monkeypatch.setattr(RE, "_run_scenario_query", _fake_runner({"test_red": True}))
    res = RE.real_sdk_canary(tmp_path)
    per = res["per_scenario_real_triggers"]["test_red"]
    assert per["correlation_id"].endswith(":test_red")
    invs = res["callback_invocations"]
    assert any(i.get("correlation_id") == per["correlation_id"] for i in invs), (
        "callback invocation 未携带 own correlation_id——runner-owned correlation 未注入")
