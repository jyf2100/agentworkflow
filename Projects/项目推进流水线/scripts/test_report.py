#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_report.py — report 段 fail-safe / 发布门阻断渲染单测（OpenSpec harden-project-pipeline tasks 5.2）。

验证 stage_report：
    - blocked_external_state / blocked_test_gate 记录渲染进 🚫 阻断 节（带类型 + 脱敏原因）；
    - 阻断项既不计入「产出 PR」、也不计入 verify 绿/红（fail-safe 与发布门均未投递）——正交不串桶；
    - 阻断项计入 active → 触发邮件（运维须 triage：auth / 远程服务 / flaky test），subject 含阻断计数。

不触真实 SMTP / IO：state JSON 落 tmp，subprocess.run 桩截 SMTP 命令。AAA 结构。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent))
import run_daily  # noqa: E402

STAMP = "20260720"


def _setup_report(tmp_path, monkeypatch, disp):
    """把 STATE_DIR/REPORT_DIR/DAILY_DIR 指到 tmp，落三份 state JSON；桩掉 SMTP 直发，返回截获的命令。"""
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(run_daily, "REPORT_DIR", tmp_path / "report")
    monkeypatch.setattr(run_daily, "DAILY_DIR", tmp_path / "daily")
    st = tmp_path / "state"
    st.mkdir(parents=True, exist_ok=True)
    (st / f"candidates_{STAMP}.json").write_text(json.dumps(
        {"candidates": [], "today_new_count": 3, "stats": {"signals_extracted": 5}}, ensure_ascii=False),
        encoding="utf-8")
    (st / f"prd_gate_{STAMP}.json").write_text(json.dumps(
        [{"project": "o/r1", "prd_path": "x.md", "verdict": "pass"}], ensure_ascii=False), encoding="utf-8")
    (st / f"dispatch_{STAMP}.json").write_text(json.dumps(disp, ensure_ascii=False), encoding="utf-8")

    smtp_cmds: list[list[str]] = []

    def fake_run(cmd, *a, **k):
        smtp_cmds.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(run_daily.subprocess, "run", fake_run)
    return smtp_cmds


def test_report_renders_blocked_and_keeps_counts_orthogonal(tmp_path, monkeypatch):
    # Arrange：1 review绿 / 1 failing / 1 blocked_external / 1 blocked_test_gate（四态各一）
    disp = [
        {"project": "o/r1", "status": "pr_open", "pr_url": "https://github.com/o/r1/pull/1",
         "branch": "auto/a", "slug": "slug-a", "verify": {"pass": True}},
        {"project": "o/r2", "status": "pr_open", "pr_url": "https://github.com/o/r2/pull/2",
         "branch": "auto/b", "slug": "slug-b", "verify": {"pass": False, "test_cmd": "pytest"}},
        {"project": "o/r3", "status": "blocked_external_state", "branch": "auto/c", "slug": "slug-c",
         "blocked_check": "branch_protection", "skip_reason": "gh 401（已脱敏）"},
        {"project": "o/r4", "status": "blocked_test_gate", "branch": "auto/d", "slug": "slug-d",
         "gate_status": "test_failed", "gate_reason": "evidence not fresh"},
    ]
    smtp_cmds = _setup_report(tmp_path, monkeypatch, disp)
    args = SimpleNamespace(dry_run=False, no_notify=False)

    # Act
    rp = run_daily.stage_report(args, {}, STAMP)
    txt = rp.read_text(encoding="utf-8")

    # Assert ① 阻断节渲染两类阻断（类型 + 原因）
    assert "## 🚫 阻断" in txt
    assert "远程态不明（branch_protection）" in txt
    assert "gh 401（已脱敏）" in txt
    assert "测试发布门（test_failed）" in txt
    assert "evidence not fresh" in txt

    # Assert ② 概览计数正交：产出 PR=2（两条 pr_open）；阻断=2，与产出 PR 分开列
    assert "产出 PR：2" in txt
    assert "阻断（未投递）：2（远程态不明 1 / 测试门未过 1）" in txt
    # 阻断项不进 verify 桶（无 verify 键）→ failing 仍只算 pr_open+verify 红那 1 条
    assert "验证 failing：1" in txt

    # Assert ③ 阻断算 active → 触发邮件；subject 含阻断计数（运维 triage 信号）
    assert smtp_cmds, "阻断项应触发邮件（active）"
    cmd = smtp_cmds[0]
    subject = cmd[cmd.index("--subject") + 1]
    assert "2 阻断" in subject


def test_report_no_blocked_shows_none(tmp_path, monkeypatch):
    # Arrange：仅 1 条 review 绿，无阻断 / 无 failing
    disp = [{"project": "o/r1", "status": "pr_open", "pr_url": "https://github.com/o/r1/pull/1",
             "branch": "auto/a", "slug": "slug-a", "verify": {"pass": True}}]
    _setup_report(tmp_path, monkeypatch, disp)
    args = SimpleNamespace(dry_run=False, no_notify=False)

    # Act
    rp = run_daily.stage_report(args, {}, STAMP)
    txt = rp.read_text(encoding="utf-8")

    # Assert：阻断节存在但「（无）」；概览阻断计数为 0
    assert "阻断（未投递）：0" in txt
    blocked_section = txt.split("## 🚫 阻断", 1)[1]
    assert "（无）" in blocked_section


