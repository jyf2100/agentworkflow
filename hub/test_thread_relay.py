"""thread_relay 单测——窗口/Gate/幂等/水位/回执/隔离（add-thread-subscription 2.1）。"""
from __future__ import annotations

import types


import thread_relay


def _post(pid, uid, msg, ms, ):
    return {"id": pid, "user_id": uid, "message": msg, "create_at": ms}


class FakeMM:
    def __init__(self, posts, members=("u1",), bot="bot"):
        self.posts, self.members, self.bot = posts, members, bot
        self.reads: list[tuple] = []

    def read_channel_posts(self, channel_id, since_ms):
        self.reads.append((channel_id, since_ms))
        return [p for p in self.posts if p["create_at"] > since_ms]

    def bot_user_id(self):
        return self.bot

    def channel_member_ids(self, channel_id):
        return list(self.members)


class FakeDB:
    def __init__(self, bindings, runs):
        self.bindings, self.runs = bindings, runs
        self.wm: dict[str, str] = {}

    def list_channel_bindings(self):
        return self.bindings

    def runs_in_status(self, status):
        return self.runs

    def get_watermark(self, key):
        return self.wm.get(key)

    def set_watermark(self, key, value):
        self.wm[key] = value


class FakeNotifier:
    def __init__(self):
        self.receipts: list[tuple] = []

    def _post_bound_channel(self, channel_id, text, tag):
        self.receipts.append((channel_id, text))


def _run_env(tmp_path, posts, started="2026-11-01T10:00:00+00:00"):
    rd = tmp_path / "runs" / "proj-a" / "20261101_x"
    rd.mkdir(parents=True, exist_ok=True)
    dbm = FakeDB([{"project": "proj-a", "channel_id": "ch1"}],
                 [{"id": 7, "project": "proj-a", "run_dir": str(rd), "started_at": started}])
    return dbm, rd


def test_relay_window_gate_and_write(tmp_path):
    # u1（成员）窗口内 2 条 + u2（非成员）1 条 + bot 1 条 + 窗口外 1 条
    start_ms = thread_relay._iso_ms("2026-11-01T10:00:00+00:00")
    posts = [_post("p1", "u1", "改用方案B", start_ms + 60_000),
             _post("p2", "u1", "先跳过第2条验收", start_ms + 120_000),
             _post("p3", "u2", "外部人", start_ms + 90_000),
             _post("p4", "bot", "[pa-hub] run #7 running", start_ms + 30_000),
             _post("p5", "u1", "run 起点前的旧话", start_ms - 10_000)]
    dbm, rd = _run_env(tmp_path, posts)
    mm, n = FakeMM(posts), FakeNotifier()
    taken = thread_relay.relay_channel_notes(types.SimpleNamespace(), dbm, mm, notifier=n)
    assert taken == 2 and len(n.receipts) == 1          # 恰 u1 两条 + 一条汇总回执
    assert "2 条" in n.receipts[0][1] and "run #7" in n.receipts[0][1]
    files = sorted(p.name for p in (rd / "human_notes").glob("*.md"))
    assert len(files) == 2 and any(f.endswith("_p1.md") for f in files)
    body = (rd / "human_notes" / [f for f in files if f.endswith("_p1.md")][0]).read_text(encoding="utf-8")
    assert "改用方案B" in body and 'message_id: "p1"' in body and "run_id: 7" in body
    assert dbm.wm["thread_relay:ch1"]                     # 水位推进
    # 幂等：同帖再读（水位后无新帖）零新增零回执
    taken2 = thread_relay.relay_channel_notes(types.SimpleNamespace(), dbm, mm, notifier=n)
    assert taken2 == 0 and len(n.receipts) == 1


def test_relay_unknown_and_no_run_advance_watermark_only(tmp_path, capsys):
    dbm, rd = _run_env(tmp_path, [])
    mm_none = FakeMM([])
    mm_none.read_channel_posts = lambda c, s: None
    assert thread_relay.relay_channel_notes(types.SimpleNamespace(), dbm, mm_none) == 0
    assert "UNKNOWN" in capsys.readouterr().err
    # 无在飞 run：窗口外帖只推水位不落盘
    dbm2 = FakeDB([{"project": "proj-a", "channel_id": "ch1"}], [])
    posts = [_post("q1", "u1", "没人跑时的话", 111)]
    mm2 = FakeMM(posts)
    assert thread_relay.relay_channel_notes(types.SimpleNamespace(), dbm2, mm2) == 0
    assert dbm2.wm["thread_relay:ch1"] == "111"
    assert not (tmp_path / "runs").exists() or not list((tmp_path / "runs").rglob("human_notes"))


def test_relay_channel_failure_isolated(tmp_path, capsys):
    dbm, _ = _run_env(tmp_path, [])

    class BoomMM(FakeMM):
        def read_channel_posts(self, c, s):
            raise RuntimeError("MM 抽风")

    assert thread_relay.relay_channel_notes(types.SimpleNamespace(), dbm, BoomMM([])) == 0
    assert "fail-open" in capsys.readouterr().err
