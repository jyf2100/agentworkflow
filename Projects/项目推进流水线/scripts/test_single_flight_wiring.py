#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_single_flight_wiring.py — single-flight-auto-merge task 2.2/2.3：``_run_one`` slot 接线集成测。

验证 ``run_daily._run_one`` 把串行互斥从进程内 ``threading.Lock`` 升级为跨进程 slot（flock + journal）的接线：
  - ``serial_shadow`` on + slot FREE → acquire slot + 调 ``dispatch_one`` + 闭环释放（acquired+released）；
  - ``serial_shadow`` on + slot IN_FLIGHT → **不**调 dispatch_one，rec=skip（让位，spec「Same repo second PRD waits」）；
  - ``serial_shadow`` on + slot UNKNOWN（journal 损坏）→ **不**调 dispatch_one，rec=blocked_external_state
    （fail-safe，spec「Single-flight slot is unknown → blocked_external_state」）；
  - ``serial_shadow`` off → baseline：不碰 slot（无 slot journal），照常调 dispatch_one（design 决策#8）。

stub 掉 ``dispatch_one`` / ``_attach_learning_memory`` / ``repo_owner_repo``，隔离 slot 准入焦点（不触 dev loop/SDK）。
跑：python3 -m pytest scripts/test_single_flight_wiring.py -q
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import journal as J  # noqa: E402
import run_daily  # noqa: E402
import single_flight as SF  # noqa: E402
from loop_state import JOURNAL_SCHEMA_VERSION, JournalEvent  # noqa: E402

STAMP = "2026-07-29T00:00:00Z"
OWNER = "test/repo"


def _entry() -> dict:
    return {"project": "p", "prd_path": "PRDs/x.md"}


def _prof(serial_shadow: bool) -> dict:
    prof = {"name": "p", "repo": "/tmp/fake", "admission": True, "dev_agent_ready": True,
            "type": "code", "default_branch": "main"}
    if serial_shadow:
        prof["loop"] = {"single_flight_serial_shadow": True}
    return prof


def _args() -> types.SimpleNamespace:
    return types.SimpleNamespace(_learning_sdk_query_fn=None)


def _stub(monkeypatch, tmp_path) -> list:
    """stub dispatch_one（记录调用）+ repo_owner_repo + STATE_DIR + _attach_learning_memory。返回 calls 列表。"""
    calls: list = []

    def _fake_dispatch(entry, prof, stamp, args):
        calls.append(entry.get("prd_path"))
        return {"project": entry.get("project"), "prd_path": entry.get("prd_path"),
                "slug": "s", "status": "planned"}

    monkeypatch.setattr(run_daily, "dispatch_one", _fake_dispatch)
    monkeypatch.setattr(run_daily, "repo_owner_repo", lambda repo: OWNER)
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path)
    monkeypatch.setattr(run_daily, "_attach_learning_memory", lambda *a, **k: None)
    return calls


def _write_acquired(jpath, *, lease_expires_at, owner_repo=OWNER):
    ev = JournalEvent(schema_version=JOURNAL_SCHEMA_VERSION, event_id="pre-e",
                      timestamp=STAMP, iteration_id="i", run_id="r", prd_id="p",
                      event_type="slot_acquired",
                      payload={"owner_repo": owner_repo, "lease_expires_at": lease_expires_at})
    J.append_event(jpath, ev)


# ─── serial_shadow on：slot 准入分支 ──────────────────────────────────────
def test_run_one_serial_shadow_acquires_slot_and_dispatches(tmp_path, monkeypatch):
    monkeypatch.setenv("PA_SINGLE_FLIGHT_SERIAL_SHADOW", "1")
    calls = _stub(monkeypatch, tmp_path)
    rec = run_daily._run_one(_entry(), _prof(True), STAMP, _args())
    assert rec["status"] == "planned"                   # dispatch_one 返回值透传
    assert calls == ["PRDs/x.md"]                       # slot FREE → dispatch_one 被调
    # 闭环完整：slot journal 留 acquired + released（with 退出释放，flock 不泄漏）
    ev_types = [e.event_type for e in J.read_events(SF.slot_journal_path(tmp_path, OWNER))]
    assert "slot_acquired" in ev_types and "slot_released" in ev_types


def test_run_one_serial_shadow_inflight_skips_dispatch(tmp_path, monkeypatch):
    monkeypatch.setenv("PA_SINGLE_FLIGHT_SERIAL_SHADOW", "1")
    calls = _stub(monkeypatch, tmp_path)
    _write_acquired(SF.slot_journal_path(tmp_path, OWNER), lease_expires_at="2099-01-01T00:00:00+00:00")
    rec = run_daily._run_one(_entry(), _prof(True), STAMP, _args())
    assert calls == []                                  # 在途 → 不调 dispatch_one（让位）
    assert rec["status"] == "skip"
    assert "inflight" in rec["skip_reason"]


def test_run_one_serial_shadow_unknown_blocks_external_state(tmp_path, monkeypatch):
    monkeypatch.setenv("PA_SINGLE_FLIGHT_SERIAL_SHADOW", "1")
    calls = _stub(monkeypatch, tmp_path)
    jp = SF.slot_journal_path(tmp_path, OWNER)
    jp.parent.mkdir(parents=True, exist_ok=True)
    jp.write_text('{"bad":"middle"}\n', encoding="utf-8")   # 损坏 → UNKNOWN
    rec = run_daily._run_one(_entry(), _prof(True), STAMP, _args())
    assert calls == []                                  # fail-safe → 不调 dispatch_one
    assert rec["status"] == "blocked_external_state"
    assert rec["blocked_check"] == "single_flight_slot"


# ─── serial_shadow off：baseline 不变 ─────────────────────────────────────
def test_run_one_serial_shadow_off_no_slot_journal(tmp_path, monkeypatch):
    monkeypatch.delenv("PA_SINGLE_FLIGHT_SERIAL_SHADOW", raising=False)
    calls = _stub(monkeypatch, tmp_path)
    rec = run_daily._run_one(_entry(), _prof(False), STAMP, _args())
    assert rec["status"] == "planned"                   # baseline dispatch_one 返回值透传
    assert calls == ["PRDs/x.md"]                       # baseline：照常调 dispatch_one
    assert not SF.slot_journal_path(tmp_path, OWNER).exists()   # flag off → 不碰 slot
