#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""semantic_gate.py — 内循环方向抽查的零依赖纯逻辑（in-loop-semantic-checkpoint）。

dev-agent.py（带连字符不可 import 测）的胶水之外，方向抽查的全部可测逻辑在此——
与 bash_allowlist / evidence / persona_call 同构（零依赖纯模块，dev-agent 顶部 import 不
连带加载 claude_agent_sdk，守 test_dev_agent_source.py 反 invariant）。

职责（stalled 之外的第二干预源，补三道机械机制对"方向"瞎眼的盲区）：
    常量                — JUDGE_K / 成本熔断 / diff 截断 / pa-progress 时长预算 / redirect_hint 白名单
    CheckpointAction    — M2: action 字面量 Literal（拼错 → IDE/mypy 红线，防 exit15 静默失效）
    LegDecision         — M3: decide_after_leg 返回 NamedTuple（拼错 → loud AttributeError）
    resolve_claude_bin_safe — claude CLI 解析，找不到返 ''（fail-open，不 sys.exit 杀进程）
    truncate_diff       — diff 截断喂评判
    collect_diff        — git_fn 注入取 diff + 截断 + 失败安全占位 + log_fn 留痕（silent-failure M4）
    validate_redirect_hint — H3: redirect_hint 内容白名单（不可信 diff→pa-progress→dev 升权链 defense）
    build_progress_prompt — pa-progress 评判 prompt（PRD 全文 + diff + JSON 契约 + H3 围栏加固）
    build_redirect_prompt — off_track 首次注入 dev 的续做提示（接受新 session 现实：break 后开新 query）
    prior_progress_note  — redirect 前情机械提取（run_log tool_use 重放：已读/已写文件 + diff 现状）
    judge_direction     — 调 pa-progress（经 persona_call），fail-open→None
    run_checkpoint      — 内循环 checkpoint 状态机（on_track 重置 / 两阶段 off_track / H2 空 hint fail-open
                          / H3 非法 hint fail-open / 熔断 / M5 record 与 state 同步）
    decide_after_leg    — dev loop 一段结束后决定下一步（exhausted→exit15 / resume redirect / 终止）

dev-agent.py 的 process_dev_loop / main 只做接线（调本模块 + 据 action/decision 控制流）。
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Callable, Literal, NamedTuple

from persona_call import run_persona_subproc   # 零依赖（stdlib + stage_contracts）

# ─── 常量（in-loop-semantic-checkpoint）──────────────────────────────
JUDGE_K = 10                      # 每 K turn 抽一次方向（max_turns=150 → 最多 ~15 次）
# ⚠ 当前 SDK cost 上报不可靠（meta.cost / total_cost_usd 常 None，dev-agent.py:824-826 canary 实证）→
#   judge_cost_acc 实际累加 0，本熔断为防御纵深、当前不触发。修复依赖 ADR-0006 #6 cost 上报修复。
JUDGE_BUDGET_CAP = 0.40           # 评判累计成本熔断（USD）；见上，当前实际失效
JUDGE_DIFF_MAX_CHARS = 10000      # 喂给评判的 diff 截断上界（防 MB 级 diff）
JUDGE_UNTRACKED_MAX_FILES = 5     # untracked 新文件进 diff_bundle 的数量上界（防 prompt 爆炸）
PROGRESS_MAX_TURNS = 15           # pa-progress max_turns（prompt-only 判方向，无 tool loop）
PROGRESS_TIMEOUT = 120            # pa-progress wall-clock（glm-5.2 + 小 prompt 常态 5-30s）
PROGRESS_AGENT = "pa-progress"

