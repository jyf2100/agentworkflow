#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_daily.py — 项目推进流水线·编排器【前半段】（SPEC §6，Phase-2）

For future Claude：控制面 cron 编排器。Phase-2 只实现前半段：
    pa-radar（今日新→技术信号→candidates）
    → pa-prd（信号×profile→PRD，含可验证验收标准）
    → pa-prd-critic（对抗质量闸，pass/drop/revise + 1 次修订回环）
后半段（pa-dispatch 投递目标仓 dev-agent + 独立验证）见 Phase-3。

调用机制（Phase-0 已实测验证，见 SPEC §3.2 关键假设）：
    claude --agent <persona> -p "<prompt>" --output-format json --max-turns N
    → stdout 是信封 JSON：{is_error, result:"<persona 吐的单行 JSON 字符串>",
                            total_cost_usd, num_turns, session_id, modelUsage}
    → 两层解析：json.loads(stdout)["result"] 再 json.loads 一次得 payload。
    模型省略 → 走 roc LiteLLM 代理默认（glm-5.2）；切勿传裸 Anthropic model id。

职责切分（关键）：
    - 机械活（今日新文件发现、marker、文件读写、去重清单拉取）→ Python，确定性，零 LLM。
    - 语义活（抽信号 / 翻译 PRD / 对抗审 PRD）→ 各 headless persona。
    幂等：date-marker 仅快路径；真幂等靠 state 产物存在性（断点续跑）+ Phase-3 的 GitHub 去重（ADR-0004）。

用法：
    python3 run_daily.py                          # 跑全段（radar→prd→critic）
    python3 run_daily.py --limit 2                # dry-run 封顶今日新内容 2 篇
    python3 run_daily.py --dry-run                # 不 bump marker
    python3 run_daily.py --from-stage prd         # 从 prd 段续跑（radar 产物已存在则复用）
    python3 run_daily.py --stamp 20260711         # 指定日期（默认今天）
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path

from slug_utils import dev_slugify   # ADR-0006 #5：分支 slug 单一源头（消解 ADR-0004 #4 shadow；无依赖模块，顶部 import 不触发 sdk 连带加载拖垮 cron）

try:
    import yaml
except ImportError:
    sys.exit("✗ 缺 PyYAML：pip3 install pyyaml")

# ─── 路径常量（编排器须从 vault 根运行）──────────────────────────────
VAULT_ROOT = Path(__file__).resolve().parents[3]   # .../日常工作
PROJECT_DIR = Path(__file__).resolve().parents[1]  # .../项目推进流水线
PA_HOME = VAULT_ROOT / ".project-auto"
STATE_DIR = PA_HOME / "state"
PROFILES_DIR = PA_HOME / "profiles"
SOURCES_FILE = PA_HOME / "sources.yaml"
REPORT_DIR = VAULT_ROOT / "项目推进"                          # §8 报告落点（vault 根）
DAILY_DIR = VAULT_ROOT / "日报"                               # §8 日报指针落点
SMTP_SEND = PROJECT_DIR / "scripts" / "smtp_send.py"          # §8 SMTP 直发 helper（已落地，Keychain 取密）

# claude 二进制：env 覆盖 > PATH > nvm 已装版本（cron 非 login shell 无 nvm PATH 的兜底）。
# 版本无关 glob（避免硬编码 vXX.Y.Z：macOS/Linux 的 node 版本号不同）。


def _nvm_claude() -> Path | None:
    """从 nvm 已装的 node 版本里找 claude（取版本号最大的那个）；无则 None。"""
    base = Path.home() / ".nvm/versions/node"
    if not base.is_dir():
        return None
    cands = sorted(base.glob("v*/bin/claude"))
    return cands[-1] if cands else None

# 每段 wall-clock 超时（秒）——防挂死，非成本控制（SPEC §6/R2）
# verify=pa-verify 裁判段（dev 产出验证闸，docs/verify-commit-loop-design.md §5-②）
TIMEOUT = {"fetch": 1500, "radar": 600, "prd": 900, "critic": 300, "verify": 300}
MAX_TURNS = {"fetch": 40, "radar": 60, "prd": 90, "critic": 40, "verify": 30}

STAGES = ["fetch", "radar", "prd", "inject", "critic", "dispatch", "report"]

# dispatch 段 wall-clock（不调 claude persona；触发目标仓 dev-agent.mjs + 独立 npm 验证）
DEV_LOOP_TIMEOUT = 3600          # 触发 dev-agent.mjs（SDK maxTurns 150）外包超时（≈60min；maxTurns 在 glm-5.2 下约 12-37min，原 30min 偏紧会误杀正常 dev loop）
VERIFY_INSTALL_TIMEOUT = 600     # 独立验证 npm ci
VERIFY_TEST_TIMEOUT = 600        # 独立验证 npm test
VERIFY_MAX_ROUNDS = 2            # pa-verify 判红增量重做机会（verify 闭环，docs/verify-commit-loop-design.md §3-③）
CONDA_ENVS_DIR = Path("/home/ubuntu/miniconda3/envs")   # conda env 根（控制面主机固定）；Python 仓 dev-agent.py / 独立验证闸用

# Phase 4：run 级互斥 + 并行 + 幂等前置闸（SPEC #30 / ADR-0004 §4）
MAX_RUN_WALL = DEV_LOOP_TIMEOUT + 1800     # run 锁陈旧阈值（≈90min）：PID 失活 OR 锁龄超此 → 自动接管
RUN_LOCK = STATE_DIR / ".run.lock"         # run 级互斥锁（cron ↔ wka，包整个 main）
LOG_LOCK = threading.Lock()                # log() 线程安全（多 worker 并发 print 不交错）
DISPATCH_LOCKS: dict[str, threading.Lock] = {}   # per-owner_repo 串行锁（修 count_inflight_prs TOCTOU；同仓串行/跨仓并行）


# ─── 基础工具 ────────────────────────────────────────────────────────
def log(msg: str) -> None:
    with LOG_LOCK:
        print(msg, flush=True)


def resolve_claude_bin() -> str:
    env = os.environ.get("PA_CLAUDE_BIN")
    if env and Path(env).is_file():
        return env
    p = shutil.which("claude")
    if p:
        return p
    nvm = _nvm_claude()
    if nvm and nvm.is_file():
        return str(nvm)
    sys.exit(f"✗ 找不到 claude CLI（试 PA_CLAUDE_BIN 环境变量或装 Claude Code）")


def today_stamp() -> str:
    return date.today().strftime("%Y%m%d")


def expand_braces(pattern: str) -> list[str]:
    """把 glob 里的 {a,b} 展开成多个 pattern（pathlib glob 不支持 brace）。"""
    m = re.search(r"\{([^}]+)\}", pattern)
    if not m:
        return [pattern]
    opts = m.group(1).split(",")
    return [pattern[: m.start()] + o + pattern[m.end() :] for o in opts]


# ─── 配置加载 ────────────────────────────────────────────────────────
def load_sources() -> list[dict]:
    with open(SOURCES_FILE, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    sources = data["sources"]
    # 采集源身份（ADR-0007 决定 #4）：name 唯一 + root 排他（一个 root 只属一个源）。
    # 杜绝双扫 / marker 互相污染 / candidate 重复。重复即拒载（硬错，不静默）。
    seen_names: set[str] = set()
    seen_roots: set[str] = set()
    for src in sources:
        name = src.get("name")
        root = src.get("root")
        if name in seen_names:
            sys.exit(f"✗ sources.yaml 采集源 name 重复：{name}（name 必须唯一）")
        seen_names.add(name)
        if root in seen_roots:
            sys.exit(f"✗ sources.yaml root 被多源共用：{root}（一个 root 只属一个采集源）")
        seen_roots.add(root)
        src.setdefault("kind", "directory")   # 缺省 directory（消费侧零分支，决定 #2）
        # fetcher 声明了但脚本不存在 → warn 不阻断（消费侧只看目录；未实现 kind 的源今日 0 产出、订阅项目不调 radar）
        fetcher = src.get("fetcher")
        if fetcher and not (PROJECT_DIR / fetcher).is_file():
            log(f"⚠ [sources] {name} 声明 fetcher={fetcher} 但脚本不存在（未实现？）——"
                f"该源今日无产出、其订阅项目不调 radar（静默，不阻断）")
    return sources


def load_profiles() -> dict:
    profiles = {}
    for p in sorted(PROFILES_DIR.glob("*.yaml")):
        with open(p, encoding="utf-8") as f:
            prof = yaml.safe_load(f)
        if prof.get("admission"):
            profiles[prof["name"]] = prof
    return profiles


def read_marker(source: dict) -> str:
    mp = PA_HOME / source["marker"]
    if mp.is_file():
        return mp.read_text(encoding="utf-8").strip()
    return "00000000"


def bump_marker(source: dict, stamp: str) -> None:
    mp = PA_HOME / source["marker"]
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(stamp + "\n", encoding="utf-8")


# ─── 机械：今日新发现 ────────────────────────────────────────────────
def discover_today_new(source: dict, marker_stamp: str, limit: int | None) -> list[Path]:
    root = VAULT_ROOT / source["root"]
    seen: dict[Path, bool] = {}
    for pat in expand_braces(source["content_glob"]):
        for p in root.glob(pat):
            if p.is_file():
                seen[p] = True
    # 排除 meta（审校报告/URL参考列表/文章清单 等）—— exclude_glob 缺省则不排除
    excl = source.get("exclude_glob")
    if excl:
        for pat in expand_braces(excl):
            for p in root.glob(pat):
                seen.pop(p, None)
    out = []
    for p in sorted(seen):
        m = re.match(r"(\d{8})", p.name)
        if not m:
            continue
        if m.group(1) <= marker_stamp:   # 只取 > marker
            continue
        out.append(p)
    if limit:
        out = out[:limit]
    return out


def fetch_dedup_list(profiles: dict) -> dict:
    """各项目未关闭 PR + 在途 PRD slug（喂 radar 去重）。任一失败容忍为空。"""
    dedup: dict[str, list[str]] = {}
    for name, prof in profiles.items():
        items: list[str] = []
        repo = prof.get("repo")
        # 1) 在途 PRD（控制面已产、未投递/未关）
        prd_dir = STATE_DIR / "prd" / name
        if prd_dir.is_dir():
            items += [f"PRD:{p.stem}" for p in prd_dir.glob("*.md")]
        # 2) 目标仓未关闭 PR（gh）
        if repo and Path(repo).is_dir():
            try:
                url = subprocess.run(
                    ["git", "-C", repo, "remote", "get-url", "origin"],
                    capture_output=True, text=True, timeout=15,
                ).stdout.strip()
                m = re.search(r"[:/]([^/]+/[^/]+?)(?:\.git)?$", url)
                if m:
                    owner_repo = m.group(1)
                    js = subprocess.run(
                        ["gh", "pr", "list", "-R", owner_repo, "--state", "open",
                         "--limit", "30", "--json", "title"],
                        capture_output=True, text=True, timeout=20,
                    )
                    for pr in json.loads(js.stdout or "[]"):
                        items.append(f"PR:{pr.get('title', '')}")
            except Exception as e:
                log(f"  ⚠ {name} 拉取未关闭 PR 失败（容忍空）: {e}")
        dedup[name] = items
    return dedup


def _source_of(cand: dict, src_files: list[tuple[str, list[Path]]]) -> str:
    """candidate.source_path（vault 相对）回溯到所属采集源 name；命中不到回 'unknown'。

    一个 candidate 来自一个文件，一个文件只属一个源（root 排他，决定 #4），故首匹配即定。
    src_files = [(source_name, [该源喂进来的绝对路径])]，由 stage_radar 聚合时传入。"""
    sp = cand.get("source_path", "")
    if sp:
        target = str(VAULT_ROOT / sp)
        for sname, files in src_files:
            if any(str(f) == target for f in files):
                return sname
    return "unknown"


# ─── 核心：调 persona ────────────────────────────────────────────────
# glm-5.2 在 headless `-p` 下偶尔不守"只吐 JSON"契约——先散文叙述发现再（或干脆不）吐 JSON。
# 两道防线：① _extract_first_json brace-matching 容忍 JSON 前后的散文/markdown；② 仍失败则重试 1 次（加强 JSON-only 指令）。
_JSON_RETRY_SUFFIX = (
    "\n\n⚠ 上一次输出不是合法 JSON（含散文/解释/markdown）。本次【必须】只输出一个 JSON 对象——"
    "前后不得有任何文字、解释、markdown 代码围栏；直接以 `{` 开头、`}` 结尾。"
)


def _extract_first_json(s: str) -> str | None:
    """容错抽取：从 glm-5.2 的散文叙述里 brace-matching 取第一个完整 {...} 对象。

    跳过 JSON 前的散文 preamble；字符串字面量内的 `{`/`}` 不计深度（防 value 含花括号误判）。
    返回首个闭合的 JSON 子串；若无完整闭合对象（被截断）→ None。"""
    start = s.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return s[start:i + 1]
    return None   # 花括号未配平（输出被截断）


def run_persona(name: str, prompt: str, stage: str, label: str,
                allowed_tools: list[str] | None = None) -> tuple[dict, dict]:
    """调 `claude --agent <name> -p <prompt> --output-format json`，两层解析返回 (payload, meta)。

    内层 result 容错：先严格 json.loads，失败则 _extract_first_json 抽取（容忍散文前后缀）；
    仍失败重试 1 次（拼 _JSON_RETRY_SUFFIX 加强 JSON-only 契约）。两轮均失败才 raise。
    allowed_tools：MCP 工具白名单透传（fetch 段调 exa 必须，--allowedTools 逗号分隔）。"""
    base_cmd = [resolve_claude_bin(), "--agent", name, "--output-format", "json",
                "--max-turns", str(MAX_TURNS[stage])]
    if allowed_tools:
        base_cmd += ["--allowedTools", ",".join(allowed_tools)]
    cur_prompt = prompt
    last_err = "（未知）"
    for attempt in (1, 2):
        cmd = base_cmd + ["-p", cur_prompt]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=TIMEOUT[stage], stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"[{label}] wall-clock 超时（{TIMEOUT[stage]}s），已 kill")
        if proc.returncode != 0:
            raise RuntimeError(f"[{label}] claude 退出 {proc.returncode}: {proc.stderr[-400:].strip()}")
        try:
            outer = json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"[{label}] stdout 非合法 JSON 信封: {e}; 头={proc.stdout[:300]}")
        if outer.get("is_error"):
            raise RuntimeError(f"[{label}] persona is_error: {str(outer.get('result',''))[:300]}")
        inner_str = outer.get("result", "") or ""
        payload = None
        try:                              # ① 严格解析（persona 守约的快路径）
            payload = json.loads(inner_str)
        except json.JSONDecodeError:
            extracted = _extract_first_json(inner_str)   # ② 容错抽取（散文前后缀）
            if extracted:
                try:
                    payload = json.loads(extracted)
                except json.JSONDecodeError as e:
                    last_err = f"抽取后仍非合法 JSON: {e}; 头={inner_str[:200]}"
            else:
                last_err = f"result 中找不到完整 JSON 对象; 头={inner_str[:200]}"
        if payload is not None:
            meta = {
                "cost": outer.get("total_cost_usd"),
                "turns": outer.get("num_turns"),
                "session_id": outer.get("session_id"),
                "duration_ms": outer.get("duration_ms"),
                "model": (outer.get("modelUsage") or {}),
            }
            return payload, meta
        # 本轮非 JSON：加强 JSON-only 指令重试一轮
        log(f"[{label}] persona result 非 JSON（attempt {attempt}/2，容错抽取仍失败）→ 重试并加强 JSON-only 契约")
        cur_prompt = prompt + _JSON_RETRY_SUFFIX
    raise RuntimeError(f"[{label}] persona result 两轮均非合法 JSON: {last_err}")


