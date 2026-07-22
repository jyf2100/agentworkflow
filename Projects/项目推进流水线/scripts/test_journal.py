#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_journal.py — append-only JSONL journal IO + 损坏检测单测（OpenSpec add-durable-loop-runtime task 2.2 + 2.3）。

journal 是第二阶段的「崩溃恢复真源」（design 决策#1）。本测试锁定 IO 契约与 **损坏容错策略**：

    task 2.2（IO）—— ``append_event`` 原子追加（O_APPEND + flush + fsync，崩溃后只可能丢最后一条，
        绝不撕裂已提交历史）；``read_events`` 读 JSONL 还原 ``JournalEvent``。
    task 2.3（损坏检测）—— spec 硬断言：「reader MUST tolerate a single incomplete trailing record
        but MUST fail closed on malformed or missing records inside committed history」。
        即 **末尾不完整容忍**（崩溃截断最后一条 → 丢弃，继续）；**中部损坏 fail-closed**（已提交
        历史里夹了坏行 → 拒绝，绝不静默吞——防止状态机基于残缺事件归约出错误状态）。

IO 测试用 pytest ``tmp_path`` 隔离（不在 cron/项目目录留痕）。模块仅依赖 ``loop_state`` 数据模型 + 标准库，
不触 SDK——cron 隔离不变（``run_daily.py`` 顶部仍不连带加载 SDK）。

跑：python3 -m pytest scripts/test_journal.py -q
AAA 结构（Arrange / Act / Assert）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import journal as J  # noqa: E402
import loop_state as L  # noqa: E402


def _ev(eid: str, etype: str = "running", **payload) -> L.JournalEvent:
    """造最小合法 JournalEvent（run/prd/iteration 固定）。"""
    return L.JournalEvent(
        schema_version=L.JOURNAL_SCHEMA_VERSION, event_id=eid,
        timestamp="2026-07-20T00:00:00Z", iteration_id="i", run_id="r",
        prd_id="p", event_type=etype, payload=payload,
    )


def _write_raw(path: Path, text: str) -> None:
    """绕过 append_event 直接写原始文本（构造损坏场景）。"""
    path.write_text(text, encoding="utf-8")


# ════════════════════════════════════════════════════════════════════════
# task 2.2：原子 append / flush / read / 往返
# ════════════════════════════════════════════════════════════════════════
def test_append_then_read_roundtrip(tmp_path):
    """append 3 条 → read 出 3 条，字段全等（含 payload dict）。

    这是「journal 可持久化 + 可还原」的最朴素契约——``asdict``→JSON→``JournalEvent(**dict)`` 往返不丢信息。"""
    # Arrange
    path = tmp_path / "run-1.jsonl"
    events = [_ev("e1", "planned"), _ev("e2", "running", session_id="s-1"),
              _ev("e3", "agent_finished", turns=7)]
    # Act
    for e in events:
        J.append_event(path, e)
    got = J.read_events(path)
    # Assert
    assert len(got) == 3
    assert [e.event_id for e in got] == ["e1", "e2", "e3"]
    assert got[1].payload == {"session_id": "s-1"}
    assert got[2].payload == {"turns": 7}
    assert got[0].schema_version == L.JOURNAL_SCHEMA_VERSION


def test_append_does_not_overwrite_existing(tmp_path):
    """append 是 **O_APPEND**（不覆盖）——多次 append 在文件尾累积，旧记录绝不丢。

    崩溃恢复的前提：已提交历史不可被新 append 撕裂/覆盖。"""
    # Arrange
    path = tmp_path / "run-1.jsonl"
    # Act：分两次 append（模拟两次 dispatch 写同一 journal）
    J.append_event(path, _ev("e1", "running"))
    J.append_event(path, _ev("e2", "agent_finished"))
    got = J.read_events(path)
    # Assert：两条都在，顺序保留
    assert [e.event_id for e in got] == ["e1", "e2"]


def test_append_creates_missing_file(tmp_path):
    """文件不存在时 append 自动创建（首次 dispatch 无 journal 文件是正常态）。"""
    path = tmp_path / "sub" / "run-1.jsonl"
    J.append_event(path, _ev("e1"))
    assert path.exists()
    assert len(J.read_events(path)) == 1