# H3: redirect_hint 内容白名单。redirect_hint 路径 = 不可信 diff → pa-progress(LLM) → dev(持 Bash/Edit/Write)，
# 是真实升权链。即便 pa-progress 被 prompt-injection 越狱，白名单在注入 dev 前拦恶意内容（defense-in-depth）。
REDIRECT_HINT_MAX = 2000
# 禁用子串（lowercase 匹配）：围栏越狱 + shell 元字符 + 网络外传 + 代码执行 + 编码绕过。
# m11 教训：单反引号 ` 曾在表内 → progress 写指引爱用内联代码标记（`git diff`）被系统性误拦
# （连续 3 轮 judge_failopen_bad_hint）。hint 注入 dev 时全文经 `> ` 引用前缀包裹（引用文本语境），
# 单反引号是 markdown 常态无执行语义；命令替换真形态 `$(` 与围栏 ``` 仍拦。
REDIRECT_HINT_FORBIDDEN = ("```", "curl", "wget", "http://", "https://", "exec ", "$(",
                           "import os", "subprocess", "eval(", "base64", "os.system", "\\x")

# M2: checkpoint action 字面量类型约束。散落 6 return + dev-agent 消费端，拼错会让 exit15 止损
# 出口静默失效且编译/ruff/单测全过——Literal 让 IDE/mypy/pyright 在拼错处报红线（零运行时改动）。
CheckpointAction = Literal["none", "continue", "redirect", "exhausted"]


# M3: decide_after_leg 返回结构。原 dict + 字符串 key 拼错 → silent None（KeyError 才 loud）；
# NamedTuple 拼错 → loud AttributeError，与既有 TestEvidence（dev-agent.py:344）同构。
class LegDecision(NamedTuple):
    terminal: Literal["exhausted"] | None
    resume_redirect: bool
    next_redirects_done: int


# ─── claude CLI 解析（safe：找不到返 ''，不 sys.exit 杀 dev 进程）────────
def resolve_claude_bin_safe() -> str:
    """claude CLI 解析：PA_CLAUDE_BIN env → shutil.which。找不到返 ''（checkpoint fail-open）。

    不用 persona_call.resolve_claude_bin（它会 sys.exit 杀进程——适合 run_daily 主路径，
    不适合 dev 内循环的额外护栏：找不到 claude 应跳过评判，不该杀死 dev loop）。"""
    env = os.environ.get("PA_CLAUDE_BIN")
    if env and Path(env).is_file():
        return env
    return shutil.which("claude") or ""


# ─── diff 摘取 ───────────────────────────────────────────────────────
def truncate_diff(diff_text: str, max_chars: int = JUDGE_DIFF_MAX_CHARS) -> str:
    """截断 diff 喂评判（防 MB 级 diff 撑爆 prompt）。"""
    if len(diff_text) <= max_chars:
        return diff_text
    return diff_text[:max_chars] + f"\n…[truncated, diff 共 {len(diff_text)} 字符，已截断前 {max_chars}]"


def collect_diff(git_fn: Callable[[list[str]], str], max_chars: int = JUDGE_DIFF_MAX_CHARS, *,
                 log_fn: Callable[[dict], None] | None = None,
                 read_fn: Callable[[str], str] | None = None,
                 junk_pred: Callable[[str], bool] | None = None) -> str:
    """经注入的 git_fn 取 worktree 未 staged diff + untracked 新文件内容 + 截断；git 失败 → 安全占位（fail-open）。

    组5.1 hub 全链 #21 实证：dev 产出纯新增文件（PRD 恰是「新增两文件」形态）时 `git diff`
    恒空 → pa-progress 两轮 covered=[] off_track 误止损（m21：文件早已写出，judge 证据面
    看不见）。同 #17 家族——#17 只修了 jsonl 快照显示面，这里是 judge 实际吃的 diff_bundle。
    untracked 段需 read_fn 注入（内容级证据；不用 add -N 保持 index 无副作用，不用
    diff --no-index——有差异时 rc=1 会被 git() 的 rc 检查抛异常）；junk_pred 过滤执行面
    遥测/字节码（与 #20a 的 diff_stat 视图共享清单）。两者 None = 兼容旧行为（跳过该段）。

    silent-failure M4：失败时经 log_fn 落 diff_collect_failed 事件进 run_log（否则占位串被
    喂给 pa-progress 但 run_log 无独立痕迹，postmortem 无从反推 verdict 奇怪是因为 diff 没抓到）。"""
    try:
        parts = [git_fn(["diff"])]
        if read_fn is not None:
            untracked = [l.strip() for l in
                         git_fn(["ls-files", "--others", "--exclude-standard"]).splitlines()
                         if l.strip()]
            if junk_pred is not None:
                untracked = [p for p in untracked if not junk_pred(p)]
            for p in untracked[:JUDGE_UNTRACKED_MAX_FILES]:
                try:
                    body = read_fn(p)
                except Exception as e:   # 单文件读失败不拖垮整段（如并发删除）
                    if log_fn:
                        log_fn({"event": "untracked_read_failed", "path": p, "error": str(e)})
                    continue
                head = "\n".join("+" + ln for ln in body.splitlines()[:80])
                parts.append(f"--- /dev/null\n+++ b/{p} (new file, untracked)\n{head}")
        return truncate_diff("\n".join(x for x in parts if x.strip()), max_chars)
    except Exception as e:
        if log_fn:
            log_fn({"event": "diff_collect_failed", "error": str(e)})
        return f"(git diff 失败: {e})"


