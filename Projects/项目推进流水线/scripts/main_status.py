"""main_status.py — 「main 是否已过 post-merge 验证」可查询状态 + 「main may be transiently red」契约（D10/F8）。

single-flight-auto-merge task 4.6（design Risks F8「main 瞬态红新契约」+ D10 wall-clock）。

**为什么需要它**：auto-merge 先 push main 再跑 post-merge 测试 → main 在 [push, post-merge verdict] 窗口**必然
可能红**（push 了未验证的 merge commit）。须对外暴露此契约（CD/分支保护/下游须容忍）+ 给出**最大红窗上界**
（= post-merge test timeout，D10）+ 提供**可查询的「main 是否已过 post-merge 验证」状态**（下游/人工可查 main
当前 HEAD 是否经过 post-merge 绿验证，而非盲猜）。

**最大红窗上界**（``MAX_MAIN_RED_WINDOW_SECONDS`` = post-merge test timeout 1800s）：push 后到 post-merge
判决的最长窗口。窗口内 main 可能红（merge commit 含未验证代码）；窗口后 main 必为已判决态（PASS 保留绿 /
FAIL 已 revert 回绿 / UNKNOWN halt）。CD 须容忍此窗口，或绑定 post-merge verdict 而非 raw push。

**可查询状态**：per-owner_repo main_status journal（``state_dir/main_status/<safe>.journal.jsonl``，跨 run 存活，
**不受 ``journal_shadow`` flag gating**——与 ``critical_alert`` 同理，状态查询须总可用），post-merge 判决后记
``main_post_merge_verified`` 事件（main_ref + merge_commit + verdict）。``main_post_merge_status`` 返最近一条 →
下游据此判 main 验证态（PASS = 当前 main 已过验证；FAIL/UNKNOWN = 曾红/不确定，已 revert 或 halt）。

纯 IO 模块（journal），零 git/SDK；cron 隔离不变。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from journal import JournalCorruptionError, append_event, read_events
from loop_state import JOURNAL_SCHEMA_VERSION, JournalEvent

# 最大红窗上界（秒）：push main 后到 post-merge 判决的最长窗口（= dev-agent POST_MERGE_TEST_TIMEOUT 1800s，D10）。
# 契约「main may be transiently red」：main 在 [push, verdict] 内可能红（merge commit 未验证）；窗口后必为已判决
# 态。CD/分支保护须容忍此窗口，或绑定 post-merge verdict（而非 raw push 触发）。
MAX_MAIN_RED_WINDOW_SECONDS: int = 1800

_MAIN_STATUS_DIR = "main_status"
_MAIN_VERIFIED_EVENT = "main_post_merge_verified"


def _safe_name(owner_repo: str) -> str:
    """owner_repo（``owner/repo``，含 ``/``）→ 路径安全文件名段（同 ``single_flight``/``circuit_breaker``/``critical_alert``）。"""
    return owner_repo.replace("/", "__")


def main_status_journal_path(state_dir, owner_repo: str) -> Path:
    """main_status journal 路径：``<state_dir>/main_status/<safe>.journal.jsonl``（per-owner_repo，跨 run，不受 flag gating）。"""
    return Path(state_dir) / _MAIN_STATUS_DIR / f"{_safe_name(owner_repo)}.journal.jsonl"


def record_main_verified(state_dir, owner_repo: str, *, main_ref: str, merge_commit: str, verdict: str,
                         prd_id: str, stamp_fn) -> None:
    """post-merge 判决后记 main 验证状态（PASS/FAIL/UNKNOWN 都记，verdict 区分）。fail-open：写失败不 raise
    （状态查询是可观测维度；判决本身已落 dispatch rec + halt/alert 已保证安全）。"""
    ts = stamp_fn()
    ev = JournalEvent(schema_version=JOURNAL_SCHEMA_VERSION, event_id=f"mvs-{prd_id}-{ts}", timestamp=ts,
                      iteration_id="", run_id="", prd_id=prd_id, event_type=_MAIN_VERIFIED_EVENT,
                      payload={"owner_repo": owner_repo, "main_ref": main_ref, "merge_commit": merge_commit,
                               "verdict": verdict})
    try:
        append_event(main_status_journal_path(state_dir, owner_repo), ev)
    except Exception:            # fail-open：写失败不 raise（状态查询是可观测维度，丢不致命；判决已落 rec）
        pass


@dataclass(frozen=True)
class MainPostMergeStatus:
    """``main_post_merge_status`` 返回的最近一次 post-merge 验证状态（无记录/fail-open → None）。"""

    __test__ = False
    main_ref: str          # 判决针对的 main ref（profile default_branch，如 main）
    merge_commit: str      # 本次 auto-merge 产出的 merge commit sha
    verdict: str           # PASS / FAIL / UNKNOWN（merge_phase.PostMergeVerdict.value）
    verified_at: str       # 判决时间（ISO）


def main_post_merge_status(state_dir, owner_repo: str) -> MainPostMergeStatus | None:
    """返最近一次 post-merge 验证状态（无记录/读失败 → None，fail-open）。

    下游据此判「main 是否已过 post-merge 验证」：verdict=PASS → 当前 main 已过验证；FAIL/UNKNOWN → 曾红/不确定
    （FAIL 已 revert 回绿，UNKNOWN 已 halt）。
    """
    try:
        events = read_events(main_status_journal_path(state_dir, owner_repo))
    except (JournalCorruptionError, OSError):
        return None                       # fail-open：读不到 → None（下游判 None = 未验证/未知，不当代绿）
    recent = [e for e in events if e.event_type == _MAIN_VERIFIED_EVENT]
    if not recent:
        return None
    e = recent[-1]
    return MainPostMergeStatus(main_ref=e.payload.get("main_ref", ""), merge_commit=e.payload.get("merge_commit", ""),
                               verdict=e.payload.get("verdict", ""), verified_at=e.timestamp)