def test_read_missing_file_returns_empty(tmp_path):
    """read 不存在的 journal → 空列表（首次运行/无历史，非错误）。"""
    assert J.read_events(tmp_path / "nope.jsonl") == []


def test_read_empty_file_returns_empty(tmp_path):
    """空文件 → 空列表。"""
    path = tmp_path / "empty.jsonl"
    path.write_text("", encoding="utf-8")
    assert J.read_events(path) == []


def test_append_calls_fsync_for_durability(tmp_path, monkeypatch):
    """append 必须调 ``os.fsync``——这是「崩溃后已 append 的行落盘」的硬保证（design 决策#1 恢复真源）。

    漏 fsync = 崩溃后 OS page cache 丢失最近 append → 恢复读到比实际少的记录 → 状态机归约错。
    本测试 spy ``os.fsync`` 确认每次 append 都落盘。"""
    calls: list[int] = []
    monkeypatch.setattr(J.os, "fsync", lambda fd: calls.append(fd))
    J.append_event(tmp_path / "j.jsonl", _ev("e1"))
    assert len(calls) == 1, "append_event 未调 os.fsync（崩溃恢复真源破损）"


def test_append_each_line_is_complete_json(tmp_path):
    """append 后文件每一行都是完整合法 JSON（无半行/撕裂）——原子性的可观测证据。

    直接读文件逐行 ``json.loads``，全过即证明 append 不产生撕裂行。"""
    path = tmp_path / "j.jsonl"
    for i in range(5):
        J.append_event(path, _ev(f"e{i}"))
    for line in path.read_text(encoding="utf-8").splitlines():
        assert line.strip(), "不应有空行"
        json.loads(line)  # 不抛即完整合法


# ════════════════════════════════════════════════════════════════════════
# task 2.3：不完整尾部容忍 + 中部损坏 fail-closed
# ════════════════════════════════════════════════════════════════════════
def test_read_tolerates_incomplete_trailing_record(tmp_path):
    """**spec 硬断言**：末尾一条不完整（崩溃截断）→ 容忍丢弃，返回前面已提交的合法行。

    场景：dispatch append 到第 3 条时崩溃，第 3 条只写了半行。恢复时读前 2 条继续，不报错。"""
    # Arrange：2 合法 + 末尾半行（无尾换行的截断 JSON）
    path = tmp_path / "j.jsonl"
    J.append_event(path, _ev("e1", "running"))
    J.append_event(path, _ev("e2", "agent_finished"))
    with open(path, "a", encoding="utf-8") as f:
        f.write('{"schema_version": 1, "event_id": "e3", "event_t')   # 截断
    # Act
    got = J.read_events(path)
    # Assert：前 2 条在，半行被容忍丢弃，不 raise
    assert [e.event_id for e in got] == ["e1", "e2"]


def test_read_fail_closed_on_middle_corruption(tmp_path):
    """**spec 硬断言**：已提交历史内夹了坏行（非末尾）→ fail-closed，raise ``JournalCorruptionError``。

    场景：journal 第 2 行损坏但第 3 行合法——说明 committed history 被污染（磁盘错/写竞争），
    绝不静默跳过坏行继续归约（否则状态机基于残缺事件得到错误状态）。"""
    # Arrange：合法 + 中部坏行 + 合法（坏行非末尾）
    path = tmp_path / "j.jsonl"
    _write_raw(path, '{"schema_version": 1, "event_id": "e1", "timestamp": "t", '
                     '"iteration_id": "i", "run_id": "r", "prd_id": "p", '
                     '"event_type": "running", "payload": {}}\n'
                     'THIS_IS_GARBAGE_MIDDLE_LINE\n'
                     '{"schema_version": 1, "event_id": "e3", "timestamp": "t", '
                     '"iteration_id": "i", "run_id": "r", "prd_id": "p", '
                     '"event_type": "agent_finished", "payload": {}}\n')
    # Act / Assert
    with pytest.raises(J.JournalCorruptionError) as ei:
        J.read_events(path)
    assert ei.value.line_number == 2   # 第 2 行（1-based）


