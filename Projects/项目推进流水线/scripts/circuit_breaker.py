"""circuit_breaker.py — revert 循环熔断：同幂等键 PRD 被 post_merge_red_reverted 后 cooldown 窗口内禁再 auto-merge（D11）。

single-flight-auto-merge task 4.4。

**为什么需要它**（design D11 / spec single-flight-auto-merge「Revert loop circuit breaker」）：post-merge
测试红 → auto-revert 成功 → PRD 进 triage。但该 PRD 下轮 cron 可能再次被 radar 命中→prd→dev 绿→verify 绿
→rebase CLEAN→**又合 main 又红又 revert**——「branch 绿但 main 红」的 PRD 夜夜复发，无限循环。熔断在
re-admission 时查 cooldown 窗口：同幂等键（prd_id）刚被 revert 过 → 禁 auto-merge → 强制 triage 等人工。

**幂等键**：``circuit_key``（``ids.prd_id(stable_slug, content_hash)``，slug-based content-addressed）。
跨 cron 稳定——``stable_slug``（frontmatter 语义 slug）+ 内容 digest 都不含 cron stamp，同 PRD 跨 cron 同键
（spec「across cron rounds」）；PRD 改了（验收标准/信号变）→ 新 digest → 新键 → 视作新 PRD 放行重试（合理：
内容变了可能已修）。record/check 同用 ``Coordinator.circuit_key``，自洽。

**task 4.4 fix（canary 判据 c 2026-07-30 暴露）**：旧键用 path-based ``prd_id``（``ids.prd_id(prd_path,
content_hash)``），但 ``prd_path`` = ``{stamp}_{slug}.md`` 含 cron stamp → 跨 cron stamp 变 → 键变 →
``is_in_cooldown`` 跨 cron 不命中 → 本模块要防的「branch 绿 main 红 PRD 夜夜复发」恰好防不住。改 slug-based
``circuit_key``（``prd_id(stable_slug, digest)``，两因子都不含 stamp）根治。

**fail-open（额外保护层，非 fail-safe 核心）**：熔断是「额外阻止」型护栏，不是「破坏性副作用准入门」。
rebase/merge/revert 那种破坏性操作无正向证据 → UNKNOWN → block（fail-safe，绝不动 main）；熔断无正向匹配
证据 → **放行**（让正常 triage/revert 流程走）。故 journal 损坏/读失败 → ``False``（不在冷却）。窗口内确有
匹配的 revert 记录 → ``True``（block）。durable 告警化（损坏提醒）留 task 4.5。

**数据**：per-owner_repo cooldown journal（``state_dir/cooldown/<safe>.journal.jsonl``，append-only 跨 run
存活），复用 ``journal.append_event`` 原子追加 + ``JournalEvent`` schema（event_type=``revert_recorded``）。

**时间注入**：``now_fn``（datetime，窗口算术）+ ``stamp_fn``（ISO 字符串，事件 timestamp）由调用方注入——
本模块不触系统时间（同 ``single_flight`` 约定，保测试确定性 + cron 隔离）。

纯 IO 模块（文件系统 + journal），零 git/SDK；cron 隔离不变。
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from journal import JournalCorruptionError, append_event, read_events
from loop_state import JOURNAL_SCHEMA_VERSION, JournalEvent

# cooldown 默认窗口（秒）：7 天（spec D11 草案「7 天 / N 轮」）。调用方可按 profile 覆盖。
DEFAULT_COOLDOWN_WINDOW: int = 7 * 24 * 3600

_COOLDOWN_DIR = "cooldown"
_COOLDOWN_EVENT = "revert_recorded"   # post-merge red → auto-revert REVERTED 记录事件类型


def _safe_name(owner_repo: str) -> str:
    """owner_repo（``owner/repo``，含 ``/``）→ 路径安全文件名段（``/`` → ``__``；同 ``single_flight``）。"""
    return owner_repo.replace("/", "__")


def cooldown_journal_path(state_dir, owner_repo: str) -> Path:
    """cooldown journal 路径：``<state_dir>/cooldown/<safe>.journal.jsonl``（per-owner_repo，跨 run 存活）。"""
    return Path(state_dir) / _COOLDOWN_DIR / f"{_safe_name(owner_repo)}.journal.jsonl"


def _parse_iso(s: str) -> datetime:
    """ISO 字符串 → aware datetime（naive 假设 UTC；``Z`` 后缀兼容）。窗口算术用。"""
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def record_revert(state_dir, owner_repo: str, circuit_key: str, *, stamp_fn) -> None:
    """记一条 revert 事件到 cooldown journal（post-merge red → auto-revert REVERTED 后调用）。

    ``circuit_key``：跨 cron 稳定的熔断键（``ids.prd_id(stable_slug, content_hash)``，slug-based）——由调用方
    （``run_daily`` 经 ``Coordinator.circuit_key``）传入。fail-open：写失败不 raise（熔断是额外护栏，不应阻断
    已成功的 revert + triage 流程；durable 告警化留 task 4.5）。复用 ``append_event`` 原子追加（crash 安全）。
    """
    ts = stamp_fn()
    ev = JournalEvent(schema_version=JOURNAL_SCHEMA_VERSION, event_id=f"cb-{circuit_key}-{ts}",
                      timestamp=ts, iteration_id="", run_id="", prd_id=circuit_key,
                      event_type=_COOLDOWN_EVENT, payload={"owner_repo": owner_repo})
    try:
        append_event(cooldown_journal_path(state_dir, owner_repo), ev)
    except Exception:            # fail-open：写失败不 raise（熔断是额外护栏，不应阻断已成功的 revert+triage）
        pass


def is_in_cooldown(state_dir, owner_repo: str, circuit_key: str, *, now_fn,
                   window_seconds: int = DEFAULT_COOLDOWN_WINDOW) -> bool:
    """该 PRD 是否在 cooldown 窗口内（窗口内有匹配 ``circuit_key`` 的 revert 记录 → True，禁 auto-merge）。

    ``circuit_key``：跨 cron 稳定熔断键（slug-based，``ids.prd_id(stable_slug, content_hash)``）——与
    ``record_revert`` 写入同键，跨 cron re-admission 命中（防 "branch 绿 main 红 PRD 夜夜复发"）。
    fail-open：journal 损坏/读失败/缺文件 → ``False``（额外保护层无正向证据则放行，不 block）。
    spec「Reverted PRD re-admitted inside cooldown」→ True；「after cooldown」→ False。
    """
    jp = cooldown_journal_path(state_dir, owner_repo)
    try:
        events = read_events(jp)
    except (JournalCorruptionError, OSError):
        return False                      # fail-open：读不到 → 不 block（让正常流程走）
    now = now_fn()
    for ev in events:
        if ev.event_type != _COOLDOWN_EVENT or ev.prd_id != circuit_key:
            continue
        try:
            reverted_at = _parse_iso(ev.timestamp)
        except ValueError:                # 损坏时间戳 → 跳过这条（不 crash）
            continue
        if (now - reverted_at).total_seconds() <= window_seconds:
            return True
    return False
