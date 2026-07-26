#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""learning_memory_schema.py — add-cross-prd-learning-memory Section 1 contract 模块。

锁定 task 1.1（versioned dataclass/enum）、task 2.3 派生的机械 equivalence_key 函数、task 1.4
（model-authored 字段在 schema 边界被 redact）。

spec「Structured and evidence-grounded candidates」+ design 决策#4 的硬约束：
    * schema-constrained enum 字段（``phase``/``failure_class``/``corrective_action_class``/``applies_when_tags``）
      + bounded free-text ``corrective_action``（可执行 corrective step，audit + prompt injection 用）
      + free-text ``pattern_description``（audit only，不进 prompt）
      + applicability + non-applicability boundaries（reusable trigger）
      + evidence references + source outcome + confidence
    * schema MUST NOT accept model-authored ``pattern_key``/``equivalence_key``：必须由 enum 字段机械派生
    * ``invariant_class``（如有）是 audit-only label，不进 equivalence_key、不触发 promotion
    * any enum value of ``unknown`` 永不参与 equivalence/merge/promotion
    * 缺证据 / 任务摘要无 reusable trigger / 无 executable corrective_action / 枚举超词表 / 字段超长 → 拒绝

equivalence_key 公式（design 决策#4 + tasks 2.3）::

    project_id + ':' + sha256(json.dumps(
        (canonical(phase), canonical(failure_class), canonical(corrective_action_class),
         applicability_signature),
        separators=(',',':'), sort_keys=True))[:16]

    canonical(t) = lower(str(t)).replace('-', '_').strip()
    applicability_signature = sorted(set(canonical(t) for t in applies_when_tags)) or '__unscoped__'

**纯 stdlib 新模块**（零 SDK 模块级导入，零 IO）——cron 隔离不变；frozen dataclass + ``__test__=False``
ClassVar 防 pytest 收集告警（同 ``loop_state`` 既定模式）。本 section 只定义 schema，不写 state。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, ClassVar


# ════════════════════════════════════════════════════════════════════════
# bounded field sizes（spec「under a schema length limit」；schema 构造时 enforce）
# ════════════════════════════════════════════════════════════════════════
MAX_CORRECTIVE_ACTION_LEN = 500           # 可执行 corrective step（prompt 用）——短、聚焦、可执行
MAX_PATTERN_DESCRIPTION_LEN = 1000        # audit-only 自由描述——稍宽，但不无界
MAX_APPLICABILITY_LEN = 500               # applicability / non-applicability 边界文本
MAX_PATTERN_KEY_LEN = 200                 # invariant_class 等 audit-only 短标签


# ════════════════════════════════════════════════════════════════════════
# 受控枚举（spec「schema-constrained enum fields」）
# ════════════════════════════════════════════════════════════════════════
class Phase(str, Enum):
    """dev lifecycle 阶段（spec schema 字段）。``UNKNOWN`` 永不参与 equivalence/合并/晋升。"""
    IMPLEMENT = "implement"            # 实现阶段
    VERIFY = "verify"                  # 验证阶段
    PUBLISH = "publish"                # 发布阶段
    POST_TERMINAL = "post_terminal"    # 终态后反思阶段
    UNKNOWN = "unknown"                # 永不参与 equivalence/合并/晋升（spec「unknown MUST NOT participate」）


class FailureClass(str, Enum):
    """失败大类——对齐终态 evidence class（design 決策#1 表 + 终态集合）。

    ``UNKNOWN`` 永不参与 equivalence/合并/晋升：spec「An unknown enum value MUST NOT participate in implicit
    merging」。覆盖 verifier_invariant_violation / gate_blocked / stalled / external_state_unknown /
    sandbox_violation / abort / state_corrupt 七类与终态 evidence 对齐的机械分类。
    """
    VERIFIER_INVARIANT_VIOLATION = "verifier_invariant_violation"
    GATE_BLOCKED = "gate_blocked"
    STALLED = "stalled"
    EXTERNAL_STATE_UNKNOWN = "external_state_unknown"
    SANDBOX_VIOLATION = "sandbox_violation"
    ABORT = "abort"
    STATE_CORRUPT = "state_corrupt"
    UNKNOWN = "unknown"


