#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dev-agent.py — 项目推进流水线「控制面标准执行器」（ADR-0006）。

历史：原为各被控仓自带（ADR-0003「仓内自治 dev agent」），每仓一份
dev-agent.{py,mjs}。2026-07-18 上收控制面：单一 Python 调度器服务所有被控仓，
消除仓间漂移（N_STALL 等常量多仓不一致）+ 消解 run_daily.py 复刻的 slug shadow。
详见 ADR-0006。

本脚本是 **cwd-相对的纯调度器**：REPO_ROOT = Path.cwd()，所有 git/SDK 操作都用
REPO_ROOT。dispatch 时 cwd=被控仓 worktree → 本脚本就地操作该仓。物理位置在
vault，但执行贴被控仓。被控仓零脚本。

仓特定知识（目录结构 / 测试入口 / 受保护路径 / 环境名）**不进本脚本**——由各仓
根 CLAUDE.md 承载，SDK 经 setting_sources=["project"] 自动加载（B1 决策）。
本脚本只含跨仓一致的：CLI、退出码、stdout JSON 契约、SPEC #27 stall 刹车、
通用 dev 守则 prompt。

契约（与历史 dev-agent.{py,mjs} 一一对应，pa-dispatch 解析端零改动）：
  CLI: --prd/--source/--base/--branch-prefix/--dry-run
  退出码: 0=成功（开 PR 或 dry-run 完成）| 10=PRD 缺失/读不到 | 11=SDK dev loop 失败 |
          12=stalled（SPEC #27 主动刹车）| 13=git/push/PR 失败 |
          14=发布门拦截（测试未跑/失败/过期，OpenSpec verified-dev-execution）| 99=未捕获
  stdout: 仅最终一行 JSON（字段名与历史完全一致）

用法（pa-dispatch 自动调，或人手动）：
  cd <被控仓 worktree> && python <vault>/.../dev-agent.py --prd <prd.md> \
      [--source <signal.md>] [--base main] [--dry-run]

⚠️ follow-up 待验证：
  - ClaudeAgentOptions 的 tools= 字段为硬白名单（SDK 验证：allowed_tools 仅是
    批准列表非可用性限制）。0.2.x 字段名以实跑为准。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from claude_agent_sdk import (
    query,
    ClaudeAgentOptions,
    AssistantMessage,
    UserMessage,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    ToolResultBlock,
    PermissionResultAllow,            # ADR-0006 #7：can_use_tool 权限闸返回类型
    PermissionResultDeny,
)

from slug_utils import dev_slugify   # ADR-0006 #5：dev_slugify 单一源头（无依赖模块，避免顶部 import 触发 sdk 连带加载拖垮 cron）
from bash_allowlist import decide_bash   # ADR-0006 #7：Bash 命令放行判定（无依赖模块，同上）
from evidence import TestEvidence, evaluate_gate, mark_stale, GATE_PUBLISH   # OpenSpec verified-dev-execution：结构化测试证据 + 发布硬门（无依赖模块，同上）
from prompt_stream import prompt_stream   # ADR-0006 #7 streaming 修正：string prompt→AsyncIterable（无依赖模块，单测可零 SDK 锁定 dict 结构）
import sdk_compat_patch                   # ADR-0006 #7（migrate-dev-agent-streaming-with-1106-patch）：#1106 keep-alive 定向 patch，根治 #1105（零依赖模块，apply 内延迟 import SDK）
from external_state import sanitize   # C2 脱敏：git()/gh() stderr 进 JSON error 前抹 token（无依赖模块，同上）
from coordinator import build_coordinator   # task 2.3b：coordinator 边界（design 决策#1：一次解析 loop flag + own journal/artifacts/IDs）
from hook_bridge import build_dev_hooks      # task 2.3b：lifecycle_hooks 开 → 注册真实 SDK lifecycle hooks（ClaudeAgentOptions.hooks）

REPO_ROOT = Path.cwd()

# ─── 仓内主动刹车 + 监控常量（SPEC §决策#27，跨仓一致——上收的核心理由）────────
WRITE_TOOLS = {"Edit", "Write", "MultiEdit"}   # 写类工具（Bash 跑 test/git，不计入"尝试修代码"）
N_STALL = 100                # verifiedRed 后连续 N 轮无写类 tool_use → stalled（2026-07-18：3→100，避免 dev「先诊断后改」被过早刹车）
INPUT_TRUNC = 500            # tool_use.input 落盘截断
MAX_BUDGET = 10              # maxBudgetUsd（降级兜底，宽松）
STATE_RUNS_DIR = REPO_ROOT / "state" / "runs"


