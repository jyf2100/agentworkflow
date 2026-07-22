#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""coordinator.py — task 2.1 production runtime coordinator（OpenSpec complete-durable-loop-runtime-integration）。

design 决策#1：把 journal/artifacts/IDs/retry/hooks/sandbox/telemetry/reconciliation 收敛到一个
coordinator 边界——``dispatch_one`` 与 ``dev-agent`` 共用，**一次解析所有 loop flag**，集中 own 运行时
设施，不再散建 ``_run``/``_prd``/``_iter``/``_sj``（spec durable-runtime-integration「Production runtime
coordinator」两个 scenario）。

职责分层：
  * **task 2.1 骨架（本文件）**：flags 一次解析（冻结快照）+ 稳定 IDs（``loop_ids`` 单一源头）+ own
    ``ShadowJournal`` + own artifact store 根 + lifecycle emit（委托 journal）+ iteration 衍生。
  * **后续 task 挂载**：hooks（task 2.3，从 ``coord.flags.lifecycle_hooks``）/ sandbox（Section 5）/
    telemetry（Section 6）/ retry·reconciliation（Section 3-4）的 adapter 都从 ``coord.flags`` 读 flag——
    coordinator 是唯一 resolve 点，adapter 不再各自 ``resolve_flags``（design「production code must not
    call them as disconnected helpers」）。

baseline 保留（spec「Disabled runtime preserves baseline」）：flags 全关 → ``is_baseline`` True，
``journal.enabled=False``（``ShadowJournal`` no-op），emit 不落盘、不悄悄触发任何 partial durable 功能，
dispatch first-phase 决策零变化。

DI（design 决策#6）：``stamp_fn``/``env``/``profile`` 注入，单测确定可复现；生产传 ``_now_iso`` /
``os.environ``。纯 stdlib，cron 隔离友好。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import ids as loop_ids
from feature_flags import LoopFlags, resolve_flags
from loop_runtime import ShadowJournal


def _real_stamp() -> str:
    """默认时间戳函数（生产用法）：UTC ISO8601（``Z`` 结尾）。测试注入固定函数免系统时间耦合。"""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class Coordinator:
    """task 2.1：一次 dispatch 的运行时协调器边界（design 决策#1）。

    集中 own：flags（一次解析快照）/ IDs（run·prd·iteration）/ journal（``ShadowJournal``）/
    artifact_root（内容寻址工件存储根）。lifecycle 经 ``emit`` 委托 journal 落盘；新 iteration 经
    ``next_iteration`` 衍生（parent run/prd + seq，task 3.3 revise/resume/fork 用）。

    flags 全关 → ``is_baseline`` True，``emit`` no-op，dispatch first-phase 决策零变化。
    """
    __test__ = False

    flags: LoopFlags
    run_id: str
    prd_id: str
    iteration_id: str               # 初始 iteration（seq=0）；next_iteration(seq) 衍生后续
    journal: ShadowJournal
    artifact_root: str

    @property
    def is_baseline(self) -> bool:
        """flags 全关 → True（第一阶段 baseline，dispatch 决策零变化；spec「Disabled runtime」）。"""
        return not any((
            self.flags.journal_shadow, self.flags.journal_driven_dispatch,
            self.flags.session_aware_retry, self.flags.lifecycle_hooks,
            self.flags.container_sandbox, self.flags.telemetry_export,
        ))

    def emit(self, event_type: str, payload: dict | None = None) -> str | None:
        """委托 journal emit lifecycle 事件（shadow 语义：flag 关 no-op，不改决策；返回值不得驱动控制流）。

        用 coordinator 的 ``iteration_id``/``prd_id``——调用方无需再传（消除散建
        ``_sj.emit(type, _iter, _prd, ...)``，统一从 coordinator 发）。
        """
        return self.journal.emit(event_type, self.iteration_id, self.prd_id, payload=payload)

    def next_iteration(self, seq: int) -> str:
        """衍生 distinct deterministic iteration ID（parent run/prd + seq）。

        task 3.3「distinct deterministic iteration ID for every revise/resume/fork/new-session」的入口；
        每次 verify revise/recovery 用新 seq 调此方法得新 iteration，引用 parent run/prd。
        """
        return loop_ids.iteration_id(self.run_id, self.prd_id, seq)


