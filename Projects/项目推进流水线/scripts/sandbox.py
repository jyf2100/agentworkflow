#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sandbox.py — task 6.1 ExecutionSandbox 接口 + LocalWorktreeSandbox + task 6.5 sandbox_blocked 不降级。

design 决策（Section 6，L72-76）：
    * 定义 ``ExecutionSandbox`` 接口，至少 local-worktree + container 两实现；
    * local 保留现状但标 **lower assurance**；container 提供 worktree-only writable mount、
      只读 PRD/source、non-root、资源限制、临时 home、显式 network allowlist、最小凭据注入；
    * **sandbox 启动/策略失败不得自动回退 local，应进入 ``sandbox_blocked``**（fail-closed，
      防降级攻击——绝不静默从 higher tier 掉到 lower tier）。

本模块（6.1 + 6.5）：接口 + 模型 + ``LocalWorktreeSandbox``（lower-assurance 显式适配器，
封装现有「cwd=被控仓 worktree 就地操作」语义）+ ``sandbox_blocked`` 统一不降级语义 +
``resolve_tier``（profile/flags → tier 选择）。

container 实现 + network allowlist 在 ``container_sandbox``（6.2/6.3）；host-side 凭据/
publication 在 ``sandbox_publication``（6.4）。

纯 stdlib（subprocess 仅在 LocalWorktreeSandbox.run 运行时调用，导入时不触发）——cron 隔离友好。
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Protocol


class AssuranceTier(str, Enum):
    """sandbox 保证等级（design L74）。local=lower（无隔离），container=higher（OS 边界隔离）。"""
    LOCAL_WORKTREE = "local_worktree"
    CONTAINER = "container"


@dataclass(frozen=True)
class SandboxSpec:
    """sandbox 启动规格（profile + flags 派生）。

    ``worktree_dir`` 是唯一可写挂载（container tier）；``prd_source_dirs`` 只读挂载；
    ``network_allowlist`` 显式允许的域名（task 6.3，空=拒绝一切外网，design L114 按 profile 声明）；
    资源限制 + 临时 home + non-root（container tier）；``credential_policy`` 控制凭据注入（task 6.4）。
    """
    worktree_dir: str
    prd_source_dirs: tuple[str, ...] = ()
    network_allowlist: tuple[str, ...] = ()
    cpu_limit: str | None = None            # 例 "2.0"（核数）
    memory_limit: str | None = None         # 例 "2g"
    process_limit: int | None = None        # pids 上限
    temp_home: bool = True
    non_root: bool = True
    credential_policy: str = "host_only"    # host_only / minimal_inject（task 6.4）
    requested_hosts: tuple[str, ...] = ()   # 本次声明要访问的 host（run 前 network policy 校验）


@dataclass(frozen=True)
class SandboxHandle:
    """sandbox 启动后的句柄（tier + runtime id + mounts + limits + home）。``running=False`` 表示已 teardown。"""
    tier: AssuranceTier
    runtime_id: str
    writable_mounts: tuple[str, ...] = ()
    readonly_mounts: tuple[str, ...] = ()
    network_allowlist: tuple[str, ...] = ()
    limits: dict = field(default_factory=dict)
    home_dir: str = ""
    running: bool = True


@dataclass(frozen=True)
class SandboxRunResult:
    """sandbox 内命令执行结果。``timed_out=True`` 时 exit_code=124（POSIX timeout 约定）。"""
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False


class SandboxError(Exception):
    """sandbox 层错误（prepare/run/teardown 异常）。"""


class SandboxPolicyError(SandboxError):
    """sandbox 策略校验失败（非法 mount / 越权 network / 凭据策略违反）→ sandbox_blocked。"""


@dataclass(frozen=True)
class SandboxBlocked:
    """sandbox 启动/策略失败的结果（design L76：进入 blocked，**绝不**自动 fallback local）。

    ``policy_violation=True`` 表示是策略校验拦截（network/credential/mount），而非运行时不可用。
    调用方收到 SandboxBlocked 后：生产路径=abort，dev smoke=显式换 local adapter（不自动）。
    """
    reason: str
    tier: AssuranceTier
    policy_violation: bool = False


