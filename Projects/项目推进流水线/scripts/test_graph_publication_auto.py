#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_graph_publication_auto.py — publish_gates + publish_merge 骨架测试（任务 3.5f）。

验证（对齐 dispatch_one auto_merge 块 L2356-2437）：
① publish_gates：auto_merge off→no-op；on + is_in_cooldown→triaged；on + has_open_intent→halted+halt+CRITICAL；过→pass
② publish_merge 骨架：auto_merge off→no-op；merge phase 串 record_event 顺序 + terminal 映射：
   - rebase fail → triaged(merge_abandoned)
   - merged(PASS) → _exit_status=merged(merge_completed)
   - reverted(FAIL+REVERTED) → triaged(record_revert + revert_completed)
   - revert CONFLICT → halted(revert_started, 无 completed)+halt+CRITICAL
   - post-merge UNKNOWN → halted(无 revert)+halt+CRITICAL
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import graph_pa_nodes as GN
import graph_pa_contracts as C
import merge_phase as MP


_BASE = {"run_id": "r", "stamp": "20260811", "config": {},
         "_owner_repo": "owner/repo", "_prd": "prd-1", "_iter": "r:prd:0",
         "_slug": "x", "_worktree_abs": "/repo", "_base": "main", "_branch": "pa-dev-x",
         "_prd_abs": "/abs/prd.md", "_dev_log_file": None,
         "_prof": {"conda_env": "", "repo": "owner/repo"},
         "_verify_payload": {"pass": True, "test_rc": 0}}

_COORD = types.SimpleNamespace(circuit_key="prd-stable-key")


def _flags_auto(on=True):
    return types.SimpleNamespace(single_flight_serial_shadow=True,
                                 single_flight_auto_merge=on, session_aware_retry=False)


def _auto_state():
    s = dict(_BASE); s["_coord_flags"] = _flags_auto(on=True); s["_coord"] = _COORD; return s


# ── 通用 mock ─────────────────────────────────────────────────────────
def _mock_run_helpers(monkeypatch):
    import run_daily
    import main_status as MS
    monkeypatch.setattr(run_daily, "_run_dev_agent", lambda cmd, wt, slug, log: "{}")
    monkeypatch.setattr(run_daily, "_post_merge_test_cmd", lambda *a, **kw: "pytest")
    monkeypatch.setattr(run_daily, "_env_python", lambda env: "py")
    monkeypatch.setattr(run_daily, "_raise_critical_alert_safe", lambda *a, **kw: None)
    monkeypatch.setattr(MS, "record_main_verified", lambda *a, **kw: None)


def _mock_mp_cmds(monkeypatch):
    monkeypatch.setattr(MP, "build_merge_cmd", lambda **kw: "mc")
    monkeypatch.setattr(MP, "build_post_merge_cmd", lambda **kw: "pmc")
    monkeypatch.setattr(MP, "build_revert_cmd", lambda **kw: "rvc")


def _capture_events(monkeypatch):
    import merge_loop as ML
    events = []
    monkeypatch.setattr(ML, "record_event",
                        lambda *a, **kw: events.append((a[3] if len(a) > 3 else None, kw)))
    return events


def _capture_reverts(monkeypatch):
    import circuit_breaker as CB
    reverts = []
    monkeypatch.setattr(CB, "record_revert", lambda *a, **kw: reverts.append(a))
    return reverts


def _capture_halts(monkeypatch):
    halts = []
    monkeypatch.setattr(GN, "_halt_slot_safe_graph", lambda state, *, reason: halts.append(reason))
    return halts


# MR/PMR/RVR fixtures
_mr_ok = types.SimpleNamespace(merged=True, merge_commit="abc123", triage_reason="")
_mr_fail = types.SimpleNamespace(merged=False, triage_reason="rebase_conflict")
_pmr_pass = types.SimpleNamespace(verdict=MP.PostMergeVerdict.PASS)
_pmr_fail = types.SimpleNamespace(verdict=MP.PostMergeVerdict.FAIL)
_pmr_unknown = types.SimpleNamespace(verdict=MP.PostMergeVerdict.UNKNOWN)
_rvr_reverted = types.SimpleNamespace(outcome=MP.RevertOutcome.REVERTED, revert_commit="def456")
_rvr_conflict = types.SimpleNamespace(outcome=MP.RevertOutcome.CONFLICT, revert_commit=None)


