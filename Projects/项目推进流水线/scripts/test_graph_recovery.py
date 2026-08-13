#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_graph_recovery.py — graph 崩溃恢复测试（task 3.8，D2 恢复侧 + r-review P0/P1 闭环）。

覆盖 graph_pa_recovery 的崩溃恢复链：recover_iteration（mock）判 external_known + RetryPolicy.decide
→ 重建 DispatchSubState → _decide_action 5 态 → resume_dispatch 续跑或返信号。

r-review（4 专家）P0/P1 闭环：
- P0 入口校验：_REQUIRED_SHELL 缺键 fail-closed（防生产 KeyError:_prof）
- P0 spec 硬要求：journal 损坏 → JournalCorruptionError 传播（fail-closed）测试锁
- P0 fail-closed：无效 iteration_status（非 enum）→ MANUAL_BLOCK，不静默续跑
- P0 TOCTOU：RESUME 二次 session_store.load 返 None / 无 session_id → RuntimeError，不静默降级 NEW_SESSION
- P0 immutability：_apply_session_params 返新 dict 不 mutate 入参
- P1 契约：resume_dispatch 返 ResumeOutcome dataclass（对称 action/plan/reason/result）
- P1 覆盖：targets 非空真 reconcile 路径 + 真 build_dispatch_subgraph().invoke 集成
- _BASE 对齐 test_graph_dispatch_e2e._BASE（含 _prof/_src_abs/_dev_log_file/verify_round）
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import graph_pa_recovery as GR
import retry_policy as RP


# ── mock 辅助 ─────────────────────────────────────────────────────────
def _coord(*, session_store=None, resolver=None, retry_budget=None,
           iteration_id="iter1", prd_id="prd1"):
    """最小 journal-driven coord mock（session_store/resolver/retry_budget 默认非 None）。

    对齐 coordinator.Coordinator 消费面（_RecoveryCoord Protocol）：prd_id/iteration_id/journal.path/
    flags/resolver/session_store/retry_budget；baseline coord 三件 None 用 test_rejects_baseline_coord 验证。
    """
    if session_store is None:
        session_store = types.SimpleNamespace(
            load=lambda i: types.SimpleNamespace(session_id="sess-resumed"))
    if resolver is None:
        resolver = types.SimpleNamespace()
    if retry_budget is None:
        retry_budget = types.SimpleNamespace(consume=lambda d: None)
    return types.SimpleNamespace(
        flags=types.SimpleNamespace(session_aware_retry=True),
        journal=types.SimpleNamespace(path="/j.jsonl"),
        iteration_id=iteration_id, prd_id=prd_id,
        resolver=resolver, session_store=session_store, retry_budget=retry_budget)


def _plan(*, iteration_status="running", mode=RP.RetryMode.RESUME,
          external_known=True, reason="ok", consumes_retry=True):
    """mock RecoveryPlan（SimpleNamespace 模拟 reconcile.RecoveryPlan 的 4 字段消费面）。

    recover_dispatch/_decide_action/_apply_session_params 只读 .iteration_status / .decision.{mode,
    reason,consumes_retry} / .reconciliation.external_known —— 不依赖真 RecoveryPlan 构造。
    """
    return types.SimpleNamespace(
        decision=types.SimpleNamespace(mode=mode, reason=reason, consumes_retry=consumes_retry),
        reconciliation=types.SimpleNamespace(external_known=external_known),
        iteration_status=iteration_status,
        context=types.SimpleNamespace())


# _BASE 对齐 test_graph_dispatch_e2e._BASE（含 dispatch_subgraph 全部所需 shell 字段，r-review P0 H2）
_BASE = {"run_id": "r", "stamp": "20260811", "config": {}, "_project": "p", "_slug": "s",
         "_base": "main", "_worktree_abs": "/repo", "_owner_repo": "owner/repo",
         "_prd_path": "p.md", "_prd_abs": "/abs/p.md",
         "_prof": {"conda_env": "", "admission": True, "dev_agent_ready": True,
                   "type": "code", "max_prs_in_flight": 2, "repo": "owner/repo"},
         "_src_abs": "/abs/src.md", "_dev_log_file": None, "verify_round": 1}


