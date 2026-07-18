"""dev_slugify 单一源头（ADR-0006 #5 落地 / 消解 ADR-0004 #4 slug shadow）。

dev_slugify 被 dev-agent.py（分支命名）与 run_daily.py（幂等前置闸按 slug 匹配 auto/*
分支）共用——两者必须产同一 slug，否则已投递检查静默失效。

抽到独立无依赖模块的理由：dev-agent.py 顶部 `from claude_agent_sdk import (...)` 是
顶层加载，run_daily.py 若 `from dev_agent import dev_slugify` 会连带触发 sdk 加载；
而 run_cron.sh 用裸 /usr/bin/python3（无 sdk）跑 run_daily.py 顶层 → 每晚 cron 直接崩。
本模块只依赖 re，run_daily.py 顶部 import 它零副作用。单一源头在此，禁止复刻。

算法（与历史 dev-agent.{py,mjs} 等价）：lowercase → [^a-z0-9]+ 替 "-" → 去首尾 "-" → 截断 24。
"""
from __future__ import annotations

import re


def dev_slugify(stem: str) -> str:
    """PRD 文件名 stem → 分支 slug。

    被 dev-agent.py 的分支命名与 run_daily.py 的幂等前置闸（按 slug 匹配 auto/* 分支）
    共用——两者必须产同一 slug，否则已投递检查静默失效。本函数是唯一源头，禁止复刻。
    """
    return re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")[:24]
