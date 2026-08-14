#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_verify_loop_branch.py — verify-loop-discipline change task A 段：PR 生命周期跟需求不跟 stamp。

覆盖 openspec change `verify-loop-discipline` 的 A 段（结构）TDD：
    - C1 / A1：``make_branch`` 跟 slug 不跟 stamp（verify revise 同分支单 PR 的基石）
    - C1a / A1a：``--phase checkout`` 分支适配（round2 复用已存在分支，不崩 already exists）
    - C2 / A2：``--phase publish`` 开 PR 幂等（已有 open PR → 只 push 不 create）

根因（proposal §A）：dev-agent ``branch = f"{prefix}/{stamp}-{slug}"``（stamp 含时分），verify revise
每轮重调 dev-agent 是独立进程 → 不同 stamp → 不同分支 → 每轮新 PR（#55/#56 堵满在途上限 9 天）。
修：branch 跟 slug（A1）、checkout 复用同分支（A1a）、PR 开启幂等（A2）。

dev-agent.py 文件名连字符不可 import → phase 测试用 subprocess + CLI（对齐 test_dev_agent_merge
范式：fake gh 经 PATH 注入 transparent + 真实 git tmp repo）。

跑：``python -m pytest scripts/test_verify_loop_branch.py -q``
AAA 结构（Arrange / Act / Assert）。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import slug_utils  # noqa: E402
from test_dev_agent_merge import FAKE_GH_SCRIPT, _git, _make_repo  # noqa: E402

DEV_AGENT_PY = Path(__file__).parent / "dev-agent.py"


# ─── C1 / A1：make_branch 跟 slug 不跟 stamp ──────────────────────────
def test_make_branch_is_slug_only_no_stamp():
    """C1：branch = ``prefix/slug``，不含 stamp 时分。

    verify revise 各轮重调 dev-agent 是独立进程；branch 若含 ``stamp()`` 时分 → 每轮不同分支 → 每轮新 PR
    （#55/#56 根因）。``make_branch`` 只用 prefix+slug，从源头保证「需求（slug）不变 → branch 不变」。
    """
    # Arrange / Act
    br = slug_utils.make_branch("auto", "agent-screenshot-verify")
    # Assert：纯 prefix/slug
    assert br == "auto/agent-screenshot-verify"
    # 不含 stamp 时分模式（YYYYMMDD-HHMM）—— 旧 f"{prefix}/{s}-{slug}" 会含
    assert not re.search(r"\d{8}-\d{4}", br), f"branch 不应含 stamp 时分: {br}"


def test_make_branch_deterministic_across_calls():
    """C1：同 slug 多次调用结果相同（确定性 = 跨进程 / 跨 cron 稳定）。

    旧 branch 含 ``stamp()`` → 同一需求 round1 / round2（不同进程）branch 不同。``make_branch`` 去 stamp 后，
    round2 dev-agent 进程算出的 branch == round1 → checkout 复用同分支 → 同 PR 累积 commits。
    """
    a = slug_utils.make_branch("auto", "feat-x")
    b = slug_utils.make_branch("auto", "feat-x")
    assert a == b == "auto/feat-x"


def test_make_branch_preserves_slug_distinction():
    """C1 回归：不同 slug 仍不同分支（``make_branch`` 不应意外归并）。

    对齐 ``dev_slugify`` 区分性（test_dev_agent_source.test_dev_slugify_known_inputs canary 判据 c）：
    同日同 project 不同 desc 的 slug 须保留区分，否则幂等前置闸子串匹配误判「已投递」→ 静默跳过。
    """
    sa = "20260805_agent-screenshot-verify"
    sb = "20260805_agent-screenshot-canary"
    assert slug_utils.make_branch("auto", sa) != slug_utils.make_branch("auto", sb)


