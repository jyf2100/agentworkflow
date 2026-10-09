# -*- coding: utf-8 -*-
"""test_codex_critic.py — critic 第二臂纯函数单测（派发单 20261009_0910 验证链第 1 步）。

锁定（评估 §2.3/§4.2-1）：
  * synthesize S1 偏序四组合（pass+pass / pass+revise / revise+pass / revise+drop）+ 并集语义；
  * 串行短路（e2=None→single）与降级路径（e2=skip→degraded，verdict 保持 e1）；
  * gate 行解析容错（banner 噪音/散文包裹/非法 verdict 拒收）；
  * arm2 flag 三态读取；
  * codex_review 失败降级（二进制缺位/非零退出重试 1 轮）与成功解析——subprocess 全桩，零网络。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))   # 现役 test_*.py 同款（conftest 注释自证）
import codex_critic as CC


def _e(verdict: str, **kw) -> dict:
    base = {"prd_path": "/tmp/x.md", "project": "p", "verdict": verdict,
            "issues": [], "revisions_needed": [], "summary": "s", "round": 1, "revised": False}
    base.update(kw)
    return base


class TestSynthesizeS1:
    def test_pass_pass_dual(self):
        out = CC.synthesize(_e("pass"), _e("pass"))
        assert out["verdict"] == "pass" and out["dual_channel"] == "dual"
        assert out["channels"] == {"claude": "pass", "codex": "pass"}

    def test_pass_revise_takes_stricter(self):
        out = CC.synthesize(_e("pass"), _e("revise", revisions_needed=["补验收标准"]))
        assert out["verdict"] == "revise" and out["dual_channel"] == "dual"
        assert out["revisions_needed"] == ["补验收标准"]

    def test_revise_pass_takes_stricter_keeps_e1_revisions(self):
        out = CC.synthesize(_e("revise", revisions_needed=["a", "b"]), _e("pass"))
        assert out["verdict"] == "revise"
        assert out["revisions_needed"] == ["a", "b"]

    def test_revise_drop_takes_drop_with_codex_issues_tagged(self):
        out = CC.synthesize(_e("revise"), _e("drop", issues=[{"check": "scope", "severity": "major",
                                                               "reason": "过载"}]))
        assert out["verdict"] == "drop" and out["dual_channel"] == "dual"
        assert any(i["reason"].startswith("[codex]") for i in out["issues"])

    def test_revisions_union_dedup_order(self):
        out = CC.synthesize(_e("revise", revisions_needed=["a", "b"]),
                            _e("revise", revisions_needed=["b", "c"]))
        assert out["revisions_needed"] == ["a", "b", "c"]

    def test_drop_shortcircuit_single(self):
        e1 = _e("drop")
        e1.pop("prd_path")           # 腾空以验证 synthesize 的 setdefault 兜底填充
        e1.pop("project")
        out = CC.synthesize(e1, None, prd_path="/tmp/p.md", project="proj")
        assert out["verdict"] == "drop" and out["dual_channel"] == "single"
        assert out["channels"] == {"claude": "drop", "codex": "skipped"}
        assert out["prd_path"] == "/tmp/p.md" and out["project"] == "proj"

    def test_skip_degraded_keeps_e1_verdict(self):
        out = CC.synthesize(_e("revise"), _e("skip", channel_error="401 unauthorized"))
        assert out["verdict"] == "revise" and out["dual_channel"] == "degraded"
        assert out["channels"]["codex"] == "skip"
        assert out["codex_error"] == "401 unauthorized"

    def test_inputs_not_mutated(self):
        e1, e2 = _e("pass"), _e("drop")
        CC.synthesize(e1, e2)
        assert e1["verdict"] == "pass" and "channels" not in e1
        assert e2["verdict"] == "drop" and "channels" not in e2


class TestParseGateLine:
    def test_plain_json_line(self):
        assert CC._parse_gate_line(json.dumps({"verdict": "pass", "prd_path": "x"}))["verdict"] == "pass"

    def test_banner_and_footer_noise(self):
        out = ("OpenAI Codex v0.142.3\nworking…\n"
               + json.dumps({"verdict": "drop", "summary": "编造断言"}, ensure_ascii=False)
               + "\ntokens used: 42")
        assert CC._parse_gate_line(out)["verdict"] == "drop"

    def test_prose_wrapped_line(self):
        assert CC._parse_gate_line('结论如下：{"verdict":"revise","summary":"x"} 以上。')["verdict"] == "revise"

    def test_invalid_verdict_rejected(self):
        assert CC._parse_gate_line(json.dumps({"verdict": "maybe"})) is None

    def test_garbage_returns_none(self):
        assert CC._parse_gate_line("no json here at all") is None


class TestArm2Flag:
    def test_off_by_default(self):
        assert CC.arm2_mode(env={}) == ""

    def test_codex_value_normalized(self):
        assert CC.arm2_mode(env={"PA_CRITIC_ARM2": "  Codex "}) == "codex"

    def test_other_value_off(self):
        assert CC.arm2_mode(env={"PA_CRITIC_ARM2": "claude2"}) == "claude2"


class TestCodexReview:
    def test_missing_bin_degrades_without_raise(self, monkeypatch):
        monkeypatch.delenv("PA_CODEX_BIN", raising=False)
        monkeypatch.setattr(CC.shutil, "which", lambda _name: None)
        out = CC.codex_review("/tmp/p.md", "/tmp/s.md", {"name": "t"}, log=lambda *_: None)
        assert out["verdict"] == "skip" and "channel_error" in out

    def test_nonzero_exit_retries_once_then_degrades(self, monkeypatch):
        calls = {"n": 0}

        def fake_run(*a, **kw):
            calls["n"] += 1
            return subprocess.CompletedProcess(a[0], 1, stdout="", stderr="stream error: 401 unauthorized")

        monkeypatch.setattr(CC.subprocess, "run", fake_run)
        monkeypatch.setattr(CC, "_resolve_codex_bin", lambda: "/fake/codex")
        out = CC.codex_review("/tmp/p.md", "/tmp/s.md", {"name": "t"}, log=lambda *_: None)
        assert out["verdict"] == "skip" and calls["n"] == 2   # 重试恰好 1 轮
        assert "401" in out["channel_error"]

    def test_success_parses_and_fills_defaults(self, monkeypatch):
        gate = json.dumps({"verdict": "pass", "summary": "ok"}, ensure_ascii=False)
        monkeypatch.setattr(CC.subprocess, "run",
                            lambda *a, **kw: subprocess.CompletedProcess(a[0], 0, stdout=gate, stderr=""))
        monkeypatch.setattr(CC, "_resolve_codex_bin", lambda: "/fake/codex")
        out = CC.codex_review("/tmp/p.md", "/tmp/s.md", {"name": "t"}, log=lambda *_: None)
        assert out["verdict"] == "pass" and out["project"] == "t" and out["prd_path"]
