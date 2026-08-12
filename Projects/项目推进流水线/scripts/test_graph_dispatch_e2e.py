#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_graph_dispatch_e2e.py — dispatch 子图端到端集成测试（任务 3.5h）。

跑完整 dispatch 子图（10 节点全拓扑），验证 baseline 各终态路径组装正确 + state 流转对。
这是 shadow parity 的 proxy：graph 路径终态 _exit_status 与 dispatch_one 语义一致
（完整 cutover.run_shadow_parity_drill 对接留主图集成 task，需真实 cron 运行环境）。

覆盖路径：
① baseline pass：admission→worktree→verify(pass)→publish_baseline(pr_open)→terminal_emit→slot_release
② admission blocked 短路：branch_protection UNKNOWN → terminal_emit（不进 worktree/verify）
③ verify interrupted_pr：verify 用满（round2 仍 revise）→ terminal_emit
"""
import json
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import graph_pa_dispatch as GD
import graph_pa_contracts as C


_BASE = {"run_id": "r", "stamp": "20260811", "config": {},
         "_project": "proj", "_slug": "x",
         "_prof": {"conda_env": "", "admission": True, "dev_agent_ready": True,
                   "type": "code", "max_prs_in_flight": 2, "repo": "owner/repo"},
         "_worktree_abs": "/repo", "_owner_repo": "owner/repo", "_base": "main",
         "_prd_path": "state/prd/x/p.md", "_prd_abs": "/abs/prd.md",
         "_src_abs": "/abs/src.md", "_dev_log_file": None, "verify_round": 1}


def _capture_subprocess(monkeypatch, dev_json=None, dev_terminal=None):
    """统一 _run_capture：worktree 命令→success；dev-agent→exit code + json。"""
    import run_daily
    _DEV_OK = {"ok": True, "branch": "pa-dev-x", "cost": 0.5, "turns": 12, "test_cmd": "pytest"}

    def fake(cmd, *a, **kw):
        cmd_str = " ".join(str(c) for c in cmd) if isinstance(cmd, list) else str(cmd)
        if "worktree" in cmd_str:
            return (0, "", "")
        if "dev-agent" in cmd_str or "dev_agent" in cmd_str:
            if dev_terminal == "blocked":
                return (14, '{"blocked_by_gate": true, "gate_status": "test_failed", "branch": "pa-dev-x"}', "")
            j = dev_json or _DEV_OK
            return (0, json.dumps(j), "")
        return (0, "", "")
    monkeypatch.setattr(run_daily, "_run_capture", fake)
    monkeypatch.setattr(run_daily, "_dev_cmd",
                        lambda *a, **kw: ["py", "dev-agent.py", "--prd", "p", "--base", "main"])


def _mock_admission_pass(monkeypatch):
    import run_daily
    monkeypatch.setattr(run_daily, "dev_slugify", lambda s: f"dev-{s}")
    monkeypatch.setattr(run_daily, "check_branch_protection", lambda *a, **kw: run_daily.found(True))
    monkeypatch.setattr(run_daily, "already_dispatched", lambda *a, **kw: run_daily.not_found("新"))
    monkeypatch.setattr(run_daily, "count_inflight_prs", lambda *a, **kw: run_daily.found(0))


def _mock_dev_post_pass(monkeypatch):
    import run_daily
    import artifact_store
    monkeypatch.setattr(run_daily, "_has_commits", lambda *a, **kw: run_daily.found(True))
    monkeypatch.setattr(run_daily, "independent_verify",
                        lambda *a, **kw: {"pass": True, "test_rc": 0, "test_cmd": "pytest"})
    monkeypatch.setattr(artifact_store, "store",
                        lambda *a, **kw: types.SimpleNamespace(digest="sha256:abc", path="sha256/ab/c", size=10))
    monkeypatch.setattr(run_daily, "_dump_branch_diff", lambda *a, **kw: None)


def _mock_verify_seq(monkeypatch, verdicts):
    import run_daily
    it = iter(verdicts)

    def fake(agent, prompt, stage, label, allowed_tools=None):
        v = next(it)
        return ({"verdict": v, "feedback_section": "fix" if v == "revise" else "",
                 "summary": v, "round": 1}, {"cost": 0.1, "turns": 3})
    monkeypatch.setattr(run_daily, "run_persona", fake)
    monkeypatch.setattr(run_daily, "_append_verify_feedback", lambda *a, **kw: None)


def _mock_publish_pr_open(monkeypatch):
    import run_daily

    def fake(repo, owner_repo, rec, base, slug, interrupted=True):
        rec["status"] = "pr_open"; rec["pr_url"] = "https://x/pr/1"
    monkeypatch.setattr(run_daily, "reconcile_pr", fake)


def _mock_sj(monkeypatch, captured):
    import run_daily
    monkeypatch.setattr(run_daily, "_sj_terminal",
                        lambda sj, rec, iter_id, prd_id, artifact_root=None: captured.update(rec))


# ── ① baseline pass → pr_open ────────────────────────────────────────
def test_e2e_baseline_pass_pr_open(monkeypatch):
    """全拓扑 baseline pass：admission→worktree→verify pass→publish_baseline→terminal_emit→slot_release。
    终态 _exit_status=pr_open（对齐 dispatch_one verify-绿兜底开 PR，L2460）。"""
    _mock_admission_pass(monkeypatch)
    _capture_subprocess(monkeypatch)                  # worktree + dev 都绿
    _mock_dev_post_pass(monkeypatch)
    _mock_verify_seq(monkeypatch, ["pass"])
    _mock_publish_pr_open(monkeypatch)
    captured = {}
    _mock_sj(monkeypatch, captured)
    s = dict(_BASE); s["_sj"] = types.SimpleNamespace(path="/j.jsonl")
    result = GD.build_dispatch_subgraph().invoke(s)
    assert result["_exit_status"] == "pr_open"        # publish_baseline 喂 terminal_emit
    assert captured["status"] == "pr_open"            # terminal_emit _sj_terminal 收 pr_open
    assert captured["pr_url"] == "https://x/pr/1"
    assert result.get("_terminal_emitted") is True
    # slot_release baseline（无 _coord_flags）→ no-op（不写 _slot_released，正确：baseline 无 slot）


# ── ② admission blocked 短路 ──────────────────────────────────────────
def test_e2e_admission_blocked_short_circuits(monkeypatch):
    """admission branch_protection UNKNOWN → terminal_emit（fail-safe），不进 worktree/verify。
    对齐 dispatch_one L2108 UNKNOWN→blocked_external_state。"""
    import run_daily
    dev_called = []
    monkeypatch.setattr(run_daily, "dev_slugify", lambda s: f"dev-{s}")
    monkeypatch.setattr(run_daily, "check_branch_protection", lambda *a, **kw: run_daily.unknown("gh 5xx"))
    monkeypatch.setattr(run_daily, "already_dispatched", lambda *a, **kw: run_daily.not_found("新"))
    monkeypatch.setattr(run_daily, "count_inflight_prs", lambda *a, **kw: run_daily.found(0))
    _capture_subprocess(monkeypatch)                  # worktree 仍 mock（但 admission 短路不达 worktree）
    monkeypatch.setattr(run_daily, "_has_commits", lambda *a, **kw: dev_called.append(1) or run_daily.found(True))
    captured = {}
    _mock_sj(monkeypatch, captured)
    s = dict(_BASE); s["_sj"] = types.SimpleNamespace(path="/j.jsonl")
    result = GD.build_dispatch_subgraph().invoke(s)
    assert result["terminal"] == C.STATUS_BLOCKED     # admission UNKNOWN fail-safe
    assert result["_exit_status"] == "blocked_external_state"
    assert captured["status"] == "blocked_external_state"
    assert dev_called == []                           # 短路：不达 dev_post（admission 直接 → terminal_emit）


# ── ③ verify interrupted_pr（用满）──────────────────────────────────
def test_e2e_verify_interrupted_pr(monkeypatch):
    """verify round1 revise→redo→round2 仍 revise，用满 → terminal=interrupted_pr → terminal_emit。
    对齐 dispatch_one L2510-2512「判红用满 → 对账降级 interrupted_pr」。"""
    _mock_admission_pass(monkeypatch)
    _capture_subprocess(monkeypatch)
    _mock_dev_post_pass(monkeypatch)
    _mock_verify_seq(monkeypatch, ["revise", "revise"])   # 两轮都 revise
    _mock_publish_pr_open(monkeypatch)                # 不达（verify terminal 短路）
    captured = {}
    _mock_sj(monkeypatch, captured)
    s = dict(_BASE); s["_sj"] = types.SimpleNamespace(path="/j.jsonl")
    result = GD.build_dispatch_subgraph().invoke(s)
    assert result["terminal"] == C.STATUS_INTERRUPTED
    assert result["terminal"] == "interrupted_pr"     # enum 字面值
    assert result["verify_round"] == 2                # redo bump 到 MAX
    assert captured["status"] == "interrupted_pr"     # terminal_emit 映射 _exit_status
