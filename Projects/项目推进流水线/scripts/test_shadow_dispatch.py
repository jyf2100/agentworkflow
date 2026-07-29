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
    """pr_open + verify.pass + verify_verdict='pass'（双绿，task 4.1 dual gate）→ published（交付终态）。"""
    sj = RT.ShadowJournal(tmp_path / "j.jsonl", "run_1", _stamp, enabled=True)
    rec = {"status": "pr_open", "verify": {"pass": True}, "verify_verdict": "pass",
           "pr_url": "https://gh/o/r/pull/1"}
    run_daily._sj_terminal(sj, rec, "iter_1", "prd_1")
    evs = J.read_events(tmp_path / "j.jsonl")
    assert len(evs) == 1 and evs[0].event_type == "published"
    assert evs[0].payload["pr_url"] == "https://gh/o/r/pull/1"


def test_sj_terminal_published_reconciles_evidence_artifact(tmp_path):
    """task 4.4：publication 前 reconcile test evidence artifact（exactly-once fresh green evidence）。
    dual gate 绿 + evidence artifact 存在&digest 匹配 → published；evidence 缺失/损坏 → emit
    blocked_evidence，**绝不** published（spec 4.4 test evidence idempotency keys before publication）。"""
    import artifact_store
    root = tmp_path / "artifacts"
    ref = artifact_store.store(root, "all tests passed", kind="test_output", sensitivity="internal")
    # ① artifact 存在 + digest 匹配 → published
    rec_ok = {"status": "pr_open", "pr_url": "https://gh/o/r/pull/1",
              "verify": {"pass": True, "evidence_ref": {"digest": ref.digest}},
              "verify_verdict": "pass"}
    sj_ok = RT.ShadowJournal(tmp_path / "j_ok.jsonl", "run_1", _stamp, enabled=True)
    run_daily._sj_terminal(sj_ok, rec_ok, "iter_1", "prd_1", artifact_root=root)
    evs_ok = J.read_events(tmp_path / "j_ok.jsonl")
    assert len(evs_ok) == 1 and evs_ok[0].event_type == "published"
    # ② evidence digest 指向缺失 artifact → blocked_evidence（不当 published）
    rec_bad = {"status": "pr_open", "pr_url": "https://gh/o/r/pull/2",
               "verify": {"pass": True, "evidence_ref": {"digest": "sha256:" + "0" * 64}},
               "verify_verdict": "pass"}
    sj_bad = RT.ShadowJournal(tmp_path / "j_bad.jsonl", "run_1", _stamp, enabled=True)
    run_daily._sj_terminal(sj_bad, rec_bad, "iter_1", "prd_1", artifact_root=root)
    evs_bad = J.read_events(tmp_path / "j_bad.jsonl")
    assert len(evs_bad) == 1 and evs_bad[0].event_type == "blocked_evidence"


def test_sj_terminal_pr_open_red_emits_revise(tmp_path):
    """interrupted_pr + verify 未过 → revise（有 PR 但验证红，**非** published——对齐 compat 防假绿）。"""
    sj = RT.ShadowJournal(tmp_path / "j.jsonl", "run_1", _stamp, enabled=True)
    rec = {"status": "interrupted_pr", "verify": {"pass": False}, "pr_url": "https://gh/o/r/pull/2"}
    run_daily._sj_terminal(sj, rec, "iter_1", "prd_1")
    evs = J.read_events(tmp_path / "j.jsonl")
    assert evs[0].event_type == "revise"


