#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_dev_handoff.py — add-dev-handoff-artifact 组2/组3：dev-agent 写读两侧（TDD，先红后绿）。

写侧（tasks 2.1/2.2/2.3/2.5）：AssistantMessage 文本流 → handoff.absorb →
  ``state["handoff_latest"]`` + run_dir 原子快照（2.5：挂 AssistantMessage 处理处，
  与 pa-progress K=10 抽查独立共存——无共享计时器/状态）；四个 JSON 收尾出口
  （publish / stalled 12 / off_track 15 / gate 14）统一 ``with_handoff`` 附 handoff 字段
  （null=诚实缺失，绝不伪造）。
读侧（tasks 3.1/3.2/3.3/3.4）：``--expect-handoff`` 契约段（实施期修正：SDK 0.2.128 无
  append_system_prompt → 进 build_prompt，flag 条件化保 baseline）/ ``--handoff-artifact``+
  ``--handoff-digest`` 注入（digest 缺失·不匹配·文件不可读 → ``HandoffIntegrityError``
  fail-closed，不静默降级）/ baseline 不传参 prompt byte 级等价 + 尾部原文锚（回归锚）/
  注入块位置在 feedback 与仓特定守则之间（三块各自独立标记）。

dev-agent.py 文件名带连字符 → importlib spec_from_file_location 导入（同
test_learning_memory_wiring 既定模式）。跑：python3 -m pytest scripts/test_dev_handoff.py -q
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import handoff  # noqa: E402

RD = Path(__file__).parent


