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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from slug_utils import dev_slugify   # ADR-0006 #5：分支 slug 单一源头（消解 ADR-0004 #4 shadow；无依赖模块，顶部 import 不触发 sdk 连带加载拖垮 cron）
from stage_contracts import validate_stage, render_repair_hint  # change 2026-07-28：stage 输出契约层（fail-open 校验 + 诊断重试提示；纯 stdlib，cron 安全）
from external_state import ExtResult, ExtState, found, not_found, unknown, sanitize   # OpenSpec fail-safe-dispatch：三态远程查询结果 + 诊断脱敏（纯 stdlib 模块，cron 安全）
from coordinator import build_coordinator, preflight  # task 2.1/2.5：runtime coordinator 边界 + flag 组合 preflight（design 决策#1；纯 stdlib，cron 安全）
from feature_flags import resolve_flags  # add-cross-prd-learning-memory Section 7：learning flag 解析（V1 allowlist + env/profile 三态）
from loop_runtime import ShadowJournal     # task 3.2：shadow journal 旁路写入器（类型注解用；纯 stdlib，cron 安全）
import journal as J                        # task 3.4：driven retry 读 journal events → recovery context（纯 stdlib）
import recovery_context as RC              # task 3.4：driven retry prompt 从 immutable PRD + journal artifacts（纯函数）
import artifact_store                      # task 3.3：内容寻址工件存储（verify feedback artifact；纯 stdlib）
import reconcile                           # task 4.4：ArtifactEvidenceResolver（publication 前 reconcile test evidence）
import retry_policy as RP                  # task 3.3 P0-3：run_daily 驱动 retry（decide/recover_iteration/budget/session 参数生成）
import cutover                             # add-cross-prd-learning-memory 批次 2 升级 A：injection 四重 gate（shadow+parity+quality+allowlist）
import ids as loop_ids                     # single-flight-auto-merge task 2.2：slot 事件审计归属 IDs（run_id/prd_id/iteration_id，纯 stdlib）
import merge_phase as MP                   # single-flight-auto-merge task 3.2：merge phase 机械判定（classify_rebase/MergeResult/parse_merge_result/build_merge_cmd；纯 stdlib 零 git/SDK，cron 安全）
import single_flight as SF                 # single-flight-auto-merge task 2.2/2.3：per-owner_repo 跨进程 slot（flock + journal；Linux fcntl，cron 安全）
import circuit_breaker as CB               # single-flight-auto-merge task 4.4：revert 循环熔断（同幂等键 PRD cooldown 窗口内禁再 auto-merge→triage；纯 stdlib journal，cron 安全）
import critical_alert as CA                # single-flight-auto-merge task 4.5：CRITICAL 告警 durable 化（halt→独立 alerts journal，不受 flag gating，crash 不丢；纯 stdlib，cron 安全）
import main_status as MS                   # single-flight-auto-merge task 4.6：main 瞬态红契约 + 可查询 post-merge 验证状态（per-owner_repo journal，不受 flag gating；纯 stdlib，cron 安全）
import merge_loop as ML                    # single-flight-auto-merge task 6.x 方案 C：merge/revert 闭环 crash 安全门（intent→push→confirm 写顺序；has_open_intent 阻盲目重 merge；纯 stdlib journal，cron 安全）
# add-cross-prd-learning-memory Section 7 接线（控制面纯 stdlib 模块，cron 隔离不变）：
#   envelope 构造（journal events → sanitized TerminalEnvelope）+ reflection（read-only SDK，mock-SDK 可注入）+
#   retrieval（dispatch-entry catalog 检索 + lesson block 渲染）+ effectiveness（memory_mode record）。
#   均零模块级 SDK 导入；本接线层 fail-open：shadow=off / profile 未启用 → 零副作用（design 决策#7/#8）。
import learning_memory_envelope as LME
import learning_memory_reflection as LMRefl
import learning_memory_retrieval as LMRet
import learning_memory_effectiveness as LMEff
import learning_memory_schema as LM     # UsageOutcomeKind 受控词表（unknown 跳过判定的真源）
import learning_memory_store as LMS     # append_usage_outcome（Section 6 闭环持久化）

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
DEV_AGENT_PY = PROJECT_DIR / "scripts" / "dev-agent.py"  # 控制面标准执行器（ADR-0006：dispatch 唯一调用源；仓内遗留 dev-agent.{py,mjs} 不再使用）

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
    sys.exit("✗ 找不到 claude CLI（试 PA_CLAUDE_BIN 环境变量或装 Claude Code）")


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


def _normalize_projects(raw: list[str] | None) -> list[str] | None:
    """``--project`` 归一：append（可重复）+ 逗号分隔混用 → 去空白 list；None/空 → None（baseline 不过滤）。
    canary 隔离用：``--project cc-web-control`` 只跑该仓。"""
    if not raw:
        return None
    out: list[str] = []
    for part in raw:
        out.extend(p.strip() for p in part.split(",") if p.strip())
    return out or None


def _filter_profiles(profiles: dict, project: list[str] | None) -> dict:
    """``--project`` 单/多仓限制：只保留命中的已准入 profile（canary 隔离核心）。
    None/空 → 全量透传（baseline）。命中不存在的项目 → 硬错（绝不静默跑空，免 canary 误判「无产出=绿」）。"""
    if not project:
        return profiles
    missing = [p for p in project if p not in profiles]
    if missing:
        sys.exit(f"✗ --project 指定项目不在已准入 profiles 中：{missing}（可用：{list(profiles)}）")
    keep = set(project)
    return {k: v for k, v in profiles.items() if k in keep}


def _apply_state_dir(override: str | None) -> None:
    """``--state-dir`` 覆盖：重绑模块级 STATE_DIR + RUN_LOCK。RUN_LOCK 在 import 时从 STATE_DIR 派生
    （L121），若不一并重算，隔离金丝雀的 run 锁仍落真实 ``state/.run.lock`` → 与真实 cron 互斥、且污染真 state。
    须在 ``acquire_run_lock`` / ``STATE_DIR.mkdir`` 之前调。"""
    global STATE_DIR, RUN_LOCK
    if override:
        STATE_DIR = Path(override).resolve()
        RUN_LOCK = STATE_DIR / ".run.lock"


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
    # 取数窗口：window_days>0 → [today-N, today] 滚动窗口（回溯，ADR-0007 follow-up，
    #   防整理产品晚产出被水位线跳过）；缺省/0 → 现状水位线（>marker，单调不回溯）。
    #   window 源幂等不靠 marker，靠 stage_radar 出口的 source_path 去重 + dispatch GitHub 去重。
    window_days = int(source.get("window_days", 0) or 0)
    floor = (date.today() - timedelta(days=window_days)).strftime("%Y%m%d") if window_days > 0 else None
    out = []
    for p in sorted(seen):
        m = re.match(r"(\d{8})", p.name)
        if not m:
            continue
        fd = m.group(1)
        if window_days > 0:
            if fd < floor:               # 早于窗口下沿 → 太老，跳过（窗口内全取，不读 marker 下界）
                continue
        elif fd <= marker_stamp:         # 现状水位线：只取 > marker
            continue
        out.append(p)
    if limit:
        out = out[:limit]
    return out


def _prd_frontmatter_source(path: Path) -> str:
    """读一份 PRD 的 frontmatter source_path（内容指纹键）。无 frontmatter / 解析失败 → ""。"""
    try:
        txt = path.read_text(encoding="utf-8")
    except Exception:
        return ""
    if not txt.startswith("---"):
        return ""
    parts = txt.split("---", 2)
    if len(parts) < 3:
        return ""
    try:
        return (yaml.safe_load(parts[1]) or {}).get("source_path") or ""
    except Exception:
        return ""


def already_prd_sources(profiles: dict) -> set[str]:
    """各项目 state/prd/<project>/*.md frontmatter source_path 集合。

    窗口源回溯重取时的内容指纹去重键（ADR-0007 follow-up）：source_path 已产过 PRD
    → 不再重喂下游，防重复 PRD 文件 + critic 重审。PRD frontmatter 契约已有 source_path
    （pa-prd 实证），无需改契约。"""
    done: set[str] = set()
    for name in profiles:
        d = STATE_DIR / "prd" / name
        if not d.is_dir():
            continue
        for p in d.glob("*.md"):
            sp = _prd_frontmatter_source(p)
            if sp:
                done.add(sp)
    return done


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
            # 语义契约层（change 2026-07-28 治本）：fail-open 校验；error→带诊断重试，warning→记 log 不改行为
            # 与语法层共享 for attempt (1,2) 预算（cap=2，满足 spec「有上限预算」）；与 dev-agent --feedback-artifact 范式对称
            issues = validate_stage(stage, payload)
            err_issues = [i for i in issues if i.severity == "error"]
            if err_issues:
                last_err = "语义契约违反: " + "; ".join(f"{i.field}({i.diagnosis})" for i in err_issues)
                if attempt < 2:   # 还有重试预算：带诊断重试一轮
                    log(f"[{label}] {last_err} → 带诊断重试（attempt {attempt}/2）")
                    cur_prompt = prompt + render_repair_hint(err_issues, json.dumps(payload, ensure_ascii=False)[:300], attempt=attempt)
                    continue
                log(f"[{label}] {last_err}（重试预算用尽，fail-open 降级返回现状 payload）")
                return payload, meta
            warn_issues = [i for i in issues if i.severity == "warning"]
            if warn_issues:
                log(f"[{label}] 契约 warning（不改行为）: " + ", ".join(i.field for i in warn_issues))
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


# ─── fetch 段（persona-based 源 fetcher：按 FETCH_CONFIG[kind] 分发 → 落 YYYYMMDD_*.md）────
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


def wechat_url_prompt(src: dict) -> str:
    """① wechat-url：把 params.urls 喂给 pa-fetch-wechat-url，要 Contract A items JSON。"""
    urls = (src.get("params") or {}).get("urls") or []
    url_block = "\n".join(f"- {u}" for u in urls) if urls else f"- {src['name']}"
    return f"""你是 pa-fetch-wechat-url。抓取以下微信文章（mp.weixin.qq.com）正文，逐篇 normalize 成 markdown。
文章 URL（采集源 {src['name']}）：
{url_block}

每篇先试 mcp__web_reader__webReader(url=..., return_format='markdown')；抓失败/正文明显残缺（如只剩导航）→ 用 mcp__plugin_ecc_exa__web_fetch_exa(urls=[url]) 兜底；都失败该篇 fetched_via='failed'、markdown 留空。
严格按 persona 输出契约：只吐一行 JSON，结构 {{"items":[{{"url":"...","title":"<篇名（ascii 优先便于 slug）>","markdown":"<干净正文 md，换行 \\n 转义>","fetched_via":"web_reader|exa|failed","ok":true}}]}}。"""


def github_repo_prompt(src: dict) -> str:
    """② github-repo：把 params.repos + window 喂给 pa-fetch-github-repo，gh CLI 拉活动，要 items JSON。"""
    params = src.get("params") or {}
    repos = params.get("repos") or []
    window = params.get("window", "7d")
    repo_block = "\n".join(f"- {r}" for r in repos) if repos else f"- {src['name']}"
    return f"""你是 pa-fetch-github-repo。监控以下 GitHub 仓库近 {window} 的活动，逐仓 summarize 成 markdown digest（radar 可消费）。
仓库（采集源 {src['name']}）：
{repo_block}
窗口：{window}

每仓用 Bash 跑 gh CLI：`gh api repos/OWNER/REPO/commits?per_page=30` 拉最近 commit、`gh api repos/OWNER/REPO/pulls?state=all&sort=updated&per_page=20` 拉最近 PR；按窗口筛日期；summarize 成「近期 commit（msg/日期/作者）+ 合并 PR（标题/url）」markdown。github MCP 在 headless 不可用，**必须用 gh CLI**。
严格按 persona 输出契约：只吐一行 JSON，结构 {{"items":[{{"repo":"owner/repo","title":"<owner-repo 窗口摘要（ascii）>","markdown":"<digest md，换行 \\n 转义>","commits_count":N,"prs_count":M}}]}}。"""


# kind → fetcher 配置。mode: "single"=一份合成 md（agent-deepresearch）；
#       "items"=一次调用产 N 篇（wechat-url/github-repo，Contract A 每 item 一文件）。
FETCH_CONFIG: dict[str, dict] = {
    "agent-deepresearch": {"agent": FETCH_AGENT, "tools": FETCH_ALLOWED_TOOLS,
                           "prompt": fetch_prompt, "mode": "single"},
    "wechat-url":         {"agent": "pa-fetch-wechat-url",
                           "tools": ["mcp__web_reader__webReader",
                                     "mcp__plugin_ecc_exa__web_fetch_exa"],
                           "prompt": wechat_url_prompt, "mode": "items"},
    "github-repo":        {"agent": "pa-fetch-github-repo",
                           # gh CLI 经 Bash（github MCP headless 不可用）。冒烟：headless 首次 Bash 调用偶发
                           # permission_denial（scope 无关——Bash(gh api:*) 与 plain Bash 都中，疑似首次调用 gate；
                           # 后续调用正常），persona 内置「deny 即重试一次」吸收。选 plain Bash：恢复调用不受 scope 限。
                           "tools": ["Bash"],
                           "prompt": github_repo_prompt, "mode": "items"},
}


def _payload_to_items(payload: dict, mode: str, src: dict) -> list[tuple[str, str, dict]]:
    """统一落盘视角：single → [(title, md, payload)]；items → payload['items'] 逐条。
    返回 (title, markdown_stripped, raw) 三元组列表，raw 透传 kind-specific 字段（如 sources_count）。"""
    if mode == "items":
        return [(it.get("title") or src["name"], (it.get("markdown") or "").strip(), it)
                for it in (payload.get("items") or [])]
    return [(payload.get("title") or src["name"], (payload.get("markdown") or "").strip(), payload)]


def stage_fetch(args, sources, stamp) -> dict:
    """persona-based 源 fetcher：按 FETCH_CONFIG[kind] 分发 agent → 落 YYYYMMDD_<slug>.md 到 source.root。

    directory/local-file/未知 kind 不在 FETCH_CONFIG → 跳过（无 fetcher）。
    mode=items：一次 agent 调用产 N 篇 → N 文件（Contract A）；mode=single：一份合成 md（agent-deepresearch）。
    fetch 不碰 marker（radar 消费后才 bump，ADR-0007 #3）；--dry-run 不影响 fetch（写文件是 fetch 的全部意义）。
    复用门：fetch_{stamp}.json 已存在且非 --force → 复用（成本护栏，镜像 stage_radar:507-509，防重跑重花）。
    per-source try/except + per-item 跳空 md：一个源/一篇炸只跳过它，不拖垮整段 fetch（fault isolation）。"""
    fetch_file = STATE_DIR / f"fetch_{stamp}.json"
    if fetch_file.is_file() and not getattr(args, "force", False):
        log(f"[fetch] 复用已有 {fetch_file.name}（--force 重跑）")
        return json.loads(fetch_file.read_text(encoding="utf-8"))
    produced = []
    for src in sources:
        cfg = FETCH_CONFIG.get(src.get("kind"))
        if not cfg:                                   # directory/local-file/未知 → 无 fetcher，跳过
            continue
        try:
            root = VAULT_ROOT / src["root"]
            root.mkdir(parents=True, exist_ok=True)
            payload, meta = run_persona(cfg["agent"], cfg["prompt"](src), "fetch",
                                        f"fetch-{src['name']}", allowed_tools=cfg["tools"])
            items = _payload_to_items(payload, cfg["mode"], src)
            if not items:
                log(f"[fetch] ⚠ {src['name']} agent 未返回任何 item（跳过落盘）")
                continue
            for title, md, raw in items:
                if not md:
                    log(f"[fetch] ⚠ {src['name']} item「{title}」markdown 空（跳过该 item）")
                    continue
                slug = dev_slugify(title) or src["name"]            # 复用 ADR-0006 单一源头
                out = root / f"{stamp}_{slug}.md"
                out.write_text(md, encoding="utf-8")
                entry = {"source": src["name"], "title": title,
                         "path": str(out.relative_to(VAULT_ROOT)),
                         "cost": meta["cost"], "turns": meta["turns"]}
                if "sources_count" in raw:            # kind-specific 透传（agent-deepresearch；保 ③ 既有测试绿）
                    entry["sources_count"] = raw["sources_count"]
                produced.append(entry)
                log(f"[fetch] ✅ {src['name']} → {out.relative_to(VAULT_ROOT)}｜「{title}」"
                    + (f" sources={raw['sources_count']}" if "sources_count" in raw else ""))
        except Exception as e:
            log(f"[fetch] ✗ {src['name']} 失败（跳过，不拖垮其他源）：{e}")
            continue
    out_json = {"produced": produced, "stamp": stamp}
    fetch_file.write_text(json.dumps(out_json, ensure_ascii=False, indent=2), encoding="utf-8")
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
        wd = src.get("window_days", 0) or 0
        log(f"[radar] source={src['name']} kind={src.get('kind','directory')} "
            f"marker={marker} window={'off' if not wd else wd}｜今日新={len(today_new)}")
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
    done_sources = already_prd_sources(profiles)   # 窗口源回溯去重：source_path 已产 PRD 的不再喂下游
    all_candidates: list[dict] = []
    per_project_stats: dict[str, dict] = {}
    for proj, src_files in proj_src_files.items():
        flat = [f for _, fs in src_files for f in fs]
        if not flat:
            continue                                      # 无订阅文件 → 不调
        # 前置去重（省 LLM）：已产 PRD 的文件不进 radar_prompt。done_sources = source_path 集合
        # （vault 相对），与 flat 文件 relative_to(VAULT_ROOT) 同格式。与下方出口 candidate 去重
        # （sp in done_sources）互补：出口防重复产 PRD，前置防重复 Read 已处理文件。
        flat = [p for p in flat if str(p.relative_to(VAULT_ROOT)) not in done_sources]
        if not flat:
            log(f"[radar] ⏭ {proj}: 订阅文件均已产 PRD，跳过 radar 调用（省 LLM）")
            continue
        payload, meta = run_persona(
            "pa-radar", radar_prompt(proj, flat, profiles[proj], dedup.get(proj, [])),
            "radar", f"radar-{proj}")
        for c in payload.get("candidates", []):
            c.setdefault("project", proj)
            c.setdefault("source", _source_of(c, src_files))   # 追溯来自哪个源（决定 #6）
            sp = c.get("source_path")
            if sp and sp in done_sources:                      # 已为该 source 产过 PRD → 去重，防重复 PRD/critic
                log(f"[radar] ⏭ 去重：{sp} 已有 PRD，跳过 candidate")
                continue
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
        # Phase 0 止血（change 2026-07-28）：prd 缺 path → 降级跳过，不 Path(None).stem TypeError 穿透 abort
        if not path:
            log(f"[critic] ⚠ prd 缺 path（project={proj}）→ 降级 drop")
            entries.append({"prd_path": path, "project": proj, "verdict": "drop",
                            "summary": "prd manifest 缺 path，无法过闸"}); continue
        entry = _critic_one(path, src, prof)
        # Phase 0 止血：critic 漏吐 verdict → 降级 drop，不 entry["verdict"] KeyError 穿透 _run_pipeline except
        if "verdict" not in entry:
            log(f"[critic] ⚠ critic 漏吐 verdict（{path}）→ 降级 drop")
            entry.setdefault("prd_path", path); entry.setdefault("project", proj)
            entry["verdict"] = "drop"; entry.setdefault("summary", "critic 输出缺 verdict 字段，降级")
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


