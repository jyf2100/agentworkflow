#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""graph_pa_aggregate.py — 主图 7 阶段 node（包装 stage_X + dispatch 聚合）。

langgraph-workflow-upgrade 批 1（task 3.9 + 5.2 前置）。主图 node 拆 2 类——

- **包装 node**（fetch/radar/prd/inject/critic/report）：MechanicalNode op 直调 ``run_daily.stage_X``，
  byte-identical 自动保证（复用门 / per-source try/except / skip_critic 全在 stage_X 内部）。
- **dispatch 聚合 node**：经 ``stage_dispatch`` 的 ``worker`` hook 注入 ``_dispatch_one_graph``（子图 invoke），
  零 drift 复用 stage_dispatch 外壳（复用门 / passed / skip / limit / locks / sort / 写文件），把命令式
  ``dispatch_one``（~485 行）替换为 ``build_dispatch_subgraph``（graph 化核心价值）。``_subgraph_result_to_record``
  映射子图 state → rec schema（21 字段，喂 dispatch_{stamp}.json + stage_report）。

critic 聚合 **循环 build_critic_subgraph**（task 5.7，替批 1 包装 stage_critic）——三边界（缺 path→drop /
漏吐 verdict→drop / revise 异常→drop）在聚合层 + 子图 ``_revise_fn`` 全镜像 stage_critic，prd_gate 内容
semantic-identical。critic 段全 graph 化（D6 完整）。

不变式（守 design D1/D7/R8 + plan 关键约束）：
- D1 claude runtime 零改动；编排器侧守同步不引 asyncio。
- D7 run_daily 不 import 本模块——``worker`` 是 callable 参数（调用方传），run_daily 源码不 import graph_pa_*；
  flag off = stage_dispatch(worker=None) 走 _run_one，legacy byte-identical。
- _args Namespace 经 state 透传（main 注入）：违 R8 不可序列化，但只 op 内读、不经 langgraph reducer 序列化
  （node 返回的 update 不含它）+ 崩溃恢复不重建（task 3.8 范围），风险可接受 + 注释标明。
- obs_log 子图不自动冒泡（langgraph reducer 只在子图内合并）：dispatch 逐 PRD 子图 obs 在 worker 内累积不回主图，
  主图 dispatch 汇总 obs 由聚合 op 自己吐；report 段读主图 obs_log 得各 stage 汇总行。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import graph_pa_nodes as GN
from graph_pa_nodes import make_mechanical_node


# ── 包装 node（MechanicalNode op 直调 stage_X，byte-identical）──────────────
# _args Namespace 经 state 透传（main 注入）：违 R8 不可序列化，但只 op 内读不经 langgraph reducer
# 序列化（node 返回 update 不含它）+ 崩溃恢复不重建（task 3.8 范围），风险可接受。


def _fetch_main_op(ni: dict, state: dict):
    """fetch 包装：直调 stage_fetch（磁盘中转——写 ``YYYYMMDD_<slug>.md``，radar 从盘 discover_today_new 重扫）。

    fetch 返回值丢弃（对齐 _run_pipeline L3390：``stage_fetch(...)`` 不接返回）。复用门 ``fetch_{stamp}.json``
    在 stage_fetch 内部。包装 node_fetch_*（单源 PersonaNode）只写 state 不落盘，故主图用 stage_fetch 整函数。
    """
    import run_daily
    run_daily.stage_fetch(state["_args"], state["_sources"], ni["stamp"])
    return ([], {}, {"stage": "fetch", "note": "disk-mediated"})


node_fetch_main = make_mechanical_node(name="fetch", stage="fetch", op=_fetch_main_op)


def _radar_main_op(ni: dict, state: dict):
    """radar 包装：直调 stage_radar → candidates payload。复用门 ``candidates_{stamp}.json`` 在 stage_radar 内部。"""
    import run_daily
    payload = run_daily.stage_radar(state["_args"], state["_sources"], state["_profiles"], ni["stamp"])
    return ([], {"candidates": payload},
            {"stage": "radar", "n_candidates": len(payload.get("candidates", []))})


node_radar_main = make_mechanical_node(name="radar", stage="radar", op=_radar_main_op)


