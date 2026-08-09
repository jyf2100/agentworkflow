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
# ─── task 3.5：planned smoke（--dispatch-skip-dev）parity——不 emit running，reduce 落 PLANNED ──
def test_dispatch_skip_dev_smoke_emits_planned_no_running(tmp_path, monkeypatch):
    """task 3.5 + spec scenario 19：--dispatch-skip-dev 零成本 smoke（过准入但不触发 dev loop）→
    journal 只 emit ``planned``（**不** emit running——dev loop 未起跑），reduce 落定 PLANNED == legacy planned。

    planned smoke 是 spec terminal class：它「已过准入、未投递」，IterationStatus 必须 PLANNED（非 RUNNING）。
    running event 表示「开始投递 dev loop」——smoke 不投递，故不应 emit；否则 reducer 归约 RUNNING，
    与 compat ``legacy_status(planned)=PLANNED`` 断裂 → shadow parity 失败（scenario 19）。"""
    _setup(tmp_path, monkeypatch)
    _admit(monkeypatch)
    repo = _repo(tmp_path)
    prof = _prof(repo)
    prof["loop"] = {"journal_shadow": True}   # journal 真写（捕获 planned/running emit）
    args = SimpleNamespace(force=False, dispatch_skip_dev=True, dispatch_limit=None, max_concurrent=1)
    # Act
    rec = run_daily.dispatch_one(_entry(), prof, "20260718", args)
    # Assert — smoke 终态 planned + journal 无 running（reduce→PLANNED = legacy planned）
    assert rec["status"] == "planned"
    import journal as J
    journals = list((tmp_path / "state").rglob("*.journal.jsonl"))
    assert journals, "journal_shadow on → planned event 应落盘"
    evs = J.read_events(journals[0])
    types = [e.event_type for e in evs]
    assert "planned" in types
    assert "running" not in types, f"skip-dev smoke 不应 emit running（dev loop 未起跑）: {types}"


def test_dispatch_green_round1(tmp_path, monkeypatch):
    # Arrange：dev r1 出分支+测试绿+pa-verify 判绿
    _setup(tmp_path, monkeypatch)
    _admit(monkeypatch)
    repo = _repo(tmp_path)
    monkeypatch.setattr(run_daily, "_run_dev_agent",
                        lambda *a, **k: {"branch": "auto/r1", "cost": 0.1, "turns": 5, "test_cmd": "npm test"})
    monkeypatch.setattr(run_daily, "_has_commits", lambda *a, **k: found(True))
    _green_log = tmp_path / "green_test.out"   # task 4.2：green evidence 读 test_log 持久化为 artifact（须真实文件）
    _green_log.write_text("all tests passed", encoding="utf-8")
    monkeypatch.setattr(run_daily, "independent_verify",
                        lambda *a, **k: {"pass": True, "test_rc": 0, "test_log": str(_green_log)})
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


