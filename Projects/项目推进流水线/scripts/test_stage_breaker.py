#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_stage_breaker.py — pa-hardening P0-1：段级熔断器（双熔丝）单测。

覆盖矩阵（方案 §5.1）：
  * flag 门控（PA_BREAKER off = record/check/add_cost 全 no-op，零文件零异常）；
  * 熔丝1：同段连续 N 败触发（默认 3 / PA_BREAKER_N 自定义）、跨段不串味、成功重置；
  * 熔丝2：PA_BREAKER_COST_CAP 当日成本超限触发、unset 不启用；
  * tripped 后 check 拒绝（CircuitBreakerTripped 携带 reason + 状态文件路径）；
  * 次日新 stamp 自然重置（状态文件按 stamp 命名）；
  * 观测模式 PA_BREAKER_OBSERVE：fuse 命中不真熔（不抛、不写 tripped）但 journal 留痕；
  * journal 事件落盘（stage_persona_failed / stage_breaker_tripped，append_event 原子追加）；
  * fail-open 边界：状态文件损坏 → 不熔断不抛；
  * consecutive_trip_days 跨日熔断史计数（窗口内不同 stamp 去重）。

时间注入：固定 now_fn（无系统时间依赖，确定性）。跑：python3 -m pytest scripts/test_stage_breaker.py -q
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import stage_breaker as SB  # noqa: E402
from journal import read_events  # noqa: E402

UTC = timezone.utc
STAMP = "20260915"
_NOW = datetime(2026, 9, 15, 3, 17, 0, tzinfo=UTC)
_now = lambda: _NOW.isoformat()  # noqa: E731  固定时间注入


def _on(**extra):
    env = {"PA_BREAKER": "1"}
    env.update(extra)
    return env


def _fail(state_dir, stage="prd", n=1, env=None, label="prd"):
    for _ in range(n):
        SB.record_failure(state_dir, STAMP, stage, label, "transient", now_fn=_now, env=env)


# ─── flag 门控：off = 零行为 ───────────────────────────────────────────────
def test_flag_off_noop(tmp_path):
    env = {"PA_BREAKER": "0"}
    _fail(tmp_path, n=5, env=env)                                     # 不抛、不计数
    SB.record_success(tmp_path, STAMP, "prd", now_fn=_now, env=env)
    SB.add_cost(tmp_path, STAMP, 99.0, now_fn=_now, env=env)
    SB.check(tmp_path, STAMP, env=env)                                # 静默
    assert not (tmp_path / f"breaker_{STAMP}.json").exists()
    assert SB.is_tripped(tmp_path, STAMP, env=env) is False


# ─── 熔丝1：同段连续失败 ──────────────────────────────────────────────────
def test_fuse1_consecutive_failures_trip_at_default_n(tmp_path):
    env = _on()
    try:
        _fail(tmp_path, n=3, env=env)                                 # 默认 N=3：第 3 败熔
        tripped = None
    except SB.CircuitBreakerTripped as e:
        tripped = e
    assert tripped is not None
    assert "连续 3 次失败" in str(tripped)
    assert tripped.breaker_path == str(tmp_path / f"breaker_{STAMP}.json")


def test_fuse1_below_threshold_no_trip(tmp_path):
    _fail(tmp_path, n=2, env=_on())                                   # 2 < 3：不熔
    assert SB.is_tripped(tmp_path, STAMP, env=_on()) is False


def test_fuse1_custom_n_via_env(tmp_path):
    env = _on(PA_BREAKER_N="2")
    try:
        _fail(tmp_path, n=2, env=env)
        raised = False
    except SB.CircuitBreakerTripped:
        raised = True
    assert raised


def test_fuse1_success_resets_consecutive(tmp_path):
    env = _on()
    _fail(tmp_path, n=2, env=env)
    SB.record_success(tmp_path, STAMP, "prd", now_fn=_now, env=env)
    _fail(tmp_path, n=2, env=env)                                     # 重置后再 2 败仍 < 3
    assert SB.is_tripped(tmp_path, STAMP, env=env) is False


def test_fuse1_stages_do_not_cross_contaminate(tmp_path):
    env = _on()
    _fail(tmp_path, stage="radar", n=2, env=env)
    _fail(tmp_path, stage="prd", n=2, env=env)                        # 各段独立计数
    st = SB.load_breaker(tmp_path, STAMP)
    assert st["consecutive"] == {"radar": 2, "prd": 2}


# ─── 熔丝2：当日成本 cap ──────────────────────────────────────────────────
def test_fuse2_cost_cap_trips(tmp_path):
    env = _on(PA_BREAKER_COST_CAP="1.0")
    SB.add_cost(tmp_path, STAMP, 0.6, now_fn=_now, env=env)
    assert SB.is_tripped(tmp_path, STAMP, env=env) is False
    try:
        SB.add_cost(tmp_path, STAMP, 0.5, now_fn=_now, env=env)       # 1.1 ≥ 1.0 → 熔
        raised = False
    except SB.CircuitBreakerTripped as e:
        raised = True
        assert "≥ cap" in str(e)
    assert raised


def test_fuse2_unset_cap_disabled(tmp_path):
    _fail(tmp_path, n=2, env=_on())                                   # 先记账 2 败（不熔）
    SB.add_cost(tmp_path, STAMP, 999.0, now_fn=_now, env=_on())       # cap unset → 只记账不熔
    assert SB.is_tripped(tmp_path, STAMP, env=_on()) is False


