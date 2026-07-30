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


def build_classify_cmd(*, python: str, dev_agent_py, branch: str, main_ref: str,
                       prd_id: str, state_dir: str | None = None) -> list[str]:
    """构造 ``dev-agent --phase merge --classify-only`` 命令（shadow 模式：只判 rebase 三态，**不 merge/push**）。

    task 7.1a shadow parity：``single_flight_serial_shadow=on``（``auto_merge=off``）时，控制面发此 cmd 让
    dev-agent 跑 fetch→rebase→收证→classify 三态，但 CLEAN 也**短路**（``--classify-only``）——不 ``--no-ff``
    merge、不 push（main 零副作用，守 docstring「merge/revert 只 log 不改 main」+ ADR-0008 护栏#7 shadow gate）。
    返回 JSON 经 ``parse_merge_result`` 消费 ``rebase_outcome``（= shadow 决策：clean/conflict/unknown）；
    ``merge_commit`` 恒 None（shadow 不合）。守 D6/ADR-0001（同 ``build_merge_cmd``：控制面只发 cmd）。
    """
    cmd = [python, str(dev_agent_py), "--phase", "merge", "--classify-only",
           "--branch", branch, "--main", main_ref, "--prd-id", prd_id]
    if state_dir:
        cmd += ["--state-dir", state_dir]
    return cmd


# ─── task 4.1 / 4.2：post-merge main 全量测试三态判定（D3 / D8，纯机械判定层）──────────
# spec「Post-merge main verification and auto-revert」：merge+push main 后对**集成后 main** 跑全量测试套件
# （基线=main，含本次合入；覆盖面/基线均 ≠ verify 的 candidate branch —— D8：这是该阶段存在的理由，须显式
# 注释防被 implementer 当 verify 重复偷工）。结果三态驱动 dispatch 路由：
#   PASS   main 绿 → 保留 + 放行（slot 释放，队列续跑）
#   FAIL   main 红 → 触发 revert（task 4.3）
#   UNKNOWN 跑不完/环境失败/超时 → **不 auto-revert 也 keep**，halt 整仓 + CRITICAL（spec scenario
#          「Post-merge test result unknown halts the queue」——不 auto-revert：不确定是否本次合入所致）
#
# fail-safe 判定（同 ``classify_rebase`` 结构——最危险的「误判 PASS 留烂代码」须最难达成）：
# PASS 仅在**全正向证据**齐时断言——确实跑过(ran) + exit0(test_rc==0) + 未超时；缺任一 → UNKNOWN（非 PASS）。
# FAIL 须**确实跑过**且非0退出（非超时）；超时/未跑/无 rc 一律 UNKNOWN（fail-safe：宁可 halt 不误 revert）。
class PostMergeVerdict(str, Enum):
    """post-merge main 全量测试三态（str 子类化便于 JSON 序列化进 state 记录，同 ``RebaseOutcome``）。

    ``UNKNOWN`` 是 fail-safe 信号：dispatch 见之 → keep + halt 整仓 + CRITICAL，**不 auto-revert**
    （spec「any UNKNOWN test result SHALL halt the queue ... MUST NOT continue」）。
    """

    PASS = "pass"      # main 全量测试绿 → 保留 merge + 放行
    FAIL = "fail"      # main 全量测试红（确实跑过 + 非0退出）→ 触发 revert（task 4.3）
    UNKNOWN = "unknown"   # 跑不完/超时/环境失败/无 rc → keep + halt + CRITICAL（不 auto-revert）


@dataclass(frozen=True)
class PostMergeEvidence:
    """post-merge 测试执行证据（dev-agent ``--phase post-merge-test`` 收集后交 ``classify_post_merge``）。

    各字段为**正向证据**语义：``ran=True``/``test_rc=0`` 表示该证据存在且良好。``classify_post_merge`` 据
    正向证据聚合判三态（PASS 须全齐；缺/坏 → UNKNOWN）。``raw`` 留原始诊断（stderr 尾部等）供审计，不参与判定。
    """

    __test__: ClassVar[bool] = False
    ran: bool                  # 测试命令是否确实执行（False=环境失败/命令缺失/未启动 → 无可信 exit code）
    test_rc: int | None        # 测试命令 exit code（None=未取到：超时被杀/异常）；0=绿，非0=红
    timed_out: bool            # 被 wall-clock timeout 杀？（D10：post-merge test profile 化上界）
    raw: dict | None = None    # 原始证据快照（诊断/审计，不参与判定逻辑）


