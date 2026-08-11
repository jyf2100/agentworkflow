#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""spike_graph_radar.py — Phase 0 共存 spike（langgraph-workflow-upgrade 任务 1.3/1.4）。

验证 design Phase 0 判据（design.md L156-158）：
  ① cron 极简 PATH import：langgraph + claude_agent_sdk + sdk_compat_patch + graph_pa* 不报错（R1）
  ② SDK 0.2.128 + sdk_compat_patch 共存：apply() 命中 + langgraph 同进程不互踩（R2）
  ③ radar prompt byte-identical：graph node_radar._radar_build_prompt vs run_daily.radar_prompt
     相同输入字节级一致（编排器迁移零漂移；LLM 输出非确定，byte-identical 止于输入侧，R7）
  ④ 真实 pa-radar persona 冒烟：1-node graph 真调 claude --agent pa-radar，验证整链路通
     （subprocess → 两层 JSON 解析 → 契约校验 → commit_node → obs 提取），产物 schema 等价
  ⑤ 隔离 state smoke：临时 state_dir + 临时 log + unset PA_HEARTBEAT（守 pa-test-no-dirty-data，
     不碰真实 cron.log/SMTP/报告/dispatch）

byte-identical 边界说明：LLM 输出非确定，两次真实调用不可能 byte-identical。故 Phase 0 spike 的
「radar 产物 byte-identical」实指 **输入侧 prompt byte-identical**（编排器行为，可证）+ 产物 schema
等价（结构契约）；mock-run_persona 层的完整 byte-identical 由 test_graph_radar.py 覆盖。

手动跑：python scripts/spike_graph_radar.py
证据产物：<state_dir>/spike_evidence.json
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

# ⑤ 隔离前置：unset PA_HEARTBEAT（守 pa-test-no-dirty-data，spike 不触发 SMTP/报告/heartbeat）
os.environ.pop("PA_HEARTBEAT", None)

PA_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PA_ROOT))
# VAULT_ROOT 不自行推导（易 off-by-one），直接用 run_daily.VAULT_ROOT（import 后取）

STAMP = date.today().strftime("%Y%m%d")
EVIDENCE: dict = {"stamp": STAMP, "checks": {}, "byte_identical_note":
                  "LLM 输出非确定；byte-identical 止于输入侧 prompt + mock 层（test_graph_radar）"}


def _record(name: str, ok: bool, detail: dict | str) -> None:
    EVIDENCE["checks"][name] = {"ok": ok, "detail": detail}
    mark = "✓" if ok else "✗"
    d = detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False)
    print(f"{mark} [{name}] {d[:240]}", flush=True)


# ── ① 极简 PATH import（R1）────────────────────────────────────────────
def check_minimal_path_import() -> bool:
    minimal = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    code = ("import langgraph, claude_agent_sdk, sdk_compat_patch, "
            "graph_pa_contracts, graph_pa_state, graph_pa_nodes; "
            "from importlib.metadata import version; "
            "print(version('langgraph'), version('claude-agent-sdk'))")
    env = {**os.environ, "PATH": minimal, "PYTHONPATH": str(PA_ROOT)}  # scripts 扁平模块需 PYTHONPATH
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)
    ok = r.returncode == 0
    _record("minimal_path_import", ok,
            r.stdout.strip() if ok else f"rc={r.returncode} stderr={r.stderr.strip()[:200]}")
    return ok


# ── ② SDK patch 共存（R2）──────────────────────────────────────────────
def check_sdk_patch_coexist() -> bool:
    try:
        import langgraph                       # noqa: F401  与 patched SDK 同进程共存
        import sdk_compat_patch as P
        import claude_agent_sdk as S
        patched = P.apply()                     # 对 Query.wait_for_result_and_end_input 施 #1106 ast 变异
        import inspect
        src = inspect.getsource(patched)                        # 正验法：patched 源码含 can_use_tool（同 test_sdk_compat_patch）
        hit = P._APPLIED and "can_use_tool" in src
        sdk_ver = getattr(S, "__version__", "?")
        _record("sdk_patch_coexist", bool(hit),
                {"sdk_applied": P._APPLIED, "patch_hit_can_use_tool": bool(hit),
                 "claude_agent_sdk": sdk_ver, "langgraph_import_ok": True})
        return bool(hit)
    except Exception as e:                      # noqa: BLE001
        _record("sdk_patch_coexist", False, f"异常: {type(e).__name__}: {e}")
        return False


