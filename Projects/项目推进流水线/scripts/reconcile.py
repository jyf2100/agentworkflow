#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""reconcile.py — task 5.5 reconcile-before-retry 驱动器（3.5 崩溃恢复契约的执行端）。

spec L10-12 契约：进程崩溃重启后，reducer 重建最后合法状态，**reconciliation 判定该副作用
是否已发生**再 retry——绝不盲目重放 commit/push/PR（exactly-once effective）。

三层结构：

1. **reconcile 谓词**（``reconcile_side_effects``）：对每个副作用 (kind, target) 算 idempotency
   key，通过注入的 ``KeyResolver`` 查实际状态（三态：confirmed 存在 / absent 不存在 / unknown
   查不到）。全明确 → ``safe_to_retry``；有 unknown → ``external_known=False``（RetryPolicy BLOCK）。
2. **KeyResolver**：注入接口（Protocol）。``LocalGitResolver``（subprocess git，查 commit/branch，
   真实可跑）+ ``GhPrResolver``（gh CLI 查 PR，真实可跑，gh 缺失时容错→unknown）+ ``CompositeResolver``
   组合。测试用 ``FakeResolver``。
3. **recovery driver**（``recover_iteration``）：read journal → reduce → 查 session → reconcile →
   RetryPolicy.decide → build_recovery_context → ``RecoveryPlan``。这是把 3.5 测试里的契约变成
   **真正能跑**的 reconcile-before-retry 入口。

副作用种类对齐 ``ids.idempotency_id`` 允许列表（commit/push/pr）：branch→push、publication→pr。
纯 stdlib（subprocess/json），cron 隔离友好。
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import ids
import journal as J
import loop_state as L
import recovery_context as RC
import retry_policy as RP
from failure_analysis import FailureFingerprint
from session_meta import ExceptionClass, SessionStore

# ids.idempotency_id 允许的 kind（commit/push/pr），覆盖 task 5.5 的 branch/commit/PR/publication
ALLOWED_KINDS: frozenset[str] = frozenset({"commit", "push", "pr"})


@dataclass(frozen=True)
class SideEffectTarget:
    """一个待 reconcile 的副作用目标。``target`` 语义随 kind：
    commit→ref/sha；push→branch（远端分支）；pr→``owner/repo:branch`` 或 ``branch``。"""
    kind: str          # commit / push / pr
    target: str


@dataclass(frozen=True)
class SideEffectStatus:
    kind: str
    target: str
    key: str               # idempotency_id（跨重放稳定，exactly-once 比对键）
    state: str             # confirmed / absent / unknown
    evidence: str = ""     # 确认依据简述

    @property
    def confirmed(self) -> bool:
        return self.state == "confirmed"


@dataclass(frozen=True)
class ReconciliationReport:
    iteration_id: str
    statuses: tuple[SideEffectStatus, ...] = ()
    confirmed: tuple[SideEffectStatus, ...] = ()      # 已发生 → retry 时跳过（不重复）
    pending: tuple[SideEffectStatus, ...] = ()        # 未发生 → retry 时执行
    unknown: tuple[SideEffectStatus, ...] = ()        # 查不到 → fail-safe，不盲目 retry

    @property
    def external_known(self) -> bool:
        """全部副作用状态明确（无 unknown）→ RetryPolicy 可安全决策；有 unknown → BLOCK。"""
        return not self.unknown

    @property
    def safe_to_retry(self) -> bool:
        """安全 retry：无 unknown（confirmed 跳过 + pending 执行，无歧义）。"""
        return self.external_known


class KeyResolver(Protocol):
    """副作用状态查询接口（三态：True=confirmed / False=absent / None=unknown）。

    返回 None（查询失败/不支持）→ fail-safe → RetryPolicy BLOCK（绝不盲目 retry，design risk#90）。"""
    def check(self, kind: str, target: str) -> bool | None: ...


def reconcile_side_effects(*, iteration_id: str, targets,
                           resolver: KeyResolver) -> ReconciliationReport:
    """对每个副作用算 idempotency key + 查实际状态，产 ReconciliationReport。

    ``targets``：SideEffectTarget 序列。非法 kind → 视 unknown（fail-safe，不盲目跳过/执行）。"""
    statuses: list[SideEffectStatus] = []
    confirmed: list[SideEffectStatus] = []
    pending: list[SideEffectStatus] = []
    unknown: list[SideEffectStatus] = []
    for t in targets or []:
        kind = t.kind
        target = t.target
        if kind not in ALLOWED_KINDS:
            # 非法 kind 不能算合法 key → fail-safe 标 unknown（绝不替调用方构造非法幂等键）
            st = SideEffectStatus(kind=kind, target=target, key="", state="unknown",
                                  evidence="illegal idempotency kind")
            statuses.append(st); unknown.append(st); continue
        key = ids.idempotency_id(kind, iteration_id, target)
        try:
            result = resolver.check(kind, target)
        except Exception:
            result = None   # resolver 抛异常 → fail-safe unknown
        if result is True:
            st = SideEffectStatus(kind, target, key, "confirmed", "exists in source of truth")
            statuses.append(st); confirmed.append(st)
        elif result is False:
            st = SideEffectStatus(kind, target, key, "absent", "not found; safe to (re)apply")
            statuses.append(st); pending.append(st)
        else:
            st = SideEffectStatus(kind, target, key, "unknown", "resolver returned no answer")
            statuses.append(st); unknown.append(st)
    return ReconciliationReport(iteration_id=iteration_id, statuses=tuple(statuses),
                                confirmed=tuple(confirmed), pending=tuple(pending),
                                unknown=tuple(unknown))


