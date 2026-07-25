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


def test_commit_evidence_rolls_back_on_post_commit_exception(tmp_path, monkeypatch):
    """r8-3（审核员）：commit 成功后任一步骤**异常**（非 returncode 失败）——push timeout 是 CI 最常见——
    → 旧 except 仅 ``return None`` 不 reset → evidence commit 残留污染 HEAD。r8-3：``_committed`` 标志 +
    except 兜底 ``reset --soft <subject_commit>`` 回滚到确切 subject 锚点。

    区别于 S2 push-returncode-!=0 测试（走显式 reset 分支）：本测试 push **抛 TimeoutExpired**（走 except），
    验证 except 块的兜底 reset 路径——堵 commit 后异常（rev-parse/diff/push timeout）的 HEAD 污染。"""
    vault = tmp_path / "vault"
    vault.mkdir()
    subject = _init_tmp_vault(vault)
    ev = vault / "docs" / "evidence" / subject
    ev.mkdir(parents=True)
    (ev / "manifest.json").write_text("{}", encoding="utf-8")
    real_run = subprocess.run

    def fake_run(cmd, *a, **kw):
        # push 抛 timeout（commit 已成功、HEAD 已进 evidence commit；模拟 CI push hang/timeout 走 except）
        if cmd[:2] == ["git", "push"]:
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=kw.get("timeout", 30))
        return real_run(cmd, *a, **kw)
    monkeypatch.setattr(subprocess, "run", fake_run)
    sha = RE._commit_evidence(ev, subject, vault_root=vault)
    assert sha is None
    # commit 后异常 → except 兜底 reset → HEAD 退回 subject（无 evidence commit 残留污染）
    head_after = subprocess.check_output(["git", "rev-parse", "HEAD"],
                                         cwd=str(vault), text=True).strip()
    assert head_after == subject, "commit 后异常未兜底 reset——HEAD 被 evidence commit 污染（r8-3 未生效）"


def test_commit_evidence_push_false_commits_without_pushing(tmp_path):
    """r9-2（审核员 P0）：``push=False`` 只本地 commit，不 push（real_cutover_suite 跑 verify.py 绿后才 push）。
    本地 HEAD 进 evidence commit，但 bare remote 无该 commit（未跨机器发布）。"""
    vault = tmp_path / "vault"
    vault.mkdir()
    subject = _init_tmp_vault(vault)            # 配 bare remote + upstream（push=True 才会 push）
    ev = vault / "docs" / "evidence" / subject
    ev.mkdir(parents=True)
    (ev / "manifest.json").write_text("{}", encoding="utf-8")
    sha = RE._commit_evidence(ev, subject, vault_root=vault, push=False)
    assert sha is not None
    # 本地 HEAD = evidence commit（commit 已落地）
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(vault), text=True).strip()
    assert head == sha
    # bare remote 无 evidence commit（未 push）—— r9-2 commit/push 分离的核心：push 留给 verify 绿后
    bare = vault.parent / f"{vault.name}-bare.git"
    remote_refs = subprocess.check_output(["git", "ls-remote", str(bare)], text=True).strip()
    assert sha not in remote_refs, "push=False 不应 push，但 remote 有 evidence commit——r9-2 分离未生效"


def test_rollback_evidence_commit_unstages_evidence_and_keeps_user_staged(tmp_path):
    """r9-5（审核员）：``_rollback_evidence_commit`` 撤 commit + unstage evidence（``reset HEAD -- <rel>``），
    恢复 index 到 subject 状态。evidence 文件 working tree 保留但 unstaged；用户暂存的其他业务文件保留。

    旧 ``reset --soft`` 不动 index → evidence 仍 staged（污染用户暂存区）。r9-5：``reset HEAD -- <rel>`` 只
    unstage evidence 路径，保留用户其他 staged（如 scripts/foo.py）。"""
    from pathlib import Path
    vault = tmp_path / "vault"
    vault.mkdir()
    subject = _init_tmp_vault(vault)
    ev = vault / "docs" / "evidence" / subject
    ev.mkdir(parents=True)
    (ev / "manifest.json").write_text("{}", encoding="utf-8")
    sha = RE._commit_evidence(ev, subject, vault_root=vault, push=False)   # 本地 evidence commit
    assert sha is not None
    # 用户暂存一个业务文件（非 evidence 路径）—— 验回滚后保留
    (vault / "scripts").mkdir()
    (vault / "scripts" / "foo.py").write_text("print(1)", encoding="utf-8")
    subprocess.run(["git", "add", "scripts/foo.py"], cwd=str(vault), check=True)
    # 回滚 evidence commit（r9-5 helper：reset --soft subject + reset HEAD -- <rel>）
    RE._rollback_evidence_commit(vault, subject, Path("docs/evidence") / subject)
    # HEAD 退回 subject（commit 撤销）
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(vault), text=True).strip()
    assert head == subject, "回滚后 HEAD 未退回 subject"
    # evidence 文件 working tree 保留（未删）
    assert (ev / "manifest.json").exists(), "回滚删了 evidence 文件（应只 unstage，不删 working tree）"
    # evidence unstaged（index 不含 docs/evidence/）—— r9-5 核心
    staged = subprocess.check_output(["git", "diff", "--name-only", "--cached"],
                                     cwd=str(vault), text=True).strip()
    staged_lines = staged.splitlines() if staged else []
    assert not any(ln.startswith("docs/evidence/") for ln in staged_lines), (
        "evidence 仍 staged——r9-5 unstate（reset HEAD -- <rel>）未生效，用户暂存区被污染")
    # 用户业务文件仍 staged（保留，未被回滚吞掉）
    assert "scripts/foo.py" in staged_lines, "用户暂存文件被回滚吞掉——r9-5 过度清理"