# ── ③ prompt byte-identical（编排器迁移零漂移，R7）─────────────────────
def check_prompt_byte_identical(prof: dict, flat: list[Path], dedup: list) -> bool:
    import run_daily
    import graph_pa_nodes as GN
    proj = prof["name"]
    # graph 路径：node_radar 的 prompt 工厂
    state = {"_project": proj, "_today_new": flat, "_profiles": {proj: prof}, "_dedup": dedup}
    graph_prompt = GN._radar_build_prompt(state)
    # stage 路径：run_daily.stage_radar L849 同形态调用
    stage_prompt = run_daily.radar_prompt(proj, flat, prof, dedup)
    ok = graph_prompt == stage_prompt
    _record("prompt_byte_identical", ok,
            {"equal": ok, "len": len(graph_prompt),
             "sha256_graph": __import__("hashlib").sha256(graph_prompt.encode()).hexdigest()[:12],
             "sha256_stage": __import__("hashlib").sha256(stage_prompt.encode()).hexdigest()[:12]})
    return ok


# ── ④ 真实 pa-radar persona 冒烟（整链路）──────────────────────────────
def check_real_persona_smoke(prof: dict, flat: list[Path], dedup: list) -> bool:
    import graph_pa_nodes as GN
    proj = prof["name"]
    state = {"run_id": f"spike-{STAMP}", "thread_id": f"spike_{STAMP}", "stamp": STAMP,
             "stage": "radar", "config": {}, "_project": proj,
             "_today_new": flat, "_profiles": {proj: prof}, "_dedup": dedup}
    prompt = GN._radar_build_prompt(state)
    ni = {"run_id": state["run_id"], "thread_id": state["thread_id"], "stamp": STAMP,
          "stage": "radar", "config": {}, "_project": proj}
    try:
        out, payload = GN.node_radar.invoke(ni, prompt)        # 真实 subprocess → claude --agent pa-radar
    except Exception as e:                                     # noqa: BLE001
        _record("real_persona_smoke", False, f"调用异常: {type(e).__name__}: {str(e)[:200]}")
        return False
    # 产物 schema 等价（与 stage_radar 输出结构对齐：candidates list + stats）
    cands = payload.get("candidates", []) if isinstance(payload, dict) else None
    schema_ok = (isinstance(payload, dict) and isinstance(cands, list)
                 and out.get("status") == "ok" and isinstance(out.get("obs"), dict))
    _record("real_persona_smoke", schema_ok, {
        "status": out.get("status"), "obs_keys": sorted((out.get("obs") or {}).keys()),
        "idempotency_key": out.get("idempotency_key"),
        "n_candidates": len(cands) if isinstance(cands, list) else None,
        "stats_keys": sorted((payload.get("stats") or {}).keys()) if isinstance(payload, dict) else None,
        "verdict_absent": "verdict" not in out})               # radar 不产 verdict（expose_verdict=False）
    return schema_ok


def main() -> int:
    state_dir = Path(tempfile.mkdtemp(prefix=f"spike_graph_{STAMP}_"))
    EVIDENCE["state_dir"] = str(state_dir)
    print(f"=== Phase 0 共存 spike（隔离 state_dir={state_dir}）===\n", flush=True)

    results = [check_minimal_path_import(), check_sdk_patch_coexist()]

    # 加载真实 profile + 构造隔离的今日新文件（VAULT_ROOT 下临时目录，radar_prompt 要 relative_to）
    import run_daily
    prof = run_daily.load_profiles().get("ashare-llm-analyst")
    if not prof:
        _record("prompt_byte_identical", False, "ashare-llm-analyst profile 未找到")
        _record("real_persona_smoke", False, "profile 缺失，跳过")
    else:
        spike_dir = run_daily.VAULT_ROOT / ".spike_tmp"
        spike_dir.mkdir(exist_ok=True)
        note = spike_dir / f"{STAMP}_akshare_pipeline.md"
        note.write_text(
            "# akshare 量化回测管道优化\n\n用 baostock 拉取 A 股日线数据，结合 akshare 实时行情接口，"
            "构建分层回测框架。技术指标层新增 RPS 相对强度排名，机器学习选股模块用随机森林做特征工程。"
            "财报分析侧接入东方财富数据，LLM（Qwen）做智能研报生成与数据管道清洗。\n", encoding="utf-8")
        flat, dedup = [note], []
        results.append(check_prompt_byte_identical(prof, flat, dedup))
        results.append(check_real_persona_smoke(prof, flat, dedup))
        try:
            shutil.rmtree(spike_dir)                            # 清理临时文件（隔离）
        except OSError:
            pass

    passed = sum(results)
    EVIDENCE["summary"] = f"{passed}/{len(results)} checks passed"
    (state_dir / "spike_evidence.json").write_text(
        json.dumps(EVIDENCE, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n=== {EVIDENCE['summary']} === 证据: {state_dir}/spike_evidence.json", flush=True)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
