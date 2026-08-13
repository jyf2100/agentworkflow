#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""graph_pa_contracts.py — LangGraph 编排层统一 node I/O 契约（TypedDict + 中心化验证）。

langgraph-workflow-upgrade（openspec/changes/langgraph-workflow-upgrade）的 spec「统一 node I/O
契约」+「ArtifactHandle 路径契约」+ design D4 落地。守 pa 纯 stdlib 风格（与 stage_contracts.py
手写 dict 校验一致），**不主动依赖 pydantic**（即便 LangGraph 间接拉入，OQ2 已决 TypedDict）。

三个核心类型：
    ArtifactHandle — 规范文件路径（替裸 path: str）：{kind, store, rel_path, digest, must_exist}
    NodeInput      — node 入参（6 类字段：标识/上下文/配置/上游产物/恢复/可观测性）
    NodeOutput     — node 返回 envelope（7 字段：status/artifacts/verdict/side_effects/obs/error/idempotency_key）

中心化验证（严格 raise；编排器自产 NodeOutput，非外部宽容）：
    validate_artifact_handle(d) -> ArtifactHandle | raise   — 结构 + store/digest 契约（OQ3）
    validate_node_input(d)       -> NodeInput | raise
    validate_node_output(d)      -> NodeOutput | raise

路径解析（node 内即时，不入 graph state，可移植 R8）：
    resolve_handle(handle, vault_root=, state_dir=, worktree_root=) -> abs_path | raise
    must_exist 缺失 → MissingArtifactError（fail-closed，调用方映射 status=blocked, error.code=missing_artifact）