class ExecutionSandbox(Protocol):
    """sandbox 适配器接口（local-worktree + container 两实现）。

    ``prepare`` 启动 sandbox（校验策略 + mount/limit/credential）→ ``SandboxHandle`` 或
    ``SandboxBlocked``（失败，不抛异常由调用方决定）；``run`` 在 sandbox 内跑命令；
    ``teardown`` 清理。``assurance_level`` 返回 "lower"/"higher"。
    """
    tier: AssuranceTier

    def assurance_level(self) -> str: ...
    def prepare(self, spec: SandboxSpec) -> SandboxHandle | SandboxBlocked: ...
    def run(self, handle: SandboxHandle, command, *,
            requested_hosts: tuple[str, ...] = (),
            timeout: float | None = None) -> SandboxRunResult | SandboxBlocked: ...
    def teardown(self, handle: SandboxHandle) -> None: ...


class LocalWorktreeSandbox:
    """现有 git worktree 行为的显式 **lower-assurance** 适配器（design L74）。

    无 mount 隔离、无资源限制、无 network allowlist 强制、无 non-root——assurance=lower。
    保留第一阶段语义：cwd=被控仓 worktree，subprocess 就地操作（与 dev-agent.py 一致）。

    本类**显式标记** lower assurance，使调度层能区分（不把 local 当 container 用）。
    network allowlist 在 local tier 仅记录不强制（lower tier 无 OS 边界，强制需 container）。
    """
    __test__ = False

    tier = AssuranceTier.LOCAL_WORKTREE

    def assurance_level(self) -> str:
        return "lower"

    def prepare(self, spec: SandboxSpec) -> SandboxHandle | SandboxBlocked:
        wt = Path(spec.worktree_dir)
        if not wt.is_dir():
            return SandboxBlocked(
                reason=f"worktree not found: {spec.worktree_dir}",
                tier=self.tier, policy_violation=False,
            )
        ro = tuple(d for d in spec.prd_source_dirs if Path(d).is_dir())
        return SandboxHandle(
            tier=self.tier,
            runtime_id=f"local:{wt.resolve()}",
            writable_mounts=(spec.worktree_dir,),
            readonly_mounts=ro,
            network_allowlist=tuple(spec.network_allowlist),
            home_dir=str(wt.resolve()),
            limits={"assurance": "lower", "isolation": "none"},
        )

    def run(self, handle: SandboxHandle, command, *,
            requested_hosts: tuple[str, ...] = (),
            timeout: float | None = None) -> SandboxRunResult | SandboxBlocked:
        if not isinstance(handle, SandboxHandle) or not handle.running:
            return SandboxBlocked(reason="handle not active", tier=self.tier)
        # local tier 无 OS 边界强制 network allowlist——lower assurance 明示（不阻断，仅记录）
        cmd = command if isinstance(command, list) else ["bash", "-lc", str(command)]
        try:
            r = subprocess.run(cmd, cwd=handle.home_dir, capture_output=True,
                               text=True, timeout=timeout)
            return SandboxRunResult(exit_code=r.returncode, stdout=r.stdout, stderr=r.stderr)
        except subprocess.TimeoutExpired as e:
            return SandboxRunResult(
                exit_code=124,
                stdout=(e.stdout or "") if isinstance(e.stdout, str) else "",
                stderr=(e.stderr or "") if isinstance(e.stderr, str) else "",
                timed_out=True,
            )
        except Exception as e:
            return SandboxBlocked(reason=f"local run failed: {e}", tier=self.tier)

    def teardown(self, handle: SandboxHandle) -> None:
        # worktree 由控制面管理（与第一阶段一致）——local tier 无需清理
        return None


def resolve_tier(*, container_sandbox_enabled: bool,
                 prefer_container: bool = False) -> AssuranceTier:
    """profile/flags → 选 tier（design L74）。

    ``container_sandbox_enabled``（feature_flags.container_sandbox）+ ``prefer_container``（profile）
    决定：两者皆真 → CONTAINER；否则 LOCAL_WORKTREE。**tier 选择不隐含降级**——container 失败由
    ``ContainerSandbox.prepare`` 返 ``SandboxBlocked``，调用方显式处理（task 6.5）。
    """
    if container_sandbox_enabled and prefer_container:
        return AssuranceTier.CONTAINER
    return AssuranceTier.LOCAL_WORKTREE


def open_sandbox(spec: SandboxSpec, adapter: ExecutionSandbox) -> SandboxHandle | SandboxBlocked:
    """启动 sandbox（统一入口）。失败 → ``SandboxBlocked``，**绝不自动降级 local tier**（design L76）。

    本函数无任何「container 失败 → 偷偷换 LocalWorktreeSandbox」路径——降级只能由调用方在收到
    ``SandboxBlocked`` 后**显式**重新选 adapter（且仅 dev smoke 允许，生产路径 abort）。
    """
    return adapter.prepare(spec)