# ══════════════════════ publish_gates ══════════════════════
def test_publish_gates_config():
    assert GN.node_publish_gates._kind is GN.KIND_MECHANICAL
    assert GN.node_publish_gates._cfg["stage"] == "dispatch"


def test_publish_gates_baseline_noop(monkeypatch):
    """auto_merge off → no-op（baseline 走 publish_baseline，L2438 else 分支）。"""
    import circuit_breaker as CB
    called = []
    monkeypatch.setattr(CB, "is_in_cooldown", lambda *a, **kw: called.append(1) or False)
    s = dict(_BASE); s["_coord_flags"] = _flags_auto(on=False)
    upd = GN.node_publish_gates(s)
    assert called == []                                        # flag off 不查 cooldown
    assert not upd.get("terminal")


def test_publish_gates_cooldown_triaged(monkeypatch):
    """is_in_cooldown True → triaged(cooldown_revert_loop)（L2360-2364）。"""
    import circuit_breaker as CB
    import merge_loop as ML
    monkeypatch.setattr(CB, "is_in_cooldown", lambda *a, **kw: True)
    monkeypatch.setattr(ML, "has_open_intent", lambda *a, **kw: False)
    upd = GN.node_publish_gates(_auto_state())
    assert upd["terminal"] == C.STATUS_TRIAGED
    assert upd["_exit_status"] == "triaged"
    assert upd["_triage_reason"] == "cooldown_revert_loop"


def test_publish_gates_open_intent_halt(monkeypatch):
    """has_open_intent True → halted + halt_slot + CRITICAL（fail-safe，L2370-2377）。"""
    import circuit_breaker as CB
    import merge_loop as ML
    monkeypatch.setattr(CB, "is_in_cooldown", lambda *a, **kw: False)
    monkeypatch.setattr(ML, "has_open_intent", lambda *a, **kw: True)
    halts = _capture_halts(monkeypatch)
    upd = GN.node_publish_gates(_auto_state())
    assert upd["terminal"] == C.STATUS_HALTED
    assert upd["_exit_status"] == "halted"
    assert upd["_triage_reason"] == "merge_loop_open_intent"
    assert halts == ["merge_loop_open_intent"]                 # halt_slot 调


def test_publish_gates_passed(monkeypatch):
    """两门过 → 无 terminal（进 publish_merge）。"""
    import circuit_breaker as CB
    import merge_loop as ML
    monkeypatch.setattr(CB, "is_in_cooldown", lambda *a, **kw: False)
    monkeypatch.setattr(ML, "has_open_intent", lambda *a, **kw: False)
    upd = GN.node_publish_gates(_auto_state())
    assert not upd.get("terminal")


# ══════════════════════ publish_merge 骨架 ══════════════════════
def test_publish_merge_config():
    assert GN.node_publish_merge._kind is GN.KIND_MECHANICAL
    assert GN.node_publish_merge._cfg["stage"] == "dispatch"


def test_publish_merge_baseline_noop(monkeypatch):
    """auto_merge off → no-op（baseline 走 publish_baseline）。"""
    import merge_loop as ML
    called = []
    monkeypatch.setattr(ML, "record_event", lambda *a, **kw: called.append(1))
    s = dict(_BASE); s["_coord_flags"] = _flags_auto(on=False)
    upd = GN.node_publish_merge(s)
    assert called == []
    assert not upd.get("terminal")


def test_publish_merge_abandoned(monkeypatch):
    """merge phase rebase fail → triaged + event 顺序 [merge_started, merge_abandoned]（L2433-2437）。"""
    _mock_run_helpers(monkeypatch); _mock_mp_cmds(monkeypatch)
    monkeypatch.setattr(MP, "parse_merge_result", lambda p: _mr_fail)
    events = _capture_events(monkeypatch)
    upd = GN.node_publish_merge(_auto_state())
    assert upd["terminal"] == C.STATUS_TRIAGED
    assert upd["_triage_reason"] == "rebase_conflict"
    assert [e[0] for e in events] == ["merge_started", "merge_abandoned"]