def test_strip_leaky_invocation_fields_removes_tool_output_keeps_metadata():
    """r9-6（审核员）：``_strip_leaky_invocation_fields`` 剥离 leaky 字段（tool_output）保留诊断元数据。
    callback_invocations 经 ``_run_scenario_query`` :628 → real_sdk_canary ``all_invocations`` → :1158
    ``TelemetryEvidence.callback_invocations`` 进 sub-evidence blob 前须剥离 tool_output（否则 cutover r9-6
    递归 denylist 拒 → 生产 publish fail-closed 永红）。tool_name/tool_exit_code（state 判定 + 诊断）保留；
    返回**副本**，不改原 inv（immutability，:598 仍能从原 inv 取 tool_output 构造 observed_state）。"""
    inv = {"event": "PostToolUse", "correlation_id": "c1",
           "tool_name": "Bash", "tool_exit_code": 0, "tool_output": "SECRET_STDOUT_WITH_TOKEN"}
    stripped = RE._strip_leaky_invocation_fields(inv)
    assert "tool_output" not in stripped, "tool_output 须剥离（leaky，禁入 sub-evidence）"
    assert stripped["tool_name"] == "Bash" and stripped["tool_exit_code"] == 0, "非 leaky 诊断元数据须保留"
    assert stripped["correlation_id"] == "c1"
    # 原 inv 不变（返回副本，非原地改——:598 仍能从原 inv 取 tool_output）
    assert inv.get("tool_output") == "SECRET_STDOUT_WITH_TOKEN", "须返回副本，不改原 dict"


