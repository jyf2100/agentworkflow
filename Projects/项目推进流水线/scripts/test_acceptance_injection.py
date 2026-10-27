#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_acceptance_injection.py — agent-verify-injection tasks 1.6/1.7：dev-agent 注入面（TDD 先红）。

参数面：``--acceptance-tests``/``--acceptance-digest``（bundle path + sha256 digest，同
``--handoff-artifact`` 模式；baseline 不传 → 空 block，prompt byte-identical）。
注入面：合法 bundle → 渲染逐文件块（path + content + criterion 标注保留指令）；不可读 /
digest 缺失·不匹配 / bundle 畸形 → ``AcceptanceIntegrityError`` fail-closed（不静默降级成
无套件运行——那会退回 dev 自测自证）。bundle 用控制面 ``acceptance_tests.store_suite`` 真
产（值域与 dev-agent 读侧经 ``handoff.digest_of`` 同一 sha256:<hex>，两侧互通被测试钉死）。

1.7 新鲜度：注入件进 worktree 后被编辑走**既有**候选写 stale 化（evidence.py 写事件标记
模型是任意候选写 → mark_stale，不区分文件）——零新增判定代码，测试钉住契约。
跑：python3 -m pytest scripts/test_acceptance_injection.py -q
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import acceptance_tests as AT   # noqa: E402
from evidence import TestEvidence, evaluate_gate, mark_stale, GATE_STALE   # noqa: E402

RD = Path(__file__).parent


