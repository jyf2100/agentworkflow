#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""learning_memory_retrieval.py — add-cross-prd-learning-memory Section 5 实现。

spec design 决策#5「Retrieve with bounded deterministic metadata matching」的机械检索 + 注入层：

    * **task 5.1 derive_task_metadata**：从 project_profile + immutable PRD **纯函数**派生检索元数据
      （project_id / tags / acceptance_categories / lifecycle_stage / declared_paths），**零 SDK / 零 LLM**。
      design 决策#5：「derives task metadata … without an additional semantic classification call」+
      「Embedding search is deferred until deterministic retrieval has measurable recall problems」。
      保守原则：只映射高置信字段，不确定**不加 tag**（防假阳性注入）。

    * **task 5.2 retrieve_lessons**：项目本地 filter + rank，stable tie-break by lesson_id ASC。
      rank key（design 决策#5 顺序）::
          (applicability_overlap DESC, verified_support_count DESC, effectiveness_score DESC,
           confidence DESC, recency_rank DESC, lesson_id ASC)
      effectiveness_score 与 catalog confidence update 方向一致（followed/recurrence_prevented +1；
      contradicted/recurrence_observed -1；not_observed/unknown 0）。cap ``MAX_INJECTED_LESSONS=5``
      （design「At most five lessons are rendered」）。

    * **task 5.3 render_lesson_block**：渲染 ≤5 lessons 为简洁 markdown checklist。**严格排除**（design
      决策#5「Evidence bodies and historical narratives are not injected」）：``evidence_refs`` /
      ``source_candidate_ids`` / ``effectiveness_history`` / ``pattern_description`` /
      ``supporting_prd_ids`` / ``equivalence_key`` / ``confidence`` / 任何历史叙事或证据正文。每条**只含**
      ``lesson_id`` + ``trigger`` + ``corrective_action`` + ``non_applicability_when``。

    * **task 5.4 inject_into_prompt**：纯字符串拼接函数。``lesson_block==""`` → 原样返回 ``dev_prompt``
      （no-op）；非空 → append 到末尾。**不改 dispatch 主路径语义**；coordinator 接线留 Section 7。

    * **task 5.5 load_catalog_for_retrieval + 反例全覆盖**：fail-open wrapper（design 决策#7「delivery
      fail-open」）：catalog 不存在/读异常 → degraded_class + ``entries=()``，**绝不抛主路径**；调用方
      据 ``degraded_class`` 记 ``learning_memory_degraded`` 继续跑，memory 故障不改 PRD 结果。

**硬约束**（CLAUDE.md + design 决策#5/#7）：
    * 纯 stdlib 新模块——检索 = 确定性集合运算 + 排序（无 SDK / LLM / embedding）；注入 = 字符串拼接。
    * **控制/目标平面严格隔离**（ADR-0001）：注入的是控制面构造的 dev prompt 字符串，**绝不写目标
      worktree / commit / PR / 不可变 PRD**。memory state 只在 ``.project-auto/state/lessons/``（gitignored）。
    * 不改 coordinator.py / dispatch.py / dev-agent.py / report.py（接线留 Section 7）。
    * 不碰 Section 4 envelope/reflection、Section 6 effectiveness 的现有函数；只读 catalog。
    * 不改 catalog/store/schema/promotion（Section 2/3/6 产物）。
    * frozen dataclass + ``__test__: ClassVar[bool] = False`` 防 pytest 收集告警（同 loop_state 模式）。

**纯 stdlib 新模块**——cron 隔离不变；零模块级 SDK 导入。
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import ClassVar

import learning_memory_catalog as LMCat
import learning_memory_schema as LM


# ════════════════════════════════════════════════════════════════════════
# 常量（design 决策#5）
# ════════════════════════════════════════════════════════════════════════
MAX_INJECTED_LESSONS: int = 5
"""注入上限（design 决策#5「At most five lessons are rendered」）。

与 ``retrieve_lessons(max_lessons=5)`` 默认对齐；上限是 design 硬约束（不可由调用方放大）。
"""

