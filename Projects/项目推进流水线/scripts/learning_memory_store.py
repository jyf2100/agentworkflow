#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""learning_memory_store.py — add-cross-prd-learning-memory Section 2.1/2.2/2.3 实现。

per-project append-only candidate/event writer + 存储层 defense-in-depth 校验 +
equivalence_key stamp + 路径隔离（spec design 决策#3 存储布局 + 决策#7 fail-closed memory）。

状态布局（design 决策#3）::

    .project-auto/state/lessons/
      candidates/<project>.jsonl   # append-only candidate 事实
      events/<project>.jsonl       # append-only lifecycle 事件
      catalog/<project>.json       # atomic rebuildable projection（learning_memory_catalog.py 负责）

每行 = 一个合法 JSON 对象 + ``\\n``。versioned（schema_version + kind 字段）。**append-only**：
``O_APPEND`` + ``flush`` + ``os.fsync``（参照 ``journal.py`` 的既定模式）+ ``fcntl.flock``
（多线程并发 append 无丢失无交错——journal 单行小写靠 O_APPEND 即可，本模块需序列化后整写故加 flock）。

**fail-closed for memory**（design 决策#7）：
    * 末尾不完整容忍（crash 截断最后一条 append → 丢弃半行继续）；
    * 中部损坏 fail-closed（committed history 污染 → ``LessonsCorruptionError``，绝不静默跳过）；
    * complete-JSON-but-schema-invalid 始终 fail-closed（task 3.6 既定：complete-but-invalid 是污染，非截断）。

**defense-in-depth**（task 2.2）：LessonCandidate.__post_init__ 已 enforce 大部分；存储层**再**校验
identity（project_id 非空 / prd_id 非空 / iteration_refs 非空）+ evidence_refs integrity shape（每条带
``digest``）—— 防「绕过 schema 直接灌 JSONL」。

**目录层 equivalence_key 兜底**（task 2.3）：derive_equivalence_key（Section 1 函数）写入 JSONL 行；
读端对每条记录重算覆盖存储值（防手灌假 key）；超词表枚举读端拒（fail-closed）。

**纯 stdlib**（hashlib/json/fcntl/os/dataclasses/pathlib/threading），零 SDK 模块级导入——cron 隔离不变。
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import fcntl
from pathlib import Path
from typing import ClassVar, Any

import learning_memory_schema as LM


# ════════════════════════════════════════════════════════════════════════
# versioning + 路径布局
# ════════════════════════════════════════════════════════════════════════
LESSONS_SCHEMA_VERSION: int = 1   # candidates.jsonl / events.jsonl 的 record schema 版本

_RECORD_KIND_CANDIDATE = "candidate"
_RECORD_KIND_EVENT = "event"
_RECORD_KIND_USAGE = "usage"   # Section 6：usage outcome facts（task 6.2 append）


def lessons_dir(state_dir: str | Path) -> Path:
    """所有 memory state 根目录（design 决策#3）。"""
    return Path(state_dir) / "lessons"


def candidate_path(state_dir: str | Path, project_id: str) -> Path:
    """candidates/<project>.jsonl 路径（append-only facts）。"""
    return lessons_dir(state_dir) / "candidates" / f"{project_id}.jsonl"


def event_path(state_dir: str | Path, project_id: str) -> Path:
    """events/<project>.jsonl 路径（append-only lifecycle events）。"""
    return lessons_dir(state_dir) / "events" / f"{project_id}.jsonl"


def usage_path(state_dir: str | Path, project_id: str) -> Path:
    """usage/<project>.jsonl 路径（Section 6 task 6.2：append-only usage outcome facts）。

    design 决策#3 存储布局扩展：candidates/events 是 Section 2 真源；usage 是 Section 6 真源
    （与 events 平行，append-only + flock + fsync + O_APPEND）。catalog 仍是 rebuildable
    projection（learning_memory_catalog._replay 把 usage 派生进 effectiveness_history 等 entry 字段）。
    """
    return lessons_dir(state_dir) / "usage" / f"{project_id}.jsonl"


def catalog_path(state_dir: str | Path, project_id: str) -> Path:
    """catalog/<project>.json 路径（atomic rebuildable projection，由 learning_memory_catalog 写）。"""
    return lessons_dir(state_dir) / "catalog" / f"{project_id}.json"


