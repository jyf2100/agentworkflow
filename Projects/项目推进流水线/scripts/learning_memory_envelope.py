#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""learning_memory_envelope.py — add-cross-prd-learning-memory Section 4.1 terminal evidence envelope。

spec design 决策#1（**硬约束**）：envelope 由 journal **实际 verifier transition history** 选择，**不是
terminal 状态标签**。同 terminal 标签可经不同路径达到（``blocked_external_state`` 既可 pre-verifier 出现，
也可 post-verifier post-publish reconcile 出现）——journal 中 verifier 事件的存在性决定权威 evidence。

evidence_class 三类（task 4.1 design 表）::

    | journal verifier transition history        | evidence_class              |
    |-------------------------------------------|-----------------------------|
    | 有 verifier pass（terminal=published 或     | VERIFIER_PASS               |
    | post-verifier short-circuit after pass）   |                             |
    |-------------------------------------------|-----------------------------|
    | verifier revise > 0 + 无 pass + terminal   | VERIFIER_REVISE_EXHAUSTED   |
    | != PUBLISHED（推断 revise-exhaustion）      |                             |
    |-------------------------------------------|-----------------------------|
    | 无 verifier 事件（pre-verifier short-       | PRE_VERIFIER_SHORT_CIRCUIT  |
    | circuit: stalled/aborted/sandbox_blocked/  |                             |
    | state_corrupt/pre-verifier external_blocked|                             |
    | /blocked_evidence/test_blocked）           |                             |

**verifier-revise-exhausted 推断**（关键决策）：从 journal verifier revise 事件计数（>0）+ 无 pass + 终端非
PUBLISHED 推断，**不新增 event_type**（保持 reflection 副作用最小，不改 dispatch 主路径）。

**反例覆盖**（task 4.1）：
    * pre-verifier ``blocked_external_state``（journal 无 verifier 事件 → PRE_VERIFIER_SHORT_CIRCUIT）；
    * post-verifier ``blocked_external_state``（journal 有 verifier pass → VERIFIER_PASS，post-publish
      reconcile UNKNOWN 不否定 verifier pass）；
    * ``verifier-revise-exhausted``（revise count > 0 + no pass + terminal != PUBLISHED）；
    * pre/post-verifier ``blocked_evidence``（同终态标签，不同 evidence class）。

**排除 raw secrets + unbounded transcripts**（design 决策#2 + risks「Evidence contains secrets or excessive
transcripts」）：
    * artifact 内容经 ``artifact_store.redact_secrets`` 抹密钥（GitHub PAT/Bearer/token=/basic-auth）；
    * 每个 artifact excerpt 截到 ``MAX_ARTIFACT_EXCERPT_BYTES``（防 unbounded transcripts 进 prompt）；
    * evidence_refs 必带 ``digest``（integrity-checked 前提；spec「readable integrity-checked evidence」）；
    * sanitized_metadata 仅留 schema-constrained 安全字段（run/prd/iteration/project/terminal/evidence_class
      + 三态 external_state_query + revise_count + 各 mechanical reason 字段——不含 raw prompt/transcript）。

**纯 stdlib**（hashlib/json/dataclasses/typing + artifact_store 的 redact_secrets），**零 SDK 导入**——cron
隔离不变；envelope 构造可独立单测；SDK 调用方（``learning_memory_reflection``）负责 reflection 副作用。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, ClassVar, Iterable

from artifact_store import redact_secrets


# ════════════════════════════════════════════════════════════════════════
# evidence class 枚举（design 决策#1 表）
# ════════════════════════════════════════════════════════════════════════
class EvidenceClass(str, Enum):
    """terminal envelope 的 evidence class（由 journal verifier history 决定，非 terminal 标签）。

    * ``VERIFIER_PASS``：journal 有 verifier pass（design 表第 1 行）
    * ``VERIFIER_REVISE_EXHAUSTED``：verifier revise > 0 + 无 pass + terminal != PUBLISHED（推断）
    * ``PRE_VERIFIER_SHORT_CIRCUIT``：journal 无 verifier 事件（design 表第 3 行）
    """
    VERIFIER_PASS = "verifier_pass"
    VERIFIER_REVISE_EXHAUSTED = "verifier_revise_exhausted"
    PRE_VERIFIER_SHORT_CIRCUIT = "pre_verifier_short_circuit"


