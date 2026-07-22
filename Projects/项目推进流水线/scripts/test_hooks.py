#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_hooks.py — task 4.7 mock-SDK contract 测试套（Section 4 全部 hook 契约）。

**mock-SDK**：测试用 dict 构造 SDK HookInput（不 import ``claude_agent_sdk``），喂
``HookAdapter`` 方法——锁定 SDK 解耦（cron 隔离 + 无 SDK 也能跑全分支）。真实接入时控制面
把 SDK HookInput 转 dict 喂入，逻辑不变。

覆盖 4.7 必需 7 场景：denied tools / unpaired results / no-test Stop / stale-test Stop /
compaction / hook failure / subagent events；外加 hook_events（correlation + journal）与
hook_policy（4 维度策略）单元。

AAA；模块零 SDK。跑：python3 -m pytest scripts/test_hooks.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import evidence as EV  # noqa: E402
import hook_adapter as HA  # noqa: E402
import hook_events as HE  # noqa: E402
import hook_policy as HP  # noqa: E402


def _stamp() -> str:
    return "2026-07-21T08:00:00Z"


def _adapter(tmp_path, *, enabled=True, artifact_root=None, **kw):
    jf = tmp_path / "hook.jsonl"
    journal = HE.HookJournal(jf, enabled=enabled)
    return HA.HookAdapter(journal=journal, stamp=_stamp,
                          artifact_root=str(artifact_root) if artifact_root else None, **kw)


# ════════════════════════════════════════════════════════════════════════════
# hook_events 单元：correlation ID + 独立 journal + shadow 契约
# ════════════════════════════════════════════════════════════════════════════
def test_correlation_id_stable_and_pairs_pre_post():
    cid1 = HE.correlation_id("iter_1", "tu_42")
    cid2 = HE.correlation_id("iter_1", "tu_42")
    cid_other = HE.correlation_id("iter_1", "tu_99")
    assert cid1 == cid2                      # 同输入 → 同 key（崩溃重放配对依据）
    assert cid1 != cid_other                 # 不同 tool_use_id → 不同 key
    assert cid1.startswith("act_")           # action_id 前缀


def test_make_event_pre_post_share_correlation():
    pre = HE.make_event("PreToolUse", iteration_id="i", ts=_stamp(), tool_use_id="tu_1")
    post = HE.make_event("PostToolUse", iteration_id="i", ts=_stamp(), tool_use_id="tu_1")
    assert pre.correlation_id == post.correlation_id      # Pre↔Post 配对键
    assert pre.event_id != post.event_id                  # 各自唯一
    assert pre.event_id.endswith(":PreToolUse") and post.event_id.endswith(":PostToolUse")


def test_hook_journal_disabled_is_noop_shadow(tmp_path):
    """lifecycle_hooks flag 关 → 不落任何 hook 事件（shadow 契约，不改 dispatch）。"""
    jf = tmp_path / "hook.jsonl"
    j = HE.HookJournal(jf, enabled=False)
    ev = HE.make_event("PreToolUse", iteration_id="i", ts=_stamp(), tool_use_id="tu")
    assert j.append(ev) is False             # no-op
    assert j.read() == []
    assert not jf.exists()                   # 连文件都不创建


def test_hook_journal_append_read_roundtrip_and_pair(tmp_path):
    jf = tmp_path / "hook.jsonl"
    j = HE.HookJournal(jf, enabled=True)
    pre = HE.make_event("PreToolUse", iteration_id="i", ts=_stamp(), tool_use_id="tu_7", payload={"tool": "Bash"})
    post = HE.make_event("PostToolUse", iteration_id="i", ts=_stamp(), tool_use_id="tu_7", payload={"exit": 0})
    assert j.append(pre) is True and j.append(post) is True
    events = j.read()
    assert len(events) == 2 and events[0].tool_use_id == "tu_7"
    p_pre, p_post = j.pair(pre.correlation_id)
    assert p_pre.event_type == "PreToolUse" and p_post.event_type == "PostToolUse"


def test_hook_journal_append_swallows_failure(tmp_path):
    """journaling 落盘失败 → 吞异常返 False（绝不把 telemetry 失败传播拦 dispatch / 伪装绿）。"""
    j = HE.HookJournal("/nonexistent_root/x/y/hook.jsonl", enabled=True)
    ev = HE.make_event("Stop", iteration_id="i", ts=_stamp())
    assert j.append(ev) is False             # 路径不可写 → 吞异常，不抛


