"""router_relay 单测——预筛/注入块/预算闸/互斥/失败静默（add-router-persona 2.1）。"""
from __future__ import annotations

import types
from pathlib import Path


import router_relay as RR


class FakeDB:
    def __init__(self):
        self.snap = {"pending": 1, "pending_oldest_min": 5, "running": 2}
        self.runs = [{"id": 110, "status": "blocked_test_gate", "project": "cc-web-control",
                      "run_dir": "/data/pa/cc/state/runs/cc/20261101_x", "started_at": "2026-11-01T05:19:13+00:00"}]

    def queue_snapshot(self):
        return self.snap

    def list_all_runs(self):
        return self.runs


def _cfg(tmp_path):
    return types.SimpleNamespace(data_root=tmp_path, router_pattern=None)


def test_prescreen_three_forms():
    assert RR.prescreen("#110 为什么阻断")
    assert RR.prescreen("sp-a1b2 是什么提案")
    assert RR.prescreen("今天跑了多少单")
    assert not RR.prescreen("好的收到")
    assert not RR.prescreen("")


def test_context_block_value_not_in_face():
    dbm = FakeDB()
    blk = RR.build_context_block(_cfg(Path("/tmp")), dbm, run_ref="#110")
    assert "run #110" in blk and "blocked_test_gate" in blk
    assert "token" not in blk.lower() or "TOKEN" not in blk  # 凭据不入块
    assert "pending 1" in blk


def test_budget_gate():
    rate = {"ch1": 6}  # 已满
    n = RR.relay_router_answers(_cfg(Path("/tmp")), FakeDB(), mm=object(), notifier=None,
                                candidates=[{"channel_id": "ch1", "user_id": "u", "message": "#110 为什么"}],
                                rate_counter=rate)
    assert n == 0  # 预算闸静默


def test_flag_off_zero_behavior(monkeypatch):
    monkeypatch.delenv("PA_ROUTER_PERSONA", raising=False)
    n = RR.relay_router_answers(_cfg(Path("/tmp")), FakeDB(), mm=object(), notifier=object(),
                                candidates=[{"channel_id": "c", "user_id": "u", "message": "#110 为什么"}])
    assert n == 0  # flag 默认关 = byte-identical


def test_answer_failure_silent(monkeypatch, capsys):
    def boom(cmd, **k):
        raise __import__("subprocess").TimeoutExpired("claude", 60)
    monkeypatch.setattr(RR.subprocess, "run", boom)
    assert RR.answer_question(_cfg(Path("/tmp")), FakeDB(), "x") is None
    assert "静默" in capsys.readouterr().err or "超时" in capsys.readouterr().err
