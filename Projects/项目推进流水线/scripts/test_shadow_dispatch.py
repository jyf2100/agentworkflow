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
    """orphan_deleted/stalled 等 first-cut 未映射 status → 不 emit（shadow gap，留 driven 阶段 task 8.6）。"""
    sj = RT.ShadowJournal(tmp_path / "j.jsonl", "run_1", _stamp, enabled=True)
    run_daily._sj_terminal(sj, {"status": "orphan_deleted"}, "iter_1", "prd_1")
    assert not (tmp_path / "j.jsonl").exists()   # 未 emit → 文件未创建


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