# ─── task 5.3：route dev/test 命令经选定 adapter + 禁止 container→local 静默 fallback ──────────
@dataclass(frozen=True)
class RouteResult:
    """``route_command`` 的结果（task 5.3）。

    执行成功 → ``result``（SandboxRunResult），``blocked=None``；prepare/run 返 ``SandboxBlocked``
    → ``blocked`` 置位、``result=None``。``fell_back_to_local=True`` **仅** dev smoke 显式
    ``allow_local_fallback=True`` 且确实切到 local tier 时——可审计，**绝不静默**（design #5 / L76）。
    """
    result: SandboxRunResult | None = None
    blocked: SandboxBlocked | None = None
    fell_back_to_local: bool = False

    @property
    def ok(self) -> bool:
        return self.blocked is None


def select_adapter(*, container_sandbox_enabled: bool, prefer_container: bool = False,
                   local_adapter=None, container_adapter=None):
    """根据 flags + profile 选 sandbox adapter + tier（design #1：coordinator own sandbox）。

    container_sandbox_enabled（feature flag）+ prefer_container（profile）皆真 → CONTAINER tier，
    返 ``container_adapter``；否则 LOCAL_WORKTREE tier，返 ``local_adapter``。adapter 由调用方注入
    （生产 coordinator 注入真实 LocalWorktreeSandbox / ContainerSandbox，测试注入桩）。"""
    tier = resolve_tier(container_sandbox_enabled=container_sandbox_enabled,
                        prefer_container=prefer_container)
    if tier is AssuranceTier.CONTAINER:
        return tier, container_adapter
    return tier, local_adapter


def _run_on_local(local_adapter, spec: SandboxSpec, command, requested_hosts,
                  timeout) -> RouteResult:
    """显式切 local tier 执行（dev smoke 仅）——标记 ``fell_back_to_local``，绝不静默（design #5）。"""
    lh = local_adapter.prepare(spec)
    if isinstance(lh, SandboxBlocked):
        return RouteResult(result=None, blocked=lh, fell_back_to_local=True)
    try:
        out = local_adapter.run(lh, command, requested_hosts=requested_hosts, timeout=timeout)
        return RouteResult(
            result=out if isinstance(out, SandboxRunResult) else None,
            blocked=out if isinstance(out, SandboxBlocked) else None,
            fell_back_to_local=True,
        )
    finally:
        local_adapter.teardown(lh)


def route_command(*, adapter, spec: SandboxSpec, command,
                  requested_hosts: tuple[str, ...] = (), timeout: float | None = None,
                  allow_local_fallback: bool = False, local_adapter=None) -> RouteResult:
    """经选定 adapter 路由 dev/test 命令（task 5.3）。prepare → run → teardown。

    **禁止 container→local 静默 fallback**（design #5 / Migration Plan L5）：adapter（典型 container
    tier）``prepare`` 返 ``SandboxBlocked``（egress 不可执行 / 策略安装失败 / 运行时不可用）时，默认
    **不切 local**——返 ``RouteResult(blocked=...)``，生产路径调用方据此 abort 或记 ``sandbox_blocked``。
    仅 ``allow_local_fallback=True``（dev smoke 显式）+ 提供 ``local_adapter`` 时才切 local tier 重试，
    且 ``fell_back_to_local=True`` 可审计（非静默降级）。run 时 blocked（policy 违例）一律不 fallback。
    """
    handle = adapter.prepare(spec)
    if isinstance(handle, SandboxBlocked):
        if allow_local_fallback and local_adapter is not None:
            return _run_on_local(local_adapter, spec, command, requested_hosts, timeout)
        return RouteResult(result=None, blocked=handle, fell_back_to_local=False)
    try:
        out = adapter.run(handle, command, requested_hosts=requested_hosts, timeout=timeout)
        if isinstance(out, SandboxBlocked):
            # run 时 policy 违例（network/credential）——绝不 fallback（违例就是违例，design #5）
            return RouteResult(result=None, blocked=out, fell_back_to_local=False)
        return RouteResult(result=out, blocked=None, fell_back_to_local=False)
    finally:
        adapter.teardown(handle)
