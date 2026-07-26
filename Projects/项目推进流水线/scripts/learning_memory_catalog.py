#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""learning_memory_catalog.py — add-cross-prd-learning-memory Section 2.4 实现。

catalog projection + atomic replacement（spec design 决策#3「atomic, rebuildable projection」+
决策#7「fail-open delivery, fail-closed memory」）。

**核心难点**：从 append-only facts（candidates.jsonl + events.jsonl）确定性 replay 派生
catalog/<project>.json。三组约束同时满足：
    * **deterministic replay**：相同 facts → byte-identical catalog（sorted keys / 稳定顺序 /
      确定性聚合）；
    * **duplicate-event idempotency**：相同 event_id 重放不重复应用（dedupe by event_id）；
    * **malformed-middle-record fail-closed**：中部损坏 → 整个 projection 失败，**绝不部分信任**
      （绝不基于残缺 facts 生成 partial catalog）；同时**绝不覆盖旧 catalog**（rebuild 失败时旧
      catalog 保持原样，可继续被读或调用方记 degraded 跳过）；
    * **incomplete-trailing-record recovery**：末尾半行（crash 截断最后一条 append）→ 截断后
      正常 replay（trailing 可恢复，middle 不可——journal.py 既定模型）。

**atomic replacement**（design 决策#3）：写 temp 文件（同目录，POSIX rename 原子保证）→ fsync →
``os.replace`` 替换 catalog。crash 中途只留 temp，旧 catalog 保持完整。

**fail-open delivery**（design 决策#7）：``project_catalog`` / ``rebuild_catalog`` 是 fail-open
wrapper——存储层 corruption 不抛主路径，返回 ``CatalogResult(ok=False, degraded_class=...)``。
调用方拿 degraded_class 记 ``learning_memory_degraded`` 继续跑，**memory 故障不改 PRD 结果**。

**Section 2 scope**：本模块只做 projection mechanic（replay + 聚合 + atomic）。promotion 状态机
（active 仅当 ≥2 distinct PRD、conflict/supersede/retire 状态迁移）是 Section 3 的 policy，这里
默认 state="active"（lifecycle event 可显式覆盖），Section 3 会扩展 state transition。

