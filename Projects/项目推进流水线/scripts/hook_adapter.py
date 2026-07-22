#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hook_adapter.py — task 4.1 hook adapter 主体 + task 4.3/4.4/4.5/4.6 各生命周期 hook 逻辑。

SDK 6 个生命周期事件（PreToolUse / PostToolUse / Stop / PreCompact / SubagentStart /
SubagentStop）的**适配层**：把 SDK HookInput（dict，SDK-agnostic）归一化 → 落 ``hook_events``
journal → 调 ``hook_policy`` / ``evidence`` / ``artifact_store`` / recovery snapshot writer →
产 ``HookOutcome``（回写 SDK hookSpecificOutput）。

**SDK 解耦**：adapter 不 import ``claude_agent_sdk``（cron 隔离 + mock-SDK 可测）。真实接入时，
控制面把 SDK HookInput 转 dict 喂 adapter；adapter 输出 ``HookOutcome.to_sdk_hook_specific_output``
转回 SDK 类型。task 4.7 mock-SDK 契约测试无需真实 SDK 即可覆盖全部分支。

各 hook 职责：
    * ``on_pre_tool_use``（4.1+4.2+4.6）—— 落 PreToolUse 事件（correlation_id）+ 调
      ``evaluate_pre_tool_use``（path/command/network/publication）+ subagent context 强制
      ``allow_publication=False``（防 subagent 发 publication）。回写 permissionDecision。
    * ``on_post_tool_use``（4.1+4.3）—— 按 tool_use_id 配对 Pre；持久化 exit status / changed
      paths / sanitized output artifact（artifact_store）/ TestEvidence 更新（绿测试→fresh；
      Edit/Write/MultiEdit→mark_stale）。
    * ``on_stop``（4.1+4.4）—— **bounded** Stop 门：``evaluate_gate`` 查新鲜绿 TestEvidence，
      非绿→deny+续命（消耗 stop_continuation 预算，bounded）；耗尽→放行交外层 independent_verify
      （design「Stop 是低延迟内门，不替代外层独立验证」）。
    * ``on_pre_compact``（4.1+4.5）—— 写 recovery snapshot；auto 压缩且 snapshot 无法持久化 →
      block 自动恢复（fail-closed：恢复无依据则不自动 resume）。
    * ``on_subagent_start`` / ``on_subagent_stop``（4.1+4.6）—— 记录 ownership（agent_id/
      agent_type/objective/tools/effort/status/result artifact），配合 PreToolUse 防 publication。

纯 stdlib + 复用 hook_events/hook_policy/evidence/artifact_store，cron 隔离友好（零 SDK 导入）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

import artifact_store
import evidence as EV
import hook_events as HE
import hook_policy as HP
from loop_state import ArtifactRef

# 写类工具：发生后 mark_stale（design 新鲜度模型「写事件标记」）。
_WRITE_TOOLS: frozenset[str] = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})