# ─── phase 测试基建：fake gh + subprocess 跑 dev-agent CLI（复用 merge 测试范式）─────
@pytest.fixture()
def fake_gh(tmp_path, monkeypatch):
    """部署 fake gh 到 tmp_path/bin + 注入 PATH/PA_GH_FAKE_STATE（复用 test_dev_agent_merge 范式）。

    dev-agent gh() 经 PATH 解析 gh → 命中 fake gh（transparent，dev-agent 代码零改动）。
    每测试独立 tmp_path → 独立 gh-state.json（PR 状态隔离）。
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "gh").write_text(FAKE_GH_SCRIPT, encoding="utf-8")
    (bin_dir / "gh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ.get('PATH', '')}")
    monkeypatch.setenv("PA_GH_FAKE_STATE", str(tmp_path / "gh-state.json"))


def _run_phase(wt: Path, phase: str, **flags):
    """subprocess 跑 dev-agent --phase <phase>（cwd=worktree），解析末行 stdout JSON。返回 (proc, payload|None)。"""
    cmd = [sys.executable, str(DEV_AGENT_PY), "--phase", phase]
    for k, v in flags.items():
        cmd += ["--" + k.replace("_", "-"), str(v)]
    r = subprocess.run(cmd, cwd=str(wt), capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=90)
    lines = (r.stdout or "").strip().splitlines()
    payload = json.loads(lines[-1]) if lines else None
    return r, payload


# ─── C1a / A1a：--phase checkout 分支适配（round2 复用已存在分支不崩）─────────
def test_checkout_round1_creates_branch(tmp_path, fake_gh):
    """C1a：round1 分支不存在 → checkout -b 新建（不回归旧创建路径）。"""
    # Arrange
    wt = _make_repo(tmp_path)
    # Act
    r, payload = _run_phase(wt, "checkout", branch="auto/feat", base="main")
    # Assert
    assert payload is not None and payload["ok"], f"stderr={r.stderr[:300]!r}"
    assert _git(wt, "branch", "--show-current") == "auto/feat"


def test_checkout_round2_reuses_existing_branch(tmp_path, fake_gh):
    """C1a/A1a：round2 分支已存在 → checkout 复用（旧无条件 ``-b`` 崩 ``already exists`` exit 13）。

    r1 P1-1：A1 去 stamp 后 round2 在同 worktree 跑 ``git checkout -b auto/{slug}`` →
    ``fatal: branch already exists`` → exit 13，单 PR 同分支目标物理不可达。
    """
    # Arrange
    wt = _make_repo(tmp_path)
    _, p1 = _run_phase(wt, "checkout", branch="auto/feat", base="main")   # round1 建
    assert p1 is not None and p1["ok"]
    # Act：round2 同分支（A1 后 branch 确定性 → 同名）
    r, payload = _run_phase(wt, "checkout", branch="auto/feat", base="main")
    # Assert
    assert r.returncode == 0, f"round2 应 checkout 复用非崩（旧 -b exit 13）: stderr={r.stderr[:300]!r}"
    assert payload is not None and payload["ok"]
    assert _git(wt, "branch", "--show-current") == "auto/feat"


# ─── R3-A F5：phase 缺 branch/base 参数 fail-fast（防建名为 "None" 的分支）─────────
def test_phase_missing_branch_fails_fast(tmp_path, fake_gh):
    """R3-A F5：checkout/publish 缺 --branch → exit 10 + ok=False（不真建 "None" 分支）。"""
    wt = _make_repo(tmp_path)
    for phase in ("checkout", "publish"):
        r, p = _run_phase(wt, phase, base="main")   # 缺 branch
        assert r.returncode == 10, f"{phase} 缺 branch 应 exit 10: rc={r.returncode}"
        assert p is not None and not p["ok"]
    # 未误建名为 None 的分支
    assert "None" not in _git(wt, "branch", "--list")


# ─── C2 / A2：--phase publish 开 PR 幂等 ──────────────────────────────
def test_publish_round1_creates_pr(tmp_path, fake_gh):
    """C2：round1 无 open PR → create（pr_reused=False），PR URL 落地。"""
    # Arrange
    wt = _make_repo(tmp_path)
    _run_phase(wt, "checkout", branch="auto/feat", base="main")
    (wt / "f1.txt").write_text("r1\n", encoding="utf-8")
    # Act
    r, p = _run_phase(wt, "publish", branch="auto/feat", base="main", prd="feat.md")
    # Assert
    assert p is not None and p["ok"], f"stderr={r.stderr[:300]!r}"
    assert p["pr_url"]
    assert p["pr_reused"] is False


def test_publish_round2_reuses_pr_pushes_only(tmp_path, fake_gh):
    """C2/A2：round2 同 branch 已有 open PR → 只 push 不 create（单 PR 累积，#55/#56 根因修复）。"""
    # Arrange
    wt = _make_repo(tmp_path)
    _run_phase(wt, "checkout", branch="auto/feat", base="main")
    (wt / "f1.txt").write_text("r1\n", encoding="utf-8")
    _, p1 = _run_phase(wt, "publish", branch="auto/feat", base="main", prd="feat.md")
    assert p1 is not None and p1["ok"] and p1["pr_reused"] is False
    # Act：round2 新 commit → push 并入已有 open PR
    (wt / "f2.txt").write_text("r2\n", encoding="utf-8")
    r, p2 = _run_phase(wt, "publish", branch="auto/feat", base="main", prd="feat.md")
    # Assert
    assert p2 is not None and p2["ok"], f"stderr={r.stderr[:300]!r}"
    assert p2["pr_reused"] is True, "round2 应复用已有 open PR（只 push 不 create）"
    assert p2["pr_url"] == p1["pr_url"], "round2 须是同一 PR"
    # fake-gh 状态恒 1 个 PR（无第二次 create）
    state_path = Path(os.environ["PA_GH_FAKE_STATE"])
    gh_state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {"prs": []}
    assert len(gh_state["prs"]) == 1, f"不应 create 第二个 PR: {gh_state}"
    # origin 分支累积两轮 commits（round1 + round2，同分支累积语义）
    _git(wt, "fetch", "-q", "origin", "auto/feat")
    assert _git(wt, "rev-list", "--count", "main..origin/auto/feat") == "2"