def build_coordinator(*, stamp: str, prd_path: str, proj: str, slug: str,
                      state_dir, profile: dict | None = None, env: dict | None = None,
                      stamp_fn: Callable[[], str] | None = None) -> Coordinator:
    """dispatch/dev-agent 入口：一次解析 flag + 建 IDs/journal/artifact_root，返回 ``Coordinator``。

    替代 ``dispatch_one`` 散建的 ``_run``/``_prd``/``_iter``/``_sj``。所有 adapter 从返回的
    ``coord.flags`` 读 flag（design 决策#1：production code 不再 disconnected helper 式各自 resolve）。

    Args:
        stamp: cron run 时间戳（``run_id`` 输入，同 run→同 id）。
        prd_path: PRD 相对路径（``prd_id`` 输入）。
        proj: 项目名（journal/artifact 路径分段）。
        slug: PRD slug（journal 文件名分段，同 dispatch_one 的 ``{stamp}_{slug}``）。
        state_dir: 运行时 state 根（journal 落 ``runs/<proj>/``，artifact 落 ``artifacts/<run>/``）。
        profile: 项目 profile（``profile["loop"][flag]`` per-project canary）。
        env: 环境变量字典（None 读 ``os.environ``，运维 kill switch 压 profile）；测试传 ``{}`` 隔离。
        stamp_fn: 时间戳函数（None → ``_real_stamp`` 调系统时间；测试注入固定函数）。
    """
    flags = resolve_flags(env=env, profile=profile)
    run = loop_ids.run_id(stamp)
    prd = loop_ids.prd_id(prd_path)
    iteration = loop_ids.iteration_id(run, prd, 0)
    journal_path = Path(state_dir) / "runs" / proj / f"{stamp}_{slug}.journal.jsonl"
    journal = ShadowJournal(journal_path, run, stamp_fn or _real_stamp,
                            enabled=flags.journal_shadow)
    artifact_root = str(Path(state_dir) / "artifacts" / run)
    return Coordinator(flags=flags, run_id=run, prd_id=prd, iteration_id=iteration,
                       journal=journal, artifact_root=artifact_root)


# ─── task 2.5：preflight 校验 loop flag 组合一致性（design 决策#1 防 impossible partial 组合）──
@dataclass(frozen=True)
class PreflightBlocked:
    """task 2.5：invalid partial feature 组合 → 结构化 blocked reason。"""
    reason: str
    violations: tuple[str, ...]      # 每条违规组合描述（dispatch 记录后返回，不起 dev loop）


@dataclass(frozen=True)
class PreflightResult:
    """preflight 校验结果。``ok=True`` 可继续 dispatch；``ok=False`` 含 blocked 详情。"""
    ok: bool
    blocked: PreflightBlocked | None = None

    @property
    def is_ok(self) -> bool:
        return self.ok


# flag 依赖链（design 决策#1 防 impossible partial 组合 + 决策#2 cutover + 决策#8 渐进）：
#   journal_driven_dispatch ⇒ journal_shadow   driven 必须先 shadow（cutover 前置 shadow parity）
#   session_aware_retry     ⇒ journal_shadow   retry 需 journal 持久化 session（无 journal = 无 session 可 resume）
#   lifecycle_hooks         ⇒ journal_shadow   hooks 需 journal 落盘事件（无 journal = hook 事件丢失）
# 形式：(flag, depends_on, violation_desc)
_FLAG_DEPENDENCIES: tuple[tuple[str, str, str], ...] = (
    ("journal_driven_dispatch", "journal_shadow",
     "journal_driven_dispatch requires journal_shadow (driven cutover needs shadow parity first)"),
    ("session_aware_retry", "journal_shadow",
     "session_aware_retry requires journal_shadow (retry needs journal-persisted session)"),
    ("lifecycle_hooks", "journal_shadow",
     "lifecycle_hooks requires journal_shadow (hooks must persist events to journal)"),
)


def preflight(flags: LoopFlags) -> PreflightResult:
    """校验 loop flag 组合一致性（task 2.5；design 决策#1）。

    散建 flag 允许 impossible partial 组合（如 hooks 无 journal、retry 无 session 持久化、driven 无
    shadow）。本函数在 dispatch preflight 阶段一次性校验所有依赖链，违规 → 结构化 blocked reason
    （dispatch 记录后返回，不起 dev loop；design 决策#1「permits impossible combinations」的反制）。

    Args:
        flags: 已解析的 ``LoopFlags``（coordinator 一次解析的快照）。
    Returns:
        ``PreflightResult``：``ok=True`` 可继续 dispatch；``ok=False`` 含违规列表。
    """
    violations: list[str] = []
    for flag, dep, desc in _FLAG_DEPENDENCIES:
        if getattr(flags, flag) and not getattr(flags, dep):
            violations.append(desc)
    if not violations:
        return PreflightResult(ok=True)
    return PreflightResult(
        ok=False,
        blocked=PreflightBlocked(
            reason=f"invalid loop flag combination: {len(violations)} violation(s) of journal_shadow dependency",
            violations=tuple(violations),
        ),
    )