# ── A. recover_dispatch：_decide_action 5 态（mock recover_iteration）────────
def test_completed_when_published(monkeypatch):
    """iteration_status=published → action=completed（D5/D6②：用 plan.iteration_status 不读 last_transition_error）。"""
    import reconcile
    monkeypatch.setattr(reconcile, "recover_iteration",
                        lambda **kw: _plan(iteration_status="published"))
    d = GR.recover_dispatch(journal_path="/j.jsonl", shell_inputs=dict(_BASE),
                            coord=_coord(), targets=[], prd_content="prd")
    assert d.action == GR.COMPLETED
    assert "published" in d.reason


def test_terminal_when_failed(monkeypatch):
    """iteration_status=failed（终态非 published）→ action=terminal（不续跑，失败已落定）。"""
    import reconcile
    monkeypatch.setattr(reconcile, "recover_iteration",
                        lambda **kw: _plan(iteration_status="failed", mode=RP.RetryMode.STOP))
    d = GR.recover_dispatch(journal_path="/j.jsonl", shell_inputs=dict(_BASE),
                            coord=_coord(), targets=[], prd_content="prd")
    assert d.action == GR.TERMINAL


def test_manual_block_when_external_unknown(monkeypatch):
    """非终态 + decision.mode=BLOCK（external_known=False）→ action=manual_block（需运维 reconcile）。"""
    import reconcile
    monkeypatch.setattr(reconcile, "recover_iteration",
                        lambda **kw: _plan(iteration_status="running", mode=RP.RetryMode.BLOCK,
                                           external_known=False, reason="external unknown"))
    d = GR.recover_dispatch(journal_path="/j.jsonl", shell_inputs=dict(_BASE),
                            coord=_coord(), targets=[], prd_content="prd")
    assert d.action == GR.MANUAL_BLOCK


def test_budget_exhausted_when_stop(monkeypatch):
    """非终态 + decision.mode=STOP（预算耗尽）→ action=budget_exhausted（需运维）。"""
    import reconcile
    monkeypatch.setattr(reconcile, "recover_iteration",
                        lambda **kw: _plan(iteration_status="running", mode=RP.RetryMode.STOP,
                                           reason="budget exhausted"))
    d = GR.recover_dispatch(journal_path="/j.jsonl", shell_inputs=dict(_BASE),
                            coord=_coord(), targets=[], prd_content="prd")
    assert d.action == GR.BUDGET_EXHAUSTED


def test_resume_action_for_resume_fork_new_modes(monkeypatch):
    """非终态 + decision.mode ∈ {RESUME, FORK, NEW_SESSION} → action=resume。"""
    import reconcile
    for mode in (RP.RetryMode.RESUME, RP.RetryMode.FORK, RP.RetryMode.NEW_SESSION):
        monkeypatch.setattr(reconcile, "recover_iteration",
                            lambda m=mode, **kw: _plan(iteration_status="verifying", mode=m))
        d = GR.recover_dispatch(journal_path="/j.jsonl", shell_inputs=dict(_BASE),
                                coord=_coord(), targets=[], prd_content="prd")
        assert d.action == GR.RESUME, f"{mode.value} 应映射到 resume"


def test_invalid_iteration_status_fail_closed(monkeypatch):
    """无效 iteration_status（非 enum 值，暗示 reducer/journal 损坏）→ MANUAL_BLOCK，不静默续跑（r-review P0）。"""
    import reconcile
    monkeypatch.setattr(reconcile, "recover_iteration",
                        lambda **kw: _plan(iteration_status="bogus_status_xyz", mode=RP.RetryMode.RESUME))
    d = GR.recover_dispatch(journal_path="/j.jsonl", shell_inputs=dict(_BASE),
                            coord=_coord(), targets=[], prd_content="prd")
    assert d.action == GR.MANUAL_BLOCK
    assert "bogus_status_xyz" in d.reason


def test_rejects_baseline_coord(monkeypatch):
    """baseline coord（session_store/resolver/retry_budget 全 None）→ ValueError（崩溃恢复需 journal-driven）。"""
    import pytest
    import reconcile
    monkeypatch.setattr(reconcile, "recover_iteration", lambda **kw: _plan())
    baseline = types.SimpleNamespace(
        flags=types.SimpleNamespace(session_aware_retry=False),
        journal=types.SimpleNamespace(path="/j.jsonl"),
        iteration_id="i", prd_id="p",
        resolver=None, session_store=None, retry_budget=None)
    with pytest.raises(ValueError, match="journal-driven"):
        GR.recover_dispatch(journal_path="/j.jsonl", shell_inputs=dict(_BASE),
                            coord=baseline, targets=[], prd_content="prd")