# ─── H3: redirect_hint 内容白名单 ─────────────────────────────────────
def validate_redirect_hint(hint) -> bool:
    """off_track 的 redirect_hint 内容校验（H3 升权链 defense-in-depth）。

    redirect_hint 由 pa-progress（受不可信 diff 影响的 LLM）产出，原样拼回持全权的 dev。
    本函数在注入前拦：空 / 超长 / 含围栏或 shell 元字符或网络外传串 → False（fail-open 丢弃，
    不注入 dev，不污染 off_track_count）。合法的「转向验收标准 A1」类纯文本指引 → True。"""
    if not hint or not isinstance(hint, str):
        return False
    if not hint.strip():             # 纯空白 = 空（无法纠偏，同 H2 fail-open）
        return False
    if len(hint) > REDIRECT_HINT_MAX:
        return False
    low = hint.lower()
    return not any(f in low for f in REDIRECT_HINT_FORBIDDEN)


# ─── prompt 构造 ─────────────────────────────────────────────────────
def _defang(text: str) -> str:
    """H3: 把连续 3+ 反引号转单引号，防 diff/PRD 内容里的 ``` 提前闭合评判 prompt 的 4 反引号围栏
    （code-fence 越狱：内容嵌 ``` 闭合围栏后，后续文字脱离「数据」语境被当顶层指令）。"""
    return re.sub(r"`{3,}", lambda m: "'" * len(m.group()), text)


def build_progress_prompt(prd_text: str, diff_bundle: str, prior_rejection: str = "") -> str:
    """pa-progress 评判 prompt：PRD 全文 + diff 摘要 + JSON 输出契约（rubric 自抽）。

    H3 围栏加固：用 4 反引号围栏 + _defang 转义内容里的 3+ 反引号，双层防 code-fence 越狱
    （第一层白名单 validate_redirect_hint 在 redirect_hint 注入 dev 前拦；本处是喂 pa-progress 时防越狱）。
    prior_rejection（m11 反馈环）：pa-progress 是无状态单次调用，上轮 hint 被白名单拒了它不知道
    → 同样的 hint 重复产出（m11 连续 3 轮）。编排器把拒因回传此处注入 prompt，产出才可修正。"""
    parts = [
        "# 内循环方向抽查（in-loop semantic checkpoint）",
        "",
        "你是 pa-progress。判 dev 当前 diff 是否在解决下面 PRD 的验收标准（on_track / off_track）。",
        "**rubric 自抽**：自己从 PRD 定位「验收标准」节。不看测试绿红、不要测试产物。",
        "",
        "## PRD（全文）",
        "````",
        _defang(prd_text),
        "````",
        "",
        "## dev 当前 git diff（未 staged）",
        "````",
        _defang(diff_bundle),
        "````",
    ]
    if prior_rejection:
        parts += [
            "",
            "## ⚠️ 你上一轮的 redirect_hint 被安全闸拒绝（未注入 dev）",
            _defang(prior_rejection),
            "",
            "这次给出的 redirect_hint 务必**纯文本**（可用中文/内联代码名，禁 URL/命令/围栏），避免上述被拒特征。",
        ]
    parts += [
        "",
        "## 输出契约（硬性）",
        "只输出一个 JSON 对象，无多余文字/markdown 围栏：",
        '{"verdict":"on_track|off_track","covered":["<已覆盖验收点>"],"off_topic":["<跑偏项>"],'
        '"redirect_hint":"<off_track 时填：可执行纠偏指引，纯文本，禁含代码/命令/URL>","summary":"<一句话>"}',
    ]
    return "\n".join(parts)