def _import_dev_agent():
    spec = importlib.util.spec_from_file_location("dev_agent_acc_test", RD / "dev-agent.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


DA = _import_dev_agent()

_BASELINE_TAIL = ("现在：读 CLAUDE.md → 读 PRD → 规划 → 改代码 → 跑本仓测试 → 到「可提交且 test 绿」即停。\n"
                  "停下前用一段话总结：改了什么 / test 结果 / 遗留风险。")

_FILES = {"test/prd_ac1.test.js": "// criterion: proj-x:ac1\ntest('ac1', () => {});"}


def _real_bundle(tmp_path):
    """用控制面 store_suite 真产 bundle → (path, digest)（两侧 digest 值域互通的实证）。"""
    ref = AT.store_suite(tmp_path, _FILES, prd_criteria_ids=["proj-x:ac1", "proj-x:ac2"],
                         suite_criteria_ids=["proj-x:ac1"], harness="jest")
    return tmp_path / ref["path"], ref["digest"]


def _prompt(tmp_path, monkeypatch, extra):
    bp, dg = _real_bundle(tmp_path)
    args = DA.parse_args(["--prd", "x.md", "--acceptance-tests", str(bp),
                          "--acceptance-digest", dg] + extra[:0])
    monkeypatch.chdir(tmp_path)
    return DA.build_prompt(args, "# PRD", "pa-dev-x")


# ─── 参数面 ────────────────────────────────────────────────────────────
def test_parse_args_acceptance_defaults_off():
    """baseline：两参数缺省 None/None（不传 = 旧调用面零变化）。"""
    args = DA.parse_args(["--prd", "p.md"])
    assert args["acceptance_tests"] is None and args["acceptance_digest"] is None


def test_parse_args_acceptance_flags():
    """--acceptance-tests/--acceptance-digest 各吃一个值 token。"""
    args = DA.parse_args(["--prd", "p.md", "--acceptance-tests", "/tmp/b.json",
                          "--acceptance-digest", "sha256:abc"])
    assert args["acceptance_tests"] == "/tmp/b.json"
    assert args["acceptance_digest"] == "sha256:abc"


# ─── 注入面 ────────────────────────────────────────────────────────────
def test_baseline_prompt_unchanged(tmp_path, monkeypatch):
    """不传参 → 无验收套件块 + 尾部原文锚不变（回归锚）。"""
    monkeypatch.chdir(tmp_path)
    p = DA.build_prompt(DA.parse_args(["--prd", "x.md"]), "# PRD", "pa-dev-x")
    assert "验收测试套件" not in p
    assert p.endswith(_BASELINE_TAIL)


def test_injection_renders_suite(tmp_path, monkeypatch):
    """合法 bundle → prompt 含块标题 + 每文件 path 与 content（含 criterion 标注）。"""
    p = _prompt(tmp_path, monkeypatch, [])
    assert "验收测试套件" in p
    assert "test/prd_ac1.test.js" in p
    assert "criterion: proj-x:ac1" in p          # 标注原样透传（traceability 锚）
    assert p.endswith(_BASELINE_TAIL)            # 只插入不改尾


def test_digest_missing_fail_closed(tmp_path, monkeypatch):
    """传 bundle 不传 digest → AcceptanceIntegrityError（不静默注入未校验内容）。"""
    bp, _ = _real_bundle(tmp_path)
    monkeypatch.chdir(tmp_path)
    args = DA.parse_args(["--prd", "x.md", "--acceptance-tests", str(bp)])
    with pytest.raises(DA.AcceptanceIntegrityError):
        DA.build_prompt(args, "# PRD", "pa-dev-x")


def test_digest_mismatch_fail_closed(tmp_path, monkeypatch):
    """落盘后被改（digest 不匹配）→ 拒绝注入（spec scenario：fail-closed）。"""
    bp, dg = _real_bundle(tmp_path)
    bp.write_text('{"files": [{"path": "x", "content": "tampered"}]}', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    args = DA.parse_args(["--prd", "x.md", "--acceptance-tests", str(bp),
                          "--acceptance-digest", dg])
    with pytest.raises(DA.AcceptanceIntegrityError):
        DA.build_prompt(args, "# PRD", "pa-dev-x")


def test_unreadable_bundle_fail_closed(tmp_path, monkeypatch):
    """path 不存在 → raise（readable=False 进 detail）。"""
    monkeypatch.chdir(tmp_path)
    args = DA.parse_args(["--prd", "x.md", "--acceptance-tests", str(tmp_path / "gone.json"),
                          "--acceptance-digest", "sha256:x"])
    with pytest.raises(DA.AcceptanceIntegrityError):
        DA.build_prompt(args, "# PRD", "pa-dev-x")


def test_malformed_bundle_fail_closed(tmp_path, monkeypatch):
    """digest 自洽但内容非 JSON / files 空 → raise（畸形不降级）。"""
    bad = tmp_path / "bad.json"
    import handoff as H
    bad.write_text("not-json", encoding="utf-8")
    args = DA.parse_args(["--prd", "x.md", "--acceptance-tests", str(bad),
                          "--acceptance-digest", H.digest_of("not-json")])
    monkeypatch.chdir(tmp_path)
    with pytest.raises(DA.AcceptanceIntegrityError):
        DA.build_prompt(args, "# PRD", "pa-dev-x")
    empty = tmp_path / "empty.json"
    empty.write_text('{"files": []}', encoding="utf-8")
    args2 = DA.parse_args(["--prd", "x.md", "--acceptance-tests", str(empty),
                           "--acceptance-digest", H.digest_of('{"files": []}')])
    with pytest.raises(DA.AcceptanceIntegrityError):
        DA.build_prompt(args2, "# PRD", "pa-dev-x")


# ─── 1.7 新鲜度门（既有机制覆盖，契约钉死） ────────────────────────────
def test_edited_injection_stales_evidence():
    """注入件被 Edit/Write（任意候选写）→ 绿证据 stale → 发布门拦（test_stale）。

    evidence.py 写事件标记模型对候选写不分文件——注入测试文件进 worktree 后被编辑
    天然命中 mark_stale，零新增判定。"""
    ev = TestEvidence(command="npx jest test/prd_ac1.test.js", exit_code=0,
                      completed_at="2026-10-27T00:00:00", fresh=True)
    verdict, _ = evaluate_gate(mark_stale(ev))    # dev 编辑注入件后再想发布
    assert verdict == GATE_STALE
