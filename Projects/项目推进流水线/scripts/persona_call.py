#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""persona_call.py — 零依赖 persona 子进程调用共享模块（in-loop-semantic-checkpoint §1）。

从 run_daily.run_persona 抽出的纯 stdlib 模块（+ stage_contracts），供 dev-agent
（目标面标准执行器，import 时不得连带加载 claude_agent_sdk——守 test_dev_agent_source.py）
在内循环调独立评判子进程（pa-progress）。零依赖 = stdlib only + stage_contracts。

不 import run_daily（重模块，且会执行其顶层逻辑）；_extract_first_json / _JSON_RETRY_SUFFIX
/ resolve_claude_bin 在本模块内复制，与 run_daily.run_persona 行为对齐（行为基线 = test_persona_call）。

核心 API：
    resolve_claude_bin() -> str                     — claude CLI 解析（PA_CLAUDE_BIN→which→nvm）
    run_persona_subproc(...) -> (payload, meta)     — 两层 JSON 解析 + 重试 cap + 契约 fail-open
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable

from stage_contracts import validate_stage, render_repair_hint   # 纯 stdlib 模块


# ─── claude CLI 解析（复制自 run_daily.resolve_claude_bin，自包含）──────
def _nvm_claude() -> Path | None:
    """从 nvm 已装的 node 版本里找 claude（取版本号最大的那个）；无则 None。"""
    base = Path.home() / ".nvm/versions/node"
    if not base.is_dir():
        return None
    cands = sorted(base.glob("v*/bin/claude"))
    return cands[-1] if cands else None


def resolve_claude_bin() -> str:
    """claude CLI 解析：PA_CLAUDE_BIN env → shutil.which → nvm 兜底 → sys.exit。"""
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


# ─── 两层 JSON 容错（复制自 run_daily，行为对齐）──────────────────────
_JSON_RETRY_SUFFIX = (
    "\n\n⚠ 上一次输出不是合法 JSON（含散文/解释/markdown）。本次【必须】只输出一个 JSON 对象——"
    "前后不得有任何文字、解释、markdown 代码围栏；直接以 `{` 开头、`}` 结尾。"
)


def _extract_first_json(s: str) -> str | None:
    """容错抽取：brace-matching 取第一个完整 {...} 对象。

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


# ─── 核心：调 persona 子进程 ─────────────────────────────────────────
def run_persona_subproc(
    claude_bin: str,
    agent_name: str,
    prompt: str,
    *,
    max_turns: int,
    timeout: float,
    stage: str | None = None,
    allowed_tools: list[str] | None = None,
    retry_cap: int = 2,
    log: Callable[[str], None] | None = None,
) -> tuple[dict, dict]:
    """调 `claude --agent <agent_name> -p <prompt> --output-format json`，两层解析返回 (payload, meta)。

    内层 result 容错：严格 json.loads 失败 → _extract_first_json 抽取（容忍散文前后缀）；
    仍失败按 retry_cap 重试（拼 _JSON_RETRY_SUFFIX 加强 JSON-only 契约）。
    stage 注册了 Contract 则 fail-open 校验：error→带诊断重试（attempt<retry_cap）/ 预算用尽
    降级返回现状 payload（不 raise）；warning→经 log 回调不改行为。
    allowed_tools 透传 --allowedTools（逗号分隔）。log 回调可选（dev-agent 传 append_run_line）。
    """
    base_cmd = [claude_bin, "--agent", agent_name, "--output-format", "json",
                "--max-turns", str(max_turns)]
    # add-per-agent-model-routing：per-persona env 查表 → --model（不设=零变更 baseline）
    # equals 形式 `--model=X`（review follow-up：跟随 SDK subprocess_cli.py 对 dash-leading
    #   value flag 的安全惯例，单 token 绑定消 parser 灰色地带）；空串 env 显式 warn（配错反馈）。
    _env_key = f"PA_PERSONA_MODEL_{agent_name.upper().replace('-', '_')}"
    _model = os.environ.get(_env_key)
    if _model == "" and log:
        log(f"[{agent_name}] ⚠ {_env_key} 设为空串（忽略→走 roc 默认 glm-5.2）")
    if _model:
        base_cmd += [f"--model={_model}"]
        if log:
            log(f"[{agent_name}] model route → {_model}（per-agent env）")
    if allowed_tools:
        base_cmd += ["--allowedTools", ",".join(allowed_tools)]
    cur_prompt = prompt
    last_err = "（未知）"
    for attempt in range(1, retry_cap + 1):
        cmd = base_cmd + ["-p", cur_prompt]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=timeout, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"[{agent_name}] wall-clock 超时（{timeout}s），已 kill")
        if proc.returncode != 0:
            raise RuntimeError(f"[{agent_name}] claude 退出 {proc.returncode}: {proc.stderr[-400:].strip()}")
        try:
            outer = json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"[{agent_name}] stdout 非合法 JSON 信封: {e}; 头={proc.stdout[:300]}")
        if outer.get("is_error"):
            raise RuntimeError(f"[{agent_name}] persona is_error: {str(outer.get('result',''))[:300]}")
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
            if stage:                     # 语义契约层（fail-open；error→重试/降级，warning→log 不改行为）
                issues = validate_stage(stage, payload)
                err_issues = [i for i in issues if i.severity == "error"]
                if err_issues:
                    last_err = "语义契约违反: " + "; ".join(f"{i.field}({i.diagnosis})" for i in err_issues)
                    if attempt < retry_cap:   # 还有重试预算：带诊断重试一轮
                        if log:
                            log(f"[{agent_name}] {last_err} → 带诊断重试（attempt {attempt}/{retry_cap}）")
                        cur_prompt = prompt + render_repair_hint(
                            err_issues, json.dumps(payload, ensure_ascii=False)[:300], attempt=attempt)
                        continue
                    if log:
                        log(f"[{agent_name}] {last_err}（重试预算用尽，fail-open 降级返回现状 payload）")
                    return payload, meta
                warn_issues = [i for i in issues if i.severity == "warning"]
                if warn_issues and log:
                    log(f"[{agent_name}] 契约 warning（不改行为）: " + ", ".join(i.field for i in warn_issues))
            return payload, meta
        # 本轮非 JSON：加强 JSON-only 指令重试一轮
        if log:
            log(f"[{agent_name}] persona result 非 JSON（attempt {attempt}/{retry_cap}，容错抽取仍失败）→ 重试并加强 JSON-only 契约")
        cur_prompt = prompt + _JSON_RETRY_SUFFIX
    raise RuntimeError(f"[{agent_name}] persona result {retry_cap} 轮均非合法 JSON: {last_err}")
