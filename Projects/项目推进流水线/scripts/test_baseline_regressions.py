#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_baseline_regressions.py — task 1.3 关键终态/证据路径 baseline 回归测试。

在 Section 2-7 把 durable runtime 真正接入生产 dispatch 之前，先为四条关键正确性路径补一张回归安全网
（task 1.3）：tests-green/semantic-revise 终态映射、complete malformed journal tail、failed
test-artifact persistence、以及此前未映射的终态类。

两类测试：
  * **GREEN 锁定**：当前已正确的契约（green→published / red→revise、真正截断尾行容忍、
    complete-schema-invalid 末行 fail-closed（task 3.6）、unmapped first-cut 不发事件、artifact 完整性
    fail-closed）——Section 2-7 重构时若回归，立即红。
  * **xfail(strict=True) 标注**（机制保留，task 3.6 落地后当前无在用项）：当前已是缺陷、由后续 task 修正的
    路径——修正后 xpass 触发 strict 失败，强制提醒把标记转成 GREEN 正式纳入保护（闭环，不让缺陷被遗忘）。

零 SDK（纯逻辑层）；AAA；跑：python3 -m pytest scripts/test_baseline_regressions.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import artifact_store as AS  # noqa: E402
import journal as J  # noqa: E402
import loop_runtime as RT  # noqa: E402
import run_daily  # noqa: E402


def _stamp() -> str:
    return "2026-07-22T00:00:00Z"


def _evt_line(event_id: str, event_type: str = "running", payload: dict | None = None) -> str:
    """造一条合法 JournalEvent JSON 行（全必填字段齐）。"""
    return json.dumps({
        "schema_version": 1, "event_id": event_id, "timestamp": "t",
        "iteration_id": "i", "run_id": "r", "prd_id": "p",
        "event_type": event_type, "payload": payload or {},
    }, ensure_ascii=False)


# ════════════════════════════════════════════════════════════════════════════
# tests-green / semantic-revise：_sj_terminal 核心终态映射锁定（防 Section 2-4 重构回归）
# ════════════════════════════════════════════════════════════════════════════
def test_green_pr_maps_to_published_and_red_maps_to_revise(tmp_path):
    """机械测试绿 + pr_open → published；验证红 + interrupted_pr → revise（非 published，防假绿）。

    注：当前 _sj_terminal 仅凭 ``verify.pass`` 判定（机械层）；task 4.1 将增强为同时要求语义
    ``verify_verdict=pass``——此处锁定当前机械契约，4.1 落地后据此扩展。
    """
    # Arrange — green
    sj_green = RT.ShadowJournal(tmp_path / "g.jsonl", "run_1", _stamp, enabled=True)
    green = {"status": "pr_open", "verify": {"pass": True}, "pr_url": "https://gh/p/1"}
    # Arrange — red
    sj_red = RT.ShadowJournal(tmp_path / "r.jsonl", "run_1", _stamp, enabled=True)
    red = {"status": "interrupted_pr", "verify": {"pass": False}, "pr_url": "https://gh/p/2"}

    # Act
    run_daily._sj_terminal(sj_green, green, "it", "prd")
    run_daily._sj_terminal(sj_red, red, "it", "prd")

    # Assert
    ge = J.read_events(tmp_path / "g.jsonl")
    re_ = J.read_events(tmp_path / "r.jsonl")
    assert ge[0].event_type == "published"
    assert re_[0].event_type == "revise"


