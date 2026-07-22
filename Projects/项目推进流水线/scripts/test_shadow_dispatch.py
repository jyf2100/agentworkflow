#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_shadow_dispatch.py — task 3.2 dispatch 旁路 emit 终态 + task 3.3 feedback artifact 双写单测。

验证 shadow journaling 接入 dispatch 的两个纯逻辑入口：
    - 3.2 ``_sj_terminal``：dispatch record status → 合法 journal 终态事件，映射对齐
      ``compat_readers.legacy_status`` 保 shadow parity（task 3.4）；flag 关 → emit no-op。
    - 3.3 ``_append_verify_feedback``：shadow 双写——feedback 落 content-addressed artifact
      （sanitized 脱敏）+ emit 事件，PRD 追加照旧；flag 关 → 只 PRD 追加（决策零变化）。

模块零 SDK（run_daily 顶部 import 不触 sdk，cron 隔离不变）；AAA 结构。
跑：python3 -m pytest scripts/test_shadow_dispatch.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import run_daily  # noqa: E402
import loop_runtime as RT  # noqa: E402
import journal as J  # noqa: E402


def _stamp() -> str:
    return "2026-07-21T00:00:00Z"


# ─── 3.2：_sj_terminal 终态映射（对齐 compat_readers.legacy_status 保 parity）────────
def test_sj_terminal_pr_open_green_emits_published(tmp_path):
    """pr_open + verify.pass → published（交付终态，payload 带 pr_url）。"""
    sj = RT.ShadowJournal(tmp_path / "j.jsonl", "run_1", _stamp, enabled=True)
    rec = {"status": "pr_open", "verify": {"pass": True}, "pr_url": "https://gh/o/r/pull/1"}
    run_daily._sj_terminal(sj, rec, "iter_1", "prd_1")
    evs = J.read_events(tmp_path / "j.jsonl")
    assert len(evs) == 1 and evs[0].event_type == "published"
    assert evs[0].payload["pr_url"] == "https://gh/o/r/pull/1"


def test_sj_terminal_pr_open_red_emits_revise(tmp_path):
    """interrupted_pr + verify 未过 → revise（有 PR 但验证红，**非** published——对齐 compat 防假绿）。"""
    sj = RT.ShadowJournal(tmp_path / "j.jsonl", "run_1", _stamp, enabled=True)
    rec = {"status": "interrupted_pr", "verify": {"pass": False}, "pr_url": "https://gh/o/r/pull/2"}
    run_daily._sj_terminal(sj, rec, "iter_1", "prd_1")
    evs = J.read_events(tmp_path / "j.jsonl")
    assert evs[0].event_type == "revise"


def test_sj_terminal_blocked_and_fail_statuses(tmp_path):
    """skip/blocked_external_state/blocked_test_gate/fail 各映射到合法终态事件。"""
    cases = [
        ({"status": "skip"}, "aborted"),
        ({"status": "blocked_external_state"}, "external_blocked"),
        ({"status": "blocked_test_gate"}, "test_blocked"),
        ({"status": "fail"}, "failed"),
    ]
    for i, (rec, expected) in enumerate(cases):
        sj = RT.ShadowJournal(tmp_path / f"j{i}.jsonl", "run_1", _stamp, enabled=True)
        run_daily._sj_terminal(sj, rec, "iter_1", "prd_1")
        evs = J.read_events(tmp_path / f"j{i}.jsonl")
        assert evs[0].event_type == expected, f"{rec['status']} 应映射 {expected}"


def test_sj_terminal_unmapped_status_emits_nothing(tmp_path):
    """sandbox_blocked/blocked_evidence 等 task 4/5 才引入的 status → 暂不 emit（前向占位，留各自 task）。
    task 3.5 已覆盖 stalled/orphan_deleted emit（见下），故用真正未引入的 status 验证 no-op 契约。"""
    sj = RT.ShadowJournal(tmp_path / "j.jsonl", "run_1", _stamp, enabled=True)
    run_daily._sj_terminal(sj, {"status": "sandbox_blocked"}, "iter_1", "prd_1")
    assert not (tmp_path / "j.jsonl").exists()   # 未 emit → 文件未创建


