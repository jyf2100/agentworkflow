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
    assert slug_utils.dev_slugify("A" * 30) == "a" * 24                  # 截断 24
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


# ─── _dev_cmd 选源（ADR-0006）─────────────────────────────────────────
def test_dev_cmd_source_vault_ignores_repo_files(tmp_path):
    """source=vault：仓内无任何 dev-agent 文件，仍返回有效 cmd（指向控制面 dev-agent.py）。"""
    # Arrange
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    py = repo / "scripts" / "dev-agent.py"      # 不存在
    mjs = repo / "scripts" / "dev-agent.mjs"    # 不存在
    prof = {"dev_agent_source": "vault", "conda_env": ""}
    vault_py = Path(run_daily.__file__).resolve().parent / "dev-agent.py"
    # Act
    cmd = run_daily._dev_cmd(prof, py, mjs, "PRD.md", "main", "")
    # Assert
    assert cmd is not None
    assert str(vault_py) in cmd                  # 指向 vault 版（忽略仓内文件）
    assert "--prd" in cmd and cmd[cmd.index("--prd") + 1] == "PRD.md"
    assert "--base" in cmd and cmd[cmd.index("--base") + 1] == "main"


def test_dev_cmd_source_vault_appends_source(tmp_path):
    """source=vault 且传 src_abs → 追加 --source。"""
    repo = tmp_path / "repo"
    prof = {"dev_agent_source": "vault", "conda_env": ""}
    cmd = run_daily._dev_cmd(prof, repo / "x.py", repo / "x.mjs", "PRD", "main", "SRC.md")
    assert cmd is not None
    assert "--source" in cmd and cmd[cmd.index("--source") + 1] == "SRC.md"


def test_dev_cmd_source_repo_default_falls_back_to_repo_python(tmp_path):
    """无 dev_agent_source（默认 repo）：仓内 py 存在 → 用仓内 py（现状不变）。"""
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    py = repo / "scripts" / "dev-agent.py"
    py.write_text("#", encoding="utf-8")
    mjs = repo / "scripts" / "dev-agent.mjs"
    cmd = run_daily._dev_cmd({"conda_env": ""}, py, mjs, "PRD", "main", "")   # 无 dev_agent_source
    assert cmd is not None
    assert str(py) in cmd                                                       # 用仓内 py，非 vault 版
