"""merge_loop.py — merge/revert 闭环 crash 安全门（single-flight-auto-merge task 6.x 方案 C / D12）。

防 Agent 实证的致命场景：「merge push main（dev-agent 子进程内一击必杀）后崩溃 → journal 最后事件
非终端 → cron 重新分发 → rebase CLEAN（merge commit 已在 main 历史）→ ``--no-ff merge`` 又造一个
merge commit + push → 重复合 main」。根因：merge/revert push main 是唯一没有 reconcile 幂等种、
没有 crash boundary、没有 intent→push→confirm 写顺序的破坏性副作用（D12 三缺）。

**方案 C（halt+follow-up，用户选定）**：dispatch 级安全门，不碰 reducer/dev-agent 阶段接口：
  * merge/revert phase **前** record ``*_started``（intent）；
  * phase **后** record ``*_completed``（confirm，带 sha）/ ``merge_abandoned``（未 push 安全结束）；
  * 下一轮 cron 进 merge 块**前**查 ``has_open_intent``：上次 started 无闭合（crash 在 phase 中，
    push 可能已发生）→ **halt 整仓 + CRITICAL**（绝不盲目重 merge）。人工查 main_status 判 main 真实状态。

**fail-safe（破坏性副作用准入门，刻意区别于 circuit_breaker 的 fail-open）**：
  * merge_loop 守「致命重复合并推送」（破坏性副作用崩溃恢复门）→ 不确定 → halt；
  * journal 中部损坏（committed history 内坏行，journal.py fail-closed raise）/ 读失败 → ``True``（halt）；
  * 文件缺失 / 该 prd 无事件 / 最后事件是闭合（completed/abandoned/reverted）→ ``False``（正常放行）。
  * 与 circuit_breaker（额外保护层，损坏→False 放行）相反——merge_loop 是 fail-safe 核心，不可放行。

6.1a（loop_state MERGED 非终态）/ 6.1b（reconcile ALLOWED_KINDS merge/revert + resolver）/ 6.1c
（cutover CRASH_BOUNDARIES merge_push/revert_push）为 follow-up（canary 前补）；本安全门是主防线，
先于 reducer/协调种落地，因它直接阻断「盲目重 merge」这一不可逆破坏。

**数据**：per-owner_repo journal（``state_dir/merge_loop/<safe>.journal.jsonl``，跨 run 存活），复用
``journal.append_event`` 原子追加（O_APPEND+fsync，crash 安全）+ ``JournalEvent`` schema。

**时间注入**：``stamp_fn``（ISO 字符串，事件 timestamp）由调用方注入——本模块不触系统时间（同
circuit_breaker 约定，保测试确定性 + cron 隔离）。

纯 IO 模块（文件系统 + journal），零 git/SDK；cron 隔离不变。
"""
from __future__ import annotations

from pathlib import Path

from journal import JournalCorruptionError, append_event, read_events
from loop_state import JOURNAL_SCHEMA_VERSION, JournalEvent

_MERGE_LOOP_DIR = "merge_loop"
# 「started 无闭合 = crash 在 phase 中（push 可能已发生）= open intent」的事件类型
_STARTED_EVENTS: frozenset[str] = frozenset({"merge_started", "revert_started"})


def _safe_name(owner_repo: str) -> str:
    """owner_repo（``owner/repo``，含 ``/``）→ 路径安全文件名段（``/`` → ``__``；同 circuit_breaker/single_flight）。"""
    return owner_repo.replace("/", "__")


def merge_loop_journal_path(state_dir, owner_repo: str) -> Path:
    """merge_loop journal 路径：``<state_dir>/merge_loop/<safe>.journal.jsonl``（per-owner_repo，跨 run 存活）。"""
    return Path(state_dir) / _MERGE_LOOP_DIR / f"{_safe_name(owner_repo)}.journal.jsonl"


def record_event(state_dir, owner_repo: str, prd_id: str, event_type: str, *, stamp_fn, **payload) -> None:
    """记一条 merge/revert 闭环事件到 journal（merge/revert phase 前后调用）。

    事件类型：``merge_started`` / ``merge_completed`` / ``merge_abandoned`` / ``revert_started`` /
    ``revert_completed``。``payload`` 携带 phase 证据（branch/main_ref/merge_commit/revert_commit/reason），
    供人工 halt 后查证 main 真实状态。

    **fail-open 写**：写失败不 raise（merge_loop 安全门不应阻断已发生的 merge/revert 流程本身；破坏性
    副作用已由 git 落盘，journal 记录是崩溃恢复用辅助）。调用方 run_daily 负责 log。复用 ``append_event``
    原子追加（O_APPEND+fsync，crash 安全：已 fsync 的更早记录必可恢复）。
    """
    ts = stamp_fn()
    ev = JournalEvent(schema_version=JOURNAL_SCHEMA_VERSION, event_id=f"ml-{prd_id}-{ts}",
                      timestamp=ts, iteration_id="", run_id="", prd_id=prd_id,
                      event_type=event_type, payload=dict(payload))
    try:
        append_event(merge_loop_journal_path(state_dir, owner_repo), ev)
    except Exception:            # fail-open 写：不阻断 merge/revert 流程本身（git 副作用已落盘）
        pass


def last_event(state_dir, owner_repo: str, prd_id: str):
    """该 prd 的最后一条事件（按 journal 顺序），无记录 → None。

    损坏（中部坏行 → journal.py raise）→ None（本函数只读不判门；fail-safe 判定在 ``has_open_intent``）。
    """
    jp = merge_loop_journal_path(state_dir, owner_repo)
    try:
        events = read_events(jp)
    except (JournalCorruptionError, OSError):
        return None
    prd_events = [e for e in events if e.prd_id == prd_id]
    return prd_events[-1] if prd_events else None


def has_open_intent(state_dir, owner_repo: str, prd_id: str) -> bool:
    """该 prd 是否有未闭合的 merge/revert intent（上次 started 无 confirm → crash 在 phase 中）。

    **fail-safe**（破坏性副作用准入门）：
      * 该 prd 最后事件是 ``merge_started``/``revert_started`` → ``True``（halt，不重 merge/revert）；
      * journal 中部损坏（committed history 内坏行 → journal.py fail-closed raise）/ 读失败 → ``True``
        （不确定上次 merge 是否完成 → halt，人工查 main_status）；
      * 文件缺失 / 该 prd 无事件 / 最后事件是闭合（completed/abandoned/reverted）→ ``False``（正常放行）。

    dispatch merge 块顶部据此门决断：True → halt 整仓 + CRITICAL 告警，绝不盲目重 merge。
    """
    jp = merge_loop_journal_path(state_dir, owner_repo)
    if not jp.exists():
        return False                      # 无 journal → 无 open intent（正常放行）
    try:
        events = read_events(jp)
    except (JournalCorruptionError, OSError):
        return True                       # fail-safe：损坏/读失败 → halt（不冒险重 merge）
    prd_events = [e for e in events if e.prd_id == prd_id]
    if not prd_events:
        return False                      # 该 prd 无记录 → 无 open intent
    return prd_events[-1].event_type in _STARTED_EVENTS
