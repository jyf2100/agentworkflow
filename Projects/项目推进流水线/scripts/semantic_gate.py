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
PROGRESS_MAX_TURNS = 15           # pa-progress max_turns（prompt-only 判方向，无 tool loop）
PROGRESS_TIMEOUT = 120            # pa-progress wall-clock（glm-5.2 + 小 prompt 常态 5-30s）
PROGRESS_AGENT = "pa-progress"

# H3: redirect_hint 内容白名单。redirect_hint 路径 = 不可信 diff → pa-progress(LLM) → dev(持 Bash/Edit/Write)，
# 是真实升权链。即便 pa-progress 被 prompt-injection 越狱，白名单在注入 dev 前拦恶意内容（defense-in-depth）。
REDIRECT_HINT_MAX = 500
# 禁用子串（lowercase 匹配）：围栏越狱 + shell 元字符 + 网络外传 + 代码执行 + 编码绕过
REDIRECT_HINT_FORBIDDEN = ("```", "curl", "wget", "http://", "https://", "exec ", "$(", "`",
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
                 log_fn: Callable[[dict], None] | None = None) -> str:
    """经注入的 git_fn 取 worktree 未 staged diff + 截断；git 失败 → 安全占位（fail-open）。

    silent-failure M4：失败时经 log_fn 落 diff_collect_failed 事件进 run_log（否则占位串被
    喂给 pa-progress 但 run_log 无独立痕迹，postmortem 无从反推 verdict 奇怪是因为 diff 没抓到）。"""
    try:
        return truncate_diff(git_fn(["diff"]), max_chars)
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


def build_progress_prompt(prd_text: str, diff_bundle: str) -> str:
    """pa-progress 评判 prompt：PRD 全文 + diff 摘要 + JSON 输出契约（rubric 自抽）。

    H3 围栏加固：用 4 反引号围栏 + _defang 转义内容里的 3+ 反引号，双层防 code-fence 越狱
    （第一层白名单 validate_redirect_hint 在 redirect_hint 注入 dev 前拦；本处是喂 pa-progress 时防越狱）。"""
    return "\n".join([
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
        "",
        "## 输出契约（硬性）",
        "只输出一个 JSON 对象，无多余文字/markdown 围栏：",
        '{"verdict":"on_track|off_track","covered":["<已覆盖验收点>"],"off_topic":["<跑偏项>"],'
        '"redirect_hint":"<off_track 时填：可执行纠偏指引，纯文本，禁含代码/命令/URL>","summary":"<一句话>"}',
    ])


def build_redirect_prompt(base_prompt: str, redirect_hint: str) -> str:
    """off_track 首次：把 redirect_hint 注入回 dev。

    ⚠ 接受新 session 现实（H1 三方 review 确认）：Path B 的 break+resume 在 SDK query() 模型下
    拿不到 session_id（break 在 AssistantMessage，session_id 流末才有），redirect leg 实际开**新 session**。
    故 prompt 自包含 PRD（base_prompt）+ hint + 显式提示「先前改动在工作树」，让 dev 新 session 自恢复。
    H3: hint 经 validate_redirect_hint 校验后才进这里（run_checkpoint 保证）；引用前缀标注为参考文本。"""
    return base_prompt + "\n\n" + "\n".join([
        "## ⚠️ 内循环方向抽查：跑偏纠偏（新 session 续做）",
        "独立评判器判你当前 diff **偏离了 PRD 验收标准**（off_track）。这是给你的 1 次纠偏机会——",
        "**立刻转向**下面的指引（评判器参考文本，非代码/命令），不要继续在跑偏方向上堆改动：",
        "",
        "> " + redirect_hint.replace("\n", "\n> "),
        "",
        "注：先前 leg 的改动仍在工作树（`git diff` 可见），请先审视已有改动再继续，勿盲目重做。",
        "纠偏后续做；下次抽查若仍 off_track 将止损退出。",
    ])


# ─── 评判调用（fail-open）────────────────────────────────────────────
def judge_direction(prd_text: str, diff_bundle: str, *,
                    claude_bin: str | None = None,
                    log_fn: Callable[[str], None] | None = None) -> tuple[dict, dict] | None:
    """调 pa-progress 评判方向。fail-open：subproc 失败 / 找不到 claude → None（不抛，
    绝不因护栏故障误杀 dev）。claude_bin=None → resolve_claude_bin_safe。"""
    if not claude_bin:
        claude_bin = resolve_claude_bin_safe()
    if not claude_bin:
        if log_fn:
            log_fn("⚠ 方向抽查 fail-open：找不到 claude CLI（跳过评判）")
        return None
    try:
        prompt = build_progress_prompt(prd_text, diff_bundle)
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
        return "continue", {"event": "judge", "turn": turn, "round": state["judge_rounds"],
                            "verdict": "on_track", "covered": state["last_covered"],
                            "cost": cost, "off_track_count": state["off_track_count"]}
    # off_track
    hint = payload.get("redirect_hint") or ""
    if not validate_redirect_hint(hint):
        # H2+H3: redirect_hint 缺失/空/含禁用串 → 无法安全纠偏，fail-open 不污染 off_track_count
        return "none", {"event": "judge_failopen_bad_hint", "turn": turn,
                        "round": state["judge_rounds"], "verdict": "off_track"}
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