# dev_slugify 已上移至 slug_utils.py（本文件顶部 import；ADR-0006 #5 单一源头，消解 ADR-0004 #4 shadow）


def parse_args(argv: list[str]) -> dict:
    out = {"prd": None, "source": None, "base": "main", "dry_run": False,
           "branch_prefix": "pa-dev", "feedback_artifact": None, "help": False,
           # task 3.3：session-aware retry 接入生产执行路径（resume/fork/new-session 分配 distinct iteration）
           "iteration_seq": 0, "resume_session": None, "fork_session": False,
           # task 3.3 P0-3：state_dir 由控制面 run_daily 注入（=STATE_DIR）→ dev-agent session_store 与
           #   控制面 coordinator 同一 SessionStore（retry 读 dev-agent 持久化的 session_id）；缺省=worktree/state
           "state_dir": None,
           # add-cross-prd-learning-memory Section 5 接线：控制面在 dispatch-entry 把检索出的 lesson block
           #   写成 content-addressed artifact，path 透传给目标面 dev-agent（与 --feedback-artifact 完全对称）。
           #   baseline / injection=off → None（build_prompt 跳过注入，prompt byte-identical baseline）。
           "lessons_artifact": None}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--prd": out["prd"] = argv[i + 1]; i += 1
        elif a == "--source": out["source"] = argv[i + 1]; i += 1
        elif a == "--base": out["base"] = argv[i + 1]; i += 1
        elif a == "--branch-prefix": out["branch_prefix"] = argv[i + 1]; i += 1
        elif a == "--feedback-artifact": out["feedback_artifact"] = argv[i + 1]; i += 1   # task 3.4：driven retry 反馈源
        elif a == "--lessons-artifact": out["lessons_artifact"] = argv[i + 1]; i += 1     # Section 5：learning memory lesson block 源
        elif a == "--iteration-seq": out["iteration_seq"] = int(argv[i + 1]); i += 1     # task 3.3：revise/resume/fork/new-session 衍生 iteration
        elif a == "--resume-session": out["resume_session"] = argv[i + 1]; i += 1        # task 3.3：retry 同 session resume（SDK ResultMessage.session_id）
        elif a == "--fork-session": out["fork_session"] = True                           # task 3.3：fork 新 session（context 污染/compaction）
        elif a == "--state-dir": out["state_dir"] = argv[i + 1]; i += 1                  # task 3.3 P0-3：控制面注入 state_dir（SessionStore 统一）
        elif a == "--dry-run": out["dry_run"] = True
        elif a in ("-h", "--help"): out["help"] = True
        i += 1
    return out


HELP = """dev-agent.py — 项目推进流水线·控制面标准执行器（ADR-0005）
用法: python dev-agent.py --prd <prd.md> [--source <signal.md>] [--base main] [--dry-run]
  （cwd 必须是被控仓 worktree；本脚本就地操作该仓）
  --prd            PRD 文件路径（必填，来自控制面 pa-prd）
  --source         触发该任务的信号文件（可选，附给 dev agent 做上下文）
  --base           分支基点（默认 main；branch protection 下永不直推主干）
  --feedback-artifact  上轮 verify 反馈 artifact path（driven 模式 retry 用；baseline 不传）
  --lessons-artifact   prior-PRD 经验 lesson block path（learning memory injection；baseline 不传）
  --dry-run        只跑到"改完代码 + 本地 test"，不 commit/push/开 PR
退出码: 0 OK | 10 无/读不到 PRD | 11 SDK 失败 | 12 stalled | 13 git/PR 失败 | 99 未捕获"""


def git(args: list[str]) -> str:
    """跑 git（arg 数组，无 shell 注入）；非零抛异常，带 stderr。"""
    try:
        r = subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=120)
        if r.returncode != 0:
            raise RuntimeError(sanitize((r.stderr or "").strip() or f"git {args[0]} 退出 {r.returncode}"))   # C2：stderr 脱敏（抹 token）后进异常消息，防落 JSON error/run log 泄密钥
        return r.stdout.strip()
    except FileNotFoundError:
        raise RuntimeError("git 不在 PATH")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"git {args[0]} 超时")


