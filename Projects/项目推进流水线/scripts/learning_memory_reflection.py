#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""learning_memory_reflection.py — add-cross-prd-learning-memory Section 4.2-4.5 terminal reflection 实现。

spec design 决策#2（**read-only SDK**）+ 决策#7（**fail-open delivery / fail-closed memory**）的硬约束实现：

    * task 4.2：独立 bounded read-only SDK reflection 调用（参照 ``runtime_evidence._run_scenario_query``
      控制面 SDK 先例 + ``dev-agent.py`` 的 ``tools=`` 硬白名单）；
    * task 4.3：仅 terminal 后调用（``loop_state.is_terminal`` 守门，反例：Stop hook 重复触发 / 中间 retry
      iteration 不生成 lesson）；
    * task 4.4：valid full output → sanitized content-addressed artifact（REFLECTION kind）+ append candidates
      （带 evidence_refs）；
    * task 4.5：timeout / SDK error / invalid JSON / schema rejection / persist failure / evidence history
      mismatch → ``learning_memory_degraded`` side-channel 记录，**fail-open for delivery**（绝不改
      test/verify/retry/publication/terminal outcome）+ **fail-closed for memory**（绝不污染 catalog）。

**SDK 钉版约束**（CLAUDE.md）：``>=0.2.121,<0.2.123``——0.2.123 起 ``can_use_tool`` 回调要求 streaming 模式，
与本执行器的 string-prompt ``query()`` 冲突。本模块用同样的 string-prompt ``query()`` 模式（不绑 streaming）。

**SDK 延迟 import 模式**（参照 ``hook_bridge.py``）：顶层不触 ``claude_agent_sdk``，仅 ``_default_sdk_query``
函数内延迟 import——核心层 SDK-free + cron 隔离 + mock-SDK 可测。测试通过 ``sdk_query_fn`` 注入替身，
不触达真实 SDK（参照 ``conftest.py`` mock-SDK fixture 既定模式）。

**模型约定**（CLAUDE.md）：``ClaudeAgentOptions(tools=["Read","Grep"], ...)`` **不传 model 参数**——走 roc
LiteLLM 代理默认 ``glm-5.2``。**切勿传裸 Anthropic model id**（会被代理拒绝）。

**envelope 排除 raw secrets + unbounded transcripts**（design 决策#2 + risks）：envelope 由
``learning_memory_envelope.build_terminal_envelope`` 构造时已 ``redact_secrets`` + 截 ``MAX_ARTIFACT_EXCERPT_BYTES``；
本模块不重新引入 raw 内容（prompt 仅渲染 envelope 的 sanitized 字段）。

