#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_load_claude_settings_env.py — _load_claude_settings_env 注入单测。

验证 settings.json env block → os.environ 的注入规则：
  - ANTHROPIC_* 注入（认证）
  - add-per-agent-model-routing：PA_*_MODEL* 注入（per-agent 路由配置入口）
  - 非 model 路由的 PA_*（PA_HEARTBEAT / PA_CLAUDE_BIN）/ OBSIDIAN_VAULT_PATH 不注入
  - setdefault：已在 os.environ 的不覆盖

AAA 结构。跑：python3 -m pytest scripts/test_load_claude_settings_env.py -q
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import run_daily  # noqa: E402


def _write_fake_settings(home: Path, env: dict) -> None:
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    (home / ".claude" / "settings.json").write_text(
        json.dumps({"env": env}), encoding="utf-8")


def test_loads_anthropic_and_pa_model_routing_from_settings(tmp_path, monkeypatch):
    """settings.json env block 的 ANTHROPIC_* + PA_*_MODEL* 都注入 os.environ（统一配置入口）。"""
    fake_home = tmp_path / "home"
    _write_fake_settings(fake_home, {
        "ANTHROPIC_MODEL": "glm-5.2",
        "PA_PERSONA_MODEL_PA_PROGRESS": "haiku",
        "PA_DEV_MODEL": "sonnet",
    })
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    for k in ["ANTHROPIC_MODEL", "PA_PERSONA_MODEL_PA_PROGRESS", "PA_DEV_MODEL"]:
        monkeypatch.delenv(k, raising=False)
    run_daily._load_claude_settings_env()
    assert os.environ["PA_PERSONA_MODEL_PA_PROGRESS"] == "haiku"
    assert os.environ["PA_DEV_MODEL"] == "sonnet"
    assert os.environ["ANTHROPIC_MODEL"] == "glm-5.2"


def test_skips_non_model_pa_env_and_obsidian_path(tmp_path, monkeypatch):
    """PA_* 非 model 项（PA_HEARTBEAT / PA_CLAUDE_BIN）+ OBSIDIAN_VAULT_PATH 不注入。"""
    fake_home = tmp_path / "home"
    _write_fake_settings(fake_home, {
        "PA_HEARTBEAT": "1",
        "PA_CLAUDE_BIN": "/fake/claude",
        "OBSIDIAN_VAULT_PATH": "/mac/only",
    })
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    for k in ["PA_HEARTBEAT", "PA_CLAUDE_BIN", "OBSIDIAN_VAULT_PATH"]:
        monkeypatch.delenv(k, raising=False)
    run_daily._load_claude_settings_env()
    assert "PA_HEARTBEAT" not in os.environ
    assert "PA_CLAUDE_BIN" not in os.environ
    assert "OBSIDIAN_VAULT_PATH" not in os.environ


def test_setdefault_does_not_override_existing_env(tmp_path, monkeypatch):
    """已在 os.environ 的不覆盖（setdefault 语义，与 claude CLI 一致）。"""
    fake_home = tmp_path / "home"
    _write_fake_settings(fake_home, {"PA_DEV_MODEL": "from-settings"})
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    monkeypatch.setenv("PA_DEV_MODEL", "from-shell")  # 显式 export 优先
    run_daily._load_claude_settings_env()
    assert os.environ["PA_DEV_MODEL"] == "from-shell"  # 未被 settings 覆盖
