#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_persona_call.py — persona_call.run_persona_subproc 单测（TDD，in-loop-semantic-checkpoint §1）。

覆盖从 run_daily.run_persona 抽出的零依赖共享模块：
    - 两层 JSON 解析（outer 信封 + inner payload）
    - 容错 _extract_first_json（散文/markdown 前后缀）
    - 重试 cap（首轮非 JSON 加强 JSON-only 重试）
    - 契约校验 fail-open（error→重试/降级，warning→不改行为）
    - 超时 / 非零退出 / is_error / 两轮失败 → raise RuntimeError
    - 反 invariant：import persona_call 不连带加载 claude_agent_sdk（守 cron 不崩）

纯函数测（monkeypatch subprocess.run），无 SDK。仿 test_bash_allowlist.py preamble。
跑：python3 -m pytest scripts/test_persona_call.py -q
AAA 结构（Arrange / Act / Assert）。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import persona_call  # noqa: E402
import stage_contracts  # noqa: E402


# ─── fake subprocess.run 工具 ────────────────────────────────────────
class _FakeProc:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _fake_runner(outputs):
    """按序消费 outputs（str→stdout / CompletedProcess / Exception）返回可调用。"""
    it = iter(outputs)

    def _run(*a, **k):
        v = next(it)
        if isinstance(v, Exception):
            raise v
        if isinstance(v, _FakeProc):
            return v
        return _FakeProc(stdout=v)
    return _run


# ─── 两层 JSON 解析 ──────────────────────────────────────────────────
def test_two_layer_json_parse_extracts_payload_and_meta(monkeypatch):
    """outer 信封 json.loads → inner result 再 json.loads 得 payload；meta 抽 cost/turns/session_id。"""
    # Arrange
    payload = {"verdict": "on_track", "covered": ["A1"], "summary": "ok"}
    outer = {"result": json.dumps(payload), "is_error": False,
             "total_cost_usd": 0.012, "num_turns": 3, "session_id": "sess-1",
             "duration_ms": 5000, "modelUsage": {}}
    monkeypatch.setattr(persona_call.subprocess, "run",
                        _fake_runner([json.dumps(outer)]))
    # Act
    got, meta = persona_call.run_persona_subproc(
        "claude", "pa-progress", "PROMPT", max_turns=15, timeout=120, stage="progress")
    # Assert
    assert got == payload
    assert meta["cost"] == 0.012
    assert meta["turns"] == 3
    assert meta["session_id"] == "sess-1"


def test_extract_first_json_tolerates_prose_around_object(monkeypatch):
    """inner result 含散文/markdown 前后缀 → _extract_first_json brace-matching 抽取。"""
    # Arrange：result 前后有散文，含字面量花括号的字符串值不应破坏配平
    inner = '我分析了一下。\n```json\n{"verdict": "off_track", "redirect_hint": "见 {验收标准} 节"}\n```\n以上。'
    outer = {"result": inner, "is_error": False}
    monkeypatch.setattr(persona_call.subprocess, "run",
                        _fake_runner([json.dumps(outer)]))
    # Act
    got, _ = persona_call.run_persona_subproc(
        "claude", "pa-progress", "P", max_turns=15, timeout=120, stage="progress")
    # Assert
    assert got["verdict"] == "off_track"
    assert got["redirect_hint"] == "见 {验收标准} 节"   # 字面量花括号未破坏 brace-matching


