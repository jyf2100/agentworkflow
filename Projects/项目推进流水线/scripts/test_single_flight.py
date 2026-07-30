#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_single_flight.py — single-flight-auto-merge task 2.2 + 2.3：per-owner_repo 单飞 slot 单测。

验证 D9 spec 契约（single-flight-auto-merge / fail-safe-dispatch）：
  - slot 状态 = journal 在途闭环状态（append-only slot journal 重放）+ lease TTL，**非** GitHub OPEN PR、
    **非** 进程内 threading.Lock（threading.Lock 跨 cron 进程不可见，D9 审核一致 F5）。
  - 三态：FREE（无记录 / released / lease 过期 stale）/ IN_FLIGHT（acquired lease 未过期）/ UNKNOWN
    （journal 中部损坏 → fail-safe 阻断，**不当代空闲**，spec「Single-flight slot is unknown」）。
  - 跨进程 flock 互斥（fcntl LOCK_EX|LOCK_NB）：crash 时 OS 自动释放 flock，但 journal+lease 留痕供 recovery。
  - task 2.3 crash 恢复：lease 过期 → known FREE（基于 lease-expiry 的显式判定，**非盲目 free**，spec
    「resolve to known, MUST NOT default to free」）；lease 未过期 → in-flight-with-lease（保留不接管）。

