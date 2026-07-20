#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_verify_loop.py — verify 闭环单测（TDD）。

覆盖 docs/verify-commit-loop-design.md §5-③「改 stage_dispatch 接入 pa-verify 闭环」：
    纯函数辅助（verify_prompt / _append_verify_feedback / _has_commits / _dev_cmd /
    _run_dev_agent / _dump_branch_diff / reconcile_pr.interrupted）＋ dispatch_one 闭环循环
    （判绿兜底开 PR / 判红保留分支＋反馈进 PRD＋增量 --base=<上次分支> 重投 / 用满降级 interrupted_pr / 无分支 stall）。

跑：python3 -m pytest scripts/test_verify_loop.py -q
AAA 结构（Arrange / Act / Assert）。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent))
import run_daily  # noqa: E402
from external_state import ExtState, found, not_found, unknown  # noqa: E402

PROJ = "cc-web-control"


# ─── 公共 fixture / 工厂 ─────────────────────────────────────────────
def _setup(tmp_path, monkeypatch):
    """把 VAULT_ROOT/STATE_DIR 指到 tmp，并落一份 PRD（_append_verify_feedback 要往里追加）。"""
    monkeypatch.setattr(run_daily, "VAULT_ROOT", tmp_path)
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path / "state")
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    prd = tmp_path / "state" / "prd" / PROJ / "20260718_test.md"
    prd.parent.mkdir(parents=True, exist_ok=True)
    prd.write_text("---\nproject: cc-web-control\n---\n# t\n\n## 验收标准\n- A\n", encoding="utf-8")
    return prd


def _admit(monkeypatch):
    """让 dispatch 准入 1-4 全过（mock 掉运行时实查 + 远程查询）。"""
    monkeypatch.setattr(run_daily, "check_branch_protection", lambda *a, **k: found(True, "ok"))
    monkeypatch.setattr(run_daily, "count_inflight_prs", lambda *a, **k: found(0))
    monkeypatch.setattr(run_daily, "already_dispatched", lambda *a, **k: not_found())
    monkeypatch.setattr(run_daily, "repo_owner_repo", lambda *a, **k: "o/r")
    monkeypatch.setattr(run_daily, "_run_capture", lambda *a, **k: (0, "", ""))   # worktree add


def _repo(tmp_path) -> Path:
    """造一个仓骨架。仓内 legacy scripts/dev-agent.py 现已被忽略（ADR-0006 控制面执行器唯一源），
    保留仅为兼容现役 dispatch 断言，不影响选源。"""
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / "dev-agent.py").write_text("# stub", encoding="utf-8")
    return repo


def _entry() -> dict:
    return {"project": PROJ, "prd_path": f"state/prd/{PROJ}/20260718_test.md", "source_path": ""}


def _prof(repo: Path) -> dict:
    return {"name": PROJ, "repo": str(repo), "admission": True, "dev_agent_ready": True,
            "type": "code", "default_branch": "main", "conda_env": "", "goal": "g",
            "match_surface": {"one_liner": "x"}, "max_prs_in_flight": 2}


def _args():
    return SimpleNamespace(force=False, dispatch_skip_dev=False, dispatch_limit=None, max_concurrent=1)


def _recon_recorder():
    """reconcile_pr mock：记录 interrupted 调用 + 按标志设 status（模拟真 reconcile 的 PR 状态语义）。
    须匹配真签名 reconcile_pr(repo, owner_repo, rec, base, slug, interrupted=...)。"""
    calls: list[bool] = []

    def fake(repo, owner_repo, rec, base, slug, interrupted=True):
        calls.append(interrupted)
        rec["status"] = "interrupted_pr" if interrupted else "pr_open"
        rec["pr_url"] = "http://pr/1"
    return calls, fake


# ─── verify_prompt ──────────────────────────────────────────────────
def test_verify_prompt_red_round2(tmp_path):
    # Arrange / Act
    p = run_daily.verify_prompt("PRD", "auto/b", "main", tmp_path / "d.diff",
                                {"test_rc": 1, "test_log": "L"}, 2, {"name": PROJ})
    # Assert：四要素都喂到了
    assert "第2轮" in p and "auto/b" in p
    assert str(tmp_path / "d.diff") in p
    assert "红（test_rc=1" in p
    assert "增量" in p          # round≥2 标注「增量：上次 dev 分支」


def test_verify_prompt_test_not_run(tmp_path):
    p = run_daily.verify_prompt("PRD", "auto/b", "main", tmp_path / "d.diff", None, 1, {"name": PROJ})
    assert "未跑" in p           # dev 未报 test_cmd → independent_verify 跳过


