#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_graph_dev_post.py — dev_post MechanicalNode 测试（任务 3.5b）。

验证（对齐 dispatch_one L2261-2298 + L2510-2512）：
① 配置（KIND_MECHANICAL, stage=dispatch）
② 无 branch → reconcile_pr(interrupted=True) 对账 + terminal（L2262-2264）
③ 无 has_commits（FOUND-False）→ 对账收尾 + terminal（L2510-2512）
④ has_commits UNKNOWN → fail-safe 对账收尾 + terminal（L2269-2270）
⑤ green evidence 持久化失败 → terminal=blocked + pass 降级 False（L2287-2295，不 reconcile_pr）
⑥ 正常 pass + evidence ok → 写 _verify_payload/evidence_ref/_diff_path/_base，不 terminal（进 verify）
⑦ 红 test（pass=False）→ 不 store evidence，_verify_payload 无 evidence_ref，交 pa-verify 审（L2281 if pass 才 store）
⑧ reconcile status→terminal 映射（7 值参数化）
⑨ diff 路径形态（STATE_DIR/runs/<proj>/<stamp>_<slug>.r<round>.diff，对齐 L2303）
"""
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import graph_pa_nodes as GN
import graph_pa_contracts as C


_BASE = {"run_id": "r", "thread_id": "t", "stamp": "20260811", "config": {},
         "_project": "proj", "_slug": "x", "_prof": {"conda_env": ""},
         "_worktree_abs": "/repo", "_branch": "pa-dev-x", "_base": "main",
         "_owner_repo": "owner/repo", "_dev_log_file": None, "verify_round": 1,
         "_dev_script": {"test_cmd": "pytest"}, "_dev_killed": False}


def _mock_has_commits(monkeypatch, result):
    import run_daily
    monkeypatch.setattr(run_daily, "_has_commits", lambda *a, **kw: result)


def _mock_independent(monkeypatch, vj):
    import run_daily
    monkeypatch.setattr(run_daily, "independent_verify", lambda *a, **kw: dict(vj))


def _mock_reconcile(monkeypatch, status, pr_url=None):
    import run_daily

    def fake(repo, owner_repo, rec, base, slug, interrupted=True):
        rec["status"] = status
        if pr_url:
            rec["pr_url"] = pr_url
    monkeypatch.setattr(run_daily, "reconcile_pr", fake)


def _mock_store_ok(monkeypatch):
    import artifact_store
    monkeypatch.setattr(artifact_store, "store",
                        lambda *a, **kw: types.SimpleNamespace(digest="sha256:abc", path="sha256/ab/c", size=10))


def _mock_dump(monkeypatch, sink=None):
    import run_daily

    def fake(repo, base_ref, branch, out_path):
        if sink is not None:
            sink.append(str(out_path))
    monkeypatch.setattr(run_daily, "_dump_branch_diff", fake)


# ── 配置 ────────────────────────────────────────────────────────────────
def test_dev_post_config():
    assert GN.node_dev_post._kind is GN.KIND_MECHANICAL
    assert GN.node_dev_post._cfg["stage"] == "dispatch"


# ── 无产出收尾（对账 + terminal）─────────────────────────────────────────
def test_dev_post_no_branch_reconcile(monkeypatch):
    """无 _branch（dev 建分支前崩）→ reconcile_pr interrupted=True + terminal（L2262-2264）。"""
    import run_daily
    seen = []

    def fake(repo, owner_repo, rec, base, slug, interrupted=True):
        seen.append(interrupted); rec["status"] = "interrupted_pr"
    monkeypatch.setattr(run_daily, "reconcile_pr", fake)
    s = dict(_BASE); s["_branch"] = None
    upd = GN.node_dev_post(s)
    assert seen == [True]                          # interrupted=True（对账收尾，非 publish）
    assert upd["terminal"] == C.STATUS_INTERRUPTED


def test_dev_post_no_has_commits_reconcile(monkeypatch):
    """FOUND-False（无新 commit）→ 对账收尾 orphan_deleted→triaged（L2510-2512）。"""
    import run_daily
    _mock_has_commits(monkeypatch, run_daily.found(False))
    _mock_reconcile(monkeypatch, "orphan_deleted")
    upd = GN.node_dev_post(dict(_BASE))
    assert upd["terminal"] == C.STATUS_TRIAGED
    assert upd["_reconcile_status"] == "orphan_deleted"


def test_dev_post_unknown_has_commits_fail_safe(monkeypatch):
    """has_commits UNKNOWN → fail-safe 跳过独立验证，对账收尾（L2269-2270）。"""
    import run_daily
    _mock_has_commits(monkeypatch, run_daily.unknown("git log 查询超时"))
    _mock_reconcile(monkeypatch, "interrupted_pr")
    upd = GN.node_dev_post(dict(_BASE))
    assert upd["terminal"] == C.STATUS_INTERRUPTED


@pytest.mark.parametrize("rec_status,terminal", [
    ("interrupted_pr", C.STATUS_INTERRUPTED),
    ("pr_open", C.STATUS_INTERRUPTED),             # 远端已有 PR，对账收尾（非本轮 publish）
    ("orphan_deleted", C.STATUS_TRIAGED),
    ("stalled", C.STATUS_TRIAGED),
    ("triaged", C.STATUS_TRIAGED),
    ("blocked_external_state", C.STATUS_BLOCKED),
    ("fail", C.STATUS_TRIAGED),
])
def test_dev_post_reconcile_status_mapping(monkeypatch, rec_status, terminal):
    """reconcile_pr 设的各 status → graph terminal enum 映射（shadow parity）。"""
    import run_daily
    _mock_has_commits(monkeypatch, run_daily.found(False))   # 无 commit → 触发收尾路径
    _mock_reconcile(monkeypatch, rec_status, pr_url="https://x/pr/1" if rec_status == "pr_open" else None)
    upd = GN.node_dev_post(dict(_BASE))
    assert upd["terminal"] == terminal


# ── green evidence fail-closed（L2287-2295）──────────────────────────────
def test_dev_post_green_evidence_blocked(monkeypatch, tmp_path):
    """pass=True 但 artifact_store.store 失败 → terminal=blocked + pass 降级 False（不当 fresh green evidence）。"""
    import run_daily
    import artifact_store
    tlog = tmp_path / "test.log"; tlog.write_text("ok stdout\n")
    _mock_has_commits(monkeypatch, run_daily.found(True))
    _mock_independent(monkeypatch, {"pass": True, "test_rc": 0, "test_log": str(tlog), "test_cmd": "pytest"})
    monkeypatch.setattr(artifact_store, "store", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("磁盘满")))
    _mock_dump(monkeypatch)
    upd = GN.node_dev_post(dict(_BASE))
    assert upd["terminal"] == C.STATUS_BLOCKED
    assert upd["_verify_payload"]["pass"] is False            # fail-closed 降级
    assert "持久化失败" in upd["_skip_reason"]


def test_dev_post_green_evidence_no_reconcile(monkeypatch):
    """green evidence fail → 不调 reconcile_pr（对齐 L2287-2295 直接 break，不走 L2510-2512 收尾）。"""
    import run_daily
    import artifact_store
    _mock_has_commits(monkeypatch, run_daily.found(True))
    _mock_independent(monkeypatch, {"pass": True, "test_rc": 0, "test_cmd": "pytest"})
    monkeypatch.setattr(artifact_store, "store", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("fail")))
    called = []
    monkeypatch.setattr(run_daily, "reconcile_pr", lambda *a, **kw: called.append(1))
    GN.node_dev_post(dict(_BASE))
    assert called == []                                       # blocked_evidence 不对账


# ── 正常路径（写 _verify_payload → verify）──────────────────────────────
def test_dev_post_normal_pass_writes_payload(monkeypatch, tmp_path):
    """pass + evidence ok → 写 _verify_payload(+evidence_ref)/_diff_path/_base，不 terminal（进 verify）。"""
    import run_daily
    tlog = tmp_path / "test.log"; tlog.write_text("ok stdout\n")
    _mock_has_commits(monkeypatch, run_daily.found(True))
    _mock_independent(monkeypatch, {"pass": True, "test_rc": 0, "test_log": str(tlog), "test_cmd": "pytest"})
    _mock_store_ok(monkeypatch)
    _mock_dump(monkeypatch)
    upd = GN.node_dev_post(dict(_BASE))
    assert not upd.get("terminal")
    assert upd["_verify_payload"]["pass"] is True
    assert upd["_verify_payload"]["evidence_ref"]["digest"] == "sha256:abc"
    assert upd["_base"] == "main"                             # cur_base 写回喂 verify_prompt
    assert "r1.diff" in upd["_diff_path"]


def test_dev_post_red_test_no_evidence(monkeypatch):
    """红 test（pass=False）→ 不 store evidence，_verify_payload 无 evidence_ref，交 pa-verify 审。"""
    import run_daily
    import artifact_store
    _mock_has_commits(monkeypatch, run_daily.found(True))
    _mock_independent(monkeypatch, {"pass": False, "test_rc": 1, "test_log": None, "test_cmd": "pytest"})
    store_called = []
    monkeypatch.setattr(artifact_store, "store", lambda *a, **kw: store_called.append(1))
    _mock_dump(monkeypatch)
    upd = GN.node_dev_post(dict(_BASE))
    assert not upd.get("terminal")                            # 红交 verify，不 terminal
    assert store_called == []                                 # L2281 if pass 才 store
    assert "evidence_ref" not in upd["_verify_payload"]
    assert upd["_verify_payload"]["pass"] is False


# ── diff 路径形态（对齐 L2303）──────────────────────────────────────────
def test_dev_post_diff_path_form(monkeypatch):
    """diff 路径 = STATE_DIR/runs/<proj>/<stamp>_<slug>.r<round>.diff（对齐 L2303）。"""
    import run_daily
    _mock_has_commits(monkeypatch, run_daily.found(True))
    _mock_independent(monkeypatch, {"pass": False, "test_rc": 1, "test_cmd": "pytest"})
    sink = []
    _mock_dump(monkeypatch, sink=sink)
    GN.node_dev_post(dict(_BASE))
    assert sink == [str(run_daily.STATE_DIR / "runs" / "proj" / "20260811_x.r1.diff")]


def test_dev_post_round2_diff_name(monkeypatch):
    """verify_round=2 → diff 文件名 r2（round2 增量重投，base=round1 branch）。"""
    import run_daily
    _mock_has_commits(monkeypatch, run_daily.found(True))
    _mock_independent(monkeypatch, {"pass": False, "test_rc": 1, "test_cmd": "pytest"})
    sink = []
    _mock_dump(monkeypatch, sink=sink)
    s = dict(_BASE); s["verify_round"] = 2
    GN.node_dev_post(s)
    assert "r2.diff" in sink[0]


def test_dev_post_cur_base_round2(monkeypatch):
    """round2：_cur_base 覆盖 _base，写回 _verify_payload 路径的 base（对齐 L2472 cur_base=branch）。"""
    import run_daily
    _mock_has_commits(monkeypatch, run_daily.found(True))
    _mock_independent(monkeypatch, {"pass": False, "test_rc": 1, "test_cmd": "pytest"})
    _mock_dump(monkeypatch)
    s = dict(_BASE); s["_cur_base"] = "pa-dev-round1"; s["verify_round"] = 2
    upd = GN.node_dev_post(s)
    assert upd["_base"] == "pa-dev-round1"                    # 不是默认 main