# ════════════════════════════════════════════════════════════════════════
# fail-closed corruption（参照 journal.JournalCorruptionError 既定模式）
# ════════════════════════════════════════════════════════════════════════
class LessonsCorruptionError(Exception):
    """lessons 已提交历史内出现损坏行（fail-closed）。

    spec design 决策#7：「A malformed catalog is skipped rather than partially trusted」。
    ``line_number`` 1-based 供运维定位（同 ``JournalCorruptionError`` 既定格式）。
    """

    def __init__(self, source: str, line_number: int, raw_snippet: str = ""):
        self.source = source
        self.line_number = line_number
        self.raw_snippet = raw_snippet
        super().__init__(
            f"{source} 第 {line_number} 行损坏（committed history 内 malformed，fail-closed）"
        )


@dataclasses.dataclass(frozen=True)
class CorruptionReport:
    """lessons 完整性扫描报告（validate_* 返回，不 raise）。

    同 ``journal.CorruptionReport`` 语义：``tail_truncated`` 末尾截断容忍丢弃；
    ``corrupted_line_numbers`` 中部损坏（1-based）——非空即 fail-closed。
    """
    __test__: ClassVar[bool] = False
    events_read: int = 0
    tail_truncated: bool = False
    corrupted_line_numbers: tuple[int, ...] = ()

    @property
    def is_fail_closed(self) -> bool:
        return bool(self.corrupted_line_numbers)


# ════════════════════════════════════════════════════════════════════════
# task 2.2：存储层 defense-in-depth 校验（防绕过 schema 灌 JSONL）
# ════════════════════════════════════════════════════════════════════════
def _validate_candidate_for_storage(candidate: LM.LessonCandidate) -> None:
    """task 2.2：存储层 defense-in-depth 再校验——schema 已 enforce 大部分，这里补 identity + evidence shape。

    spec design 决策#4 + task 2.2：「project/PRD/iteration identity / bounded field sizes /
    integrity-checked evidence_refs」。schema 已 enforce field sizes + enum vocab + reusable trigger
    （applicability_when 非空）+ executable corrective_action（非空）。存储层补：
        * ``project_id`` 非空（scope 隔离前提）；
        * ``prd_id`` 非空（cross-PRD recurrence 判定依赖）；
        * ``iteration_refs`` 非空（evidence 回溯真源）；
        * ``evidence_refs`` 每条带 ``digest``（integrity-checked，参照 artifact_store.verify_digest）。
    """
    if not isinstance(candidate.project_id, str) or not candidate.project_id.strip():
        raise ValueError(
            "LessonCandidate.project_id 不可为空（spec task 2.2：identity project/PRD/iteration 必填）")
    if not isinstance(candidate.prd_id, str) or not candidate.prd_id.strip():
        raise ValueError(
            "LessonCandidate.prd_id 不可为空（spec task 2.2：cross-PRD recurrence 判定依赖）")
    if not candidate.iteration_refs:
        raise ValueError(
            "LessonCandidate.iteration_refs 不可为空（spec task 2.2：evidence 回溯真源）")
    for i, ref in enumerate(candidate.evidence_refs):
        if not isinstance(ref, dict) or "digest" not in ref or not str(ref["digest"]).strip():
            raise ValueError(
                f"evidence_refs[{i}] 缺 digest（spec task 2.2：integrity-checked evidence_refs）")


# ════════════════════════════════════════════════════════════════════════
# task 2.3：candidate_id + equivalence_key stamp
# ════════════════════════════════════════════════════════════════════════
def _derive_candidate_id(candidate: LM.LessonCandidate, equivalence_key: str) -> str:
    """确定性 candidate_id = sha256(equivalence_key + prd_id + iteration_refs + evidence digests)[:12]。

    同内容同 ID → catalog replay dedupe（同一 candidate 写两次不产生两条 catalog entry）；
    不同 evidence → 不同 ID → 都保留（spec「Merges preserve all source candidate IDs」）。
    """
    h = hashlib.sha256()
    h.update(equivalence_key.encode("utf-8"))
    h.update(b"|")
    h.update(candidate.prd_id.encode("utf-8"))
    h.update(b"|")
    for it in candidate.iteration_refs:
        h.update(it.encode("utf-8"))
        h.update(b",")
    h.update(b"|")
    for ref in candidate.evidence_refs:
        dig = ref.get("digest", "") if isinstance(ref, dict) else ""
        h.update(str(dig).encode("utf-8"))
        h.update(b",")
    return "cand_" + h.hexdigest()[:12]


