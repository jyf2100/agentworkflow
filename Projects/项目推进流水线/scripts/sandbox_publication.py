#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sandbox_publication.py — task 6.4 host-side verified publication（长期凭据留控制面 host）。

design 决策（Section 6，L76）：「Git push/PR 可继续由控制面宿主执行，避免把长期 GitHub 凭据
放进 agent sandbox」。即：**长期凭据（GitHub PAT / SMTP / cloud）绝不进 sandbox**——sandbox
内 agent 只产出 artifact（diff/test output/构建产物），publication 由控制面 host 用宿主凭据
**verified** 执行（带 idempotency 对账，exactly-once effective）。

与 task 4.6（subagent 防 publication）+ task 6.2（sandbox 最小凭据注入）呼应：
    * task 4.6：PreToolUse 拦 subagent 发 commit/push/PR（agent/subagent 不持凭据）；
    * task 6.2：container sandbox ``credential_policy=host_only``（零长期凭据注入）；
    * 本模块（6.4）：控制面 host 收 publication request → 用宿主凭据 verified 执行。

``host_side_publish``：``host_credentials`` 标哪些 kind 的宿主凭据可用；缺凭据 → ``no_credentials``
blocked（不静默失败、不降级用 sandbox 内残余凭据）；有凭据 → ``published``（带 idempotency 证据）。

纯 stdlib（不触 git/gh，真实 publication 由控制面 dispatch 执行；本模块是策略 + 结果模型）。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class CredentialPolicy(str, Enum):
    """sandbox 凭据注入策略（design L74「最小凭据注入」）。

    ``HOST_ONLY``：长期凭据（GitHub/SMTP/cloud）留控制面 host，sandbox 零长期凭据（默认，最严）；
    ``MINIMAL_INJECT``：仅注入本次任务必需的最小**短期**凭据（如一次性 registry token），用后即弃。
    """
    HOST_ONLY = "host_only"
    MINIMAL_INJECT = "minimal_inject"


# publication 种类（对齐 ids.idempotency_id 的 commit/push/pr + 扩展 smtp/cloud）
PUB_GIT_PUSH = "git_push"
PUB_PR_CREATE = "pr_create"
PUB_SMTP_SEND = "smtp_send"
PUB_CLOUD_DEPLOY = "cloud_deploy"

# 需要宿主长期凭据的 publication kind（sandbox 内不得直接执行）
HOST_CREDENTIAL_KINDS: frozenset[str] = frozenset({
    PUB_GIT_PUSH, PUB_PR_CREATE, PUB_SMTP_SEND, PUB_CLOUD_DEPLOY,
})


@dataclass(frozen=True)
class HostPublicationRequest:
    """sandbox 内产出 → 控制面 host 的 publication 请求。

    agent/subagent 在 sandbox 内**不直接 publication**，只产 artifact + 发请求；控制面 host
    用宿主凭据 verified 执行（exactly-once，对齐 task 5.5 reconcile 的 idempotency key）。
    """
    kind: str                          # PUB_* 之一
    target: str                        # branch / repo / recipient / deploy target
    artifact_ref: dict | None = None   # 关联的产出工件指针（diff/test/构建产物）
    idempotency_key: str = ""          # 跨重放稳定（task 5.5 reconcile 对账键）
    requested_by_subagent: bool = False   # 标记来源（subagent 发的 publication 一律走 host verified）


@dataclass(frozen=True)
class HostPublicationResult:
    """host-side publication 结果。``status`` ∈ published/blocked/no_credentials/error。"""
    kind: str
    target: str
    status: str
    evidence: str = ""
    idempotency_key: str = ""


def host_side_publish(request: HostPublicationRequest, *,
                      host_credentials: dict,
                      already_published: bool = False) -> HostPublicationResult:
    """控制面 host 用长期凭据 **verified** 执行 publication（design L76）。

    Args:
        request: sandbox 内产出的 publication 请求。
        host_credentials: ``{kind: bool}`` 标哪些 kind 的宿主长期凭据可用（GitHub/SMTP/cloud）。
        already_published: 外层 reconcile 已确认该 idempotency key 的副作用已发生（exactly-once 跳过）。

    流程（fail-closed，绝不静默降级）：
        1. kind 非 HOST_CREDENTIAL_KINDS → error（未知 publication 种类）；
        2. ``already_published`` → published（exactly-once 跳过，不重复）；
        3. 宿主无该 kind 凭据 → ``no_credentials`` blocked（不静默失败/不降级用 sandbox 残余凭据）；
        4. 宿主有凭据 → ``published``（host verified 执行，带 idempotency 证据）。

    **长期凭据不进 sandbox**：本函数在 host 侧执行，sandbox 内 agent 无任何长期凭据访问。
    """
    if request.kind not in HOST_CREDENTIAL_KINDS:
        return HostPublicationResult(
            kind=request.kind, target=request.target, status="error",
            evidence=f"unknown publication kind: {request.kind}",
            idempotency_key=request.idempotency_key,
        )
    if already_published:
        return HostPublicationResult(
            kind=request.kind, target=request.target, status="published",
            evidence="idempotency key already reconciled; skipped (exactly-once)",
            idempotency_key=request.idempotency_key,
        )
    if not host_credentials.get(request.kind):
        return HostPublicationResult(
            kind=request.kind, target=request.target, status="no_credentials",
            evidence=(f"no host-side long-lived credential for {request.kind}; "
                      f"long-term credentials never enter the sandbox"),
            idempotency_key=request.idempotency_key,
        )
    return HostPublicationResult(
        kind=request.kind, target=request.target, status="published",
        evidence=(f"host-side verified publication via long-lived credential "
                  f"({request.kind}); sandbox held no long-term credentials"),
        idempotency_key=request.idempotency_key,
    )


def sandbox_credential_allowed(*, policy: CredentialPolicy,
                               kind: str) -> bool:
    """判定某凭据 kind 是否允许注入 sandbox（task 6.2 ``credential_policy`` 配合）。

    ``HOST_ONLY``：所有长期凭据 kind 拒注入（留 host）；
    ``MINIMAL_INJECT``：仅允许显式标注的短期凭据（本函数保守拒绝长期 kind）。
    """
    if kind in HOST_CREDENTIAL_KINDS:
        return False   # 长期凭据无论何种 policy 都不进 sandbox（design L76 硬约束）
    return policy is CredentialPolicy.MINIMAL_INJECT