def test_sj_terminal_stalled_emits_stalled_event(tmp_path):
    """task 3.5：stalled（dev loop 主动刹车无 commit，连续 N 轮无写类进展）→ emit ``stalled`` 终态事件。
    spec terminal class（scenario 19）：与 compat ``legacy_status`` + reducer STALLED 三端对齐保 parity。"""
    sj = RT.ShadowJournal(tmp_path / "j.jsonl", "run_1", _stamp, enabled=True)
    run_daily._sj_terminal(sj, {"status": "stalled"}, "iter_1", "prd_1")
    evs = J.read_events(tmp_path / "j.jsonl")
    assert len(evs) == 1 and evs[0].event_type == "stalled"


def test_sj_terminal_orphan_deleted_emits_orphan_event(tmp_path):
    """task 3.5：orphan_deleted（dev 无 commit 孤儿分支清理）→ emit ``orphan_deleted`` 终态事件。
    spec terminal class（scenario 19）：与 compat ``legacy_status`` + reducer ORPHAN_DELETED 三端对齐保 parity。"""
    sj = RT.ShadowJournal(tmp_path / "j.jsonl", "run_1", _stamp, enabled=True)
    run_daily._sj_terminal(sj, {"status": "orphan_deleted"}, "iter_1", "prd_1")
    evs = J.read_events(tmp_path / "j.jsonl")
    assert len(evs) == 1 and evs[0].event_type == "orphan_deleted"


def test_sj_terminal_noop_when_flag_off(tmp_path):
    """flag 关 → sj.emit no-op（_sj_terminal 调用零副作用，baseline 不变，design 决策#8）。"""
    sj = RT.ShadowJournal(tmp_path / "j.jsonl", "run_1", _stamp, enabled=False)
    run_daily._sj_terminal(sj, {"status": "fail"}, "iter_1", "prd_1")
    assert not (tmp_path / "j.jsonl").exists()


# ─── 3.3：_append_verify_feedback shadow 双写 ────────────────────────────────
def test_append_feedback_shadow_double_writes_artifact_and_prd(tmp_path):
    """flag 开：feedback 落 artifact（脱敏）+ emit verifier_feedback 事件 + PRD 照旧追加（决策不变）。"""
    prd = tmp_path / "prd.md"; prd.write_text("# 原始 PRD", encoding="utf-8")
    aroot = tmp_path / "artifacts"
    sj = RT.ShadowJournal(tmp_path / "j.jsonl", "run_1", _stamp, enabled=True)
    fb = "修复 X：泄露的 token=ghp_secret12345 须脱敏\n定位 src/a.py:L10"
    run_daily._append_verify_feedback(str(prd), fb, 1, sj=sj, iter_id="iter_1", prd_id="prd_1",
                                      artifact_root=aroot)
    # PRD 照旧追加（保 verify 闭环下一轮读反馈决策不变）
    prd_txt = prd.read_text(encoding="utf-8")
    assert "审核反馈（verify 第1轮" in prd_txt and "修复 X" in prd_txt
    # artifact 落盘 + verifier_feedback 事件
    evs = J.read_events(tmp_path / "j.jsonl")
    assert len(evs) == 1 and evs[0].event_type == "verifier_feedback"
    art_content = (aroot / evs[0].payload["path"]).read_text(encoding="utf-8")
    assert "ghp_secret12345" not in art_content   # sanitized redact_secrets 抹密钥
    assert "src/a.py:L10" in art_content           # 非密钥内容保留（审计/重放）


def test_append_feedback_flag_off_only_appends_prd(tmp_path):
    """flag 关：只 PRD 追加，无 artifact / 无事件（决策零变化 = 第一阶段行为，向后兼容）。"""
    prd = tmp_path / "prd.md"; prd.write_text("# PRD", encoding="utf-8")
    aroot = tmp_path / "artifacts"
    sj = RT.ShadowJournal(tmp_path / "j.jsonl", "run_1", _stamp, enabled=False)
    run_daily._append_verify_feedback(str(prd), "反馈内容", 1, sj=sj, iter_id="iter_1", prd_id="prd_1",
                                      artifact_root=aroot)
    assert "反馈内容" in prd.read_text(encoding="utf-8")        # PRD 追加照旧
    assert not (tmp_path / "j.jsonl").exists()                  # 无事件
    assert not aroot.exists() or not any(aroot.iterdir())       # 无 artifact


