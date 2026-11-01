"""daily_digest 单测——三面聚合文本 + 双通道投递（add-hub-daily-digest 2.1）。"""
from __future__ import annotations

import types
from datetime import datetime, timedelta, timezone

import daily_digest


def _now_iso(hours_ago: float = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat(timespec="seconds")


class FakeDB:
    def __init__(self, *, pending=0, oldest_min=0, running=0, runs=(), rows=()):
        self.snap = {"pending": pending, "pending_oldest_min": oldest_min, "running": running}
        self.runs, self.rows = runs, rows

    def queue_snapshot(self):
        return self.snap

    def list_all_runs(self):
        return self.runs

    def list_runners(self):
        return self.rows


class FakeNotifier:
    def __init__(self):
        self.sent: list[str] = []

    def notify_admin(self, text):
        self.sent.append(text)


def test_digest_three_sections_content():
    dbm = FakeDB(
        pending=2, oldest_min=34, running=1,
        runs=[{"status": "succeeded", "ended_at": _now_iso(1)},
              {"status": "failed", "ended_at": _now_iso(2)},
              {"status": "succeeded", "ended_at": _now_iso(48)}],   # 窗口外不计
        rows=[{"name": "gpu-a", "probe_ok": 1, "probe_json": None, "load": 1, "capacity": 4,
               "enabled": 1, "image_sync_json": '{"ok": true, "tag": "t:1"}'},
              {"name": "gpu-b", "probe_ok": 0, "probe_json": '{"daemon": "ok", "image": "镜像缺失: t:1", "mount": "ok"}',
               "load": 0, "capacity": 1, "enabled": 1, "image_sync_json": None},
              {"name": "gpu-c", "probe_ok": None, "probe_json": None, "load": 0, "capacity": 1,
               "enabled": 0, "image_sync_json": None}])
    text = daily_digest.build_digest(types.SimpleNamespace(), dbm, {("drift", "R1", "K")})
    assert "pending 2（最老等待 34min）｜running 1" in text
    assert "succeeded 1 / failed 1" in text                       # 24h 窗口过滤
    assert "gpu-a 🟢｜忙闲 1/4｜上次分发 ✅｜启用" in text
    assert "gpu-b 🔴（坏项：image）" in text                       # 坏项只取名不含报文全文
    assert "镜像缺失" not in text                                  # 报文全文不入面
    assert "gpu-c 未探" in text and "禁用" in text
    assert "漂移：未消解 1 项" in text


def test_digest_drift_none_and_all_green_and_empty_pool():
    dbm = FakeDB(rows=[])
    t1 = daily_digest.build_digest(types.SimpleNamespace(), dbm, None)
    assert "看门狗未运行" in t1 and "空池——本机形态" in t1
    t2 = daily_digest.build_digest(types.SimpleNamespace(), dbm, set())
    assert "未消解 0 项（四规则全绿）" in t2


def test_digest_section_failure_isolated():
    class BoomDB(FakeDB):
        def queue_snapshot(self):
            raise RuntimeError("sqlite 抽风（模拟）")
    text = daily_digest.build_digest(types.SimpleNamespace(), BoomDB(), set())
    assert "队列：（读取失败" in text and "漂移：未消解 0 项" in text  # 单节挂不连坐


def test_emit_dual_channel_and_degrade(capsys):
    n = FakeNotifier()
    daily_digest.emit_digest(types.SimpleNamespace(), FakeDB(), notifier=n, drift_seen=set())
    assert len(n.sent) == 1 and "[pa-hub 日报]" in n.sent[0]
    assert "[pa-hub 日报]" in capsys.readouterr().err
    daily_digest.emit_digest(types.SimpleNamespace(), FakeDB(), notifier=None, drift_seen=set())
    assert capsys.readouterr().err.count("[pa-hub 日报]") == 1       # 未配通道降级 stderr