# ════════════════════════════════════════════════════════════════════════
# bounded excerpt 上限（防 unbounded transcripts 进 SDK prompt）
# ════════════════════════════════════════════════════════════════════════
MAX_ARTIFACT_EXCERPT_BYTES: int = 4096   # 单 artifact excerpt 上限（足够诊断，远低于 token 上限）


# ════════════════════════════════════════════════════════════════════════
# verifier event 识别（journal 真源）
# ════════════════════════════════════════════════════════════════════════
# journal event_type ∈ 此集合 → 视为权威 verifier 事件（判定 verifier_history 用）。
# spec design 决策#1 表：「journal records a verifier event that passed」——权威 verifier 事件 = 带显式
# verdict 的 ``verifier`` 判决观测事件（payload.verdict ∈ pass/revise）或 ``verifier_feedback`` 反馈工件
# 落盘事件（payload.digest/path/size）。
#
# **外部评审 P1 #2 修复**：生产 ``run_daily.py`` 有**两处** verifier 相关 emit：
#   * L1208 ``sj.emit("verifier_feedback", payload={round, digest, path, size})`` —— 反馈**内容**事件，
#     **无 verdict 字段**（工件落盘记录，digest 用于 evidence_refs）；
#   * L1948 ``_sj.emit("verifier", payload={round, verdict})`` —— 判决**观测**事件，**有 verdict 字段**
#     （verifier 的 pass/revise 判决）。
# 修复前只收 ``verifier_feedback`` → ``_has_verifier_pass`` / ``_count_revise_verdicts`` 永远查不到
# verdict（payload 无该字段）→ evidence_class 永远误判为 PRE_VERIFIER_SHORT_CIRCUIT（违反 state machine
# 不变量：published 必经 verifier pass）。
# 现加入 ``verifier`` 让判决观测事件参与 evidence_class 决策；``verifier_feedback`` 仍保留（digest/path
# 用于 evidence_refs 关联——生产 emit 用裸字段，artifact_ref 抽取另见 ``_extract_artifact_refs``）。
#
# ``verifying``/``revise`` 仅是状态迁移（state machine transition），无 verdict 字段——不计入权威
# verifier_history（避免把单纯的状态迁移信号误读为「verifier 已下裁决」）。
_VERIFIER_EVENT_TYPES: frozenset[str] = frozenset({"verifier_feedback", "verifier"})

# verdict 取值（payload.verdict）
_VERDICT_PASS = "pass"
_VERDICT_REVISE = "revise"


# ════════════════════════════════════════════════════════════════════════
# TerminalEnvelope（frozen；envelope 是 reflection 的 sanitized 输入）
# ════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class TerminalEnvelope:
    """terminal PRD 的 curated evidence envelope（design 决策#1 + #2）。

    传给 read-only SDK reflection 调用（``learning_memory_reflection``）作 sanitized 输入：
        * ``evidence_class``：决定 matching evidence 的权威类别（不靠 terminal 标签）；
        * ``verifier_events``：sanitized verifier transition 序列（仅 pass/revise verdict + artifact_ref）；
        * ``evidence_refs``：integrity-checked 工件引用（每条带 ``digest``——spec「readable
          integrity-checked evidence」）；
        * ``evidence_excerpts``：bounded + redacted 内容片段（design「receives sanitized metadata +
          integrity-checked artifact excerpts」）；
        * ``sanitized_metadata``：schema-constrained 安全字段（不含 raw prompt/transcript）。

    所有字段 immutable + JSON-serializable；envelope 是 reflection SDK prompt 的唯一 sanitized 输入。
    """
    __test__: ClassVar[bool] = False

    evidence_class: str
    terminal_status: str
    verifier_events: tuple[dict, ...]
    evidence_refs: tuple[dict, ...]
    evidence_excerpts: tuple[dict, ...]
    sanitized_metadata: dict