def _prd_main_op(ni: dict, state: dict):
    """prd 包装：直调 stage_prd → manifest。复用门 ``prd_manifest_{stamp}.json`` 在 stage_prd 内部。"""
    import run_daily
    manifest = run_daily.stage_prd(state["_args"], state.get("candidates") or {"candidates": []},
                                   state["_profiles"], ni["stamp"])
    return ([], {"prd_manifest": manifest},
            {"stage": "prd", "n_prds": len(manifest.get("prds", []))})


node_prd_main = make_mechanical_node(name="prd", stage="prd", op=_prd_main_op)


def _inject_main_op(ni: dict, state: dict):
    """inject 包装：直调 stage_inject → ``(manifest, actual_stamp)``。

    stamp 自增同步（inject bump stamp 写回 state["stamp"]，下游 critic/dispatch/report 用 actual 找文件，
    对齐 _run_pipeline L3396 ``manifest, stamp = stage_inject(...)``）。inject 条件加进图（build_main_graph
    ``config["inject_prd"]`` 非空才挂此 node），op 内不重复判。
    """
    import run_daily
    manifest, actual = run_daily.stage_inject(state["_args"], state["_profiles"], ni["stamp"])
    return ([], {"prd_manifest": manifest, "stamp": actual},
            {"stage": "inject", "inject_stamp": actual, "n_prds": len(manifest.get("prds", []))})


node_inject_main = make_mechanical_node(name="inject", stage="inject", op=_inject_main_op)


def _critic_main_op(ni: dict, state: dict):
    """critic 聚合：复用门 + skip_critic 全 pass + 循环 build_critic_subgraph（每 PRD invoke）。

    task 5.7（替批 1 包装 stage_critic）：critic 段全 graph 化（D6 完整）。镜像 stage_critic L915-965：
    skip_critic 全 pass（canary/演练，优先）→ 复用门 prd_gate_{stamp}.json（--force 跳）→ 循环单 PRD
    critic 子图（round1 + revise round2 回环 + 三边界全在子图/本 op）。shadow parity：prd_gate 内容与
    stage_critic semantic-identical（子图 entry = dict(payload) 同 _critic_one payload 来源 pa-prd-critic；
    round/revised 字段对齐 stage_critic L956-957/L973-974）。

    三边界（对齐 stage_critic）：① 缺 path→drop 预过滤（L934-937，子图前）；② 漏吐 verdict→drop 后处理
    （L940-943，子图 route 对 None 静默 done，最后 entry 缺 verdict 时降级）；③ revise 异常→drop 在子图
    _revise_fn（graph_pa_critic，保 round1 entry 不丢）。
    """
    import run_daily
    import graph_pa_critic as GC
    args = state["_args"]
    stamp = ni["stamp"]
    manifest = state.get("prd_manifest") or {"prds": []}
    gate_file = run_daily.STATE_DIR / f"prd_gate_{stamp}.json"

    if getattr(args, "skip_critic", False):
        gate = [{"prd_path": e.get("path", ""), "project": e.get("project", ""),
                 "verdict": "pass", "summary": "--skip-critic 直 pass（canary/演练）",
                 "round": 1, "revised": False, "issues": []} for e in manifest.get("prds", [])]
        gate_file.write_text(json.dumps(gate, ensure_ascii=False, indent=2), encoding="utf-8")
        run_daily.log(f"[critic] ⏭ skip（--skip-critic）：{len(gate)} 条 PRD 直 pass（canary/演练，绕质量闸）")
        return ([], {"critic_results": gate},
                {"stage": "critic", "n_gate": len(gate),
                 "n_pass": sum(1 for e in gate if e.get("verdict") == "pass")})

    # 复用门（镜像 stage_critic L916-919，仅 skip_critic=False 时；skip 优先保 canary/演练语义）
    if gate_file.is_file() and not getattr(args, "force", False):
        run_daily.log(f"[critic] 复用已有 {gate_file.name}（--force 重跑）")
        gate = json.loads(gate_file.read_text(encoding="utf-8"))
        return ([], {"critic_results": gate},
                {"stage": "critic", "n_gate": len(gate),
                 "n_pass": sum(1 for e in gate if e.get("verdict") == "pass")})

    # 循环 build_critic_subgraph（每 PRD invoke，替 stage_critic 命令式 for 循环 + revise 回环）
    profiles = state["_profiles"]
    entries: list = []
    for prd in manifest.get("prds", []):
        proj = prd.get("project")
        prof = profiles.get(proj, {})
        path = prd.get("path")
        src = prd.get("source_path", "")
        # 边界①缺 path → drop 预过滤（镜像 stage_critic L934-937）
        if not path:
            run_daily.log(f"[critic] ⚠ prd 缺 path（project={proj}）→ 降级 drop")
            entries.append({"prd_path": path, "project": proj, "verdict": "drop",
                            "summary": "prd manifest 缺 path，无法过闸"})
            continue
        sub_state = {
            "run_id": state.get("run_id", ""), "stamp": stamp, "config": {},
            "_prd_path": path, "_source_path": src, "_project": proj,
            "_prof": prof, "_profiles": profiles,
            "prd_round": 1, "entries": [],
        }
        result = GC.build_critic_subgraph().invoke(sub_state)
        sub_entries = result.get("entries", [])
        # 边界②漏吐 verdict → drop 后处理（镜像 stage_critic L940-943：critic 输出缺 verdict 字段降级）。
        # 子图 route_critic 对 _critic_verdict=None 静默 done（不进 revise），最后 entry 可能缺 verdict。
        if sub_entries:
            last = sub_entries[-1]
            if "verdict" not in last:
                run_daily.log(f"[critic] ⚠ critic 漏吐 verdict（{path}）→ 降级 drop")
                last.setdefault("prd_path", path)
                last.setdefault("project", proj)
                last["verdict"] = "drop"
                last.setdefault("summary", "critic 输出缺 verdict 字段，降级")
        entries.extend(sub_entries)

    gate_file.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    return ([], {"critic_results": entries},
            {"stage": "critic", "n_gate": len(entries),
             "n_pass": sum(1 for e in entries if e.get("verdict") == "pass")})


