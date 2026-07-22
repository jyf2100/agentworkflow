#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hook_policy.py — task 4.2 deterministic PreToolUse policy 组件 + task 4.6 publication 防线。

把 path / command / network / protected-resource 检查从「散在 dispatch / 被追加 PRD」抽成
**确定性纯函数**，PreToolUse hook 调用（design 决策#4「path/command/network/protected-resource
移入确定性 PreToolUse 策略组件，保留第一阶段 can_use_tool 闸」）。

维度：
    * ``check_path``        —— 保护资源不可写（PRD/journal/state/.git/secrets，不可变真源）；
    * ``check_command``     —— 复用 ``bash_allowlist.decide_bash``（第一阶段 can_use_tool 闸精神）；
    * ``check_network``     —— 云元数据端点 / 本地回环 deny（SSRF + 凭证泄漏内门）；
    * ``check_publication`` —— task 4.6：subagent / 未授权上下文 deny commit/push/PR；
    * ``evaluate_pre_tool_use`` —— 顶层编排（pure，hook adapter 调此 + 落 journal）。

威胁模型（与 ``bash_allowlist`` 一致）：非硬沙箱，是**第一层确定性闸**。真正隔离在 task 6
container sandbox。本模块 fail-closed：未知/越界 → deny（绝不静默放行危险动作）。

纯 stdlib + 复用 ``bash_allowlist``，cron 隔离友好（零 SDK 导入）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

import bash_allowlist


class PermissionDecision(str, Enum):
    """PreToolUse / Stop hook 回写 SDK 的 permissionDecision（对齐 SDK 契约）。"""
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"
    DEFER = "defer"


@dataclass(frozen=True)
class PolicyVerdict:
    """单维度策略判定结果。``violated`` 标哪个维度触发（path/command/network/resource/publication）。"""
    decision: PermissionDecision
    reason: str
    violated: str = ""


# 保护资源路径前缀（agent 工具**不可写**——防篡改不可变真源 PRD/journal/state + VCS + 配置）。
PROTECTED_WRITE_PREFIXES: tuple[str, ...] = (
    ".git/",
    "state/prd/",
    "state/journal/",
    "state/artifacts/",
    "state/sess/",
    "openspec/",
    ".claude/",
    ".project-auto/",
)

# 敏感文件名片段（无论出现在路径哪，写读均拦——凭证泄漏防线）。
_SENSITIVE_FILE_FRAGMENTS: tuple[str, ...] = (
    ".env", "credentials", "secrets", "id_rsa", ".npmrc", ".pypirc",
    ".aws/credentials", "gh_token",
)

# 网络危险目标：云元数据端点 + 本地回环（SSRF / 凭证窃取）。task 6.3 profile-driven allowlist
# 在 sandbox 阶段；此处 PreToolUse 内门只拦最危险目标。
_NETWORK_DENY_RE = re.compile(
    r"(169\.254\.169\.254"          # AWS/GCP/Azure 元数据
    r"|169\.254\.170\.2"            # ECS 任务元数据
    r"|metadata\.google\.internal"  # GCP 元数据
    r"|127\.0\.0\.1"                # IPv4 回环
    r"|0\.0\.0\.0"                  # 全接口（绑定/访问）
    r"|\[::1\]"                     # IPv6 回环
    r"|localhost)",
    re.IGNORECASE,
)

# publication 命令片段（task 4.6：subagent / 未授权上下文 deny）。
_PUBLICATION_COMMANDS: tuple[str, ...] = (
    "git push", "gh pr create", "gh pr merge", "gh pr close",
)


