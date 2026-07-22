"""compat_readers.py — 历史 dispatch JSON 兼容读取器（OpenSpec add-durable-loop-runtime task 2.7）。

第二阶段 journal 是新真源，但 **迁移期**（``journal_driven_dispatch`` flag 开之前）与 **shadow mode
parity 比对**（task 3.4）必须能读「无 journal 的历史 dispatch 记录」——否则迁移无法验证 parity、回退无据、
shadow 比对缺基准。本读取器把第一阶段 ``dispatch_<stamp>.json`` 的 records 翻译成等价 ``IterationState``
（loop 终态模型），让新旧两套真源可机械比对。

映射规则（``legacy_status``）—— 历史 dispatch ``status`` + ``verify.pass`` + ``verify_verdict`` → ``IterationStatus``：
    * ``pr_open``/``interrupted_pr`` + verify.pass + verify_verdict=='pass' → ``PUBLISHED``（双绿已交付）；
    * ``pr_open``/``interrupted_pr`` + verify.pass 但 verify_verdict 非 pass → ``REVISE``（机械绿但语义红，
      task 4.1 dual gate——防假绿，与 ``_sj_terminal`` 对齐保 shadow parity）；
    * ``pr_open``/``interrupted_pr`` + verify 未过 → ``REVISE``（有 PR 但验证红）；
      **兼容**：历史 record 无 ``verify_verdict`` 字段 → fallback 仅看 ``verify.pass``（迁移前旧契约）；
    * ``blocked_external_state`` → ``EXTERNAL_BLOCKED``；``blocked_test_gate`` → ``TEST_BLOCKED``；
    * ``fail`` → ``FAILED``；``skip`` → ``ABORTED``；``planned`` → ``PLANNED``；
    * **未知 status → ``STATE_CORRUPT``**（fail-closed：不认识的历史态保守标记，绝不假装成功）。

模块依赖 ``loop_state`` 数据模型 + 标准库 json，不触 SDK——cron 隔离不变。
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

from loop_state import IterationState, IterationStatus, initial_state, reduce


# 历史 dispatch record status → IterationStatus 基础映射（verify.pass=False 时 pr_open/interrupted_pr 降级为 REVISE）。
# 未知 status 不在表 → STATE_CORRUPT（fail-closed）。
_LEGACY_STATUS_MAP: dict[str, IterationStatus] = {
    "pr_open": IterationStatus.PUBLISHED,
    "interrupted_pr": IterationStatus.PUBLISHED,
    "blocked_external_state": IterationStatus.EXTERNAL_BLOCKED,
    "blocked_test_gate": IterationStatus.TEST_BLOCKED,
    "fail": IterationStatus.FAILED,
    "skip": IterationStatus.ABORTED,
    "planned": IterationStatus.PLANNED,
    "stalled": IterationStatus.STALLED,                # task 3.5：dev loop 主动刹车终态
    "orphan_deleted": IterationStatus.ORPHAN_DELETED,  # task 3.5：无 commit 孤儿清理终态
}


def legacy_status(record: dict) -> IterationStatus:
    """把一条历史 dispatch record 映射到 ``IterationStatus``。

    规则见模块 docstring。关键：``pr_open``/``interrupted_pr`` 的「已交付」判定 **必须** 双绿佐证
    （task 4.1 dual gate）——``verify.pass=True``（独立机械绿）+ ``verify_verdict=='pass'``（语义绿）。
    只有 PR 不够（验证红的 PR 是 REVISE）；机械绿但语义红亦是 REVISE（防假绿，与 ``_sj_terminal`` 对齐保 parity）。
    **兼容**：历史 record 无 ``verify_verdict`` 字段（迁移前旧格式）→ fallback 仅看 ``verify.pass``。
    未知/缺失 status → ``STATE_CORRUPT``（fail-closed，绝不假装成功）。
    """
    raw = record.get("status")
    mapped = _LEGACY_STATUS_MAP.get(raw)
    if mapped is None:
        return IterationStatus.STATE_CORRUPT

    # 已交付态需双绿佐证（task 4.1 dual gate）：verify.pass（机械绿）+ verify_verdict=='pass'（语义绿）。
    # 历史 record 无 verify_verdict → fallback 仅 verify.pass（兼容迁移前旧契约）。
    if mapped is IterationStatus.PUBLISHED:
        verify = record.get("verify") or {}
        if not verify.get("pass"):
            return IterationStatus.REVISE
        verdict = record.get("verify_verdict")
        if verdict is not None and verdict != "pass":
            return IterationStatus.REVISE
    return mapped


def read_legacy_dispatch(path: str | Path) -> list[IterationState]:
    """读历史 ``dispatch_<stamp>.json`` → 每条 record 翻译成一个等价 ``IterationState``。

    历史 record 无 run/iteration ID（那是 journal 时代才有的），用 ``legacy:<project>:<slug>`` 合成稳定
    标识——给 shadow 比对一个可与 journal-reduced state 对齐的 key。文件缺失/空 → 空列表（非错误）。
    """
    target = Path(path)
    if not target.exists():
        return []
    try:
        records = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []   # 历史 JSON 损坏：不崩，返回空（调用方按「无历史」处理）

    states: list[IterationState] = []
    for r in records:
        if not isinstance(r, dict):
            continue
        project = r.get("project", "?")
        slug = r.get("slug", "")
        legacy_id = f"legacy:{project}:{slug}"
        head = r.get("commit_sha") or r.get("head")
        states.append(IterationState(
            iteration_id=legacy_id,
            run_id=legacy_id,
            prd_id=r.get("prd_path", ""),
            status=legacy_status(r),
            base=r.get("base", "") or "",
            head=head,
        ))
    return states


def summarize_terminal(records: list[dict]) -> Counter:
    """对历史 dispatch records 按等价 ``IterationStatus`` 分桶计数。

    shadow 比对（task 3.4）核心：同一 run 的「历史 dispatch 终态计数」应与「journal-reducer 归约出的
    终态计数」一致——两份 ``Counter`` 相等即 parity 成立（迁移未改变行为）。
    """
    counts: Counter = Counter()
    for r in records:
        counts[legacy_status(r)] += 1
    return counts


def summarize_journal(events: list) -> Counter:
    """对 journal events 按 ``iteration_id`` 分组 reduce，汇总各 iteration 终态计数。

    journal 端的 ``summarize``（对应 dispatch 端 ``summarize_terminal``）。shadow parity（task 3.4）：
    ``summarize_journal(journal_events) == summarize_terminal(dispatch_records)`` 即两套真源终态分布一致。
    每个 iteration 从其首条 event 的 run/prd/base 构造初始 state 后 reduce。
    """
    by_iter: dict[str, list] = defaultdict(list)
    for ev in events:
        by_iter[ev.iteration_id].append(ev)

    counts: Counter = Counter()
    for iter_id, evs in by_iter.items():
        first = evs[0]
        base = first.payload.get("base", "") if isinstance(first.payload, dict) else ""
        state = reduce(evs, initial=initial_state(first.run_id, first.prd_id, iter_id, base=base))
        counts[state.status] += 1
    return counts
