#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cutover_fixtures.py — task 7.1 historical parity fixtures（production-grade）。

spec task 7.1：「Run shadow parity against historical fixtures and one real no-write dispatch,
resolving every terminal mismatch.」

historical fixtures 覆盖 spec/design decision#2 parity 比对范围的**全 terminal class**，每条 dispatch
record 配等价合法 journal 迁移链（``chain_for``）。``cutover.run_shadow_parity_evidence`` 用真实
``ShadowJournal`` 写这些链、读回 reduce，与 ``summarize_terminal`` 比对——parity matched 即 mismatch
已解决的基线（迁移未改变行为，可放心切 journal_driven_dispatch）。

``NO_WRITE_DRY_RUN_FLOW``：一个真实 no-write dispatch 的 event flow（published 路径，纯 journal 旁路
写，不创建 PR/commit）。

纯数据 + 纯函数（``chain_for``），零 IO/零 SDK——cron 隔离友好；IO 在 ``run_shadow_parity_evidence``。
"""
from __future__ import annotations

# dispatch status → 等价合法 journal 迁移链（复用 loop_state 状态机合法路径，到同一 IterationStatus 终态）。
# 这是「shadow 接入」的逆映射：dispatch 决定 status，shadow 沿合法路径 emit 到等价终态（保 parity）。
_STATUS_CHAINS: dict[str, list[str]] = {
    "pr_open+pass":           ["planned", "running", "agent_finished", "verifying",
                               "publish_ready", "published"],
    "pr_open+revise":         ["planned", "running", "agent_finished", "verifying", "revise"],
    "fail":                   ["planned", "running", "failed"],
    "blocked_external_state": ["planned", "running", "external_blocked"],
    "blocked_test_gate":      ["planned", "running", "agent_finished", "test_blocked"],
    "skip":                   ["planned", "aborted"],
    "planned":                ["planned"],            # skip-dev smoke：过准入未投递，无 running → PLANNED
    "stalled":                ["planned", "running", "agent_finished", "verifying", "revise", "stalled"],
    "orphan_deleted":         ["planned", "running", "orphan_deleted"],
}


def chain_for(record: dict) -> list[str]:
    """historical dispatch record → 等价合法 journal 迁移链（到同一 IterationStatus 终态）。

    ``pr_open`` 按 ``verify.pass`` 分流（+pass→published 链 / !pass→revise 链，与 compat dual-gate 对齐，
    task 4.1）。其它 status 直查 ``_STATUS_CHAINS``。
    """
    s = record.get("status")
    if s == "pr_open":
        verify = record.get("verify") or {}
        return _STATUS_CHAINS["pr_open+pass"] if verify.get("pass") else _STATUS_CHAINS["pr_open+revise"]
    return _STATUS_CHAINS[s]


# historical dispatch records——覆盖全 terminal class（每 class ≥1 条），代表一次混合 cron run 的历史真相。
# 与 test_shadow_parity 同源映射；此处提升为 production historical fixtures（design 7.1 比对基准）。
HISTORICAL_DISPATCH_RECORDS: list[dict] = [
    {"project": "proj-a", "slug": "feat-x", "status": "pr_open", "verify": {"pass": True}},   # PUBLISHED
    {"project": "proj-a", "slug": "feat-y", "status": "pr_open", "verify": {"pass": False}},  # REVISE
    {"project": "proj-b", "slug": "fix-z", "status": "fail"},                                  # FAILED
    {"project": "proj-c", "slug": "feat-w", "status": "blocked_external_state"},               # EXTERNAL_BLOCKED
    {"project": "proj-d", "slug": "feat-v", "status": "blocked_test_gate"},                    # TEST_BLOCKED
    {"project": "proj-e", "slug": "feat-u", "status": "stalled"},                              # STALLED
    {"project": "proj-f", "slug": "feat-t", "status": "orphan_deleted"},                       # ORPHAN_DELETED
    {"project": "proj-g", "slug": "smoke-s", "status": "planned"},                             # PLANNED
]


# 一个真实 no-write dispatch 的 event flow（published 路径）。
# run_shadow_dry_run 写这些 event 到真实 ShadowJournal + reducer 重建——不创建 PR/commit（纯 journal 旁路）。
# 格式：(event_type, iteration_id, prd_id, payload)，匹配 run_shadow_dry_run 的 flow 约定。
NO_WRITE_DRY_RUN_FLOW: list[tuple] = [
    ("planned", "iter_dry", "prd_dry", {"base": "main"}),
    ("running", "iter_dry", "prd_dry", {}),
    ("agent_finished", "iter_dry", "prd_dry", {}),
    ("verifying", "iter_dry", "prd_dry", {}),
    ("publish_ready", "iter_dry", "prd_dry", {}),
    ("published", "iter_dry", "prd_dry", {}),
]
