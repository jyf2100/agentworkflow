#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_single_flight_wiring.py — single-flight-auto-merge task 2.2/2.3：``_run_one`` slot 接线集成测。

验证 ``run_daily._run_one`` 把串行互斥从进程内 ``threading.Lock`` 升级为跨进程 slot（flock + journal）的接线：
  - ``serial_shadow`` on + slot FREE → acquire slot + 调 ``dispatch_one`` + 闭环释放（acquired+released）；
  - ``serial_shadow`` on + slot IN_FLIGHT → **不**调 dispatch_one，rec=skip（让位，spec「Same repo second PRD waits」）；
  - ``serial_shadow`` on + slot UNKNOWN（journal 损坏）→ **不**调 dispatch_one，rec=blocked_external_state
    （fail-safe，spec「Single-flight slot is unknown → blocked_external_state」）；
  - ``serial_shadow`` off → baseline：不碰 slot（无 slot journal），照常调 dispatch_one（design 决策#8）。

stub 掉 ``dispatch_one`` / ``_attach_learning_memory`` / ``repo_owner_repo``，隔离 slot 准入焦点（不触 dev loop/SDK）。
跑：python3 -m pytest scripts/test_single_flight_wiring.py -q
"""
from __future__ import annotations

import sys
import types
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import journal as J  # noqa: E402
import run_daily  # noqa: E402
import single_flight as SF  # noqa: E402
import critical_alert as CA  # noqa: E402
from loop_state import JOURNAL_SCHEMA_VERSION, JournalEvent  # noqa: E402

STAMP = "2026-07-29T00:00:00Z"
OWNER = "test/repo"


def _entry() -> dict:
    return {"project": "p", "prd_path": "PRDs/x.md"}


def _prof(serial_shadow: bool) -> dict:
    prof = {"name": "p", "repo": "/tmp/fake", "admission": True, "dev_agent_ready": True,
            "type": "code", "default_branch": "main"}
    if serial_shadow:
        prof["loop"] = {"single_flight_serial_shadow": True}
    return prof


def _args() -> types.SimpleNamespace:
    return types.SimpleNamespace(_learning_sdk_query_fn=None)


def _stub(monkeypatch, tmp_path) -> list:
    """stub dispatch_one（记录调用）+ repo_owner_repo + STATE_DIR + _attach_learning_memory。返回 calls 列表。"""
    calls: list = []

    def _fake_dispatch(entry, prof, stamp, args, *, slot_handle=None):
        calls.append(entry.get("prd_path"))
        return {"project": entry.get("project"), "prd_path": entry.get("prd_path"),
                "slug": "s", "status": "planned"}

    monkeypatch.setattr(run_daily, "dispatch_one", _fake_dispatch)
    monkeypatch.setattr(run_daily, "repo_owner_repo", lambda repo: OWNER)
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path)
    monkeypatch.setattr(run_daily, "_attach_learning_memory", lambda *a, **k: None)
    return calls


def _write_acquired(jpath, *, lease_expires_at, owner_repo=OWNER):
    ev = JournalEvent(schema_version=JOURNAL_SCHEMA_VERSION, event_id="pre-e",
                      timestamp=STAMP, iteration_id="i", run_id="r", prd_id="p",
                      event_type="slot_acquired",
                      payload={"owner_repo": owner_repo, "lease_expires_at": lease_expires_at})
    J.append_event(jpath, ev)


# ─── serial_shadow on：slot 准入分支 ──────────────────────────────────────
def test_run_one_serial_shadow_acquires_slot_and_dispatches(tmp_path, monkeypatch):
    monkeypatch.setenv("PA_SINGLE_FLIGHT_SERIAL_SHADOW", "1")
    calls = _stub(monkeypatch, tmp_path)
    rec = run_daily._run_one(_entry(), _prof(True), STAMP, _args())
    assert rec["status"] == "planned"                   # dispatch_one 返回值透传
    assert calls == ["PRDs/x.md"]                       # slot FREE → dispatch_one 被调
    # 闭环完整：slot journal 留 acquired + released（with 退出释放，flock 不泄漏）
    ev_types = [e.event_type for e in J.read_events(SF.slot_journal_path(tmp_path, OWNER))]
    assert "slot_acquired" in ev_types and "slot_released" in ev_types


def test_run_one_serial_shadow_inflight_skips_dispatch(tmp_path, monkeypatch):
    monkeypatch.setenv("PA_SINGLE_FLIGHT_SERIAL_SHADOW", "1")
    calls = _stub(monkeypatch, tmp_path)
    _write_acquired(SF.slot_journal_path(tmp_path, OWNER), lease_expires_at="2099-01-01T00:00:00+00:00")
    rec = run_daily._run_one(_entry(), _prof(True), STAMP, _args())
    assert calls == []                                  # 在途 → 不调 dispatch_one（让位）
    assert rec["status"] == "skip"
    assert "inflight" in rec["skip_reason"]


def test_run_one_serial_shadow_unknown_blocks_external_state(tmp_path, monkeypatch):
    monkeypatch.setenv("PA_SINGLE_FLIGHT_SERIAL_SHADOW", "1")
    calls = _stub(monkeypatch, tmp_path)
    jp = SF.slot_journal_path(tmp_path, OWNER)
    jp.parent.mkdir(parents=True, exist_ok=True)
    jp.write_text('{"bad":"middle"}\n', encoding="utf-8")   # 损坏 → UNKNOWN
    rec = run_daily._run_one(_entry(), _prof(True), STAMP, _args())
    assert calls == []                                  # fail-safe → 不调 dispatch_one
    assert rec["status"] == "blocked_external_state"
    assert rec["blocked_check"] == "single_flight_slot"


# ─── serial_shadow off：baseline 不变 ─────────────────────────────────────
def test_run_one_serial_shadow_off_no_slot_journal(tmp_path, monkeypatch):
    monkeypatch.delenv("PA_SINGLE_FLIGHT_SERIAL_SHADOW", raising=False)
    calls = _stub(monkeypatch, tmp_path)
    rec = run_daily._run_one(_entry(), _prof(False), STAMP, _args())
    assert rec["status"] == "planned"                   # baseline dispatch_one 返回值透传
    assert calls == ["PRDs/x.md"]                       # baseline：照常调 dispatch_one
    assert not SF.slot_journal_path(tmp_path, OWNER).exists()   # flag off → 不碰 slot


# ═══ task 4.x：post-merge/halt dispatch 接线辅助（_post_merge_test_cmd + _halt_slot_safe）═══
# 控制面 post-merge 闸 + auto-revert 的 dispatch 接线（run_daily._mr.merged 块）。核心链 4.1-4.3：
# merged→post-merge-test→PASS(merged)/FAIL(revert:REVERTED=triage,CONFLICT·UNKNOWN=halt)/UNKNOWN(halt)。
# _post_merge_test_cmd 决定「post-merge 测什么」（D8：基线=main，≠ verify candidate）；_halt_slot_safe 决定
# 「halt 怎么落地」（UNKNOWN/revert 失败 → slot_halted 终态，下轮 acquire blocked(halted)，spec「no further
# PRD admitted」）。dev-agent post-merge/revert phase 真实 git 行为由 test_dev_agent_merge.py 离线 drill 覆盖。
UTC = timezone.utc


def _now(): return datetime(2026, 7, 29, 0, 0, 0, tzinfo=UTC)


def _stamp(): return STAMP


# ─── _post_merge_test_cmd：命令源同 verify（决策 E），Node→scripts.test / Python→dev_test_cmd ──
def test_post_merge_test_cmd_node_uses_scripts_test(tmp_path):
    repo = tmp_path / "repo"; repo.mkdir()
    (repo / "package.json").write_text('{"scripts":{"test":"jest"}}', encoding="utf-8")
    assert run_daily._post_merge_test_cmd(str(repo), {}, None) == "jest"


def test_post_merge_test_cmd_python_uses_dev_test_cmd(tmp_path):
    # 无 package.json → Python 仓 → 重放 dev-agent 上报的 dev_test_cmd（dev-agent 注入 conda env PATH）
    assert run_daily._post_merge_test_cmd(str(tmp_path), {"dev_test_cmd": "python -m pytest -q"}, None) \
        == "python -m pytest -q"


def test_post_merge_test_cmd_none_when_python_unreported(tmp_path):
    # Python 仓 dev-agent 未上报 → None（→ dev-agent ran=False → UNKNOWN → halt，不当代绿）
    assert run_daily._post_merge_test_cmd(str(tmp_path), {"dev_test_cmd": None}, None) is None


def test_post_merge_test_cmd_none_when_package_json_broken(tmp_path):
    repo = tmp_path / "repo"; repo.mkdir()
    (repo / "package.json").write_text('not json', encoding="utf-8")
    assert run_daily._post_merge_test_cmd(str(repo), {}, None) is None


# ─── _halt_slot_safe：UNKNOWN/revert 失败 → halt 整仓（handle None 时 no-op；fail-open）─────
def test_halt_slot_safe_noop_when_no_handle():
    # handle=None（serial_shadow off / baseline 无 slot）→ no-op（halt 仅记 rec/log，不 raise）
    run_daily._halt_slot_safe(None, reason="post_merge_unknown", run_id="r", prd_id="p",
                              iteration_id="i", owner_repo=OWNER)


def test_halt_slot_safe_halts_slot_and_blocks_next_acquire(tmp_path, monkeypatch):
    # 真 acquire → _halt_slot_safe → release（slot_scope __exit__ 语义）→ HALTED → 下次 acquire blocked(halted)
    monkeypatch.setattr(run_daily, "_now_iso", _stamp)
    res, handle = SF.acquire_slot(tmp_path, OWNER, run_id="r", prd_id="p", iteration_id="i",
                                  now_fn=_now, stamp_fn=_stamp, lease_ttl=3600)
    assert res.acquired
    run_daily._halt_slot_safe(handle, reason="post_merge_revert_unknown", run_id="r", prd_id="p",
                              iteration_id="i", owner_repo=OWNER)
    SF.release_slot(handle, stamp_fn=_stamp, run_id="r", prd_id="p", iteration_id="i", owner_repo=OWNER)
    assert SF.query_slot(tmp_path, OWNER, now_fn=_now).state is SF.SlotState.HALTED
    res2, _ = SF.acquire_slot(tmp_path, OWNER, run_id="r2", prd_id="p2", iteration_id="i2",
                              now_fn=_now, stamp_fn=_stamp, lease_ttl=3600)
    assert not res2.acquired and res2.blocked_reason == "halted"


def test_halt_slot_safe_failopen_on_exception(monkeypatch):
    # halt_slot 自身异常（journal 写失败等）→ _halt_slot_safe 不 raise（fail-open；rec 已标 halted 由调用方保证）
    def _boom(*a, **k):
        raise RuntimeError("journal 写失败")
    monkeypatch.setattr(SF, "halt_slot", _boom)
    run_daily._halt_slot_safe(object(), reason="x", run_id="r", prd_id="p",
                              iteration_id="i", owner_repo=OWNER)   # 不 raise 即 pass


# ─── task 4.5：_raise_critical_alert_safe（halt→durable CRITICAL 告警，不受 flag gating）────
def test_raise_critical_alert_safe_persists_to_alerts_journal(tmp_path):
    # halt 时落 durable 告警 → pending_alerts 可读（crash 后 report/恢复器能读出，不受 journal_shadow flag gating）
    run_daily._raise_critical_alert_safe(tmp_path, OWNER, "prd_x", reason="post_merge_unknown", stamp_fn=_stamp)
    pending = CA.pending_alerts(tmp_path, OWNER)
    assert len(pending) == 1
    assert pending[0].payload["reason"] == "post_merge_unknown"


def test_raise_critical_alert_safe_failopen_on_exception(monkeypatch, tmp_path):
    # raise_alert 自身异常（journal 写失败等）→ _raise_critical_alert_safe 不 raise（fail-open；halt 安全已由 slot 保证）
    def _boom(*a, **k):
        raise RuntimeError("alerts journal 写失败")
    monkeypatch.setattr(CA, "raise_alert", _boom)
    run_daily._raise_critical_alert_safe(tmp_path, OWNER, "prd_x", reason="r", stamp_fn=_stamp)   # 不 raise 即 pass


# ─── task 7.1a：_shadow_merge_decision（serial_shadow 门控 + fail-safe 降级）──────────
def test_shadow_merge_decision_none_when_serial_shadow_off():
    """task 7.1a：serial_shadow off → None（baseline 无 shadow 决策；dispatch 据此不跑 classify、不记不 emit）。"""
    assert run_daily._shadow_merge_decision(False, {"rebase_outcome": "clean"}) is None


def test_shadow_merge_decision_returns_rebase_outcome_when_on():
    """task 7.1a：serial_shadow on → 据 classify-only payload 返 rebase 三态（clean/conflict/unknown）。"""
    assert run_daily._shadow_merge_decision(True, {"rebase_outcome": "clean"}) == "clean"
    assert run_daily._shadow_merge_decision(True, {"rebase_outcome": "conflict"}) == "conflict"
    assert run_daily._shadow_merge_decision(True, {"rebase_outcome": "unknown"}) == "unknown"


def test_shadow_merge_decision_fail_safe_unknown_on_bad_payload():
    """task 7.1a：坏/缺 payload（dev-agent 崩/超时/无输出）→ parse_merge_result 降级 unknown（绝不当代 clean）。"""
    assert run_daily._shadow_merge_decision(True, None) == "unknown"
    assert run_daily._shadow_merge_decision(True, {}) == "unknown"
    assert run_daily._shadow_merge_decision(True, "not a dict") == "unknown"


# ═══ task 7.2：--project / --state-dir 隔离能力（金丝雀前置：单仓限制 + state 物理隔离）═══
# 设计：canary 须只跑 cc-web-control 且与真实 cron state 物理隔离（不互斥真 run 锁、不污染真 state）。
#   --project  → _normalize_projects(append/逗号归一) → _filter_profiles 只留命中（缺失硬错）
#   --state-dir → _apply_state_dir 重绑模块级 STATE_DIR + RUN_LOCK（RUN_LOCK import 时从 STATE_DIR 派生，须一并重算，
#                 否则隔离 run 锁仍落真实 state/.run.lock）。须在 acquire_run_lock / STATE_DIR.mkdir 之前调。
def test_normalize_projects_none_passthrough():
    """None/空 list → None（baseline：不过滤，全量跑）。"""
    assert run_daily._normalize_projects(None) is None
    assert run_daily._normalize_projects([]) is None


def test_normalize_projects_splits_commas_and_strips():
    """append + 逗号混用 → 归一为去空白 list（--project a,b --project c）。"""
    assert run_daily._normalize_projects(["a,b", " c "]) == ["a", "b", "c"]
    assert run_daily._normalize_projects(["solo"]) == ["solo"]


def test_filter_profiles_none_returns_all():
    """None → 不过滤，原 dict 透传（baseline）。"""
    profs = {"a": {"name": "a"}, "b": {"name": "b"}}
    assert run_daily._filter_profiles(profs, None) == profs


def test_filter_profiles_keeps_only_named():
    """命中 → 只留该 profile（canary --project cc-web-control 的隔离核心）。"""
    profs = {"cc-web-control": {"name": "cc-web-control"}, "other": {"name": "other"}}
    out = run_daily._filter_profiles(profs, ["cc-web-control"])
    assert list(out) == ["cc-web-control"]


def test_filter_profiles_missing_exits():
    """命中不存在的项目 → 硬错 SystemExit（绝不静默跑空，免 canary 误判「无产出=绿」）。"""
    import pytest
    with pytest.raises(SystemExit):
        run_daily._filter_profiles({"a": {"name": "a"}}, ["ghost"])


def test_apply_state_dir_rebinds_state_dir_and_run_lock(tmp_path):
    """--state-dir → STATE_DIR + RUN_LOCK 双重重绑到隔离根（RUN_LOCK 须随 STATE_DIR 重算，否则落真实锁）。"""
    before_sd, before_rl = run_daily.STATE_DIR, run_daily.RUN_LOCK
    try:
        run_daily._apply_state_dir(str(tmp_path / "iso"))
        assert run_daily.STATE_DIR == (tmp_path / "iso").resolve()
        assert run_daily.RUN_LOCK == (tmp_path / "iso").resolve() / ".run.lock"
    finally:                                   # 恢复模块全局，免污染后续测试
        run_daily.STATE_DIR = before_sd
        run_daily.RUN_LOCK = before_rl


def test_apply_state_dir_none_is_noop():
    """None → STATE_DIR/RUN_LOCK 原样不动（baseline 无 --state-dir）。"""
    before_sd, before_rl = run_daily.STATE_DIR, run_daily.RUN_LOCK
    try:
        run_daily._apply_state_dir(None)
        assert run_daily.STATE_DIR is before_sd
        assert run_daily.RUN_LOCK is before_rl
    finally:
        run_daily.STATE_DIR = before_sd
        run_daily.RUN_LOCK = before_rl