node_critic_main = make_mechanical_node(name="critic", stage="critic", op=_critic_main_op)


def _report_main_op(ni: dict, state: dict):
    """report 包装：stage_report（byte-identical md/日报/SMTP）+ _aggregate_obs 写 metrics（决策 M 路径 A）。

    metrics 是 graph 新增可观测产物（stage_report 不产），复用 ``GN._aggregate_obs`` + 固定名
    ``metrics_<stamp>.json``（STATE_DIR 同目录同 stamp 作用域，.gitignore 不入仓）。stage_report 复用门
    ``report_{stamp}.md`` 在内部。
    """
    import run_daily
    path = run_daily.stage_report(state["_args"], state["_profiles"], ni["stamp"])
    metrics = GN._aggregate_obs(state.get("obs_log", []),
                                run_id=state.get("run_id", ""), stamp=ni["stamp"])
    metrics_path = run_daily.STATE_DIR / f"metrics_{ni['stamp']}.json"
    metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    return ([], {"report": {"path": str(path)}},
            {"stage": "report", "report_path": str(path),
             "node_count": metrics.get("node_count", 0)})


node_report_main = make_mechanical_node(name="report", stage="report", op=_report_main_op)


# ── dispatch 聚合 node（stage_dispatch worker hook + 子图 invoke）─────────────

def _build_dispatch_shell(entry: dict, prof: dict, stamp: str) -> dict:
    """组装 dispatch 子图 shell 输入（13 字段 ``_REQUIRED_SHELL``，对齐 test_graph_dispatch_e2e._BASE +
    dispatch_one L2035-2044 的字段推导）。

    leaf 值字段（可序列化，graph_pa_recovery._REQUIRED_SHELL 校验）。coord 派生（``_coord``/``_sj``/...）
    由 ``_invoke_dispatch_subgraph`` 内 build_coordinator 重建（每 PRD 独立 coord，对齐 dispatch_one L2075）。
    run_id 占位 ""——coord 重建后由 ``_invoke_dispatch_subgraph`` 覆盖为 coord.run_id。
    """
    import run_daily
    proj = prof.get("name", "?")
    repo = prof.get("repo", "")
    slug = Path(entry.get("prd_path", "")).stem or "unknown"
    base = prof.get("default_branch", "main")
    owner_repo = run_daily.repo_owner_repo(repo) if repo else ""
    prd_abs = str(run_daily.VAULT_ROOT / entry.get("prd_path", ""))
    src_rel = entry.get("source_path") or ""
    src_abs = str(run_daily.VAULT_ROOT / src_rel) if src_rel else ""
    log_file = run_daily.STATE_DIR / "runs" / proj / f"{stamp}_{slug}.log"
    return {
        "run_id": "",                  # _invoke_dispatch_subgraph 内 coord.run_id 覆盖
        "stamp": stamp,
        "_project": proj,
        "_slug": slug,
        "_prof": prof,
        "_worktree_abs": repo,         # 主仓本地路径（worktree node 覆盖成 detached wt，对齐 L2152）
        "_owner_repo": owner_repo,
        "_base": base,
        "_prd_path": entry.get("prd_path"),
        "_prd_abs": prd_abs,
        "_src_abs": src_abs,
        "_dev_log_file": str(log_file) if log_file else None,
        "verify_round": 1,             # 每 PRD 重置（不可跨 PRD 累加，否则首次用满后二次直接 interrupted_pr）
    }