def test_rejects_missing_shell_inputs(monkeypatch):
    """shell_inputs 缺 _REQUIRED_SHELL 任一键 → ValueError fail-closed（防生产 KeyError:_prof，r-review P0）。"""
    import pytest
    import reconcile
    monkeypatch.setattr(reconcile, "recover_iteration", lambda **kw: _plan())
    incomplete = {"run_id": "r", "stamp": "s", "_base": "main"}  # 缺绝大多数 _REQUIRED_SHELL 键
    with pytest.raises(ValueError, match="缺必填字段"):
        GR.recover_dispatch(journal_path="/j.jsonl", shell_inputs=incomplete,
                            coord=_coord(), targets=[], prd_content="prd")


def test_journal_corruption_propagates_recover(monkeypatch):
    """journal 中部损坏 → JournalCorruptionError 传播（fail-closed，spec 硬要求；r-review P0 测试锁）。"""
    import pytest
    import journal
    import reconcile
    def _boom(**kw):
        raise journal.JournalCorruptionError("bad line 42")
    monkeypatch.setattr(reconcile, "recover_iteration", _boom)
    with pytest.raises(journal.JournalCorruptionError):
        GR.recover_dispatch(journal_path="/j.jsonl", shell_inputs=dict(_BASE),
                            coord=_coord(), targets=[], prd_content="prd")


# ── B. _rebuild_dispatch_state（D2 分层）──────────────────────────────
def test_rebuild_dispatch_state_fields():
    """shell_inputs + coord → DispatchSubState 字段齐全（shell 原样 + coord 派生 + recovery dict）。"""
    coord = _coord()
    plan = _plan(iteration_status="verifying", mode=RP.RetryMode.RESUME, external_known=True)
    state = GR._rebuild_dispatch_state(dict(_BASE), coord, plan)
    # shell 输入原样保留（含 _prof 等）
    assert state["_base"] == "main" and state["_project"] == "p" and state["run_id"] == "r"
    assert state["_prof"]["repo"] == "owner/repo"
    # coord 派生（运行期对象，不入 journal，重建时回填）
    assert state["_coord"] is coord
    assert state["_coord_flags"] is coord.flags
    assert state["_sj"] is coord.journal
    assert state["_iter"] == "iter1" and state["_prd"] == "prd1"
    assert state["_journal_path"] == "/j.jsonl"
    # recovery 上下文（节点观测 + 续跑追溯）
    assert state["recovery"]["iteration_status"] == "verifying"
    assert state["recovery"]["decision_mode"] == "resume"
    assert state["recovery"]["external_known"] is True


# ── C. resume_dispatch 端到端（续跑调度，返 ResumeOutcome）─────────────
def test_resume_completed_does_not_invoke(monkeypatch):
    """已完成（published）→ 不 invoke dispatch_subgraph，返 ResumeOutcome(completed, result=None)。"""
    import reconcile
    invoked = []
    monkeypatch.setattr(reconcile, "recover_iteration",
                        lambda **kw: _plan(iteration_status="published"))
    import graph_pa_dispatch as GD
    monkeypatch.setattr(GD, "build_dispatch_subgraph",
                        lambda: types.SimpleNamespace(invoke=lambda s: invoked.append(s) or {}))
    r = GR.resume_dispatch(journal_path="/j.jsonl", shell_inputs=dict(_BASE),
                           coord=_coord(), targets=[], prd_content="prd")
    assert r.action == GR.COMPLETED
    assert r.result is None                          # 不 re-invoke（已完成无需续跑）
    assert r.reason and r.plan                       # 对称契约：所有路径填 action/plan/reason
    assert invoked == []


def test_resume_manual_block_does_not_invoke(monkeypatch):
    """external_known=False（BLOCK）→ manual_block，不 invoke（盲目 re-invoke 违反 fail-safe）。"""
    import reconcile
    invoked = []
    monkeypatch.setattr(reconcile, "recover_iteration",
                        lambda **kw: _plan(iteration_status="running", mode=RP.RetryMode.BLOCK,
                                           external_known=False))
    import graph_pa_dispatch as GD
    monkeypatch.setattr(GD, "build_dispatch_subgraph",
                        lambda: types.SimpleNamespace(invoke=lambda s: invoked.append(s) or {}))
    r = GR.resume_dispatch(journal_path="/j.jsonl", shell_inputs=dict(_BASE),
                           coord=_coord(), targets=[], prd_content="prd")
    assert r.action == GR.MANUAL_BLOCK
    assert r.result is None
    assert invoked == []