def check_branch_protection(owner_repo: str, base: str) -> ExtResult:
    """运行时实查 main 分支保护（SPEC §4.4：protection 是平台态可被外部改动，故运行时实查、不进静态 profile）。

    三态（OpenSpec fail-safe-dispatch）：
      FOUND(True)  200 → 已保护（可投）；
      NOT_FOUND    404 → 明确未保护（拒投，属「可决断」非阻断态）；
      UNKNOWN      超时/非零/缺 gh/异常 → 状态不明，fail-safe：准入见之即 blocked_external_state。"""
    try:
        r = subprocess.run(
            ["gh", "api", f"repos/{owner_repo}/branches/{base}/protection", "--silent",
             "-H", "Accept: application/vnd.github+json"],
            capture_output=True, text=True, timeout=20)
        if r.returncode == 0:
            return found(True, "已保护")
        if "404" in (r.stderr or ""):
            return not_found("未保护（404），拒投")
        return unknown(f"查询失败 rc={r.returncode}: {(r.stderr or '').strip()[:120]}")
    except subprocess.TimeoutExpired:
        return unknown("查询超时")
    except FileNotFoundError:
        return unknown("缺 gh 命令（未安装/不在 PATH）")
    except Exception as e:
        return unknown(f"查询异常: {e}")


def count_inflight_prs(owner_repo: str) -> ExtResult:
    """在途开放 PR 数（SPEC R1：≤ max_prs_in_flight）。

    三态：FOUND(count) 查询成功（0 个开放 PR 亦为 FOUND(0)，可决断）；UNKNOWN 查询失败。
    旧版「失败返 0（容忍）」是 fail-open——可能超额投递；现改 fail-safe：UNKNOWN → 准入阻断。"""
    try:
        js = subprocess.run(
            ["gh", "pr", "list", "-R", owner_repo, "--state", "open", "--limit", "50", "--json", "number"],
            capture_output=True, text=True, timeout=20)
        if js.returncode != 0:
            return unknown(f"gh pr list rc={js.returncode}: {(js.stderr or '').strip()[:120]}")
        return found(len(json.loads(js.stdout or "[]")))
    except json.JSONDecodeError as e:
        return unknown(f"坏 JSON: {e}")
    except subprocess.TimeoutExpired:
        return unknown("查询超时")
    except FileNotFoundError:
        return unknown("缺 gh 命令（未安装/不在 PATH）")
    except Exception as e:
        return unknown(f"查询异常: {e}")


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


def already_dispatched(owner_repo: str, repo: str, devslug: str) -> ExtResult:
    """幂等前置闸（SPEC #30 ④ / ADR-0004 §4）：按 slug 子串查 GitHub PR(all state) + 远端 auto/* 分支。

    三态（OpenSpec fail-safe-dispatch）：
      FOUND(True)  命中已有 PR 或远端 auto/* 分支（slug=date+24字描述够特异，子串误命中可忽略）；
      NOT_FOUND    两路查询都成功且无命中；
      UNKNOWN      任一查询失败（旧版「任一失败容忍返 (False,"")」是 fail-open——可能重复投递；现改 fail-safe 阻断）。
    用 slug 子串而非精确 --head <branch>，因 dev-agent stamp()=YYYYMMDD-HHMM（含时分）不可预测。"""
    pr_err, pr_hit = _scan_pr_list_for_slug(owner_repo, devslug)
    if pr_hit:
        return found(True, pr_hit)
    branch_err, branch_hit = _scan_remote_branches_for_slug(repo, devslug)
    if branch_hit:
        return found(True, branch_hit)
    if pr_err or branch_err:
        return unknown(" / ".join(e for e in (pr_err, branch_err) if e))
    return not_found()


def _scan_pr_list_for_slug(owner_repo: str, devslug: str) -> tuple[str | None, str | None]:
    """gh pr list(all state) 里找含 devslug 的分支名。返回 (err, hit_reason)；成功无命中→(None, None)。"""
    try:
        js = subprocess.run(
            ["gh", "pr", "list", "-R", owner_repo, "--state", "all", "--limit", "100",
             "--json", "number,headRefName,state"],
            capture_output=True, text=True, timeout=20)
        if js.returncode != 0:
            return f"gh pr list rc={js.returncode}: {(js.stderr or '').strip()[:80]}", None
        for pr in json.loads(js.stdout or "[]"):
            if devslug in (pr.get("headRefName") or ""):
                return None, f"已投递（PR #{pr.get('number')} {pr.get('state')}，分支 {pr.get('headRefName')}）"
        return None, None
    except json.JSONDecodeError as e:
        return f"坏 PR JSON: {e}", None
    except subprocess.TimeoutExpired:
        return "PR 查询超时", None
    except FileNotFoundError:
        return "缺 gh 命令", None
    except Exception as e:
        return f"PR 查询异常: {e}", None


def _scan_remote_branches_for_slug(repo: str, devslug: str) -> tuple[str | None, str | None]:
    """git ls-remote origin 里找 auto/* 且含 devslug 的分支。返回 (err, hit_reason)；成功无命中→(None, None)。"""
    try:
        out = subprocess.run(["git", "-C", repo, "ls-remote", "--heads", "origin"],
                             capture_output=True, text=True, timeout=20)
        if out.returncode != 0:
            return f"ls-remote rc={out.returncode}: {(out.stderr or '').strip()[:80]}", None
        for line in out.stdout.splitlines():
            if "\t" not in line:
                continue
            ref = line.split("\t", 1)[1].replace("refs/heads/", "")
            if ref.startswith("auto/") and devslug in ref:
                return None, f"已投递（远端分支 {ref}，无 PR）"
        return None, None
    except subprocess.TimeoutExpired:
        return "ls-remote 超时", None
    except FileNotFoundError:
        return "缺 git 命令", None
    except Exception as e:
        return f"ls-remote 异常: {e}", None


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
def _dev_cmd(prof: dict, prd_abs: str, base: str, src_abs: str,
             feedback_artifact: str | None = None, *,
             state_dir: str | None = None, iteration_seq: int = 0,
             resume_session: str | None = None, fork_session: bool = False,
             lessons_artifact: str | None = None) -> list[str] | None:
    """构造 dev-agent 触发命令（ADR-0006：控制面标准执行器为唯一源）。

    始终调用控制面 ``scripts/dev-agent.py``（DEV_AGENT_PY）——执行器贴目标仓跑（cwd=调用方传入的
    worktree，见 ``_run_capture`` 的 ``wt``），源码归控制面。目标仓语言不决定执行器语言；Python 运行时
    按 profile.conda_env / 宿主解析（``_env_python``）。仓内遗留 ``scripts/dev-agent.{py,mjs}`` 不再使用
    （legacy ignored，OpenSpec verified-dev-execution）——``dev_agent_source`` 旧 profile 字段保留读取兼容
    （不报错），但其值不再分支：无论 vault 还是 repo，都走控制面执行器。

    --base 由 verify 闭环调用方按轮次传入（round1=默认分支；round≥2=上次 dev 分支，增量重投）。
    --feedback-artifact（task 3.4）：driven（``journal_driven_dispatch``）模式 retry（round≥2）从 immutable
        PRD + journal feedback artifact 构 prompt——传 round_n 的 ``verifier_feedback`` artifact path
        （``build_recovery_context`` 从 journal 抽），dev-agent 读它 inject prompt（baseline 照旧读 PRD 反馈节）。
    --lessons-artifact（add-cross-prd-learning-memory Section 5 接线）：cross_prd_learning_injection 开仓时，
        控制面在 dispatch-entry 把检索出的 lesson block 写成 content-addressed artifact，path 透传给目标面
        dev-agent（与 ``--feedback-artifact`` 完全对称的模式；ADR-0001 控制面/目标面隔离：dev-agent 只读
        传入的 path，**不读控制面 state**）。baseline / injection=off → None（dev-agent build_prompt 跳过注入）。
    控制面 dev-agent.py 缺失（DEV_AGENT_PY 不存在）→ None（dispatch_one 判 fail：控制面安装异常）。"""
    if not DEV_AGENT_PY.exists():
        return None
    cmd = [_env_python(prof.get("conda_env", "")), str(DEV_AGENT_PY),
           "--prd", prd_abs, "--branch-prefix", "auto", "--base", base]
    if src_abs:
        cmd += ["--source", src_abs]
    if feedback_artifact:    # task 3.4：driven retry prompt 从 feedback artifact（PRD 不可变，反馈在 artifact）
        cmd += ["--feedback-artifact", feedback_artifact]
    if lessons_artifact:     # Section 5 接线：lesson block artifact path（None=不注入，dev-agent baseline prompt）
        cmd += ["--lessons-artifact", lessons_artifact]
    # task 3.3 P0-3：session-aware retry 参数（run_daily 据 RetryPolicy.decide 生成 → dev-agent 透传 SDK）。
    #   state_dir=控制面 STATE_DIR → dev-agent session_store 与控制面同一（retry 读 dev-agent 持久化 session_id）。
    if state_dir:
        cmd += ["--state-dir", state_dir]
    if iteration_seq > 0:          # seq=0 baseline（新 session）；seq>0 retry 衍生 distinct iteration
        cmd += ["--iteration-seq", str(iteration_seq)]
    if resume_session:
        cmd += ["--resume-session", resume_session]
    if fork_session:
        cmd += ["--fork-session"]
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


def _has_commits(repo: str, base_ref: str, branch: str) -> ExtResult:
    """branch 相对 base_ref 是否有新 commit（verify 闸门 + verify 闭环判增量产出用）。

    三态：FOUND(bool) 查询成功；UNKNOWN 查询失败。旧版「失败容忍→False」是 fail-open——reconcile 会把
    「查不出 commit」当成「无 commit」误删有产出的分支；现改 fail-safe：reconcile 见 UNKNOWN 即保留分支。"""
    try:
        r = subprocess.run(["git", "-C", repo, "log", f"{base_ref}..{branch}", "--oneline"],
                           capture_output=True, text=True, timeout=20)
        if r.returncode != 0:
            return unknown(f"git log rc={r.returncode}: {(r.stderr or '').strip()[:120]}")
        return found(bool(r.stdout.strip()))
    except subprocess.TimeoutExpired:
        return unknown("查询超时")
    except FileNotFoundError:
        return unknown("缺 git 命令")
    except Exception as e:
        return unknown(f"查询异常: {e}")


def _lookup_pr(owner_repo: str, branch: str) -> ExtResult:
    """实查 GitHub 是否已有该分支的 PR（reconcile 对账用，OpenSpec fail-safe-dispatch）。

    三态：FOUND(pr_dict) 命中；NOT_FOUND 明确无；UNKNOWN 查询失败（reconcile 见之即保留分支不补开/删除）。"""
    try:
        js = subprocess.run(
            ["gh", "pr", "list", "-R", owner_repo, "--head", branch, "--state", "all",
             "--limit", "5", "--json", "number,url,state"],
            capture_output=True, text=True, timeout=20)
        if js.returncode != 0:
            return unknown(f"gh pr list rc={js.returncode}: {(js.stderr or '').strip()[:120]}")
        prs = json.loads(js.stdout or "[]")
        if prs:
            return found(prs[0])
        return not_found(f"无 {branch} 的 PR")
    except json.JSONDecodeError as e:
        return unknown(f"坏 PR JSON: {e}")
    except subprocess.TimeoutExpired:
        return unknown("PR 查询超时")
    except FileNotFoundError:
        return unknown("缺 gh 命令")
    except Exception as e:
        return unknown(f"PR 查询异常: {e}")


