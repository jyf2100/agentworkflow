#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""learning_memory_promotion.py — add-cross-prd-learning-memory Section 3 cross-PRD promotion policy。

spec design 决策#4 的硬约束实现（policy 层，不改 Section 2 的 fail-closed/deterministic/atomic
mechanic）：

    * **3.1 普通 promotion 仅在等效 valid candidate 引用 ≥2 distinct PRD IDs（同项目）**：
      ``supporting_prd_ids`` 去重后 <2 → entry 不进 active catalog projection（过滤掉）。
      重复 iteration 计一次（Section 2 的 supporting_prd_ids 已是 set 去重，直接 len()）。
      **不新增 CatalogState 状态**（schema 已锁 active/conflicted/superseded/retired）；未达 recurrence
      的 entry 就是不进 projection（spec「No single-occurrence fast path exists in V1」）。

    * **3.2 merge 行为**：保留所有 source_candidate_ids + evidence lineage（Section 2 已聚合）；
      冲突 corrective_action（同 equivalence_key 但文本不同）→ state=conflicted，保持 inactive 直到
      evidence 解决（spec「Conflicting corrective actions create a conflict event and remain inactive
      until evidence resolves them」）。

    * **3.3 反例证明单次出现永不 promotion**：即使 verifier 确认的 critical invariant violation、
      model self-labeled critical（invariant_class audit-only）、或任何 unknown enum 值，单 PRD 出现
      也绝不 promotion（spec「even a verifier-confirmed critical invariant violation must recur across
      two distinct PRDs before promotion」）。unknown enum 已在 schema 边界拒（candidate 永不进
      candidates.jsonl），本模块对 equivalence_key 结构再兜底。

    * **3.4 active/conflicted/superseded/retired 作为 projection**：从 candidates + events replay 派生；
      retirement 只改 projection，candidates.jsonl / events.jsonl append-only 真源不变
      （spec「without deleting historical facts」）。

**设计约束**：
    * 纯 stdlib，零 IO（policy 纯函数，不触时间 / 不碰 flag / 不碰 journal 主路径）；
    * frozen dataclass + ``__test__: ClassVar[bool] = False`` 防 pytest 收集告警；
    * fail-open delivery（policy 异常由 catalog wrapper 兜底，不抛主路径）；
    * 不改 Section 2 的 fail-closed / deterministic replay / atomic mechanic —— 本模块是 catalog
      ``_replay`` 在 ``_aggregate_candidates`` + ``_apply_events_idempotent`` **之后**调用的 policy 层。

