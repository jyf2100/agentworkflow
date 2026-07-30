#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_merge_loop.py — merge/revert 闭环 crash 安全门单测（single-flight-auto-merge task 6.x 方案 C / D12）。

验证 merge_loop journal（防「merge push 后 crash 重复合 main」致命场景，Agent 实证）：
  - merge/revert phase 前 record *_started（intent），phase 后 record *_completed（confirm，带 sha）/ merge_abandoned（未 push 安全结束）；
  - has_open_intent：最后一条是 *_started（crash 在 phase 中，未闭合）→ True → dispatch halt（不盲目重 merge）；
  - started+completed/abandoned → False（闭合，正常 / 可重试）；
  - 不同 prd_id / owner_repo 隔离；最后一条事件为准（多轮）；
  - journal 损坏 fail-safe（True，halt——破坏性副作用门，不冒险重 merge），文件缺失/无事件 → False。

不触真实 git/IO：state_dir 落 tmp。AAA 结构。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import merge_loop as ML  # noqa: E402


def _stamp():
    return "2026-07-30T00:00:00"


def test_record_and_last_event(tmp_path):
    # Arrange + Act
    ML.record_event(tmp_path, "o/r", "prd1", "merge_started", stamp_fn=_stamp, branch="b", main_ref="main")
    ev = ML.last_event(tmp_path, "o/r", "prd1")
    # Assert
    assert ev is not None
    assert ev.event_type == "merge_started"
    assert ev.prd_id == "prd1"
    assert ev.payload["branch"] == "b"
    assert ev.payload["main_ref"] == "main"


def test_last_event_none_when_no_records(tmp_path):
    assert ML.last_event(tmp_path, "o/r", "prd1") is None


def test_has_open_intent_started_only_true(tmp_path):
    # crash 在 merge phase 中（started 无 completed）→ open → dispatch halt
    ML.record_event(tmp_path, "o/r", "prd1", "merge_started", stamp_fn=_stamp)
    assert ML.has_open_intent(tmp_path, "o/r", "prd1") is True


def test_has_open_intent_started_then_completed_false(tmp_path):
    # merge 成功（push 完成 + confirm）→ closed → 正常
    ML.record_event(tmp_path, "o/r", "prd1", "merge_started", stamp_fn=_stamp)
    ML.record_event(tmp_path, "o/r", "prd1", "merge_completed", stamp_fn=_stamp, merge_commit="abc1234")
    assert ML.has_open_intent(tmp_path, "o/r", "prd1") is False


def test_has_open_intent_started_then_abandoned_false(tmp_path):
    # merge CONFLICT/UNKNOWN（未 push，安全结束）→ abandoned → closed → 允许重试（不 halt）
    ML.record_event(tmp_path, "o/r", "prd1", "merge_started", stamp_fn=_stamp)
    ML.record_event(tmp_path, "o/r", "prd1", "merge_abandoned", stamp_fn=_stamp, reason="rebase_conflict")
    assert ML.has_open_intent(tmp_path, "o/r", "prd1") is False


def test_has_open_intent_revert_started_true(tmp_path):
    # crash 在 revert phase 中（revert push 可能已发生）→ open → halt（防重复 revert）
    ML.record_event(tmp_path, "o/r", "prd1", "revert_started", stamp_fn=_stamp, merge_commit="abc1234")
    assert ML.has_open_intent(tmp_path, "o/r", "prd1") is True


def test_has_open_intent_revert_completed_false(tmp_path):
    ML.record_event(tmp_path, "o/r", "prd1", "revert_started", stamp_fn=_stamp, merge_commit="abc1234")
    ML.record_event(tmp_path, "o/r", "prd1", "revert_completed", stamp_fn=_stamp,
                    merge_commit="abc1234", revert_commit="def5678")
    assert ML.has_open_intent(tmp_path, "o/r", "prd1") is False


def test_has_open_intent_no_records_false(tmp_path):
    # 无记录（首次 / 文件缺失）→ 无 open intent（正常放行）
    assert ML.has_open_intent(tmp_path, "o/r", "prd1") is False


def test_has_open_intent_per_prd_isolation(tmp_path):
    # prd1 open（crash），prd2 无 → 互不影响
    ML.record_event(tmp_path, "o/r", "prd1", "merge_started", stamp_fn=_stamp)
    assert ML.has_open_intent(tmp_path, "o/r", "prd1") is True
    assert ML.has_open_intent(tmp_path, "o/r", "prd2") is False


def test_has_open_intent_per_owner_repo_isolation(tmp_path):
    ML.record_event(tmp_path, "o/r1", "prd1", "merge_started", stamp_fn=_stamp)
    assert ML.has_open_intent(tmp_path, "o/r1", "prd1") is True
    assert ML.has_open_intent(tmp_path, "o/r2", "prd1") is False


def test_has_open_intent_latest_event_wins(tmp_path):
    # 多轮：prd1 第一次 completed（正常），第二次 started（crash）→ 最后是 started → open
    ML.record_event(tmp_path, "o/r", "prd1", "merge_started", stamp_fn=_stamp)
    ML.record_event(tmp_path, "o/r", "prd1", "merge_completed", stamp_fn=_stamp, merge_commit="abc")
    ML.record_event(tmp_path, "o/r", "prd1", "merge_started", stamp_fn=_stamp)   # 第二轮 crash
    assert ML.has_open_intent(tmp_path, "o/r", "prd1") is True


def test_has_open_intent_corrupted_journal_fail_safe(tmp_path):
    # journal 中部损坏（committed history 内夹坏行）→ journal.py fail-closed raise
    # merge_loop 捕获 → fail-safe True（halt，不冒险重 merge）
    good = json.dumps({"schema_version": 1, "event_id": "ml-x", "timestamp": "t",
                       "iteration_id": "", "run_id": "", "prd_id": "prd1",
                       "event_type": "merge_started", "payload": {}}, ensure_ascii=False)
    jp = ML.merge_loop_journal_path(tmp_path, "o/r")
    jp.parent.mkdir(parents=True, exist_ok=True)
    # 合法行 + 中部损坏（非 JSON，非末行）+ 合法末行 → read_events fail-closed raise
    jp.write_text(good + "\n<<<非json中部损坏>>>\n" + good + "\n", encoding="utf-8")
    assert ML.has_open_intent(tmp_path, "o/r", "prd1") is True


def test_record_does_not_raise_on_write(tmp_path):
    # record 正常落盘不 raise（fail-safe wrapper 保证不阻断 merge/revert 流程本身）
    ML.record_event(tmp_path, "o/r", "prd1", "merge_started", stamp_fn=_stamp, branch="b")
    ML.record_event(tmp_path, "o/r", "prd1", "merge_completed", stamp_fn=_stamp, merge_commit="abc")
    # 跨读持久化
    assert ML.last_event(tmp_path, "o/r", "prd1").event_type == "merge_completed"
