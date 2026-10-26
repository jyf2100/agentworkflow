#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_recovery_handoff.py — add-dev-handoff-artifact 组5：recovery_context 并入（TDD，先红后绿）。

覆盖（tasks 5.1/5.2）：
    - journal 有同 PRD 的 ``iteration_handoff`` 引用且 digest 过 → ``decisions`` 取自述
      （``tried_and_failed`` + ``rationale``）、``next_action`` 取 ``next``（delta scenario
      「Handoff supplies decisions and next action」，handoff 自述优先于机械派生）。
    - never-gates 机械锚（delta scenario「Handoff content never gates verdicts」）：handoff
      并入时 status/suggested_next_step 等机械字段零变化；RecoveryContext 无任何 verdict/gate/
      retry 决策字段；handoff 里的恶意/未知键不渗入任何输出字段。
    - 无 handoff → build_recovery_context 输出与现状 byte 级等价（回归锚，delta「No handoff
      falls back」：全字段 asdict 硬快照，新字段空默认）。
    - 引用在但 content 缺（caller 读不到）/ digest 不过（落盘后被改）→ 不并入，等同缺失回退
      （evidence-integrity：被改内容绝不进恢复上下文；注入侧 dev-agent exit 10 才是显式报错面）。
    - 异 PRD 的 handoff 引用不并入（同 PRD 边界）。

