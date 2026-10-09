#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
codex_critic.py — critic 第二评审臂（异源 harness：codex exec → roc LiteLLM → glm-5.3）。

For future Claude：派发单 20261009_0910（A1 生产实施）新增模块；设计规格 = Mac vault
产品分析/advance-pipeline/advance-pipeline_critic异源化评估_20261009.md §2.0-2.5 + §4.2-1。
与 claude 臂（run_persona → pa-prd-critic persona，glm-5.1）串行双闸：claude 主审照旧，
本臂复审，``synthesize()`` S1 偏序取严合成（drop > revise > pass，revisions_needed 并集）。

设计约束（对齐评估 §2.0/§2.4/§2.5）：
  * **不 import run_daily / run_persona**——与 3804 行主干零耦合，自包含可独测；
  * flag off（PA_CRITIC_ARM2 未设或不识别）时 run_daily 根本不加载本模块（调用点惰性 import），
    byte-identical baseline（先例 PA_PONYTAIL run_daily.py:427-435）；
  * codex 臂任何失败（二进制缺位/超时/非零退出/非法 JSON，重试 1 轮后仍败）→ verdict="skip"
    降级标记，合成回退 claude 单通道（fail-open 到基线而非 fail-closed 停摆，评估 §2.4）；
  * gate entry 只**附加** channels/dual_channel 字段（向后兼容，CriticContract 只校验必填不拒
    新增——stage_contracts.py CriticContract，评估 §2.5）。

调用形态（2026-10-09 Mac 侧大脑亲测端到端验证，codex 0.142.3 → LiteLLM /v1/responses → glm-5.3）::

    codex exec --skip-git-repo-check -s read-only \\
        -c model_provider=litellm184 -c model=glm-5.3 "<prompt>"

~/.codex/config.toml 追加 [model_providers.litellm184]（base_url=http://127.0.0.1:4000/v1、
wire_api="responses"、env_key="LITELLM_API_KEY"）。注意 codex 0.142.3 已移除 wire_api="chat"。

纯 stdlib。零凭证入档（key 经 env LITELLM_API_KEY 注入，来源 config/.env.codex，
run_cron.sh source；占位符 → 认证失败 → 本臂降级，不影响 claude 臂）。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

# ── flag：PA_CRITIC_ARM2=codex 开第二臂（unset/其余值 = off）。run_daily 侧另有同判定的
#    直读 env 分支（PA_PONYTAIL 先例），本函数供测试与运维自检用。────────────────────
ARM2_ENV = "PA_CRITIC_ARM2"
ARM2_CODEX = "codex"

# ── codex 调用参数 ──
CODEX_BIN_ENV = "PA_CODEX_BIN"           # codex 二进制覆盖（缺省 shutil.which 兜底）
CODEX_TIMEOUT_ENV = "PA_CODEX_TIMEOUT"   # 单次 wall-clock 上限秒（缺省 420 > claude 臂 300，codex exec 含启动开销）
CODEX_TIMEOUT_DEFAULT = 420
CODEX_PROVIDER = "litellm184"
CODEX_MODEL = "glm-5.3"

# S1 偏序：drop(最严) > revise > pass(最宽)（评估 §2.3）
_SEVERITY = {"pass": 0, "revise": 1, "drop": 2}


def arm2_mode(env: dict | None = None) -> str:
    """读第二臂开关：''=off；'codex'=codex 臂（strip + 小写，PA_PONYTAIL 真值判定先例）。"""
    e = os.environ if env is None else env
    return e.get(ARM2_ENV, "").strip().lower()


def _resolve_codex_bin() -> str | None:
    env = os.environ.get(CODEX_BIN_ENV)
    if env and Path(env).is_file():
        return env
    return shutil.which("codex")