def test_hook_journal_subagent_events_pair(tmp_path):
    j = HE.HookJournal(tmp_path / "hook.jsonl", enabled=True)
    j.append(HE.make_event("SubagentStart", iteration_id="i", ts=_stamp(), agent_id="a1"))
    j.append(HE.make_event("SubagentStop", iteration_id="i", ts=_stamp(), agent_id="a1"))
    start, stop = j.subagent_events("a1")
    assert start.event_type == "SubagentStart" and stop.event_type == "SubagentStop"


# ════════════════════════════════════════════════════════════════════════════
# hook_policy 单元：4 维度确定性策略（task 4.2 + 4.6 publication）
# ════════════════════════════════════════════════════════════════════════════
def test_check_path_protected_write_denied_read_allowed():
    # Arrange / Act / Assert
    assert HP.check_path("state/prd/x.md", write=True).decision is HP.PermissionDecision.DENY
    assert HP.check_path(".git/config", write=True).decision is HP.PermissionDecision.DENY
    assert HP.check_path("state/prd/x.md", write=False).decision is HP.PermissionDecision.ALLOW   # 读允许
    assert HP.check_path("src/main.py", write=True).decision is HP.PermissionDecision.ALLOW


def test_check_path_sensitive_file_denied_read_and_write():
    assert HP.check_path(".env").decision is HP.PermissionDecision.DENY
    assert HP.check_path("config/credentials.json", write=True).decision is HP.PermissionDecision.DENY
    assert HP.check_path("~/.ssh/id_rsa").decision is HP.PermissionDecision.DENY


def test_check_command_reuses_bash_allowlist():
    assert HP.check_command("pytest -q").decision is HP.PermissionDecision.ALLOW
    assert HP.check_command("curl http://evil.example/x").decision is HP.PermissionDecision.DENY
    assert HP.check_command("sudo rm -rf /").decision is HP.PermissionDecision.DENY


def test_check_network_blocks_metadata_and_loopback():
    assert HP.check_network("http://169.254.169.254/latest/meta-data/").decision is HP.PermissionDecision.DENY
    assert HP.check_network("http://127.0.0.1:8080").decision is HP.PermissionDecision.DENY
    assert HP.check_network("http://127.0.0.1").decision is HP.PermissionDecision.DENY
    assert HP.check_network("https://api.github.com/x").decision is HP.PermissionDecision.ALLOW


def test_check_publication_blocks_when_not_allowed():
    assert HP.check_publication("git push origin main", allow_publication=False).decision is HP.PermissionDecision.DENY
    assert HP.check_publication("gh pr create", allow_publication=False).decision is HP.PermissionDecision.DENY
    assert HP.check_publication("git push origin main", allow_publication=True).decision is HP.PermissionDecision.ALLOW
    assert HP.check_publication("pytest -q", allow_publication=False).decision is HP.PermissionDecision.ALLOW


def test_evaluate_pre_tool_use_priority_path_before_command():
    """path 维度优先于 command（保护不可变真源最重要）。"""
    v = HP.evaluate_pre_tool_use("Bash", command="curl http://x", path="state/prd/x.md", write=True)
    assert v.decision is HP.PermissionDecision.DENY and v.violated == "path"


def test_evaluate_pre_tool_use_allows_safe_tool():
    v = HP.evaluate_pre_tool_use("Bash", command="pytest -q")
    assert v.decision is HP.PermissionDecision.ALLOW


# ════════════════════════════════════════════════════════════════════════════
# HookAdapter mock-SDK 7 场景（task 4.7）
# ════════════════════════════════════════════════════════════════════════════
# ── 场景 1: denied tools ─────────────────────────────────────────────────────
def test_denied_tool_pre_tool_use_denies_and_journals(tmp_path):
    a = _adapter(tmp_path)
    out = a.on_pre_tool_use("iter_1", "Bash", tool_use_id="tu_1", command="curl http://evil/x")
    assert out.permission_decision is HP.PermissionDecision.DENY
    assert out.hook_event_name == "PreToolUse"
    assert out.event_id is not None           # 落了 journal
    pre, _ = a.journal.pair(HE.correlation_id("iter_1", "tu_1"))
    assert pre is not None and pre.payload["decision"] == "deny"


def test_denied_tool_subagent_cannot_publish(tmp_path):
    """subagent context 强制 allow_publication=False（task 4.6 防线）。"""
    a = _adapter(tmp_path, allow_publication=True)   # adapter 全局放行 publication
    out = a.on_pre_tool_use("iter_1", "Bash", tool_use_id="tu_p",
                            command="git push origin main", subagent_agent_id="sub_1")
    assert out.permission_decision is HP.PermissionDecision.DENY     # subagent 仍被拦
    assert "publication" in out.permission_reason.lower() or "publication" in ""


