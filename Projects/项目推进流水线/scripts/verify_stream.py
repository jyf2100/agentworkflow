#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_stream.py — agent-verify-injection 2.2：stream 解析 + 三机械锚 + verify-report 组装。

与既有 ``verify_anchors``（harden-pa-verify-determinism 的**行级** failing anchor 解析）
同词不同物：本模块的「三锚」是 design 锚①②③——transcript 退出码（命令同一性）/ git 工作区
净 / 注入件逐文件 digest。纯函数 + git subprocess 面（锚②），零 LLM；机械组装判定零语义。

stream 契约（2.0 冒烟实证，fixtures/verify_stream_*.slim.jsonl，2026-10-27）：
- ``assistant`` 事件 content 块 ``tool_use``(name=Bash) 带 ``{id, input.command}``；
- ``user`` 事件 content 块 ``tool_result`` 带 ``{tool_use_id, content, is_error}``——
  **无结构化 exit code 字段**：``is_error=false`` ⇔ exit 0（content=纯 stdout）；
  ``is_error=true`` ⇔ content 首行 ``Exit code N``（抽不到 → 保守 1）；
- ``result`` 事件带会话元（session_id / num_turns / duration_api_ms / total_cost_usd）。

锚①同一性谓词（D5.1 勿与 dev 信任谓词合一）：经 ``test_cmd_primitives`` **原语层**（拆段 /
末端段 / 危险结构检测——与 dev-agent is_clean_test_cmd v0.1.14 同一份语义），判定
「执行的命令 ≡ declared cmd」：链全净且末端段 strip 后全等即采信（链 exit code = 末端段
语义——``cd wt && npm ci && npm test`` 合法匹配 ``npm test``，勿错杀）；管道/重定向段或
非全等变体 → 该 declared **unresolved**（保守不可判读）。dev 侧白名单/形态上限**不装进**
本匹配面。

verdict 三态：``green``（全 declared 匹配且 exit 0 + 锚②③净）/ ``red``（全匹配但有非零）/
``invalid``（任一 unresolved 或锚 violation——不可判读，编排层 triage 不进 revise 回环；
会话墙钟超时由编排层 catch VerifySessionTimeout 后同归 invalid，T4）。
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import test_cmd_primitives as P   # D5.1 共享原语层（一份语义两消费点）
from acceptance_tests import verify_file   # 锚③逐文件 sha256 复核（同 digest 值域）

_EXIT_CODE_LINE = re.compile(r"^Exit code (\d+)")
# 环境奇偶（T3）：Bash 命令文本里的 env 装配机械提取（VAR=val 前缀赋值 / export VAR=val）
_ENV_ASSIGN = re.compile(r"(?:^|[\s;])export\s+([A-Z_][A-Z0-9_]*)=(\S+)|(?:^|[\s;])([A-Z_][A-Z0-9_]*)=(\S+)")

VERDICT_GREEN = "green"
VERDICT_RED = "red"
VERDICT_INVALID = "invalid"


@dataclass
class BashExecution:
    """一次被采信为「可判读」的 Bash 执行（tool_use↔tool_result 按 id 配对）。"""
    command: str
    tool_use_id: str
    is_error: bool
    exit_code: int
    output: str      # stdout（is_error 时已剥「Exit code N」首行；行锚解析输入不断供 B1）


def _exit_code(content: str, is_error: bool) -> int:
    """exit code 提取（2.0 冒烟契约）：is_error=false → 0；true → 首行 ``Exit code N``，无 → 保守 1。"""
    if not is_error:
        return 0
    m = _EXIT_CODE_LINE.match((content or "").lstrip())
    return int(m.group(1)) if m else 1


def _stdout(content: str, is_error: bool) -> str:
    """输出文本：is_error 时剥首行 Exit code N（保留其余供行锚解析），否则全量。"""
    if not is_error:
        return content or ""
    m = _EXIT_CODE_LINE.match((content or "").lstrip())
    if not m:
        return content or ""
    return (content or "").lstrip()[m.end():].lstrip("\n")