def prior_progress_note(run_log) -> str:
    """redirect 前情机械提取（m10 缺口①，纯机械零 LLM）：重放 run_log 每 turn 的 tool_use
    [{name,target}] —— Read 归已读、Edit/Write/MultiEdit 归已写（去重），末次 diff_stat 为现状。

    背景：redirect leg 是全新 session（H1，无前任记忆）——大仓探索期（>K turn 纯读文件）被
    判 off_track 后，新 session 按标准开局重读同样文件 → 再 off_track 死循环（m10 实证：
    30 turn 全在读、worktree 零改动、exit 15）。注入前情让新 session 跳过已完成的探索。
    只收文件路径可靠的 name（Read/Edit/Write/MultiEdit）；Glob/Grep 的 target 是 pattern 不可靠，
    Bash 是命令——宁缺毋滥。run_log 缺/空/全损坏 → ""（调用面兜底零变化）。"""
    read_files: list[str] = []
    wrote_files: list[str] = []
    diff_stat = ""
    try:
        import json
        with open(run_log, encoding="utf-8") as f:
            for line in f:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue                                     # 容错：损坏行跳过（run_log 中部损坏既有语义）
                if not isinstance(e, dict) or "tool_use" not in e:
                    continue                                     # judge/redirect 事件行
                if e.get("diff_stat"):
                    diff_stat = e["diff_stat"]
                for tu in e.get("tool_use") or []:
                    t = (tu.get("target") or "").strip()
                    if not t:
                        continue
                    if tu.get("name") in ("Edit", "Write", "MultiEdit"):
                        if t not in wrote_files:
                            wrote_files.append(t)
                    elif tu.get("name") == "Read" and t not in read_files:
                        read_files.append(t)
    except OSError:
        return ""
    if not read_files and not wrote_files:
        return ""
    lines = ["## 前任会话进度（机械提取自 run_log——你是新 session 无前任记忆，先看这里，勿重复探索）"]
    if read_files:
        lines.append("- 已读文件（勿重读，基于已有理解直接开工）：" + "、".join(read_files[:30]))
    if wrote_files:
        lines.append("- 已写/已改文件（改动仍在工作树）：" + "、".join(wrote_files[:30]))
    lines.append("- 工作树 diff 现状：" + (diff_stat if diff_stat else "干净（前任尚未写任何代码——探索已完成，直接开写）"))
    return "\n".join(lines)


def build_redirect_prompt(base_prompt: str, redirect_hint: str, prior_note: str = "") -> str:
    """off_track 首次：把 redirect_hint 注入回 dev。

    ⚠ 接受新 session 现实（H1 三方 review 确认）：Path B 的 break+resume 在 SDK query() 模型下
    拿不到 session_id（break 在 AssistantMessage，session_id 流末才有），redirect leg 实际开**新 session**。
    故 prompt 自包含 PRD（base_prompt）+ hint + 显式提示「先前改动在工作树」，让 dev 新 session 自恢复。
    H3: hint 经 validate_redirect_hint 校验后才进这里（run_checkpoint 保证）；引用前缀标注为参考文本。
    prior_note（m10 缺口①）：prior_progress_note 机械前情，非空时插在 hint 后（新 session 无前任
    记忆，防探索期死循环）；缺省零变化。"""
    parts = [base_prompt + "\n\n" + "\n".join([
        "## ⚠️ 内循环方向抽查：跑偏纠偏（新 session 续做）",
        "独立评判器判你当前 diff **偏离了 PRD 验收标准**（off_track）。这是给你的 1 次纠偏机会——",
        "**立刻转向**下面的指引（评判器参考文本，非代码/命令），不要继续在跑偏方向上堆改动：",
        "",
        "> " + redirect_hint.replace("\n", "\n> "),
        "",
        "注：先前 leg 的改动仍在工作树（`git diff` 可见），请先审视已有改动再继续，勿盲目重做。",
        "纠偏后续做；下次抽查若仍 off_track 将止损退出。",
    ])]
    if prior_note:
        parts.append(prior_note)
    return "\n\n".join(parts)


