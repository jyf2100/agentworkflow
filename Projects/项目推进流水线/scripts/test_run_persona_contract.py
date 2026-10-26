#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_run_persona_contract.py — run_persona 语义契约接线单测（change 2026-07-28 Phase 2）。

验证 run_persona 在语法 parse 成功后接入 stage_contracts：
    - 契约违反（critic 缺 verdict）+ 有预算 → 带诊断重试 → 第二次合规则返回合规 payload
    - 契约违反 + 预算用尽 → fail-open 降级返回现状 payload（不 raise，不改 stage 终态）

mock subprocess.run 返回构造的两层 JSON 信封（绕过真实 claude CLI）；AAA 结构。
add-per-agent-model-routing：twin 镜像守（run_daily 端 env→--model，与 persona_call 行为对齐）。
跑：python3 -m pytest scripts/test_run_persona_contract.py -q
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import run_daily  # noqa: E402


def _envelope(payload: dict) -> str:
    """构造 claude --output-format json 的两层信封：outer.result = json(inner payload)。"""
    return json.dumps({"result": json.dumps(payload, ensure_ascii=False), "total_cost_usd": 0,
                       "num_turns": 1, "is_error": False}, ensure_ascii=False)


def _mock_run(stdout_seq):
    """假 subprocess.run：按序吐 stdout（pop 模拟多次调用，耗尽则返空信封）。"""
    seq = list(stdout_seq)
    def _fake(*a, **k):
        return SimpleNamespace(returncode=0, stdout=seq.pop(0) if seq else _envelope({}), stderr="")
    return _fake


def test_run_persona_contract_violation_retries_then_succeeds(monkeypatch):
    """critic 第一次缺 verdict（契约违反）→ 带诊断重试；第二次合规 → 返回合规 payload。"""
    monkeypatch.setattr(run_daily.subprocess, "run",
                        _mock_run([_envelope({"prd_path": "a.md", "project": "p1"}),          # 缺 verdict
                                   _envelope({"verdict": "pass", "prd_path": "a.md"})]))        # 合规
    payload, _meta = run_daily.run_persona("pa-prd-critic", "prompt", "critic", "t1")
    assert payload["verdict"] == "pass"                       # 重试后拿到合规 payload
    assert payload["prd_path"] == "a.md"


def test_run_persona_contract_violation_budget_exhausted_failopen(monkeypatch):
    """critic 连续缺 verdict → 重试预算用尽 → fail-open 降级返回现状 payload，不 raise。"""
    bad = _envelope({"prd_path": "a.md", "project": "p1"})     # 持续缺 verdict
    monkeypatch.setattr(run_daily.subprocess, "run", _mock_run([bad, bad]))
    payload, _meta = run_daily.run_persona("pa-prd-critic", "prompt", "critic", "t2")
    assert payload["prd_path"] == "a.md"                      # 降级返回现状 payload，未 raise


# ─── exit_diag 镜像守（2026-10-23 hub 首跑 #3 实证修复；与 test_persona_call 同形态）──
def test_run_persona_nonzero_exit_includes_stdout_envelope(monkeypatch):
    """退出 1 + 信封 is_error（如 Not logged in 配置错）→ RuntimeError（permanent 臂，
    不退避重试烧容器——本体论审核 B-MEDIUM 2026-10-23：与 returncode==0 的 is_error 臂同语义）。"""
    env = json.dumps({"is_error": True, "result": "Not logged in · Please run /login"})
    monkeypatch.setattr(run_daily.subprocess, "run",
                        lambda *a, **k: SimpleNamespace(returncode=1, stdout=env, stderr=""))
    with pytest.raises(RuntimeError) as ei:
        run_daily.run_persona("pa-prd-critic", "prompt", "critic", "t-exit")
    assert "Not logged in" in str(ei.value)
    assert not isinstance(ei.value, run_daily.TransientPersonaError)   # 不是瞬态


def test_run_persona_nonzero_exit_without_envelope_stays_transient(monkeypatch):
    """退出 1 + 无信封 stdout（被杀/OOM 等瞬态形态）→ TransientPersonaError（重试合理）。"""
    monkeypatch.setattr(run_daily.subprocess, "run",
                        lambda *a, **k: SimpleNamespace(returncode=137, stdout="", stderr=""))
    with pytest.raises(run_daily.TransientPersonaError) as ei:
        run_daily.run_persona("pa-prd-critic", "prompt", "critic", "t-exit2")
    assert "137" in str(ei.value)


# ─── exit_diag 双实现直比守（本体论审核 B-LOW 2026-10-23：一个测试锁死全部形态不漂移）──
def test_exit_diag_mirror_identical_across_implementations():
    """同输入同输出：run_daily.exit_diag 与 persona_call.exit_diag 全形态直比
    （镜像惯例此前只靠各自测试同形态锚定，直比锁死后任一侧单独改动立即红）。"""
    import persona_call  # noqa: E402  （本地延迟 import：与 test_persona_call 同路径语义）
    cases = [
        (1, json.dumps({"is_error": True, "result": "Not logged in · Please run /login"}), ""),
        (137, "", ""),
        (1, "not json at all", "some stderr tail"),
        (1, '{"is_error": false, "result": "ok"}', "stderr\nlines\ntail"),
        (2, "", "短 stderr"),
    ]
    for rc, out, err in cases:
        p1 = SimpleNamespace(returncode=rc, stdout=out, stderr=err)
        p2 = SimpleNamespace(returncode=rc, stdout=out, stderr=err)
        assert run_daily.exit_diag("lbl", p1) == persona_call.exit_diag("lbl", p2), (rc, out[:30])