# ════════════════════════════════════════════════════════════════════════
# 构造 helper：从 journal events 抽 verifier transition history
# ════════════════════════════════════════════════════════════════════════
def _event_payload(ev: Any) -> dict:
    """健壮取 event.payload（duck-typed：dict 或 None）。"""
    p = getattr(ev, "payload", None)
    return p if isinstance(p, dict) else {}


def _event_type(ev: Any) -> str:
    """健壮取 event.event_type（string）。"""
    et = getattr(ev, "event_type", None)
    return str(et) if et is not None else ""


def _collect_verifier_history(events: Iterable[Any]) -> list[dict]:
    """从 journal events 抽 verifier transition 序列（保留时序；含 verifier/verifier_feedback）。

    输出每条形如 ``{"event_type": ..., "verdict": .../"none", "artifact_ref": {...}}`` —— 仅取结构性
    字段（不收 payload 里的 raw transcript / 错误堆栈）。verdict 在 ``verifier`` 判决事件中显式给
    （生产 L1948 emit），亦可出现在 ``verifier_feedback`` 反馈事件中（旧式/测试用 shape）；``verifying``/
    ``revise`` 是状态迁移事件（无 verdict 字段，记 ``"none"``——且它们不在 ``_VERIFIER_EVENT_TYPES``）。

    **外部评审 P1 #2 修复**：``verifier`` 事件加入 _VERIFIER_EVENT_TYPES 后，本函数从其 payload 抽
    ``verdict``（生产 emit shape ``{round, verdict}``）；``verifier_feedback`` 仍抽 verdict + artifact_ref。
    """
    history: list[dict] = []
    for ev in events:
        etype = _event_type(ev)
        if etype not in _VERIFIER_EVENT_TYPES:
            continue
        payload = _event_payload(ev)
        verdict = "none"
        artifact_ref = None
        # verdict 在两类事件里都可能存在（生产 verifier emit 带 verdict；生产 verifier_feedback emit
        # 无 verdict，但旧式/测试 shape 可能带）。统一从 payload.get("verdict") 抽。
        v = payload.get("verdict")
        if isinstance(v, str):
            verdict = v
        # artifact_ref 仅 verifier_feedback 反馈工件事件可能有（生产 L1208 用裸 digest/path/size——
        # 此处不强收裸字段；旧式 ArtifactRef dict shape 仍兼容）。
        if etype == "verifier_feedback":
            ref = payload.get("artifact_ref") or payload.get("ref")
            if isinstance(ref, dict):
                artifact_ref = ref
        entry = {"event_type": etype, "verdict": verdict}
        if artifact_ref is not None:
            entry["artifact_ref"] = artifact_ref
        history.append(entry)
    return history


def _has_verifier_pass(verifier_history: list[dict], *, terminal_status: str) -> bool:
    """判定 journal 是否记录 verifier pass。

    spec design 决策#1 表第 1 行：「journal records a verifier event that passed (terminal published, or
    post-verifier short-circuit after pass)」。

    pass 信号（任一即可）：
        * 任一 ``_VERIFIER_EVENT_TYPES`` 事件（``verifier`` / ``verifier_feedback``）带
          ``verdict == "pass"``（显式）；
        * terminal == ``published`` **且** verifier_history 非空（state machine 保证：PUBLISHED 前必经
          VERIFYING/REVISE/PUBLISH_READY，故 journal 有 verifier transition）。

    **外部评审 P1 #2 修复**：不再写死 ``event_type == "verifier_feedback"``——生产 ``verifier`` 事件
    才是带 verdict 的判决观测，旧实现因只看 verifier_feedback 永远查不到 verdict==pass。
    """
    for ev in verifier_history:
        if ev.get("verdict") == _VERDICT_PASS:
            return True
    # terminal=PUBLISHED 且 journal 有 verifier transition → state machine 保证发生过 pass
    if terminal_status == "published" and verifier_history:
        return True
    return False


