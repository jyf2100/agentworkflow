#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""container_sandbox.py — task 6.2 ContainerSandbox（higher-assurance）+ task 6.3 network allowlist。

design 决策（Section 6，L74）container tier：只挂载目标 worktree（writable）+ 只读 PRD/source +
非 root 用户 + CPU/内存/进程限制 + 临时 home + **显式网络 allowlist**（L114：按 profile 声明域名，
非全网开放）+ 最小凭据注入。

**解耦真实 docker**（环境可能无 docker/podman）：``ContainerRunner`` Protocol 注入——真实用
``DockerCliRunner``（subprocess docker/podman，运行时调用），测试用 ``FakeContainerRunner``。
container 逻辑（策略校验 / mount 构造 / network policy / 资源限制）与运行时解耦，无 docker 也能测全分支。

network allowlist（6.3）：``NetworkPolicy`` 纯函数——``allowed(host)`` 判定、``violations(hosts)``
列未声明目标。container ``prepare`` 校验 spec 一致性，``run`` 校验 ``requested_hosts ⊆ allowlist``，
违例 → ``SandboxBlocked(policy_violation=True)``（fail-closed，绝不放行未声明外网）。

纯 stdlib（subprocess 仅在 DockerCliRunner 运行时调用）——cron 隔离友好（导入零 docker 依赖）。
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from sandbox import (
    AssuranceTier, SandboxBlocked, SandboxHandle, SandboxPolicyError,
    SandboxRunResult, SandboxSpec,
)


# ════════════════════════════════════════════════════════════════════════════
# task 6.3 network allowlist（纯函数）
# ════════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class NetworkPolicy:
    """profile 声明的网络 allowlist（design L114：按项目 profile 声明域名，非全网开放）。

    ``allowlist`` 为允许的 host/域名（归一化小写、去 schema/port）。``strict=True``（container
    tier）时未声明目标一律拒；``strict=False``（local tier）仅记录不强制（lower assurance）。
    """
    allowlist: tuple[str, ...] = ()
    strict: bool = True

    @classmethod
    def from_profile(cls, profile: dict | None, *, strict: bool = True) -> "NetworkPolicy":
        """从 profile 派生 allowlist（``profile["sandbox"]["network_allowlist"]``）。"""
        prof = ((profile or {}).get("sandbox") or {})
        raw = prof.get("network_allowlist") or []
        return cls(allowlist=tuple(_normalize_host(h) for h in raw if h), strict=strict)

    def allowed(self, host: str) -> bool:
        if not host:
            return not self.strict   # 空目标：strict 拒、non-strict 放
        return _normalize_host(host) in set(self.allowlist)

    def violations(self, requested_hosts) -> tuple[str, ...]:
        """返回 requested 中未在 allowlist 声明的 host（未声明目标，container tier 应 block）。"""
        allowed = set(self.allowlist)
        return tuple(h for h in (_normalize_host(x) for x in requested_hosts if x)
                     if h not in allowed)


def _normalize_host(host: str) -> str:
    """归一化 host：去 schema/path/port，小写。``https://PyPI.org/x`` → ``pypi.org``。"""
    h = str(host).strip().lower()
    if "://" in h:
        h = h.split("://", 1)[1]
    h = h.split("/", 1)[0]      # 去 path
    h = h.split(":", 1)[0]      # 去 port
    h = h.lstrip("@")           # 去 userinfo 尾的 @
    return h


# ════════════════════════════════════════════════════════════════════════════
# task 6.2 ContainerRunner Protocol（注入式，解耦真实 docker）
# ════════════════════════════════════════════════════════════════════════════
class ContainerRunner(Protocol):
    """容器运行时抽象（docker/podman）。注入 ``ContainerSandbox``，测试用 Fake。

    真实实现 ``DockerCliRunner``（subprocess）；运行时 ``available`` 判定运行时是否安装。
    """
    def available(self) -> bool: ...
    def create(self, *, image: str, writable_mounts: tuple[str, ...],
               readonly_mounts: tuple[str, ...], network_allowlist: tuple[str, ...],
               cpu_limit: str | None, memory_limit: str | None,
               process_limit: int | None, temp_home: bool, non_root: bool,
               run_as_user: str) -> str: ...
    def exec(self, container_id: str, command: list[str]) -> tuple[int, str, str]: ...
    def remove(self, container_id: str) -> None: ...


