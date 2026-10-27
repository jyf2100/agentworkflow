"""repo_owner_repo 双形态契约：本地仓路径走 git 提取；URL 形态回退正则解析。

背景（2026-10-27 cc-web-control hub 首跑）：容器首跑目标仓不存在，准入 2 先于
clone 段判 owner_repo（git -C 提取）恒 None → github 面必 skip（gitlab 面 fapi≠None
绕过此门）。mac 真身 workspace 仓常驻从未暴露。修：URL 形态（hub 容器 profile
repo 常态）直接正则解析，本地路径形态维持 git 提取零变化。
"""
import subprocess

import run_daily


def test_url_form_https(monkeypatch):
    # 本地 git 提取不可用（仓不存在）→ URL 回退解析
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=""))
    assert run_daily.repo_owner_repo("https://github.com/jyf2100/cc-web-control") == "jyf2100/cc-web-control"


def test_url_form_with_git_suffix_and_slash(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=""))
    assert run_daily.repo_owner_repo("https://github.com/jyf2100/cc-web-control.git/") == "jyf2100/cc-web-control"


def test_url_form_gitlab_multi_segment(monkeypatch):
    # 多级路径（组/子组/仓）——取末两级（与 git remote 提取正则「末两级」语义一致）
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=""))
    assert run_daily.repo_owner_repo("https://gitlab.com/grp/sub/cc-web-control") == "sub/cc-web-control"
    assert run_daily.repo_owner_repo("https://gitlab.com/grp/sub/proj.git") == "sub/proj"


def test_local_path_without_url_fallback(monkeypatch):
    # 本地路径（无 scheme）解析不出 → None（现行为，由真 git 提取兜底）
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, stdout=""))
    assert run_daily.repo_owner_repo("/abs/path/to/repo") is None


def test_git_extract_still_wins(monkeypatch):
    # 本地仓存在（git 提取成功）→ URL 回退不参与
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="git@github.com:real/one.git\n"))
    assert run_daily.repo_owner_repo("/abs/path") == "real/one"


def test_empty_and_garbage(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, stdout=""))
    assert run_daily.repo_owner_repo("") is None
    assert run_daily.repo_owner_repo("not a url") is None