def _serialize_candidate_record(candidate: LM.LessonCandidate, *,
                                run_id: str, timestamp: str,
                                equivalence_key: str | None = None) -> dict:
    """序列化 candidate → versioned JSONL record dict。

    task 2.3：equivalence_key 由 ``LM.derive_equivalence_key`` 派生（**不接受调用方传入的 model-authored key**）；
    若调用方传 equivalence_key 则忽略重算（目录层兜底，绝不信任外部值）。
    """
    derived_key = LM.derive_equivalence_key(candidate)   # 永远重算（task 2.3 目录层兜底）
    cid = _derive_candidate_id(candidate, derived_key)
    return {
        "schema_version": LESSONS_SCHEMA_VERSION,
        "kind": _RECORD_KIND_CANDIDATE,
        "candidate_id": cid,
        "run_id": run_id,
        "timestamp": timestamp,
        "equivalence_key": derived_key,
        "candidate": _candidate_to_jsonable(candidate),
    }


def _candidate_to_jsonable(candidate: LM.LessonCandidate) -> dict:
    """LessonCandidate → JSON-serializable dict（enum 转 value，tuple 转 list）。"""
    from enum import Enum
    d = dataclasses.asdict(candidate)
    # enum 成员 → value（asdict 不自动转）
    out = {}
    for k, v in d.items():
        if isinstance(v, Enum):
            out[k] = v.value
        elif isinstance(v, tuple):
            out[k] = [_enum_to_value(e) for e in v]
        else:
            out[k] = v
    return out


def _enum_to_value(e: Any) -> Any:
    from enum import Enum
    return e.value if isinstance(e, Enum) else e


def _serialize_event_record(event: LM.LessonLifecycleEvent, *, run_id: str) -> dict:
    """序列化 lifecycle event → versioned JSONL record dict。"""
    return {
        "schema_version": LESSONS_SCHEMA_VERSION,
        "kind": _RECORD_KIND_EVENT,
        "run_id": run_id,
        "event": {
            "event_id": event.event_id,
            "timestamp": event.timestamp,
            "project_id": event.project_id,
            "lesson_id": event.lesson_id,
            "event_type": event.event_type,
            "payload": event.payload,
            "schema_version": event.schema_version,
        },
    }


def _serialize_usage_record(outcome: LM.UsageOutcome, *, run_id: str) -> dict:
    """Section 6 task 6.2：序列化 usage outcome → versioned JSONL record dict。

    与 candidate/event 同 schema 形状（``{schema_version, kind:"usage", run_id, usage:{...}}``）。
    usage outcome 是 **append-only facts**（design 决策#3 + 决策#6）：每条记录一次 lesson 在
    某 PRD 上的应用结果；catalog._apply_usage_outcomes 派生 effectiveness_history 等字段。
    """
    return {
        "schema_version": LESSONS_SCHEMA_VERSION,
        "kind": _RECORD_KIND_USAGE,
        "run_id": run_id,
        "usage": {
            "event_id": outcome.event_id,
            "timestamp": outcome.timestamp,
            "project_id": outcome.project_id,
            "lesson_id": outcome.lesson_id,
            "prd_id": outcome.prd_id,
            "action_observed": bool(outcome.action_observed),
            "failure_recurred": bool(outcome.failure_recurred),
            "outcome": outcome.outcome,
            "evidence_refs": list(outcome.evidence_refs),
            "schema_version": outcome.schema_version,
        },
    }