def check_path(path: str, *, write: bool = False,
               protected: tuple[str, ...] = PROTECTED_WRITE_PREFIXES) -> PolicyVerdict:
    """path 维度：保护资源不可被 agent **写**（PRD/journal/state/.git 等不可变真源）。

    读允许（agent 需读项目代码 / PRD）；写 deny（防篡改本真源 + 状态机依据）。
    敏感文件（.env/credentials/id_rsa...）读写均 deny（凭证防线）。
    """
    if not path:
        return PolicyVerdict(PermissionDecision.ALLOW, "no path operand")
    # 归一化：斜杠统一 + 去前导 ./（不用 lstrip("./")——它剥字符集 {.,/}，会把 ".git" 的点也剥掉）。
    norm = path.replace("\\", "/")
    if norm.startswith("./"):
        norm = norm[2:]
    # 敏感文件：无论读写都拦
    low = path.lower()
    for frag in _SENSITIVE_FILE_FRAGMENTS:
        if frag in low:
            return PolicyVerdict(
                PermissionDecision.DENY,
                f"sensitive file/credential fragment: {frag}", "resource",
            )
    if write:
        for pref in protected:
            if norm.startswith(pref) or f"/{pref}" in f"/{norm}":
                return PolicyVerdict(
                    PermissionDecision.DENY,
                    f"protected resource not writable: {pref}", "path",
                )
    return PolicyVerdict(PermissionDecision.ALLOW, "path ok")


def check_command(command: str) -> PolicyVerdict:
    """command 维度：复用第一阶段 ``bash_allowlist.decide_bash``（can_use_tool 闸精神）。

    保留第一阶段语义：默认拒绝 + 显式 token 允许 + 危险子句（sudo/curl/wget/rm 系统...）拒绝。
    """
    if not command:
        return PolicyVerdict(PermissionDecision.ALLOW, "no command operand")
    allowed, reason = bash_allowlist.decide_bash(command)
    if allowed:
        return PolicyVerdict(PermissionDecision.ALLOW, reason or "command allowed", "command")
    return PolicyVerdict(
        PermissionDecision.DENY, reason or "command not in allowlist", "command",
    )


def check_network(url: str = "") -> PolicyVerdict:
    """network 维度：云元数据端点 / 本地回环 deny（SSRF + 凭证窃取内门）。

    其余 URL 放行（task 6.3 profile-driven allowlist 在 sandbox 阶段；此处只拦最危险）。
    """
    if not url:
        return PolicyVerdict(PermissionDecision.ALLOW, "no url operand")
    if _NETWORK_DENY_RE.search(url):
        return PolicyVerdict(
            PermissionDecision.DENY,
            "blocked network target (metadata/loopback)", "network",
        )
    return PolicyVerdict(PermissionDecision.ALLOW, "url ok")


def check_publication(command: str, *, allow_publication: bool) -> PolicyVerdict:
    """publication 维度（task 4.6）：subagent / 未授权上下文 deny commit/push/PR。

    ``allow_publication`` 由控制面注入——host-side verified publication（task 6.4：长期凭证
    留控制面，subagent/agent 不持），subagent context 默认 ``False``。
    """
    if not command:
        return PolicyVerdict(PermissionDecision.ALLOW, "no publication operand")
    low = command.lower()
    is_pub = any(p in low for p in _PUBLICATION_COMMANDS)
    if is_pub and not allow_publication:
        return PolicyVerdict(
            PermissionDecision.DENY,
            "publication not allowed in this context (host-side only)", "publication",
        )
    return PolicyVerdict(PermissionDecision.ALLOW, "publication ok")


def evaluate_pre_tool_use(tool_name: str, *, command: str = "", path: str = "",
                          write: bool = False, url: str = "",
                          allow_publication: bool = False) -> PolicyVerdict:
    """PreToolUse 顶层策略编排（**纯函数**，无 IO，确定性）。

    优先级（fail-closed：任一维度 deny 即 deny）：
        1. path / protected resource（不可变真源保护最高）；
        2. publication（task 4.6 subagent 防线）；
        3. command（第一阶段 can_use_tool 闸，复用 bash_allowlist）；
        4. network（SSRF 内门）。

    hook adapter 调此 → 落 hook journal + 回写 SDK ``permissionDecision``。
    未知 tool：仍过 command/path/network 维度（不因 tool 名未知就放行危险操作）。
    """
    v = check_path(path, write=write)
    if v.decision is PermissionDecision.DENY:
        return v
    v = check_publication(command, allow_publication=allow_publication)
    if v.decision is PermissionDecision.DENY:
        return v
    v = check_command(command)
    if v.decision is PermissionDecision.DENY:
        return v
    v = check_network(url)
    if v.decision is PermissionDecision.DENY:
        return v
    return PolicyVerdict(PermissionDecision.ALLOW, f"pre-tool-use allowed ({tool_name})")
