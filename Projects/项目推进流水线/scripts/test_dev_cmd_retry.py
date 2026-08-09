#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_dev_cmd_retry.py — task 3.3 P0-3：_dev_cmd 生成 session-aware retry 参数。

r2 §6.3 要求「run_daily → RetryPolicy → recover_iteration → **dev-agent args** → SDK」端到端证据。
本文件验证命令行参数生成环节：run_daily 据 RetryPolicy.decide 的 mode，向控制面 dev-agent.py 透传
``--state-dir / --iteration-seq / --resume-session / --fork-session``（run_daily.py:_dev_cmd）。

**flag 门控语义**（baseline 零回归的核心保证）：
  * baseline（session_aware_retry 关）→ 调用点不传 session 参数 → cmd 与 r2 前完全一致（test_baseline_*）；
  * retry 模式 → 据 mode 注入对应参数（test_resume_/fork_/state_dir_/iteration_seq_）。

recover_iteration 接入与 budget 终止的门控逻辑在 run_daily.dispatch_one revise 触发点
（``if _coord.flags.session_aware_retry ...``），由 test_dispatch_flag_integration / test_coordinator
覆盖 flag→路径边界；本文件专注 _dev_cmd 的纯函数参数生成。
"""
import pytest

import run_daily


@pytest.fixture(autouse=True)
def _isolate_dev_model_routing(tmp_path, monkeypatch):
    """隔离 dev model 路由：env 清 + 文件指向不存在（baseline 测试不受真实 config/env 干扰）。"""
    monkeypatch.delenv("PA_DEV_MODEL", raising=False)
    monkeypatch.setattr(run_daily.model_routing, "_DEFAULT_PATH", tmp_path / "isolated-empty.json")


def _cmd(**kw) -> list:
    """构造 _dev_cmd 调用（prof 无 conda_env → 宿主 python）。"""
    return run_daily._dev_cmd({"conda_env": ""}, "/tmp/x.prd", "main", "/tmp/src", **kw)


def test_baseline_cmd_no_session_args():
    """baseline（不传 session 参数）→ cmd 不含任何 retry 参数（与 r2 前零差异）。"""
    cmd = _cmd()
    for flag in ("--state-dir", "--iteration-seq", "--resume-session", "--fork-session"):
        assert flag not in cmd, f"baseline cmd 不应含 {flag}: {cmd}"


def test_state_dir_unifies_session_store():
    """state_dir=控制面 STATE_DIR → 注入 --state-dir（dev-agent SessionStore 与控制面同一）。"""
    cmd = _cmd(state_dir="/vault/.project-auto/state", iteration_seq=2)
    assert cmd[cmd.index("--state-dir") + 1] == "/vault/.project-auto/state"


def test_iteration_seq_injected_when_positive():
    """seq>0（retry 衍生 distinct iteration）→ 注入 --iteration-seq N。"""
    cmd = _cmd(state_dir="/x", iteration_seq=3)
    assert cmd[cmd.index("--iteration-seq") + 1] == "3"


def test_iteration_seq_zero_omitted():
    """seq=0（baseline 新 session）→ 不注入 --iteration-seq（dev-agent 走默认 round1）。"""
    cmd = _cmd(state_dir="/x", iteration_seq=0)
    assert "--iteration-seq" not in cmd


def test_resume_session_arg_for_resume_mode():
    """RetryMode.RESUME → 注入 --resume-session <id>，不含 --fork-session。"""
    cmd = _cmd(iteration_seq=2, resume_session="sess-abc")
    assert cmd[cmd.index("--resume-session") + 1] == "sess-abc"
    assert "--fork-session" not in cmd


def test_fork_session_arg_for_fork_mode():
    """RetryMode.FORK → 注入 --fork-session，不含 --resume-session。"""
    cmd = _cmd(iteration_seq=2, fork_session=True)
    assert "--fork-session" in cmd
    assert "--resume-session" not in cmd


def test_new_session_mode_emits_neither():
    """RetryMode.NEW_SESSION → resume/fork 均不注入（仅 state_dir + iteration_seq）。"""
    cmd = _cmd(state_dir="/x", iteration_seq=2)
    assert "--resume-session" not in cmd
    assert "--fork-session" not in cmd


# ─── per-agent model routing：_dev_cmd 文件透传（add-per-agent-model-routing）─────────
# dev-agent 是 ADR-0006 纯调度器，不读控制面文件；config 的 "dev" key + env PA_DEV_MODEL
# 经 _dev_cmd 解析成 --model flag 送达 dev-agent。优先级：env（canary 覆盖）> config 文件 > 不透传。
def test_dev_model_file_routed_to_flag(tmp_path, monkeypatch):
    """config 配 "dev": sonnet → _dev_cmd 透传 --model=sonnet（文件为主配置源）。"""
    cfg = tmp_path / "model-routing.json"
    cfg.write_text('{"dev": "sonnet"}', encoding="utf-8")
    monkeypatch.setattr(run_daily.model_routing, "_DEFAULT_PATH", cfg)
    cmd = _cmd()
    assert "--model=sonnet" in cmd


def test_dev_model_env_overrides_file(tmp_path, monkeypatch):
    """env PA_DEV_MODEL + 文件都配 → env 胜（canary 覆盖主配置）。"""
    monkeypatch.setenv("PA_DEV_MODEL", "haiku")
    cfg = tmp_path / "model-routing.json"
    cfg.write_text('{"dev": "sonnet"}', encoding="utf-8")
    monkeypatch.setattr(run_daily.model_routing, "_DEFAULT_PATH", cfg)
    cmd = _cmd()
    assert "--model=haiku" in cmd
    assert "--model=sonnet" not in cmd


def test_dev_model_neither_env_nor_file_omits_flag(tmp_path, monkeypatch):
    """env 未设 + 文件未配 dev → cmd 无 --model*（baseline byte-identical）。"""
    monkeypatch.setattr(run_daily.model_routing, "_DEFAULT_PATH", tmp_path / "nope.json")
    cmd = _cmd()
    assert not any(x.startswith("--model") for x in cmd)


# ─── review 2026-08-09 dev 对称性（⑤route log ⑥空串 warn，对称 persona run_persona）──
def test_dev_model_env_empty_warns(capsys, monkeypatch):
    """PA_DEV_MODEL="" → log warn（对称 persona 空串 warn；review ⑥）。"""
    monkeypatch.setenv("PA_DEV_MODEL", "")
    cmd = _cmd()
    out = capsys.readouterr().out
    assert "PA_DEV_MODEL 设为空串" in out
    assert not any(x.startswith("--model") for x in cmd)


def test_dev_model_route_logged(capsys, tmp_path, monkeypatch):
    """dev model 命中（文件）→ log [dev] model route 审计行（对称 persona route log；review ⑤）。"""
    cfg = tmp_path / "model-routing.json"
    cfg.write_text('{"dev": "sonnet"}', encoding="utf-8")
    monkeypatch.setattr(run_daily.model_routing, "_DEFAULT_PATH", cfg)
    cmd = _cmd()
    out = capsys.readouterr().out
    assert "[dev] model route → sonnet" in out
    assert "--model=sonnet" in cmd