def test_append_feedback_shadow_swallows_artifact_failure(tmp_path):
    """shadow 契约：artifact 写失败（非法 artifact_root）→ 吞异常，PRD 追加照常（观测层不拖垮 verify 闭环）。"""
    prd = tmp_path / "prd.md"; prd.write_text("# PRD", encoding="utf-8")
    sj = RT.ShadowJournal(tmp_path / "j.jsonl", "run_1", _stamp, enabled=True)
    # artifact_root 指向只读/非法路径触发 store 异常 → _append_verify_feedback 须吞掉、PRD 仍追加
    run_daily._append_verify_feedback(str(prd), "反馈", 1, sj=sj, iter_id="iter_1", prd_id="prd_1",
                                      artifact_root=Path("/proc/cannot/store/here"))
    assert "反馈" in prd.read_text(encoding="utf-8")   # PRD 追加未被 artifact 失败拖垮


# ─── task 3.2：driven 模式摘除 PRD 追加，feedback 只 sanitized content-addressed artifact ──
def test_append_feedback_driven_stops_prd_append_keeps_artifact(tmp_path):
    """task 3.2 + spec「Immutable new-run input」：``journal_driven_dispatch``（driven）开 → 不再追加 PRD
    （original PRD byte-for-byte unchanged），feedback 只落 sanitized content-addressed artifact +
    journal ``verifier_feedback`` event（引用 digest/path）。verify 闭环读路径切 artifact 由 task 3.4 配合，
    driven flag 真正 enable 在 task 7.5 cutover（3.2-3.4 完成前 driven 不开 → verify 闭环不断）。"""
    prd = tmp_path / "prd.md"
    original = "# 原始 PRD\n\n不可变真源。"
    prd.write_text(original, encoding="utf-8")
    aroot = tmp_path / "artifacts"
    sj = RT.ShadowJournal(tmp_path / "j.jsonl", "run_1", _stamp, enabled=True)
    fb = "修复 X：token=ghp_secret12345 须脱敏\n定位 src/a.py:L10"
    # Act
    run_daily._append_verify_feedback(str(prd), fb, 1, sj=sj, iter_id="iter_1", prd_id="prd_1",
                                      artifact_root=aroot, driven=True)
    # Assert — PRD byte-for-byte 不变（spec SHALL）+ feedback 落 sanitized artifact + journal event
    assert prd.read_text(encoding="utf-8") == original
    evs = J.read_events(tmp_path / "j.jsonl")
    assert len(evs) == 1 and evs[0].event_type == "verifier_feedback"
    art = (aroot / evs[0].payload["path"]).read_text(encoding="utf-8")
    assert "ghp_secret12345" not in art        # sanitized redact_secrets 抹密钥
    assert "src/a.py:L10" in art               # 非密钥内容保留（审计/重放/3.4 retry prompt 读）


def test_append_feedback_driven_swallows_artifact_failure_prd_still_unchanged(tmp_path):
    """driven 模式 shadow 契约保留：artifact 写失败 → 吞异常不崩；PRD 仍 byte-for-byte 不变（driven 不追加）。
    反馈丢失（既未 artifact 也未 PRD）是 known——task 4.2 加 evidence-integrity fail closed 补。"""
    prd = tmp_path / "prd.md"
    original = "# PRD"
    prd.write_text(original, encoding="utf-8")
    sj = RT.ShadowJournal(tmp_path / "j.jsonl", "run_1", _stamp, enabled=True)
    run_daily._append_verify_feedback(str(prd), "反馈", 1, sj=sj, iter_id="iter_1", prd_id="prd_1",
                                      artifact_root=Path("/proc/cannot/store/here"), driven=True)
    assert prd.read_text(encoding="utf-8") == original    # driven：artifact 失败也不追加 PRD


