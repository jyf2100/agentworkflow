#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_critical_alert.py — single-flight-auto-merge task 4.5：CRITICAL 告警 durable 化单测。

design Risks F5「告警非 durable」：halt 时落独立 alerts journal（crash 不丢，**不受 journal_shadow flag gating**）。
raise_alert/acknowledge_alert/pending_alerts（未确认语义）。halt 安全已由 slot_halted 保证；告警 durable 是可观测
+ 通知维度（report 读 pending → SMTP；ack↔resume_slot 联动留 follow-up）。

跑：python3 -m pytest scripts/test_critical_alert.py -q
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import critical_alert as CA  # noqa: E402

UTC = timezone.utc
OWNER = "test/repo"
OWNER2 = "other/repo"
PRD_A = "prd_aaaa1111"
PRD_B = "prd_bbbb2222"


def _iso(dt): return dt.isoformat()


def _stamp(): return _iso(datetime(2026, 7, 30, 0, 0, 0, tzinfo=UTC))


# ─── raise_alert：durable 落盘（不受 flag gating，独立 journal）──────────────
def test_raise_alert_persists_to_alerts_journal(tmp_path):
    aid = CA.raise_alert(tmp_path, OWNER, PRD_A, reason="post_merge_unknown", stamp_fn=_stamp)
    assert aid.startswith("alert-")                          # 返回告警 id（ack 锚点）
    assert CA.alerts_journal_path(tmp_path, OWNER).exists()  # 独立 alerts journal 落盘
    pending = CA.pending_alerts(tmp_path, OWNER)
    assert len(pending) == 1
    assert pending[0].prd_id == PRD_A
    assert pending[0].payload["reason"] == "post_merge_unknown"
    assert pending[0].payload["severity"] == "CRITICAL"


def test_raise_alert_custom_severity(tmp_path):
    CA.raise_alert(tmp_path, OWNER, PRD_A, reason="x", stamp_fn=_stamp, severity="WARNING")
    assert CA.pending_alerts(tmp_path, OWNER)[0].payload["severity"] == "WARNING"


def test_raise_alert_per_owner_repo_isolated(tmp_path):
    """不同 owner_repo → 不同 alerts journal → 隔离（防跨仓告警混淆）。"""
    CA.raise_alert(tmp_path, OWNER, PRD_A, reason="x", stamp_fn=_stamp)
    CA.raise_alert(tmp_path, OWNER2, PRD_B, reason="y", stamp_fn=_stamp)
    assert len(CA.pending_alerts(tmp_path, OWNER)) == 1
    assert len(CA.pending_alerts(tmp_path, OWNER2)) == 1
    assert CA.pending_alerts(tmp_path, OWNER)[0].prd_id == PRD_A
    assert CA.pending_alerts(tmp_path, OWNER2)[0].prd_id == PRD_B


# ─── pending_alerts：未确认语义 ─────────────────────────────────────────────
def test_pending_alerts_empty_when_no_alerts(tmp_path):
    assert CA.pending_alerts(tmp_path, OWNER) == []


def test_acknowledge_alert_removes_from_pending(tmp_path):
    """acknowledge_alert → pending 不含该告警（未确认语义）。"""
    aid = CA.raise_alert(tmp_path, OWNER, PRD_A, reason="post_merge_revert_unknown", stamp_fn=_stamp)
    assert len(CA.pending_alerts(tmp_path, OWNER)) == 1
    CA.acknowledge_alert(tmp_path, OWNER, aid, stamp_fn=_stamp)
    assert CA.pending_alerts(tmp_path, OWNER) == []          # 已 ack → 不在 pending


def test_ack_does_not_touch_other_alerts(tmp_path):
    """ack 一条不影响其他 pending（按 alert_id 精确扣除）。"""
    aid1 = CA.raise_alert(tmp_path, OWNER, PRD_A, reason="r1", stamp_fn=_stamp)
    CA.raise_alert(tmp_path, OWNER, PRD_B, reason="r2", stamp_fn=_stamp)
    CA.acknowledge_alert(tmp_path, OWNER, aid1, stamp_fn=_stamp)
    pending = CA.pending_alerts(tmp_path, OWNER)
    assert len(pending) == 1
    assert pending[0].prd_id == PRD_B                        # 只剩未 ack 的


def test_multiple_alerts_all_pending_until_acked(tmp_path):
    # 多告警来自不同 PRD 的 halt（真实场景；同 prd 同刻 raise 视为幂等同一告警——halt 后 slot HALTED 下轮 blocked）
    prds = [f"prd_multi_{i}" for i in range(3)]
    aids = [CA.raise_alert(tmp_path, OWNER, prds[i], reason=f"r{i}", stamp_fn=_stamp) for i in range(3)]
    assert len(CA.pending_alerts(tmp_path, OWNER)) == 3
    for aid in aids[:2]:
        CA.acknowledge_alert(tmp_path, OWNER, aid, stamp_fn=_stamp)
    assert len(CA.pending_alerts(tmp_path, OWNER)) == 1      # 2 ack 后剩 1


# ─── durable：跨「进程」（独立读）仍可查（crash 后 report/恢复器能读出）──────
def test_raise_alert_survives_independent_read(tmp_path):
    """模拟 cron crash 后新进程：raise_alert 落盘 → 独立 pending_alerts 调用能读出（durable）。"""
    aid = CA.raise_alert(tmp_path, OWNER, PRD_A, reason="post_merge_unknown", stamp_fn=_stamp)
    # 不复用内存，新进程从磁盘读（跨 cron 进程语义）
    pending = CA.pending_alerts(tmp_path, OWNER)
    assert len(pending) == 1
    assert pending[0].event_id == aid


# ─── fail-open：journal 损坏 → 空（不阻塞 report；halt 安全已由 slot 保证）────
def test_corrupted_journal_failopen_empty_pending(tmp_path):
    jp = CA.alerts_journal_path(tmp_path, OWNER)
    jp.parent.mkdir(parents=True, exist_ok=True)
    jp.write_text('{"bad":"middle"}\n', encoding="utf-8")     # 损坏 → JournalCorruptionError
    assert CA.pending_alerts(tmp_path, OWNER) == []          # fail-open：读不到 → 空