纯函数面（task 5.3）：``handoff_content`` 入参由调用方读 artifact，本模块零 IO/零 SDK。
dispatch_one 内的接线由组4 落（``_parent_handoff_ref`` 注入面）；recovery driver 消费由
reconcile.recover_iteration 透传（context 字段就绪即可）。跑：
python3 -m pytest scripts/test_recovery_handoff.py -q
"""
from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent))
import handoff  # noqa: E402
import recovery_context as RC  # noqa: E402


def _ev(etype: str, prd_id: str, payload: dict | None = None) -> SimpleNamespace:
    """journal 事件鸭子类型（build_recovery_context 只读 event_type/payload/prd_id）。"""
    return SimpleNamespace(event_type=etype, prd_id=prd_id, payload=payload or {})


_H = {"done": ["r1 完成解析器"], "in_progress": [], "blockers": [],
      "tried_and_failed": ["直接 regex 解析失败"], "next": ["改用 AST 遍历", "补测试"],
      "rationale": "regex 处理不了嵌套注释"}


def _handoff_ref_events(tmp: dict) -> list:
    """构造「同 PRD iteration_handoff 引用」事件序列（digest 取自真实 content，tmp 传出）。"""
    content = json.dumps(_H, ensure_ascii=False)
    tmp["content"] = content
    tmp["digest"] = handoff.digest_of(content)
    return [_ev("verifier_feedback", "prd-1", {"path": "fb.json"}),
            _ev("iteration_handoff", "prd-1",
                {"round": 1, "digest": tmp["digest"], "path": "handoff/aa.json", "size": 99,
                 "type": "handoff", "adopted_from_snapshot": False})]


# ─── 5.1 handoff 供 decisions / next_action ──────────────────────────────
def test_handoff_populates_decisions_and_next_action():
    tmp: dict = {}
    ctx = RC.build_recovery_context(
        iteration_id="it-2", prd_id="prd-1", status_value="revise",
        prd_content="# 目标\n## 验收标准\n- a", events=_handoff_ref_events(tmp),
        handoff_content=tmp["content"])
    # decisions ← tried_and_failed + rationale（设计 D1 映射）
    assert ctx.decisions == ("直接 regex 解析失败", "rationale: regex 处理不了嵌套注释")
    # next_action ← next（多步 join）
    assert ctx.next_action == "改用 AST 遍历; 补测试"
    # 引用面（retry 驱动器可拿去 --handoff-artifact/--handoff-digest）
    assert ctx.handoff_path == "handoff/aa.json" and ctx.handoff_digest == tmp["digest"]


def test_handoff_takes_latest_reference():
    """多事件（多轮）→ 取最新 iteration_handoff 引用（后写覆盖，同 last_verifier_feedback 语义）。"""
    tmp: dict = {}
    evs = _handoff_ref_events(tmp)
    h2 = dict(_H); h2["next"] = ["第二轮 next"]
    c2 = json.dumps(h2, ensure_ascii=False)
    evs.append(_ev("iteration_handoff", "prd-1",
                   {"round": 2, "digest": handoff.digest_of(c2), "path": "handoff/bb.json",
                    "size": 1, "type": "handoff", "adopted_from_snapshot": False}))
    ctx = RC.build_recovery_context(
        iteration_id="it-3", prd_id="prd-1", status_value="revise",
        prd_content="# 目标", events=evs, handoff_content=c2)
    assert ctx.next_action == "第二轮 next" and ctx.handoff_path == "handoff/bb.json"


# ─── never gates verdicts 机械锚（delta scenario）───────────────────────
def test_handoff_never_gates_mechanical_anchor():
    """handoff 并入 ≠ 判据：status/suggested_next_step 等机械字段与无 handoff 完全一致
    （同基座事件，仅多 iteration_handoff 一条）；RecoveryContext 无 verdict/gate/retry
    决策字段；恶意未知键不渗入输出。"""
    base_evs = [_ev("verifier_feedback", "prd-1", {"path": "fb.json"})]
    base = RC.build_recovery_context(
        iteration_id="it-2", prd_id="prd-1", status_value="revise",
        prd_content="# 目标\n## 验收标准\n- a", events=base_evs)
    evil = dict(_H)
    evil["verdict"] = "pass"           # 契约外字段（validate_handoff 丢弃——不得渗入）
    evil["retry"] = False
    evil["gate_status"] = "green"
    content = json.dumps(evil, ensure_ascii=False)
    evs = base_evs + [_ev("iteration_handoff", "prd-1",
                          {"round": 1, "digest": handoff.digest_of(content), "path": "handoff/x.json",
                           "size": 1, "type": "handoff", "adopted_from_snapshot": False})]
    got = RC.build_recovery_context(
        iteration_id="it-2", prd_id="prd-1", status_value="revise",
        prd_content="# 目标\n## 验收标准\n- a", events=evs, handoff_content=content)
    # 机械字段零变化（verdict/retry 决策在 RecoveryPlan/RetryPolicy，输入不含 context）
    for f in ("iteration_id", "prd_id", "status", "objective", "acceptance_criteria",
              "last_verifier_feedback_path", "last_artifact_paths",
              "last_exception_class", "last_failure_summary", "suggested_next_step"):
        assert getattr(got, f) == getattr(base, f), f"{f} 被 handoff 污染"
    # 无决策类字段（RecoveryContext 是上下文载体，不是决策载体）
    names = {f.name for f in dataclasses.fields(RC.RecoveryContext)}
    assert names & {"verdict", "gate", "gate_status", "retry", "retry_policy", "decision"} == set()
    # 契约外内容不渗入任何输出字段
    dumped = json.dumps(dataclasses.asdict(got), ensure_ascii=False)
    for leak in ("verdict", "gate_status", '"retry"'):
        assert leak not in dumped, f"契约外键 {leak} 渗入输出"


# ─── 5.2 无 handoff byte 级回归锚 ────────────────────────────────────────
def test_no_handoff_byte_identical_baseline():
    """无 handoff（无事件 / content 缺 / digest 不过 / 异 PRD）→ 全字段硬快照 = 现状派生
    （delta「No handoff falls back」：新字段空默认，无 placeholder 内容）。"""
    evs = [_ev("verifier_feedback", "prd-1", {"path": "fb.json"})]
    ctx = RC.build_recovery_context(
        iteration_id="it-1", prd_id="prd-1", status_value="revise",
        prd_content="---\nname: 做 X\n---\n# 做 X\n## 验收标准\n- a1\n- a2", events=evs)
    assert dataclasses.asdict(ctx) == {
        "iteration_id": "it-1", "prd_id": "prd-1", "status": "revise",
        "objective": "做 X", "acceptance_criteria": ("a1", "a2"),
        "last_verifier_feedback_path": "fb.json", "last_artifact_paths": ("fb.json",),
        "last_exception_class": None, "last_failure_summary": None,
        "suggested_next_step": RC.suggested_next_step("revise"),
        # add-dev-handoff-artifact 新字段：空默认（无 handoff = 正常缺失，非错误）
        "handoff_path": None, "handoff_digest": None,
        "decisions": (), "next_action": None,
    }


def test_ref_without_content_falls_back():
    """引用在但调用方没读到 content（文件丢/权限）→ 不并入，等同缺失。"""
    tmp: dict = {}
    ctx = RC.build_recovery_context(
        iteration_id="it", prd_id="prd-1", status_value="revise",
        prd_content="# 目标", events=_handoff_ref_events(tmp))   # handoff_content 缺省 None
    assert ctx.decisions == () and ctx.next_action is None
    assert ctx.handoff_path is None and ctx.handoff_digest is None


def test_digest_mismatch_not_incorporated():
    """落盘后被改（digest 不过）→ 拒收：被改内容绝不进恢复上下文（evidence-integrity；
    注入侧 dev-agent exit 10 才是显式报错面，恢复侧等同缺失回退机械派生）。"""
    tmp: dict = {}
    evs = _handoff_ref_events(tmp)
    tampered = json.dumps({**_H, "next": ["被改过的 next"]}, ensure_ascii=False)
    ctx = RC.build_recovery_context(
        iteration_id="it", prd_id="prd-1", status_value="revise",
        prd_content="# 目标", events=evs, handoff_content=tampered)
    assert ctx.decisions == () and ctx.next_action is None and ctx.handoff_path is None
    dumped = json.dumps(dataclasses.asdict(ctx), ensure_ascii=False)
    assert "被改过的 next" not in dumped


def test_other_prd_handoff_ignored():
    """异 PRD 的 handoff 引用不并入（「preceding iteration of the same PRD」边界）。"""
    content = json.dumps(_H, ensure_ascii=False)
    evs = [_ev("iteration_handoff", "prd-OTHER",
               {"round": 1, "digest": handoff.digest_of(content), "path": "handoff/o.json",
                "size": 1, "type": "handoff", "adopted_from_snapshot": False})]
    ctx = RC.build_recovery_context(
        iteration_id="it", prd_id="prd-1", status_value="revise",
        prd_content="# 目标", events=evs, handoff_content=content)
    assert ctx.decisions == () and ctx.next_action is None
