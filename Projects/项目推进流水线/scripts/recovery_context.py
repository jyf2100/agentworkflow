#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""recovery_context.py — task 5.4 evidence-derived recovery context。

从 **immutable PRD 内容** + **journal artifacts** 派生恢复上下文（design 决策#4 PreCompact
recovery snapshot 的精神：目标 / 验收 / 最后 diff / 最后 test / 失败 / 下一步）。

关键约束（spec「Immutable PRD source」）：PRD 文件保持不可变需求源；verify feedback 与执行
进度记在 journal，**绝不**追加回 PRD。本模块只**读** PRD（抽目标/验收）+ 读 journal events
（抽最后 verifier_feedback / artifact refs），不改任何文件。

喂给 retry 驱动器（task 5.5 recovery driver）：决定 retry mode 后，用 recovery context 构造
下一轮 iteration 的输入（new/fork session 都从同一 immutable 需求 + 最新证据出发）。
纯函数、纯 stdlib（cron 隔离友好）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# ─── immutable PRD 解析（只读，绝不改 PRD 文件；spec「Immutable PRD source」）────────
_FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.S)
_FM_NAME_RE = re.compile(r"(?im)^(?:name|title|objective|desc|description):\s*(.+?)\s*$")
_ACC_HEADER_RE = re.compile(r"(?im)^#{1,6}\s*(验收标准|acceptance(?:\s*criteria)?|验收)\b")
_LIST_RE = re.compile(r"(?m)^\s{0,3}[-*]\s+(.+?)\s*$")


def parse_prd(content: str) -> tuple[str, tuple[str, ...]]:
    """从 immutable PRD markdown 抽 (objective, acceptance_criteria)。

    objective：frontmatter 的 name/title/objective，兜底 body 第一 ``#`` 标题，再兜底首段。
    acceptance：``## 验收标准`` / ``## Acceptance Criteria`` 节下的列表项。纯文本解析，不引 yaml。"""
    objective = ""
    acceptance: list[str] = []
    fm_match = _FM_RE.match(content or "")
    body = content or ""
    if fm_match:
        fm, body = fm_match.group(1), fm_match.group(2)
        m = _FM_NAME_RE.search(fm)
        if m:
            objective = m.group(1).strip().strip("\"'")
    if not objective:
        m = re.search(r"(?m)^#\s+(.+?)\s*$", body)
        objective = m.group(1).strip() if m else (body.strip().splitlines()[:1] or [""])[0].strip()
    # acceptance 节
    am = _ACC_HEADER_RE.search(body)
    if am:
        section = body[am.end():]
        # 截到下一个 ## 标题
        nxt = re.search(r"(?m)^#{1,6}\s+\S", section)
        section = section[:nxt.start()] if nxt else section
        acceptance = [item.strip() for item in _LIST_RE.findall(section)]
        if not acceptance:   # 节里有内容但非列表 → 取首行非空
            lines = [ln.strip() for ln in section.splitlines() if ln.strip()]
            acceptance = lines[:8]
    return objective, tuple(acceptance)


# ─── status → 下一步建议（喂 recovery driver 构造下一 iteration 输入）─────────────
_NEXT_STEP = {
    "planned": "resume: invoke development agent for the planned PRD (reconcile side effects first)",
    "running": "resume: continue the interrupted development session",
    "agent_finished": "advance: run independent tests + verifier gate (SDK success ≠ published)",
    "test_blocked": "retry: fix or add tests blocking the green-test gate, then re-run",
    "verifying": "await: outer verifier decision pending",
    "revise": "retry: apply last verifier feedback artifact and re-run the agent",
    "external_blocked": "reconcile: resolve external state of truth before retry (does not consume retry budget)",
    "publish_ready": "publish: reconcile PR idempotency key then create the pull request",
    "published": "delivered: exactly-once terminal state; no further action",
    "aborted": "halt: iteration aborted; diagnose skip reason before re-planning",
    "failed": "diagnose: iteration failed; consider new session if failure repeats",
    "state_corrupt": "operator: journal corruption detected; manual recovery required",
    "blocked_evidence": "operator: green-test evidence artifact could not be persisted (evidence integrity block); manual recovery required",
}


def suggested_next_step(status_value: str) -> str:
    return _NEXT_STEP.get(status_value, f"resume from current state ({status_value})")


@dataclass(frozen=True)
class RecoveryContext:
    """retry 驱动器构造下一 iteration 输入所需的恢复上下文。"""
    iteration_id: str
    prd_id: str
    status: str
    objective: str
    acceptance_criteria: tuple[str, ...] = ()
    last_verifier_feedback_path: str | None = None
    last_artifact_paths: tuple[str, ...] = ()
    last_exception_class: str | None = None
    last_failure_summary: str | None = None
    suggested_next_step: str = ""


def _extract_artifact_path(payload: dict) -> str | None:
    """从 event payload 健壮抽取 artifact path（字段名兼容 path/artifact_path/ref/path）。"""
    if not isinstance(payload, dict):
        return None
    for key in ("path", "artifact_path", "ref", "artifact_ref"):
        v = payload.get(key)
        if isinstance(v, str) and v:
            return v
        if isinstance(v, dict):   # 嵌套 ArtifactRef-like
            inner = v.get("path")
            if isinstance(inner, str) and inner:
                return inner
    return None


def build_recovery_context(*, iteration_id: str, prd_id: str, status_value: str,
                           prd_content: str, events,
                           session_meta=None) -> RecoveryContext:
    """从 immutable PRD + journal events 派生 RecoveryContext（纯函数，只读）。

    ``events``：journal 事件序列（鸭子类型，需有 ``event_type`` 与 ``payload``）。
    ``session_meta``：可选 SessionMeta，提供最后失败分类（喂 suggested_next_step 诊断）。"""
    objective, acceptance = parse_prd(prd_content)
    last_feedback: str | None = None
    artifact_paths: list[str] = []
    for ev in events or []:
        etype = getattr(ev, "event_type", None) or ""
        payload = getattr(ev, "payload", None) or {}
        path = _extract_artifact_path(payload)
        if path:
            artifact_paths.append(path)
            if etype == "verifier_feedback":
                last_feedback = path
    # 去重保序（同一 artifact 可能被多事件引用）
    seen: set[str] = set()
    uniq_paths = [p for p in artifact_paths if not (p in seen or seen.add(p))]
    last_exc = None
    last_fail = None
    if session_meta is not None:
        last_exc = session_meta.exception_class.value
        if session_meta.exception_message:
            last_fail = session_meta.exception_message
        elif session_meta.result_subtype and session_meta.result_subtype.value != "success":
            last_fail = f"SDK result subtype={session_meta.result_subtype.value}"
    return RecoveryContext(
        iteration_id=iteration_id,
        prd_id=prd_id,
        status=status_value,
        objective=objective,
        acceptance_criteria=acceptance,
        last_verifier_feedback_path=last_feedback,
        last_artifact_paths=tuple(uniq_paths),
        last_exception_class=last_exc,
        last_failure_summary=last_fail,
        suggested_next_step=suggested_next_step(status_value),
    )
