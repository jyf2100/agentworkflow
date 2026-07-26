"""loop_state.py — 持久 loop 数据模型 + 显式迭代状态机（OpenSpec add-durable-loop-runtime task 2.1 + 2.6）。

第二阶段把「验证/重试分散在 JSON/日志/被追加的 PRD/git 分支」收敛为 **append-only journal + 显式状态机**
（design 决策#1「append-only journal 为本真源」、#2「显式状态机，SDK 成功≠已发布」）。

    task 2.1（数据模型）—— 版本化 ``JournalEvent``、``IterationState``（reducer 归约出的不可变快照）、
        ``ArtifactRef``（内容寻址工件指针）、``FailureClassification``（RetryPolicy 机械输入）、
        ``RecoverySnapshot``（重试/PreCompact 恢复上下文）、``SessionRunMeta``（SDK session 真源）、
        ``SubagentRecord``（子代理归属与产出）。所有模型 frozen + 可 JSON 序列化。
    task 2.6（状态机）—— ``validate_transition`` 显式迁移表，**拒绝非法/重复迁移**。reducer（``apply_event``/
        ``reduce``）做 dedup（同 event_id 幂等）+ validate（非法迁移 status 不变但记 ``last_transition_error``）。

**spec 核心断言（design 决策#2）**：SDK 成功（``agent_finished``）只能停在 ``AGENT_FINISHED``——
``running``→``published`` 直跳、``agent_finished``→``published`` 直跳都被状态机拦死。必须经
test 门 → ``verifying`` → ``publish_ready`` → ``published``，质量闸一道都不能跳。

纯逻辑零依赖模块（同 ``evidence``/``external_state`` 既定模式）：单测零 IO、零 SDK 导入。
reducer 在本模块提供 status 迁移 + dedup 骨架；payload 字段合并在 task 3.2 增强、journal IO（append/
read/损坏检测）在 ``journal`` 模块（task 2.2/2.3）。
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, ClassVar


# ── journal 事件 schema 版本（每条 event 带；读端按版本路由，不兼容即 fail-closed）──────
JOURNAL_SCHEMA_VERSION = 1


# ════════════════════════════════════════════════════════════════════════
# 枚举（str 子类化便于 JSON 序列化进 journal/state 记录）
# ════════════════════════════════════════════════════════════════════════
class IterationStatus(str, Enum):
    """迭代显式状态机（design 决策#2）。``published`` 是唯一成功交付终态。

    迁移表见 ``_TRANSITIONS``。``state_corrupt`` 是 journal 中部损坏时 reducer 落定的保守终态
    （spec「fail closed on malformed middle」）——需运维介入，不可自动恢复。
    """
    PLANNED = "planned"
    RUNNING = "running"
    AGENT_FINISHED = "agent_finished"     # SDK 跑完（≠ 已发布）；须经 test/verify 门
    TEST_BLOCKED = "test_blocked"         # 发布门拦截（test_not_run/failed/stale）
    VERIFYING = "verifying"               # 进入 outer independent verification
    REVISE = "revise"                     # pa-verify 判红 → 增量重投
    EXTERNAL_BLOCKED = "external_blocked"  # 三态 fail-safe 阻断（reconcile 见 UNKNOWN）
    PUBLISH_READY = "publish_ready"       # 验证绿，待 reconcile_pr 开 PR
    PUBLISHED = "published"               # 唯一成功交付终态
    ABORTED = "aborted"                   # 主动放弃终态
    FAILED = "failed"                     # 重试耗尽/预算用尽终态
    STALLED = "stalled"                   # task 3.5：dev loop 主动刹车终态（连续 N 轮无写类进展 exit 12）
    ORPHAN_DELETED = "orphan_deleted"     # task 3.5：无 commit 孤儿分支清理终态（dev 跑过但无产出）
    BLOCKED_EVIDENCE = "blocked_evidence"  # task 3.5 前向（task 4.2/4.3）：证据无法持久化/校验终态
    SANDBOX_BLOCKED = "sandbox_blocked"    # task 3.5 前向（task 5.2）：沙箱策略安装/校验失败终态
    STATE_CORRUPT = "state_corrupt"       # journal 损坏保守终态（需运维）


# 终态集合：已交付/已废弃/已失败/已停滞/已清理孤儿/已损坏——无任何出向迁移，不可复活。
# task 3.5：stalled/orphan_deleted 是 dispatch 旁路放弃终态（spec scenario 19 terminal class）。
TERMINAL_STATUSES: frozenset[IterationStatus] = frozenset({
    IterationStatus.PUBLISHED,
    IterationStatus.ABORTED,
    IterationStatus.FAILED,
    IterationStatus.STALLED,
    IterationStatus.ORPHAN_DELETED,
    IterationStatus.BLOCKED_EVIDENCE,
    IterationStatus.SANDBOX_BLOCKED,
    IterationStatus.STATE_CORRUPT,
})


# 显式迁移表（design 决策#2）。表外迁移一律拒（含 running→published 直跳——SDK 成功≠已发布）。
_TRANSITIONS: dict[IterationStatus, frozenset[IterationStatus]] = {
    IterationStatus.PLANNED: frozenset({
        IterationStatus.RUNNING,
        IterationStatus.ABORTED,          # planning 阶段放弃（profile 不满足/dev 被跳过，agent 未跑即弃）
        IterationStatus.EXTERNAL_BLOCKED, # task 3.5：admission 阶段三态 fail-safe 阻断（分支保护/幂等/inflight UNKNOWN）
        IterationStatus.SANDBOX_BLOCKED,  # task 3.5 前向（task 5.2）：沙箱策略安装/校验失败
    }),
    IterationStatus.RUNNING: frozenset({
        IterationStatus.AGENT_FINISHED,
        IterationStatus.FAILED,
        IterationStatus.EXTERNAL_BLOCKED,
        IterationStatus.ABORTED,
        IterationStatus.STALLED,           # task 3.5：dev 主动刹车无产出 → 放弃终态
        IterationStatus.ORPHAN_DELETED,    # task 3.5：无 commit 孤儿清理
        IterationStatus.SANDBOX_BLOCKED,   # task 3.5 前向（task 5.2）：沙箱运行时阻断
    }),
    IterationStatus.AGENT_FINISHED: frozenset({
        IterationStatus.TEST_BLOCKED,
        IterationStatus.VERIFYING,
        IterationStatus.FAILED,
        IterationStatus.STALLED,           # task 3.5：agent 跑完但无 commit → 放弃
        IterationStatus.ORPHAN_DELETED,    # task 3.5：无产出孤儿清理
    }),
    IterationStatus.TEST_BLOCKED: frozenset({
        IterationStatus.RUNNING,      # retry：补/修测试后重投
        IterationStatus.ABORTED,
        IterationStatus.FAILED,
    }),
    IterationStatus.VERIFYING: frozenset({
        IterationStatus.PUBLISH_READY,
        IterationStatus.REVISE,
        IterationStatus.EXTERNAL_BLOCKED,
        IterationStatus.FAILED,
        IterationStatus.STALLED,           # task 3.5：verify 闭环中连续无进展 → 放弃
        IterationStatus.BLOCKED_EVIDENCE,  # task 3.5 前向（task 4.2/4.3）：证据无法持久化/校验
    }),
    IterationStatus.REVISE: frozenset({
        IterationStatus.RUNNING,           # 增量重投（反馈进 PRD/artifact，next iteration）
        IterationStatus.STALLED,           # task 3.5：连续 revise 无写类进展 → 重试耗尽放弃
        IterationStatus.ORPHAN_DELETED,    # task 3.5：revise 后无 commit 孤儿清理
    }),
    IterationStatus.EXTERNAL_BLOCKED: frozenset({
        IterationStatus.RUNNING,      # reconciled（远程态变可决断）→ 重投
        IterationStatus.ABORTED,
        IterationStatus.FAILED,
    }),
    IterationStatus.PUBLISH_READY: frozenset({
        IterationStatus.PUBLISHED,
        IterationStatus.EXTERNAL_BLOCKED,   # reconcile_pr 见 UNKNOWN → 阻断不创 PR
        IterationStatus.FAILED,
        IterationStatus.BLOCKED_EVIDENCE,   # task 3.5 前向（task 4.4）：发布前证据 reconcile 无法持久化/校验
    }),
    # 终态：空集（无出向迁移）
    IterationStatus.PUBLISHED: frozenset(),
    IterationStatus.ABORTED: frozenset(),
    IterationStatus.FAILED: frozenset(),
    IterationStatus.STALLED: frozenset(),
    IterationStatus.ORPHAN_DELETED: frozenset(),
    IterationStatus.BLOCKED_EVIDENCE: frozenset(),
    IterationStatus.SANDBOX_BLOCKED: frozenset(),
    IterationStatus.STATE_CORRUPT: frozenset(),
}


class Sensitivity(str, Enum):
    """工件敏感度分层（task 2.5 脱敏 + 落盘/上报策略依据）。

    ``public`` 可外发（diff 摘要）；``sanitized`` 已脱敏（test 输出/verifier 反馈，抹密钥后可落盘）；
    ``internal`` 仅内部（recovery snapshot/原始 transcript，不进 telemetry）。
    """
    PUBLIC = "public"
    SANITIZED = "sanitized"
    INTERNAL = "internal"


class ArtifactKind(str, Enum):
    """内容寻址工件类别（task 2.4 工件存储按类别路由存储/保留策略）。"""
    DIFF = "diff"
    TEST_OUTPUT = "test_output"
    VERIFIER_FEEDBACK = "verifier_feedback"
    RECOVERY_SNAPSHOT = "recovery_snapshot"
    TRANSCRIPT = "transcript"
    CUTOVER_SUITE = "cutover_suite"        # task 7.6：完整 cutover 套件通过 summary 归档（immutable passing evidence）
    REFLECTION = "reflection"              # add-cross-prd-learning-memory task 1.2：终态反思全量输出（sanitized）


class AssuranceTier(str, Enum):
    """执行沙盒保证等级（task 6.1/6.2）。

    ``local_worktree`` 是现有行为、显式标为 **较低保证**（同主机进程，凭证可达）；``isolated_container``
    是非 root + 只读挂载 + 临时 home + 资源限额的强隔离。``container_sandbox`` flag 关时一律前者。
    """
    LOCAL_WORKTREE = "local_worktree"
    ISOLATED_CONTAINER = "isolated_container"


# ════════════════════════════════════════════════════════════════════════
# task 2.1：数据模型（frozen dataclass，可 JSON 序列化）
# ════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class ArtifactRef:
    """内容寻址工件引用——journal 里指向工件存储落盘内容的指针（task 2.4）。

    ``digest``（``sha256:<hex>``）是真源：读端按 digest 校验内容完整性（task 2.5），不信任 path/size
    这类可被篡改的元数据。``sensitivity`` 决定脱敏与落盘策略。
    """
    __test__: ClassVar[bool] = False
    digest: str            # sha256:<hex>
    size: int
    kind: str              # ArtifactKind.value
    path: str              # 工件存储内相对路径
    sensitivity: str       # Sensitivity.value


@dataclass(frozen=True)
class FailureClassification:
    """一次失败的结构化分类（RetryPolicy 的机械输入，design 决策#3）。

    ``transient`` 决定 resume 候选（429/500/529/超时 → True）；``fingerprint`` 是归一化失败指纹，
    做重复失败检测（task 5.2：连续相同 fingerprint = 无进展 → 换 new_session）。
    """
    __test__: ClassVar[bool] = False
    subtype: str | None          # ResultMessage.subtype（success/error_max_budget/...）
    is_error: bool
    stop_reason: str | None
    api_error_status: int | None   # HTTP 429/500/529 → transient 判定
    transient: bool
    fingerprint: str


@dataclass(frozen=True)
class SessionRunMeta:
    """SDK session 真源（task 5.1 持久化）。resume/fork/new_session 决策直接消费这些字段。

    ``compaction_count`` 是 PreCompact 累计次数（task 5.6 分层限额）；``usage``/``total_cost_usd``
    喂成本指标与 trusted-cost 限额。
    """
    __test__: ClassVar[bool] = False
    session_id: str | None
    subtype: str | None
    stop_reason: str | None
    num_turns: int | None
    usage: dict | None = None
    total_cost_usd: float | None = None
    api_error_status: int | None = None
    compaction_count: int = 0


@dataclass(frozen=True)
class SubagentRecord:
    """子代理归属与产出（task 4.6）。子代理禁止直接发布——只留 result_ref 证据给父迭代。

    ``parent_iteration_id`` 建立父子因果；``tools``/``effort`` 记录授权面（防止越权）。
    """
    __test__: ClassVar[bool] = False
    agent_id: str
    agent_type: str
    objective: str
    parent_iteration_id: str
    tools: tuple[str, ...] = ()
    effort: str | None = None
    status: str | None = None
    result_ref: ArtifactRef | None = None


@dataclass(frozen=True)
class RecoverySnapshot:
    """恢复上下文（task 4.5 PreCompact + task 5.4 重试恢复）。

    由 **不可变 PRD 内容 + journal 工件** 合成（design：PRD 是不可变真源）。``next_action`` 是恢复后
    的明确下一步；``failures`` 携带历史分类避免重蹈覆辙。
    """
    __test__: ClassVar[bool] = False
    objective: str
    acceptance_criteria: tuple[str, ...]
    base: str
    head: str | None
    changed_files: tuple[str, ...]
    decisions: tuple[str, ...]
    test_evidence_ref: ArtifactRef | None
    failures: tuple[FailureClassification, ...]
    next_action: str


@dataclass(frozen=True)
class JournalEvent:
    """append-only journal 的单条事件（design 决策#1）。

    每条带 ``schema_version``（演化）+ ``event_id``（dedup 依据）+ iteration/run/prd 三级归属 +
    ``event_type`` + ``payload``（自由 dict，按 event_type 解释）。落盘为 JSONL 一行。
    """
    __test__: ClassVar[bool] = False
    schema_version: int
    event_id: str
    timestamp: str            # ISO8601 stamp（调用方传入，本模块不触时间）
    iteration_id: str
    run_id: str
    prd_id: str
    event_type: str
    payload: dict


@dataclass(frozen=True)
class IterationState:
    """reducer 归约出的迭代不可变快照（design 决策#1「不原地覆写记录，iteration 通过事件归约得状态」）。

    ``applied_event_ids`` 是 dedup 依据（重放幂等）；``last_transition_error`` 记录被拒迁移的原因
    （status 不变但可审计——绝不静默吞非法迁移）。
    """
    __test__: ClassVar[bool] = False
    iteration_id: str
    run_id: str
    prd_id: str
    status: IterationStatus
    base: str
    prd_content_hash: str | None = None
    head: str | None = None
    session_meta: SessionRunMeta | None = None
    artifacts: tuple[ArtifactRef, ...] = ()
    test_evidence_ref: ArtifactRef | None = None
    last_failure: FailureClassification | None = None
    subagents: tuple[SubagentRecord, ...] = ()
    recovery_snapshot: RecoverySnapshot | None = None
    last_transition_error: str | None = None
    applied_event_ids: frozenset[str] = field(default_factory=frozenset)


# ════════════════════════════════════════════════════════════════════════
# task 2.6：状态机
# ════════════════════════════════════════════════════════════════════════
def is_terminal(status: IterationStatus) -> bool:
    """是否终态（published/aborted/failed/state_corrupt）——终态无出向迁移，不可复活。"""
    return status in TERMINAL_STATUSES


def validate_transition(cur: IterationStatus, nxt: IterationStatus) -> tuple[bool, str]:
    """裁定迁移合法性：``nxt`` 必须在 ``_TRANSITIONS[cur]`` 表里。

    Returns:
        ``(True, reason)`` 合法迁移；``(False, reason)`` 非法（含 running→published 直跳——
        SDK 成功≠已发布，design 决策#2 的硬落地）。
    """
    allowed = _TRANSITIONS.get(cur, frozenset())
    if nxt in allowed:
        return True, f"合法迁移 {cur.value}->{nxt.value}"
    return False, (f"非法迁移 {cur.value}->{nxt.value}（不在状态机迁移表；"
                   "SDK 成功≠已发布，须经 test 门→verifying→publish_ready→published）")


# event_type → 目标 status（reducer status 迁移用）。``planned`` 是声明性事件（不改 status，初始已 PLANNED）。
# 带 payload 子语义的 event（如 verifier 带 verdict）在 task 3.2 细化；此处覆盖 status 级映射。
_EVENT_STATUS_MAP: dict[str, IterationStatus] = {
    "planned": IterationStatus.PLANNED,
    "running": IterationStatus.RUNNING,
    "agent_finished": IterationStatus.AGENT_FINISHED,
    "test_blocked": IterationStatus.TEST_BLOCKED,
    "verifying": IterationStatus.VERIFYING,
    "revise": IterationStatus.REVISE,
    "external_blocked": IterationStatus.EXTERNAL_BLOCKED,
    "publish_ready": IterationStatus.PUBLISH_READY,
    "published": IterationStatus.PUBLISHED,
    "aborted": IterationStatus.ABORTED,
    "failed": IterationStatus.FAILED,
    "stalled": IterationStatus.STALLED,                  # task 3.5：dev loop 主动刹车终态
    "orphan_deleted": IterationStatus.ORPHAN_DELETED,    # task 3.5：无 commit 孤儿清理终态
    "blocked_evidence": IterationStatus.BLOCKED_EVIDENCE,  # task 3.5 前向（task 4.2/4.3）
    "sandbox_blocked": IterationStatus.SANDBOX_BLOCKED,    # task 3.5 前向（task 5.2）
    "state_corrupt": IterationStatus.STATE_CORRUPT,
}


def initial_state(run_id: str, prd_id: str, iteration_id: str,
                  base: str, prd_content_hash: str | None = None) -> IterationState:
    """构造一个初始 IterationState（status=PLANNED）。reduce 的起点。"""
    return IterationState(
        iteration_id=iteration_id, run_id=run_id, prd_id=prd_id,
        status=IterationStatus.PLANNED, base=base, prd_content_hash=prd_content_hash,
    )


def apply_event(state: IterationState, event: JournalEvent) -> IterationState:
    """reducer：把单条 event 应用到 state，返回**新**不可变 state（不改入参）。

    规则（task 2.6 拒非法/重复迁移）：
        1. ``event_id`` 已在 ``applied_event_ids`` → 跳过（幂等），记 duplicate 到 ``last_transition_error``；
        2. ``event_type`` 映射的目标 status 若与当前相同 → 视为幂等重申，不改 status；
        3. 否则 ``validate_transition`` 校验——非法迁移 **status 不变**，记原因到 ``last_transition_error``；
        4. 合法迁移推进 status，累积 ``applied_event_ids``，清 ``last_transition_error``。

    payload 字段合并（head/session_meta/artifacts/test_evidence_ref 等）在 task 3.2 增强；
    本骨架仅做 status 迁移 + dedup + 拒非法——已足够锁死 spec「SDK 成功≠已发布」。
    """
    # 1. dedup：同 event_id 幂等（journal 重放/恢复时可能重读同一条）
    if event.event_id in state.applied_event_ids:
        return replace(state, last_transition_error=f"duplicate event_id: {event.event_id}")

    nxt = _EVENT_STATUS_MAP.get(event.event_type)

    # 2/3. status 迁移（self 跳过；非法拒）
    new_state = state
    if nxt is not None and nxt is not state.status:
        ok, reason = validate_transition(state.status, nxt)
        if not ok:
            # 非法迁移：status 不变，但记原因（绝不静默推进）
            return replace(state, last_transition_error=reason)
        new_state = replace(state, status=nxt)

    # 4. 累积 applied + 清 error
    return replace(
        new_state,
        applied_event_ids=state.applied_event_ids | {event.event_id},
        last_transition_error=None,
    )


def reduce(events: Any, initial: IterationState | None = None) -> IterationState:
    """把一串 event fold 归约为最终 IterationState（design 决策#1：iteration 状态由事件归约得到）。

    Args:
        events: 可迭代的 JournalEvent（journal 读出的全部行）。
        initial: 起始 state；None 时从首个 event 的 run/prd/iteration 构造初始 PLANNED state。

    Returns:
        归约后的不可变 IterationState。
    """
    events = list(events)
    if initial is None:
        if not events:
            raise ValueError("reduce 无 initial 且 events 为空——无法构造起始 state")
        first = events[0]
        base = first.payload.get("base", "") if isinstance(first.payload, dict) else ""
        initial = initial_state(first.run_id, first.prd_id, first.iteration_id, base)

    state = initial
    for event in events:
        state = apply_event(state, event)
    return state
