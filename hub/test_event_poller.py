"""event_poller 单测——声明装载/机械筛/幂等落盘/三态（add-event-sources 2.1，spec: event-intake）。"""
from __future__ import annotations

import types

import event_poller


def _mk_cfg(tmp_path, gh_token="t"):
    return types.SimpleNamespace(data_root=tmp_path, gh_token=gh_token)


def _proj(tmp_path, name="proj-a", events_yaml=None, sources="sources:\n  - name: x\n    root: r/\n"):
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    body = sources if events_yaml is None else sources + events_yaml
    (d / "sources.yaml").write_text(body, encoding="utf-8")
    return d


def _ev(i, typ="IssueCommentEvent", actor="alice", title="T", body="B", url="http://u", created="2026-11-01T01:02:03Z"):
    return {"id": str(i), "type": typ, "actor": actor, "created_at": created,
            "payload": {"title": title, "body": body, "url": url, "action": "created"}}


def test_declarations_parse_and_tolerances(tmp_path):
    _proj(tmp_path, events_yaml="events:\n  - repo: o/r\n    authors: [alice]\n")
    _proj(tmp_path, "proj-b", events_yaml="events: []\n")           # 空声明
    _proj(tmp_path, "proj-c")                                        # 无 events 键
    (tmp_path / "proj-d").mkdir(); (tmp_path / "proj-d" / "sources.yaml").write_text("{{{{bad", encoding="utf-8")  # 坏 yaml
    d = event_poller.load_events_declarations(tmp_path)
    assert list(d) == ["proj-a"]
    assert d["proj-a"][0]["repo"] == "o/r" and d["proj-a"][0]["authors"] == ("alice",)
    assert d["proj-a"][0]["types"] == event_poller.DEFAULT_EVENT_TYPES  # 缺省类型白名单


def test_filter_and_idempotent_write(tmp_path):
    _proj(tmp_path, events_yaml="events:\n  - repo: o/r\n    authors: [alice]\ntypes: [IssueCommentEvent]\n" if False else "events:\n  - repo: o/r\n    authors: [alice]\n")
    cfg = _mk_cfg(tmp_path)
    events = [_ev(1),                                   # 命中
              _ev(2, actor="bob"),                      # 作者不在授权面 → 拒
              _ev(3, typ="PushEvent"),                  # 类型不在白名单 → 拒
              _ev(4, title=None, body=None, url=None)]  # 极简 payload 也命中（机械面）
    n1 = event_poller.poll_events(cfg, client=lambda repo: (events, None))
    assert n1 == 2
    p1 = tmp_path / "proj-a" / "events" / "20261101_1.md"
    txt = p1.read_text(encoding="utf-8")
    assert 'repo: "o/r"' in txt and 'actor: "alice"' in txt and "event_id: \"1\"" in txt
    assert "原文：http://u" in txt and "B" in txt          # 机械拼接正文
    n2 = event_poller.poll_events(cfg, client=lambda repo: (events, None))
    assert n2 == 0 and p1.exists()                        # 文件名幂等去重（重启安全）
    assert not (tmp_path / "proj-a" / "events" / "20261101_2.md").exists()


def test_empty_authors_deny_all_with_warn(tmp_path, capsys):
    _proj(tmp_path, events_yaml="events:\n  - repo: o/r\n    authors: []\n")
    n = event_poller.poll_events(_mk_cfg(tmp_path), client=lambda repo: ([_ev(1)], None))
    assert n == 0 and "authors 为空" in capsys.readouterr().err  # 零误报取向


def test_unknown_and_exception_isolated(tmp_path, capsys):
    _proj(tmp_path, events_yaml="events:\n  - repo: o/r\n    authors: [a]\n")
    cfg = _mk_cfg(tmp_path)
    assert event_poller.poll_events(cfg, client=lambda repo: (None, None)) == 0   # UNKNOWN 跳轮
    assert "UNKNOWN" in capsys.readouterr().err

    def boom(repo):
        raise RuntimeError("gh 抽风")
    assert event_poller.poll_events(cfg, client=boom) == 0                        # 单 repo 异常不连坐
    assert "拉取异常" in capsys.readouterr().err