# effectiveness_score 方向（与 catalog._apply_usage_outcomes confidence update 方向一致——
# catalog.py: followed/recurrence_prevented → +CONFIDENCE_UP_BOUND；contradicted/recurrence_observed
# → -CONFIDENCE_DOWN_BOUND；not_observed/unknown → 不变。retrieval ranking 同向）。
_POSITIVE_OUTCOMES: frozenset[str] = frozenset({"followed", "recurrence_prevented"})
_NEGATIVE_OUTCOMES: frozenset[str] = frozenset({"contradicted", "recurrence_observed"})

_VALID_CATALOG_STATES: frozenset[str] = frozenset({"active", "conflicted", "superseded", "retired"})

# 受控 AppliesWhenTag 词表（防 task_metadata.tags 含超词表值后污染 overlap 计算）
_VALID_APPLIES_TAGS: frozenset[str] = frozenset(
    t.value for t in LM.AppliesWhenTag if t != LM.AppliesWhenTag.UNKNOWN)

# profile.language → AppliesWhenTag 受控映射（保守：未列出值不加 tag）
_LANGUAGE_TAG_MAP: dict[str, str] = {
    "python": "python", "py": "python",
    "typescript": "typescript", "ts": "typescript",
    "go": "golang", "golang": "golang",
}

# profile.dependency_management 取值（保守：仅识别常见工具名 → dependency_mgmt tag）
_DEPMGMT_VALUES: frozenset[str] = frozenset({
    "pip", "poetry", "pdm", "uv", "pipenv",
    "npm", "yarn", "pnpm",
    "go_modules", "gomod",
    "cargo",
    "maven", "gradle",
})


# ════════════════════════════════════════════════════════════════════════
# 结果对象（fail-open 通信契约）
# ════════════════════════════════════════════════════════════════════════
@dataclasses.dataclass(frozen=True)
class RetrievalResult:
    """``retrieve_lessons`` 结果（fail-open：selected=() + degraded_class=None 也合法）。

    Attributes:
        selected: 按 ranking 排序后 cap ``max_lessons`` 的 entry dict tuple（byte-stable）。
        selected_lesson_ids: ``selected`` 的 lesson_id tuple（同序），供 coordinator（Section 7）
            记入 dispatch record，terminal learning step（Section 4 reflection + Section 6
            effectiveness）据此评估每个 injected lesson 的 outcome。
        degraded_class: ``None`` 正常；retrieval 自身**永不** raise degraded（fail-open）；该字段
            预留给 ``load_catalog_for_retrieval`` 上游 catalog 不可用场景经 ``retrieve_from_source``
            透传。``retrieve_lessons`` 直接调用时永远返回 ``None``。
        filtered_count: 经 filter 后、cap 前的候选数（监控用，区分「无候选」与「命中 cap」）。
    """
    __test__: ClassVar[bool] = False
    selected: tuple[dict, ...]
    selected_lesson_ids: tuple[str, ...]
    degraded_class: str | None = None
    filtered_count: int = 0


@dataclasses.dataclass(frozen=True)
class RetrievalSource:
    """``load_catalog_for_retrieval`` 结果（fail-open：catalog 故障 → entries=() + degraded_class）。

    design 决策#7「delivery fail-open」+「memory fail-closed」：catalog 不存在/损坏时**绝不抛主路径**，
    返回空 entries + degraded_class；调用方据 degraded_class 记 ``learning_memory_degraded`` 继续跑。
    retrieval 故障绝不阻断 dispatch（``retrieve_from_source`` 把空 entries 透传成空 selected）。

    Attributes:
        entries: catalog ``entries`` tuple（正常）/ ``()``（catalog 不可用或损坏）。
        degraded_class: ``None`` 正常 / ``"catalog_unavailable"``（文件不存在）/
            ``"catalog_read_error"``（JSONDecodeError / entries 字段缺失等）。
    """
    __test__: ClassVar[bool] = False
    entries: tuple[dict, ...]
    degraded_class: str | None = None


