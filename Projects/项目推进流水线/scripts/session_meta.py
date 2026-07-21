#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""session_meta.py — task 5.1 SDK session metadata 持久化。

每 iteration 持久化：SDK session ID、ResultMessage subtype、stop reason、turns、
usage（input/output/cache tokens）、optional cost、compaction count、exception 分类。

身份分工（design 决策#1、open-question「第一版 filesystem session store」）：
    - journal 是 iteration **状态**真源；
    - SDK session store 是 **conversation** 真源（SDK 自管）；
    - 本模块把 session 身份 + 执行 metadata 持久化到 filesystem，供 RetryPolicy（task 5.3）
      消费 session 可恢复性、compaction 次数、失败分类。

纯 stdlib（cron 隔离友好：/usr/bin/python3 可 import，不触 sdk）。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class ResultSubtype(str, Enum):
    """SDK ResultMessage normalized subtype——覆盖控制面 RetryPolicy 决策所需分类。

    SDK success **只能**产生 ``agent_finished``，不能直接 ``publish_ready``/``published``
    （design 决策#2、spec「Explicit loop state machine」）。"""
    SUCCESS = "success"          # 正常 end_turn（仍须 test/verify 门）
    MAX_TURNS = "max_turns"      # 轮次耗尽
    STOPPED = "stopped"          # Stop hook / 外层中断
    ERROR = "error"              # SDK/transport/provider 异常（is_error=True）
    REFUSED = "refused"          # 模型 refusal


class ExceptionClass(str, Enum):
    """Normalized exception 分类——直接决定 RetryPolicy 的 resume vs new_session。

    对齐 design 决策#3：transient+session 可用→resume；context 污染→new_session。"""
    NONE = "none"
    TRANSIENT = "transient"               # provider/transport 临时中断 → resume 候选
    PROVIDER = "provider"                 # provider 固有错误 → fork/new_session 候选
    CONTEXT_CORRUPT = "context_corrupt"   # 上下文污染/compaction 损坏 → new_session
    UNKNOWN = "unknown"


# exception 分类启发式（喂 RetryPolicy；message 已 redact，只看类型/关键词）
_TRANSIENT_MARKERS = (
    "overloaded", "rate_limit", "rate limit", "timeout", "timed out",
    "connection", "reset", "temporarily", "retry", "529", "503", "502", "504",
)
_PROVIDER_MARKERS = (
    "invalid_request", "invalid request", "bad_request", "bad request",
    "permission", "forbidden", "content_policy", "content policy", "400", "401", "403",
)
_CORRUPT_MARKERS = (
    "context", "compaction", "malformed", "too_large", "too large",
    "prompt too long", "maximum context", "length",
)


def classify_exception(exc_type: str, message: str = "") -> ExceptionClass:
    """normalized exception 分类（transient / provider / context_corrupt / unknown）。

    输入只看异常类型名 + 已脱敏 message 关键词——绝不依赖模型自述。返回值直接喂
    RetryPolicy：transient→resume 候选；context_corrupt→new_session。"""
    blob = f"{exc_type} {message}".lower()
    if any(m in blob for m in _TRANSIENT_MARKERS):
        return ExceptionClass.TRANSIENT
    if any(m in blob for m in _CORRUPT_MARKERS):
        return ExceptionClass.CONTEXT_CORRUPT
    if any(m in blob for m in _PROVIDER_MARKERS):
        return ExceptionClass.PROVIDER
    return ExceptionClass.UNKNOWN


def _sanitize_message(msg: str) -> str:
    """抹掉 message 里可能的 secret 残片（ghp_/Bearer/token=…），保留分类用关键词。"""
    if not msg:
        return ""
    s = re.sub(r"ghp_[A-Za-z0-9]{20,}", "[redacted]", msg)
    s = re.sub(r"(?i)bearer\s+[A-Za-z0-9._\-]+", "bearer [redacted]", s)
    s = re.sub(r"(?i)token\s*=\s*[^\s;]+", "token=[redacted]", s)
    return s.strip()[:500]   # 限长，防 journal 膨胀


