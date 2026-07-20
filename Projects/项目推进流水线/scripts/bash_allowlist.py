#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bash_allowlist.py — dev-agent 的 Bash 命令放行闸（ADR-0006 #7 长效修法）。

why
---
vault dev-agent 在被控仓 worktree 里跑 SDK，``permission_mode="acceptEdits"`` 只自动批编辑类、
**不自动批 Bash**。历史靠各仓 ``.claude/settings.local.json`` 里的 ``Bash(...)`` allow 规则放行——
但该文件 **gitignored、机器本地**，worktree（尤其 ``/tmp`` 或跨机新克隆）常常摸不到，导致 headless
下 ``python``/``pytest`` 被 approval 闸拦死、``test_passed=false``（2026-07-18 dry-run 实证）。

本模块把放行规则收敛进**控制面单一源头**，dev-agent 经 SDK 的 ``can_use_tool`` 回调调用，**摆脱对
机器本地 settings 的依赖**——任意 worktree 摆放、任意被控仓，dev loop 都能确定性地跑测试/git。

安全姿态（贴合 ADR-0006 #7）
---------------------------
**默认拒绝**。仅放行 dev loop 合法需要的命令族（测试 / 构建 / lint / VCS / 只读探查 / 仓内文件操作），
显式拒绝网络外传与破坏性操作（sudo / curl / wget / ssh / rm 系统路径 / mkfs / base64 解码等）。

局限（如实声明）：prefix 匹配**不是硬沙箱**——shell 元字符 / ``python -c "os.system('...')"`` 类
内嵌调用、dev 刚 Write 出的 ``./脚本.sh``、``pip install`` typo-squat 等理论上可绕。威胁模型为「可控
内部仓的自家流水线」：本闸旨在挡住**模型误操与注入触发的明显危险命令**（网络外传/提权/系统破坏/
远程分支删除/克隆/家目录逃逸）并提供审计点，**不抗定向沙箱逃逸**。

⚠ 威胁模型升级提示（2026-07-20 code-review）：本变更的 streaming 修复使本闸成为 dev loop 的**实时**
Bash 屏障（此前 ``can_use_tool`` 未真生效）。部分 PRD 由外源信号（radar 扫 wechat/github）生成 → 对抗性
PRD 是现实向量，docstring 的「可信内部」框架不应低估。本次已补廉价 deny（``git push --delete`` / ``clone``
/ ``remote`` 改配 / ``rm`` ``..`` 与 ``$HOME`` 逃逸）；**深度隔离（容器 / 网络出口 allowlist）应单列
ADR/issue**，本闸只作第一层。需要硬隔离时叠加 OS 级沙箱（firejail / 容器 + 出口代理）。

