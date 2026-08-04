"""feature_flags.py — Loop runtime 渐进启用开关（OpenSpec add-durable-loop-runtime task 1.3 / design 决策#8）。

第二阶段所有新能力（journal / hooks / retry / sandbox / telemetry）默认 **全关**——以 shadow mode
旁路写 journal、不改 dispatch 决策；逐项 canary 一致后再翻 flag 切到「驱动」。这样大改动可分阶段、
可回滚（关 flag 即恢复旧 dispatch，append-only 数据留审计，design 决策#8 回滚条款）。

每个 flag 三态优先级：**环境变量 > profile.loop > 默认（全 False）**。
- 环境变量优先：运维可用 ``PA_LOOP_*=false`` 一键 kill switch，无视 profile 的 canary 开启；
- profile.loop：单项目 canary（只对一个白名单项目翻 flag）；
- 默认全 False：未显式开启 = 行为与第一阶段完全一致（baseline 不变）。

真值（大小写不敏感，strip 后判定）：``1/true/yes/on`` = True，其余（含 ``0/false/no/off/空/乱串``）= False。

纯逻辑零依赖模块（同 ``evidence``/``external_state`` 既定模式）：单测零 IO 锁定三态优先级与真值解析。
``resolve_flags`` 不带参调用读 ``os.environ``（生产用法）；单测显式传 ``env={}`` 隔离，免环境耦合。
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class LoopFlags:
    """Loop runtime 6 个渐进启用开关。默认全 False = 第一阶段行为不变（design 决策#8）。

    开关语义（对应 tasks）：
        ``journal_shadow``          — 旁路写 journal（不改决策）；shadow 一致后才能开 driven。
        ``journal_driven_dispatch`` — journal reducer 驱动 dispatch（取代旧 state）。
        ``session_aware_retry``     — 启用 versioned RetryPolicy（resume/fork/new_session）。
        ``lifecycle_hooks``         — 接入 SDK hook adapter（PreToolUse/Stop/PreCompact/...）。
        ``container_sandbox``       — 用 container sandbox（否则 local-worktree 低保证 tier）。
        ``telemetry_export``        — OTLP export 启用（后端不可用时本地降级，不转失败）。

    add-cross-prd-learning-memory task 1.3a/1.3b：
        ``cross_prd_learning_shadow``    — terminal 学习步骤跑 read-only reflection + 投射 catalog（shadow，
                                          不改 prompt）；off → 无 reflection SDK 调用、无 prompt/state 变化。
        ``cross_prd_learning_injection`` — dispatch 注入 bounded lessons 进 dev prompt。gated on shadow +
                                          parity + quality（cutover 三门控，镜像 journal_driven_dispatch）。

    single-flight-auto-merge task 1.1（双 flag，镜像 shadow→driven 模式）：
        ``single_flight_serial_shadow`` — 同目标仓 dispatch 串行单飞消费，但 merge/revert 只 log 不改 main（shadow）。
        ``single_flight_auto_merge``    — 真实 merge/revert 闭环（改目标仓 main，破坏性）。gated on serial_shadow +
                                          parity + canary。off → 仍并发投递 + 兜底开 PR 待 review（baseline 不变）。

    harden-pa-verify-determinism task 4.0：
        ``verify_anchor_evidence`` — pa-verify revise 反馈注入机械行级锚点（testout→anchor→diff hunk）。
                                     off → anchor 推导零执行、prompt 零变化（baseline 行为不变，design 迁移
                                     flag-gated rollout）。独立能力域（pa-verify 质量闸），非 loop runtime 6 面。
    """
    __test__ = False   # 显式声明非测试类，免 pytest 收集告警（与 evidence.TestEvidence 一致；名 Loop* 不命中但保持一致）

    journal_shadow: bool = False
    journal_driven_dispatch: bool = False
    session_aware_retry: bool = False
    lifecycle_hooks: bool = False
    container_sandbox: bool = False
    telemetry_export: bool = False
    cross_prd_learning_shadow: bool = False       # task 1.3a
    cross_prd_learning_injection: bool = False    # task 1.3b（gated on shadow；preflight 静态阻断 invalid 组合）
    single_flight_serial_shadow: bool = False     # single-flight-auto-merge task 1.1：串行单飞消费（shadow：merge/revert 只 log 不改 main）
    single_flight_auto_merge: bool = False        # single-flight-auto-merge task 1.1：真实 merge/revert 闭环（gated on shadow+parity+canary；破坏性，改目标仓 main）
    verify_anchor_evidence: bool = False          # harden-pa-verify-determinism task 4.0（flag-gated rollout）


# flag 名 → 环境变量名（运维/CI 文档化的稳定开关名）。改这些 = 改对外契约。
FLAGS_ENV_MAP: dict[str, str] = {
    "journal_shadow": "PA_LOOP_JOURNAL_SHADOW",
    "journal_driven_dispatch": "PA_LOOP_JOURNAL_DRIVEN_DISPATCH",
    "session_aware_retry": "PA_LOOP_SESSION_AWARE_RETRY",
    "lifecycle_hooks": "PA_LOOP_LIFECYCLE_HOOKS",
    "container_sandbox": "PA_LOOP_CONTAINER_SANDBOX",
    "telemetry_export": "PA_LOOP_TELEMETRY_EXPORT",
    # add-cross-prd-learning-memory task 1.3a/1.3b：**不带 PA_LOOP_ prefix，有意域切割**——
    # learning memory 是独立能力域（跨 PRD 反思 + 注入），不属于 loop runtime 6 大渐进启用面。
    # env 名稳定（运维/CI 文档化的开关名），改这些 = 改对外契约。
    "cross_prd_learning_shadow": "PA_LEARNING_SHADOW",
    "cross_prd_learning_injection": "PA_LEARNING_INJECTION",
    # single-flight-auto-merge task 1.1：dispatch 串行单飞 + auto-merge 双 flag（域切割，PA_SINGLE_FLIGHT_ 前缀，
    # 非 PA_LOOP_——dispatch 串行+auto-merge 是独立能力域，不属于 loop runtime 6 大渐进启用面）。
    "single_flight_serial_shadow": "PA_SINGLE_FLIGHT_SERIAL_SHADOW",
    "single_flight_auto_merge": "PA_SINGLE_FLIGHT_AUTO_MERGE",
    # harden-pa-verify-determinism task 4.0：**不带 PA_LOOP_ prefix，域切割**——pa-verify 质量闸是独立
    # 能力域（机械锚点 + bundle 覆盖），不属于 loop runtime 6 大渐进启用面。env 名稳定，改 = 改对外契约。
    "verify_anchor_evidence": "PA_VERIFY_ANCHOR_EVIDENCE",
}

# 真值集合（strip + lower 后判定）。其余一律 False（保守：未知字符串不开）。
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _env_truthy(value: str | None) -> bool:
    """环境变量字符串真值判定：strip + lower 后命中 ``_TRUTHY``。None/非 str → False。"""
    if not isinstance(value, str):
        return False
    return value.strip().lower() in _TRUTHY


def resolve_flags(env: dict[str, str] | None = None,
                  profile: dict | None = None) -> LoopFlags:
    """解析当前生效的 6 个 loop flag。

    优先级：**环境变量 > profile.loop > 默认 False**。
        - 环境变量键存在 → 用其真值（显式 kill switch，压过 profile）；
        - 否则 profile.loop 有该键 → 用其 bool；
        - 否则 False。

    Args:
        env: 环境变量字典；None 读 ``os.environ``（生产）。单测传 ``{}`` 隔离。
        profile: 项目 profile dict；``profile["loop"][flag]`` 提供 per-project canary 开关。

    Returns:
        冻结的 LoopFlags（不可变，调用方不得意外改写）。
    """
    env_map = os.environ if env is None else env
    prof_loop = ((profile or {}).get("loop") or {}) if profile else {}
    resolved: dict[str, bool] = {}
    for flag_name, env_key in FLAGS_ENV_MAP.items():
        if env_key in env_map:
            resolved[flag_name] = _env_truthy(env_map[env_key])
        elif flag_name in prof_loop:
            resolved[flag_name] = bool(prof_loop[flag_name])
        else:
            resolved[flag_name] = False
    return LoopFlags(**resolved)