def classify_post_merge(ev: PostMergeEvidence) -> PostMergeVerdict:
    """据 post-merge 测试证据判三态（**纯机械判定**，spec「Post-merge main verification」）。

    判定顺序（fail-safe：「误判 PASS 留烂代码」须最难达成——安全网失效 = 最危险）：
      1. ``timed_out`` → ``UNKNOWN``（半跑完，无法判定；spec scenario「timeout/crash/env failure」）。
      2. 未 ``ran`` → ``UNKNOWN``（命令未执行，exit code 无意义）。
      3. ``test_rc is None`` → ``UNKNOWN``（未取到 exit code）。
      4. ``test_rc == 0`` → ``PASS``（全正向证据齐）。
      5. 其余（``test_rc != 0``）→ ``FAIL``（确实跑过且明确红）。
    """
    if ev.timed_out:
        return PostMergeVerdict.UNKNOWN
    if not ev.ran:
        return PostMergeVerdict.UNKNOWN
    if ev.test_rc is None:
        return PostMergeVerdict.UNKNOWN
    if ev.test_rc == 0:
        return PostMergeVerdict.PASS
    return PostMergeVerdict.FAIL


@dataclass(frozen=True)
class PostMergeResult:
    """post-merge phase 整体结果（控制面 parse dev-agent 返回所得；驱动 dispatch 路由）。

    ``verdict`` = 三态；``evidence`` 留诊断；``error`` 留非预期原因。dispatch 据 ``verdict`` 决 keep/revert/halt。
    """

    __test__: ClassVar[bool] = False
    verdict: PostMergeVerdict
    evidence: PostMergeEvidence
    error: str | None = None


def parse_post_merge_result(payload) -> PostMergeResult:
    """解析 dev-agent post-merge-test phase 末行 JSON 为 ``PostMergeResult``（**fail-safe**：坏/缺 → UNKNOWN）。

    非 dict / 缺证据 / 坏 verdict 值 → 一律降级 UNKNOWN（绝不误判 PASS——否则烂代码留 main 不 revert，安全网
    失效；同 fail-safe-dispatch 不变式：状态不明即阻断）。允许 dev-agent 已判 verdict 直传，亦允许只传原始
    证据由本函数重判（双重保险：dev-agent 判错时控制面据证据纠正）。
    """
    if not isinstance(payload, dict):
        return PostMergeResult(PostMergeVerdict.UNKNOWN,
                               PostMergeEvidence(False, None, False), error="非 dict payload")
    try:
        evidence = PostMergeEvidence(
            ran=bool(payload.get("ran", False)),
            test_rc=payload.get("test_rc"),
            timed_out=bool(payload.get("timed_out", False)),
        )
    except (TypeError, ValueError) as ex:
        return PostMergeResult(PostMergeVerdict.UNKNOWN, PostMergeEvidence(False, None, False),
                               error=f"post-merge 证据解析失败: {ex}")
    # 双重保险：以控制面据证据重判的 verdict 为准（dev-agent 传的 verdict 仅诊断参考）
    return PostMergeResult(classify_post_merge(evidence), evidence, error=payload.get("error"))


