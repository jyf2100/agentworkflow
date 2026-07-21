#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_session_meta.py — task 5.1 SDK session metadata 持久化单测。

覆盖：异常分类启发式、session_resumable 判定、to/from_dict 往返、SessionStore 原子
落盘/读回/缺失 fallback、from_sdk_result 字段抽取与 subtype 映射。AAA；模块零 SDK。
跑：python3 -m pytest scripts/test_session_meta.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import session_meta as SM  # noqa: E402


# ─── 异常分类启发式（喂 RetryPolicy：transient→resume / context_corrupt→new_session）────
def test_classify_transient_markers():
    assert SM.classify_exception("APIError", "service overloaded, retry") is SM.ExceptionClass.TRANSIENT
    assert SM.classify_exception("ConnectionError", "connection reset by peer") is SM.ExceptionClass.TRANSIENT
    assert SM.classify_exception("HTTPError", "503 temporarily unavailable") is SM.ExceptionClass.TRANSIENT


def test_classify_context_corrupt_markers():
    assert SM.classify_exception("ContextError", "prompt too long, compaction failed") is SM.ExceptionClass.CONTEXT_CORRUPT
    assert SM.classify_exception("ModelError", "maximum context length exceeded") is SM.ExceptionClass.CONTEXT_CORRUPT


def test_classify_provider_markers():
    assert SM.classify_exception("BadRequest", "invalid_request_error: bad request") is SM.ExceptionClass.PROVIDER
    assert SM.classify_exception("PermissionDenied", "403 forbidden") is SM.ExceptionClass.PROVIDER


def test_classify_unknown_fallback():
    assert SM.classify_exception("ValueError", "something odd") is SM.ExceptionClass.UNKNOWN
    assert SM.classify_exception("") is SM.ExceptionClass.UNKNOWN


def test_sanitize_strips_secrets():
    """exception_message 落盘前抹 secret 残片（ghp_/Bearer/token=）。"""
    assert "ghp_secret1234567890abcde" not in SM._sanitize_message("err token=ghp_secret1234567890abcde")
    assert "[redacted]" in SM._sanitize_message("dump ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ1234567890")
    assert "bearer [redacted]" in SM._sanitize_message("auth bearer abc.def.ghi")


# ─── SessionMeta 模型 ────────────────────────────────────────────────────
def _meta(**kw):
    base = dict(iteration_id="iter_1", session_id="sess_abc", result_subtype=SM.ResultSubtype.SUCCESS)
    base.update(kw)
    return SM.SessionMeta(**base)


def test_session_resumable_requires_session_id_and_clean_context():
    assert _meta().session_resumable is True
    assert _meta(session_id=None).session_resumable is False                       # 缺 session → new_session fallback
    assert _meta(session_id="").session_resumable is False
    assert _meta(exception_class=SM.ExceptionClass.CONTEXT_CORRUPT).session_resumable is False  # 污染 → new_session
    assert _meta(exception_class=SM.ExceptionClass.TRANSIENT).session_resumable is True          # transient 仍可 resume


def test_to_from_dict_roundtrip_preserves_all_fields():
    m = _meta(stop_reason="end_turn", turns=12, input_tokens=1000, output_tokens=500,
              cache_read_tokens=200, cost_usd=0.42, compaction_count=1,
              exception_class=SM.ExceptionClass.TRANSIENT, exception_message="timeout")
    m2 = SM.SessionMeta.from_dict(m.to_dict())
    assert m2 == m
    assert m2.cost_usd == 0.42 and m2.compaction_count == 1


# ─── SessionStore：原子落盘 / 读回 / 缺失 fallback ─────────────────────────
def test_store_save_load_roundtrip(tmp_path):
    store = SM.SessionStore(tmp_path / "sessions")
    m = _meta(iteration_id="iter_xyz", session_id="sess_1", turns=7, cost_usd=1.5)
    p = store.save(m)
    assert p.exists() and p.suffix == ".json"
    assert not (tmp_path / "sessions" / "iter_xyz.tmp").exists()   # 原子写：tmp 已 replace
    loaded = store.load("iter_xyz")
    assert loaded == m
    assert store.exists("iter_xyz") and not store.exists("iter_missing")


def test_store_load_missing_returns_none_for_retry_fallback(tmp_path):
    """缺失 session meta → None → RetryPolicy fallback new_session（5.7 场景之一）。"""
    store = SM.SessionStore(tmp_path / "sessions")
    assert store.load("never_saved") is None


def test_store_persists_json_with_redacted_message(tmp_path):
    store = SM.SessionStore(tmp_path / "s")
    m = _meta(exception_class=SM.ExceptionClass.UNKNOWN,
              exception_message=SM._sanitize_message("leak token=ghp_supersecret1234567890"))
    store.save(m)
    raw = json.loads((tmp_path / "s" / "iter_1.json").read_text(encoding="utf-8"))
    assert "ghp_supersecret" not in raw["exception_message"]
    assert "[redacted]" in raw["exception_message"]


# ─── from_sdk_result：从真实 SDK ResultMessage dict 抽字段 ──────────────────
def test_from_sdk_result_success_normal_end_turn():
    result = {"subtype": "result", "is_error": False, "stop_reason": "end_turn",
              "session_id": "sess_1", "num_turns": 5,
              "usage": {"input_tokens": 1000, "output_tokens": 400, "cache_read_input_tokens": 50},
              "total_cost_usd": 0.33}
    m = SM.from_sdk_result("iter_1", result)
    assert m.result_subtype is SM.ResultSubtype.SUCCESS
    assert m.session_id == "sess_1" and m.turns == 5
    assert m.input_tokens == 1000 and m.cache_read_tokens == 50
    assert m.cost_usd == 0.33 and m.session_resumable


def test_from_sdk_result_max_turns_and_error_subtypes():
    assert SM.from_sdk_result("i", {"stop_reason": "max_turns"}).result_subtype is SM.ResultSubtype.MAX_TURNS
    assert SM.from_sdk_result("i", {"is_error": True, "stop_reason": ""}).result_subtype is SM.ResultSubtype.ERROR
    assert SM.from_sdk_result("i", {"stop_reason": "refusal"}).result_subtype is SM.ResultSubtype.REFUSED


def test_from_sdk_result_classifies_exception_tuple():
    """SDK 抛异常 → (type, msg) 喂分类；message 脱敏落盘。"""
    m = SM.from_sdk_result("i", {"stop_reason": ""}, exception=("APIStatusError", "service overloaded 503"))
    assert m.exception_class is SM.ExceptionClass.TRANSIENT
    assert "overloaded" in m.exception_message   # 分类用关键词保留（message 仍脱敏 secret）


def test_from_sdk_result_classifies_base_exception():
    exc = ConnectionError("connection reset by peer")
    m = SM.from_sdk_result("i", {"session_id": "s", "stop_reason": ""}, exception=exc)
    assert m.exception_class is SM.ExceptionClass.TRANSIENT


def test_from_sdk_result_tolerates_missing_fields():
    """SDK 版本字段名差异 / 缺字段 → 兜底归零，不崩。"""
    m = SM.from_sdk_result("i", {})   # 全空 dict
    assert m.session_id is None and m.turns == 0 and m.cost_usd is None
    assert m.input_tokens == 0 and m.result_subtype is SM.ResultSubtype.STOPPED