# 测试命令片段：PostToolUse 识别「这是干净测试」→ exit 0 记 fresh green TestEvidence。
_TEST_CMD_RE = re.compile(
    r"(pytest|py\.test|python\s+\S*\s*-m\s+pytest|tox|nox|go\s+test|cargo\s+test|"
    r"npm\s+(test|run\s+test)|yarn\s+test|deno\s+test|jest|vitest|mocha|rspec)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class HookOutcome:
    """hook 回调归一化输出（对齐 SDK hookSpecificOutput 的回写字段）。

    ``permission_decision`` 仅 PreToolUse/Stop 回写（SDK 这两 hook 有 permissionDecision 通道）；
    其余 hook 为 None（SDK PreCompact/Subagent 用 continue/stop，本层用 ``block_reason`` 表达）。
    """
    hook_event_name: str
    permission_decision: HP.PermissionDecision | None = None
    permission_reason: str = ""
    continue_active: bool = False        # Stop hook 续命（bounded by stop_continuation 预算）
    block_reason: str = ""               # PreCompact 阻恢复 / Stop 阻完成的结构化原因（空=放行）
    event_id: str | None = None          # 落 hook journal 的 event_id（None=disabled/no-op）
    artifact_ref: dict | None = None     # PostToolUse 工件 / PreCompact snapshot 指针

    def to_sdk_hook_specific_output(self) -> dict:
        """转 SDK hookSpecificOutput（控制面真实接入 SDK 时用）。

        PreToolUse/Stop 带 ``permissionDecision`` + ``permissionDecisionReason``；Stop 带
        ``continueActive``（SDK Stop 续命字段）。PreCompact/Subagent 用 ``block_reason`` 透传。
        """
        out: dict = {"hookEventName": self.hook_event_name}
        if self.permission_decision is not None:
            out["permissionDecision"] = self.permission_decision.value
            out["permissionDecisionReason"] = self.permission_reason
        if self.hook_event_name == "Stop":
            out["continueActive"] = self.continue_active
        if self.block_reason:
            out["blockReason"] = self.block_reason
        return out


def _sanitize_command(command: str) -> str:
    """抹密钥（hook journal 落盘前消毒，复用 artifact_store.redact_secrets）。"""
    return artifact_store.redact_secrets(command or "")


def _looks_like_test_command(command: str) -> bool:
    return bool(command) and bool(_TEST_CMD_RE.search(command))


class HookAdapter:
    """SDK hook 适配器（有状态：持有当前 TestEvidence + stop 续命计数 + subagent 归属表）。

    状态可替换（每次 hook 返回新 ``HookOutcome``，但 evidence/计数/归属表为 adapter 内部状态，
    控制面持有 adapter 实例跨整个 SDK session）。测试用 ``stamp`` 注入固定时间、``HookJournal``
    控制是否落盘。
    """
    __test__ = False

    def __init__(self, *, journal: HE.HookJournal, stamp: Callable[[], str] | None = None,
                 artifact_root: str | None = None, stop_continuation_limit: int = 3,
                 allow_publication: bool = False):
        self.journal = journal
        self.stamp = stamp or (lambda: "1970-01-01T00:00:00Z")
        self.artifact_root = artifact_root
        self.stop_continuation_limit = int(stop_continuation_limit)
        self.allow_publication = bool(allow_publication)
        self._evidence: EV.TestEvidence | None = None
        self._stop_used: int = 0
        self._seq: int = 0
        self._subagents: dict[str, dict] = {}

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    @property
    def evidence(self) -> EV.TestEvidence | None:
        """当前 TestEvidence（控制面/测试读取；4.4 Stop 门 + 4.7 契约断言用）。"""
        return self._evidence

    def set_evidence(self, evidence: EV.TestEvidence | None) -> None:
        """控制面注入外部独立验证产的 TestEvidence（如恢复后重建证据状态）。"""
        self._evidence = evidence

    # ── PreToolUse（4.1 + 4.2 + 4.6 publication 防线）─────────────────────────
    def on_pre_tool_use(self, iteration_id: str, tool_name: str, *,
                        tool_use_id: str | None = None, command: str = "",
                        path: str = "", write: bool = False, url: str = "",
                        subagent_agent_id: str | None = None) -> HookOutcome:
        """落 PreToolUse 事件（correlation_id 绑 tool_use_id）+ 调确定性策略。

        subagent context（``subagent_agent_id`` 非 None）强制 ``allow_publication=False``
        ——subagent 不得发 commit/push/PR（task 4.6，host-side verified publication）。
        """
        allow_pub = self.allow_publication and subagent_agent_id is None
        verdict = HP.evaluate_pre_tool_use(
            tool_name, command=command, path=path, write=write, url=url,
            allow_publication=allow_pub,
        )
        payload = {
            "tool": tool_name,
            "decision": verdict.decision.value,
            "reason": verdict.reason,
            "violated": verdict.violated,
            "command": _sanitize_command(command),
            "path": path,
            "url": url,
            "subagent": subagent_agent_id,
            "allow_publication": allow_pub,
        }
        ev = HE.make_event(
            "PreToolUse", iteration_id=iteration_id, ts=self.stamp(),
            tool_use_id=tool_use_id, agent_id=subagent_agent_id,
            seq=self._next_seq(), payload=payload,
        )
        self.journal.append(ev)
        return HookOutcome(
            hook_event_name="PreToolUse",
            permission_decision=verdict.decision,
            permission_reason=verdict.reason,
            event_id=ev.event_id if self.journal.enabled else None,
        )

    # ── PostToolUse（4.1 + 4.3 配对 + 工件/证据持久化）──────────────────────────
    def on_post_tool_use(self, iteration_id: str, *, tool_use_id: str | None = None,
                         tool_name: str = "", exit_code: int | None = None,
                         changed_paths=None, output: str = "",
                         subagent_agent_id: str | None = None,
                         command: str = "") -> HookOutcome:
        """按 tool_use_id 配对 Pre；持久化 exit status / changed paths / sanitized artifact /
        TestEvidence 更新（测试绿→fresh；写类工具→mark_stale）。"""
        changed_paths = tuple(changed_paths or ())
        # sanitized output artifact 落盘（artifact_store 内容寻址 + 自动抹密钥）
        artifact_ref: dict | None = None
        if output and self.artifact_root and self.journal.enabled:
            try:
                kind = "test_output" if _looks_like_test_command(command or tool_name) else "transcript"
                ref: ArtifactRef = artifact_store.store(
                    self.artifact_root, output, kind=kind, sensitivity="sanitized",
                )
                artifact_ref = {"digest": ref.digest, "path": ref.path, "kind": ref.kind,
                                "size": ref.size}
            except Exception:
                artifact_ref = None   # artifact 落盘失败吞异常（不阻断 dispatch）

        # TestEvidence 更新（design 新鲜度模型）
        evidence_note = ""
        if _looks_like_test_command(command):
            if exit_code == 0:
                self._evidence = EV.TestEvidence(
                    command=command, exit_code=0, completed_at=self.stamp(), fresh=True,
                )
                evidence_note = "fresh_green"
            else:
                self._evidence = EV.TestEvidence(
                    command=command, exit_code=int(exit_code if exit_code is not None else 1),
                    completed_at=self.stamp(), fresh=True,
                )
                evidence_note = f"red_exit={exit_code}"
        elif tool_name in _WRITE_TOOLS and self._evidence is not None:
            self._evidence = EV.mark_stale(self._evidence)
            evidence_note = "staled_by_write"

        payload = {
            "tool": tool_name,
            "exit_code": exit_code,
            "changed_paths": list(changed_paths),
            "artifact": artifact_ref,
            "evidence": evidence_note,
            "paired_pre": tool_use_id is not None,
        }
        ev = HE.make_event(
            "PostToolUse", iteration_id=iteration_id, ts=self.stamp(),
            tool_use_id=tool_use_id, agent_id=subagent_agent_id,
            seq=self._next_seq(), payload=payload,
        )
        self.journal.append(ev)
        return HookOutcome(
            hook_event_name="PostToolUse",
            permission_decision=None,   # PostToolUse 无 permissionDecision 通道
            permission_reason=f"paired={tool_use_id is not None}; evidence={evidence_note}",
            event_id=ev.event_id if self.journal.enabled else None,
            artifact_ref=artifact_ref,
        )

    # ── Stop（4.1 + 4.4 bounded fresh-green TestEvidence 门）────────────────────
    def on_stop(self, iteration_id: str, *, stop_hook_active: bool = False) -> HookOutcome:
        """bounded Stop 门：无新鲜绿 TestEvidence → deny 完成 + 续命（消耗 stop_continuation 预算）。

        - fresh green（GATE_PUBLISH）→ 允许完成（不替代外层 ``independent_verify``）；
        - 非绿 + 续命预算未耗尽 → deny + ``continue_active=True``（让 agent 补测试）；
        - 非绿 + 预算耗尽 → 允许完成（design：permits completion，defer to outer independent_verify；
          Stop 是低延迟内门，不无限阻塞）。
        """
        verdict, reason = EV.evaluate_gate(self._evidence)
        if verdict == EV.GATE_PUBLISH:
            payload = {"gate": verdict, "evidence": "fresh_green", "stop_hook_active": stop_hook_active}
            ev = HE.make_event("Stop", iteration_id=iteration_id, ts=self.stamp(),
                               seq=self._next_seq(), payload=payload)
            self.journal.append(ev)
            return HookOutcome(
                hook_event_name="Stop",
                permission_decision=HP.PermissionDecision.ALLOW,
                permission_reason="fresh green TestEvidence (inner gate; outer independent_verify still runs)",
                event_id=ev.event_id if self.journal.enabled else None,
            )
        # 非绿：bounded 续命 or 耗尽放行
        if self._stop_used < self.stop_continuation_limit:
            self._stop_used += 1
            payload = {"gate": verdict, "block": reason, "continue": True,
                       "used": self._stop_used, "limit": self.stop_continuation_limit}
            ev = HE.make_event("Stop", iteration_id=iteration_id, ts=self.stamp(),
                               seq=self._next_seq(), payload=payload)
            self.journal.append(ev)
            return HookOutcome(
                hook_event_name="Stop",
                permission_decision=HP.PermissionDecision.DENY,
                permission_reason=f"no fresh green TestEvidence: {verdict} — {reason}",
                continue_active=True,
                block_reason=f"{verdict}: {reason}",
                event_id=ev.event_id if self.journal.enabled else None,
            )
        # 预算耗尽 → 放行交外层（不无限阻塞；design permits completion）
        payload = {"gate": verdict, "block": reason, "continue": False,
                   "budget_exhausted": True, "used": self._stop_used,
                   "limit": self.stop_continuation_limit}
        ev = HE.make_event("Stop", iteration_id=iteration_id, ts=self.stamp(),
                           seq=self._next_seq(), payload=payload)
        self.journal.append(ev)
        return HookOutcome(
            hook_event_name="Stop",
            permission_decision=HP.PermissionDecision.ALLOW,
            permission_reason=(f"stop-continuation budget exhausted ({verdict}); "
                               f"deferring to outer independent_verify"),
            continue_active=False,
            block_reason=f"{verdict}: {reason} (budget exhausted; outer gate authoritative)",
            event_id=ev.event_id if self.journal.enabled else None,
        )

    # ── PreCompact（4.1 + 4.5 recovery snapshot + 阻断恢复）──────────────────────
    def on_pre_compact(self, iteration_id: str, *, trigger: str = "auto",
                       snapshot_writer: Callable[[], dict | None] | None = None,
                       snapshot_content: str | None = None) -> HookOutcome:
        """写 recovery snapshot；auto 压缩且 snapshot 无法持久化 → block 自动恢复（fail-closed）。

        ``snapshot_writer``（注入）：控制面提供，返回 ``{path/digest}`` 或 None。无注入时若有
        ``snapshot_content`` + ``artifact_root`` 则直存 recovery_snapshot（internal）。
        ``trigger="manual"`` 不阻断（用户主动压缩，无 auto-resume 风险）。
        """
        snapshot_ref: dict | None = None
        persisted = False
        block = ""
        if snapshot_writer is not None:
            try:
                snapshot_ref = snapshot_writer()
                persisted = snapshot_ref is not None
            except Exception:
                persisted = False
                snapshot_ref = None
        elif snapshot_content is not None and self.artifact_root and self.journal.enabled:
            try:
                ref = artifact_store.store(
                    self.artifact_root, snapshot_content,
                    kind="recovery_snapshot", sensitivity="internal",
                )
                snapshot_ref = {"digest": ref.digest, "path": ref.path, "kind": ref.kind}
                persisted = True
            except Exception:
                persisted = False
                snapshot_ref = None
        # auto 压缩必须能持久化 snapshot，否则 block（防 auto-resume 无依据）
        if trigger == "auto" and not persisted:
            block = "recovery snapshot unpersistable; automatic recovery blocked"
        payload = {"trigger": trigger, "snapshot": snapshot_ref,
                   "persisted": persisted, "block": block}
        ev = HE.make_event("PreCompact", iteration_id=iteration_id, ts=self.stamp(),
                           seq=self._next_seq(), payload=payload)
        self.journal.append(ev)
        return HookOutcome(
            hook_event_name="PreCompact",
            permission_decision=None,
            permission_reason=(f"snapshot {'persisted' if persisted else 'unpersistable'} "
                               f"(trigger={trigger})"),
            block_reason=block,
            event_id=ev.event_id if self.journal.enabled else None,
            artifact_ref=snapshot_ref,
        )

    # ── SubagentStart / SubagentStop（4.1 + 4.6 归属记录 + publication 防线）─────
    def on_subagent_start(self, iteration_id: str, agent_id: str, *,
                          agent_type: str = "", objective: str = "",
                          tools=None, effort: str | None = None) -> HookOutcome:
        """记录 subagent ownership（agent_id/agent_type/objective/tools/effort）。

        PreToolUse 在 subagent context 强制 ``allow_publication=False``（见 ``on_pre_tool_use``），
        故 subagent 发 commit/push/PR 会被拦——本方法只记归属（不重复判定）。
        """
        rec = {"agent_type": agent_type, "objective": objective,
               "tools": list(tools or []), "effort": effort, "status": "running"}
        self._subagents[agent_id] = rec
        ev = HE.make_event(
            "SubagentStart", iteration_id=iteration_id, ts=self.stamp(),
            agent_id=agent_id, seq=self._next_seq(), payload=dict(rec),
        )
        self.journal.append(ev)
        return HookOutcome(
            hook_event_name="SubagentStart",
            permission_reason="subagent ownership recorded",
            event_id=ev.event_id if self.journal.enabled else None,
        )

    def on_subagent_stop(self, iteration_id: str, agent_id: str, *,
                         status: str = "completed", result_artifact: dict | None = None) -> HookOutcome:
        """记录 subagent status + result artifact，结算归属表。"""
        rec = self._subagents.pop(agent_id, {})
        rec["status"] = status
        rec["result"] = result_artifact
        payload = {"status": status, "result": result_artifact,
                   "agent_type": rec.get("agent_type", "")}
        ev = HE.make_event(
            "SubagentStop", iteration_id=iteration_id, ts=self.stamp(),
            agent_id=agent_id, seq=self._next_seq(), payload=payload,
        )
        self.journal.append(ev)
        return HookOutcome(
            hook_event_name="SubagentStop",
            permission_reason=f"subagent {agent_id} stopped: {status}",
            event_id=ev.event_id if self.journal.enabled else None,
            artifact_ref=result_artifact,
        )

    @property
    def stop_continuations_used(self) -> int:
        """Stop 续命已消耗次数（测试/控制面观测 bounded 行为）。"""
        return self._stop_used