纯 IO 模块（文件系统 + fcntl），零 SDK；AAA 结构；时间注入 now_fn/stamp_fn 保测试确定性（同 ShadowJournal
不触系统时间的约定）。跑：python3 -m pytest scripts/test_single_flight.py -q
"""
from __future__ import annotations

import fcntl
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import journal as J  # noqa: E402
import single_flight as SF  # noqa: E402  (RED：模块尚未实现)
from loop_state import JOURNAL_SCHEMA_VERSION, JournalEvent  # noqa: E402
from single_flight import SlotState  # noqa: E402

UTC = timezone.utc
OWNER = "jyf2100/cc-web-control"


def _now(h: float = 0) -> datetime:
    """注入的「当前时间」——datetime 对象（lease 算术用），h 小时偏移。"""
    return datetime(2026, 7, 29, 0, 0, 0, tzinfo=UTC) + timedelta(hours=h)


def _stamp() -> str:
    """注入的事件 timestamp（ISO 字符串，同 ShadowJournal stamp 约定）。"""
    return "2026-07-29T00:00:00Z"


def _acquired_event(jpath, *, lease_expires_at, owner_repo=OWNER, event_id="e1",
                    run_id="run-1", prd_id="prd-1", iteration_id="iter-1"):
    """直接写一条 slot_acquired 事件（测 query 独立于 acquire 实现）。"""
    ev = JournalEvent(schema_version=JOURNAL_SCHEMA_VERSION, event_id=event_id,
                      timestamp=_stamp(), iteration_id=iteration_id, run_id=run_id,
                      prd_id=prd_id, event_type="slot_acquired",
                      payload={"owner_repo": owner_repo, "lease_expires_at": lease_expires_at})
    J.append_event(jpath, ev)


def _released_event(jpath, *, owner_repo=OWNER, outcome="done", event_id="e2",
                    run_id="run-1", prd_id="prd-1", iteration_id="iter-1"):
    ev = JournalEvent(schema_version=JOURNAL_SCHEMA_VERSION, event_id=event_id,
                      timestamp=_stamp(), iteration_id=iteration_id, run_id=run_id,
                      prd_id=prd_id, event_type="slot_released",
                      payload={"owner_repo": owner_repo, "outcome": outcome})
    J.append_event(jpath, ev)


# ─── query_slot 三态（task 2.2 核心：slot 状态机）─────────────────────────
def test_query_free_when_no_journal(tmp_path):
    # 无 slot journal = 从未投递 → FREE
    q = SF.query_slot(tmp_path, OWNER, now_fn=lambda: _now(0))
    assert q.state is SlotState.FREE
    assert "no_record" in q.reason


def test_query_free_when_released(tmp_path):
    jp = SF.slot_journal_path(tmp_path, OWNER)
    _acquired_event(jp, lease_expires_at=_now(2).isoformat())
    _released_event(jp)
    q = SF.query_slot(tmp_path, OWNER, now_fn=lambda: _now(1))
    assert q.state is SlotState.FREE
    assert "released" in q.reason


def test_query_free_when_lease_expired_stale(tmp_path):
    # task 2.3：acquired 但 lease 已过期 = crash 残留 → known FREE（显式 lease-expiry 判定，非盲目 free）
    jp = SF.slot_journal_path(tmp_path, OWNER)
    _acquired_event(jp, lease_expires_at=_now(1).isoformat())   # lease 1h
    q = SF.query_slot(tmp_path, OWNER, now_fn=lambda: _now(2))  # now 2h > lease → stale
    assert q.state is SlotState.FREE
    assert "stale" in q.reason


def test_query_inflight_when_lease_active(tmp_path):
    jp = SF.slot_journal_path(tmp_path, OWNER)
    _acquired_event(jp, lease_expires_at=_now(2).isoformat())   # lease 2h
    q = SF.query_slot(tmp_path, OWNER, now_fn=lambda: _now(1))  # now 1h < lease → 在途
    assert q.state is SlotState.IN_FLIGHT
    assert q.lease_expires_at == _now(2).isoformat()


def test_query_unknown_when_journal_corrupted(tmp_path):
    # complete-JSON-but-schema-invalid（缺 JournalEvent 必填字段）→ journal.read_events fail-closed raise
    # → query_slot 捕获 → UNKNOWN（fail-safe：不当代空闲，spec「Single-flight slot is unknown」）
    jp = SF.slot_journal_path(tmp_path, OWNER)
    jp.parent.mkdir(parents=True, exist_ok=True)
    jp.write_text('{"bad":"middle"}\n', encoding="utf-8")
    q = SF.query_slot(tmp_path, OWNER, now_fn=lambda: _now(0))
    assert q.state is SlotState.UNKNOWN
    assert q.reason


def test_query_takes_last_event_as_current_state(tmp_path):
    # append-only：最后一条事件 = 当前 slot 态（acquired→released→acquired = 在途）
    jp = SF.slot_journal_path(tmp_path, OWNER)
    _acquired_event(jp, lease_expires_at=_now(1).isoformat(), event_id="a1")
    _released_event(jp, event_id="r1")
    _acquired_event(jp, lease_expires_at=_now(5).isoformat(), event_id="a2")   # 再 acquire（lease 远未来）
    q = SF.query_slot(tmp_path, OWNER, now_fn=lambda: _now(2))
    assert q.state is SlotState.IN_FLIGHT


# ─── safe filename（owner/repo → 路径安全名）──────────────────────────────
def test_safe_name_replaces_slash():
    assert SF._safe_name("a/b") == "a__b"
    assert SF._safe_name("jyf2100/cc-web-control") == "jyf2100__cc-web-control"


def test_slot_paths_use_safe_name(tmp_path):
    jp = SF.slot_journal_path(tmp_path, "a/b")
    lk = SF.slot_lock_path(tmp_path, "a/b")
    assert jp.name == "a__b.journal.jsonl"
    assert lk.name == "a__b.lock"
    assert jp.parent == tmp_path / "slots"


# ─── acquire_slot（task 2.2 准入门：query + flock + 写 acquired 原子闭环）──
def test_acquire_free_succeeds_and_writes_acquired(tmp_path):
    res, handle = SF.acquire_slot(tmp_path, OWNER, run_id="r", prd_id="p", iteration_id="i",
                                  now_fn=lambda: _now(0), stamp_fn=_stamp, lease_ttl=3600)
    assert res.acquired and handle is not None
    jp = SF.slot_journal_path(tmp_path, OWNER)
    evs = J.read_events(jp)
    acquired = next(e for e in evs if e.event_type == "slot_acquired")
    assert acquired.payload["owner_repo"] == OWNER
    # lease_expires_at = now(0) + 1h
    assert acquired.payload["lease_expires_at"] == _now(1).isoformat()
    SF.release_slot(handle, stamp_fn=_stamp, run_id="r", prd_id="p", iteration_id="i", owner_repo=OWNER)


def test_acquire_inflight_blocked(tmp_path):
    jp = SF.slot_journal_path(tmp_path, OWNER)
    _acquired_event(jp, lease_expires_at=_now(2).isoformat())   # 在途
    res, handle = SF.acquire_slot(tmp_path, OWNER, run_id="r2", prd_id="p2", iteration_id="i2",
                                  now_fn=lambda: _now(1), stamp_fn=_stamp, lease_ttl=3600)
    assert not res.acquired and handle is None
    assert res.blocked_reason == "inflight"
    assert res.query.state is SlotState.IN_FLIGHT


def test_acquire_unknown_blocked(tmp_path):
    jp = SF.slot_journal_path(tmp_path, OWNER)
    jp.parent.mkdir(parents=True, exist_ok=True)
    jp.write_text('{"bad":"middle"}\n', encoding="utf-8")   # 损坏 → UNKNOWN
    res, handle = SF.acquire_slot(tmp_path, OWNER, run_id="r2", prd_id="p2", iteration_id="i2",
                                  now_fn=lambda: _now(0), stamp_fn=_stamp, lease_ttl=3600)
    assert not res.acquired and handle is None
    assert res.blocked_reason == "unknown"
    assert res.query.state is SlotState.UNKNOWN


def test_acquire_flock_busy_blocked(tmp_path):
    # 预占 lock 文件（模拟另一 cron 进程持有跨进程 flock）→ acquire flock 失败
    lk = SF.slot_lock_path(tmp_path, OWNER)
    lk.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lk, os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        res, handle = SF.acquire_slot(tmp_path, OWNER, run_id="r2", prd_id="p2", iteration_id="i2",
                                      now_fn=lambda: _now(0), stamp_fn=_stamp, lease_ttl=3600)
        assert not res.acquired and handle is None
        assert res.blocked_reason == "flock_busy"
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd)


# ─── release_slot + slot_scope（try/finally 保证 release，flock 不泄漏）────
def test_release_writes_event_and_unlocks_for_next_acquire(tmp_path):
    res, handle = SF.acquire_slot(tmp_path, OWNER, run_id="r", prd_id="p", iteration_id="i",
                                  now_fn=lambda: _now(0), stamp_fn=_stamp, lease_ttl=3600)
    assert res.acquired
    SF.release_slot(handle, stamp_fn=_stamp, run_id="r", prd_id="p", iteration_id="i", owner_repo=OWNER)
    q = SF.query_slot(tmp_path, OWNER, now_fn=lambda: _now(0))
    assert q.state is SlotState.FREE                       # released
    res2, _ = SF.acquire_slot(tmp_path, OWNER, run_id="r3", prd_id="p3", iteration_id="i3",
                              now_fn=lambda: _now(0), stamp_fn=_stamp, lease_ttl=3600)
    assert res2.acquired                                   # flock 已释放可重新 acquire


def test_slot_scope_releases_on_exception(tmp_path):
    # with 内抛异常 → __exit__ 仍 release（flock/journal 不泄漏）
    with pytest.raises(RuntimeError):
        with SF.slot_scope(tmp_path, OWNER, run_id="r", prd_id="p", iteration_id="i",
                           now_fn=lambda: _now(0), stamp_fn=_stamp, lease_ttl=3600) as res:
            assert res.acquired
            raise RuntimeError("boom")
    res2, _ = SF.acquire_slot(tmp_path, OWNER, run_id="r2", prd_id="p2", iteration_id="i2",
                              now_fn=lambda: _now(0), stamp_fn=_stamp, lease_ttl=3600)
    assert res2.acquired                                   # scope 异常退出后 flock 已释放


def test_slot_scope_blocked_returns_non_acquired(tmp_path):
    jp = SF.slot_journal_path(tmp_path, OWNER)
    _acquired_event(jp, lease_expires_at=_now(2).isoformat())   # 在途
    with SF.slot_scope(tmp_path, OWNER, run_id="r2", prd_id="p2", iteration_id="i2",
                       now_fn=lambda: _now(1), stamp_fn=_stamp, lease_ttl=3600) as res:
        assert not res.acquired
        assert res.blocked_reason == "inflight"


# ═══ task 4.2 / 4.3：HALTED 态（post-merge UNKNOWN / revert 非 REVERTED → halt 整仓到人工）═══════
# spec「Revert itself fails halts the queue ... no further PRD admitted until manual resolution」+「UNKNOWN test
# result SHALL halt the queue」。slot 须能表达「halt 到人工」——lease(2h) 过期会自动 free，撑不住；故新 HALTED
# 态：末事件 slot_halted → HALTED（覆盖 lease，lease 过期也不自动 free），直到 slot_resumed（人工 unblock）。
def _halted_event(jpath, *, owner_repo=OWNER, reason="post_merge_safety", event_id="e3",
                  run_id="run-1", prd_id="prd-1", iteration_id="iter-1"):
    """直接写一条 slot_halted 事件（测 query 独立于 halt_slot 实现）。"""
    ev = JournalEvent(schema_version=JOURNAL_SCHEMA_VERSION, event_id=event_id,
                      timestamp=_stamp(), iteration_id=iteration_id, run_id=run_id,
                      prd_id=prd_id, event_type="slot_halted",
                      payload={"owner_repo": owner_repo, "reason": reason})
    J.append_event(jpath, ev)


def _resumed_event(jpath, *, owner_repo=OWNER, event_id="e4",
                   run_id="run-1", prd_id="prd-1", iteration_id="iter-1"):
    ev = JournalEvent(schema_version=JOURNAL_SCHEMA_VERSION, event_id=event_id,
                      timestamp=_stamp(), iteration_id=iteration_id, run_id=run_id,
                      prd_id=prd_id, event_type="slot_resumed",
                      payload={"owner_repo": owner_repo})
    J.append_event(jpath, ev)


def test_query_halted_when_last_event_halted(tmp_path):
    # acquired→halted：末事件 slot_halted → HALTED（4.2/4.3 halt 整仓）
    jp = SF.slot_journal_path(tmp_path, OWNER)
    _acquired_event(jp, lease_expires_at=_now(2).isoformat())
    _halted_event(jp)
    assert SF.query_slot(tmp_path, OWNER, now_fn=lambda: _now(1)).state is SlotState.HALTED


def test_query_halted_persists_past_lease_expiry(tmp_path):
    # halted + lease 早过期 → 仍 HALTED（不自动 free——halt 须到人工 resume，lease 不能偷放）
    jp = SF.slot_journal_path(tmp_path, OWNER)
    _acquired_event(jp, lease_expires_at=_now(1).isoformat())
    _halted_event(jp)
    q = SF.query_slot(tmp_path, OWNER, now_fn=lambda: _now(48))   # now 48h ≫ lease
    assert q.state is SlotState.HALTED


def test_query_free_after_resume(tmp_path):
    # halted→resumed：末事件 slot_resumed → FREE（人工 unblock 后可重新投递）
    jp = SF.slot_journal_path(tmp_path, OWNER)
    _acquired_event(jp, lease_expires_at=_now(2).isoformat())
    _halted_event(jp)
    _resumed_event(jp)
    assert SF.query_slot(tmp_path, OWNER, now_fn=lambda: _now(1)).state is SlotState.FREE


def test_acquire_halted_blocked(tmp_path):
    # halt 状态下新 PRD 准入 → blocked(halted)（不投递，spec「no further PRD admitted」）
    jp = SF.slot_journal_path(tmp_path, OWNER)
    _halted_event(jp)
    res, handle = SF.acquire_slot(tmp_path, OWNER, run_id="r2", prd_id="p2", iteration_id="i2",
                                  now_fn=lambda: _now(0), stamp_fn=_stamp, lease_ttl=3600)
    assert not res.acquired and res.blocked_reason == "halted"


def test_halt_slot_then_release_keeps_halted(tmp_path):
    # acquire → halt_slot(handle) → release（slot_scope __exit__ 语义）→ 末事件仍 slot_halted（非 released）
    res, handle = SF.acquire_slot(tmp_path, OWNER, run_id="r", prd_id="p", iteration_id="i",
                                  now_fn=lambda: _now(0), stamp_fn=_stamp, lease_ttl=3600)
    assert res.acquired
    SF.halt_slot(handle, reason="post_merge_revert_unknown", run_id="r", prd_id="p",
                 iteration_id="i", owner_repo=OWNER, stamp_fn=_stamp)
    SF.release_slot(handle, stamp_fn=_stamp, run_id="r", prd_id="p",
                    iteration_id="i", owner_repo=OWNER)   # 模拟 slot_scope __exit__
    q = SF.query_slot(tmp_path, OWNER, now_fn=lambda: _now(0))
    assert q.state is SlotState.HALTED   # release 未写 slot_released 覆盖 halt


def test_resume_slot_clears_halt_then_acquire_ok(tmp_path):
    # halt 后人工 resume_slot → FREE → 可重新 acquire（恢复投递）
    jp = SF.slot_journal_path(tmp_path, OWNER)
    _halted_event(jp)
    SF.resume_slot(tmp_path, OWNER, run_id="r", prd_id="p", iteration_id="i", stamp_fn=_stamp)
    assert SF.query_slot(tmp_path, OWNER, now_fn=lambda: _now(0)).state is SlotState.FREE
    res, _ = SF.acquire_slot(tmp_path, OWNER, run_id="r2", prd_id="p2", iteration_id="i2",
                             now_fn=lambda: _now(0), stamp_fn=_stamp, lease_ttl=3600)
    assert res.acquired
