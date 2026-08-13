#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""graph_pa.py — pa 主图（7 阶段线性）+ feature_flag cutover 入口。

langgraph-workflow-upgrade Phase 3（task 3.9 + 5.2）。把命令式 ``_run_pipeline``（run_daily 7 阶段 if 门控
串）替换为 LangGraph StateGraph 线性组装：fetch→radar→prd→[inject]→critic→dispatch→report。node 实装在
``graph_pa_aggregate``（6 包装 stage_X + dispatch 聚合子图 invoke）。

设计（守 plan 批 1 + design D1/D2/D5/D7/D8）：
- **严格线性**（无条件分支）：terminal/enum 升人工路径不短路主图——interrupted_pr/halted/cooldown 全在
  dispatch 子图内部处理，记录全累积进 dispatch_{stamp}.json。唯一短路 RuntimeError→exit(1)（state 已落盘
  可 --from-stage 续跑，对齐 _run_pipeline L3414-3417）。
- **编译期条件加 node**（非 node 内 skip）：config["lo"]/["hi"]（--from-stage/--to-stage stage 编号）决定哪些
  node 进拓扑；config["inject_prd"] 非空才挂 inject（prd 后覆盖 manifest，对齐 _run_pipeline L3395-3396）。
- **无 Checkpointer**（D2 journal 单写真源）；**flag off = run_daily 完整保留**（D7：run_daily 不 import 本模块，
  graph_pa.py 经 run_daily 的 stage_*/lock/setup 复用，不重写）。
- **claude runtime 零改动**（D1）；argparse 与 run_daily byte-identical（shadow parity 前置），lock/setup 复用
  run_daily（零重写避双锁漂移）。