class DockerCliRunner:
    """真实 docker/podman CLI 适配（subprocess，运行时调用）。

    构造 ``docker run`` 参数：``--user``（non-root）、``--mount type=bind``（writable/readonly）、
    ``--cpus``/``--memory``/``--pids-limit``（资源限制）、临时 home。network allowlist 在 docker
    里通过自定义 network / egress 规则实现（部署侧），本层在命令标志表达意图 + NetworkPolicy 强制。
    """

    def __init__(self, runtime: str = "docker", image: str = "pa-sandbox:latest",
                 run_as_user: str = "1000:1000", timeout: float = 120):
        self.runtime = runtime
        self.image = image
        self.run_as_user = run_as_user
        self.timeout = timeout

    def available(self) -> bool:
        return shutil.which(self.runtime) is not None

    def _build_run_args(self, *, writable_mounts, readonly_mounts, network_allowlist,
                        cpu_limit, memory_limit, process_limit, temp_home, non_root):
        args = ["run", "-d", "--rm"]
        if non_root:
            args += ["--user", self.run_as_user]
        # 只读 PRD/source 挂载（design L74：read-only PRD/source mounts）
        for m in readonly_mounts:
            args += ["--mount", f"type=bind,source={m},target={m},readonly"]
        # 唯一可写：目标 worktree
        for m in writable_mounts:
            args += ["--mount", f"type=bind,source={m},target={m}"]
        if temp_home:
            args += ["--tmpfs", "/home/sandbox:rw,size=256m,mode=0700"]
        if cpu_limit:
            args += ["--cpus", str(cpu_limit)]
        if memory_limit:
            args += ["--memory", str(memory_limit)]
        if process_limit:
            args += ["--pids-limit", str(process_limit)]
        # network allowlist 意图（部署侧自定义 network + egress 规则落实；此处记录入 label 供审计）
        if network_allowlist:
            args += ["--network", "pa-egress", "--label",
                     f"pa.network_allowlist={','.join(network_allowlist)}"]
        else:
            args += ["--network", "none"]   # 空 allowlist = 全拒（design L114 非 profile 声明即禁）
        args.append(self.image)
        return args

    def create(self, *, image, writable_mounts, readonly_mounts, network_allowlist,
               cpu_limit, memory_limit, process_limit, temp_home, non_root, run_as_user):
        args = ["run", "-d", "--rm"]
        if non_root:
            args += ["--user", run_as_user]
        for m in readonly_mounts:
            args += ["--mount", f"type=bind,source={m},target={m},readonly"]
        for m in writable_mounts:
            args += ["--mount", f"type=bind,source={m},target={m}"]
        if temp_home:
            args += ["--tmpfs", "/home/sandbox:rw,size=256m,mode=0700"]
        if cpu_limit:
            args += ["--cpus", str(cpu_limit)]
        if memory_limit:
            args += ["--memory", str(memory_limit)]
        if process_limit:
            args += ["--pids-limit", str(process_limit)]
        if network_allowlist:
            args += ["--network", "pa-egress", "--label",
                     f"pa.network_allowlist={','.join(network_allowlist)}"]
        else:
            args += ["--network", "none"]
        args.append(image)
        r = subprocess.run([self.runtime] + args, capture_output=True, text=True,
                           timeout=self.timeout)
        if r.returncode != 0:
            raise SandboxPolicyError(f"container create failed: {r.stderr.strip()}")
        return r.stdout.strip()

    def exec(self, container_id, command):
        r = subprocess.run([self.runtime, "exec", container_id] + list(command),
                           capture_output=True, text=True, timeout=self.timeout)
        return r.returncode, r.stdout, r.stderr

    def remove(self, container_id):
        subprocess.run([self.runtime, "rm", "-f", container_id],
                       capture_output=True, text=True, timeout=self.timeout)


# ════════════════════════════════════════════════════════════════════════════
# task 6.2 ContainerSandbox（higher assurance）
# ════════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class _PreparedContainer:
    """ContainerSandbox.prepare 的内部产物（handle + network policy + runner state）。"""
    handle: SandboxHandle
    policy: NetworkPolicy
    image: str