def test_sj_terminal_interrupted_pr_mechanical_green_semantic_red_emits_revise(tmp_path):
    """task 4.1 dual publication gate：interrupted_pr + verify.pass=True（独立测试机械绿）但
    verify_verdict='revise'（pa-verify 语义红）→ emit ``revise``，**绝不** published。spec「Tests green
    but semantic review red」：机械绿非充分证据，须 semantic pass + 对账 known 才 published（防假绿）。"""
    sj = RT.ShadowJournal(tmp_path / "j.jsonl", "run_1", _stamp, enabled=True)
    rec = {"status": "interrupted_pr", "verify": {"pass": True}, "verify_verdict": "revise",
           "pr_url": "https://gh/o/r/pull/3"}
    run_daily._sj_terminal(sj, rec, "iter_1", "prd_1")
    evs = J.read_events(tmp_path / "j.jsonl")
    assert evs[0].event_type == "revise"


def test_sj_terminal_mechanical_green_missing_semantic_verdict_emits_revise(tmp_path):
    """task 4.1：verify_verdict 缺失（pa-verify 异常/未跑）+ 机械绿 → ``revise``（fail-closed：语义判决
    不明不当 published，design 决策#3「never a green substitute for unknown semantic verdict」）。"""
    sj = RT.ShadowJournal(tmp_path / "j.jsonl", "run_1", _stamp, enabled=True)
    rec = {"status": "pr_open", "verify": {"pass": True}}   # 无 verify_verdict
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


# ─── task 4.4：publication 前对账目标构造（_publication_targets，coordinator.owned resolver 消费）──
def test_publication_targets_covers_push_pr_and_test_evidence():
    """4.4：_publication_targets 产 push（远端分支）/ pr（``owner:branch``）/ test（green evidence digest）。
    publication 前对账这三类关键副作用幂等键（commit 已在 verify 阶段 ``_has_commits`` 查 GitHub 视角）。
    这些 target 喂 ``reconcile.reconcile_side_effects`` + coord.owned resolver 算 confirmed/pending/unknown 三态。"""
    rec = {"branch": "auto/feat"}
    vj = {"evidence_ref": {"digest": "sha256:abc"}}
    targets = run_daily._publication_targets("owner/repo", rec, vj)
    by_kind = {t.kind: t.target for t in targets}
    assert by_kind["push"] == "auto/feat"                 # 远端分支
    assert by_kind["pr"] == "owner/repo:auto/feat"        # owner:branch
    assert by_kind["test"] == "sha256:abc"                # green evidence digest


def test_publication_targets_handles_missing_fields():
    """4.4：无 branch → 无 push/pr；无 evidence_ref → 无 test；owner_repo 空 → pr target=branch（无前缀）。"""
    assert run_daily._publication_targets("o/r", {}, {}) == []      # 无 branch 无 evidence → 空
    only_branch = run_daily._publication_targets("o/r", {"branch": "b"}, {})
    assert {t.kind for t in only_branch} == {"push", "pr"}          # 有 branch 无 evidence → push/pr
    no_owner = run_daily._publication_targets("", {"branch": "b"}, {})
    assert next(t for t in no_owner if t.kind == "pr").target == "b"   # owner_repo 空 → pr target=branch


# ─── single-flight-auto-merge task 1.2：dispatch_one rec schema 扩展（向后兼容）──────────
def test_dispatch_one_rec_has_single_flight_schema_fields(tmp_path, monkeypatch):
    """task 1.2：dispatch_one 返回的 rec 含 single-flight-auto-merge 新增的 4 字段（merge_commit /
    reverted / triage_reason / post_merge_verdict），默认值保持 baseline 语义（未 merge / 未 revert /
    未进 triage / 未跑 post-merge 验证）。向后兼容：旧字段全保留（rec 仍是完整 dispatch record）。

    复用 preflight 阻断短路（prof 违规→dispatch_one 早退 return rec），验证 rec schema 在最早构造点
    （run_daily rec 初始化）即含新字段——后续 dev→verify→merge 闭环（task 3/4/5）填这些字段，本测钉死 schema。
    """
    from types import SimpleNamespace
    # Arrange — 违规 profile（preflight 阻断短路，不起 dev loop）
    prof = {"name": "p", "loop": {"lifecycle_hooks": True}}
    entry = {"prd_path": "x.md"}
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path)
    # Act
    rec = run_daily.dispatch_one(entry, prof, "20260722", SimpleNamespace())
    # Assert — single-flight-auto-merge 4 新字段存在 + baseline 默认值
    assert rec["merge_commit"] is None        # 未 merge → 无 commit sha
    assert rec["reverted"] is False            # 未 auto-revert
    assert rec["triage_reason"] is None        # 未进 triage 池
    assert rec["post_merge_verdict"] is None   # 未跑 post-merge main 全量测试
    # 向后兼容：旧字段全保留（rec schema 扩展非破坏）
    for legacy in ["project", "prd_path", "slug", "base", "status", "pr_url", "branch",
                   "dev_killed", "stalled", "run_log", "dev_cost", "dev_turns", "verify",
                   "skip_reason", "dev_test_cmd", "verify_verdict", "verify_round"]:
        assert legacy in rec, f"rec 缺旧字段 {legacy}（向后兼容破坏）"


