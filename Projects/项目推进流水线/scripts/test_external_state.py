#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_external_state.py — 三态外部查询结果 + 脱敏单测（OpenSpec fail-safe-dispatch / tasks 4.1）。

覆盖三态构造、fail-safe 判定（UNKNOWN 阻断）、脱敏（PAT/Bearer/basic-auth/截断/压行）、不可变。
纯逻辑零 SDK 导入（external_state.py 无依赖）。AAA 结构。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from external_state import (  # noqa: E402
    ExtResult, ExtState, found, not_found, sanitize, unknown,
)


# ─── 三态构造 ───────────────────────────────────────────────────────
def test_found_carries_value():
    assert found(5).state is ExtState.FOUND
    assert found(5).value == 5
    assert found({"number": 7}).value == {"number": 7}
    assert found().value is None          # 纯存在性查询无载荷


def test_not_found_and_unknown_states():
    assert not_found("404").state is ExtState.NOT_FOUND
    assert unknown("超时").state is ExtState.UNKNOWN
    assert not_found().value is None and unknown().value is None


# ─── fail-safe 判定 ─────────────────────────────────────────────────
def test_unknown_is_fail_safe_signal():
    assert unknown().is_unknown is True
    assert unknown().is_decidable is False


def test_found_and_not_found_are_decidable():
    assert found(True).is_decidable is True
    assert not_found().is_decidable is True
    assert found(True).is_unknown is False
    assert not_found().is_unknown is False


# ─── 诊断脱敏 ───────────────────────────────────────────────────────
def test_sanitize_strips_github_pat():
    raw = "error: ghp_abcdefghijklmnopqrstuvwxyz trailing"
    out = sanitize(raw)
    assert "ghp_abcdef" not in out          # PAT 抹除
    assert "***" in out


def test_sanitize_strips_bearer_and_token_kv():
    assert "Bearer" not in sanitize("Authorization: Bearer abc.def.ghi")
    assert "supersecret" not in sanitize("token=supersecret here")
    assert "supersecret" not in sanitize("token: supersecret here")


def test_sanitize_strips_basic_auth_url():
    out = sanitize("clone https://user:secret@github.com/o/r.git")
    assert "secret" not in out
    assert "user" not in out                # user:secret@ 整段抹除


def test_sanitize_truncates_and_flattens_newlines():
    assert len(sanitize("x" * 300, limit=50)) == 50
    assert "\n" not in sanitize("line1\nline2\nline3")


def test_sanitize_none_and_empty():
    assert sanitize(None) == ""
    assert sanitize("") == ""


def test_constructors_auto_sanitize_reason():
    r = found(1, "err: ghp_aaaaaaaaaaaaaaaaaaaaaaaaaa tail")
    assert "ghp_aaa" not in r.reason
    assert "***" in r.reason


# ─── 不可变 ─────────────────────────────────────────────────────────
def test_extresult_is_immutable():
    r = found(1, "ok")
    with pytest.raises(Exception):
        r.value = 2                        # frozen dataclass


# ─── 工厂 vs 直接构造（脱敏契约）────────────────────────────────────
def test_direct_construction_skips_sanitize():
    # 直接构造 ExtResult 不经工厂——reason 视为已脱敏，原样保留（调用方自负其责）
    r = ExtResult(ExtState.FOUND, 1, "raw ghp_aaaaaaaaaaaaaaaaaaaa")
    assert "ghp_aaa" in r.reason
