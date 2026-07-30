"""single_flight.py — per-owner_repo 单飞 slot：跨进程 flock + journal 持久化在途态（D9）。

single-flight-auto-merge task 2.2（slot 准入门）+ task 2.3（slot crash 恢复）。

**为什么需要它**（design D9 / spec single-flight-auto-merge「Per-repository serial single-flight
consumption」）：dispatch 段同目标仓 PRD 必须串行单飞（一次一个走完 dev→verify→merge 闭环）。task 2.1
用进程内 ``DISPATCH_LOCKS``（threading.Lock）实现了同进程串行，但 **threading.Lock 跨 cron 进程不可见**
（cron 每次 ``run_daily`` 起新进程，D9 审核一致 F5）——上一轮 cron crash 后，新进程看不到旧锁，会误判
slot 空闲而并发投递。本模块把「串行保证」升级为**跨进程**：

    slot 状态 = journal 在途闭环状态（append-only slot journal 重放）+ lease TTL
              + 跨进程 flock（fcntl ``LOCK_EX|LOCK_NB``，crash 时 OS 自动释放）

**三态**（对齐 ``external_state.ExtState`` fail-safe 范式，但 slot 是本地态非远程查询）：
    FREE       无记录 / 已 ``slot_released`` / ``slot_acquired`` 但 lease 已过期（crash 残留）
    IN_FLIGHT  ``slot_acquired`` 且 lease 未过期——另一闭环在途
    UNKNOWN    slot journal 中部损坏 / 读失败——fail-safe：**不当代空闲**（spec「Single-flight
               slot is unknown → blocked_external_state」），交 dispatch 准入阻断

**task 2.3 crash 恢复**：cron crash 后 flock 被 OS 释放，但 slot journal 留 ``slot_acquired`` 记录。
``query_slot`` 据 lease TTL 显式判定：lease 过期 → **known FREE**（基于 lease-expiry 的显式判定，非盲目
free，spec「resolve to known, MUST NOT default to free」）；lease 未过期 → in-flight-with-lease（保留不
接管，等 lease 过期或人工）。journal 中部损坏 → UNKNOWN（fail-closed，绝不静默跳过——同 journal.py 策略）。

**为什么 slot 用独立 per-owner_repo journal**：现有 shadow journal 是 per-run-per-PRD
（``state_dir/runs/<proj>/<stamp>_<slug>.journal.jsonl``），无法承载跨 cron（跨 stamp）的 per-owner_repo
slot 态。故 slot 落 ``state_dir/slots/<safe_owner_repo>.journal.jsonl``（append-only，跨 run 存活），
复用 ``journal.append_event`` 的原子追加（``O_APPEND``+``fsync``，crash 安全）。

**时间注入**：``now_fn``（返回 datetime，lease 算术）+ ``stamp_fn``（返回 ISO 字符串，事件 timestamp）由
调用方注入——本模块不触系统时间（同 ``ShadowJournal`` 约定，保测试确定性 + cron 隔离）。

**flag gating** 在调用方（``run_daily._run_one``）：``single_flight_serial_shadow`` on → 走 slot_scope；
off → 原 threading.Lock baseline 不变（design 决策#8）。

纯 IO 模块（文件系统 + fcntl + journal），零 SDK；cron 隔离不变。Linux only（fcntl POSIX）。
"""
from __future__ import annotations

import fcntl
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import ClassVar

from journal import JournalCorruptionError, append_event, read_events
from loop_state import JOURNAL_SCHEMA_VERSION, JournalEvent

# slot lease 默认 TTL（秒）。须 ≥ 单 PRD dev→verify→merge 闭环 wall-clock 上界（D10）——lease 内判在途，
# lease 过期判 crash 残留（known FREE）。默认 2h（宽于单闭环；调用方可按 profile 覆盖）。
DEFAULT_SLOT_LEASE_TTL: int = 7200

_SLOT_DIR = "slots"
_SLOT_EVENTS: frozenset[str] = frozenset({"slot_acquired", "slot_released",
                                           "slot_halted", "slot_resumed"})


class SlotState(str, Enum):
    """slot 四态（str 子类化便于 JSON 序列化进 state 记录，同 ``ExtState``）。

    ``HALTED``（task 4.2/4.3）：post-merge UNKNOWN / revert 非 REVERTED → 整仓 halt 到人工。HALTED 是**终态**
    （覆盖 lease：lease 过期也不自动 free），直到 ``resume_slot`` 人工 unblock（spec「no further PRD admitted
    until manual resolution」）。lease 撑不住「halt 到人工」（2h 过期自动 free），故独立 HALTED 态。
    """

    FREE = "free"             # 无在途（无记录 / released / resumed / lease 过期 stale）
    IN_FLIGHT = "in_flight"   # acquired 且 lease 未过期——另一闭环在途
    HALTED = "halted"         # slot_halted 终态——整仓 halt 到人工（覆盖 lease，不自动 free）
    UNKNOWN = "unknown"       # journal 损坏/读失败 → fail-safe 阻断（不当代空闲）


