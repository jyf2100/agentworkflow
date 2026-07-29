"""merge_phase.py — single-flight-auto-merge 自动 merge 阶段的机械判定层（D2 / D3 / D7）。

task 3.x 把 dispatch 的收尾从「兜底开 PR 待 review」升级为「verify 绿 + rebase 干净 → 自动合 main」。
本模块是该升级的**纯机械判定**核心（确定性、零 LLM、零 git）。

**机械活 vs 语义活切分**（ADR-0001 + design D6）：
- 本模块 = 机械判定（给定执行证据 → 判三态）。
- 执行（``git fetch`` / ``git rebase`` / ``git merge --no-ff`` / ``git push`` / revert）经 dev-agent.py 在
  目标仓 worktree 内跑——控制面只发 cmd，**不直接持 git 写句柄**（D6：与 dev/verify 同范式，守 ADR-0001）。
  merge/rebase 是确定性机械 git 操作，走 dev-agent 机械层（``git()`` 进程内 subprocess），不经 SDK loop
  （loop 是给写代码的语义活用的；机械 git 操作用机械层更稳、可测、不引入 agent 非确定性）。

**三态范式**（对齐 ``external_state.ExtState`` / fail-safe-dispatch 不变式——UNKNOWN=阻断，绝不静默当成功）：

  task 3.1 rebase 判定：
    CLEAN      rebase 在当前 main 上干净（全正向证据 + 无冲突）→ 触发自动 merge
    CONFLICT   rebase 在 fetched main 上**明确报告冲突**（fetch 成功 + 非超时 + 冲突标记存在）→ triage
    UNKNOWN    状态不明（fetch 失败 / exit≠0 / 工作树脏 / 超时 / 缺证 / 超时残留冲突标记）→ triage，**不当代干净**

**CLEAN 须正向证据**（spec single-flight-auto-merge「Three-state rebase safety」）：CLEAN 仅在全部正向证据齐时
断言——fetch 成功 + rebase exit0 + 干净工作树 + 无冲突标记 + 未超时；缺任一即 UNKNOWN（spec scenario
「No positive evidence is not clean」：rebase 被 timeout 杀留半完成状态、或 fetch 失败，即使无冲突标记也是 UNKNOWN）。

**CONFLICT 须在 fetched main 上明确报告**：fetch 失败（base 过时不可信）或超时（半完成残留）下的冲突标记，
不算「rebase 明确报告冲突」→ UNKNOWN（fail-safe：宁可 triage 不强合）。

后续 task 3.2（merge 本体：--no-ff + ff-only push + 记 merge_commit）/ 3.3（triage 路由）/ 4.x（post-merge
三态 + auto-revert）在本模块续加判定函数 + 接 dev-agent merge 执行 + dispatch_one 接线。

纯模块（dataclass + 枚举 + 纯函数），零 SDK 零 git；时间/状态由调用方注入（保测试确定性 + cron 隔离）。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import ClassVar


class RebaseOutcome(str, Enum):
    """rebase 三态（str 子类化便于 JSON 序列化进 state 记录，同 ``SlotState`` / ``ExtState``）。

    ``UNKNOWN`` 是 fail-safe 信号：dispatch 见之即转 triage（rebase_unknown），**绝不当代 CLEAN 强合**
    （fail-safe-dispatch 不变式）。
    """

    CLEAN = "clean"          # rebase 干净（全正向证据 + 无冲突）→ 触发自动 merge
    CONFLICT = "conflict"    # rebase 明确报告冲突（fetched main + 非超时 + 冲突标记）→ triage
    UNKNOWN = "unknown"      # 状态不明（fetch 失败/exit≠0/工作树脏/超时/缺证）→ triage，不当代干净


@dataclass(frozen=True)
class RebaseEvidence:
    """rebase 执行证据（dev-agent merge 执行层收集后交 ``classify_rebase`` 判定）。

    各字段均为**正向证据**语义：``True``/0 表示该证据存在且良好。``classify_rebase`` 据正向证据聚合判三态
    （CLEAN 须全齐；任一缺/坏 → UNKNOWN）。``raw`` 留原始诊断（脱敏 stderr 片段等）供审计，不参与判定。
    """

    __test__: ClassVar[bool] = False
    fetch_ok: bool                 # ``git fetch origin <main>`` 成功？（失败 = base 过时，rebase 无意义）
    rebase_rc: int | None          # ``git rebase`` exit code（None = 未跑/超时被杀未取到 rc）
    worktree_clean: bool           # ``git status --porcelain`` 为空？（rebase 干净后工作树须干净）
    conflict_files: int            # ``git diff --name-only --diff-filter=U | wc -l``（>0 = 有冲突标记）
    timed_out: bool                # rebase 被 wall-clock timeout 杀？（D10：rebase ~120s 上界）
    raw: dict | None = None        # 原始证据快照（诊断/审计，不参与判定逻辑）


def classify_rebase(ev: RebaseEvidence) -> RebaseOutcome:
    """据 rebase 执行证据判三态（**纯机械判定**，spec「Three-state rebase safety before merge」）。

    判定顺序（fail-safe：最危险的「误判 CLEAN 强合」须最难达成）：
      1. ``CONFLICT`` —— rebase 在 fetched main 上**明确报告冲突**：``fetch_ok`` + 非 ``timed_out`` +
         ``conflict_files > 0``。fetch 失败/超时下的冲突标记不可信（base 过时/半完成残留）→ 不算明确冲突。
      2. ``CLEAN`` —— 全正向证据齐：``fetch_ok`` + ``rebase_rc == 0`` + ``worktree_clean`` +
         ``conflict_files == 0`` + 非 ``timed_out``。缺任一即不 CLEAN。
      3. ``UNKNOWN`` —— 其余全部（fetch 失败 / exit≠0 / 工作树脏 / 超时 / 缺证 / 超时残留标记）。

    Args:
        ev: dev-agent merge 执行层收集的 rebase 证据（``RebaseEvidence``）。
    """
    # 1. CONFLICT：明确冲突（fetched main + 非超时 + 冲突标记）。CONFLICT 优先于 CLEAN（冲突标记存在时
    #    worktree 必脏、rc 必非 0，但这些是冲突的伴生表现，不算缺证）。
    if ev.fetch_ok and not ev.timed_out and ev.conflict_files > 0:
        return RebaseOutcome.CONFLICT
    # 2. CLEAN：全正向证据齐（spec「asserted only on positive evidence」）。
    if ev.fetch_ok and ev.rebase_rc == 0 and ev.worktree_clean \
            and ev.conflict_files == 0 and not ev.timed_out:
        return RebaseOutcome.CLEAN
    # 3. 其余 = UNKNOWN（fail-safe：不当代干净，转 triage rebase_unknown）。
    return RebaseOutcome.UNKNOWN


# ─── task 3.2：merge phase 整体结果 + 控制面↔dev-agent 契约 ──────────────────────
# dev-agent merge phase（机械执行 fetch→rebase→merge→push）吐末行 JSON，控制面 parse 成 MergeResult。
# schema（dev-agent run_merge_phase print）：
#   {"phase":"merge", "rebase_outcome":"clean"|"conflict"|"unknown",
#    "merge_commit":"<sha>"|null, "push_failed":bool, "rebase":{...RebaseEvidence...}, "error":...|null}


@dataclass(frozen=True)
class MergeResult:
    """merge phase 整体结果（控制面 parse dev-agent 返回所得；驱动 dispatch 收尾路由）。

    ``merged`` = rebase CLEAN + push 成功 + merge_commit 落地（真合 main）；否则据 ``triage_reason`` 进 triage。
    push 失败时 ``rebase_outcome`` 仍可能 CLEAN（rebase 干净但 push 被 reject）——由 ``push_failed`` 独立标，
    spec「Push of the merge commit fails → UNKNOWN, main unchanged, slot released」。
    """

    __test__: ClassVar[bool] = False
    rebase_outcome: RebaseOutcome
    merge_commit: str | None
    push_failed: bool
    evidence: RebaseEvidence
    error: str | None = None

    @property
    def merged(self) -> bool:
        """真合 main：rebase CLEAN + 未 push 失败 + merge_commit 落地。"""
        return (self.rebase_outcome is RebaseOutcome.CLEAN
                and not self.push_failed and bool(self.merge_commit))

    @property
    def triage_reason(self) -> str | None:
        """未合 main 时的 triage 枚举原因（None=已合）。对齐 spec 固定枚举。

        CONFLICT → ``rebase_conflict``；push reject → ``push_failed``；其余 UNKNOWN → ``rebase_unknown``。
        """
        if self.merged:
            return None
        if self.rebase_outcome is RebaseOutcome.CONFLICT:
            return "rebase_conflict"
        if self.push_failed:
            return "push_failed"
        return "rebase_unknown"


def parse_merge_result(payload) -> MergeResult:
    """解析 dev-agent merge phase 末行 JSON 为 ``MergeResult``（**fail-safe**：坏/缺字段 → UNKNOWN）。

    非 dict / 缺 rebase 证据 / 坏 outcome 值 → 一律降级 UNKNOWN（绝不误判 merged——同 fail-safe-dispatch
    不变式：状态不明即阻断，不当代成功）。``rebase_rc`` 允许 None（超时未取到）。
    """
    if not isinstance(payload, dict):
        return MergeResult(RebaseOutcome.UNKNOWN, None, False,
                           RebaseEvidence(False, None, False, 0, False),
                           error="非 dict payload")
    rb = payload.get("rebase") or {}
    try:
        evidence = RebaseEvidence(
            fetch_ok=bool(rb.get("fetch_ok", False)),
            rebase_rc=rb.get("rebase_rc"),
            worktree_clean=bool(rb.get("worktree_clean", False)),
            conflict_files=int(rb.get("conflict_files", 0) or 0),
            timed_out=bool(rb.get("timed_out", False)),
        )
    except (TypeError, ValueError) as ex:
        evidence = RebaseEvidence(False, None, False, 0, False)
        outcome, err = RebaseOutcome.UNKNOWN, f"rebase 证据解析失败: {ex}"
    else:
        try:
            outcome = RebaseOutcome(payload.get("rebase_outcome", "unknown"))
        except ValueError:
            outcome, err = RebaseOutcome.UNKNOWN, f"坏 rebase_outcome: {payload.get('rebase_outcome')!r}"
        else:
            err = payload.get("error")
    merge_commit = payload.get("merge_commit")
    if not isinstance(merge_commit, str):
        merge_commit = None
    return MergeResult(outcome, merge_commit, bool(payload.get("push_failed", False)),
                       evidence, error=err)


def build_merge_cmd(*, python: str, dev_agent_py, branch: str, main_ref: str,
                    prd_id: str, state_dir: str | None = None) -> list[str]:
    """构造 ``dev-agent --phase merge`` 命令（控制面发，dev-agent 在目标仓执行 rebase→merge→push）。

    守 D6/ADR-0001：控制面只发 cmd，**不直接持 git 写句柄**；dev-agent 在目标仓 worktree 经机械层
    （``git()`` 进程内 subprocess）执行 merge 序列。``state_dir`` 透传（merge 事件落控制面 state，task 6.x）。
    """
    cmd = [python, str(dev_agent_py), "--phase", "merge",
           "--branch", branch, "--main", main_ref, "--prd-id", prd_id]
    if state_dir:
        cmd += ["--state-dir", state_dir]
    return cmd