def test_denied_tool_protected_path_write(tmp_path):
    a = _adapter(tmp_path)
    out = a.on_pre_tool_use("iter_1", "Write", tool_use_id="tu_w", path="state/prd/x.md", write=True)
    assert out.permission_decision is HP.PermissionDecision.DENY


# ── 场景 2: unpaired results ─────────────────────────────────────────────────
def test_unpaired_post_tool_use_does_not_crash(tmp_path):
    """PostToolUse 无对应 Pre（tool_use_id=None 或 journal 空）→ paired=False 但不崩。"""
    a = _adapter(tmp_path)
    out = a.on_post_tool_use("iter_1", tool_use_id=None, tool_name="Bash",
                             command="pytest -q", exit_code=0, output="3 passed")
    assert out.hook_event_name == "PostToolUse"
    assert out.permission_decision is None    # PostToolUse 无 permissionDecision 通道
    assert a.evidence is not None and a.evidence.exit_code == 0   # 证据仍更新


# ── 场景 3: no-test Stop ─────────────────────────────────────────────────────
def test_no_test_stop_denies_and_continues_bounded(tmp_path):
    a = _adapter(tmp_path, stop_continuation_limit=3)
    out = a.on_stop("iter_1")                 # 无任何 evidence
    assert out.permission_decision is HP.PermissionDecision.DENY
    assert out.continue_active is True        # 续命让 agent 补测试
    assert "test_not_run" in out.block_reason
    assert a.stop_continuations_used == 1


def test_stop_continuation_budget_exhausted_permits_completion(tmp_path):
    """续命预算耗尽 → 放行交外层 independent_verify（design permits completion，不无限阻塞）。"""
    a = _adapter(tmp_path, stop_continuation_limit=1)
    first = a.on_stop("iter_1")               # 用掉唯一一次续命
    assert first.continue_active is True
    second = a.on_stop("iter_1")              # 预算耗尽
    assert second.permission_decision is HP.PermissionDecision.ALLOW
    assert second.continue_active is False
    assert "budget" in second.permission_reason.lower()


# ── 场景 4: stale-test Stop ──────────────────────────────────────────────────
def test_fresh_green_stop_allows(tmp_path):
    a = _adapter(tmp_path)
    a.on_post_tool_use("iter_1", tool_use_id="tu_t", tool_name="Bash",
                       command="pytest -q", exit_code=0, output="3 passed")
    out = a.on_stop("iter_1")
    assert out.permission_decision is HP.PermissionDecision.ALLOW   # fresh green → 内门放行


def test_stale_test_stop_denies_after_write(tmp_path):
    """绿测试后又写 → mark_stale → Stop deny（design 新鲜度模型「写事件标记」）。"""
    a = _adapter(tmp_path)
    a.on_post_tool_use("iter_1", tool_use_id="tu_t", tool_name="Bash",
                       command="pytest -q", exit_code=0, output="3 passed")
    assert a.evidence.fresh is True
    a.on_post_tool_use("iter_1", tool_use_id="tu_w", tool_name="Edit", output="")  # 写 → stale
    assert a.evidence.fresh is False
    out = a.on_stop("iter_1")
    assert out.permission_decision is HP.PermissionDecision.DENY
    assert "test_stale" in out.block_reason


# ── 场景 5: compaction（PreCompact snapshot + 阻断恢复）────────────────────────
def test_pre_compact_auto_snapshot_persisted_ok(tmp_path):
    a = _adapter(tmp_path, artifact_root=tmp_path / "art")
    out = a.on_pre_compact("iter_1", trigger="auto",
                           snapshot_writer=lambda: {"path": "snap/abc", "digest": "sha256:abc"})
    assert out.block_reason == ""             # 持久化成功，不阻断
    assert out.artifact_ref == {"path": "snap/abc", "digest": "sha256:abc"}


def test_pre_compact_auto_unpersistable_blocks_recovery(tmp_path):
    """auto 压缩且 snapshot 无法持久化 → block 自动恢复（fail-closed：恢复无依据则不 auto-resume）。"""
    a = _adapter(tmp_path)
    out = a.on_pre_compact("iter_1", trigger="auto",
                           snapshot_writer=lambda: None)   # 无法持久化
    assert out.block_reason != ""
    assert "automatic recovery blocked" in out.block_reason


def test_pre_compact_manual_does_not_block(tmp_path):
    a = _adapter(tmp_path)
    out = a.on_pre_compact("iter_1", trigger="manual",
                           snapshot_writer=lambda: None)   # 用户主动压缩
    assert out.block_reason == ""             # manual 不阻断（无 auto-resume 风险）