def _subgraph_result_to_record(result: dict, entry: dict, prof: dict) -> dict:
    """dispatch 子图 invoke 返回的 state → dispatch_one rec schema（21 字段，对齐 L2046-2056）。

    子图 state 字段（``_`` 前缀运行期）→ rec key（无前缀）。喂 ``dispatch_{stamp}.json`` + stage_report。
    缺失字段 → None/False（对齐 dispatch_one rec 初始默认值）。**路径依赖扩展字段**（仅特定 dispatch_one 路径
    rec.update 写，否则字段不存在）留 follow-up，**Phase 4 真 dev cutover（flag 默认 on + 非 skip-dev）前须补**：
    ① ``off_track``（legacy L2236 dev 成功路径，子图 state ``_dev_off_track`` 已声明未映射）
    ② ``publication_reconciliation``（legacy L2332，子图 state ``_publication_reconciliation`` 已声明未映射）
    ③ verify_anchors/verify_bundles（子图 verify 节点 state 字段待核对）。**canary 盲区**（code-review M1）：本期
    canary 用 ``--dispatch-skip-dev``（零 outward + 零 LLM），dev 循环整体跳过 → legacy 也不写 ①② → 双源同缺
    假性 match，per_stage shadow parity 报告驱动不了这俩字段补全；须 Phase 4 非 skip-dev canary 或单测守护。
    learning_memory 由 ``_dispatch_one_graph`` 的 _attach_learning_memory 后处理填（对齐 _run_one）。
    """
    rec = {
        "project": prof.get("name", "?"),
        "prd_path": entry.get("prd_path"),
        "slug": result.get("_slug") or Path(entry.get("prd_path", "")).stem or "unknown",
        "base": result.get("_base") or prof.get("default_branch", "main"),
        "status": result.get("_exit_status") or result.get("terminal") or "fail",
        "pr_url": result.get("_pr_url"),
        "branch": result.get("_branch"),
        "dev_killed": result.get("_dev_killed", False),
        "stalled": result.get("_dev_stalled", False),
        "run_log": result.get("_dev_run_log"),
        "dev_cost": result.get("_dev_cost"),
        "dev_turns": result.get("_dev_turns"),
        "verify": result.get("_verify_payload"),
        "skip_reason": result.get("_skip_reason"),
        "dev_test_cmd": result.get("_dev_test_cmd"),
        "verify_verdict": result.get("_verify_verdict"),
        "verify_round": result.get("verify_round"),
        "merge_commit": result.get("_merge_commit"),
        "reverted": result.get("_reverted", False),
        "triage_reason": result.get("_triage_reason"),
        "post_merge_verdict": result.get("_post_merge_verdict"),
    }
    # langgraph r-review I1 + canary 坐实修正：子图产 _blocked_check/_gate_status/_gate_reason 仅在
    #   blocked_external_state / 测试发布门路径（graph_pa_nodes 749/846/935）。对齐 dispatch_one 条件性
    #   rec.update（L2109/2219 等，仅 blocked/gate 路径写，否则字段不存在）：无条件写入致 skip/正常路径多
    #   3 个 None → shadow parity per_stage dispatch 漂移（canary shadow-run 坐实）。非 None 才写，
    #   report（run_daily.py:3164-3167）在 blocked/gate 路径仍能分桶（.get(...) or '?' 兼容字段缺失）。
    for _src, _dst in (("_blocked_check", "blocked_check"),
                       ("_gate_status", "gate_status"),
                       ("_gate_reason", "gate_reason")):
        _v = result.get(_src)
        if _v is not None:
            rec[_dst] = _v
    return rec