def test_dispatch_green_evidence_artifact_failure_blocks_not_published(tmp_path, monkeypatch):
    """task 4.2 evidence integrity：green test result（机械绿）但 evidence artifact 持久化失败
    （artifact_store.store raise → 模拟磁盘满/IO 错）→ 不当 fresh green evidence → ``blocked_evidence``
    终态（不 pr_open/published、不进 pa-verify、不 reconcile）。spec verified-publication-integrity
    「Test artifact write fails」：无法持久化/校验的 green result 不得成 complete fresh green evidence，
    记 integrity-block reason。"""
    _setup(tmp_path, monkeypatch)
    _admit(monkeypatch)
    repo = _repo(tmp_path)
    monkeypatch.setattr(run_daily, "_run_dev_agent",
                        lambda *a, **k: {"branch": "auto/r1", "cost": 0.1, "turns": 5, "test_cmd": "npm test"})
    monkeypatch.setattr(run_daily, "_has_commits", lambda *a, **k: found(True))
    _green_log = tmp_path / "green_evidence.out"   # 真实文件：read 成功，store 被 mock raise（精确测 store 失败）
    _green_log.write_text("all tests passed", encoding="utf-8")
    monkeypatch.setattr(run_daily, "independent_verify",
                        lambda *a, **k: {"pass": True, "test_rc": 0, "test_log": str(_green_log)})
    monkeypatch.setattr(run_daily, "_dump_branch_diff", lambda *a, **k: None)

    def _boom(*a, **k):   # green evidence artifact 持久化失败（store raise → 模拟磁盘满/IO 错）
        raise IOError("disk full")
    monkeypatch.setattr(run_daily.artifact_store, "store", _boom)
    monkeypatch.setattr(run_daily, "run_persona",
                        lambda *a, **k: ({"verdict": "pass"}, {"cost": 0.0, "turns": 1}))
    recon_calls, fake_rec = _recon_recorder()
    monkeypatch.setattr(run_daily, "reconcile_pr", fake_rec)
    # Act
    rec = run_daily.dispatch_one(_entry(), _prof(repo), "20260718", _args())
    # Assert：不当 green evidence → blocked_evidence（不 pr_open/published、未 reconcile、未进 pa-verify）
    assert rec["status"] == "blocked_evidence"
    assert recon_calls == []
    assert "evidence" in rec.get("skip_reason", "").lower()


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

    def _spy(*a, **k):   # *a/**k 兼容 _dev_cmd 的 feedback_artifact keyword（task 3.4）
        base = k.get("base") or (a[2] if len(a) > 2 else None)
        bases.append(base)
        return real(*a, **k)
    monkeypatch.setattr(run_daily, "_dev_cmd", _spy)

    # Act
    rec = run_daily.dispatch_one(_entry(), _prof(repo), "20260718", _args())
    # Assert：r2 用 r1 分支做 base；r1 红未 reconcile；r2 绿兜底开 PR；反馈进了 PRD
    assert bases == ["main", "auto/r1"]
    assert rec["verify_round"] == 2 and rec["verify_verdict"] == "pass"
    assert recon_calls == [False]               # 只在判绿时收尾一次
    prd_text = (tmp_path / "state" / "prd" / PROJ / "20260718_test.md").read_text(encoding="utf-8")
    assert "审核反馈（verify 第1轮" in prd_text and "fix X at a.ts" in prd_text


# ─── task 3.3：verify revise creates a new iteration（distinct + references prior + feedback）──
def test_dispatch_revise_creates_distinct_iteration_referencing_prior(tmp_path, monkeypatch):
    """task 3.3 + spec durable-runtime-integration「Iteration identity / Verify revise creates a new
    iteration」：semantic verify 返回 revise → next attempt（round2）得**新 distinct deterministic
    iteration ID**（``next_iteration(2)`` ≠ round1 ``next_iteration(1)``），其 ``agent_finished`` 事件
    references the prior iteration（round1 iter）+ feedback artifact digest（round1 ``verifier_feedback``）。
    planned/running（run 级 seq0）不混入 attempt iteration。"""
    import journal as J
    # Arrange — 同 revise→pass 闭环，但 journal_shadow 开（emit 落盘可观测 iteration）
    _setup(tmp_path, monkeypatch); _admit(monkeypatch); repo = _repo(tmp_path)
    branches = iter(["auto/r1", "auto/r2"])
    monkeypatch.setattr(run_daily, "_run_dev_agent",
                        lambda *a, **k: {"branch": next(branches), "cost": 0.1, "turns": 5, "test_cmd": "npm test"})
    monkeypatch.setattr(run_daily, "_has_commits", lambda *a, **k: found(True))
    monkeypatch.setattr(run_daily, "independent_verify",
                        lambda *a, **k: {"pass": False, "test_rc": 1, "test_log": "L"})
    monkeypatch.setattr(run_daily, "_dump_branch_diff", lambda *a, **k: None)
    verdicts = iter([{"verdict": "revise", "feedback_section": "fix X at a.ts"}, {"verdict": "pass"}])
    monkeypatch.setattr(run_daily, "run_persona", lambda *a, **k: (next(verdicts), {"cost": 0.0, "turns": 1}))
    monkeypatch.setattr(run_daily, "reconcile_pr", lambda *a, **k: None)
    prof = _prof(repo)
    prof["loop"] = {"journal_shadow": True}      # 开 shadow → emit 落盘（baseline 关则 no-op，不可观测 iteration）

    # Act
    run_daily.dispatch_one(_entry(), prof, "20260718", _args())

    # Assert — journal 落盘；round1/round2 agent_finished 用 distinct deterministic iteration
    journals = list((tmp_path / "state").rglob("*.journal.jsonl"))
    assert journals, "journal_shadow on → lifecycle 事件应落盘"
    evs = J.read_events(journals[0])
    agent_fin = [e for e in evs if e.event_type == "agent_finished"]
    assert len(agent_fin) >= 2, "revise→增量重投应有两轮 agent_finished"
    iter_r1, iter_r2 = agent_fin[0].iteration_id, agent_fin[1].iteration_id
    assert iter_r1 != iter_r2, "task 3.3：每轮 distinct deterministic iteration（spec Iteration identity）"
    # next attempt（round2）references prior iteration + feedback artifact（spec scenario）
    assert agent_fin[1].payload.get("parent_iteration") == iter_r1, "next attempt 引用 prior iteration"
    assert agent_fin[1].payload.get("parent_feedback_digest"), "next attempt 引用 feedback artifact digest"
    # round1 feedback artifact digest 来自 round1 verifier_feedback 事件（一致性）
    vf_r1 = [e for e in evs if e.event_type == "verifier_feedback" and e.payload.get("round") == 1]
    assert vf_r1, "round1 revise → 应落 verifier_feedback artifact 事件"
    assert agent_fin[1].payload["parent_feedback_digest"] == vf_r1[0].payload["digest"]