def gh(args: list[str]) -> str:
    try:
        r = subprocess.run(["gh", *args], cwd=REPO_ROOT, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=60)
        if r.returncode != 0:
            raise RuntimeError(sanitize((r.stderr or "").strip() or f"gh {args[0]} 退出 {r.returncode}"))   # C2：stderr 脱敏（抹 token）后进异常消息
        return r.stdout.strip()
    except FileNotFoundError:
        raise RuntimeError("gh 不在 PATH")


def stamp() -> str:
    d = datetime.now()
    return f"{d.year}{d.month:02d}{d.day:02d}-{d.hour:02d}{d.minute:02d}"


def read_text(p: str) -> str | None:
    try:
        return Path(p).read_text(encoding="utf-8")
    except Exception:
        return None


def trunc(s, n: int) -> str:
    x = "" if s is None else str(s)
    return x if len(x) <= n else x[:n] + "…[trunc]"


def git_diff_stat() -> str:
    try:
        out = git(["diff", "--stat"])
        lines = [l for l in out.splitlines() if l.strip()]
        return " | ".join(lines[-3:])[:200]
    except Exception:
        return ""


def classify_test_result(text: str) -> str | None:
    """识别测试结果红/绿（文本 fallback，跨语言）。
    python: OK/FAILED/passed/failed；node(mocha/jest/vitest): passing/failing/passed/failed。"""
    t = (text or "").lower()
    has_fail = bool(re.search(r"failed|failing|error|traceback|\bfail\s+[1-9]", t))  # fail 0 不算红
    has_pass = bool(re.search(r"ok\b|passing|passed|\bpass\s+[1-9]|\btests?\s+passed", t))
    if has_fail: return "red"
    if has_pass: return "green"
    return None


def classify_test_exit(tool_result: ToolResultBlock, text: str) -> str | None:
    """判定一次干净 Bash 测试的红/绿。优先 is_error（=退出码≠0），
    避大输出被截断后文本解析失效。is_error 缺失 → 文本 fallback。"""
    if getattr(tool_result, "is_error", None) is True: return "red"
    if getattr(tool_result, "is_error", None) is False: return "green"
    return classify_test_result(text)


def is_clean_test_cmd(cmd: str) -> bool:
    """判定 Bash command 是否为「干净」的测试运行（SPEC #27：捕获端过滤）。
    跟踪无管道/重定向/链式的裸测试命令——这类 tool_result 的 exit code 才可信；
    带 |>&; 的变体结果无意义。跨语言：python tests / pytest / unittest / npm test / npx|yarn。"""
    c = (cmd or "").strip()
    if re.search(r"[|>&;]", c):
        return False
    pat_py = r"^\s*(\S*/)?python\d?(\.\d+)?\s+(tests/|src/.*run_|-\s*m\s+(pytest|unittest)\b)"
    pat_pt = r"^\s*(\S*/)?pytest\b"
    pat_npm = r"^\s*(\S*/)?(npm\s+(test|run\s+(test|spec))|npx\s+(mocha|jest|vitest)|yarn\s+(test|test:unit))\b"
    return bool(re.match(pat_py, c) or re.match(pat_pt, c) or re.match(pat_npm, c))


def append_run_line(run_log: Path, obj: dict) -> None:
    try:
        run_log.parent.mkdir(parents=True, exist_ok=True)
        with run_log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")
    except Exception as e:
        sys.stderr.write(f"⚠ run 落盘失败（忽略）: {e}\n")


def create_loop_state(run_log: Path) -> dict:
    return {"run_log": run_log, "turn": 0, "last_test": None, "last_test_cmd": None,
            "no_write_streak": 0, "stalled": False, "pending_test_ids": set(),
            "test_evidence": None}   # OpenSpec verified-dev-execution：结构化测试证据（None=未采集）