class CorrectiveActionClass(str, Enum):
    """可执行 corrective step 大类（参与 equivalence_key）。

    注：spec「The ``corrective_action_class`` enum participates only in the equivalence key」——枚举值
    参与等效判定；具体可执行内容由 ``corrective_action`` 文本携带。
    """
    ADD_TEST = "add_test"               # 补回归（覆盖被跳过的边界）
    FIX_PATTERN = "fix_pattern"          # 修代码 pattern（错抽象/漏校验）
    GUARD_BOUNDARY = "guard_boundary"    # 加边界保护（precondition/invariant）
    CONFIG_CHANGE = "config_change"      # 配置/环境调整
    UNKNOWN = "unknown"                  # 永不参与 equivalence/合并/晋升


class AppliesWhenTag(str, Enum):
    """项目无关的技术标签——``applies_when_tags`` 元素的受控词表（spec「project-agnostic tags」）。

    跨项目通用的技术域标签：语言、CI/CD、测试基础设施、依赖管理等。``UNKNOWN`` 不可入 catalog。
    """
    PYTHON = "python"
    TYPESCRIPT = "typescript"
    GOLANG = "golang"
    CI_GATE = "ci_gate"
    TEST_INFRA = "test_infra"
    DEPENDENCY_MGMT = "dependency_mgmt"
    UNKNOWN = "unknown"


# 受控词汇集合（candidate 构造时校验枚举值命中——防超词表）
_VALID_PHASES: frozenset[str] = frozenset(p.value for p in Phase if p != Phase.UNKNOWN)
_VALID_FAILURE_CLASSES: frozenset[str] = frozenset(f.value for f in FailureClass if f != FailureClass.UNKNOWN)
_VALID_CORRECTIVE_ACTION_CLASSES: frozenset[str] = frozenset(
    c.value for c in CorrectiveActionClass if c != CorrectiveActionClass.UNKNOWN)
_VALID_APPLIES_WHEN_TAGS: frozenset[str] = frozenset(
    t.value for t in AppliesWhenTag if t != AppliesWhenTag.UNKNOWN)


# ════════════════════════════════════════════════════════════════════════
# task 2.3：机械 equivalence_key 派生（公开放 schema 模块，因依赖 enum）
# ════════════════════════════════════════════════════════════════════════
def canonical(t: Any) -> str:
    """canonical 公式（design 决策#4）：``lower(str(t)).replace('-', '_').strip()``。

    kebab/lowercase 输入与 enum value byte-equal——枚举值 ``add-test`` 与 ``add_test`` 等价。
    """
    return lower(str(t)).replace('-', '_').strip()


def lower(s: str) -> str:
    """lowercase（独立函数便于 monkeypatch 测试，与 design 公式严格对齐）。"""
    return s.lower()


def _applicability_signature(tags: tuple) -> list[str]:
    """applicability_signature = ``sorted(set(canonical(t) for t in tags))``；空 → ``['__unscoped__']``。

    spec：「ordering of applies_when_tags is the only model-permitted freedom and does not affect
    equivalence」——set 去序，sorted 稳定。``__unscoped__`` 兜底防空集导致 key 退化（所有空 tags 共享同
    signature 仍是合法的「unscoped」等价类）。
    """
    sig = sorted({canonical(t) for t in tags})
    return sig or ["__unscoped__"]


