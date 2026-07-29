#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_dispatch_skip_projects.py — DISPATCH_SKIP_PROJECTS 临时降噪单测（#1105）。

验证 stage_dispatch 的 env 驱动跳过：
    - 命中项目 → 落 status=skip 记录、不调 _run_one（不触发 #1105 stream-closed）
    - 未命中项目 → 正常 _run_one 投递
    - env 空集 = 精确 no-op（不跳任何）
    - 过闸 PRD 全被跳过 → 直接 return skip_records、不进并行投递

stub _run_one 拦截真实 SDK dev loop（线程池入口）；AAA 结构。零 SDK（run_daily 顶部 import 不触 sdk）。
跑：python3 -m pytest scripts/test_dispatch_skip_projects.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent))
import run_daily  # noqa: E402


def _args(**over):
    base = dict(force=True, max_concurrent=1, dispatch_limit=None, dispatch_skip_dev=False)
    base.update(over); return SimpleNamespace(**base)


def _gate():
    return [
        {"verdict": "pass", "project": "cc-web-control", "prd_path": "p1.md", "slug": "s1"},
        {"verdict": "pass", "project": "other-proj", "prd_path": "p2.md", "slug": "s2"},
        {"verdict": "drop", "project": "dropped", "prd_path": "p3.md", "slug": "s3"},
    ]


def _stub_run_one(sink: list[str]):
    """返回假 _run_one：记录被投递的 project，返回模拟 pr_open record。"""
    def _fake(entry, prof, stamp, args):
        sink.append(entry["project"])
        return {"project": entry["project"], "prd_path": entry["prd_path"],
                "slug": entry["slug"], "status": "pr_open"}
    return _fake


def test_skip_listed_project_and_dispatch_rest(tmp_path, monkeypatch):
    """DISPATCH_SKIP_PROJECTS=cc-web-control → cc-web-control 落 skip 不投；other-proj 正常投；dropped 不过闸。"""
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path)
    monkeypatch.setenv("DISPATCH_SKIP_PROJECTS", "cc-web-control")
    dispatched: list[str] = []
    monkeypatch.setattr(run_daily, "_run_one", _stub_run_one(dispatched))

    recs = run_daily.stage_dispatch(_args(), _gate(), {}, "20260727")

    assert dispatched == ["other-proj"]                      # cc-web-control 被跳过未投
    by_proj = {r["project"]: r for r in recs}
    assert by_proj["cc-web-control"]["status"] == "skip"
    assert "#1105" in by_proj["cc-web-control"]["skip_reason"]
    assert by_proj["other-proj"]["status"] == "pr_open"
    # skip 记录落 disp_file（report 段可见，非 silent drop）
    disp = json.loads((tmp_path / "dispatch_20260727.json").read_text(encoding="utf-8"))
    assert any(r["project"] == "cc-web-control" and r["status"] == "skip" for r in disp)


def test_env_empty_is_noop(tmp_path, monkeypatch):
    """env 不设 = no-op：cc-web-control 也正常投递，records 无 skip。"""
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path)
    monkeypatch.delenv("DISPATCH_SKIP_PROJECTS", raising=False)
    dispatched: list[str] = []
    monkeypatch.setattr(run_daily, "_run_one", _stub_run_one(dispatched))

    recs = run_daily.stage_dispatch(_args(), _gate(), {}, "20260727")

    assert sorted(dispatched) == ["cc-web-control", "other-proj"]   # 都投、无跳过
    assert not any(r.get("status") == "skip" for r in recs)


def test_all_passed_skipped_returns_skip_records(tmp_path, monkeypatch):
    """过闸 PRD 全被跳过 → 不进并行投递、直接 return skip_records 并落盘。"""
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path)
    monkeypatch.setenv("DISPATCH_SKIP_PROJECTS", "cc-web-control")
    called = {"run_one": False}

    def _fake(entry, prof, stamp, args):
        called["run_one"] = True; return {"project": entry["project"], "status": "pr_open"}
    monkeypatch.setattr(run_daily, "_run_one", _fake)

    gate = [{"verdict": "pass", "project": "cc-web-control", "prd_path": "p1.md", "slug": "s1"}]
    recs = run_daily.stage_dispatch(_args(), gate, {}, "20260727")

    assert not called["run_one"]                            # 边界短路，_run_one 未被调
    assert len(recs) == 1 and recs[0]["status"] == "skip"
    disp = json.loads((tmp_path / "dispatch_20260727.json").read_text(encoding="utf-8"))
    assert disp == recs                                     # 落盘 = 返回一致