def test_blocked_with_verify_red_not_double_counted_in_failing(tmp_path, monkeypatch):
    # I-P 回归（code-review）：blocked_external_state + verify红（verify 跑了但 reconcile 卡 UNKNOWN）
    # 旧版 failing 过滤器与状态无关 → 这条既算 failing 又算 blocked（双计，违反 5.2 正交）。
    # 修复后 failing 排除 blocked 桶 → 仅算 blocked，failing 计数不含它。
    disp = [
        {"project": "o/r1", "status": "pr_open", "pr_url": "https://github.com/o/r1/pull/1",
         "branch": "auto/a", "slug": "a", "verify": {"pass": False, "test_cmd": "pytest"}},  # 真 failing
        {"project": "o/r2", "status": "blocked_external_state", "branch": "auto/b", "slug": "b",
         "blocked_check": "pr_lookup", "skip_reason": "PR态不明",
         "verify": {"pass": False, "test_cmd": "pytest"}},  # 阻断但带 verify红 → 不应再计 failing
    ]
    _setup_report(tmp_path, monkeypatch, disp)
    rp = run_daily.stage_report(SimpleNamespace(dry_run=True, no_notify=False), {}, STAMP)
    txt = rp.read_text(encoding="utf-8")
    # failing 只算 pr_open 那条（1）；blocked 那条虽 verify红但已落阻断桶、不双计
    assert "验证 failing：1" in txt
    assert "阻断（未投递）：1（远程态不明 1" in txt


def test_report_reads_legacy_records_without_new_fields(tmp_path, monkeypatch):
    # 6.4 rollback compat：旧版 dispatch 记录（缺 gate_status/blocked_check/evidence_fresh/
    # dev_test_cmd/verify_verdict/verify_round 等新可选字段）仍能被 report 正常读取渲染、计数不崩。
    legacy = [
        {"project": "o/r1", "status": "pr_open",                       # 仅 legacy 字段
         "pr_url": "https://github.com/o/r1/pull/9", "branch": "auto/old", "slug": "old-slug",
         "verify": {"pass": True}},
        {"project": "o/r2", "status": "skip", "skip_reason": "legacy skip"},   # 连 verify 都没有
    ]
    _setup_report(tmp_path, monkeypatch, legacy)
    # dry_run=True：不触 SMTP，只验报告落盘 + 计数（可选字段全缺席不致 KeyError）
    rp = run_daily.stage_report(SimpleNamespace(dry_run=True, no_notify=False), {}, STAMP)
    txt = rp.read_text(encoding="utf-8")

    # Assert：旧记录正常渲染、计数；新可选字段缺席由 .get() 容忍，无异常
    assert "old-slug" in txt and "legacy skip" in txt
    assert "产出 PR：1" in txt                  # 仅 pr_open 那条计产出
    assert "阻断（未投递）：0" in txt           # 无新阻断字段 → 不误计


def test_report_skip_with_explicit_null_pr_url_does_not_crash(tmp_path, monkeypatch):
    # 回归（fix f98086a）：dispatch 对超额 skip 项写 "pr_url": null（显式 None），
    # 旧 repo_of 用 d.get("pr_url", "") → default 不覆盖显式 null → 返回 None →
    # re.search(pattern, None) 抛 TypeError，致 20260720 cron stage_report 整段崩、报告+简讯未出。
    # 修复 url = d.get("pr_url") or "" 后 None coerce 成 "" 不崩。此测试锁该修复、防回退。
    # 复现 07-20 cron 实况：过闸 PRD 9 份全部超额 skip（在途 2 ≥ 2）、pr_url=null。
    disp = [
        {"project": "cc-web-control", "status": "skip", "pr_url": None,   # 显式 null（不是缺键）
         "slug": "hub-supervised-autonomy", "skip_reason": "跳过-超额（在途 2 ≥ 2）"},
        {"project": "ashare-llm-analyst", "status": "skip", "pr_url": None,
         "slug": "fin-report-llm-screen", "skip_reason": "跳过-超额（在途 2 ≥ 2）"},
    ]
    _setup_report(tmp_path, monkeypatch, disp)
    args = SimpleNamespace(dry_run=True, no_notify=False)   # dry_run：不触 SMTP，只验不崩 + 渲染

    # Act：旧代码此处抛 TypeError；修复后正常落报告
    rp = run_daily.stage_report(args, {}, STAMP)
    txt = rp.read_text(encoding="utf-8")

    # Assert ① 不崩 + 报告落盘（能读到就算过——这是回归核心）
    assert "项目推进报告" in txt
    # Assert ② 两条 skip 渲染进「异常/超时/跳过」节；repo_of 对 pr_url=None 回退到 project 名，不崩
    abnormal = txt.split("## ⚠️ 异常 / 超时 / 跳过", 1)[1]
    assert "cc-web-control" in abnormal
    assert "ashare-llm-analyst" in abnormal
    assert "跳过-超额（在途 2 ≥ 2）" in abnormal
    # Assert ③ 概览计数：全 skip 无 PR（产出 PR=0）；失败/超时/跳过=2
    assert "产出 PR：0" in txt
    assert "失败/超时/跳过：2" in txt