# ─── 三段 prompt 构造 ────────────────────────────────────────────────
def radar_prompt(project: str, today_new: list[Path], prof: dict, dedup_items: list[str]) -> str:
    """per-project radar prompt（ADR-0007 决定 #5）。

    只含这一个项目的 match_surface + 它订阅到的文件 + 它的去重清单。多源时 stage_radar 按项目各调一次，
    避免单源混喂让无关项目白读（现状 ashare 被 wechat 文件淹没、全进低分桶）。"""
    files = "\n".join(f"- {p.relative_to(VAULT_ROOT)}" for p in today_new)
    ms = prof.get("match_surface", {})
    surf = f"- {project}: one_liner=\"{ms.get('one_liner','')}\" keywords={ms.get('keywords',[])}"
    dd_block = "\n".join(dedup_items) if dedup_items else "（无未关闭 PR / 在途 PRD）"
    return f"""今日新内容文件（共 {len(today_new)} 篇，逐篇 Read 后抽技术信号，只针对项目【{project}】）：
{files}

白名单项目 match_surface：
{surf}

去重清单（命中则丢弃该信号）：
{dd_block}

按你的 persona 输出契约：只吐一行 JSON（candidates 数组，relevance<0.5 丢弃，每条带 source_path）。"""


def prd_prompt(candidates: list[dict], profiles: dict, stamp: str,
               revise: dict | None = None) -> str:
    import json as _j
    prof_block = []
    names = {c.get("project") for c in candidates}
    for name in names:
        if name in profiles:
            prof = profiles[name]
            prof_block.append(
                f"- {name}: goal=\"{prof.get('goal','')}\" tech_stack={prof.get('tech_stack',[])} "
                f"current_focus={prof.get('current_focus',[])} "
                f"match_surface.one_liner=\"{prof.get('match_surface',{}).get('one_liner','')}\""
            )
    cand_block = _j.dumps(candidates, ensure_ascii=False, indent=2)
    head = (
        f"今天是 {stamp}。按你的 persona 输出契约：为每条 candidate 把信号翻译成项目专属 PRD"
        f"（含可验证验收标准），Write 到 .project-auto/state/prd/<project>/{stamp}_<slug>.md，"
        f"再吐一行 manifest JSON。"
    )
    if revise:
        head = (
            f"这是 pa-prd-critic 打回的【revise 轮 round=2】。今天是 {stamp}。"
            f"对下面这份 PRD 按 revisions_needed 修订后重写到原路径（frontmatter round=2），"
            f"不要重写无关部分，再吐一行 manifest JSON（只含这一份）。\n"
            f"待修订 PRD 路径：{revise['prd_path']}\n"
            f"revisions_needed：\n" + "\n".join(f"- {r}" for r in revise["revisions_needed"])
        )
    return f"""{head}

candidates / 上下文：
{cand_block}

涉及项目 profile：
{chr(10).join(prof_block) or '（见 candidate.project）'}"""


def critic_prompt(prd_path: str, source_path: str, prof: dict) -> str:
    ms = prof.get("match_surface", {})
    return f"""待过闸 PRD：{prd_path}
信息源原文：{source_path}
项目 profile：name={prof.get('name')} goal="{prof.get('goal','')}" match_surface.one_liner="{ms.get('one_liner','')}"

按你的 persona 输出契约：Read PRD + 信息源 + profile，逐条验（有据/可执行/贴合/scope），只吐一行 gate JSON（verdict=pass|drop|revise）。"""


def verify_prompt(prd_path: str, branch: str | None, base: str, diff_path: Path,
                  verify: dict | None, round_n: int, prof: dict) -> str:
    """pa-verify prompt（docs/verify-commit-loop-design.md §5-② 契约）：喂 PRD+分支+base+diff+测试输出+round。

    base 在 round≥2 是「上次 dev 分支」（增量重投）；verify 是 independent_verify 的产物（含 test_rc/test_log），
    None 表示测试未跑（dev 未报 test_cmd / 仓无 scripts.test）。"""
    test_rc = (verify or {}).get("test_rc")
    if test_rc == 0:
        test_state = "绿（test_rc=0，全量测试过）"
    elif test_rc is not None:
        test_state = f"红（test_rc={test_rc}，有测试失败）"
    else:
        test_state = "未跑（dev 未报 test_cmd 或仓无 scripts.test → independent_verify 跳过）"
    test_log = (verify or {}).get("test_log")
    return f"""[verify 第{round_n}轮] 项目={prof.get('name')} 分支={branch} base={base}（round≥2 为增量：上次 dev 分支）

PRD（验收标准在此）：{prd_path}
git diff（{base}..{branch}）：{diff_path}
测试输出（independent_verify 全量重跑的 stdout）：{test_log or '（无；测试未跑）'}
测试结论：{test_state}

按你的 persona 输出契约：Read PRD 验收标准 + diff + 测试输出，判「验证绿且无回归」。
- 测试绿 → verdict=pass（快速确认 diff 与验收标准大致对应、无重大跑题即可）。
- 测试红 → verdict=revise，写 feedback_section 四要素：①定位（文件/测试/断言行）②原因 ③怎么改（可执行）④收尾门（全量测试绿才算过）。
- round=2 表示 dev 已按上轮反馈增量重做过一次，重点看反馈是否被落实。
只吐那一行 JSON，多一个字都算失败。"""


# ─── fetch 段（agent-deepresearch 源：调 pa-fetch-deepresearch 深研 → 落 YYYYMMDD_*.md）────
FETCH_AGENT = "pa-fetch-deepresearch"
# exa MCP 工具白名单（ECC plugin 提供；firecrawl 缺席，单 exa 即可——冒烟已证可用）
FETCH_ALLOWED_TOOLS = ["mcp__plugin_ecc_exa__web_search_exa",
                       "mcp__plugin_ecc_exa__web_fetch_exa"]


def fetch_prompt(src: dict) -> str:
    prompts = (src.get("params") or {}).get("prompts") or []
    topic_block = "\n".join(f"- {p}" for p in prompts) if prompts else f"- {src['name']}"
    return f"""你是 pa-fetch-deepresearch。对以下研究主题做多源深研（exa 搜 → 深读 → 合成带引用 markdown），产出 radar 可消费的信号文件。
研究主题（采集源 {src['name']}）：
{topic_block}

严格按 persona 输出契约：只吐一行 JSON，结构 {{"title":"...","markdown":"<完整带引用 md 全文>","sources_count":N,"confidence":"High|Medium|Low"}}。markdown 字段内换行用 \\n 转义。"""