# ════════════════════════════════════════════════════════════════════════
# task 5.1：derive_task_metadata（确定性派生，零 SDK）
# ════════════════════════════════════════════════════════════════════════
def derive_task_metadata(*, project_profile: dict, prd: dict, project_id: str) -> dict:
    """task 5.1：从 profile + PRD 确定性派生检索元数据（纯函数，零 LLM）。

    design 决策#5 原文：「At dispatch entry, the control plane derives task metadata from the project
    profile and PRD: project ID, language or surface hints, acceptance categories, lifecycle stage,
    and declared paths when present」+ Open Question「Which existing PRD/profile fields are reliable
    enough for initial applicability tags without adding a new semantic classification call at
    dispatch time?」—— 本函数是 V1 回答：**保守映射**，仅高置信字段入 tag，不确定不加。

    **保守原则**（避免假阳性注入）：只读显式字段；缺字段 / 值未列出 / 类型不匹配 → **不加 tag**。
    假阳性 tag 会让无关 lesson 通过 applicability filter，浪费 5 个槽位甚至误导 dev。

    **profile 字段假设**（Section 7 接线对齐）::

        profile.language: str ∈ {"python", "typescript", "golang"} → tags += "python"/"typescript"/"golang"
        profile.primary_language: str     # language 别名（language 缺失时回退）
        profile.primary: str              # language 二次回退（兼容 pa project profile）
        profile.has_ci: bool = True       # 显式 CI 标记 → tags += "ci_gate"
        profile.ci_config: dict (非空)    # CI 配置 presence → tags += "ci_gate"
        profile.dependency_management: str ∈ {pip/poetry/npm/yarn/go_modules/cargo/maven/gradle/...}
                                         # → tags += "dependency_mgmt"
        profile.dependency_management: bool=True / dict (非空) → tags += "dependency_mgmt"
        profile.declared_paths: list[str] # 路径提示（供 non_applicability_when 文本匹配）

    **prd 字段假设**（Section 7 接线对齐）::

        prd.acceptance_criteria: list[str] | list[dict]
            # 含子串 "test"（不区分大小写）→ tags += "test_infra"
            # dict 元素读 ``category`` + ``description`` 两字段拼成文本参与匹配
        prd.acceptance_categories: list[str]   # 透传到返回（供 non_applicability_when 匹配）
        prd.lifecycle_stage: str               # 透传（供 non_applicability_when 匹配）
        prd.declared_paths: list[str]          # 透传（profile 无 paths 时回退）

    Args:
        project_profile: pa project profile dict（coordinator 接线时传真实 profile）。
        prd: immutable PRD dict（coordinator 接线时传真实 prd）。
        project_id: scope 隔离 ID（V1 项目内 promotion + retrieval；spec「Cross-project/global lesson
            promotion in V1」是 Non-Goal）。

    Returns:
        ``{"project_id": str, "tags": set[str], "acceptance_categories": tuple[str, ...],
        "lifecycle_stage": str, "declared_paths": tuple[str, ...]}``。
        tags 仅含受控 AppliesWhenTag value（防超词表污染 overlap 计算）。
    """
    profile = project_profile if isinstance(project_profile, dict) else {}
    prd_dict = prd if isinstance(prd, dict) else {}
    tags: set[str] = set()

    # ── language → python/typescript/golang（保守：仅受控值入 tag）──────────
    lang = _str_lower(profile.get("language")) or _str_lower(profile.get("primary_language")) \
        or _str_lower(profile.get("primary"))
    if lang:
        mapped = _LANGUAGE_TAG_MAP.get(lang)
        if mapped:
            tags.add(mapped)
    # 未列出 / 缺失 → 不加 tag（保守；防假阳性注入）

    # ── CI 配置 presence → ci_gate ────────────────────────────────────────
    ci_cfg = profile.get("ci_config")
    if (isinstance(ci_cfg, dict) and ci_cfg) or profile.get("has_ci") is True:
        tags.add("ci_gate")

    # ── dependency_management → dependency_mgmt（保守：仅识别常见工具名）──
    dep = profile.get("dependency_management")
    if isinstance(dep, str) and dep.strip().lower() in _DEPMGMT_VALUES:
        tags.add("dependency_mgmt")
    elif isinstance(dep, bool) and dep:
        tags.add("dependency_mgmt")
    elif isinstance(dep, dict) and dep:
        tags.add("dependency_mgmt")

    # ── acceptance_criteria 含 "test" → test_infra ───────────────────────
    ac = prd_dict.get("acceptance_criteria") or ()
    if isinstance(ac, (list, tuple)):
        for item in ac:
            text = ""
            if isinstance(item, str):
                text = item
            elif isinstance(item, dict):
                text = f"{item.get('category', '')} {item.get('description', '')}"
            if "test" in text.lower():
                tags.add("test_infra")
                break

    # ── 透传 acceptance_categories / lifecycle_stage / declared_paths ────
    cats: list[str] = []
    raw_cats = prd_dict.get("acceptance_categories") or ()
    if isinstance(raw_cats, (list, tuple)):
        cats = [str(c).strip() for c in raw_cats
                if isinstance(c, (str, int, float)) and str(c).strip()]

    stage = ""
    raw_stage = prd_dict.get("lifecycle_stage")
    if isinstance(raw_stage, str) and raw_stage.strip():
        stage = raw_stage.strip()

    paths: list[str] = []
    raw_paths = prd_dict.get("declared_paths") or profile.get("declared_paths") or ()
    if isinstance(raw_paths, (list, tuple)):
        paths = [str(p).strip() for p in raw_paths
                 if isinstance(p, (str, int, float)) and str(p).strip()]

    return {
        "project_id": project_id,
        "tags": tags,
        "acceptance_categories": tuple(cats),
        "lifecycle_stage": stage,
        "declared_paths": tuple(paths),
    }


