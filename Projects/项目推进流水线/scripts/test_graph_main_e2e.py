#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_graph_main_e2e.py — 主图端到端集成测试（task 5.8）。

补 r-review 测试缺口：批 1 有子图级 e2e（test_graph_dispatch_e2e）+ 拓扑/映射单测，但缺主图级
``build_main_graph.invoke`` 全 7 stage 线性推进 smoke。本文件用例 A 验主图编排正确：

A. test_main_graph_full_smoke — ``build_main_graph({lo:0,hi:6}).invoke(state)`` 全 6 包装 node 线性推进
   + obs_log 累积冒泡（Annotated[list, operator.add] reducer）+ metrics_<stamp>.json 落盘（决策 M 路径 A）。
B. test_dispatch_one_graph_worker_to_subgraph — 直调 _dispatch_one_graph（worker hook），mock 子图 invoke +
   准入底层（preflight/resolve_flags/build_coordinator/learning_memory），验 worker→_invoke_dispatch_subgraph
   →build_dispatch_subgraph().invoke(shell)→_subgraph_result_to_record（rec 映射）+ shell 13 字段
   _REQUIRED_SHELL + serial_shadow off 走 baseline nullcontext。

mock 策略守「只 mock 跨进程/跨网络/LLM 边界」：用例 A mock 6 个 ``run_daily.stage_X`` 纯函数（包装 node
直调的），让包装 node 的 op 逻辑（obs 构造 + ``_report_main_op`` 的 ``_aggregate_obs`` + metrics 写入）真跑；
不达 run_persona/subprocess/SMTP（被 stage_X mock 短路）。用例 B mock 子图 invoke + 准入底层（避 dev loop
subprocess/SDK），让 worker 的 shell 组装 + coord 派生注入 + rec 映射真跑。rec 21+3 字段映射由
test_subgraph_to_record_mapping 锁定；本文件两用例互补（A=主图编排 / B=worker hook 链路）。
"""
import json
import os
import sys
from argparse import Namespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import graph_pa

STAMP_A = "20260813"


def _isolate_state(monkeypatch, tmp_path):
    """重绑 run_daily 全局到 tmp_path（5.8 直接 invoke 不走 main()，手动隔离，守 pa-test-no-dirty-data）。

    重绑 STATE_DIR/VAULT_ROOT/REPORT_DIR/DAILY_DIR/RUN_LOCK + 清 serial_shadow/dispatch_skip env
    （避跨进程 flock 路径 + stage_dispatch 跳过）。
    """
    import run_daily
    sd = tmp_path / "state"; sd.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(run_daily, "STATE_DIR", sd)
    monkeypatch.setattr(run_daily, "VAULT_ROOT", tmp_path)
    monkeypatch.setattr(run_daily, "REPORT_DIR", tmp_path / "项目推进")
    monkeypatch.setattr(run_daily, "DAILY_DIR", tmp_path / "日报")
    monkeypatch.setattr(run_daily, "RUN_LOCK", sd / ".run.lock")
    monkeypatch.delenv("DISPATCH_SKIP_PROJECTS", raising=False)
    monkeypatch.delenv("PA_GRAPH_SINGLE_FLIGHT_SERIAL_SHADOW", raising=False)


def _mock_all_stages(monkeypatch, tmp_path):
    """mock 6 个 stage_X 纯函数（包装 node 直调）——不达 run_persona/subprocess/SMTP。

    签名对齐 run_daily：stage_fetch(a,s,st)/stage_radar(a,s,p,st)/stage_prd(a,c,p,st)/
    stage_critic(a,m,p,st)/stage_dispatch(a,g,p,st,*,worker=None)/stage_report(a,p,st)。
    mock 后包装 node op 真跑（obs 构造 + report 的 _aggregate_obs + metrics 写入）。
    """
    import run_daily
    monkeypatch.setattr(run_daily, "stage_fetch", lambda a, s, st: {"produced": [], "stamp": st})
    monkeypatch.setattr(run_daily, "stage_radar",
                        lambda a, s, p, st: {"candidates": [], "today_new_count": 0,
                                              "stats": {}, "per_source": {}})
    monkeypatch.setattr(run_daily, "stage_prd", lambda a, c, p, st: {"prds": [], "skipped": []})
    monkeypatch.setattr(run_daily, "stage_critic", lambda a, m, p, st: [])
    monkeypatch.setattr(run_daily, "stage_dispatch", lambda a, g, p, st, *, worker=None: [])
    monkeypatch.setattr(run_daily, "stage_report", lambda a, p, st: tmp_path / "report.md")


def test_main_graph_full_smoke(monkeypatch, tmp_path):
    """全 6 包装 node 线性推进 + obs_log 累积冒泡 + metrics_<stamp>.json 落盘。

    验证：fetch→radar→prd→critic→dispatch→report 严格线性（D8），每包装 node 追加 1 条 obs（含 "stage"
    字段），reducer operator.add 自动累加；_report_main_op 聚合 obs_log 写 metrics（node_count=5：report
    聚合时 obs_log 含前 5 node 的 obs，report 自己的 obs 由 reducer 在聚合后才追加）。
    """
    _isolate_state(monkeypatch, tmp_path)
    _mock_all_stages(monkeypatch, tmp_path)
    args = Namespace(stamp=STAMP_A, from_stage="fetch", to_stage="report", force=False,
                     dry_run=True, limit=None, dispatch_skip_dev=False, dispatch_limit=None,
                     max_concurrent=1, skip_critic=False, inject_prd=None, no_notify=True,
                     break_lock=False, project=None, state_dir=None)
    state = {
        "run_id": STAMP_A, "thread_id": f"run_{STAMP_A}", "stamp": STAMP_A,
        "prd_round": 0, "verify_round": 0, "obs_log": [], "side_effect_log": [],
        "_args": args,
        "_sources": [{"name": "s1", "kind": "directory", "root": "Knowledge/x",
                      "content_glob": "*.md", "marker": "m_s1.txt", "target_projects": ["proj-x"]}],
        "_profiles": {"proj-x": {"name": "proj-x", "repo": "", "type": "code",
                                  "admission": True, "dev_agent_ready": True,
                                  "default_branch": "main", "max_prs_in_flight": 2}},
        "_journal_path": "",   # top-level 无 journal（commit_node no-op；dispatch per-PRD 注入 coord.journal.path）
    }
    final = graph_pa.build_main_graph({"lo": 0, "hi": 6}).invoke(state)

    # ① 线性推进：obs_log 按 stage 顺序含 6 条（fetch→radar→prd→critic→dispatch→report，D8 严格线性）
    stages = [o.get("stage") for o in final["obs_log"]]
    assert stages == ["fetch", "radar", "prd", "critic", "dispatch", "report"], f"线性顺序错：{stages}"

    # ② obs_log 累积冒泡（Annotated[list, operator.add] reducer 自动累加每 node 的 [obs]）
    assert len(final["obs_log"]) == 6

    # ③ metrics_<stamp>.json 落盘 + schema（_report_main_op 调 _aggregate_obs 写，决策 M 路径 A）
    m_path = tmp_path / "state" / f"metrics_{STAMP_A}.json"
    assert m_path.exists(), f"metrics 文件未落盘：{m_path}"
    m = json.loads(m_path.read_text(encoding="utf-8"))
    assert set(m.keys()) == {"stamp", "run_id", "node_count", "totals", "by_model", "nodes"}, f"metrics keys 漂移：{m.keys()}"
    assert m["stamp"] == STAMP_A
    # report 聚合时 obs_log 含前 5 node 的 obs（report 自己的 obs 由 reducer 在聚合后才追加进 final）
    assert m["node_count"] == 5, f"node_count 应为 5（report 聚合时 obs_log 5 条）：{m['node_count']}"
    assert len(m["nodes"]) == 5
    assert m["totals"]["cost"] == 0.0    # mock stage_X 不产 cost（obs 无 cost/token_usage 字段）

    # ④ 产物字段依次填充（每包装 node 的 state_extra 写入主图 state）
    assert final.get("candidates") is not None              # radar 写 candidates payload
    assert "prd_manifest" in final                          # prd 写 manifest
    assert final.get("critic_results") == []                # critic mock 返 []（无 PRD）
    assert final.get("dispatch_results") == []              # dispatch mock 返 []（无过闸 PRD）
    assert "report" in final and final["report"]["path"]    # report 写 handle {"path": ...}


STAMP_B = "20260814"


def test_dispatch_one_graph_worker_to_subgraph(monkeypatch, tmp_path):
    """dispatch 聚合 worker=_dispatch_one_graph → 子图 invoke → rec 映射（task 5.8 第二簇）。

    用例 A 的 dispatch 是 mock stage_dispatch 返 []（未真跑 worker）；本用例直调 _dispatch_one_graph（worker），
    mock 子图 invoke + 准入底层（preflight/resolve_flags/build_coordinator/learning_memory），验证 worker hook
    链路：_dispatch_one_graph → _invoke_dispatch_subgraph（build shell + coord 派生注入）→
    build_dispatch_subgraph().invoke(shell) → _subgraph_result_to_record（rec schema）。

    rec 21+3 字段映射由 test_subgraph_to_record_mapping 锁定；本用例验：① rec 关键字段值映射（status/pr_url/
    branch/slug）② shell 含 13 字段 _REQUIRED_SHELL（_build_dispatch_shell + coord 派生 run_id 覆盖）③
    serial_shadow off + repo 空 → owner_repo 空 → nullcontext（无 slot_handle 注入，走 baseline threading.Lock 路径）。
    """
    import types
    import graph_pa_aggregate as AGG
    import graph_pa_dispatch as GD
    import graph_pa_recovery as GR
    import run_daily
    import coordinator
    _isolate_state(monkeypatch, tmp_path)

    # mock 准入底层 + 子图 invoke（避 dev loop subprocess/SDK/跨进程 flock）
    flags = types.SimpleNamespace(single_flight_serial_shadow=False)   # serial_shadow off → baseline lock 路径
    monkeypatch.setattr(run_daily, "resolve_flags", lambda env=None: flags)
    monkeypatch.setattr(coordinator, "preflight", lambda f: types.SimpleNamespace(is_ok=True))   # per-PRD C2 放行
    monkeypatch.setattr(run_daily, "build_coordinator", lambda **kw: types.SimpleNamespace(   # coord 派生注入
        run_id="r1", flags=flags, journal=types.SimpleNamespace(path="/j.jsonl"),
        iteration_id="i1", prd_id="p1"))
    monkeypatch.setattr(run_daily, "_attach_learning_memory", lambda *a, **kw: None)   # learning 后处理 no-op
    captured = []
    def fake_invoke(shell):
        captured.append(shell)
        return {"_exit_status": "pr_open", "_pr_url": "https://x/pr/1",
                "_branch": "pa-dev-x", "verify_round": 1, "terminal": ""}
    monkeypatch.setattr(GD, "build_dispatch_subgraph", lambda: types.SimpleNamespace(invoke=fake_invoke))

    # 预置 PRD 文件（_invoke_dispatch_subgraph 读 prd_abs 内容 + _split_frontmatter 抽 slug）
    prd_rel = "state/prd/proj-x/p.md"
    prd_abs = tmp_path / prd_rel
    prd_abs.parent.mkdir(parents=True, exist_ok=True)
    prd_abs.write_text("---\nslug: p\n---\nbody", encoding="utf-8")

    entry = {"verdict": "pass", "project": "proj-x", "prd_path": prd_rel,
             "source_path": "Knowledge/x/s1.md"}
    prof = {"name": "proj-x", "repo": "", "type": "code", "admission": True,
            "dev_agent_ready": True, "default_branch": "main", "max_prs_in_flight": 2,
            "conda_env": ""}
    args = Namespace(stamp=STAMP_B, force=True, max_concurrent=1, dispatch_limit=None,
                     dispatch_skip_dev=False, no_notify=True, dry_run=True, skip_critic=False)
    rec = AGG._dispatch_one_graph(entry, prof, STAMP_B, args)

    # ① rec 关键字段（_subgraph_result_to_record 映射子图 state → rec schema）
    assert rec["status"] == "pr_open", f"status 映射错：{rec['status']}"
    assert rec["pr_url"] == "https://x/pr/1"
    assert rec["branch"] == "pa-dev-x"
    assert rec["slug"] == "p"           # _split_frontmatter 抽 frontmatter slug="p"（stable_slug 覆盖 Path stem）

    # ② shell 含 13 字段 _REQUIRED_SHELL（_build_dispatch_shell + coord 派生注入后 run_id="r1"）
    shell = captured[0]
    missing = [k for k in GR._REQUIRED_SHELL if k not in shell]
    assert not missing, f"shell 缺 _REQUIRED_SHELL 字段：{missing}"
    assert shell["run_id"] == "r1"      # _invoke_dispatch_subgraph L247 coord.run_id 覆盖占位 ""

    # ③ serial_shadow off + repo 空 → owner_repo 空 → nullcontext（无 slot_handle 注入）
    assert shell["_owner_repo"] == ""
    assert "_slot_handle" not in shell   # baseline 路径不注入 slot_handle