与 stage_contracts.py 的关系：stage_contracts 校验 **persona 输出**（外部、fail-open 宽容，
registry 缺/异常 → []）；本模块校验 **编排器 node I/O**（内部、严格 raise）。verdict 字段只在
PersonaNode 写（D3/R6），本模块不约束「谁能写 verdict」（那是 check_boundary.py lint 的职责），
只定义结构 + 在运行时宽容接受 verdict 结构（编排器不应信任「来源已被守」）。
"""
from __future__ import annotations

import os
from typing import Any, TypedDict

# ── store 三态（ADR-0001 控制面/目标面隔离的类型层锚点）──────────────────
STORE_VAULT = "vault"          # 控制面，rel_path 相对 vault_root
STORE_WORKTREE = "worktree"    # 目标面，rel_path 相对 worktree_root（仅 DevLoopNode 可写）
STORE_TMP = "tmp"              # stamp 作用域，rel_path 相对 state_dir（隔离）
VALID_STORES = (STORE_VAULT, STORE_WORKTREE, STORE_TMP)
_LONG_TERM_STORES = (STORE_VAULT, STORE_WORKTREE)   # digest 强制（OQ3：长期产物强、tmp 可选）

DIGEST_PREFIX = "sha256:"

# ── NodeOutput.status 终态值集（升人工路径保留 enum，D5 撤 / spec「机械硬门」）────
# graph 条件边机械路由这些终态，不替判（不替 persona 判死/判语义）。
STATUS_OK = "ok"
STATUS_BLOCKED = "blocked"              # fail-safe 门：三态 UNKNOWN / missing_artifact / test_gate
STATUS_TRIAGED = "triaged"              # 升人工：编排器残缺输入 / dev exit 15 / off_track 连续
STATUS_INTERRUPTED = "interrupted_pr"   # verify revise 用满（enum 终态，非 interrupt）
STATUS_HALTED = "halted"                # circuit_breaker hard halt（跨 cron）
STATUS_COOLDOWN = "cooldown"            # circuit_breaker cooldown（跨 cron）
VALID_STATUSES = (STATUS_OK, STATUS_BLOCKED, STATUS_TRIAGED,
                  STATUS_INTERRUPTED, STATUS_HALTED, STATUS_COOLDOWN)

# ── error.code 受控值集（编排器产，非 persona 语义判决）──────────────────
ERR_MISSING_ARTIFACT = "missing_artifact"      # must_exist 上游 artifact 缺失（fail-closed）
ERR_CONTRACT_VIOLATION = "contract_violation"  # NodeOutput 自身结构不合规 / persona 输出残缺被 triaged
ERR_TIMEOUT = "timeout"                        # wall-clock 超时
ERR_UNKNOWN_REMOTE = "unknown_remote"          # 三态 UNKNOWN（FOUND/NOT_FOUND/UNKNOWN）
ERR_TEST_GATE = "test_gate"                    # dev exit 14（GATE_FAILED/NOT_RUN/STALE）
ERR_PERSONA_CRASH = "persona_crash"            # persona 子进程非 0 / 非 JSON（被 triaged，不替判死）
VALID_ERROR_CODES = (ERR_MISSING_ARTIFACT, ERR_CONTRACT_VIOLATION, ERR_TIMEOUT,
                     ERR_UNKNOWN_REMOTE, ERR_TEST_GATE, ERR_PERSONA_CRASH)


class ContractError(ValueError):
    """NodeInput/NodeOutput/ArtifactHandle 结构违反（严格 raise）。"""


class MissingArtifactError(ContractError):
    """must_exist=True 的上游 artifact 在 node 入口缺失（fail-closed，不静默跳过）。

    调用方（node 包装层）捕获后映射为 NodeOutput(status=blocked, error.code=missing_artifact)。
    """


class ArtifactHandle(TypedDict, total=False):
    """规范文件路径（替裸 path: str）。跨面隔离 + 可移植 + 完整性 + fail-closed（D4）。

    state 只存 rel_path + store（绝对路径 node 内解析，不入 state，可移植 R8）。
    store=worktree 只由 DevLoopNode 写（ADR-0001 类型层守，check_boundary.py lint 兜底）。
    """
    kind: str          # artifact 语义类型（radar_candidates/prd_manifest/install_log/test_log/...）
    store: str         # ∈ VALID_STORES（必填）
    rel_path: str      # 相对 store root（必填，可移植）
    digest: str        # sha256:...；长期产物（vault/worktree）强制、tmp 可选（OQ3）
    must_exist: bool   # True→node 入口 fail-closed 校验，缺失则 status=blocked


class Obs(TypedDict, total=False):
    """标准化可观测性元数据（每 node 必吐，report node 聚合为可查询 metrics，决策 M 路径 A）。

    字段源自 run_persona 的 meta（cost/turns/duration_ms/model），token_usage 取自 modelUsage 细项。
    """
    cost: float            # USD（meta.total_cost_usd）
    turns: int             # claude turns（meta.num_turns）
    duration_ms: int       # 壁钟毫秒（meta.duration_ms）
    model: str             # 模型 id / alias（meta.modelUsage 归约）
    token_usage: dict      # {input, output, ...}（meta.modelUsage 细项）


class Verdict(TypedDict, total=False):
    """语义判决（仅 PersonaNode 可写，D3/R6）。

    value 受 persona 契约约束（critic∈pass/revise/drop、progress∈on_track/off_track），
    本结构不重复校验 value 域（那是 stage_contracts.py 的职责），只要求 value/reason 非空。
    """
    value: str       # pass/revise/drop/on_track/off_track（必填）
    reason: str      # 判决理由（必填，人读）
    feedback: str    # 反馈/重做提示（revise/off_track 时给下游 node 或 persona）


class NodeError(TypedDict, total=False):
    """node 错误（编排器产，非 persona 语义）。code ∈ VALID_ERROR_CODES。"""
    code: str        # 必填，∈ VALID_ERROR_CODES
    message: str     # 必填，人读
    detail: dict     # 可选诊断（stage/field/excerpt/exit_code 等）


class NodeOutput(TypedDict, total=False):
    """node 返回 envelope（7 字段，spec「统一 node I/O 契约」）。

    编排器（graph node/edge）MUST 只读 status/verdict 做路由，不判定语义、不改写 verdict。
    编排器残缺输入（prd 缺 path / critic 漏吐 verdict / revise 异常）→ 产 status=triaged（升人工，
    不替判死，spec「编排器残缺输入改 triaged」修边界审查层 2）。
    """
    status: str             # ∈ VALID_STATUSES（必填）
    artifacts: list         # list[ArtifactHandle]（产出，可空）
    verdict: dict           # Verdict（仅 PersonaNode；其他类型写则被 check_boundary.py 拒）
    side_effects: list      # [{kind, ...}]（commit/push/PR/test 终态等副作用，喂 reconcile）
    obs: dict               # Obs（每 node 必吐）
    error: dict             # NodeError（status≠ok 时必填）
    idempotency_key: str    # exactly-once reconcile key（必填）


class NodeInput(TypedDict, total=False):
    """node 入参（6 类字段，spec「统一 node I/O 契约」）。"""
    # 1. 标识
    run_id: str
    thread_id: str          # graph invoke thread_id（run_<stamp>）
    stamp: str              # YYYYMMDD
    stage: str              # fetch/radar/prd/inject/critic/dispatch/report
    node_id: str            # node 实例标识
    # 2. 上下文路径（绝对路径 node 内解析，不入 graph state）
    vault_root: str
    state_dir: str
    worktree_root: str      # 仅 DevLoopNode 用
    # 3. 配置（通用 node 工厂注入：agent_name/op/contracts/timeout 等）
    config: dict
    # 4. 上游产物
    upstream_artifacts: list   # list[ArtifactHandle]（must_exist=True 的入口 fail-closed 校验）
    # 5. 恢复上下文（崩溃恢复：recovery_cli 重建 initial state）
    recovery: dict
    # 6. 可观测性种子
    obs_seed: dict


# ── 中心化验证（严格 raise）──────────────────────────────────────────
def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise ContractError(msg)


def validate_artifact_handle(d: Any) -> ArtifactHandle:
    """校验 ArtifactHandle 结构 + store/digest 契约（OQ3：长期强制 digest、tmp 可选）。

    不做文件系统存在性校验（must_exist 的存在性由 resolve_handle 在 node 入口 fail-closed）。
    """
    _require(isinstance(d, dict), f"ArtifactHandle 必须是 dict，实际 {type(d).__name__}")
    store = d.get("store")
    _require(store in VALID_STORES, f"store 必须 ∈{VALID_STORES}，实际 {store!r}")
    rel_path = d.get("rel_path")
    _require(isinstance(rel_path, str) and rel_path,
             "rel_path 必须是非空 str（可移植相对路径）")
    kind = d.get("kind")
    _require(isinstance(kind, str) and kind, "kind 必须是非空 str")
    digest = d.get("digest")
    if store in _LONG_TERM_STORES:
        # OQ3：长期产物（vault/worktree）digest 强制
        _require(isinstance(digest, str) and digest.startswith(DIGEST_PREFIX),
                 f"store={store} 长期 artifact 必须 digest（{DIGEST_PREFIX}...），实际 {digest!r}")
    elif digest is not None:
        _require(isinstance(digest, str) and digest.startswith(DIGEST_PREFIX),
                 f"digest 必须是 {DIGEST_PREFIX}... 形式，实际 {digest!r}")
    me = d.get("must_exist", False)
    _require(isinstance(me, bool), f"must_exist 必须 bool，实际 {type(me).__name__}")
    return d


def validate_node_input(d: Any) -> NodeInput:
    """校验 NodeInput（严格 raise）。必填：run_id / stage / config。upstream_artifacts 逐项校验。"""
    _require(isinstance(d, dict), f"NodeInput 必须是 dict，实际 {type(d).__name__}")
    _require(isinstance(d.get("run_id"), str) and d.get("run_id"), "run_id 必填")
    _require(isinstance(d.get("stage"), str) and d.get("stage"), "stage 必填")
    _require(isinstance(d.get("config"), dict), "config 必填（通用 node 工厂注入）")
    for a in d.get("upstream_artifacts", []) or []:
        validate_artifact_handle(a)
    return d


def validate_node_output(d: Any) -> NodeOutput:
    """中心化校验 NodeOutput（严格 raise；编排器自产，非外部宽容）。

    verdict 存在时校验结构，但**不约束来源**（「仅 PersonaNode 可写」由 check_boundary.py lint 守，
    D3/R6）。status≠ok 时 error 必填且 code ∈受控值集。obs 必吐。idempotency_key 必填。
    """
    _require(isinstance(d, dict), f"NodeOutput 必须是 dict，实际 {type(d).__name__}")
    status = d.get("status")
    _require(status in VALID_STATUSES, f"status 必须 ∈{VALID_STATUSES}，实际 {status!r}")
    artifacts = d.get("artifacts", [])
    _require(isinstance(artifacts, list), "artifacts 必须是 list")
    for a in artifacts:
        validate_artifact_handle(a)                       # 任一不合规 → raise（fail-closed）
    validate_obs(d.get("obs"))                            # 每 node 必吐
    ik = d.get("idempotency_key")
    _require(isinstance(ik, str) and ik, "idempotency_key 必填（exactly-once reconcile）")
    if status != STATUS_OK:                               # 非 ok → error 必填
        err = d.get("error")
        _require(isinstance(err, dict), f"status={status}（非 ok）须含 error dict")
        code = err.get("code")
        _require(code in VALID_ERROR_CODES,
                 f"error.code 必须 ∈{VALID_ERROR_CODES}（编排器产受控值），实际 {code!r}")
        _require(isinstance(err.get("message"), str) and err.get("message"),
                 "error.message 必填非空")
    v = d.get("verdict")
    if v is not None:                                     # verdict 存在 → 校验结构（value/reason 必填）
        _require(isinstance(v, dict), "verdict 必须是 dict")
        _require(isinstance(v.get("value"), str) and v.get("value"), "verdict.value 必填非空")
        _require(isinstance(v.get("reason"), str) and v.get("reason"), "verdict.reason 必填非空")
    se = d.get("side_effects", [])
    _require(isinstance(se, list), "side_effects 必须是 list")
    return d


def validate_obs(d: Any) -> Obs:
    """校验 Obs（必吐；宽松：字段缺可，但须是 dict）。"""
    _require(d is not None, "obs 必填（每 node 吐标准化可观测性，决策 M 路径 A）")
    _require(isinstance(d, dict), f"obs 必须是 dict，实际 {type(d).__name__}")
    return d


# ── 路径解析（node 内即时，不入 graph state，可移植 R8）────────────────
def resolve_handle(handle: ArtifactHandle, *, vault_root: str | None = None,
                   state_dir: str | None = None, worktree_root: str | None = None) -> str:
    """把 ArtifactHandle 的 (store, rel_path) 解析为绝对路径（node 内用）。

    must_exist=True 且文件缺失 → raise MissingArtifactError（fail-closed，不静默跳过）。
    调用方捕获后产 NodeOutput(status=blocked, error.code=missing_artifact)。
    """
    store = handle["store"]
    rel = handle["rel_path"]
    if store == STORE_VAULT:
        base = vault_root
    elif store == STORE_WORKTREE:
        base = worktree_root
    else:                                                 # STORE_TMP
        base = state_dir
    if not base:
        raise ContractError(f"store={store} 需对应 root（vault_root/state_dir/worktree_root）未提供")
    abs_path = os.path.normpath(os.path.join(base, rel))
    _norm_base = os.path.normpath(base)
    if os.path.commonpath([abs_path, _norm_base]) != _norm_base:   # 纵深防御：拒 path traversal（security-review M1；当前 rel 全编排器控无 live exploit，接线即受护）
        raise ContractError(f"path traversal 拒绝: store={store} rel={rel} → {abs_path} 越出 {_norm_base}")
    if handle.get("must_exist") and not os.path.exists(abs_path):
        raise MissingArtifactError(f"must_exist artifact 缺失: store={store} rel={rel} → {abs_path}")
    return abs_path


def make_obs(*, cost: float | None = None, turns: int | None = None,
             duration_ms: int | None = None, model: str | None = None,
             token_usage: dict | None = None) -> Obs:
    """从 run_persona 的 meta dict 构造 Obs（drop-None，便于 node 吐标准化 obs）。

    meta 形如 {cost, turns, session_id, duration_ms, model(dict)}；本函数取子集 + 归约 model。
    """
    o: dict = {}
    if cost is not None:
        o["cost"] = cost
    if turns is not None:
        o["turns"] = turns
    if duration_ms is not None:
        o["duration_ms"] = duration_ms
    if model is not None:
        o["model"] = model
    if token_usage is not None:
        o["token_usage"] = token_usage
    return o  # type: ignore[return-value]


def obs_from_meta(meta: dict | None) -> Obs:
    """从 run_persona 返回的 meta dict 直接构造 Obs（meta=None → 空 obs dict，仍合规）。"""
    meta = meta or {}
    model_usage = meta.get("model") or {}
    # modelUsage 是 {model_id: {input, output, ...}}；归约成 model id + token_usage 合计
    model_id = next(iter(model_usage), None) if isinstance(model_usage, dict) else None
    token_usage = None
    if isinstance(model_usage, dict) and model_id:
        mu = model_usage.get(model_id) or {}
        if isinstance(mu, dict):
            token_usage = {"input": mu.get("input"), "output": mu.get("output")}
    return make_obs(
        cost=meta.get("cost"),
        turns=meta.get("turns"),
        duration_ms=meta.get("duration_ms"),
        model=model_id,
        token_usage=token_usage,
    )