def test_commit_evidence_excludes_untracked_stray_file(tmp_path):
    """r10-B2（审核员 r9 复审）：docs/evidence/<subject>/ 里未引用的 untracked stray 文件不得进 evidence commit。

    反例：publish 写白名单文件后，目录残留 untracked stray.log（崩溃重跑残留 / 并发 claude 会话注入），
    含 AWS 凭据模式。旧 ``git add/commit -- <整目录>`` 会 stage+commit stray.log → push → 凭据泄漏远端，
    绕过 publish 层 secret scan（publish 只 scan manifest.json + sub_evidence_refs 引用的 blob）。
    r10-B2：per-file pathspec 白名单（git add + commit 只列 publish 写的文件）→ stray.log 不进 commit。
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    subject = _init_tmp_vault(vault)
    ev = vault / "docs" / "evidence" / subject
    ev.mkdir(parents=True)
    (ev / "manifest.json").write_text("{}", encoding="utf-8")
    (ev / "stray.log").write_text("leaked: AKIA" + "A" * 16, encoding="utf-8")   # untracked stray 含凭据
    sha = RE._commit_evidence(ev, subject, vault_root=vault, push=False)
    assert sha is not None, "per-file 白名单 manifest.json 应正常 commit"
    show = subprocess.check_output(["git", "show", "--name-only", "--format=", sha],
                                   cwd=str(vault), text=True).strip()
    # ⚠️ 断言须匹配完整路径行（docs/evidence/<subject>/stray.log），非精确等于 "stray.log"——
    # 否则恒真（vacuously pass），守不住回归（r5→r9「守门弱」病根，mutation 验证暴露）
    _files = show.splitlines()
    assert not any("stray.log" in _f for _f in _files), (
        "r10-B2 回归：untracked stray.log 进了 evidence commit → 未引用文件绕过 secret scan + push 远端\n"
        f"实际 commit 文件：{_files}")


def test_commit_evidence_excludes_tracked_modified_stray_with_credential(tmp_path):
    """r10-B2（审核员 r9 复审 + 红队 DECISIVE）：tracked-modified stray 是真泄漏路径，commit 必须 per-file。

    ``git commit -- <dir>`` 提交匹配 pathspec 的【已跟踪文件工作树状态，忽略 index】，故仅改 ``git add`` 为
    per-file 拦不住 tracked-modified stray——必须 ``git commit`` 也 per-file。反例：前次崩溃 evidence run
    （旧整目录逻辑）已把 stray.log 提交为 tracked（CLAUDE.md 明言 durable runtime 预期 crash+restart），本次
    重跑改写它含 AKIA 凭据（并发会话注入 / retry sidecar / 重注入），旧 ``git commit -- <dir>`` 照常纳入 →
    ``git cat-file -p HEAD:.../stray.log`` 可读凭据，per-file ``git add`` 形同虚设。r10-B2：commit per-file →
    tracked-modified stray 不进新 evidence commit（凭据改动留在 working tree，不 push）。
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    subject = _init_tmp_vault(vault)
    ev = vault / "docs" / "evidence" / subject
    ev.mkdir(parents=True)
    (ev / "manifest.json").write_text("{}", encoding="utf-8")
    rel_ev = ev.relative_to(vault)
    # 1. 模拟前次崩溃 run（旧整目录 commit）把 stray.log + manifest.json 一起提交 → stray.log tracked
    (ev / "stray.log").write_text("clean", encoding="utf-8")
    subprocess.run(["git", "add", str(rel_ev)], cwd=str(vault), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "prior evidence run (legacy dir-pathspec add)"],
                   cwd=str(vault), check=True)
    prior = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(vault), text=True).strip()
    # 2. 本次重跑：改写 tracked stray.log 含 AWS 凭据 + 重写 manifest.json
    (ev / "stray.log").write_text("leaked: AKIA" + "B" * 16, encoding="utf-8")
    (ev / "manifest.json").write_text('{"redrill": true}', encoding="utf-8")
    # 3. r10-B2 per-file _commit_evidence（白名单只 manifest.json；stray.log 不在白名单）
    sha = RE._commit_evidence(ev, prior, vault_root=vault, push=False)
    assert sha is not None, "per-file 白名单 manifest.json 应正常 commit"
    # 4. 反向断言（红队 DECISIVE 核心）：新 evidence commit 不得含 tracked-modified stray.log
    #    ⚠️ 须匹配完整路径行（mutation 验证暴露：精确等于断言恒真，vacuously pass）
    show = subprocess.check_output(["git", "show", "--name-only", "--format=", sha],
                                   cwd=str(vault), text=True).strip()
    _files = show.splitlines()
    assert not any("stray.log" in _f for _f in _files), (
        "r10-B2 回归：tracked-modified stray.log 进了 evidence commit——git commit 须 per-file（红队 DECISIVE："
        "per-file add 拦不住 tracked-modified；commit --<dir> 照带 stray 含凭据，git cat-file 可读直达远端）\n"
        f"实际 commit 文件：{_files}")


def test_derive_evidence_whitelist_rejects_path_traversal_ref(tmp_path):
    """r10-B2-traversal（红队 DECISIVE）：``_derive_evidence_whitelist`` 对 ``sub_evidence_refs`` 的 path
    traversal ref（``../leaky.pem`` / 绝对路径 / ``~`` / ``\\``）须 fail-closed 返 ``[]``。

    红队实测：旧版 ``_d`` 直接拼接 ``f"artifacts/{_d}"`` 零校验——``"../leaky.pem"`` → 候选
    ``"artifacts/../leaky.pem"`` → ``Path.exists()`` 归一化为 ``(evidence_dir/"leaky.pem").exists()`` 为真 →
    白名单含该 ref → ``_commit_evidence`` 的 ``git add/commit -- docs/evidence/<subj>/artifacts/../leaky.pem``
    被 git pathspec 归一化 → stage+commit 真实 stray。``sub_evidence_refs`` 是内容寻址 digest
    （``sha256:<hex>``，``_archive_sub_evidence`` 返 ``ref.digest``），全安全字符，校验安全字符集 + 拒 ``..``
    不误杀生产。任一 ref 非法 → 整白名单 ``[]``（manifest 被外部构造带 traversal = 可疑，不部分信任）。
    """
    ev = tmp_path / "ev"
    ev.mkdir()
    (ev / "manifest.json").write_text(
        '{"sub_evidence_refs": ["../leaky.pem", "sha256:abc"]}', encoding="utf-8")
    (ev / "leaky.pem").write_text("stray", encoding="utf-8")          # traversal 目标存在（诱饵）
    # 任一 ref 含 path traversal → 整白名单 fail-closed []（即便另一 ref 合法）
    assert RE._derive_evidence_whitelist(ev) == [], (
        "r10-B2-traversal 回归：含 '../leaky.pem' 的 sub_evidence_refs 须 fail-closed 返 []\n"
        "红队 DECISIVE：ref 零校验 + Path.exists 归一化 + git pathspec 归一化 = traversal ref 让白名单指向 stray")