async def process_dev_loop(messages, state: dict) -> ResultMessage | None:
    """dev loop 循环体（SPEC #27：监控落盘 + 无进展刹车 + 配对 tool_result 拿红/绿）。"""
    result_msg: ResultMessage | None = None
    async for msg in messages:
        if isinstance(msg, AssistantMessage):
            state["turn"] += 1
            blocks = msg.content or []
            has_write = False
            tool_uses = []
            for b in blocks:
                if isinstance(b, TextBlock):
                    sys.stderr.write(f"[dev] {b.text}\n")
                elif isinstance(b, ToolUseBlock):
                    name = b.name or "?"
                    inp = b.input or {}
                    target = (inp.get("file_path") or inp.get("path") or inp.get("notebook_path")
                              or (inp.get("command", "").split("\n")[0] if isinstance(inp.get("command"), str) else "")
                              or "")
                    tool_uses.append({"name": name, "target": trunc(target, 120),
                                      "input": trunc(json.dumps(inp, ensure_ascii=False), INPUT_TRUNC)})
                    if name in WRITE_TOOLS: has_write = True
                    if name == "Bash" and isinstance(inp.get("command"), str):
                        if is_clean_test_cmd(inp["command"].strip()):
                            state["pending_test_ids"].add(b.id)   # 等下个 user msg 配对 tool_result
                            state["last_test_cmd"] = inp["command"].strip()   # 记最后一次干净 test 命令，上报给 dispatch 重放
            if has_write:
                state["no_write_streak"] = 0
                # OpenSpec verified-dev-execution：绿后又发生候选写 → 证据过期（mark_stale 返回新副本，None 原样）
                if state["test_evidence"] is not None:
                    state["test_evidence"] = mark_stale(state["test_evidence"])
            elif state["last_test"] == "red": state["no_write_streak"] += 1
            append_run_line(state["run_log"], {
                "turn": state["turn"], "tool_use": tool_uses, "diff_stat": git_diff_stat(),
                "test": state["last_test"], "verified_red": state["last_test"] == "red",
                "no_write_streak": state["no_write_streak"],
            })
            if state["last_test"] == "red" and state["no_write_streak"] >= N_STALL:
                state["stalled"] = True
                sys.stderr.write(f"🧯 stalled：验证红后连续 {state['no_write_streak']} 轮无写类进展，主动刹车\n")
                break
        elif isinstance(msg, UserMessage):
            blocks = msg.content or []
            if blocks:
                sys.stderr.write(f"[dev] ← user msg（{len(blocks)} blocks）\n")
            for b in blocks:
                if isinstance(b, ToolResultBlock) and b.tool_use_id in state["pending_test_ids"]:
                    txt = b.content if isinstance(b.content, str) else json.dumps(b.content or "", ensure_ascii=False)
                    res = classify_test_exit(b, txt)
                    if res:
                        state["last_test"] = res
                        # OpenSpec verified-dev-execution：记录结构化证据（整体替换为最新测试，fresh=True）
                        state["test_evidence"] = TestEvidence(
                            command=state.get("last_test_cmd") or "",
                            exit_code=0 if res == "green" else 1,
                            completed_at=stamp(), fresh=True)
                        sys.stderr.write(f"[dev] test → {res}（is_error={getattr(b,'is_error',None)}, {len(txt)} chars）\n")
                    else:
                        sys.stderr.write(f"[dev] test 结果未识别（is_error={getattr(b,'is_error',None)}, {len(txt)} chars）\n")
                    state["pending_test_ids"].discard(b.tool_use_id)
        elif isinstance(msg, ResultMessage):
            result_msg = msg
    return result_msg


def build_env_for_sdk() -> dict:
    """构造 SDK 子进程 env：把启动本脚本的 python 的 bin 目录 + ~/.local/bin（claude CLI）
    前置进 PATH。dev-agent.py 由被控仓 runtime 的 python 启动（Node 仓用系统 python3、
    Python 仓用 conda env python）→ 其 bin 目录前置后，agent 的裸 python 落在该 runtime。"""
    env = dict(os.environ)
    prefix = []
    py_dir = str(Path(sys.executable).resolve().parent)
    if py_dir: prefix.append(py_dir)
    local_bin = str(Path.home() / ".local" / "bin")
    if local_bin: prefix.append(local_bin)
    env["PATH"] = os.pathsep.join(prefix) + os.pathsep + env.get("PATH", "")
    return env