# ─── single-flight-auto-merge task 2.4：max_prs_in_flight 退化为同项目恒 1（flag gated）─────────
def test_serial_shadow_degrades_inflight_ceiling_to_one(stub_externals, tmp_path, monkeypatch):
    """task 2.4：serial_shadow on → max_prs_in_flight 退化为恒 1（inflight≥1 即超额 skip）；
    count_inflight_prs 保留为独立「OPEN PR 上限」门（D5/D9，≠ slot——slot 是 journal+flock，task 2.2）。

    dispatch_skip_dev smoke：避免跑到真实 dev-agent（RED 时 max 未退化→过门4→smoke 返回 planned；
    GREEN 时 max=1→1≥1 超额 skip，不到 smoke）。"""
    from external_state import found
    from types import SimpleNamespace
    monkeypatch.setattr(stub_externals, "STATE_DIR", tmp_path)
    monkeypatch.setattr(stub_externals, "VAULT_ROOT", tmp_path)
    monkeypatch.setattr(stub_externals, "count_inflight_prs", lambda *a, **k: found(1))   # 已有 1 OPEN PR
    prof = {"name": "p", "repo": str(tmp_path / "r"), "default_branch": "main",
            "admission": True, "dev_agent_ready": True, "type": "code",
            "loop": {"single_flight_serial_shadow": True}}
    entry = {"prd_path": "p.md"}
    # Act
    rec = stub_externals.dispatch_one(entry, prof, "20260728", SimpleNamespace(dispatch_skip_dev=True))
    # Assert — 上限退化为 1，inflight=1 ≥ 1 → 超额 skip
    assert rec["status"] == "skip"
    assert "超额" in rec["skip_reason"] and "≥ 1" in rec["skip_reason"]


def test_serial_shadow_off_keeps_baseline_inflight_ceiling(stub_externals, tmp_path, monkeypatch):
    """task 2.4：serial_shadow off → max_prs_in_flight 维持 baseline（默认 2），inflight=1 不超额（1<2），
    过准入门 4（dispatch_skip_dev smoke 返回 planned，证门 4 未 skip）。baseline 不变（design 决策#8）。"""
    from external_state import found
    from types import SimpleNamespace
    monkeypatch.setattr(stub_externals, "STATE_DIR", tmp_path)
    monkeypatch.setattr(stub_externals, "VAULT_ROOT", tmp_path)
    monkeypatch.setattr(stub_externals, "count_inflight_prs", lambda *a, **k: found(1))
    prof = {"name": "p", "repo": str(tmp_path / "r"), "default_branch": "main",
            "admission": True, "dev_agent_ready": True, "type": "code"}   # 无 serial_shadow → off
    entry = {"prd_path": "p.md"}
    # Act
    rec = stub_externals.dispatch_one(entry, prof, "20260728", SimpleNamespace(dispatch_skip_dev=True))
    # Assert — 过门 4（1<2 不超额）→ skip-dev smoke 返回 planned
    assert rec["status"] == "planned"

