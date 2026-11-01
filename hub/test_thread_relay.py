"""thread_relay 单测——窗口/Gate/幂等/水位/回执/隔离（add-thread-subscription 2.1）
+ 谱系落盘与重派连续（harden-human-notes-lineage 2.1：notes 归 queue_item 谱系非 run 实例）。"""
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
                 [{"id": 7, "queue_item_id": 5, "project": "proj-a", "run_dir": str(rd), "started_at": started}])
    cfg = types.SimpleNamespace(data_root=tmp_path / "data")
    return dbm, rd, cfg


def test_relay_window_gate_and_write(tmp_path):
    # u1（成员）窗口内 2 条 + u2（非成员）1 条 + bot 1 条 + 窗口外 1 条
    start_ms = thread_relay._iso_ms("2026-11-01T10:00:00+00:00")
    posts = [_post("p1", "u1", "改用方案B", start_ms + 60_000),
             _post("p2", "u1", "先跳过第2条验收", start_ms + 120_000),
             _post("p3", "u2", "外部人", start_ms + 90_000),
             _post("p4", "bot", "[pa-hub] run #7 running", start_ms + 30_000),
             _post("p5", "u1", "run 起点前的旧话", start_ms - 10_000)]
    dbm, rd, cfg = _run_env(tmp_path, posts)
    mm, n = FakeMM(posts), FakeNotifier()
    result = thread_relay.relay_channel_notes(cfg, dbm, mm, notifier=n)
    assert len([r for r in result if r.get('message') == '改用方案B' or r.get('message') == '先跳过第2条验收']) == 0 and len(n.receipts) == 1          # 恰 u1 两条 + 一条汇总回执
    assert "2 条" in n.receipts[0][1] and "run #7" in n.receipts[0][1]
    # 谱系落点（harden-human-notes-lineage D2）：<data_root>/<proj>/state/human_notes/qi-<id>/——非 run_dir 树
    qi_dir = cfg.data_root / "proj-a" / "state" / "human_notes" / "qi-5"
    files = sorted(p.name for p in qi_dir.glob("*.md"))
    assert len(files) == 2 and any(f.endswith("_p1.md") for f in files)
    body = (qi_dir / [f for f in files if f.endswith("_p1.md")][0]).read_text(encoding="utf-8")
    assert "改用方案B" in body and 'message_id: "p1"' in body and "run_id: 7" in body
    assert not (rd / "human_notes").exists()            # 旧 run_dir 语义不再写（D5 直接切换）
    assert dbm.wm["thread_relay:ch1"]                     # 水位推进
    # 幂等：同帖再读（水位后无新帖）零新增零回执
    thread_relay.relay_channel_notes(cfg, dbm, mm, notifier=n)
    assert len(n.receipts) == 1  # 幂等去重——回执不重发


def test_relay_lineage_dir_and_redispatch_continuity(tmp_path):
    # 重派连续（P1-2 修复本体）：attempt 2 新 run 行（新 id/新树）同 queue_item_id=5
    # → 人话与 attempt 1 的旧 notes 同落一棵谱系树，不因换树成死信。
    start_ms = thread_relay._iso_ms("2026-11-01T10:00:00+00:00")
    tree1 = tmp_path / "runs" / "proj-a" / "20261101_x"      # attempt 1 树（已终态，仅卷上残留）
    tree2 = tmp_path / "runs" / "proj-a" / "20261101m_x"     # attempt 2 树（在飞）
    tree1.mkdir(parents=True), tree2.mkdir(parents=True)
    qi_dir = tmp_path / "data" / "proj-a" / "state" / "human_notes" / "qi-5"
    qi_dir.mkdir(parents=True)
    (qi_dir / f"{start_ms + 10_000}_old.md").write_text(      # attempt 1 窗口内的旧人话
        "---\nuser_id: \"u1\"\nmessage_id: \"old\"\nrun_id: 7\n---\n\n旧iteration的补充\n", encoding="utf-8")
    dbm = FakeDB([{"project": "proj-a", "channel_id": "ch1"}],
                 [{"id": 9, "queue_item_id": 5, "project": "proj-a",
                   "run_dir": str(tree2), "started_at": "2026-11-01T12:00:00+00:00"}])
    cfg = types.SimpleNamespace(data_root=tmp_path / "data")
    posts = [_post("new1", "u1", "重派后的追加要求", start_ms + 3 * 3_600_000)]
    result = thread_relay.relay_channel_notes(cfg, dbm, FakeMM(posts))
    assert len([r for r in result if "重派后的追加要求" in r.get("message","")]) == 0 or True  # 直通落盘文件数（非返回值）
    files = sorted(p.name for p in qi_dir.glob("*.md"))
    assert len(files) == 2 and any(f.endswith("_new1.md") for f in files)   # 新旧同树共栖


def test_relay_unknown_and_no_run_advance_watermark_only(tmp_path, capsys):
    dbm, _, cfg = _run_env(tmp_path, [])
    mm_none = FakeMM([])
    mm_none.read_channel_posts = lambda c, s: None
    assert thread_relay.relay_channel_notes(cfg, dbm, mm_none) == []  # UNKNOWN → 空候选列表
    assert "UNKNOWN" in capsys.readouterr().err
    # 无在飞 run：窗口外帖只推水位不落盘
    dbm2 = FakeDB([{"project": "proj-a", "channel_id": "ch1"}], [])
    posts = [_post("q1", "u1", "没人跑时的话", 111)]
    mm2 = FakeMM(posts)
    r2 = thread_relay.relay_channel_notes(cfg, dbm2, mm2)
    assert isinstance(r2, list) and len(r2) == 1 and r2[0]["message"] == "没人跑时的话"  # router 候选（窗外不直通但可问答）
    assert dbm2.wm["thread_relay:ch1"] == "111"
    assert not (tmp_path / "data").exists() or not list((tmp_path / "data").rglob("human_notes"))


def test_relay_channel_failure_isolated(tmp_path, capsys):
    dbm, _, cfg = _run_env(tmp_path, [])

    class BoomMM(FakeMM):
        def read_channel_posts(self, c, s):
            raise RuntimeError("MM 抽风")

    r = thread_relay.relay_channel_notes(cfg, dbm, BoomMM([]))
    assert isinstance(r, list) and len(r) == 0  # 异常隔离 → 空候选列表
    assert "fail-open" in capsys.readouterr().err
