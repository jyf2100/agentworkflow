#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_graph_verify.py — verify PersonaNode + dev↔dev_post↔verify↔redo 完整闭环子图测试（任务 3.4 + 3.5c）。

验证：
① node_verify 配置（KIND_PERSONA, expose_verdict=True 的 verdict 提取 + feedback_section→feedback）
② node_verify 调用形态 == _pa_verify_round L1420（agent/stage/label per-round/tools=None/prompt 同源）
③ install_log ArtifactHandle 传递通道（task 4.1 预留：handle 在 → prompt 追加段落；默认 byte-identical）
④ verify 子图拓扑（任务 3.5c 升级）：
   - dev terminal（blocked_by_gate/off_track）→ END（不进 dev_post/verify）
   - dev_post terminal（无 has_commits/green evidence fail）→ END（不进 verify）
   - verify pass → END（publication 接）；revise & round<MAX → redo → dev round2；用满 → interrupted_pr → END
   - redo：_append_verify_feedback + cur_base=branch + bump verify_round + session retry flag-gate
     （RESUME→_cur_resume_session；BLOCK/STOP→terminal triaged→END）
"""
import json
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import graph_pa_nodes as GN
import graph_pa_verify as GV
import graph_pa_contracts as C


# ── node_verify 配置 + verdict 提取 ──────────────────────────────────
def test_verify_node_config():
    assert GN.node_verify._kind is GN.KIND_PERSONA
    assert GN.node_verify._cfg["agent_name"] == "pa-verify"
    assert GN.node_verify._cfg["stage"] == "verify"


def _capture_persona(monkeypatch, captured, payload_override=None):
    import run_daily
    default_payload = {"verdict": "pass", "feedback_section": "", "summary": "ok", "round": 1}
    payload = payload_override or default_payload
    monkeypatch.setattr(run_daily, "run_persona",
                        lambda agent, prompt, stage, label, allowed_tools=None:
                        (captured.update(agent=agent, prompt=prompt, stage=stage,
                                         label=label, tools=allowed_tools) or
                         (dict(payload), {"cost": 0.1, "turns": 3})))


def test_verify_call_shape(monkeypatch):
    captured = {}
    _capture_persona(monkeypatch, captured)
    import run_daily
    prof = {"name": "x", "goal": "g"}
    GN.node_verify({"run_id": "r1", "stamp": "20260811", "config": {},
                    "_prd_path": "state/prd/x/p.md", "_branch": "feat-x", "_base": "main",
                    "_diff_path": "runs/x/20260811_x.r1.diff",
                    "_verify_payload": {"test_rc": 0, "test_log": "t.log"},
                    "_slug": "x", "_prof": prof, "verify_round": 1})
    assert captured["agent"] == "pa-verify"
    assert captured["stage"] == "verify"
    assert captured["label"] == "verify:x:r1"          # per-round label（对齐 _pa_verify_round L1420）
    assert captured["tools"] is None                   # pa-verify 只 Read，不限工具
    assert captured["prompt"] == run_daily.verify_prompt(
        "state/prd/x/p.md", "feat-x", "main", "runs/x/20260811_x.r1.diff",
        {"test_rc": 0, "test_log": "t.log"}, 1, prof)


def test_verify_exposes_verdict(monkeypatch):
    """verify 是 expose_verdict=True：payload.verdict 提取到 out['verdict']，feedback_section→feedback。"""
    captured = {}
    _capture_persona(monkeypatch, captured,
                     {"verdict": "revise",
                      "feedback_section": "①定位 X ②原因 Y ③怎么改 Z ④收尾门 全量绿",
                      "summary": "测试红", "round": 1})
    ni = {"run_id": "r9", "stamp": "s", "stage": "verify", "config": {},
          "_prd_path": "p.md", "_branch": "b", "_base": "main", "_diff_path": "d.diff",
          "_verify_payload": {}, "_slug": "x", "_prof": {}, "verify_round": 1}
    out, _ = GN.node_verify.invoke(ni, "prompt")
    assert out["verdict"]["value"] == "revise"         # expose_verdict=True 提取（条件边 route_verify 用）
    assert "定位" in out["verdict"]["feedback"]        # feedback_section → feedback（dev redo 注入）


def test_verify_verdict_mapper():
    """verify verdict_mapper：feedback_section→feedback（供 dev redo 注入）；summary→reason；缺 summary 回退 verdict。"""
    v = GN._verify_verdict_mapper({"verdict": "revise", "feedback_section": "fix X", "summary": "红"})
    assert v == {"value": "revise", "reason": "红", "feedback": "fix X"}
    v = GN._verify_verdict_mapper({"verdict": "pass"})  # 缺 summary → verdict 作 reason
    assert v["reason"] == "pass"
    assert v["feedback"] == ""


# ── install_log ArtifactHandle 传递通道（task 4.1 预留）────────────────
def test_install_log_none_byte_identical(monkeypatch):
    """无 _install_log → prompt == verify_prompt 原样（byte-identical，task 3.10 shadow parity 基线）。"""
    captured = {}
    _capture_persona(monkeypatch, captured)
    import run_daily
    prof = {"name": "x"}
    GN.node_verify({"run_id": "r", "stamp": "s", "config": {},
                    "_prd_path": "p.md", "_branch": "b", "_base": "main", "_diff_path": "d.diff",
                    "_verify_payload": {"test_rc": 0}, "_slug": "x", "_prof": prof, "verify_round": 1})
    assert captured["prompt"] == run_daily.verify_prompt(
        "p.md", "b", "main", "d.diff", {"test_rc": 0}, 1, prof)
    assert "install_log" not in captured["prompt"]


def test_install_log_handle_appends_segment(monkeypatch):
    """_install_log ArtifactHandle 在 → build_prompt 追加 [install_log artifact] 段落（传递通道）。"""
    captured = {}
    _capture_persona(monkeypatch, captured)
    base = {"run_id": "r", "stamp": "s", "config": {},
            "_prd_path": "p.md", "_branch": "b", "_base": "main", "_diff_path": "d.diff",
            "_verify_payload": {}, "_slug": "x", "_prof": {}, "verify_round": 1}
    s2 = dict(base)
    s2["_install_log"] = {"kind": "install_log", "store": "tmp",
                          "rel_path": "runs/x/install.log", "digest": "sha256:abc", "must_exist": True}
    GN.node_verify(s2)
    assert "[install_log artifact]" in captured["prompt"]
    assert "runs/x/install.log" in captured["prompt"]
    assert "tmp" in captured["prompt"]                    # store 标注（task 4.1 补 resolve 绝对）


# ── verify 子图拓扑（任务 3.5c：完整 dev↔dev_post↔verify↔redo 闭环）──────
_BASE = {"run_id": "r", "stamp": "20260811", "config": {},
         "_project": "proj", "_slug": "x", "_prof": {"conda_env": ""},
         "_worktree_abs": "/repo", "_owner_repo": "owner/repo",
         "_base": "main", "_prd_path": "state/prd/x/p.md", "_prd_abs": "/abs/prd.md",
         "_src_abs": "/abs/src.md", "_dev_log_file": None, "verify_round": 1}

_DEV_OK = {"ok": True, "branch": "pa-dev-x", "cost": 0.5, "turns": 12, "test_cmd": "pytest"}


def _mock_dev(monkeypatch, dev_json=None, dev_terminal=None):
    """mock dev node：_dev_cmd + _run_capture。dev_terminal=off_track/blocked → 模拟 dev terminal。"""
    import run_daily
    monkeypatch.setattr(run_daily, "_dev_cmd",
                        lambda *a, **kw: ["py", "dev-agent.py", "--prd", "p", "--base", "main"])
    if dev_terminal == "off_track":
        monkeypatch.setattr(run_daily, "_run_capture",
                            lambda *a, **kw: (15, '{"off_track": true, "branch": "pa-dev-x"}', ""))
    elif dev_terminal == "blocked":
        monkeypatch.setattr(run_daily, "_run_capture",
                            lambda *a, **kw: (14, '{"blocked_by_gate": true, "gate_status": "test_failed",'
                                                 ' "branch": "pa-dev-x"}', ""))
    else:
        j = dev_json or _DEV_OK
        monkeypatch.setattr(run_daily, "_run_capture", lambda *a, **kw: (0, json.dumps(j), ""))


def _mock_dev_post_ok(monkeypatch, test_pass=True):
    import run_daily
    import artifact_store
    monkeypatch.setattr(run_daily, "_has_commits", lambda *a, **kw: run_daily.found(True))
    monkeypatch.setattr(run_daily, "independent_verify",
                        lambda *a, **kw: {"pass": test_pass, "test_rc": 0 if test_pass else 1, "test_cmd": "pytest"})
    monkeypatch.setattr(artifact_store, "store",
                        lambda *a, **kw: types.SimpleNamespace(digest="sha256:abc", path="sha256/ab/c", size=10))
    monkeypatch.setattr(run_daily, "_dump_branch_diff", lambda *a, **kw: None)


def _mock_dev_post_no_commits(monkeypatch):
    import run_daily
    monkeypatch.setattr(run_daily, "_has_commits", lambda *a, **kw: run_daily.found(False))

    def fake_reconcile(repo, owner_repo, rec, base, slug, interrupted=True):
        rec["status"] = "orphan_deleted"
    monkeypatch.setattr(run_daily, "reconcile_pr", fake_reconcile)


def _mock_dev_post_blocked_evidence(monkeypatch):
    import run_daily
    import artifact_store
    monkeypatch.setattr(run_daily, "_has_commits", lambda *a, **kw: run_daily.found(True))
    monkeypatch.setattr(run_daily, "independent_verify",
                        lambda *a, **kw: {"pass": True, "test_rc": 0, "test_cmd": "pytest"})
    monkeypatch.setattr(artifact_store, "store",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("磁盘满")))
    monkeypatch.setattr(run_daily, "_dump_branch_diff", lambda *a, **kw: None)


def _mock_verify_seq(monkeypatch, verdicts):
    """pa-verify 按序消费 verdicts（revise→feedback 'fix'）。"""
    import run_daily
    it = iter(verdicts)

    def fake(agent, prompt, stage, label, allowed_tools=None):
        if agent == "pa-verify":
            v = next(it)
            return ({"verdict": v, "feedback_section": "fix" if v == "revise" else "",
                     "summary": v, "round": 1}, {"cost": 0.1, "turns": 3})
        return ({"prds": []}, {"cost": 0.2, "turns": 5})
    monkeypatch.setattr(run_daily, "run_persona", fake)


def _no_prd_write(monkeypatch):
    import run_daily
    monkeypatch.setattr(run_daily, "_append_verify_feedback", lambda *a, **kw: None)


# ── dev terminal 各出口 ─────────────────────────────────────────────
def test_subgraph_dev_blocked_test_gate(monkeypatch):
    """dev blocked_by_gate → terminal=blocked → END（不进 dev_post/verify）。"""
    _mock_dev(monkeypatch, dev_terminal="blocked")
    result = GV.build_verify_subgraph().invoke(dict(_BASE))
    assert result["terminal"] == C.STATUS_BLOCKED


def test_subgraph_dev_off_track(monkeypatch):
    """dev off_track → terminal=triaged → END（语义跑偏止损）。"""
    _mock_dev(monkeypatch, dev_terminal="off_track")
    result = GV.build_verify_subgraph().invoke(dict(_BASE))
    assert result["terminal"] == C.STATUS_TRIAGED


# ── dev_post terminal 各出口 ────────────────────────────────────────
def test_subgraph_dev_post_no_commits_terminal(monkeypatch):
    """dev ok → dev_post 无 has_commits → reconcile orphan_deleted→triaged → END（不进 verify）。"""
    _mock_dev(monkeypatch)
    _mock_dev_post_no_commits(monkeypatch)
    result = GV.build_verify_subgraph().invoke(dict(_BASE))
    assert result["terminal"] == C.STATUS_TRIAGED


def test_subgraph_dev_post_blocked_evidence(monkeypatch):
    """dev ok → dev_post green evidence 持久化失败 → terminal=blocked → END。"""
    _mock_dev(monkeypatch)
    _mock_dev_post_blocked_evidence(monkeypatch)
    result = GV.build_verify_subgraph().invoke(dict(_BASE))
    assert result["terminal"] == C.STATUS_BLOCKED


# ── verify pass / revise 回环 / 用满 ─────────────────────────────────
def test_subgraph_pass_flow(monkeypatch):
    """dev→dev_post→verify pass → END（无 terminal，publication 接）。"""
    _mock_dev(monkeypatch)
    _mock_dev_post_ok(monkeypatch)
    _mock_verify_seq(monkeypatch, ["pass"])
    result = GV.build_verify_subgraph().invoke(dict(_BASE))
    assert result["_verify_verdict"] == "pass"
    assert not result.get("terminal")


def test_subgraph_revise_loop_round2(monkeypatch):
    """verify revise → redo → dev round2 → dev_post → verify pass（1 次 redo 回环）。"""
    _mock_dev(monkeypatch)
    _mock_dev_post_ok(monkeypatch)
    _mock_verify_seq(monkeypatch, ["revise", "pass"])
    _no_prd_write(monkeypatch)
    result = GV.build_verify_subgraph().invoke(dict(_BASE))
    assert result["verify_round"] == 2                 # redo bump
    assert result["_verify_verdict"] == "pass"
    assert len(result["entries"]) == 2


def test_subgraph_revise_exhausted_interrupted(monkeypatch):
    """round1 revise → redo → round2 仍 revise，verify_round=2=MAX → terminal=interrupted_pr。"""
    _mock_dev(monkeypatch)
    _mock_dev_post_ok(monkeypatch)
    _mock_verify_seq(monkeypatch, ["revise", "revise"])
    _no_prd_write(monkeypatch)
    result = GV.build_verify_subgraph().invoke(dict(_BASE))
    assert result["verify_round"] == 2
    assert result["terminal"] == C.STATUS_INTERRUPTED
    assert result["terminal"] == "interrupted_pr"      # enum 字面值（对齐 L2510-2512）


# ── redo 行为（feedback 暂存 + cur_base + bump + session retry）──────────
def test_subgraph_redo_carries_feedback_and_base(monkeypatch):
    """redo：暂存 round1 revise feedback → _redo_feedback + _cur_base=branch + bump round（baseline，flag off）。"""
    _mock_dev(monkeypatch)
    _mock_dev_post_ok(monkeypatch)
    _mock_verify_seq(monkeypatch, ["revise", "pass"])
    _no_prd_write(monkeypatch)
    result = GV.build_verify_subgraph().invoke(dict(_BASE))
    assert result.get("_redo_feedback") == "fix"       # round1 revise feedback
    assert result["_cur_base"] == "pa-dev-x"           # round1 branch 作 round2 base（L2472）
    assert result["verify_round"] == 2


def test_subgraph_redo_session_retry_resume(monkeypatch):
    """session_aware_retry on + recover_iteration RESUME → _cur_resume_session 设（对齐 L2497-2499）。"""
    import reconcile
    import retry_policy as RP
    _mock_dev(monkeypatch)
    _mock_dev_post_ok(monkeypatch)
    _mock_verify_seq(monkeypatch, ["revise", "pass"])
    _no_prd_write(monkeypatch)
    rplan = types.SimpleNamespace(
        decision=types.SimpleNamespace(mode=RP.RetryMode.RESUME, consumes_retry=True, reason="局部反馈"),
        reconciliation=types.SimpleNamespace(external_known=True), iteration_status="revise")
    monkeypatch.setattr(reconcile, "recover_iteration", lambda **kw: rplan)
    flags = types.SimpleNamespace(session_aware_retry=True)
    coord = types.SimpleNamespace(
        resolver=None,
        session_store=types.SimpleNamespace(load=lambda i: types.SimpleNamespace(session_id="sess-1")),
        retry_budget=types.SimpleNamespace(consume=lambda dim: None))
    s = dict(_BASE); s["_coord_flags"] = flags; s["_coord"] = coord
    s["_sj"] = types.SimpleNamespace(path="/j.jsonl")
    result = GV.build_verify_subgraph().invoke(s)
    assert result.get("_cur_resume_session") == "sess-1"
    assert result.get("_retry_mode") == "resume"


def test_subgraph_redo_session_retry_block(monkeypatch):
    """session_aware_retry on + recover_iteration BLOCK → terminal=triaged → END（不重试，对齐 L2491-2495）。"""
    import reconcile
    import retry_policy as RP
    _mock_dev(monkeypatch)
    _mock_dev_post_ok(monkeypatch)
    _mock_verify_seq(monkeypatch, ["revise"])          # redo BLOCK 终止，不到 round2
    _no_prd_write(monkeypatch)
    rplan = types.SimpleNamespace(
        decision=types.SimpleNamespace(mode=RP.RetryMode.BLOCK, consumes_retry=False, reason="unknown"),
        reconciliation=types.SimpleNamespace(external_known=False), iteration_status="blocked")
    monkeypatch.setattr(reconcile, "recover_iteration", lambda **kw: rplan)
    flags = types.SimpleNamespace(session_aware_retry=True)
    coord = types.SimpleNamespace(
        resolver=None, session_store=types.SimpleNamespace(load=lambda i: None),
        retry_budget=types.SimpleNamespace(consume=lambda dim: None))
    s = dict(_BASE); s["_coord_flags"] = flags; s["_coord"] = coord
    s["_sj"] = types.SimpleNamespace(path="/j.jsonl")
    result = GV.build_verify_subgraph().invoke(s)
    assert result["terminal"] == C.STATUS_TRIAGED      # BLOCK → 升人工
    assert result["_retry_mode"] == "block"