def _count_revise_verdicts(verifier_history: list[dict]) -> int:
    """统计权威 verifier 事件中 ``verdict == "revise"`` 的次数（推断 revise-exhaustion 用）。

    **外部评审 P1 #2 修复**：不再写死 ``event_type == "verifier_feedback"``——``verifier_history``
    已经过 ``_VERIFIER_EVENT_TYPES`` 过滤（只含 ``verifier`` / ``verifier_feedback``），其他状态迁移事件
    早已被排除；本函数计其中 verdict==revise 次数 = verifier 显式判红次数。
    """
    return sum(1 for ev in verifier_history if ev.get("verdict") == _VERDICT_REVISE)


# ════════════════════════════════════════════════════════════════════════
# 构造 helper：evidence_refs + excerpts（integrity-checked + bounded + redacted）
# ════════════════════════════════════════════════════════════════════════
def _extract_artifact_refs(events: Iterable[Any], verifier_history: list[dict]) -> list[dict]:
    """从 journal events + verifier_history 抽全部 artifact_ref（dedup by digest）。

    收录字段（payload 中常见命名兼容）：
        * ``verifier_feedback`` 的 ``artifact_ref``（verifier verdict 工件）；
        * ``verifying`` / ``test_blocked`` / ``test_evidence_ref`` 的 ``test_evidence_ref``（fresh-green 工件）；
        * 任何 event payload 的 ``artifact_ref`` / ``test_evidence_ref`` 字段（ArtifactRef-like dict）。

    每条必带 ``digest``（integrity-checked 前提——无 digest 的 ref 视为可疑，**不收**）。
    """
    seen_digests: set[str] = set()
    refs: list[dict] = []
    # verifier_history 的 artifact_ref（已 sanitized 抽出）
    for ev in verifier_history:
        ref = ev.get("artifact_ref")
        if isinstance(ref, dict) and isinstance(ref.get("digest"), str) and ref["digest"].startswith("sha256:"):
            if ref["digest"] not in seen_digests:
                seen_digests.add(ref["digest"])
                refs.append(ref)
    # 全 events 扫 artifact_ref / test_evidence_ref
    for ev in events:
        payload = _event_payload(ev)
        for key in ("artifact_ref", "test_evidence_ref"):
            ref = payload.get(key)
            if isinstance(ref, dict) and isinstance(ref.get("digest"), str) and ref["digest"].startswith("sha256:"):
                if ref["digest"] not in seen_digests:
                    seen_digests.add(ref["digest"])
                    refs.append(ref)
    return refs


def _build_excerpt(ref: dict, *, loader: Callable[[dict], bytes],
                   max_bytes: int = MAX_ARTIFACT_EXCERPT_BYTES) -> dict:
    """构造单 artifact 的 bounded + redacted excerpt。

    design 决策#2 + risks「Evidence contains secrets or excessive transcripts」：
        * ``redact_secrets`` 抹 GitHub PAT/Bearer/token=/basic-auth（artifact_store 既定规则）；
        * 截到 ``max_bytes``（防 unbounded transcripts 进 SDK prompt）；
        * loader 返回空 → ``missing_content=True``（不静默，但容忍——reflection 端感知）。
    """
    digest = ref.get("digest", "")
    kind = ref.get("kind", "")
    try:
        raw = loader(ref)
    except Exception:
        raw = b""
    if not raw:
        return {
            "digest": digest, "kind": kind, "content": b"", "truncated": False,
            "missing_content": True, "size": 0,
        }
    # 敏感分层：先抹密钥（artifact_store 既定规则）
    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception:
        text = ""
    redacted = redact_secrets(text)
    content = redacted.encode("utf-8", errors="replace")
    truncated = len(content) > max_bytes
    if truncated:
        content = content[:max_bytes]
    return {
        "digest": digest, "kind": kind, "content": content,
        "truncated": truncated, "missing_content": False, "size": len(raw),
    }