def _dump_branch_diff(repo: str, base_ref: str, branch: str, out_path: Path) -> None:
    """落 branch 相对 base_ref 的 diff 到文件（喂 pa-verify Read；ADR-0002：只读 git，不改目标仓）。失败→空 diff 文件。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        out = subprocess.run(["git", "-C", repo, "diff", f"{base_ref}..{branch}"],
                             capture_output=True, text=True, timeout=120).stdout
    except Exception:
        out = ""
    out_path.write_text(out or "（diff 为空或获取失败）", encoding="utf-8")


def _append_verify_feedback(prd_abs: str, feedback_section: str, round_n: int, *,
                            sj: ShadowJournal | None = None, iter_id: str = "", prd_id: str = "",
                            artifact_root: Path | None = None, driven: bool = False) -> str | None:
    """把 pa-verify 反馈节追加进 PRD 末尾（baseline）+ shadow/drift 阶段落 journal artifact（task 3.2/3.3）。

    反馈是施工指引（非需求变更），故不重过 pa-prd-critic 闸（docs/verify-commit-loop-design.md §3-④）。
    **task 3.3 shadow 双写**：``journal_shadow`` flag 开（``sj.enabled``）时，feedback 额外写成 content-addressed
    artifact（``verifier_feedback`` / ``sanitized``，密钥脱敏后算 digest）+ emit 事件。
    **task 3.2 driven 模式**：``driven``（``journal_driven_dispatch`` flag，dispatch_one 传入）开 → **摘除 PRD 追加**
    （spec「Immutable new-run input」：original PRD byte-for-byte unchanged，feedback 只 content-addressed artifact
    真源 + journal 事件）。verify 闭环下一轮读路径切 artifact 由 task 3.4 配合（retry prompt 读 artifact）；
    driven flag 真正 enable 在 task 7.5 cutover——3.2-3.4 完成前 driven 默认关，verify 闭环照旧读 PRD（不破）。
    driven 模式 artifact 写失败 → shadow 契约吞异常，反馈丢失（known，task 4.2 evidence-integrity fail closed 补）。

    Returns:
        feedback artifact digest（``sha256:<hex>``；shadow/drift store 成功时）；``None`` = 未 store
        （baseline flag 关 / store 失败 / 无 feedback_section）。task 3.3 下轮 iteration 引用此 digest（spec
        scenario「next attempt references ... feedback artifact」）。
    """
    section = (f"\n\n## ⚠️ 审核反馈（verify 第{round_n}轮·非需求变更，未重过 critic 闸）\n\n"
               + (feedback_section or "").strip() + "\n")
    digest: str | None = None
    # shadow 双写：feedback → 内容寻址 artifact（不可变真源）+ journal 事件（payload-only，不改状态机）
    if sj is not None and getattr(sj, "enabled", False) and feedback_section and artifact_root is not None:
        try:
            ref = artifact_store.store(artifact_root, feedback_section,
                                       kind="verifier_feedback", sensitivity="sanitized")
            sj.emit("verifier_feedback", iter_id, prd_id,
                    payload={"round": round_n, "digest": ref.digest, "path": ref.path, "size": ref.size})
            digest = ref.digest
        except Exception:
            pass   # shadow 契约：观测层自身故障不得拖垮 verify 闭环（与 loop_runtime 契约#3 同源）
    if not driven:   # task 3.2：driven（journal_driven_dispatch）模式摘除 PRD 追加（spec「Immutable PRD source」
                     #   byte-for-byte unchanged）；baseline/shadow（driven 关）照旧追加——保 verify 闭环读 PRD 决策不变
        with open(prd_abs, "a", encoding="utf-8") as f:
            f.write(section)
    return digest


def _pa_verify_round(rec: dict, prof: dict, prd_abs: str, cur_base: str,
                     diff_path: Path, round_n: int, slug: str) -> dict:
    """单轮 pa-verify 裁判：喂 PRD+diff+测试输出+round，吐一行 JSON payload（verdict=pass|revise）。"""
    prompt = verify_prompt(prd_abs, rec.get("branch"), cur_base, diff_path, rec.get("verify"), round_n, prof)
    payload, meta = run_persona("pa-verify", prompt, "verify", f"verify:{slug}:r{round_n}")
    log(f"[verify] r{round_n} {str(payload.get('verdict', '?')).upper():6} {slug}｜"
        f"cost=${meta['cost']:.4f} turns={meta['turns']}")
    payload.setdefault("round", round_n)
    return payload


def _now_iso() -> str:
    """当前 UTC ISO8601 时间戳（喂 ShadowJournal.stamp）。

    独立成函数（非 inline ``datetime.now``）——便于测试 monkeypatch 固定时间，且 ShadowJournal 契约
    要求 stamp 由调用方注入（loop_runtime 不触系统时间）。
    """
    return datetime.now(timezone.utc).isoformat()


# ════════════════════════════════════════════════════════════════════════
# add-cross-prd-learning-memory Section 7 接线（控制面侧）
# ════════════════════════════════════════════════════════════════════════
# 三个接线点（design 决策#7 fail-open by construction；spec tasks 7.1 shadow parity + 7.7 两级 rollback）：
#   ① terminal reflection hook（dispatch 出口 → envelope + read-only SDK → rec["learning_memory"]）；
#   ② injection（dispatch-entry → load catalog → render lesson block → --lessons-artifact 透传 dev-agent）；
#   ③ memory_mode report（per-record 合并 ``rec["memory_mode"]``，不改 status/success/failure 语义）。
#
# **V1 project-only scope**：``prof["learning_memory"]["enabled"] is True`` 是项目级 canary 标记。缺字段 /
# 非 True → 整个 learning memory 子系统零副作用（不读 catalog、不调 SDK、不改 prompt）。default 状态下
# 即使用户误开环境变量 flag，非 allowlist 项目仍跳过（fail-safe 防误启用）。
_LEARNING_MEMORY_PROFILE_KEY = "learning_memory"


def _resolve_learning_enabled(prof: dict) -> tuple[bool, bool, str | None]:
    """解析 effective ``(shadow_on, injection_on, degraded_class)`` 用于本次 dispatch。

    批次 2 升级 A（spec task 1.3b）：injection 走 ``cutover.resolve_learning_injections_source`` 四重 gate
    （shadow flag + parity + quality + allowlist），不再用批次 1 的简化 AND。design 决策#8：injection
    cutover 必须先满足 shadow parity + quality evidence，防止跳过 shadow 直接注入未经验证的 prompt 内容。

    优先级：
        * **V1 allowlist**（``prof["learning_memory"]["enabled"] is True``）未启用 → 整个 learning 子系统
          零副作用（``shadow_on=False, injection_on=False``）；``degraded_class=None``——非 allowlist 项目
          静默跳过（运维只在 canary 项目上关心降级）。
        * ``cross_prd_learning_shadow`` flag 关 → ``shadow_on=False``；injection 无前置 → ``injection_on=False``。
        * ``cross_prd_learning_injection`` 经四重 gate（cutover.resolve_learning_injections_source）：
            * shadow off + injection on → fallback ``injection_not_gated``（invalid 组合，design 决策#8）
            * parity 未过 → fallback ``injection_parity_failed``
            * quality 未过 → fallback ``injection_quality_failed``
            * project 不在 allowlist → fallback ``injection_not_allowlisted``
            * injection flag 本身关 → 非 degraded（normal "off"）
            * 全过 → ``injection_on=True``。

    **parity_passed / quality_passed V1 取值**（fail-safe）：从 ``prof["learning_memory"]`` 显式标记读，
        缺省 ``False``——**绝不假阳 injection=on 当 evidence 缺失**。canary profile 须显式标
        ``parity_passed: True`` + ``quality_passed: True`` 才开 injection（design「evidence 流尚未自动化前
        人工签发」）。批次 3+ 接 ``quality_evidence.readiness`` 自动化。

    Args:
        prof: 项目 profile dict（coordinator 已 resolve flag；本函数读 ``prof["learning_memory"]`` 项目标记）。

    Returns:
        ``(shadow_on, injection_on, degraded_class)``。``degraded_class`` 非 None 表示本次 dispatch 走
        shadow-only 或 both-off（injection 因 gate 未过降级）。
    """
    lm_cfg = prof.get(_LEARNING_MEMORY_PROFILE_KEY) if isinstance(prof, dict) else None
    v1_allowed = isinstance(lm_cfg, dict) and lm_cfg.get("enabled") is True
    if not v1_allowed:
        return (False, False, None)
    flags = resolve_flags(env=os.environ, profile=prof)
    shadow_on = bool(flags.cross_prd_learning_shadow)
    injection_flag_on = bool(flags.cross_prd_learning_injection)
    # injection flag 本身关 → 不调 gate resolver（gate 是判断「injection 想开但前置不够」的降级路径；
    # flag 关是 normal "off" 状态，非 degraded；shadow 仍可独立开 candidate generation）。
    if not injection_flag_on:
        return (shadow_on, False, None)
    # V1 fail-safe：parity/quality 缺省 False（无 evidence 流时绝不假阳 injection=on）
    parity_passed = bool(lm_cfg.get("parity_passed", False)) if isinstance(lm_cfg, dict) else False
    quality_passed = bool(lm_cfg.get("quality_passed", False)) if isinstance(lm_cfg, dict) else False
    # 四重 gate（cutover.resolve_learning_injections_source）：driven_by='learning_injection' = 全过开仓
    gate = cutover.resolve_learning_injections_source(
        injection_flag=injection_flag_on, shadow_flag=shadow_on,
        project_id=prof.get("name", "?"), allowlist={prof.get("name", "?")},
        parity_passed=parity_passed, quality_passed=quality_passed)
    if gate.driven_by == "learning_injection":
        return (shadow_on, True, None)
    # injection flag 本身关不会走到这里（上方短路）；其他 fallback → injection 降级到 off
    degraded_class = _INJECTION_GATE_DEGRADED_CLASS.get(gate.driven_by, gate.driven_by)
    return (shadow_on, False, degraded_class)


# cutover resolver ``driven_by`` fallback 值 → memory_mode.degraded_status 受控词表映射。
# batch 1 既定 ``injection_not_gated``（invalid 组合）保留；新加 parity/quality/allowlist 维度。
_INJECTION_GATE_DEGRADED_CLASS: dict[str, str] = {
    "learning_injection_shadow_off": "injection_not_gated",
    "learning_injection_parity_failed": "injection_parity_failed",
    "learning_injection_quality_failed": "injection_quality_failed",
    "learning_injection_not_allowlisted": "injection_not_allowlisted",
}


def _load_run_events(state_dir: Path, proj: str, stamp: str, slug: str) -> list:
    """读本次 run 的 journal events（dispatch_one 已 emit 终态事件；envelope 据 verifier_history 选 evidence）。

    无 journal 文件 / 读失败 → 空列表（fail-open：reflection 在 envelope 构造时若 evidence_refs 缺失会
    走 evidence_class=PRE_VERIFIER_SHORT_CIRCUIT 路径或后续 schema reject，仍 fail-open）。
    """
    jpath = state_dir / "runs" / proj / f"{stamp}_{slug}.journal.jsonl"
    try:
        return J.read_events(jpath)
    except Exception:
        return []


def _make_artifact_loader(run_id: str):
    """构造 envelope artifact_loader callable（ArtifactRef dict → bytes 内容）。

    生产用 ``artifact_store.load``；找不到则返回空 bytes（envelope 兜底为 missing_content）。
    """
    root = STATE_DIR / "artifacts" / run_id

    def _loader(ref: dict) -> bytes:
        try:
            from artifact_store import ArtifactRef
            return artifact_store.load(str(root), ArtifactRef(**ref))
        except Exception:
            return b""
    return _loader


def _build_lessons_pkg(prof: dict, prd_abs: str, *, project_id: str, run_id: str,
                       timestamp: str, injection_on: bool) -> dict:
    """add-cross-prd-learning-memory Section 5 接线：dispatch-entry 检索 + lesson block artifact 写盘。

    **fail-open by construction**（design 决策#7）：任一步骤失败 → ``artifact_path=None``，dev-agent 不收
    ``--lessons-artifact``（build_prompt baseline，identity no-op）+ ``degraded_class`` 非 None 供 memory_mode
    记录。selected_lesson_ids 永远是 tuple（[] 表示无注入或 retrieval 空）。

    Pipeline（spec design 决策#5）：
        1. ``injection_on=False`` → 直接返 ``{artifact_path:None, selected_lesson_ids:(), ...}``（零 catalog 读）。
        2. ``load_catalog_for_retrieval`` fail-open：catalog 缺/损坏 → 空 entries + degraded_class。
        3. ``derive_task_metadata`` 从 profile + PRD 确定性派生（零 LLM）。
        4. ``retrieve_from_source`` filter+rank+cap=5。
        5. ``render_lesson_block`` 渲染 ≤5 lessons（严格排除 evidence/叙事）。
        6. 非空 lesson_block → ``artifact_store.store`` 写 content-addressed artifact（kind=lessons_block；
           sensitivity=sanitized），返回 path 透传 dev-agent。
        7. ``candidate_count`` / ``promotion_count`` 从 catalog 投影读（监控用；retrieval 失败时省略）。

    Returns:
        ``{"artifact_path": str|None, "selected_lesson_ids": tuple[str, ...],
        "degraded_class": str|None, "candidate_count": int, "promotion_count": int}``。
    """
    pkg: dict = {"artifact_path": None, "selected_lesson_ids": (),
                 "degraded_class": None, "candidate_count": 0, "promotion_count": 0}
    if not injection_on:
        return pkg
    try:
        source = LMRet.load_catalog_for_retrieval(str(STATE_DIR), project_id)
        if source.degraded_class is not None:
            pkg["degraded_class"] = source.degraded_class
            return pkg
        # 投影统计（监控用，失败容忍）
        try:
            import learning_memory_catalog as LMCat
            catalog = LMCat.load_catalog_file(str(STATE_DIR), project_id)
            if isinstance(catalog, dict):
                entries = catalog.get("entries") or []
                pkg["candidate_count"] = len(entries)
                pkg["promotion_count"] = sum(1 for e in entries
                                             if isinstance(e, dict) and e.get("state") == "active")
        except Exception:
            pass
        # task_metadata：从 profile + PRD 确定性派生（保守：缺字段不加 tag，防假阳性）
        prof_dict = prof if isinstance(prof, dict) else {}
        try:
            prd_text = Path(prd_abs).read_text(encoding="utf-8")
        except Exception:
            prd_text = ""
        prd_dict = _split_frontmatter(prd_text)[0] if prd_text else {}
        task_metadata = LMRet.derive_task_metadata(
            project_profile=prof_dict, prd=prd_dict, project_id=project_id)
        result = LMRet.retrieve_from_source(source, task_metadata)
        if result.degraded_class is not None:
            pkg["degraded_class"] = result.degraded_class
        pkg["selected_lesson_ids"] = tuple(result.selected_lesson_ids)
        if not result.selected:
            return pkg   # 无 applicable lessons → 无注入（baseline prompt）；非 degraded
        lesson_block = LMRet.render_lesson_block(result.selected)
        if not lesson_block.strip():
            return pkg
        # 写 content-addressed artifact（kind=lessons_block；与 feedback_artifact 对称的模式）。
        # **绝对路径**：dev-agent 的 cwd 是目标仓 worktree，相对路径找不到——必须传 absolute path
        # （与现有 --feedback-artifact 的相对路径模式不同；后者 driven 模式尚未 cutover，路径解析问题待
        # 接线时一并修；lessons_block 新接线一开始就走绝对路径，避免同类问题）。
        root = STATE_DIR / "artifacts" / run_id
        ref = artifact_store.store(str(root), lesson_block,
                                   kind="lessons_block", sensitivity="sanitized")
        pkg["artifact_path"] = str(root / ref.path)
        return pkg
    except Exception:
        # 任一意外失败 → 不注入（dev-agent baseline prompt）；记 degraded_class 供 memory_mode。
        pkg["degraded_class"] = "injection_internal_error"
        pkg["selected_lesson_ids"] = ()
        pkg["artifact_path"] = None
        return pkg


def _attach_learning_memory(rec: dict, prof: dict, entry: dict, stamp: str, *,
                            sdk_query_fn=None) -> None:
    """add-cross-prd-learning-memory Section 7 terminal hook：reflection + usage outcomes + memory_mode。

    批次 2 升级 B（spec task 6.1/6.2 接线）：reflection 之后对每个 selected_lesson_id 做 detect→classify→
    build→append usage outcome（Section 6 闭环）。**红线（Section 6 偏差 #3）**：``classify_outcome`` 可返
    ``"unknown"``，但 ``UsageOutcome.__post_init__`` 拒绝 unknown outcome —— **必须先判 ``outcome != "unknown"``
    再 build**（unknown 跳过持久化；catalog ``_apply_usage_outcomes`` 也跳过 unknown，design 决策#6「absent
    evidence ≠ disobedience」）。

    **接线点 1 + 3 + 升级 B**（接线点 2 在 dispatch_one 内 ``_dev_cmd`` 前）。在 ``_run_one`` 调用
    ``dispatch_one`` 返回后调用——所有 terminal 出口统一收尾。

    **fail-open 硬约束**（design 决策#7）：reflection / usage outcome / catalog 任何故障 → side-channel
    记录，**绝不改 record.status / verify verdict / publish outcome / retry 计数**。

    **shadow=off → 零 reflection + 零 usage outcome 副作用**（design 决策#8）。
    **injection=off / selected_ids 空 → 零 usage outcome 写入**（无注入无从评估 effectiveness）。

    Args:
        rec: dispatch_one 返回的 record dict（in-place 合并 ``learning_memory`` + ``memory_mode``）。
        prof: 项目 profile（V1 allowlist 检查）。
        entry: candidate entry（取 prd_path 推 slug）。
        stamp: run 时间戳。
        sdk_query_fn: 测试注入 mock-SDK（生产 None → reflection 走真 SDK + asyncio.wait_for 硬超时）。
    """
    # fail-open 兜底：本函数任何意外异常都不能改 terminal outcome（design 决策#7）。
    try:
        shadow_on, injection_on, degraded_class = _resolve_learning_enabled(prof)
        proj = prof.get("name", "?") if isinstance(prof, dict) else "?"
        slug = Path(entry.get("prd_path", "") or "").stem or "unknown"
        # 接线点 2 桥接：dispatch_one 在 injection 时记入的 selected IDs（injection pkg 已写盘）
        sel_ids = tuple(rec.pop("_learning_selected_ids", ()) or ())
        inj_degraded = rec.pop("_learning_injection_degraded", None)
        cand_count = int(rec.pop("_learning_candidate_count", 0) or 0)
        prom_count = int(rec.pop("_learning_promotion_count", 0) or 0)

        # 接线点 1：terminal reflection（shadow=on 才跑）
        learning_rec: dict | None = None
        envelope = None
        run_id_for_usage = stamp
        prd_id_for_usage = entry.get("prd_path") or ""
        if shadow_on:
            try:
                events = _load_run_events(STATE_DIR, proj, stamp, slug)
                # run_id / prd_id 复用 coordinator 公式（同 dispatch_one 内 build_coordinator 输入 → 同 IDs）
                _coord2 = build_coordinator(stamp=stamp, prd_path=entry.get("prd_path") or "",
                                            proj=proj, slug=slug, state_dir=STATE_DIR,
                                            profile=prof, stamp_fn=_now_iso)
                run_id_for_usage = _coord2.run_id
                prd_id_for_usage = _coord2.prd_id
                loader = _make_artifact_loader(_coord2.run_id)
                # 外部评审 P1 #1 修复：dispatch rec.status（pr_open/fail/skip/...）不在 IterationStatus
                # 合法 value 集合 → 直传会让 reflection.IterationStatus() 抛 ValueError → degraded{not_terminal}。
                # 经 _dispatch_status_to_envelope_terminal 映射到受控词表（fail-open：未知→""→degraded，不改 terminal）。
                envelope = LME.build_terminal_envelope(
                    terminal_status=_dispatch_status_to_envelope_terminal(rec),
                    events=events, artifact_loader=loader,
                    run_id=_coord2.run_id, prd_id=_coord2.prd_id,
                    iteration_id=_coord2.iteration_id, project_id=proj)
                refl_kwargs = {}
                if sdk_query_fn is not None:
                    refl_kwargs["sdk_query_fn"] = sdk_query_fn
                result = LMRefl.run_terminal_reflection(
                    envelope=envelope, state_dir=str(STATE_DIR),
                    project_id=proj, run_id=_coord2.run_id, prd_id=_coord2.prd_id,
                    iteration_id=_coord2.iteration_id, timestamp=_now_iso(),
                    is_terminal_outcome=_dispatch_is_terminal_outcome(rec),
                    **refl_kwargs)
                learning_rec = {
                    "reflection": result.outcome,   # "ok" / "degraded"
                    "class": result.degraded_class,
                    "run_id": _coord2.run_id, "prd_id": _coord2.prd_id,
                    "evidence_class": result.evidence_class,
                    "candidate_count": len(result.candidates),
                }
            except Exception as e:
                # reflection 全链路意外故障（envelope 构造崩等）→ fail-open side-channel，不改 terminal outcome
                try:
                    LMRefl._append_degraded_record(
                        str(STATE_DIR), proj,
                        {"schema_version": LMRefl.DEGRADED_SCHEMA_VERSION,
                         "timestamp": _now_iso(), "project_id": proj,
                         "run_id": stamp, "prd_id": entry.get("prd_path") or "",
                         "degraded_class": "reflection_attach_error",
                         "reason": f"attach_learning_memory: {str(e)[:180]}"})
                except Exception:
                    pass
                learning_rec = {"reflection": "degraded",
                                "class": "reflection_attach_error",
                                "run_id": stamp, "prd_id": entry.get("prd_path") or ""}

            # 升级 B（Section 6 闭环）：terminal effectiveness — 仅 injection_on + selected IDs + envelope 可用
            # 时评估。fail-open：catalog 不可达 / lesson 缺 / detect 异常 → 跳过该 lesson，不改 terminal outcome。
            if injection_on and sel_ids and envelope is not None:
                usage_recorded = _record_usage_outcomes(
                    state_dir=str(STATE_DIR), project_id=proj,
                    run_id=run_id_for_usage, prd_id=prd_id_for_usage,
                    selected_ids=sel_ids, envelope=envelope, timestamp=_now_iso())
                if learning_rec is not None and usage_recorded:
                    learning_rec["usage_outcomes"] = usage_recorded

        # invalid 组合 injection=on, shadow=off → emit injection_not_gated 降级（design 决策#8）
        eff_degraded = degraded_class or inj_degraded
        if learning_rec is not None:
            rec["learning_memory"] = learning_rec
        # 接线点 3：memory_mode（附加字段，不改现有 status/success/failure 语义）
        rec["memory_mode"] = LMEff.build_memory_mode_record(
            shadow_on, injection_on,
            selected_lesson_ids=sel_ids,
            candidate_count=cand_count, promotion_count=prom_count,
            degraded_status=eff_degraded)
    except Exception as e:
        # 终极兜底：本函数自身意外故障也不能拖垮 dispatch（fail-open by construction）。
        # 不写 memory_mode / learning_memory 字段（rec 仍保留 dispatch_one 设的全部 terminal 字段）。
        log(f"  ⚠ learning_memory attach 异常（fail-open 兜底）: {e}")


def _build_terminal_evidence_from_envelope(envelope) -> dict:
    """从 envelope 抽 ``detect_action_observed`` 要的 ``terminal_evidence`` dict（零 LLM，纯结构字段）。

    ``detect_action_observed`` 读这些字段（learning_memory_effectiveness.V1 启发式）：
        * ``verifier_verdict``：verifier pass/revise（决定 failure_recurred 推断）
        * ``test_log``：test_output artifact 内容（action_observed token 匹配 corpus）
        * ``skip_reason``：terminal reason（failure_class token 匹配 corpus）
        * ``diff``：可选（envelope 不直接含 diff；留空——多数 case test_log 已够 token 匹配）
    """
    meta = envelope.sanitized_metadata if isinstance(envelope.sanitized_metadata, dict) else {}
    # verifier_verdict：取最后一条 verifier_feedback 的 verdict（pass / revise / none）
    verdict = ""
    for ev in envelope.verifier_events or ():
        if isinstance(ev, dict) and ev.get("event_type") == "verifier_feedback":
            v = ev.get("verdict")
            if isinstance(v, str):
                verdict = v
    # test_log：拼 evidence_excerpts 的 content（test_output kind；bytes → str）
    test_log_parts: list[str] = []
    for ex in envelope.evidence_excerpts or ():
        if isinstance(ex, dict) and ex.get("kind") == "test_output":
            c = ex.get("content")
            if isinstance(c, (bytes, bytearray)):
                c = c.decode("utf-8", errors="replace")
            if isinstance(c, str) and c:
                test_log_parts.append(c)
    return {
        "verifier_verdict": verdict,
        "test_log": "\n".join(test_log_parts),
        "skip_reason": str(meta.get("terminal_reason", "")),
    }


def _record_usage_outcomes(*, state_dir: str, project_id: str, run_id: str, prd_id: str,
                           selected_ids: tuple[str, ...], envelope, timestamp: str) -> list[tuple[str, str]]:
    """Section 6 闭环：per-lesson detect→classify→build→append usage outcome。

    **红线（Section 6 偏差 #3）**：``classify_outcome`` 可返 ``"unknown"``，但 ``UsageOutcome.__post_init__``
    拒绝 unknown outcome——本函数**显式跳过 unknown**（不 build、不 append）。design 决策#6「absent evidence
    ≠ disobedience」：unknown 是合法的「无法判定」结果，记入 catalog 只增加噪声。

    **fail-open**：catalog 不可达 / lesson 缺 / detect 异常 → 跳过该 lesson（不崩，不改 terminal outcome，
    不影响其他 lesson 的评估）。

    Args:
        selected_ids: 本次 dispatch 注入的 lesson IDs（来自 ``_build_lessons_pkg`` 的 retrieval 结果）。
        envelope: terminal envelope（喂 ``detect_action_observed`` 的 terminal_evidence 来源）。

    Returns:
        ``[(lesson_id, outcome), ...]`` 实际持久化的 usage outcomes（已滤 unknown）。空 = 无注入 / 全 unknown /
        catalog 不可达。
    """
    if not selected_ids:
        return []
    # 读 catalog 拿 lesson entries（按 selected_lesson_id 查 corrective_action/failure_class 喂 detect）
    try:
        import learning_memory_catalog as LMCat
        catalog = LMCat.load_catalog_file(state_dir, project_id)
    except Exception:
        return []
    if not isinstance(catalog, dict):
        return []
    entries = catalog.get("entries") if isinstance(catalog.get("entries"), list) else []
    entries_by_id = {e.get("lesson_id"): e for e in entries
                     if isinstance(e, dict) and isinstance(e.get("lesson_id"), str)}
    terminal_ev = _build_terminal_evidence_from_envelope(envelope)
    recorded: list[tuple[str, str]] = []
    for lid in selected_ids:
        entry = entries_by_id.get(lid)
        if not entry:
            continue   # lesson 不在 catalog（可能已 retire / 跨 project / catalog stale）→ 跳过
        try:
            action, failure, evidence_avail = LMEff.detect_action_observed(entry, terminal_ev)
            outcome = LMEff.classify_outcome(
                action_observed=action, failure_recurred=failure,
                evidence_available=evidence_avail)
            # 红线：unknown 不持久化（UsageOutcome.__post_init__ 拒绝；catalog 也跳过）
            if outcome == LM.UsageOutcomeKind.UNKNOWN.value:
                continue
            usage = LMEff.build_usage_outcome(
                event_id=f"usage_{run_id}_{lid}", timestamp=timestamp,
                project_id=project_id, lesson_id=lid, prd_id=prd_id,
                action_observed=action, failure_recurred=failure,
                outcome=outcome, evidence_refs=())
            LMS.append_usage_outcome(state_dir, project_id, usage, run_id=run_id)
            recorded.append((lid, outcome))
        except Exception:
            continue   # 单 lesson 故障不拖垮其他 lesson 的评估（fail-open）
    return recorded


# ════════════════════════════════════════════════════════════════════════
# 外部评审 P1 #1 修复：dispatch rec.status → envelope.terminal_status 映射
# ════════════════════════════════════════════════════════════════════════
# dispatch_one 出口 ``rec["status"]`` 用 dispatch 词汇（``pr_open`` / ``fail`` / ``skip`` /
# ``blocked_external_state`` / ``pr_merged`` / ...），与 ``loop_state.IterationStatus`` 受控 value 集合
# （``published`` / ``failed`` / ``aborted`` / ``external_blocked`` / ...）不完全一致。修复前 envelope 接线点
# L1481 直传 ``rec.get("status") or ""`` → ``learning_memory_reflection.py`` L362
# ``IterationStatus(envelope.terminal_status)`` 抛 ValueError → degraded{class:not_terminal}
# → **不生成候选**（learning memory 失效，但不改 terminal outcome——fail-open by construction）。
#
# 修复：在 run_daily 做映射，**不改** reflection.py 的 ``IterationStatus()`` 守护契约（那是正确的边界
# ——loop_state 的合法 value 集合是 state machine 真源）。映射表对齐 ``_SJ_TERMINAL_MAP``（dispatch
# status → terminal event）+ ``_sj_terminal`` 的 pr_open/interrupted_pr 双门分流逻辑（机械绿 + 语义
# pass → published，否则 revise）。
#
# **fail-open**：映射不到的 status → 返回 ``""``（让 reflection 走 not_terminal degraded，**不崩**
# envelope 构造，**不改** terminal outcome）。
#
# ``_ITERATION_STATUS_VALUES`` 是 ``loop_state.IterationStatus.value`` 合法集合的镜像（state machine
# 真源——硬编码 enum 的同款硬编码 frozenset；任何 state machine 演化需同步二者）。用于 identity
# pass-through：若 dispatch status 本身已是合法 IterationStatus.value（如 loop_state 终态名直传场景），
# 原样返回，无需映射。
_ITERATION_STATUS_VALUES: frozenset[str] = frozenset({
    "planned", "running", "agent_finished", "test_blocked", "verifying", "revise",
    "external_blocked", "publish_ready", "published", "aborted", "failed", "stalled",
    "orphan_deleted", "blocked_evidence", "sandbox_blocked", "state_corrupt",
})

_DISPATCH_STATUS_TO_ENVELOPE_TERMINAL: dict[str, str] = {
    # 直接对齐 _SJ_TERMINAL_MAP（同源 status 词汇）
    "skip": "aborted",
    "blocked_external_state": "external_blocked",   # loop_state 视角非 terminal；envelope 保守传，reflection 诚实 not_terminal degraded
    "blocked_test_gate": "test_blocked",
    "blocked_evidence": "blocked_evidence",
    "fail": "failed",
    "stalled": "stalled",
    "orphan_deleted": "orphan_deleted",
    # single-flight-auto-merge task #5：merge phase 出口 → envelope terminal label（保 reflection 不 degraded）。
    #   merged=已合 main（≈ published，已交付；task 6.1a 引 MERGED 非终态允许 revert 时再调）；triaged=待人工（≈ external_blocked）。
    "merged": "published",
    "triaged": "external_blocked",
    # pr_* 系列需配合 verify 双门决定 published/revise（见 _dispatch_status_to_envelope_terminal 函数体）
}


def _dispatch_status_to_envelope_terminal(rec: dict) -> str:
    """dispatch rec.status → ``loop_state.IterationStatus.value`` 映射（envelope.terminal_status 受控词表）。

    Why（外部评审 P1 #1）：dispatch status 词汇（pr_open/fail/skip/...）不在 ``IterationStatus`` 合法
    value 集合，直传会让 ``learning_memory_reflection.IterationStatus()`` 抛 ValueError → degraded
    {not_terminal}（不生成候选）。本函数做受控映射，让 envelope.terminal_status 落在合法 value 集合。

    Map rules（对齐 ``_SJ_TERMINAL_MAP`` + ``_sj_terminal`` 双门分流）：
        * **identity pass-through**：dispatch status 本身已是合法 ``IterationStatus.value``（如
          ``published`` / ``failed`` / ``aborted`` 直传场景）→ 原样返回（无需映射）；
        * 直接映射表覆盖的 dispatch status → 对应 IterationStatus.value（与 _sj_terminal emit 的
          终态事件一致：skip→aborted、fail→failed、blocked_evidence→blocked_evidence 等）；
        * ``pr_open`` / ``interrupted_pr`` → 看 verify 双门（``mechanical_green = verify.pass`` 且
          ``semantic_pass = verify_verdict == "pass"``）：双绿 → ``published``（_sj_terminal 已 emit
          published）；否则 → ``revise``（中间态，reflection 会 not_terminal degraded，但这是诚实记录
          「dispatch 已 return 但 loop_state 视角非终态」——绝不在 envelope 层假 terminal）；
        * 其他 ``pr_*`` 前缀（``pr_merged`` / ``pr_closed``）：``pr_merged`` → ``published``（reconcile
          看到 merged 即等价成功交付）；其余保守 → ``revise``；
        * 未映射/空 → ``""``（fail-open：让 reflection 走 not_terminal degraded，不改 terminal outcome）。

    Fail-open by construction：未知 status 绝不让 envelope 构造崩；rec 仍保留 dispatch 设置的全部字段。
    """
    status = rec.get("status") or ""
    # 0. identity pass-through：已是合法 IterationStatus.value（loop_state 终态名/中间态名直传场景）
    if status in _ITERATION_STATUS_VALUES:
        return status
    # 1. 直接映射表
    direct = _DISPATCH_STATUS_TO_ENVELOPE_TERMINAL.get(status)
    if direct is not None:
        return direct
    # 2. pr_open / interrupted_pr：看 verify 双门（与 _sj_terminal L1665-1685 同款分流）
    if status in ("pr_open", "interrupted_pr"):
        verify = rec.get("verify") or {}
        mechanical_green = bool(verify.get("pass"))
        semantic_pass = rec.get("verify_verdict") == "pass"
        if mechanical_green and semantic_pass:
            return "published"
        return "revise"
    # 3. 其他 pr_* 前缀（pr_merged/pr_closed/...）：merged → published；其余保守 revise
    if status.startswith("pr_"):
        return "published" if "merged" in status else "revise"
    # 4. 兜底：未知/空 status → ""（fail-open：reflection 走 not_terminal degraded，不改 terminal outcome）
    return ""


# dispatch-terminal 出口集合（评审 #1 残留修复）：dispatch_one 已 return 的所有出口 status。
# 这些 status 在 dispatch 视角是 terminal outcome（dispatch 已结束），即便 loop_state 视角对应中间态
# （blocked_test_gate/blocked_external_state/interrupted_pr/retry_* → loop_state test_blocked/
# external_blocked/revise）。冻结契约（spec L97 stalled/gate-blocked/verifier-revise-exhausted/failed
# + L101-105 post-verifier blocked_external_state + design L43）：这些终态必须能贡献候选。
# 与 _DISPATCH_STATUS_TO_ENVELOPE_TERMINAL 区别：那个表是 status→envelope label 映射（label 可能中间态）；
# 本集合是 status→dispatch-terminal? 判定（解耦 reflection 守护，不依赖 label 是否 loop_state 真终态）。
# 同步要求：dispatch_one 任何新 return 出口（rec.update(status=...) / rec["status"]=...）须同步加入本集合，
# 否则该出口会被 _dispatch_is_terminal_outcome 判 False → reflection degrade{not_terminal}（漏候选）。
_DISPATCH_TERMINAL_OUTCOMES: frozenset[str] = frozenset({
    # 直接映射表覆盖的 dispatch status（对齐 _DISPATCH_STATUS_TO_ENVELOPE_TERMINAL）
    "skip", "fail", "blocked_test_gate", "blocked_evidence", "blocked_external_state", "stalled",
    "orphan_deleted",
    # pr_* 系列（dispatch 已开/合并/关闭/中断 PR = terminal outcome）
    "pr_open", "pr_merged", "pr_closed", "interrupted_pr",
    # retry 阻断/耗尽（dispatch 已放弃 = terminal outcome；未映射 envelope label 但 is_terminal_outcome 解锁）
    "retry_blocked", "retry_budget_exhausted",
    # loop_state 真终态名直传场景（identity pass-through；这些 label 也在 loop_state.TERMINAL_STATUSES）
    "sandbox_blocked", "state_corrupt", "published", "aborted", "failed",
    # single-flight-auto-merge task #5：merge phase 出口（dispatch 已 return = terminal outcome；merged=已合 main，
    #   triaged=进 triage 池不强合）。缺之则 _dispatch_is_terminal_outcome 判 False → reflection degrade 漏候选。
    "merged", "triaged",
})


def _dispatch_is_terminal_outcome(rec: dict) -> bool:
    """dispatch rec.status 是否 terminal outcome（评审 #1 残留修复：解耦 reflection terminal 守护）。

    Why（design L43）：``loop_state.is_terminal`` 是 state-machine 内部视角，不覆盖 dispatch-terminal
    语义。dispatch_one 已 return 的出口（gate-blocked/revise-exhausted/blocked_external_state/pr_*/...）
    在 loop_state 视角可能对应中间态 label（test_blocked/external_blocked/revise），但 dispatch 视角
    已 terminal outcome（可贡献候选）。本函数让调用方（``_attach_learning_memory``）把「dispatch 已
    terminal」的判定显式传给 ``run_terminal_reflection(is_terminal_outcome=...)``，解耦 reflection 守护
    对 ``loop_state.is_terminal(envelope.terminal_status label)`` 的绑死。

    判定规则：``rec["status"] in _DISPATCH_TERMINAL_OUTCOMES`` → True；中间态/未知/空 → False（fail-safe）。
    绝不动 ``loop_state.TERMINAL_STATUSES`` / ``_TRANSITIONS``（spec L90 terminal predicates 不变量）。

    fail-safe：未知 status 默认 False（degrade，绝不假阳）；rec 无 status 键 / status=None → False。
    """
    status = rec.get("status") if isinstance(rec, dict) else None
    return isinstance(status, str) and status in _DISPATCH_TERMINAL_OUTCOMES


# dispatch record status → journal 终态 event（task 3.2 + 3.5）。
# 映射对齐 ``compat_readers.legacy_status`` 保 shadow parity（task 3.4 / spec scenario 19）：pr_open/interrupted_pr
# 叠 verify.pass（绿→published，红→revise）；blocked→external/test_blocked；fail→failed；skip→aborted；
# stalled/orphan_deleted→同名终态（task 3.5：dev loop 主动刹车 / 无 commit 孤儿清理，独立 terminal class）。
# 仍未映射：planned smoke（task 3.5 阶段 2 处理 running emit 位置）+ sandbox_blocked（task 5 引入）。
_SJ_TERMINAL_MAP: dict[str, str] = {
    "skip": "aborted",
    "blocked_external_state": "external_blocked",
    "blocked_test_gate": "test_blocked",
    "fail": "failed",
    "stalled": "stalled",
    "orphan_deleted": "orphan_deleted",
    "blocked_evidence": "blocked_evidence",   # task 4.2：green evidence artifact 持久化失败（不当 fresh green evidence）
    "merged": "published",            # single-flight-auto-merge task #5：已合 main → published（for 外 _sj_terminal 统一 emit）
    "triaged": "external_blocked",    # single-flight-auto-merge task #5：进 triage 池待人工 → external_blocked
}


def _sj_terminal(sj: ShadowJournal, rec: dict, iteration_id: str, prd_id: str,
                 *, artifact_root=None) -> None:
    """dispatch 出口旁路 emit 终态事件（task 3.2）。

    flag 关→``sj.emit`` 内部 no-op（ShadowJournal 契约）；映射对齐 compat 保 parity。**旁路**：不改 rec、
    不影响控制流（调用方在 ``return rec`` 前调，emit 返回值丢弃）。
    """
    status = rec.get("status")
    if status in ("pr_open", "interrupted_pr"):
        verify = rec.get("verify") or {}
        # task 4.1 dual publication gate（spec verified-publication-integrity）：published 当且仅当
        #   ① 机械绿（independent verify.pass）
        #   ② 语义 pass（verify_verdict=='pass'）
        #   ③ 对账 known（status ∈ {pr_open, interrupted_pr} 蕴含 reconcile 成功对账；external unknown
        #      已落 blocked_external_state 走下方 map，不进此分支）
        # 机械绿但语义红/异常/缺失 → revise，绝不假绿 published（design 决策#3「never a green substitute」）。
        mechanical_green = bool(verify.get("pass"))
        semantic_pass = rec.get("verify_verdict") == "pass"
        if mechanical_green and semantic_pass:
            # task 4.4：publication 前 reconcile test evidence artifact（exactly-once fresh green evidence）。
            # evidence_ref.digest 指向的 artifact 必须仍在 + digest 匹配；缺失/损坏 → 不当 published
            # （spec 4.4 test evidence idempotency keys before publication；与 4.2 verify 时持久化互补，
            #  防 verify→publication 窗口 crash/磁盘致 evidence 丢失）。
            _ev_ref = verify.get("evidence_ref") or {}
            _ev_digest = _ev_ref.get("digest") if isinstance(_ev_ref, dict) else None
            if artifact_root and _ev_digest and \
                    reconcile.ArtifactEvidenceResolver(artifact_root).check("test", _ev_digest) is not True:
                sj.emit("blocked_evidence", iteration_id, prd_id,
                        payload={"status": status, "pr_url": rec.get("pr_url"),
                                 "reason": "publication evidence artifact not confirmed"})
                return
            sj.emit("published", iteration_id, prd_id,
                    payload={"status": status, "pr_url": rec.get("pr_url")})
        else:
            sj.emit("revise", iteration_id, prd_id,
                    payload={"status": status, "pr_url": rec.get("pr_url"),
                             "verify_verdict": rec.get("verify_verdict")})
        return
    event = _SJ_TERMINAL_MAP.get(status)
    if event:
        sj.emit(event, iteration_id, prd_id,
                payload={"status": status, "skip_reason": rec.get("skip_reason")})


def dispatch_one(entry: dict, prof: dict, stamp: str, args, *, slot_handle=None) -> dict:
    """单 PRD 全流程：准入→建 worktree→触发 dev-agent→对账→独立验证。返回记录 dict。

    task 3.2：全程用 ``ShadowJournal`` 旁路写 journal 事件（``journal_shadow`` flag 关→全 no-op，dispatch
    决策零变化，design 决策#8）。中间事件 inline emit；终态由 ``_sj_terminal`` 在各出口统一 emit。
    """
    proj = prof.get("name", "?")
    repo = prof.get("repo", "")
    slug = Path(entry.get("prd_path", "")).stem or "unknown"
    devslug = dev_slugify(slug)   # dev 分支 slug（slug_utils 单一源头，ADR-0006 #5；幂等前置闸按 slug 匹配 auto/* 用）
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
                 "dev_test_cmd": None, "verify_verdict": None, "verify_round": None,   # verify 闭环字段
                 # single-flight-auto-merge task 1.2：merge 闭环 schema 扩展（向后兼容，默认 = baseline 语义）。
                 #   merge_commit：--no-ff merge 产出的单一 merge commit sha（merge 时记，revert/对账锚点）。
                 #   reverted：post-merge 测试红→auto-revert 是否已执行（revert commit sha 另由 reconcile 记录）。
                 #   triage_reason：PRD 出队进 triage 池的固定枚举原因（task 5.1）。
                 #   post_merge_verdict：merge 后 main 全量测试三态判决（PASS/FAIL/UNKNOWN，task 4.2）。
                 "merge_commit": None, "reverted": False, "triage_reason": None, "post_merge_verdict": None}

    # ── task 2.1：coordinator 集中 own 运行时设施（design 决策#1；替代散建 _run/_prd/_iter/_sj）。
    #    一次解析所有 loop flag + 建 IDs/journal/artifact_root；flag 全关→baseline no-op（dispatch 决策零变化）。
    #    _run/_prd/_iter/_sj 局部别名保留，后续主体零改动；adapter（hooks/sandbox/telemetry）从 _coord.flags 挂载。
    # task 3.1：dispatch entry 捕获 PRD 内容 digest（spec「Immutable new-run input」）——读 prd_abs 内容算
    #    sha256:<hex>，纳入 prd_id（content-addressed）+ planned event payload；读失败→None（baseline 容错）。
    try:
        prd_content = Path(prd_abs).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        prd_content = None
    # task 4.4 fix：stable_slug = frontmatter 语义 slug（跨 cron 稳定），喂 circuit_key（熔断跨 cron 键）。
    #   slug（本函数 1907）= Path(prd_path).stem = {stamp}_{slug}（含 cron stamp，跨 cron 变），不可直接做熔断键——
    #   旧 path-based prd_id 跨 cron 变致 is_in_cooldown 跨 cron 不命中（"branch 绿 main 红 PRD 夜夜复发"防不住）。
    #   frontmatter.slug（pa-prd/inject 都写）是语义 slug；缺则 fallback slug（仅异常 PRD 兜底）。
    stable_slug = slug
    if prd_content:
        _fm, _ = _split_frontmatter(prd_content)
        stable_slug = _fm.get("slug") or slug
    _coord = build_coordinator(stamp=stamp, prd_path=entry.get("prd_path") or "",
                               proj=proj, slug=slug, state_dir=STATE_DIR, profile=prof,
                               stamp_fn=_now_iso, prd_content=prd_content,
                               resolver=reconcile.default_resolver(repo, owner_repo),
                               stable_slug=stable_slug)   # task 2.1：coord own reconcile resolver（4.4 publication 前对账消费）
    _run, _prd, _iter, _sj = (_coord.run_id, _coord.prd_id, _coord.iteration_id, _coord.journal)
    # task 2.5：preflight 校验 loop flag 组合一致性（design 决策#1 防 impossible partial 组合）。
    #   违规→阻断不投递（status=skip + 结构化 reason），在 admission profile 门**之前**拦截，不起 dev loop。
    _pf = preflight(_coord.flags)
    if not _pf.is_ok:
        rec.update(status="skip",
                   skip_reason="阻断-loop flag 组合非法: " + "; ".join(_pf.blocked.violations))
        _sj_terminal(_sj, rec, _iter, _prd); log(f"  ⛔ {slug}: {rec['skip_reason']}"); return rec
    _planned_payload = {"base": base, "prd_path": entry.get("prd_path"), "project": proj}
    if _coord.prd_digest is not None:    # task 3.1：PRD 可读→initial event 锚定内容版本（不可读省略）
        _planned_payload["prd_digest"] = _coord.prd_digest
    _sj.emit("planned", _iter, _prd, payload=_planned_payload)
    # add-cross-prd-learning-memory Section 7：learning flag resolve 一次（接线点 2 injection + 接线点 1 reflection 消费）。
    #   V1 allowlist（prof["learning_memory"]["enabled"]）未启用 → 整个 learning 子系统零副作用（design 决策#8）。
    _learning_shadow_on, _learning_injection_on, _learning_degraded_class = _resolve_learning_enabled(prof)
    # task 3.5：running emit 推迟到「确认投递 dev loop」后（见下方 skip-dev 检查之后）——admission 阶段终态
    #   与 skip-dev smoke 均未投递，不应 RUNNING；否则 planned smoke reduce RUNNING ≠ legacy PLANNED
    #   （spec scenario 19 terminal-class parity 断裂）。admission 终态从 PLANNED 迁移（含 EXTERNAL_BLOCKED）。

    # ── 准入 1：profile 门
    if not (prof.get("admission") and prof.get("dev_agent_ready") and prof.get("type") == "code"):
        rec.update(status="skip", skip_reason="profile 不满足（admission/dev_agent_ready/type≠code）")
        _sj_terminal(_sj, rec, _iter, _prd); log(f"  ⏭ {slug}: {rec['skip_reason']}"); return rec
    # ── 准入 2：branch protection 运行时实查（三态）
    if not owner_repo:
        rec.update(status="skip", skip_reason="跳过-无 remote（取不到 owner/repo）")
        _sj_terminal(_sj, rec, _iter, _prd); log(f"  ⏭ {slug}: {rec['skip_reason']}"); return rec
    prot = check_branch_protection(owner_repo, base)
    if prot.is_unknown:   # fail-safe：保护态不明 → 阻断，不起 dev loop（OpenSpec fail-safe-dispatch / tasks 4.3）
        rec.update(status="blocked_external_state", blocked_check="branch_protection",
                   skip_reason=f"阻断-分支保护态不明: {prot.reason}")
        _sj_terminal(_sj, rec, _iter, _prd); log(f"  ⛔ {slug}: {rec['skip_reason']}"); return rec
    if prot.state is not ExtState.FOUND or not prot.value:   # NOT_FOUND（明确未保护）/ 兜底 → 拒投
        rec.update(status="skip", skip_reason=f"跳过-{prot.reason}")
        _sj_terminal(_sj, rec, _iter, _prd); log(f"  ⏭ {slug}: {rec['skip_reason']}"); return rec
    # ── 准入 3：幂等前置闸（SPEC #30 ④ / ADR-0004 §4：投递前去重，已投递→skip 不起 dev loop，省 SDK 启动+$）
    idem = already_dispatched(owner_repo, repo, devslug)
    if idem.is_unknown:   # fail-safe：幂等态不明 → 阻断（旧版容忍可能重复投递）
        rec.update(status="blocked_external_state", blocked_check="idempotency",
                   skip_reason=f"阻断-幂等态不明: {idem.reason}")
        _sj_terminal(_sj, rec, _iter, _prd); log(f"  ⛔ {slug}: {rec['skip_reason']}"); return rec
    if idem.state is ExtState.FOUND:   # 明确已投递 → skip
        rec.update(status="skip", skip_reason=f"跳过-{idem.reason}")
        _sj_terminal(_sj, rec, _iter, _prd); log(f"  ⏭ {slug}: {rec['skip_reason']}"); return rec
    # ── 准入 4：在途 PR 限量（R1）
    inflight_res = count_inflight_prs(owner_repo)
    if inflight_res.is_unknown:   # fail-safe：在途数不明 → 阻断（旧版返 0 容忍可能超额投递）
        rec.update(status="blocked_external_state", blocked_check="inflight_count",
                   skip_reason=f"阻断-在途PR数不明: {inflight_res.reason}")
        _sj_terminal(_sj, rec, _iter, _prd); log(f"  ⛔ {slug}: {rec['skip_reason']}"); return rec
    inflight = inflight_res.value
    # single-flight-auto-merge task 2.4：serial_shadow on → max_prs_in_flight 退化为恒 1（D5：不再是「分支总数」
    #   语义；count_inflight_prs 保留为独立「OPEN PR 上限」门，≠ slot——slot 是 journal+flock，task 2.2）。
    #   off → 维持 baseline（prof 上限，默认 2，design 决策#8 不变）。
    _max_inflight = 1 if _coord.flags.single_flight_serial_shadow else int(prof.get("max_prs_in_flight", 2))
    if inflight >= _max_inflight:
        rec.update(status="skip", skip_reason=f"跳过-超额（在途 {inflight} ≥ {_max_inflight}）")
        _sj_terminal(_sj, rec, _iter, _prd); log(f"  ⏭ {slug}: {rec['skip_reason']}"); return rec

    # ── 零成本 smoke：过准入但不触发 dev loop（不花钱、不开 PR）
    if getattr(args, "dispatch_skip_dev", False):
        rec.update(status="planned",
                   skip_reason=f"--dispatch-skip-dev smoke（已过准入，in-flight {inflight}，未触发 dev loop）")
        log(f"  📋 {slug}: 将投递（已过准入，in-flight {inflight}）— skip-dev 未触发")
        return rec

    # task 3.5：确认投递 dev loop → emit running（round 1）。上方 skip-dev smoke 已 return，不触此处 → 不 RUNNING。
    _sj.emit("running", _iter, _prd, payload={"round": 1})

    # ── 投递：detached worktree on main → 触发控制面 dev-agent.py（ADR-0006 vault-only 执行器）
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
        _sj_terminal(_sj, rec, _iter, _prd); log(f"  ✗ {slug}: {rec['skip_reason']}"); return rec

    # ── verify 闭环（docs/verify-commit-loop-design.md §3）：dev→独立验证→pa-verify 裁判；判红保留分支+反馈进 PRD+增量重投；判绿兜底开 PR
    #    同构模板：stage_critic revise loop（§4.3）。reconcile 顺位后移到「裁定后收尾」——不预先为中间红的分支补开 PR。
    #    执行器选源已固化（ADR-0006）：控制面 scripts/dev-agent.py 唯一，不再探测仓内 dev-agent.{py,mjs}。
    cur_base = base
    _parent_iter: str | None = None       # task 3.3：prior iteration（revise→next attempt 引用，spec Iteration identity）
    # task 3.3 P0-3：session-aware retry 参数（revise→RetryPolicy.decide 生成；baseline flag 关=保持 None/False）
    cur_resume_session: str | None = None
    cur_fork_session = False
    _parent_fb_digest: str | None = None  # task 3.3：prior feedback artifact digest（next attempt 引用）
    for round_n in range(1, VERIFY_MAX_ROUNDS + 1):
        _iter = _coord.next_iteration(round_n)   # task 3.3：每轮 distinct deterministic iteration（seq=round_n；
        #   planned/running 用 run 级 seq0，每 attempt 一个新 iteration；distinct 使 reducer/recovery 可按 attempt 切片）
        # task 3.4：driven retry prompt 从 immutable PRD + journal feedback artifact（recovery context 从
        #   journal 抽 last verifier_feedback artifact path；baseline 照旧读 PRD 反馈节，不 inject）
        _fb_artifact: str | None = None
        if _coord.flags.journal_driven_dispatch and _parent_fb_digest is not None and prd_content is not None:
            try:
                _ctx = RC.build_recovery_context(
                    iteration_id=_iter, prd_id=_prd, status_value="revise",
                    prd_content=prd_content, events=J.read_events(_sj.path))
                _fb_artifact = _ctx.last_verifier_feedback_path
            except Exception:
                _fb_artifact = None    # 容错：recovery 抽取失败 → 退回 baseline 读 PRD，不崩 verify 闭环
        # add-cross-prd-learning-memory Section 5 接线（接线点 2）：dispatch-entry 检索 + lesson block
        # artifact。``_build_lessons_pkg`` fail-open：injection=off / catalog 故障 / retrieval 空 → artifact_path=None
        # （dev-agent baseline prompt，identity no-op）。selected_lesson_ids 记入 rec 桥接 terminal memory_mode
        # （接线点 3 在 ``_attach_learning_memory`` 消费）。每轮重算（catalog append-only；fresh retrieval）。
        _lessons_pkg = _build_lessons_pkg(prof, prd_abs, project_id=proj, run_id=_run,
                                          timestamp=_now_iso(), injection_on=_learning_injection_on)
        _lessons_artifact: str | None = _lessons_pkg["artifact_path"]
        if round_n == 1 or _lessons_pkg["selected_lesson_ids"]:
            # round 1 是 canonical injection 信号；后续 round 若仍有注入则覆盖（terminal effectiveness 用最新）
            rec["_learning_selected_ids"] = _lessons_pkg["selected_lesson_ids"]
            rec["_learning_candidate_count"] = _lessons_pkg["candidate_count"]
            rec["_learning_promotion_count"] = _lessons_pkg["promotion_count"]
            if _lessons_pkg["degraded_class"]:
                rec["_learning_injection_degraded"] = _lessons_pkg["degraded_class"]
        if _coord.flags.session_aware_retry:   # task 3.3 P0-3：retry 模式注入 state_dir+session 参数（baseline flag 关→原 cmd 零变化）
            cmd = _dev_cmd(prof, prd_abs, cur_base, src_abs, feedback_artifact=_fb_artifact,
                           state_dir=str(STATE_DIR), iteration_seq=round_n,
                           resume_session=cur_resume_session, fork_session=cur_fork_session,
                           lessons_artifact=_lessons_artifact)
        else:
            cmd = _dev_cmd(prof, prd_abs, cur_base, src_abs, feedback_artifact=_fb_artifact,
                           lessons_artifact=_lessons_artifact)
        if cmd is None:
            rec.update(status="fail", skip_reason="控制面 dev-agent.py 缺失（控制面安装异常）")
            _sj_terminal(_sj, rec, _iter, _prd); log(f"  ✗ {slug}: {rec['skip_reason']}"); return rec
        script_json = _run_dev_agent(cmd, wt, slug, log_file)
        # 5.1 测试发布门拦截（dev-agent exit 14 → blocked_by_gate）——终态短路，不进 verify/reconcile、不开 PR。
        # dev-agent 已跑完 dev loop 但结构化测试证据不达发布门（test_not_run / test_failed / test_stale）：
        # 门在 commit/push/PR **之前**触发 → 分支已建但无发布提交。宁拦勿错放：既不算 dispatch 成功、也不算
        # verify 绿；本地分支与证据保留待运维 triage（flaky test？证据窗过期？测试基建？——非 verify 闭环可自愈）。
        if script_json and script_json.get("blocked_by_gate"):
            rec.update(status="blocked_test_gate",
                       branch=script_json.get("branch"),                 # 已建但未 push（门在 commit 前）
                       gate_status=script_json.get("gate_status"),        # test_not_run | test_failed | test_stale
                       gate_reason=sanitize(script_json.get("gate_reason")),   # 脱敏落 state（机械文本，defense-in-depth）
                       test_status=script_json.get("test_status"),        # green | red | none
                       evidence_fresh=script_json.get("evidence_fresh"),
                       dev_cost=script_json.get("cost"), dev_turns=script_json.get("turns"),
                       dev_test_cmd=script_json.get("test_cmd"),
                       dev_killed=False,
                       skip_reason=f"阻断-测试发布门: {script_json.get('gate_status')}")
            log(f"  🚫 {slug}: dev r{round_n} 测试发布门拦截（{rec['gate_status']}）→ 不验证/不开 PR，待运维 triage")
            _sj.emit("agent_finished", _iter, _prd, payload={   # test_gate 在 agent_finished emit 前分流，补齐迁移前置
                "round": round_n, "branch": rec.get("branch"), "gate_blocked": True})
            _sj_terminal(_sj, rec, _iter, _prd)
            return rec
        if script_json:
            rec["dev_cost"] = script_json.get("cost"); rec["dev_turns"] = script_json.get("turns")
            rec["branch"] = script_json.get("branch")
            rec["stalled"] = bool(script_json.get("stalled"))   # SPEC #27：dev-agent 主动刹车（exit 12，非超时）
            rec["run_log"] = script_json.get("run_log")          # 监控 jsonl 路径（state/runs/...）
            rec["dev_test_cmd"] = script_json.get("test_cmd")
            _af_payload = {   # task 3.2：dev-agent 阶段结束（RUNNING→AGENT_FINISHED）
                "round": round_n, "branch": rec.get("branch"), "cost": rec.get("dev_cost"),
                "turns": rec.get("dev_turns"), "stalled": rec.get("stalled")}
            if _parent_iter is not None:   # task 3.3：next attempt references prior iteration + feedback artifact
                #   （spec scenario「Verify revise creates a new iteration」；round≥2 由 revise 置位，round1 _parent_iter=None）
                _af_payload["parent_iteration"] = _parent_iter
                _af_payload["parent_feedback_digest"] = _parent_fb_digest
            _sj.emit("agent_finished", _iter, _prd, payload=_af_payload)
        rec["dev_killed"] = script_json is None   # 无 stdout JSON → 大概率 kill/崩（与 stalled 互补：超时 vs 主动刹车）

        branch = rec.get("branch")
        if not branch:                        # dev 建分支前就崩/超时 → 无可验证、无分支做下次 base，对账收尾（stall 救不了，§2 实证）
            log(f"  ✗ {slug}: dev r{round_n} 未吐 branch（建分支前崩/超时）→ 对账收尾")
            reconcile_pr(repo, owner_repo, rec, base, slug, interrupted=True); break

        # 独立验证（门=branch+相对 cur_base 有 commit；与 reconcile status 解耦——闭环内 verify 先于对账）
        commits = _has_commits(repo, cur_base, branch)
        has_commits = commits.state is ExtState.FOUND and bool(commits.value)
        if commits.is_unknown:      # fail-safe：commit 态不明 → 跳过独立验证（不臆断有无产出）
            log(f"  ⚠ {slug}: r{round_n} commit 态不明，跳过独立验证: {commits.reason}")
        if has_commits:
            rec["verify"] = independent_verify(repo, branch, stamp, slug, log_file, prof,
                                               test_cmd_hint=(script_json.get("test_cmd") if script_json else None))
        else:
            rec["verify"] = None              # 无新增 commit（如 round2 dev 未动）→ 无可验证
        _vj = rec.get("verify") or {}
        # task 4.2 evidence integrity：green test result（机械绿）必须持久化为 content-addressed artifact
        # （valid digest），否则不当 fresh green evidence → blocked_evidence（spec verified-publication-integrity
        # 「Test artifact write fails」：无法持久化/校验的 green result 不得成 complete fresh green evidence，
        # 记 integrity-block reason）。fail-closed：store/读失败即降级，防下游 dual gate 误判 published。
        if _vj.get("pass"):
            try:
                _green_out = Path(_vj["test_log"]).read_text(encoding="utf-8") if _vj.get("test_log") else ""
                _ev = artifact_store.store(STATE_DIR / "artifacts" / _run, _green_out,
                                           kind="test_output", sensitivity="internal")
                _vj["evidence_ref"] = {"digest": _ev.digest, "path": _ev.path, "size": _ev.size}
            except Exception as _ee:
                rec["status"] = "blocked_evidence"
                rec["skip_reason"] = f"green test evidence artifact 持久化失败（不当 fresh green evidence）: {_ee}"
                rec["verify"] = {**_vj, "pass": False}   # fail-closed 降级（不当 green evidence，防下游误判）
                log(f"  ⛔ {slug}: green evidence artifact 持久化失败 → blocked_evidence（不当 fresh green evidence）")
                _sj.emit("test", _iter, _prd, payload={
                    "round": round_n, "test_pass": True, "evidence_blocked": True, "reason": sanitize(str(_ee))})
                _sj_terminal(_sj, rec, _iter, _prd)
                break
        _sj.emit("test", _iter, _prd, payload={   # task 3.2 + 4.2：独立验证结果 + green evidence artifact ref
            "round": round_n, "test_pass": _vj.get("pass"), "test_rc": _vj.get("test_rc"),
            "has_commits": has_commits, "evidence_ref": _vj.get("evidence_ref")})

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
        # task 3.2：verify 闭环判决旁路 emit。verifying=状态迁移（AGENT_FINISHED→VERIFYING）；
        # verifier=payload 观测（判决）；pass→publish_ready（VERIFYING→PUBLISH_READY）/ revise（VERIFYING→REVISE）。
        _sj.emit("verifying", _iter, _prd, payload={"round": round_n})
        if vinfo:
            _sj.emit("verifier", _iter, _prd,
                     payload={"round": round_n, "verdict": vinfo.get("verdict")})
            if vinfo.get("verdict") == "pass":
                _sj.emit("publish_ready", _iter, _prd, payload={"round": round_n})
            elif vinfo.get("verdict") == "revise":
                _sj.emit("revise", _iter, _prd, payload={"round": round_n})

        if vinfo and vinfo.get("verdict") == "pass":
            # task 4.4：publication 前结构化 reconcile（idempotency keys + KeyResolver 三态）——
            #   session_aware_retry 开 → 用 coord.owned resolver 对账 push/pr/test 幂等键，ReconciliationReport
            #   记入 rec + journal（exactly-once 结构化证据；unknown → fail-safe 阻断，不盲目 publish）。
            #   baseline（flag 关）→ 跳过（reconcile_pr 仍做真实 GitHub 对账，dispatch 决策零变化）。
            if _coord.flags.session_aware_retry and _coord.resolver is not None:
                _pub_report = reconcile.reconcile_side_effects(
                    iteration_id=_iter, targets=_publication_targets(owner_repo, rec, _vj),
                    resolver=_coord.resolver)
                rec["publication_reconciliation"] = {
                    "confirmed": tuple(t.kind for t in _pub_report.confirmed),
                    "pending": tuple(t.kind for t in _pub_report.pending),
                    "unknown": tuple(t.kind for t in _pub_report.unknown),
                    "safe_to_publish": _pub_report.safe_to_retry,
                }
                _sj.emit("reconcile", _iter, _prd, payload={
                    "round": round_n, "phase": "publication",
                    "confirmed": list(t.kind for t in _pub_report.confirmed),
                    "pending": list(t.kind for t in _pub_report.pending),
                    "unknown": list(t.kind for t in _pub_report.unknown),
                    "safe_to_publish": _pub_report.safe_to_retry})
                if not _pub_report.safe_to_retry:
                    log(f"  ⛔ {slug}: publication reconcile 有 unknown 副作用 → fail-safe 阻断（不盲目 publish）")
                    rec.update(status="blocked_external_state", blocked_check="publication_reconcile",
                               skip_reason="阻断-publication reconcile 有 unknown 副作用（不盲目 publish）")
                    break   # 不进 reconcile_pr；for 外 _sj_terminal 统一收尾
            # single-flight-auto-merge task #5 接线（D2/D6/D7）：verify 绿 + auto_merge flag 开 → 真实自动合 main
            #   （dev-agent --phase merge 机械执行 fetch→rebase→收证→classify→CLEAN 则 --no-ff merge + ff-only push，
            #   守 D6：控制面只发 cmd，在目标仓 worktree 内经 git() 机械层跑，不直接持 git 写句柄）。flag 关 → baseline
            #   兜底开 PR 待 review（dispatch 决策零变化，design 决策#8）。merged→记 merge_commit + status=merged；
            #   CONFLICT/UNKNOWN/push_failed→不强合（main 未碰），triage_reason 进 triage 池（task 5.1）。
            #   post-merge 闸 + auto-revert 兜底已接（task 4.x：PASS→merged / FAIL→revert，REVERTED=triage /
            #   revert CONFLICT·UNKNOWN 或 post-merge UNKNOWN→halt 整仓+CRITICAL；flag 默认关→baseline 不真合，待 canary 后再开）。
            if _coord.flags.single_flight_auto_merge:
                # task 4.4 revert 循环熔断（D11）：同幂等键（_prd）PRD 在 cooldown 窗口内曾被 post_merge_red_reverted
                #   → 禁再 auto-merge，直接进 triage——防「branch 绿但 main 红」的 PRD 夜夜复发无限循环（spec
                #   「Reverted PRD re-admitted inside cooldown」）。fail-open：冷却 journal 读不到/损坏 → 不 block。
                if CB.is_in_cooldown(STATE_DIR, owner_repo, _coord.circuit_key, now_fn=_slot_now):
                    rec["status"] = "triaged"
                    rec["triage_reason"] = "cooldown_revert_loop"
                    log(f"  🧊 {slug}: 熔断命中（PRD {_prd[:12]} 在 {owner_repo} cooldown 窗口内）→ 不 merge，进 triage(cooldown_revert_loop)")
                    break   # → round 循环外 2272 统一 _sj_terminal 收尾
                # task 6.x 方案 C（D12）：merge 闭环 crash 安全门——上次 merge/revert started 无闭合（crash 在
                #   phase 中，push 可能已发生）→ halt 整仓 + CRITICAL（绝不盲目重 merge，防「merge push 后 crash
                #   → cron 重分发 → rebase CLEAN → 重复合 main」致命场景，Agent 实证 D12 三缺）。fail-safe：journal
                #   损坏→True halt（破坏性副作用门不可放行，区别于 circuit_breaker fail-open）。6.1b reconcile 种 /
                #   6.1c crash boundary 为 follow-up，本门是主防线，先落地。
                if ML.has_open_intent(STATE_DIR, owner_repo, _prd):
                    rec["status"] = "halted"
                    rec["triage_reason"] = "merge_loop_open_intent"
                    _halt_slot_safe(slot_handle, reason="merge_loop_open_intent", run_id=_run, prd_id=_prd,
                                    iteration_id=_iter, owner_repo=owner_repo)
                    _raise_critical_alert_safe(STATE_DIR, owner_repo, _prd, reason="merge_loop_open_intent", stamp_fn=_now_iso)
                    log(f"  🛑 {slug}: merge_loop 检出未闭合 intent（PRD {_prd[:12]} 上次 merge/revert crash 在 phase 中）→ halt 整仓 + CRITICAL（不重 merge，须人工查 main_status）")
                    break   # → round 循环外统一 _sj_terminal 收尾
                ML.record_event(STATE_DIR, owner_repo, _prd, "merge_started", stamp_fn=_now_iso, branch=branch, main_ref=base)
                _merge_cmd = MP.build_merge_cmd(
                    python=_env_python(prof.get("conda_env", "")), dev_agent_py=DEV_AGENT_PY,
                    branch=branch, main_ref=base, prd_id=_prd, state_dir=str(STATE_DIR))
                _merge_json = _run_dev_agent(_merge_cmd, wt, slug, log_file)   # 复用末行 JSON 解析（同 dev loop）
                _mr = MP.parse_merge_result(_merge_json)
                if _mr.merged:
                    rec["merge_commit"] = _mr.merge_commit
                    log(f"  🎉 {slug}: verify 绿 + rebase CLEAN → 已合 main（merge {_mr.merge_commit[:8]}）")
                    # task 4.x post-merge 闸（D8：基线=集成后 main 全量 suite，≠ verify candidate branch；三态经
                    #   classify_post_merge）。PASS→放行 merged；FAIL→revert(4.3)；UNKNOWN→keep+halt+CRITICAL（不 auto-revert）。
                    _pm_cmd = MP.build_post_merge_cmd(
                        python=_env_python(prof.get("conda_env", "")), dev_agent_py=DEV_AGENT_PY,
                        test_cmd=_post_merge_test_cmd(repo, rec, prof) or "", main_ref=base, prd_id=_prd,
                        state_dir=str(STATE_DIR))
                    _pmr = MP.parse_post_merge_result(_run_dev_agent(_pm_cmd, wt, slug, log_file))
                    rec["post_merge_verdict"] = _pmr.verdict.value
                    # task 4.6：可查询「main 是否已过 post-merge 验证」状态（design F8）——下游/CD 据 main_post_merge_status
                    #   判 main 验证态，而非盲猜（main 在 [push, verdict] 窗口可能红，MAX_MAIN_RED_WINDOW_SECONDS 上界）。
                    MS.record_main_verified(STATE_DIR, owner_repo, main_ref=base, merge_commit=_mr.merge_commit,
                                            verdict=_pmr.verdict.value, prd_id=_prd, stamp_fn=_now_iso)
                    if _pmr.verdict is MP.PostMergeVerdict.PASS:
                        rec["status"] = "merged"
                        ML.record_event(STATE_DIR, owner_repo, _prd, "merge_completed", stamp_fn=_now_iso, merge_commit=_mr.merge_commit)   # task 6.x：闭合 merge intent（闭环成功=merged，crash 后据闭合事件知 main 已验证）
                        log(f"  ✅ {slug}: post-merge main 全量测试绿 → 保留（merged）")
                    elif _pmr.verdict is MP.PostMergeVerdict.FAIL:
                        # task 4.3：post-merge FAIL → revert 单一 merge commit（D7：git revert -m 1 + ff-only push）
                        log(f"  🔴 {slug}: post-merge main 红 → auto-revert merge {_mr.merge_commit[:8]}")
                        ML.record_event(STATE_DIR, owner_repo, _prd, "revert_started", stamp_fn=_now_iso, merge_commit=_mr.merge_commit)   # task 6.x：revert intent（revert 是独立破坏性 push；crash 在 revert push 中→下轮 has_open_intent True→halt 防重复 revert）
                        _rv_cmd = MP.build_revert_cmd(
                            python=_env_python(prof.get("conda_env", "")), dev_agent_py=DEV_AGENT_PY,
                            merge_commit=_mr.merge_commit, main_ref=base, prd_id=_prd, state_dir=str(STATE_DIR))
                        _rvr = MP.parse_revert_result(_run_dev_agent(_rv_cmd, wt, slug, log_file))
                        if _rvr.outcome is MP.RevertOutcome.REVERTED:
                            rec["reverted"] = True
                            rec["revert_commit"] = _rvr.revert_commit
                            rec["status"] = "triaged"
                            rec["triage_reason"] = "post_merge_red_reverted"
                            CB.record_revert(STATE_DIR, owner_repo, _coord.circuit_key, stamp_fn=_now_iso)   # task 4.4 fix：记 cooldown（circuit_key 跨 cron 稳定，下轮 cron re-admission 熔断查此）
                            ML.record_event(STATE_DIR, owner_repo, _prd, "revert_completed", stamp_fn=_now_iso, merge_commit=_mr.merge_commit, revert_commit=_rvr.revert_commit)   # task 6.x：闭合 revert intent（main 回绿，闭环以 triage 收尾，可重试）
                            log(f"  ↩️ {slug}: revert 成功（{_rvr.revert_commit[:8]}）→ main 回绿，进 triage(post_merge_red_reverted)")
                        else:   # CONFLICT/UNKNOWN：revert 本身失败 → halt 整仓 + CRITICAL（main 仍红，绝不 continue）
                            rec["status"] = "halted"
                            rec["triage_reason"] = f"post_merge_revert_{_rvr.outcome.value}"
                            _halt_slot_safe(slot_handle, reason=rec["triage_reason"], run_id=_run, prd_id=_prd,
                                            iteration_id=_iter, owner_repo=owner_repo)
                            _raise_critical_alert_safe(STATE_DIR, owner_repo, _prd, reason=rec["triage_reason"], stamp_fn=_now_iso)
                            log(f"  🛑 {slug}: revert {_rvr.outcome.value} → halt 整仓 + CRITICAL（main 仍红，须人工）")
                    else:   # UNKNOWN：不确定真红 → 保留 main（不 auto-revert）+ halt 整仓 + CRITICAL
                        rec["status"] = "halted"
                        rec["triage_reason"] = "post_merge_unknown"
                        _halt_slot_safe(slot_handle, reason="post_merge_unknown", run_id=_run, prd_id=_prd,
                                        iteration_id=_iter, owner_repo=owner_repo)
                        _raise_critical_alert_safe(STATE_DIR, owner_repo, _prd, reason="post_merge_unknown", stamp_fn=_now_iso)
                        log(f"  🛑 {slug}: post-merge UNKNOWN（ran={_pmr.evidence.ran}）→ halt 整仓 + CRITICAL（不 auto-revert，须人工）")
                else:
                    rec["triage_reason"] = _mr.triage_reason    # 固定枚举：rebase_conflict/rebase_unknown/push_failed
                    rec["status"] = "triaged"            # task 5.1 triage 池（不阻塞，不强合，main 未碰）
                    ML.record_event(STATE_DIR, owner_repo, _prd, "merge_abandoned", stamp_fn=_now_iso, reason=_mr.triage_reason)   # task 6.x：闭合 merge intent（main 未碰，安全结束→允许重试，非 halt）
                    log(f"  🧪 {slug}: merge 进 triage（{_mr.triage_reason}，rebase={_mr.rebase_outcome.value}）→ 不强合")
            else:
                # single-flight-auto-merge task 7.1a shadow 模式（serial_shadow on, auto_merge off）：verify 绿后
                #   跑 classify-only rebase，记 shadow merge 决策（CLEAN/CONFLICT/UNKNOWN）作 parity 证据，但**不
                #   merge/push**（main 不碰，守 docstring「merge/revert 只 log」+ ADR-0008 护栏#7 shadow gate）。
                #   fail-open：classify 调用异常/超时/无输出 → 记 unknown 但**不阻断** baseline 开 PR（shadow 是
                #   可观测层非安全层；「main 不碰」由 dev-agent classify-only 契约 + 离线 drill 7.1b 保证）。
                if _coord.flags.single_flight_serial_shadow:
                    _decision = None
                    try:
                        _cls_cmd = MP.build_classify_cmd(
                            python=_env_python(prof.get("conda_env", "")), dev_agent_py=DEV_AGENT_PY,
                            branch=branch, main_ref=base, prd_id=_prd, state_dir=str(STATE_DIR))
                        _decision = _shadow_merge_decision(True, _run_dev_agent(_cls_cmd, wt, slug, log_file))
                    except Exception as _e:
                        _decision = "unknown"
                        log(f"  ⚠️ {slug}: shadow classify 异常→记 unknown，不阻断 baseline 开 PR（{_e}）")
                    if _decision is not None:
                        rec["shadow_merge_decision"] = _decision
                        _sj.emit("shadow_merge_decision", _iter, _prd,
                                 payload={"owner_repo": owner_repo, "decision": _decision})
                        log(f"  👻 {slug}: shadow merge 决策={_decision}（serial_shadow：只 log 不改 main）")
                # baseline：兜底开正常 PR 收尾（治 baostock 式 interrupted_pr；reconcile 查到 dev 自开 PR 则保持 pr_open）
                log(f"  ✅ {slug}: verify 绿（r{round_n}）→ 兜底开 PR 收尾")
                reconcile_pr(repo, owner_repo, rec, base, slug, interrupted=False)
            break
        if vinfo and vinfo.get("verdict") == "revise" and round_n < VERIFY_MAX_ROUNDS:
            # 判红（机会未用满）：保留分支做下次 base + 反馈追加进 PRD + 增量 --base=<上次分支> 重投
            log(f"  🔴 {slug}: verify 红（r{round_n}）→ 保留 {branch} 做下次 base，反馈进 PRD，增量重投 r{round_n + 1}")
            _fb_digest = _append_verify_feedback(prd_abs, vinfo.get("feedback_section", ""), round_n,
                                     sj=_sj, iter_id=_iter, prd_id=_prd,
                                     artifact_root=STATE_DIR / "artifacts" / _run,
                                     driven=_coord.flags.journal_driven_dispatch)   # task 3.2：driven→摘 PRD 追加
            _parent_iter = _iter              # task 3.3：next attempt（round_n+1）引用 prior iteration
            _parent_fb_digest = _fb_digest    # task 3.3：next attempt 引用 round_n feedback artifact digest
            cur_base = branch
            # task 3.3 P0-3：session-aware retry 闭环——run_daily 驱动 RetryPolicy（recover_iteration 内
            #   reconcile 副作用 + decide）→ 据 mode 生成 dev-agent session 参数 + emit journal 决策事件 +
            #   消耗 retry 预算。baseline（flag 关）→ 跳过，走原增量 --base 重投（dispatch 决策零变化）。
            if _coord.flags.session_aware_retry and _coord.resolver is not None \
                    and getattr(_coord, "session_store", None) and getattr(_coord, "retry_budget", None):
                _rplan = reconcile.recover_iteration(
                    journal_path=_sj.path, run_id=_run, prd_id=_prd, iteration_id=_iter,
                    base=cur_base, prd_content=prd_abs.read_text(encoding="utf-8"),
                    targets=_publication_targets(owner_repo, rec, _vj),
                    resolver=_coord.resolver, session_store=_coord.session_store,
                    budget=_coord.retry_budget,
                    verifier_signal=RP.VerifierSignal.LOCAL_FEEDBACK)   # revise=局部反馈，history 可信
                _rmode = _rplan.decision.mode
                _sj.emit("retry_decided", _iter, _prd, payload={
                    "round": round_n, "next_round": round_n + 1, "mode": _rmode.value,
                    "reason": _rplan.decision.reason, "consumes_retry": _rplan.decision.consumes_retry,
                    "external_known": _rplan.reconciliation.external_known,
                    "iteration_status": _rplan.iteration_status})
                if _rmode in (RP.RetryMode.BLOCK, RP.RetryMode.STOP):
                    log(f"  ⛔ {slug}: retry {_rmode.value}（{_rplan.decision.reason}）→ 不重试")
                    rec.update(status="retry_blocked" if _rmode is RP.RetryMode.BLOCK else "retry_budget_exhausted",
                               skip_reason=f"阻断-retry {_rmode.value}: {_rplan.decision.reason}")
                    break
                # 据 mode 设下一轮 session 参数（dev-agent 透传 SDK：resume/fork/new-session）
                if _rmode is RP.RetryMode.RESUME:
                    _sess = _coord.session_store.load(_iter)
                    cur_resume_session = getattr(_sess, "session_id", None) if _sess else None
                    cur_fork_session = False
                elif _rmode is RP.RetryMode.FORK:
                    cur_resume_session = None
                    cur_fork_session = True
                else:   # NEW_SESSION
                    cur_resume_session = None
                    cur_fork_session = False
                if _rplan.decision.consumes_retry:
                    _coord.retry_budget.consume(RP.BudgetDimension.SDK_RETRY)
            continue
        # 判红用满（round_n==VERIFY_MAX_ROUNDS）/ pa-verify 异常 / 无产出 → 对账降级 interrupted_pr（不 drop，半成品留 review）
        log(f"  ⏸ {slug}: verify 终止（r{round_n}, verdict={rec['verify_verdict']}）→ 对账收尾（中断 PR 不 drop）")
        reconcile_pr(repo, owner_repo, rec, base, slug, interrupted=True); break

    _sj_terminal(_sj, rec, _iter, _prd, artifact_root=STATE_DIR / "artifacts" / _run)   # task 3.2 + 4.4：统一终态 emit（publication 前 reconcile test evidence）
    return rec


def _publication_targets(owner_repo: str, rec: dict, vj: dict) -> list:
    """task 4.4：publication 前对账的副作用目标（push 远端分支 / pr ``owner:branch`` / test green evidence digest）。

    commit 由 ``_has_commits`` 在 verify 阶段已查（GitHub 视角）；此处对账 publication 关键副作用幂等键，
    交 ``reconcile.reconcile_side_effects`` + coord.owned resolver 算 confirmed/pending/unknown 三态。
    """
    targets = []
    branch = rec.get("branch")
    if branch:
        targets.append(reconcile.SideEffectTarget("push", branch))
        targets.append(reconcile.SideEffectTarget("pr",
                     f"{owner_repo}:{branch}" if owner_repo else branch))
    ev = vj.get("evidence_ref") if vj else None
    if isinstance(ev, dict) and ev.get("digest"):
        targets.append(reconcile.SideEffectTarget("test", ev["digest"]))
    return targets


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

    # 1) 实查 GitHub 是否已有该分支的 PR（三态；UNKNOWN → 保留分支，不补开/删除/覆盖，OpenSpec fail-safe-dispatch / tasks 4.4）
    pr = _lookup_pr(owner_repo, branch)
    if pr.is_unknown:
        rec.update(status="blocked_external_state", blocked_check="pr_lookup",
                   skip_reason=f"保留分支-PR态不明不补开/删除: {pr.reason}")
        log(f"  ⛔ {slug}: {rec['skip_reason']}"); return
    if pr.state is ExtState.FOUND:
        pr_url, pr_state = pr.value.get("url"), pr.value.get("state")
        rec["pr_url"] = pr_url
        rec["status"] = "pr_open" if pr_state == "OPEN" else f"pr_{(pr_state or '').lower()}"
        log(f"  ✅ {slug}: 已开 PR {pr_url}（{pr_state}）"); return

    # 2) 无 PR（NOT_FOUND）：查分支有无 commit（三态；UNKNOWN → 保留分支不删——旧版容忍可能误删有产出分支）
    commits = _has_commits(repo, base, branch)
    if commits.is_unknown:
        rec.update(status="blocked_external_state", blocked_check="commit_lookup",
                   skip_reason=f"保留分支-commit态不明不补开/删除: {commits.reason}")
        log(f"  ⛔ {slug}: {rec['skip_reason']}"); return
    has_commit = commits.state is ExtState.FOUND and bool(commits.value)
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
            r = subprocess.run(
                ["gh", "pr", "create", "-R", owner_repo, "--base", base, "--head", branch,
                 "--title", title, "--body", body],
                capture_output=True, text=True, timeout=30)
            if r.returncode != 0:   # fail-safe：补开 PR 失败 → 保留分支不臆断成功（旧版无视 rc → 空 url 却标 pr_open=幻影绿 PR）
                rec.update(status="blocked_external_state", blocked_check="pr_create",
                           skip_reason=f"保留分支-补开PR失败(rc={r.returncode}): {sanitize(r.stderr)}")
                log(f"  ⛔ {slug}: {rec['skip_reason']}"); return
            url = r.stdout.strip()
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


def _post_merge_test_cmd(repo: str, rec: dict, prof: dict | None) -> str | None:
    """single-flight-auto-merge task 4.x：构造 post-merge main 全量测试命令（D8：基线=集成后 main，≠ verify 的
    candidate branch）。命令源同 verify（决策 E）：Node 仓 → package.json scripts.test；Python 仓 → dev-agent
    上报的 ``dev_test_cmd``（dev-agent 经 sys.executable 注入 conda env PATH，裸 python 命中 env）。未上报 →
    None（dev-agent ran=False → UNKNOWN → halt，不当代绿）。"""
    if (Path(repo) / "package.json").exists():
        try:
            scripts = json.loads((Path(repo) / "package.json").read_text(encoding="utf-8")).get("scripts", {})
            return scripts.get("test")
        except Exception:
            return None
    return rec.get("dev_test_cmd")


def _halt_slot_safe(slot_handle, *, reason: str, run_id: str, prd_id: str,
                    iteration_id: str, owner_repo: str) -> None:
    """single-flight-auto-merge task 4.x：post-merge UNKNOWN / revert 非 REVERTED → halt 整仓到人工（spec
    「halts the queue ... no further PRD admitted until manual resolution」）。serial_shadow on（有 handle）
    → ``halt_slot`` 写 slot_halted 终态（下轮 acquire blocked(halted)，本 run 后续同仓 PRD 亦 blocked）；
    off（baseline 无 slot）→ no-op（halt 仅记 rec/log）。幂等 + fail-open（halt_slot 异常不掩盖 rec 已标 halted）。"""
    if slot_handle is None:
        return
    try:
        SF.halt_slot(slot_handle, reason=reason, run_id=run_id, prd_id=prd_id,
                     iteration_id=iteration_id, owner_repo=owner_repo, stamp_fn=_now_iso)
    except Exception as e:
        log(f"  ⚠️ halt_slot 失败（{reason}）: {e}（rec 已标 halted，须人工查 slot）")


def _raise_critical_alert_safe(state_dir, owner_repo: str, prd_id: str, *, reason: str, stamp_fn) -> None:
    """single-flight-auto-merge task 4.5：halt 时落 durable CRITICAL 告警到 alerts journal（design F5）。
    CRITICAL 安全事件须总 durable（**不受 ``journal_shadow`` flag gating**，crash 不丢），区别于 ShadowJournal。
    fail-open：写失败不 raise（halt 安全已由 ``_halt_slot_safe`` 的 slot_halted 保证；告警丢失须人工查）。"""
    try:
        CA.raise_alert(state_dir, owner_repo, prd_id, reason=reason, stamp_fn=stamp_fn)
    except Exception as e:
        log(f"  ⚠️ raise_alert 失败（{reason}）: {e}（rec 已标 halted + slot 已 halt，须人工查 alerts）")


def _shadow_merge_decision(serial_shadow: bool, classify_payload: dict | None) -> str | None:
    """single-flight-auto-merge task 7.1a：serial_shadow on → 据 classify-only dev-agent 结果返 shadow merge 决策。

    纯决策缝合点（接 ``dispatch_one`` verify-绿 else 分支）：``single_flight_serial_shadow=on`` 且 ``auto_merge=off``
    时，控制面已发 ``--classify-only`` cmd 跑 fetch→rebase→classify（main 不碰），本函数把 dev-agent 末行 JSON
    经 ``parse_merge_result`` 降级解析为 rebase 三态（clean/conflict/unknown）作 shadow parity 证据（canary gate
    的对照基线——shadow 须产出可观测决策信号，否则 canary=开盲盒）。serial_shadow off → None（baseline 无 shadow
    决策，不记不 emit）。fail-safe：坏/缺 payload → ``parse_merge_result`` 降级 unknown（绝不当代 clean）。"""
    if not serial_shadow:
        return None
    return MP.parse_merge_result(classify_payload).rebase_outcome.value


def _slot_now() -> datetime:
    """slot lease 算术用的「当前时间」（datetime）。独立成函数便于测试 monkeypatch（同 ``_now_iso``）。"""
    return datetime.now(timezone.utc)


def _slot_blocked_record(entry: dict, owner_repo: str, slot_res) -> dict:
    """single-flight slot 准入 blocked 时 ``_run_one`` 构造的等价 rec（不调 dispatch_one）。

    ``unknown``（slot journal 损坏/读失败）→ ``blocked_external_state``（fail-safe，spec「Single-flight slot
    is unknown → blocked_external_state」）；``inflight``/``flock_busy`` → ``skip``（让位，下轮 cron 再投）。
    rec schema 对齐 ``dispatch_one`` 初始 rec（含 task 1.2 single-flight 字段），保 report 段兼容。"""
    slug = Path(entry.get("prd_path", "")).stem or "unknown"
    rec: dict = {"project": entry.get("project"), "prd_path": entry.get("prd_path"), "slug": slug,
                 "base": None, "status": "pending", "pr_url": None, "branch": None, "dev_killed": False,
                 "stalled": False, "run_log": None, "dev_cost": None, "dev_turns": None, "verify": None,
                 "skip_reason": None, "dev_test_cmd": None, "verify_verdict": None, "verify_round": None,
                 "merge_commit": None, "reverted": False, "triage_reason": None, "post_merge_verdict": None}
    qreason = slot_res.query.reason
    if slot_res.blocked_reason == "unknown":
        rec.update(status="blocked_external_state", blocked_check="single_flight_slot",
                   skip_reason=f"阻断-single-flight slot 状态不明: {qreason}")
    else:   # inflight / flock_busy
        rec.update(status="skip",
                   skip_reason=f"跳过-single-flight slot 占用({slot_res.blocked_reason}): {qreason}")
    return rec


def _run_one(entry: dict, prof: dict | None, stamp: str, args) -> dict:
    """ThreadPoolExecutor worker：取 per-owner_repo 串行互斥后调 dispatch_one（同仓串行、跨仓并行）。

    single-flight-auto-merge task 2.2/2.3：``serial_shadow`` on → ``slot_scope`` 包 dispatch_one（跨进程 flock
    + journal 持久化 slot 态，跨 cron 存活；``threading.Lock`` 进程内不可见，D9 审核一致 F5）。slot lifecycle
    在此 chokepoint 用 ``with`` 包裹——所有 dispatch_one 出口（含异常）统一经 ``__exit__`` release，flock 不泄漏。
    off → baseline ``threading.Lock``（design 决策#8 不变）。仓无 remote（无 owner_repo）→ 不串行。

    add-cross-prd-learning-memory Section 7 接线点 1+3：``dispatch_one`` 返回后调 ``_attach_learning_memory``
    做 terminal reflection + memory_mode 聚合（fail-open；shadow=off 零副作用）。"""
    if not prof:
        return {"project": entry.get("project"), "prd_path": entry.get("prd_path"),
                "status": "skip", "skip_reason": "profile 不存在"}
    repo = prof.get("repo", "")
    owner_repo = repo_owner_repo(repo) if repo else ""

    def _learn(rec: dict) -> None:
        _attach_learning_memory(rec, prof, entry, stamp,
                                sdk_query_fn=getattr(args, "_learning_sdk_query_fn", None))

    # serial_shadow on → 跨进程 single-flight slot（task 2.2/2.3：flock + journal，跨 cron 存活）
    if owner_repo and resolve_flags(env=os.environ).single_flight_serial_shadow:
        _run = loop_ids.run_id(stamp)
        _prd = loop_ids.prd_id(entry.get("prd_path", ""), None)
        _iter = loop_ids.iteration_id(_run, _prd, 0)
        _scope = SF.slot_scope(STATE_DIR, owner_repo, run_id=_run, prd_id=_prd, iteration_id=_iter,
                               now_fn=_slot_now, stamp_fn=_now_iso)
        with _scope as _slot:
            if not _slot.acquired:                 # inflight/unknown/flock_busy → 不投递（让位/fail-safe）
                rec = _slot_blocked_record(entry, owner_repo, _slot)
                _learn(rec); return rec
            # task 4.x：传 slot_handle 进 dispatch_one——post-merge UNKNOWN / revert 非 REVERTED 时 halt 整仓
            rec = dispatch_one(entry, prof, stamp, args, slot_handle=_scope.handle)
            _learn(rec); return rec
    # off → baseline：进程内 threading.Lock（同仓串行、跨仓并行；design 决策#8 不变）
    lock = DISPATCH_LOCKS.get(owner_repo) if owner_repo else None
    with lock if lock else contextlib.nullcontext():
        rec = dispatch_one(entry, prof, stamp, args)
        _learn(rec); return rec


def _dispatch_serial_by_repo(passed: list[dict], profiles: dict, stamp: str, args) -> list[dict]:
    """single-flight-auto-merge task 2.1：同 owner_repo 串行单飞投递（一次一个 PRD 走完 _run_one 闭环才下一个）、
    跨 owner_repo 并行。flag gated（serial_shadow on → stage_dispatch 走此路径；off → 现有全并行 baseline）。

    D1/D9：消灭「merge 时 main 被并发动过」冲突前提——同仓 PRD 顺序投递不重叠。跨进程 flock（task 2.2）
    在此分组结构上加，slot = journal + flock（D9）；当前阶段（无 merge/revert 闭环）= dev→verify。
    DISPATCH_LOCKS（threading.Lock）仍由 _run_one 内取，兜底防 in-process TOCTOU（task 2.2 flock 前的串行保证）。"""
    # 按 owner_repo 分组（保提交序；无 remote 的归 "" 组，彼此并行）
    groups: dict[str, list[dict]] = {}
    for e in passed:
        prof = profiles.get(e.get("project")) or {}
        repo = prof.get("repo", "")
        owner_repo = repo_owner_repo(repo) if repo else ""
        groups.setdefault(owner_repo or "", []).append(e)

    def _run_group_serial(entries: list[dict]) -> list[dict]:
        recs: list[dict] = []
        for e in entries:   # 同组顺序：前一个 _run_one return 才下一个（single-flight，不重叠）
            try:
                recs.append(_run_one(e, profiles.get(e.get("project")), stamp, args))
            except Exception as ex:
                log(f"  ✗ {e.get('prd_path')}: dispatch 异常 {ex}")
                recs.append({"project": e.get("project"), "prd_path": e.get("prd_path"),
                             "status": "fail", "skip_reason": f"异常: {ex}"})
        return recs

    records: list[dict] = []
    with ThreadPoolExecutor(max_workers=len(groups) or 1) as exe:
        for recs in exe.map(_run_group_serial, groups.values()):   # 跨组并行、同组顺序
            records.extend(recs)
    return records


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
    # 临时降噪：DISPATCH_SKIP_PROJECTS（逗号分隔）临时跳过指定项目的 dispatch。
    # 背景 #1105（memory pa-target-plane-dev-exec-lock）：claude CLI 子进程 stdio can_use_tool
    # 权限协议 bug，致 node 项目（cc-web-control 等）dispatch 反复 test_failed（AbortError: Stream
    # closed）。Python 端 workaround 均证伪（方案A 堵 end_input / C3 patch 方法），等 SDK/CLI 上游修。
    # env 空集 = no-op；上游修后清 env 即恢复，代码无残留分支。
    _skip_set = {p.strip() for p in os.environ.get("DISPATCH_SKIP_PROJECTS", "").split(",") if p.strip()}
    skip_records: list[dict] = []
    if _skip_set:
        _skipped = [e for e in passed if e.get("project") in _skip_set]
        passed = [e for e in passed if e.get("project") not in _skip_set]
        for e in _skipped:
            log(f"[dispatch] ⏭ {e.get('project')}: DISPATCH_SKIP_PROJECTS 临时跳过（#1105 stream-closed 降噪）")
            skip_records.append({"project": e.get("project"), "prd_path": e.get("prd_path"),
                                 "slug": Path(e.get("prd_path") or "").stem or None, "status": "skip",
                                 "skip_reason": "#1105 DISPATCH_SKIP_PROJECTS 临时跳过"})
        if _skipped:
            log(f"[dispatch] DISPATCH_SKIP_PROJECTS 跳过 {len(_skipped)} 份，剩 {len(passed)} 份待投")
    if getattr(args, "dispatch_limit", None):
        passed = passed[:args.dispatch_limit]
        log(f"[dispatch] --dispatch-limit={args.dispatch_limit}，只投前 {len(passed)} 份")
    if not passed:
        if skip_records:
            log(f"[dispatch] 过闸 PRD 全被临时跳过（{len(skip_records)} 份），无投递")
            disp_file.write_text(json.dumps(skip_records, ensure_ascii=False, indent=2), encoding="utf-8")
            return skip_records
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

    # single-flight-auto-merge task 2.1：串行单飞投递（flag gated）。on → 同 owner_repo 顺序、跨 owner_repo
    #   并行（_dispatch_serial_by_repo）；off → 现有全并行 ThreadPoolExecutor（baseline 不变，design 决策#8）。
    _serial_shadow = resolve_flags(env=os.environ).single_flight_serial_shadow
    records: list[dict] = []
    if _serial_shadow:
        log(f"[dispatch] single-flight 串行单飞：{len(passed)} 份按 owner_repo 分组串行投递")
        records = _dispatch_serial_by_repo(passed, profiles, stamp, args)
    else:
        # 并行投递（ThreadPoolExecutor，sync subprocess.run 释放 GIL）；dict 保提交序、per-future 异常隔离（#26）
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
    # 临时跳过的项目也落 disp_file（status=skip），让 report 段可见，避免 silent drop
    records.extend(skip_records)
    # records 按 project+slug 排序保 diff 稳定（并行下完成序不确定）
    records.sort(key=lambda r: (r.get("project") or "", r.get("slug") or ""))
    disp_file.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    return records


# task 5.1：triage/halt 出口 triage_reason 固定枚举（防漂移）。report 段聚合校验：triaged/halted rec 的
# triage_reason 非枚举值→log warning（fail-open 不阻断报告）。含 spec D4 七值 + 实现新增（4.3 revert 非
# REVERTED halt / 4.4 cooldown 熔断；D3 halt 强于 D4，故 post_merge_unknown 走 halted 而非 triage）。
TRIAGE_REASONS: frozenset[str] = frozenset({
    "timeout",                       # dev wall-clock 超时（D10；pre-merge 出口，预留）
    "verify_exhausted",              # verify 2 轮仍红（D4；pre-merge 出口，预留）
    "rebase_conflict",               # rebase CONFLICT→不强合（D2）
    "rebase_unknown",                # rebase UNKNOWN→不强合（fail-safe）
    "push_failed",                   # ff-only push reject→不强合（D7）
    "post_merge_red_reverted",       # post-merge FAIL→revert REVERTED→放行进 triage（D3）
    "post_merge_unknown",            # post-merge UNKNOWN→halt 整仓（D3 强于 D4）
    "cooldown_revert_loop",          # 4.4 熔断：同 PRD 冷却窗口内禁再 auto-merge（D11）
    "post_merge_revert_conflict",    # 4.3 revert CONFLICT→halt 整仓（D3）
    "post_merge_revert_unknown",     # 4.3 revert UNKNOWN→halt 整仓（D3）
    "merge_loop_open_intent",        # 6.x 方案 C：merge_loop 检出未闭合 intent→halt 整仓（D12，防 merge push 后 crash 重复合 main）
})


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
    gate = _read_json(STATE_DIR / f"prd_gate_{stamp}.json", [])
    disp = _read_json(STATE_DIR / f"dispatch_{stamp}.json", [])

    date_disp = f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}"
    stats = cand.get("stats", {}) or {}
    passed = [g for g in gate if g.get("verdict") == "pass"]
    dropped = [g for g in gate if g.get("verdict") == "drop"]
    review = [d for d in disp
              if d.get("status") in ("pr_open", "interrupted_pr") and (d.get("verify") or {}).get("pass")]
    failing = [d for d in disp
               if d.get("verify") and not d["verify"].get("pass")
               and d.get("status") not in ("blocked_external_state", "blocked_test_gate",
                                           "blocked_evidence")]   # 5.2 正交：阻断桶不计 failing；task 4.3：blocked_evidence 虽 verify.pass=False 但语义是「绿灯证据无法持久化」（非自报绿但独立红），独立成完整性节
    abnormal = [d for d in disp if d.get("status") in ("skip", "planned", "fail")]   # interrupted_pr 必带 verify（reconcile 仅在有 commit 时补开）→ 已落 review/failing，不混入 abnormal（5.2 桶正交，与概览行一致）
    # 5.2 阻断（fail-safe-dispatch / verified-dev-execution）：远程态不明 / 测试发布门未过——未投递，
    # 既不计入「产出 PR」也不计入「verify 绿/红」。独立成节促运维 triage（auth/远程服务/flaky test）。
    blocked_external = [d for d in disp if d.get("status") == "blocked_external_state"]
    blocked_gate = [d for d in disp if d.get("status") == "blocked_test_gate"]
    # task 4.3：证据/日志完整性阻断——green test evidence 无法持久化（blocked_evidence）或 journal
    # 损坏 fail-closed（state_corrupt）。独立桶：既非测试红（不进 failing）、也非未投递（不在
    # blocked_external/gate）——运维须 triage artifact 存储 / journal 恢复，不能混进 verify 绿红。
    integrity_blocked = [d for d in disp if d.get("status") in ("blocked_evidence", "state_corrupt")]
    # task 5.3：single-flight-auto-merge 闭环三态——merged（已合 main）/ triaged（出队进池不阻塞）/ halted
    # （整仓暂停须人工）。新闭环语义，不进 baseline 的 review/failing/abnormal/blocked/integrity 桶（防混计）。
    merged = [d for d in disp if d.get("status") == "merged"]
    triaged = [d for d in disp if d.get("status") == "triaged"]
    halted = [d for d in disp if d.get("status") == "halted"]
    # task 5.1：triaged/halted 的 triage_reason 须取自 TRIAGE_REASONS 固定枚举；漂移→log warning（fail-open，
    # 不阻断报告；reason 原样渲染 + 计数仍计）。单点聚合校验，不改各 dispatch 出口。
    for _d in (*triaged, *halted):
        _reason = _d.get("triage_reason")
        if _reason and _reason not in TRIAGE_REASONS:
            log(f"[report] ⚠️ triage_reason 漂移：{_reason} 不在固定枚举（slug={_d.get('slug')}）")
    target_repos = sorted({d.get("project", "?") for d in disp})

    def repo_of(d: dict) -> str:
        url = d.get("pr_url") or ""   # pr_url 可能显式为 None（skip/planned 项无 PR）→ coerce 防 re.search TypeError
        m = re.search(r"github\.com/([^/]+/[^/]+)/pull/", url)
        if m:                                   # pr_url 有则取 GitHub owner/name（最可读）
            return m.group(1)
        prof = profiles.get(d.get("project"))
        if prof and prof.get("repo"):           # 回退：本地 repo 路径取 basename
            return Path(prof["repo"]).name
        return d.get("project", "?")

    def pr_no(d: dict) -> str:
        return (d.get("pr_url") or "").rstrip("/").split("/")[-1] or "PR"

    L: list[str] = [f"# 项目推进报告 {date_disp}", "",
                    "## 概览",
                    f"- 今日新内容：{cand.get('today_new_count', 0)} 篇｜"
                    f"技术信号：{stats.get('signals_extracted', 0)}｜"
                    f"候选：{len(cand.get('candidates', []))}｜"
                    f"过闸 PRD：{len(passed)}（drop {len(dropped)}）｜"
                    f"投递目标仓：{len(target_repos)}｜"
                    f"已合 main：{len(merged)}｜"
                    f"产出 PR：{len([d for d in disp if d.get('status') in ('pr_open', 'interrupted_pr')])}｜"
                    f"验证 failing：{len(failing)}｜"
                    f"triage：{len(triaged)}｜"
                    f"halt：{len(halted)}｜"
                    f"完整性阻断：{len(integrity_blocked)}｜"
                    f"失败/超时/跳过：{len([d for d in disp if d.get('status') in ('skip', 'planned', 'fail')])}｜"
                    f"阻断（未投递）：{len(blocked_external) + len(blocked_gate)}"
                    f"（远程态不明 {len(blocked_external)} / 测试门未过 {len(blocked_gate)}）", ""]

    # ✅ 待 review 绿 PR
    L += ["## ✅ 待你 review 合并的 PR（验证绿）"]
    if review:
        L += ["| 目标仓 | PR | 分支 | PRD |", "|---|---|---|---|"]
        for d in review:
            mark = " ⏸中断PR" if d.get("status") == "interrupted_pr" else ""
            L.append(f"| {repo_of(d)} | [{pr_no(d)}]({d.get('pr_url') or ''}){mark} | "
                     f"`{d.get('branch', '')}` | {d.get('slug', '')} |")
    else:
        L.append("（无）")
    L += ["", "> 📝 若某 PR 触碰了既有 `test/*` 文件，review 请重点看测试 diff——"
          "独立验证抓不到测试篡改（§7 盲区）。", ""]

    # ✅ 已自动合入 main（single-flight-auto-merge task 5.2/5.3）——dev+verify 双绿 + rebase CLEAN → 自动
    # --no-ff merge + ff-only push main（替换兜底开 PR）。merge_commit 是本次合入产出的单一 merge commit sha。
    L += ["## ✅ 已自动合入 main（auto-merge）"]
    if merged:
        L += ["| 目标仓 | commit | PRD |", "|---|---|---|"]
        for d in merged:
            L.append(f"| {repo_of(d)} | `{d.get('merge_commit') or '—'}` | {d.get('slug', '')} |")
    else:
        L.append("（无）")
    L.append("")

    # 🔧 需 triage（task 5.2/5.3 / design D4）——出队进池，**不阻塞队列**：dev 超时 / verify 耗尽 / rebase
    # 冲突或不明 / push 失败 / post-merge 红已 revert / 冷却熔断。ejection reason 取自固定枚举（task 5.1）。
    L += ["## 🔧 需 triage（出队进池，不阻塞队列）"]
    if triaged:
        L += ["| 目标仓 | PRD | 原因 |", "|---|---|---|"]
        for d in triaged:
            L.append(f"| {repo_of(d)} | {d.get('slug', '')} | {d.get('triage_reason') or '—'} |")
    else:
        L.append("（无）")
    L.append("")

    # 🛑 halted（task 5.2/5.3 / design D3）——整仓队列暂停，**须人工介入**：post-merge UNKNOWN 或 revert 非
    # REVERTED（CONFLICT/UNKNOWN）。CRITICAL 告警已 durable 落盘（task 4.5），ack 后方可 resume。
    L += ["## 🛑 halted（整仓队列暂停，须人工介入）"]
    if halted:
        L += ["| 目标仓 | PRD | 原因 |", "|---|---|---|"]
        for d in halted:
            L.append(f"| {repo_of(d)} | {d.get('slug', '')} | {d.get('triage_reason') or '—'} |")
    else:
        L.append("（无）")
    L.append("")

    # 🔴 failing
    L += ["## 🔴 验证 failing（项目自报绿但独立测试红，慎合）"]
    if failing:
        L += ["| 目标仓 | PR | 失败测试 | 说明 |", "|---|---|---|---|"]
        for d in failing:
            v = d.get("verify") or {}
            L.append(f"| {repo_of(d)} | [{pr_no(d)}]({d.get('pr_url') or ''}) | "
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

    # 🚫 阻断（fail-safe-dispatch / verified-dev-execution）——远程态不明或测试发布门未过，**未投递**。
    # 单独成节、不计入产出 PR / verify 绿红——运维须 triage：auth 失效 / 远程服务抖动 / flaky test / 证据窗过期。
    L += ["## 🚫 阻断（远程态不明 / 测试发布门未过，未投递）"]
    if blocked_external or blocked_gate:
        L += ["| 目标仓 | 分支 | 阻断类型 | 原因（已脱敏） |", "|---|---|---|---|"]
        for d in blocked_external:
            L.append(f"| {repo_of(d)} | `{d.get('branch') or '—'}` | "
                     f"远程态不明（{d.get('blocked_check') or '?'}） | {d.get('skip_reason') or ''} |")
        for d in blocked_gate:
            L.append(f"| {repo_of(d)} | `{d.get('branch') or '—'}` | "
                     f"测试发布门（{d.get('gate_status') or '?'}） | {d.get('gate_reason') or d.get('skip_reason') or ''} |")
    else:
        L.append("（无）")
    L.append("")

    # 🚫 证据/日志完整性阻断（task 4.3）——green test evidence 无法持久化（evidence）或 journal
    # 损坏 fail-closed（journal）。非测试红、非未投递：独立成节促运维 triage（artifact 存储 / journal
    # 恢复），不计 verify 绿红（spec verified-publication「Test artifact write fails」integrity-block reason）。
    L += ["## 🚫 证据/日志完整性阻断"]
    if integrity_blocked:
        L += ["| 目标仓 | PRD | 分支 | 完整性类别 | 原因（已脱敏） |", "|---|---|---|---|---|"]
        for d in integrity_blocked:
            kind = "证据完整性（evidence）" if d.get("status") == "blocked_evidence" \
                else "日志完整性（journal）"
            L.append(f"| {repo_of(d)} | {d.get('slug', '')} | `{d.get('branch') or '—'}` | "
                     f"{kind} | {d.get('skip_reason') or ''} |")
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
                L.append(f"- `{d.get('slug', '')}` — [{tag}]{mark} {d.get('pr_url') or ''} ｜"
                         f" run log {log_link}{tail}")
            L.append("")
    else:
        L += ["（今日无投递）", ""]

    report_path = REPORT_DIR / f"项目推进报告_{stamp}.md"
    report_path.write_text("\n".join(L) + "\n", encoding="utf-8")
    n_blocked = len(blocked_external) + len(blocked_gate)
    log(f"[report] 已生成 {report_path}（review {len(review)} / failing {len(failing)} / "
        f"drop {len(dropped)} / 阻断 {n_blocked}）")

    _append_daily_pointer(date_disp, stamp, len(review), len(failing), n_blocked,
                          n_merged=len(merged), n_triaged=len(triaged), n_halted=len(halted))

    # SMTP 直发（§8：有活才发，全绿不发；--dry-run/--no-notify 只落盘）
    # 心跳模式（PA_HEARTBEAT=1，cron 触发）：全绿也发一封状态邮件——无头服务器上邮件断了即流水线挂了。
    # 阻断（远程态不明 / 测试门未过）计入 active：须运维 triage（auth / 远程服务 / flaky test），不能静默。
    active = bool(review or failing or n_blocked or integrity_blocked or triaged or halted)   # task 4.3 完整性阻断 + task 5.3 triaged/halted 均计入 active（运维须 triage：halted=CRITICAL / triaged=可人工 review 重试）
    heartbeat = os.environ.get("PA_HEARTBEAT", "").lower() in ("1", "true", "yes")
    if not active and not heartbeat:
        log("[report] 全绿（无待 review 绿 PR / 无 failing / 无阻断）——不发邮件（SPEC §8 全绿不投递）")
        return report_path
    if getattr(args, "dry_run", False) or getattr(args, "no_notify", False):
        tag = "DRY-RUN" if getattr(args, "dry_run", False) else "--no-notify"
        state = "有活但 " + tag if active else "全绿（心跳模式）但 " + tag
        log(f"[report] {state}——不发邮件，报告已落盘")
        return report_path
    _smtp_notify(stamp, report_path, len(review), len(failing), n_blocked, active=active,
                 n_merged=len(merged), n_triaged=len(triaged), n_halted=len(halted))
    return report_path


def _append_daily_pointer(date_disp: str, stamp: str, n_review: int, n_failing: int,
                          n_blocked: int = 0, *, n_merged: int = 0, n_triaged: int = 0,
                          n_halted: int = 0) -> None:
    """日报加一行指针指向本报告（§8）；日报不存在则极简创建（仅指针，不侵入既有日报）。

    single-flight-auto-merge task 5.2：指针含 auto-merge 闭环三态计数（已合 main / triage / halt）。
    """
    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    daily = DAILY_DIR / f"work-daily-{date_disp}.md"
    pointer = (f"- 项目推进报告 → [[项目推进报告_{stamp}]]（{n_merged} 已合main / "
               f"{n_review} 待 review / {n_failing} failing / {n_triaged} triage / "
               f"{n_halted} halt / {n_blocked} 阻断）")
    if daily.is_file():
        txt = daily.read_text(encoding="utf-8")
        if f"项目推进报告_{stamp}" not in txt:        # 幂等：同日重出报告不重复加指针
            daily.write_text(txt.rstrip() + "\n" + pointer + "\n", encoding="utf-8")
    else:
        daily.write_text(f"# 工作日报 {date_disp}\n\n{pointer}\n", encoding="utf-8")


def _smtp_notify(stamp: str, report_path: Path, n_review: int, n_failing: int,
                 n_blocked: int = 0, *, active: bool = True, n_merged: int = 0,
                 n_triaged: int = 0, n_halted: int = 0) -> None:
    """发简讯（§8/§10）：标题=N 待 review / M failing / K 阻断；报告为正文+附件。失败退化为告警，不阻塞流水线。

    active=False 时为「全绿心跳」（PA_HEARTBEAT 触发的 cron 模式）——无头服务器上
    邮件断了即流水线挂了，故全绿也发一封状态邮件做心跳。

    single-flight-auto-merge task 5.2：subject 含 auto-merge 闭环三态（已合 main / 需 triage / halted）——
    已合是完成态（信息性），triage/halted 是行动项（运维须 triage/halt 介入）。
    """
    suffix = "" if active else "（全绿心跳）"
    subject = (f"项目推进 {stamp}｜已合{n_merged} 待review{n_review} failing{n_failing} "
               f"需triage{n_triaged} halted{n_halted} 阻断{n_blocked}{suffix}")
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
    ap.add_argument("--project", action="append", default=None, metavar="NAME",
                    help="只跑指定项目（可重复 / 逗号分隔多仓）；canary 隔离用，如 --project cc-web-control")
    ap.add_argument("--state-dir", default=None, metavar="PATH",
                    help="覆盖 state 落盘根（默认 .project-auto/state）；canary/演练用，与真实 cron state 物理隔离")
    ap.add_argument("--skip-critic", action="store_true",
                    help="跳过 critic 质量闸，manifest 全 PRD 直 pass（canary/演练用——canary 载体正交于项目 goal 会被 critic drop）")
    args = ap.parse_args()
    args.project = _normalize_projects(args.project)          # append+逗号 归一为 list（None=不过滤）
    _apply_state_dir(args.state_dir)                          # 须在 STATE_DIR.mkdir / acquire_run_lock 之前：重绑 STATE_DIR+RUN_LOCK
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
    if args.project:                                           # canary 单仓隔离：只留命中的 profile（其余 stage 自动收窄）
        profiles = _filter_profiles(profiles, args.project)
        log(f"  [PROJECT] 仅跑：{args.project}")
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
            if getattr(args, "skip_critic", False):
                gate = [{"prd_path": e.get("path", ""), "project": e.get("project", ""),
                         "verdict": "pass", "summary": "--skip-critic 直 pass（canary/演练）",
                         "round": 1, "revised": False, "issues": []} for e in manifest.get("prds", [])]
                (STATE_DIR / f"prd_gate_{stamp}.json").write_text(
                    json.dumps(gate, ensure_ascii=False, indent=2), encoding="utf-8")
                log(f"[critic] ⏭ skip（--skip-critic）：{len(gate)} 条 PRD 直 pass（canary/演练，绕质量闸）")
            else:
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
