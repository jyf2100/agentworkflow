#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_dev_agent_source.py — ADR-0006 dev-agent 选源 + slug 单一源头单测（TDD）。

覆盖 ADR-0006 follow-up ② 接线：
    - dev_slugify 单一源头（slug_utils.py，消解 ADR-0004 #4 shadow）
    - _dev_cmd 的 dev_agent_source 选源（vault | repo 默认）
    - run_daily.py 顶部 import slug_utils 不连带加载 claude_agent_sdk（保护 cron 不崩）

跑：python3 -m pytest scripts/test_dev_agent_source.py -q
AAA 结构（Arrange / Act / Assert）。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import run_daily  # noqa: E402
import slug_utils  # noqa: E402


# ─── dev_slugify 单一源头（slug_utils.py）─────────────────────────────
def test_dev_slugify_known_inputs():
    """确立新源头行为基线（与历史 dev-agent.{py,mjs} 等价）。"""
    # Arrange / Act / Assert
    assert slug_utils.dev_slugify("feat-branch-name") == "feat-branch-name"
    assert slug_utils.dev_slugify("--Hello World--") == "hello-world"
    assert slug_utils.dev_slugify("测试-ABC 123!@#") == "abc-123"        # 非 [a-z0-9] 规整为 -，去首尾
    assert slug_utils.dev_slugify("A" * 60) == "a" * 48                  # 截断 48
    # canary 判据 c 回归（2026-07-30）：同日同 project 不同 desc 的 slug，devslug 须保留区分性，
    # 否则幂等闸子串匹配误判「已投递」→ 静默跳过（[:24] 会把两者都截成 20260730-cc-web-control-）。
    assert slug_utils.dev_slugify("20260730_cc-web-control-canary-red") != \
        slug_utils.dev_slugify("20260730_cc-web-control-canary-green")
    assert slug_utils.dev_slugify("---all separators---") == "all-separators"
    assert slug_utils.dev_slugify("") == ""                             # 空串稳


def test_dev_slugify_is_single_source_in_run_daily():
    """run_daily.dev_slugify 必须就是 slug_utils.dev_slugify（消解 shadow 回归网）。"""
    assert run_daily.dev_slugify is slug_utils.dev_slugify
    assert run_daily.dev_slugify.__module__ == "slug_utils"


def test_run_daily_import_does_not_load_claude_sdk():
    """run_daily.py 顶部不得连带加载 claude_agent_sdk。

    权威 cron 副作用回归网：run_cron.sh 用裸 /usr/bin/python3（无 sdk）跑 run_daily.py 顶层。
    用全新 subprocess 模拟（避免 pytest 主进程污染）——一旦有人把 run_daily 顶部改成
    import dev-agent（含 sdk 顶层加载），此测试 RED，挡住「每晚 cron 崩」。"""
    scripts_dir = str(Path(__file__).resolve().parent)
    result = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0,'.'); import run_daily; "
         "print('claude_agent_sdk' in sys.modules)"],
        capture_output=True, text=True, timeout=30, check=True, cwd=scripts_dir,
    )
    assert result.stdout.strip() == "False", (
        "run_daily.py 连带加载了 claude_agent_sdk——cron 的 /usr/bin/python3 无 sdk 会崩。\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


# ─── _dev_cmd 控制面执行器唯一源（ADR-0006 / OpenSpec verified-dev-execution / fail-safe-dispatch）─────
def test_dev_cmd_always_uses_vault_executor_without_local_executor():
    """仓内无任何 dev-agent 文件 → 仍返回有效 cmd，指向控制面 dev-agent.py。

    「控制面执行器为唯一源」核心断言：目标仓不自带执行器即可投递。"""
    # Arrange / Act
    cmd = run_daily._dev_cmd({"conda_env": ""}, "PRD.md", "main", "")
    # Assert
    assert cmd is not None
    assert str(run_daily.DEV_AGENT_PY) in cmd
    assert "--prd" in cmd and cmd[cmd.index("--prd") + 1] == "PRD.md"
    assert "--base" in cmd and cmd[cmd.index("--base") + 1] == "main"
    assert "--source" not in cmd                      # 空 source 不追加


def test_dev_cmd_appends_source():
    """传 src_abs → 追加 --source。"""
    cmd = run_daily._dev_cmd({"conda_env": ""}, "PRD", "main", "SRC.md")
    assert cmd is not None
    assert "--source" in cmd and cmd[cmd.index("--source") + 1] == "SRC.md"


def test_dev_cmd_ignores_legacy_repo_executor(tmp_path):
    """仓内遗留 scripts/dev-agent.{py,mjs} 存在 → 仍走控制面执行器（legacy ignored）。

    旧 profile 字段 dev_agent_source=repo 也不再分支（向后兼容：读取不报错，但值被忽略）。"""
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / "dev-agent.py").write_text("# legacy", encoding="utf-8")
    (repo / "scripts" / "dev-agent.mjs").write_text("# legacy", encoding="utf-8")
    prof = {"dev_agent_source": "repo", "conda_env": ""}    # 旧字段，值被忽略
    cmd = run_daily._dev_cmd(prof, "PRD", "main", "")
    cmd_str = " ".join(cmd)
    assert str(run_daily.DEV_AGENT_PY) in cmd_str          # 控制面执行器
    assert str(repo) not in cmd_str                        # 仓内 legacy 路径不在命令里
    assert cmd[0] != "node"                                # 不走 Node 兜底


def test_dev_cmd_none_when_vault_executor_missing(tmp_path, monkeypatch):
    """控制面 dev-agent.py 缺失（DEV_AGENT_PY 不存在）→ None（dispatch 判 fail：控制面安装异常）。"""
    monkeypatch.setattr(run_daily, "DEV_AGENT_PY", tmp_path / "nonexistent.py")
    assert run_daily._dev_cmd({"conda_env": ""}, "PRD", "main", "") is None


# ─── 目标仓语言不决定执行器语言（用 conftest 的 Node/Python 仓骨架，task 3.1 × 3.5）─────
def test_dev_cmd_node_repo_without_local_executor_uses_control_plane(node_target_repo):
    """Node 目标仓（package.json，无 scripts/dev-agent.*）→ 执行器仍是控制面 Python，不走 node。"""
    cmd = run_daily._dev_cmd({"conda_env": ""}, "PRD", "main", "")
    assert str(run_daily.DEV_AGENT_PY) in " ".join(cmd)
    assert cmd[0] != "node"


def test_dev_cmd_python_repo_without_local_executor_uses_control_plane(python_target_repo):
    """Python 目标仓（pyproject.toml，无 scripts/dev-agent.*）→ 执行器仍是控制面 dev-agent.py。"""
    cmd = run_daily._dev_cmd({"conda_env": ""}, "PRD", "main", "")
    assert str(run_daily.DEV_AGENT_PY) in " ".join(cmd)
