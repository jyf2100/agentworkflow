#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""r6 P1-3（R4 §2.2 + §2.3）：``_commit_evidence`` 单元测试——独立 evidence_commit + ancestry allowlist。

评审 P1-3 反例：r5 bundle 落 mkdtemp，无 docs/evidence 落地 + evidence_commit + ancestry；subject_commit=None
不阻断。r6 修复：(1) bundle 落仓内 ``docs/evidence/<subject_commit>/``；(2) 生成独立 evidence_commit（只含
evidence 路径）；(3) ancestry 自检 ``subject..evidence`` 只含 ``docs/evidence/``（allowlist），夹带业务代码
变更 → fail-closed None；(4) git 不可用 → None。

``_commit_evidence`` 生产在 vault 仓真 git commit；本文件用 **tmp git 仓**（``vault_root`` 注入）验证 ancestry
逻辑，不触碰真实 vault 历史。
"""
import subprocess

import runtime_evidence as RE


def _init_tmp_vault(vault, subject_file_content: str = "vault") -> str:
    """初始化 tmp git 仓 + 一个含业务文件的 subject commit（README.md）→ 返回 subject sha。

    README.md 模拟被验收代码（非 evidence 路径），用于验证 ancestry allowlist 只允许 docs/evidence/ 变更。
    """
    subprocess.run(["git", "init", "-q"], cwd=str(vault), check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=str(vault), check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=str(vault), check=True)
    (vault / "README.md").write_text(subject_file_content, encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(vault), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "subject"], cwd=str(vault), check=True)
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(vault), text=True).strip()


def test_commit_evidence_creates_commit_with_clean_ancestry(tmp_path):
    """r6 P1-3 正向：_commit_evidence 生成 evidence_commit，subject..evidence ancestry 只含 docs/evidence/。"""
    vault = tmp_path / "vault"
    vault.mkdir()
    subject = _init_tmp_vault(vault)
    ev = vault / "docs" / "evidence" / subject
    ev.mkdir(parents=True)
    (ev / "manifest.json").write_text("{}", encoding="utf-8")
    sha = RE._commit_evidence(ev, subject, vault_root=vault)
    assert sha is not None and sha != subject
    diff = subprocess.check_output(["git", "diff", "--name-only", f"{subject}..{sha}"],
                                   cwd=str(vault), text=True).strip()
    assert diff and all(ln.startswith("docs/evidence/") for ln in diff.splitlines()), (
        f"ancestry 含非 evidence 路径: {diff}")


def test_commit_evidence_returns_none_when_not_git_repo(tmp_path):
    """r6 P1-3 fail-closed：vault_root 非 git 仓（git add/commit 抛）→ _commit_evidence 返回 None（不崩不假绿）。"""
    vault = tmp_path / "notgit"
    vault.mkdir()
    ev = vault / "docs" / "evidence" / "abc123"
    ev.mkdir(parents=True)
    (ev / "manifest.json").write_text("{}", encoding="utf-8")
    assert RE._commit_evidence(ev, "abc123", vault_root=vault) is None


def test_commit_evidence_rejects_ancestry_with_non_evidence_paths(tmp_path, monkeypatch):
    """r6 P1-3（R4 §2.3-3 反例）：ancestry subject..evidence 含非 docs/evidence/ 路径 → fail-closed None。

    防 evidence_commit 夹带业务代码变更（如 scripts/cutover.py）被误当 subject 重新执行。monkeypatch
    ``git diff --name-only`` 返回含非 evidence 路径，模拟 ancestry 被污染 → _commit_evidence 必返回 None。
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    subject = _init_tmp_vault(vault)
    ev = vault / "docs" / "evidence" / subject
    ev.mkdir(parents=True)
    (ev / "manifest.json").write_text("{}", encoding="utf-8")
    real_check_output = subprocess.check_output

    def fake_check_output(cmd, *a, **kw):
        if "diff" in cmd and "--name-only" in cmd:
            return "docs/evidence/x/manifest.json\nscripts/cutover.py\n"   # scripts/ 非 evidence
        return real_check_output(cmd, *a, **kw)
    monkeypatch.setattr(subprocess, "check_output", fake_check_output)
    assert RE._commit_evidence(ev, subject, vault_root=vault) is None, (
        "ancestry 含非 evidence 路径仍返回 sha——P1-3 ancestry allowlist 未生效")
