#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_model_routing.py — model_routing 独立文件解析单测。

验证 config/model-routing.json → model 的解析规则：
  - persona key / dev key 命中返回值
  - 文件不存在 / key 缺 / null / 空串 → None（走 roc 默认，零变更 baseline）
  - 损坏 JSON → None（降级不 raise）

AAA 结构。跑：python3 -m pytest scripts/test_model_routing.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import model_routing  # noqa: E402


def test_resolve_persona_model_returns_value(tmp_path):
    """persona key 命中返回值；key 缺返回 None。"""
    cfg = tmp_path / "model-routing.json"
    cfg.write_text(json.dumps({"pa-progress": "haiku", "dev": "sonnet"}), encoding="utf-8")
    assert model_routing.resolve_persona_model("pa-progress", path=cfg) == "haiku"
    assert model_routing.resolve_persona_model("pa-radar", path=cfg) is None      # key 缺


def test_resolve_dev_model(tmp_path):
    """dev key（"dev"）命中返回值。"""
    cfg = tmp_path / "model-routing.json"
    cfg.write_text(json.dumps({"dev": "sonnet"}), encoding="utf-8")
    assert model_routing.resolve_dev_model(path=cfg) == "sonnet"


def test_missing_file_returns_none(tmp_path):
    """文件不存在 → None（零变更 baseline；不 raise）。"""
    nope = tmp_path / "nope.json"
    assert model_routing.resolve_persona_model("pa-progress", path=nope) is None
    assert model_routing.resolve_dev_model(path=nope) is None


def test_null_or_empty_value_returns_none(tmp_path):
    """value 为 null / 空串 → None（视作未配，走 roc 默认）。"""
    cfg = tmp_path / "m.json"
    cfg.write_text(json.dumps({"pa-progress": None, "dev": "", "pa-radar": "haiku"}), encoding="utf-8")
    assert model_routing.resolve_persona_model("pa-progress", path=cfg) is None    # null
    assert model_routing.resolve_dev_model(path=cfg) is None                       # 空串
    assert model_routing.resolve_persona_model("pa-radar", path=cfg) == "haiku"    # 正常值不受影响


def test_corrupt_json_returns_none(tmp_path):
    """损坏 JSON → None（降级 roc 默认，不 raise）。"""
    cfg = tmp_path / "m.json"
    cfg.write_text("{not valid json", encoding="utf-8")
    assert model_routing.resolve_persona_model("pa-progress", path=cfg) is None
    assert model_routing.resolve_dev_model(path=cfg) is None


def test_non_object_json_returns_none(tmp_path):
    """顶层非 object（如数组/字符串）→ None（不 crash）。"""
    cfg = tmp_path / "m.json"
    cfg.write_text(json.dumps(["haiku", "sonnet"]), encoding="utf-8")
    assert model_routing.resolve_persona_model("pa-progress", path=cfg) is None


# ─── review 2026-08-09 健壮性补强（①RecursionError ②value类型 ③非dict warn ④未知key warn）──
def test_non_string_value_returns_none(tmp_path):
    """value 非字符串（int/bool/object 真值）→ None（守 str|None 契约，损坏配置降级；review ②）。"""
    cfg = tmp_path / "m.json"
    cfg.write_text(json.dumps({"dev": 123, "pa-progress": True, "pa-radar": {"x": 1}}), encoding="utf-8")
    assert model_routing.resolve_dev_model(path=cfg) is None                     # int 123（真值，旧版穿透）
    assert model_routing.resolve_persona_model("pa-progress", path=cfg) is None  # bool True（真值穿透）
    assert model_routing.resolve_persona_model("pa-radar", path=cfg) is None     # object（真值穿透）


def test_deeply_nested_json_returns_none(tmp_path):
    """深嵌套 JSON（触发 RecursionError）→ None（不 raise，守「降级不 raise」契约；review ①）。"""
    cfg = tmp_path / "m.json"
    cfg.write_text("[" * 10000 + "]" * 10000, encoding="utf-8")  # 20KB < 64KB cap，过 cap 进 json.loads 触发 RecursionError
    assert model_routing.resolve_persona_model("pa-progress", path=cfg) is None
    assert model_routing.resolve_dev_model(path=cfg) is None


def test_oversized_file_returns_none(tmp_path):
    """超大文件（> 64KB cap）→ None（防 MemoryError，降级不 raise；review ①）。"""
    cfg = tmp_path / "m.json"
    cfg.write_text("x" * 70000, encoding="utf-8")  # > 65536 cap
    assert model_routing.resolve_persona_model("pa-progress", path=cfg) is None


def test_non_object_json_warns(tmp_path, caplog):
    """顶层非 object（数组）→ None + warn（形状错配错反馈，对称语法错 warn；review ③）。"""
    cfg = tmp_path / "m.json"
    cfg.write_text(json.dumps(["haiku"]), encoding="utf-8")
    with caplog.at_level("WARNING", logger="model_routing"):
        assert model_routing.resolve_persona_model("pa-progress", path=cfg) is None
    assert "顶层非 object" in caplog.text


def test_unknown_key_warns(tmp_path, caplog):
    """未知 key（typo/大小写）→ warn（主配置通道配错反馈；review ④）。"""
    cfg = tmp_path / "m.json"
    cfg.write_text(json.dumps({"pa-progres": "haiku", "DEV": "sonnet"}), encoding="utf-8")
    with caplog.at_level("WARNING", logger="model_routing"):
        model_routing.resolve_persona_model("pa-progress", path=cfg)
    assert "未知 key" in caplog.text