def test_resume_re_invokes_with_session_params(monkeypatch):
    """resume（非终态 + RESUME）→ _apply_session_params + invoke dispatch_subgraph（D3 re-invoke from START）。

    验证传给 invoke 的 state 含 session 参数（_cur_resume_session）+ recovery dict + _journal_path。
    """
    import reconcile
    captured = {}
    monkeypatch.setattr(reconcile, "recover_iteration",
                        lambda **kw: _plan(iteration_status="verifying", mode=RP.RetryMode.RESUME))
    import graph_pa_dispatch as GD
    monkeypatch.setattr(GD, "build_dispatch_subgraph",
                        lambda: types.SimpleNamespace(
                            invoke=lambda s: captured.update(s) or {"_exit_status": "pr_open"}))
    r = GR.resume_dispatch(journal_path="/j.jsonl", shell_inputs=dict(_BASE),
                           coord=_coord(), targets=[], prd_content="prd")
    assert r.action == GR.RESUME
    assert r.result == {"_exit_status": "pr_open"}   # ResumeOutcome.result
    # invoke 收到的 state 含 _apply_session_params 注入的 session 参数
    assert captured["_cur_resume_session"] == "sess-resumed"
    assert captured["_retry_mode"] == "resume"
    # + _rebuild_dispatch_state 的 recovery dict + coord 派生
    assert captured["recovery"]["iteration_status"] == "verifying"
    assert captured["_journal_path"] == "/j.jsonl"


def test_resume_journal_corruption_propagates(monkeypatch):
    """resume_dispatch 链路也传播 JournalCorruptionError（双层 fail-closed 不漏，r-review P0）。"""
    import pytest
    import journal
    import reconcile
    def _boom(**kw):
        raise journal.JournalCorruptionError("bad line 7")
    monkeypatch.setattr(reconcile, "recover_iteration", _boom)
    with pytest.raises(journal.JournalCorruptionError):
        GR.resume_dispatch(journal_path="/j.jsonl", shell_inputs=dict(_BASE),
                           coord=_coord(), targets=[], prd_content="prd")


def test_resume_re_invoke_real_dispatch_subgraph(monkeypatch):
    """resume → 真 build_dispatch_subgraph().invoke（不 mock invoke），验证 _rebuild_dispatch_state 产的
    state 含 dispatch_subgraph 全部所需字段（_prof 等），不 KeyError（r-review P1 H2 契约验证）。

    恢复 coord 是 journal-driven（session_aware_retry on + resolver 非 None）→ publication_reconcile 门控
    为真走真实 reconcile_side_effects（_publication_reconcile_op L830-832）。mock 它返 safe_to_retry=True
    模拟「合法恢复：external_known 已确认、无 unknown 副作用」→ publication_reconcile pass → publish_baseline
    → pr_open（D3：publish 靠 reconcile external_known；fail-safe 阻断 UNKNOWN 留 test_e2e_admission_blocked）。
    """
    import test_graph_dispatch_e2e as E2E
    E2E._mock_admission_pass(monkeypatch)
    E2E._capture_subprocess(monkeypatch)
    E2E._mock_dev_post_pass(monkeypatch)
    E2E._mock_verify_seq(monkeypatch, ["pass"])
    E2E._mock_publish_pr_open(monkeypatch)
    captured = {}
    E2E._mock_sj(monkeypatch, captured)

    import reconcile
    monkeypatch.setattr(reconcile, "recover_iteration",
                        lambda **kw: _plan(iteration_status="verifying", mode=RP.RetryMode.RESUME))
    # 恢复 coord 触发 publication_reconcile 真实对账；mock 合法恢复（safe_to_retry=True）
    monkeypatch.setattr(reconcile, "reconcile_side_effects",
                        lambda **kw: types.SimpleNamespace(
                            confirmed=[], pending=[], unknown=[],
                            safe_to_retry=True, external_known=True))

    s = dict(_BASE)
    s["_sj"] = types.SimpleNamespace(path="/j.jsonl")
    r = GR.resume_dispatch(journal_path="/j.jsonl", shell_inputs=s,
                           coord=_coord(), targets=[], prd_content="prd")
    assert r.action == GR.RESUME
    assert r.result["_exit_status"] == "pr_open"      # 真 re-invoke 合法恢复 → pr_open（无 KeyError）


