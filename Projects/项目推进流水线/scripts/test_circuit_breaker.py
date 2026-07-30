#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_circuit_breaker.py — single-flight-auto-merge task 4.4：revert 循环熔断单测。

D11 / spec「Revert loop circuit breaker」：同幂等键 PRD 被 post_merge_red_reverted 后，cooldown 窗口内
（默认 7 天）re-admission 禁 auto-merge，强制 triage——防「branch 绿但 main 红」的 PRD 夜夜复发无限循环。

熔断 = 额外保护层（非 fail-safe 核心）：读不到/损坏 → fail-open（False，让正常 triage/revert 流程走），
对齐「block 需正向匹配证据」。跨 cron 稳定键 = circuit_key（slug-based：``ids.prd_id(stable_slug, content_hash)``，
两因子都不含 cron stamp；task 4.4 fix——旧 path-based prd_id 含 stamp 跨 cron 变，熔断跨 cron 失效）。

跑：python3 -m pytest scripts/test_circuit_breaker.py -q
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import circuit_breaker as CB  # noqa: E402

UTC = timezone.utc
OWNER = "test/repo"
PRD_A = "prd_aaaa1111"
PRD_B = "prd_bbbb2222"


def _now(): return datetime(2026, 7, 30, 0, 0, 0, tzinfo=UTC)


def _iso(dt): return dt.isoformat()


# ─── 基线：无记录 / 不匹配 → 不在冷却 ──────────────────────────────────────
def test_no_record_not_in_cooldown(tmp_path):
    assert CB.is_in_cooldown(tmp_path, OWNER, PRD_A, now_fn=_now) is False


def test_revert_recorded_for_different_prd_not_in_cooldown(tmp_path):
    CB.record_revert(tmp_path, OWNER, PRD_A, stamp_fn=lambda: _iso(_now()))
    assert CB.is_in_cooldown(tmp_path, OWNER, PRD_B, now_fn=_now) is False


def test_revert_recorded_for_different_owner_repo_not_in_cooldown(tmp_path):
    CB.record_revert(tmp_path, OWNER, PRD_A, stamp_fn=lambda: _iso(_now()))
    # 不同 owner_repo → 不同 cooldown journal 文件 → 不命中
    assert CB.is_in_cooldown(tmp_path, "other/repo", PRD_A, now_fn=_now) is False


# ─── spec scenario：窗口内命中 / 窗口过后放行 ───────────────────────────────
def test_revert_recorded_inside_window_in_cooldown(tmp_path):
    """spec「Reverted PRD re-admitted inside cooldown」→ block（True）。"""
    now = _now()
    CB.record_revert(tmp_path, OWNER, PRD_A, stamp_fn=lambda: _iso(now - timedelta(hours=1)))
    assert CB.is_in_cooldown(tmp_path, OWNER, PRD_A, now_fn=lambda: now) is True


def test_revert_recorded_after_window_elapsed_not_in_cooldown(tmp_path):
    """spec「Reverted PRD re-admitted after cooldown」→ 放行（False）。默认 7d 窗口。"""
    now = _now()
    reverted_at = now - timedelta(days=7, seconds=1)   # 刚过 7 天窗口
    CB.record_revert(tmp_path, OWNER, PRD_A, stamp_fn=lambda: _iso(reverted_at))
    assert CB.is_in_cooldown(tmp_path, OWNER, PRD_A, now_fn=lambda: now) is False


def test_revert_at_exact_window_boundary_in_cooldown(tmp_path):
    """窗口边界（恰好 == window）→ 仍在冷却（``<=`` 含边界）。"""
    now = _now()
    reverted_at = now - timedelta(seconds=CB.DEFAULT_COOLDOWN_WINDOW)
    CB.record_revert(tmp_path, OWNER, PRD_A, stamp_fn=lambda: _iso(reverted_at))
    assert CB.is_in_cooldown(tmp_path, OWNER, PRD_A, now_fn=lambda: now) is True


def test_custom_window_seconds(tmp_path):
    now = _now()
    CB.record_revert(tmp_path, OWNER, PRD_A, stamp_fn=lambda: _iso(now - timedelta(hours=2)))
    assert CB.is_in_cooldown(tmp_path, OWNER, PRD_A, now_fn=lambda: now) is True          # 默认 7d → 命中
    assert CB.is_in_cooldown(tmp_path, OWNER, PRD_A, now_fn=lambda: now,
                             window_seconds=3600) is False                                # 1h 自定义 → 过期