# ─── A4 round marker：--phase publish commit 带 pa-dev: round N（供 commit 级回滚定位轮边界）──
def test_publish_commit_carries_round_marker(tmp_path, fake_gh):
    """A4：--round N → commit message 带 ``pa-dev: round N`` marker（git log --grep 可定位轮边界）。"""
    # Arrange
    wt = _make_repo(tmp_path)
    _run_phase(wt, "checkout", branch="auto/feat", base="main")
    (wt / "f1.txt").write_text("r1\n", encoding="utf-8")
    # Act
    r, p = _run_phase(wt, "publish", branch="auto/feat", base="main", prd="feat.md", round=1)
    # Assert
    assert p is not None and p["ok"], f"stderr={r.stderr[:300]!r}"
    assert "pa-dev: round 1" in _git(wt, "log", "-1", "--format=%B")
    # 不传 --round（手动 baseline / 单轮）→ 无 marker（零噪声）
    (wt / "f2.txt").write_text("r2\n", encoding="utf-8")
    _, p2 = _run_phase(wt, "publish", branch="auto/feat", base="main", prd="feat.md")
    assert p2["ok"]
    assert "pa-dev: round" not in _git(wt, "log", "-1", "--format=%B")


# ─── A3 / A4 / A4a（run_daily 侧：worktree 跟 slug + 同分支累积 + prompt/diff 语义同步）──────
import run_daily  # noqa: E402
from external_state import found  # noqa: E402
from test_verify_loop import _admit, _args, _entry, _prof, _repo, _setup  # noqa: E402


