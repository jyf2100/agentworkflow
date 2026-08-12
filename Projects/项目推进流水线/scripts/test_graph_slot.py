#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_graph_slot.py — slot_acquire/release MechanicalNode 测试（任务 3.5f）。

验证（对齐 _run_one L2779-2796 + _slot_blocked_record L2742-2755）：
① slot_acquire：flag off/无 remote→no-op；on+acquired→写 _slot_handle；
   unknown→blocked_external_state（fail-safe）；inflight/flock_busy/halted→skip（让位）
② slot_release：无 handle→no-op；有 handle→release_slot（outcome 据终态：halted/cooldown→halted，余→done）
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import graph_pa_nodes as GN
import graph_pa_contracts as C


_BASE = {"run_id": "r", "stamp": "20260811", "config": {},
         "_owner_repo": "owner/repo", "_prd": "prd-1", "_iter": "r:prd:0",
         "_slug": "x", "_worktree_abs": "/repo", "_base": "main"}


def _flags(serial_shadow=True):
    return types.SimpleNamespace(single_flight_serial_shadow=serial_shadow,
                                 single_flight_auto_merge=False,
                                 session_aware_retry=False)


def _acq_result(acquired, blocked_reason=None, qreason=""):
    return types.SimpleNamespace(
        acquired=acquired, blocked_reason=blocked_reason,
        query=types.SimpleNamespace(reason=qreason))


# ── slot_acquire ──────────────────────────────────────────────────────
def _mock_acquire(monkeypatch, acq, handle="handle"):
    import single_flight as SF
    monkeypatch.setattr(SF, "acquire_slot", lambda *a, **kw: (acq, handle))


def test_slot_acquire_config():
    assert GN.node_slot_acquire._kind is GN.KIND_MECHANICAL
    assert GN.node_slot_acquire._cfg["stage"] == "dispatch"


def test_slot_acquire_baseline_noop(monkeypatch):
    """serial_shadow off → no-op（不调 acquire_slot，无 terminal，对齐 L2792 baseline threading.Lock）。"""
    import single_flight as SF
    called = []
    monkeypatch.setattr(SF, "acquire_slot", lambda *a, **kw: called.append(1))
    s = dict(_BASE); s["_coord_flags"] = _flags(serial_shadow=False)
    upd = GN.node_slot_acquire(s)
    assert called == []
    assert not upd.get("terminal")


def test_slot_acquire_no_remote_noop(monkeypatch):
    """flag on 但无 owner_repo → no-op（baseline 不串行，对齐 L2764 仓无 remote）。"""
    _mock_acquire(monkeypatch, _acq_result(True))
    s = dict(_BASE); s["_coord_flags"] = _flags(); s["_owner_repo"] = ""
    upd = GN.node_slot_acquire(s)
    assert not upd.get("terminal")


def test_slot_acquire_acquired(monkeypatch):
    """acquire 成功 → 写 _slot_handle（slot_release/halt 用），无 terminal。"""
    _mock_acquire(monkeypatch, _acq_result(True), handle="slot-handle")
    s = dict(_BASE); s["_coord_flags"] = _flags()
    upd = GN.node_slot_acquire(s)
    assert upd["_slot_handle"] == "slot-handle"
    assert not upd.get("terminal")


def test_slot_acquire_unknown_blocked(monkeypatch):
    """acquire 返 unknown → terminal=blocked + _exit_status（fail-safe，L2749-2751）。"""
    _mock_acquire(monkeypatch, _acq_result(False, "unknown", "gh 5xx"))
    s = dict(_BASE); s["_coord_flags"] = _flags()
    upd = GN.node_slot_acquire(s)
    assert upd["terminal"] == C.STATUS_BLOCKED
    assert upd["_exit_status"] == "blocked_external_state"
    assert upd["_blocked_check"] == "single_flight_slot"
    assert "状态不明" in upd["_skip_reason"]


def test_slot_acquire_inflight_skip(monkeypatch):
    """acquire 返 inflight → skip（让位，下轮 cron 再投，L2752-2754）。"""
    _mock_acquire(monkeypatch, _acq_result(False, "inflight", "另一闭环在途"))
    s = dict(_BASE); s["_coord_flags"] = _flags()
    upd = GN.node_slot_acquire(s)
    assert upd["terminal"] == "skip"
    assert upd["_exit_status"] == "skip"
    assert "inflight" in upd["_skip_reason"]


def test_slot_acquire_halted_skip(monkeypatch):
    """acquire 返 halted → skip（halt 整仓，归 else 分支让位）。"""
    _mock_acquire(monkeypatch, _acq_result(False, "halted", "整仓 halt"))
    s = dict(_BASE); s["_coord_flags"] = _flags()
    upd = GN.node_slot_acquire(s)
    assert upd["terminal"] == "skip"


# ── slot_release ──────────────────────────────────────────────────────
def _mock_release(monkeypatch, captured):
    import single_flight as SF
    monkeypatch.setattr(SF, "release_slot",
                        lambda handle, **kw: captured.update(outcome=kw.get("outcome"), handle=handle))


def test_slot_release_config():
    assert GN.node_slot_release._kind is GN.KIND_MECHANICAL
    assert GN.node_slot_release._cfg["stage"] == "dispatch"


def test_slot_release_no_handle_noop(monkeypatch):
    """无 _slot_handle（baseline）→ no-op（不调 release_slot）。"""
    import single_flight as SF
    called = []
    monkeypatch.setattr(SF, "release_slot", lambda *a, **kw: called.append(1))
    GN.node_slot_release(dict(_BASE))                            # 无 _slot_handle → no-op
    assert called == []


def test_slot_release_done_outcome(monkeypatch):
    """handle + 无 terminal → outcome=done（正常结束释放 flock）。"""
    captured = {}
    _mock_release(monkeypatch, captured)
    s = dict(_BASE); s["_slot_handle"] = "h"
    upd = GN.node_slot_release(s)
    assert captured["outcome"] == "done"
    assert captured["handle"] == "h"
    assert upd["_slot_handle"] is None
    assert upd["_slot_released"] is True


def test_slot_release_halted_outcome(monkeypatch):
    """handle + terminal=HALTED → outcome=halted（保 slot_halted 终态语义）。"""
    captured = {}
    _mock_release(monkeypatch, captured)
    s = dict(_BASE); s["_slot_handle"] = "h"; s["terminal"] = C.STATUS_HALTED
    GN.node_slot_release(s)
    assert captured["outcome"] == "halted"


def test_slot_release_cooldown_halted_outcome(monkeypatch):
    """handle + terminal=COOLDOWN → outcome=halted（cooldown 同 halt 终态）。"""
    captured = {}
    _mock_release(monkeypatch, captured)
    s = dict(_BASE); s["_slot_handle"] = "h"; s["terminal"] = C.STATUS_COOLDOWN
    GN.node_slot_release(s)
    assert captured["outcome"] == "halted"
