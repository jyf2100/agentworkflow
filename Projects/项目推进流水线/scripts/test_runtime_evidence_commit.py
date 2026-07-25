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
    r7-S2：配 bare 远程 + upstream（evidence commit 须 push 才算发布；无 upstream → _commit_evidence push 失败）。
    """
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=str(vault), check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=str(vault), check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=str(vault), check=True)
    (vault / "README.md").write_text(subject_file_content, encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(vault), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "subject"], cwd=str(vault), check=True)
    # r7-S2：bare 远程 + upstream（git push 依赖 upstream；无则 push 失败 → _commit_evidence fail-closed None）
    _bare = vault.parent / f"{vault.name}-bare.git"
    subprocess.run(["git", "init", "-q", "--bare", str(_bare)], check=True)
    subprocess.run(["git", "remote", "add", "origin", str(_bare)], cwd=str(vault), check=True)
    subprocess.run(["git", "push", "-q", "-u", "origin", "main"], cwd=str(vault), check=True)
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


def test_commit_evidence_does_not_swallow_user_staged_files(tmp_path):
    """r7-P0-1（审核员反例）：vault index 已有用户暂存的业务文件（scripts/foo.py）→ evidence commit 须只含
    docs/evidence/，不吞入 scripts/foo.py。旧 ``git commit -m``（无 pathspec）提交全部 staged → 业务改动被
    误并入 evidence commit。r7：``git commit -- <rel>`` 限定路径。"""
    vault = tmp_path / "vault"
    vault.mkdir()
    subject = _init_tmp_vault(vault)
    # 用户暂存一个业务文件（非 evidence 路径），模拟被验收仓有未提交业务改动
    (vault / "scripts").mkdir()
    (vault / "scripts" / "foo.py").write_text("print(1)", encoding="utf-8")
    subprocess.run(["git", "add", "scripts/foo.py"], cwd=str(vault), check=True)
    ev = vault / "docs" / "evidence" / subject
    ev.mkdir(parents=True)
    (ev / "manifest.json").write_text("{}", encoding="utf-8")
    sha = RE._commit_evidence(ev, subject, vault_root=vault)
    assert sha is not None
    # evidence commit 只含 docs/evidence/，不含 scripts/foo.py
    diff = subprocess.check_output(["git", "diff", "--name-only", f"{subject}..{sha}"],
                                   cwd=str(vault), text=True).strip()
    assert "scripts/foo.py" not in diff.splitlines(), (
        "evidence commit 吞入了用户暂存的业务文件——P0-1 pathspec 隔离未生效")
    assert all(ln.startswith("docs/evidence/") for ln in diff.splitlines())
    # 用户暂存的 scripts/foo.py 仍在 index（未被 evidence commit 吞掉）
    staged = subprocess.check_output(["git", "diff", "--name-only", "--cached", "HEAD"],
                                     cwd=str(vault), text=True).strip()
    assert "scripts/foo.py" in staged.splitlines(), (
        "用户暂存文件被 evidence commit 吞掉（index 清空）——P0-1 隔离未生效")


def test_commit_evidence_rolls_back_commit_on_ancestry_failure(tmp_path, monkeypatch):
    """r7-P0-1（审核员反例）：ancestry 自检失败（subject..evidence 含非 evidence 路径）→ 须回滚 evidence
    commit（HEAD 退回），不留污染 commit。旧实现 ``return None`` 但 commit 已生成 → HEAD 仍指向 evidence
    commit（污染）。r7：``reset --soft HEAD~1`` 撤销 commit。"""
    vault = tmp_path / "vault"
    vault.mkdir()
    subject = _init_tmp_vault(vault)
    ev = vault / "docs" / "evidence" / subject
    ev.mkdir(parents=True)
    (ev / "manifest.json").write_text("{}", encoding="utf-8")
    head_before = subprocess.check_output(["git", "rev-parse", "HEAD"],
                                          cwd=str(vault), text=True).strip()
    real_check_output = subprocess.check_output

    def fake_check_output(cmd, *a, **kw):
        if "diff" in cmd and "--name-only" in cmd:
            return "docs/evidence/x/manifest.json\nscripts/cutover.py\n"   # 含非 evidence 路径
        return real_check_output(cmd, *a, **kw)
    monkeypatch.setattr(subprocess, "check_output", fake_check_output)
    sha = RE._commit_evidence(ev, subject, vault_root=vault)
    assert sha is None
    # evidence commit 已回滚 → HEAD 退回 head_before（无污染 commit 残留）
    head_after = subprocess.check_output(["git", "rev-parse", "HEAD"],
                                         cwd=str(vault), text=True).strip()
    assert head_after == head_before, (
        "ancestry 失败但 evidence commit 未回滚——HEAD 被污染，P0-1 回滚未生效")


def test_commit_evidence_returns_none_when_push_fails(tmp_path):
    """r7-S2（审核员反例）：evidence commit 未推送不算跨机器发布。vault 无远程/upstream → ``git push`` 失败 →
    ``_commit_evidence`` fail-closed 回滚 commit + 返回 None（杜绝「本地有 commit 假装已发布」）。本地 commit
    已生成但 push 失败 → HEAD 须退回 subject（reset --soft HEAD~1，无污染 commit 残留）。"""
    vault = tmp_path / "vault"
    vault.mkdir()
    # init 但**不**配远程（无 origin → git push 失败）
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=str(vault), check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=str(vault), check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=str(vault), check=True)
    (vault / "README.md").write_text("vault", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(vault), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "subject"], cwd=str(vault), check=True)
    subject = subprocess.check_output(["git", "rev-parse", "HEAD"],
                                      cwd=str(vault), text=True).strip()
    ev = vault / "docs" / "evidence" / subject
    ev.mkdir(parents=True)
    (ev / "manifest.json").write_text("{}", encoding="utf-8")
    sha = RE._commit_evidence(ev, subject, vault_root=vault)
    assert sha is None, "无远程 push 失败应返回 None（未推送不算跨机器发布）——S2 push 未生效"
    # evidence commit 已回滚 → HEAD 退回 subject（无污染 commit 残留）
    head_after = subprocess.check_output(["git", "rev-parse", "HEAD"],
                                         cwd=str(vault), text=True).strip()
    assert head_after == subject, "push 失败但 evidence commit 未回滚——HEAD 被污染"
