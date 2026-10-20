# test_admit_repo.py — admit_repo 准入检查器单测（productize-pa-template task 4.4）。
#
# 全部经注入 fake runner（零真实 gh/git/网络），覆盖 mock 仓矩阵：
#   无 protection / 无 test script / 身份缺失 / 查询超时（UNKNOWN）/ 仓不可见 404（UNKNOWN）/
#   全项 FOUND（准入通过）/ CLAUDE.md 缺失只输出建议文本 / 白名单操作成败 / 交互确认默认拒 /
#   结构性守护：admit_repo.py 源码零写目标仓文件路径（R3）。
from __future__ import annotations

import base64
import json
import re
import subprocess
from pathlib import Path

import admit_repo as AR

SCRIPTS_DIR = Path(__file__).resolve().parent


def _resp(rc: int = 0, out: str = "", err: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], rc, out, err)


def _runner(gh: dict[str, subprocess.CompletedProcess] | None = None,
            git: dict[str, subprocess.CompletedProcess] | None = None,
            gh_exc: Exception | None = None):
    """按端点子串回放预置响应；未覆盖的调用 AssertionError（测试矩阵显式穷举）。"""
    def run(cmd, **_kw):
        joined = " ".join(cmd)
        if cmd[:2] == ["gh", "api"]:
            if gh_exc is not None:
                raise gh_exc
            for key, resp in (gh or {}).items():
                if key in joined:
                    return resp
        if cmd[:2] == ["git", "config"]:
            for key, resp in (git or {}).items():
                if key in joined:
                    return resp
        raise AssertionError(f"fake runner 未覆盖调用：{cmd}")
    return run


def _pkg_json(scripts: dict | None) -> str:
    pkg = {"name": "target-repo", "scripts": scripts or {}}
    return base64.b64encode(json.dumps(pkg).encode()).decode()


# ─── mock 仓矩阵：单项检查三态 ───────────────────────────────────────────────

def test_branch_protection_three_states():
    r = AR.check_branch_protection("o/r", "main", _runner(gh={
        "branches/main": _resp(out='{"protected": true}')}))
    assert (r.state, r.guidance == "") == (AR.FOUND, True)
    r = AR.check_branch_protection("o/r", "main", _runner(gh={
        "branches/main": _resp(out='{"protected": false}')}))
    assert r.state == AR.NOT_FOUND and "--configure-protection" in r.guidance
    r = AR.check_branch_protection("o/r", "main", _runner(gh={
        "branches/main": _resp(rc=1, err="gh: Not Found (HTTP 404)")}))
    assert r.state == AR.UNKNOWN and "404" in r.detail          # 仓不可见 = 不可判，阻断
    r = AR.check_branch_protection("o/r", "main", _runner(
        gh_exc=subprocess.TimeoutExpired(["gh"], 1)))
    assert r.state == AR.UNKNOWN                                  # 超时 = UNKNOWN fail-safe


def test_test_entry_states():
    r = AR.check_test_entry("o/r", _runner(gh={
        "contents/package.json": _resp(out=json.dumps({"content": _pkg_json({"test": "npm test"})}))}))
    assert r.state == AR.FOUND and "npm test" in r.detail
    r = AR.check_test_entry("o/r", _runner(gh={
        "contents/package.json": _resp(out=json.dumps({"content": _pkg_json(None)}))}))
    assert r.state == AR.NOT_FOUND and "package.json" in r.guidance
    r = AR.check_test_entry("o/r", _runner(gh={
        "contents/package.json": _resp(rc=1, err="gh: Not Found (HTTP 404)")}))
    assert r.state == AR.NOT_FOUND and "package.json" in r.guidance   # 明确缺失，可行动指引
    r = AR.check_test_entry("o/r", _runner(gh={
        "contents/package.json": _resp(out=json.dumps({"content": "!!!非base64!!!"}))}))
    assert r.state == AR.UNKNOWN                                    # 解析失败不猜


def test_commit_identity_states():
    ok = _runner(git={"user.name": _resp(out="同事\n"), "user.email": _resp(out="mate@example.com\n")})
    assert AR.check_commit_identity(None, ok).state == AR.FOUND
    no_name = _runner(git={"user.name": _resp(rc=1), "user.email": _resp(out="a@b.c\n")})
    r = AR.check_commit_identity(None, no_name)
    assert r.state == AR.NOT_FOUND and "git config --global" in r.guidance
    empty_mail = _runner(git={"user.name": _resp(out="x\n"), "user.email": _resp(out="  \n")})
    assert AR.check_commit_identity(None, empty_mail).state == AR.NOT_FOUND   # 空值同未配置
    def _boom(cmd, **_kw):
        raise RuntimeError("git 不可用")
    assert AR.check_commit_identity(None, _boom).state == AR.UNKNOWN


# ─── 端到端 CLI 矩阵（main() + capsys）──────────────────────────────────────

