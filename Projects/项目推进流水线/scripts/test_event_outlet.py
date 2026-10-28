#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_event_outlet.py — 语义事件出口单测（add-hub-slice-4b tasks 2.4，spec: pa-event-outlet）。

覆盖三层：
    - writer 契约（event_outlet.append_event）：路径拼法（与 journal 伴生同拼法）、
      行字段（v/event/seq/stamp/project/facts）、seq 单调、fail-open（fsync 失败不 raise）；
    - critic 挂点集成（stage_critic，仿 test_critic_tourniquet 的 _critic_one 桩）：
      verdict 落定即产事件行；复用门（gate 已在）不重复发；
    - pr_opened 挂点集成（stage_dispatch，经 worker hook 注入——langgraph task 3.9 既有
      接缝零新增 mock 面）：rec 带 pr_url 收尾即发（repo 从 url 机械析出）、无 pr_url 不发；
    - report 双写两态（PA_REPORT_DIR set/unset，仿 test_report 的 _setup_report）。

取舍记录：verify 挂点（run_daily verify 循环内）与 critic 挂点同构（同函数同参数形态
append_event 调用），驱动它须跑 _run_one 全链（dev SDK 子进程）成本不成比例——由
writer 契约单测 + critic 集成覆盖其调用形态，不单独集成测。AAA 结构，零 SDK。
跑：python3 -m pytest scripts/test_event_outlet.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent))
import event_outlet as EO  # noqa: E402
import run_daily  # noqa: E402

STAMP = "20261022"


# ── writer 契约 ───────────────────────────────────────────────────


def test_append_event_path_and_contract_fields(tmp_path):
    # Act：同 (stamp, slug) 追加两条
    EO.append_event(tmp_path, "p1", STAMP, "hand-a1b2c3d4", "critic_verdict",
                    verdict="pass", summary="有据可执行")
    EO.append_event(tmp_path, "p1", STAMP, "hand-a1b2c3d4", "pr_opened",
                    pr_url="https://github.com/o/r/pull/1", repo="o/r")
    # Assert：路径与 journal 伴生同拼法（runs/<proj>/<stamp>_<slug>.events.jsonl）
    f = tmp_path / "runs" / "p1" / f"{STAMP}_hand-a1b2c3d4.events.jsonl"
    assert f.is_file()
    lines = [json.loads(ln) for ln in f.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 2
    # Assert：契约字段逐行齐备 + facts 机械透传
    assert lines[0]["v"] == 1 and lines[0]["event"] == "critic_verdict"
    assert lines[0]["seq"] == 0 and lines[0]["stamp"] == STAMP and lines[0]["project"] == "p1"
    assert lines[0]["verdict"] == "pass" and lines[0]["summary"] == "有据可执行"
    assert lines[1]["seq"] == 1 and lines[1]["event"] == "pr_opened"  # seq run 内单调
    assert lines[1]["pr_url"] == "https://github.com/o/r/pull/1" and lines[1]["repo"] == "o/r"


def test_append_event_fail_open_on_fsync_failure(tmp_path, monkeypatch, capsys):
    """fail-open：出口写失败 → stderr 告警不 raise（观察面绝不炸执行面挂点）。"""
    def _boom(fd):
        raise OSError("disk full")
    monkeypatch.setattr(EO.os, "fsync", _boom)
    EO.append_event(tmp_path, "p1", STAMP, "s", "verify_verdict", verdict="pass")  # 不 raise
    assert "fail-open" in capsys.readouterr().err


# ── critic 挂点集成 ───────────────────────────────────────────────


def _args(**over):
    base = dict(force=True)
    base.update(over); return SimpleNamespace(**base)


def test_stage_critic_emits_verdict_events(tmp_path, monkeypatch):
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path)
    manifest = {"prds": [
        {"project": "p1", "path": f"{STAMP}_a.md", "source_path": "s"},
        {"project": "p2", "path": f"{STAMP}_b.md", "source_path": "s"},
    ]}
    verdicts = iter(["pass", "drop"])
    proj_of = {f"{STAMP}_a.md": "p1", f"{STAMP}_b.md": "p2"}
    monkeypatch.setattr(run_daily, "_critic_one",
                        lambda path, src, prof: {"verdict": next(verdicts), "prd_path": path,
                                                 "project": proj_of[path], "summary": "s1"})
    run_daily.stage_critic(_args(), manifest, {}, STAMP)
    # Assert：两 project 各自事件文件（slug = PRD stem），verdict 机械转发。
    # fix-event-outlet-verify-parity 1.3 起 pass 项随后编套件再产 acceptance_suite 行
    # （PRD 文件不在 → absent no_criteria）——pass 项双事件、drop 项单行。
    e1 = tmp_path / "runs" / "p1" / f"{STAMP}_{STAMP}_a.events.jsonl"
    e2 = tmp_path / "runs" / "p2" / f"{STAMP}_{STAMP}_b.events.jsonl"
    l1 = [json.loads(ln) for ln in e1.read_text(encoding="utf-8").splitlines()]
    l2 = [json.loads(ln) for ln in e2.read_text(encoding="utf-8").splitlines()]
    assert [ln for ln in l1 if ln["event"] == "critic_verdict"][0]["verdict"] == "pass"
    assert [ln for ln in l1 if ln["event"] == "acceptance_suite"][0]["status"] == "absent"
    assert l2[0]["verdict"] == "drop" and len(l2) == 1   # drop 不编套件 → 无 acceptance 行


