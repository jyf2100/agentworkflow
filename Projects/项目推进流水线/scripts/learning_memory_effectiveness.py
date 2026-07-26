#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""learning_memory_effectiveness.py — add-cross-prd-learning-memory Section 6.1/6.2/6.4 实现。

spec design 决策#6「Close the loop with evidence-derived usage outcomes」的机械评估层：

    * **task 6.1**：classify_outcome —— 把 (action_observed, failure_recurred, evidence_available,
      has_explicit_prevention_evidence) 四元组机械映射到受控 ``UsageOutcomeKind``。
      核心硬约束（spec）：「Absence of a detectable action is recorded as ``unknown``, not
      automatically as disobedience」—— ``evidence_available=False`` 永远 → ``unknown``。

    * **task 6.2**：build_usage_outcome —— 经 schema 校验构造 ``UsageOutcome`` record（无 IO）。
      detect_action_observed —— V1 保守机械判定 helper：从 terminal_evidence 的结构化字段推断
      (action_observed, failure_recurred, evidence_available)。**保守原则：无法确认 →
      evidence_available=False → classify → unknown**。语义增强（SDK synthesis）是 follow-up，
      由调用方在 Section 7 决定是否替换。

    * **task 6.4**：build_memory_mode_record —— 纯函数构造 dispatch/report 的 memory_mode 字段
      （ ``(shadow_on, injection_on)`` bool pair + selected_lesson_ids + counts + degraded_status）。
      spec task 6.4：「memory mode as a (shadow_on, injection_on) boolean pair, selected lesson IDs,
      candidate counts, promotions, and degraded status while keeping existing success/failure
      semantics unchanged」。纯函数——coordinator 接线留 Section 7。

**bounded deterministic confidence update 常量**（design 决策#6：「deterministic state transitions
apply bounded confidence updates」）：
    * ``CONFIDENCE_UP_BOUND = 0.1``：每个 followed/recurrence_prevented 加（cap 1.0）。
    * ``CONFIDENCE_DOWN_BOUND = 0.2``：每个 contradicted/recurrence_observed 减（floor 0.0）。
    * ``CONTRADICTION_RETIRE_THRESHOLD = 2``：repeated contradiction → retire（spec「repeated
      contradiction retires it」）。

**设计约束**（CLAUDE.md + design 决策#6）：
    * 纯 stdlib，零 IO，零 SDK 调用（effectiveness 评估是**机械判定**，design 决策#6：semantic
      synthesis 是 follow-up；deterministic transitions own every trust boundary）；
    * 状态转换是确定性公式（不调 SDK；幂等：相同输入 → 相同输出，无副作用）；
    * frozen dataclass + ``__test__: ClassVar[bool] = False`` 防 pytest 收集告警（同 loop_state 模式）；
    * 不改 dispatch/report record 现有字段（coordinator 接线留 Section 7）。

**纯 stdlib 新模块**——cron 隔离不变；零模块级 SDK 导入。
"""
from __future__ import annotations

import learning_memory_schema as LM


# ════════════════════════════════════════════════════════════════════════
# task 6.2 bounded deterministic confidence update 常量（design 决策#6）
# ════════════════════════════════════════════════════════════════════════
CONFIDENCE_UP_BOUND: float = 0.1
"""每个 followed / recurrence_prevented 应用结果对 entry.confidence 的上调量（cap 1.0）。

spec design 决策#6：「deterministic state transitions apply bounded confidence updates」。
0.1 是 V1 谨慎值——单个正面证据不应让 lesson 跳到高 confidence；累积才显著。
catalog._apply_usage_outcomes 使用，cap 在 ``min(1.0, ...)`` enforce。
"""

CONFIDENCE_DOWN_BOUND: float = 0.2
"""每个 contradicted / recurrence_observed 应用结果对 entry.confidence 的下调量（floor 0.0）。

下调幅度 > 上调幅度（0.2 > 0.1）—— 保守原则：负面证据比正面证据权重更大（spec「A contradicted
lesson loses confidence」）。catalog._apply_usage_outcomes 使用，floor 在 ``max(0.0, ...)`` enforce。
"""

CONTRADICTION_RETIRE_THRESHOLD: int = 2
"""重复 contradicted 应用结果次数阈值——达到则 state=retired（spec「repeated contradiction retires it」）。