@dataclass(frozen=True)
class SlotQuery:
    """``query_slot`` 结果。``UNKNOWN`` 是 fail-safe 信号：dispatch 准入见之即 blocked_external_state。"""

    __test__: ClassVar[bool] = False
    state: SlotState
    lease_expires_at: str | None   # IN_FLIGHT 时的 lease 过期点（ISO）；其余 None
    reason: str                    # 已脱敏诊断（no_record / released / stale_lease_expired / in_flight / 损坏）


@dataclass(frozen=True)
class AcquireResult:
    """``acquire_slot`` 结果。``acquired=False`` 时 ``blocked_reason`` 标阻断类（inflight/unknown/flock_busy）。"""

    __test__: ClassVar[bool] = False
    acquired: bool
    blocked_reason: str | None
    query: SlotQuery


@dataclass
class SlotHandle:
    """已获取 slot 的持有句柄（``release_slot`` 消费）。持 flock fd，release 时 close → unlock。"""

    __test__: ClassVar[bool] = False
    fd: int
    lock_path: Path
    journal_path: Path
    _released: bool = False
    _halted: bool = False      # task 4.x：halt_slot 标记——release_slot 见之跳过 slot_released（保 HALTED 终态）


def _safe_name(owner_repo: str) -> str:
    """owner_repo（``owner/repo``，含 ``/``）→ 路径安全文件名段（``/`` → ``__``）。

    owner/repo 来自 git remote（GitHub 用户名/仓名，字母数字-_.），唯一路径不安全字符是 ``/``。
    """
    return owner_repo.replace("/", "__")


def slot_journal_path(state_dir, owner_repo: str) -> Path:
    """slot journal 路径：``<state_dir>/slots/<safe>.journal.jsonl``（per-owner_repo，跨 run 存活）。"""
    return Path(state_dir) / _SLOT_DIR / f"{_safe_name(owner_repo)}.journal.jsonl"


def slot_lock_path(state_dir, owner_repo: str) -> Path:
    """跨进程 flock lock 文件路径：``<state_dir>/slots/<safe>.lock``。"""
    return Path(state_dir) / _SLOT_DIR / f"{_safe_name(owner_repo)}.lock"


def _parse_dt(s: str) -> datetime:
    """解析 ISO 字符串为 datetime（容忍 ``Z`` 后缀 → ``+00:00``，py3.11 前 fromisoformat 不认 Z）。"""
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def query_slot(state_dir, owner_repo: str, *, now_fn) -> SlotQuery:
    """查 slot 当前态（**纯读**）：重放 slot journal + lease TTL 判定。

    末尾 ``slot_acquired`` 是当前态锚点：lease 未过期 → IN_FLIGHT；过期 → known FREE（stale）。
    ``slot_released`` / 无记录 → FREE。journal 中部损坏 / 读失败 → UNKNOWN（fail-safe，不当代空闲）。

    Args:
        state_dir: 运行时 state 根（slot journal 落 ``slots/``）。
        owner_repo: ``owner/repo``。
        now_fn: 注入的「当前时间」()→datetime（lease 算术；本模块不触系统时间）。
    """
    jp = slot_journal_path(state_dir, owner_repo)
    if not jp.exists():
        return SlotQuery(SlotState.FREE, None, "no_record: slot journal 不存在（从未投递该仓）")
    try:
        events = read_events(jp)
    except JournalCorruptionError as ex:
        return SlotQuery(SlotState.UNKNOWN, None,
                         f"journal 损坏 line {ex.line_number}: fail-closed，不当代空闲")
    except OSError as ex:
        return SlotQuery(SlotState.UNKNOWN, None, f"journal 读失败: {ex}")

    # append-only：最后一条 slot 事件 = 当前态（acquired→released→acquired 则在途）
    last = None
    for e in events:
        if e.event_type in _SLOT_EVENTS:
            last = e
    if last is None:
        return SlotQuery(SlotState.FREE, None, "no_record: journal 无 slot 事件")
    if last.event_type == "slot_halted":
        return SlotQuery(SlotState.HALTED, None,
                         f"halted: {last.payload.get('reason', 'post_merge_safety')}（整仓 halt 到人工，覆盖 lease）")
    if last.event_type == "slot_resumed":
        return SlotQuery(SlotState.FREE, None, "resumed: 人工 unblock halt，slot 恢复空闲")
    if last.event_type == "slot_released":
        return SlotQuery(SlotState.FREE, None, "released: 上次闭环已释放 slot")
    # slot_acquired：判 lease（task 2.3 crash 恢复核心）
    lease_str = last.payload.get("lease_expires_at", "")
    try:
        lease_dt, now_dt = _parse_dt(lease_str), now_fn()
    except (ValueError, TypeError) as ex:
        return SlotQuery(SlotState.UNKNOWN, None, f"lease 解析失败: {ex}")
    if now_dt < lease_dt:
        return SlotQuery(SlotState.IN_FLIGHT, lease_str, f"in_flight: lease 至 {lease_str} 未过期")
    return SlotQuery(SlotState.FREE, None,
                     f"stale_lease_expired: lease {lease_str} 已过期（crash 残留，known FREE 非盲目）")