# ─── task 3.4：driven retry prompt 从 immutable PRD + journal feedback artifact 构造 ──
def test_dispatch_driven_retry_injects_feedback_artifact(tmp_path, monkeypatch):
    """task 3.4 + design.md:42「recovery context is generated from the immutable PRD plus referenced
    artifacts」: driven（``journal_driven_dispatch``）模式 retry（round2）的 dev-agent 命令 inject
    ``--feedback-artifact <path>``——path 来自 ``build_recovery_context`` 从 journal 抽的 last
    ``verifier_feedback`` artifact（spec「remaining acceptance criteria + verified journal artifacts」）。
    driven 摘除 PRD 追加（task 3.2）→ 反馈真源在 artifact，retry prompt 须从 artifact 读，不依赖 PRD 反馈节。
    baseline（driven 关）retry 命令无 ``--feedback-artifact``（照旧读 PRD 反馈节）。"""
    # Arrange — driven 开（preflight⇒shadow 开）+ revise→pass 闭环
    _setup(tmp_path, monkeypatch); _admit(monkeypatch); repo = _repo(tmp_path)
    branches = iter(["auto/r1", "auto/r2"])
    monkeypatch.setattr(run_daily, "_run_dev_agent",
                        lambda *a, **k: {"branch": next(branches), "cost": 0.1, "turns": 5, "test_cmd": "npm test"})
    monkeypatch.setattr(run_daily, "_has_commits", lambda *a, **k: found(True))
    monkeypatch.setattr(run_daily, "independent_verify",
                        lambda *a, **k: {"pass": False, "test_rc": 1, "test_log": "L"})
    monkeypatch.setattr(run_daily, "_dump_branch_diff", lambda *a, **k: None)
    verdicts = iter([{"verdict": "revise", "feedback_section": "fix X at a.ts"}, {"verdict": "pass"}])
    monkeypatch.setattr(run_daily, "run_persona", lambda *a, **k: (next(verdicts), {"cost": 0.0, "turns": 1}))
    monkeypatch.setattr(run_daily, "reconcile_pr", lambda *a, **k: None)
    prof = _prof(repo)
    prof["loop"] = {"journal_shadow": True, "journal_driven_dispatch": True}
    # spy _dev_cmd：捕获每轮命令（base=main 是 round1；base=auto/r1 是 round2 retry）
    cmds: list[tuple[str, list[str]]] = []
    real_cmd = run_daily._dev_cmd

    def _spy(*a, **kw):
        c = real_cmd(*a, **kw)
        cmds.append((kw.get("base", a[2] if len(a) > 2 else "?"), list(c or [])))
        return c
    monkeypatch.setattr(run_daily, "_dev_cmd", _spy)

    # Act
    run_daily.dispatch_one(_entry(), prof, "20260718", _args())

    # Assert — round2（retry，base=auto/r1）inject --feedback-artifact；round1（base=main）无
    assert len(cmds) >= 2, "revise→重投应触发两轮 _dev_cmd"
    r2_cmd = [c for base, c in cmds if base != "main"][0]
    r1_cmd = [c for base, c in cmds if base == "main"][0]
    assert "--feedback-artifact" in r2_cmd, "task 3.4：driven retry 从 feedback artifact 构 prompt"
    # feedback artifact path 是 round1 verifier_feedback 的 artifact path（recovery context 从 journal 抽）
    fa_idx = r2_cmd.index("--feedback-artifact") + 1
    assert r2_cmd[fa_idx] and r2_cmd[fa_idx] != "--source"
    assert "--feedback-artifact" not in r1_cmd, "baseline round1 无 feedback artifact（初始无反馈）"