# ─── 真实 KeyResolver 实现（真正能跑）──────────────────────────────────────
class LocalGitResolver:
    """subprocess git 查 commit/branch（本地，真实可跑）。

    assurance 较低（design 决策#6：local adapter 标 lower assurance）——查不到远端 push/PR。
    ``pr`` kind 返回 None（需 ``GhPrResolver``）。git 命令失败 → None（fail-safe）。"""

    def __init__(self, repo_dir: str | Path = "."):
        self.repo_dir = str(repo_dir)

    def check(self, kind: str, target: str) -> bool | None:
        try:
            if kind == "commit":
                r = subprocess.run(["git", "cat-file", "-e", target], cwd=self.repo_dir,
                                   capture_output=True, timeout=10)
                return r.returncode == 0
            if kind == "push":
                # 本地 branch ref 存在性（lower-assurance 近似；真实远端 push 需 ls-remote）
                r = subprocess.run(["git", "show-ref", "--verify", f"refs/heads/{target}"],
                                   cwd=self.repo_dir, capture_output=True, timeout=10)
                return r.returncode == 0
            # pr 本地查不到 → None（交 GhPrResolver）
            return None
        except Exception:
            return None


class GhPrResolver:
    """gh CLI 查 PR（真实可跑，pa-fetch-github-repo 已证 gh 在 cron 可用）。

    gh 缺失/无 token/超时 → None（fail-safe，RetryPolicy BLOCK，绝不盲目补开 PR）。"""

    def __init__(self, default_repo: str | None = None, timeout: int = 15):
        self.default_repo = default_repo
        self.timeout = timeout

    def check(self, kind: str, target: str) -> bool | None:
        if kind != "pr":
            return None
        try:
            repo, _, branch = target.partition(":")
            branch = branch or target
            cmd = ["gh", "pr", "list", "--head", branch, "--state", "all",
                   "--json", "url", "--limit", "1"]
            use_repo = repo or self.default_repo
            if use_repo:
                cmd[1:1] = ["-R", use_repo]
            r = subprocess.run(cmd, capture_output=True, timeout=self.timeout, text=True)
            if r.returncode != 0:
                return None   # gh 失败 → unknown（不假装不存在）
            data = json.loads(r.stdout or "[]")
            return len(data) > 0
        except Exception:
            return None


class CompositeResolver:
    """组合多个 resolver：第一个返回非 None 的胜出；全 None → None。"""

    def __init__(self, resolvers):
        self.resolvers = list(resolvers)

    def check(self, kind: str, target: str) -> bool | None:
        for r in self.resolvers:
            v = r.check(kind, target)
            if v is not None:
                return v
        return None


def default_resolver(repo_dir: str | Path = ".", gh_repo: str | None = None) -> CompositeResolver:
    """默认组合：本地 git（commit/branch）+ gh（PR）。两者都真实可跑。"""
    return CompositeResolver([LocalGitResolver(repo_dir), GhPrResolver(default_repo=gh_repo)])


# ─── recovery driver：reconcile-before-retry 顶层入口（3.5 契约执行端）─────────
@dataclass(frozen=True)
class RecoveryPlan:
    """recover_iteration 的输出：决策 + 恢复上下文 + 对账报告。"""
    decision: RP.RetryDecision
    context: RC.RecoveryContext
    reconciliation: ReconciliationReport
    iteration_status: str              # loop_state 归约态（供调用方判断是否终态/需 reconcile）


def recover_iteration(*, journal_path, run_id: str, prd_id: str, iteration_id: str,
                      base: str, prd_content: str, targets, resolver: KeyResolver,
                      session_store: SessionStore, budget: RP.BudgetState,
                      verifier_signal: RP.VerifierSignal = RP.VerifierSignal.NONE,
                      failure_history=None) -> RecoveryPlan:
    """reconcile-before-retry 主入口（真正能跑）。

    流程（spec L10-12）：read journal → reduce 归约态 → 查 session metadata → reconcile
    副作用 idempotency keys → RetryPolicy.decide（external_known 来自对账）→ build recovery
    context → RecoveryPlan。

    journal 中部损坏 → ``JournalCorruptionError`` 传播（state_corrupt 需运维，不自动恢复）。
    """
    events = J.read_events(journal_path)                                  # corruption 让其传播
    state = L.reduce(events, L.initial_state(run_id, prd_id, iteration_id, base))
    session = session_store.load(iteration_id)                            # None → fallback new_session
    # failure fingerprint：从 session 异常分类构造（无异常→None）
    fingerprint: FailureFingerprint | None = None
    if session is not None and session.exception_class is not ExceptionClass.NONE:
        fingerprint = FailureFingerprint.of(session.exception_class, session.exception_message)
    # reconcile 副作用（fail-safe：unknown→BLOCK）
    report = reconcile_side_effects(iteration_id=iteration_id, targets=targets, resolver=resolver)
    decision = RP.decide(budget=budget, session=session, fingerprint=fingerprint,
                         progress=None, external_known=report.external_known,
                         verifier_signal=verifier_signal, failure_history=failure_history)
    context = RC.build_recovery_context(iteration_id=iteration_id, prd_id=prd_id,
                                        status_value=state.status.value,
                                        prd_content=prd_content, events=events,
                                        session_meta=session)
    return RecoveryPlan(decision=decision, context=context, reconciliation=report,
                        iteration_status=state.status.value)