def _str_lower(v) -> str:
    """helpers：把 v 安全转小写 str（None/非 str → ""，不抛）。保守映射的基础。"""
    if not isinstance(v, str):
        return ""
    return v.lower().strip()


# ════════════════════════════════════════════════════════════════════════
# task 5.2：retrieve_lessons（filter + rank，stable tie-break）
# ════════════════════════════════════════════════════════════════════════
def retrieve_lessons(catalog_entries: list[dict], task_metadata: dict,
                     *, max_lessons: int = MAX_INJECTED_LESSONS) -> RetrievalResult:
    """task 5.2：项目本地 filter + rank + cap（纯函数，fail-open，零 SDK）。

    **filter**（task 5.5 反例全覆盖；任一不通过 → 排除）::
        1. malformed（缺 lesson_id/project_id/state 等关键字段 / confidence 超界 / state 非法）→ 排除
        2. state != "active"（conflicted/superseded/retired）→ 排除（design 决策#7「retired lessons
           remain replayable but are excluded from retrieval」）
        3. project_id != task_metadata.project_id → 排除（V1 项目内 scope）
        4. non_applicability_when 命中 task_metadata（任一 acceptance_categories / lifecycle_stage /
           declared_paths 子串匹配）→ 排除（boundary 命中）
        5. applies_when_tags 非空且与 task_metadata.tags 无 overlap → 排除（unrelated）
           **注**：``applies_when_tags`` 为空（``__unscoped__``）→ **保留**（通用 lesson，不因 tag
           缺失被排除；spec 决策#4 applicability_signature 兜底 ``__unscoped__``）。

    **rank**（design 决策#5 顺序，stable tie-break by lesson_id ASC）::
        key = (applicability_overlap DESC, verified_support_count DESC,
               effectiveness_score DESC, confidence DESC, recency_rank DESC, lesson_id ASC)

    实现用 Python ``list.sort`` 稳定排序的多 pass（从最次要 → 最主要；stable sort 保留前次序作 tie-break）。
    ``recency_rank``：``last_outcome_ts``（ISO8601 字典序 = 时间序，None → "" 视为最低）。

    **cap**：``max_lessons`` 默认 ``MAX_INJECTED_LESSONS=5``（design「At most five」），**不接受 >
    5 的值**（design 硬上限——调用方传更大也 cap 到 5，防 accidentally bypass 上限）。

    Args:
        catalog_entries: catalog ``entries`` list（``load_catalog_file`` 返回的 ``catalog["entries"]``）。
        task_metadata: ``derive_task_metadata`` 返回的 dict。
        max_lessons: cap 上限；默认 5（design 硬上限）。传入 > 5 自动 cap 到 5；传入 < 0 视为 0。

    Returns:
        ``RetrievalResult``——``selected`` / ``selected_lesson_ids`` 按 ranking 排序；
        ``filtered_count`` 是 filter 后 cap 前的候选数（监控用）。
    """
    if not isinstance(catalog_entries, list):
        catalog_entries = []
    if not isinstance(task_metadata, dict):
        task_metadata = {}
    # design 硬上限：max_lessons 上限 5；下限 0
    cap = max(0, min(MAX_INJECTED_LESSONS, int(max_lessons)))

    task_pid = task_metadata.get("project_id", "")
    task_tags = _coerce_tag_set(task_metadata.get("tags"))

    # ── filter：依次 malformed → state → project_id → non_applicability → applicability ──
    filtered: list[dict] = []
    for entry in catalog_entries:
        if not isinstance(entry, dict) or _is_malformed(entry):
            continue
        if entry.get("state") != "active":
            continue
        if entry.get("project_id") != task_pid:
            continue
        if _matches_non_applicability(entry.get("non_applicability_when", ""), task_metadata):
            continue
        entry_tags = _coerce_tag_set(entry.get("applies_when_tags"))
        if entry_tags and not (entry_tags & task_tags):
            continue   # 有 scope 但不 overlap → unrelated（__unscoped__ 空集保留）
        filtered.append(entry)

    # ── rank：多 pass stable sort（最次要 → 最主要；末次序 = tie-break）─────────
    # 1) 最次要：lesson_id ASC（最终 tie-break）
    filtered.sort(key=lambda e: str(e.get("lesson_id", "")))
    # 2) recency DESC（ISO8601 字典序 = 时间序；None → "" 最低）
    filtered.sort(key=lambda e: str(e.get("last_outcome_ts") or ""), reverse=True)
    # 3) confidence DESC
    filtered.sort(key=lambda e: float(e.get("confidence", 0.0)), reverse=True)
    # 4) effectiveness_score DESC（与 catalog confidence update 同向）
    filtered.sort(key=lambda e: _effectiveness_score(e), reverse=True)
    # 5) verified_support_count DESC
    filtered.sort(key=lambda e: int(e.get("verified_support_count", 0)), reverse=True)
    # 6) 最主要：applicability_overlap DESC
    filtered.sort(key=lambda e: _applicability_overlap(e, task_tags), reverse=True)

    # ── cap ──
    selected = tuple(filtered[:cap])
    selected_ids = tuple(str(e.get("lesson_id", "")) for e in selected)
    return RetrievalResult(
        selected=selected,
        selected_lesson_ids=selected_ids,
        degraded_class=None,    # retrieve_lessons 自身永不 degrade；上游 catalog 故障经 retrieve_from_source 透传
        filtered_count=len(filtered),
    )


