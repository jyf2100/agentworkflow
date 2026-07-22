#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""trace_context.py — task 7.2 trace-context 持久化 + span links（resume/fork/subprocess/cross-process）。

design 决策（Section 7，L78-80）：resume/fork 或跨进程 continuation 用 **trace context + span links**
表达因果。每 PRD run 一个 root span（trace），iteration/session/tool/... 为子 span；resume 接续旧
trace，fork 用 span link 指回 parent trace（因果可见但不混入同一 trace）。

**确定性 trace_id**（design 决策#1 稳定 ID）：``trace_id`` 由 ``run_id`` 派生（同 run 同 trace），
``span_id`` 由 ``(trace_id, span_name, seq)`` 派生——崩溃/跨进程重放产同 trace context，reducer/dedup
友好（与 ids.py 同源确定性原则）。W3C traceparent 格式（``00-{trace}-{span}-01``）兼容标准 collector。

纯 stdlib（hashlib/json/pathlib），cron 隔离友好。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

_TRACE_ID_LEN = 32      # W3C trace_id：128 bit = 32 hex
_SPAN_ID_LEN = 16       # W3C span_id：64 bit = 16 hex


def _hex_digest(*parts, length: int) -> str:
    raw = "\x1f".join(str(p) for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]


def new_trace_id(run_id: str) -> str:
    """确定性 trace_id：``sha256(run_id)[:32]``。同 run → 同 trace（跨进程/重放稳定）。"""
    return _hex_digest("trace", run_id, length=_TRACE_ID_LEN)


def new_span_id(trace_id: str, span_name: str, seq: int | None = None) -> str:
    """确定性 span_id：``sha256(trace_id, span_name, seq)[:16]``。同 (trace, name, seq) → 同 span。"""
    return _hex_digest("span", trace_id, span_name, seq if seq is not None else 0,
                       length=_SPAN_ID_LEN)


@dataclass(frozen=True)
class TraceContext:
    """W3C trace context（trace_id + 当前 span_id + parent）。跨进程持久化 + 派生子 context。"""
    trace_id: str                          # 32 hex
    span_id: str                           # 16 hex（当前 span）
    parent_span_id: str | None = None

    def traceparent(self) -> str:
        """W3C traceparent 头（``00-{trace_id}-{span_id}-01``，version=0 flags=01 sampled）。"""
        return f"00-{self.trace_id}-{self.span_id}-01"

    def child(self, span_name: str, seq: int | None = None) -> "TraceContext":
        """派生子 span context（parent 指向当前 span_id）。"""
        return TraceContext(
            trace_id=self.trace_id,
            span_id=new_span_id(self.trace_id, span_name, seq),
            parent_span_id=self.span_id,
        )


def trace_context_for_run(run_id: str, *, root_span_name: str = "run") -> TraceContext:
    """构造 run 的 root trace context（trace_id 由 run_id 派生）。"""
    trace_id = new_trace_id(run_id)
    return TraceContext(
        trace_id=trace_id,
        span_id=new_span_id(trace_id, root_span_name, 0),
        parent_span_id=None,
    )


def span_link(from_span_id: str, to_span_id: str, *, relation: str = "resume") -> dict:
    """构造 span link（表达 resume/fork 因果，design L80）。

    ``relation`` ∈ resume/fork/subprocess/cross_process——fork 时 child 用 link 指回 parent trace
    的 span（因果可见，但 fork 开新 trace 不混入 parent trace）。
    """
    return {"from": from_span_id, "to": to_span_id, "relation": relation}


def persist(ctx: TraceContext, path: str | Path) -> None:
    """持久化 trace context 到磁盘（跨进程/subprocess continuation 恢复用）。原子写。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps({
        "trace_id": ctx.trace_id, "span_id": ctx.span_id,
        "parent_span_id": ctx.parent_span_id,
    }), encoding="utf-8")
    tmp.replace(p)


def load(path: str | Path) -> TraceContext | None:
    """读回持久化的 trace context（不存在/损坏 → None，调用方 fallback 新 trace）。"""
    p = Path(path)
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return TraceContext(
            trace_id=str(d["trace_id"]), span_id=str(d["span_id"]),
            parent_span_id=d.get("parent_span_id"),
        )
    except Exception:
        return None
