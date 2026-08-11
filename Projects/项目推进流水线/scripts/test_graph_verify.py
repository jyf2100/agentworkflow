#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_graph_verify.py — verify PersonaNode + revise 子图测试（任务 3.4）。

验证：
① node_verify 配置（KIND_PERSONA, expose_verdict=True 的 verdict 提取 + feedback_section→feedback）
② node_verify 调用形态 == _pa_verify_round L1420（agent/stage/label per-round/tools=None/prompt 同源）
③ node_dev_redo stub（暂存 verify feedback → _redo_feedback，真实 dev loop 留 task 3.5）
④ install_log ArtifactHandle 传递通道（task 4.1 预留：handle 在 → prompt 追加段落；默认 byte-identical）
⑤ verify 子图拓扑：pass→END / revise→redo→verify round2 / 用满（round2 仍 revise）→ terminal=interrupted_pr
   （enum 终态，非 interrupt，D5 撤 / spec「升人工路径保持机械硬门」）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import graph_pa_nodes as GN
import graph_pa_verify as GV
import graph_pa_contracts as C


# ── node_verify 配置 + verdict 提取 ──────────────────────────────────
def test_verify_node_config():
    assert GN.node_verify._kind is GN.KIND_PERSONA
    assert GN.node_verify._cfg["agent_name"] == "pa-verify"
    assert GN.node_verify._cfg["stage"] == "verify"


def _capture_persona(monkeypatch, captured, payload_override=None):
    import run_daily
    default_payload = {"verdict": "pass", "feedback_section": "", "summary": "ok", "round": 1}
    payload = payload_override or default_payload
    monkeypatch.setattr(run_daily, "run_persona",
                        lambda agent, prompt, stage, label, allowed_tools=None:
                        (captured.update(agent=agent, prompt=prompt, stage=stage,
                                         label=label, tools=allowed_tools) or
                         (dict(payload), {"cost": 0.1, "turns": 3})))


def test_verify_call_shape(monkeypatch):
    captured = {}
    _capture_persona(monkeypatch, captured)
    import run_daily
    prof = {"name": "x", "goal": "g"}
    GN.node_verify({"run_id": "r1", "stamp": "20260811", "config": {},
                    "_prd_path": "state/prd/x/p.md", "_branch": "feat-x", "_base": "main",
                    "_diff_path": "runs/x/20260811_x.r1.diff",
                    "_verify_payload": {"test_rc": 0, "test_log": "t.log"},
                    "_slug": "x", "_prof": prof, "verify_round": 1})
    assert captured["agent"] == "pa-verify"
    assert captured["stage"] == "verify"
    assert captured["label"] == "verify:x:r1"          # per-round label（对齐 _pa_verify_round L1420）
    assert captured["tools"] is None                   # pa-verify 只 Read，不限工具（_pa_verify_round 没传 allowed_tools）
    assert captured["prompt"] == run_daily.verify_prompt(
        "state/prd/x/p.md", "feat-x", "main", "runs/x/20260811_x.r1.diff",
        {"test_rc": 0, "test_log": "t.log"}, 1, prof)


def test_verify_exposes_verdict(monkeypatch):
    """verify 是 expose_verdict=True：payload.verdict 提取到 out['verdict']，feedback_section→feedback。"""
    captured = {}
    _capture_persona(monkeypatch, captured,
                     {"verdict": "revise",
                      "feedback_section": "①定位 X ②原因 Y ③怎么改 Z ④收尾门 全量绿",
                      "summary": "测试红", "round": 1})
    ni = {"run_id": "r9", "stamp": "s", "stage": "verify", "config": {},
          "_prd_path": "p.md", "_branch": "b", "_base": "main", "_diff_path": "d.diff",
          "_verify_payload": {}, "_slug": "x", "_prof": {}, "verify_round": 1}
    out, _ = GN.node_verify.invoke(ni, "prompt")
    assert out["verdict"]["value"] == "revise"         # expose_verdict=True 提取（条件边 route_verify 用）
    assert "定位" in out["verdict"]["feedback"]        # feedback_section → feedback（dev redo 注入）


def test_verify_verdict_mapper():
    """verify verdict_mapper：feedback_section→feedback（供 dev redo 注入）；summary→reason；缺 summary 回退 verdict。"""
    v = GN._verify_verdict_mapper({"verdict": "revise", "feedback_section": "fix X", "summary": "红"})
    assert v == {"value": "revise", "reason": "红", "feedback": "fix X"}
    v = GN._verify_verdict_mapper({"verdict": "pass"})  # 缺 summary → verdict 作 reason（validate 要求非空）
    assert v["reason"] == "pass"
    assert v["feedback"] == ""


# ── node_dev_redo stub（task 3.5 接 DevLoopNode）──────────────────────
def test_dev_redo_stub_config():
    assert GN.node_dev_redo._kind is GN.KIND_MECHANICAL
    assert GN.node_dev_redo._cfg["stage"] == "dispatch"   # dev loop 属 dispatch（对齐 make_devloop_node）


def test_dev_redo_stub_carries_feedback():
    """node_dev_redo stub：暂存 verify feedback_section → _redo_feedback（task 3.5 dev 注入用）。"""
    update = GN.node_dev_redo({"run_id": "r", "config": {}, "_project": "x",
                               "_verify_result": {"feedback_section": "fix X"}})
    assert update["_redo_feedback"] == "fix X"
    assert update["obs_log"][0]["redo_stub"] is True
    assert update["obs_log"][0]["has_feedback"] is True