def codex_prompt(prd_path: str, source_path: str, prof: dict) -> str:
    """渲染 codex 臂评审 prompt：内联 pa-prd-critic persona 四 check 契约（codex 不吃
    .claude/agents 体系，prompt 即 persona，评估 §2.1）+ gate JSON schema + 只输出一行 JSON。
    路径转绝对——不依赖调用方 cwd（生产 manifest 传相对路径、验证驱动传 /tmp 路径均可）。"""
    prd_abs = Path(prd_path).resolve()
    src_abs = Path(source_path).resolve() if source_path else prd_abs
    ms = prof.get("match_surface", {})
    return f"""你是「项目推进流水线」控制面的 PRD 质量闸：独立对抗评审，默认怀疑，专司反驳「有据 + 可执行」，不判价值。
待过闸 PRD：{prd_abs}
信息源原文：{src_abs}
项目 profile：name={prof.get('name')} goal="{prof.get('goal', '')}" match_surface.one_liner="{ms.get('one_liner', '')}"

先完整阅读上面两个文件，再逐条验四个 check：
- traceability（有据）：PRD 每条事实断言能否追溯到信息源？防编造/跑题/过度演绎。
- verifiable（可执行）：验收标准是否具体到能变成测试？「优化体验」「提升性能」这类不可验证 = 不过。
- fit（贴合）：PRD 是否贴合该项目 match_surface/goal？硬扯的不贴合 = 不过。
- scope（单 PR 可完成）：scope 是否一个 PR 做得完？过载 = 不过。

verdict 判定：pass=四项全过（或仅极轻微不影响可执行）；drop=任一 major 不过（编造断言/验收不可验证/严重跑题/scope 过载）；revise=仅 1 项轻微不过（给一次修订机会，revisions_needed 写清改什么）。
硬约束：默认怀疑，宁可 drop 不放行垃圾；不评「值不值得做」；revise 是例外不是常态。

只输出一行 JSON，无任何其他文字（多一个字都算失败）：
{{"prd_path":"{prd_abs}","project":"{prof.get('name', '')}","verdict":"pass|drop|revise","issues":[{{"check":"traceability|verifiable|fit|scope","severity":"major|minor","reason":"<具体哪条、为什么不过>"}}],"revisions_needed":["<仅 revise 时填：给 pa-prd 的具体修改指令>"],"summary":"<一句话结论>"}}"""


def _extract_braced(line: str) -> dict | None:
    """单行容错抽取：首个 '{' 到末个 '}' 的片段再解析（镜像 run_persona _extract_first_json
    思路，容忍行内散文前后缀）。解析失败或非 object 返 None。"""
    start = line.find("{")
    if start < 0:
        return None
    end = line.rfind("}")
    if end <= start:
        return None
    try:
        obj = json.loads(line[start:end + 1])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def _parse_gate_line(stdout: str) -> dict | None:
    """codex exec stdout 两层解析：逐行**倒序**找含 '{' 的行——先严格 ``json.loads`` 整行，
    失败再 ``_extract_braced`` 容错抽取（兼容行内散文前后缀）。倒序免疫头部 banner/尾部
    token 统计噪音。两层均失败返 None（调用方走重试/降级）。"""
    for line in reversed((stdout or "").splitlines()):
        line = line.strip()
        if "{" not in line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            obj = _extract_braced(line)
            if obj is None:
                continue
        if isinstance(obj, dict) and obj.get("verdict") in _SEVERITY:
            return obj
    return None