def _append_slot_event(journal_path, *, event_type: str, run_id: str, prd_id: str,
                       iteration_id: str, owner_repo: str, stamp_fn,
                       lease_expires_at: str | None = None,
                       outcome: str | None = None,
                       reason: str | None = None) -> None:
    """原子追加一条 slot 事件（复用 ``journal.append_event`` 的 O_APPEND+fsync）。"""
    payload: dict = {"owner_repo": owner_repo}
    if lease_expires_at is not None:
        payload["lease_expires_at"] = lease_expires_at
    if outcome is not None:
        payload["outcome"] = outcome
    if reason is not None:
        payload["reason"] = reason
    ev = JournalEvent(
        schema_version=JOURNAL_SCHEMA_VERSION,
        event_id=f"slot-{run_id}-{prd_id}-{event_type}",   # 单次投递 acquire/release 各一，event_type 区分唯一
        timestamp=stamp_fn(),
        iteration_id=iteration_id, run_id=run_id, prd_id=prd_id,
        event_type=event_type, payload=payload,
    )
    append_event(journal_path, ev)


def acquire_slot(state_dir, owner_repo: str, *, run_id: str, prd_id: str, iteration_id: str,
                 now_fn, stamp_fn, lease_ttl: int = DEFAULT_SLOT_LEASE_TTL):
    """尝试获取 slot（准入门）：query → flock → 写 acquired，返回 ``(AcquireResult, SlotHandle|None)``。

    顺序保证无 TOCTOU：flock 临界区内才 query+写 acquired，跨进程互斥。
      - query IN_FLIGHT → ``(blocked=inflight, None)``（另一闭环在途，本 PRD 让位）；
      - query UNKNOWN   → ``(blocked=unknown, None)``（fail-safe，交 dispatch 记 blocked_external_state）；
      - query FREE      → flock ``LOCK_EX|LOCK_NB``：成功→写 acquired+返回 handle；被占→``(blocked=flock_busy, None)``。

    crash 安全：flock 持有期间进程崩 → OS 释放 flock，slot journal 留 acquired，下轮 ``query_slot`` 据 lease
    判 stale（task 2.3）。
    """
    q = query_slot(state_dir, owner_repo, now_fn=now_fn)
    if q.state is SlotState.IN_FLIGHT:
        return AcquireResult(False, "inflight", q), None
    if q.state is SlotState.UNKNOWN:
        return AcquireResult(False, "unknown", q), None
    if q.state is SlotState.HALTED:
        # task 4.2/4.3：halt 整仓到人工——新 PRD 不投递（spec「no further PRD admitted until manual resolution」）
        return AcquireResult(False, "halted", q), None
    # FREE：跨进程 flock（O_CREAT 兜底建 lock 文件）
    lk = slot_lock_path(state_dir, owner_repo)
    lk.parent.mkdir(parents=True, exist_ok=True)
    jp = slot_journal_path(state_dir, owner_repo)
    try:
        fd = os.open(lk, os.O_CREAT | os.O_RDWR)
    except OSError as ex:                       # lock 文件创建失败 = 状态不明（盘满/权限）→ fail-safe
        return AcquireResult(False, "unknown",
                             SlotQuery(SlotState.UNKNOWN, None, f"lock 文件创建失败: {ex}")), None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):          # 另一进程持锁 → 不阻塞等（cron 有 wall-clock 上限）
        os.close(fd)
        return AcquireResult(False, "flock_busy", q), None
    # flock 持有 → 写 acquired（lease = now + ttl）
    lease_str = (now_fn() + timedelta(seconds=lease_ttl)).isoformat()
    _append_slot_event(jp, event_type="slot_acquired", run_id=run_id, prd_id=prd_id,
                       iteration_id=iteration_id, owner_repo=owner_repo, stamp_fn=stamp_fn,
                       lease_expires_at=lease_str)
    return AcquireResult(True, None, q), SlotHandle(fd=fd, lock_path=lk, journal_path=jp)