# ─── per-agent model routing：twin 镜像守（add-per-agent-model-routing）─────────
# review HIGH（3 人共识）：persona_call 端有 env 注入测试，run_daily 端（7 persona）零守护。
# 此处补 twin 守护——捕获 run_persona 拼的 cmd，断言 equals 形式注入 / 不设则无。
# 优先级：env（canary）> config/model-routing.json（主配置）> roc 默认。
@pytest.fixture(autouse=True)
def _isolate_model_routing_file(tmp_path, monkeypatch):
    """隔离文件层：默认指向不存在的临时文件 → resolve 返回 None（env 测试不受真实 config 干扰）。"""
    monkeypatch.setattr(run_daily.model_routing, "_DEFAULT_PATH", tmp_path / "isolated-empty.json")


def _cmd_capturing_run(captured: dict):
    """记下每次 subprocess.run 收到的 cmd（位置参 args[0]），再返空信封。"""
    def _fake(*a, **k):
        captured["cmds"].append(list(a[0]))
        return SimpleNamespace(returncode=0, stdout=_envelope({"ok": True}), stderr="")
    return _fake


def test_run_persona_model_env_injects_equals_form(monkeypatch):
    """设 PA_PERSONA_MODEL_PA_RADAR=sonnet → run_daily 拼的 cmd 含 '--model=sonnet'（twin 镜像守）。"""
    monkeypatch.setenv("PA_PERSONA_MODEL_PA_RADAR", "sonnet")
    captured: dict = {"cmds": []}
    monkeypatch.setattr(run_daily.subprocess, "run", _cmd_capturing_run(captured))
    run_daily.run_persona("pa-radar", "P", "radar", "t-radar")
    assert any(x == "--model=sonnet" for x in captured["cmds"][0])


def test_run_persona_no_model_env_omits_flag(monkeypatch):
    """不设任何 PA_PERSONA_MODEL_* → run_daily 拼的 cmd 无任何 --model*（baseline byte-identical）。"""
    for k in [k for k in os.environ if k.startswith("PA_PERSONA_MODEL_")]:
        monkeypatch.delenv(k, raising=False)
    captured: dict = {"cmds": []}
    monkeypatch.setattr(run_daily.subprocess, "run", _cmd_capturing_run(captured))
    run_daily.run_persona("pa-radar", "P", "radar", "t-radar2")
    assert not any(x.startswith("--model") for x in captured["cmds"][0])


def test_run_persona_model_file_fallback_twin(monkeypatch, tmp_path):
    """env 未设 + config 配 pa-radar=haiku → cmd 含 '--model=haiku'（文件兜底，twin 镜像 persona_call）。"""
    for k in [k for k in os.environ if k.startswith("PA_PERSONA_MODEL_")]:
        monkeypatch.delenv(k, raising=False)
    cfg = tmp_path / "model-routing.json"
    cfg.write_text(json.dumps({"pa-radar": "haiku"}), encoding="utf-8")
    monkeypatch.setattr(run_daily.model_routing, "_DEFAULT_PATH", cfg)
    captured: dict = {"cmds": []}
    monkeypatch.setattr(run_daily.subprocess, "run", _cmd_capturing_run(captured))
    run_daily.run_persona("pa-radar", "P", "radar", "t-radar3")
    assert any(x == "--model=haiku" for x in captured["cmds"][0])


def test_run_persona_model_env_overrides_file_twin(monkeypatch, tmp_path):
    """env + 文件都配 → env 胜（canary 覆盖主配置，twin 镜像）。"""
    monkeypatch.setenv("PA_PERSONA_MODEL_PA_RADAR", "opus")
    cfg = tmp_path / "model-routing.json"
    cfg.write_text(json.dumps({"pa-radar": "haiku"}), encoding="utf-8")
    monkeypatch.setattr(run_daily.model_routing, "_DEFAULT_PATH", cfg)
    captured: dict = {"cmds": []}
    monkeypatch.setattr(run_daily.subprocess, "run", _cmd_capturing_run(captured))
    run_daily.run_persona("pa-radar", "P", "radar", "t-radar4")
    assert any(x == "--model=opus" for x in captured["cmds"][0])
    assert not any(x == "--model=haiku" for x in captured["cmds"][0])


# ─── ponytail-ab：PA_PONYTAIL 开关守护（20260917-D）────────────────────────────
# off → cmd 不含 --plugin-dir（baseline 零变更）；on → 含且后跟正确 plugin 目录。
@pytest.mark.parametrize("truthy", ["1", "true", "yes", "on", "TRUE", "On"])
def test_run_persona_ponytail_on_injects_plugin_dir(monkeypatch, truthy):
    """设 PA_PONYTAIL 真值（大小写不敏感）→ cmd 含 '--plugin-dir' + 默认目录。"""
    monkeypatch.setenv("PA_PONYTAIL", truthy)
    captured: dict = {"cmds": []}
    monkeypatch.setattr(run_daily.subprocess, "run", _cmd_capturing_run(captured))
    run_daily.run_persona("pa-radar", "P", "radar", "t-pony-on")
    cmd = captured["cmds"][0]
    assert "--plugin-dir" in cmd
    assert cmd[cmd.index("--plugin-dir") + 1] == "/mnt/disk01/workspaces/worksummary/pa-ponytail/ponytail"


def test_run_persona_ponytail_off_omits_flag(monkeypatch):
    """不设 PA_PONYTAIL（或非真值）→ cmd 不含 --plugin-dir（off=零行为变化）。"""
    for k in [k for k in os.environ if k.startswith("PA_PONYTAIL")]:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("PA_PONYTAIL", "0")
    captured: dict = {"cmds": []}
    monkeypatch.setattr(run_daily.subprocess, "run", _cmd_capturing_run(captured))
    run_daily.run_persona("pa-radar", "P", "radar", "t-pony-off")
    assert "--plugin-dir" not in captured["cmds"][0]