def retrieve_from_source(source: RetrievalSource, task_metadata: dict,
                         *, max_lessons: int = MAX_INJECTED_LESSONS) -> RetrievalResult:
    """fail-open 串联：``load_catalog_for_retrieval`` → ``retrieve_lessons``。

    design 决策#7「delivery fail-open」：catalog 故障（``source.degraded_class`` 非 None）→ 直接返回
    ``selected=()`` + 透传 ``degraded_class``，**不调 retrieve_lessons**（无 entries 可检索）。
    coordinator 拿 ``degraded_class`` 记 ``learning_memory_degraded`` 继续跑（不阻断 dispatch）。
    """
    if not isinstance(source, RetrievalSource) or source.degraded_class is not None or not source.entries:
        return RetrievalResult(
            selected=(),
            selected_lesson_ids=(),
            degraded_class=source.degraded_class if isinstance(source, RetrievalSource) else None,
            filtered_count=0,
        )
    result = retrieve_lessons(list(source.entries), task_metadata, max_lessons=max_lessons)
    # retrieve_lessons 内部 degraded_class 永远 None；保留 source 的（None）
    return result


def _coerce_tag_set(value) -> set[str]:
    """把 applies_when_tags / task_metadata.tags 安全规整为 ``set[str]``（仅保留受控词表内值）。

    防 catalog 被灌超词表值后污染 overlap 计算：非 str / 非受控词表的元素全部丢弃（保守）。
    """
    if not isinstance(value, (list, tuple, set, frozenset)):
        return set()
    return {str(t) for t in value if isinstance(t, str) and t in _VALID_APPLIES_TAGS}