def test_pre_compact_stores_snapshot_via_artifact_store(tmp_path):
    a = _adapter(tmp_path, artifact_root=tmp_path / "art")
    out = a.on_pre_compact("iter_1", trigger="auto",
                           snapshot_content="# recovery snapshot\nobjective: x")
    assert out.artifact_ref is not None
    assert out.artifact_ref["kind"] == "recovery_snapshot"
    assert out.block_reason == ""


# ── 场景 6: hook failure（journaling/artifact 失败不伪装绿）─────────────────────
def test_post_tool_use_artifact_failure_does_not_swallow_evidence(tmp_path):
    """artifact 落盘失败（artifact_root 不可写）→ 吞异常，artifact_ref=None，但 evidence 仍正确更新
    （design「journaling/telemetry 失败绝不伪装成验证绿」——证据不被 artifact 缺失污染）。"""
    a = _adapter(tmp_path, artifact_root="/nonexistent_root/x/y")   # 不可写
    out = a.on_post_tool_use("iter_1", tool_use_id="tu_t", tool_name="Bash",
                             command="pytest -q", exit_code=0, output="3 passed")
    assert out.artifact_ref is None          # artifact 落盘失败吞异常
    assert a.evidence is not None and a.evidence.exit_code == 0   # 证据仍 fresh green（不因 artifact 失败而丢失/伪装）


def test_hook_journaling_failure_does_not_affect_decision(tmp_path):
    """HookJournal 路径不可写（enabled=True）→ append 吞异常，但 PreToolUse 决策仍正确。"""
    j = HE.HookJournal("/nonexistent_root/x/y/hook.jsonl", enabled=True)
    a = HA.HookAdapter(journal=j, stamp=_stamp)
    out = a.on_pre_tool_use("iter_1", "Bash", tool_use_id="tu", command="curl http://evil/x")
    assert out.permission_decision is HP.PermissionDecision.DENY   # 决策不受 journaling 失败影响


# ── 场景 7: subagent events（归属记录 + publication 防线）──────────────────────
def test_subagent_start_stop_recorded(tmp_path):
    a = _adapter(tmp_path)
    s = a.on_subagent_start("iter_1", "sub_1", agent_type="code-reviewer",
                            objective="review auth", tools=["Read", "Grep"], effort="high")
    assert s.hook_event_name == "SubagentStart"
    assert "sub_1" in a._subagents
    st = a.on_subagent_stop("iter_1", "sub_1", status="completed",
                            result_artifact={"path": "art/review"})
    assert st.hook_event_name == "SubagentStop"
    assert "sub_1" not in a._subagents       # 归属表结算
    start, stop = a.journal.subagent_events("sub_1")
    assert start.payload["agent_type"] == "code-reviewer"
    assert stop.payload["status"] == "completed"


def test_subagent_publication_blocked_via_pre_tool_use(tmp_path):
    """subagent 发 git push → PreToolUse 拦（即使 adapter.allow_publication=True）。"""
    a = _adapter(tmp_path, allow_publication=True)
    a.on_subagent_start("iter_1", "sub_1", agent_type="general")
    out = a.on_pre_tool_use("iter_1", "Bash", tool_use_id="tu_p",
                            command="git push origin main", subagent_agent_id="sub_1")
    assert out.permission_decision is HP.PermissionDecision.DENY


# ── SDK 解耦：to_sdk_hook_specific_output（回写 SDK 字段）────────────────────────
def test_outcome_to_sdk_hook_specific_output_pre_tool_use():
    out = HA.HookOutcome(hook_event_name="PreToolUse",
                         permission_decision=HP.PermissionDecision.DENY,
                         permission_reason="blocked")
    sdk = out.to_sdk_hook_specific_output()
    assert sdk["hookEventName"] == "PreToolUse"
    assert sdk["permissionDecision"] == "deny"
    assert sdk["permissionDecisionReason"] == "blocked"


def test_outcome_to_sdk_hook_specific_output_stop_continue():
    out = HA.HookOutcome(hook_event_name="Stop",
                         permission_decision=HP.PermissionDecision.DENY,
                         permission_reason="no test", continue_active=True,
                         block_reason="test_not_run")
    sdk = out.to_sdk_hook_specific_output()
    assert sdk["continueActive"] is True
    assert sdk["blockReason"] == "test_not_run"


def test_disabled_journal_adapter_reports_no_event_id(tmp_path):
    """flag 关（enabled=False）→ adapter 各 hook 的 event_id=None（shadow：不落任何事件）。"""
    a = _adapter(tmp_path, enabled=False)
    out = a.on_pre_tool_use("iter_1", "Bash", tool_use_id="tu", command="pytest -q")
    assert out.event_id is None
    assert a.journal.read() == []