# ════════════════════════════════════════════════════════════════════════
# 构造 helper：sanitized_metadata（schema-constrained 安全字段）
# ════════════════════════════════════════════════════════════════════════
def _build_sanitized_metadata(*, run_id: str, prd_id: str, iteration_id: str, project_id: str,
                              terminal_status: str, evidence_class: str,
                              events: Iterable[Any], verifier_history: list[dict]) -> dict:
    """构造 sanitized_metadata——仅 schema-constrained 安全字段（不收 raw prompt/transcript）。

    字段：
        * 基本 IDs（run/prd/iteration/project）+ terminal_status + evidence_class（可定位）；
        * ``external_state_query``：三态（FOUND/NOT_FOUND/UNKNOWN）—— pre-verifier short-circuit 的
          matching mechanical evidence（design 表第 3 行）；
        * ``revise_count``：verifier revise 次数（revise-exhausted audit 用）；
        * ``gate_reason`` / ``skip_reason`` / ``sandbox_violation``：终态 payload 的 reason 字段（机械可重放）。

    所有字段经 ``redact_secrets`` 抹密钥（防 external_state raw_error 含 token）。
    """
    meta: dict = {
        "run_id": str(run_id),
        "prd_id": str(prd_id),
        "iteration_id": str(iteration_id),
        "project_id": str(project_id),
        "terminal_status": str(terminal_status),
        "evidence_class": str(evidence_class),
    }
    # 扫 events 抽三态 external_state_query / revise_count / 终态 reason 字段
    revise_count = _count_revise_verdicts(verifier_history)
    if revise_count > 0:
        meta["revise_count"] = revise_count
    # 最后一个 external_blocked 事件的 query_state（三态真源——pre-verifier short-circuit matching evidence）
    last_query_state = None
    last_external_reason = None
    last_terminal_reason = None
    last_sandbox_violation = None
    for ev in events:
        etype = _event_type(ev)
        payload = _event_payload(ev)
        if etype == "external_blocked":
            qs = payload.get("query_state")
            if isinstance(qs, str):
                last_query_state = qs
            r = payload.get("reason")
            if isinstance(r, str):
                last_external_reason = r
        if etype in ("stalled", "aborted", "failed", "blocked_evidence", "sandbox_blocked",
                     "state_corrupt", "test_blocked"):
            r = payload.get("reason") or payload.get("skip_reason")
            if isinstance(r, str):
                last_terminal_reason = r
            sv = payload.get("violation") or payload.get("sandbox_violation")
            if isinstance(sv, str):
                last_sandbox_violation = sv
    if last_query_state is not None:
        meta["external_state_query"] = last_query_state
    if last_external_reason is not None:
        meta["external_block_reason"] = redact_secrets(last_external_reason)
    if last_terminal_reason is not None:
        meta["terminal_reason"] = redact_secrets(last_terminal_reason)
    if last_sandbox_violation is not None:
        meta["sandbox_violation"] = redact_secrets(last_sandbox_violation)
    # 任务 4.1 反例：raw_error / error / stderr 等 free-form 错误字段也抹密钥（external_state raw_error
    # 可能含 token=... —— spec risks「Evidence contains secrets」）。
    for ev in events:
        payload = _event_payload(ev)
        for key in ("raw_error", "error", "stderr"):
            v = payload.get(key)
            if isinstance(v, str) and v:
                meta.setdefault("terminal_raw_error", redact_secrets(v))
                break
    return meta