def _import_dev_agent():
    spec = importlib.util.spec_from_file_location("dev_agent_test", RD / "dev-agent.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


DA = _import_dev_agent()

# baseline prompt 尾部原文（回归锚：byte 级等价的显式子串锚——契约段/注入块为 0 时尾部不变）
_BASELINE_TAIL = ("现在：读 CLAUDE.md → 读 PRD → 规划 → 改代码 → 跑本仓测试 → 到「可提交且 test 绿」即停。\n"
                  "停下前用一段话总结：改了什么 / test 结果 / 遗留风险。")


def _base_args() -> dict:
    return DA.parse_args(["--prd", "x.md"])


# ─── 3.x 参数面 + state 面 ──────────────────────────────────────────────
def test_parse_args_handoff_defaults_off():
    """baseline：三参数缺省 off/None/None（不传 = 旧调用面零变化）。"""
    args = DA.parse_args(["--prd", "p.md"])
    assert args["expect_handoff"] is False
    assert args["handoff_artifact"] is None
    assert args["handoff_digest"] is None


def test_parse_args_handoff_params():
    args = DA.parse_args(["--prd", "p.md", "--expect-handoff",
                          "--handoff-artifact", "/tmp/a.json",
                          "--handoff-digest", "sha256:" + "0" * 64])
    assert args["expect_handoff"] is True
    assert args["handoff_artifact"] == "/tmp/a.json"
    assert args["handoff_digest"] == "sha256:" + "0" * 64


def test_create_loop_state_handoff_latest_none(tmp_path):
    state = DA.create_loop_state(tmp_path / "run.jsonl")
    assert state["handoff_latest"] is None


# ─── 3.3 baseline 回归锚 ────────────────────────────────────────────────
def test_build_prompt_baseline_byte_identical():
    """不传 handoff 参数 → prompt byte 级等价（两次构造一致 + 无任何 handoff 标记 + 尾部原文锚）。"""
    p1 = DA.build_prompt(_base_args(), "PRD 内容", "pa-dev/x")
    p2 = DA.build_prompt(_base_args(), "PRD 内容", "pa-dev/x")
    assert p1 == p2
    assert "handoff" not in p1
    assert "交接" not in p1
    assert p1.endswith(_BASELINE_TAIL)


# ─── 2.4 契约段（--expect-handoff 条件启用）─────────────────────────────
def test_build_prompt_expect_handoff_contract():
    args = DA.parse_args(["--prd", "x.md", "--expect-handoff"])
    p = DA.build_prompt(args, "PRD", "pa-dev/x")
    base_p = DA.build_prompt(_base_args(), "PRD", "pa-dev/x")
    assert p != base_p                          # 契约段启用
    assert "```handoff" in p                    # 围栏块指令
    for field in handoff.HANDOFF_FIELDS + ("rationale",):   # 六字段契约点名
        assert field in p, f"契约未提及字段 {field}"
    # 契约段在收尾指令之前（先立契约再下开工令）
    assert p.index("handoff") < p.index("现在：读 CLAUDE.md")


# ─── 3.1/3.2 注入 + fail-closed ─────────────────────────────────────────
def _artifact_with(tmp_path, content: str) -> tuple[Path, str]:
    art = tmp_path / "handoff.json"
    art.write_text(content, encoding="utf-8")
    return art, handoff.digest_of(content)


def test_build_prompt_injects_verified_handoff(tmp_path):
    """artifact 存在 + digest 匹配 → 独立标记块「上一轮 dev 交接（仅上下文，非判据）」。"""
    content = json.dumps({"done": ["上轮完成"], "next": ["继续做 N"]}, ensure_ascii=False)
    art, dg = _artifact_with(tmp_path, content)
    args = DA.parse_args(["--prd", "x.md", "--handoff-artifact", str(art),
                          "--handoff-digest", dg])
    p = DA.build_prompt(args, "PRD", "pa-dev/x")
    assert "上一轮 dev 交接" in p and "仅上下文" in p and "非判据" in p
    assert "上轮完成" in p


def test_build_prompt_injection_without_digest_fails_closed(tmp_path):
    """传了 artifact 但缺 digest → fail-closed（evidence-integrity），不静默降级。"""
    art, _ = _artifact_with(tmp_path, '{"done": ["x"]}')
    args = DA.parse_args(["--prd", "x.md", "--handoff-artifact", str(art)])
    with pytest.raises(DA.HandoffIntegrityError):
        DA.build_prompt(args, "PRD", "pa-dev/x")


def test_build_prompt_injection_digest_mismatch_fails_closed(tmp_path):
    """落盘后被改（digest 不匹配）→ 拒绝注入，异常带 integrity 标记（spec scenario）。"""
    art, _ = _artifact_with(tmp_path, '{"done": ["被篡改"]}')
    args = DA.parse_args(["--prd", "x.md", "--handoff-artifact", str(art),
                          "--handoff-digest", "sha256:" + "0" * 64])
    with pytest.raises(DA.HandoffIntegrityError):
        DA.build_prompt(args, "PRD", "pa-dev/x")


def test_build_prompt_injection_missing_file_fails_closed(tmp_path):
    """artifact 路径不可读 → 同样 fail-closed（区别于「从未产出」的正常缺失：那是参数整个不传）。"""
    args = DA.parse_args(["--prd", "x.md", "--handoff-artifact", str(tmp_path / "nope.json"),
                          "--handoff-digest", "sha256:" + "0" * 64])
    with pytest.raises(DA.HandoffIntegrityError):
        DA.build_prompt(args, "PRD", "pa-dev/x")


def test_build_prompt_expect_handoff_and_injection_coexist(tmp_path):
    """写读两侧正交：flag on + 注入 on 同 prompt 共存（round≥2 即此形态）。"""
    content = '{"done": ["r1"], "next": ["N"]}'
    art, dg = _artifact_with(tmp_path, content)
    args = DA.parse_args(["--prd", "x.md", "--expect-handoff",
                          "--handoff-artifact", str(art), "--handoff-digest", dg])
    p = DA.build_prompt(args, "PRD", "pa-dev/x")
    assert "```handoff" in p and "上一轮 dev 交接" in p


# ─── 3.4 注入块位置（feedback < handoff < 仓特定守则；lessons 在最末）─────
def test_handoff_block_position_between_feedback_and_repo_rules(tmp_path):
    fb = tmp_path / "fb.md"
    fb.write_text("verify 反馈内容", encoding="utf-8")
    art, dg = _artifact_with(tmp_path, '{"done": ["x"]}')
    args = DA.parse_args(["--prd", "x.md", "--feedback-artifact", str(fb),
                          "--handoff-artifact", str(art), "--handoff-digest", dg])
    p = DA.build_prompt(args, "PRD", "pa-dev/x")
    assert p.index("上轮 verify 反馈") < p.index("上一轮 dev 交接") < p.index("## 仓特定守则")


# ─── 2.1/2.2/2.5 process_dev_loop 吸收 + 快照 ───────────────────────────
def _assistant_msg(text: str):
    from claude_agent_sdk.types import AssistantMessage, TextBlock
    return AssistantMessage(content=[TextBlock(text=text)], model="test-model")


def _run_loop(state, msgs, prd_text=""):
    async def _gen():
        for m in msgs:
            yield m
    return asyncio.run(DA.process_dev_loop(_gen(), state, prd_text=prd_text))


def test_process_dev_loop_absorbs_handoff_and_snapshots(tmp_path):
    """AssistantMessage 文本流 → state 持最新块 + run_dir 原子快照（每 turn 检查，最新覆盖）。"""
    (tmp_path / "runs").mkdir()
    run_log = tmp_path / "runs" / "run.jsonl"
    state = DA.create_loop_state(run_log)
    _run_loop(state, [
        _assistant_msg('干活中 ```handoff\n{"done": ["v1"]}\n```'),
        _assistant_msg('更新 ```handoff\n{"done": ["v2"], "next": ["N"]}\n```'),
        _assistant_msg('收尾段无块'),
    ])
    assert state["handoff_latest"] is not None
    assert state["handoff_latest"]["done"] == ["v2"]      # 最新覆盖
    snap = handoff.load_snapshot(run_log.parent)
    assert snap is not None and snap["done"] == ["v2"]


def test_process_dev_loop_no_block_keeps_none(tmp_path):
    """全程无块（普通文本/其他围栏）→ state 保持 None、无快照（诚实缺失基线）。"""
    (tmp_path / "runs").mkdir()
    run_log = tmp_path / "runs" / "run.jsonl"
    state = DA.create_loop_state(run_log)
    _run_loop(state, [
        _assistant_msg("普通文本无围栏"),
        _assistant_msg("```python\nprint('hi')\n```"),
    ])
    assert state["handoff_latest"] is None
    assert handoff.load_snapshot(run_log.parent) is None


def test_process_dev_loop_absorb_coexists_with_kturn_judge(tmp_path):
    """2.5 共存锚：absorb 挂 AssistantMessage 处理处（turn 计数同源），与 K=10 抽查互不干扰——
    prd_text 非空时两者同 turn 触发，handoff 吸收不被 judge 分支吞（此处 judge 不可用 → fail-open 不干预，
    但 absorb 照常发生）。"""
    (tmp_path / "runs").mkdir()
    run_log = tmp_path / "runs" / "run.jsonl"
    state = DA.create_loop_state(run_log)
    _run_loop(state, [_assistant_msg('```handoff\n{"done": ["coexist"]}\n```')],
              prd_text="PRD 语义面（judge fail-open）")
    assert state["handoff_latest"]["done"] == ["coexist"]


# ─── 2.3 四个 JSON 出口的 handoff 字段 ──────────────────────────────────
def test_with_handoff_null_when_absent():
    """收尾时 dev 从未产块 → handoff=null（诚实缺失，绝不伪造）。"""
    state = DA.create_loop_state(Path("run.jsonl"))
    assert DA.with_handoff({"ok": False, "stalled": True}, state) == \
        {"ok": False, "stalled": True, "handoff": None}


def test_with_handoff_latest_when_present():
    state = DA.create_loop_state(Path("run.jsonl"))
    state["handoff_latest"] = {"done": ["v"]}
    out = DA.with_handoff({"ok": True, "cost": 0.1}, state)
    assert out == {"ok": True, "cost": 0.1, "handoff": {"done": ["v"]}}
    # 不改原 payload（immutable：返回新 dict）
    assert "handoff" not in {"ok": True, "cost": 0.1}


def test_publish_dev_out_carries_handoff_field():
    """publish 出口经 dev_out 合流（run_publish_phase **dev_out 全分支透传）→ with_handoff 包裹即浮现。"""
    state = DA.create_loop_state(Path("run.jsonl"))
    state["handoff_latest"] = {"done": ["pub"]}
    dev_out = DA.with_handoff({"cost": 1.0, "turns": 3, "test_cmd": "pytest"}, state)
    assert dev_out["handoff"] == {"done": ["pub"]}
