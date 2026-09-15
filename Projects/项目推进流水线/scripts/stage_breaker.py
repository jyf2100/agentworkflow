"""stage_breaker.py — 段级熔断器：同段 persona 连续失败 + 当日累计成本双熔丝（pa-hardening P0-1）。

**为什么需要它**（故障① LLM API 5h 限流的代码级入口）：run_persona 的超时/非零退出分支即时
raise，无重试无熔断——限流期整链反复撞墙直到崩在中途段，遗留停机现场一夜。P1-1 的退避重试只熬
**分钟级**抖动；小时级限流由本熔断器止损（重试与熔断职责分离：重试耗尽 → record_failure 交裁决）。

**双熔丝**（对齐 CH07「预算熔丝 + 轮次熔丝」形状，参数换批处理语义）：
    熔丝1 ``PA_BREAKER_N``（默认 3）：同段 persona **连续**失败达阈值 → trip（成功即重置计数）；
    熔丝2 ``PA_BREAKER_COST_CAP``（默认 unset=不启用）：当日 persona 成本累计 ≥ cap（美元）→ trip。
    cap 不拍脑袋：灰度期 ``PA_BREAKER_OBSERVE=1`` 观测模式（fuse 命中只 log + 落 journal，不真熔）
    + cap 放宽到观测值 3 倍，从生产 trace 反推后再收紧。

**与既有 ``circuit_breaker.py`` 不重叠**（文件名刻意区分）：那边熔「合入后回退」循环（revert
cooldown，per-PRD 幂等键，merge 场景）；本模块熔「段级 persona 连续失败 + 日成本」（生产场景）。
键空间、journal 文件、event_type、调用点均不同。

**状态与 flag（默认 off = baseline 零行为变化）**：
    * ``PA_BREAKER`` —— 总门（off = record/check/add_cost 全 no-op，零读写零开销）；
    * ``PA_BREAKER_OBSERVE`` —— 观测模式（fuse 命中降级为 log + journal 事件，不写 tripped 不抛）；
    * ``PA_BREAKER_N`` / ``PA_BREAKER_COST_CAP`` —— 两熔丝阈值。
    状态文件 ``<state_dir>/breaker_<stamp>.json``：按 stamp 命名 → 次日新 stamp 自然重置（批处理
    场景**不做自动半开**，当日恢复走人工）；跨日熔断史走 journal（跨 stamp 存活）。

**熔断动作**：``trip()`` 写 ``tripped=true`` + journal 落 ``stage_breaker_tripped`` 事件 + 抛
``CircuitBreakerTripped``（对齐 CH07 设计决策 3：熔断用异常而非返回值——携带 reason + 状态文件
路径）。run_daily 捕获后写 checkpoint ``status=halted`` + durable 告警 + ``exit 3``（区别于普通
失败 exit 1，监控可区分）。durable 告警复用 ``critical_alert`` 通道由 run_daily 侧注入（本模块
不 import critical_alert，保持纯 journal 依赖——同 ``circuit_breaker`` 形态）。

**fail-open 边界**：熔断是「额外阻止」型护栏（非破坏性副作用准入门）——状态文件损坏/读写失败
→ 不熔断但 stderr 告警（block 需正向证据，对齐 circuit_breaker 的护栏语义）；journal 写失败 →
不阻断 trip 本身（内存判定 + 状态文件为准，journal 是跨日观测面）。

**时间注入**：``now_fn``（ISO 字符串）由调用方注入，本模块不触系统时间（测试确定性）。成本记账
（``add_cost``）与 checkpoint 的 cost_usd 是**双账**（各自 flag 独立：PA_BREAKER on + PA_CHECKPOINT
off 时熔丝2 仍须工作，故不读 checkpoint）——同源喂入（run_persona_with_backoff），量级每天几十条
无漂移风险，报告以 checkpoint 账为准。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from journal import JournalCorruptionError, append_event, read_events
from loop_state import JOURNAL_SCHEMA_VERSION, JournalEvent

_TRUTHY = frozenset({"1", "true", "yes", "on"})

BREAKER_FLAG = "PA_BREAKER"
OBSERVE_FLAG = "PA_BREAKER_OBSERVE"
N_ENV = "PA_BREAKER_N"
COST_CAP_ENV = "PA_BREAKER_COST_CAP"

DEFAULT_BREAKER_N = 3            # 熔丝1 默认阈值（CH07 团队复用建议 2：生产 trace 反推前先用保守默认）
DEFAULT_TRIP_WINDOW_DAYS = 7     # consecutive_trip_days 观测窗口

_TRIP_EVENT = "stage_breaker_tripped"      # trip（或 observe 降级）事件——跨 stamp 存活，跨日熔断史真源
_FAIL_EVENT = "stage_persona_failed"       # persona 失败记账事件（fuse1 计数的可审计副本）


class CircuitBreakerTripped(RuntimeError):
    """熔断终态异常（RuntimeError 子类：run_daily 既有 ``except RuntimeError`` 兜底仍捕获，
    专门分支先于它匹配做 halted+exit 3 处理）。携带 reason + breaker 状态文件路径。"""

    def __init__(self, reason: str, breaker_path: str | None = None):
        self.reason = reason
        self.breaker_path = breaker_path
        super().__init__(reason)


def _warn(msg: str) -> None:
    print(f"[stage_breaker] ⚠ {msg}", file=sys.stderr)


def _env(env: dict[str, str] | None):
    return os.environ if env is None else env


def breaker_path(state_dir, stamp: str) -> Path:
    """熔断状态文件：``<state_dir>/breaker_<stamp>.json``（按 stamp 命名，次日自然重置）。"""
    return Path(state_dir) / f"breaker_{stamp}.json"


def breaker_journal_path(state_dir) -> Path:
    """跨 stamp 熔断史 journal：``<state_dir>/breaker.journal.jsonl``（append-only，consecutive_trip_days 真源）。"""
    return Path(state_dir) / "breaker.journal.jsonl"


def enabled(env: dict[str, str] | None = None) -> bool:
    """PA_BREAKER 总门（off = 本模块全部操作 no-op）。"""
    v = _env(env).get(BREAKER_FLAG)
    return isinstance(v, str) and v.strip().lower() in _TRUTHY


def _observe(env: dict[str, str] | None = None) -> bool:
    v = _env(env).get(OBSERVE_FLAG)
    return isinstance(v, str) and v.strip().lower() in _TRUTHY


def _breaker_n(env: dict[str, str] | None = None) -> int:
    raw = _env(env).get(N_ENV, "").strip()
    try:
        n = int(raw)
    except ValueError:
        return DEFAULT_BREAKER_N
    return n if n >= 1 else DEFAULT_BREAKER_N


def _cost_cap(env: dict[str, str] | None = None) -> float | None:
    """成本熔丝阈值（美元）。unset/非法 → None = 熔丝2 不启用（灰度期先观测后设值）。"""
    raw = _env(env).get(COST_CAP_ENV, "").strip()
    if not raw:
        return None
    try:
        cap = float(raw)
    except ValueError:
        _warn(f"{COST_CAP_ENV}={raw!r} 非法（须数字，美元）→ 成本熔丝不启用")
        return None
    return cap if cap > 0 else None


def _empty_state() -> dict:
    return {"consecutive": {}, "cost_usd": 0.0, "tripped": False, "tripped_reason": None, "updated": None}


def load_breaker(state_dir, stamp: str) -> dict | None:
    """读熔断状态。缺文件 → None（report 渲染「无熔断」）；损坏 → None + 告警（fail-open 不熔断）。"""
    p = breaker_path(state_dir, stamp)
    if not p.is_file():
        return None
    try:
        st = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
        _warn(f"breaker 状态损坏（{p.name}: {e}）→ fail-open 视为未熔断（护栏需正向证据）")
        return None
    if not isinstance(st, dict):
        _warn(f"breaker 状态结构非法（{p.name}）→ fail-open 视为未熔断")
        return None
    st.setdefault("consecutive", {})
    st.setdefault("cost_usd", 0.0)
    st.setdefault("tripped", False)
    return st


def _save(state_dir, stamp: str, st: dict, now_fn) -> bool:
    """原子写状态文件（tmp + replace）。失败 → 告警返 False（fail-open：写不进不熔断）。"""
    st["updated"] = now_fn()
    try:
        p = breaker_path(state_dir, stamp)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, p)
    except OSError as e:
        _warn(f"breaker 状态写失败（{e}）→ fail-open（本次失败不计入持久计数）")
        return False
    return True


def _journal_append(state_dir, stamp: str, event_type: str, payload: dict, now_fn) -> None:
    """熔断史落 journal（append_event 原子追加 + fsync，crash 安全）。fail-open：写失败仅告警。"""
    ts = now_fn()
    ev = JournalEvent(schema_version=JOURNAL_SCHEMA_VERSION, event_id=f"sb-{event_type}-{stamp}-{ts}",
                      timestamp=ts, iteration_id="", run_id=stamp, prd_id=f"{stamp}",
                      event_type=event_type, payload={"stamp": stamp, **payload})
    try:
        append_event(breaker_journal_path(state_dir), ev)
    except Exception as e:   # journal 是跨日观测面，非熔断判定的真源（状态文件才是）→ 写失败不阻断
        _warn(f"breaker journal 追加失败（{e}）→ 跨日观测缺一条（不影响当日熔断判定）")


def _parse_iso(s: str) -> datetime:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def check(state_dir, stamp: str, stage: str = "", env: dict[str, str] | None = None) -> None:
    """段入口 / persona 调用前检查：已 tripped → 抛 ``CircuitBreakerTripped``（防捕获后复用继续烧钱）。

    flag off / 未 tripped / 状态损坏 → 静默返回（fail-open）。
    """
    if not enabled(env):
        return
    st = load_breaker(state_dir, stamp)
    if st is None or not st.get("tripped"):
        return
    reason = st.get("tripped_reason") or "（无原因记录）"
    where = f"@{stage} " if stage else ""
    raise CircuitBreakerTripped(f"已熔断（{where}当日 stamp={stamp}）: {reason}",
                                breaker_path=str(breaker_path(state_dir, stamp)))


def record_success(state_dir, stamp: str, stage: str, *, now_fn, env: dict[str, str] | None = None) -> None:
    """persona 调用成功：清零该段连续失败计数（熔丝1 的「连续」语义）。flag off → no-op。"""
    if not enabled(env):
        return
    st = load_breaker(state_dir, stamp) or _empty_state()
    if st.get("consecutive", {}).get(stage):
        st.setdefault("consecutive", {})[stage] = 0
        _save(state_dir, stamp, st, now_fn)


def _trip(state_dir, stamp: str, reason: str, *, now_fn, env: dict[str, str] | None) -> None:
    """熔断动作：写 tripped + journal 事件；观测模式降级为 log（不写 tripped 不抛——灰度只观测）。

    抛 ``CircuitBreakerTripped`` 交调用链顶层（run_daily._run_pipeline）做 halted + exit 3。
    """
    observe = _observe(env)
    _journal_append(state_dir, stamp, _TRIP_EVENT, {"reason": reason, "observe": observe}, now_fn)
    if observe:
        _warn(f"观测模式（{OBSERVE_FLAG}=1）：fuse 命中不真熔——{reason}")
        return
    st = load_breaker(state_dir, stamp) or _empty_state()
    st["tripped"] = True
    st["tripped_reason"] = reason
    if not _save(state_dir, stamp, st, now_fn):
        return   # 状态写失败（fail-open）→ 不抛（block 需正向持久证据）
    raise CircuitBreakerTripped(reason, breaker_path=str(breaker_path(state_dir, stamp)))


def record_failure(state_dir, stamp: str, stage: str, label: str, err_kind: str, *,
                   now_fn, env: dict[str, str] | None = None) -> None:
    """persona 调用失败记账（run_persona_with_backoff 的 except 臂统一调）：

    熔丝1——``consecutive[stage] += 1`` 达 ``PA_BREAKER_N`` → trip；
    熔丝2——顺带复查当日 ``cost_usd ≥ PA_BREAKER_COST_CAP``（成本主记账在 add_cost，此处兜底
    「只失败不花钱」路径的 cap 检查时机）。已 tripped → 静默返回（trip 只抛一次，防嵌套重复抛）。
    """
    if not enabled(env):
        return
    st = load_breaker(state_dir, stamp) or _empty_state()
    if st.get("tripped"):
        return
    consecutive = st.setdefault("consecutive", {})
    consecutive[stage] = int(consecutive.get(stage, 0)) + 1
    if not _save(state_dir, stamp, st, now_fn):
        return
    _journal_append(state_dir, stamp, _FAIL_EVENT,
                    {"stage": stage, "label": label, "err_kind": err_kind, "consecutive": consecutive[stage]},
                    now_fn)
    n = _breaker_n(env)
    if consecutive[stage] >= n:
        _trip(state_dir, stamp, f"stage={stage} 连续 {consecutive[stage]} 次失败（阈值 {n}）",
              now_fn=now_fn, env=env)
        return
    cap = _cost_cap(env)
    if cap is not None and float(st.get("cost_usd", 0.0)) >= cap:
        _trip(state_dir, stamp, f"当日成本 ${float(st.get('cost_usd', 0.0)):.4f} ≥ cap ${cap}",
              now_fn=now_fn, env=env)


def add_cost(state_dir, stamp: str, amount, *, now_fn, env: dict[str, str] | None = None) -> None:
    """P2-1→熔丝2 数据源：persona 成功调用后累加当日成本（美元实数，来自 CLI 信封 total_cost_usd）。

    None/非数/负数跳过（观测面非准入门）；累加后 ``≥ PA_BREAKER_COST_CAP`` → trip。与
    run_checkpoint.add_cost 双账（flag 独立性见模块 docstring）。
    """
    if not enabled(env):
        return
    if not isinstance(amount, (int, float)) or isinstance(amount, bool) or amount < 0:
        return
    st = load_breaker(state_dir, stamp) or _empty_state()
    if st.get("tripped"):
        return
    st["cost_usd"] = round(float(st.get("cost_usd", 0.0)) + float(amount), 6)
    if not _save(state_dir, stamp, st, now_fn):
        return
    cap = _cost_cap(env)
    if cap is not None and st["cost_usd"] >= cap:
        _trip(state_dir, stamp, f"当日成本 ${st['cost_usd']:.4f} ≥ cap ${cap}", now_fn=now_fn, env=env)


def is_tripped(state_dir, stamp: str, env: dict[str, str] | None = None) -> bool:
    """当前 stamp 是否已熔断（测试/运维查询面；flag off 恒 False）。"""
    if not enabled(env):
        return False
    st = load_breaker(state_dir, stamp)
    return bool(st and st.get("tripped"))


def consecutive_trip_days(state_dir, *, now_fn, window_days: int = DEFAULT_TRIP_WINDOW_DAYS) -> int:
    """近 ``window_days`` 天内出现 ``stage_breaker_tripped`` 事件的不同 stamp 数（report「连续熔断」行）。

    fail-open：journal 损坏/缺文件 → 0（观测面，不 raise）。观测模式事件也计入（灰度期同样要看趋势）。
    """
    try:
        events = read_events(breaker_journal_path(state_dir))
    except (JournalCorruptionError, OSError):
        return 0
    try:
        now = _parse_iso(now_fn())
    except ValueError:
        return 0
    cutoff = now.timestamp() - window_days * 86400
    stamps: set[str] = set()
    for ev in events:
        if ev.event_type != _TRIP_EVENT:
            continue
        try:
            ts = _parse_iso(ev.timestamp).timestamp()
        except (ValueError, TypeError):
            continue
        if ts >= cutoff:
            s = (ev.payload or {}).get("stamp") or ev.run_id
            if s:
                stamps.add(str(s))
    return len(stamps)