V1 取 2（与 promotion 的 cross-PRD 阈值对齐）：两次独立 PRD 上 contradicted → 强信号 lesson 错了。
catalog._apply_usage_outcomes 仅对 state=="active" 的 entry 触发（terminal stickiness）。
"""


# ════════════════════════════════════════════════════════════════════════
# task 6.1：classify_outcome（机械映射 → UsageOutcomeKind.value）
# ════════════════════════════════════════════════════════════════════════
def classify_outcome(*, action_observed: bool, failure_recurred: bool,
                     evidence_available: bool,
                     has_explicit_prevention_evidence: bool = False) -> str:
    """task 6.1：把应用结果四元组机械映射到 ``UsageOutcomeKind.value``（受控词表）。

    spec design 决策#6 硬约束：「Absence of a detectable action is recorded as ``unknown``, not
    automatically as disobedience」—— ``evidence_available=False`` 永远返回 ``"unknown"``。

    判定矩阵（``evidence_available=True`` 时）::

        action | failure | has_prevention_evidence → outcome
        -------|---------|--------------------------|------------------------
          T    |   F     |   T                      | recurrence_prevented
          T    |   F     |   F                      | followed
          T    |   T     |   *                      | contradicted (failure 优先于 prevention)
          F    |   T     |   *                      | recurrence_observed
          F    |   F     |   *                      | not_observed

    failure_recurred=True 优先于 has_prevention_evidence（spec 语义：若 failure 真复现，prevention
    并未实际 prevent）。``unknown`` 是 UsageOutcomeKind 的合法枚举值但**不参与 confidence update**
    （catalog._apply_usage_outcomes 跳过 ``unknown``）。

    Args:
        action_observed: 是否观察到 prescribed corrective action 发生（机械判定）。
        failure_recurred: 关联 failure pattern 是否复现（机械判定）。
        evidence_available: 是否有足够结构性证据做判定——False → 直接返回 ``"unknown"``。
        has_explicit_prevention_evidence: 是否有显式证据表明 action 成功 prevent 了 failure
            （区分 followed vs recurrence_prevented）；默认 False。

    Returns:
        ``UsageOutcomeKind.value`` 字符串（受控词表）。

    Raises:
        ValueError: 不会 raise（所有输入组合都有映射；``unknown`` 兜底）。
    """
    # spec 硬约束：absent evidence → unknown（绝不当 disobedience）
    if not evidence_available:
        return LM.UsageOutcomeKind.UNKNOWN.value
    # failure_recurred 优先：action 发生但 failure 仍复现 → contradicted（prevention 失败）
    if failure_recurred:
        if action_observed:
            return LM.UsageOutcomeKind.CONTRADICTED.value
        return LM.UsageOutcomeKind.RECURRENCE_OBSERVED.value
    # failure 未复现：action 发生 → 看 prevention evidence 区分 followed vs recurrence_prevented
    if action_observed:
        if has_explicit_prevention_evidence:
            return LM.UsageOutcomeKind.RECURRENCE_PREVENTED.value
        return LM.UsageOutcomeKind.FOLLOWED.value
    # action 未观察 + failure 未复现 → not_observed（中性，不算 disobey 也不算 confirm）
    return LM.UsageOutcomeKind.NOT_OBSERVED.value


# ════════════════════════════════════════════════════════════════════════
# task 6.1：detect_action_observed（V1 保守机械判定 helper）
# ════════════════════════════════════════════════════════════════════════
def detect_action_observed(lesson_entry: dict,
                           terminal_evidence: dict) -> tuple[bool, bool, bool]:
    """task 6.1 V1 helper：从 terminal_evidence 机械推断 (action_observed, failure_recurred,
    evidence_available)。

    **保守原则**（spec design 决策#6）：「Absence of a detectable action is recorded as unknown」
    —— 任何不确定 → ``evidence_available=False`` → ``classify_outcome`` → ``"unknown"``。

    V1 机械判定启发式（**确定性、零 SDK**）：
        * **failure_recurred**：True iff ``terminal_evidence.verifier_verdict`` 是失败值
          （``test_fail`` / ``fail`` / ``failed`` / ``revise_exhausted``），OR 显式
          ``failure_recurred: bool`` 字段，OR ``failure_class`` token 出现在 ``skip_reason``/
          ``error_log`` 文本。
        * **action_observed**：True iff 显式 ``action_observed: bool`` 字段，OR ``corrective_action``
          的显著 token（len>=4 的 word）多数出现在 ``diff``+``test_log`` 文本（>=50% 命中）。
        * **evidence_available**：True iff action_observed OR failure_recurred OR 显式
          ``has_structured_evidence: True``。

    **V1 限制**（docstring 显式标注）：
        * 不调 SDK（semantic synthesis 是 follow-up）；
        * 启发式偏保守——多数模糊场景会落到 ``evidence_available=False`` → ``unknown``；
        * 调用方（Section 7 接线）可替换为 SDK 增强版，只要保持同样的返回契约。

    Args:
        lesson_entry: catalog entry dict（用 ``corrective_action`` / ``failure_class`` 做 token 匹配）。
        terminal_evidence: terminal 阶段的结构化证据 dict（字段约定见上方启发式说明）。

    Returns:
        ``(action_observed, failure_recurred, evidence_available)`` 三元组，传给 ``classify_outcome``。
    """
    if not isinstance(terminal_evidence, dict) or not isinstance(lesson_entry, dict):
        return (False, False, False)

    # 显式 bool 字段优先（便于测试与结构化集成）
    if isinstance(terminal_evidence.get("action_observed"), bool):
        action_observed = terminal_evidence["action_observed"]
    else:
        action_observed = _detect_action_tokens(lesson_entry, terminal_evidence)

    if isinstance(terminal_evidence.get("failure_recurred"), bool):
        failure_recurred = terminal_evidence["failure_recurred"]
    else:
        failure_recurred = _detect_failure_recurred(lesson_entry, terminal_evidence)

    evidence_available = bool(
        action_observed
        or failure_recurred
        or terminal_evidence.get("has_structured_evidence") is True
    )
    return (action_observed, failure_recurred, evidence_available)


def _detect_action_tokens(lesson_entry: dict, terminal_evidence: dict) -> bool:
    """token 匹配启发式：corrective_action 显著 token 多数出现在 diff+test_log 文本。

    「显著 token」= 长度 >=4 的 word（短 token 如 ``a``/``the``/``add`` 噪声大）；「多数」= 命中数
    >= ``max(1, len(tokens) // 2 + 1)``。保守阈值——既要看到痕迹，又允许部分 token 表达差异。

    任何一侧空（无 corrective_action / 无 corpus） → False（evidence_available 由调用方综合判定）。
    """
    corrective = str(lesson_entry.get("corrective_action", "") or "")
    if not corrective:
        return False
    diff_text = str(terminal_evidence.get("diff", "") or "")
    test_log = str(terminal_evidence.get("test_log", "") or "")
    corpus = (diff_text + "\n" + test_log).lower()
    if not corpus.strip():
        return False
    tokens = [w.lower() for w in corrective.split() if len(w) >= 4]
    if not tokens:
        return False
    hits = sum(1 for t in tokens if t in corpus)
    threshold = max(1, len(tokens) // 2 + 1)
    return hits >= threshold


def _detect_failure_recurred(lesson_entry: dict, terminal_evidence: dict) -> bool:
    """failure 复现启发式：verifier 失败 verdict，或 failure_class token 在 skip_reason/error_log。

    机械保守——无 verifier 字段且无 token 命中 → False（evidence_available 由调用方综合判定）。
    """
    verdict = str(terminal_evidence.get("verifier_verdict", "") or "").lower().strip()
    if verdict in ("test_fail", "fail", "failed", "revise_exhausted", "verifier_revise_exhausted"):
        return True
    failure_class = str(lesson_entry.get("failure_class", "") or "").lower()
    if not failure_class:
        return False
    skip_reason = str(terminal_evidence.get("skip_reason", "") or "")
    error_log = str(terminal_evidence.get("error_log", "") or "")
    combined = (skip_reason + "\n" + error_log).lower()
    if not combined.strip():
        return False
    # failure_class 形如 "verifier_invariant_violation" / "gate_blocked" —— 子串匹配（含下划线归一）
    return failure_class in combined


# ════════════════════════════════════════════════════════════════════════
# task 6.2：build_usage_outcome（构造 schema-valid UsageOutcome，无 IO）
# ════════════════════════════════════════════════════════════════════════
def build_usage_outcome(*, event_id: str, timestamp: str, project_id: str,
                        lesson_id: str, prd_id: str,
                        action_observed: bool, failure_recurred: bool,
                        outcome: str, evidence_refs: tuple[dict, ...] = ()) -> LM.UsageOutcome:
    """task 6.2：构造 ``UsageOutcome`` record（经 schema 校验，无 IO）。

    调用方流程（Section 7 接线时）：
        1. ``detect_action_observed`` 机械推断 (action, failure, evidence_available)；
        2. ``classify_outcome(...)`` 得到 ``outcome``；
        3. ``build_usage_outcome(...)`` 封装成 schema-valid ``UsageOutcome``；
        4. ``LMS.append_usage_outcome(state_dir, project_id, outcome)`` 持久化。

    本函数只做构造 + schema 校验（``UsageOutcome.__post_init__`` enforce outcome 在受控词表）。
    """
    return LM.UsageOutcome(
        event_id=event_id,
        timestamp=timestamp,
        project_id=project_id,
        lesson_id=lesson_id,
        prd_id=prd_id,
        action_observed=bool(action_observed),
        failure_recurred=bool(failure_recurred),
        outcome=outcome,
        evidence_refs=tuple(evidence_refs or ()),
        schema_version=1,
    )


# ════════════════════════════════════════════════════════════════════════
# task 6.4：build_memory_mode_record（dispatch/report 纯函数，接线留 Section 7）
# ════════════════════════════════════════════════════════════════════════
def build_memory_mode_record(shadow_on: bool, injection_on: bool, *,
                             selected_lesson_ids: tuple[str, ...] = (),
                             candidate_count: int = 0,
                             promotion_count: int = 0,
                             degraded_status: str | None = None) -> dict:
    """task 6.4：构造 dispatch/report record 的 ``memory_mode`` 字段（纯函数）。

    spec task 6.4：「Extend dispatch/report records with memory mode as a ``(shadow_on,
    injection_on)`` boolean pair, selected lesson IDs, candidate counts, promotions, and
    degraded status while keeping existing success/failure semantics unchanged」。

    **纯函数**——不改 dispatch/report record 现有字段；coordinator 在 Section 7 接线时把返回的
    dict 合并进现有 record。``degraded_status=None`` 表示无 degraded（normal 运行）；非 None 时
    调用方应同时记 ``learning_memory_degraded`` 事件（class=degraded_status）。

    Args:
        shadow_on: ``cross_prd_learning_shadow`` flag 解析值（coordinator 传入）。
        injection_on: ``cross_prd_learning_injection`` flag 解析值（已 honor shadow gating）。
        selected_lesson_ids: 本次 dispatch 选择注入的 lesson IDs（按 ranking 排序）。
        candidate_count: 当前 catalog 的 candidate 总数（监控用）。
        promotion_count: 已 promote 的 lesson 数（监控用）。
        degraded_status: ``None`` 或 degraded class 字符串（如 ``middle_corruption`` /
            ``injection_not_gated`` / ``reflection_timeout``）。

    Returns:
        ``dict`` —— ``{"shadow_on", "injection_on", "selected_lesson_ids", "candidate_count",
        "promotion_count", "degraded_status"}``，全部 JSON-serializable。
    """
    return {
        "shadow_on": bool(shadow_on),
        "injection_on": bool(injection_on),
        "selected_lesson_ids": tuple(selected_lesson_ids or ()),
        "candidate_count": int(candidate_count),
        "promotion_count": int(promotion_count),
        "degraded_status": degraded_status,
    }
