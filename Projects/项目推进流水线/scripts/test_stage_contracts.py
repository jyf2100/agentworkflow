#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_stage_contracts.py — stage 输出契约层单测（change 2026-07-28 治本 Phase 1+3）。

覆盖：
    - validate_stage fail-open（未注册 → []；validator 抛 → []）
    - render_repair_hint（空/全 warning → ""；error 列字段路径 + 要求完整 JSON；attempt≥2 加提醒）
    - CriticContract（verdict 缺失/受控值越界/正常；prd_path 缺失）
    - PrdContract（prds[i].path 缺失；正常；空 prds）
    - 注册表（critic / prd 已注册）

被测模块纯 stdlib。AAA 结构。跑：python3 -m pytest scripts/test_stage_contracts.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from stage_contracts import (  # noqa: E402
    CONTRACTS, CriticContract, Issue, PrdContract,
    get_contract, render_repair_hint, validate_stage,
)


# ── validate_stage fail-open ──────────────────────────────────────
def test_validate_stage_unregistered_returns_empty():
    """未注册 stage → []（no-op，行为与现状字节一致）。"""
    assert validate_stage("no-such-stage", {"a": 1}) == []


def test_validate_stage_validator_raises_returns_empty():
    """validator 抛异常 → []（fail-open，绝不抛主路径，不改 stage 终态）。"""
    class _Boom:
        def validate(self, p): raise RuntimeError("契约层故障模拟")
    CONTRACTS["__boom__"] = _Boom()
    try:
        assert validate_stage("__boom__", {}) == []
    finally:
        del CONTRACTS["__boom__"]


# ── render_repair_hint ─────────────────────────────────────────────
def test_render_repair_hint_empty_when_no_errors():
    """空 issues / 全 warning → ""（no-op，不触发重试）。"""
    assert render_repair_hint([], attempt=1) == ""
    assert render_repair_hint([Issue("x", "warning", "软契约")], attempt=1) == ""


def test_render_repair_hint_lists_error_fields_and_requires_full_json():
    """error Issue → 提示含字段路径 + 诊断 + 要求重新输出完整合规 JSON。"""
    hint = render_repair_hint([Issue("verdict", "error", "必须 ∈{pass,drop,revise}")], attempt=1)
    assert "verdict" in hint and "pass,drop,revise" in hint
    assert "完整合规 JSON" in hint


def test_render_repair_hint_attempt_reminder_only_after_first():
    """attempt=1 无提醒；attempt≥2 加「第 N 次，上次已被告知」提醒。"""
    h1 = render_repair_hint([Issue("v", "error", "x")], attempt=1)
    h2 = render_repair_hint([Issue("v", "error", "x")], attempt=2)
    assert "第 2 次" in h2 and "第 2 次" not in h1


# ── CriticContract ─────────────────────────────────────────────────
def test_critic_missing_verdict_is_error():
    issues = CriticContract().validate({"prd_path": "a.md"})
    assert any(i.field == "verdict" and i.severity == "error" for i in issues)


def test_critic_verdict_unknown_controlled_value_is_error():
    issues = CriticContract().validate({"verdict": "unknown", "prd_path": "a.md"})
    err = [i for i in issues if i.field == "verdict"]
    assert err and "unknown" in err[0].diagnosis


def test_critic_missing_prd_path_is_error():
    issues = CriticContract().validate({"verdict": "pass"})
    assert any(i.field == "prd_path" and i.severity == "error" for i in issues)


def test_critic_valid_verdicts_have_no_errors():
    for v in ("pass", "drop", "revise"):
        errs = [i for i in CriticContract().validate({"verdict": v, "prd_path": "a.md"}) if i.severity == "error"]
        assert errs == [], f"verdict={v} 不该报 error"


# ── PrdContract ────────────────────────────────────────────────────
def test_prd_missing_path_is_error():
    issues = PrdContract().validate({"prds": [{"project": "p1"}]})   # 缺 path
    assert any(i.field == "prds[0].path" and i.severity == "error" for i in issues)


def test_prd_valid_has_no_errors():
    issues = PrdContract().validate({"prds": [{"path": "a.md", "project": "p1"}]})
    assert [i for i in issues if i.severity == "error"] == []


def test_prd_empty_prds_is_clean():
    """空 prds = 本次无 PRD 产出（合法），非契约违反。"""
    assert PrdContract().validate({"prds": []}) == []


# ── 注册表 ─────────────────────────────────────────────────────────
def test_critic_and_prd_registered():
    assert get_contract("critic") is not None
    assert get_contract("prd") is not None