**conflict 判据**：``corrective_action`` 文本 ``strip()`` 后 exact string equality。语义相近但文本不同
→ 仍 conflict（语义归一交给 equivalence_key 的 corrective_action_class；文本不同 = executable step
不同 = 保守判 conflict，spec「keep conflicting actions inactive」）。
"""
from __future__ import annotations

import dataclasses
from typing import ClassVar


# ════════════════════════════════════════════════════════════════════════
# 常量（spec design 决策#4）
# ════════════════════════════════════════════════════════════════════════
PROMOTION_MIN_DISTINCT_PRDS: int = 2
"""普通 promotion 需要的最低 distinct PRD IDs 数（spec design 决策#4：「at least two distinct PRD IDs
in the same project」）。同 PRD 多 iteration 只算一个 distinct PRD（supporting_prd_ids 是 set）。"""


# ════════════════════════════════════════════════════════════════════════
# promotion 判定结果（audit trail）
# ════════════════════════════════════════════════════════════════════════
@dataclasses.dataclass(frozen=True)
class PromotionDecision:
    """单 entry 的 promotion 判定（audit trail，可序列化到 catalog projection）。

    ``promoted`` 仅对 active 状态有意义；conflicted/retired/superseded 算「entry 进 catalog 但不
    active-injectable」（audit 保留）。``reason`` 是人读理由（audit / degraded event 诊断用）。
    """
    __test__: ClassVar[bool] = False
    lesson_id: str
    equivalence_key: str
    distinct_prd_count: int
    has_corrective_action_conflict: bool
    promoted: bool            # True = 进 active catalog projection（state=active）
    reason: str               # 人读理由（audit）


# ════════════════════════════════════════════════════════════════════════
# conflict 检测
# ════════════════════════════════════════════════════════════════════════
def detect_corrective_action_conflict(corrective_actions_seen: set[str]) -> bool:
    """同 equivalence_key 下 >1 distinct corrective_action 文本 → conflict。

    比较方式：exact string equality（``strip()`` 后，由 ``_aggregate_candidates`` 入 set 前归一）。
    语义相近但文本不同 → 仍 conflict（spec「keep conflicting actions inactive」；语义归一交给
    equivalence_key 的 corrective_action_class，文本不同 = executable step 不同）。
    """
    return len(corrective_actions_seen) > 1


# ════════════════════════════════════════════════════════════════════════
# defense-in-bottom-line（spec「promotion 层再兜底」）
# ════════════════════════════════════════════════════════════════════════
def _is_structurally_promotable(entry: dict) -> tuple[bool, str]:
    """defense-in-bottom-line：验 entry 结构完整（防 corrupted entry 漏过 schema + store 两层）。

    unknown enum 已在 schema 边界 + store 读端 defense-in-depth 拒（candidate 永不进 candidates.jsonl）；
    这里再验 equivalence_key 结构（非空 + 含 project scope 分隔符 ``:``）+ supporting_prd_ids 类型。
    结构不合法 → 不 promote（绝不部分信任）。
    """
    eq_key = entry.get("equivalence_key", "")
    if not isinstance(eq_key, str) or not eq_key.strip() or ":" not in eq_key:
        return False, f"malformed_equivalence_key:{eq_key!r}"
    prd_ids = entry.get("supporting_prd_ids")
    if not isinstance(prd_ids, (set, frozenset, list, tuple)):
        return False, f"malformed_supporting_prd_ids:{type(prd_ids).__name__}"
    return True, ""


# ════════════════════════════════════════════════════════════════════════
# 单 entry promotion 判定
# ════════════════════════════════════════════════════════════════════════
def evaluate_promotion(entry: dict) -> PromotionDecision:
    """评估单 entry 的 promotion 判定（纯函数，不 IO）。

    判定边界（spec design 决策#4，按优先级）：
        1. **结构合法性**（bottom-line）：equivalence_key malformed → 不 promote；
        2. **distinct PRD count < 2** → 不 promote（spec「No single-occurrence fast path exists in V1；
           even a verifier-confirmed critical invariant violation must recur across two distinct PRDs
           before promotion」）—— **即使 failure_class 是 VERIFIER_INVARIANT_VIOLATION，即使 invariant_class
           标 critical**，单 PRD 也不 promote；
        3. **corrective_action 冲突** → 不 promote to active（spec「Conflicting corrective actions
           create a conflict event and remain inactive」）；
        4. 否则 promote（state=active）。

    注：unknown enum 已在 schema 边界拒（candidate 永不进 candidates.jsonl），故此处不再判 unknown ——
    结构合法的 entry 必来自 schema-valid candidate（defense-in-bottom-line 仅兜底 corrupted entry）。
    """
    ok, reason = _is_structurally_promotable(entry)
    if not ok:
        return PromotionDecision(
            lesson_id=str(entry.get("lesson_id", "")),
            equivalence_key=str(entry.get("equivalence_key", "")),
            distinct_prd_count=0,
            has_corrective_action_conflict=False,
            promoted=False,
            reason=reason,
        )

    prd_ids = entry.get("supporting_prd_ids") or set()
    distinct_prd_count = len(prd_ids)
    actions_seen = entry.get("_audit_corrective_actions") or set()
    has_conflict = detect_corrective_action_conflict(actions_seen)

    lesson_id = str(entry.get("lesson_id", ""))
    eq_key = str(entry.get("equivalence_key", ""))

    if distinct_prd_count < PROMOTION_MIN_DISTINCT_PRDS:
        return PromotionDecision(
            lesson_id=lesson_id,
            equivalence_key=eq_key,
            distinct_prd_count=distinct_prd_count,
            has_corrective_action_conflict=has_conflict,
            promoted=False,
            reason=(f"insufficient_cross_prd_recurrence:{distinct_prd_count}"
                    f"<{PROMOTION_MIN_DISTINCT_PRDS}"
                    f"(spec:even verifier-confirmed critical must recur across 2 distinct PRDs)"),
        )

    if has_conflict:
        return PromotionDecision(
            lesson_id=lesson_id,
            equivalence_key=eq_key,
            distinct_prd_count=distinct_prd_count,
            has_corrective_action_conflict=True,
            promoted=False,
            reason="conflicting_corrective_actions:inactive_until_evidence_resolves",
        )

    return PromotionDecision(
        lesson_id=lesson_id,
        equivalence_key=eq_key,
        distinct_prd_count=distinct_prd_count,
        has_corrective_action_conflict=False,
        promoted=True,
        reason="cross_prd_recurrence_met",
    )


# ════════════════════════════════════════════════════════════════════════
# 批量 policy 应用（catalog _replay 调用入口）
# ════════════════════════════════════════════════════════════════════════
def apply_promotion_policy(grouped: dict[str, dict]) -> tuple[dict[str, dict], tuple[PromotionDecision, ...]]:
    """应用 promotion policy 到 grouped entries（``_aggregate_candidates`` + ``_apply_events_idempotent``
    之后调用）。

    返回 ``(filtered_grouped, decisions)``：
        * ``filtered_grouped``：只含进 catalog projection 的 entries：
            - **≥2 PRD + 无冲突** → 保留（state=active 或 lifecycle override）；
            - **≥2 PRD + 冲突** → 保留为 state=conflicted（除非 lifecycle 已标 retired/superseded）；
            - **<2 PRD** → **过滤掉**（不进 active catalog projection；candidates.jsonl 真源不动）。
        * ``decisions``：所有 entries 的 promotion 判定（audit trail，含被过滤的），按 lesson_id 排序。

    **state 语义**（spec active/conflicted/superseded/retired 受控词表）：
        * conflict 检测只在 state=="active" 时把 state 改为 "conflicted"；
        * lifecycle 已标的 retired/superseded 优先（terminal 状态不再标 conflicted——已排除 injection）。

    **不改 Section 2 mechanic**：纯 dict 变换，不触 IO / 不触 replay / 不触 atomic write。
    """
    decisions: list[PromotionDecision] = []
    filtered: dict[str, dict] = {}
    for key, entry in grouped.items():
        decision = evaluate_promotion(entry)
        decisions.append(decision)
        if not decision.promoted:
            if (decision.has_corrective_action_conflict
                    and decision.distinct_prd_count >= PROMOTION_MIN_DISTINCT_PRDS):
                # 冲突 entry 进 catalog（auditable）但 state=conflicted（inactive for injection）
                # 仅当 lifecycle 未已标 terminal 状态（retired/superseded 优先）
                if entry.get("state") == "active":
                    entry["state"] = "conflicted"
                filtered[key] = entry
            # else: <2 PRD 或 malformed → 过滤掉（不进 projection；candidates.jsonl 不动）
            continue
        filtered[key] = entry
    # deterministic audit trail（sorted by lesson_id）
    decisions.sort(key=lambda d: d.lesson_id)
    return filtered, tuple(decisions)
