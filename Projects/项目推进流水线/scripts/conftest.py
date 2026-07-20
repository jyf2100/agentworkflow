# -*- coding: utf-8 -*-
"""conftest.py — pytest 共享 fixture（OpenSpec reproducible-pipeline-validation / 本变更 task 3.1）。

提供 Node / Python 目标仓骨架 + dispatch 外部依赖统一桩，让所有 dispatch 相关单测：

    - 不触达真实 SDK（claude_agent_sdk）、GitHub（gh）、SMTP、凭证、模型调用；
    - 不依赖目标仓自带执行器——仓内故意 **不含** scripts/dev-agent.*，证明控制面执行器为唯一源
      （ADR-0006 / OpenSpec verified-dev-execution）；
    - 目标仓语言（Node / Python）可切换，验证「目标仓语言不决定执行器语言」。

约定：dispatch 单测的运行时实查与远程查询一律由 ``stub_externals`` 桩掉；
仓骨架 fixture 只落语言骨架（package.json / pyproject.toml），故意不含 dev-agent.*。
（这些 fixture 主要被本变更 3.5 测试与 Section 4 失败模式测试消费；现役 dispatch 单测沿用其
模块内 ``_setup/_admit/_repo`` 辅助，互不冲突。）
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

# scripts/ 已在 pyproject [tool.pytest.ini_options].pythonpath 下；此处补 insert 保稳健（与现役
# test_*.py 顶部 sys.path.insert 模式一致），使 ``import run_daily`` 在任意收集方式下可解析。
sys.path.insert(0, str(Path(__file__).parent))
from external_state import found, not_found   # 三态桩返回值（OpenSpec fail-safe-dispatch）


def _git_init(repo: Path) -> Path:
    """把临时目录初始化成可用 git 仓（worktree add 前置：dispatch_one 会对仓做 ``git -C repo worktree add``）。"""
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "pa-test@example.com"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "pa-test"], cwd=repo, check=True, capture_output=True)
    # 一个初始 commit：worktree add --detach <base> 需要 base 存在于对象库
    (repo / ".gitkeep").write_text("", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True, capture_output=True)
    return repo


@pytest.fixture
def node_target_repo(tmp_path) -> Path:
    """Node 目标仓骨架：package.json + src/index.js + tests/；**故意不含** scripts/dev-agent.*。

    证明：Node 仓不自带执行器也能投递（控制面执行器唯一源）。"""
    repo = tmp_path / "node-repo"
    _git_init(repo)
    (repo / "package.json").write_text(
        '{"name":"node-repo","version":"0.1.0","scripts":{"test":"node --test"}}', encoding="utf-8")
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src" / "index.js").write_text("module.exports = {};\n", encoding="utf-8")
    (repo / "tests").mkdir(exist_ok=True)
    (repo / "tests" / "smoke.test.js").write_text(
        "const assert = require('assert');\nassert.ok(true);\n", encoding="utf-8")
    return repo


@pytest.fixture
def python_target_repo(tmp_path) -> Path:
    """Python 目标仓骨架：pyproject.toml + pkg/__init__.py + tests/；**故意不含** scripts/dev-agent.*。

    证明：Python 仓不自带执行器也能投递（控制面执行器唯一源）。"""
    repo = tmp_path / "py-repo"
    _git_init(repo)
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "py-repo"\nversion = "0.1.0"\n'
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n', encoding="utf-8")
    (repo / "pkg").mkdir(exist_ok=True)
    (repo / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "tests").mkdir(exist_ok=True)
    (repo / "tests" / "test_smoke.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    return repo


@pytest.fixture
def stub_externals(monkeypatch):
    """桩掉 dispatch 全部外部依赖（SDK / GitHub / SMTP / 凭证 / 模型）——单测零 IO。

    覆盖准入三查（branch_protection / inflight / idempotency）+ commit 探测（_has_commits /
    _dump_branch_diff）。准入门全过；其余场景化桩（_run_dev_agent / independent_verify /
    run_persona / reconcile_pr）由具体测试按需覆盖（它们控制 dev 分支序列与 verify 裁决）。

    返回 run_daily 模块对象，供调用方进一步 monkeypatch。"""
    import run_daily
    monkeypatch.setattr(run_daily, "check_branch_protection", lambda *a, **k: found(True, "stub:已保护"))
    monkeypatch.setattr(run_daily, "count_inflight_prs", lambda *a, **k: found(0))
    monkeypatch.setattr(run_daily, "already_dispatched", lambda *a, **k: not_found())
    monkeypatch.setattr(run_daily, "repo_owner_repo", lambda *a, **k: "owner/repo")
    monkeypatch.setattr(run_daily, "_has_commits", lambda *a, **k: found(True))
    monkeypatch.setattr(run_daily, "_dump_branch_diff", lambda *a, **k: None)
    return run_daily