def test_stage_critic_reuse_gate_does_not_reemit(tmp_path, monkeypatch):
    """复用门（gate_file 已在、force=False）= 事件已发过，幂等不再追加。"""
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path)
    manifest = {"prds": [{"project": "p1", "path": f"{STAMP}_a.md", "source_path": "s"}]}
    monkeypatch.setattr(run_daily, "_critic_one",
                        lambda path, src, prof: {"verdict": "pass", "prd_path": path,
                                                 "project": "p1", "summary": "s"})
    run_daily.stage_critic(_args(), manifest, {}, STAMP)
    ev = tmp_path / "runs" / "p1" / f"{STAMP}_{STAMP}_a.events.jsonl"
    before = ev.read_text(encoding="utf-8")
    # Act：二跑走复用门（force=False）
    run_daily.stage_critic(_args(force=False), manifest, {}, STAMP)
    assert ev.read_text(encoding="utf-8") == before  # 无重复事件行


# ── pr_opened 挂点集成（worker hook 注入）────────────────────────


def test_stage_dispatch_emits_pr_opened(tmp_path, monkeypatch):
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path)
    gate = [{"project": "p1", "prd_path": f"{STAMP}_a.md", "verdict": "pass"}]

    def _worker(e, prof, stamp, args):
        return {"project": e["project"], "prd_path": e["prd_path"], "slug": f"{STAMP}_a",
                "status": "pr_open", "pr_url": "https://github.com/o/r/pull/7"}

    run_daily.stage_dispatch(_args(max_concurrent=1, dispatch_limit=None), gate,
                             {"p1": {"repo": "https://github.com/o/r"}}, STAMP, worker=_worker)
    ev = tmp_path / "runs" / "p1" / f"{STAMP}_{STAMP}_a.events.jsonl"
    line = json.loads(ev.read_text(encoding="utf-8"))
    assert line["event"] == "pr_opened" and line["pr_url"].endswith("/pull/7")
    assert line["repo"] == "o/r"  # 目标仓从 url 机械析出


def test_stage_dispatch_no_pr_no_event(tmp_path, monkeypatch):
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path)
    gate = [{"project": "p1", "prd_path": f"{STAMP}_a.md", "verdict": "pass"}]

    def _worker(e, prof, stamp, args):  # blocked：无 PR
        return {"project": e["project"], "prd_path": e["prd_path"], "slug": f"{STAMP}_a",
                "status": "blocked_external_state", "pr_url": None}

    run_daily.stage_dispatch(_args(max_concurrent=1, dispatch_limit=None), gate,
                             {"p1": {"repo": "https://github.com/o/r"}}, STAMP, worker=_worker)
    assert not (tmp_path / "runs" / "p1" / f"{STAMP}_{STAMP}_a.events.jsonl").exists()


# ── report 双写两态（T2a）────────────────────────────────────────


def _setup_report_env(tmp_path, monkeypatch):
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(run_daily, "REPORT_DIR", tmp_path / "report")
    monkeypatch.setattr(run_daily, "DAILY_DIR", tmp_path / "daily")
    st = tmp_path / "state"; st.mkdir(parents=True, exist_ok=True)
    (st / f"candidates_{STAMP}.json").write_text(
        json.dumps({"candidates": [], "today_new_count": 0, "stats": {}}, ensure_ascii=False), encoding="utf-8")
    (st / f"prd_gate_{STAMP}.json").write_text("[]", encoding="utf-8")
    (st / f"dispatch_{STAMP}.json").write_text("[]", encoding="utf-8")


def test_report_double_write_when_env_set(tmp_path, monkeypatch):
    _setup_report_env(tmp_path, monkeypatch)
    monkeypatch.setenv("PA_REPORT_DIR", str(tmp_path / "state" / "reports"))
    rp = run_daily.stage_report(SimpleNamespace(dry_run=True, no_notify=False), {}, STAMP)
    extra = tmp_path / "state" / "reports" / f"项目推进报告_{STAMP}.md"
    # Assert：原落点不变 + 出口目录同容一份
    assert rp.is_file() and extra.is_file()
    assert rp.read_text(encoding="utf-8") == extra.read_text(encoding="utf-8")


def test_report_single_write_by_default(tmp_path, monkeypatch):
    _setup_report_env(tmp_path, monkeypatch)
    monkeypatch.delenv("PA_REPORT_DIR", raising=False)
    rp = run_daily.stage_report(SimpleNamespace(dry_run=True, no_notify=False), {}, STAMP)
    assert rp.is_file()
    assert not (tmp_path / "state" / "reports").exists()  # 缺省零变化：出口目录根本不建
