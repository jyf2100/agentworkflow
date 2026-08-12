#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_graph_admission.py — admission MechanicalNode 测试（任务 3.5d）。

验证（对齐 dispatch_one L2099-2137 准入 4 闸）：
① 配置（KIND_MECHANICAL, stage=dispatch）
② profile 门：admission/dev_agent_ready/type≠code 缺一 → skip（L2099-2102）
③ branch_protection 三态：UNKNOWN→blocked_external_state；NOT_FOUND/False→skip；FOUND+True→过（L2103-2114）
④ already_dispatched 三态：UNKNOWN→blocked；FOUND→skip；NOT_FOUND→过（L2115-2123）
⑤ count_inflight_prs 三态：UNKNOWN→blocked；>=max→skip；<max→过（L2124-2137）
⑥ serial_shadow on → max_inflight=1（off → prof 默认 2，L2134）
⑦ 过 → 不 terminal + _max_inflight/_admission_inflight 写入
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import graph_pa_nodes as GN
import graph_pa_contracts as C


_BASE = {"run_id": "r", "stamp": "20260811", "config": {},
         "_slug": "x", "_prof": {"admission": True, "dev_agent_ready": True, "type": "code", "max_prs_in_flight": 2},
         "_owner_repo": "owner/repo", "_worktree_abs": "/repo", "_base": "main"}


def _mock_branch(monkeypatch, result):
    import run_daily
    monkeypatch.setattr(run_daily, "check_branch_protection", lambda *a, **kw: result)


def _mock_dispatched(monkeypatch, result):
    import run_daily
    monkeypatch.setattr(run_daily, "already_dispatched", lambda *a, **kw: result)


def _mock_inflight(monkeypatch, result):
    import run_daily
    monkeypatch.setattr(run_daily, "count_inflight_prs", lambda *a, **kw: result)


def _mock_devslug(monkeypatch):
    import run_daily
    monkeypatch.setattr(run_daily, "dev_slugify", lambda s: f"dev-{s}")


# ── 配置 ────────────────────────────────────────────────────────────────
def test_admission_config():
    assert GN.node_admission._kind is GN.KIND_MECHANICAL
    assert GN.node_admission._cfg["stage"] == "dispatch"


# ── profile 门（L2099-2102）──────────────────────────────────────────────
def test_admission_profile_missing_admission_skip(monkeypatch):
    _mock_devslug(monkeypatch)
    s = dict(_BASE); s["_prof"] = {**_BASE["_prof"], "admission": False}
    upd = GN.node_admission(s)
    assert upd["terminal"] == "skip"
    assert upd["_exit_status"] == "skip"
    assert "profile" in upd["_skip_reason"]


def test_admission_profile_type_not_code_skip(monkeypatch):
    _mock_devslug(monkeypatch)
    s = dict(_BASE); s["_prof"] = {**_BASE["_prof"], "type": "doc"}
    upd = GN.node_admission(s)
    assert upd["terminal"] == "skip"


def test_admission_profile_not_dev_ready_skip(monkeypatch):
    _mock_devslug(monkeypatch)
    s = dict(_BASE); s["_prof"] = {**_BASE["_prof"], "dev_agent_ready": False}
    upd = GN.node_admission(s)
    assert upd["terminal"] == "skip"


# ── branch protection 三态（L2103-2114）──────────────────────────────────
def test_admission_no_remote_skip(monkeypatch):
    _mock_devslug(monkeypatch)
    s = dict(_BASE); s["_owner_repo"] = ""
    upd = GN.node_admission(s)
    assert upd["terminal"] == "skip"
    assert "无 remote" in upd["_skip_reason"]


def test_admission_branch_protection_unknown_blocked(monkeypatch):
    import run_daily
    _mock_devslug(monkeypatch)
    _mock_branch(monkeypatch, run_daily.unknown("gh 超时"))
    upd = GN.node_admission(dict(_BASE))
    assert upd["terminal"] == C.STATUS_BLOCKED                       # fail-safe（L2108）
    assert upd["_exit_status"] == "blocked_external_state"
    assert upd["_blocked_check"] == "branch_protection"


