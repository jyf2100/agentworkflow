#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_dev_agent_merge.py — single-flight-auto-merge task 7.1b：离线 merge drill（dev-agent --phase merge 真实 git 全链路）。

验证 ``run_merge_phase`` 机械执行序列在**真实 git tmp repo**上的行为（subprocess 调 dev-agent，
``cwd=worktree`` → ``REPO_ROOT = Path.cwd()`` 自然指向 tmp repo；**不 import dev-agent**，无 SDK 连带加载副作用）：

  - **CLEAN**：rebase 干净 → ``--no-ff`` merge → ff-only push → ``merge_commit`` 落地 + marker footer 可 grep；
    origin/main 前进到 merge commit，历史保留双 parent（--no-ff 非快进）。
  - **CONFLICT**：rebase 冲突 → triage ``rebase_conflict``，``merge_commit=None``，**main 未碰**
    （origin/main 仍是冲突前的 HEAD；fail-safe：不强合）。
  - **UNKNOWN**：fetch 失败（``main_ref`` 不存在 = base 过时不可信）→ ``UNKNOWN``，main 未碰。

守 D6/ADR-0001：控制面只发 ``--phase merge`` cmd，dev-agent 在目标仓 worktree 内执行（控制面不持 git 写句柄）。
三态判定对齐 ``merge_phase.classify_rebase``（CLEAN 须正向证据；CONFLICT 须 fetched main 明确报告；UNKNOWN 兜底）。

post-merge 验证 + auto-revert（task 4.x）：post-merge-test phase 三态（PASS/FAIL/UNKNOWN）+ revert phase 三态
（REVERTED/CONFLICT/UNKNOWN）的**真实 git 全链路**离线 drill。核心安全网证据链：merge 合入红代码（机械层不跑
测试，照合）→ post-merge-test FAIL → revert REVERTED → main 回绿（D3/D8，spec「Post-merge ... revert」）。
跑：python -m pytest scripts/test_dev_agent_merge.py -q
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

DEV_AGENT_PY = Path(__file__).parent / "dev-agent.py"


# ─── PR 路径离线 drill：fake gh（模拟 GitHub pr list/create/merge，transparent 经 PATH 注入）──────────
# dev-agent gh() 经 PATH 解析 gh → 命中 fake gh（dev-agent 代码不变）。fake gh 用真实 git 在 cwd(worktree)
# 上模拟 GitHub --no-ff PR merge（commit-tree 双 parent + push main），使 PR 路径全链路离线可测（无真实 GitHub）。
FAKE_GH_SCRIPT = r'''#!/usr/bin/env python3
"""fake gh CLI（测试用）：模拟 gh pr list/create/merge。匹配 dev-agent gh() 调用（无 -R，cwd=worktree）。

pr merge 模拟 GitHub --no-ff 合并：fetch head + commit-tree 双 parent（origin/main + origin/<head>）
+ push origin <merge>:refs/heads/main → dev-agent fetch+rev-parse origin/main 取得 merge_commit
（= 真实 GitHub PR merge 后 main tip；merge commit message = --subject 值 = PR title，含 Pipeline-Merge marker）。
状态存 PA_GH_FAKE_STATE（测试 fixture 设 tmp_path 隔离，跨 PRD/phase 不串）。
"""
import json, os, subprocess, sys


def _load():
    p = os.environ.get("PA_GH_FAKE_STATE")
    if p and os.path.exists(p):
        return json.load(open(p))
    return {"prs": [], "next": 1}


def _save(s):
    p = os.environ.get("PA_GH_FAKE_STATE")
    if p:
        json.dump(s, open(p, "w"))


def _git(*a):
    r = subprocess.run(["git", *a], capture_output=True, text=True)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def main():
    args = sys.argv[1:]
    if len(args) < 2 or args[0] != "pr":
        sys.stderr.write("fake-gh: 只支持 pr 子命令\n"); sys.exit(1)
    sub, rest = args[1], args[2:]
    s = _load()
    if sub == "list":
        head = rest[rest.index("--head") + 1] if "--head" in rest else None
        st = rest[rest.index("--state") + 1] if "--state" in rest else "open"
        out = [{"number": p["number"], "url": p["url"], "state": p["state"]}
               for p in s["prs"]
               if p.get("state") == st and (head is None or p["head"] == head)]
        print(json.dumps(out))
    elif sub == "create":
        head = rest[rest.index("--head") + 1]
        title = rest[rest.index("--title") + 1]
        num = s["next"]; s["next"] += 1
        url = "https://fake-gh.test/pr/%d" % num
        s["prs"].append({"number": num, "head": head, "state": "open", "url": url, "title": title})
        _save(s); print(url)
    elif sub == "merge":
        token = rest[0]   # number 或 url
        num = int(token.rstrip("/").split("/")[-1])
        pr = next((p for p in s["prs"] if p["number"] == num), None)
        if pr is None:
            sys.stderr.write("fake-gh: PR %d 不存在\n" % num); sys.exit(1)
        head = pr["head"]
        _git("fetch", "origin", head)                         # 确保 head 最新（rebase 后 force-push 的 rebased 分支）
        _, main_sha, _ = _git("rev-parse", "origin/main")
        _, head_sha, _ = _git("rev-parse", "origin/" + head)
        _, tree, _ = _git("rev-parse", head_sha + "^{tree}")
        # 模拟真实 gh pr merge --merge：--subject 决定 merge commit title（含 marker），--body 成第二段；
        # 无 --subject → 默认 "Merge pull request #N"（无 marker，对齐真实 gh → 可捕获「dev-agent 漏传 --subject」回归）。
        ct = ["commit-tree", tree, "-p", main_sha, "-p", head_sha]
        if "--subject" in rest:
            ct += ["-m", rest[rest.index("--subject") + 1]]
            if "--body" in rest:
                ct += ["-m", rest[rest.index("--body") + 1]]
        else:
            ct += ["-m", "Merge pull request #%d" % num]
        _, merge_sha, _ = _git(*ct)
        _git("push", "origin", merge_sha + ":refs/heads/main")
        pr["state"] = "merged"; _save(s)
    else:
        sys.stderr.write("fake-gh: 不支持 pr %s\n" % sub); sys.exit(1)


if __name__ == "__main__":
    main()
'''