# ════════════════════════════════════════════════════════════════════════
# 主入口：build_terminal_envelope
# ════════════════════════════════════════════════════════════════════════
def build_terminal_envelope(*, terminal_status: str,
                            events: Iterable[Any],
                            artifact_loader: Callable[[dict], bytes],
                            run_id: str, prd_id: str, iteration_id: str, project_id: str,
                            max_artifact_bytes: int = MAX_ARTIFACT_EXCERPT_BYTES) -> TerminalEnvelope:
    """task 4.1：构造 terminal evidence envelope（design 决策#1 + #2 硬约束）。

    envelope 由 journal 实际 verifier transition history 选 evidence_class（**不靠 terminal 标签**）：
        1. 抽 verifier_history（``verifier_feedback``/``verifying``/``revise`` 事件，保时序）；
        2. 判定 evidence_class：
            * ``_has_verifier_pass`` → ``VERIFIER_PASS``（含 published + post-verifier short-circuit after pass）；
            * revise_count > 0 + terminal != PUBLISHED + 无 pass → ``VERIFIER_REVISE_EXHAUSTED``（推断）；
            * 否则（无 verifier 事件 / verifier 事件无 verdict） → ``PRE_VERIFIER_SHORT_CIRCUIT``；
        3. 抽 evidence_refs（integrity-checked，每条带 ``digest``）；
        4. 构造 excerpts（``redact_secrets`` + 截 ``max_artifact_bytes``）；
        5. 构造 sanitized_metadata（schema-constrained 安全字段 + 三态 external_state_query + revise_count）。

    Args:
        terminal_status: ``IterationStatus.value``（如 ``"published"`` / ``"failed"`` / ``"external_blocked"``）。
        events: journal 事件序列（鸭子类型：``event_type`` + ``payload``——生产 ``JournalEvent`` 满足）。
        artifact_loader: callable，传 ArtifactRef dict → 返回 bytes 内容（生产用 ``artifact_store.load``；
            测试用 mock）。
        run_id/prd_id/iteration_id/project_id: 关联 IDs（envelope metadata）。
        max_artifact_bytes: 单 artifact excerpt 上限（默认 ``MAX_ARTIFACT_EXCERPT_BYTES``）。

    Returns:
        immutable ``TerminalEnvelope``（reflection SDK 调用的 sanitized 输入）。
    """
    events_list = list(events)
    verifier_history = _collect_verifier_history(events_list)

    # evidence_class 决策（design 决策#1 表）
    if _has_verifier_pass(verifier_history, terminal_status=terminal_status):
        evidence_class = EvidenceClass.VERIFIER_PASS.value
    elif (verifier_history and
          _count_revise_verdicts(verifier_history) > 0 and
          terminal_status != "published"):
        evidence_class = EvidenceClass.VERIFIER_REVISE_EXHAUSTED.value
    else:
        evidence_class = EvidenceClass.PRE_VERIFIER_SHORT_CIRCUIT.value

    # evidence_refs + excerpts（integrity-checked + bounded + redacted）
    refs = _extract_artifact_refs(events_list, verifier_history)
    excerpts = tuple(
        _build_excerpt(ref, loader=artifact_loader, max_bytes=max_artifact_bytes) for ref in refs)

    # sanitized_metadata（schema-constrained 安全字段）
    sanitized_metadata = _build_sanitized_metadata(
        run_id=run_id, prd_id=prd_id, iteration_id=iteration_id, project_id=project_id,
        terminal_status=terminal_status, evidence_class=evidence_class,
        events=events_list, verifier_history=verifier_history)

    return TerminalEnvelope(
        evidence_class=evidence_class,
        terminal_status=terminal_status,
        verifier_events=tuple(verifier_history),
        evidence_refs=tuple(refs),
        evidence_excerpts=excerpts,
        sanitized_metadata=sanitized_metadata,
    )