**degraded side-channel**（task 4.5 / design 决策#7）：degraded record 走
``.project-auto/state/lessons/degraded/<project>.jsonl``——**不耦合 journal 主路径**（journal 由 coordinator
own），**不污染 catalog events**（catalog 经 ``LessonLifecycleEvent`` 受控词表）。每行 = 一个合法 JSON +
``\\n``（参照 ``learning_memory_store._atomic_append_line`` 既定模式：``flock`` + ``O_APPEND`` + ``fsync``）。
"""
from __future__ import annotations

import dataclasses
import fcntl
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, ClassVar

import learning_memory_schema as LM
import learning_memory_envelope as ENV
from loop_state import is_terminal, IterationStatus, ArtifactKind, Sensitivity


# ════════════════════════════════════════════════════════════════════════
# 默认配置（bounded）
# ════════════════════════════════════════════════════════════════════════
DEFAULT_REFLECTION_TIMEOUT_SECONDS: float = 30.0   # asyncio.wait_for 硬超时（max_turns 被 bypass 故硬限）
DEFAULT_REFLECTION_MAX_TURNS: int = 6              # bounded turn budget（spec「bounded turn/time budget」）
DEFAULT_REFLECTION_MAX_BUDGET_USD: float = 0.05    # bounded cost ceiling（spec open question 实测前的保守值）

# read-only tools 白名单（spec design 决策#2「no mutable tools」；dev-agent.py 既定模式）
_READONLY_TOOLS: tuple[str, ...] = ("Read", "Grep")

# degraded side-channel record schema 版本
DEGRADED_SCHEMA_VERSION: int = 1


# ════════════════════════════════════════════════════════════════════════
# ReflectionResult（reflection 的 immutable 结果；fail-open by construction）
# ════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class ReflectionResult:
    """reflection 调用的 immutable 结果（design 决策#7 fail-open for delivery）。

    字段集**只**暴露 reflection 自身产物——**无** retry/abort/publish/terminal mutation 入口
    （fail-open by construction：调用方无法用此结果改 terminal outcome）。

    Attributes:
        outcome: ``"ok"`` / ``"degraded"``。
        degraded_class: ``None``（ok）或 ``"not_terminal"`` / ``"timeout"`` / ``"sdk_error"`` /
            ``"invalid_json"`` / ``"schema_reject"`` / ``"persist_failure"`` / ``"evidence_history_mismatch"``。
        degraded_reason: 人类可读的简短原因（截 200 字防长 stack 泄密钥）。
        candidates: schema-valid LessonCandidate 元组（degraded 时空）。
        reflection_artifact_ref: 持久化的 REFLECTION artifact ``ArtifactRef`` dict（degraded 时 None）。
        evidence_class: 从 envelope 捕获的 evidence_class（audit 用）。
    """
    __test__: ClassVar[bool] = False

    outcome: str
    degraded_class: str | None
    degraded_reason: str | None
    candidates: tuple[LM.LessonCandidate, ...]
    reflection_artifact_ref: dict | None
    evidence_class: str


# ════════════════════════════════════════════════════════════════════════
# SDK 调用：默认实现（延迟 import 保 cron 隔离 + mock-SDK 可测）
# ════════════════════════════════════════════════════════════════════════
def _default_sdk_query(prompt: str, options: dict) -> str:
    """生产默认 SDK query：real ``claude_agent_sdk.query`` + ``asyncio.wait_for`` 硬超时。

    spec design 决策#2「separate read-only SDK synthesis call」+ CLAUDE.md SDK 钉版约束（>=0.2.121,<0.2.123）。
    参照 ``runtime_evidence._run_scenario_query`` 控制面 SDK 先例（string-prompt ``query()``，非 streaming）。

    **延迟 import**（参照 ``hook_bridge.py``）：核心层 SDK-free + cron 隔离 + mock-SDK 可测。
    **model 省略**：走 roc LiteLLM 代理默认 ``glm-5.2``（**切勿传裸 Anthropic model id**——会被代理拒绝）。
    **tools= 硬白名单只读**（Read/Grep，无 Write/Edit/Bash/Mutable）。
    **asyncio.wait_for 硬超时**：SDK 0.2.121 的 max_turns/max_budget 被 bypass（dev-agent.py L420 既证），
    故用 ``wait_for`` 硬限（spec open question「What bounded timeout」实测前的保守实现）。
    **string-prompt query()**：不 resume，不 fork，不 overwrite 主 dev session metadata。
    """
    import asyncio
    import claude_agent_sdk as CAS

    timeout = float(options.get("timeout_seconds", DEFAULT_REFLECTION_TIMEOUT_SECONDS))

    async def _run() -> str:
        # ClaudeAgentOptions：tools 硬白名单只读；model 刻意不传；max_turns/max_budget_usd 是软上限
        # （SDK 0.2.121 被 bypass，故外层 asyncio.wait_for 是真硬限）。
        options_kwargs: dict = {
            "tools": list(options.get("tools", _READONLY_TOOLS)),
            "max_turns": options.get("max_turns", DEFAULT_REFLECTION_MAX_TURNS),
            "max_budget_usd": options.get("max_budget_usd", DEFAULT_REFLECTION_MAX_BUDGET_USD),
            "permission_mode": options.get("permission_mode", "default"),
        }
        cwd = options.get("cwd")
        if cwd:
            options_kwargs["cwd"] = cwd
        # 不传 model（roc LiteLLM 代理默认 glm-5.2；裸 Anthropic id 会被代理拒）
        # 不传 resume / fork_session（不污染主 dev session）
        sdk_options = CAS.ClaudeAgentOptions(**options_kwargs)
        result_msg = None
        async for msg in CAS.query(prompt=prompt, options=sdk_options):
            if isinstance(msg, CAS.ResultMessage):
                result_msg = msg
        if result_msg is None:
            raise RuntimeError("SDK returned no ResultMessage")
        # 抽 ResultMessage 文本：读 .result（string）。
        # SDK 0.2.121 契约（pa 钉版 >=0.2.121,<0.2.123）：ResultMessage 字段 =
        # ['subtype','duration_ms','duration_api_ms','is_error','num_turns','session_id',
        #  'stop_reason','total_cost_usd','usage','result','structured_output','model_usage',
        #  'permission_denials','deferred_tool_use','errors','api_error_status','uuid']
        # —— **无 content 字段**（旧实现 getattr(result_msg, "content", []) 永远返回 [] → 吞文本 →
        # reflection 必然 degraded）。result 是 string（不是 content blocks list），直接用。
        # fail-open：result 缺失/非 str → 返回空串（上层 json.loads 触 invalid_json degraded，
        # 不改 terminal outcome，design 决策#7 fail-open for delivery 不变量保持）。
        result_text = getattr(result_msg, "result", None)
        return result_text if isinstance(result_text, str) else ""

    # asyncio.wait_for 是硬限（spec「bounded time budget」）；SDK 内部 max_turns/max_budget 不可靠
    return asyncio.run(_with_timeout(_run(), timeout))


async def _with_timeout(coro, timeout: float):
    """包装 asyncio.wait_for——单独函数便于异常分类（TimeoutError → degraded{class:timeout}）。"""
    import asyncio
    return await asyncio.wait_for(coro, timeout=timeout)


# ════════════════════════════════════════════════════════════════════════
# SDK prompt 渲染（envelope → sanitized JSON prompt）
# ════════════════════════════════════════════════════════════════════════
def _render_sdk_prompt(envelope: ENV.TerminalEnvelope, *, project_id: str, prd_id: str) -> str:
    """渲染 SDK prompt（bounded + sanitized JSON 输入；不收 raw prompt/transcript）。

    spec design 决策#2：「receives only sanitized metadata and integrity-checked artifact excerpts needed
    for diagnosis」。本函数把 envelope 的 sanitized 字段渲染成结构化 JSON——SDK 收到的是结构化输入，
    不是自由 narrative。

    prompt 三段：
        1. system instruction（bounded JSON schema 描述 + 受控枚举）；
        2. envelope metadata + evidence_class + verifier_events（structural）；
        3. evidence_excerpts（已 redact + 戌 bound）。
    """
    instruction = (
        "You are a control-plane learning-memory reflection agent. Read the sanitized terminal evidence "
        "and propose at most 2 reusable lesson candidates that would apply to *future* PRDs in this project. "
        "Respond with strict JSON: {\"candidates\": [...], \"audit_summary\": \"...\"}. "
        "Each candidate MUST include: phase, failure_class, corrective_action_class, applies_when_tags, "
        "corrective_action (executable step, <=500 chars), pattern_description (audit only, <=1000 chars), "
        "applicability_when (reusable trigger), non_applicability_when (boundary), evidence_refs (list of "
        "{\"digest\": \"sha256:...\"} drawn from the provided excerpts), source_outcome, confidence (0..1). "
        "Controlled vocabularies: phase=implement|verify|publish|post_terminal; "
        "failure_class=verifier_invariant_violation|gate_blocked|stalled|external_state_unknown|"
        "sandbox_violation|abort|state_corrupt; "
        "corrective_action_class=add_test|fix_pattern|guard_boundary|config_change; "
        "applies_when_tags=python|typescript|golang|ci_gate|test_infra|dependency_mgmt. "
        "Do NOT propose a candidate that cites evidence not present in the envelope's evidence_class. "
        "Do NOT output pattern_key/equivalence_key/promotion_count/storage_path (system fields, redacted)."
    )
    # envelope metadata + verifier_events（structural，已 sanitized）
    excerpts_payload = [
        {"digest": ex["digest"], "kind": ex["kind"],
         "content": ex["content"].decode("utf-8", errors="replace") if isinstance(ex.get("content"), (bytes, bytearray)) else str(ex.get("content", "")),
         "truncated": ex.get("truncated", False),
         "missing_content": ex.get("missing_content", False)}
        for ex in envelope.evidence_excerpts
    ]
    full_payload = {
        "instruction": instruction,
        "metadata": envelope.sanitized_metadata,
        "verifier_events": list(envelope.verifier_events),
        "evidence_excerpts": excerpts_payload,
        "context": {"project_id": project_id, "prd_id": prd_id},
    }
    return json.dumps(full_payload, ensure_ascii=False)


# ════════════════════════════════════════════════════════════════════════
# evidence history mismatch 检测（design 决策#1）
# ════════════════════════════════════════════════════════════════════════
# failure_class → 必需的 envelope evidence_class（design 决策#1 表 + LearningMemorySchema 受控枚举）
_FAILURE_CLASS_REQUIRES_VERIFIER: frozenset[str] = frozenset({
    LM.FailureClass.VERIFIER_INVARIANT_VIOLATION.value,
})


def _detect_evidence_history_mismatch(envelope: ENV.TerminalEnvelope,
                                      candidates: list[LM.LessonCandidate]) -> str | None:
    """检测 candidate 是否与 envelope 的 evidence_class 匹配（design 决策#1）。

    spec design 决策#1：「A candidate whose cited evidence does not match the journal's actual verifier
    transition history is rejected with a ``learning_memory_degraded`` event of class
    ``evidence_history_mismatch``」。

    mismatch 检测规则（envelope.evidence_class 是真源——由 journal 决定）：
        * ``PRE_VERIFIER_SHORT_CIRCUIT`` envelope + 任一 candidate ``failure_class=verifier_invariant_violation``
          → mismatch（pre-verifier 终态却引用 verifier evidence）；
        * ``VERIFIER_PASS`` / ``VERIFIER_REVISE_EXHAUSTED`` envelope + candidate ``evidence_refs`` 引用
          envelope 之外的 digest → mismatch（candidate 编造未在 envelope 出现的 verifier evidence）。
    """
    env_class = envelope.evidence_class
    # 规则 1：pre-verifier envelope + verifier-evidence candidate → mismatch
    if env_class == ENV.EvidenceClass.PRE_VERIFIER_SHORT_CIRCUIT.value:
        for cand in candidates:
            if cand.failure_class.value in _FAILURE_CLASS_REQUIRES_VERIFIER:
                return ("pre-verifier short-circuit envelope cannot host "
                        f"failure_class={cand.failure_class.value}（verifier evidence 未在 journal）")
    # 规则 2：candidate 引用 envelope 外的 digest → mismatch（编造证据）
    envelope_digests = {ref.get("digest") for ref in envelope.evidence_refs if isinstance(ref, dict)}
    for cand in candidates:
        for cand_ref in cand.evidence_refs:
            if isinstance(cand_ref, dict):
                d = cand_ref.get("digest")
                if isinstance(d, str) and d and d not in envelope_digests:
                    return (f"candidate evidence_refs 引用 envelope 外的 digest={d}"
                            f"（journal verifier history 不含此证据）")
    return None


# ════════════════════════════════════════════════════════════════════════
# degraded side-channel writer + reader（task 4.5；不耦合 journal 主路径）
# ════════════════════════════════════════════════════════════════════════
def _degraded_path(state_dir: str | Path, project_id: str) -> Path:
    """task 4.5 side-channel：``.project-auto/state/lessons/degraded/<project>.jsonl``。

    不耦合 journal 主路径（journal 由 coordinator own runs/<proj>/...jsonl）；
    不污染 catalog events（catalog 经 ``LessonLifecycleEvent`` 受控词表）。
    """
    return Path(state_dir) / "lessons" / "degraded" / f"{project_id}.jsonl"


def _append_degraded_record(state_dir: str | Path, project_id: str, record: dict) -> None:
    """原子追加一条 degraded record（flock + O_APPEND + fsync；参照 learning_memory_store._atomic_append_line）。"""
    path = _degraded_path(state_dir, project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True)
    with open(path, "a", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def read_degraded_records(state_dir: str | Path, project_id: str) -> list[dict]:
    """读 degraded side-channel 全部记录（运维审计 / 可观测性）。"""
    path = _degraded_path(state_dir, project_id)
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            # side-channel 末尾截断容忍（同 journal/_scan 既定模式）；中部损坏留运维处理
            continue
    return out


# ════════════════════════════════════════════════════════════════════════
# 默认 persist 实现（task 4.4：reflection artifact + append candidates）
# ════════════════════════════════════════════════════════════════════════
def _default_persist(*, state_dir: str | Path, run_id: str, project_id: str,
                    reflection_text: str, candidates: list[LM.LessonCandidate],
                    timestamp: str) -> tuple[dict, list[str]]:
    """生产默认 persist：reflection artifact（REFLECTION kind, sanitized）+ append candidates。

    Returns:
        (reflection_artifact_ref_dict, candidate_ids)。
    """
    import artifact_store as AS
    import learning_memory_store as LMS

    # task 4.4：reflection 全量输出落 sanitized content-addressed artifact（REFLECTION kind）
    artifact_root = Path(state_dir) / "artifacts" / run_id
    ref = AS.store(str(artifact_root), reflection_text,
                   kind=ArtifactKind.REFLECTION.value,
                   sensitivity=Sensitivity.SANITIZED.value)
    reflection_ref_dict = dataclasses.asdict(ref)

    # task 4.4：accepted candidates → append_candidate with evidence_refs
    candidate_ids: list[str] = []
    for cand in candidates:
        cid = LMS.append_candidate(
            state_dir, project_id, cand,
            run_id=run_id, timestamp=timestamp)
        candidate_ids.append(cid)
    return reflection_ref_dict, candidate_ids


# ════════════════════════════════════════════════════════════════════════
# 主入口：run_terminal_reflection（task 4.2 + 4.3 + 4.4 + 4.5）
# ════════════════════════════════════════════════════════════════════════
def run_terminal_reflection(*, envelope: ENV.TerminalEnvelope,
                            state_dir: str | Path,
                            project_id: str, run_id: str, prd_id: str,
                            iteration_id: str, timestamp: str,
                            sdk_query_fn: Callable[[str, dict], str] | None = None,
                            persist_callback: Callable | None = None,
                            is_terminal_outcome: bool = False,
                            timeout_seconds: float = DEFAULT_REFLECTION_TIMEOUT_SECONDS) -> ReflectionResult:
    """task 4.2-4.5：跑 terminal reflection（design 决策#2 + #7 硬约束）。

    Pipeline：
        1. **task 4.3 terminal guard（OR 逻辑，评审 #1 残留修复）**：放行条件 = 「envelope.terminal_status
           是 ``loop_state`` 真终态」**或**「调用方声明 ``is_terminal_outcome=True``（dispatch-terminal
           outcome）」。两者皆否 → ``degraded{class:not_terminal}``（不调 SDK，不写 candidate）。
           Why（design L43）：「The terminal label alone never determines the evidence class; the journal
           does」——loop_state.TERMINAL_STATUSES 是 state-machine 内部视角（不可复活），不覆盖
           dispatch-terminal 语义（gate-blocked/revise-exhausted/blocked_external_state 在 dispatch_one
           已 return = terminal outcome，但 loop_state 视角可中间）。spec L90「MUST NOT change terminal
           predicates」禁止改 loop_state 终态集，故解耦：dispatch-terminal outcome 由调用方判定传入。
           fail-safe：``is_terminal_outcome`` 默认 ``False``（不传 → 仅 label 路径判定）。
        2. **task 4.2 SDK 调用**：渲染 sanitized prompt → ``sdk_query_fn``（默认 ``_default_sdk_query``
           走真 SDK，asyncio.wait_for 硬超时）；timeout / sdk_error → degraded。
        3. **task 4.2 strict JSON parse**：解析 SDK response；invalid_json → degraded。
        4. **schema 校验**：每个 candidate 经 ``candidate_from_model_output``（schema 边界 redaction）；
           invalid → schema_reject degraded。
        5. **task 4.5 evidence history mismatch**：candidate vs envelope.evidence_class；mismatch → degraded。
        6. **task 4.4 persist**：``persist_callback``（默认 ``_default_persist``：REFLECTION artifact +
           append_candidate）；persist 故障 → degraded。
        7. 返回 ``ReflectionResult(outcome="ok")``（**绝不**改 terminal outcome——fail-open by construction）。

    Args:
        envelope: ``learning_memory_envelope.build_terminal_envelope`` 的 sanitized envelope（必已构造好）。
        state_dir: 控制面 state 根（``.project-auto/state``）。
        project_id/run_id/prd_id/iteration_id: 关联 IDs（degraded record + candidate association）。
        timestamp: ISO8601（本模块不触时间——cron 隔离 + 可重放；调用方传入）。
        sdk_query_fn: SDK 调用注入（None → ``_default_sdk_query`` 走真 SDK + asyncio.wait_for 硬超时）；
            测试注入固定 JSON 返回（mock-SDK 模式，参照 conftest.py 既定）。
        persist_callback: persist 注入（None → ``_default_persist``：artifact_store + append_candidate）；
            测试可注入故障桩模拟 persist 失败（task 4.5 persist_failure 反例）。
        is_terminal_outcome: 调用方声明「dispatch 已 terminal outcome」（dispatch_one 已 return 的出口，
            如 blocked_test_gate / blocked_external_state / interrupted_pr / retry_blocked /
            retry_budget_exhausted / pr_* / stalled / failed / ...）。默认 ``False`` = fail-safe（调用方
            不传 → 仅靠 envelope.terminal_status label 判定；中间态 label + 不传 flag → degrade，绝不假阳）。
            run_daily 接线点传 ``_dispatch_is_terminal_outcome(rec)``。
        timeout_seconds: ``asyncio.wait_for`` 硬超时（SDK 内部 max_turns/max_budget 被 bypass，故用 wait_for 硬限）。

    Returns:
        immutable ``ReflectionResult``——字段集**只**暴露 reflection 自身产物，无 terminal mutation
        入口（fail-open by construction；调用方无法用此结果改 terminal outcome）。
    """
    # ─── task 4.3：terminal guard（评审 #1 残留修复：OR 逻辑 = label-terminal OR dispatch-outcome）──
    # loop_state.TERMINAL_STATUSES 是 state-machine 内部视角（published/aborted/failed/stalled/
    # orphan_deleted/blocked_evidence/sandbox_blocked/state_corrupt）——不覆盖 dispatch-terminal 语义
    # （gate-blocked/revise-exhausted/blocked_external_state 在 dispatch_one 已 return = terminal outcome，
    # 但 loop_state 视角对应中间态 test_blocked/external_blocked/revise）。design L43 明确「terminal
    # label alone never determines evidence class」——故触发门也不该绑死 loop_state.is_terminal(label)。
    # spec L90「MUST NOT change terminal predicates」：绝不动 loop_state 终态集（那是 dispatch/verify/
    # retry/publish 共用的 terminal predicate）。解耦：dispatch-terminal outcome 由调用方传入。
    # fail-safe：is_terminal_outcome 默认 False（调用方不传 → 仅 label 路径；中间态 label + 不传 flag → degrade）。
    label_terminal = False
    if envelope.terminal_status:
        try:
            label_terminal = is_terminal(IterationStatus(envelope.terminal_status))
        except ValueError:
            # envelope 给了未知 status label（非 IterationStatus.value，如未映射的 dispatch status）→
            # label 路径不放行；但 is_terminal_outcome=True 仍可放行（调用方判定 dispatch 已 terminal）。
            label_terminal = False
    if not (label_terminal or is_terminal_outcome):
        return _emit_degraded(
            state_dir=state_dir, project_id=project_id, run_id=run_id, prd_id=prd_id,
            iteration_id=iteration_id, timestamp=timestamp,
            envelope_evidence_class=envelope.evidence_class,
            degraded_class="not_terminal",
            reason=(f"terminal_status={envelope.terminal_status!r} 非 loop_state 终态且"
                    f"调用方未声明 dispatch-terminal outcome（is_terminal_outcome=False）"
                    f"——Stop/retry iteration 不是 reflection 边界"))

    # ─── task 4.2：渲染 sanitized prompt + 调 SDK ───
    prompt = _render_sdk_prompt(envelope, project_id=project_id, prd_id=prd_id)
    sdk_options = {
        "tools": list(_READONLY_TOOLS),
        "timeout_seconds": timeout_seconds,
        "max_turns": DEFAULT_REFLECTION_MAX_TURNS,
        "max_budget_usd": DEFAULT_REFLECTION_MAX_BUDGET_USD,
        "permission_mode": "default",
        # 不传 model（roc LiteLLM 代理默认 glm-5.2；裸 Anthropic id 会被代理拒）
        # 不传 resume / fork_session（不污染主 dev session）
    }
    sdk_fn = sdk_query_fn if sdk_query_fn is not None else _default_sdk_query
    try:
        raw_response = sdk_fn(prompt, sdk_options)
    except TimeoutError as e:
        return _emit_degraded(
            state_dir=state_dir, project_id=project_id, run_id=run_id, prd_id=prd_id,
            iteration_id=iteration_id, timestamp=timestamp,
            envelope_evidence_class=envelope.evidence_class,
            degraded_class="timeout", reason=f"SDK 超时：{str(e)[:200]}")
    except Exception as e:
        return _emit_degraded(
            state_dir=state_dir, project_id=project_id, run_id=run_id, prd_id=prd_id,
            iteration_id=iteration_id, timestamp=timestamp,
            envelope_evidence_class=envelope.evidence_class,
            degraded_class="sdk_error", reason=f"SDK 异常：{str(e)[:200]}")

    # ─── task 4.2：strict JSON parse ───
    try:
        response_obj = json.loads(raw_response)
    except (json.JSONDecodeError, TypeError) as e:
        return _emit_degraded(
            state_dir=state_dir, project_id=project_id, run_id=run_id, prd_id=prd_id,
            iteration_id=iteration_id, timestamp=timestamp,
            envelope_evidence_class=envelope.evidence_class,
            degraded_class="invalid_json",
            reason=f"SDK 返回非 JSON：{str(e)[:200]}；raw[:80]={str(raw_response)[:80]}")
    if not isinstance(response_obj, dict) or not isinstance(response_obj.get("candidates"), list):
        return _emit_degraded(
            state_dir=state_dir, project_id=project_id, run_id=run_id, prd_id=prd_id,
            iteration_id=iteration_id, timestamp=timestamp,
            envelope_evidence_class=envelope.evidence_class,
            degraded_class="invalid_json",
            reason="SDK JSON 缺 candidates 数组字段")

    # ─── schema 校验：candidate_from_model_output（schema 边界 redaction + 受控词表）──
    candidates: list[LM.LessonCandidate] = []
    for i, raw_cand in enumerate(response_obj["candidates"]):
        try:
            cand = LM.candidate_from_model_output(
                raw_cand,
                project_id=project_id, prd_id=prd_id,
                iteration_refs=(iteration_id,),
                source_outcome=envelope.terminal_status,
                confidence=raw_cand.get("confidence") if isinstance(raw_cand, dict) else None)
        except (ValueError, TypeError) as e:
            return _emit_degraded(
                state_dir=state_dir, project_id=project_id, run_id=run_id, prd_id=prd_id,
                iteration_id=iteration_id, timestamp=timestamp,
                envelope_evidence_class=envelope.evidence_class,
                degraded_class="schema_reject",
                reason=f"candidates[{i}] schema reject：{str(e)[:200]}")
        candidates.append(cand)

    # ─── task 4.5：evidence history mismatch 检测（design 决策#1）──
    mismatch = _detect_evidence_history_mismatch(envelope, candidates)
    if mismatch is not None:
        return _emit_degraded(
            state_dir=state_dir, project_id=project_id, run_id=run_id, prd_id=prd_id,
            iteration_id=iteration_id, timestamp=timestamp,
            envelope_evidence_class=envelope.evidence_class,
            degraded_class="evidence_history_mismatch", reason=mismatch[:200])

    # ─── task 4.4：persist（artifact + append candidates）──
    persist_fn = persist_callback if persist_callback is not None else _default_persist
    try:
        if persist_callback is not None:
            # 测试注入的 persist_callback：签名兼容（可抛 OSError 模拟 persist_failure）
            persist_fn(state_dir=state_dir, run_id=run_id, project_id=project_id,
                       reflection_text=raw_response, candidates=candidates, timestamp=timestamp)
            reflection_ref_dict = None
        else:
            reflection_ref_dict, _cand_ids = persist_fn(
                state_dir=state_dir, run_id=run_id, project_id=project_id,
                reflection_text=raw_response, candidates=candidates, timestamp=timestamp)
    except Exception as e:
        return _emit_degraded(
            state_dir=state_dir, project_id=project_id, run_id=run_id, prd_id=prd_id,
            iteration_id=iteration_id, timestamp=timestamp,
            envelope_evidence_class=envelope.evidence_class,
            degraded_class="persist_failure", reason=f"persist 异常：{str(e)[:200]}")

    # ─── task 4.2/4.4：ok ───
    return ReflectionResult(
        outcome="ok",
        degraded_class=None,
        degraded_reason=None,
        candidates=tuple(candidates),
        reflection_artifact_ref=reflection_ref_dict,
        evidence_class=envelope.evidence_class,
    )


def _emit_degraded(*, state_dir: str | Path, project_id: str, run_id: str, prd_id: str,
                   iteration_id: str, timestamp: str,
                   envelope_evidence_class: str,
                   degraded_class: str, reason: str) -> ReflectionResult:
    """task 4.5：emit ``learning_memory_degraded`` 到 side-channel + 返回 degraded ReflectionResult。

    design 决策#7 fail-open for delivery：degraded record 走 side-channel
    （``lessons/degraded/<project>.jsonl``），**不耦合 journal 主路径**（journal 由 coordinator own）、
    **不改 catalog events**（catalog 经 ``LessonLifecycleEvent`` 受控词表）。

    fail-closed for memory：``ReflectionResult.candidates=()``——绝不污染 catalog。
    """
    record = {
        "schema_version": DEGRADED_SCHEMA_VERSION,
        "timestamp": timestamp,
        "run_id": run_id,
        "prd_id": prd_id,
        "iteration_id": iteration_id,
        "project_id": project_id,
        "envelope_evidence_class": envelope_evidence_class,
        "degraded_class": degraded_class,
        "reason": reason[:200],
    }
    try:
        _append_degraded_record(state_dir, project_id, record)
    except OSError:
        # side-channel 写故障也不能改 terminal outcome（fail-open by construction）——
        # 仅 ReflectionResult 暴露 degraded 状态（调用方感知），但不改任何 PRD outcome
        pass
    return ReflectionResult(
        outcome="degraded",
        degraded_class=degraded_class,
        degraded_reason=reason[:200],
        candidates=(),
        reflection_artifact_ref=None,
        evidence_class=envelope_evidence_class,
    )