@pytest.fixture(autouse=True)
def _fake_gh(tmp_path, monkeypatch):
    """部署 fake gh 到 tmp_path/bin + 注入 PATH/PA_GH_FAKE_STATE（subprocess 经继承当前进程 env 命中 fake gh）。

    dev-agent gh() 经 PATH 解析 gh → 命中 fake gh（transparent，dev-agent 代码零改动）。每个测试独立 tmp_path
    → 独立 gh-state.json（PR 状态隔离，不跨测试串）。merge/revert 走 PR 路径时 fake gh 模拟 GitHub PR merge。
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "gh").write_text(FAKE_GH_SCRIPT, encoding="utf-8")
    (bin_dir / "gh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ.get('PATH', '')}")
    monkeypatch.setenv("PA_GH_FAKE_STATE", str(tmp_path / "gh-state.json"))


def _git(cwd: Path, *args: str) -> str:
    """跑 git -C <cwd>，断言成功（fail-fast：fixture 建仓失败立即暴露，不静默）。返回 stdout（strip）。"""
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=30)
    assert r.returncode == 0, f"git {args} failed (rc={r.returncode}): {r.stderr.strip()[:200]}"
    return r.stdout.strip()


def _make_repo(tmp_path: Path) -> Path:
    """裸 origin + worktree clone；origin/main 有 init commit。返回 worktree Path。"""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)],
                   capture_output=True, check=True)
    wt = tmp_path / "wt"
    subprocess.run(["git", "clone", "-q", str(origin), str(wt)], capture_output=True, check=True)
    _git(wt, "config", "user.email", "t@t.t")
    _git(wt, "config", "user.name", "test")
    (wt / "f.txt").write_text("init\n", encoding="utf-8")
    _git(wt, "add", ".")
    _git(wt, "commit", "-q", "-m", "init")
    _git(wt, "push", "-q", "origin", "main")
    return wt


def _add_branch_commit(wt: Path, branch: str, path: str, content: str) -> None:
    """从 main 建分支、改 path 文件、commit、push、回 main（feature 分支就绪）。"""
    _git(wt, "checkout", "-q", "-b", branch, "main")
    (wt / path).write_text(content, encoding="utf-8")
    _git(wt, "add", ".")
    _git(wt, "commit", "-q", "-m", branch)
    _git(wt, "push", "-q", "origin", branch)
    _git(wt, "checkout", "-q", "main")


def _run_merge(wt: Path, branch: str, main_ref: str = "main", prd_id: str = "prd-test"):
    """subprocess 跑 dev-agent --phase merge（cwd=worktree），解析末行 stdout JSON。返回 (proc, payload|None)。"""
    cmd = [sys.executable, str(DEV_AGENT_PY), "--phase", "merge",
           "--branch", branch, "--main", main_ref, "--prd-id", prd_id]
    r = subprocess.run(cmd, cwd=str(wt), capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=90)
    lines = (r.stdout or "").strip().splitlines()
    payload = json.loads(lines[-1]) if lines else None
    return r, payload


def _make_linked_worktree_repo(tmp_path: Path):
    """复刻 canary 布局：主仓 primary（main 已检出）+ 链接 worktree wt（feature 分支）。

    canary 实测 bug：merge phase 想在 wt 里 ``git checkout main`` 做 --no-ff merge，但 main 已在
    primary 主工作目录检出 → git 拒绝同分支双 worktree 检出 → auto-merge 永不成功（常规仓布局：main 总
    在主工作目录）。返回 (primary, wt)。
    """
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)], capture_output=True, check=True)
    primary = tmp_path / "primary"
    subprocess.run(["git", "clone", "-q", str(origin), str(primary)], capture_output=True, check=True)
    _git(primary, "config", "user.email", "t@t.t"); _git(primary, "config", "user.name", "test")
    (primary / "f.txt").write_text("init\n", encoding="utf-8")
    _git(primary, "add", "."); _git(primary, "commit", "-q", "-m", "init"); _git(primary, "push", "-q", "origin", "main")
    # 链接 worktree：-b auto/feat 从 main 建分支并在 wt 检出它（main 仍绑在 primary → wt 里 checkout main 会撞）
    wt = tmp_path / "wt"
    _git(primary, "worktree", "add", "-b", "auto/feat", str(wt), "main")
    _git(wt, "config", "user.email", "t@t.t"); _git(wt, "config", "user.name", "test")
    (wt / "feat.txt").write_text("feat\n", encoding="utf-8")
    _git(wt, "add", "."); _git(wt, "commit", "-q", "-m", "auto/feat"); _git(wt, "push", "-q", "origin", "auto/feat")
    return primary, wt


# ─── canary bug 修复：main 已在主工作目录检出时 merge phase 仍须 CLEAN（ref 级合并，永不 checkout main）──
def test_merge_clean_when_main_checked_out_elsewhere(tmp_path):
    """canary 实测 bug（task 7.2，cc-web-control）：main 已在 primary 主工作目录检出，merge phase 在
    linked worktree 里 ``git checkout main`` 会被 git 拒绝（同分支双 worktree 检出）→ auto-merge 永不成功。

    修后：ref 级合并（``commit-tree`` 双 parent = --no-ff 语义 + ``push <sha>:refs/heads/main``），永不
    checkout main → CLEAN 合并成功，origin/main 前进到双 parent merge commit，且 main 仍绑在 primary
    （wt 检出未动，仍在 auto/feat）。
    """
    primary, wt = _make_linked_worktree_repo(tmp_path)
    pre_main = _git(wt, "rev-parse", "origin/main")
    r, payload = _run_merge(wt, "auto/feat")
    assert payload is not None, f"无末行 JSON（stdout={r.stdout!r} stderr={r.stderr[:400]!r})"
    assert payload["rebase_outcome"] == "clean", payload
    assert payload["merge_commit"], "CLEAN 须落地 merge_commit（ref 级合并，不 checkout main）"
    assert payload["push_failed"] is False
    # origin/main 前进到 merge commit（真合 main，非双检出 bail）
    _git(wt, "fetch", "-q", "origin", "main")
    assert _git(wt, "rev-parse", "origin/main") == payload["merge_commit"]
    # --no-ff：merge commit 双 parent + feature commit 保留
    assert _git(wt, "rev-list", "--count", f"{pre_main}..origin/main") == "2"
    # marker footer
    msg = _git(wt, "log", "-1", "--format=%B", "origin/main")
    assert "Pipeline-Merge: prd-test" in msg
    # main 仍未在 wt 检出（ref 级合并不动 wt 检出；wt 仍在 auto/feat）—— canary bug 核心：不 checkout main
    assert _git(wt, "branch", "--show-current") == "auto/feat"


# ─── CLEAN：rebase 干净 → --no-ff merge + ff-only push + marker footer ──────────────
def test_merge_clean_no_ff_push_and_marker(tmp_path):
    wt = _make_repo(tmp_path)
    _add_branch_commit(wt, "auto/feat", "feat.txt", "feat\n")
    pre_main = _git(wt, "rev-parse", "origin/main")
    r, payload = _run_merge(wt, "auto/feat")
    assert payload is not None, f"无末行 JSON（stdout={r.stdout!r} stderr={r.stderr[:300]!r})"
    assert payload["rebase_outcome"] == "clean", payload
    assert payload["merge_commit"], "CLEAN 须落地 merge_commit"
    assert payload["push_failed"] is False
    assert payload["rebase"]["fetch_ok"] is True
    assert payload["rebase"]["rebase_rc"] == 0
    assert payload["rebase"]["worktree_clean"] is True
    # origin/main 前进到 merge commit（真合 main）
    _git(wt, "fetch", "-q", "origin", "main")
    assert _git(wt, "rev-parse", "origin/main") == payload["merge_commit"]
    # --no-ff：merge commit 是新提交（非快进），历史保留 feature commit + 双 parent
    assert _git(wt, "rev-list", "--count", f"{pre_main}..origin/main") == "2"   # merge commit + feat commit
    # marker footer（task 3.4）：merge commit message 含稳定 marker，可 git log --grep 找出
    msg = _git(wt, "log", "-1", "--format=%B", "origin/main")
    assert "Pipeline-Merge: prd-test" in msg
    assert _git(wt, "log", "origin/main", "--grep=Pipeline-Merge: prd-test", "--oneline")


# ─── task 7.1a shadow（--classify-only）：CLEAN 也只判 rebase 三态，**不 merge/push**，main 未碰 ──
def test_classify_only_clean_does_not_touch_main(tmp_path):
    wt = _make_repo(tmp_path)
    _add_branch_commit(wt, "auto/feat", "feat.txt", "feat\n")
    pre_main = _git(wt, "rev-parse", "origin/main")
    # --classify-only：shadow 模式只判 rebase 三态作 parity 证据，CLEAN 也不 merge/push（main 零副作用）
    cmd = [sys.executable, str(DEV_AGENT_PY), "--phase", "merge", "--classify-only",
           "--branch", "auto/feat", "--main", "main", "--prd-id", "prd-shadow"]
    r = subprocess.run(cmd, cwd=str(wt), capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=90)
    lines = (r.stdout or "").strip().splitlines()
    payload = json.loads(lines[-1]) if lines else None
    assert payload is not None, f"无末行 JSON（stdout={r.stdout!r} stderr={r.stderr[:300]!r})"
    # shadow 决策=clean（rebase 干净判定），但**绝不**合（merge_commit 必 None）
    assert payload["rebase_outcome"] == "clean", payload
    assert payload["merge_commit"] is None, "classify-only 绝不合（merge_commit 必 None，main 不碰）"
    assert payload["push_failed"] is False
    assert payload["rebase"]["fetch_ok"] is True
    # fail-safe：main 完全未碰（origin/main 仍是 classify 前 HEAD；未 checkout main/未 merge/未 push）
    _git(wt, "fetch", "-q", "origin", "main")
    assert _git(wt, "rev-parse", "origin/main") == pre_main, "classify-only 不应碰 main"
    # 无 Pipeline-Merge marker（未产生 merge commit——shadow 不合）
    assert not _git(wt, "log", "origin/main", "--grep=Pipeline-Merge: prd-shadow", "--oneline")
    # worktree 干净（rebase --abort 还原 feature 分支到 ORIG_HEAD，不留半完成 rebased 态）
    assert _git(wt, "status", "--porcelain") == ""


# ─── CONFLICT：rebase 冲突 → triage rebase_conflict，main 未碰 ──────────────────────
def test_merge_conflict_does_not_touch_main(tmp_path):
    wt = _make_repo(tmp_path)
    # feature 改 f.txt（从 init）
    _add_branch_commit(wt, "auto/feat", "f.txt", "feat-line\n")
    # main 前进：也改 f.txt 同行（feature 落后 main → rebase 冲突）
    (wt / "f.txt").write_text("main-line\n", encoding="utf-8")
    _git(wt, "add", ".")
    _git(wt, "commit", "-q", "-m", "main-commit")
    _git(wt, "push", "-q", "origin", "main")
    pre_main = _git(wt, "rev-parse", "origin/main")
    r, payload = _run_merge(wt, "auto/feat")
    assert payload is not None, f"无末行 JSON（stderr={r.stderr[:300]!r})"
    assert payload["rebase_outcome"] == "conflict", payload
    assert payload["merge_commit"] is None, "CONFLICT 绝不合（merge_commit 须 None）"
    assert payload["rebase"]["conflict_files"] > 0, "CONFLICT 须有冲突标记"
    # fail-safe：main 未碰（origin/main 仍是冲突前 HEAD）
    _git(wt, "fetch", "-q", "origin", "main")
    assert _git(wt, "rev-parse", "origin/main") == pre_main, "CONFLICT 不应碰 main"
    # worktree 残留清理：rebase --abort 后工作树干净（不留半完成冲突态）
    assert _git(wt, "status", "--porcelain") == ""


# ─── UNKNOWN：fetch 失败（main_ref 不存在 = base 过时）→ UNKNOWN，main 未碰 ─────────
def test_merge_unknown_when_main_ref_missing(tmp_path):
    wt = _make_repo(tmp_path)
    _add_branch_commit(wt, "auto/feat", "feat.txt", "feat\n")
    pre_main = _git(wt, "rev-parse", "origin/main")
    r, payload = _run_merge(wt, "auto/feat", main_ref="nonexistent")
    assert payload is not None, f"无末行 JSON（stderr={r.stderr[:300]!r})"
    assert payload["rebase_outcome"] == "unknown", payload
    assert payload["merge_commit"] is None
    assert payload["rebase"]["fetch_ok"] is False, "fetch 不存在 main_ref → fetch_ok=False"
    # fail-safe：main 未碰
    _git(wt, "fetch", "-q", "origin", "main")
    assert _git(wt, "rev-parse", "origin/main") == pre_main


# ═══ task 4.1-4.3：post-merge-test + revert phase 离线 drill（真实 git 全链路）═══════
# 守 D6/ADR-0001：控制面只发 --phase post-merge-test|revert cmd + 参数；dev-agent 在 worktree 内机械执行。
# post-merge-test 测**集成后 main**（D8：基线=main，≠ verify 的 candidate branch）；revert 撤销单一 merge commit
# （D7：git revert -m 1 + ff-only push）。三态 fail-safe：UNKNOWN/CONFLICT 不留半完成态、不误判 REVERTED。
TEST_CMD = "bash test.sh"   # worktree 内的「测试脚本」（post-merge-test --test-cmd）


def _write_test_script(wt: Path, exit_code: int = 0) -> None:
    """写 test.sh（exit <exit_code>）——模拟「测试套件」：0=绿，非0=红。"""
    (wt / "test.sh").write_text(f"#!/bin/sh\nexit {exit_code}\n", encoding="utf-8")


def _run_phase(wt: Path, phase: str, **flags) -> tuple:
    """subprocess 跑 dev-agent --phase <phase>（cwd=worktree），解析末行 stdout JSON。返回 (proc, payload|None)。"""
    cmd = [sys.executable, str(DEV_AGENT_PY), "--phase", phase]
    for k, v in flags.items():
        cmd += ["--" + k.replace("_", "-"), str(v)]
    r = subprocess.run(cmd, cwd=str(wt), capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=90)
    lines = (r.stdout or "").strip().splitlines()
    payload = json.loads(lines[-1]) if lines else None
    return r, payload


def _green_baseline(wt: Path) -> None:
    """在 main 上加一个绿的 test.sh baseline（exit0），让 feature 可在其上改红。"""
    _write_test_script(wt, exit_code=0)
    _git(wt, "add", ".")
    _git(wt, "commit", "-q", "-m", "green baseline")
    _git(wt, "push", "-q", "origin", "main")


# ─── post-merge-test：PASS（main 合入后仍绿）─────────────────────────────────
def test_post_merge_pass_when_main_stays_green(tmp_path):
    wt = _make_repo(tmp_path)
    _green_baseline(wt)                                   # main: test.sh=exit0
    _add_branch_commit(wt, "auto/feat", "feat.txt", "feat\n")   # 无关文件，test.sh 不动 → 合后仍绿
    _, mp = _run_merge(wt, "auto/feat")
    assert mp["merge_commit"]
    r, pm = _run_phase(wt, "post-merge-test", test_cmd=TEST_CMD, main="main", prd_id="prd-test")
    assert pm is not None, f"无末行 JSON（stderr={r.stderr[:300]!r})"
    assert pm["verdict"] == "pass", pm
    assert pm["ran"] is True and pm["test_rc"] == 0


# ─── 核心安全网证据链：merge 合红 → post-merge FAIL → revert REVERTED → main 回绿 ──
def test_post_merge_fail_then_revert_restores_green(tmp_path):
    wt = _make_repo(tmp_path)
    _green_baseline(wt)                                   # main: test.sh=exit0
    # feature：在分支上把 test.sh 改红（exit1）——模拟 dev 提交了 bug / 集成后才暴露的回归
    _git(wt, "checkout", "-q", "-b", "auto/red", "main")
    _write_test_script(wt, exit_code=1)
    _git(wt, "add", "."); _git(wt, "commit", "-q", "-m", "red code")
    _git(wt, "push", "-q", "origin", "auto/red")
    _git(wt, "checkout", "-q", "main")
    # merge：机械层不跑测试，照合（merge_commit 落地，main 此刻是红的）
    _, mp = _run_merge(wt, "auto/red")
    merge_commit = mp["merge_commit"]
    assert merge_commit, "须先 merge 成功（机械层不拦红代码）"
    _git(wt, "fetch", "-q", "origin", "main")
    assert _git(wt, "rev-parse", "origin/main") == merge_commit
    # post-merge-test：checkout main → bash test.sh → exit1 → FAIL（触发 revert）
    _, pm = _run_phase(wt, "post-merge-test", test_cmd=TEST_CMD, main="main", prd_id="prd-test")
    assert pm["verdict"] == "fail", pm
    assert pm["test_rc"] == 1
    # revert：revert merge_commit → REVERTED → main 回绿
    r, rv = _run_phase(wt, "revert", merge_commit=merge_commit, main="main", prd_id="prd-test")
    assert rv["outcome"] == "reverted", f"REVERTED {rv}（stderr={r.stderr[:300]!r}）"
    assert rv["revert_commit"], "REVERTED 须记 revert_commit（D12 reconcile 锚点）"
    # origin/main 前进到 revert commit
    _git(wt, "fetch", "-q", "origin", "main")
    assert _git(wt, "rev-parse", "origin/main") == rv["revert_commit"]
    # main 回绿：revert 撤销红代码，test.sh 回 exit0
    _git(wt, "checkout", "-q", "origin/main")
    rc = subprocess.run(["bash", "test.sh"], cwd=str(wt), capture_output=True).returncode
    assert rc == 0, "revert 后 main 须回绿（test.sh exit0）"
    # revert commit 的 parent 含 merge_commit（revert 的是它，历史可追溯）
    assert merge_commit in _git(wt, "log", "-1", "--format=%P", "origin/main").split()


# ─── post-merge-test：UNKNOWN（无 --test-cmd → ran=False，不当代绿）────────────
def test_post_merge_unknown_when_no_test_cmd(tmp_path):
    wt = _make_repo(tmp_path)
    _add_branch_commit(wt, "auto/feat", "feat.txt", "feat\n")
    _, mp = _run_merge(wt, "auto/feat")
    assert mp["merge_commit"]
    # 不传 --test-cmd → ran=False → UNKNOWN（fail-safe：无测试证据不当代 PASS）
    r, pm = _run_phase(wt, "post-merge-test", main="main", prd_id="prd-test")
    assert pm["verdict"] == "unknown", pm
    assert pm["ran"] is False


# ─── post-merge-test：UNKNOWN（main_ref 不存在 → checkout 失败）────────────────
def test_post_merge_unknown_when_main_ref_missing(tmp_path):
    wt = _make_repo(tmp_path)
    _add_branch_commit(wt, "auto/feat", "feat.txt", "feat\n")
    _, _ = _run_merge(wt, "auto/feat")
    r, pm = _run_phase(wt, "post-merge-test", test_cmd=TEST_CMD, main="nonexistent", prd_id="prd-test")
    assert pm["verdict"] == "unknown", pm   # checkout 失败 → UNKNOWN


# ─── revert：UNKNOWN（merge_commit 不存在 → rc≠0 无冲突标记 → UNKNOWN，main 未碰）──
def test_revert_unknown_when_merge_commit_missing(tmp_path):
    wt = _make_repo(tmp_path)
    pre_main = _git(wt, "rev-parse", "origin/main")
    r, rv = _run_phase(wt, "revert", merge_commit="deadbeef", main="main", prd_id="prd-test")
    assert rv["outcome"] == "unknown", rv
    # main 未碰
    _git(wt, "fetch", "-q", "origin", "main")
    assert _git(wt, "rev-parse", "origin/main") == pre_main


# ─── revert：CONFLICT（merge 后 main 改了同一行 → revert 三方冲突 → abort，main 未碰）──
def test_revert_conflict_aborts_and_keeps_main(tmp_path):
    wt = _make_repo(tmp_path)                            # init: f.txt="init\n"
    _add_branch_commit(wt, "auto/feat", "f.txt", "feat-line\n")   # feature: f.txt="feat-line"
    _, mp = _run_merge(wt, "auto/feat")                  # merge → main f.txt="feat-line"
    merge_commit = mp["merge_commit"]
    # main 前进：改 f.txt 同行（revert merge 会与此三方冲突）
    _git(wt, "fetch", "-q", "origin", "main")
    _git(wt, "checkout", "-q", "main")
    _git(wt, "reset", "--hard", "origin/main")
    (wt / "f.txt").write_text("post-merge-edit\n", encoding="utf-8")
    _git(wt, "add", "."); _git(wt, "commit", "-q", "-m", "post merge edit")
    _git(wt, "push", "-q", "origin", "main")
    pre_revert = _git(wt, "rev-parse", "origin/main")
    r, rv = _run_phase(wt, "revert", merge_commit=merge_commit, main="main", prd_id="prd-test")
    assert rv["outcome"] == "conflict", f"CONFLICT {rv}（stderr={r.stderr[:300]!r}）"
    assert rv["conflict_files"] > 0
    # main 未碰（revert --abort 清冲突残留）；origin/main 仍是 revert 前
    _git(wt, "fetch", "-q", "origin", "main")
    assert _git(wt, "rev-parse", "origin/main") == pre_revert


# ─── canary bug 修复（revert phase 同因）：main 已在主工作目录检出时 revert 仍须 REVERTED ──
def test_revert_clean_when_main_checked_out_elsewhere(tmp_path):
    """canary bug（revert phase，与 merge phase 同因）：main 已在 primary 主工作目录检出，revert phase 在
    linked worktree 里 ``git checkout main`` 会被 git 拒绝（同分支双 worktree 检出）→ UNKNOWN → halt（red
    path 走不到回滚）。修后：``checkout --detach origin/main``（不锁 main 分支）+ ``git revert -m 1`` +
    ref-level push → REVERTED → main 回绿，且 wt 检出恢复（不留 detached HEAD）。
    """
    primary, wt = _make_linked_worktree_repo(tmp_path)
    # 前置：先 merge auto/feat 到 main（修复后能在 linked wt 合）→ 拿 merge_commit
    _, mp = _run_merge(wt, "auto/feat")
    merge_commit = mp["merge_commit"]
    assert merge_commit, "前置：merge 须成功（linked worktree 场景）"
    _git(wt, "fetch", "-q", "origin", "main")
    assert _git(wt, "rev-parse", "origin/main") == merge_commit
    # revert merge_commit（main 在 primary 主工作目录检出 = dual-checkout 现场）
    r, rv = _run_phase(wt, "revert", merge_commit=merge_commit, main="main", prd_id="prd-test")
    assert rv["outcome"] == "reverted", f"REVERTED {rv}（stderr={r.stderr[:300]!r}）"
    assert rv["revert_commit"], "REVERTED 须记 revert_commit（D12 reconcile 锚点）"
    # origin/main 前进到 revert commit（真回滚 main）
    _git(wt, "fetch", "-q", "origin", "main")
    assert _git(wt, "rev-parse", "origin/main") == rv["revert_commit"]
    # revert commit 的 parent 含 merge_commit（revert 的是它，历史可追溯）
    assert merge_commit in _git(wt, "log", "-1", "--format=%P", "origin/main").split()
    # wt 检出恢复（detached 不留）—— canary bug 核心：不 checkout main 分支，且结束回原分支
    assert _git(wt, "branch", "--show-current") == "auto/feat", "revert 须恢复原检出（不留 detached HEAD）"