@dataclass(frozen=True)
class SessionMeta:
    """单次 SDK iteration 的执行 metadata（不可变、可序列化）。"""
    iteration_id: str
    session_id: str | None                      # SDK session ID；缺失→RetryPolicy fallback new_session
    result_subtype: ResultSubtype
    stop_reason: str = ""                        # SDK 原值（end_turn / max_turns / tool_use / …）
    turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float | None = None                # trusted cost；None=未上报（预算用 trusted 源）
    compaction_count: int = 0
    exception_class: ExceptionClass = ExceptionClass.NONE
    exception_message: str = ""                  # sanitized（无 secret）

    @property
    def usage(self) -> dict:
        return {"input": self.input_tokens, "output": self.output_tokens,
                "cache_read": self.cache_read_tokens}

    @property
    def session_resumable(self) -> bool:
        """session 可否 resume：有 session_id 且非 context_corrupt（design risk#91
        「Session resume 固化错误上下文」→ 污染时强制 new_session）。"""
        return bool(self.session_id) and self.exception_class is not ExceptionClass.CONTEXT_CORRUPT

    def to_dict(self) -> dict:
        return {
            "iteration_id": self.iteration_id,
            "session_id": self.session_id,
            "result_subtype": self.result_subtype.value,
            "stop_reason": self.stop_reason,
            "turns": self.turns,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cost_usd": self.cost_usd,
            "compaction_count": self.compaction_count,
            "exception_class": self.exception_class.value,
            "exception_message": self.exception_message,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SessionMeta":
        return cls(
            iteration_id=d["iteration_id"],
            session_id=d.get("session_id"),
            result_subtype=ResultSubtype(d.get("result_subtype", "error")),
            stop_reason=d.get("stop_reason", ""),
            turns=int(d.get("turns", 0)),
            input_tokens=int(d.get("input_tokens", 0)),
            output_tokens=int(d.get("output_tokens", 0)),
            cache_read_tokens=int(d.get("cache_read_tokens", 0)),
            cost_usd=d.get("cost_usd"),
            compaction_count=int(d.get("compaction_count", 0)),
            exception_class=ExceptionClass(d.get("exception_class", "none")),
            exception_message=d.get("exception_message", ""),
        )


# SDK ResultMessage subtype 原值 → normalized ResultSubtype 映射
_SUBTYPE_MAP = {
    "init": ResultSubtype.SUCCESS,        # 不应作 iteration 结果，保守归 success 由上游门拦截
    "result": ResultSubtype.SUCCESS,
}


def _subtype_from_sdk(subtype: str | None, is_error: bool, stop_reason: str) -> ResultSubtype:
    """把 SDK 原始 subtype/stop_reason 映射到 normalized 分类。"""
    if is_error:
        return ResultSubtype.ERROR
    sr = (stop_reason or "").lower()
    if "max_turn" in sr or "max turn" in sr:
        return ResultSubtype.MAX_TURNS
    if "refusal" in sr or "refuse" in sr:
        return ResultSubtype.REFUSED
    if subtype and subtype in _SUBTYPE_MAP:
        return _SUBTYPE_MAP[subtype]
    # 缺省：有 stop_reason/end_turn 视为正常结束（仍须经 test/verify 门）
    return ResultSubtype.SUCCESS if stop_reason else ResultSubtype.STOPPED


def from_sdk_result(iteration_id: str, result: dict, *,
                    compaction_count: int = 0,
                    exception: BaseException | tuple | None = None) -> SessionMeta:
    """从 SDK ResultMessage dict 构造 SessionMeta（真正能跑：接真实 SDK 输出）。

    容错：SDK 版本字段名差异（usage 嵌套 vs 扁平、cost 字段名）统一兜底，缺字段归零/None。
    ``exception`` 为 (type_name, message) 或 BaseException，用于失败分类。"""
    usage = result.get("usage") or {}
    if isinstance(usage, dict):
        # 兼容嵌套 (usage.input_tokens) 与扁平
        inp = usage.get("input_tokens") or usage.get("input") or 0
        out = usage.get("output_tokens") or usage.get("output") or 0
        cache = (usage.get("cache_read_input_tokens") or usage.get("cache_read_tokens")
                 or usage.get("cache_read") or 0)
    else:
        inp = out = cache = 0
    stop_reason = str(result.get("stop_reason") or result.get("stopReason") or "")
    subtype_raw = result.get("subtype")
    is_error = bool(result.get("is_error") or result.get("isError"))
    exc_class = ExceptionClass.NONE
    exc_msg = ""
    if exception is not None:
        if isinstance(exception, BaseException):
            exc_class = classify_exception(type(exception).__name__, str(exception))
            exc_msg = _sanitize_message(str(exception))
        else:   # (type_name, message)
            tname, tmsg = (exception + ("", ""))[:2]
            exc_class = classify_exception(str(tname), str(tmsg))
            exc_msg = _sanitize_message(str(tmsg))
    # cost：优先 total_cost_usd，次 cost_usd，缺失 None
    cost = result.get("total_cost_usd")
    if cost is None:
        cost = result.get("cost_usd")
    cost = float(cost) if cost is not None else None
    return SessionMeta(
        iteration_id=iteration_id,
        session_id=result.get("session_id") or result.get("sessionId"),
        result_subtype=_subtype_from_sdk(subtype_raw, is_error, stop_reason),
        stop_reason=stop_reason,
        turns=int(result.get("num_turns") or result.get("turns") or 0),
        input_tokens=int(inp),
        output_tokens=int(out),
        cache_read_tokens=int(cache),
        cost_usd=cost,
        compaction_count=int(compaction_count),
        exception_class=exc_class,
        exception_message=exc_msg,
    )


class SessionStore:
    """FileSystem session metadata store（design open-question：第一版 filesystem）。

    每 iteration 一个 JSON 文件；journal event 通过 ``session_id`` 引用。原子写
    （tmp→replace）防半写。run 结束后保留供审计/恢复（保留期策略见 design open-question）。"""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def _path(self, iteration_id: str) -> Path:
        return self.root / f"{iteration_id}.json"

    def save(self, meta: SessionMeta) -> Path:
        """原子落盘：tmp→replace，防崩溃半写。返回最终路径。"""
        self.root.mkdir(parents=True, exist_ok=True)
        p = self._path(meta.iteration_id)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(meta.to_dict(), ensure_ascii=False, sort_keys=True),
                       encoding="utf-8")
        tmp.replace(p)
        return p

    def load(self, iteration_id: str) -> SessionMeta | None:
        """读回；缺失返回 None（RetryPolicy 据此 fallback new_session）。"""
        p = self._path(iteration_id)
        if not p.exists():
            return None
        return SessionMeta.from_dict(json.loads(p.read_text(encoding="utf-8")))

    def exists(self, iteration_id: str) -> bool:
        return self._path(iteration_id).exists()
