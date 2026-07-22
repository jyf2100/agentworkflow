#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hook_events.py — task 4.1 hook adapter 事件模型 + 独立 hook journal。

SDK 6 个生命周期事件（对齐 ``test_sdk_contract.py`` 锁定的 HookEvent：PreToolUse /
PostToolUse / Stop / PreCompact / SubagentStart / SubagentStop）的归一化记录层。

**为什么独立 journal**（不混进 ``loop_state`` iteration journal）：``apply_event`` 对未知
event_type 是「无状态迁移但仍累积 event_id」——hook 事件混进 iteration journal 会污染
reducer 的 ``applied_event_ids`` 且语义不清。hook 是**证据/观测流**，iteration 是**状态流**，
分文件天然隔离（``loop_state.reduce`` 只读 iteration journal，从不读 hook journal）。

**shadow 契约**（design 决策#8）：``enabled=False``（``lifecycle_hooks`` flag 关）→ no-op，
不落任何 hook 事件、不改 dispatch 决策。``enabled=True`` → 旁路写 hook 证据。

**journaling 失败语义**（design「journaling/telemetry 失败不伪装绿」）：``append`` 自身抛
异常 → 吞掉返回 ``False``，绝不把 hook 证据落盘失败传播成验证绿或拦 dispatch。

correlation ID：PreToolUse↔PostToolUse 用 ``ids.action_id(iteration, tool_use_id)`` 作配对键
（task 4.3 串联依据）；Stop/PreCompact/Subagent 用 ``action_id(iteration, seq)`` 唯一键。

纯 stdlib（json/os/hashlib + ids），cron 隔离友好（模块导入零 SDK）。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

import ids

# SDK 6 个生命周期事件（test_sdk_contract.py 锁定的 HookEvent 名）。
HOOK_EVENT_TYPES: tuple[str, ...] = (
    "PreToolUse", "PostToolUse", "Stop", "PreCompact",
    "SubagentStart", "SubagentStop",
)

_PRE = "PreToolUse"
_POST = "PostToolUse"


@dataclass(frozen=True)
class HookEvent:
    """一条 hook 事件（归一化、可 JSON 序列化）。

    ``correlation_id``：PreToolUse 与其 PostToolUse 共享同一键（``action_id(iteration,
    tool_use_id)``），供 ``HookJournal.pair`` 配对；Stop/PreCompact/Subagent 事件用各自
    唯一键（``action_id(iteration, seq)``），correlation_id 即自身分组键。
    """
    event_type: str              # HOOK_EVENT_TYPES 之一
    event_id: str                # 全局唯一（correlation_id + ":" + event_type）
    correlation_id: str          # Pre↔Post 配对键
    iteration_id: str
    ts: str
    payload: dict = field(default_factory=dict)
    tool_use_id: str | None = None
    agent_id: str | None = None

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type,
            "event_id": self.event_id,
            "correlation_id": self.correlation_id,
            "iteration_id": self.iteration_id,
            "ts": self.ts,
            "payload": self.payload,
            "tool_use_id": self.tool_use_id,
            "agent_id": self.agent_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "HookEvent":
        return cls(
            event_type=str(d.get("event_type", "")),
            event_id=str(d.get("event_id", "")),
            correlation_id=str(d.get("correlation_id", "")),
            iteration_id=str(d.get("iteration_id", "")),
            ts=str(d.get("ts", "")),
            payload=dict(d.get("payload") or {}),
            tool_use_id=d.get("tool_use_id"),
            agent_id=d.get("agent_id"),
        )


def correlation_id(iteration_id: str, tool_use_id: str | None = None,
                   seq: int | None = None) -> str:
    """Pre/Post 配对键（``ids.action_id`` 封装）。

    同 ``(iteration, tool_use_id)`` → 同 key → Pre 与 Post 能配对（task 4.3）。
    崩溃重放：同输入产同 key（确定性，design 决策#1）。
    """
    return ids.action_id(iteration_id, tool_use_id, seq)