def test_publish_merge_merged(monkeypatch):
    """PASS → _exit_status=merged + event [merge_started, merge_completed]（L2399-2402）。"""
    _mock_run_helpers(monkeypatch); _mock_mp_cmds(monkeypatch)
    monkeypatch.setattr(MP, "parse_merge_result", lambda p: _mr_ok)
    monkeypatch.setattr(MP, "parse_post_merge_result", lambda p: _pmr_pass)
    events = _capture_events(monkeypatch)
    upd = GN.node_publish_merge(_auto_state())
    assert upd["_exit_status"] == "merged"
    assert upd["_merge_commit"] == "abc123"
    assert not upd.get("terminal")                             # merged 不 terminal，交 terminal_emit
    assert [e[0] for e in events] == ["merge_started", "merge_completed"]


def test_publish_merge_reverted(monkeypatch):
    """FAIL+REVERTED → triaged(post_merge_red_reverted) + record_revert + [merge_started,revert_started,revert_completed]（L2403-2418）。"""
    _mock_run_helpers(monkeypatch); _mock_mp_cmds(monkeypatch)
    monkeypatch.setattr(MP, "parse_merge_result", lambda p: _mr_ok)
    monkeypatch.setattr(MP, "parse_post_merge_result", lambda p: _pmr_fail)
    monkeypatch.setattr(MP, "parse_revert_result", lambda p: _rvr_reverted)
    events = _capture_events(monkeypatch)
    reverts = _capture_reverts(monkeypatch)
    upd = GN.node_publish_merge(_auto_state())
    assert upd["terminal"] == C.STATUS_TRIAGED
    assert upd["_triage_reason"] == "post_merge_red_reverted"
    assert upd["_reverted"] is True
    assert upd["_revert_commit"] == "def456"
    assert [e[0] for e in events] == ["merge_started", "revert_started", "revert_completed"]
    assert len(reverts) == 1                                    # CB.record_revert 调（cooldown 种）


def test_publish_merge_revert_conflict_halt(monkeypatch):
    """FAIL+CONFLICT → halted + halt_slot + CRITICAL + [merge_started,revert_started]（无 completed，L2419-2425）。"""
    _mock_run_helpers(monkeypatch); _mock_mp_cmds(monkeypatch)
    monkeypatch.setattr(MP, "parse_merge_result", lambda p: _mr_ok)
    monkeypatch.setattr(MP, "parse_post_merge_result", lambda p: _pmr_fail)
    monkeypatch.setattr(MP, "parse_revert_result", lambda p: _rvr_conflict)
    events = _capture_events(monkeypatch)
    halts = _capture_halts(monkeypatch)
    upd = GN.node_publish_merge(_auto_state())
    assert upd["terminal"] == C.STATUS_HALTED
    assert upd["_exit_status"] == "halted"
    assert "post_merge_revert_conflict" == upd["_triage_reason"]
    assert [e[0] for e in events] == ["merge_started", "revert_started"]
    assert halts == ["post_merge_revert_conflict"]


def test_publish_merge_post_merge_unknown_halt(monkeypatch):
    """post-merge UNKNOWN → halted（不 auto-revert）+ halt_slot + CRITICAL（L2426-2432）。"""
    _mock_run_helpers(monkeypatch); _mock_mp_cmds(monkeypatch)
    monkeypatch.setattr(MP, "parse_merge_result", lambda p: _mr_ok)
    monkeypatch.setattr(MP, "parse_post_merge_result", lambda p: _pmr_unknown)
    events = _capture_events(monkeypatch)
    halts = _capture_halts(monkeypatch)
    upd = GN.node_publish_merge(_auto_state())
    assert upd["terminal"] == C.STATUS_HALTED
    assert upd["_triage_reason"] == "post_merge_unknown"
    assert [e[0] for e in events] == ["merge_started"]          # 无 revert（UNKNOWN 不 auto-revert）
    assert halts == ["post_merge_unknown"]
