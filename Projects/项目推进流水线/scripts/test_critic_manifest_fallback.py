#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_critic_manifest_fallback.py — F7：critic 段 manifest 盘读兜底。

7.3 试点抓出（verification-7 §8）：``--from-stage critic`` 单独跑时 manifest 是空初值
（``{"prds": []}``，_run_pipeline 3728）→ stage_critic「无 PRD 跳过」并**写空 gate []**，
连锁清掉 dispatch 段兜底要读的 prd_gate——分段放行断裂。

修法：``_manifest_disk_fallback(manifest, stamp)``——manifest 空 且盘上有
``prd_manifest_{stamp}.json`` → 读回（与 dispatch 段 prd_gate 盘读兜底对称，run_daily 3758-3762）。
局限同 dispatch 兜底：inject 自增（m/m2）场景只读原始 stamp 文件，不扩。

AAA 结构，零 SDK。跑：``python3 -m pytest scripts/test_critic_manifest_fallback.py -q``
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import run_daily  # noqa: E402


def test_nonempty_manifest_untouched(tmp_path, monkeypatch):
    """Arrange: manifest 非空（prd/inject 段跑过）且盘上另有 manifest 文件。"""
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path)
    (tmp_path / "prd_manifest_20261020.json").write_text(
        json.dumps({"prds": [{"project": "disk", "path": "d.md"}], "skipped": []}), encoding="utf-8")
    live = {"prds": [{"project": "live", "path": "l.md"}], "skipped": []}
    # Act
    out, hit = run_daily._manifest_disk_fallback(live, "20261020")
    # Assert：段内活值优先，盘文件不覆盖
    assert hit is False
    assert out is live


def test_empty_manifest_no_disk_file_stays_empty(tmp_path, monkeypatch):
    """Arrange: manifest 空（--from-stage critic）且盘上无 manifest 文件。"""
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path)
    empty = {"prds": []}
    # Act
    out, hit = run_daily._manifest_disk_fallback(empty, "20261020")
    # Assert：行为不变（stage_critic 仍「无 PRD 跳过」，baseline 不动）
    assert hit is False
    assert out == {"prds": []}


def test_empty_manifest_disk_file_loaded(tmp_path, monkeypatch):
    """Arrange: manifest 空 + 盘上有 prd_manifest_{stamp}.json（inject 先跑过）。"""
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path)
    disk = {"prds": [{"project": "cc", "path": "prd/cc/x.md", "source_path": "s"}], "skipped": []}
    (tmp_path / "prd_manifest_20261020.json").write_text(json.dumps(disk), encoding="utf-8")
    # Act
    out, hit = run_daily._manifest_disk_fallback({"prds": []}, "20261020")
    # Assert：读回盘上 manifest，critic 不再「无 PRD 跳过」、不再写空 gate
    assert hit is True
    assert out == disk


def test_empty_manifest_wrong_stamp_not_loaded(tmp_path, monkeypatch):
    """Arrange: 盘上只有自增后缀文件（prd_manifest_20261020m.json），原始 stamp 无文件。"""
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path)
    (tmp_path / "prd_manifest_20261020m.json").write_text(
        json.dumps({"prds": [{"project": "bump", "path": "b.md"}], "skipped": []}), encoding="utf-8")
    # Act：stamp=原始值（局限：不追自增后缀，与 dispatch 兜底对称）
    out, hit = run_daily._manifest_disk_fallback({"prds": []}, "20261020")
    # Assert：不误读他轮 manifest（宁可跳过也不投错轮——fail-safe）
    assert hit is False
    assert out == {"prds": []}