def parse_stream(raw: str) -> tuple[list[BashExecution], dict]:
    """stream-json 文本 → (Bash 执行列表[出现序], result 会话元 dict)。

    非 Bash tool_use / 无对应 tool_result 的孤儿块 / 坏行（跳过）均不进列表——解析面
    只服务锚①，宽松容忍 stream 噪声（system/init/hook/thinking 事件天然被 type 过滤）。
    """
    uses: dict[str, str] = {}
    results: dict[str, tuple[str, bool]] = {}
    meta: dict = {}
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        etype = e.get("type")
        if etype == "assistant":
            for c in (e.get("message") or {}).get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_use" and c.get("name") == "Bash":
                    uses[c.get("id")] = (c.get("input") or {}).get("command", "")
        elif etype == "user":
            for c in (e.get("message") or {}).get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_result":
                    results[c.get("tool_use_id")] = (c.get("content") or "", c.get("is_error") is True)
        elif etype == "result":
            meta = {k: e[k] for k in
                    ("session_id", "num_turns", "duration_api_ms", "total_cost_usd") if k in e}
    execs = []
    for tid, cmd in uses.items():
        if tid in results:
            content, is_err = results[tid]
            execs.append(BashExecution(command=cmd, tool_use_id=tid, is_error=is_err,
                                       exit_code=_exit_code(content, is_err),
                                       output=_stdout(content, is_err)))
    return execs, meta


# ─── 锚①：declared cmd 同一性匹配（D5.1 谓词独立实现） ───────────────
def match_declared(executions: list[BashExecution],
                   declared_cmds: list[str]) -> dict[str, BashExecution | None]:
    """每个 declared cmd → 匹配的执行（None = unresolved）。

    匹配 = 执行命令链全净（无 ``|><&```/``$(``）且**末端段** strip 后与 declared strip 全等
    （链 exit code = 末端段语义）。非全等变体 / 管道变体 / 未执行 → None（保守不可判读）。
    同一 declared 取**首次**匹配（重试同命令取最后一次语义留编排层——v1 首次即可判）。
    """
    out: dict[str, BashExecution | None] = {d: None for d in declared_cmds}
    for d in declared_cmds:
        want = (d or "").strip()
        if not want:
            continue
        for ex in executions:
            if P.chain_all_clean(ex.command) and P.tail_segment(ex.command) == want:
                out[d] = ex
                break
    return out


# ─── 锚②：workspace 干净（tracked 零变更 + 非 ignored untracked ⊆ 注入集） ──
def check_workspace_clean(cwd: Path, injected_rel: set[str]) -> tuple[bool, str]:
    """git status --porcelain 判定：无 tracked 变更（M/A/D/R）；?? untracked ⊆ 注入集。

    porcelain 默认不列 ignored（!! 只在 --ignored 下出现）——天然满足「非 ignored」限定。
    git 不可用 / 非仓 → fail-closed 拒（锚②不可判读 = invalid 语义）。
    """
    try:
        proc = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=str(cwd),
                              capture_output=True, text=True, timeout=30)
    except (subprocess.TimeoutExpired, OSError) as e:
        return False, f"git status 执行失败（fail-closed）: {e}"
    if proc.returncode != 0:
        return False, f"git status 非零退出: {proc.stderr.strip()[:200]}"
    injected = {str(p).strip() for p in injected_rel}
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        code, path = line[:2], line[3:].strip().strip('"')
        if code == "??":
            if path not in injected:
                return False, f"非注入 untracked 文件: {path}"
        else:
            return False, f"tracked 变更（{code.strip()}）: {path}"
    return True, "ok"


# ─── 锚③：注入件逐文件 digest 复核 ───────────────────────────────────
def check_injected_digests(worktree: Path, expected: dict[str, str]) -> list[str]:
    """注入文件在 worktree 的逐文件 sha256 vs store 记录；返回违规清单（空 = 净）。

    违规形态：``missing:<rel>``（文件不在——被删/未注入）/ ``digest-mismatch:<rel>``（被改）。
    """
    bad: list[str] = []
    for rel, dg in sorted(expected.items()):
        p = worktree / rel
        if not p.is_file():
            bad.append(f"missing:{rel}")
        elif not verify_file(p.read_text(encoding="utf-8"), dg):
            bad.append(f"digest-mismatch:{rel}")
    return bad