# ─── 评判调用（fail-open）────────────────────────────────────────────
def judge_direction(prd_text: str, diff_bundle: str, *,
                    claude_bin: str | None = None,
                    log_fn: Callable[[str], None] | None = None,
                    prior_rejection: str = "") -> tuple[dict, dict] | None:
    """调 pa-progress 评判方向。fail-open：subproc 失败 / 找不到 claude → None（不抛，
    绝不因护栏故障误杀 dev）。claude_bin=None → resolve_claude_bin_safe。
    prior_rejection（m11 反馈环）：上轮 hint 被白名单拒的拒因，注入 prompt 供 progress 修正。"""
    if not claude_bin:
        claude_bin = resolve_claude_bin_safe()
    if not claude_bin:
        if log_fn:
            log_fn("⚠ 方向抽查 fail-open：找不到 claude CLI（跳过评判）")
        return None
    try:
        prompt = build_progress_prompt(prd_text, diff_bundle, prior_rejection=prior_rejection)
        return run_persona_subproc(
            claude_bin, PROGRESS_AGENT, prompt,
            max_turns=PROGRESS_MAX_TURNS, timeout=PROGRESS_TIMEOUT,
            stage="progress", retry_cap=2, log=log_fn)
    except Exception as e:
        if log_fn:
            log_fn(f"⚠ 方向抽查 fail-open（忽略）: {e}")
        return None


