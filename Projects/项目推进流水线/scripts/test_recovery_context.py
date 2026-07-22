#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_recovery_context.py — task 5.4 evidence-derived recovery context 单测。

覆盖：immutable PRD 解析（objective/acceptance，含 frontmatter 与纯 body）、status→下一步映射、
从 journal events 健壮抽取 verifier_feedback / artifact path、session_meta 失败摘要注入。
AAA；模块零 SDK。跑：python3 -m pytest scripts/test_recovery_context.py -q
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import recovery_context as RC  # noqa: E402
from session_meta import ExceptionClass as EC, ResultSubtype, SessionMeta  # noqa: E402


@dataclass
class _Ev:
    """journal event 鸭子类型（event_type + payload）。"""
    event_type: str
    payload: dict


# ─── immutable PRD 解析 ──────────────────────────────────────────────────
def test_parse_prd_frontmatter_name_and_acceptance_section():
    prd = """---
name: 给 pa 加崩溃恢复
source_path: Knowledge/微信/x.md
---

# 给 pa 加崩溃恢复

正文描述。

## 验收标准
- 崩溃后 journal 能重建状态
- 不重复开 PR
- 全套测试绿
"""
    objective, acceptance = RC.parse_prd(prd)
    assert objective == "给 pa 加崩溃恢复"
    assert len(acceptance) == 3
    assert "不重复开 PR" in acceptance


def test_parse_prd_body_only_no_frontmatter():
    prd = "# 纯 body 标题\n\n描述。\n\n## Acceptance Criteria\n- 条件一\n- 条件二\n"
    objective, acceptance = RC.parse_prd(prd)
    assert objective == "纯 body 标题"
    assert acceptance == ("条件一", "条件二")


def test_parse_prd_empty_returns_empty():
    objective, acceptance = RC.parse_prd("")
    assert objective == "" and acceptance == ()


def test_parse_prd_acceptance_section_stops_at_next_header():
    """acceptance 节遇到下一个 ## 标题即截断，不混入下一节。"""
    prd = ("## 验收标准\n- a\n- b\n\n## 设计\n- 不该进 acceptance\n")
    _, acceptance = RC.parse_prd(prd)
    assert acceptance == ("a", "b")


# ─── status → 下一步建议 ─────────────────────────────────────────────────
def test_suggested_next_step_known_statuses():
    assert "fix or add tests" in RC.suggested_next_step("test_blocked")
    assert "reconcile" in RC.suggested_next_step("external_blocked")
    assert "publish" in RC.suggested_next_step("publish_ready").lower()
    assert "no further action" in RC.suggested_next_step("published")
    assert "operator" in RC.suggested_next_step("state_corrupt")


def test_suggested_next_step_blocked_evidence_is_operator_triage():
    """task 4.3：blocked_evidence（green test evidence artifact 无法持久化/校验）→ operator triage，
    与 state_corrupt 同属完整性阻塞（不自动 retry，需运维介入；spec verified-publication
    「Test artifact write fails」integrity-block reason）。"""
    nxt = RC.suggested_next_step("blocked_evidence")
    assert "operator" in nxt
    assert "evidence" in nxt


def test_suggested_next_step_unknown_falls_back_to_resume():
    assert "resume" in RC.suggested_next_step("weird_status")


# ─── build_recovery_context：从 events 抽 verifier feedback + artifacts ─────
def test_build_context_extracts_last_verifier_feedback_path():
    events = [
        _Ev("agent_finished", {"path": "art/diff/aaa"}),
        _Ev("verifier_feedback", {"path": "art/feedback/bbb"}),
        _Ev("verifier_feedback", {"path": "art/feedback/ccc"}),   # 最后一条
    ]
    ctx = RC.build_recovery_context(iteration_id="i", prd_id="p", status_value="revise",
                                    prd_content="# 目标\n\n## 验收标准\n- 条件\n", events=events)
    assert ctx.last_verifier_feedback_path == "art/feedback/ccc"   # 取最后一条
    assert "art/feedback/bbb" in ctx.last_artifact_paths          # 全部 artifact path 收集
    assert "art/diff/aaa" in ctx.last_artifact_paths
    assert ctx.objective == "目标" and ctx.acceptance_criteria == ("条件",)
    assert ctx.suggested_next_step and "verifier feedback" in ctx.suggested_next_step


def test_build_context_dedups_artifact_paths_preserving_order():
    events = [
        _Ev("test", {"path": "art/test/x"}),
        _Ev("agent_finished", {"path": "art/test/x"}),   # 重复 path
        _Ev("verifier_feedback", {"path": "art/fb/y"}),
    ]
    ctx = RC.build_recovery_context(iteration_id="i", prd_id="p", status_value="verifying",
                                    prd_content="# T", events=events)
    assert ctx.last_artifact_paths == ("art/test/x", "art/fb/y")   # 去重保序


def test_build_context_handles_nested_artifact_ref_payload():
    """payload 里 artifact ref 是嵌套 dict（ArtifactRef-like）→ 也能抽 path。"""
    events = [_Ev("test", {"artifact_ref": {"path": "art/nested/z", "digest": "abc"}})]
    ctx = RC.build_recovery_context(iteration_id="i", prd_id="p", status_value="running",
                                    prd_content="# T", events=events)
    assert ctx.last_artifact_paths == ("art/nested/z",)


def test_build_context_injects_session_failure_summary():
    """session_meta 的 exception 分类/消息注入 recovery context（喂诊断）。"""
    sm = SessionMeta(iteration_id="i", session_id="s", result_subtype=ResultSubtype.ERROR,
                     exception_class=EC.TRANSIENT, exception_message="connection reset")
    ctx = RC.build_recovery_context(iteration_id="i", prd_id="p", status_value="failed",
                                    prd_content="# T", events=[], session_meta=sm)
    assert ctx.last_exception_class == "transient"
    assert ctx.last_failure_summary == "connection reset"


def test_build_context_no_artifacts_when_events_empty():
    ctx = RC.build_recovery_context(iteration_id="i", prd_id="p", status_value="planned",
                                    prd_content="# T", events=[])
    assert ctx.last_artifact_paths == ()
    assert ctx.last_verifier_feedback_path is None