def make_event(event_type: str, *, iteration_id: str, ts: str,
               tool_use_id: str | None = None, agent_id: str | None = None,
               seq: int | None = None, payload: dict | None = None) -> HookEvent:
    """构造 HookEvent（自动算 correlation_id / event_id）。

    PreToolUse/PostToolUse 用 ``(iteration, tool_use_id)`` 配对键（**不含 seq**——否则 Pre/Post
    的 seq 递增会破坏配对）；其余事件用 ``(iteration, seq)``（seq 缺省回退 None）。
    """
    if event_type in (_PRE, _POST):
        cid = correlation_id(iteration_id, tool_use_id)
    else:
        cid = correlation_id(iteration_id, None, seq)
    eid = f"{cid}:{event_type}"
    return HookEvent(
        event_type=event_type, event_id=eid, correlation_id=cid,
        iteration_id=iteration_id, ts=ts, payload=dict(payload or {}),
        tool_use_id=tool_use_id, agent_id=agent_id,
    )


class HookJournal:
    """独立 hook 事件流（append-only JSONL，**不走 loop_state.reduce**）。

    与 iteration journal 完全解耦：``loop_state.reduce`` 从不读本文件；本文件只作 hook
    证据/观测记录（task 4.3 工件配对、task 4.7 契约断言、task 8.2 canary 可观测）。

    读端容忍坏行（hook 证据流，**不** fail-closed 拦 dispatch——与 iteration journal
    中部损坏 fail-closed 不同：状态损坏需运维，证据缺一行不阻断）。
    """
    __test__ = False

    def __init__(self, path, enabled: bool = False):
        self.path = str(path)
        self.enabled = bool(enabled)

    def append(self, event: HookEvent) -> bool:
        """原子 append 一条 hook 事件；返回是否落盘成功。

        ``enabled=False`` → no-op 返 ``False``（shadow 契约）。
        落盘异常 → 吞掉返 ``False``（绝不把 journaling 失败伪装成验证绿 / 传播拦 dispatch）。
        """
        if not self.enabled:
            return False
        try:
            line = json.dumps(event.to_dict(), ensure_ascii=False,
                              separators=(",", ":")) + "\n"
            # O_APPEND 写 + flush + fsync：崩溃后 page cache 不丢已 append 行（同 journal 模式）。
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())
            return True
        except Exception:
            return False

    def read(self) -> list[HookEvent]:
        """读回全部 hook 事件（容忍坏行跳过）。``enabled=False`` 或文件不存在 → 空列表。"""
        if not self.enabled:
            return []
        if not os.path.exists(self.path):
            return []
        events: list[HookEvent] = []
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        events.append(HookEvent.from_dict(json.loads(line)))
                    except Exception:
                        continue   # 坏行跳过（证据流不阻断 dispatch）
        except Exception:
            return []
        return events

    def pair(self, correlation_id: str) -> tuple[HookEvent | None, HookEvent | None]:
        """按配对键找 ``(PreToolUse, PostToolUse)``。无对应则该位为 None。"""
        pre: HookEvent | None = None
        post: HookEvent | None = None
        for ev in self.read():
            if ev.correlation_id != correlation_id:
                continue
            if ev.event_type == _PRE and pre is None:
                pre = ev
            elif ev.event_type == _POST and post is None:
                post = ev
        return pre, post

    def subagent_events(self, agent_id: str) -> tuple[HookEvent | None, HookEvent | None]:
        """按 agent_id 找 ``(SubagentStart, SubagentStop)``（task 4.6 归属配对）。"""
        start: HookEvent | None = None
        stop: HookEvent | None = None
        for ev in self.read():
            if ev.agent_id != agent_id:
                continue
            if ev.event_type == "SubagentStart" and start is None:
                start = ev
            elif ev.event_type == "SubagentStop" and stop is None:
                stop = ev
        return start, stop