def _mock_green(monkeypatch, tmp_path, dev_ret=None, verdicts=None):
    """dispatch_one 闭环 mock 套件（对齐 test_verify_loop 范式）：dev→independent_verify→pa-verify 全绿默认。"""
    if dev_ret:
        monkeypatch.setattr(run_daily, "_run_dev_agent", lambda *a, **k: dict(next(dev_ret)))
    monkeypatch.setattr(run_daily, "_has_commits", lambda *a, **k: found(True))
    green_log = tmp_path / "green_test.out"
    green_log.write_text("all tests passed", encoding="utf-8")
    monkeypatch.setattr(run_daily, "independent_verify",
                        lambda *a, **k: {"pass": True, "test_rc": 0, "test_log": str(green_log)})
    monkeypatch.setattr(run_daily, "_dump_branch_diff", lambda *a, **k: None)
    if verdicts:
        monkeypatch.setattr(run_daily, "run_persona",
                            lambda *a, **k: (dict(next(verdicts)), {"cost": 0.0, "turns": 1}))
    monkeypatch.setattr(run_daily, "reconcile_pr", lambda *a, **k: None)


def test_worktree_path_follows_slug_not_stamp(tmp_path, monkeypatch):
    """A3：dispatch worktree 名 = ``.worktrees/<slug>``（非 ``stamp-slug``）。

    跨 cron 确定性清理（同 slug → 同路径 → remove-if-exists 自清）+ 与分支名一致（A1 branch=auto/<slug>）。
    spec verified-publication-integrity「Worktree identifier follows the requirement slug」。
    """
    # Arrange
    _setup(tmp_path, monkeypatch); _admit(monkeypatch)
    repo = _repo(tmp_path)
    wt_adds: list[str] = []

    def _spy(cmd, *a, **k):
        if "worktree" in cmd and "add" in cmd:
            wt_adds.append(cmd[cmd.index("--detach") + 1])
        return (0, "", "")
    monkeypatch.setattr(run_daily, "_run_capture", _spy)
    _mock_green(monkeypatch, tmp_path,
                dev_ret=iter([{"branch": "auto/20260718-test", "cost": 0.1, "turns": 5, "test_cmd": "npm test"}]),
                verdicts=iter([{"verdict": "pass"}]))
    # Act
    run_daily.dispatch_one(_entry(), _prof(repo), "20260805", _args())
    # Assert：worktree 路径 = .worktrees/<slug>，不含 stamp 时分（YYYYMMDD-HHMM）
    assert wt_adds, "应创建 worktree"
    slug = slug_utils.dev_slugify("20260718_test")
    assert wt_adds == [str(Path(repo) / ".worktrees" / slug)]
    assert not re.search(r"\d{8}-\d{4}", wt_adds[0]), f"worktree 路径不应含 stamp: {wt_adds[0]}"