# ─── 重试 cap ────────────────────────────────────────────────────────
def test_retry_on_non_json_then_succeed(monkeypatch):
    """首轮 result 非 JSON → 加强 JSON-only 重试一轮 → 次轮合法 → 成功（subprocess 调 2 次）。"""
    # Arrange
    calls = {"n": 0}

    def _run(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeProc(stdout=json.dumps({"result": "这不是 JSON 散文", "is_error": False}))
        return _FakeProc(stdout=json.dumps({"result": '{"verdict":"on_track"}', "is_error": False}))
    monkeypatch.setattr(persona_call.subprocess, "run", _run)
    # Act
    got, _ = persona_call.run_persona_subproc(
        "claude", "pa-progress", "P", max_turns=15, timeout=120, retry_cap=2)
    # Assert
    assert got["verdict"] == "on_track"
    assert calls["n"] == 2


def test_two_rounds_non_json_raises(monkeypatch):
    """两轮均非合法 JSON → raise RuntimeError（带诊断）。"""
    monkeypatch.setattr(persona_call.subprocess, "run", _fake_runner([
        json.dumps({"result": "散文1", "is_error": False}),
        json.dumps({"result": "散文2", "is_error": False}),
    ]))
    with pytest.raises(RuntimeError):
        persona_call.run_persona_subproc(
            "claude", "pa-progress", "P", max_turns=15, timeout=120, retry_cap=2)


# ─── 契约校验 fail-open ──────────────────────────────────────────────
def test_contract_violation_retries_then_succeeds(monkeypatch):
    """progress verdict 越界（非受控值）→ 契约 error 触发重试 → 次轮合法。"""
    calls = {"n": 0}

    def _run(*a, **k):
        calls["n"] += 1
        v = "maybe" if calls["n"] == 1 else "on_track"
        return _FakeProc(stdout=json.dumps({"result": f'{{"verdict":"{v}"}}', "is_error": False}))
    monkeypatch.setattr(persona_call.subprocess, "run", _run)
    got, _ = persona_call.run_persona_subproc(
        "claude", "pa-progress", "P", max_turns=15, timeout=120, stage="progress", retry_cap=2)
    assert got["verdict"] == "on_track"
    assert calls["n"] == 2


def test_contract_violation_failopen_when_no_retry_budget(monkeypatch):
    """retry_cap=1 + verdict 越界 → 无重试预算 → fail-open 返回现状 payload（不 raise）。"""
    monkeypatch.setattr(persona_call.subprocess, "run", _fake_runner([
        json.dumps({"result": '{"verdict":"maybe"}', "is_error": False})]))
    got, _ = persona_call.run_persona_subproc(
        "claude", "pa-progress", "P", max_turns=15, timeout=120, stage="progress", retry_cap=1)
    assert got["verdict"] == "maybe"   # fail-open 降级返回现状


# ─── raise 路径 ──────────────────────────────────────────────────────
def test_timeout_raises(monkeypatch):
    monkeypatch.setattr(persona_call.subprocess, "run",
                        _fake_runner([subprocess.TimeoutExpired(cmd=["claude"], timeout=120)]))
    with pytest.raises(RuntimeError):
        persona_call.run_persona_subproc("claude", "pa-progress", "P", max_turns=15, timeout=120)


def test_nonzero_exit_raises(monkeypatch):
    monkeypatch.setattr(persona_call.subprocess, "run",
                        _fake_runner([_FakeProc(stdout="", stderr="boom", returncode=2)]))
    with pytest.raises(RuntimeError):
        persona_call.run_persona_subproc("claude", "pa-progress", "P", max_turns=15, timeout=120)


def test_is_error_envelope_raises(monkeypatch):
    monkeypatch.setattr(persona_call.subprocess, "run", _fake_runner([
        json.dumps({"result": "err", "is_error": True})]))
    with pytest.raises(RuntimeError):
        persona_call.run_persona_subproc("claude", "pa-progress", "P", max_turns=15, timeout=120)


def test_outer_non_json_raises(monkeypatch):
    monkeypatch.setattr(persona_call.subprocess, "run",
                        _fake_runner(["这不是信封 JSON"]))
    with pytest.raises(RuntimeError):
        persona_call.run_persona_subproc("claude", "pa-progress", "P", max_turns=15, timeout=120)


# ─── T11：反 invariant（import persona_call 不连带加载 SDK）─────────────
def test_persona_call_does_not_load_sdk():
    """persona_call 是零依赖模块：import 后 sys.modules 无 claude_agent_sdk。

    守 test_dev_agent_source.py 的反 invariant 扩展——persona_call 可被 cron 的裸
    /usr/bin/python3（无 sdk）进程 import（如未来 run_daily 复用它）。"""
    scripts_dir = str(Path(__file__).resolve().parent)
    result = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0,'.'); import persona_call; "
         "print('claude_agent_sdk' in sys.modules)"],
        capture_output=True, text=True, timeout=30, check=True, cwd=scripts_dir,
    )
    assert result.stdout.strip() == "False", (
        "persona_call 连带加载了 claude_agent_sdk——非零依赖模块。\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}")


# ─── progress 契约注册 ───────────────────────────────────────────────
def test_progress_contract_registered():
    """CONTRACTS['progress'] 已注册，verdict ∈{on_track,off_track} 合法 / 越界 error。"""
    c = stage_contracts.get_contract("progress")
    assert c is not None
    assert stage_contracts.validate_stage("progress", {"verdict": "on_track"}) == []
    assert stage_contracts.validate_stage("progress", {"verdict": "off_track"}) == []
    issues = stage_contracts.validate_stage("progress", {"verdict": "maybe"})
    assert any(i.severity == "error" and i.field == "verdict" for i in issues)