def _is_malformed(entry: dict) -> bool:
    """task 5.5 反例：malformed entry 检测（缺关键字段 / confidence 超界 / state 非法）。

    关键字段：``lesson_id`` / ``project_id`` / ``state`` / ``confidence``。
    设计上保守——任一不确定 → 视为 malformed 排除（绝不部分信任，design 决策#7「memory fail-closed」）。
    """
    if not isinstance(entry, dict):
        return True
    lid = entry.get("lesson_id")
    if not isinstance(lid, str) or not lid.strip():
        return True
    pid = entry.get("project_id")
    if not isinstance(pid, str) or not pid.strip():
        return True
    state = entry.get("state")
    if not isinstance(state, str) or state not in _VALID_CATALOG_STATES:
        return True
    conf = entry.get("confidence")
    if not isinstance(conf, (int, float)) or not (0.0 <= float(conf) <= 1.0):
        return True
    return False


def _matches_non_applicability(non_applicability_when: str, task_metadata: dict) -> bool:
    """task 5.5 反例：boundary 命中检测。

    判定：task_metadata 的 acceptance_categories / lifecycle_stage / declared_paths 任一值（小写）
    作为**子串**出现在 ``non_applicability_when``（小写）文本中 → 命中 → 排除该 entry。

    双向都是文本匹配的 conservative reading：boundary 文本通常形如「skip when stage=post_terminal」
    或「skip for path src/legacy」，task_metadata 字段值（如 ``post_terminal`` / ``src/legacy``）
    作子串命中即视为该任务被 boundary 显式排除。
    """
    if not isinstance(non_applicability_when, str) or not non_applicability_when.strip():
        return False
    text = non_applicability_when.lower()
    for c in task_metadata.get("acceptance_categories", ()) or ():
        if isinstance(c, str) and c.strip() and c.lower() in text:
            return True
    stage = task_metadata.get("lifecycle_stage", "")
    if isinstance(stage, str) and stage.strip() and stage.lower() in text:
        return True
    for p in task_metadata.get("declared_paths", ()) or ():
        if isinstance(p, str) and p.strip() and p.lower() in text:
            return True
    return False


def _applicability_overlap(entry: dict, task_tags: set[str]) -> int:
    """``len(set(entry.applies_when_tags) ∩ task_tags)``（int；空 entry tags → 0，但 filter 已保留）。"""
    entry_tags = _coerce_tag_set(entry.get("applies_when_tags"))
    return len(entry_tags & task_tags)


