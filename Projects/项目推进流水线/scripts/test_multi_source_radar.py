#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_multi_source_radar.py — ADR-0007 多源 radar 消费接口单测（TDD）。

覆盖：
    - load_sources 校验（name 唯一 / root 排他 / kind 缺省 / fetcher warn 不阻断）
    - _source_of 候选源追溯
    - radar_prompt per-project 签名
    - stage_radar 多源遍历 + target_projects 路由 + 无订阅不调 + 失败不 bump + candidate 带 source

跑：python3 -m pytest scripts/test_multi_source_radar.py -q
AAA 结构（Arrange / Act / Assert）。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import run_daily  # noqa: E402


# ─── load_sources 校验（ADR-0007 决定 #2/#4）──────────────────────────
def _write_sources(tmp_path, body: str, monkeypatch) -> Path:
    sf = tmp_path / "sources.yaml"
    sf.write_text(body, encoding="utf-8")
    monkeypatch.setattr(run_daily, "SOURCES_FILE", sf)
    monkeypatch.setattr(run_daily, "PROJECT_DIR", tmp_path)   # fetcher 路径解析兜底
    return sf


def test_load_sources_rejects_duplicate_name(tmp_path, monkeypatch):
    # Arrange：两条源同名
    _write_sources(tmp_path, "sources:\n  - {name: a, root: r1}\n  - {name: a, root: r2}\n", monkeypatch)
    # Act / Assert：name 重复 → 拒载退出
    with pytest.raises(SystemExit):
        run_daily.load_sources()


def test_load_sources_rejects_shared_root(tmp_path, monkeypatch):
    # Arrange：两条源共用 root
    _write_sources(tmp_path, "sources:\n  - {name: a, root: same}\n  - {name: b, root: same}\n", monkeypatch)
    # Act / Assert：root 排他 → 拒载退出
    with pytest.raises(SystemExit):
        run_daily.load_sources()


def test_load_sources_defaults_kind_directory(tmp_path, monkeypatch):
    # Arrange：源未写 kind
    _write_sources(tmp_path, "sources:\n  - {name: a, root: r1}\n", monkeypatch)
    # Act / Assert：缺省 kind=directory（消费侧零分支）
    out = run_daily.load_sources()
    assert out[0]["kind"] == "directory"


def test_load_sources_warns_missing_fetcher_but_keeps_source(tmp_path, monkeypatch, capsys):
    # Arrange：声明 fetcher 指向不存在的脚本（本次未实现的 kind）
    _write_sources(
        tmp_path,
        'sources:\n  - {name: deep, root: r1, kind: agent-deepresearch,'
        '  fetcher: "scripts/fetchers/x.py"}\n',
        monkeypatch,
    )
    # Act：不抛（消费侧只看目录；fetcher 未就绪静默）
    out = run_daily.load_sources()
    # Assert：源保留 + 打了 warn
    assert out[0]["name"] == "deep"
    captured = capsys.readouterr()
    assert "fetcher" in captured.out and "不存在" in captured.out