def test_derive_evidence_whitelist_rejects_absolute_and_tilde_ref(tmp_path):
    """r10-B2-traversal 边界：绝对路径 / ``~`` home / 反斜杠 / 空皆须拒（traversal 变体，同根因）。"""
    import json as _json
    ev = tmp_path / "ev"
    ev.mkdir()
    (ev / "manifest.json").write_text("{}", encoding="utf-8")
    for _bad in ("/etc/passwd", "~/leaky.pem", "a\\b", "", "a/../b", "a/.."):
        (ev / "manifest.json").write_text(
            _json.dumps({"sub_evidence_refs": [_bad]}), encoding="utf-8")
        assert RE._derive_evidence_whitelist(ev) == [], (
            f"r10-B2-traversal 回归：恶意 ref {_bad!r} 须被拒（traversal 变体）")


def test_derive_evidence_whitelist_accepts_legal_digest_ref(tmp_path):
    """r10-B2-traversal 正向：合法内容寻址 digest ref（``sha256:<hex>``）须通过——校验不误杀生产。

    生产 ``_archive_sub_evidence`` 返 ``ref.digest`` = ``sha256:<hex>``（cutover.py:1532/1829 文件名=digest），
    全 ``[A-Za-z0-9._:-]`` 安全字符。校验后白名单含 ``manifest.json`` + ``artifacts/<digest>``（存在的）。
    """
    ev = tmp_path / "ev"
    ev.mkdir()
    _digest = "sha256:" + "a" * 64
    (ev / "manifest.json").write_text(
        '{"sub_evidence_refs": ["%s"]}' % _digest, encoding="utf-8")
    (ev / "artifacts").mkdir()
    (ev / "artifacts" / _digest).write_text("blob", encoding="utf-8")
    wl = RE._derive_evidence_whitelist(ev)
    assert "manifest.json" in wl and f"artifacts/{_digest}" in wl, (
        f"合法 digest ref 误杀——生产 sha256:<hex> 须通过校验。实际白名单：{wl}")


def test_commit_evidence_rejects_path_traversal_ref_fail_closed(tmp_path):
    """r10-B2-traversal 端到端（红队 DECISIVE）：manifest.sub_evidence_refs 含 traversal ref + 同目录 stray
    含**未检出凭据格式**（PEM 私钥，``_scan_for_secrets`` 盲区）→ ``_commit_evidence`` 须 fail-closed 返
    ``None``（``_derive_evidence_whitelist`` 返 [] → ``if not _expected: return None``），HEAD 不前进、stray 不进 commit。

    红队 exploit 链（旧代码成立）：traversal ref 让 stray 进白名单(1) → git pathspec 归一化 stage stray(2)
    → ancestry ``startswith("docs/evidence/")`` 放行(3) → Layer 3 ``_scan_for_secrets`` 对 PEM 盲区零命中(4)
    → push 远端泄漏(5)。修 Layer 1（ref 校验）断链第(1)步；Layer 3 不在 ``_commit_evidence`` 内（公开 API，
    测试直接用），故 ``_commit_evidence`` 自身须堵 traversal（不依赖 publish 层 scan 兜底）。
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    subject = _init_tmp_vault(vault)
    ev = vault / "docs" / "evidence" / subject
    ev.mkdir(parents=True)
    # manifest 被「外部构造」带 traversal ref（生产 TOCTOU：并发 claude 会话改写 / retry sidecar 重注入）
    (ev / "manifest.json").write_text(
        '{"sub_evidence_refs": ["../leaky.pem"]}', encoding="utf-8")
    # stray 含 PEM 私钥（Layer 3 _scan_for_secrets 无此模式 → 盲区；AKIA 也用上确保双格式都不被兜底）
    (ev / "leaky.pem").write_text(
        "-----BEGIN RSA PRIVATE KEY-----\nLEAKED_AKIA_CREDENTIAL_VALUE\n", encoding="utf-8")
    sha = RE._commit_evidence(ev, subject, vault_root=vault, push=False)
    # 修复：traversal ref → _derive_evidence_whitelist fail-closed [] → _commit_evidence None
    assert sha is None, (
        "r10-B2-traversal 回归：path traversal sub_evidence_ref 让 stray leaky.pem 进白名单 → commit\n"
        "红队 DECISIVE：ref 零校验 + git pathspec 归一化 + ancestry 前缀放行 + Layer 3 PEM 盲区 = 含凭据 stray 直达远端")
    # fail-closed 不 commit → HEAD 退回 subject（无污染 commit 残留）
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(vault),
                                   text=True).strip()
    assert head == subject, "traversal ref 须 fail-closed 不 commit（HEAD 不应前进）"
