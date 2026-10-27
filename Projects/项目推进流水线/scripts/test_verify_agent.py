#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_verify_agent.py — agent-verify-injection 2.1：verify agent 执行器（TDD 先红）。

D12 共享参数面实证：cmd = stream-json 面（--output-format stream-json --verbose——2.0 冒烟
实证 ``-p`` 下必须 --verbose）+ persona_call.shared_persona_args 同源段（--effort medium；
PA_PERSONA_MODEL_PA_VERIFY_AGENT 命中 → ``--model=<值>``；不设 → 无 --model 走 roc 默认），
不在本模块手拼第三份。D13 墙钟分档：PA_VERIFY_WALL_CLOCK 覆盖 > /data/pa 探测 hub 档 3600s
> 本机档。会话墙钟超时 raise VerifySessionTimeout（编排层映射 **invalid 非红**——测试学二审
T4：超时是不可判读不是失败）。subprocess 全 mock 零 LLM。
跑：python3 -m pytest scripts/test_verify_agent.py -q
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import verify_agent as VA   # noqa: E402


class _FakeProc:
    returncode = 0
    stdout = '{"type":"result"}\n'
    stderr = ""


def _run_executor(monkeypatch, env=None, timeout_exc=None):
    """fake subprocess.run 捕 cmd；env 附加注入；可模拟墙钟超时。"""
    calls = {}

    def fake_run(cmd, **kw):
        calls["cmd"] = cmd
        calls["kw"] = kw
        if timeout_exc:
            raise timeout_exc
        return _FakeProc()

    monkeypatch.setattr(VA.subprocess, "run", fake_run)
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    ex = VA.ClaudeHeadlessExecutor(claude_bin="/bin/claude-fake")
    return ex, calls


# ─── D12 共享参数面 ────────────────────────────────────────────────────
def test_cmd_stream_format_and_shared_args(monkeypatch):
    """cmd = stream-json --verbose + --effort medium（共享面）+ allowedTools/max-turns。"""
    monkeypatch.delenv("PA_PERSONA_MODEL_PA_VERIFY_AGENT", raising=False)
    monkeypatch.setattr(VA.persona_call.model_routing, "resolve_persona_model", lambda n: None)
    ex, calls = _run_executor(monkeypatch)
    raw = ex.run(Path("/tmp/wt"), "verify prompt", timeout=60)
    cmd = calls["cmd"]
    assert raw == _FakeProc.stdout
    assert cmd[0] == "/bin/claude-fake"
    assert "--output-format" in cmd and cmd[cmd.index("--output-format") + 1] == "stream-json"
    assert "--verbose" in cmd                       # 2.0 冒烟：-p + stream-json 必须 --verbose
    assert "--effort" in cmd and cmd[cmd.index("--effort") + 1] == "medium"   # 共享面同源
    assert "--allowedTools" in cmd and cmd[cmd.index("--allowedTools") + 1] == "Bash,Read"
    assert "--max-turns" in cmd
    assert calls["kw"]["cwd"] == "/tmp/wt"           # verify 会话贴 worktree


def test_cmd_model_route_via_shared_env(monkeypatch):
    """PA_PERSONA_MODEL_PA_VERIFY_AGENT=haiku → --model=haiku（经共享面，非手拼）。"""
    ex, calls = _run_executor(monkeypatch, env={"PA_PERSONA_MODEL_PA_VERIFY_AGENT": "haiku"})
    ex.run(Path("/tmp/wt"), "p", timeout=60)
    assert "--model=haiku" in calls["cmd"]


def test_cmd_no_model_when_unset(monkeypatch):
    """未设 env 且 config 无条目 → 无 --model（roc 默认）。"""
    monkeypatch.delenv("PA_PERSONA_MODEL_PA_VERIFY_AGENT", raising=False)
    monkeypatch.setattr(VA.persona_call.model_routing, "resolve_persona_model", lambda n: None)
    ex, calls = _run_executor(monkeypatch)
    ex.run(Path("/tmp/wt"), "p", timeout=60)
    assert not any(a.startswith("--model") for a in calls["cmd"])


# ─── 超时语义（T4：invalid 非红） ──────────────────────────────────────
def test_wall_clock_timeout_raises(monkeypatch):
    """墙钟超时 → VerifySessionTimeout（编排层映射 invalid，非红）。"""
    ex, _ = _run_executor(monkeypatch, timeout_exc=subprocess.TimeoutExpired(cmd=[], timeout=5))
    with pytest.raises(VA.VerifySessionTimeout):
        ex.run(Path("/tmp/wt"), "p", timeout=5)


# ─── D13 墙钟分档 ─────────────────────────────────────────────────────
def test_wall_clock_env_override(monkeypatch):
    monkeypatch.setenv("PA_VERIFY_WALL_CLOCK", "99")
    assert VA.resolve_wall_clock() == 99.0


def test_wall_clock_hub_tier(monkeypatch):
    monkeypatch.delenv("PA_VERIFY_WALL_CLOCK", raising=False)
    monkeypatch.setattr(VA, "_is_hub_env", lambda: True)
    assert VA.resolve_wall_clock() == VA.HUB_WALL_CLOCK == 3600.0


def test_wall_clock_local_tier(monkeypatch):
    monkeypatch.delenv("PA_VERIFY_WALL_CLOCK", raising=False)
    monkeypatch.setattr(VA, "_is_hub_env", lambda: False)
    assert VA.resolve_wall_clock() == VA.LOCAL_WALL_CLOCK


# ─── prompt 结构（design：env spec + 验收测试命令 + 全量门命令 + 围栏）──
def test_prompt_builder_sections():
    p = VA.build_verify_prompt(env_spec="NODE_ENV=test",
                               suite_cmds=["npx jest test/a.test.js", "npx jest test/b.test.js"],
                               gate_cmd="npm test", gate_source="profile")
    assert "NODE_ENV=test" in p
    assert "npx jest test/a.test.js" in p and "npx jest test/b.test.js" in p
    assert "npm test" in p and "profile" in p          # 命令 + 来源
    assert "不要修改" in p and "依赖" in p              # 围栏：禁改文件 / 可装依赖
    assert "逐字一致" in p                              # 同一性指令（declared 全等，锚①同源）


def test_prompt_builder_no_gate_cmd():
    """无全量门命令 → 只跑注入测试的显式声明（不编造命令）。"""
    p = VA.build_verify_prompt(env_spec="", suite_cmds=["python3 -m pytest t/x.py"],
                               gate_cmd=None, gate_source="none")
    assert "只跑" in p and "python3 -m pytest t/x.py" in p


# ─── declared 命令机械构造（2.4：prompt 指令与锚①判定同一来源）────────
def test_declared_suite_cmds_for_harnesses():
    assert VA.declared_suite_cmds_for("jest", ["test/a.test.js"]) == ["npx jest test/a.test.js"]
    assert VA.declared_suite_cmds_for("pytest", ["t/x.py"]) == ["python3 -m pytest t/x.py"]
    assert VA.declared_suite_cmds_for("mocha", ["t/x.js"]) == []   # 集外 harness → 空（defensive）
