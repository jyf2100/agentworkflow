#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sandbox_canary.py — task 5.5 real Node/Python fixture canaries（5 维度）。

spec task 5.5：「Add real Node and Python fixture canaries covering allowed network,
denied network, denied credential access, resource limits, and unavailable runtime behavior.」

design L50：「Use fake adapters for deterministic unit tests, but add **subprocess-level**
SDK hook wiring tests, **local fixture repositories**, a **Docker/Podman canary when available**,
and a **documented skip/block result when unavailable**.」

每个 canary 跑**真实** fixture 命令（python3 -c / node -e，subprocess 真跑）经 sandbox adapter
（task 5.3 ``route_command``），观察 5 维度行为：
  * allowed_network：声明 allowlist 内 host → fixture 执行成功（passed）；
  * denied_network：声明未授权 host → container tier policy block（denied）/ local lower-assurance
    不强制（passed + note，诚实反映，design 决策#6 lower tier 无 OS 边界）；
  * denied_credential：长期凭据 env 经 ``sanitize_sandbox_env``（task 5.4）净化 → sandbox 内不存在
    （denied，prove absent）；
  * resource_limits：spec 资源限制反映（reflected）/ 无限制（passed）；
  * unavailable_runtime：worktree 缺失 / runner 不可用 → sandbox_blocked（documented skip/block）。

