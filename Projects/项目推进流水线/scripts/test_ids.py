#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_ids.py — 稳定 ID 生成单测（OpenSpec add-durable-loop-runtime task 3.1）。

第二阶段每次 dispatch/iteration/side-effect 都要有 **稳定、确定的 ID**（design 决策#1）：崩溃恢复重放时，
相同输入必须产出相同 ID——否则 journal 里的 event_id/iteration_id 对不上，reducer 无法 dedup、幂等键失效。

    ``run_id``         per-run（stamp + scope）——同 cron run → 同 id；
    ``prd_id``         per-PRD（prd_path + 内容 hash）——PRD 改动 → 新 id（不可变真源）；
    ``iteration_id``   per-iteration（run + prd + retry 序号）——恢复重放产同 id；
    ``action_id``      per-action（iteration + tool_use_id/序号）——hook 配对依据（task 4.3）；
    ``idempotency_id`` 副作用幂等键（kind + iteration + target）——恢复时同 key → 跳过已执行（exactly-once，
                       task 5.5 reconcile + 8.3 crash drill）。

**确定性是硬契约**：ID 生成不得依赖时间/随机/全局状态——本测试锁定「同入同出」+ 分隔防碰撞 + kind 允许列表。

纯逻辑零依赖模块（hashlib 标准库）。跑：python3 -m pytest scripts/test_ids.py -q。AAA 结构。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import ids as I  # noqa: E402


# ─── 确定性：同入同出（恢复重放的前提）──────────────────────────────────
def test_run_id_stable():
    """同 stamp+scope → 同 run_id（恢复重放、shadow 比对都对齐同一 id）。"""
    assert I.run_id("20260720", "cron") == I.run_id("20260720", "cron")


def test_run_id_changes_with_input():
    """不同 stamp 或 scope → 不同 run_id（区分不同 run）。"""
    assert I.run_id("20260720", "cron") != I.run_id("20260721", "cron")
    assert I.run_id("20260720", "cron") != I.run_id("20260720", "wka")


def test_iteration_id_stable_and_distinct_per_seq():
    """iteration_id = run+prd+seq：同序号稳定；不同 retry 序号 → 不同 id（区分第 1/2 次重投）。"""
    base = (I.run_id("s", "c"), I.prd_id("p/x.md"), 0)
    same = (I.run_id("s", "c"), I.prd_id("p/x.md"), 0)
    assert I.iteration_id(*base) == I.iteration_id(*same)           # 稳定
    assert I.iteration_id(*base) != I.iteration_id(base[0], base[1], 1)  # seq 不同 → 不同


def test_prd_id_changes_with_content_hash():
    """prd_id 含内容 hash：PRD 内容改 → 新 id（design「PRD 是不可变真源」——改了就是新 PRD）。"""
    path = "prd/demo/add-x.md"
    assert I.prd_id(path, "hash_v1") != I.prd_id(path, "hash_v2")


# ─── 分隔防碰撞（"ab"+"cd" 绝不能 == "a"+"bcd"）─────────────────────────
def test_digest_separator_prevents_collision():
    """_digest 用 unit-separator 分隔——防 ``("ab","cd")`` 与 ``("a","bcd")`` 撞同一 hash。

    无分隔的话拼接碰撞会让两个不同 PRD/iteration 误判为同一个——dedup/幂等全乱。"""
    assert I._digest("ab", "cd") != I._digest("a", "bcd")
    assert I._digest("a", "b", "c") != I._digest("a", "b:c")   # 多段也不撞


# ─── 格式（前缀让人在日志/state 里一眼分辨 id 种类）─────────────────────
def test_id_prefixes_distinguish_kind():
    """每种 id 带稳定前缀——日志/state 里一眼分辨 run/prd/iter/act/idem（运维 triage 友好）。"""
    assert I.run_id("s", "c").startswith("run_")
    assert I.prd_id("p.md").startswith("prd_")
    assert I.iteration_id("run_x", "prd_y", 0).startswith("iter_")
    assert I.action_id("iter_x", "tu_1").startswith("act_")
    assert I.idempotency_id(I.IDEMPOTENCY_COMMIT, "iter_x", "main").startswith("idem_")


# ─── 幂等键：kind 允许列表 + target 区分 ────────────────────────────────
def test_idempotency_id_stable_for_same_side_effect():
    """同一副作用（commit/push/pr × iteration × target）→ 同幂等键——恢复时据之跳过已执行（exactly-once）。"""
    k1 = I.idempotency_id(I.IDEMPOTENCY_PR, "iter_x", "auto/demo-add-x")
    k2 = I.idempotency_id(I.IDEMPOTENCY_PR, "iter_x", "auto/demo-add-x")
    assert k1 == k2


def test_idempotency_id_distinct_per_kind_and_target():
    """不同副作用种类或目标 → 不同键（commit ≠ push ≠ pr；不同 branch ≠）。"""
    a = I.idempotency_id(I.IDEMPOTENCY_COMMIT, "iter_x", "main")
    b = I.idempotency_id(I.IDEMPOTENCY_PUSH, "iter_x", "main")
    c = I.idempotency_id(I.IDEMPOTENCY_PR, "iter_x", "auto/demo-add-x")
    assert len({a, b, c}) == 3


def test_idempotency_id_rejects_unknown_kind():
    """kind 必须在允许列表（commit/push/pr）——防构造非法幂等键混入 reconcile 逻辑（边界校验）。"""
    with pytest.raises(ValueError):
        I.idempotency_id("malicious", "iter_x", "main")


def test_action_id_distinct_per_tool_use():
    """action_id 区分不同 tool 调用——hook 配对（task 4.3 PreToolUse↔PostToolUse）的串联依据。"""
    a1 = I.action_id("iter_x", "tu_1")
    a2 = I.action_id("iter_x", "tu_2")
    assert a1 != a2