# ─── task 4.3：auto-revert 三态判定（D3 / D7，纯机械判定层）──────────────────────────
# spec「Post-merge ... revert itself SHALL be three-state REVERTED/CONFLICT/UNKNOWN」：post-merge FAIL →
# revert 本次自动合入产出的单一 merge commit（``git revert -m 1 --no-edit``，D7 单 commit 粒度；journal 记
# revert_commit sha 供 exactly-once reconcile，D12 / task 6.1b）。三态驱动 dispatch：
#   REVERTED  远端 main 已回滚干净 → triage(post_merge_red_reverted) + 放行（slot 释放，队列续跑）
#   CONFLICT  revert 产生冲突 → halt 整仓 + CRITICAL（revert --abort，不强改 main）
#   UNKNOWN   push reject / 超时 / 无 rc → halt 整仓 + CRITICAL（**不 continue**——远端 main 仍红，
#             spec scenario「Revert itself fails halts the queue ... no further PRD admitted」）
#
# fail-safe（同 classify_rebase/post_merge——「误判 REVERTED 当成功放行」最危险：烂代码留 main + 队列续跑叠加）：
# REVERTED 须**全正向证据**：rc0 + 无冲突 + push 成功 + 未超时；push reject = UNKNOWN（本地 revert 了但远端
# main 仍红——非 REVERTED，halt）。
class RevertOutcome(str, Enum):
    """auto-revert 三态（str 子类化便于 JSON 序列化进 state 记录，同 ``RebaseOutcome``）。

    非 ``REVERTED``（``CONFLICT``/``UNKNOWN``）= halt 整仓 + CRITICAL——dispatch **不 continue** 到下一 PRD
    （spec「Any non-REVERTED revert outcome ... SHALL halt the queue ... MUST NOT continue」）。
    """

    REVERTED = "reverted"   # 远端 main 已回滚干净 → triage + 放行
    CONFLICT = "conflict"   # revert 产生冲突标记 → halt + CRITICAL（revert --abort，不强改 main）
    UNKNOWN = "unknown"     # push reject / 超时 / 无 rc → halt + CRITICAL（不 continue）


@dataclass(frozen=True)
class RevertEvidence:
    """revert 执行证据（dev-agent ``--phase revert`` 收集后交 ``classify_revert``）。

    各字段为**正向证据**语义。``classify_revert`` 据正向证据聚合判三态（REVERTED 须全齐；缺/坏 → UNKNOWN）。
    """

    __test__: ClassVar[bool] = False
    revert_rc: int | None      # ``git revert`` exit code（None=未取到：超时/异常）；0=干净 revert
    conflict_files: int        # unmerged 文件数（>0 = revert 产生冲突）
    push_failed: bool          # revert 后 ``git push`` 被 reject？（True=远端 main 未回滚 → UNKNOWN）
    timed_out: bool            # revert 被 wall-clock timeout 杀？（D10：revert ~60s 上界）
    raw: dict | None = None    # 原始证据快照（诊断/审计，不参与判定逻辑）


def classify_revert(ev: RevertEvidence) -> RevertOutcome:
    """据 revert 执行证据判三态（**纯机械判定**，spec「revert itself SHALL be three-state」）。

    判定顺序（fail-safe：「误判 REVERTED 放行」最危险——烂代码留 main + 队列续跑叠加）：
      1. ``timed_out`` → ``UNKNOWN``（半完成，无法判定）。
      2. ``revert_rc is None`` → ``UNKNOWN``（未取到 exit code）。
      3. ``conflict_files > 0`` → ``CONFLICT``（revert 产生冲突标记；revert --abort）。
      4. ``push_failed`` → ``UNKNOWN``（本地 revert 了但 push reject → 远端 main 仍红，非 REVERTED）。
      5. ``revert_rc == 0`` → ``REVERTED``（rc0 + 无冲突 + push 成功）。
      6. 其余 → ``UNKNOWN``（rc 非0 但无冲突标记等异常态，无法确立）。
    """
    if ev.timed_out:
        return RevertOutcome.UNKNOWN
    if ev.revert_rc is None:
        return RevertOutcome.UNKNOWN
    if ev.conflict_files > 0:
        return RevertOutcome.CONFLICT
    if ev.push_failed:
        return RevertOutcome.UNKNOWN
    if ev.revert_rc == 0:
        return RevertOutcome.REVERTED
    return RevertOutcome.UNKNOWN