def test_verify_prompt_green(tmp_path):
    p = run_daily.verify_prompt("PRD", "auto/b", "main", tmp_path / "d.diff",
                                {"test_rc": 0, "test_log": "L"}, 1, {"name": PROJ})
    assert "绿（test_rc=0" in p


# ─── _append_verify_feedback ────────────────────────────────────────
def test_append_verify_feedback(tmp_path):
    # Arrange
    f = tmp_path / "p.md"
    f.write_text("ORIG BODY", encoding="utf-8")
    # Act
    run_daily._append_verify_feedback(
        str(f), "定位：a.ts L12\n原因：x\n怎么改：y\n收尾门：全量 npm test 绿", 1)
    # Assert：原文保留 + 醒目节追加 + 轮次标注
    txt = f.read_text(encoding="utf-8")
    assert txt.startswith("ORIG BODY")
    assert "## ⚠️ 审核反馈（verify 第1轮·非需求变更，未重过 critic 闸）" in txt
    assert "定位：a.ts L12" in txt


# ─── _has_commits ───────────────────────────────────────────────────
class _Proc:
    def __init__(self, stdout=""):
        self.stdout = stdout
        self.returncode = 0


def test_has_commits_found_true_false(monkeypatch):
    monkeypatch.setattr(run_daily.subprocess, "run", lambda *a, **k: _Proc("abc\n"))
    r = run_daily._has_commits("r", "b", "br")
    assert r.state is ExtState.FOUND and r.value is True
    monkeypatch.setattr(run_daily.subprocess, "run", lambda *a, **k: _Proc(""))
    r = run_daily._has_commits("r", "b", "br")
    assert r.state is ExtState.FOUND and r.value is False


def test_has_commits_unknown_on_error(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(run_daily.subprocess, "run", boom)
    r = run_daily._has_commits("r", "b", "br")
    assert r.state is ExtState.UNKNOWN    # fail-safe：失败→UNKNOWN（旧版容忍→False）


# ─── _dev_cmd（控制面执行器唯一源，ADR-0006）────────────────────────
def test_dev_cmd_builds_vault_cmd_and_appends_source():
    """始终构造控制面 dev-agent.py 命令；空 source 不追加 --source，传则追加。"""
    cmd = run_daily._dev_cmd({"conda_env": ""}, "PRD", "main", "")
    assert cmd is not None
    assert str(run_daily.DEV_AGENT_PY) in cmd
    assert "--base" in cmd and cmd[cmd.index("--base") + 1] == "main"
    assert "--source" not in cmd                # 空 source 不追加
    cmd2 = run_daily._dev_cmd({"conda_env": ""}, "PRD", "main", "SRC")
    assert cmd2[cmd2.index("--source") + 1] == "SRC"


def test_dev_cmd_ignores_target_repo_language(tmp_path):
    """目标仓是 Node 仓（package.json + legacy mjs）→ 执行器仍是控制面 Python，不走 node 兜底。

    目标仓语言不决定执行器语言（ADR-0006 控制面单一源）。"""
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "package.json").write_text("{}", encoding="utf-8")
    (repo / "scripts" / "dev-agent.mjs").write_text("# legacy", encoding="utf-8")
    cmd = run_daily._dev_cmd({"conda_env": ""}, "PRD", "main", "")
    assert cmd is not None and cmd[0] != "node"          # 不走 node
    assert str(run_daily.DEV_AGENT_PY) in cmd


def test_dev_cmd_none_when_vault_executor_missing(tmp_path, monkeypatch):
    """控制面 dev-agent.py 缺失 → None（dispatch 判 fail：控制面安装异常）。"""
    monkeypatch.setattr(run_daily, "DEV_AGENT_PY", tmp_path / "nonexistent.py")
    assert run_daily._dev_cmd({"conda_env": ""}, "PRD", "main", "") is None


# ─── _run_dev_agent ─────────────────────────────────────────────────
def test_run_dev_agent_parses_tail_json(tmp_path, monkeypatch):
    monkeypatch.setattr(run_daily, "_run_capture",
                        lambda *a, **k: (0, 'pre\n{"branch":"auto/x","cost":0.1}', ""))
    j = run_daily._run_dev_agent(["x"], tmp_path, "slug", tmp_path / "l.log")
    assert j["branch"] == "auto/x"


