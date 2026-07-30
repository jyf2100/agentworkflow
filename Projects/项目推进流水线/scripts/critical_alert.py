"""critical_alert.py — CRITICAL 告警 durable 化：halt 时落独立 alerts journal（crash 不丢，不受 flag gating）。

single-flight-auto-merge task 4.5（design Risks F5「告警非 durable」）。

**为什么需要它**：post-merge UNKNOWN / revert 非 REVERTED → halt 整仓 + CRITICAL（task 4.2/4.3）。但当前告警仅
log + rec 字段（**非 durable**）——cron crash 在 halt 后、report 段前 → 告警丢失，人工不知 main 可能红、队列已
halt。4.5 把告警落**独立 alerts journal**（append-only，跨 run 存活，**不受 ``journal_shadow`` flag gating**——
CRITICAL 安全事件总 durable，flag 关也落盘），crash 后 report/恢复器仍能读出未确认告警重发。

**与 ``ShadowJournal`` 的区别**（刻意）：``ShadowJournal.emit`` 受 ``journal_shadow`` flag gating（flag 关→
no-op，dispatch 决策零变化，design 决策#8）——适合可观测旁路事件；CRITICAL 告警是**安全事件**，须总 durable
（flag 关也不能丢），故走独立 alerts journal（复用 ``append_event`` 原子追加，与 ``circuit_breaker`` 同模式）。

**未确认语义**（spec「告警未确认 + revert 未确认 → 队列 halt」）：``raise_alert`` 落 ``critical_alert`` 事件 →
``pending_alerts`` 返**未被 ``acknowledge_alert`` 确认**的告警。halt 本身已由 ``slot_halted`` 终态保证（队列 halt
到人工，task 4.2/4.3）；告警 durable 是其**可观测 + 通知**维度（report 段读 pending → SMTP 成段；ack 联动
``resume_slot`` 人工 unblock 留 follow-up，本模块先提供 API）。

**retry**（spec「复用 retry_policy.py」）：告警 durable 落盘即保证 crash 不丢；实际"发送 retry"由 report 段
``_smtp_notify`` 聚合发送承载（fail 退化不阻塞流水线，crash 后下轮 report 重发 = 天然 retry）。retry_policy 是
dev/verify iteration retry 框架，语义错位于告警发送，故不复用其决策（design 注明）。

纯 IO 模块（journal），零 git/SDK；cron 隔离不变。
"""
from __future__ import annotations

from pathlib import Path

from journal import JournalCorruptionError, append_event, read_events
from loop_state import JOURNAL_SCHEMA_VERSION, JournalEvent

_ALERT_DIR = "alerts"
_ALERT_EVENT = "critical_alert"      # CRITICAL 告警事件（halt 时 raise）
_ACK_EVENT = "critical_alert_acked"  # 告警确认事件（人工 ack；联动 resume_slot 留 follow-up）

DEFAULT_SEVERITY = "CRITICAL"


def _safe_name(owner_repo: str) -> str:
    """owner_repo（``owner/repo``，含 ``/``）→ 路径安全文件名段（``/`` → ``__``；同 ``single_flight``/``circuit_breaker``）。"""
    return owner_repo.replace("/", "__")


def alerts_journal_path(state_dir, owner_repo: str) -> Path:
    """alerts journal 路径：``<state_dir>/alerts/<safe>.journal.jsonl``（per-owner_repo，跨 run 存活，不受 flag gating）。"""
    return Path(state_dir) / _ALERT_DIR / f"{_safe_name(owner_repo)}.journal.jsonl"


def raise_alert(state_dir, owner_repo: str, prd_id: str, *, reason: str, stamp_fn,
                severity: str = DEFAULT_SEVERITY) -> str:
    """落一条 CRITICAL 告警到 alerts journal（halt 时调）。返回告警 id（= event_id，ack 锚点）。

    fail-open：写失败不 raise（告警是 halt 的通知维度，halt 安全已由 slot_halted 保证；写失败时调用方 log，
    rec 已标 halted）。durable：不受 ``journal_shadow`` flag gating（CRITICAL 总落盘）。
    """
    ts = stamp_fn()
    alert_id = f"alert-{prd_id}-{ts}"
    ev = JournalEvent(schema_version=JOURNAL_SCHEMA_VERSION, event_id=alert_id, timestamp=ts,
                      iteration_id="", run_id="", prd_id=prd_id, event_type=_ALERT_EVENT,
                      payload={"owner_repo": owner_repo, "reason": reason, "severity": severity})
    append_event(alerts_journal_path(state_dir, owner_repo), ev)
    return alert_id


def acknowledge_alert(state_dir, owner_repo: str, alert_id: str, *, stamp_fn) -> None:
    """确认一条告警（人工 unblock 时调；联动 ``resume_slot`` 留 follow-up）。

    落 ``critical_alert_acked`` 事件（payload 带 ``alert_id``），``pending_alerts`` 见之扣除。fail-open：写失败不
    raise（ack 失败 → 告警仍 pending，report 仍会提，安全侧 fail-closed）。
    """
    ts = stamp_fn()
    ev = JournalEvent(schema_version=JOURNAL_SCHEMA_VERSION, event_id=f"ack-{alert_id}-{ts}", timestamp=ts,
                      iteration_id="", run_id="", prd_id="", event_type=_ACK_EVENT,
                      payload={"alert_id": alert_id})
    append_event(alerts_journal_path(state_dir, owner_repo), ev)


def _read_all(state_dir, owner_repo: str) -> list[JournalEvent]:
    """读 alerts journal 全量事件（fail-open：损坏/缺文件 → 空 list，不阻塞 report）。"""
    try:
        return read_events(alerts_journal_path(state_dir, owner_repo))
    except (JournalCorruptionError, OSError):
        return []


def pending_alerts(state_dir, owner_repo: str) -> list[JournalEvent]:
    """返**未确认**的 CRITICAL 告警（``critical_alert`` 事件扣除已 ``acknowledge_alert`` 的）。

    report 段读之成段（pending = 须人工处理）。fail-open：读失败 → 空 list。
    """
    events = _read_all(state_dir, owner_repo)
    acked = {e.payload.get("alert_id") for e in events if e.event_type == _ACK_EVENT}
    return [e for e in events if e.event_type == _ALERT_EVENT and e.event_id not in acked]