@dataclass(frozen=True)
class RevertResult:
    """revert phase 整体结果（控制面 parse dev-agent 返回所得；驱动 dispatch 路由）。

    ``revert_commit`` = revert 产出的新 commit sha（``git revert`` 新建，非删原 merge commit；记录供
    exactly-once reconcile 查 ancestry，D12 / task 6.1b）。``outcome`` 驱动 keep/halt。
    """

    __test__: ClassVar[bool] = False
    outcome: RevertOutcome
    revert_commit: str | None
    evidence: RevertEvidence
    error: str | None = None


def parse_revert_result(payload) -> RevertResult:
    """解析 dev-agent revert phase 末行 JSON 为 ``RevertResult``（**fail-safe**：坏/缺 → UNKNOWN）。

    非 dict / 缺证据 → 一律降级 UNKNOWN（绝不误判 REVERTED——否则不 halt，烂代码留 main + 队列续跑）。
    以控制面据证据重判的 outcome 为准（dev-agent 传值仅诊断参考，双重保险）。
    """
    if not isinstance(payload, dict):
        return RevertResult(RevertOutcome.UNKNOWN, None,
                            RevertEvidence(None, 0, False, False), error="非 dict payload")
    try:
        evidence = RevertEvidence(
            revert_rc=payload.get("revert_rc"),
            conflict_files=int(payload.get("conflict_files", 0) or 0),
            push_failed=bool(payload.get("push_failed", False)),
            timed_out=bool(payload.get("timed_out", False)),
        )
    except (TypeError, ValueError) as ex:
        return RevertResult(RevertOutcome.UNKNOWN, None, RevertEvidence(None, 0, False, False),
                            error=f"revert 证据解析失败: {ex}")
    revert_commit = payload.get("revert_commit")
    if not isinstance(revert_commit, str):
        revert_commit = None
    return RevertResult(classify_revert(evidence), revert_commit, evidence, error=payload.get("error"))


def build_post_merge_cmd(*, python: str, dev_agent_py, test_cmd: str, main_ref: str,
                         prd_id: str, state_dir: str | None = None,
                         timeout: int | None = None) -> list[str]:
    """构造 ``dev-agent --phase post-merge-test`` 命令（控制面发，dev-agent 在目标仓对集成后 main 跑测试）。

    守 D6/ADR-0001（同 ``build_merge_cmd``）：控制面只发 cmd，dev-agent 在目标仓 worktree 经机械层
    （subprocess）跑测试套件。``test_cmd`` = verify 用过的同一条命令（决策 E：profile/上报单一真理源），
    基线换成集成后 main（D8：≠ verify 的 candidate branch）。``timeout`` = D10 post-merge test 上界。
    """
    cmd = [python, str(dev_agent_py), "--phase", "post-merge-test",
           "--test-cmd", test_cmd, "--main", main_ref, "--prd-id", prd_id]
    if state_dir:
        cmd += ["--state-dir", state_dir]
    if timeout is not None:
        cmd += ["--timeout", str(timeout)]
    return cmd


def build_revert_cmd(*, python: str, dev_agent_py, merge_commit: str, main_ref: str,
                     prd_id: str, state_dir: str | None = None) -> list[str]:
    """构造 ``dev-agent --phase revert`` 命令（控制面发，dev-agent 在目标仓 revert 单一 merge commit + push）。

    守 D6/ADR-0001：控制面只发 cmd，dev-agent 在目标仓 worktree 经机械层（``git()``）执行
    ``git revert -m 1 --no-edit <merge_commit>`` + ff-only push + 记 revert_commit。``merge_commit`` = 本次
    自动合入产出的单一 commit（D7 粒度；journal 须另记 revert_commit sha，task 6.1b）。
    """
    cmd = [python, str(dev_agent_py), "--phase", "revert",
           "--merge-commit", merge_commit, "--main", main_ref, "--prd-id", prd_id]
    if state_dir:
        cmd += ["--state-dir", state_dir]
    return cmd