def _effectiveness_score(entry: dict) -> int:
    """effectiveness_history 派生 score（与 catalog confidence update 方向一致）。

    catalog._apply_usage_outcomes：followed/recurrence_prevented → ``+CONFIDENCE_UP_BOUND``；
    contradicted/recurrence_observed → ``-CONFIDENCE_DOWN_BOUND``；not_observed/unknown → 不变。
    retrieval ranking 同向：``+1 / -1 / 0``（相对值即可，无需乘常量）。
    """
    history = entry.get("effectiveness_history") or ()
    if not isinstance(history, (list, tuple)):
        return 0
    score = 0
    for u in history:
        if not isinstance(u, dict):
            continue
        outcome = str(u.get("outcome", ""))
        if outcome in _POSITIVE_OUTCOMES:
            score += 1
        elif outcome in _NEGATIVE_OUTCOMES:
            score -= 1
        # not_observed / unknown / 其他 → 0
    return score


# ════════════════════════════════════════════════════════════════════════
# task 5.3：render_lesson_block（简洁 checklist，严格排除证据/叙事）
# ════════════════════════════════════════════════════════════════════════
def render_lesson_block(selected_entries: tuple[dict, ...] | list[dict]) -> str:
    """task 5.3：渲染 ≤5 lessons 为简洁 markdown checklist。

    design 决策#5 原文：「At most five lessons are rendered as concise trigger/action/boundary
    checklist entries. Evidence bodies and historical narratives are not injected.」

    **每条只含**（4 字段）：
        * ``lesson_id``（粗体前缀）
        * ``trigger``（applicability trigger）
        * ``corrective_action``（可执行 corrective step）
        * ``non_applicability_when``（boundary）

    **严格排除**（design 决策#5「Evidence bodies and historical narratives are not injected」）：
        ``evidence_refs`` / ``source_candidate_ids`` / ``effectiveness_history`` /
        ``pattern_description`` / ``supporting_prd_ids`` / ``equivalence_key`` / ``confidence`` /
        ``usage_count`` / ``contradiction_count`` / ``verified_support_count`` / ``last_outcome_ts`` /
        ``schema_version`` / 任何历史叙事或证据正文。

    格式样例（``len(selected) == 2``）::

        ## Applicable lessons from prior PRDs (apply where relevant)
        - **lesson_a** — trigger: when X happens
          - action: do Y to prevent recurrence
          - skip when: stage=post_terminal
        - **lesson_b** — trigger: when Z happens
          - action: do W
          - skip when: path=src/legacy

    Args:
        selected_entries: ``retrieve_lessons`` 返回的 ``selected`` tuple（或 list）。空 → 返回 ``""``。

    Returns:
        markdown 字符串；``len(selected_entries) == 0`` → ``""``（inject 时 no-op）。
    """
    if not isinstance(selected_entries, (tuple, list)) or not selected_entries:
        return ""
    lines: list[str] = ["## Applicable lessons from prior PRDs (apply where relevant)"]
    for entry in selected_entries:
        if not isinstance(entry, dict):
            continue
        lid = str(entry.get("lesson_id", "")).strip()
        trigger = str(entry.get("trigger", "")).strip()
        action = str(entry.get("corrective_action", "")).strip()
        skip = str(entry.get("non_applicability_when", "")).strip()
        lines.append(f"- **{lid}** — trigger: {trigger}")
        lines.append(f"  - action: {action}")
        lines.append(f"  - skip when: {skip}")
    return "\n".join(lines)


