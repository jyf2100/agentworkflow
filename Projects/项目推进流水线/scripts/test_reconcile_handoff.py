#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_reconcile_handoff.py — 本体论审核 B1（2026-10-26 裁决：补接线）。

delta spec session-aware-retry scenario「Handoff supplies decisions and next action」的
恢复摘要并入面在此闭环：``recover_iteration`` 增 ``artifact_root`` 接缝——journal 最新同
PRD ``iteration_handoff`` 引用 → 读 artifact 内容 → 喂 ``build_recovery_context``（digest
门 + 契约校验**单源于该函数**，本侧只读文件不二次校验，防双源漂移）。

覆盖：
    - artifact 在盘 + artifact_root 传 → plan.context.decisions/next_action 取 dev 自述、
      handoff_path/handoff_digest 引用面就绪（spec scenario 行为经生产 driver 可达）；
    - artifact_root 缺省 None（既有调用方零改动）→ 空默认（向后兼容，行为不变）；
    - artifact 文件缺失 / 落盘后被改（digest 不过）→ 等同缺失回退机械派生（evidence-integrity
      端到端穿透：被改内容绝不进 RecoveryPlan）。

run_daily retry 闭环对 ``_rplan.context`` 的消费（retry_decided journal payload）是
dispatch_one 内联接线，repo 惯例不在单测锁巨函数（组4 同边界）。跑：
python3 -m pytest scripts/test_reconcile_handoff.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import handoff  # noqa: E402
import loop_runtime as RT  # noqa: E402
import reconcile as RE  # noqa: E402
import retry_policy as RP  # noqa: E402
from session_meta import SessionStore  # noqa: E402

_H = {"done": ["r1 解析完成"], "in_progress": [], "blockers": [],
      "tried_and_failed": ["regex 路不通"], "next": ["改用 AST 遍历"],
      "rationale": "嵌套注释"}


def _journal_with_handoff(tmp_path: Path, content: str) -> Path:
    """生命周期事件 + 真实 digest 的 iteration_handoff 引用事件落 journal。"""
    sj = RT.ShadowJournal(tmp_path / "j.jsonl", "run_1", lambda: "2026-10-26T00:00:00Z", enabled=True)
    for et in ("planned", "running", "agent_finished"):
        sj.emit(et, "iter_1", "prd_1", payload={"base": "abc"})
    sj.emit("iteration_handoff", "iter_1", "prd_1", payload={
        "round": 1, "digest": handoff.digest_of(content), "path": "handoff/aa.json",
        "size": len(content.encode()), "type": "handoff", "adopted_from_snapshot": False})
    return sj.path


def _artifact_on_disk(tmp_path: Path, rel: str, content: str) -> Path:
    root = tmp_path / "art" / "run_1"
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return root


def _recover(jf: Path, art_root, tmp_path: Path):
    return RE.recover_iteration(
        journal_path=jf, run_id="run_1", prd_id="prd_1", iteration_id="iter_1", base="abc",
        prd_content="# 目标\n\n## 验收标准\n- 条件\n",
        targets=[RE.SideEffectTarget("pr", "owner/repo:auto/b")],
        resolver=_FakeResolver(), session_store=SessionStore(tmp_path / "sess"),
        budget=RP.BudgetState(limits=RP.BudgetLimits()), artifact_root=art_root)


class _FakeResolver:
    def check(self, kind, target):
        return False


def test_recover_iteration_incorporates_handoff(tmp_path):
    """artifact 在盘 + artifact_root → 恢复摘要 decisions/next_action 取 dev 自述（spec 场景可达）。"""
    content = json.dumps(_H, ensure_ascii=False)
    jf = _journal_with_handoff(tmp_path, content)
    root = _artifact_on_disk(tmp_path, "handoff/aa.json", content)
    plan = _recover(jf, root, tmp_path)
    assert plan.context.decisions == ("regex 路不通", "rationale: 嵌套注释")
    assert plan.context.next_action == "改用 AST 遍历"
    assert plan.context.handoff_path == "handoff/aa.json"
    assert plan.context.handoff_digest == handoff.digest_of(content)


def test_recover_iteration_no_artifact_root_keeps_baseline(tmp_path):
    """artifact_root 缺省 None（既有调用方）→ 不喂 content，空默认回退（向后兼容零改动）。"""
    content = json.dumps(_H, ensure_ascii=False)
    jf = _journal_with_handoff(tmp_path, content)
    _artifact_on_disk(tmp_path, "handoff/aa.json", content)
    plan = RE.recover_iteration(
        journal_path=jf, run_id="run_1", prd_id="prd_1", iteration_id="iter_1", base="abc",
        prd_content="# 目标", targets=[RE.SideEffectTarget("pr", "r:b")],
        resolver=_FakeResolver(), session_store=SessionStore(tmp_path / "sess"),
        budget=RP.BudgetState(limits=RP.BudgetLimits()))
    assert plan.context.decisions == () and plan.context.next_action is None


def test_recover_iteration_artifact_missing_falls_back(tmp_path):
    """引用在但 artifact 文件缺失 → 等同缺失（读取失败非错误，不伪造）。"""
    content = json.dumps(_H, ensure_ascii=False)
    jf = _journal_with_handoff(tmp_path, content)
    root = tmp_path / "art" / "run_1"   # 不写文件
    root.mkdir(parents=True)
    plan = _recover(jf, root, tmp_path)
    assert plan.context.decisions == () and plan.context.next_action is None


def test_recover_iteration_tampered_artifact_rejected(tmp_path):
    """落盘后被改（digest 不过）→ 端到端拒收：被改内容绝不进 RecoveryPlan（门单源在
    build_recovery_context，本测试穿透 driver 验证门仍生效）。"""
    content = json.dumps(_H, ensure_ascii=False)
    jf = _journal_with_handoff(tmp_path, content)
    tampered = json.dumps({**_H, "next": ["被改过的 next"]}, ensure_ascii=False)
    root = _artifact_on_disk(tmp_path, "handoff/aa.json", tampered)
    plan = _recover(jf, root, tmp_path)
    assert plan.context.decisions == () and plan.context.next_action is None
    assert "被改过的 next" not in json.dumps(plan.context.__dict__, ensure_ascii=False, default=str)