def _invoke_dispatch_subgraph(entry: dict, prof: dict, stamp: str, owner_repo: str, *, slot_handle) -> dict:
    """组装 shell + coord 派生 → build_dispatch_subgraph().invoke → _subgraph_result_to_record。

    镜像 dispatch_one L2063-2080 的 coord/shell 组装（build_coordinator 重建 run_id/IDs/journal/flags + 6
    coord 派生字段 ``_coord``/``_coord_flags``/``_sj``/``_iter``/``_prd``/``_journal_path``）+ L2029-2515 的
    执行（替换为子图 invoke）。slot_handle（serial_shadow on）注入 shell，子图 slot_acquire canary 消费
    （baseline no-op）。
    """
    import run_daily
    import reconcile
    import graph_pa_dispatch as GD
    shell = _build_dispatch_shell(entry, prof, stamp)
    prd_abs = shell["_prd_abs"]
    try:
        prd_content = Path(prd_abs).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        prd_content = None
    stable_slug = shell["_slug"]
    if prd_content:
        _fm, _ = run_daily._split_frontmatter(prd_content)
        stable_slug = _fm.get("slug") or shell["_slug"]
    coord = run_daily.build_coordinator(
        stamp=stamp, prd_path=entry.get("prd_path") or "", proj=shell["_project"],
        slug=shell["_slug"], state_dir=run_daily.STATE_DIR, profile=prof,
        stamp_fn=run_daily._now_iso, prd_content=prd_content,
        resolver=reconcile.default_resolver(prof.get("repo", ""), owner_repo),
        stable_slug=stable_slug)
    shell["run_id"] = coord.run_id
    shell["_coord"] = coord
    shell["_coord_flags"] = coord.flags
    shell["_sj"] = coord.journal
    shell["_iter"] = coord.iteration_id
    shell["_prd"] = coord.prd_id
    shell["_journal_path"] = coord.journal.path
    if slot_handle is not None:
        shell["_slot_handle"] = slot_handle   # serial_shadow on：子图 slot_acquire canary 消费（baseline no-op）
    result = GD.build_dispatch_subgraph().invoke(shell)
    return _subgraph_result_to_record(result, entry, prof)


