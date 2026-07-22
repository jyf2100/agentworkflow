#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_compat_readers.py — 历史 dispatch JSON 兼容读取器单测（OpenSpec add-durable-loop-runtime task 2.7）。

第二阶段 journal 是新真源，但**迁移期（journal_driven_dispatch flag 开之前）** + **shadow mode 比对（task 3.4）**
必须能读「无 journal 的历史 dispatch 记录」——否则迁移无法验证 parity、回退无据。本读取器把第一阶段
``dispatch_<stamp>.json`` 的 records 翻译成等价 ``IterationState``（loop 终态模型），让新旧两套真源可机械比对。

    ``legacy_status(record)`` —— 历史 dispatch record 的 ``status`` + ``verify.pass`` + ``verify_verdict`` → ``IterationStatus``：
        * pr_open/interrupted_pr + verify.pass + verify_verdict=='pass' → PUBLISHED（双绿已交付）；
        * pr_open/interrupted_pr + verify.pass 但 verify_verdict 非 pass → REVISE（机械绿但语义红，task 4.1 dual gate）；
        * pr_open/interrupted_pr + verify 未过 → REVISE（有 PR 但验证红，打回重做态）；
          兼容：无 verify_verdict 字段 → fallback 仅 verify.pass（迁移前旧契约）；
        * blocked_external_state → EXTERNAL_BLOCKED；blocked_test_gate → TEST_BLOCKED；
        * fail → FAILED；skip → ABORTED；planned → PLANNED；
        * **未知 status → STATE_CORRUPT**（fail-closed：不认识的历史态保守标记，绝不假装成功）。
    ``read_legacy_dispatch(path)`` —— 读 dispatch JSON → ``list[IterationState]``（每 record 一个等价态）。
    ``summarize_terminal(records)`` —— 终态分桶计数（shadow 比对：journal-reduced 终态 vs dispatch 终态）。

纯逻辑模块（dispatch JSON 是已有结构化产物，标准库 json 即可），不触 SDK——cron 隔离不变。

跑：python3 -m pytest scripts/test_compat_readers.py -q
AAA 结构（Arrange / Act / Assert）。
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import compat_readers as C  # noqa: E402
from loop_state import IterationStatus as S  # noqa: E402


def _rec(status: str, verify_pass=None, **extra) -> dict:
    """造一条历史 dispatch record（verify_pass=None 表示无 verify 字段）。"""
    r = {"project": "demo", "slug": "add-x", "prd_path": "prd/demo/add-x.md", "status": status}
    if verify_pass is not None:
        r["verify"] = {"pass": verify_pass}
    r.update(extra)
    return r


# ════════════════════════════════════════════════════════════════════════
# legacy_status：历史 record → IterationStatus 映射
# ════════════════════════════════════════════════════════════════════════
def test_legacy_status_pr_open_verified_is_published():
    """pr_open + verify.pass → PUBLISHED（已交付的唯一合法判定）。"""
    assert C.legacy_status(_rec("pr_open", verify_pass=True)) is S.PUBLISHED


def test_legacy_status_interrupted_pr_verified_is_published():
    """interrupted_pr + verify.pass（reconcile 后补开 PR 且验证绿）→ PUBLISHED。"""
    assert C.legacy_status(_rec("interrupted_pr", verify_pass=True)) is S.PUBLISHED


def test_legacy_status_pr_open_unverified_is_revise():
    """**关键**：有 PR 但 verify 未过 → REVISE（不是 PUBLISHED！）。

    与状态机一致：交付物存在不等于验证通过。兼容读取器必须保留这层语义，否则会把验证红的 PR 算成已交付
    → shadow 比对假绿（掩盖回归）。"""
    assert C.legacy_status(_rec("pr_open", verify_pass=False)) is S.REVISE


def test_legacy_status_blocked_external_state():
    """blocked_external_state → EXTERNAL_BLOCKED（三态 fail-safe 阻断，远程态不明）。"""
    assert C.legacy_status(_rec("blocked_external_state")) is S.EXTERNAL_BLOCKED