# ════════════════════════════════════════════════════════════════════════════
# complete malformed journal tail
# ════════════════════════════════════════════════════════════════════════════
def test_complete_schema_invalid_tail_must_fail_closed(tmp_path):
    """spec verified-publication-integrity「Complete malformed journal tail」（task 3.6 已实现）：
    末行是完整 JSON 但 schema 非法（缺必填字段）→ 应标记 corrupt（fail-closed），而非当截断容忍丢弃。
    ``_scan`` 分离 ``JSONDecodeError``（截断，末行容忍）与 schema 构造失败（complete-but-invalid，始终 fail-closed）。"""
    # Arrange — 两行合法 + 末行 complete JSON 但缺 schema_version 等必填字段
    j = tmp_path / "bad.jsonl"
    j.write_text(_evt_line("e1") + "\n" + _evt_line("e2") + "\n"
                 + json.dumps({"event_type": "published"}) + "\n", encoding="utf-8")

    # Act
    report = J.validate_journal(j)

    # Assert — 末行 schema-invalid 应进 corrupted（fail-closed），而非 tail_truncated
    assert report.is_fail_closed


def test_truncated_incomplete_tail_remains_tolerated(tmp_path):
    """真正的不完整尾行（半行 JSON，json.loads 失败）仍容忍丢弃——确保 task 3.6 收紧时只针对
    schema-invalid，不破坏对真实崩溃截断的容忍（spec「tolerate single incomplete trailing record」）。"""
    # Arrange — 两行合法 + 末行被截断的半行
    j = tmp_path / "trunc.jsonl"
    j.write_text(_evt_line("e1") + "\n" + _evt_line("e2") + "\n"
                 + '{"event_type": "publ', encoding="utf-8")

    # Act
    report = J.validate_journal(j)
    events = J.read_events(j)   # 不 raise

    # Assert — 截断容忍：两行合法事件读回，末半行丢弃，非 fail-closed
    assert report.tail_truncated is True
    assert not report.is_fail_closed
    assert len(events) == 2


# ════════════════════════════════════════════════════════════════════════════
# every previously unmapped terminal state（first-cut 不发 journal 终态事件）
# ════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("status", ["planned", "pr_closed", "pr_merged", "sandbox_blocked"])
def test_unmapped_terminal_emits_no_journal_event(tmp_path, status):
    """planned/pr_<other>/sandbox_blocked 当前**不经 ``_sj_terminal`` emit** 终态事件：
      - planned：parity 靠 dispatch_one 不 emit running（task 3.5 移动 running emit 到 skip-dev 之后），
        reduce [planned] 落 PLANNED == legacy planned；``_sj_terminal`` 不额外 emit（planned 已由 dispatch entry emit）。
      - pr_closed/pr_merged：reconcile 罕见态，暂未映射（留驱动阶段）。
      - sandbox_blocked：task 5.2 才引入的 status，前向占位暂不 emit。
    task 3.5 已让 stalled/orphan_deleted 经 ``_sj_terminal`` emit（见 test_shadow_dispatch），故移出此列表。"""
    # Arrange
    sj = RT.ShadowJournal(tmp_path / "u.jsonl", "run_1", _stamp, enabled=True)

    # Act
    run_daily._sj_terminal(sj, {"status": status}, "it", "prd")

    # Assert — 未映射 → 不 emit → 文件未创建
    assert not (tmp_path / "u.jsonl").exists()


# ════════════════════════════════════════════════════════════════════════════
# failed test-artifact persistence：artifact 完整性 fail-closed 基线（task 4.2 前置）
# ════════════════════════════════════════════════════════════════════════════
def test_evidence_artifact_integrity_blocks_tampered_content(tmp_path):
    """evidence 完整性基线：artifact 落盘后 load 必重算 digest 校验；内容被篡改 →
    ArtifactIntegrityError（绝不返回可疑内容）。task 4.2「无法持久化/校验的测试结果不得成 fresh green
    evidence」依赖此 fail-closed 契约。"""
    # Arrange
    root = tmp_path / "art"
    ref = AS.store(root, "plain green test output", kind="test_output", sensitivity="internal")

    # Act — 正常 load 读回并校验通过
    assert AS.load(root, ref).decode("utf-8") == "plain green test output"

    # Assert — 篡改落盘内容后 load 必 fail-closed（digest 不匹配）
    (Path(root) / ref.path).write_bytes(b"TAMPERED")
    with pytest.raises(AS.ArtifactIntegrityError):
        AS.load(root, ref)