def test_admission_branch_unprotected_skip(monkeypatch):
    import run_daily
    _mock_devslug(monkeypatch)
    _mock_branch(monkeypatch, run_daily.found(False))                # 明确未保护（L2112）
    upd = GN.node_admission(dict(_BASE))
    assert upd["terminal"] == "skip"


def test_admission_branch_not_found_skip(monkeypatch):
    import run_daily
    _mock_devslug(monkeypatch)
    _mock_branch(monkeypatch, run_daily.not_found("分支不存在"))
    upd = GN.node_admission(dict(_BASE))
    assert upd["terminal"] == "skip"


# ── already_dispatched 三态（L2115-2123）─────────────────────────────────
def test_admission_dispatched_unknown_blocked(monkeypatch):
    import run_daily
    _mock_devslug(monkeypatch)
    _mock_branch(monkeypatch, run_daily.found(True))
    _mock_dispatched(monkeypatch, run_daily.unknown("gh 5xx"))
    upd = GN.node_admission(dict(_BASE))
    assert upd["terminal"] == C.STATUS_BLOCKED                       # fail-safe（L2117）
    assert upd["_blocked_check"] == "idempotency"


def test_admission_dispatched_found_skip(monkeypatch):
    import run_daily
    _mock_devslug(monkeypatch)
    _mock_branch(monkeypatch, run_daily.found(True))
    _mock_dispatched(monkeypatch, run_daily.found("auto/dev-x"))     # 明确已投递（L2121）
    upd = GN.node_admission(dict(_BASE))
    assert upd["terminal"] == "skip"


# ── count_inflight_prs 三态（L2124-2137）─────────────────────────────────
def test_admission_inflight_unknown_blocked(monkeypatch):
    import run_daily
    _mock_devslug(monkeypatch)
    _mock_branch(monkeypatch, run_daily.found(True))
    _mock_dispatched(monkeypatch, run_daily.not_found("新"))
    _mock_inflight(monkeypatch, run_daily.unknown("gh 5xx"))
    upd = GN.node_admission(dict(_BASE))
    assert upd["terminal"] == C.STATUS_BLOCKED                       # fail-safe（L2126）
    assert upd["_blocked_check"] == "inflight_count"


def test_admission_inflight_excess_skip(monkeypatch):
    import run_daily
    _mock_devslug(monkeypatch)
    _mock_branch(monkeypatch, run_daily.found(True))
    _mock_dispatched(monkeypatch, run_daily.not_found("新"))
    _mock_inflight(monkeypatch, run_daily.found(2))                  # = max(2) → 超额（L2135）
    upd = GN.node_admission(dict(_BASE))
    assert upd["terminal"] == "skip"
    assert "超额" in upd["_skip_reason"]


def test_admission_serial_shadow_max1(monkeypatch):
    """single_flight_serial_shadow on → max_inflight=1（inflight=1 即超额，L2134）。"""
    import run_daily
    import types
    _mock_devslug(monkeypatch)
    _mock_branch(monkeypatch, run_daily.found(True))
    _mock_dispatched(monkeypatch, run_daily.not_found("新"))
    _mock_inflight(monkeypatch, run_daily.found(1))
    s = dict(_BASE); s["_coord_flags"] = types.SimpleNamespace(single_flight_serial_shadow=True)
    upd = GN.node_admission(s)
    assert upd["terminal"] == "skip"                                 # 1 >= 1（serial_shadow max=1）
    assert "≥ 1" in upd["_skip_reason"]


# ── 过（不 terminal + _max_inflight 写入）──────────────────────────────────
def test_admission_pass(monkeypatch):
    import run_daily
    _mock_devslug(monkeypatch)
    _mock_branch(monkeypatch, run_daily.found(True))
    _mock_dispatched(monkeypatch, run_daily.not_found("新"))
    _mock_inflight(monkeypatch, run_daily.found(0))
    upd = GN.node_admission(dict(_BASE))
    assert not upd.get("terminal")
    assert "_exit_status" not in upd                                  # 过不设 _exit_status
    assert upd["_max_inflight"] == 2
    assert upd["_admission_inflight"] == 0