def test_legacy_status_blocked_test_gate():
    """blocked_test_gate → TEST_BLOCKED（发布门拦截：test_not_run/failed/stale）。"""
    assert C.legacy_status(_rec("blocked_test_gate")) is S.TEST_BLOCKED


def test_legacy_status_fail_is_failed():
    """fail → FAILED（dispatch 异常/重试耗尽终态）。"""
    assert C.legacy_status(_rec("fail", skip_reason="异常")) is S.FAILED


def test_legacy_status_skip_is_aborted():
    """skip → ABORTED（主动跳过，如 --dispatch-limit 截断 / 项目无 profile）。"""
    assert C.legacy_status(_rec("skip")) is S.ABORTED


def test_legacy_status_planned_is_planned():
    """planned → PLANNED（已排队未投递，迁移期回退读取应保留「未开始」语义）。"""
    assert C.legacy_status(_rec("planned")) is S.PLANNED


def test_legacy_status_stalled_is_stalled():
    """task 3.5：stalled（dev loop 主动刹车，验证红后连续 N 轮无写类进展）→ STALLED 终态。

    独立 terminal class（spec scenario 19「Real shadow parity」列示）——区别于 FAILED（异常），
    stalled 是「主动放弃，分支已清理」的语义，shadow parity 须作独立桶匹配。"""
    assert C.legacy_status(_rec("stalled")) is S.STALLED


def test_legacy_status_orphan_deleted_is_orphan():
    """task 3.5：orphan_deleted（无 commit 孤儿分支清理）→ ORPHAN_DELETED 终态。

    独立 terminal class（spec scenario 19）——区别于 ABORTED（准入跳过），orphan 是「dev 跑过但
    无产出、孤儿分支已删」的语义，shadow parity 须作独立桶匹配。"""
    assert C.legacy_status(_rec("orphan_deleted")) is S.ORPHAN_DELETED


def test_legacy_status_blocked_evidence_is_blocked_evidence():
    """task 4.2：blocked_evidence（green test evidence artifact 持久化失败，不当 fresh green evidence）
    → BLOCKED_EVIDENCE 终态。独立 terminal class——shadow parity 须与 ``_sj_terminal`` + reducer 三端对齐。"""
    assert C.legacy_status(_rec("blocked_evidence")) is S.BLOCKED_EVIDENCE


def test_legacy_status_unknown_is_state_corrupt_fail_closed():
    """**fail-closed**：未知/无法识别的历史 status → STATE_CORRUPT。

   绝不假装成功或映射到 PUBLISHED——历史态若不在已知映射表，保守标记需运维 triage
    （spec fail-safe 精神 + design 决策#2「SDK 成功≠已发布」的延伸：不认识 ≠ 成功）。"""
    assert C.legacy_status(_rec("some_new_future_status")) is S.STATE_CORRUPT
    assert C.legacy_status({}) is S.STATE_CORRUPT   # 完全无 status 字段


def test_legacy_status_dual_gate_semantic_red_demotes_to_revise():
    """task 4.1 dual gate parity 对端：pr_open/interrupted_pr + verify.pass=True（机械绿）但
    verify_verdict='revise'（pa-verify 语义红）→ REVISE。与 ``_sj_terminal`` dual gate 对齐——否则
    shadow parity 断裂（dispatch 端 PUBLISHED、journal 端 REVISE）。spec「Tests green but semantic review red」。"""
    assert C.legacy_status(_rec("interrupted_pr", verify_pass=True, verify_verdict="revise")) is S.REVISE
    assert C.legacy_status(_rec("pr_open", verify_pass=True, verify_verdict="revise")) is S.REVISE