def test_dev_redo_stub_empty_feedback():
    """verify 无 feedback_section（pass 后不应触达，但防御）→ _redo_feedback 空串，redo_stub 仍标记。"""
    update = GN.node_dev_redo({"run_id": "r", "config": {}, "_verify_result": {}})
    assert update["_redo_feedback"] == ""
    assert update["obs_log"][0]["has_feedback"] is False


# ── install_log ArtifactHandle 传递通道（task 4.1 预留）────────────────
def test_install_log_none_byte_identical(monkeypatch):
    """无 _install_log → prompt == verify_prompt 原样（byte-identical，task 3.10 shadow parity 基线）。"""
    captured = {}
    _capture_persona(monkeypatch, captured)
    import run_daily
    prof = {"name": "x"}
    GN.node_verify({"run_id": "r", "stamp": "s", "config": {},
                    "_prd_path": "p.md", "_branch": "b", "_base": "main", "_diff_path": "d.diff",
                    "_verify_payload": {"test_rc": 0}, "_slug": "x", "_prof": prof, "verify_round": 1})
    assert captured["prompt"] == run_daily.verify_prompt(
        "p.md", "b", "main", "d.diff", {"test_rc": 0}, 1, prof)
    assert "install_log" not in captured["prompt"]


def test_install_log_handle_appends_segment(monkeypatch):
    """_install_log ArtifactHandle 在 → build_prompt 追加 [install_log artifact] 段落（传递通道）。"""
    captured = {}
    _capture_persona(monkeypatch, captured)
    base = {"run_id": "r", "stamp": "s", "config": {},
            "_prd_path": "p.md", "_branch": "b", "_base": "main", "_diff_path": "d.diff",
            "_verify_payload": {}, "_slug": "x", "_prof": {}, "verify_round": 1}
    s2 = dict(base)
    s2["_install_log"] = {"kind": "install_log", "store": "tmp",
                          "rel_path": "runs/x/install.log", "digest": "sha256:abc", "must_exist": True}
    GN.node_verify(s2)
    assert "[install_log artifact]" in captured["prompt"]
    assert "runs/x/install.log" in captured["prompt"]
    assert "tmp" in captured["prompt"]                    # store 标注（task 4.1 补 resolve 绝对）


# ── verify 子图拓扑（revise 回环 + round 上限 + interrupted_pr 终态）──────
def _persona_seq(monkeypatch, verdicts):
    """verify 调用按序消费 verdicts；redo（dev_redo stub）机械执行（不触达 run_persona）。"""
    import run_daily
    it = iter(verdicts)

    def fake(agent, prompt, stage, label, allowed_tools=None):
        if agent == "pa-verify":
            v = next(it)
            return ({"verdict": v, "feedback_section": "fix" if v == "revise" else "",
                     "summary": v, "round": 1}, {"cost": 0.1, "turns": 3})
        return ({"prds": []}, {"cost": 0.2, "turns": 5})   # 非 pa-verify（不应触达）
    monkeypatch.setattr(run_daily, "run_persona", fake)


_BASE = {"run_id": "r", "stamp": "s", "config": {},
         "_prd_path": "p.md", "_branch": "b", "_base": "main", "_diff_path": "d.diff",
         "_verify_payload": {}, "_slug": "x", "_prof": {}}


def test_subgraph_pass_terminates(monkeypatch):
    _persona_seq(monkeypatch, ["pass"])
    sg = GV.build_verify_subgraph()
    result = sg.invoke(dict(_BASE))
    assert result["_verify_verdict"] == "pass"
    assert not result.get("terminal")                  # pass 不 mark terminal（成功收尾，上层 dispatch 开 PR）
    assert len(result["entries"]) == 1


def test_subgraph_revise_loop_round2(monkeypatch):
    """verify revise → redo → verify round2 pass（1 次 redo 回环）。"""
    _persona_seq(monkeypatch, ["revise", "pass"])
    sg = GV.build_verify_subgraph()
    result = sg.invoke(dict(_BASE))
    assert result["verify_round"] == 2                 # redo bump 了 round
    assert result["_verify_verdict"] == "pass"
    assert len(result["entries"]) == 2                 # round1 revise entry + round2 pass entry


def test_subgraph_revise_exhausted_interrupted(monkeypatch):
    """round1 revise → redo → round2 仍 revise，verify_round=2=MAX → terminal=interrupted_pr（enum 终态，非 interrupt）。"""
    _persona_seq(monkeypatch, ["revise", "revise"])
    sg = GV.build_verify_subgraph()
    result = sg.invoke(dict(_BASE))
    assert result["verify_round"] == 2
    assert result["_verify_verdict"] == "revise"       # round2 仍 revise
    assert result["terminal"] == C.STATUS_INTERRUPTED  # 用满 → interrupted_pr（机械硬门升人工，D5 撤）
    assert result["terminal"] == "interrupted_pr"      # enum 字面值（对齐 stage_dispatch L2510-2512）
    assert len(result["entries"]) == 2


def test_subgraph_redo_carries_feedback(monkeypatch):
    """redo stub 暂存 round1 revise 的 feedback_section → _redo_feedback（task 3.5 dev 注入用）。"""
    _persona_seq(monkeypatch, ["revise", "pass"])
    sg = GV.build_verify_subgraph()
    result = sg.invoke(dict(_BASE))
    assert result.get("_redo_feedback") == "fix"       # round1 revise 的 feedback 被 redo 暂存