依赖：仅 ``re``（与 slug_utils.py 同，无 sdk，cron /usr/bin/python3 可裸 import）。
"""
from __future__ import annotations

import re

# ─── 显式拒绝：命令文本命中即拒（优先于 allow 判定）──────────────────────────
# 每条用 \b 词边界 + 前导分隔符锚定，避免误伤文件名（如 evaluate.py 不被 eval 命中）。
_DENY_CLAUSES = [
    r"sudo\b",
    r"\bsu\b",
    r"chmod\b[^|;&\n]*\b777\b",
    # 网络外传
    r"\bcurl\b",
    r"\bwget\b",
    r"\bnc\b",
    r"\bnetcat\b",
    r"\bssh\b",
    r"\bscp\b",
    r"\brsync\b",
    r"\bftp\b",
    r"\btelnet\b",
    # 磁盘 / 系统破坏
    r"\bmkfs\b",
    r"\bdd\b\s+if=",
    r"\bshutdown\b",
    r"\breboot\b",
    r"\bpoweroff\b",
    r"\bhalt\b",
    # rm 系统路径 / 家目录（仓内 rm build/ 等不命中）
    r"\brm\b\s+(?:-\S+\s+)*(?:/+|~)",
    # rm 父目录(..)/家目录变量($HOME)逃逸（仓内 rm build/、rm ./dist 不命中——只拦 .. 与 $VAR 形态）
    r"\brm\b\s+(?:-\S+\s+)*(?:\.\.|\$\{?[A-Za-z])",
    # git 远程破坏 / 网络克隆 / 改 remote（dev 守则禁；reconcile 的 push --delete 走 run_daily 直
    # subprocess、不经本 can_use_tool 闸，故此拒只作用于 dev loop 内的 Bash）
    r"\bgit\b[^|;&\n]*\bpush\b[^|;&\n]*--delete\b",
    r"\bgit\b[^|;&\n]*\bclone\b",
    r"\bgit\b[^|;&\n]*\bremote\b[^|;&\n]*\b(?:add|set-url|remove|rename)\b",
    # 写系统目录
    r">\s*/(?:etc|boot|proc|sys|usr|var)/",
    # 混淆 / 解码外传
    r"\bbase64\b\s+[^|;&\n]*-(?:d|D|decode|decode-input)",
    # 包管理装未知来源（pip/npm 装包允许，但禁止从 URL 直装）
    r"\bpip\b[^|;&\n]*install\s+https?://",
    r"\bnpm\b[^|;&\n]*install\s+https?://",
]
_DENY_RE = re.compile(r"(?:^|[\s|;&`$(>])(" + "|".join(_DENY_CLAUSES) + ")")


# ─── 放行命令族：首个有效 token 的 basename 命中即放行 ──────────────────────────
_ALLOWED_TOKENS = frozenset({
    # Python 测试 / 包 / lint
    "python", "python2", "python3",
    "python3.8", "python3.9", "python3.10", "python3.11", "python3.12", "python3.13", "python3.14",
    "pytest", "py.test", "ipython",
    "pip", "pip3", "uv", "poetry", "pipenv", "tox", "coverage", "conda", "virtualenv",
    "ruff", "black", "isort", "mypy", "pyright", "flake8", "pylint",
    # Node 测试 / 构建 / lint
    "node", "npm", "npx", "pnpm", "yarn", "tsc", "tsx", "jest", "vitest", "mocha", "karma", "babel",
    "eslint", "prettier", "stylelint",
    # VCS
    "git",
    # 只读探查
    "ls", "ll", "cat", "head", "tail", "less", "more", "find", "locate",
    "grep", "egrep", "fgrep", "rg", "ag", "fd", "ack",
    "echo", "printf", "pwd", "wc", "which", "whereis", "file", "stat", "tree", "du", "df",
    "env", "printenv", "hostname", "whoami", "id", "date", "uname", "diff", "cmp",
    # 仓内文件操作（路径由仓 CLAUDE.md / worktree 边界约束）
    "mkdir", "touch", "cp", "mv", "rm", "rmdir", "ln",
    "sed", "awk", "tr", "sort", "uniq", "cut", "paste", "tee",
    "tar", "unzip", "zip", "gzip", "gunzip", "zcat",
    # 脚本入口
    "bash", "sh", "source", "zsh",
    # 其他构建链
    "make", "cmake", "cargo", "go", "dotnet", "gcc", "g++", "cc", "ld",
})

# 前导 ``VAR=val`` 赋值（可多个）—— ``PYTHONPATH=x FOO=y python ...``
_LEADING_ASSIGN = re.compile(r"^(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)+")
# 前导 ``cd ... &&`` / ``cd ... ;`` —— ``cd src && python -m pytest``
_LEADING_CD = re.compile(r"^cd\s+\S+?(?:\s*&&?\s*|;)+")


def first_command_token(command: str) -> str:
    """提取命令首个有效 token 的 basename。

    剥离前导 ``VAR=val`` 赋值与 ``cd ... &&``/``;`` 链（循环最多 6 轮，覆盖
    ``cd a && cd b && python ...``），再取首个词、取 basename（``/usr/bin/python3`` → ``python3``）。
    """
    s = command.strip()
    for _ in range(6):
        nxt = _LEADING_CD.sub("", s, count=1)
        nxt = _LEADING_ASSIGN.sub("", nxt, count=1)
        if nxt == s:
            break
        s = nxt
    s = s.lstrip(";()&| \t")
    parts = s.split()
    if not parts:
        return ""
    return parts[0].rsplit("/", 1)[-1]


def decide_bash(command: str) -> tuple[bool, str]:
    """判定一条 Bash 命令是否放行。

    Returns:
        ``(allowed, reason)`` —— allowed=True 时 reason 为放行依据（命中 token）；
        False 时 reason 为拒绝原因（供权限闸回写 SDK deny message + 审计）。
    """
    if command is None or not command.strip():
        return False, "空命令"
    if _DENY_RE.search(command):
        return False, f"命中拒绝规则（网络/破坏性/提权 等）: {command.strip()[:80]}"
    tok = first_command_token(command)
    if tok in _ALLOWED_TOKENS:
        return True, f"放行命令族: {tok}"
    # 仓内脚本：./run.sh / tests/run.sh / build.sh
    if tok.endswith(".sh") or tok.startswith("./"):
        return True, f"放行仓内脚本: {tok}"
    return False, f"未在放行表: {tok or command.strip()[:40]}"