def build_prompt(args: dict, prd_text: str, branch: str | None) -> str:
    """通用 dev 守则 prompt（ADR-0005 B1 决策）。
    仓特定知识（目录/测试入口/保护路径/env 名）**不在此**——由各仓根 CLAUDE.md 承载，
    SDK 经 setting_sources=["project"] 自动加载。本 prompt 只放跨仓一致的守则。"""
    base = args["base"]
    dry = args["dry_run"]
    head = ("你是本仓的自治 dev agent。**dry-run 模式**：在当前工作区跑，"
            "不切分支 / 不 commit / 不 push，改动留工作树供 review。"
            if dry else
            f"你是本仓的自治 dev agent。当前分支 {branch}（基点 {base}）。")
    prot = "" if dry else f"主干 {base} 有 branch protection——你永远只在这个 feature 分支上干活，绝不直推主干。"
    source_block = ""
    if args["source"]:
        src = read_text(args["source"]) or "(读不到 source)"
        source_block = f"\n\n## 触发信号（来自控制面 pa-radar）\n```\n{src}\n```"
    # task 3.4：driven 模式 retry 反馈源——PRD 不可变（task 3.2 摘除追加），上轮 verify 反馈落 journal
    # content-addressed artifact，经控制面 --feedback-artifact 传入；baseline（无此参）照旧读 PRD 反馈节。
    feedback_block = ""
    if args.get("feedback_artifact"):
        fb = read_text(args["feedback_artifact"]) or "(读不到 feedback artifact)"
        feedback_block = f"\n\n## ⚠️ 上轮 verify 反馈（journal artifact；driven 模式 PRD 不可变，按此落实后重做）\n```\n{fb}\n```"
    # add-cross-prd-learning-memory Section 5 接线：lesson block（控制面检索的 prior-PRD 经验）经
    # ``--lessons-artifact`` path 传入（与 feedback_artifact 完全对称）。ADR-0001 控制面/目标面隔离：
    # dev-agent **只读传入的 path**，不读控制面 state；lesson_block 是 sanitized markdown checklist
    # （仅 trigger/action/boundary，无 evidence/叙事）。baseline / 读不到 → 空（inject_into_prompt no-op）。
    lessons_block = ""
    if args.get("lessons_artifact"):
        lb = read_text(args["lessons_artifact"])
        if lb and lb.strip():
            lessons_block = lb
    # inject_into_prompt（learning_memory_retrieval）：lesson_block 非空 → append 到 prompt 末尾；
    # 空 → identity no-op（baseline prompt byte-identical，design 决策#7 fail-open）。
    base_prompt = "\n".join([
        head,
        prot,
        "",
        "## 你的任务（PRD）",
        prd_text,
        source_block,
        feedback_block,
        "",
        "## 仓特定守则（必读）",
        "本仓的目录结构 / 测试入口 / 受保护路径 / 环境名等仓特定知识，详见仓库根 `CLAUDE.md`",
        "（已由 SDK setting_sources=project 自动加载）。**开工前先读 CLAUDE.md**，遵守其中的目录约定与测试方式。",
        "",
        "## 通用 dev 守则（所有仓一致）",
        "1. 只在当前 feature 分支、本仓范围内改；遵循 CLAUDE.md 的目录约定。",
        "2. 改完必须跑本仓的测试——**怎么测你自治决定**（读 CLAUDE.md / 探测试入口）。**绿才算完事**：",
        "   红（test 失败 / import 错 / raise）= 没做完，修到绿或回滚，绝不留红提交。",
        "3. 遵循既有代码风格；依赖只通过本仓既有的依赖管理方式增删。",
        "4. commit 用 conventional commits，只 commit 与 PRD 相关的改动。",
        "5. 不要 push、不要开 PR——push/PR 由本脚本在你停下后代办。",
        "6. 不碰 .github/ branch protection、不 force push、不删分支、不 publish。",
        "",
        "## 何时用子代理分工（Agent 工具）",
        "满足任一才考虑分工，否则单干（子代理有独立 context 成本，别为分工而分工）：",
        "- PRD 横跨多个独立关注点（新增功能 + 补测试 + 审类型，可拆开）；",
        "- 估摸单干要 30+ turn 或读改 5+ 文件。",
        "分工纪律：用 Agent 工具 spawn 子代理，每个单一职责；给子代理明确目标 + 限定文件范围；",
        "子代理产出回你整合 + 跑本仓测试验证整体；commit/push 只由你（parent）守。",
        "",
        "现在：读 CLAUDE.md → 读 PRD → 规划 → 改代码 → 跑本仓测试 → 到「可提交且 test 绿」即停。",
        "停下前用一段话总结：改了什么 / test 结果 / 遗留风险。",
    ])
    # add-cross-prd-learning-memory Section 5：lesson_block append（空=identity no-op，baseline prompt）
    #   控制面/目标面隔离（ADR-0001）：lesson_block 来自控制面 ``--lessons-artifact``，仅 sanitized
    #   markdown checklist（trigger/action/boundary）。dev-agent 不读控制面 state，不改 dispatch 主路径语义。
    if lessons_block:
        try:
            from learning_memory_retrieval import inject_into_prompt as _inject
            return _inject(base_prompt, lessons_block)
        except Exception:
            # 兜底：learning_memory_retrieval 不可用（不应发生——纯 stdlib）→ 原样返 baseline（fail-open）
            return f"{base_prompt}\n\n{lessons_block}"
    return base_prompt