def test_read_tolerates_trailing_partial_then_eof(tmp_path):
    """末尾半行后无换行（典型崩溃截断）→ 容忍。与 trailing blank 混合也稳定。"""
    path = tmp_path / "j.jsonl"
    _write_raw(path, '{"schema_version": 1, "event_id": "e1", "timestamp": "t", '
                     '"iteration_id": "i", "run_id": "r", "prd_id": "p", '
                     '"event_type": "running", "payload": {}}\n\n'
                     '{"broken":')   # 末尾半行（无换行）
    got = J.read_events(path)
    assert [e.event_id for e in got] == ["e1"]


def test_validate_journal_reports_tail_truncation(tmp_path):
    """validate_journal 不 raise——返回 CorruptionReport 描述尾部截断（运维可据此决定是否补写）。"""
    path = tmp_path / "j.jsonl"
    J.append_event(path, _ev("e1", "running"))
    with open(path, "a", encoding="utf-8") as f:
        f.write('{"truncated')   # 末尾半行
    report = J.validate_journal(path)
    assert report.tail_truncated is True
    assert report.corrupted_line_numbers == ()
    assert report.events_read == 1


def test_validate_journal_reports_middle_corruption(tmp_path):
    """validate_journal 对中部损坏填 ``corrupted_line_numbers``（不 raise，调用方/reducer 据此落 STATE_CORRUPT）。"""
    path = tmp_path / "j.jsonl"
    _write_raw(path, '{"schema_version": 1, "event_id": "e1", "timestamp": "t", '
                     '"iteration_id": "i", "run_id": "r", "prd_id": "p", '
                     '"event_type": "running", "payload": {}}\n'
                     'GARBAGE\n'
                     '{"schema_version": 1, "event_id": "e3", "timestamp": "t", '
                     '"iteration_id": "i", "run_id": "r", "prd_id": "p", '
                     '"event_type": "agent_finished", "payload": {}}\n')
    report = J.validate_journal(path)
    assert report.corrupted_line_numbers == (2,)
    assert report.is_fail_closed is True


def test_validate_journal_clean_report_on_healthy_journal(tmp_path):
    """健康 journal 的 report：全 False / 空 / events_read 正确。"""
    path = tmp_path / "j.jsonl"
    for i in range(3):
        J.append_event(path, _ev(f"e{i}"))
    report = J.validate_journal(path)
    assert report.tail_truncated is False
    assert report.corrupted_line_numbers == ()
    assert report.is_fail_closed is False
    assert report.events_read == 3


# ════════════════════════════════════════════════════════════════════════
# journal → state 归约（task 2.2 reduction：read + reduce 串起来）
# ════════════════════════════════════════════════════════════════════════
def test_read_then_reduce_reaches_published(tmp_path):
    """read_events + loop_state.reduce 串成「journal → IterationState」真源链路（design 决策#1）。

    模拟完整一次绿路径 dispatch 写的 journal，恢复时读出归约到 PUBLISHED 终态。"""
    # Arrange
    path = tmp_path / "run-1.jsonl"
    for etype, eid in [("planned", "e0"), ("running", "e1"), ("agent_finished", "e2"),
                       ("verifying", "e3"), ("publish_ready", "e4"), ("published", "e5")]:
        J.append_event(path, _ev(eid, etype))
    # Act
    events = J.read_events(path)
    state = L.reduce(events, initial=L.initial_state("r", "p", "i", base="abc1234"))
    # Assert
    assert state.status is L.IterationStatus.PUBLISHED


def test_reduce_over_recovered_truncated_journal_skips_partial(tmp_path):
    """恢复时末尾截断被容忍——归约只基于已提交事件，不被半行干扰（2.3 + 2.2 协同）。"""
    path = tmp_path / "run-1.jsonl"
    J.append_event(path, _ev("e1", "running"))
    J.append_event(path, _ev("e2", "agent_finished"))
    with open(path, "a", encoding="utf-8") as f:
        f.write('{"schema_version": 1, "event_id": "e3", "event_t')   # 截断的 published
    state = L.reduce(J.read_events(path), initial=L.initial_state("r", "p", "i", base="abc"))
    # 截断的 e3 被丢 → status 停在 AGENT_FINISHED（未到 published），恢复正确
    assert state.status is L.IterationStatus.AGENT_FINISHED