# ════════════════════════════════════════════════════════════════════════
# task 5.4：inject_into_prompt（纯字符串拼接，no-op on empty）
# ════════════════════════════════════════════════════════════════════════
def inject_into_prompt(dev_prompt: str, lesson_block: str) -> str:
    """task 5.4：把 lesson_block 注入 dev_prompt（纯函数）。

    design 决策#5 原文：「The prompt records injected lesson IDs so terminal processing can evaluate
    their outcomes」—— lesson ID 记录由 coordinator（Section 7 接线）写 dispatch record，不在本函数。

    **no-op on empty**（fail-open delivery）：``lesson_block == ""`` → 原样返回 ``dev_prompt``
    （design 决策#7「fail open for delivery」：retrieval 故障 → 不注入 → dispatch 正常跑）。

    **append 策略**：非空 lesson_block → append 到 dev_prompt 末尾，用两个换行分隔。选择 append
    而非插中间，避免解析 dev_prompt 结构（控制面构造的字符串可能各项目异构）；docstring 显式标注
    供 Section 7 接线方对齐。**不改 dispatch 主路径语义**——dev_prompt 仍是控制面构造的字符串，
    lesson_block 是补充 checklist 而非覆盖指令。

    **控制/目标平面隔离**（ADR-0001）：本函数只做字符串拼接，**绝不写目标 worktree / commit / PR /
    不可变 PRD**；返回的字符串由 coordinator 经 dev-agent SDK 传给目标仓 dev loop。

    Args:
        dev_prompt: 控制面构造的 dev prompt 字符串。
        lesson_block: ``render_lesson_block`` 返回的 markdown checklist（可能为 ``""``）。

    Returns:
        ``lesson_block == ""`` → ``dev_prompt`` 原样（identity，no-op）；
        否则 → ``dev_prompt + "\\n\\n" + lesson_block``。
    """
    if not isinstance(lesson_block, str) or not lesson_block.strip():
        return dev_prompt
    if not isinstance(dev_prompt, str):
        dev_prompt = ""
    return f"{dev_prompt}\n\n{lesson_block}"


# ════════════════════════════════════════════════════════════════════════
# task 5.5：load_catalog_for_retrieval（fail-open wrapper）
# ════════════════════════════════════════════════════════════════════════
def load_catalog_for_retrieval(state_dir: str | Path, project_id: str) -> RetrievalSource:
    """task 5.5 fail-open wrapper：读 catalog 文件 → ``RetrievalSource``（**绝不抛主路径**）。

    design 决策#7「delivery fail-open」+「memory fail-closed」：
        * catalog 不存在（``load_catalog_file`` 返回 None）→ ``entries=()`` +
          ``degraded_class="catalog_unavailable"``（首跑 / catalog 重建中 / 项目无 lessons）；
        * catalog 读异常（JSONDecodeError / OSError / entries 字段缺失）→ ``entries=()`` +
          ``degraded_class="catalog_read_error"``（绝不部分信任 corrupted catalog，design「A malformed
          catalog is skipped rather than partially trusted」）；
        * 正常 → ``entries=catalog["entries"]`` + ``degraded_class=None``。

    **retrieval 故障绝不阻断 dispatch**：coordinator 据 ``degraded_class`` 记 ``learning_memory_degraded``
    事件后照常跑 dispatch；``retrieve_from_source`` 把空 entries 透传成空 selected → ``inject_into_prompt``
    no-op → dev prompt 原样传入 dev loop。

    Args:
        state_dir: ``.project-auto/state`` 根（coordinator 传入）。
        project_id: 项目 ID（catalog 路径 scope）。

    Returns:
        ``RetrievalSource``（永不 raise；catalog 故障 → 空 entries + degraded_class）。
    """
    try:
        catalog = LMCat.load_catalog_file(state_dir, project_id)
    except (json.JSONDecodeError, OSError, ValueError) as e:  # noqa: F841
        # JSONDecodeError：catalog 文件 corrupted（design「malformed catalog is skipped」）
        # OSError：IO 故障（权限 / 磁盘 / 路径）
        # ValueError：JSON 字段类型异常（如 entries 非 list）
        return RetrievalSource(entries=(), degraded_class="catalog_read_error")
    if catalog is None:
        return RetrievalSource(entries=(), degraded_class="catalog_unavailable")
    raw_entries = catalog.get("entries")
    if not isinstance(raw_entries, list):
        return RetrievalSource(entries=(), degraded_class="catalog_read_error")
    return RetrievalSource(entries=tuple(raw_entries), degraded_class=None)