async def _can_use_tool(
    tool_name: str, tool_input: dict, _context
) -> PermissionResultAllow | PermissionResultDeny:
    """ADR-0006 #7 长效修法：SDK can_use_tool 权限闸（摆脱机器本地 settings 依赖）。

    - 非 Bash 工具：直接放行（可用性已由 ClaudeAgentOptions.tools= 硬白名单兜底；
      acceptEdits 另自动批 Edit/Write，本回调实际主要落到 Bash）。
    - Bash：经 bash_allowlist.decide_bash 判定——默认拒，仅放行测试/构建/VCS/只读族，
      显式拒网络外传与破坏性操作。拒绝时回写 deny message 给 dev，并 stderr 留审计点。

    背景：acceptEdits 不自动批 Bash，历史靠各仓 gitignored 的 .claude/settings.local.json
    放行，worktree（尤其 /tmp 或跨机新克隆）摸不到 → headless 下 python/pytest 被拦死、
    test_passed=false。本闸把放行规则收敛进控制面单一源头，任意 worktree 摆放都确定性可跑。
    """
    if tool_name != "Bash":
        return PermissionResultAllow()
    command = (tool_input or {}).get("command", "")
    allowed, reason = decide_bash(command)
    if allowed:
        return PermissionResultAllow(updated_input=tool_input)
    sys.stderr.write(f"[权限闸] 拦截 Bash: {reason}\n")
    return PermissionResultDeny(message=f"[dev-agent 权限闸] {reason}")


