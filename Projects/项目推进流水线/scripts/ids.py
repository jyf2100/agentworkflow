"""ids.py — 稳定 ID 生成（OpenSpec add-durable-loop-runtime task 3.1）。

第二阶段每次 dispatch/iteration/side-effect 都要有 **稳定、确定的 ID**（design 决策#1）。崩溃恢复重放时，
相同输入必须产出相同 ID——否则 journal 里的 ``event_id``/``iteration_id`` 对不上，reducer 无法 dedup、
幂等键失效、shadow 比对错位。因此 **ID 生成纯确定性**：``sha256(确定输入)``，不依赖时间/随机/全局状态。

ID 种类（前缀让人在日志/state 一眼分辨）：
    ``run_id``         per-run（stamp + scope）——同 cron run → 同 id；
    ``prd_id``         per-PRD（prd_path + 内容 hash）——PRD 改动 → 新 id（不可变真源）；
    ``iteration_id``   per-iteration（run + prd + retry 序号）——恢复重放产同 id（reducer dedup 依据）；
    ``action_id``      per-action（iteration + tool_use_id/序号）——hook 配对（task 4.3）串联依据；
    ``idempotency_id`` 副作用幂等键（commit/push/pr × iteration × target）——恢复时同 key → 跳过已执行
                       （exactly-once effective，task 5.5 reconcile + 8.3 crash drill）。

纯逻辑零依赖模块（hashlib 标准库）。cron 隔离不变。
"""
from __future__ import annotations

import hashlib

# ID hex 长度：16 hex = 64 bit，单 run 内碰撞概率可忽略（2^64 空间）；前缀 + 16hex 足够辨识与去重。
_ID_LEN = 16

# unit separator（0x1f）分隔各 part——防 ``("ab","cd")`` 与 ``("a","bcd")`` 拼接碰撞（无分隔会撞同一 hash）。
_SEP = "\x1f"

# 副作用幂等键种类（idempotency_id 的 kind 允许列表）。
IDEMPOTENCY_COMMIT = "commit"
IDEMPOTENCY_PUSH = "push"
IDEMPOTENCY_PR = "pr"
_IDEMPOTENCY_KINDS = frozenset({IDEMPOTENCY_COMMIT, IDEMPOTENCY_PUSH, IDEMPOTENCY_PR})


def _digest(*parts) -> str:
    """从若干 part 合成稳定 hex digest（sha256 前 ``_ID_LEN`` 位）。part 任意类型（先 str 化）。"""
    raw = _SEP.join(str(p) for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:_ID_LEN]


def run_id(stamp: str, scope: str = "") -> str:
    """per-run ID：``stamp``（cron run 时间戳）+ ``scope``（cron/wka/手动）。同 run → 同 id。"""
    return f"run_{_digest(stamp, scope)}"


def prd_id(prd_path: str, content_hash: str | None = None) -> str:
    """per-PRD ID：``prd_path`` + 内容 hash（若给）。内容 hash 使 PRD 改动 → 新 id（不可变真源）。"""
    return f"prd_{_digest(prd_path, content_hash or '')}"


def iteration_id(run: str, prd: str, seq: int) -> str:
    """per-iteration ID：``run`` + ``prd`` + retry 序号 ``seq``。恢复重放产同 id（reducer dedup 依据）。"""
    return f"iter_{_digest(run, prd, seq)}"


def action_id(iteration: str, tool_use_id: str | None = None, seq: int | None = None) -> str:
    """per-action ID：``iteration`` + ``tool_use_id``（SDK 工具调用 id）或序号 ``seq``。

    hook 配对（task 4.3 PreToolUse↔PostToolUse）与工具结果落盘的串联依据。
    """
    return f"act_{_digest(iteration, tool_use_id or '', seq if seq is not None else '')}"


def idempotency_id(kind: str, iteration: str, target: str) -> str:
    """副作用幂等键：``kind``（commit/push/pr）+ ``iteration`` + ``target``（branch/repo）。

    恢复重放时同 key → 该副作用已执行则跳过（exactly-once effective）。``kind`` 必须在允许列表
    （commit/push/pr），防构造非法幂等键混入 reconcile 逻辑。
    """
    if kind not in _IDEMPOTENCY_KINDS:
        raise ValueError(f"非法 idempotency kind（允许 commit/push/pr）: {kind!r}")
    return f"idem_{_digest(kind, iteration, target)}"