def _ok_runner(extra: dict | None = None) -> dict:
    gh = {
        "branches/main": _resp(out='{"protected": true}'),
        "contents/package.json": _resp(out=json.dumps({"content": _pkg_json({"test": "npm test"})})),
        "contents/CLAUDE.md": _resp(out='{"content": "IyM="}'),
    }
    git = {"user.name": _resp(out="同事\n"), "user.email": _resp(out="mate@example.com\n")}
    return {"gh": {**gh, **(extra or {})}, "git": git}


def test_cli_all_found_admits(capsys, monkeypatch):
    monkeypatch.setattr(AR.subprocess, "run", _runner(**_ok_runner()))
    assert AR.main(["o/r"]) == 0
    out = capsys.readouterr().out
    assert "准入通过" in out and "--from-stage inject" in out


def test_cli_missing_items_exit_1_with_guidance(capsys, monkeypatch):
    cfg = _ok_runner({"branches/main": _resp(out='{"protected": false}')})
    monkeypatch.setattr(AR.subprocess, "run", _runner(**cfg))
    assert AR.main(["o/r"]) == 1
    assert "未准入" in capsys.readouterr().out


def test_cli_unknown_blocks_exit_2(capsys, monkeypatch):
    monkeypatch.setattr(AR.subprocess, "run", _runner(
        gh_exc=subprocess.TimeoutExpired(["gh"], 1),
        git={"user.name": _resp(out="x\n"), "user.email": _resp(out="x@y.z\n")}))
    assert AR.main(["o/r"]) == 2
    out = capsys.readouterr().out
    assert "blocked" in out and "请勿注入" in out


def test_cli_claude_md_missing_prints_suggestion_only(capsys, monkeypatch):
    cfg = _ok_runner({"contents/CLAUDE.md": _resp(rc=1, err="gh: Not Found (HTTP 404)")})
    monkeypatch.setattr(AR.subprocess, "run", _runner(**cfg))
    assert AR.main(["o/r"]) == 0                    # CLAUDE.md 是附检：缺失不改变准入判定
    out = capsys.readouterr().out
    assert "CLAUDE.md（建议起点" in out and "不会替你写入" in out


def test_cli_bad_arg_exit_3(capsys):
    assert AR.main(["no-slash-repo"]) == 3


# ─── 平台操作白名单（R2）────────────────────────────────────────────────────

def test_configure_protection_success_and_failure(capsys, monkeypatch):
    calls: list[list[str]] = []
    def run(cmd, **_kw):
        calls.append(cmd)
        if "protection" not in " ".join(cmd):
            return _resp(out='{"protected": false}')  # 现状查询
        if "PUT" in cmd:
            return _resp()                            # 白名单写操作成功
        raise AssertionError(f"未覆盖：{cmd}")
    monkeypatch.setattr(AR.subprocess, "run", run)
    assert AR.main(["o/r", "--configure-protection", "--yes"]) == 0
    put = [c for c in calls if "PUT" in c]
    assert len(put) == 1 and put[0][-1] == "-"       # 恰一次 PUT，经 --input -
    capsys.readouterr()

    def fail_put(cmd, **_kw):
        if "PUT" in cmd:
            return _resp(rc=1, err="gh: Must have admin rights (HTTP 403)")
        return _resp(out='{"protected": false}')
    monkeypatch.setattr(AR.subprocess, "run", fail_put)
    assert AR.main(["o/r", "--configure-protection", "--yes"]) == 2   # 失败即 UNKNOWN 阻断
    assert "admin" in capsys.readouterr().out


def test_configure_protection_needs_confirmation(capsys, monkeypatch):
    """非交互终端且无 --yes → 默认拒绝，零平台写操作（monkeypatch 后 run 只该被读调用）。"""
    def run(cmd, **_kw):
        assert "PUT" not in cmd, "未经确认不得发平台写操作"
        return _resp(out='{"protected": false}')
    monkeypatch.setattr(AR.subprocess, "run", run)
    monkeypatch.setattr(AR.sys.stdin, "isatty", lambda: False)
    assert AR.main(["o/r", "--configure-protection"]) == 2
    assert "默认拒绝" in capsys.readouterr().err


def test_configure_protection_skipped_when_found(capsys, monkeypatch):
    def run(cmd, **_kw):
        assert "PUT" not in cmd
        return _resp(out='{"protected": true}')
    monkeypatch.setattr(AR.subprocess, "run", run)
    assert AR.main(["o/r", "--configure-protection", "--yes"]) == 0
    assert "无需配置" in capsys.readouterr().out


# ─── 结构性守护：控制面不写目标面（R3）──────────────────────────────────────

def test_no_write_target_surface_in_source():
    """admit_repo.py 源码不得含任何写文件调用——缺失项只能 stdout 输出文本（spec MUST NOT）。"""
    src = (SCRIPTS_DIR / "admit_repo.py").read_text(encoding="utf-8")
    assert not re.search(r"\.write_text\(|\.write_bytes\(|open\([^)]*['\"][wax]", src)
    assert not re.search(r"\bmkdir\b|\brmtree\b|\bunlink\b|\brename\b", src)