def _dispatch_one_graph(entry: dict, prof: dict | None, stamp: str, args) -> dict:
    """单 PRD graph worker（签名对齐 ``_run_one``，作 stage_dispatch ``worker`` hook 注入）。

    镜像 _run_one（L2758-2796）的 lock/slot 框架 + _attach_learning_memory 后处理，把命令式 dispatch_one
    替换为 ``_invoke_dispatch_subgraph``（10 节点子图状态机）。serial_shadow on → ``SF.slot_scope`` 包子图
    invoke + slot_handle 注入（跨进程 flock，D9；子图 slot_acquire canary 激活消费，baseline no-op）；
    off → ``DISPATCH_LOCKS`` 包子图 invoke（同仓串行，子图 admission count_inflight_prs check-then-act TOCTOU
    守卫，R 风险 11；串行在 worker 层，子图 admission 看不到 threading.Lock）。
    """
    import contextlib
    import coordinator as _coord_mod
    import run_daily
    if not prof:
        return {"project": entry.get("project"), "prd_path": entry.get("prd_path"),
                "status": "skip", "skip_reason": "profile 不存在"}

    # langgraph r-review C2：per-PRD preflight（镜像 dispatch_one run_daily.py:2083-2087，守 R7 byte-identical）。
    #   resolve_flags(env) == coord.flags（flags 全局 env 态，coordinator 不按 profile 改 flag）。preflight fail →
    #   skip 记录（status/skip_reason 对齐 dispatch_one），在 slot/lock 获取前（不占并发资源）。不写 journal
    #   terminal（coord 此处未 build；非法组合不投递无 terminal 事件——shadow parity 比 dispatch_{stamp}.json
    #   records 终态非 journal，微差可接受）。C1 main 入口已挡全局违规，此处为 parity + 防御性逐 PRD 复核。
    _pf = _coord_mod.preflight(run_daily.resolve_flags(env=os.environ))
    if not _pf.is_ok:
        return {"project": prof.get("name", "?"), "prd_path": entry.get("prd_path"),
                "slug": Path(entry.get("prd_path", "")).stem or "unknown",
                "status": "skip",
                "skip_reason": "阻断-loop flag 组合非法: " + "; ".join(_pf.blocked.violations)}

    def _learn(rec: dict) -> None:
        run_daily._attach_learning_memory(rec, prof, entry, stamp,
                                          sdk_query_fn=getattr(args, "_learning_sdk_query_fn", None))

    repo = prof.get("repo", "")
    owner_repo = run_daily.repo_owner_repo(repo) if repo else ""

    # serial_shadow on → 跨进程 single-flight slot（对齐 _run_one L2779-2791）
    if owner_repo and run_daily.resolve_flags(env=os.environ).single_flight_serial_shadow:
        _run = run_daily.loop_ids.run_id(stamp)
        _prd = run_daily.loop_ids.prd_id(entry.get("prd_path", ""), None)
        _iter = run_daily.loop_ids.iteration_id(_run, _prd, 0)
        _scope = run_daily.SF.slot_scope(run_daily.STATE_DIR, owner_repo, run_id=_run, prd_id=_prd,
                                         iteration_id=_iter, now_fn=run_daily._slot_now,
                                         stamp_fn=run_daily._now_iso)
        with _scope as _slot:
            if not _slot.acquired:                 # inflight/unknown/flock_busy → 不投递（让位/fail-safe）
                rec = run_daily._slot_blocked_record(entry, owner_repo, _slot)
                _learn(rec); return rec
            rec = _invoke_dispatch_subgraph(entry, prof, stamp, owner_repo, slot_handle=_scope.handle)
            _learn(rec); return rec
    # off → baseline：进程内 threading.Lock（同仓串行、跨仓并行；design 决策#8 不变）
    lock = run_daily.DISPATCH_LOCKS.get(owner_repo) if owner_repo else None
    with lock if lock else contextlib.nullcontext():
        rec = _invoke_dispatch_subgraph(entry, prof, stamp, owner_repo, slot_handle=None)
        _learn(rec); return rec


def _dispatch_aggregate_op(ni: dict, state: dict):
    """dispatch 聚合：经 stage_dispatch ``worker`` hook 注入 _dispatch_one_graph（零 drift 复用 stage_dispatch
    外壳——复用门 / passed / DISPATCH_SKIP_PROJECTS / dispatch_limit / locks 预构 / serial_shadow 分割 / 排序 /
    写文件），把 _run_one 替换为子图 invoke。

    --from-stage dispatch：critic 未跑，从盘读 prd_gate（镜像 _run_pipeline L3408-3410）。obs_log：dispatch
    逐 PRD 的子图 obs 在 worker 内累积不回主图（langgraph reducer 只在子图内合并）；本 op 吐汇总 obs 行
    （n_records/n_pass），report 段读主图 obs_log 得 dispatch 汇总。
    """
    import run_daily
    gate = state.get("critic_results") or []
    if not gate:   # --from-stage dispatch：critic 未跑，从盘读 prd_gate（镜像 _run_pipeline L3408-3410）
        gf = run_daily.STATE_DIR / f"prd_gate_{ni['stamp']}.json"
        gate = json.loads(gf.read_text(encoding="utf-8")) if gf.is_file() else []
    records = run_daily.stage_dispatch(state["_args"], gate, state["_profiles"], ni["stamp"],
                                       worker=_dispatch_one_graph)
    _PASS_STATUSES = ("pr_open", "merged", "interrupted_pr")   # r-review I4：子图实际成功 status（pr_open/interrupted_pr=开 PR 对账收尾，merged=publish_merge 真合）；删死代码 published（子图无此出口），补 interrupted_pr（对齐 legacy run_daily:3021/3069/3442 产出 PR 语义）
    n_pass = sum(1 for r in records if r.get("status") in _PASS_STATUSES)
    return ([], {"dispatch_results": records},
            {"stage": "dispatch", "n_records": len(records), "n_pass": n_pass})


node_dispatch_aggregate = make_mechanical_node(name="dispatch", stage="dispatch", op=_dispatch_aggregate_op)
