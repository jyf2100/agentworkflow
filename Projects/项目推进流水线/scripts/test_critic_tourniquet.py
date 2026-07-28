#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_critic_tourniquet.py — critic 段输出契约止血单测（Phase 0，change 2026-07-28）。

验证 persona 漏吐硬契约字段时 stage_critic 不崩、不穿透 _run_pipeline 的
except RuntimeError 屏障（run_daily.py:2822，只接 RuntimeError）拖垮整晚 cron，
而是把该 PRD 降级 drop：
    - critic 漏吐 verdict（_critic_one 返回无 verdict）→ 降级 drop，不 KeyError
    - prd manifest 缺 path（path=None）→ 降级跳过，不 Path(None) TypeError
    - 正常 verdict（pass/drop）原样穿过（不回归）

stub _critic_one 拦截真实 persona（_critic_one 内 794 行 Path(path).stem 对 None
会 TypeError，path 用例的 stub 还原该行为以证明现状确实崩）；AAA 结构。零 SDK。
跑：python3 -m pytest scripts/test_critic_tourniquet.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent))
import run_daily  # noqa: E402


def _args(**over):
    base = dict(force=True)
    base.update(over); return SimpleNamespace(**base)


def test_critic_missing_verdict_degrades_not_crash(tmp_path, monkeypatch):
    """critic persona 漏吐 verdict → 该 PRD 降级 drop，不 KeyError 穿透 abort。"""
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path)
    manifest = {"prds": [{"project": "p1", "path": "a.md", "source_path": "s"}]}
    monkeypatch.setattr(run_daily, "_critic_one",
                        lambda path, src, prof: {"prd_path": path, "project": "p1"})  # 故意无 verdict
    gate = run_daily.stage_critic(_args(), manifest, {}, "20260728")
    assert len(gate) == 1
    assert gate[0]["verdict"] == "drop"                       # 降级，不崩
    assert gate[0]["prd_path"] == "a.md"                      # 脏数据不被丢


def test_critic_missing_prd_path_degrades_not_crash(tmp_path, monkeypatch):
    """prd manifest 缺 path（path=None）→ 降级跳过，不 Path(None) TypeError。"""
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path)
    manifest = {"prds": [{"project": "p1", "source_path": "s"}]}     # 无 path
    called: list = []
    def _fake(path, src, prof):
        called.append(path)
        # 还原真 _critic_one 794 行 Path(path).stem 对 None 的崩溃，证明现状确实 TypeError
        if path is None: raise TypeError("Path(None) 还原真实 _critic_one 行为")
        return {"verdict": "pass", "prd_path": path, "project": "p1"}
    monkeypatch.setattr(run_daily, "_critic_one", _fake)
    gate = run_daily.stage_critic(_args(), manifest, {}, "20260728")
    assert len(gate) == 1
    assert gate[0]["verdict"] == "drop"                       # 缺 path 无法过闸 → 降级
    assert called == []                                       # None path 提前跳过，_critic_one 未被调


def test_critic_normal_verdicts_unaffected(tmp_path, monkeypatch):
    """止血不回归：正常 pass/drop verdict 原样穿过，不被误降级。"""
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path)
    manifest = {"prds": [
        {"project": "p1", "path": "a.md", "source_path": "s"},
        {"project": "p2", "path": "b.md", "source_path": "s"},
    ]}
    verdicts = iter(["pass", "drop"])
    monkeypatch.setattr(run_daily, "_critic_one",
                        lambda path, src, prof: {"verdict": next(verdicts), "prd_path": path})
    gate = run_daily.stage_critic(_args(), manifest, {}, "20260728")
    assert {g["verdict"] for g in gate} == {"pass", "drop"}   # 原样穿过