class ContainerSandbox:
    """higher-assurance container sandbox（design L74）。

    non-root + writable worktree-only mount + ro PRD/source + temp home + CPU/mem/process 限制 +
    显式 network allowlist（task 6.3）+ 最小凭据注入（task 6.4，凭证由 sandbox_publication 管）。

    失败语义（task 6.5）：runner 不可用 / 策略违例 / create 失败 → ``SandboxBlocked``，
    **绝不自动降级 local**（调用方收到 blocked 显式处理）。
    """
    __test__ = False

    tier = AssuranceTier.CONTAINER

    def __init__(self, runner: ContainerRunner, *, image: str = "pa-sandbox:latest",
                 run_as_user: str = "1000:1000"):
        self.runner = runner
        self.image = image
        self.run_as_user = run_as_user
        self._prepared: dict[str, _PreparedContainer] = {}

    def assurance_level(self) -> str:
        return "higher"

    def prepare(self, spec: SandboxSpec) -> SandboxHandle | SandboxBlocked:
        # 1. runner 可用性（不可用 → blocked，不降级）
        if not self.runner.available():
            return SandboxBlocked(
                reason=f"container runtime unavailable ({type(self.runner).__name__})",
                tier=self.tier, policy_violation=False,
            )
        # 2. worktree 必须存在且唯一可写
        wt = Path(spec.worktree_dir)
        if not wt.is_dir():
            return SandboxBlocked(reason=f"worktree not found: {spec.worktree_dir}",
                                  tier=self.tier, policy_violation=False)
        ro = tuple(d for d in spec.prd_source_dirs if Path(d).is_dir())
        # 3. network policy（task 6.3）
        policy = NetworkPolicy(allowlist=tuple(spec.network_allowlist), strict=True)
        viol = policy.violations(spec.requested_hosts)
        if viol:
            return SandboxBlocked(
                reason=f"undeclared network destinations blocked: {list(viol)}",
                tier=self.tier, policy_violation=True,
            )
        # 4. create container（runner 抛 → blocked）
        try:
            cid = self.runner.create(
                image=self.image,
                writable_mounts=(spec.worktree_dir,),
                readonly_mounts=ro,
                network_allowlist=tuple(spec.network_allowlist),
                cpu_limit=spec.cpu_limit, memory_limit=spec.memory_limit,
                process_limit=spec.process_limit, temp_home=spec.temp_home,
                non_root=spec.non_root, run_as_user=self.run_as_user,
            )
        except SandboxPolicyError as e:
            return SandboxBlocked(reason=str(e), tier=self.tier, policy_violation=True)
        except Exception as e:
            return SandboxBlocked(reason=f"container create error: {e}",
                                  tier=self.tier, policy_violation=False)
        handle = SandboxHandle(
            tier=self.tier, runtime_id=cid,
            writable_mounts=(spec.worktree_dir,), readonly_mounts=ro,
            network_allowlist=tuple(spec.network_allowlist),
            limits={"cpu": spec.cpu_limit, "memory": spec.memory_limit,
                    "pids": spec.process_limit, "non_root": spec.non_root,
                    "temp_home": spec.temp_home, "assurance": "higher"},
            home_dir="/home/sandbox",
        )
        self._prepared[cid] = _PreparedContainer(handle=handle, policy=policy, image=self.image)
        return handle

    def run(self, handle: SandboxHandle, command, *,
            requested_hosts: tuple[str, ...] = (),
            timeout: float | None = None) -> SandboxRunResult | SandboxBlocked:
        if not isinstance(handle, SandboxHandle) or not handle.running:
            return SandboxBlocked(reason="handle not active", tier=self.tier)
        prep = self._prepared.get(handle.runtime_id)
        if prep is None:
            return SandboxBlocked(reason="handle not prepared by this sandbox", tier=self.tier)
        # network allowlist 强制（task 6.3）：requested_hosts 必须全在 allowlist
        viol = prep.policy.violations(requested_hosts)
        if viol:
            return SandboxBlocked(
                reason=f"undeclared network destinations blocked at run: {list(viol)}",
                tier=self.tier, policy_violation=True,
            )
        cmd = command if isinstance(command, list) else ["bash", "-lc", str(command)]
        try:
            code, out, err = self.runner.exec(handle.runtime_id, cmd)
            return SandboxRunResult(exit_code=code, stdout=out, stderr=err)
        except Exception as e:
            return SandboxBlocked(reason=f"container exec error: {e}", tier=self.tier)

    def teardown(self, handle: SandboxHandle) -> None:
        prep = self._prepared.pop(handle.runtime_id, None)
        if prep is not None:
            try:
                self.runner.remove(handle.runtime_id)
            except Exception:
                pass   # teardown 失败不抛（best-effort 清理，container --rm 自清理）