def test_dispatch_round2_same_branch_accumulation(tmp_path, monkeypatch):
    """A4 + A4a③：revise round2 --base 恒项目默认分支（同分支累积）+ --round 透传 + diff 全量。

    旧语义 ``cur_base = branch``（run_daily L2472）→ r2 base=上轮分支（增量重投、分支级隔离）；
    A4 改同分支累积：r2 --base=main + dev-agent（A1a）checkout 复用同分支 → commits 累积进同一 PR（A2）。
    diff（A4a③）随 cur_base 恒=默认分支变全量累积（默认分支..branch 含全部轮次 commits）。
    """
    # Arrange：r1 判红 → r2 判绿
    _setup(tmp_path, monkeypatch); _admit(monkeypatch)
    repo = _repo(tmp_path)
    _mock_green(monkeypatch, tmp_path,
                dev_ret=iter([{"branch": "auto/feat-x"}, {"branch": "auto/feat-x"}]),   # 同分支（A4 真实语义）
                verdicts=iter([{"verdict": "revise", "feedback_section": "fix X at a.ts"}, {"verdict": "pass"}]))
    # spy _dev_cmd：捕获每轮 base（位置参数 3）+ round_n（keyword）
    calls: list[dict] = []
    real = run_daily._dev_cmd

    def _spy(*a, **k):
        calls.append({"base": k.get("base") or (a[2] if len(a) > 2 else None),
                      "round": k.get("round_n", 0)})
        return real(*a, **k)
    monkeypatch.setattr(run_daily, "_dev_cmd", _spy)
    # spy _dump_branch_diff：捕获 base_ref（A4a③：round2 diff 应全量=默认分支..branch）
    diff_bases: list[str] = []
    monkeypatch.setattr(run_daily, "_dump_branch_diff",
                        lambda repo, base_ref, branch, out, *a, **k: diff_bases.append(base_ref))
    # Act
    rec = run_daily.dispatch_one(_entry(), _prof(repo), "20260718", _args())
    # Assert：两轮 base 恒 main（同分支累积）+ round 递增透传 + diff 两轮都全量 + r2 判绿闭环
    assert [c["base"] for c in calls] == ["main", "main"], \
        f"A4：round2 base 应=项目默认分支（同分支累积），实际 {calls}"
    assert [c["round"] for c in calls] == [1, 2], "--round 应逐轮透传（commit marker 源）"
    assert diff_bases == ["main", "main"], f"A4a③：round2 diff 应全量（默认分支..branch），实际 {diff_bases}"
    assert rec["verify_round"] == 2 and rec["verify_verdict"] == "pass"


def test_dispatch_round2_no_changes_terminates_no_progress(tmp_path, monkeypatch):
    """R3-A F2：round≥2 dev 零新 commit（publish no_changes）→ no_progress 提前终止。

    同分支累积（A4）后 has_commits(main,branch) 恒 True（round1 commits 在）→ 不加此门会白跑一轮
    verify（同一全量 diff 大概率重复 revise）→ 耗尽 rounds 退化 interrupted_pr。
    """
    # Arrange：r1 判红 → r2 dev 零产出（no_changes）
    _setup(tmp_path, monkeypatch); _admit(monkeypatch)
    repo = _repo(tmp_path)
    iv_calls: list[int] = []

    (tmp_path / "g.out").write_text("all tests passed", encoding="utf-8")   # green evidence artifact 源文件

    def _iv_spy(*a, **k):
        iv_calls.append(1)
        return {"pass": True, "test_rc": 0, "test_log": str(tmp_path / "g.out")}
    _mock_green(monkeypatch, tmp_path,
                dev_ret=iter([{"branch": "auto/r1"}, {"branch": "auto/r1", "no_changes": True}]),
                verdicts=iter([{"verdict": "revise", "feedback_section": "fix X"}] * 2))
    monkeypatch.setattr(run_daily, "independent_verify", _iv_spy)   # 在 _mock_green 之后包 spy
    # Act
    rec = run_daily.dispatch_one(_entry(), _prof(repo), "20260718", _args())
    # Assert：no_progress 终止（非白跑耗尽 interrupted_pr）+ round2 不再跑 verify
    assert rec["status"] == "no_progress", f"零增量应 no_progress，实际 {rec.get('status')}"
    assert rec["verify_round"] == 2
    assert len(iv_calls) == 1, f"round2 零增量不应再跑 independent_verify，实际 {len(iv_calls)} 次"


