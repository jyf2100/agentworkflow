#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_graph_devloop.py — DevLoopNode 完整实装测试（任务 3.5a）。

验证：
① parse_dev_exit exit code→terminal 机械映射（utility 覆盖，对齐 dev-agent exit 0/14/15/12/未知）
② node_dev 配置（KIND_DEVLOOP, stage=dispatch）
③ dev 字段写入（对齐 dispatch_one L2232-2247：_dev_script/_branch/_dev_cost/_dev_turns/_dev_stalled/...）
④ terminal 映射（对齐 dispatch_one L2216-2259）：
   blocked_by_gate→blocked / off_track→triaged / cmd None·超时→triaged；正常/stalled/无JSON→不 terminal（交 dev_post）
⑤ cmd 形态（session_aware_retry flag-gate：off→baseline 不注入 state_dir/session；on→注入 iteration_seq/resume）
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import graph_pa_nodes as GN
import graph_pa_contracts as C


_BASE = {"run_id": "r", "thread_id": "t", "stamp": "s", "config": {},
         "_project": "proj", "_slug": "x", "_prof": {"conda_env": ""},
         "_base": "main", "_prd_abs": "/abs/prd.md", "_src_abs": "/abs/src.md",
         "_worktree_abs": "/wt", "_dev_log_file": None, "verify_round": 0}


def _mock_capture(monkeypatch, rc, stdout):
    import run_daily
    monkeypatch.setattr(run_daily, "_run_capture", lambda *a, **kw: (rc, stdout, ""))


_DEFAULT_CMD = ["py", "dev-agent.py", "--prd", "p", "--base", "main"]
_SENTINEL = object()


def _mock_devcmd(monkeypatch, returns=_SENTINEL):
    """mock run_daily._dev_cmd：默认返回有效 cmd；returns=None → build_cmd 返回 None（DEV_AGENT_PY 缺失）。"""
    import run_daily
    val = _DEFAULT_CMD if returns is _SENTINEL else returns
    monkeypatch.setattr(run_daily, "_dev_cmd", lambda *a, **kw: val)


# ── parse_dev_exit utility（exit code→terminal 机械映射）──────────────────
def test_parse_dev_exit_mapping():
    assert GN.parse_dev_exit(0) == (None, None)                                       # 正常
    assert GN.parse_dev_exit(GN.DEV_EXIT_TEST_GATE) == (C.STATUS_BLOCKED, C.ERR_TEST_GATE)
    assert GN.parse_dev_exit(GN.DEV_EXIT_OFF_TRACK) == (C.STATUS_TRIAGED, C.ERR_CONTRACT_VIOLATION)
    assert GN.parse_dev_exit(GN.DEV_EXIT_BRAKE) == (C.STATUS_TRIAGED, C.ERR_CONTRACT_VIOLATION)
    assert GN.parse_dev_exit(13) == (C.STATUS_TRIAGED, C.ERR_PERSONA_CRASH)           # 未知非 0→triaged


# ── node_dev 配置 ────────────────────────────────────────────────────────
def test_devloop_node_config():
    assert GN.node_dev._kind is GN.KIND_DEVLOOP
    assert GN.node_dev._cfg["stage"] == "dispatch"


# ── terminal 映射（对齐 dispatch_one L2216-2259）─────────────────────────
def test_devloop_normal_writes_state(monkeypatch):
    """正常 JSON → 写 dev 字段（_branch/_dev_cost/_dev_turns/...），不 terminal（交 dev_post）。"""
    _mock_devcmd(monkeypatch)
    _mock_capture(monkeypatch, 0, '{"ok":true,"branch":"pa-dev-x","cost":0.5,"turns":12,'
                                  '"stalled":false,"off_track":false,"run_log":"r.jsonl","test_cmd":"pytest"}')
    upd = GN.node_dev(dict(_BASE))
    assert upd["_branch"] == "pa-dev-x"
    assert upd["_dev_cost"] == 0.5
    assert upd["_dev_turns"] == 12
    assert upd["_dev_stalled"] is False
    assert upd["_dev_run_log"] == "r.jsonl"
    assert upd["_dev_test_cmd"] == "pytest"
    assert upd["_dev_killed"] is False
    assert not upd.get("terminal")                       # 正常 → 不 terminal


def test_devloop_blocked_by_gate(monkeypatch):
    """blocked_by_gate=True → terminal=blocked + _gate_status（dev exit 14，对齐 L2216-2231）。"""
    _mock_devcmd(monkeypatch)
    _mock_capture(monkeypatch, 14, '{"ok":false,"blocked_by_gate":true,"gate_status":"test_failed",'
                                   '"gate_reason":"r","test_status":"red","evidence_fresh":false,"branch":"pa-dev-x"}')
    upd = GN.node_dev(dict(_BASE))
    assert upd["terminal"] == C.STATUS_BLOCKED
    assert upd["_gate_status"] == "test_failed"
    assert upd["_test_status"] == "red"
    assert upd["_evidence_fresh"] is False
    assert upd["_branch"] == "pa-dev-x"                  # 门在 commit 前，分支已建


def test_devloop_off_track(monkeypatch):
    """off_track=True → terminal=triaged（dev exit 15 语义跑偏止损，对齐 L2251-2259）。"""
    _mock_devcmd(monkeypatch)
    _mock_capture(monkeypatch, 15, '{"ok":false,"off_track":true,"branch":"pa-dev-x",'
                                   '"judge_rounds":2,"last_verdict":"off_track"}')
    upd = GN.node_dev(dict(_BASE))
    assert upd["terminal"] == C.STATUS_TRIAGED
    assert upd["_dev_off_track"] is True