批 1 范围：主图拓扑 + invoke smoke。run_cron 分流（5.2）/flag（5.1）/preflight（5.4）留批 2。
"""
from __future__ import annotations

import argparse
import os
import sys

from graph_pa_state import GraphState


def build_main_graph(config: dict | None = None):
    """构建主图（compiled StateGraph，7 阶段线性）。

    config 编译期条件（对齐 _run_pipeline 的 ``if lo<=N<=hi`` stage 编号门控）：
    - ``lo``/``hi``：stage 编号窗口（fetch=0 radar=1 prd=2 inject=3 critic=4 dispatch=5 report=6）。
    - ``inject_prd``：非空才挂 inject node（prd 后，覆盖 manifest）；空则 prd→critic 直连。

    无 Checkpointer（D2）；线性边（无条件分支）。terminal/enum 升人工路径不短路主图（全在 dispatch 子图内）。
    """
    from langgraph.graph import START, END, StateGraph
    import graph_pa_aggregate as AGG
    cfg = config or {}
    lo = cfg.get("lo", 0)
    hi = cfg.get("hi", 6)
    inject_prd = cfg.get("inject_prd")
    g = StateGraph(GraphState)
    # stage 编号窗口决定哪些 node 进拓扑（对齐 _run_pipeline if lo<=N<=hi）
    nodes: list[str] = []
    if lo <= 0 <= hi:
        g.add_node("fetch", AGG.node_fetch_main); nodes.append("fetch")
    if lo <= 1 <= hi:
        g.add_node("radar", AGG.node_radar_main); nodes.append("radar")
    if lo <= 2 <= hi:
        g.add_node("prd", AGG.node_prd_main); nodes.append("prd")
    if lo <= 3 <= hi and inject_prd:                       # inject 条件挂（prd 后覆盖 manifest）
        g.add_node("inject", AGG.node_inject_main); nodes.append("inject")
    if lo <= 4 <= hi:
        g.add_node("critic", AGG.node_critic_main); nodes.append("critic")
    if lo <= 5 <= hi:
        g.add_node("dispatch", AGG.node_dispatch_aggregate); nodes.append("dispatch")
    if lo <= 6 <= hi:
        g.add_node("report", AGG.node_report_main); nodes.append("report")
    if not nodes:                                          # 窗口空（防御性）：START→END 直通
        g.add_edge(START, END)
        return g.compile()
    g.add_edge(START, nodes[0])
    for a, b in zip(nodes, nodes[1:]):
        g.add_edge(a, b)
    g.add_edge(nodes[-1], END)
    return g.compile()


def _run_pipeline_graph(args) -> None:
    """graph 路径流水线主体（镜像 _run_pipeline，命令式 stage 串替换为 build_main_graph.invoke）。

    state 注入：initial GraphState + 运行期 ``_args``/``_sources``/``_profiles``（_args Namespace 违 R8 不可
    序列化，但只 node 内读不经 reducer 序列化 + 崩溃恢复不重建，风险可接受；详见 graph_pa_aggregate 模块头）。
    RuntimeError→exit(1)（state 已落盘可 --from-stage 续跑，对齐 _run_pipeline L3414-3417）。
    """
    import run_daily
    (run_daily.STATE_DIR / "prd").mkdir(parents=True, exist_ok=True)
    run_daily.log(f"═══ 项目推进流水线（graph）{args.stamp} ═══  stage {args.from_stage}→{args.to_stage}"
                  f"{'  [DRY-RUN]' if args.dry_run else ''}{'  [LIMIT=' + str(args.limit) + ']' if args.limit else ''}"
                  f"{'  [INJECT=' + str(args.inject_prd) + ']' if args.inject_prd else ''}")
    sources = run_daily.load_sources()
    profiles = run_daily.load_profiles()
    if args.project:                                       # canary 单仓隔离：只留命中的 profile
        profiles = run_daily._filter_profiles(profiles, args.project)
        run_daily.log(f"  [PROJECT] 仅跑：{args.project}")
    run_daily.log(f"sources={[s['name'] for s in sources]}  profiles={list(profiles)}")
    run_map = {s: i for i, s in enumerate(run_daily.STAGES)}
    lo, hi = run_map[args.from_stage], run_map[args.to_stage]
    stamp = args.stamp                                     # inject 段可能自增（node 写回 state["stamp"]）
    try:
        state = {
            "run_id": stamp, "thread_id": f"run_{stamp}", "stamp": stamp,   # run_id 非空（对齐 run_daily run_id=stamp + contract validate_node_input 要求；task 5.8 主图 invoke smoke 暴露原 "" 占位触发 ContractError）
            "prd_round": 0, "verify_round": 0,
            "obs_log": [], "side_effect_log": [],
            "_args": args, "_sources": sources, "_profiles": profiles,
            "_journal_path": "",  # top-level 无 journal（commit_node no-op；dispatch per-PRD 注入 coord.journal.path）
        }
        config = {"lo": lo, "hi": hi, "inject_prd": getattr(args, "inject_prd", None)}
        build_main_graph(config).invoke(state)
    except (RuntimeError, ImportError) as e:   # r-review I3：RuntimeError=stage 业务异常；ImportError=langgraph 未装（shadow 路径误开 grafeno）
        import traceback
        traceback.print_exc()                  # python-review H3：ImportError 也会被真 import bug 触发，须见 traceback 区分「环境缺 langgraph」vs「代码 bug」
        run_daily.log(f"✗ {e}")
        run_daily.log("（state 产物已落盘，修参后可 --from-stage 续跑）")
        sys.exit(1)


def main():
    """graph 入口（镜像 run_daily.main：argparse byte-identical + lock/setup 复用，_run_pipeline 换 graph）。

    argparse 与 run_daily byte-identical（shadow parity 前置，仅 description 标 graph 路径）；lock/setup
    复用 run_daily 函数（零重写避双锁漂移）。批 2 run_cron.sh 分流后，cron 经此入口跑 graph 路径。
    """
    import run_daily
    ap = argparse.ArgumentParser(description="项目推进流水线·编排器（graph 路径，Phase-3）")
    ap.add_argument("--stamp", default=run_daily.today_stamp(), help="日期 YYYYMMDD（默认今天）")
    ap.add_argument("--from-stage", choices=run_daily.STAGES, default="radar")
    ap.add_argument("--to-stage", choices=run_daily.STAGES, default="dispatch",
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
    args.project = run_daily._normalize_projects(args.project)          # append+逗号 归一为 list（None=不过滤）
    run_daily._apply_state_dir(args.state_dir)                          # 须在 STATE_DIR.mkdir / acquire_run_lock 前：重绑 STATE_DIR+RUN_LOCK
    if args.from_stage == "inject" and not args.inject_prd:
        ap.error("--from-stage inject 需要 --inject-prd <path>")
    run_daily._load_claude_settings_env()   # SDK 子进程拿到 ANTHROPIC_* 认证（cron/SSH 启动兜底）

    # langgraph r-review C1：入口级 preflight（图路径独有安全门）。
    #   legacy run_daily 只在 dispatch_one 内 per-PRD preflight（run_daily.py:2083），无入口级；图 cutover 路径
    #   需更严门——orchestrator on 但 shadow off（或任何 loop flag 依赖违规）在跑任何 stage 前早失败 exit(2)，
    #   不占锁/不跑 fetch...critic/不浪费 persona 调用。亦解决 gate 空（无过闸 PRD）时 per-PRD preflight 不触发
    #   致 orchestrator⇒shadow 绕过的窗口。resolve_flags(env) == coord.flags（flags 全局 env 态）。
    import coordinator as _coord_mod
    _pf = _coord_mod.preflight(run_daily.resolve_flags(env=os.environ))
    if not _pf.is_ok:
        run_daily.log(f"✗ preflight blocked：{_pf.blocked.reason}")
        for _v in _pf.blocked.violations:
            run_daily.log(f"    - {_v}")
        sys.exit(2)

    # run 级互斥锁（复用 run_daily，零重写避双锁漂移）：锁获取在 STATE_DIR.mkdir 后，包整个 pipeline
    run_daily.STATE_DIR.mkdir(parents=True, exist_ok=True)
    if not run_daily.acquire_run_lock(args.break_lock):
        sys.exit(2)
    try:
        _run_pipeline_graph(args)
    finally:
        run_daily.release_run_lock()


if __name__ == "__main__":
    main()