def release_slot(handle: SlotHandle, *, stamp_fn, run_id: str, prd_id: str,
                 iteration_id: str, owner_repo: str, outcome: str = "done") -> None:
    """释放 slot：写 ``slot_released`` + 释放 flock（close fd → unlock）。幂等（重复 release no-op）。

    task 4.x halt 路径：若 ``handle._halted``（``halt_slot`` 已写 ``slot_halted`` 终态），**跳过** ``slot_released``
    追加——保末事件 = ``slot_halted`` → 下轮 ``query_slot`` 判 HALTED（不自动 free）。flock 仍释放（进程退出语义）。
    """
    if handle._released:
        return
    if not handle._halted:   # halt 是终态：不写 slot_released 覆盖（保 HALTED）
        _append_slot_event(handle.journal_path, event_type="slot_released", run_id=run_id, prd_id=prd_id,
                           iteration_id=iteration_id, owner_repo=owner_repo, stamp_fn=stamp_fn, outcome=outcome)
    try:
        fcntl.flock(handle.fd, fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        os.close(handle.fd)
    except OSError:
        pass
    handle._released = True


def halt_slot(handle: SlotHandle, *, reason: str, run_id: str, prd_id: str,
              iteration_id: str, owner_repo: str, stamp_fn) -> None:
    """把已获取的 slot 标 HALTED（写 ``slot_halted`` 终态事件）——task 4.2/4.3 整仓 halt。

    spec「Revert itself fails halts the queue ... no further PRD admitted until manual resolution」+「UNKNOWN test
    result SHALL halt」。触发场景：post-merge UNKNOWN（不 auto-revert，keep+halt）/ revert 非 REVERTED（CONFLICT/
    UNKNOWN）。HALTED 覆盖 lease（lease 过期也不自动 free），直到 ``resume_slot`` 人工 unblock。

    标 ``handle._halted``：后续 ``release_slot``（slot_scope ``__exit__``）见之跳过 ``slot_released``，保末事件 =
    ``slot_halted``。幂等（重复 halt no-op）。须在持 slot（acquired）时调；调用方 ``_run_one`` 经 ``scope.handle`` 取句柄。
    """
    if handle._halted:
        return
    _append_slot_event(handle.journal_path, event_type="slot_halted", run_id=run_id, prd_id=prd_id,
                       iteration_id=iteration_id, owner_repo=owner_repo, stamp_fn=stamp_fn, reason=reason)
    handle._halted = True


def resume_slot(state_dir, owner_repo: str, *, run_id: str, prd_id: str,
                iteration_id: str, stamp_fn) -> None:
    """人工 unblock halt（写 ``slot_resumed`` → FREE，恢复投递）——运维 triage 解决 halt 原因后手动调。

    幂等 append（末事件 ``slot_resumed`` → ``query_slot`` 判 FREE → 可重新 acquire）。recovery_cli / 运维工具接线点
    （task 4.6「可查询的 halt 状态」+ 人工 unblock 闭环）。
    """
    jp = slot_journal_path(state_dir, owner_repo)
    jp.parent.mkdir(parents=True, exist_ok=True)
    _append_slot_event(jp, event_type="slot_resumed", run_id=run_id, prd_id=prd_id,
                       iteration_id=iteration_id, owner_repo=owner_repo, stamp_fn=stamp_fn)


class slot_scope:
    """slot lifecycle context manager：``with slot_scope(...) as res:`` 包裹 dispatch_one 闭环。

    ``__enter__`` acquire；``__exit__`` 仅在 acquired 时 release（try/finally 语义，异常也释放，flock 不泄漏）。
    blocked（inflight/unknown/flock_busy）时 ``res.acquired=False``，调用方据 ``blocked_reason`` 构造 rec。
    """

    def __init__(self, state_dir, owner_repo: str, *, run_id: str, prd_id: str, iteration_id: str,
                 now_fn, stamp_fn, lease_ttl: int = DEFAULT_SLOT_LEASE_TTL):
        self._acquire_kwargs = dict(state_dir=state_dir, owner_repo=owner_repo, run_id=run_id,
                                    prd_id=prd_id, iteration_id=iteration_id, now_fn=now_fn,
                                    stamp_fn=stamp_fn, lease_ttl=lease_ttl)
        self._release_owner_repo = owner_repo
        self._handle: SlotHandle | None = None
        self._acquired = False

    def __enter__(self) -> AcquireResult:
        res, handle = acquire_slot(**self._acquire_kwargs)
        self._handle = handle
        self.handle = handle    # task 4.x：公开句柄供 _run_one 在 halt 路径调 halt_slot（acquired 时非 None）
        self._acquired = res.acquired
        return res

    def __exit__(self, *exc):
        if self._acquired and self._handle is not None:
            release_slot(self._handle, stamp_fn=self._acquire_kwargs["stamp_fn"],
                         run_id=self._acquire_kwargs["run_id"], prd_id=self._acquire_kwargs["prd_id"],
                         iteration_id=self._acquire_kwargs["iteration_id"],
                         owner_repo=self._release_owner_repo)
        return False   # 不吞异常（交上层 dispatch 异常隔离）
