#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_inject.py — inject 段单测（TDD：先 RED）。

inject 段：把手写 PRD md 注入成标准 manifest（替 radar→prd 的自动路径），
下游 critic/dispatch 零改动即可消费。覆盖契约见 Projects/项目推进流水线/SPEC.md §4.x。

跑：python3 -m pytest scripts/test_inject.py -q   （miniconda python，含 pypinyin）

AAA 结构（Arrange / Act / Assert）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import run_daily  # noqa: E402

PROJ = "cc-web-control"
PROFILES = {PROJ: {"name": PROJ, "admission": True, "type": "code", "dev_agent_ready": True,
                   "goal": "g", "match_surface": {"one_liner": "x"}}}

SAMPLE_PRD = """\
---
project: cc-web-control
---
# 测试标题

## 验收标准
- 条件 A 成立时可被一条断言验证
"""


def _write_prd(tmp: Path, body: str = SAMPLE_PRD) -> Path:
    p = tmp / "inbox_prd.md"
    p.write_text(body, encoding="utf-8")
    return p


def _setup(tmp_path, monkeypatch):
    """把 STATE_DIR/VAULT_ROOT 指到 tmp，隔离真实 vault state。"""
    monkeypatch.setattr(run_daily, "VAULT_ROOT", tmp_path)
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path / "state")
    run_daily.STATE_DIR.mkdir(parents=True, exist_ok=True)


# ─── _pinyin_slug ──────────────────────────────────────────────────
def test_pinyin_slug_pure_chinese():
    # Arrange / Act
    slug = run_daily._pinyin_slug("密钥审计日志")
    # Assert：非空 ASCII、含「密钥」拼音前缀
    assert slug and slug != "-"
    assert all(c.isalnum() or c == "-" for c in slug)
    assert "miyao" in slug  # 密钥 → miyao


def test_pinyin_slug_mixed_ascii():
    slug = run_daily._pinyin_slug("cc-web-control 配置重构")
    assert slug.startswith("cc-web-control")  # ASCII 片段原样保留（再被 dev_slugify 规整）


# ─── _bump_stamp_suffix ────────────────────────────────────────────
def test_bump_stamp_suffix():
    assert run_daily._bump_stamp_suffix("20260717") == "20260717m"
    assert run_daily._bump_stamp_suffix("20260717m") == "20260717m2"
    assert run_daily._bump_stamp_suffix("20260717m2") == "20260717m3"


# ─── stage_inject ──────────────────────────────────────────────────
def test_inject_writes_prd_and_manifest(tmp_path, monkeypatch):
    # Arrange
    _setup(tmp_path, monkeypatch)
    args = SimpleNamespace(inject_prd=str(_write_prd(tmp_path)))

    # Act
    manifest, stamp = run_daily.stage_inject(args, PROFILES, "20260717")

    # Assert：stamp 无碰撞不变
    assert stamp == "20260717"
    # PRD 文件落地到 state/prd/<project>/
    prd = tmp_path / "state" / "prd" / PROJ / "20260717_ceshibiaoti.md"
    assert prd.is_file()
    # manifest 落地且字段齐
    man_file = tmp_path / "state" / f"prd_manifest_{stamp}.json"
    man = json.loads(man_file.read_text(encoding="utf-8"))
    assert len(man["prds"]) == 1
    e = man["prds"][0]
    assert e["project"] == PROJ
    assert e["slug"] == "ceshibiaoti"
    assert e["path"] == f"state/prd/{PROJ}/20260717_ceshibiaoti.md"
    assert e["title"] == "测试标题"
    # 返回的 manifest 与盘上一致
    assert manifest == man
    # frontmatter 补全（date/round/slug）
    fm, _body = run_daily._split_frontmatter(prd.read_text(encoding="utf-8"))
    assert fm["project"] == PROJ
    assert fm["round"] == 1
    assert fm["date"] == "2026-07-17"
    assert fm["slug"] == "ceshibiaoti"


def test_inject_rejects_unknown_project(tmp_path, monkeypatch):
    # Arrange：frontmatter.project 不在白名单 profile
    _setup(tmp_path, monkeypatch)
    bad = "---\nproject: does-not-exist\n---\n# X\n"
    args = SimpleNamespace(inject_prd=str(_write_prd(tmp_path, bad)))
    # Act / Assert：硬性拒绝（sys.exit）
    with pytest.raises(SystemExit):
        run_daily.stage_inject(args, PROFILES, "20260717")


def test_inject_stamp_auto_bumps_on_collision(tmp_path, monkeypatch):
    # Arrange：预占今天的 manifest（模拟今天已有自动跑的 state）
    _setup(tmp_path, monkeypatch)
    (tmp_path / "state" / "prd_manifest_20260717.json").write_text(
        '{"prds":[{"project":"x","slug":"y","path":"z","source_path":"","title":"t"}],"skipped":[]}',
        encoding="utf-8")
    args = SimpleNamespace(inject_prd=str(_write_prd(tmp_path)))

    # Act
    _manifest, stamp = run_daily.stage_inject(args, PROFILES, "20260717")

    # Assert：自增到 m，写新 manifest，不碰原有 manifest
    assert stamp == "20260717m"
    assert (tmp_path / "state" / "prd_manifest_20260717m.json").is_file()
    assert (tmp_path / "state" / "prd_manifest_20260717.json").is_file()  # 原有未被覆盖