# ─── 多条记录：最早过期、最近在窗口 → 命中 ─────────────────────────────────
def test_multiple_records_latest_in_window_in_cooldown(tmp_path):
    now = _now()
    CB.record_revert(tmp_path, OWNER, PRD_A, stamp_fn=lambda: _iso(now - timedelta(days=10)))  # 过期
    CB.record_revert(tmp_path, OWNER, PRD_A, stamp_fn=lambda: _iso(now - timedelta(days=1)))   # 窗口内
    assert CB.is_in_cooldown(tmp_path, OWNER, PRD_A, now_fn=lambda: now) is True


def test_multiple_records_all_expired_not_in_cooldown(tmp_path):
    now = _now()
    CB.record_revert(tmp_path, OWNER, PRD_A, stamp_fn=lambda: _iso(now - timedelta(days=10)))
    CB.record_revert(tmp_path, OWNER, PRD_A, stamp_fn=lambda: _iso(now - timedelta(days=9)))
    assert CB.is_in_cooldown(tmp_path, OWNER, PRD_A, now_fn=lambda: now) is False


# ─── fail-open：journal 损坏 → False（额外保护层无正向证据不 block）──────────
def test_corrupted_journal_failopen_not_in_cooldown(tmp_path):
    jp = CB.cooldown_journal_path(tmp_path, OWNER)
    jp.parent.mkdir(parents=True, exist_ok=True)
    jp.write_text('{"bad":"middle"}\n', encoding="utf-8")   # 损坏 → JournalCorruptionError
    assert CB.is_in_cooldown(tmp_path, OWNER, PRD_A, now_fn=_now) is False


# ─── record_revert 持久化：写后跨「进程」（新 is_in_cooldown 调用）仍命中 ───
def test_record_revert_persists_across_reads(tmp_path):
    """record_revert 落盘后，独立的 is_in_cooldown 调用（模拟下一轮 cron 新进程）能读到。"""
    now = _now()
    CB.record_revert(tmp_path, OWNER, PRD_A, stamp_fn=lambda: _iso(now))
    # 不复用内存，重新从磁盘读（跨 cron 进程语义）
    assert CB.is_in_cooldown(tmp_path, OWNER, PRD_A, now_fn=lambda: now) is True
    assert CB.cooldown_journal_path(tmp_path, OWNER).exists()


# ─── task 4.4 fix：circuit_key 跨 cron 稳定（slug-based，防夜夜复发）──────────────────
def test_circuit_key_stable_across_cron_stamps_endtoend(tmp_path):
    """canary 判据 c（2026-07-30）端到端回归：同 PRD 跨 cron stamp → circuit_key 同 → record/is_in_cooldown 跨 stamp 命中。

    旧 path-based prd_id 含 stamp（``{stamp}_{slug}.md``）跨 cron 变 → is_in_cooldown 跨 cron 不命中 →
    "branch 绿 main 红 PRD 夜夜复发"防不住（本模块设计目标）。circuit_key = prd_id(stable_slug, digest)
    两因子不含 stamp → 跨 cron 稳定 → record day1 后 day2 re-admission 命中。
    """
    import ids as loop_ids
    digest = "sha256:deadbeef"
    # 跨两天 cron：prd_path 变（stamp），但 stable_slug + 内容同 → circuit_key 同
    key_day1 = loop_ids.prd_id("feature-x", digest)
    key_day2 = loop_ids.prd_id("feature-x", digest)
    assert key_day1 == key_day2
    # 对照：旧 path-based prd_id 跨 stamp 变（已弃用做熔断键）
    assert loop_ids.prd_id("prd/cc/20260730-0317_feature-x.md", digest) != \
        loop_ids.prd_id("prd/cc/20260731-0317_feature-x.md", digest)
    # 闭环：day1 record（post-merge 红 revert）→ day2 re-admission 命中（跨 cron 熔断生效）
    CB.record_revert(tmp_path, OWNER, key_day1, stamp_fn=lambda: _iso(_now()))
    assert CB.is_in_cooldown(tmp_path, OWNER, key_day2, now_fn=_now) is True
