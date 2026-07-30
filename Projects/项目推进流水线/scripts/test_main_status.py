#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_main_status.py — single-flight-auto-merge task 4.6：main 瞬态红契约 + 可查询验证状态单测。

design Risks F8（main 瞬态红）+ D10（wall-clock）：MAX_MAIN_RED_WINDOW_SECONDS 红窗上界 + 可查询
main_post_merge_status（per-owner_repo journal，不受 flag gating）。post-merge 判决后 record，下游据此判
main 是否已过 post-merge 验证。

跑：python3 -m pytest scripts/test_main_status.py -q
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import main_status as MS  # noqa: E402

UTC = timezone.utc
OWNER = "test/repo"
OWNER2 = "other/repo"


def _iso(dt): return dt.isoformat()


def _stamp(): return _iso(datetime(2026, 7, 30, 0, 0, 0, tzinfo=UTC))


# ─── 契约：MAX_MAIN_RED_WINDOW_SECONDS 红窗上界（= post-merge timeout，D10）─────────
def test_max_red_window_constant_is_post_merge_timeout():
    """红窗上界 = post-merge test timeout（1800s，D10）；push 后到 verdict 的最长窗口。"""
    assert MS.MAX_MAIN_RED_WINDOW_SECONDS == 1800


# ─── 可查询状态：record + main_post_merge_status ─────────────────────────────
def test_no_record_returns_none(tmp_path):
    assert MS.main_post_merge_status(tmp_path, OWNER) is None


def test_record_pass_status(tmp_path):
    MS.record_main_verified(tmp_path, OWNER, main_ref="main", merge_commit="abc123", verdict="PASS",
                            prd_id="prd_x", stamp_fn=_stamp)
    st = MS.main_post_merge_status(tmp_path, OWNER)
    assert st is not None
    assert st.verdict == "PASS"
    assert st.merge_commit == "abc123"
    assert st.main_ref == "main"


def test_record_fail_status(tmp_path):
    """FAIL → 已 revert 回绿；status 反映最近判决 FAIL（下游知 main 曾红）。"""
    MS.record_main_verified(tmp_path, OWNER, main_ref="main", merge_commit="abc", verdict="FAIL",
                            prd_id="prd_x", stamp_fn=_stamp)
    assert MS.main_post_merge_status(tmp_path, OWNER).verdict == "FAIL"


def test_record_unknown_status(tmp_path):
    MS.record_main_verified(tmp_path, OWNER, main_ref="main", merge_commit="abc", verdict="UNKNOWN",
                            prd_id="prd_x", stamp_fn=_stamp)
    assert MS.main_post_merge_status(tmp_path, OWNER).verdict == "UNKNOWN"


# ─── 多次判决：返最近一条（recent[-1]）────────────────────────────────────────
def test_multiple_records_returns_most_recent(tmp_path):
    """跨 cron 多次 auto-merge → 返最近一次 post-merge 验证态（下游查当前 main 状态）。"""
    MS.record_main_verified(tmp_path, OWNER, main_ref="main", merge_commit="c1", verdict="PASS",
                            prd_id="prd_1", stamp_fn=lambda: _iso(datetime(2026, 7, 28, tzinfo=UTC)))
    MS.record_main_verified(tmp_path, OWNER, main_ref="main", merge_commit="c2", verdict="FAIL",
                            prd_id="prd_2", stamp_fn=lambda: _iso(datetime(2026, 7, 29, tzinfo=UTC)))
    MS.record_main_verified(tmp_path, OWNER, main_ref="main", merge_commit="c3", verdict="PASS",
                            prd_id="prd_3", stamp_fn=lambda: _iso(datetime(2026, 7, 30, tzinfo=UTC)))
    st = MS.main_post_merge_status(tmp_path, OWNER)
    assert st.merge_commit == "c3"                      # 最近一次
    assert st.verdict == "PASS"


def test_per_owner_repo_isolated(tmp_path):
    """不同 owner_repo → 不同 main_status journal → 隔离。"""
    MS.record_main_verified(tmp_path, OWNER, main_ref="main", merge_commit="a", verdict="PASS",
                            prd_id="p", stamp_fn=_stamp)
    MS.record_main_verified(tmp_path, OWNER2, main_ref="main", merge_commit="b", verdict="FAIL",
                            prd_id="p", stamp_fn=_stamp)
    assert MS.main_post_merge_status(tmp_path, OWNER).merge_commit == "a"
    assert MS.main_post_merge_status(tmp_path, OWNER2).merge_commit == "b"


# ─── durable + fail-open ─────────────────────────────────────────────────────
def test_record_survives_independent_read(tmp_path):
    """跨「进程」（独立 read）可查——crash 后下游仍能查 main 验证态。"""
    MS.record_main_verified(tmp_path, OWNER, main_ref="main", merge_commit="abc", verdict="PASS",
                            prd_id="prd_x", stamp_fn=_stamp)
    assert MS.main_post_merge_status(tmp_path, OWNER) is not None   # 独立从磁盘读


def test_corrupted_journal_failopen_none(tmp_path):
    jp = MS.main_status_journal_path(tmp_path, OWNER)
    jp.parent.mkdir(parents=True, exist_ok=True)
    jp.write_text('{"bad":"middle"}\n', encoding="utf-8")     # 损坏 → JournalCorruptionError
    assert MS.main_post_merge_status(tmp_path, OWNER) is None   # fail-open：读不到 → None（不当代绿）
