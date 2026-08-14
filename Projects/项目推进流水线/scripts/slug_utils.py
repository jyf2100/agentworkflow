"""dev_slugify 单一源头（ADR-0006 #5 落地 / 消解 ADR-0004 #4 slug shadow）。

dev_slugify 被 dev-agent.py（分支命名）与 run_daily.py（幂等前置闸按 slug 匹配 auto/*
分支）共用——两者必须产同一 slug，否则已投递检查静默失效。

抽到独立无依赖模块的理由：dev-agent.py 顶部 `from claude_agent_sdk import (...)` 是
顶层加载，run_daily.py 若 `from dev_agent import dev_slugify` 会连带触发 sdk 加载；
而 run_cron.sh 用裸 /usr/bin/python3（无 sdk）跑 run_daily.py 顶层 → 每晚 cron 直接崩。
本模块只依赖 re，run_daily.py 顶部 import 它零副作用。单一源头在此，禁止复刻。

算法（与历史 dev-agent.{py,mjs} 等价）：lowercase → [^a-z0-9]+ 替 "-" → 去首尾 "-" → 截断 48。

截断长度 48（非历史 24）：slug = date(8)+project+desc，24 会吞掉描述（cc-web-control 占 15 + date 8 = 23，
描述全截）→ 同日同 project PRD devslug 退化成相同串 → 幂等前置闸子串匹配误判「已投递」→ 静默跳过
（canary 判据 c 暴露，2026-07-30）。48 保留描述区分性，分支总长 auto/<stamp>-<stamp>-<48> 仍 < 255（git ref 安全）。
"""
from __future__ import annotations

import re


def dev_slugify(stem: str) -> str:
    """PRD 文件名 stem → 分支 slug。

    被 dev-agent.py 的分支命名与 run_daily.py 的幂等前置闸（按 slug 匹配 auto/* 分支）
    共用——两者必须产同一 slug，否则已投递检查静默失效。本函数是唯一源头，禁止复刻。
    """
    return re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")[:48]


def make_branch(prefix: str, slug: str) -> str:
    """slug → feature 分支名（verify-loop-discipline change task A1）。

    branch = ``f"{prefix}/{slug}"``，**不含 stamp**。verify revise 各轮重调 dev-agent 是独立进程，
    旧 ``f"{prefix}/{stamp}-{slug}"``（stamp 含时分）→ 每轮不同分支 → 每轮新 PR（#55/#56 根因）。
    本函数从源头保证「需求（slug）不变 → branch 不变」：同 slug → 同 branch（跨进程确定性），使
    **同一 PRD 文件**的 verify revise 各轮 + 崩溃恢复重入 checkout 复用同分支、commits 累积进同一 PR。
    ⚠️ 收敛边界（R3-A F7）：slug 来自 PRD 文件名 stem（含日期前缀）——跨 cron 同一需求生成新 PRD 文件
    → 新 slug → 新分支/新 PR；跨 cron 防重不靠本函数，靠 already_dispatched 前置闸（substring 匹配）。

    **不二次 slugify**：``slug`` 须由 caller 先经 ``dev_slugify`` 规整（职责单一——本函数只拼 prefix，
    规整归 ``dev_slugify``）。

    被 dev-agent.py 分支命名（ADR-0006 #5 单一源头延伸）。
    """
    return f"{prefix}/{slug}"