# ════════════════════════════════════════════════════════════════════════
# task 2.1：append-only writers（flock + O_APPEND + flush + fsync）
# ════════════════════════════════════════════════════════════════════════
def _atomic_append_line(path: Path, line: str) -> None:
    """原子追加一行（flock + O_APPEND + flush + fsync）。

    ``fcntl.flock`` 阻塞锁跨线程互斥（序列化 → write → fsync 整段不交错）；
    ``open("a")`` 走 O_APPEND（POSIX 保证追加不撕裂已提交历史）；``flush`` 推到 OS；
    ``os.fsync`` 落盘。crash 只可能丢「正在写的最后一条」，已 fsync 的更早记录必可恢复
    （design 决策#3 append-only 真源前提；同 journal.append_event 既定模式 + 加 flock）。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        # 排他锁：保证整段 write+fsync 跨线程不交错（journal 单行小写仅靠 O_APPEND，本模块序列化后整写故加锁）
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def append_candidate(state_dir: str | Path, project_id: str,
                     candidate: LM.LessonCandidate, *,
                     run_id: str, timestamp: str) -> str:
    """task 2.1 + 2.2 + 2.3：append 一条 candidate 到 ``candidates/<project>.jsonl``。

    流程：
        1. task 2.2 ``_validate_candidate_for_storage``（defense-in-depth identity + evidence shape）；
        2. task 2.3 ``_serialize_candidate_record``（derive equivalence_key + candidate_id）；
        3. ``_atomic_append_line``（flock + O_APPEND + flush + fsync）。

    Args:
        state_dir: 控制面 state 根（``.project-auto/state``）。
        project_id: 项目 scope（与 candidate.project_id 必须一致——per-project 文件隔离）。
        candidate: schema-valid LessonCandidate。
        run_id: 关联 run（design 决策#3「correlated with run_id」）。
        timestamp: 调用方传入 ISO8601（本模块不触时间——cron 隔离 + 可重放）。

    Returns:
        candidate_id（确定性，便于上游记 journal 引用）。

    Raises:
        ValueError: identity / evidence 校验失败（task 2.2 defense-in-depth）。
        OSError: IO 故障（调用方据场景记 degraded 或重试）。
    """
    if candidate.project_id != project_id:
        raise ValueError(
            f"project_id mismatch：candidate.project_id={candidate.project_id!r} != path project_id={project_id!r}"
            "（per-project 文件隔离，绝不可混写）")
    _validate_candidate_for_storage(candidate)
    record = _serialize_candidate_record(candidate, run_id=run_id, timestamp=timestamp)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True)
    _atomic_append_line(candidate_path(state_dir, project_id), line)
    return record["candidate_id"]


def append_lifecycle_event(state_dir: str | Path, project_id: str,
                           event: LM.LessonLifecycleEvent, *,
                           run_id: str) -> None:
    """task 2.1：append 一条 lifecycle event 到 ``events/<project>.jsonl``。

    event 本身经 ``LessonLifecycleEvent.__post_init__`` enforce（event_type 受控词表）；本函数不重复校验。
    """
    if event.project_id != project_id:
        raise ValueError(
            f"project_id mismatch：event.project_id={event.project_id!r} != path project_id={project_id!r}")
    record = _serialize_event_record(event, run_id=run_id)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True)
    _atomic_append_line(event_path(state_dir, project_id), line)


def append_usage_outcome(state_dir: str | Path, project_id: str,
                         outcome: LM.UsageOutcome, *,
                         run_id: str) -> None:
    """Section 6 task 6.2：append 一条 usage outcome 到 ``usage/<project>.jsonl``。

    与 ``append_candidate`` / ``append_lifecycle_event`` 同模式（design 决策#3 + 决策#6）：
    flock + O_APPEND + flush + fsync。outcome 经 ``UsageOutcome.__post_init__`` enforce
    （outcome 受控词表，action_observed/failure_recurred 是 bool）；本函数补 identity 校验。

    **append-only facts**（spec design 决策#6）：usage 是 observation 事实——绝不删除/修改；
    catalog 是 rebuildable projection（``_apply_usage_outcomes`` 派生 effectiveness_history 等）。
    """
    if outcome.project_id != project_id:
        raise ValueError(
            f"project_id mismatch：outcome.project_id={outcome.project_id!r} "
            f"!= path project_id={project_id!r}")
    # identity 校验（防 None / 空 / 类型错——防绕过 schema 灌 JSONL）
    if not isinstance(outcome.event_id, str) or not outcome.event_id.strip():
        raise ValueError("UsageOutcome.event_id 不可为空（usage record identity）")
    if not isinstance(outcome.lesson_id, str) or not outcome.lesson_id.strip():
        raise ValueError("UsageOutcome.lesson_id 不可为空（usage 必须关联 lesson）")
    if not isinstance(outcome.prd_id, str) or not outcome.prd_id.strip():
        raise ValueError("UsageOutcome.prd_id 不可为空（usage 必须关联 PRD）")
    if not isinstance(outcome.timestamp, str) or not outcome.timestamp.strip():
        raise ValueError("UsageOutcome.timestamp 不可为空")
    record = _serialize_usage_record(outcome, run_id=run_id)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True)
    _atomic_append_line(usage_path(state_dir, project_id), line)


# ════════════════════════════════════════════════════════════════════════
# read 端：strict scan（trailing 容忍 / middle fail-closed / 目录层 equivalence_key 重算）
# ════════════════════════════════════════════════════════════════════════
def _scan_jsonl(path: Path, *, source: str) -> tuple[list[dict], CorruptionReport]:
    """扫描 JSONL，返回 (records, CorruptionReport)——同 journal._scan 模型。

    损坏策略（spec design 决策#7 + journal 既定）：
        * JSONDecodeError 在「最后一条非空行」→ tail_truncated（容忍，丢弃半行）；
        * JSONDecodeError 非末尾 → corrupted_line_numbers（fail-closed）；
        * complete-JSON 但 schema 构造失败（complete-but-invalid）→ 始终 fail-closed（task 3.6 既定）。
    """
    if not path.exists():
        return [], CorruptionReport()

    lines = path.read_text(encoding="utf-8").splitlines()
    non_empty_idx = [i for i, ln in enumerate(lines) if ln.strip()]
    last_nonempty = non_empty_idx[-1] if non_empty_idx else -1

    records: list[dict] = []
    corrupted: list[int] = []
    tail_truncated = False

    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            if i == last_nonempty:
                tail_truncated = True
            else:
                corrupted.append(i + 1)   # 1-based
            continue
        records.append(obj)

    report = CorruptionReport(
        events_read=len(records),
        tail_truncated=tail_truncated,
        corrupted_line_numbers=tuple(corrupted),
    )
    return records, report


def _validate_candidate_record(record: dict, *, line_number: int, source: str) -> None:
    """读端 defense-in-depth：对读出的 candidate record 再校验（防手灌 JSONL 绕过 schema）。

    spec task 2.2 + 2.3：「reject out-of-vocabulary enum」「redact model-authored equivalence_key」。
    complete-JSON-but-schema-invalid → raise LessonsCorruptionError（fail-closed，task 3.6 既定）。
    """
    if not isinstance(record, dict):
        raise LessonsCorruptionError(source, line_number)
    if record.get("kind") != _RECORD_KIND_CANDIDATE:
        raise LessonsCorruptionError(source, line_number)
    cand_dict = record.get("candidate")
    if not isinstance(cand_dict, dict):
        raise LessonsCorruptionError(source, line_number)
    # task 2.3：equivalence_key 永远重算覆盖存储值（防手灌假 key）
    try:
        cand = _candidate_from_record(cand_dict)
    except Exception:
        raise LessonsCorruptionError(source, line_number)
    expected_key = LM.derive_equivalence_key(cand)
    record["equivalence_key"] = expected_key   # 覆盖存储值（task 2.3 目录层兜底）
    record["candidate"] = _candidate_to_jsonable(cand)   # 规整 enum value
    # task 2.2：identity + evidence integrity shape 再校验（防绕过 schema）
    try:
        _validate_candidate_for_storage(cand)
    except ValueError:
        raise LessonsCorruptionError(source, line_number)


def _candidate_from_record(cand_dict: dict) -> LM.LessonCandidate:
    """从存储 dict 还原 LessonCandidate——经 candidate_from_model_output 走 schema 边界 redaction。

    task 2.3 目录层兜底：model-authored pattern_key/equivalence_key/invariant_class/...
    若混入存储行，candidate_from_model_output 已 redact（spec task 1.4）。超词表枚举 → ValueError。
    """
    return LM.candidate_from_model_output(
        cand_dict,
        project_id=cand_dict.get("project_id", ""),
        prd_id=cand_dict.get("prd_id", ""),
        iteration_refs=tuple(cand_dict.get("iteration_refs") or ()),
        source_outcome=cand_dict.get("source_outcome"),
        confidence=cand_dict.get("confidence"),
    )


def read_candidate_records(state_dir: str | Path, project_id: str) -> list[dict]:
    """读 candidates/<project>.jsonl → list[record]。每条 record 经 _validate_candidate_record 重校验。

    末尾不完整容忍；中部损坏 → raise ``LessonsCorruptionError``（fail-closed）。
    文件不存在 → 空列表（首次运行未 reflect 过是正常态）。
    """
    path = candidate_path(state_dir, project_id)
    records, report = _scan_jsonl(path, source=f"candidates/{project_id}.jsonl")
    if report.corrupted_line_numbers:
        raise LessonsCorruptionError(
            f"candidates/{project_id}.jsonl", report.corrupted_line_numbers[0])
    # 逐条 defense-in-depth 校验（line_number 1-based）
    for i, rec in enumerate(records, start=1):
        _validate_candidate_record(rec, line_number=i, source=f"candidates/{project_id}.jsonl")
    return records


def read_event_records(state_dir: str | Path, project_id: str) -> list[dict]:
    """读 events/<project>.jsonl → list[record]。

    末尾不完整容忍；中部损坏 → raise ``LessonsCorruptionError``（fail-closed）。
    """
    path = event_path(state_dir, project_id)
    records, report = _scan_jsonl(path, source=f"events/{project_id}.jsonl")
    if report.corrupted_line_numbers:
        raise LessonsCorruptionError(
            f"events/{project_id}.jsonl", report.corrupted_line_numbers[0])
    return records


def _validate_usage_record(record: dict, *, line_number: int, source: str) -> None:
    """Section 6 task 6.2 读端 defense-in-depth：对读出的 usage record 再校验。

    spec design 决策#7「fail-closed for memory」：complete-JSON-but-schema-invalid → raise
    ``LessonsCorruptionError``（绝不部分信任）。校验：
        * kind == "usage"；
        * usage 子字典存在 + 必填 identity 字段（event_id/timestamp/project_id/lesson_id/prd_id）；
        * action_observed / failure_recurred 是 bool；
        * outcome 在 ``LM._VALID_USAGE_OUTCOMES`` 受控词表（防手灌 unknown/超词表）。
    """
    if not isinstance(record, dict):
        raise LessonsCorruptionError(source, line_number)
    if record.get("kind") != _RECORD_KIND_USAGE:
        raise LessonsCorruptionError(source, line_number)
    u = record.get("usage")
    if not isinstance(u, dict):
        raise LessonsCorruptionError(source, line_number)
    for field in ("event_id", "timestamp", "project_id", "lesson_id", "prd_id"):
        v = u.get(field)
        if not isinstance(v, str) or not v.strip():
            raise LessonsCorruptionError(source, line_number)
    if not isinstance(u.get("action_observed"), bool):
        raise LessonsCorruptionError(source, line_number)
    if not isinstance(u.get("failure_recurred"), bool):
        raise LessonsCorruptionError(source, line_number)
    if u.get("outcome") not in LM._VALID_USAGE_OUTCOMES:
        raise LessonsCorruptionError(source, line_number)


def read_usage_records(state_dir: str | Path, project_id: str) -> list[dict]:
    """Section 6 task 6.2：读 usage/<project>.jsonl → list[record]。

    末尾不完整容忍；中部损坏 → raise ``LessonsCorruptionError``（fail-closed）。
    每条 record 经 ``_validate_usage_record`` defense-in-depth 再校验（防手灌绕过 schema）。
    文件不存在 → 空列表（未注入过 lesson 是正常态）。
    """
    path = usage_path(state_dir, project_id)
    records, report = _scan_jsonl(path, source=f"usage/{project_id}.jsonl")
    if report.corrupted_line_numbers:
        raise LessonsCorruptionError(
            f"usage/{project_id}.jsonl", report.corrupted_line_numbers[0])
    for i, rec in enumerate(records, start=1):
        _validate_usage_record(rec, line_number=i, source=f"usage/{project_id}.jsonl")
    return records


def validate_candidates(state_dir: str | Path, project_id: str) -> CorruptionReport:
    """validate 不 raise——返回 CorruptionReport（运维探查 / degraded 决策用）。"""
    _, report = _scan_jsonl(candidate_path(state_dir, project_id),
                            source=f"candidates/{project_id}.jsonl")
    return report


def validate_events(state_dir: str | Path, project_id: str) -> CorruptionReport:
    """validate 不 raise——返回 CorruptionReport。"""
    _, report = _scan_jsonl(event_path(state_dir, project_id),
                            source=f"events/{project_id}.jsonl")
    return report


def validate_usage(state_dir: str | Path, project_id: str) -> CorruptionReport:
    """Section 6 task 6.2：validate 不 raise——返回 CorruptionReport（运维探查 / degraded 决策用）。"""
    _, report = _scan_jsonl(usage_path(state_dir, project_id),
                           source=f"usage/{project_id}.jsonl")
    return report
