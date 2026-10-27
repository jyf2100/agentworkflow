"""github 面容器形态 dispatch 解锁（2026-10-27 cc-web-control 全链测试）：

容器无本地仓时 repo 为 URL 形态（hub 面板/CLI 建 profile 常态）。#12 给 forge 面
补的两通道，github 面对齐：①幂等 ls-remote 直连 URL（-C repo 必炸 No such
directory）②clone 落点重映射 vault 根下 workspace/<name>（URL 当 Path 炸协议前缀）。
"""
import subprocess

import run_daily
from external_state import ExtState


class _Cap:
    """捕 _net_gate 收到的 argv（ls-remote 路径）。"""
    def __init__(self):
        self.argv = None

    def __call__(self, fn, label):
        self.argv = None  # run 前清；真 argv 由 fake subprocess 捕
        return fn()


def test_scan_remote_url_repo_no_dash_c(monkeypatch):
    """repo 为 URL 形态 → ls-remote 直连 URL，argv 不含 -C（容器无本地仓）。"""
    seen = {}

    def fake_run(argv, **k):
        seen["argv"] = argv
        return subprocess.CompletedProcess(argv, 0, stdout="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(run_daily, "_net_gate", lambda fn, label: fn())
    err, hit = run_daily._scan_remote_branches_for_slug(
        "https://github.com/jyf2100/cc-web-control", "devslug")
    assert err is None and hit is None
    assert seen["argv"] == ["git", "ls-remote", "--heads",
                            "https://github.com/jyf2100/cc-web-control"]


def test_scan_remote_local_repo_unchanged(monkeypatch):
    """repo 本地路径形态 → 旧路径 -C repo（真机行为零变化）。"""
    seen = {}

    def fake_run(argv, **k):
        seen["argv"] = argv
        return subprocess.CompletedProcess(argv, 0, stdout="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(run_daily, "_net_gate", lambda fn, label: fn())
    run_daily._scan_remote_branches_for_slug("/abs/repo", "devslug")
    assert seen["argv"] == ["git", "-C", "/abs/repo", "ls-remote", "--heads", "origin"]


# ─── ls-remote 超时重试 1 次（v0.1.17，m15/m16 连续死于同一处）─────────────────
# 幂等分支扫描单次 20s 无重试：时段性网络抖一下 → UNKNOWN → fail-safe 阻断
# （m15 #69 / m16 #70 同路径；事后实测 1s 通 = 抖动非持续故障）。修：TimeoutExpired
# 重试 1 次，两次都超时才返错阻断（fail-safe 语义不破）；rc≠0（401 凭据错等永久
# 形态）不重试——重试只救瞬态，不给永久错多烧 20s。
def test_scan_remote_timeout_retries_once_then_succeeds(monkeypatch):
    """第 1 次超时（抖动）→ 重试；第 2 次成功无命中 → (None, None)，共调 2 次。"""
    calls = []

    def fake_run(argv, **k):
        calls.append(argv)
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(argv, 20)
        return subprocess.CompletedProcess(argv, 0, stdout="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(run_daily, "_net_gate", lambda fn, label: fn())
    err, hit = run_daily._scan_remote_branches_for_slug(
        "https://github.com/jyf2100/cc-web-control", "devslug")
    assert err is None and hit is None
    assert len(calls) == 2


def test_scan_remote_both_timeouts_returns_err(monkeypatch):
    """两次都超时 → 返错（UNKNOWN fail-safe 阻断语义保留），不再第三试。"""
    calls = []

    def fake_run(argv, **k):
        calls.append(argv)
        raise subprocess.TimeoutExpired(argv, 20)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(run_daily, "_net_gate", lambda fn, label: fn())
    err, hit = run_daily._scan_remote_branches_for_slug(
        "https://github.com/jyf2100/cc-web-control", "devslug")
    assert err and "超时" in err
    assert hit is None
    assert len(calls) == 2


def test_scan_remote_nonzero_rc_no_retry(monkeypatch):
    """rc≠0（如私有仓 401）→ 立即返错不重试（永久形态重试=白烧 20s×N）。"""
    calls = []

    def fake_run(argv, **k):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 128, stdout="", stderr="fatal: Authentication failed")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(run_daily, "_net_gate", lambda fn, label: fn())
    err, hit = run_daily._scan_remote_branches_for_slug(
        "https://github.com/jyf2100/cc-web-control", "devslug")
    assert err and "rc=128" in err
    assert len(calls) == 1


# ─── github 面分支幂等改走 gh api（v0.1.18，m15-m17 三连 ls-remote 间歇挂死）─────
# 同容器实测：gh（api.github.com）三轮全稳；git ls-remote（github.com smart HTTP）
# 分钟级间歇挂死（132s 才 fatal，同时 curl 1s 通 = 非网络封锁是端点间歇抽）。
# 分支扫描与 PR 扫描并轨 gh 同通道；forge 面（无 gh）保留 ls-remote+重试。
def test_branch_scan_via_gh_hit_found(monkeypatch):
    """github 面：gh api 分支列表含 auto/*+slug → already_dispatched 判 FOUND。"""
    def fake_run(argv, **k):
        if any("branches" in a for a in argv):
            return subprocess.CompletedProcess(argv, 0, stdout="main\ndev\nauto/20261027-slug-x")
        return subprocess.CompletedProcess(argv, 0, stdout="[]")   # gh pr list 空

    monkeypatch.setattr(run_daily.subprocess, "run", fake_run)
    monkeypatch.setattr(run_daily, "_net_gate", lambda fn, label: fn())
    r = run_daily.already_dispatched("jyf2100/cc-web-control",
                                     "https://github.com/jyf2100/cc-web-control", "slug-x")
    assert r.state is ExtState.FOUND
    assert "auto/20261027-slug-x" in (r.reason or "")


def test_branch_scan_via_gh_no_git_call(monkeypatch):
    """github 面：git ls-remote 完全不被调（挂死通道隔离）。"""
    def fake_run(argv, **k):
        if argv[0] == "git":
            raise AssertionError("github 面不应再调 git ls-remote")
        if any("branches" in a for a in argv):
            return subprocess.CompletedProcess(argv, 0, stdout="main")
        return subprocess.CompletedProcess(argv, 0, stdout="[]")

    monkeypatch.setattr(run_daily.subprocess, "run", fake_run)
    monkeypatch.setattr(run_daily, "_net_gate", lambda fn, label: fn())
    r = run_daily.already_dispatched("jyf2100/cc-web-control",
                                     "https://github.com/jyf2100/cc-web-control", "slug-x")
    assert r.state is ExtState.NOT_FOUND


def test_branch_scan_via_gh_failure_unknown(monkeypatch):
    """gh api 分支查询 rc≠0 → UNKNOWN（fail-safe 阻断语义不变）。"""
    def fake_run(argv, **k):
        if any("branches" in a for a in argv):
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="gh: Not Found")
        return subprocess.CompletedProcess(argv, 0, stdout="[]")

    monkeypatch.setattr(run_daily.subprocess, "run", fake_run)
    monkeypatch.setattr(run_daily, "_net_gate", lambda fn, label: fn())
    r = run_daily.already_dispatched("jyf2100/cc-web-control",
                                     "https://github.com/jyf2100/cc-web-control", "slug-x")
    assert r.state is ExtState.UNKNOWN
    assert "rc=1" in (r.reason or "")


def test_branch_scan_forge_keeps_lsremote(monkeypatch):
    """forge 面（fapi 带 web_url）→ 仍走 git ls-remote 旧通道（含重试）。"""
    seen = {}

    class _Fapi:
        web_url = "https://gitlab.example.com/grp/proj.git"
        forge = "gitlab"

    def fake_run(argv, **k):
        seen["argv0"] = argv[0]
        if argv[0] == "git":
            return subprocess.CompletedProcess(argv, 0, stdout="")
        return subprocess.CompletedProcess(argv, 0, stdout="[]")

    monkeypatch.setattr(run_daily.subprocess, "run", fake_run)
    monkeypatch.setattr(run_daily, "_net_gate", lambda fn, label: fn())
    monkeypatch.setattr(run_daily, "_scan_pr_list_for_slug",
                        lambda owner, slug, fapi=None: (None, None))
    run_daily.already_dispatched("o/r", "grp/proj", "slug-x", fapi=_Fapi())
    assert seen.get("argv0") == "git"


def test_repo_local_path_mapping():
    """URL → vault/workspace/<name>；本地路径原样。"""
    p = run_daily._repo_local_path("https://github.com/jyf2100/cc-web-control")
    assert p.endswith("workspace/cc-web-control") and "https:" not in p
    p2 = run_daily._repo_local_path("https://github.com/o/r.git/")
    assert p2.endswith("workspace/r")
    assert run_daily._repo_local_path("/abs/repo") == "/abs/repo"
    assert run_daily._repo_local_path("pa-qa/sub/proj") == "pa-qa/sub/proj"
