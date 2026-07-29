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

post-merge 验证 + auto-revert（task 4.x）尚未实现，故本测只覆盖 **merge 阶段全链路**（rebase→merge→push 三态）。
跑：python -m pytest scripts/test_dev_agent_merge.py -q
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

DEV_AGENT_PY = Path(__file__).parent / "dev-agent.py"


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
