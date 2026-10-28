#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_event_outlet_parity.py — verify 三态/acceptance 状态投射到事件出口（fix-event-outlet-verify-parity）。

覆盖四层：
    - reason 枚举单元（verify_stream）：六常量 + INVALID_REASONS 恰六值；derive_invalid_reason
      分支序（unresolved 优先于 anchor_violation，与 build_verify_report 人读 reason 同序；
      非 invalid → None）；build_verify_report 携带结构化 invalid_reason 字段（green/red None）；
    - invalid 构造点归一（run_daily）：agent 轨三分支（套件 load 失败 / worktree 建立失败 /
      会话墙钟超时）+ 机械轨超时 → out 携带 invalid_reason ∈ 枚举；dispatch 考卷分支经集成钉；
    - 事件投射（dispatch_one 集成，复用 test_verify_loop VL fixtures）：invalid 分流点 emit
      verdict=invalid + reason；语义审轨事件行回归锚（pass/revise 值域、无 reason 键）；
      acceptance_suite stored/absent 投射（_author_acceptance_for_gate 挂点）；
    - verify-report 双写（_double_write_report：env set/unset + 拷贝失败 fail-open）+
      agent 轨落卷链路集成（report 落盘后双写真发生）。

取舍记录：六成因 → 事件行 = 两层合盖（构造点单测钉 invalid_reason 值 + 分流点集成钉
invalid_reason → 事件行投射）；考卷不变式分支的构造点在 dispatch_one 内 → 直接集成测到行。
graph 轨无独立 emit 点（包装直调共享函数）→ inspect 源断言防回归。AAA，零 SDK。
跑：python3 -m pytest scripts/test_event_outlet_parity.py -q
"""
from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import acceptance_tests as AT  # noqa: E402
import verify_stream as VS  # noqa: E402
import run_daily  # noqa: E402

STAMP = "20261028"
PROJ = "cc-web-control"
SEVEN_REASONS = ("unresolved", "anchor_violation", "wallclock_timeout", "session_failed",
                 "suite_load_failed", "worktree_setup_failed", "exam_snapshot_mismatch")


# ── A. reason 枚举单元（verify_stream）──────────────────────────────

def test_invalid_reason_constants_and_enum():
    assert VS.REASON_UNRESOLVED == "unresolved"
    assert VS.REASON_ANCHOR_VIOLATION == "anchor_violation"
    assert VS.REASON_WALLCLOCK_TIMEOUT == "wallclock_timeout"
    assert VS.REASON_SESSION_FAILED == "session_failed"   # run 92 真机：claude 非零退出（max-turns/API 断）
    assert VS.REASON_SUITE_LOAD_FAILED == "suite_load_failed"
    assert VS.REASON_WORKTREE_SETUP_FAILED == "worktree_setup_failed"
    assert VS.REASON_EXAM_SNAPSHOT_MISMATCH == "exam_snapshot_mismatch"
    assert VS.INVALID_REASONS == frozenset(SEVEN_REASONS)


def _exec(cmd: str = "npm test", rc: int = 0):
    return VS.BashExecution(command=cmd, tool_use_id="t1", is_error=rc != 0, exit_code=rc, output="")


def test_derive_invalid_reason_branch_order():
    # 非 invalid（全匹配 + 两锚净）→ None（green/red 面不携带 reason）
    assert VS.derive_invalid_reason({"c": _exec()}, True, []) is None
    # unresolved 优先：unresolved 与锚 violation 并存 → unresolved（与 build_verify_report 人读 reason 同序）
    assert VS.derive_invalid_reason({"c": None, "g": _exec()}, False, ["f.js"]) == VS.REASON_UNRESOLVED
    # 锚 violation 两形态：工作区不净 / 注入件 digest 违规
    assert VS.derive_invalid_reason({"c": _exec()}, False, []) == VS.REASON_ANCHOR_VIOLATION
    assert VS.derive_invalid_reason({"c": _exec()}, True, ["test/a.js"]) == VS.REASON_ANCHOR_VIOLATION


def test_build_verify_report_carries_invalid_reason():
    kw = dict(run_id=STAMP, project="p", prd_id="s", iteration_id="i1", harness="jest",
              gate_cmd=None, gate_source="profile", stream_path="", session_meta={},
              env_spec="", env_observed={}, anchor_workspace_detail="")
    r_inv = VS.build_verify_report(matched={"c": None}, anchor_workspace_ok=True,
                                   anchor_digest_violations=[], **kw)
    assert r_inv["verdict"] == "invalid" and r_inv["invalid_reason"] == "unresolved"
    r_green = VS.build_verify_report(matched={"c": _exec()}, anchor_workspace_ok=True,
                                     anchor_digest_violations=[], **kw)
    assert r_green["verdict"] == "green" and r_green["invalid_reason"] is None


# ── B. invalid 构造点归一（run_daily）───────────────────────────────

def _store_a_suite(tmp_path, monkeypatch) -> dict:
    """真 store 一份 jest 套件（content-addressed），返回 suite_ref（supersedes=None 首版形态）。"""
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path / "state")
    files = {"test/ac.test.js": "test('ac-1', () => { expect(1).toBe(1); });"}
    return AT.store_suite(tmp_path / "state" / "acceptance_store", files,
                          prd_criteria_ids=["ac-1"], suite_criteria_ids=["ac-1"], harness="jest")


def test_agent_track_suite_load_failed_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path / "state")
    bad_ref = {"digest": "sha256:" + "f" * 64, "path": "sha256/ffffff/x", "size": 1}
    out = run_daily._agent_track_verify("/nonexistent-repo", "b1", STAMP, "s",
                                        tmp_path / "run.log", {}, None, bad_ref)
    assert out["verdict"] == "invalid"
    assert out["invalid_reason"] == VS.REASON_SUITE_LOAD_FAILED


def test_agent_track_worktree_setup_failed_reason(tmp_path, monkeypatch):
    ref = _store_a_suite(tmp_path, monkeypatch)
    # repo 目录存在但非 git 仓 → git worktree add rc≠0（_run_capture 真跑，不 mock）
    (tmp_path / "repo-not-git").mkdir()
    out = run_daily._agent_track_verify(str(tmp_path / "repo-not-git"), "b1", STAMP, "s",
                                        tmp_path / "run.log", {}, None, ref)
    assert out["verdict"] == "invalid"
    assert out["invalid_reason"] == VS.REASON_WORKTREE_SETUP_FAILED


def test_agent_track_wallclock_timeout_reason(tmp_path, monkeypatch):
    ref = _store_a_suite(tmp_path, monkeypatch)
    monkeypatch.setattr(run_daily, "_run_capture", lambda *a, **k: (0, "", ""))  # worktree add 成功

    class _Boom:
        def __init__(self, log=None):
            pass

        def run(self, *a, **k):
            raise run_daily.VA_MOD.VerifySessionTimeout("wall clock exceeded")

    monkeypatch.setattr(run_daily.VA_MOD, "ClaudeHeadlessExecutor", _Boom)
    out = run_daily._agent_track_verify(str(tmp_path), "b1", STAMP, "s",
                                        tmp_path / "run.log", {}, None, ref)
    assert out["verdict"] == "invalid"
    assert out["invalid_reason"] == VS.REASON_WALLCLOCK_TIMEOUT


def test_mechanical_track_timeout_reason(tmp_path, monkeypatch):
    """机械轨 flag off 默认路径：worktree add 成功 + 重放 test_cmd 抛 RuntimeError → invalid 同附 reason。"""
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path / "state")
    monkeypatch.delenv("PA_VERIFY_AGENT", raising=False)

    def _rc(cmd, *a, **k):
        if "worktree" in " ".join(str(c) for c in cmd):
            return (0, "", "")
        raise RuntimeError("TimeoutExpired")   # _run_capture 超时契约（TimeoutExpired→RuntimeError）

    monkeypatch.setattr(run_daily, "_run_capture", _rc)
    repo = tmp_path / "repo"; repo.mkdir()   # 无 package.json → Python 轨重放 test_cmd_hint
    out = run_daily.independent_verify(str(repo), "b1", STAMP, "s", tmp_path / "run.log",
                                       {}, test_cmd_hint="npm test", suite_ref=None)
    assert out["verdict"] == "invalid"
    assert out["invalid_reason"] == VS.REASON_WALLCLOCK_TIMEOUT


# ── C. 分流点/语义审轨事件投射（dispatch_one 集成）─────────────────

def _read_events(state_dir, proj, stamp, slug) -> list[dict]:
    f = Path(state_dir) / "runs" / proj / f"{stamp}_{slug}.events.jsonl"
    return [json.loads(ln) for ln in f.read_text(encoding="utf-8").splitlines()]


@pytest.mark.parametrize("reason", SEVEN_REASONS)
def test_dispatch_invalid_emits_verify_verdict_with_reason(tmp_path, monkeypatch, reason):
    """审核补（r2）：六成因值全参数化钉分流点透传——事件行 reason 必 ∈ INVALID_REASONS。"""
    import test_verify_loop as VL
    VL._setup(tmp_path, monkeypatch)
    VL._admit(monkeypatch)
    repo = VL._repo(tmp_path)
    monkeypatch.setattr(run_daily, "_run_dev_agent",
                        lambda *a, **k: {"branch": "auto/r1", "cost": 0.1, "turns": 5, "test_cmd": "npm test"})
    monkeypatch.setattr(run_daily, "_has_commits", lambda *a, **k: VL.found(True))
    monkeypatch.setattr(run_daily, "independent_verify",
                        lambda *a, **k: {"verdict": "invalid", "pass": False, "test_rc": None,
                                         "invalid_reason": reason,
                                         "test_log": None, "note": "构造点集成"})
    monkeypatch.setattr(run_daily, "run_persona",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("invalid 不应烧语义审")))
    _c, fake_rec = VL._recon_recorder()
    monkeypatch.setattr(run_daily, "reconcile_pr", fake_rec)
    rec = run_daily.dispatch_one(VL._entry(), VL._prof(repo), "20260718", VL._args())
    assert rec["status"] == "verify_invalid"
    lines = _read_events(run_daily.STATE_DIR, PROJ, "20260718", "20260718_test")
    assert len(lines) == 1   # invalid 分流在 pr_opened/语义审之前 → 事件文件只此一行
    assert lines[0]["event"] == "verify_verdict" and lines[0]["verdict"] == "invalid"
    assert lines[0]["reason"] == reason and lines[0]["round"] == 1


def _snap_ref(digest: str) -> dict:
    return {"digest": digest, "path": f"sha256/{digest[:6]}/x", "size": 9, "status": "stored",
            "criteria_ids": ["ac-1"], "uncovered": [], "harness": "jest",
            "files": [{"path": "test/a.test.js", "digest": digest}], "supersedes": None}


def test_dispatch_exam_mismatch_emits_event(tmp_path, monkeypatch):
    import test_verify_loop as VL
    VL._setup(tmp_path, monkeypatch)
    VL._admit(monkeypatch)
    repo = VL._repo(tmp_path)
    monkeypatch.setattr(run_daily, "_acceptance_ref_for",
                        lambda *a: _snap_ref("sha256:" + "a" * 64))
    monkeypatch.setattr(run_daily, "_run_dev_agent",
                        lambda *a, **k: {"branch": "auto/r1", "cost": 0.1, "turns": 5,
                                         "test_cmd": "npm test", "acceptance_digest": "sha256:" + "e" * 64})
    monkeypatch.setattr(run_daily, "_has_commits", lambda *a, **k: VL.found(True))
    monkeypatch.setattr(run_daily, "independent_verify",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不变式破坏应在 verify 前分流")))
    monkeypatch.setattr(run_daily, "run_persona",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("invalid 不进 revise")))
    _c, fake_rec = VL._recon_recorder()
    monkeypatch.setattr(run_daily, "reconcile_pr", fake_rec)
    rec = run_daily.dispatch_one(VL._entry(), VL._prof(repo), "20260718", VL._args())
    assert rec["status"] == "verify_invalid"
    lines = _read_events(run_daily.STATE_DIR, PROJ, "20260718", "20260718_test")
    assert lines[0]["event"] == "verify_verdict" and lines[0]["verdict"] == "invalid"
    assert lines[0]["reason"] == "exam_snapshot_mismatch"


def test_semantic_track_event_byte_anchor(tmp_path, monkeypatch):
    """回归锚：非 invalid 走语义审，事件行值域仍 pass/revise、无 reason 键（emit 形态零变化）。"""
    import test_verify_loop as VL
    VL._setup(tmp_path, monkeypatch)
    VL._admit(monkeypatch)
    repo = VL._repo(tmp_path)
    monkeypatch.setattr(run_daily, "_run_dev_agent",
                        lambda *a, **k: {"branch": "auto/r1", "cost": 0.1, "turns": 5, "test_cmd": "npm test"})
    monkeypatch.setattr(run_daily, "_has_commits", lambda *a, **k: VL.found(True))
    monkeypatch.setattr(run_daily, "independent_verify",
                        lambda *a, **k: {"verdict": "green", "pass": True, "test_rc": 0,
                                         "test_log": None, "note": "绿"})
    monkeypatch.setattr(run_daily, "_dump_branch_diff", lambda *a, **k: None)
    monkeypatch.setattr(run_daily, "run_persona",
                        lambda *a, **k: ({"verdict": "pass", "feedback_section": ""}, {"cost": 0.0, "turns": 1}))
    _c, fake_rec = VL._recon_recorder()
    monkeypatch.setattr(run_daily, "reconcile_pr", fake_rec)
    run_daily.dispatch_one(VL._entry(), VL._prof(repo), "20260718", VL._args())
    lines = _read_events(run_daily.STATE_DIR, PROJ, "20260718", "20260718_test")
    vlines = [ln for ln in lines if ln["event"] == "verify_verdict"]
    assert len(vlines) == 1
    assert vlines[0]["verdict"] == "pass" and vlines[0]["round"] == 1
    assert vlines[0]["summary"] == ""
    assert "reason" not in vlines[0]   # 语义审轨不携带 reason（byte 级锚）


# ── D. acceptance_suite 事件投射（_author_acceptance_for_gate）─────

def _gate_entry() -> dict:
    return {"project": PROJ, "prd_path": f"{STAMP}_a.md", "source_path": "s", "verdict": "pass"}


def test_acceptance_stored_event_first_version(tmp_path, monkeypatch):
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path / "state")
    stored = {"status": "stored", "prd_id": f"{STAMP}_a", "digest": "sha256:" + "a" * 64,
              "path": "sha256/aaaaaa/x", "size": 9, "criteria_ids": ["ac-1"], "uncovered": [],
              "harness": "jest", "files": [{"path": "test/a.test.js", "digest": "sha256:b"}],
              "supersedes": None}
    monkeypatch.setattr(run_daily, "_author_acceptance_suite", lambda *a, **k: dict(stored))
    run_daily._author_acceptance_for_gate([_gate_entry()], {}, STAMP)
    line = _read_events(tmp_path / "state", PROJ, STAMP, f"{STAMP}_a")[0]
    assert line["event"] == "acceptance_suite" and line["status"] == "stored"
    assert line["digest"] == stored["digest"] and line["criteria_ids"] == ["ac-1"]
    assert line["supersedes"] is None   # 首版：supersedes 显式 null


def test_acceptance_stored_event_supersedes_chain(tmp_path, monkeypatch):
    """换版形态：triage 确认重编写后新 stored 行 supersedes 非 null——消费方读链即见换版。"""
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path / "state")
    stored = {"status": "stored", "prd_id": f"{STAMP}_a", "digest": "sha256:" + "b" * 64,
              "path": "sha256/bbbbbb/x", "size": 9, "criteria_ids": ["ac-1"], "uncovered": [],
              "harness": "jest", "files": [], "supersedes": "sha256:" + "a" * 64}
    monkeypatch.setattr(run_daily, "_author_acceptance_suite", lambda *a, **k: dict(stored))
    run_daily._author_acceptance_for_gate([_gate_entry()], {}, STAMP)
    line = _read_events(tmp_path / "state", PROJ, STAMP, f"{STAMP}_a")[0]
    assert line["supersedes"] == "sha256:" + "a" * 64


@pytest.mark.parametrize("reason", ["no_criteria", "no_harness", "no_files", "self_check_failed",
                                    "store_failed", "refused", "author_failed"])
def test_acceptance_absent_event_reason_domain(tmp_path, monkeypatch, reason):
    """absent reason 七值 = 编排器落账 result 全集（record_absence 同名五 + persona 面两）。"""
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path / "state")
    absent = {"status": "absent", "reason": reason, "prd_id": f"{STAMP}_a", "detail": "d"}
    monkeypatch.setattr(run_daily, "_author_acceptance_suite", lambda *a, **k: dict(absent))
    run_daily._author_acceptance_for_gate([_gate_entry()], {}, STAMP)
    line = _read_events(tmp_path / "state", PROJ, STAMP, f"{STAMP}_a")[0]
    assert line["event"] == "acceptance_suite" and line["status"] == "absent"
    assert line["reason"] == reason and line["detail"] == "d"


def test_acceptance_gate_wiring_exception_still_emits(tmp_path, monkeypatch):
    """审核补（r2）：编写接线异常 fail-open 吞异常不得吞事件——absent/author_failed 行必落卷。"""
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path / "state")

    def _boom(*a, **k):
        raise RuntimeError("wiring exploded")

    monkeypatch.setattr(run_daily, "_author_acceptance_suite", _boom)
    run_daily._author_acceptance_for_gate([_gate_entry()], {}, STAMP)
    line = _read_events(tmp_path / "state", PROJ, STAMP, f"{STAMP}_a")[0]
    assert line["event"] == "acceptance_suite" and line["status"] == "absent"
    assert line["reason"] == "author_failed" and "wiring exploded" in line["detail"]


def test_graph_track_dispatch_emit_parity():
    """graph 轨 dispatch 段是子图独立分流（非包装直调 dispatch_one）——invalid 分流点须自持
    emit 且 facts 形态与 legacy 一致；组装层 graph_pa_dispatch 保持零业务 emit。
    （审核修正：旧断言只查组装文件 = 假保证——真分流逻辑在 graph_pa_nodes._dev_post_op。）"""
    import graph_pa_dispatch
    import graph_pa_nodes
    assert "append_event" not in inspect.getsource(graph_pa_dispatch)  # 组装层零业务 emit
    src = inspect.getsource(graph_pa_nodes._dev_post_op)
    assert 'EO.append_event' in src and '"verify_verdict"' in src      # invalid 分流点自持 emit
    assert "verdict=\"invalid\"" in src and "reason=vj.get(\"invalid_reason\")" in src
    assert "REASON_EXAM_SNAPSHOT_MISMATCH" in src                      # 考卷分支 vj 带 invalid_reason


# ── E. verify-report 双写（_double_write_report + agent 轨落卷链路）─

def test_double_write_report_env_set(tmp_path, monkeypatch):
    extra = tmp_path / "extra"; extra.mkdir()
    monkeypatch.setenv("PA_REPORT_DIR", str(extra))
    rp = tmp_path / "run.verify-report.json"; rp.write_text("{}", encoding="utf-8")
    run_daily._double_write_report(rp)
    assert (extra / rp.name).read_text(encoding="utf-8") == "{}"


def test_double_write_report_default_noop(tmp_path, monkeypatch):
    monkeypatch.delenv("PA_REPORT_DIR", raising=False)
    extra = tmp_path / "extra"
    rp = tmp_path / "run.verify-report.json"; rp.write_text("{}", encoding="utf-8")
    run_daily._double_write_report(rp)
    assert not extra.exists()   # 缺省零变化：出口目录根本不建


def test_double_write_report_fail_open(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PA_REPORT_DIR", str(tmp_path / "extra"))

    def _boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(run_daily.shutil, "copy2", _boom)
    rp = tmp_path / "run.verify-report.json"; rp.write_text("{}", encoding="utf-8")
    run_daily._double_write_report(rp)   # 不 raise
    assert "双写" in capsys.readouterr().err


def test_agent_track_report_double_write_integration(tmp_path, monkeypatch):
    """agent 轨全链（report 落盘点）：PA_REPORT_DIR 设置 → verify-report 双写；非 invalid 无 invalid_reason。"""
    ref = _store_a_suite(tmp_path, monkeypatch)
    extra = tmp_path / "extra"
    monkeypatch.setenv("PA_REPORT_DIR", str(extra))
    monkeypatch.setattr(run_daily, "_run_capture", lambda *a, **k: (0, "", ""))

    class _FakeExec:
        def __init__(self, log=None):
            pass

        def run(self, *a, **k):
            return ""

    monkeypatch.setattr(run_daily.VA_MOD, "ClaudeHeadlessExecutor", _FakeExec)
    monkeypatch.setattr(run_daily.VS_MOD, "parse_stream", lambda raw: ([], {}))
    monkeypatch.setattr(run_daily.VS_MOD, "match_declared", lambda execs, declared: {})
    monkeypatch.setattr(run_daily.VS_MOD, "check_workspace_clean", lambda vt, inj: (True, "ok"))
    monkeypatch.setattr(run_daily.VS_MOD, "check_injected_digests", lambda vt, dg: [])
    monkeypatch.setattr(run_daily.VS_MOD, "observed_env_exports", lambda execs: {})
    out = run_daily._agent_track_verify(str(tmp_path), "b1", STAMP, "s",
                                        tmp_path / "run.log", {}, None, ref)
    assert out["verdict"] == "green" and "invalid_reason" not in out   # 非 invalid 不携带
    assert out["verify_report"] and Path(out["verify_report"]).is_file()
    assert (extra / Path(out["verify_report"]).name).is_file()   # 双写落卷


@pytest.mark.parametrize("matched,clean,expect", [
    ({"c": None}, (True, "ok"), "unresolved"),                       # 有声明未匹配 → unresolved
    ({"c": _exec()}, (False, "工作区不净"), "anchor_violation"),       # 锚违规（工作区不净）
])
def test_agent_track_report_reason_propagation(tmp_path, monkeypatch, matched, clean, expect):
    """审核补（r2）：report.invalid_reason → out.invalid_reason 传播行贯通——unresolved/
    anchor_violation 两成因经 _agent_track_verify 全链直调钉住（此前仅构造点常量单测）。"""
    ref = _store_a_suite(tmp_path, monkeypatch)
    monkeypatch.setattr(run_daily, "_run_capture", lambda *a, **k: (0, "", ""))

    class _FakeExec:
        def __init__(self, log=None):
            pass

        def run(self, *a, **k):
            return ""

    monkeypatch.setattr(run_daily.VA_MOD, "ClaudeHeadlessExecutor", _FakeExec)
    monkeypatch.setattr(run_daily.VS_MOD, "parse_stream", lambda raw: ([], {}))
    monkeypatch.setattr(run_daily.VS_MOD, "match_declared", lambda execs, declared: matched)
    monkeypatch.setattr(run_daily.VS_MOD, "check_workspace_clean", lambda vt, inj: clean)
    monkeypatch.setattr(run_daily.VS_MOD, "check_injected_digests", lambda vt, dg: [])
    monkeypatch.setattr(run_daily.VS_MOD, "observed_env_exports", lambda execs: {})
    out = run_daily._agent_track_verify(str(tmp_path), "b1", STAMP, "s",
                                        tmp_path / "run.log", {}, None, ref)
    assert out["verdict"] == "invalid" and out["invalid_reason"] == expect