async def main() -> int:
    args = parse_args(sys.argv[1:])
    if args["help"]:
        print(HELP); return 0
    if not args["prd"]:
        sys.stderr.write("✗ 缺 --prd（控制面投递的 PRD）\n" + HELP + "\n"); return 10

    prd_text = read_text(args["prd"])
    if prd_text is None:
        sys.stderr.write(f"✗ 读不到 PRD: {args['prd']}\n"); return 10

    base = args["base"]
    slug = dev_slugify(Path(args["prd"]).stem)
    s = stamp()                      # task 2.3b：一次 stamp，coordinator/branch/run_log 复用（stamp() 含时分不可预测，多次调用会漂）
    branch = f"{args['branch_prefix']}/{s}-{slug}"

    # 1. 建 feature 分支（dry-run 不切，改动留工作树）
    if not args["dry_run"]:
        try:
            git(["checkout", "-b", branch, base])
        except Exception as e:
            sys.stderr.write(f"✗ 建分支失败: {e}\n"); return 13

    # 2. 组 prompt
    prompt = build_prompt(args, prd_text, None if args["dry_run"] else branch)

    # 3. SDK dev loop（acceptEdits + settingSources + 硬白名单 tools，对齐历史 mjs/py / SPEC §决策#23）
    run_log = STATE_RUNS_DIR / f"{branch.replace('/', '-')}-{s}.jsonl"
    state = create_loop_state(run_log)
    # task 2.3b：coordinator 边界（design 决策#1）——一次解析 loop flag + own journal/artifacts/IDs；
    # lifecycle_hooks 开 → build_dev_hooks 注册真实 SDK lifecycle hooks（PreToolUse/Stop/...），
    # 关 → sdk_hooks=None（baseline，dev-agent 行为零变化，design 决策#8）。
    # task 3.3 P0-3：state_dir 优先用控制面注入（=run_daily STATE_DIR）→ dev-agent session_store 与控制面
    #   coordinator 同一 SessionStore（retry 读到 dev-agent 持久化的 SDK session_id）；缺省=worktree/state（baseline）
    _state_dir = args["state_dir"] or str(REPO_ROOT / "state")
    _coord = build_coordinator(stamp=s, prd_path=args["prd"], proj=REPO_ROOT.name,
                               slug=slug, state_dir=_state_dir,
                               env=dict(os.environ))
    _dev_adapter, sdk_hooks = build_dev_hooks(_coord)
    result_msg: ResultMessage | None = None
    try:
        # ADR-0006 #7（migrate-dev-agent-streaming-with-1106-patch）：本地 ast 变异根治 #1105。
        # sdk_compat_patch.apply() 在真实 Query.wait_for_result_and_end_input 保活条件末位加
        # can_use_tool（#1106 未合前的定向补丁）；detection 偏离即 raise RuntimeError → 被 main 的
        # except 捕获 → exit 11（fail-loud：dispatch 标红→人介入），严禁单独 try/except 吞掉。
        # apply 幂等 + conftest session fixture 已在测试 session 打过，此处生产路径再打是 no-op。
        sdk_compat_patch.apply()
        options = ClaudeAgentOptions(
            cwd=str(REPO_ROOT),
            # model 刻意省略 → 走 roc 代理默认（glm-5.2）；勿传裸 Anthropic model id
            permission_mode="acceptEdits",          # 编辑类自动过；Bash 不自动批 → 走 can_use_tool 闸（下）
            can_use_tool=_can_use_tool,             # ADR-0006 #7：Bash 放行长效修法，摆脱机器本地 settings 依赖
            setting_sources=["project"],            # 加载仓 CLAUDE.md(仓特定守则) + .claude/hooks
            tools=["Read", "Grep", "Glob", "Edit", "Write", "MultiEdit",
                   "TodoWrite", "Bash", "Agent"],   # 硬白名单（Python SDK：tools=可用性限制；allowed_tools 仅批准列表，非白名单）
            # ⚠ follow-up（canary 2026-07-20 实证）：max_turns/max_budget_usd 在 SDK 0.2.121 似被绕过
            #   （canary 实跑 325 turn 远超 150、total_cost_usd=None 未报成本）——实际仅 SPEC #27 stall
            #   刹车兜住失控。budget/turn 硬执行 + SDK cost 上报机制排查列为 follow-up（ADR-0006 #6）。
            max_turns=150,
            max_budget_usd=MAX_BUDGET,               # 预算刹车（降级兜底，非硬保证——见上 follow-up）
            env=build_env_for_sdk(),                 # PATH 前置 runtime python + claude CLI
            hooks=sdk_hooks,                          # task 2.3b：lifecycle_hooks 开→真实 SDK lifecycle hooks（None=baseline，design 决策#8）
            # task 3.3：session-aware retry 接入生产执行路径
            #   retry 同 session → resume=<session_id>（SDK ResultMessage.session_id，transient/agent 中断续传）；
            #   context 污染/compaction → fork_session=True（new_session 从 parent session 派生）；
            #   均无 → 新 session（seq=0 baseline）。design 决策#3：transient+session 可用→resume；context 污染→new_session。
            resume=args["resume_session"],
            fork_session=args["fork_session"] or None,
        )
        # can_use_tool 回调要求 streaming 模式（SDK 0.2.x：_internal/client.py:103 见 prompt 为
        # str 即 raise「can_use_tool callback requires streaming mode」）。string prompt 经
        # prompt_stream() 包成单条 user 消息异步流（dict 对齐 _internal/client.py:214-219）。
        # 实现抽到零依赖模块 prompt_stream.py 以便单测锁定 dict 结构（ADR-0006 #7）。
        result_msg = await process_dev_loop(query(prompt=prompt_stream(prompt), options=options), state)
    except Exception as e:
        sys.stderr.write(f"✗ SDK dev loop 异常: {e}\n"); return 11

    # task 3.3：持久化 SDK session_id 到 coordinator SessionStore（resume/fork/new-session 决策消费）。
    # retry（seq>0）→ next_iteration(seq) 衍生 distinct iteration（revise/resume/fork/new-session 各自 iteration）；
    # seq=0 → build_coordinator 初始 iteration。session_id 缺失 → RetryPolicy fallback new_session（design risk#91）。
    _iter_for_session = (_coord.next_iteration(args["iteration_seq"])
                         if args["iteration_seq"] > 0 else _coord.iteration_id)
    if result_msg and getattr(_coord, "session_store", None):
        from session_meta import SessionMeta, ResultSubtype
        _subtype = ResultSubtype.ERROR if getattr(result_msg, "is_error", False) else ResultSubtype.SUCCESS
        _coord.session_store.save(SessionMeta(
            iteration_id=_iter_for_session,
            session_id=getattr(result_msg, "session_id", None),
            result_subtype=_subtype))

    cost = getattr(result_msg, "total_cost_usd", None) if result_msg else None
    turns = getattr(result_msg, "num_turns", None) if result_msg else state["turn"]

    if cost in (None, 0):
        sys.stderr.write(f"⚠ 预算刹车未生效（total_cost_usd={cost}），仅 maxTurns 兜底\n")
    else:
        sys.stderr.write(f"💰 本次 cost=${cost}（maxBudgetUsd={MAX_BUDGET}）\n")

    # stalled：不 commit/不开 PR，吐 JSON + exit 12（SPEC #27；半成品靠 run_log 留痕）
    if state["stalled"]:
        print(json.dumps({"ok": False, "stalled": True, "branch": branch, "base": base,
                          "run_log": str(run_log), "cost": cost, "turns": turns, "test_cmd": state.get("last_test_cmd"), "test_passed": state.get("last_test") == "green"}, ensure_ascii=False))
        return 12

    if result_msg and result_msg.is_error:
        sys.stderr.write(f"✗ dev loop 返回错误: {result_msg.result}\n"); return 11

    # 4. dry-run：到此为止
    if args["dry_run"]:
        print(json.dumps({"ok": True, "dry_run": True, "branch": branch, "base": base,
                          "cost": cost, "turns": turns, "test_cmd": state.get("last_test_cmd"), "test_passed": state.get("last_test") == "green", "run_log": str(run_log),
                          "result": getattr(result_msg, "result", None) if result_msg else None},
                         ensure_ascii=False))
        return 0

    # 4.5 发布硬门（OpenSpec verified-dev-execution）：无新鲜绿色证据 → 不 commit/push/PR。
    # 宁拦勿错放：dev 自治产出的「绿」必须落到结构化证据、且采集后无后续候选写，才允许发布动作。
    verdict, reason = evaluate_gate(state.get("test_evidence"))
    if verdict != GATE_PUBLISH:
        ev = state.get("test_evidence")
        gate_status = verdict   # test_not_run | test_failed | test_stale
        sys.stderr.write(f"🚫 发布门拦截（{gate_status}）: {reason}\n")
        print(json.dumps({"ok": False, "blocked_by_gate": True, "gate_status": gate_status,
                          "gate_reason": reason, "branch": branch, "base": base,
                          "cost": cost, "turns": turns,
                          "test_cmd": (ev.command if ev else state.get("last_test_cmd")),
                          "test_status": ("green" if (ev and ev.exit_code == 0) else ("red" if ev else "none")),
                          "evidence_fresh": bool(ev and ev.fresh)}, ensure_ascii=False))
        return 14

    # 5. commit + push + 开 PR（branch protection 要求走 PR）
    try:
        git(["add", "-A"])
        diff_stat = git(["diff", "--cached", "--stat"])
        if not diff_stat:
            print(json.dumps({"ok": True, "no_changes": True, "branch": branch,
                              "base": base, "cost": cost, "turns": turns, "test_cmd": state.get("last_test_cmd"), "test_passed": state.get("last_test") == "green"}, ensure_ascii=False))
            return 0
        title = Path(args["prd"]).stem
        git(["commit", "-m", f"feat(pa-dev): {title}",
             "-m", f"项目推进流水线 dev-agent（控制面标准执行器，ADR-0005）自治产出（基点 {base}）。"])
        git(["push", "-u", "origin", branch])
        pr_url = gh(["pr", "create", "--base", base, "--head", branch,
                     "--title", f"pa-dev: {title}",
                     "--body", "自治 dev agent 产出（PRD 见任务投递）。验证闸（dev-agent 自治）已绿。"])
        ev = state.get("test_evidence")
        # 发布门已放行 → test_passed=True 现为可证（新鲜绿色证据），并补结构化 gate/test 字段（2.5 truthful JSON）
        print(json.dumps({"ok": True, "branch": branch, "base": base, "pr_url": pr_url,
                          "cost": cost, "turns": turns, "test_cmd": state.get("last_test_cmd"),
                          "test_passed": True, "test_status": "green",
                          "gate_status": GATE_PUBLISH, "evidence_fresh": bool(ev and ev.fresh)}, ensure_ascii=False))
        return 0
    except Exception as e:
        sys.stderr.write(f"✗ git/push/PR 阶段失败: {e}\n")
        print(json.dumps({"ok": False, "error": str(e), "branch": branch,
                          "base": base, "cost": cost, "turns": turns, "test_cmd": state.get("last_test_cmd"), "test_passed": state.get("last_test") == "green"}, ensure_ascii=False))
        return 13


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(99)
    except Exception as e:
        sys.stderr.write(f"✗ 未捕获异常: {e}\n")
        sys.exit(99)