# ─── task 2.5：dispatch_one 入口 preflight 阻断非法 flag 组合 ──────────────────
def test_dispatch_one_preflight_blocks_invalid_flag_combo(tmp_path, monkeypatch):
    """task 2.5：dispatch_one 入口 preflight——lifecycle_hooks 开但 journal_shadow 关（impossible partial
    组合，design 决策#1）→ 阻断不投递（status=skip + 结构化 reason），**在 admission profile 门之前**
    拦截，不起 dev loop（不触 git/gh/SDK）。"""
    from types import SimpleNamespace
    # Arrange — 违规 profile（hooks 开但 journal_shadow 默认关 → 依赖链违）+ 故意无 admission
    # （证 preflight 先于 profile 门：未接入时会被 profile 门先 skip，reason 不含「loop flag 组合非法」）
    prof = {"name": "p", "loop": {"lifecycle_hooks": True}}
    entry = {"prd_path": "x.md"}
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path)   # journal 路径指向 tmp_path（shadow 关→不 IO）

    # Act
    rec = run_daily.dispatch_one(entry, prof, "20260722", SimpleNamespace())

    # Assert — preflight 阻断（先于 admission profile 门），结构化 reason 含违规详情
    assert rec["status"] == "skip"
    assert "loop flag 组合非法" in rec["skip_reason"]
    assert "lifecycle_hooks requires journal_shadow" in rec["skip_reason"]


# ─── task 3.1：dispatch entry 捕获 PRD 内容 digest → planned event 携带 ──────────
def test_dispatch_one_planned_event_carries_prd_digest(tmp_path, monkeypatch):
    """task 3.1：dispatch_one 在 dispatch entry 读 PRD 内容 → build_coordinator(prd_content=...) →
    planned journal event payload 携带 ``prd_digest``（spec「Immutable new-run input」：initial event
    锚定 PRD 内容版本；prd_abs=VAULT_ROOT/prd_path，run_daily:1222）。

    admission 1 fail（prof 无 admission）→ planned emit 后 skip return，不跑 dev-agent（短路重依赖）。
    """
    from types import SimpleNamespace
    import artifact_store
    # Arrange — tmp PRD（entry["prd_path"] 相对 VAULT_ROOT）+ prof（journal_shadow on 满足 preflight，
    # 无 admission → admission 1 fail 短路在 dev-agent 前）
    prd_content = "# PRD\n实现 X\n验收: tests green"
    (tmp_path / "prd.md").write_text(prd_content, encoding="utf-8")
    monkeypatch.setattr(run_daily, "VAULT_ROOT", tmp_path)
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path)
    prof = {"name": "p", "loop": {"journal_shadow": True}}
    entry = {"prd_path": "prd.md"}
    # Act
    run_daily.dispatch_one(entry, prof, "20260722", SimpleNamespace())
    # Assert — planned event 落盘 + payload 含 prd_digest（content-addressed 真源）
    journals = list(tmp_path.rglob("*.journal.jsonl"))
    assert journals, "journal_shadow on → planned/running 事件应落盘"
    planned = [e for e in J.read_events(journals[0]) if e.event_type == "planned"]
    assert planned, "dispatch entry 应 emit planned 事件"
    expected = artifact_store.compute_digest(prd_content.encode("utf-8"))
    assert planned[0].payload["prd_digest"] == expected


def test_dispatch_one_planned_omits_digest_when_prd_unreadable(tmp_path, monkeypatch):
    """baseline 容错：PRD 文件缺失/不可读 → prd_content=None → prd_digest 不进 planned payload
    （design「store the PRD content digest」只在能读时捕获；读失败不崩，dispatch 继续baseline）。"""
    from types import SimpleNamespace
    monkeypatch.setattr(run_daily, "VAULT_ROOT", tmp_path)
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path)
    prof = {"name": "p", "loop": {"journal_shadow": True}}
    entry = {"prd_path": "missing.md"}        # 文件不存在
    # Act
    run_daily.dispatch_one(entry, prof, "20260722", SimpleNamespace())
    # Assert — planned 落盘但 payload 不含 prd_digest（未捕获）
    journals = list(tmp_path.rglob("*.journal.jsonl"))
    planned = [e for e in J.read_events(journals[0]) if e.event_type == "planned"]
    assert planned
    assert "prd_digest" not in planned[0].payload