# ─── task 3.4：dev-agent 侧消费 --feedback-artifact（parse_args + build_prompt inject）──
_DA = None


def _dev_agent():
    """lazy 加载 dev-agent.py（带连字符，importlib；SDK 可 import → exec_module 安全）。"""
    global _DA
    if _DA is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location("_dev_agent_under_test",
                                                      Path(__file__).parent / "dev-agent.py")
        _DA = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_DA)
    return _DA


def test_dev_agent_parse_args_feedback_artifact():
    """task 3.4：dev-agent parse_args 解析 --feedback-artifact（driven retry 反馈源 path）。"""
    da = _dev_agent()
    args = da.parse_args(["--prd", "p.md", "--base", "main", "--feedback-artifact", "/art/fb.txt"])
    assert args["feedback_artifact"] == "/art/fb.txt"
    # baseline（无 --feedback-artifact）默认 None
    assert da.parse_args(["--prd", "p.md"])["feedback_artifact"] is None


def test_dev_agent_parse_args_model():
    """add-per-agent-model-routing：dev-agent parse_args 解析 --model（手动/canary）。

    review follow-up（code-review MED）：推翻 design「dev-agent 不可 import」论据——
    test_verify_loop._dev_agent() 已用 importlib lazy 加载，parse_args --model 可直测。"""
    da = _dev_agent()
    assert da.parse_args(["--prd", "p.md", "--base", "main", "--model", "haiku"])["model"] == "haiku"
    # baseline（无 --model）默认 None → 走 PA_DEV_MODEL env / roc 默认
    assert da.parse_args(["--prd", "p.md"])["model"] is None
    # 空 flag：--model "" → model="" （parse_args 层不吞；_build_options 用 is-not-None 精确语义）
    assert da.parse_args(["--prd", "p.md", "--model", ""])["model"] == ""


def test_dev_agent_build_prompt_injects_feedback_artifact(tmp_path):
    """task 3.4：build_prompt 在 args[feedback_artifact] 时 inject「上轮 verify 反馈」段（driven 模式
    PRD 不可变 task 3.2 摘除追加 → 反馈真源在 artifact，prompt 须从此读，非 PRD 反馈节）。"""
    da = _dev_agent()
    fb = tmp_path / "fb.txt"
    fb.write_text("修复 X：src/a.py:L10 token=ghp_xxx 须脱敏", encoding="utf-8")
    args = {"prd": "p.md", "source": None, "base": "main", "dry_run": False,
            "branch_prefix": "pa-dev", "feedback_artifact": str(fb), "help": False}
    prompt = da.build_prompt(args, "# PRD\n实现 X\n\n## 验收标准\n- A", "auto/b")
    assert "上轮 verify 反馈" in prompt and "src/a.py:L10" in prompt


def test_dev_agent_build_prompt_baseline_no_feedback_block():
    """baseline（feedback_artifact=None）build_prompt 不含反馈段（照旧读 PRD 反馈节，决策零变化）。"""
    da = _dev_agent()
    args = {"prd": "p.md", "source": None, "base": "main", "dry_run": False,
            "branch_prefix": "pa-dev", "feedback_artifact": None, "help": False}
    prompt = da.build_prompt(args, "# PRD", "auto/b")
    assert "上轮 verify 反馈" not in prompt


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