def test_run_dev_agent_none_on_error_or_bad_json(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(run_daily, "_run_capture", boom)
    assert run_daily._run_dev_agent(["x"], tmp_path, "slug", tmp_path / "l.log") is None
    monkeypatch.setattr(run_daily, "_run_capture", lambda *a, **k: (0, "not json", ""))
    assert run_daily._run_dev_agent(["x"], tmp_path, "slug", tmp_path / "l.log") is None


# ─── _dump_branch_diff ──────────────────────────────────────────────
def test_dump_branch_diff_writes_and_tolerates(tmp_path, monkeypatch):
    out = tmp_path / "d.diff"
    monkeypatch.setattr(run_daily.subprocess, "run", lambda *a, **k: _Proc("DIFF CONTENT"))
    run_daily._dump_branch_diff("repo", "main", "auto/b", out)
    assert "DIFF CONTENT" in out.read_text(encoding="utf-8")

    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(run_daily.subprocess, "run", boom)
    run_daily._dump_branch_diff("repo", "main", "auto/b", out)   # 容错：不抛，落空 diff 提示
    assert "diff 为空" in out.read_text(encoding="utf-8")


# ─── reconcile_pr interrupted 标志 ──────────────────────────────────
def test_reconcile_interrupted_flag(monkeypatch):
    created: list[list[str]] = []

    def fake_run(cmd, *a, **k):
        if "create" in cmd:
            created.append(list(cmd))
            return _Proc("http://pr/1")         # gh pr create → PR url
        if "log" in cmd:
            return _Proc("deadbeef commit\n")   # git log → has_commit True
        return _Proc("")                        # gh pr list → [] (无 PR)；branch -D/push → 忽略

    monkeypatch.setattr(run_daily.subprocess, "run", fake_run)

    rec_g = {"branch": "auto/b", "prd_path": "p", "dev_killed": False, "stalled": False}
    run_daily.reconcile_pr("repo", "o/r", rec_g, "main", "slug", interrupted=False)
    assert rec_g["status"] == "pr_open"
    assert any("pa-dev: slug" in " ".join(c) for c in created)        # 正常标题

    rec_r = {"branch": "auto/b2", "prd_path": "p", "dev_killed": False, "stalled": False}
    run_daily.reconcile_pr("repo", "o/r", rec_r, "main", "slug", interrupted=True)
    assert rec_r["status"] == "interrupted_pr"
    assert any("⏸ pa-dev 中断" in " ".join(c) for c in created)       # 中断标题


# ─── reconcile_pr fail-safe：UNKNOWN 保留分支不补开/删除（6.4 rollback compat）───
def test_reconcile_preserves_on_pr_lookup_unknown(monkeypatch):
    # Arrange：_lookup_pr UNKNOWN（如 gh 超时）→ 须保留分支、零破坏性远程写
    cmds: list[list[str]] = []

    def fake_run(cmd, *a, **k):
        cmds.append(list(cmd)); return _Proc("")
    monkeypatch.setattr(run_daily.subprocess, "run", fake_run)
    monkeypatch.setattr(run_daily, "_lookup_pr", lambda *a, **k: unknown("gh timeout"))
    rec = {"branch": "auto/x", "prd_path": "p", "dev_killed": False, "stalled": False}

    # Act
    run_daily.reconcile_pr("repo", "o/r", rec, "main", "slug", interrupted=True)
    # Assert：blocked_external_state / pr_lookup；分支保留；无 pr create / branch -D / push --delete
    assert rec["status"] == "blocked_external_state"
    assert rec["blocked_check"] == "pr_lookup"
    assert rec["branch"] == "auto/x"
    joined = [" ".join(c) for c in cmds]
    assert not any("pr create" in x for x in joined)
    assert not any("branch -D" in x for x in joined)
    assert not any("push origin --delete" in x for x in joined)


def test_reconcile_preserves_on_commit_lookup_unknown(monkeypatch):
    # Arrange：_lookup_pr NOT_FOUND（明确无 PR）但 _has_commits UNKNOWN → 仍保留分支不删
    cmds: list[list[str]] = []

    def fake_run(cmd, *a, **k):
        cmds.append(list(cmd)); return _Proc("")
    monkeypatch.setattr(run_daily.subprocess, "run", fake_run)
    monkeypatch.setattr(run_daily, "_lookup_pr", lambda *a, **k: not_found())
    monkeypatch.setattr(run_daily, "_has_commits", lambda *a, **k: unknown("git ls-remote fail"))
    rec = {"branch": "auto/y", "prd_path": "p", "dev_killed": False, "stalled": False}

    # Act
    run_daily.reconcile_pr("repo", "o/r", rec, "main", "slug", interrupted=False)
    # Assert：blocked_external_state / commit_lookup；分支保留；零破坏性远程写
    assert rec["status"] == "blocked_external_state"
    assert rec["blocked_check"] == "commit_lookup"
    assert rec["branch"] == "auto/y"
    joined = [" ".join(c) for c in cmds]
    assert not any("pr create" in x for x in joined)
    assert not any("branch -D" in x for x in joined)
    assert not any("push origin --delete" in x for x in joined)


# ─── dispatch_one 闭环循环（mock 驱动）──────────────────────────────
def test_dispatch_green_round1(tmp_path, monkeypatch):
    # Arrange：dev r1 出分支+测试绿+pa-verify 判绿
    _setup(tmp_path, monkeypatch)
    _admit(monkeypatch)
    repo = _repo(tmp_path)
    monkeypatch.setattr(run_daily, "_run_dev_agent",
                        lambda *a, **k: {"branch": "auto/r1", "cost": 0.1, "turns": 5, "test_cmd": "npm test"})
    monkeypatch.setattr(run_daily, "_has_commits", lambda *a, **k: found(True))
    monkeypatch.setattr(run_daily, "independent_verify",
                        lambda *a, **k: {"pass": True, "test_rc": 0, "test_log": "L"})
    monkeypatch.setattr(run_daily, "_dump_branch_diff", lambda *a, **k: None)
    monkeypatch.setattr(run_daily, "run_persona",
                        lambda *a, **k: ({"verdict": "pass"}, {"cost": 0.0, "turns": 1}))
    recon_calls, fake_rec = _recon_recorder()
    monkeypatch.setattr(run_daily, "reconcile_pr", fake_rec)

    # Act
    rec = run_daily.dispatch_one(_entry(), _prof(repo), "20260718", _args())
    # Assert：r1 即判绿、兜底开「正常」PR（interrupted=False）、未进 r2
    assert rec["verify_verdict"] == "pass"
    assert rec["verify_round"] == 1
    assert recon_calls == [False]
    assert rec["status"] == "pr_open"


def test_dispatch_red_then_green(tmp_path, monkeypatch):
    # Arrange：r1 判红→增量重投 r2→判绿
    _setup(tmp_path, monkeypatch)
    _admit(monkeypatch)
    repo = _repo(tmp_path)
    branches = iter(["auto/r1", "auto/r2"])
    monkeypatch.setattr(run_daily, "_run_dev_agent",
                        lambda *a, **k: {"branch": next(branches), "cost": 0.1, "turns": 5, "test_cmd": "npm test"})
    monkeypatch.setattr(run_daily, "_has_commits", lambda *a, **k: found(True))
    monkeypatch.setattr(run_daily, "independent_verify",
                        lambda *a, **k: {"pass": False, "test_rc": 1, "test_log": "L"})
    monkeypatch.setattr(run_daily, "_dump_branch_diff", lambda *a, **k: None)
    verdicts = iter([{"verdict": "revise", "feedback_section": "fix X at a.ts"},
                     {"verdict": "pass"}])
    monkeypatch.setattr(run_daily, "run_persona", lambda *a, **k: (next(verdicts), {"cost": 0.0, "turns": 1}))
    recon_calls, fake_rec = _recon_recorder()
    monkeypatch.setattr(run_daily, "reconcile_pr", fake_rec)
    # spy：捕获每轮 --base（验证 r2 增量 base=上次 dev 分支）
    bases: list[str] = []
    real = run_daily._dev_cmd
    monkeypatch.setattr(run_daily, "_dev_cmd",
                        lambda prof, prd, base, src: (bases.append(base), real(prof, prd, base, src))[1])

    # Act
    rec = run_daily.dispatch_one(_entry(), _prof(repo), "20260718", _args())
    # Assert：r2 用 r1 分支做 base；r1 红未 reconcile；r2 绿兜底开 PR；反馈进了 PRD
    assert bases == ["main", "auto/r1"]
    assert rec["verify_round"] == 2 and rec["verify_verdict"] == "pass"
    assert recon_calls == [False]               # 只在判绿时收尾一次
    prd_text = (tmp_path / "state" / "prd" / PROJ / "20260718_test.md").read_text(encoding="utf-8")
    assert "审核反馈（verify 第1轮" in prd_text and "fix X at a.ts" in prd_text


def test_dispatch_red_used_up(tmp_path, monkeypatch):
    # Arrange：两轮全红
    _setup(tmp_path, monkeypatch)
    _admit(monkeypatch)
    repo = _repo(tmp_path)
    branches = iter(["auto/r1", "auto/r2"])
    monkeypatch.setattr(run_daily, "_run_dev_agent",
                        lambda *a, **k: {"branch": next(branches), "cost": 0.1, "turns": 5, "test_cmd": "npm test"})
    monkeypatch.setattr(run_daily, "_has_commits", lambda *a, **k: found(True))
    monkeypatch.setattr(run_daily, "independent_verify",
                        lambda *a, **k: {"pass": False, "test_rc": 1, "test_log": "L"})
    monkeypatch.setattr(run_daily, "_dump_branch_diff", lambda *a, **k: None)
    verdicts = iter([{"verdict": "revise", "feedback_section": "fix1"},
                     {"verdict": "revise", "feedback_section": "fix2"}])
    monkeypatch.setattr(run_daily, "run_persona", lambda *a, **k: (next(verdicts), {"cost": 0.0, "turns": 1}))
    recon_calls, fake_rec = _recon_recorder()
    monkeypatch.setattr(run_daily, "reconcile_pr", fake_rec)

    # Act
    rec = run_daily.dispatch_one(_entry(), _prof(repo), "20260718", _args())
    # Assert：用满 2 轮 → 降级 interrupted_pr（不 drop）
    assert rec["verify_round"] == 2 and rec["verify_verdict"] == "revise"
    assert recon_calls == [True]
    assert rec["status"] == "interrupted_pr"


def test_dispatch_no_branch_stall(tmp_path, monkeypatch):
    # Arrange：dev 建分支前崩（无 branch）—— stall 救不了，不进 verify、不重投
    _setup(tmp_path, monkeypatch)
    _admit(monkeypatch)
    repo = _repo(tmp_path)
    monkeypatch.setattr(run_daily, "_run_dev_agent", lambda *a, **k: None)
    pv_calls: list[str] = []
    monkeypatch.setattr(run_daily, "run_persona",
                        lambda *a, **k: (pv_calls.append("hit"), {"verdict": "pass"})[1])
    # 真 reconcile（无 branch 早返，不触 subprocess）

    # Act
    rec = run_daily.dispatch_one(_entry(), _prof(repo), "20260718", _args())
    # Assert：dev_killed、未触 pa-verify、status=fail（reconcile 无 branch+dev_killed）
    assert rec["dev_killed"] is True
    assert rec["verify_round"] is None and rec["verify_verdict"] is None
    assert pv_calls == []                       # 从未调 pa-verify
    assert rec["status"] == "fail"


def test_dispatch_blocked_test_gate(tmp_path, monkeypatch):
    # Arrange：dev r1 跑完 dev loop 但测试发布门拦截（exit 14 JSON）→ 终态短路，不进 verify/reconcile、不开 PR
    _setup(tmp_path, monkeypatch)
    _admit(monkeypatch)
    repo = _repo(tmp_path)
    monkeypatch.setattr(run_daily, "_run_dev_agent",
                        lambda *a, **k: {"ok": False, "blocked_by_gate": True, "gate_status": "test_failed",
                                         "gate_reason": "evidence not fresh", "branch": "auto/r1",
                                         "test_status": "red", "evidence_fresh": False,
                                         "cost": 0.1, "turns": 5, "test_cmd": "npm test"})
    pv_calls: list[str] = []
    monkeypatch.setattr(run_daily, "run_persona",
                        lambda *a, **k: (pv_calls.append("hit"), {"verdict": "pass"})[1])
    recon_calls, fake_rec = _recon_recorder()
    monkeypatch.setattr(run_daily, "reconcile_pr", fake_rec)

    # Act
    rec = run_daily.dispatch_one(_entry(), _prof(repo), "20260718", _args())
    # Assert：终态 blocked_test_gate；带门诊断字段；dev_killed=False；未触 pa-verify/reconcile；未进 r2
    assert rec["status"] == "blocked_test_gate"
    assert rec["gate_status"] == "test_failed"
    assert rec["gate_reason"] == "evidence not fresh"
    assert rec["test_status"] == "red" and rec["evidence_fresh"] is False
    assert rec["branch"] == "auto/r1"          # 分支已建（门在 commit 前），保留待 triage
    assert rec["dev_killed"] is False
    assert rec["verify_round"] is None and rec["verify_verdict"] is None
    assert pv_calls == []                       # 从未调 pa-verify（门拦截先于 verify）
    assert recon_calls == []                    # 从未对账（不补开 PR、不删分支）
    assert "test_failed" in rec["skip_reason"]