# ─── A5：reconcile_pr 兜底开 PR 与 A2 幂等对齐（TOCTOU 复用，非 blocked）──────────────
def test_reconcile_create_race_reuses_open_pr(monkeypatch):
    """A5：补开 PR 撞并发已开的同 branch open PR（TOCTOU）→ 复用录入，不 blocked_external_state。

    lookup NOT_FOUND 后、create 前若另一进程已开同 branch PR（graph 路径/并发 cron），GitHub 拒 create
    （"a pull request for branch already exists"）。对齐 A2 幂等语义：create 失败 → 重查 open PR，
    FOUND → 复用（pr_reused 语义）；仍无 → 才 blocked_external_state（fail-safe 原样）。
    """
    import subprocess as sp
    pr_list_calls: list[int] = []

    class _P:   # 可设 rc 的 fake Proc
        def __init__(self, stdout="", rc=0):
            self.stdout, self.returncode, self.stderr = stdout, rc, ""

    def fake_run(cmd, *a, **k):
        if "pr" in cmd and "list" in cmd:
            pr_list_calls.append(1)
            # 第 1 次（reconcile 入口对账）无 PR；第 2 次（create 失败重查）并发已开 open PR
            return _P('[{"url": "http://pr/9", "state": "OPEN"}]') if len(pr_list_calls) > 1 else _P("[]")
        if "pr" in cmd and "create" in cmd:
            return _P("", rc=1)                     # GitHub 拒：branch 已有 open PR
        if "log" in cmd:
            return _P("deadbeef commit\n")          # has_commit True
        return _P("")
    monkeypatch.setattr(sp, "run", fake_run)

    rec = {"branch": "auto/feat", "prd_path": "p", "dev_killed": False, "stalled": False}
    run_daily.reconcile_pr("repo", "o/r", rec, "main", "slug", interrupted=True)
    # Assert：复用并发已开的 PR（非 blocked），status 走 interrupted 收尾语义
    assert rec["pr_url"] == "http://pr/9"
    assert rec["status"] == "interrupted_pr", f"TOCTOU 应复用 open PR，实际 {rec.get('status')}"


# ─── B2/C3：install 基建失败 → blocked_infra 升人工，不进 revise（不消耗 verify round）──────
def test_dispatch_install_fail_blocks_infra_not_revise(tmp_path, monkeypatch):
    """C3/B2：install_rc≠0（基建失败，三态 UNKNOWN）→ terminal blocked_infra，不喂 pa-verify。

    spec verified-dev-execution：基建失败 = 系统无法判定代码是否正确 → 升人工，绝不当 revise 信号
    （让 dev 修它修不了的环境问题 = 无意义回环）。
    """
    # Arrange
    _setup(tmp_path, monkeypatch); _admit(monkeypatch)
    repo = _repo(tmp_path)
    persona_calls: list[int] = []
    monkeypatch.setattr(run_daily, "run_persona",
                        lambda *a, **k: (persona_calls.append(1) or ({"verdict": "revise", "feedback_section": "x"}, {"cost": 0, "turns": 1})))
    _mock_green(monkeypatch, tmp_path, dev_ret=iter([{"branch": "auto/feat"}]))
    monkeypatch.setattr(run_daily, "independent_verify", lambda *a, **k: {
        "pass": False, "install_rc": 2, "test_rc": None, "note": "npm ci 失败"})
    # Act
    rec = run_daily.dispatch_one(_entry(), _prof(repo), "20260718", _args())
    # Assert：blocked_infra 终态 + 未进 revise（pa-verify 未被调、round 未消耗）
    assert rec["status"] == "blocked_infra", f"基建失败应 blocked_infra，实际 {rec.get('status')}"
    assert persona_calls == [], "install 失败不得喂 pa-verify（不进 revise loop）"
    assert rec.get("verify_round", 1) == 1


