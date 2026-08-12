#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_graph_publication.py — publication baseline 路 + terminal_emit 测试（任务 3.5e）。

验证（对齐 dispatch_one L2323-2515 publication + L2514 _sj_terminal 统一收尾）：
① publication_reconcile：flag off→no-op；flag on + safe→写 reconciliation；unknown→blocked（L2328-2348）
② publish_baseline：reconcile_pr(interrupted=False) → _exit_status=rec.status（pr_open/fail/blocked_external_state，L2460）
③ terminal_emit：_exit_status/terminal 映射 + 构造 rec → _sj_terminal（L2514）；sj=None→no-op
④ terminal_emit enum fallback：terminal=STATUS_TRIAGED 无 _exit_status → "triaged"
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import graph_pa_nodes as GN
import graph_pa_contracts as C


_BASE = {"run_id": "r", "stamp": "20260811", "config": {},
         "_slug": "x", "_worktree_abs": "/repo", "_owner_repo": "owner/repo",
         "_base": "main", "_branch": "pa-dev-x", "_prd_abs": "/abs/prd.md",
         "_verify_payload": {"pass": True, "test_rc": 0, "evidence_ref": {"digest": "sha256:abc"}},
         "_verify_verdict": "pass"}


# ── publication_reconcile ─────────────────────────────────────────────
def _mock_reconcile_se(monkeypatch, safe, unknown_kinds=()):
    import reconcile
    confirmed = [types.SimpleNamespace(kind="push"), types.SimpleNamespace(kind="pr")]
    unknown = [types.SimpleNamespace(kind=k) for k in unknown_kinds]
    report = types.SimpleNamespace(confirmed=confirmed, pending=[], unknown=unknown, safe_to_retry=safe)
    monkeypatch.setattr(reconcile, "reconcile_side_effects", lambda **kw: report)


def _flags_coord(session_aware_retry=True, resolver="resolver"):
    flags = types.SimpleNamespace(session_aware_retry=session_aware_retry)
    coord = types.SimpleNamespace(resolver=resolver if resolver else None)
    return flags, coord


def test_pub_reconcile_config():
    assert GN.node_publication_reconcile._kind is GN.KIND_MECHANICAL
    assert GN.node_publication_reconcile._cfg["stage"] == "dispatch"


def test_pub_reconcile_baseline_noop(monkeypatch):
    """flag off（无 _coord_flags）→ no-op（不调 reconcile_side_effects，无 terminal，L2327-2328）。"""
    import reconcile
    called = []
    monkeypatch.setattr(reconcile, "reconcile_side_effects", lambda **kw: called.append(1))
    upd = GN.node_publication_reconcile(dict(_BASE))             # 无 _coord_flags → baseline
    assert called == []
    assert not upd.get("terminal")


def test_pub_reconcile_no_resolver_noop(monkeypatch):
    """session_aware_retry on 但无 resolver → no-op（baseline，dispatch 决策零变化）。"""
    _mock_reconcile_se(monkeypatch, safe=True)
    s = dict(_BASE); s["_coord_flags"], s["_coord"] = _flags_coord(resolver=None)
    upd = GN.node_publication_reconcile(s)
    assert not upd.get("terminal")                                # resolver None → no-op


def test_pub_reconcile_safe_passed(monkeypatch):
    """flag on + safe_to_retry → 写 _publication_reconciliation，无 terminal（L2329-2343）。"""
    _mock_reconcile_se(monkeypatch, safe=True)
    s = dict(_BASE); s["_coord_flags"], s["_coord"] = _flags_coord()
    upd = GN.node_publication_reconcile(s)
    assert not upd.get("terminal")
    assert upd["_publication_reconciliation"]["safe_to_publish"] is True
    assert "push" in upd["_publication_reconciliation"]["confirmed"]


def test_pub_reconcile_unknown_blocked(monkeypatch):
    """flag on + unknown 副作用 → terminal=blocked + _exit_status（fail-safe，L2344-2348）。"""
    _mock_reconcile_se(monkeypatch, safe=False, unknown_kinds=("push",))
    s = dict(_BASE); s["_coord_flags"], s["_coord"] = _flags_coord()
    upd = GN.node_publication_reconcile(s)
    assert upd["terminal"] == C.STATUS_BLOCKED
    assert upd["_exit_status"] == "blocked_external_state"
    assert upd["_blocked_check"] == "publication_reconcile"
    assert upd["_publication_reconciliation"]["safe_to_publish"] is False


# ── publish_baseline ──────────────────────────────────────────────────
def _mock_reconcile_pr(monkeypatch, status, pr_url=None):
    import run_daily

    def fake(repo, owner_repo, rec, base, slug, interrupted=True):
        rec["status"] = status
        if pr_url:
            rec["pr_url"] = pr_url
    monkeypatch.setattr(run_daily, "reconcile_pr", fake)


def test_publish_baseline_config():
    assert GN.node_publish_baseline._kind is GN.KIND_MECHANICAL
    assert GN.node_publish_baseline._cfg["stage"] == "dispatch"


def test_publish_baseline_pr_open(monkeypatch):
    """reconcile_pr 设 pr_open → _exit_status=pr_open + _pr_url（verify 绿正常 PR，L2460）。"""
    _mock_reconcile_pr(monkeypatch, "pr_open", pr_url="https://x/pr/1")
    upd = GN.node_publish_baseline(dict(_BASE))
    assert upd["_exit_status"] == "pr_open"
    assert upd["_pr_url"] == "https://x/pr/1"
    assert not upd.get("terminal")                                # 不 terminal，交 terminal_emit


