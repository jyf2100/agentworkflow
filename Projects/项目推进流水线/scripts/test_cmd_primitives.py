#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_cmd_primitives.py — 测试命令链式拆段原语层（agent-verify-injection 2.2，D5.1）。

从 ``dev-agent.py is_clean_test_cmd``（v0.1.14 语义）抽出的**原语层**，一份语义两消费点：
- dev 采集端**信任谓词**（这条自报命令可信吗——形态白名单 + 段约束）；
- verify 锚①**同一性谓词**（执行的命令 ≡ declared cmd）。

**两谓词分开实现勿合一**（本体论再审 D5.1）：信任谓词的段数/形态上限若泄漏进匹配面，会把
agent 会话里合法的链式变体（``cd wt && npm ci && npm test``）错杀成 unmatched——共享的正确
粒度是原语层（拆段/末端段/危险结构检测），不是整判定函数。勿从 v0.1.14 前基线分叉（F7——
m11 缺口③教训：链式一概拒收曾误拦 1132 pass 证据）。

纯 stdlib 零依赖（同 evidence.py / handoff.py 惯例，dev-agent 顶层 import 安全）。
"""
from __future__ import annotations

import re

_CHAIN_SPLIT = re.compile(r";|&&")
_SHELL_STRUCT = re.compile(r"[|><&`]|\$\(")


def split_chain(cmd: str) -> list[str]:
    """链式拆段：按 ``;`` / ``&&`` 切并剥离段前后空白，去空段。

    链的 exit code = 末端命令的（``&&`` 短路 = 前置失败码 ≠ 0，fail-safe 方向安全）——
    这是「采信末端段」语义的依据。
    """
    return [s.strip() for s in _CHAIN_SPLIT.split(cmd or "") if s.strip()]


def tail_segment(cmd: str) -> str:
    """末端段提取：链的 exit code 归属段。"""
    segs = split_chain(cmd)
    return segs[-1] if segs else ""


def has_shell_struct(seg: str) -> bool:
    """危险结构检测：段内含管道/重定向/后台/命令替换（``|`` ``>`` ``<`` ``&`` `` ` `` ``$(``）。

    管道吞 exit code = 假绿风险（``npm test | tail`` 退出码是 tail 的）；重定向/替换/后台
    为变形面——含任一则该命令退出码不可判读。
    """
    return bool(_SHELL_STRUCT.search(seg or ""))


def chain_all_clean(cmd: str) -> bool:
    """全段无危险结构且非空链（原语组合，供两谓词共用）。"""
    segs = split_chain(cmd)
    return bool(segs) and not any(has_shell_struct(s) for s in segs)