**纯 stdlib**（json/os/dataclasses/pathlib/tempfile），零 SDK——cron 隔离不变。
"""
from __future__ import annotations

import dataclasses
import json
import os
import tempfile
from pathlib import Path
from typing import ClassVar

import learning_memory_effectiveness as LME
import learning_memory_promotion as LMP
import learning_memory_store as LMS


CATALOG_SCHEMA_VERSION: int = 1   # catalog 文件 schema 版本（区别于 record schema 版本）


# ════════════════════════════════════════════════════════════════════════
# 结果对象（fail-open 通信契约）
# ════════════════════════════════════════════════════════════════════════
@dataclasses.dataclass(frozen=True)
class CatalogSnapshot:
    """replay 派生的 catalog 快照（rebuildable，绝非真源）。

    ``entries``：按 lesson_id 排序的 ActiveCatalogEntry dict tuple（byte-stable）。
    ``source_candidate_count`` / ``source_event_count``：去重后的 source facts 数（监控用）。
    ``tail_truncated_candidates`` / ``tail_truncated_events``：末尾截断容忍标记。
    """
    __test__: ClassVar[bool] = False
    project_id: str
    entries: tuple[dict, ...]
    source_candidate_count: int = 0
    source_event_count: int = 0
    tail_truncated_candidates: bool = False
    tail_truncated_events: bool = False
    promotion_decisions: tuple[dict, ...] = ()   # Section 3：promotion 判定 audit trail（spec 决策#4）


@dataclasses.dataclass(frozen=True)
class CatalogResult:
    """fail-open 结果：``ok=True`` + snapshot；``ok=False`` + degraded_class（绝不 raise）。

    degraded_class 取值：``middle_corruption``（committed history 中部损坏，fail-closed for memory）/
    ``storage_error``（IO 故障）/ ``schema_reject``（facts schema 非法）。
    spec design 决策#7：「memory fail-closed」= ok=False；「delivery fail-open」= 不抛主路径。
    """
    __test__: ClassVar[bool] = False
    ok: bool
    snapshot: CatalogSnapshot | None = None
    degraded_class: str | None = None
    detail: str = ""


# ════════════════════════════════════════════════════════════════════════
# task 2.4：deterministic replay（核心难点）
# ════════════════════════════════════════════════════════════════════════
def lesson_id_from_equivalence_key(equivalence_key: str) -> str:
    """从 equivalence_key 派生稳定 lesson_id（``lesson_<hash 后缀>``）。

    deterministic：相同 key → 相同 lesson_id（catalog rebuild byte-stable 的前提）。
    公开给调用方：lifecycle event 引用 lesson_id 时必须用此函数派生（event.lesson_id 字段对齐 catalog）。
    """
    # equivalence_key 形如 "proj-a:deadbeefdeadbeef" → 取冒号后 hash 部分
    suffix = equivalence_key.split(":", 1)[1] if ":" in equivalence_key else equivalence_key
    return f"lesson_{suffix}"


def _aggregate_candidates(candidate_records: list[dict], project_id: str) -> dict[str, dict]:
    """task 2.4：按 equivalence_key 聚合 candidates → dict[key, entry]。

    每个 entry 保留所有 source candidate_ids + supporting_prd_ids（spec「Merges preserve all source
    candidate IDs and evidence lineages」）。聚合是确定性的（sorted tuple）。

    Section 6 task A 扩展字段（供 Section 5 retrieval ranking + Section 6 effectiveness lifecycle）：
        * ``applies_when_tags``：candidate.applies_when_tags 的 union（set，projection 时 → sorted tuple）；
        * ``verified_support_count``：= len(supporting_prd_ids)（冗余但显式，ranking 维度）；
        * ``effectiveness_history`` / ``last_outcome_ts`` / ``usage_count`` / ``contradiction_count``：
          默认空值（_apply_usage_outcomes 派生）。
    """
    grouped: dict[str, dict] = {}
    for rec in candidate_records:
        cand = rec["candidate"]
        key = rec["equivalence_key"]
        cand_id = rec["candidate_id"]
        prd_id = cand.get("prd_id", "")
        if key not in grouped:
            grouped[key] = {
                "lesson_id": lesson_id_from_equivalence_key(key),
                "project_id": project_id,
                "equivalence_key": key,
                "source_candidate_ids": set(),
                "supporting_prd_ids": set(),
                "corrective_action": cand.get("corrective_action", ""),
                "trigger": cand.get("applicability_when", ""),
                # P1 #4：non_applicability_when 合并所有等价候选的 boundary（set 内部累加 →
                # projection 时 sorted + "; " join 为 string）。旧实现只在 group 创建时取 first
                # candidate 的值，subsequent candidate 的 boundary 全部丢失（违反 spec task 2.2/3.2
                # 「Merges preserve every source candidate and evidence lineages」）。
                "non_applicability_when": set(),
                "state": "active",   # 默认；Section 3 promotion policy 收紧（conflict/<2 PRD 过滤）
                "confidence": float(cand.get("confidence", 0.0)),
                "schema_version": CATALOG_SCHEMA_VERSION,
                "_audit_corrective_actions": set(),   # Section 3 conflict 检测用（projection 时 strip）
                # Section 6 task A：catalog entry 字段扩展（design 决策#5 ranking 维度 + decision#6 lifecycle）
                "applies_when_tags": set(),   # union of candidate tags；projection → sorted tuple
                # effectiveness-derived 字段默认空值（_apply_usage_outcomes 在 _replay 后派生）
                "effectiveness_history": (),
                "last_outcome_ts": None,
                "usage_count": 0,
                "contradiction_count": 0,
                "verified_support_count": 0,   # 占位；循环结束后由 len(supporting_prd_ids) 派生
            }
        entry = grouped[key]
        entry["source_candidate_ids"].add(cand_id)
        if prd_id:
            entry["supporting_prd_ids"].add(prd_id)
        # Section 3.2 conflict 检测：收集所有 distinct corrective_action 文本（strip 后 exact 比较）
        action_text = str(cand.get("corrective_action", "")).strip()
        if action_text:
            entry["_audit_corrective_actions"].add(action_text)
        # P1 #4：合并所有等价候选的 non_applicability_when（非空 + strip 后去重）。
        # 对照 applies_when_tags 的 union 模式（去重 set）；schema 保证 candidate 字段非空
        # （LearningMemorySchema.LessonCandidate.__post_init__ L222），但 strip 后空值仍跳过
        # （防数据腐化时把空字符串当合法 boundary 累加）。
        non_app_boundary = str(cand.get("non_applicability_when", "")).strip()
        if non_app_boundary:
            entry["non_applicability_when"].add(non_app_boundary)
        # Section 6 task A：aggregate applies_when_tags（union，去重）
        for tag in (cand.get("applies_when_tags") or []):
            if isinstance(tag, str) and tag:
                entry["applies_when_tags"].add(tag)
    # Section 6 task A：派生 verified_support_count（len(supporting_prd_ids)）
    for entry in grouped.values():
        entry["verified_support_count"] = len(entry["supporting_prd_ids"])
    return grouped


def _apply_events_idempotent(grouped: dict[str, dict], event_records: list[dict]) -> int:
    """task 2.4：把 lifecycle events 幂等应用到 entries（dedupe by event_id）。

    相同 event_id 多次重放只应用一次（spec「duplicate-event idempotency」）。
    返回去重后实际应用的 event 数。
    """
    seen_event_ids: set[str] = set()
    applied = 0
    # lesson_id → entry 映射（events 引用 lesson_id，candidates 派生 lesson_id）
    by_lesson_id = {e["lesson_id"]: e for e in grouped.values()}

    for rec in event_records:
        ev = rec.get("event") or {}
        eid = ev.get("event_id")
        if not eid or eid in seen_event_ids:
            continue   # dedupe（task 2.4 idempotency）
        seen_event_ids.add(eid)
        lesson_id = ev.get("lesson_id")
        entry = by_lesson_id.get(lesson_id)
        if entry is None:
            # event 指向的 lesson 不在 catalog（候选已 retired/未 promote 或 cross-section 状态机未就绪）
            # Section 2 不创建 ghost entry；Section 3 的 promotion/conflict 状态机会处理
            continue
        _apply_single_event(entry, ev.get("event_type"), ev.get("payload") or {})
        applied += 1
    return applied


def _apply_single_event(entry: dict, event_type: str, payload: dict) -> None:
    """应用单条 lifecycle event 到 entry（Section 2 最小语义；Section 3 扩展状态机）。

    Section 2 只处理最基础的 confidence 调整 + state 映射；具体状态迁移规则在 Section 3。
    """
    if event_type == "confirmed":
        # bounded confidence 提升（Section 6 会给出精确的 bounded update 规则）
        entry["confidence"] = min(1.0, entry["confidence"] + 0.1 * int(payload.get("count", 1)))
    elif event_type == "confidence_reduced":
        entry["confidence"] = max(0.0, entry["confidence"] - 0.2)
    elif event_type == "superseded":
        entry["state"] = "superseded"
    elif event_type == "retired":
        entry["state"] = "retired"
    elif event_type == "conflicted":
        entry["state"] = "conflicted"
    elif event_type == "merged":
        pass   # merge 在 _aggregate_candidates 已聚合 source_candidate_ids；无额外 state 变更


# ════════════════════════════════════════════════════════════════════════
# Section 6 task 6.2/6.3：_apply_usage_outcomes（bounded confidence + contradiction retire）
# ════════════════════════════════════════════════════════════════════════
def _apply_usage_outcomes(grouped: dict[str, dict], usage_records: list[dict]) -> int:
    """Section 6 task 6.2 + 6.3：把 usage outcome facts 派生到 entry 字段 + bounded confidence update。

    spec design 决策#6 硬约束（机械判定 own every trust boundary）：
        * **deterministic**：相同 usage_records → 相同 entry.confidence/state/effectiveness_history
          （crash-recovery task 7.2 要求 byte-identical）；
        * **bounded confidence update**：
            - 每个 followed/recurrence_prevented → ``+= CONFIDENCE_UP_BOUND`` (cap 1.0)；
            - 每个 contradicted/recurrence_observed → ``-= CONFIDENCE_DOWN_BOUND`` (floor 0.0)；
            - not_observed/unknown → **不变**（spec「absent evidence ≠ disobedience」）；
        * **contradiction-driven retire**（task 6.3）：``contradiction_count >= THRESHOLD`` → state=retired；
        * **terminal stickiness**：仅 ``state=="active"`` 的 entry 应用 confidence/retire 迁移——
          lifecycle 显式 retired/superseded 优先，usage 不覆盖（spec「retirement only changes the
          projection and never deletes source facts」）。

    **幂等**：相同 usage_records（按 event_id 去重 + 按 timestamp 排序）→ byte-identical 派生。
    **fail-closed for memory**：usage 中部损坏 → ``LMS.read_usage_records`` raise → ``_replay`` 不捕获
    → fail-open wrapper 返回 degraded_class=middle_corruption。

    Returns:
        实际应用的（去重后的）usage record 数（监控用）。
    """
    by_lesson_id = {e["lesson_id"]: e for e in grouped.values()}

    # 1. dedupe by event_id + group by lesson_id（按 timestamp 排序保证 deterministic）
    per_lesson: dict[str, list[dict]] = {}
    seen_event_ids: set[str] = set()
    for rec in usage_records:
        u = rec.get("usage") or {}
        eid = u.get("event_id")
        if not isinstance(eid, str) or not eid or eid in seen_event_ids:
            continue   # dedupe（task 7.2 idempotency：crash 后重放同一 event_id 不重复应用）
        seen_event_ids.add(eid)
        lid = u.get("lesson_id")
        if not isinstance(lid, str) or not lid:
            continue
        per_lesson.setdefault(lid, []).append(u)

    applied = 0
    for lesson_id, usages in per_lesson.items():
        entry = by_lesson_id.get(lesson_id)
        if entry is None:
            # usage 指向的 lesson 不在 catalog（candidate 未 promote 或 cross-section 状态机未就绪）
            # 不创建 ghost entry（同 _apply_events_idempotent 既定语义）
            continue
        # deterministic sort by timestamp（稳定 → byte-identical replay）
        usages_sorted = sorted(usages, key=lambda u: str(u.get("timestamp", "")))

        # 2. 派生 effectiveness_history / usage_count / last_outcome_ts / contradiction_count
        history = tuple(
            {
                "outcome": str(u.get("outcome", "")),
                "prd_id": str(u.get("prd_id", "")),
                "timestamp": str(u.get("timestamp", "")),
                "action_observed": bool(u.get("action_observed", False)),
                "failure_recurred": bool(u.get("failure_recurred", False)),
            }
            for u in usages_sorted
        )
        entry["effectiveness_history"] = history
        entry["usage_count"] = len(usages_sorted)
        entry["last_outcome_ts"] = (
            str(usages_sorted[-1].get("timestamp", "")) if usages_sorted else None)
        contradiction_count = sum(
            1 for u in usages_sorted if u.get("outcome") == "contradicted")
        entry["contradiction_count"] = contradiction_count
        applied += len(usages_sorted)

        # 3. terminal stickiness：仅 state=="active" 的 entry 应用 confidence/retire 迁移
        #    lifecycle 显式 retired/superseded 优先——usage 不覆盖（spec design 决策#6 + 决策#3）
        if entry.get("state") != "active":
            continue

        # 4. bounded deterministic confidence update（design 决策#6）
        for u in usages_sorted:
            outcome = u.get("outcome")
            if outcome in ("followed", "recurrence_prevented"):
                entry["confidence"] = min(1.0, entry["confidence"] + LME.CONFIDENCE_UP_BOUND)
            elif outcome in ("contradicted", "recurrence_observed"):
                entry["confidence"] = max(0.0, entry["confidence"] - LME.CONFIDENCE_DOWN_BOUND)
            # not_observed / unknown → 不变（absent evidence ≠ disobedience）

        # 5. contradiction-driven retire（task 6.3）
        if contradiction_count >= LME.CONTRADICTION_RETIRE_THRESHOLD:
            entry["state"] = "retired"

    return applied


def _replay(state_dir: str | Path, project_id: str) -> CatalogSnapshot:
    """task 2.4 核心：从 append-only facts 确定性 replay 派生 CatalogSnapshot。

    **fail-closed for memory**（design 决策#7）：read_candidate_records / read_event_records /
    read_usage_records 在中部损坏时 raise LessonsCorruptionError——本函数不捕获，透传给 fail-open
    wrapper。

    Section 6 扩展：在 promotion policy 之后调用 ``_apply_usage_outcomes`` 派生 effectiveness
    lifecycle（bounded confidence update + contradiction retire + effectiveness_history）。
    """
    # 1. 读 candidates（trailing 容忍 / middle fail-closed 由 store 负责）
    cand_report = LMS.validate_candidates(state_dir, project_id)
    candidate_records = LMS.read_candidate_records(state_dir, project_id)

    # 2. 读 events
    evt_report = LMS.validate_events(state_dir, project_id)
    event_records = LMS.read_event_records(state_dir, project_id)

    # 3. Section 6：读 usage outcome facts（trailing 容忍 / middle fail-closed）
    #    注：不调 validate_usage 单独取 tail_truncated（CatalogSnapshot 不扩展该字段，避免改 dataclass）；
    #    read_usage_records 内部已 scan，中部损坏 raise → fail-open wrapper 兜底。
    usage_records = LMS.read_usage_records(state_dir, project_id)

    # 4. 确定性聚合（task 2.4 + Section 6 task A 字段扩展）
    grouped = _aggregate_candidates(candidate_records, project_id)
    applied = _apply_events_idempotent(grouped, event_records)

    # 5. Section 3 promotion policy（design 决策#4）：<2 PRD 过滤、冲突标 conflicted。
    #    不改 Section 2 的 fail-closed/deterministic/atomic mechanic——纯 policy 层（spec：policy
    #    异常由 fail-open wrapper 兜底，不抛主路径）。
    grouped, decisions = LMP.apply_promotion_policy(grouped)

    # 6. Section 6 task 6.2/6.3：usage outcome → effectiveness lifecycle（design 决策#6）
    #    在 promotion 之后应用：active entry 接受 bounded confidence update + contradiction retire；
    #    retired/superseded terminal 优先（lifecycle 显式标记不被 usage 覆盖）。
    _apply_usage_outcomes(grouped, usage_records)

    # 7. 稳定排序 → tuple（byte-stable serialization 前提）；strip 内部 _audit_* 字段
    entries = tuple(
        _entry_for_projection(entry)
        for entry in sorted(grouped.values(), key=lambda e: e["lesson_id"])
    )
    return CatalogSnapshot(
        project_id=project_id,
        entries=entries,
        source_candidate_count=len(candidate_records),
        source_event_count=applied,
        tail_truncated_candidates=cand_report.tail_truncated,
        tail_truncated_events=evt_report.tail_truncated,
        promotion_decisions=tuple(dataclasses.asdict(d) for d in decisions),
    )


def _entry_for_projection(entry: dict) -> dict:
    """把内部 entry dict 规整为 projection-ready：strip ``_audit_*`` 内部字段 + set → sorted tuple。

    Section 3 在 ``_aggregate_candidates`` 注入 ``_audit_corrective_actions`` 做 conflict 检测；
    projection 输出不可带内部字段（byte-stable + schema 清晰）。set 全转 sorted tuple（确定性）。

    P1 #4 例外：``non_applicability_when`` 是 set 内部累加（合并所有等价候选的 boundary），
    但 schema 契约（``ActiveCatalogEntry.non_applicability_when: str``）+ retrieval 子串匹配
    （``_matches_non_applicability`` 接 str）要求 projection 输出是 string。故此处 sorted 后
    ``"; "`` join 为 string（deterministic；空 set → ""，与 schema「candidate 必须非空」契合）。
    """
    out: dict = {}
    for k, v in entry.items():
        if k.startswith("_audit_"):
            continue
        if k == "non_applicability_when":
            # P1 #4：sorted join 为 string（deterministic + 去重 + schema 契约保持）
            out[k] = "; ".join(sorted(v)) if isinstance(v, (set, frozenset)) else str(v)
        elif isinstance(v, (set, frozenset)):
            out[k] = tuple(sorted(v))
        else:
            out[k] = v
    return out


# ════════════════════════════════════════════════════════════════════════
# task 2.4：atomic replacement（temp + fsync + os.replace）
# ════════════════════════════════════════════════════════════════════════
def _serialize_catalog(snapshot: CatalogSnapshot) -> bytes:
    """snapshot → byte-stable JSON bytes（sorted keys + 紧凑分隔符 → byte-identical on same facts）。"""
    catalog = {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "project_id": snapshot.project_id,
        "entries": [dict(e) for e in snapshot.entries],
        "source_candidate_count": snapshot.source_candidate_count,
        "source_event_count": snapshot.source_event_count,
        "tail_truncated_candidates": snapshot.tail_truncated_candidates,
        "tail_truncated_events": snapshot.tail_truncated_events,
        "promotion_decisions": list(snapshot.promotion_decisions),   # Section 3 audit trail
    }
    return json.dumps(catalog, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _atomic_write_catalog(state_dir: str | Path, project_id: str,
                          snapshot: CatalogSnapshot) -> None:
    """task 2.4：temp + fsync + os.replace 原子替换 catalog。

    流程：
        1. 在 catalog/ 同目录写 ``.<project>.json.tmp``（POSIX rename 原子保证要求 src/dst 同 FS）；
        2. fsync temp；
        3. ``os.replace(temp, target)`` 原子替换；
        4. best-effort fsync catalog 目录（防 directory entry 未落盘）。

    crash 在写 temp 中途 → 半写的 temp 未 rename，旧 catalog 完整无损；
    crash 在 rename 后但 dir fsync 前 → rename 已生效（dir entry 可能延迟但 fsync 后必落盘）。
    """
    target = LMS.catalog_path(state_dir, project_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = _serialize_catalog(snapshot)

    # NamedTemporaryFile 用同目录（delete=False 自己管，rename 后清 temp）
    tmp_fd, tmp_path = tempfile.mkstemp(
        prefix=f".{project_id}.", suffix=".json.tmp", dir=str(target.parent))
    try:
        with os.fdopen(tmp_fd, "wb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, target)
        # best-effort dir fsync（POSIX 保证 rename 的 directory entry 落盘）
        try:
            dir_fd = os.open(str(target.parent), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass   # 某些 FS（如 tmpfs）不支持 dir fsync；rename 已生效即可
    except Exception:
        # 清理半写 temp（不污染 catalog 目录）
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ════════════════════════════════════════════════════════════════════════
# task 2.4 + 决策#7：fail-open 公开 API
# ════════════════════════════════════════════════════════════════════════
def project_catalog(state_dir: str | Path, project_id: str) -> CatalogResult:
    """**fail-open**（design 决策#7）：从 facts replay 派生 CatalogSnapshot。

    存储层 corruption → 不 raise，返回 ``CatalogResult(ok=False, degraded_class=...)``。
    调用方据 degraded_class 记 ``learning_memory_degraded`` 继续跑——memory 故障不改 PRD 结果。

    Returns:
        ``CatalogResult(ok=True, snapshot=...)``：replay 成功；
        ``CatalogResult(ok=False, degraded_class="middle_corruption")``：committed history 中部损坏；
        ``CatalogResult(ok=False, degraded_class="storage_error")``：IO 故障。
    """
    try:
        snap = _replay(state_dir, project_id)
        return CatalogResult(ok=True, snapshot=snap)
    except LMS.LessonsCorruptionError as e:
        return CatalogResult(ok=False, degraded_class="middle_corruption", detail=str(e))
    except (OSError, ValueError) as e:
        return CatalogResult(ok=False, degraded_class="storage_error", detail=str(e))


def rebuild_catalog(state_dir: str | Path, project_id: str) -> CatalogResult:
    """**fail-open**：replay + atomic write ``catalog/<project>.json``。

    design 决策#7：fail-closed for memory——corruption 时**绝不**写 partial catalog（旧 catalog 保持
    原样，可继续被读或调用方记 degraded 跳过）。这与「delivery fail-open」配合：memory 状态可能 stale
    但绝不污染；主路径继续跑。

    Returns:
        ``CatalogResult(ok=True, snapshot=...)``：rebuild 成功并写盘；
        ``CatalogResult(ok=False, degraded_class=...)``：replay 失败，旧 catalog 未被覆盖。
    """
    result = project_catalog(state_dir, project_id)
    if result.ok and result.snapshot is not None:
        try:
            _atomic_write_catalog(state_dir, project_id, result.snapshot)
        except (OSError, ValueError) as e:
            return CatalogResult(ok=False, degraded_class="storage_error", detail=str(e))
    # replay 失败：不写 catalog，旧文件保持原样（fail-closed for memory；绝不部分信任）
    return result


def load_catalog_file(state_dir: str | Path, project_id: str) -> dict | None:
    """读 catalog/<project>.json 文件 → dict（不存在 → None）。

    纯读取，不 replay。用于不需要最新 snapshot 的场景（如检索 prompt injection 直接读 projection）。
    catalog 是 rebuildable，故此函数失败 = 调用方触发 ``rebuild_catalog`` 重建。
    """
    p = LMS.catalog_path(state_dir, project_id)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))