def test_publish_baseline_interrupted_false_called(monkeypatch):
    """verify 绿 → reconcile_pr interrupted=False（正常 PR，非中断 PR）。"""
    import run_daily
    seen = []

    def fake(repo, owner_repo, rec, base, slug, interrupted=True):
        seen.append(interrupted); rec["status"] = "pr_open"
    monkeypatch.setattr(run_daily, "reconcile_pr", fake)
    GN.node_publish_baseline(dict(_BASE))
    assert seen == [False]                                        # interrupted=False（正常 PR）


def test_publish_baseline_blocked_external(monkeypatch):
    """reconcile_pr 设 blocked_external_state（pr lookup unknown）→ _exit_status 喂 terminal_emit。"""
    _mock_reconcile_pr(monkeypatch, "blocked_external_state")
    upd = GN.node_publish_baseline(dict(_BASE))
    assert upd["_exit_status"] == "blocked_external_state"


def test_publish_baseline_fail_no_branch(monkeypatch):
    """无 branch + dev_killed → reconcile_pr 设 fail（L2545-2546）。"""
    import run_daily

    def fake(repo, owner_repo, rec, base, slug, interrupted=True):
        if not rec.get("branch"):
            rec["status"] = "fail"; rec["skip_reason"] = "dev loop 未吐 branch"
    monkeypatch.setattr(run_daily, "reconcile_pr", fake)
    s = dict(_BASE); s["_branch"] = None; s["_dev_killed"] = True
    upd = GN.node_publish_baseline(s)
    assert upd["_exit_status"] == "fail"


# ── terminal_emit ─────────────────────────────────────────────────────
def _mock_sj_terminal(monkeypatch, captured):
    import run_daily
    monkeypatch.setattr(run_daily, "_sj_terminal",
                        lambda sj, rec, iter_id, prd_id, artifact_root=None: captured.update(rec))


def test_terminal_emit_config():
    assert GN.node_terminal_emit._kind is GN.KIND_MECHANICAL
    assert GN.node_terminal_emit._cfg["stage"] == "dispatch"


def test_terminal_emit_no_sj_noop(monkeypatch):
    """sj=None → no-op（short-circuit，不调 _sj_terminal），仍写 _exit_status。"""
    import run_daily
    called = []
    monkeypatch.setattr(run_daily, "_sj_terminal", lambda *a, **kw: called.append(1))
    s = dict(_BASE); s["_exit_status"] = "skip"; s["_sj"] = None
    upd = GN.node_terminal_emit(s)
    assert called == []
    assert upd["_exit_status"] == "skip"


def test_terminal_emit_skip_event(monkeypatch):
    """_exit_status=skip → _sj_terminal 收 rec.status=skip（_SJ_TERMINAL_MAP → aborted）。"""
    captured = {}
    _mock_sj_terminal(monkeypatch, captured)
    s = dict(_BASE); s["_exit_status"] = "skip"; s["_skip_reason"] = "profile 不满足"
    s["_sj"] = types.SimpleNamespace(path="/j.jsonl")
    GN.node_terminal_emit(s)
    assert captured["status"] == "skip"
    assert captured["skip_reason"] == "profile 不满足"


def test_terminal_emit_pr_open_published_rec(monkeypatch):
    """_exit_status=pr_open + 机械绿+语义pass → rec.status=pr_open + verify（_sj_terminal 内双门判 published）。"""
    captured = {}
    _mock_sj_terminal(monkeypatch, captured)
    s = dict(_BASE); s["_exit_status"] = "pr_open"; s["_pr_url"] = "https://x/pr/1"
    s["_sj"] = types.SimpleNamespace(path="/j.jsonl")
    GN.node_terminal_emit(s)
    assert captured["status"] == "pr_open"
    assert captured["pr_url"] == "https://x/pr/1"
    assert captured["verify_verdict"] == "pass"
    assert captured["verify"]["pass"] is True                       # 机械绿（dual gate ①）


def test_terminal_emit_enum_fallback_triaged(monkeypatch):
    """terminal=STATUS_TRIAGED 无 _exit_status → fallback 映射 "triaged"（dev off_track 路径）。"""
    captured = {}
    _mock_sj_terminal(monkeypatch, captured)
    s = dict(_BASE); s.pop("_verify_payload", None)
    s["terminal"] = C.STATUS_TRIAGED                              # 无 _exit_status → enum fallback
    s["_sj"] = types.SimpleNamespace(path="/j.jsonl")
    GN.node_terminal_emit(s)
    assert captured["status"] == "triaged"


def test_terminal_emit_blocked_test_gate(monkeypatch):
    """_exit_status=blocked_test_gate（dev blocked_by_gate）→ rec.status=blocked_test_gate（_SJ_TERMINAL_MAP test_blocked）。"""
    captured = {}
    _mock_sj_terminal(monkeypatch, captured)
    s = dict(_BASE); s["_exit_status"] = "blocked_test_gate"
    s["_sj"] = types.SimpleNamespace(path="/j.jsonl")
    GN.node_terminal_emit(s)
    assert captured["status"] == "blocked_test_gate"


def test_terminal_emit_carries_artifact_root(monkeypatch):
    """terminal_emit 传 artifact_root（publication evidence reconcile，L2514 task 4.4）。"""
    captured = {}
    import run_daily

    def fake(sj, rec, iter_id, prd_id, artifact_root=None):
        captured["artifact_root"] = artifact_root
    monkeypatch.setattr(run_daily, "_sj_terminal", fake)
    s = dict(_BASE); s["_exit_status"] = "pr_open"
    s["_sj"] = types.SimpleNamespace(path="/j.jsonl")
    GN.node_terminal_emit(s)
    assert captured["artifact_root"] == run_daily.STATE_DIR / "artifacts" / "r"