def derive_equivalence_key(candidate: "LessonCandidate") -> str:
    """task 2.3 公式（design 决策#4）：机械派生 deterministic equivalence_key。

    ``project_id + ':' + sha256(json.dumps((canonical(phase), canonical(failure_class),
    canonical(corrective_action_class), applicability_signature), separators=(',',':'),
    sort_keys=True))[:16]``

    两条 candidate byte-equal 当且仅当 equivalence_key byte-equal。``project_id`` 前缀把等效判定 scope
    到项目内（V1 项目内 promotion，spec「under a project scope」）。

    不读 ``corrective_action`` 文本 / ``pattern_description`` / ``invariant_class`` / ``evidence_refs`` /
    ``prd_id`` / ``iteration_refs``——这些字段不参与等效（spec「Semantic model output alone MUST NOT
    select equivalence」）。
    """
    payload = (
        canonical(candidate.phase.value if isinstance(candidate.phase, Enum) else candidate.phase),
        canonical(candidate.failure_class.value if isinstance(candidate.failure_class, Enum) else candidate.failure_class),
        canonical(candidate.corrective_action_class.value
                  if isinstance(candidate.corrective_action_class, Enum) else candidate.corrective_action_class),
        _applicability_signature(candidate.applies_when_tags),
    )
    hashed = hashlib.sha256(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()
    return f"{candidate.project_id}:{hashed[:16]}"


# ════════════════════════════════════════════════════════════════════════
# task 1.1：LessonCandidate frozen dataclass + schema 校验
# ════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class LessonCandidate:
    """一条 lesson candidate（spec「Structured and evidence-grounded candidates」）。

    schema-constrained enum 字段：``phase``/``failure_class``/``corrective_action_class``/
    ``applies_when_tags``；bounded free-text ``corrective_action``（可执行，prompt 用）；
    ``pattern_description``（audit only）；``applicability_when`` / ``non_applicability_when``
    （reusable trigger 与边界）；``evidence_refs``（integrity-checked 工件引用）；``source_outcome``；
    ``confidence`` ∈ [0, 1]；``schema_version``。

    ``__post_init__`` enforce schema 边界（spec「MUST reject candidates that ...」）：
        * ``evidence_refs`` 非空（缺证据 → 拒）；
        * ``corrective_action`` 非空（无可执行 corrective → 拒）；
        * ``applicability_when`` 非空（无 reusable trigger → 任务摘要式拒绝）；
        * 所有 enum 字段非 UNKNOWN（unknown 永不参与 equivalence/merge/promotion）；
        * 所有 enum 字段在受控词表内（防超词表）；
        * bounded 文本字段不超长（schema length limit）；
        * ``confidence`` ∈ [0, 1]。

    ``invariant_class`` 是 audit-only label（spec：MUST NOT 驱动 promotion），默认 None；不进 equivalence_key。
    """
    __test__: ClassVar[bool] = False

    project_id: str
    prd_id: str
    iteration_refs: tuple[str, ...]
    phase: Phase
    failure_class: FailureClass
    corrective_action_class: CorrectiveActionClass
    applies_when_tags: tuple[AppliesWhenTag, ...]
    corrective_action: str
    pattern_description: str
    applicability_when: str
    non_applicability_when: str
    evidence_refs: tuple[dict, ...]
    source_outcome: str
    confidence: float
    schema_version: int = 1
    invariant_class: str | None = None        # audit-only（spec：MUST NOT 驱动 promotion）

    def __post_init__(self) -> None:
        # 缺证据 → 拒（spec「lack readable integrity-checked evidence」）
        if not self.evidence_refs:
            raise ValueError("LessonCandidate.evidence_refs 不可为空（spec：必须有 readable evidence）")
        # 无可执行 corrective_action → 拒（spec「do not prescribe an executable corrective action」）
        if not isinstance(self.corrective_action, str) or not self.corrective_action.strip():
            raise ValueError("LessonCandidate.corrective_action 不可为空（spec：必须 prescribe executable action）")
        # 无 reusable trigger → 拒（spec「task-specific summaries without a reusable trigger」）
        if not isinstance(self.applicability_when, str) or not self.applicability_when.strip():
            raise ValueError("LessonCandidate.applicability_when 不可为空（spec：必须有 reusable trigger）")
        if not isinstance(self.non_applicability_when, str) or not self.non_applicability_when.strip():
            raise ValueError("LessonCandidate.non_applicability_when 不可为空（spec：必须有 boundary）")
        # enum 字段非 UNKNOWN + 命中受控词表（spec「out-of-vocabulary enum values」与「unknown MUST NOT participate」）
        _assert_enum_not_unknown_and_in_vocab("phase", self.phase, Phase, _VALID_PHASES)
        _assert_enum_not_unknown_and_in_vocab("failure_class", self.failure_class, FailureClass, _VALID_FAILURE_CLASSES)
        _assert_enum_not_unknown_and_in_vocab(
            "corrective_action_class", self.corrective_action_class,
            CorrectiveActionClass, _VALID_CORRECTIVE_ACTION_CLASSES)
        if not isinstance(self.applies_when_tags, tuple):
            raise ValueError("LessonCandidate.applies_when_tags 必须是 tuple")
        for t in self.applies_when_tags:
            _assert_enum_not_unknown_and_in_vocab("applies_when_tags", t, AppliesWhenTag, _VALID_APPLIES_WHEN_TAGS)
        # bounded 文本字段长度上限（spec「schema length limit」）
        if len(self.corrective_action) > MAX_CORRECTIVE_ACTION_LEN:
            raise ValueError(
                f"corrective_action 超 schema 长度上限 ({len(self.corrective_action)} > {MAX_CORRECTIVE_ACTION_LEN})")
        if len(self.pattern_description) > MAX_PATTERN_DESCRIPTION_LEN:
            raise ValueError(
                f"pattern_description 超 schema 长度上限 ({len(self.pattern_description)} > {MAX_PATTERN_DESCRIPTION_LEN})")
        if len(self.applicability_when) > MAX_APPLICABILITY_LEN or len(self.non_applicability_when) > MAX_APPLICABILITY_LEN:
            raise ValueError(f"applicability 文本超 schema 长度上限 ({MAX_APPLICABILITY_LEN})")
        # confidence ∈ [0, 1]
        if not isinstance(self.confidence, (int, float)) or not (0.0 <= float(self.confidence) <= 1.0):
            raise ValueError(f"confidence 必须在 [0, 1] 区间，收到 {self.confidence!r}")
        # invariant_class audit-only 长度上限（防 audit 字段被滥用为长文本载体）
        if self.invariant_class is not None and len(str(self.invariant_class)) > MAX_PATTERN_KEY_LEN:
            raise ValueError(f"invariant_class 超 audit-only 长度上限 ({MAX_PATTERN_KEY_LEN})")


def _assert_enum_not_unknown_and_in_vocab(field_name: str, value: Any,
                                          enum_cls: type[Enum], valid_values: frozenset[str]) -> None:
    """enum 字段校验：必须是 Enum 成员、非 UNKNOWN、值在受控词表（防 model 输出超词表）。

    spec：「MUST reject candidates that ... carry any enum value outside the controlled vocabulary」
    +「An unknown enum value MUST NOT participate in implicit merging」。
    """
    # 接受 Enum 成员或字符串值（candidate_from_model_output 从 dict 构造时传 string）
    raw_value = value.value if isinstance(value, Enum) else value
    if not isinstance(raw_value, str):
        raise ValueError(f"{field_name} 必须是 {enum_cls.__name__} 或其 string value，收到 {type(value).__name__}")
    if raw_value == "unknown":
        raise ValueError(
            f"{field_name}={raw_value!r}：unknown 永不参与 equivalence/合并/晋升（spec forbid）")
    if raw_value not in valid_values:
        raise ValueError(
            f"{field_name}={raw_value!r} 不在受控词表 {sorted(valid_values)}（spec：超词表拒绝）")


# ════════════════════════════════════════════════════════════════════════
# task 1.4：candidate_from_model_output —— schema 边界 redaction
# ════════════════════════════════════════════════════════════════════════
# model-authored 字段黑名单（spec：「MUST NOT accept a model-authored pattern_key or equivalence_key；
# an invariant_class field, if present, is an audit-only label」）。equivalence_key 由机械派生函数算出，
# project_id 从外部注入。``promotion_count`` / ``storage_path`` 是 system 字段，model 输出含也丢弃。
_MODEL_AUTHORED_DROP: frozenset[str] = frozenset({
    "pattern_key", "equivalence_key", "promotion_count", "storage_path",
})


def candidate_from_model_output(raw: dict, *, project_id: str, prd_id: str,
                                iteration_refs: tuple[str, ...],
                                source_outcome: str | None = None,
                                confidence: float | None = None) -> LessonCandidate:
    """task 1.4：从 model 输出 dict 构造 LessonCandidate，**schema 边界 redact model-authored 字段**。

    spec：「The schema MUST NOT accept a model-authored ``pattern_key`` or ``equivalence_key``; these are
    derived mechanically from the enum fields. An ``invariant_class`` field, if present, is an audit-only
    label asserted by the verifier and MUST NOT drive promotion.」+ task 1.4：「prove a model-authored
    pattern_key, equivalence_key, invariant_class, project_id, promotion count, or storage path is
    redacted at the schema boundary and cannot influence equivalence or promotion」。

    处理：
        * ``pattern_key`` / ``equivalence_key`` / ``promotion_count`` / ``storage_path`` —— **构造时丢弃**
          （equivalence_key 由 ``derive_equivalence_key`` 机械派生；promotion/storage 是 system 字段）；
        * ``project_id`` —— **从外部注入**（防 model 串项目注入；raw 含的 project_id 被丢弃）；
        * ``invariant_class`` —— **保留为 audit-only 字段**（不进 equivalence_key；spec：MUST NOT 驱动 promotion）；
        * 其余 enum 字段（``phase``/``failure_class``/``corrective_action_class``/``applies_when_tags``）
          经 ``_assert_enum_not_unknown_and_in_vocab`` 受控词表校验。

    raw dict 的 enum 字段接受 ``kebab-case`` string（``add-test``）或 snake_case string（``add_test``）
    或 Enum 成员——``canonical`` 公式做归一化，byte-equal 即等价。

    Args:
        raw: model 输出 dict（含 schema 字段 + 可能的 model-authored 字段，后者被 redact）。
        project_id: 从外部注入（dispatch context 真源，不可由 model 灌）。
        prd_id: 从外部注入。
        iteration_refs: 从外部注入（journal iteration_id 真源）。
        source_outcome: 覆盖 raw 中的 source_outcome（默认 None 时取 raw 值）。
        confidence: 覆盖 raw 中的 confidence（默认 None 时取 raw 值）。

    Returns:
        schema-valid LessonCandidate（model-authored 字段已被 redact）。

    Raises:
        ValueError: 任一 schema 边界违反（缺证据 / enum 超词表 / enum=unknown / 字段超长 / ...）。
    """
    if not isinstance(raw, dict):
        raise ValueError(f"candidate_from_model_output: raw 必须是 dict，收到 {type(raw).__name__}")
    # task 1.4 redaction：显式不读 model-authored 字段（即便 raw 含，构造时也丢弃）。
    # 仅 invariant_class 保留（audit-only）。
    invariant_class = raw.get("invariant_class")
    if isinstance(invariant_class, str) and len(invariant_class) > MAX_PATTERN_KEY_LEN:
        raise ValueError(f"invariant_class 超 audit-only 长度上限 ({MAX_PATTERN_KEY_LEN})")
    return LessonCandidate(
        project_id=project_id,                   # 外部注入（raw.project_id 被丢弃，spec task 1.4）
        prd_id=prd_id,
        iteration_refs=iteration_refs,
        phase=_coerce_enum("phase", raw.get("phase"), Phase),
        failure_class=_coerce_enum("failure_class", raw.get("failure_class"), FailureClass),
        corrective_action_class=_coerce_enum(
            "corrective_action_class", raw.get("corrective_action_class"), CorrectiveActionClass),
        applies_when_tags=tuple(
            _coerce_enum("applies_when_tags", t, AppliesWhenTag) for t in (raw.get("applies_when_tags") or ())),
        corrective_action=_require_str(raw, "corrective_action"),
        pattern_description=_require_str(raw, "pattern_description", allow_empty=True),
        applicability_when=_require_str(raw, "applicability_when"),
        non_applicability_when=_require_str(raw, "non_applicability_when"),
        evidence_refs=tuple(raw.get("evidence_refs") or ()),
        source_outcome=source_outcome if source_outcome is not None else str(raw.get("source_outcome", "")),
        confidence=float(confidence) if confidence is not None else float(raw.get("confidence", 0.0)),
        # invariant_class 保留为 audit-only（spec：MUST NOT 驱动 promotion；不进 equivalence_key）
        invariant_class=invariant_class if isinstance(invariant_class, str) else None,
        # 显式不传：pattern_key/equivalence_key/promotion_count/storage_path（构造时丢弃；LessonCandidate
        # 无对应字段，dataclass 会忽略未知 kwarg？——不会，LessonCandidate 不接受这些字段，故本来就不传）
    )


def _coerce_enum(field_name: str, value: Any, enum_cls: type[Enum]) -> Enum:
    """把 raw model 输出的 enum 字段（string / Enum 成员）规整为 Enum 成员，经 canonical 接受 kebab。

    kebab-case ``add-test`` 与 snake_case ``add_test`` 经 ``canonical`` 归一后等价（byte-equal）。
    超词表 / unknown / 非 str-non-Enum → ValueError。
    """
    if isinstance(value, Enum):
        # 已是 Enum 成员——仍要校验非 UNKNOWN 且在词表（防调用方直接传 UNKNOWN）
        _assert_enum_not_unknown_and_in_vocab(
            field_name, value, enum_cls,
            frozenset(e.value for e in enum_cls if e.value != "unknown"))
        return value
    if not isinstance(value, str):
        raise ValueError(f"{field_name} 必须是 {enum_cls.__name__} 或 string value，收到 {type(value).__name__}")
    # canonical 接受 kebab-case：add-test → add_test
    canon = canonical(value)
    for member in enum_cls:
        if member.value == "unknown":
            continue
        if canonical(member.value) == canon:
            return member
    raise ValueError(
        f"{field_name}={value!r}（canonical={canon!r}）不在受控词表 "
        f"{sorted(e.value for e in enum_cls if e.value != 'unknown')}")


def _require_str(raw: dict, key: str, *, allow_empty: bool = False) -> str:
    """从 raw 取必填 string 字段；非 str → ValueError；空且不允许空 → ValueError。"""
    val = raw.get(key)
    if not isinstance(val, str):
        raise ValueError(f"{key} 必须是 str，收到 {type(val).__name__}")
    if not allow_empty and not val.strip():
        raise ValueError(f"{key} 不可为空")
    return val


# ════════════════════════════════════════════════════════════════════════
# task 1.1：lifecycle / catalog / usage dataclass（spec「Lesson effectiveness feedback and lifecycle」）
# ════════════════════════════════════════════════════════════════════════
class LifecycleEventType(str, Enum):
    """catalog 生命周期事件类型（spec「confirmation, confidence reduction, supersession, and retirement
    without deleting historical facts」）。受控词表，防 model 灌未授权状态。"""
    CONFIRMED = "confirmed"               # 应用成功 → 提升/保持 confidence
    CONFIDENCE_REDUCED = "confidence_reduced"   # 部分被反驳 → 降 confidence
    SUPERSEDED = "superseded"             # 被新版本替代（旧版仍 auditable）
    RETIRED = "retired"                   # 退役（不再注入，但 source/lifecycle 事实保留）
    MERGED = "merged"                     # 跨 PRD 等效合并
    CONFLICTED = "conflicted"             # 冲突 corrective_action（保持 inactive 直到 evidence 解决）
    UNKNOWN = "unknown"                   # 永不参与状态机迁移（防 model 灌未知 lifecycle）


_VALID_LIFECYCLE_EVENT_TYPES: frozenset[str] = frozenset(
    e.value for e in LifecycleEventType if e != LifecycleEventType.UNKNOWN)


class CatalogState(str, Enum):
    """active catalog entry 的状态（spec「active, conflicted, superseded, retired」）。"""
    ACTIVE = "active"
    CONFLICTED = "conflicted"
    SUPERSEDED = "superseded"
    RETIRED = "retired"
    UNKNOWN = "unknown"


_VALID_CATALOG_STATES: frozenset[str] = frozenset(
    s.value for s in CatalogState if s != CatalogState.UNKNOWN)


class UsageOutcomeKind(str, Enum):
    """应用结果分类（spec scenario：prevented recurrence / contradicted / unknown）。

    注：spec 关注 ``action_observed`` + ``failure_recurred`` 两轴，``outcome`` 是机械派生的语义标签。
    """
    FOLLOWED = "followed"                       # action_observed=True, failure_recurred=False
    NOT_OBSERVED = "not_observed"               # action_observed=False, 无证据
    RECURRENCE_PREVENTED = "recurrence_prevented"   # 同 followed 且有显式证据
    RECURRENCE_OBSERVED = "recurrence_observed"     # failure_recurred=True
    CONTRADICTED = "contradicted"               # action 反而触发/未防住 failure
    UNKNOWN = "unknown"


_VALID_USAGE_OUTCOMES: frozenset[str] = frozenset(
    o.value for o in UsageOutcomeKind if o != UsageOutcomeKind.UNKNOWN)


@dataclass(frozen=True)
class LessonLifecycleEvent:
    """append-only lifecycle 事件（spec「confirmation, confidence reduction, supersession, and retirement」）。

    ``event_type`` 受控（``LifecycleEventType``）；``payload`` 自由 dict（按 event_type 解释）；
    ``schema_version`` 演化。绝不删除——retirement 只改 projection（spec「without deleting historical facts」）。
    """
    __test__: ClassVar[bool] = False

    event_id: str
    timestamp: str            # ISO8601（调用方传入，本模块不触时间）
    project_id: str
    lesson_id: str
    event_type: str           # LifecycleEventType.value
    payload: dict
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.event_type not in _VALID_LIFECYCLE_EVENT_TYPES:
            raise ValueError(
                f"event_type={self.event_type!r} 不在受控词表 {sorted(_VALID_LIFECYCLE_EVENT_TYPES)}"
                f"（unknown/超词表禁止，spec：lifecycle 状态机受控）")


@dataclass(frozen=True)
class ActiveCatalogEntry:
    """active catalog projection entry（spec「active lesson」）。

    ``source_candidate_ids`` + ``supporting_prd_ids`` 保留所有来源（spec「Merges preserve all source
    candidate IDs and evidence lineages」）；``state`` 受控（active/conflicted/superseded/retired）。
    ``equivalence_key`` 是 ``derive_equivalence_key`` 派生值的快照（rebuildable）。
    """
    __test__: ClassVar[bool] = False

    lesson_id: str
    project_id: str
    equivalence_key: str
    source_candidate_ids: tuple[str, ...]
    supporting_prd_ids: tuple[str, ...]
    corrective_action: str
    trigger: str                       # applicability trigger（prompt injection 用）
    non_applicability_when: str
    state: str                         # CatalogState.value
    confidence: float
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.state not in _VALID_CATALOG_STATES:
            raise ValueError(
                f"state={self.state!r} 不在受控词表 {sorted(_VALID_CATALOG_STATES)}"
                f"（unknown/超词表禁止）")
        if not isinstance(self.confidence, (int, float)) or not (0.0 <= float(self.confidence) <= 1.0):
            raise ValueError(f"confidence 必须在 [0, 1] 区间，收到 {self.confidence!r}")


@dataclass(frozen=True)
class EvidenceLineage:
    """单 candidate 的 evidence 溯源（spec「Merges preserve all source candidate IDs and evidence lineages」）。

    保留 ``candidate_id`` / ``prd_id`` / ``iteration_id`` / ``evidence_refs`` 全链路，retired 后仍可重放。
    """
    __test__: ClassVar[bool] = False

    lesson_id: str
    candidate_id: str
    prd_id: str
    iteration_id: str
    evidence_refs: tuple[dict, ...]
    schema_version: int = 1


@dataclass(frozen=True)
class UsageOutcome:
    """应用结果记录（spec「record whether the development run exhibited the prescribed action and whether
    the associated failure pattern recurred」）。

    ``action_observed`` + ``failure_recurred`` 是两轴真值（机械判定），``outcome`` 是语义标签（受控词表）。
    spec「Absence of a detectable action is recorded as ``unknown``, not automatically as disobedience」。
    """
    __test__: ClassVar[bool] = False

    event_id: str
    timestamp: str
    project_id: str
    lesson_id: str
    prd_id: str
    action_observed: bool
    failure_recurred: bool
    outcome: str                       # UsageOutcomeKind.value
    evidence_refs: tuple[dict, ...] = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.outcome not in _VALID_USAGE_OUTCOMES:
            raise ValueError(
                f"outcome={self.outcome!r} 不在受控词表 {sorted(_VALID_USAGE_OUTCOMES)}"
                f"（unknown/超词表禁止；spec：usage outcome 受控）")