def test_devloop_stalled_no_terminal(monkeypatch):
    """stalled=True → _dev_stalled=True 但不 terminal（交 dev_post has_commits 判无 commit→reconcile_pr）。"""
    _mock_devcmd(monkeypatch)
    _mock_capture(monkeypatch, 12, '{"ok":false,"stalled":true,"branch":"pa-dev-x","cost":0.1,"turns":100}')
    upd = GN.node_dev(dict(_BASE))
    assert upd["_dev_stalled"] is True
    assert not upd.get("terminal")                       # stalled 不立即 terminal（对齐 dispatch_one 不 return）


def test_devloop_no_json_dev_killed(monkeypatch):
    """空 stdout / 坏 JSON → _dev_killed=True，不 terminal（交 dev_post：branch 缺失→reconcile_pr）。"""
    _mock_devcmd(monkeypatch)
    _mock_capture(monkeypatch, 99, '')                   # 无 stdout（崩/kill）
    upd = GN.node_dev(dict(_BASE))
    assert upd["_dev_killed"] is True
    assert upd["_dev_script"] is None
    assert not upd.get("terminal")                       # 对齐 dispatch_one：无 JSON→dev_killed→has_commits，不查 exit


def test_devloop_bad_json_dev_killed(monkeypatch):
    """末行非 JSON → _dev_killed=True（json.JSONDecodeError 容忍）。"""
    _mock_devcmd(monkeypatch)
    _mock_capture(monkeypatch, 0, 'not json at all')
    upd = GN.node_dev(dict(_BASE))
    assert upd["_dev_killed"] is True
    assert not upd.get("terminal")


def test_devloop_cmd_none_no_dev_agent(monkeypatch):
    """build_cmd 返回 None（DEV_AGENT_PY 缺失）→ terminal=triaged + _dev_fail=no_dev_agent。"""
    _mock_devcmd(monkeypatch, returns=None)
    upd = GN.node_dev(dict(_BASE))
    assert upd["terminal"] == C.STATUS_TRIAGED
    assert upd["_dev_fail"] == "no_dev_agent"


def test_devloop_timeout(monkeypatch):
    """_run_capture raise RuntimeError（wall-clock 超时）→ terminal=triaged + _dev_fail=timeout。"""
    import run_daily
    _mock_devcmd(monkeypatch)

    def boom(*a, **kw):
        raise RuntimeError("wall-clock 超时")
    monkeypatch.setattr(run_daily, "_run_capture", boom)
    upd = GN.node_dev(dict(_BASE))
    assert upd["terminal"] == C.STATUS_TRIAGED
    assert upd["_dev_fail"] == "timeout"


# ── cmd 形态（session_aware_retry flag-gate，对齐 dispatch_one L2200-2207）──
def test_devloop_cmd_form_baseline(monkeypatch):
    """baseline（无 _coord_flags / session_aware_retry off）→ _dev_cmd 不注入 state_dir/iteration_seq/resume。"""
    import run_daily
    captured = {}

    def fake_devcmd(prof, prd_abs, base, src_abs, **kw):
        captured.update(kw)
        return ["py", "dev-agent.py", "--prd", prd_abs, "--base", base]
    monkeypatch.setattr(run_daily, "_dev_cmd", fake_devcmd)
    _mock_capture(monkeypatch, 0, '{"ok":true}')
    GN.node_dev(dict(_BASE))                             # 无 _coord_flags → baseline
    assert "state_dir" not in captured
    assert "iteration_seq" not in captured
    assert "resume_session" not in captured
    assert "fork_session" not in captured
    assert captured.get("feedback_artifact") is None
    assert captured.get("lessons_artifact") is None


def test_devloop_cmd_form_session_aware(monkeypatch):
    """session_aware_retry on → _dev_cmd 注入 state_dir/iteration_seq/resume_session（对齐 L2200-2204）。"""
    import run_daily
    captured = {}

    def fake_devcmd(prof, prd_abs, base, src_abs, **kw):
        captured.update(kw)
        return ["py", "dev-agent.py"]
    monkeypatch.setattr(run_daily, "_dev_cmd", fake_devcmd)
    _mock_capture(monkeypatch, 0, '{"ok":true}')
    s = dict(_BASE)
    s["_coord_flags"] = types.SimpleNamespace(session_aware_retry=True)
    s["_cur_resume_session"] = "sess-abc"
    s["_cur_fork_session"] = False
    s["_fb_artifact"] = "/fb.md"
    s["_lessons_artifact"] = "/lessons.md"
    s["verify_round"] = 1
    GN.node_dev(s)
    assert captured["state_dir"] == str(run_daily.STATE_DIR)
    assert captured["iteration_seq"] == 2                 # verify_round 1 + 1 = 2（round2 retry）
    assert captured["resume_session"] == "sess-abc"
    assert captured["fork_session"] is False
    assert captured["feedback_artifact"] == "/fb.md"
    assert captured["lessons_artifact"] == "/lessons.md"


def test_devloop_cur_base_round2(monkeypatch):
    """round≥2：_cur_base 覆盖 _base（增量重投，上次 dev 分支作 base，对齐 dispatch_one L2472 cur_base=branch）。"""
    import run_daily
    captured = {}

    def fake_devcmd(prof, prd_abs, base, src_abs, **kw):
        captured["base"] = base
        return ["py", "dev-agent.py"]
    monkeypatch.setattr(run_daily, "_dev_cmd", fake_devcmd)
    _mock_capture(monkeypatch, 0, '{"ok":true}')
    s = dict(_BASE)
    s["_cur_base"] = "pa-dev-x"                           # round2 增量重投：base=上次 dev 分支
    GN.node_dev(s)
    assert captured["base"] == "pa-dev-x"                 # 不是默认 "main"