# ─── B2a：Python 仓 install 闸（缺依赖 test_rc≠0 不再被当代码红）────────────────
def test_independent_verify_python_install_gate(tmp_path, monkeypatch):
    """B2a：Python 仓加 install 步骤（profile install_cmd 优先，fallback 仓有打包文件 → pip install -e '.[dev]'）。

    install_rc≠0 → 提前返回（test_rc 留 None → 上游 blocked_infra），不再硬编码 install_rc=0 把
    缺依赖的 test_rc≠0 当代码红喂 revise（r1 F-B2：对 Python 仓 B2 失效）。
    """
    # Arrange：Python 仓骨架（无 package.json、有 pyproject.toml）+ 分支
    import subprocess as sp
    repo = tmp_path / "pyrepo"; repo.mkdir()
    for c in (["git", "init", "-q", "-b", "main"], ["git", "config", "user.email", "t@t"],
              ["git", "config", "user.name", "t"], ["git", "commit", "-q", "--allow-empty", "-m", "init"],
              ["git", "branch", "auto/x"]):
        sp.run(c, cwd=str(repo), check=True, capture_output=True)
    (repo / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    # worktree add 走被 mock 的 _run_capture（不落盘）→ 预建 vt 骨架供 install 探测（vt 内容 = branch checkout）
    vt = repo / ".worktrees" / "verify-20260814-s"; vt.mkdir(parents=True)
    (vt / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    caps: list[str] = []

    def fake_cap(cmd, *a, **k):
        s = " ".join(cmd); caps.append(s)
        rc = 1 if "pip install" in s else 0        # install 失败；worktree add / test 不到达
        return (rc, "", "")
    monkeypatch.setattr(run_daily, "_run_capture", fake_cap)
    # subprocess.run 不 mock——worktree add/remove 真实执行（install 探测的是 vt 内打包文件，需真 checkout）
    log_file = tmp_path / "run.log"; log_file.write_text("", encoding="utf-8")
    # Act
    out = run_daily.independent_verify(str(repo), "auto/x", "20260814", "s", log_file, prof={},
                                       test_cmd_hint="python -m pytest -q")
    # Assert：install 闸生效（install_rc=1、test 未跑）+ 走了 pip install fallback
    assert out["install_rc"] == 1
    assert out["test_rc"] is None
    assert any("pip install" in c for c in caps), f"Python 仓应执行 install 步骤: {caps}"


# ─── B3/B4/C4-C6：verify_prompt 三锚源 + 稳定 id + 维度裁剪 + 无 verdict 机械映射 ───────
_PRD_MD = """# 需求 X

## 验收标准
- [ ] 登录页支持手机号验证码登录
- [ ] 连续错 5 次锁定 10 分钟

## 其他
无关内容不进验收点。
"""


def test_extract_acceptance_ids_stable(tmp_path):
    """B3/SHALL2：PRD 验收点提取稳定 id（slug-derived，跨轮稳定——同 PRD 文件同序号）。"""
    prd = tmp_path / "20260718_test.md"; prd.write_text(_PRD_MD, encoding="utf-8")
    ids1 = run_daily._extract_acceptance_ids(prd, "20260718_test")
    ids2 = run_daily._extract_acceptance_ids(prd, "20260718_test")   # 再调（= round2 重复解析）
    assert [i["id"] for i in ids1] == ["20260718-test:ac1", "20260718-test:ac2"], ids1
    assert ids1 == ids2, "同 PRD 跨轮（重解析）id 须稳定"
    assert "验证码" in ids1[0]["text"]


def test_verify_prompt_no_mechanical_verdict_mapping(tmp_path):
    """B3/SHALL3：prompt 无 test_rc→verdict 硬编码（绿≠自动 pass），verdict 交 persona 三维度综合。"""
    prd = tmp_path / "20260718_test.md"; prd.write_text(_PRD_MD, encoding="utf-8")
    p = run_daily.verify_prompt(str(prd), "auto/b", "main", tmp_path / "d.diff",
                                {"test_rc": 0, "test_log": "L"}, 1, {"name": "cc"})
    # 机械映射行删除：不得出现「测试绿 → verdict=pass」式指令
    assert "测试绿 → verdict=pass" not in p
    assert "测试红 → verdict=revise" not in p
    # 三维度指令 + 三锚源齐备（spec scenario：no single source is the sole input）
    for kw in ("功能对齐", "开发遗漏", "质量达标"):
        assert kw in p, f"缺三维度指令: {kw}"
    for kw in ("PRD（验收标准在此）", "git diff", "测试输出"):
        assert kw in p
    # 稳定 id 列表注入（非测试缺陷锚）
    assert "20260718-test:ac1" in p and "20260718-test:ac2" in p


def test_verify_prompt_dimensions_pruned_per_profile(tmp_path):
    """B4/SHALL4：维度集按 profile 裁剪——frontend 含 UX/响应式；纯后端省略。"""
    prd = tmp_path / "20260718_test.md"; prd.write_text(_PRD_MD, encoding="utf-8")
    p_fe = run_daily.verify_prompt(str(prd), "auto/b", "main", tmp_path / "d.diff",
                                   {"test_rc": 0, "test_log": "L"}, 1,
                                   {"name": "cc", "review_profile": "frontend"})
    p_be = run_daily.verify_prompt(str(prd), "auto/b", "main", tmp_path / "d.diff",
                                   {"test_rc": 0, "test_log": "L"}, 1,
                                   {"name": "cc", "review_profile": "backend"})
    assert "UX" in p_fe and "响应式" in p_fe
    assert "UX" not in p_be and "响应式" not in p_be, "纯后端 profile 不得含 UX/响应式维度"
    assert "安全" in p_be, "后端保留安全/性能/兼容"


def test_dispatch_green_revise_enters_loop(tmp_path, monkeypatch):
    """C4：测试绿但 persona 判 revise（语义缺陷）→ 正常进 revise loop（编排器不拦「绿 revise」）。"""
    _setup(tmp_path, monkeypatch); _admit(monkeypatch)
    repo = _repo(tmp_path)
    _mock_green(monkeypatch, tmp_path,
                dev_ret=iter([{"branch": "auto/feat-x"}, {"branch": "auto/feat-x"}]),
                verdicts=iter([{"verdict": "revise", "feedback_section": "AC1 未实现：登录页缺验证码"},
                               {"verdict": "pass"}]))
    rec = run_daily.dispatch_one(_entry(), _prof(repo), "20260718", _args())
    # Assert：绿 revise 进 loop（round2 跑了）→ 最终 pass；feedback 语义锚（AC id）透传无拦
    assert rec["verify_round"] == 2 and rec["verify_verdict"] == "pass"


def test_dispatch_all_green_single_pass(tmp_path, monkeypatch):
    """C6：install ok + test 绿 + 无语义缺陷 → pass（normal path 不回归，单轮）。"""
    _setup(tmp_path, monkeypatch); _admit(monkeypatch)
    repo = _repo(tmp_path)
    rounds: list[int] = []
    real = run_daily._dev_cmd

    def _spy(*a, **k):
        rounds.append(k.get("round_n", 0)); return real(*a, **k)
    monkeypatch.setattr(run_daily, "_dev_cmd", _spy)
    _mock_green(monkeypatch, tmp_path,
                dev_ret=iter([{"branch": "auto/feat-x"}]),
                verdicts=iter([{"verdict": "pass"}]))
    rec = run_daily.dispatch_one(_entry(), _prof(repo), "20260718", _args())
    assert rec["verify_verdict"] == "pass" and rounds == [1], "全绿单轮即 pass（无多余 round）"


def test_verify_prompt_round2_same_branch_semantics(tmp_path):
    """A4a①②：round≥2 verify_prompt 文本改「同分支累积」（base=项目默认分支、diff 全量、按 round marker
    定位本轮增量），删旧「增量：上次 dev 分支」——A4 改 cur_base 后旧文本自相矛盾（R2-A-5 耦合点）。"""
    # Arrange / Act
    p = run_daily.verify_prompt("PRD", "auto/b", "main", tmp_path / "d.diff",
                                {"test_rc": 1, "test_log": "L"}, 2, {"name": "cc-web-control"})
    # Assert：新语义在、旧语义删
    assert "同分支累积" in p
    assert "增量：上次 dev 分支" not in p
    assert "marker" in p.lower()   # 按 round marker 定位本轮增量的指引