# ─── checkpoint 状态机（内循环每 K turn 调一次）──────────────────────
def run_checkpoint(state: dict, turn: int, prd_text: str, diff_bundle: str, *,
                   judge_fn: Callable[[str, str], tuple[dict, dict] | None]
                   ) -> tuple[CheckpointAction, dict | None]:
    """执行一次方向抽查。返回 (action, record)。

    action ∈ {"none","continue","redirect","exhausted"}：
      none      — 非 K 边界 / 成本熔断 / 评判 fail-open / H2 空 hint / H3 非法 hint → dev loop 照常推进（不 break）
      continue  — on_track → off_track_count 重置，dev loop 继续
      redirect  — 首 off_track（且 hint 合法）→ 设 redirect_pending，调用方 break（main 新 session 续做）
      exhausted — 二 off_track → 设 off_track_exhausted，调用方 break（main exit 15）
    record：要落 run_log 的 judge 事件（None=非 K 边界无事件）。state 字段在内部更新。

    H2+H3：off_track 但 redirect_hint 缺失/空/非法（validate_redirect_hint 不过）→ fail-open
      返回 none，**不递增 off_track_count**（否则 LLM 漏填一字段就吞掉首次纠偏、两阶段降级零阶段）。
    M5：record 的 off_track_count 在 state 变更**之后**捕获（与 state 同步，postmortem 不困惑）。
    judge_fn：依赖注入（生产=judge_direction 带 claude_bin/log_fn 闭包；测试=stub）。"""
    if turn == 0 or turn % state["judge_k"] != 0:
        return "none", None
    # m12 缺口④：零 diff 探索期不判方向。off_track ≡ 有产出且方向错——无 diff = 无方向可判，
    # judge 只能瞎猜（m10/m11/m12 三轮实证全死于零 diff 期误判：大仓探索期 10 turn 纯读是正常
    # 起步非跑偏；m11 更是靠 hint 被拦 fail-open 不计数才活到写码期）。skip 不调 judge、不算
    # round、不污染 last_verdict；探索慢/死循环由 maxTurns / dev 时限 / N_STALL 兜底（stall 域）。
    # git 失败时 collect_diff 返回非空占位串 → 不走此分支，照常 judge（fail-open 语义保留）。
    if not diff_bundle.strip():
        return "none", {"event": "judge_skip", "reason": "no_diff_yet", "turn": turn}
    if state["judge_cost_acc"] >= JUDGE_BUDGET_CAP:
        return "none", {"event": "judge_skip", "reason": "cost_breaker",
                        "turn": turn, "acc_cost": state["judge_cost_acc"]}
    res = judge_fn(prd_text, diff_bundle)
    if res is None:
        return "none", {"event": "judge_failopen", "turn": turn}
    payload, meta = res
    cost = meta.get("cost")
    if isinstance(cost, (int, float)):
        state["judge_cost_acc"] += cost
    state["judge_rounds"] += 1
    state["last_verdict"] = payload.get("verdict")
    state["last_covered"] = payload.get("covered") or []
    if payload.get("verdict") == "on_track":
        state["off_track_count"] = 0
        state.pop("last_hint_rejection", None)     # m11 反馈环：on_track → 陈旧拒因失效（防误导下次 judge）
        return "continue", {"event": "judge", "turn": turn, "round": state["judge_rounds"],
                            "verdict": "on_track", "covered": state["last_covered"],
                            "cost": cost, "off_track_count": state["off_track_count"]}
    # off_track
    hint = payload.get("redirect_hint") or ""
    if not validate_redirect_hint(hint):
        # H2+H3: redirect_hint 缺失/空/含禁用串 → 无法安全纠偏，fail-open 不污染 off_track_count。
        # m11 反馈环：拒因 + hint 样本落 state（下次 judge 经 prior_rejection 回传 progress——
        # 无状态 persona 不然永远重复同样的非法 hint）与事件（诊断可见，不再瞎猜拒因）。
        reason = ("empty" if not hint.strip()
                  else "over_max" if len(hint) > REDIRECT_HINT_MAX
                  else "forbidden:" + ",".join(f for f in REDIRECT_HINT_FORBIDDEN if f in hint.lower()))
        state["last_hint_rejection"] = (
            f"hint 被拒原因={reason}（上限 {REDIRECT_HINT_MAX} 字、禁串：围栏/$(/URL/执行类）；"
            f"被拒 hint 开头：{hint[:200]}")
        return "none", {"event": "judge_failopen_bad_hint", "turn": turn,
                        "round": state["judge_rounds"], "verdict": "off_track",
                        "reason": reason, "hint_sample": hint[:200]}
    state["off_track_count"] += 1
    if state["off_track_count"] >= 2:
        state["off_track_exhausted"] = True
        return "exhausted", {"event": "judge", "turn": turn, "round": state["judge_rounds"],
                             "verdict": "off_track", "covered": state["last_covered"],
                             "cost": cost, "off_track_count": state["off_track_count"], "exhausted": True}
    state["redirect_pending"] = hint
    return "redirect", {"event": "judge", "turn": turn, "round": state["judge_rounds"],
                        "verdict": "off_track", "covered": state["last_covered"],
                        "cost": cost, "off_track_count": state["off_track_count"]}


# ─── dev loop 一段结束后的下一步决策（main while 循环消费）────────────
def decide_after_leg(state: dict, redirects_done: int) -> LegDecision:
    """dev loop 一段（一次 query）结束后决定下一步。返回 LegDecision：
      terminal       — "exhausted"（main exit 15）| None
      resume_redirect — True（用 redirect prompt 再跑一段，新 session）| False
      next_redirects_done — 下一轮的 redirects_done 计数

    规则（spec in-loop-semantic-checkpoint §两阶段纠偏）：
    - off_track_exhausted → terminal=exhausted（优先，exit 15）
    - redirect_pending 且 redirects_done==0 → resume_redirect=True（给且仅给 1 次纠偏）
    - 否则 → dev loop 正常完成（进 stalled/gate/commit）"""
    if state.get("off_track_exhausted"):
        return LegDecision(terminal="exhausted", resume_redirect=False,
                           next_redirects_done=redirects_done)
    if state.get("redirect_pending") and redirects_done == 0:
        return LegDecision(terminal=None, resume_redirect=True, next_redirects_done=1)
    return LegDecision(terminal=None, resume_redirect=False,
                       next_redirects_done=redirects_done)