# ─── 环境奇偶（T3：观测面，记录不阻断） ───────────────────────────────
def observed_env_exports(executions: list[BashExecution]) -> dict[str, list[str]]:
    """transcript Bash 命令机械提取 env 装配（VAR=val 前缀 / export VAR=val）→ var → 值集。

    v1 只记录（与 declared env spec 的比对留报告消费方人工/后续阻断），不进判定。
    """
    out: dict[str, list[str]] = {}
    for ex in executions:
        for m in _ENV_ASSIGN.finditer(ex.command):
            var, val = (m.group(1) or m.group(3)), (m.group(2) or m.group(4))
            if var and val:
                vals = out.setdefault(var, [])
                if val not in vals:
                    vals.append(val)
    return out


# ─── verdict 三态 + verify-report 组装 ────────────────────────────────
def derive_verdict(matched: dict[str, BashExecution | None],
                   anchor_workspace_ok: bool,
                   anchor_digest_violations: list[str]) -> str:
    """green|red|invalid：unresolved 或锚 violation → invalid（不可判读）；否则按 exit code。"""
    if any(ex is None for ex in matched.values()) or not anchor_workspace_ok or anchor_digest_violations:
        return VERDICT_INVALID
    return VERDICT_RED if any(ex.exit_code != 0 for ex in matched.values()) else VERDICT_GREEN


def build_verify_report(*, run_id: str, project: str, prd_id: str, iteration_id: str,
                        harness: str, gate_cmd: str | None, gate_source: str,
                        matched: dict[str, BashExecution | None],
                        anchor_workspace_ok: bool, anchor_digest_violations: list[str],
                        stream_path: str, session_meta: dict,
                        env_spec: str, env_observed: dict[str, list[str]],
                        anchor_workspace_detail: str = "") -> dict:
    """完整测试报告（⑤：两轨统一输出契约，机械组装零 LLM）。

    落 ``state/runs/<stamp>.verify-report.json``（hub 容器形态必须命中挂载卷——D13）；
    dispatch record 持路径。命令来源与清单 / 逐命令 exit code / 锚检查 / 判定与证据指针 /
    会话元 / 环境奇偶节（declared spec vs transcript 观测，T3 v1 记录不阻断）全字段。
    """
    results = [{"declared": d, "unresolved": ex is None,
                "matched_command": ex.command if ex else None,
                "exit_code": ex.exit_code if ex else None}
               for d, ex in matched.items()]
    verdict = derive_verdict(matched, anchor_workspace_ok, anchor_digest_violations)
    reason = {
        VERDICT_GREEN: "全 declared 命令匹配且 exit 0；锚②③净",
        VERDICT_RED: "全 declared 匹配但存在非零退出",
        VERDICT_INVALID: ("unresolved（declared 未被执行/变体不可判读）" if any(e is None for e in matched.values())
                          else "锚 violation（工作区不净或注入件 digest 不符）"),
    }[verdict]
    return {
        "schema_version": "pa.verify-report.v1",
        "run": {"run_id": run_id, "project": project, "prd_id": prd_id,
                "iteration_id": iteration_id, "harness": harness},
        "commands": {"source": gate_source, "gate_cmd": gate_cmd,
                     "declared": list(matched.keys()), "results": results},
        "anchors": {"workspace_clean": {"ok": anchor_workspace_ok,
                                        "detail": anchor_workspace_detail or ("ok" if anchor_workspace_ok else "dirty")},
                    "injected_digests": {"ok": not anchor_digest_violations,
                                         "violations": anchor_digest_violations}},
        "verdict": verdict,
        "reason": reason,
        "evidence": {"stream_path": stream_path},
        "session": session_meta,
        "env_parity": {"declared": env_spec, "observed_exports": env_observed,
                       "note": "v1 记录不阻断（测试学二审 T3）"},
    }