def test_legacy_status_missing_verdict_falls_back_to_mechanical_pass():
    """task 4.1 兼容：历史 record 无 verify_verdict 字段（迁移前旧格式）→ fallback 仅看 verify.pass。
    pr_open + pass（无 verdict）→ PUBLISHED——保历史 records 读取不因 dual gate 升级而破坏。"""
    assert C.legacy_status(_rec("pr_open", verify_pass=True)) is S.PUBLISHED
    assert C.legacy_status(_rec("interrupted_pr", verify_pass=True)) is S.PUBLISHED


# ════════════════════════════════════════════════════════════════════════
# read_legacy_dispatch：dispatch JSON → list[IterationState]
# ════════════════════════════════════════════════════════════════════════
def test_read_legacy_dispatch_translates_records(tmp_path):
    """读 dispatch JSON → 每条 record 一个等价 IterationState，status 映射正确。"""
    # Arrange
    records = [
        _rec("pr_open", verify_pass=True, project="a", slug="s1", commit_sha="deadbeef"),
        _rec("blocked_test_gate", project="b", slug="s2"),
        _rec("fail", project="c", slug="s3"),
    ]
    path = tmp_path / "dispatch_20260720.json"
    path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    # Act
    states = C.read_legacy_dispatch(path)
    # Assert
    assert len(states) == 3
    assert [s.status for s in states] == [S.PUBLISHED, S.TEST_BLOCKED, S.FAILED]
    # 历史 ID 合成稳定（project+slug，无 journal 时给 shadow 比对一个可对齐的 key）
    assert states[0].iteration_id  # 非空
    assert states[0].head == "deadbeef"


def test_read_legacy_dispatch_missing_file_returns_empty(tmp_path):
    """无历史 dispatch JSON（更早的 run / 首次）→ 空列表，非错误。"""
    assert C.read_legacy_dispatch(tmp_path / "nope.json") == []


def test_read_legacy_dispatch_empty_records(tmp_path):
    """空 dispatch（无过闸 PRD 的 run）→ 空列表。"""
    path = tmp_path / "dispatch_x.json"
    path.write_text("[]", encoding="utf-8")
    assert C.read_legacy_dispatch(path) == []


# ════════════════════════════════════════════════════════════════════════
# summarize_terminal：终态分桶计数（shadow 比对 task 3.4）
# ════════════════════════════════════════════════════════════════════════
def test_summarize_terminal_counts_by_status():
    """summarize_terminal 按终态分桶计数——shadow 比对的核心：journal-reduced 终态 vs dispatch 终态两份计数对齐。"""
    records = [
        _rec("pr_open", verify_pass=True),       # PUBLISHED
        _rec("pr_open", verify_pass=True),       # PUBLISHED
        _rec("pr_open", verify_pass=False),      # REVISE
        _rec("blocked_external_state"),           # EXTERNAL_BLOCKED
        _rec("blocked_test_gate"),                # TEST_BLOCKED
        _rec("fail"),                             # FAILED
    ]
    counts = C.summarize_terminal(records)
    assert counts[S.PUBLISHED] == 2
    assert counts[S.REVISE] == 1
    assert counts[S.EXTERNAL_BLOCKED] == 1
    assert counts[S.TEST_BLOCKED] == 1
    assert counts[S.FAILED] == 1


def test_summarize_terminal_shadow_parity_with_journal():
    """shadow 比对场景：同一 run 的 dispatch records 与（模拟的）journal-reduced 终态计数应一致。

    迁移期 journal 旁路写（不改决策），reducer 归约出的终态计数 == 历史 dispatch 终态计数 → parity 成立。"""
    # 「历史 dispatch 真相」
    dispatch_records = [_rec("pr_open", verify_pass=True), _rec("fail")]
    # 「journal reducer 归约出的等价终态」（模拟——真实由 journal.reduce 得到）
    journal_states = [S.PUBLISHED, S.FAILED]
    # Act / Assert：两份计数对齐 → shadow parity
    assert C.summarize_terminal(dispatch_records) == Counter(journal_states)