def test_resume_with_nonempty_targets_exercises_reconcile(monkeypatch):
    """targets 非空 → 真 reconcile 路径被触达（r-review P1 H3：D3 exactly-once 契约，非空 targets 卫生）。

    targets=[] 让 reconcile 报 external_known=True 假安全；非空 targets 验证 recover_iteration 收到
    targets 透传（mock 捕获 kw），生产调用方须构造全副作用幂等键。
    """
    import reconcile
    captured_kw = {}
    def _cap(**kw):
        captured_kw.update(kw)
        return _plan(iteration_status="verifying", mode=RP.RetryMode.RESUME)
    monkeypatch.setattr(reconcile, "recover_iteration", _cap)
    targets = [types.SimpleNamespace(kind="push", target="pa-dev-x"),
               types.SimpleNamespace(kind="pr", target="owner:pa-dev-x")]
    GR.recover_dispatch(journal_path="/j.jsonl", shell_inputs=dict(_BASE),
                        coord=_coord(), targets=targets, prd_content="prd")
    assert captured_kw["targets"] is targets          # targets 透传到 recover_iteration


# ── D. _apply_session_params（D3 session 映射 + immutability + TOCTOU，对齐 _redo_session_retry）──
def test_apply_session_params_resume_sets_resume_session():
    """RESUME → _cur_resume_session = session_store.load(iter).session_id；返新 dict 不 mutate 入参。"""
    state = {"existing": 1}
    coord = _coord()                           # session_store.load → session_id="sess-resumed"
    plan = _plan(mode=RP.RetryMode.RESUME, consumes_retry=True)
    out = GR._apply_session_params(state, plan, coord)
    assert out["_retry_mode"] == "resume"
    assert out["_cur_resume_session"] == "sess-resumed"
    assert "_cur_fork_session" not in out
    # immutability（r-review P0）：返新 dict，入参不被 mutate
    assert state == {"existing": 1}
    assert out is not state


def test_apply_session_params_fork_sets_fork_flag():
    """FORK → _cur_fork_session=True；不设 resume session；返新 dict 不 mutate。"""
    state = {"existing": 1}
    coord = _coord()
    plan = _plan(mode=RP.RetryMode.FORK, consumes_retry=False)
    out = GR._apply_session_params(state, plan, coord)
    assert out["_retry_mode"] == "fork"
    assert out["_cur_fork_session"] is True
    assert "_cur_resume_session" not in out
    assert state == {"existing": 1}                  # 入参不变
    assert out is not state


def test_apply_session_params_new_session_sets_neither():
    """NEW_SESSION → 两者皆不设（dev cmd 默认 new session）；返新 dict 不 mutate。"""
    state = {"existing": 1}
    coord = _coord()
    plan = _plan(mode=RP.RetryMode.NEW_SESSION, consumes_retry=False)
    out = GR._apply_session_params(state, plan, coord)
    assert out["_retry_mode"] == "new_session"
    assert "_cur_resume_session" not in out
    assert "_cur_fork_session" not in out
    assert state == {"existing": 1}


def test_apply_session_params_resume_toctou_load_none_raises():
    """RESUME 二次 load 返 None → RuntimeError fail-closed（不静默降级 NEW_SESSION 丢 dev 进度，r-review P0）。

    retry_policy.decide 时 session 非 None，apply 时 None = TOCTOU/IO 损坏。
    """
    import pytest
    state = {}
    coord = _coord(session_store=types.SimpleNamespace(load=lambda i: None))
    plan = _plan(mode=RP.RetryMode.RESUME)
    with pytest.raises(RuntimeError, match="TOCTOU"):
        GR._apply_session_params(state, plan, coord)


def test_apply_session_params_resume_no_session_id_raises():
    """RESUME 二次 load 返对象但无 session_id → RuntimeError fail-closed（r-review P0）。"""
    import pytest
    state = {}
    coord = _coord(session_store=types.SimpleNamespace(
        load=lambda i: types.SimpleNamespace(session_id=None)))
    plan = _plan(mode=RP.RetryMode.RESUME)
    with pytest.raises(RuntimeError, match="TOCTOU"):
        GR._apply_session_params(state, plan, coord)