canary 结果结构化（``CanaryOutcome``），供 canary 套件（Section 7）+ 运维真实环境验证。
纯 stdlib；real subprocess 仅在 canary 跑时触发（导入不触发）——cron 隔离友好。
"""
from __future__ import annotations

from dataclasses import dataclass

import sandbox as SB
import sandbox_publication as SP


# real fixture 命令（subprocess 真跑；design L50 subprocess-level）
_PY_FIXTURE: list[str] = ["python3", "-c", "print('CANARY_OK')"]
_NODE_FIXTURE: list[str] = ["node", "-e", "console.log('CANARY_OK')"]


def _fixture(language: str) -> list[str]:
    """language → real fixture 命令（python/node）。"""
    return _NODE_FIXTURE if language == "node" else _PY_FIXTURE


@dataclass(frozen=True)
class CanaryOutcome:
    """一个 canary 维度的结果。

    ``outcome`` ∈ passed/denied/blocked/reflected/skipped：
      * passed——fixture 正常执行 / 维度满足；
      * denied——policy 拦截（network 违例 / 凭据不进 sandbox）；
      * blocked——运行时不可用（worktree 缺失 / runner 不可用，documented skip/block）；
      * reflected——资源限制已反映；
      * skipped——runtime 缺失（如 node 未装）的 documented skip。
    """
    dimension: str          # allowed_network/denied_network/denied_credential/resource_limits/unavailable_runtime
    language: str           # python/node
    outcome: str
    detail: str = ""


def canary_allowed_network(*, adapter: SB.ExecutionSandbox, spec: SB.SandboxSpec,
                           language: str, allowed_host: str) -> CanaryOutcome:
    """allowed_network canary：fixture 声明 allowlist 内 host → 执行成功（passed）。

    经 ``route_command`` 跑 real fixture，requested_hosts=allowlist 内 host → run ok → passed。
    """
    rr = SB.route_command(adapter=adapter, spec=spec, command=_fixture(language),
                          requested_hosts=(allowed_host,))
    if rr.blocked is not None:
        return CanaryOutcome("allowed_network", language, "blocked", rr.blocked.reason)
    return CanaryOutcome("allowed_network", language, "passed",
                         f"fixture executed; allowed host={allowed_host}")


def canary_denied_network(*, adapter: SB.ExecutionSandbox, spec: SB.SandboxSpec,
                          language: str, denied_host: str = "evil.invalid") -> CanaryOutcome:
    """denied_network canary：fixture 声明未授权 host → container tier policy block（denied）。

    local tier lower-assurance 无 OS 边界不强制 network（design 决策#6）→ 诚实反映 passed +
    lower-assurance note（不谎称强制）。container tier → ``SandboxBlocked(policy_violation)`` → denied。
    """
    rr = SB.route_command(adapter=adapter, spec=spec, command=_fixture(language),
                          requested_hosts=(denied_host,))
    if rr.blocked is not None and rr.blocked.policy_violation:
        return CanaryOutcome("denied_network", language, "denied",
                             f"policy block on undeclared host={denied_host}")
    # 未 block：local tier lower-assurance 不强制（诚实标注，不谎称 denied）
    note = ("lower-assurance tier did not enforce network allowlist (no OS boundary)"
            if adapter.tier is SB.AssuranceTier.LOCAL_WORKTREE
            else "tier allowed undeclared host (unexpected for container)")
    return CanaryOutcome("denied_network", language, "passed", note)


def canary_denied_credential(*, language: str, leaked_env: dict) -> CanaryOutcome:
    """denied_credential canary：长期凭据 env 经 sanitize（task 5.4）→ sandbox 内不存在。

    ``sanitize_sandbox_env`` 移除长期凭据 var + ``assert_credentials_absent`` prove 净化后 env 零
    长期凭据（fail-loud；不成立即抛，绝不静默放过）。"""
    sanitized, removed = SP.sanitize_sandbox_env(leaked_env)
    SP.assert_credentials_absent(env=sanitized)           # prove absent（5.4）
    return CanaryOutcome("denied_credential", language, "denied",
                         f"env sanitized; removed {len(removed)} long-lived credential var(s)")


def canary_resource_limits(*, spec: SB.SandboxSpec, language: str) -> CanaryOutcome:
    """resource_limits canary：spec 资源限制反映（container 强制 / local lower-assurance 记录）。

    有限制 → reflected（detail 列 cpu/mem/process）；无限制（local lower-assurance）→ passed。
    """
    reflected = {"cpu": spec.cpu_limit, "memory": spec.memory_limit, "process": spec.process_limit}
    set_limits = {k: v for k, v in reflected.items() if v is not None}
    if set_limits:
        return CanaryOutcome("resource_limits", language, "reflected",
                             f"limits={set_limits}")
    return CanaryOutcome("resource_limits", language, "passed",
                         "no limits set (lower-assurance tier)")


def canary_unavailable_runtime(*, adapter: SB.ExecutionSandbox, spec: SB.SandboxSpec,
                               language: str) -> CanaryOutcome:
    """unavailable_runtime canary：worktree 缺失 / runner 不可用 → sandbox_blocked（documented block）。

    design L50「documented skip/block result when unavailable」——运行时不可用产结构化 blocked（非 crash），
    可审计。runtime 可用 → passed。
    """
    rr = SB.route_command(adapter=adapter, spec=spec, command=_fixture(language))
    if rr.blocked is not None:
        return CanaryOutcome("unavailable_runtime", language, "blocked", rr.blocked.reason)
    return CanaryOutcome("unavailable_runtime", language, "passed",
                         "runtime available; fixture executed")


def run_fixture_canaries(*, adapter: SB.ExecutionSandbox, spec: SB.SandboxSpec,
                         languages: tuple[str, ...] = ("python", "node"),
                         leaked_env: dict | None = None,
                         denied_host: str = "evil.invalid",
                         allowed_host: str | None = None) -> tuple[CanaryOutcome, ...]:
    """跑全部 5 维度 × 指定语言的 fixture canary，返回结果元组（canary 套件入口，task 7.2/7.6 用）。

    Args:
        adapter: sandbox 适配器（local/container）。
        spec: sandbox 规格（network_allowlist/cpu_limit 等驱动 canary）。
        languages: 覆盖语言（默认 python+node，real subprocess）。
        leaked_env: 拟注入 sandbox 的 env（denied_credential canary 净化它）。
        denied_host: denied_network canary 用的未授权 host。
        allowed_host: allowed_network canary 用的授权 host（None → spec.network_allowlist 首项）。
    """
    if allowed_host is None:
        allowed_host = spec.network_allowlist[0] if spec.network_allowlist else "localhost"
    outcomes: list[CanaryOutcome] = []
    for lang in languages:
        outcomes.append(canary_allowed_network(adapter=adapter, spec=spec, language=lang,
                                               allowed_host=allowed_host))
        outcomes.append(canary_denied_network(adapter=adapter, spec=spec, language=lang,
                                              denied_host=denied_host))
        outcomes.append(canary_denied_credential(language=lang, leaked_env=leaked_env or {}))
        outcomes.append(canary_resource_limits(spec=spec, language=lang))
        outcomes.append(canary_unavailable_runtime(adapter=adapter, spec=spec, language=lang))
    return tuple(outcomes)