def stage_fetch(args, sources, stamp) -> dict:
    """agent-deepresearch 源 fetcher：调 pa-fetch-deepresearch agent 深研 → 落 YYYYMMDD_<slug>.md 到 source.root。

    其他 kind 跳过（directory/local-file 无 fetcher；wechat-url/github-repo 后续 follow-up ①②）。
    fetch 不碰 marker（radar 消费后才 bump，ADR-0007 #3）；--dry-run 不影响 fetch（写文件是 fetch 的全部意义）。
    stamp = 采集日（编排器传入的 YYYYMMDD），满足「文件名 = 采集戳」契约。"""
    produced = []
    for src in sources:
        if src.get("kind") != "agent-deepresearch":
            continue
        root = VAULT_ROOT / src["root"]
        root.mkdir(parents=True, exist_ok=True)
        payload, meta = run_persona(FETCH_AGENT, fetch_prompt(src), "fetch",
                                    f"fetch-{src['name']}", allowed_tools=FETCH_ALLOWED_TOOLS)
        md = (payload.get("markdown") or "").strip()
        if not md:
            log(f"[fetch] ⚠ {src['name']} agent 未返回 markdown（跳过落盘）")
            continue
        title = payload.get("title") or src["name"]
        slug = dev_slugify(title) or src["name"]            # 复用 ADR-0006 单一源头
        out = root / f"{stamp}_{slug}.md"
        out.write_text(md, encoding="utf-8")
        produced.append({"source": src["name"],
                         "path": str(out.relative_to(VAULT_ROOT)),
                         "sources_count": payload.get("sources_count"),
                         "cost": meta["cost"], "turns": meta["turns"]})
        log(f"[fetch] ✅ {src['name']} → {out.relative_to(VAULT_ROOT)}｜"
            f"sources={payload.get('sources_count')} cost=${meta['cost']:.4f} turns={meta['turns']}")
    out_json = {"produced": produced, "stamp": stamp}
    (STATE_DIR / f"fetch_{stamp}.json").write_text(
        json.dumps(out_json, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_json


# ─── 三段执行 ────────────────────────────────────────────────────────
def stage_radar(args, sources, profiles, stamp) -> dict:
    cand_file = STATE_DIR / f"candidates_{stamp}.json"
    if cand_file.is_file() and not args.force:
        log(f"[radar] 复用已有 {cand_file.name}（--force 重跑）")
        return json.loads(cand_file.read_text(encoding="utf-8"))

    # 1) 每源 discover（marker 在「全部 radar 成功后」才 bump——保持原失败不 bump 语义；
    #    先记 new_max，不立即写 marker）。dry_run 时同样延后到末尾跳过。
    per_source_new: dict[str, list[Path]] = {}
    per_source_newmax: dict[str, str] = {}
    for src in sources:                                   # ← 去 sources[0] 硬编码（ADR-0007 决定 #3）
        marker = read_marker(src)
        today_new = discover_today_new(src, marker, args.limit)
        per_source_new[src["name"]] = today_new
        if today_new:
            per_source_newmax[src["name"]] = max(re.match(r"(\d{8})", p.name).group(1) for p in today_new)
        log(f"[radar] source={src['name']} kind={src.get('kind','directory')} "
            f"marker={marker}｜今日新（>marker）={len(today_new)}")
        for p in today_new:
            log(f"        - {p.relative_to(VAULT_ROOT)}")

    total_new = sum(len(v) for v in per_source_new.values())
    if total_new == 0:
        log("[radar] 全源今日无新内容，跳过")
        empty = {"candidates": [], "today_new_count": 0, "per_source": {}, "stats": {}, "per_project_stats": {}}
        cand_file.write_text(json.dumps(empty, ensure_ascii=False, indent=2), encoding="utf-8")
        return empty

    # 2) 按项目聚合：project → [(source_name, [该源喂进来的文件])]
    #    target_projects 缺省 = 不喂任何项目（grilling Q4 定，防新源忘标喂到不相关项目）
    proj_src_files: dict[str, list[tuple[str, list[Path]]]] = {}
    for src in sources:
        for proj in (src.get("target_projects") or []):
            if proj in profiles:
                proj_src_files.setdefault(proj, []).append((src["name"], per_source_new[src["name"]]))

    # 3) 按项目调 radar（只有「订阅到新文件」的项目才调——无订阅不调，比现状更省）
    dedup = fetch_dedup_list(profiles)
    all_candidates: list[dict] = []
    per_project_stats: dict[str, dict] = {}
    for proj, src_files in proj_src_files.items():
        flat = [f for _, fs in src_files for f in fs]
        if not flat:
            continue                                      # 无订阅文件 → 不调
        payload, meta = run_persona(
            "pa-radar", radar_prompt(proj, flat, profiles[proj], dedup.get(proj, [])),
            "radar", f"radar-{proj}")
        for c in payload.get("candidates", []):
            c.setdefault("project", proj)
            c.setdefault("source", _source_of(c, src_files))   # 追溯来自哪个源（决定 #6）
            all_candidates.append(c)
        per_project_stats[proj] = {**payload.get("stats", {}), "cost": meta["cost"], "turns": meta["turns"]}
        log(f"[radar] ✅ {proj}: candidates={len(payload.get('candidates', []))}｜"
            f"cost=${meta['cost']:.4f} turns={meta['turns']}")

    # 4) stats 扁平聚合（report 段向后兼容：仍读 stats.signals_extracted / dropped_*）
    flat_stats = {"signals_extracted": 0, "dropped_low_relevance": 0, "dropped_dedup": 0}
    for s in per_project_stats.values():
        for k in flat_stats:
            flat_stats[k] += s.get(k, 0)

    out = {"candidates": all_candidates,
           "today_new_count": total_new,
           "per_source": {k: len(v) for k, v in per_source_new.items()},
           "stats": flat_stats,
           "per_project_stats": per_project_stats}
    cand_file.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    # 5) radar 全成功后才 bump：只 bump「有新文件 且 至少喂了一个有效订阅项目」的源。
    #    无 target_projects / 目标不是已知 profile 的源不 bump——文件保持可发现，待日后接源（防静默丢失）。
    if not args.dry_run:
        for src in sources:
            newmax = per_source_newmax.get(src["name"])
            if newmax is None:
                continue
            valid_targets = [t for t in (src.get("target_projects") or []) if t in profiles]
            if not valid_targets:
                continue
            bump_marker(src, newmax)
            log(f"[radar] {src['name']} marker bump → {newmax}")
    else:
        log("[radar] --dry-run，不 bump marker")
    return out


def stage_prd(args, candidates_payload, profiles, stamp) -> dict:
    man_file = STATE_DIR / f"prd_manifest_{stamp}.json"
    if man_file.is_file() and not args.force:
        log(f"[prd] 复用已有 {man_file.name}（--force 重跑）")
        return json.loads(man_file.read_text(encoding="utf-8"))

    candidates = candidates_payload.get("candidates", [])
    if not candidates:
        log("[prd] 无 candidates，跳过")
        empty = {"prds": [], "skipped": []}
        man_file.write_text(json.dumps(empty, ensure_ascii=False, indent=2), encoding="utf-8")
        return empty

    log(f"[prd] 翻译 {len(candidates)} 条候选 → PRD")
    payload, meta = run_persona("pa-prd", prd_prompt(candidates, profiles, stamp), "prd", "prd")
    log(f"[prd] ✅ PRD={len(payload.get('prds',[]))} skipped={len(payload.get('skipped',[]))}｜cost=${meta['cost']:.4f} turns={meta['turns']}")
    for prd in payload.get("prds", []):
        log(f"        - {prd.get('path')}")
    man_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def stage_critic(args, manifest, profiles, stamp) -> list[dict]:
    gate_file = STATE_DIR / f"prd_gate_{stamp}.json"
    if gate_file.is_file() and not args.force:
        log(f"[critic] 复用已有 {gate_file.name}（--force 重跑）")
        return json.loads(gate_file.read_text(encoding="utf-8"))

    prds = manifest.get("prds", [])
    if not prds:
        log("[critic] 无 PRD，跳过")
        gate_file.write_text("[]", encoding="utf-8")
        return []

    entries = []
    for prd in prds:
        proj = prd.get("project")
        prof = profiles.get(proj, {})
        path = prd.get("path")
        src = prd.get("source_path", "")
        entry = _critic_one(path, src, prof)
        entries.append(entry)

        # revise 回环：1 次修订机会（SPEC §4.3）
        if entry["verdict"] == "revise":
            log(f"[critic] revise：{path} → 喂回 pa-prd round 2")
            rev = entry
            try:
                rev_payload, _ = run_persona(
                    "pa-prd", prd_prompt([], profiles, stamp, revise=rev), "prd", f"prd-revise:{proj}")
                rev_prd = (rev_payload.get("prds") or [{}])[0]
                rev_path = rev_prd.get("path", path)
                entry2 = _critic_one(rev_path, src, prof)
                entry2["round"] = 2
                entry2["revised"] = True
                entries.append(entry2)
            except Exception as e:
                log(f"[critic] revise 失败，按 drop：{e}")
                entries.append({"prd_path": path, "project": proj, "verdict": "drop",
                                "summary": f"revise 失败：{e}", "round": 2, "revised": True})

    gate_file.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    return entries


def _critic_one(path: str, source_path: str, prof: dict) -> dict:
    label = f"critic:{Path(path).stem}"
    payload, meta = run_persona("pa-prd-critic", critic_prompt(path, source_path, prof), "critic", label)
    log(f"[critic] {payload.get('verdict','?').upper():6} {path}｜cost=${meta['cost']:.4f} "
        f"issues={len(payload.get('issues',[]))}")
    payload.setdefault("round", 1)
    payload.setdefault("revised", False)
    return payload


# ─── dispatch 段（投递 + 对账 + 独立验证，纯机械，SPEC §4.4 / ADR-0004）───
# dispatch 是「编排器逻辑」（SPEC §4.4 标题自证），不立 pa-dispatch persona（全机械无语义，persona 化只增成本）。
# 分支设计 A：dispatch 建 detached worktree + 传 --branch-prefix auto；dev-agent.mjs 自建分支/开 PR（不改目标面）。
def repo_owner_repo(repo: str) -> str | None:
    """从 git remote origin 提取 owner/repo（如 jyf2100/cc-web-control）。失败返 None。"""
    try:
        url = subprocess.run(["git", "-C", repo, "remote", "get-url", "origin"],
                             capture_output=True, text=True, timeout=15).stdout.strip()
        m = re.search(r"[:/]([^/]+/[^/]+?)(?:\.git)?$", url)
        return m.group(1) if m else None
    except Exception:
        return None


def _env_python(env_name: str) -> str:
    """conda env 名 → 该 env 的 python 绝对路径（控制面主机）。env 缺失/未给 → 回退 python3。"""
    if env_name:
        p = CONDA_ENVS_DIR / env_name / "bin" / "python"
        if p.exists():
            return str(p)
    return "python3"


def check_branch_protection(owner_repo: str, base: str) -> tuple[bool, str]:
    """运行时实查 main 分支保护（SPEC §4.4：protection 是平台态可被外部改动，故运行时实查、不进静态 profile）。
    返回 (protected, reason)。404→(False, "未保护，拒投")。"""
    try:
        r = subprocess.run(
            ["gh", "api", f"repos/{owner_repo}/branches/{base}/protection", "--silent",
             "-H", "Accept: application/vnd.github+json"],
            capture_output=True, text=True, timeout=20)
        if r.returncode == 0:
            return True, "已保护"
        if "404" in (r.stderr or ""):
            return False, "未保护（404），拒投"
        return False, f"查询失败 rc={r.returncode}: {(r.stderr or '').strip()[:120]}"
    except subprocess.TimeoutExpired:
        return False, "查询超时"
    except Exception as e:
        return False, f"查询异常: {e}"


def count_inflight_prs(owner_repo: str) -> int:
    """在途开放 PR 数（SPEC R1：≤ max_prs_in_flight）。失败返 0（容忍）。"""
    try:
        js = subprocess.run(
            ["gh", "pr", "list", "-R", owner_repo, "--state", "open", "--limit", "50", "--json", "number"],
            capture_output=True, text=True, timeout=20)
        return len(json.loads(js.stdout or "[]"))
    except Exception:
        return 0


# dev_slugify 已上移至 slug_utils.py（顶部 import；ADR-0006 #5 单一源头，消解历史 shadow）


# ─── inject 段（手动注入入口，SPEC §4.x）─────────────────────────────
def _split_frontmatter(text: str) -> tuple[dict, str]:
    """拆 YAML frontmatter：返回 (fm_dict, body_str)。无 frontmatter → ({}, 原文)。
    split 限 2 次，正文里的 '---'（如分隔线）不受影响。"""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            fm = yaml.safe_load(parts[1]) or {}
            if isinstance(fm, dict):
                return fm, parts[2]
    return {}, text


def _extract_title(body: str) -> str | None:
    """取正文首个 '# ' 一级标题文本；无则 None（'## ' 不会被误匹配）。"""
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()
    return None


def _pinyin_slug(title: str) -> str:
    """标题 → ASCII slug：pypinyin 转拼音（lazy_pinyin 连写）→ dev_slugify 规整（≤24）。

    pypinyin 惰性 import：cron 的 /usr/bin/python3 未装它，但 inject 段 cron 不触发
    （inject 仅手动触发），模块顶层 import 会拖垮每晚 cron 运行（镜像顶部 yaml 的兜底模式）。"""
    try:
        from pypinyin import lazy_pinyin, Style
    except ImportError:
        sys.exit("✗ 缺 pypinyin：pip3 install pypinyin（inject 段用，cron 不触发）")
    py = "".join(lazy_pinyin(title, style=Style.NORMAL))
    return dev_slugify(py)


def _bump_stamp_suffix(stamp: str) -> str:
    """manifest 自增避碰：'20260717'→'20260717m'→'20260717m2'→'...m3'。
    fresh stamp 保证 critic/dispatch 必跑（不复用旧 gate/dispatch，避 SPEC 重用陷阱）。"""
    m = re.match(r"^(.*?)(m(\d*))$", stamp)
    if m:
        n = int(m.group(3)) if m.group(3) else 1
        return f"{m.group(1)}m{n + 1}"
    return stamp + "m"


def _stamp_to_date(stamp: str) -> str:
    """stamp → 'YYYY-MM-DD'（取前 8 位日期；非日期 stamp 原样返回）。"""
    d = re.match(r"(\d{4})(\d{2})(\d{2})", stamp)
    return f"{d.group(1)}-{d.group(2)}-{d.group(3)}" if d else stamp


def stage_inject(args, profiles: dict, stamp: str) -> tuple[dict, str]:
    """inject 段（手动注入入口）：把手写 PRD md 转成标准 manifest，替 radar→prd 自动路径。

    读 args.inject_prd 指向的 md（YAML frontmatter + 正文），校验 project 是已知 profile，
    标题转拼音 slug，写入 state/prd/<project>/<stamp>_<slug>.md，吐 prd_manifest_<stamp>.json。
    stamp 若已被占（今天已有自动跑的 manifest）→ 自增 m/m2/... 保证 critic/dispatch 必跑。
    返回 (manifest, actual_stamp)：actual_stamp 可能 != 入参（自增过），下游用它对齐文件名。

    硬约束（ADR-0001）：Write 仅限 .project-auto/state/prd/，绝不写目标仓；用户原文件不动（copy）。"""
    if not getattr(args, "inject_prd", None):
        sys.exit("✗ inject 段需要 --inject-prd <path>（手写 PRD md）")

    src = Path(args.inject_prd)
    if not src.is_file():
        sys.exit(f"✗ --inject-prd 文件不存在：{src}")

    raw = src.read_text(encoding="utf-8")
    fm, body = _split_frontmatter(raw)

    project = fm.get("project")
    if not project or project not in profiles:
        sys.exit(f"✗ frontmatter.project={project!r} 不是已知 profile（admission=true 的）：{list(profiles)}")

    title = _extract_title(body) or src.stem
    slug = fm.get("slug") or _pinyin_slug(title)
    if not slug:
        sys.exit(f"✗ 从标题 {title!r} 派生不出合法 slug；在 frontmatter 显式给 slug=")

    source_path = fm.get("source_path") or ""
    if not source_path:
        log("  ⚠ inject 的 PRD 无 source_path——critic 可能因「无据」revise/drop，dev-agent 也缺上下文")

    # stamp 自增避碰：今天已有自动跑的 manifest → m/m2/... 直到空位
    actual = stamp
    while (STATE_DIR / f"prd_manifest_{actual}.json").is_file():
        actual = _bump_stamp_suffix(actual)

    # 落 PRD（copy 原文 + 补全 frontmatter；不动用户原文件）
    prd_dir = STATE_DIR / "prd" / project
    prd_dir.mkdir(parents=True, exist_ok=True)
    prd_path = prd_dir / f"{actual}_{slug}.md"
    fm_full = dict(fm)
    fm_full.setdefault("project", project)
    fm_full.setdefault("source_path", source_path)
    fm_full.setdefault("date", _stamp_to_date(actual))
    fm_full.setdefault("signal", fm.get("signal") or title)
    fm_full.setdefault("round", 1)
    fm_full["slug"] = slug   # 记录解析出的 slug（dev-agent 实按文件名 stem slugify，此字段供追溯）
    front = yaml.safe_dump(fm_full, allow_unicode=True, sort_keys=False).strip()
    prd_path.write_text(f"---\n{front}\n---\n{body.lstrip()}", encoding="utf-8")

    # 落 manifest（单行 JSON，vault 相对路径，与 pa-prd 输出契约一致）
    entry = {"project": project, "slug": slug,
             "path": str(prd_path.relative_to(VAULT_ROOT)),
             "source_path": source_path, "title": title}
    manifest = {"prds": [entry], "skipped": []}
    (STATE_DIR / f"prd_manifest_{actual}.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    log(f"[inject] ✓ 注入 {prd_path.name} → prd_manifest_{actual}.json（project={project}）")

    return manifest, actual


def already_dispatched(owner_repo: str, repo: str, devslug: str) -> tuple[bool, str]:
    """幂等前置闸（SPEC #30 ④ / ADR-0004 §4）：按 slug 子串查 GitHub PR(all state) + 远端 auto/* 分支。
    命中→(True, reason)；slug=date+24字描述够特异、子串误命中可忽略；任一查询失败容忍（不阻断，reconcile_pr 仍兜底对账）。
    用 slug 子串而非精确 --head <branch>，因 dev-agent.mjs stamp()=YYYYMMDD-HHMM（含时分）run_daily.py 不可预测
    （SPEC #30 ④ 选型 ii：纯控制面，不动 dev-agent.mjs）。"""
    try:
        js = subprocess.run(
            ["gh", "pr", "list", "-R", owner_repo, "--state", "all", "--limit", "100",
             "--json", "number,headRefName,state"],
            capture_output=True, text=True, timeout=20)
        for pr in json.loads(js.stdout or "[]"):
            if devslug in (pr.get("headRefName") or ""):
                return True, f"已投递（PR #{pr.get('number')} {pr.get('state')}，分支 {pr.get('headRefName')}）"
    except Exception:
        pass
    try:
        out = subprocess.run(["git", "-C", repo, "ls-remote", "--heads", "origin"],
                             capture_output=True, text=True, timeout=20).stdout
        for line in out.splitlines():
            if "\t" not in line:
                continue
            ref = line.split("\t", 1)[1].replace("refs/heads/", "")
            if ref.startswith("auto/") and devslug in ref:
                return True, f"已投递（远端分支 {ref}，无 PR）"
    except Exception:
        pass
    return False, ""


def _run_capture(cmd: list[str], cwd: str, timeout: int, label: str,
                 log_file: Path | None = None, env: dict | None = None) -> tuple[int, str, str]:
    """跑子进程，捕获 stdout/stderr（整段写 log_file）；返回 (rc, stdout, stderr_tail)。env=None 继承本进程环境。超时抛 RuntimeError。"""
    log(f"  {label}▶ {Path(cmd[0]).name} …（cwd={Path(cwd).name}, {timeout}s）")
    try:
        proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"{label} wall-clock 超时（{timeout}s）") from e
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"\n$ {' '.join(cmd)}\n[stdout]\n{proc.stdout}\n[stderr]\n{proc.stderr}\n")
    return proc.returncode, proc.stdout, (proc.stderr or "")[-800:]


# ─── verify 闭环辅助（dev→独立验证→pa-verify，docs/verify-commit-loop-design.md §3/§5）──
def _dev_cmd(prof: dict, py: Path, mjs: Path, prd_abs: str, base: str, src_abs: str) -> list[str] | None:
    """构造 dev-agent 触发命令（ADR-0003 / ADR-0006 选源）。

    dev_agent_source（profile 字段，默认 repo）：
      vault → 控制面 dev-agent.py（与本文件同目录），忽略仓内 py/mjs；
      repo  → 仓内 Python dev-agent.py（conda env python）> Node dev-agent.mjs（现状不变）。
    --base 由调用方按 verify 闭环轮次传入（round1=默认分支；round≥2=上次 dev 分支，增量重投）。
    选定源缺运行时 → None（dispatch_one 判 fail）。"""
    source = prof.get("dev_agent_source", "repo")
    if source == "vault":
        vault_py = Path(__file__).resolve().parent / "dev-agent.py"
        if not vault_py.exists():
            return None
        cmd = [_env_python(prof.get("conda_env", "")), str(vault_py),
               "--prd", prd_abs, "--branch-prefix", "auto", "--base", base]
    elif py.exists():
        cmd = [_env_python(prof.get("conda_env", "")), str(py),
               "--prd", prd_abs, "--branch-prefix", "auto", "--base", base]
    elif mjs.exists():
        cmd = ["node", str(mjs), "--prd", prd_abs, "--branch-prefix", "auto", "--base", base]
    else:
        return None
    if src_abs:
        cmd += ["--source", src_abs]
    return cmd


def _run_dev_agent(cmd: list[str], wt: Path, slug: str, log_file: Path) -> dict | None:
    """在 worktree 触发 dev-agent，解析末行 stdout JSON。异常/无输出 → None（→ dev_killed）。"""
    try:
        _rc, out, _ = _run_capture(cmd, str(wt), DEV_LOOP_TIMEOUT, f"[{slug}:dev]", log_file)
        tail = (out or "").strip().splitlines()
        return json.loads(tail[-1]) if tail else None
    except (RuntimeError, json.JSONDecodeError, IndexError) as e:
        log(f"  ✗ {slug}: dev loop 异常 {e}")
        return None


def _has_commits(repo: str, base_ref: str, branch: str) -> bool:
    """branch 相对 base_ref 是否有新 commit（verify 闸门 + verify 闭环判增量产出用）。失败容忍→False。"""
    try:
        return bool(subprocess.run(["git", "-C", repo, "log", f"{base_ref}..{branch}", "--oneline"],
                                   capture_output=True, text=True, timeout=20).stdout.strip())
    except Exception:
        return False


def _dump_branch_diff(repo: str, base_ref: str, branch: str, out_path: Path) -> None:
    """落 branch 相对 base_ref 的 diff 到文件（喂 pa-verify Read；ADR-0002：只读 git，不改目标仓）。失败→空 diff 文件。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        out = subprocess.run(["git", "-C", repo, "diff", f"{base_ref}..{branch}"],
                             capture_output=True, text=True, timeout=120).stdout
    except Exception:
        out = ""
    out_path.write_text(out or "（diff 为空或获取失败）", encoding="utf-8")


def _append_verify_feedback(prd_abs: str, feedback_section: str, round_n: int) -> None:
    """把 pa-verify 反馈节追加进 PRD 末尾（醒目独立节，标注「非需求变更、未重过 critic 闸」）。

    反馈是施工指引（非需求变更），故不重过 pa-prd-critic 闸（docs/verify-commit-loop-design.md §3-④）。"""
    section = (f"\n\n## ⚠️ 审核反馈（verify 第{round_n}轮·非需求变更，未重过 critic 闸）\n\n"
               + (feedback_section or "").strip() + "\n")
    with open(prd_abs, "a", encoding="utf-8") as f:
        f.write(section)


def _pa_verify_round(rec: dict, prof: dict, prd_abs: str, cur_base: str,
                     diff_path: Path, round_n: int, slug: str) -> dict:
    """单轮 pa-verify 裁判：喂 PRD+diff+测试输出+round，吐一行 JSON payload（verdict=pass|revise）。"""
    prompt = verify_prompt(prd_abs, rec.get("branch"), cur_base, diff_path, rec.get("verify"), round_n, prof)
    payload, meta = run_persona("pa-verify", prompt, "verify", f"verify:{slug}:r{round_n}")
    log(f"[verify] r{round_n} {str(payload.get('verdict', '?')).upper():6} {slug}｜"
        f"cost=${meta['cost']:.4f} turns={meta['turns']}")
    payload.setdefault("round", round_n)
    return payload


def dispatch_one(entry: dict, prof: dict, stamp: str, args) -> dict:
    """单 PRD 全流程：准入→建 worktree→触发 dev-agent→对账→独立验证。返回记录 dict。"""
    proj = prof.get("name", "?")
    repo = prof.get("repo", "")
    slug = Path(entry.get("prd_path", "")).stem or "unknown"
    devslug = dev_slugify(slug)   # 复刻 dev-agent.mjs 分支 slug（幂等前置闸按 slug 匹配 auto/* 用）
    base = prof.get("default_branch", "main")   # SPEC：默认分支从 profile 来（main | master）；check_branch_protection 实查该分支保护
    owner_repo = repo_owner_repo(repo) if repo else ""
    prd_abs = str(VAULT_ROOT / entry.get("prd_path", ""))    # 控制面 PRD 绝对路径（只读喂 dev-agent）
    src_rel = entry.get("source_path") or ""
    src_abs = str(VAULT_ROOT / src_rel) if src_rel else ""
    log_file = STATE_DIR / "runs" / proj / f"{stamp}_{slug}.log"

    rec: dict = {"project": proj, "prd_path": entry.get("prd_path"), "slug": slug, "base": base,
                 "status": "pending", "pr_url": None, "branch": None, "dev_killed": False,
                 "stalled": False, "run_log": None,
                 "dev_cost": None, "dev_turns": None, "verify": None, "skip_reason": None,
                 "dev_test_cmd": None, "verify_verdict": None, "verify_round": None}   # verify 闭环字段

    # ── 准入 1：profile 门
    if not (prof.get("admission") and prof.get("dev_agent_ready") and prof.get("type") == "code"):
        rec.update(status="skip", skip_reason="profile 不满足（admission/dev_agent_ready/type≠code）")
        log(f"  ⏭ {slug}: {rec['skip_reason']}"); return rec
    # ── 准入 2：branch protection 运行时实查
    if not owner_repo:
        rec.update(status="skip", skip_reason="跳过-无 remote（取不到 owner/repo）")
        log(f"  ⏭ {slug}: {rec['skip_reason']}"); return rec
    ok, why = check_branch_protection(owner_repo, base)
    if not ok:
        rec.update(status="skip", skip_reason=f"跳过-{why}")
        log(f"  ⏭ {slug}: {rec['skip_reason']}"); return rec
    # ── 准入 3：幂等前置闸（SPEC #30 ④ / ADR-0004 §4：投递前去重，已投递→skip 不起 dev loop，省 SDK 启动+$）
    hit, why_idem = already_dispatched(owner_repo, repo, devslug)
    if hit:
        rec.update(status="skip", skip_reason=f"跳过-{why_idem}")
        log(f"  ⏭ {slug}: {rec['skip_reason']}"); return rec
    # ── 准入 4：在途 PR 限量（R1）
    inflight = count_inflight_prs(owner_repo)
    if inflight >= int(prof.get("max_prs_in_flight", 2)):
        rec.update(status="skip", skip_reason=f"跳过-超额（在途 {inflight} ≥ {prof.get('max_prs_in_flight', 2)}）")
        log(f"  ⏭ {slug}: {rec['skip_reason']}"); return rec

    # ── 零成本 smoke：过准入但不触发 dev loop（不花钱、不开 PR）
    if getattr(args, "dispatch_skip_dev", False):
        rec.update(status="planned",
                   skip_reason=f"--dispatch-skip-dev smoke（已过准入，in-flight {inflight}，未触发 dev loop）")
        log(f"  📋 {slug}: 将投递（已过准入，in-flight {inflight}）— skip-dev 未触发")
        return rec

    # ── 投递：detached worktree on main → 触发 dev-agent.mjs（分支设计 A）
    if not log_file.exists():
        log_file.parent.mkdir(parents=True, exist_ok=True)
    wt = Path(repo) / ".worktrees" / f"{stamp}-{slug}"
    if wt.exists():
        subprocess.run(["git", "-C", repo, "worktree", "remove", "--force", str(wt)],
                       capture_output=True, text=True, timeout=60)
    try:
        _run_capture(["git", "-C", repo, "worktree", "add", "--detach", str(wt), base],
                     repo, 120, f"[{slug}:worktree]", log_file)
    except RuntimeError as e:
        rec.update(status="fail", skip_reason=f"建 worktree 失败: {e}")
        log(f"  ✗ {slug}: {rec['skip_reason']}"); return rec

    # 运行时探测 dev-agent 运行时（ADR-0003 通用化）：Python 仓 dev-agent.py（conda env python）> Node 仓 dev-agent.mjs
    mjs = Path(repo) / "scripts" / "dev-agent.mjs"
    py = Path(repo) / "scripts" / "dev-agent.py"

    # ── verify 闭环（docs/verify-commit-loop-design.md §3）：dev→独立验证→pa-verify 裁判；判红保留分支+反馈进 PRD+增量重投；判绿兜底开 PR
    #    同构模板：stage_critic revise loop（§4.3）。reconcile 顺位后移到「裁定后收尾」——不预先为中间红的分支补开 PR。
    cur_base = base
    for round_n in range(1, VERIFY_MAX_ROUNDS + 1):
        cmd = _dev_cmd(prof, py, mjs, prd_abs, cur_base, src_abs)
        if cmd is None:
            _src = prof.get("dev_agent_source", "repo")
            _why = ("vault 版 dev-agent.py 缺失（控制面安装异常）" if _src == "vault"
                    else "仓内无 scripts/dev-agent.{py,mjs}（ADR-0003 准入未满足）")
            rec.update(status="fail", skip_reason=_why)
            log(f"  ✗ {slug}: {rec['skip_reason']}"); return rec
        script_json = _run_dev_agent(cmd, wt, slug, log_file)
        if script_json:
            rec["dev_cost"] = script_json.get("cost"); rec["dev_turns"] = script_json.get("turns")
            rec["branch"] = script_json.get("branch")
            rec["stalled"] = bool(script_json.get("stalled"))   # SPEC #27：dev-agent 主动刹车（exit 12，非超时）
            rec["run_log"] = script_json.get("run_log")          # 监控 jsonl 路径（state/runs/...）
            rec["dev_test_cmd"] = script_json.get("test_cmd")
        rec["dev_killed"] = script_json is None   # 无 stdout JSON → 大概率 kill/崩（与 stalled 互补：超时 vs 主动刹车）

        branch = rec.get("branch")
        if not branch:                        # dev 建分支前就崩/超时 → 无可验证、无分支做下次 base，对账收尾（stall 救不了，§2 实证）
            log(f"  ✗ {slug}: dev r{round_n} 未吐 branch（建分支前崩/超时）→ 对账收尾")
            reconcile_pr(repo, owner_repo, rec, base, slug, interrupted=True); break

        # 独立验证（门=branch+相对 cur_base 有 commit；与 reconcile status 解耦——闭环内 verify 先于对账）
        has_commits = _has_commits(repo, cur_base, branch)
        if has_commits:
            rec["verify"] = independent_verify(repo, branch, stamp, slug, log_file, prof,
                                               test_cmd_hint=(script_json.get("test_cmd") if script_json else None))
        else:
            rec["verify"] = None              # 无新增 commit（如 round2 dev 未动）→ 无可验证

        # pa-verify 裁判（仅有产出可审时；无产出 → 终止不空转）
        vinfo: dict | None = None
        if has_commits:
            diff_path = STATE_DIR / "runs" / proj / f"{stamp}_{slug}.r{round_n}.diff"
            _dump_branch_diff(repo, cur_base, branch, diff_path)
            try:
                vinfo = _pa_verify_round(rec, prof, prd_abs, cur_base, diff_path, round_n, slug)
            except Exception as e:
                log(f"  ⚠ {slug}: pa-verify r{round_n} 异常 → 终止循环，按现状对账: {e}")
                vinfo = None
        rec["verify_verdict"] = vinfo.get("verdict") if vinfo else None
        rec["verify_round"] = round_n

        if vinfo and vinfo.get("verdict") == "pass":
            # 判绿：兜底开正常 PR 收尾（治 baostock 式 interrupted_pr；reconcile 查到 dev 自开 PR 则保持 pr_open）
            log(f"  ✅ {slug}: verify 绿（r{round_n}）→ 兜底开 PR 收尾")
            reconcile_pr(repo, owner_repo, rec, base, slug, interrupted=False); break
        if vinfo and vinfo.get("verdict") == "revise" and round_n < VERIFY_MAX_ROUNDS:
            # 判红（机会未用满）：保留分支做下次 base + 反馈追加进 PRD + 增量 --base=<上次分支> 重投
            log(f"  🔴 {slug}: verify 红（r{round_n}）→ 保留 {branch} 做下次 base，反馈进 PRD，增量重投 r{round_n + 1}")
            _append_verify_feedback(prd_abs, vinfo.get("feedback_section", ""), round_n)
            cur_base = branch
            continue
        # 判红用满（round_n==VERIFY_MAX_ROUNDS）/ pa-verify 异常 / 无产出 → 对账降级 interrupted_pr（不 drop，半成品留 review）
        log(f"  ⏸ {slug}: verify 终止（r{round_n}, verdict={rec['verify_verdict']}）→ 对账收尾（中断 PR 不 drop）")
        reconcile_pr(repo, owner_repo, rec, base, slug, interrupted=True); break

    return rec


def reconcile_pr(repo: str, owner_repo: str, rec: dict, base: str, slug: str,
                 interrupted: bool = True) -> None:
    """三态对账：有 PR 录入 / 无 PR 有 commit 补开 PR / 无 commit 删孤儿。以 GitHub 为真源。

    interrupted=True（默认：红/异常收尾）→ 补开「⏸ 中断」PR（interrupted_pr）；
    interrupted=False（verify 绿收尾）→ 补开正常 PR（pr_open，治 baostock 式 interrupted_pr）。
    有 PR / 无 commit 两态与 interrupted 无关（真源对账 / 孤儿清理照旧）。"""
    branch = rec.get("branch")
    if not branch:                                   # dev-agent 建分支前就崩 → 无可对账
        if rec.get("dev_killed"):
            rec["status"] = "fail"; rec["skip_reason"] = "dev loop 未吐 branch（建分支前崩/超时）"
        return

    # 1) 实查 GitHub 是否已有该分支的 PR
    pr_url = pr_state = None
    try:
        js = subprocess.run(
            ["gh", "pr", "list", "-R", owner_repo, "--head", branch, "--state", "all",
             "--limit", "5", "--json", "number,url,state"],
            capture_output=True, text=True, timeout=20)
        prs = json.loads(js.stdout or "[]")
        if prs:
            pr_url, pr_state = prs[0]["url"], prs[0]["state"]
    except Exception as e:
        log(f"  ⚠ {slug}: 查 PR 失败（容忍）: {e}")
    if pr_url:
        rec["pr_url"] = pr_url
        rec["status"] = "pr_open" if pr_state == "OPEN" else f"pr_{(pr_state or '').lower()}"
        log(f"  ✅ {slug}: 已开 PR {pr_url}（{pr_state}）"); return

    # 2) 无 PR：查分支有无 commit
    try:
        has_commit = bool(subprocess.run(
            ["git", "-C", repo, "log", f"{base}..{branch}", "--oneline"],
            capture_output=True, text=True, timeout=20).stdout.strip())
    except Exception:
        has_commit = False
    if has_commit:
        try:   # dev loop 留了 commit：dispatch 补开 PR（interrupted=True=⏸中断PR / False=verify绿正常PR）
            if interrupted:
                title = f"⏸ pa-dev 中断: {slug}"
                body = (f"dev-agent 返回但未开 PR（可能 wall-clock 超时/崩溃/自中止/verify 判红用满）。"
                        f"commit 已在 `{branch}`，需人工 review。")
                new_status = "interrupted_pr"
            else:
                title = f"pa-dev: {slug}"
                body = (f"dev-agent 产出经 pa-verify 判绿（verify 闭环收尾）。commit 在 `{branch}`。"
                        f"PRD：{rec.get('prd_path')}")
                new_status = "pr_open"
            url = subprocess.run(
                ["gh", "pr", "create", "-R", owner_repo, "--base", base, "--head", branch,
                 "--title", title, "--body", body],
                capture_output=True, text=True, timeout=30).stdout.strip()
            rec["pr_url"] = url or None; rec["status"] = new_status
            log(f"  {'⏸' if interrupted else '✅'} {slug}: {'中断' if interrupted else '正常'} PR 已补开 {url}")
            return   # 坑3 修复（2026-07-17）：has_commit 且 PR 已补开 → 到此为止，不再落到下方删分支。原代码漏 return，导致失败时「开完中断 PR 又删它的分支 + status 被覆盖成 orphan_deleted + dev 工作 branch -D 丢失」
        except Exception as e:
            rec.update(status="fail", skip_reason=f"补开 PR 失败: {e}")
            log(f"  ✗ {slug}: {rec['skip_reason']}"); return

    # 3) 无 PR 无 commit → 删分支（本地+远端，容错）。区分 stalled（dev-agent 主动刹车）vs orphan（啥都没干）
    subprocess.run(["git", "-C", repo, "branch", "-D", branch],
                   capture_output=True, text=True, timeout=20)
    subprocess.run(["git", "-C", repo, "push", "origin", "--delete", branch],
                   capture_output=True, text=True, timeout=30)
    if rec.get("stalled"):
        rec["status"] = "stalled"
        rec["skip_reason"] = "dev loop 主动刹车（验证红后连续 N 轮无写类进展，exit 12）"
        log(f"  🧯 {slug}: stalled，无 commit 分支 {branch} 已删（run_log: {rec.get('run_log')})")
    else:
        rec["status"] = "orphan_deleted"
        log(f"  🗑 {slug}: 无 commit 孤儿分支 {branch} 已删")


def independent_verify(repo: str, branch: str, stamp: str, slug: str, log_file: Path,
                       prof: dict | None = None, test_cmd_hint: str | None = None) -> dict:
    """独立验证闸（SPEC §4.4）：PR 分支全新 checkout worktree 重跑 test（非 dev 热乎树）。
    双轨：Node 仓（package.json）→ npm ci + npm test；Python 仓 → 重放 dev-agent 上报的 test_cmd（conda env PATH 注入）。
    测试归项目自治（ADR-0002）：dispatch 不替项目决定怎么测，只重放 dev-agent 上报的命令；dev-agent 未上报 → 跳过。
    返回 {test_cmd, pass, install_rc, test_rc, note}。红=标 failing（PR 留着不关）。"""
    out: dict = {"test_cmd": None, "pass": False, "install_rc": None, "test_rc": None, "note": None,
                 "test_log": None}   # test_log=干净测试 stdout 路径（喂 pa-verify Read，避开 log_file 的 install/wt 噪声）
    is_node = (Path(repo) / "package.json").exists()
    if is_node:
        try:
            scripts = json.loads((Path(repo) / "package.json").read_text(encoding="utf-8")).get("scripts", {})
        except Exception as e:
            out["note"] = f"读 package.json 失败: {e}"; return out
        test_cmd = scripts.get("test")
        out["test_cmd"] = test_cmd
        if not test_cmd:
            out["note"] = "package.json 无 scripts.test，跳过验证"; return out
    else:
        # 测试归项目自治：只重放 dev-agent 上报的 test_cmd；未上报则跳过（不替项目猜怎么测）
        if not test_cmd_hint:
            out["note"] = "dev-agent 未上报 test_cmd，跳过独立验证（测试归项目自治）"; return out
        out["test_cmd"] = test_cmd_hint

    vt = Path(repo) / ".worktrees" / f"verify-{stamp}-{slug}"
    if vt.exists():
        subprocess.run(["git", "-C", repo, "worktree", "remove", "--force", str(vt)],
                       capture_output=True, text=True, timeout=60)
    try:
        _run_capture(["git", "-C", repo, "worktree", "add", "--detach", str(vt), branch],
                     repo, 120, f"[{slug}:verify-wt]", log_file)
    except RuntimeError as e:
        out["note"] = f"建验证 worktree 失败: {e}"; return out
    try:
        if is_node:
            irc, _, _ = _run_capture(["npm", "ci"], str(vt), VERIFY_INSTALL_TIMEOUT, f"[{slug}:npm-ci]", log_file)
            out["install_rc"] = irc
            if irc != 0:
                out["note"] = "npm ci 失败"; return out
            trc, test_out, _ = _run_capture(["npm", "test"], str(vt), VERIFY_TEST_TIMEOUT, f"[{slug}:npm-test]", log_file)
        else:
            # Python：deps 在 conda env（无 install 闸）。重放 dev-agent 上报的 test_cmd（bash -c），PATH 注入 env bin
            # ——让命令里的裸 python / run_tests.py 的子进程都落到 env python（launching 基建，非测试逻辑）
            out["install_rc"] = 0
            venv = dict(os.environ)
            if prof and prof.get("conda_env"):
                venv["PATH"] = str(CONDA_ENVS_DIR / prof["conda_env"] / "bin") + os.pathsep + venv.get("PATH", "")
            trc, test_out, _ = _run_capture(["bash", "-c", test_cmd_hint], str(vt), VERIFY_TEST_TIMEOUT,
                                     f"[{slug}:py-test]", log_file, env=venv or None)
        out["test_rc"] = trc; out["pass"] = (trc == 0)
        test_log = log_file.parent / (log_file.stem + ".testout")   # 干净测试 stdout（vt 在 finally 里删，test_log 在 state/runs 下留存）
        try:
            test_log.write_text(test_out or "", encoding="utf-8")
            out["test_log"] = str(test_log)
        except Exception:
            out["test_log"] = None
        out["note"] = "绿" if trc == 0 else f"红（test rc={trc}）→ 标 failing，PR 留待 review"
        log(f"  {'✅' if trc == 0 else '🔴'} {slug}: 独立验证 {out['note']}")
        return out
    finally:
        subprocess.run(["git", "-C", repo, "worktree", "remove", "--force", str(vt)],
                       capture_output=True, text=True, timeout=60)


def _run_one(entry: dict, prof: dict | None, stamp: str, args) -> dict:
    """ThreadPoolExecutor worker：取 per-owner_repo 串行锁后调 dispatch_one（同仓串行、跨仓并行）。
    锁由 stage_dispatch 按 owner_repo 预建（避免并发 lazy 创建竞态）。仓无 remote（无锁）→ nullcontext 不串行。"""
    if not prof:
        return {"project": entry.get("project"), "prd_path": entry.get("prd_path"),
                "status": "skip", "skip_reason": "profile 不存在"}
    repo = prof.get("repo", "")
    owner_repo = repo_owner_repo(repo) if repo else ""
    lock = DISPATCH_LOCKS.get(owner_repo) if owner_repo else None
    with lock if lock else contextlib.nullcontext():
        return dispatch_one(entry, prof, stamp, args)


def acquire_run_lock(break_lock: bool) -> bool:
    """获取 run 级互斥锁（SPEC #30 ③ / ADR-0004 §4）。
    O_CREAT+O_EXCL 原子获取；已存在则判陈旧（PID 失活 OR 锁龄>MAX_RUN_WALL）→ 自动接管（删+重建）；
    活锁（PID 活且未超龄）→ 返回 False（main exit 2）；--break-lock 强拆重建。"""
    if break_lock and RUN_LOCK.exists():
        log(f"🔒 --break-lock：强拆旧 run 锁 {RUN_LOCK.name}")
        try:
            RUN_LOCK.unlink()
        except FileNotFoundError:
            pass
    try:
        fd = os.open(RUN_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, json.dumps({"pid": os.getpid(),
                                 "started": datetime.now(timezone.utc).isoformat()}).encode())
        os.close(fd)
        return True
    except FileExistsError:
        pass
    try:
        info = json.loads(RUN_LOCK.read_text(encoding="utf-8"))
        pid = int(info.get("pid", 0))
        started = info.get("started", "")
    except Exception:
        pid, started = 0, ""
    stale_pid = False
    if pid:
        try:
            os.kill(pid, 0)
        except OSError:
            stale_pid = True            # PID 失活 → 可接管
    stale_age = False
    if started:
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(started)).total_seconds()
            stale_age = age > MAX_RUN_WALL
        except ValueError:
            stale_age = True            # 时间戳解析不了 → 当陈旧
    if stale_pid or stale_age:
        why = ("PID 失活" if stale_pid else "") + ("、" if stale_pid and stale_age else "") + \
              ("锁龄超限" if stale_age else "")
        log(f"🔒 检测到陈旧 run 锁（{why}）→ 自动接管（pid={pid}, started={started}）")
        try:
            RUN_LOCK.unlink()
        except FileNotFoundError:
            pass
        return acquire_run_lock(break_lock=False)   # 递归一次重建
    log(f"🔒 run 锁被活进程占用（pid={pid}, started={started}）→ 拒绝启动（确信无活进程可用 --break-lock 强拆）")
    return False


def release_run_lock() -> None:
    """释放 run 锁（try/finally 全 exit 路径调）。"""
    try:
        RUN_LOCK.unlink()
    except FileNotFoundError:
        pass


def stage_dispatch(args, gate: list[dict], profiles: dict, stamp: str) -> list[dict]:
    """dispatch 段顶层：取过闸 pass PRD → 按 project 准入+投递+对账+验证 → 写 dispatch_<stamp>.json。"""
    disp_file = STATE_DIR / f"dispatch_{stamp}.json"
    if disp_file.is_file() and not args.force:
        log(f"[dispatch] 复用已有 {disp_file.name}（--force 重跑）")
        return json.loads(disp_file.read_text(encoding="utf-8"))
    passed = [e for e in gate if e.get("verdict") == "pass"]
    if getattr(args, "dispatch_limit", None):
        passed = passed[:args.dispatch_limit]
        log(f"[dispatch] --dispatch-limit={args.dispatch_limit}，只投前 {len(passed)} 份")
    if not passed:
        log("[dispatch] 无过闸 PRD，跳过")
        disp_file.write_text("[]", encoding="utf-8"); return []
    log(f"[dispatch] 过闸 PRD {len(passed)} 份，投递（max-concurrent={args.max_concurrent}）"
        f"{'  [SKIP-DEV 零成本 smoke]' if getattr(args, 'dispatch_skip_dev', False) else ''}")

    # 预建 per-owner_repo 串行锁（修并行下 count_inflight_prs check-then-act TOCTOU；同仓串行、跨仓并行）
    for e in passed:
        prof = profiles.get(e.get("project"))
        if not prof:
            continue
        repo = prof.get("repo", "")
        owner_repo = repo_owner_repo(repo) if repo else ""
        if owner_repo and owner_repo not in DISPATCH_LOCKS:
            DISPATCH_LOCKS[owner_repo] = threading.Lock()

    # 并行投递（ThreadPoolExecutor，sync subprocess.run 释放 GIL）；dict 保提交序、per-future 异常隔离（#26）
    records: list[dict] = []
    with ThreadPoolExecutor(max_workers=max(1, args.max_concurrent)) as exe:
        fut_to_entry = {exe.submit(_run_one, e, profiles.get(e.get("project")), stamp, args): e
                        for e in passed}
        for fut in fut_to_entry:          # 按提交序收集（.result() 阻塞到该 future 完，其余仍并行）
            e = fut_to_entry[fut]
            try:
                records.append(fut.result())
            except Exception as ex:
                log(f"  ✗ {e.get('prd_path')}: dispatch 异常 {ex}")
                records.append({"project": e.get("project"), "prd_path": e.get("prd_path"),
                                "status": "fail", "skip_reason": f"异常: {ex}"})
    # records 按 project+slug 排序保 diff 稳定（并行下完成序不确定）
    records.sort(key=lambda r: (r.get("project") or "", r.get("slug") or ""))
    disp_file.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    return records


# ─── report 段（§8：纯机械聚合 state JSON → 报告 + 日报指针 + SMTP 直发）──
def _read_json(path: Path, default):
    """读 state JSON；缺失/损坏返回 default（纯函数语义，不改入参）。"""
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return default
    return default


def stage_report(args, profiles: dict, stamp: str) -> Path:
    """报告段（SPEC §8）：读 4 份 state JSON → 项目推进/项目推进报告_<stamp>.md + 日报指针 + 有活则 SMTP 直发。

    纯控制面、**无 persona**（与 dispatch 同理：全机械聚合 state，不调 claude、零语义、零成本）。
    自包含从盘读全量 state → 支持 `--from-stage report` 单独重出报告 / cron 全流程尾段。
    """
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    cand = _read_json(STATE_DIR / f"candidates_{stamp}.json",
                      {"candidates": [], "today_new_count": 0, "stats": {}})
    manifest = _read_json(STATE_DIR / f"prd_manifest_{stamp}.json", {"prds": [], "skipped": []})
    gate = _read_json(STATE_DIR / f"prd_gate_{stamp}.json", [])
    disp = _read_json(STATE_DIR / f"dispatch_{stamp}.json", [])

    date_disp = f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}"
    stats = cand.get("stats", {}) or {}
    passed = [g for g in gate if g.get("verdict") == "pass"]
    dropped = [g for g in gate if g.get("verdict") == "drop"]
    review = [d for d in disp
              if d.get("status") in ("pr_open", "interrupted_pr") and (d.get("verify") or {}).get("pass")]
    failing = [d for d in disp if d.get("verify") and not d["verify"].get("pass")]
    abnormal = [d for d in disp if d.get("status") in ("skip", "planned", "fail", "interrupted_pr")]
    target_repos = sorted({d.get("project", "?") for d in disp})

    def repo_of(d: dict) -> str:
        url = d.get("pr_url", "")
        m = re.search(r"github\.com/([^/]+/[^/]+)/pull/", url)
        if m:                                   # pr_url 有则取 GitHub owner/name（最可读）
            return m.group(1)
        prof = profiles.get(d.get("project"))
        if prof and prof.get("repo"):           # 回退：本地 repo 路径取 basename
            return Path(prof["repo"]).name
        return d.get("project", "?")

    def pr_no(d: dict) -> str:
        return d.get("pr_url", "").rstrip("/").split("/")[-1] or "PR"

    L: list[str] = [f"# 项目推进报告 {date_disp}", "",
                    "## 概览",
                    f"- 今日新内容：{cand.get('today_new_count', 0)} 篇｜"
                    f"技术信号：{stats.get('signals_extracted', 0)}｜"
                    f"候选：{len(cand.get('candidates', []))}｜"
                    f"过闸 PRD：{len(passed)}（drop {len(dropped)}）｜"
                    f"投递目标仓：{len(target_repos)}｜"
                    f"产出 PR：{len([d for d in disp if d.get('status') in ('pr_open', 'interrupted_pr')])}｜"
                    f"验证 failing：{len(failing)}｜"
                    f"失败/超时/跳过：{len([d for d in disp if d.get('status') in ('skip', 'planned', 'fail')])}", ""]

    # ✅ 待 review 绿 PR
    L += ["## ✅ 待你 review 合并的 PR（验证绿）"]
    if review:
        L += ["| 目标仓 | PR | 分支 | PRD |", "|---|---|---|---|"]
        for d in review:
            mark = " ⏸中断PR" if d.get("status") == "interrupted_pr" else ""
            L.append(f"| {repo_of(d)} | [{pr_no(d)}]({d.get('pr_url', '')}){mark} | "
                     f"`{d.get('branch', '')}` | {d.get('slug', '')} |")
    else:
        L.append("（无）")
    L += ["", "> 📝 若某 PR 触碰了既有 `test/*` 文件，review 请重点看测试 diff——"
          "独立验证抓不到测试篡改（§7 盲区）。", ""]

    # 🔴 failing
    L += ["## 🔴 验证 failing（项目自报绿但独立测试红，慎合）"]
    if failing:
        L += ["| 目标仓 | PR | 失败测试 | 说明 |", "|---|---|---|---|"]
        for d in failing:
            v = d.get("verify") or {}
            L.append(f"| {repo_of(d)} | [{pr_no(d)}]({d.get('pr_url', '')}) | "
                     f"`{v.get('test_cmd', '')}` | {v.get('note', '')} |")
    else:
        L.append("（无）")
    L.append("")

    # 🗑 drop
    L += ["## 🗑 PRD 未过质量闸（drop）"]
    if dropped:
        L += ["| 项目 | PRD | critic 理由 |", "|---|---|---|"]
        for g in dropped:
            reason = g.get("summary") or (g.get("issues", [{}])[0].get("reason", "") if g.get("issues") else "")
            L.append(f"| {g.get('project', '?')} | {Path(g.get('prd_path', '')).stem} | {reason} |")
    else:
        L.append("（无）")
    L.append("")

    # ⚠️ 异常/超时/跳过
    L += ["## ⚠️ 异常 / 超时 / 跳过"]
    if abnormal:
        for d in abnormal:
            L.append(f"- [{repo_of(d)}] {d.get('slug', '')}：{d.get('skip_reason') or d.get('status', '')}"
                     "（超 max_prs_in_flight / wall-clock 超时 / 开发 agent 异常 / 幂等前置闸命中）")
    else:
        L.append("（无）")
    L.append("")

    # 📭 未匹配信号
    sig_total = stats.get("signals_extracted", 0)
    matched = len(cand.get("candidates", []))
    unmatched = max(0, sig_total - matched)
    L += ["## 📭 未匹配信号（无目标仓命中）",
          f"- 今日抽出技术信号 {sig_total}，命中候选 {matched}，未匹配 {unmatched}"
          f"（低相关 drop {stats.get('dropped_low_relevance', 0)} / 去重 drop {stats.get('dropped_dedup', 0)}）。", ""]

    # 📊 各目标仓详情
    L.append("## 📊 各目标仓详情")
    by_proj: dict[str, list] = {}
    for d in disp:
        by_proj.setdefault(d.get("project", "?"), []).append(d)
    if by_proj:
        for proj in sorted(by_proj):
            L.append(f"### {proj}")
            for d in by_proj[proj]:
                v = d.get("verify") or {}
                tag = "🟢绿" if v.get("pass") else ("🔴红failing" if v else "?未验证")
                mark = "⏸" if d.get("status") == "interrupted_pr" else ""
                log_link = f"`.project-auto/state/runs/{proj}/{stamp}_{d.get('slug', '')}.log`"
                tail = f"  （skip：{d.get('skip_reason')}）" if d.get("skip_reason") else ""
                L.append(f"- `{d.get('slug', '')}` — [{tag}]{mark} {d.get('pr_url', '')} ｜"
                         f" run log {log_link}{tail}")
            L.append("")
    else:
        L += ["（今日无投递）", ""]

    report_path = REPORT_DIR / f"项目推进报告_{stamp}.md"
    report_path.write_text("\n".join(L) + "\n", encoding="utf-8")
    log(f"[report] 已生成 {report_path}（review {len(review)} / failing {len(failing)} / drop {len(dropped)}）")

    _append_daily_pointer(date_disp, stamp, len(review), len(failing))

    # SMTP 直发（§8：有活才发，全绿不发；--dry-run/--no-notify 只落盘）
    # 心跳模式（PA_HEARTBEAT=1，cron 触发）：全绿也发一封状态邮件——无头服务器上邮件断了即流水线挂了。
    active = bool(review or failing)
    heartbeat = os.environ.get("PA_HEARTBEAT", "").lower() in ("1", "true", "yes")
    if not active and not heartbeat:
        log("[report] 全绿（无待 review 绿 PR / 无 failing）——不发邮件（SPEC §8 全绿不投递）")
        return report_path
    if getattr(args, "dry_run", False) or getattr(args, "no_notify", False):
        tag = "DRY-RUN" if getattr(args, "dry_run", False) else "--no-notify"
        state = "有活但 " + tag if active else "全绿（心跳模式）但 " + tag
        log(f"[report] {state}——不发邮件，报告已落盘")
        return report_path
    _smtp_notify(stamp, report_path, len(review), len(failing), active=active)
    return report_path


def _append_daily_pointer(date_disp: str, stamp: str, n_review: int, n_failing: int) -> None:
    """日报加一行指针指向本报告（§8）；日报不存在则极简创建（仅指针，不侵入既有日报）。"""
    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    daily = DAILY_DIR / f"work-daily-{date_disp}.md"
    pointer = f"- 项目推进报告 → [[项目推进报告_{stamp}]]（{n_review} 待 review / {n_failing} failing）"
    if daily.is_file():
        txt = daily.read_text(encoding="utf-8")
        if f"项目推进报告_{stamp}" not in txt:        # 幂等：同日重出报告不重复加指针
            daily.write_text(txt.rstrip() + "\n" + pointer + "\n", encoding="utf-8")
    else:
        daily.write_text(f"# 工作日报 {date_disp}\n\n{pointer}\n", encoding="utf-8")


def _smtp_notify(stamp: str, report_path: Path, n_review: int, n_failing: int, *, active: bool = True) -> None:
    """发简讯（§8/§10）：标题=N 待 review / M failing；报告为正文+附件。失败退化为告警，不阻塞流水线。

    active=False 时为「全绿心跳」（PA_HEARTBEAT 触发的 cron 模式）——无头服务器上
    邮件断了即流水线挂了，故全绿也发一封状态邮件做心跳。
    """
    suffix = "" if active else "（全绿心跳）"
    subject = f"项目推进 {stamp}｜{n_review} 待 review / {n_failing} failing{suffix}"
    # 收件人：观察期发自己（dvs），稳定后改回 juyf@newland.com.cn（居燕峰）——2026-07-17 上线初用户决策。
    # 如需更灵活可挪到 profile/env（PA_REPORT_TO），当前按用户选择保持简单硬编码 + 醒目注释。
    report_to = "dvs@vip.sina.com"   # 观察期；稳定后改 "juyf@newland.com.cn"
    cmd = [sys.executable, str(SMTP_SEND), "--subject", subject,
           "--body-file", str(report_path), "--to", report_to,
           "--attach", str(report_path)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        log("[report] ✗ SMTP 发送超时——报告已落盘，邮件未发（不阻塞流水线）")
        return
    if r.returncode == 0:
        log(f"[report] ✅ 已 SMTP 直发简讯：{subject}")
    elif r.returncode == 2:
        # 凭据缺失（macOS Keychain / Linux pass）；smtp_send stderr 已打平台对应的修复指引，此处补一句 sina 默认。
        cred_hint = ("security add-generic-password -s sina-smtp -a dvs@vip.sina.com -w"
                     if sys.platform == "darwin" else "pass insert smtp/sina")
        log(f"[report] ✗ SMTP 凭据缺失——报告已落盘；本人执行一次写入后即可自动发：{cred_hint}")
        log(f"    （helper 完整输出：{(r.stderr or '').strip()[:200]}）")
    else:
        log(f"[report] ✗ SMTP 发送失败 rc={r.returncode}（公司 SMTP 不可达/凭据失效？报告已落盘，不阻塞）："
            f"{(r.stderr or '').strip()[:200]}")


# ─── main ───────────────────────────────────────────────────────────
def _load_claude_settings_env() -> None:
    """启动时把 ~/.claude/settings.json 的 env block 里的 ANTHROPIC_* 注入 os.environ（setdefault 语义）。

    背景：settings.json 的 env block 默认只被 claude CLI 自身读取并注入到 CLI 进程；非 CLI 子进程拿不到。
    典型坑——dispatch 段触发 node dev-agent.mjs，其 claude-agent-sdk query() 以 settingSources:["project"]
    派生 claude 子进程（刻意跳过用户级 ~/.claude/settings.json），认证只能靠「启动环境已 export 的 ANTHROPIC_*」
    经进程链继承。若 run_daily.py 由 cron / SSH / 干净 shell 启动（env block 未被 export），SDK 子进程即报
    "Not logged in · Please run /login"。Mac 上手工能跑是因 Claude Code 会话已把 env block 注入自身进程；
    cron 下 Mac 同样会崩（潜伏 bug，迁移 smoke 才暴露）。

    本函数在编排器启动时统一注入 ANTHROPIC_*（只此前缀——避开 OBSIDIAN_VAULT_PATH 等 Mac 专属路径），
    使 CLI persona + node dev-agent 都继承到认证，与启动方式（交互/cron/SSH）无关。setdefault：已在环境中
    显式 export 的不覆盖（与 claude CLI 自身行为一致）。
    """
    sf = Path.home() / ".claude" / "settings.json"
    if not sf.is_file():
        return
    try:
        env = json.loads(sf.read_text(encoding="utf-8")).get("env", {}) or {}
    except (OSError, ValueError) as e:
        log(f"⚠ 读取 {sf} env block 失败（{e}），跳过 ANTHROPIC_* 注入")
        return
    n = 0
    for k, v in env.items():
        if k.startswith("ANTHROPIC_") and k not in os.environ:
            os.environ[k] = str(v)
            n += 1
    if n:
        log(f"[env] 注入 {n} 个 ANTHROPIC_* 自 ~/.claude/settings.json（供 node dev-agent 的 SDK 子进程认证）")


def main():
    ap = argparse.ArgumentParser(description="项目推进流水线·编排器前半段（Phase-2）")
    ap.add_argument("--stamp", default=today_stamp(), help="日期 YYYYMMDD（默认今天）")
    ap.add_argument("--from-stage", choices=STAGES, default="radar")
    ap.add_argument("--to-stage", choices=STAGES, default="dispatch",
                    help="默认跑到 dispatch（含投递）；只验前半段用 critic；出报告/发邮件用 report")
    ap.add_argument("--limit", type=int, default=None, help="封顶今日新内容篇数（dry-run 用）")
    ap.add_argument("--dry-run", action="store_true", help="不 bump consumed marker")
    ap.add_argument("--force", action="store_true", help="忽略已有 state 产物，强制重跑各段")
    ap.add_argument("--dispatch-skip-dev", action="store_true",
                    help="dispatch 段零成本 smoke：过准入但不触发 dev loop（不花钱、不开 PR，仅验证机械逻辑）")
    ap.add_argument("--dispatch-limit", type=int, default=None,
                    help="dispatch 只投前 N 个过闸 PRD（实测控成本用，默认全投）")
    ap.add_argument("--max-concurrent", type=int, default=4,
                    help="dispatch 并行上限（ThreadPool size；默认 4；=1 等价旧顺序）")
    ap.add_argument("--break-lock", action="store_true",
                    help="强拆 run 锁（活锁时用；与 --force 语义无关）")
    ap.add_argument("--no-notify", action="store_true",
                    help="报告段不 SMTP 直发（仍落盘报告+日报指针；cron/wka 默认发）")
    ap.add_argument("--inject-prd", default=None, metavar="PATH",
                    help="inject 段：手写 PRD md 路径（--from-stage inject 时必填）。替 radar→prd，直接产出 manifest")
    args = ap.parse_args()
    if args.from_stage == "inject" and not args.inject_prd:
        ap.error("--from-stage inject 需要 --inject-prd <path>")
    _load_claude_settings_env()   # 供 node dev-agent 的 SDK 子进程拿到 ANTHROPIC_* 认证（cron/SSH 启动兜底）

    # run 级互斥锁（SPEC #30 ③）：锁获取在 STATE_DIR.mkdir 后（默认⑦），包整个 pipeline，try/finally 全路径释放
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if not acquire_run_lock(args.break_lock):
        sys.exit(2)
    try:
        _run_pipeline(args)
    finally:
        release_run_lock()


def _run_pipeline(args) -> None:
    """流水线主体（run 锁由 main 持有）。RuntimeError→exit(1)（state 已落盘可 --from-stage 续跑）。"""
    (STATE_DIR / "prd").mkdir(parents=True, exist_ok=True)
    log(f"═══ 项目推进流水线 {args.stamp} ═══  stage {args.from_stage}→{args.to_stage}"
        f"{'  [DRY-RUN]' if args.dry_run else ''}{'  [LIMIT='+str(args.limit)+']' if args.limit else ''}"
        f"{'  [INJECT='+str(args.inject_prd)+']' if args.inject_prd else ''}")

    sources = load_sources()
    profiles = load_profiles()
    log(f"sources={[s['name'] for s in sources]}  profiles={list(profiles)}")

    run = {s: i for i, s in enumerate(STAGES)}
    lo, hi = run[args.from_stage], run[args.to_stage]

    candidates_payload, manifest, gate, dispatch = {"candidates": []}, {"prds": []}, [], []
    stamp = args.stamp   # inject 段可能自增（避碰），下游 critic/dispatch/report 用此 stamp 对齐文件名
    try:
        if lo <= 0 <= hi:
            stage_fetch(args, sources, args.stamp)
        if lo <= 1 <= hi:
            candidates_payload = stage_radar(args, sources, profiles, args.stamp)
        if lo <= 2 <= hi:
            manifest = stage_prd(args, candidates_payload, profiles, args.stamp)
        if lo <= 3 <= hi and getattr(args, "inject_prd", None):
            manifest, stamp = stage_inject(args, profiles, stamp)   # 手动注入 PRD（替 radar→prd）
        if lo <= 4 <= hi:
            gate = stage_critic(args, manifest, profiles, stamp)
        if lo <= 5 <= hi:
            if not gate:   # --from-stage dispatch：critic 未跑，从盘读 prd_gate
                gf = STATE_DIR / f"prd_gate_{stamp}.json"
                gate = json.loads(gf.read_text(encoding="utf-8")) if gf.is_file() else []
            dispatch = stage_dispatch(args, gate, profiles, stamp)
        if lo <= 6 <= hi:
            stage_report(args, profiles, stamp)
    except RuntimeError as e:
        log(f"✗ {e}")
        log("（state 产物已落盘，修参后可 --from-stage 续跑）")
        sys.exit(1)

    # 汇总
    passed = [e for e in gate if e.get("verdict") == "pass"]
    dropped = [e for e in gate if e.get("verdict") == "drop"]
    log(f"\n═══ 汇总 {stamp} ═══")
    log(f"  今日新：{candidates_payload.get('today_new_count','?')}｜candidates：{len(candidates_payload.get('candidates',[]))}")
    log(f"  PRD 产出：{len(manifest.get('prds',[]))}（skipped {len(manifest.get('skipped',[]))}）")
    log(f"  质量闸：✅ pass {len(passed)}｜🗑 drop {len(dropped)}")
    for e in passed:
        log(f"    ✅ {e['prd_path']}")
    for e in dropped:
        log(f"    🗑 {e['prd_path']} — {e.get('summary','')}")
    log("  过闸 PRD 见 state/prd/，gate 记录见 state/prd_gate_{stamp}.json。")
    # dispatch 段汇总
    if dispatch:
        opened = [r for r in dispatch if r.get("status") in ("pr_open", "interrupted_pr")]
        skipped = [r for r in dispatch if r.get("status") == "skip"]
        planned = [r for r in dispatch if r.get("status") == "planned"]
        green = [r for r in dispatch if (r.get("verify") or {}).get("pass")]
        red = [r for r in dispatch if r.get("verify") and not r["verify"].get("pass")]
        log(f"  dispatch：投递 {len(dispatch)}｜✅ PR {len(opened)}｜🟢 验证绿 {len(green)}｜🔴 红 {len(red)}"
            f"｜⏭ 跳过 {len(skipped)}｜📋 planned {len(planned)}")
        for r in opened:
            v = r.get("verify") or {}
            tag = "🟢绿" if v.get("pass") else ("🔴红failing" if v else "?未验证")
            mark = "⏸" if r.get("status") == "interrupted_pr" else "✅"
            log(f"    {mark} {r.get('pr_url')} [{tag}] — {r.get('slug')}")
        for r in skipped + planned:
            log(f"    ⏭ {r.get('slug')}: {r.get('skip_reason')}")
        log("  dispatch 记录见 state/dispatch_{stamp}.json，dev 日志见 state/runs/<project>/。")


if __name__ == "__main__":
    main()