def codex_review(path: str, source_path: str, prof: dict, *, log=print) -> dict:
    """跑 codex 第二评审臂（subprocess 调 codex exec，重试 1 轮）。

    成功 → 含合法 verdict 的 gate payload（补 prd_path/project 兜底字段）；
    失败（二进制缺位/超时/非零退出/非法 JSON，两轮均败）→ ``{"verdict": "skip",
    "channel_error": …}`` 降级标记（synthesize 回退 claude 单通道基线）。
    **绝不 raise**——第二臂失败不阻断生产日跑（评估 §2.4 fail-open 决策）。"""
    timeout_raw = os.environ.get(CODEX_TIMEOUT_ENV, "")
    try:
        timeout_s = int(timeout_raw) if timeout_raw.strip() else CODEX_TIMEOUT_DEFAULT
    except ValueError:
        timeout_s = CODEX_TIMEOUT_DEFAULT
    bin_path = _resolve_codex_bin()
    if not bin_path:
        return {"verdict": "skip",
                "channel_error": f"codex CLI 不在 PATH（可用 {CODEX_BIN_ENV} 指定）——臂未执行"}
    base_cmd = [bin_path, "exec", "--skip-git-repo-check", "-s", "read-only",
                "-c", f"model_provider={CODEX_PROVIDER}", "-c", f"model={CODEX_MODEL}"]
    prompt = codex_prompt(path, source_path, prof)
    last_err = "（未知）"
    for attempt in (1, 2):
        cur_prompt = prompt if attempt == 1 else prompt + "\n\n（重试：只输出那一行 JSON，无任何其他文字）"
        try:
            proc = subprocess.run(base_cmd + [cur_prompt], capture_output=True, text=True,
                                  timeout=timeout_s, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            last_err = f"wall-clock 超时（{timeout_s}s），已 kill"
            continue
        except FileNotFoundError:
            return {"verdict": "skip", "channel_error": f"codex 二进制不可执行：{bin_path}"}
        if proc.returncode != 0:
            tail = " / ".join((proc.stderr or proc.stdout or "").strip().splitlines()[-4:])
            last_err = f"codex 退出 {proc.returncode}: {tail[-400:]}"
            continue
        payload = _parse_gate_line(proc.stdout or "")
        if payload is not None:
            payload.setdefault("prd_path", str(Path(path).resolve()))
            payload.setdefault("project", prof.get("name", ""))
            return payload
        last_err = f"stdout 无合法 gate JSON（attempt {attempt}）"
    log(f"[critic-arm2] ⚠ codex 臂降级（skip）：{last_err}")
    return {"verdict": "skip", "channel_error": last_err}


def synthesize(e1: dict, e2: dict | None, *, prd_path: str = "", project: str = "") -> dict:
    """S1 偏序取严合成（评估 §2.3 推荐默认，机械保守无歧义）。

    - 两臂均出判（dual）：verdict=最严（drop>revise>pass）；revisions_needed 取并集
      （e1 先、保序去重）；issues 附加 codex 条目（reason 加 [codex] 前缀可溯源）；
      summary 附加双通道摘要（report 段 critic 理由表零改动即可见）。
    - e2=None（claude 已 drop 串行短路，评估 §2.1）：dual_channel="single"，verdict=e1。
    - e2.verdict="skip"（codex 臂降级，评估 §2.4）：dual_channel="degraded"，verdict=e1
      （回退现状基线），channel_error 记入 codex_error 字段。
    返回新 dict，**不变异** e1/e2（不可变纪律）。channels/dual_channel 为附加字段，
    gate JSON 一行 entry 原 schema 不变（评估 §2.5 契约兼容）。"""
    out = dict(e1)
    v1 = e1.get("verdict")
    out.setdefault("prd_path", prd_path)
    out.setdefault("project", project)
    if e2 is None:
        out["channels"] = {"claude": v1, "codex": "skipped"}
        out["dual_channel"] = "single"
        return out
    v2 = e2.get("verdict")
    out["channels"] = {"claude": v1, "codex": v2}
    if v2 not in _SEVERITY:
        out["dual_channel"] = "degraded"
        if e2.get("channel_error"):
            out["codex_error"] = e2["channel_error"]
        return out
    out["dual_channel"] = "dual"
    out["verdict"] = v1 if _SEVERITY.get(v1, 2) >= _SEVERITY[v2] else v2
    r1 = list(e1.get("revisions_needed") or [])
    r2 = list(e2.get("revisions_needed") or [])
    if r2:
        out["revisions_needed"] = r1 + [x for x in r2 if x not in r1]
    issues2 = e2.get("issues") or []
    if issues2:
        tagged = [dict(i, reason=f"[codex] {i.get('reason', '')}") if isinstance(i, dict) else i
                  for i in issues2]
        out["issues"] = list(e1.get("issues") or []) + tagged
    out["summary"] = f"{e1.get('summary', '')}｜dual: claude={v1} codex={v2} → {out['verdict']}"
    return out
