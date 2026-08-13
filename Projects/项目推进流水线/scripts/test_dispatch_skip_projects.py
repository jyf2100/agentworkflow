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


# ════════════════════════════════════════════════════════════════════════════
# single-flight-auto-merge task 2.1：串行单飞投递（flag gated，同 owner_repo 串行 / 跨 owner_repo 并行）
# D1/D9：消灭「merge 时 main 被并发动过」冲突前提——同仓 PRD 顺序投递不重叠。跨进程 flock（task 2.2）
# 在此分组结构上加。flag off → 现有全并行 baseline 不变（design 决策#8）。
# ════════════════════════════════════════════════════════════════════════════
def test_serial_shadow_on_routes_to_serial_dispatch_path(tmp_path, monkeypatch):
    """task 2.1：single_flight_serial_shadow=on → stage_dispatch 走 _dispatch_serial_by_repo（按 owner_repo
    分组串行单飞）；off → 现有全并行 ThreadPoolExecutor（baseline，design 决策#8 不变）。"""
    import feature_flags as FF
    called = {"serial": False}

    def _fake_serial(passed, profiles, stamp, args, *, worker=None):
        called["serial"] = True
        return [{"project": e.get("project"), "prd_path": e.get("prd_path"), "status": "pr_open"} for e in passed]
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path)
    monkeypatch.delenv("DISPATCH_SKIP_PROJECTS", raising=False)
    monkeypatch.setattr(run_daily, "_dispatch_serial_by_repo", _fake_serial)
    monkeypatch.setattr(run_daily, "resolve_flags",
                        lambda env=None, profile=None: FF.LoopFlags(single_flight_serial_shadow=True))
    gate = [{"verdict": "pass", "project": "p1", "prd_path": "p1.md", "slug": "s1"}]
    # Act
    run_daily.stage_dispatch(_args(), gate, {}, "20260728")
    # Assert
    assert called["serial"] is True                         # on → 走串行路径


def test_serial_shadow_off_uses_baseline_parallel_path(tmp_path, monkeypatch):
    """task 2.1：serial_shadow=off → 不走串行路径（_dispatch_serial_by_repo 不被调），维持现有全并行 baseline。"""
    import feature_flags as FF
    called = {"serial": False}
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path)
    monkeypatch.delenv("DISPATCH_SKIP_PROJECTS", raising=False)
    monkeypatch.setattr(run_daily, "_dispatch_serial_by_repo",
                        lambda *a, **k: called.__setitem__("serial", True) or [])
    monkeypatch.setattr(run_daily, "resolve_flags", lambda env=None, profile=None: FF.LoopFlags())  # 全 False
    monkeypatch.setattr(run_daily, "_run_one",
                        lambda e, p, s, a: {"project": e["project"], "status": "pr_open"})
    gate = [{"verdict": "pass", "project": "p1", "prd_path": "p1.md", "slug": "s1"}]
    # Act
    run_daily.stage_dispatch(_args(), gate, {}, "20260728")
    # Assert
    assert called["serial"] is False                        # baseline：未走串行路径


def test_dispatch_serial_by_repo_serializes_same_owner_repo(monkeypatch):
    """task 2.1：_dispatch_serial_by_repo 同 owner_repo 的 entry 顺序不重叠（single-flight：前一个 _run_one
    完成才下一个）；跨 owner_repo 可重叠（并行）。用带耗时的 _run_one stub 记录时间窗，断言同组不重叠。"""
    import re
    import time
    import threading
    # repo_owner_repo 实跑 `git -C <repo> remote get-url`（需本地 git 仓）；单测隔离为纯 URL 解析 fake，
    # 让分组 key 可控（o/r、o/other），聚焦 task 2.1 的串行分组属性（repo_owner_repo 解析正确性归各自单测）。
    def _owner_of(repo):
        m = re.search(r"/([^/]+/[^/]+?)(?:\.git)?$", repo or "")
        return m.group(1) if m else None
    monkeypatch.setattr(run_daily, "repo_owner_repo", _owner_of)
    spans: dict[str, list] = {}
    lock = threading.Lock()

    def _slow_run_one(entry, prof, stamp, args):
        owner = run_daily.repo_owner_repo((prof or {}).get("repo", "")) or ""
        t0 = time.monotonic(); time.sleep(0.03); t1 = time.monotonic()
        with lock:
            spans.setdefault(owner, []).append((t0, t1))
        return {"project": entry["project"], "prd_path": entry["prd_path"], "status": "pr_open"}
    monkeypatch.setattr(run_daily, "_run_one", _slow_run_one)
    passed = [
        {"project": "p1", "prd_path": "p1.md", "slug": "s1"},
        {"project": "p1b", "prd_path": "p2.md", "slug": "s2"},   # 同 owner_repo（o/r）
        {"project": "p2", "prd_path": "p3.md", "slug": "s3"},    # 不同 owner_repo（o/other）
    ]
    profiles = {
        "p1": {"name": "p1", "repo": "https://github.com/o/r.git"},
        "p1b": {"name": "p1b", "repo": "https://github.com/o/r.git"},
        "p2": {"name": "p2", "repo": "https://github.com/o/other.git"},
    }
    # Act
    run_daily._dispatch_serial_by_repo(passed, profiles, "20260728", _args())
    # Assert — 同 owner_repo（o/r）2 个 span 不重叠（single-flight）
    r_spans = sorted(spans["o/r"])
    assert len(r_spans) == 2
    assert r_spans[0][1] <= r_spans[1][0], "同 owner_repo 的 PRD 应串行不重叠（single-flight）"
    assert len(spans["o/other"]) == 1                         # 跨组各自独立（并行不重叠验证留集成测）