def test_fuse2_checked_on_failure_path_too(tmp_path):
    """「只失败不花钱」路径：record_failure 兜底复查 cap（成本主记账在 add_cost）。"""
    SB.add_cost(tmp_path, STAMP, 1.0, now_fn=_now, env=_on())         # cap unset：只记账不熔
    env = _on(PA_BREAKER_COST_CAP="1.0")
    try:
        SB.record_failure(tmp_path, STAMP, "prd", "prd", "transient", now_fn=_now, env=env)
        raised = False
    except SB.CircuitBreakerTripped:
        raised = True
    assert raised


def test_add_cost_skips_invalid_amounts(tmp_path):
    env = _on()
    for bad in (None, "1", True, -5):
        SB.add_cost(tmp_path, STAMP, bad, now_fn=_now, env=env)
    assert not (tmp_path / f"breaker_{STAMP}.json").exists()          # 非法值连状态文件都不建


# ─── tripped 后 check 拒绝 + 次日重置 ─────────────────────────────────────
def test_check_raises_after_tripped(tmp_path):
    env = _on()
    try:
        _fail(tmp_path, n=3, env=env)
    except SB.CircuitBreakerTripped:
        pass
    try:
        SB.check(tmp_path, STAMP, "report", env=env)                  # halted 后拒绝续跑
        raised = False
    except SB.CircuitBreakerTripped as e:
        raised = True
        assert "report" in str(e)
    assert raised


def test_next_day_stamp_resets(tmp_path):
    """次日新 stamp 自然重置（新状态文件；批处理场景不做自动半开）。"""
    env = _on()
    try:
        _fail(tmp_path, n=3, env=env)
    except SB.CircuitBreakerTripped:
        pass
    next_day = "20260916"
    SB.check(tmp_path, next_day, "fetch", env=env)                    # 新 stamp 干净
    _fail(tmp_path, n=1, env=env)                                     # 老 stamp 再败也不重复抛（已 tripped 静默）
    assert SB.is_tripped(tmp_path, next_day, env=env) is False


def test_record_failure_after_tripped_silent(tmp_path):
    env = _on()
    try:
        _fail(tmp_path, n=3, env=env)
    except SB.CircuitBreakerTripped:
        pass
    SB.record_failure(tmp_path, STAMP, "prd", "prd", "permanent", now_fn=_now, env=env)  # 不再抛


# ─── 观测模式 ──────────────────────────────────────────────────────────────
def test_observe_mode_never_trips_but_journals(tmp_path):
    env = _on(PA_BREAKER_OBSERVE="1")
    _fail(tmp_path, n=10, env=env)                                    # fuse 命中也只观测
    SB.add_cost(tmp_path, STAMP, 100.0, now_fn=_now, env=_on(PA_BREAKER_COST_CAP="1", PA_BREAKER_OBSERVE="1"))
    assert SB.is_tripped(tmp_path, STAMP, env=_on()) is False
    SB.check(tmp_path, STAMP, env=_on())                              # 静默通过
    events = read_events(SB.breaker_journal_path(tmp_path))
    trips = [e for e in events if e.event_type == "stage_breaker_tripped"]
    assert len(trips) >= 2
    assert all((e.payload or {}).get("observe") is True for e in trips)


# ─── journal 事件落盘 ──────────────────────────────────────────────────────
def test_journal_events_written(tmp_path):
    env = _on()
    _fail(tmp_path, n=2, env=env)
    events = read_events(SB.breaker_journal_path(tmp_path))
    fails = [e for e in events if e.event_type == "stage_persona_failed"]
    assert len(fails) == 2
    assert fails[0].payload["stage"] == "prd" and fails[0].payload["err_kind"] == "transient"
    try:
        _fail(tmp_path, n=1, env=env)                                 # 第 3 败 → trip
    except SB.CircuitBreakerTripped:
        pass
    events = read_events(SB.breaker_journal_path(tmp_path))
    assert any(e.event_type == "stage_breaker_tripped" and (e.payload or {}).get("stamp") == STAMP
               for e in events)


# ─── fail-open：状态损坏 ───────────────────────────────────────────────────
def test_corrupt_state_fail_open(tmp_path):
    (tmp_path / f"breaker_{STAMP}.json").write_text("{broken", encoding="utf-8")
    SB.record_failure(tmp_path, STAMP, "prd", "prd", "transient", now_fn=_now, env=_on())  # 不抛（自愈重计）
    SB.check(tmp_path, STAMP, env=_on())                              # 不抛
    st = json.loads((tmp_path / f"breaker_{STAMP}.json").read_text(encoding="utf-8"))
    assert st["consecutive"]["prd"] == 1                              # 从头计（坏账不续）


# ─── 跨日熔断史 ────────────────────────────────────────────────────────────
def test_consecutive_trip_days_counts_distinct_stamps(tmp_path):
    def _trip_at(stamp, when):
        SB._journal_append(tmp_path, stamp, SB._TRIP_EVENT, {"reason": "x", "observe": False},
                           now_fn=lambda: when.isoformat())
    _trip_at("20260913", _NOW - timedelta(days=2))
    _trip_at("20260914", _NOW - timedelta(days=1))
    _trip_at("20260915", _NOW)
    assert SB.consecutive_trip_days(tmp_path, now_fn=_now) == 3
    old = datetime(2026, 8, 1, tzinfo=UTC)
    _trip_at("20260801", old)                                          # 窗口外不计
    assert SB.consecutive_trip_days(tmp_path, now_fn=_now) == 3


def test_consecutive_trip_days_no_journal(tmp_path):
    assert SB.consecutive_trip_days(tmp_path, now_fn=_now) == 0
