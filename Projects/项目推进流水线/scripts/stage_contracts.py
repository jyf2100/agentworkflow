#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""stage_contracts.py — persona/stage 输出契约：定义、校验、诊断、纠正重试提示。

横切所有走 run_persona 的 stage。机械可验证（字段存在性 / 受控值 / 类型），非 LLM 语义
判断——故无需 cross-PRD recurrence，可当轮即时回流。与 cross-prd-learning-memory（领域
经验，慢回路）是两个不同对象。

fail-open 不变量（对齐 cross-prd-learning-memory design 决策#7）：契约层自身故障（registry
读不到 / validator 抛异常 / render 失败）不改 stage 终态，降级回现状（不校验、不重试、按现有
.get() 走）。重试成功 = 本该成功；重试失败 = 照旧 raise/降级。契约层是纯增益，不是新依赖。

核心 API：
    Issue(field, severity, diagnosis)              — 一条契约违反（frozen）
    Contract.validate(payload) -> list[Issue]      — stage 输出契约（子类实现）
    CONTRACTS[stage] / get_contract(stage)         — 注册表
    validate_stage(stage, payload) -> list[Issue]  — fail-open wrapper（registry 缺/异常 → []）
    render_repair_hint(issues, bad_excerpt, attempt) -> str  — 诊断→重试提示（空/全warning → ""）

第一版硬契约（change 2026-07-28）：
    CONTRACTS[critic] = {verdict ∈{pass,drop,revise}, prd_path}
    CONTRACTS[prd]    = {prds[i].path}
其余字段 = warning（保持现状 .get() 宽容语义，不改行为）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Issue:
    """一条契约违反。field=字段路径，severity∈{error,warning}，diagnosis=人读+喂重试提示。"""
    field: str
    severity: str
    diagnosis: str


class Contract(Protocol):
    """stage 输出契约：纯函数 validate，malformed payload 不抛（返 error Issue）。"""
    def validate(self, payload: object) -> list[Issue]: ...


# ── 注册表 ─────────────────────────────────────────────────────────
CONTRACTS: dict[str, Contract] = {}


def get_contract(stage: str) -> Contract | None:
    """取 stage 的契约；未注册返 None（= 该 stage 第一版无契约，跳过校验=现状行为）。"""
    return CONTRACTS.get(stage)


def register(stage: str, contract: Contract) -> None:
    CONTRACTS[stage] = contract


# ── fail-open wrapper ──────────────────────────────────────────────
def validate_stage(stage: str, payload: object) -> list[Issue]:
    """对已注册 stage 校验 payload。fail-open：registry 缺 / validator 异常 → []，
    绝不抛主路径（契约层故障不改 stage 终态，降级回现状）。"""
    contract = CONTRACTS.get(stage)
    if contract is None:
        return []
    try:
        return list(contract.validate(payload) or [])
    except Exception:
        return []


# ── 诊断 → 重试提示 ─────────────────────────────────────────────────
def render_repair_hint(issues: list[Issue], bad_excerpt: str = "", *, attempt: int = 1) -> str:
    """把 error Issue 渲染成「中颗粒度」纠正提示（指明字段路径 + 问题，不给答案）。
    空 issues / 全 warning → ""（no-op，不触发重试）。attempt≥2 加「第 N 次」提醒。
    bad_excerpt（可选）截 500 字附上供定位。"""
    errors = [i for i in (issues or []) if i.severity == "error"]
    if not errors:
        return ""
    lines = ["## ⚠️ 上轮输出违反契约，请修正后重新输出完整合规 JSON（禁止只给补丁）："]
    if attempt > 1:
        lines.append(f"（这是第 {attempt} 次尝试，你上次已被告知同样问题仍未改正，请逐字核对）")
    for i in errors:
        lines.append(f"- 字段 `{i.field}`：{i.diagnosis}")
    if bad_excerpt:
        lines.append(f"\n上轮输出片段（供定位）：\n```\n{bad_excerpt[:500]}\n```")
    return "\n".join(lines) + "\n"


# ── 契约定义（第一版硬契约）─────────────────────────────────────────
_CRITIC_VERDICT_VALUES = ("pass", "drop", "revise")


class CriticContract:
    """critic（pa-prd-critic）输出契约。
    error: verdict（必填 + ∈{pass,drop,revise}）、prd_path（非空）。
    其余字段 warning（保持现状宽容语义）。"""

    def validate(self, payload: object) -> list[Issue]:
        if not isinstance(payload, dict):
            return [Issue("payload", "error", "critic 输出必须是 JSON 对象")]
        issues: list[Issue] = []
        verdict = payload.get("verdict")
        if verdict is None:
            issues.append(Issue("verdict", "error", "缺 verdict 字段（必须 ∈{pass,drop,revise}）"))
        elif verdict not in _CRITIC_VERDICT_VALUES:
            issues.append(Issue("verdict", "error",
                                f"verdict 必须 ∈{{pass,drop,revise}}，实际 {verdict!r}"))
        if not payload.get("prd_path"):
            issues.append(Issue("prd_path", "error", "缺 prd_path 字段（非空，指向被审 PRD 文件）"))
        return issues


class PrdContract:
    """prd（pa-prd）输出契约。
    error: prds[i].path（每个 prd 必填非空）。空 prds = 本次无产出（合法，非违反）。"""

    def validate(self, payload: object) -> list[Issue]:
        if not isinstance(payload, dict):
            return [Issue("payload", "error", "prd 输出必须是 JSON 对象")]
        prds = payload.get("prds")
        if prds is None:
            return [Issue("prds", "error", "缺 prds 字段（prd manifest 必须含 prds 数组）")]
        if not isinstance(prds, list):
            return [Issue("prds", "error", "prds 必须是数组")]
        issues: list[Issue] = []
        for idx, prd in enumerate(prds):
            if not isinstance(prd, dict):
                issues.append(Issue(f"prds[{idx}]", "error", f"prds[{idx}] 必须是对象"))
                continue
            if not prd.get("path"):
                issues.append(Issue(f"prds[{idx}].path", "error",
                                    f"prds[{idx}] 缺 path 字段（非空，指向生成的 PRD 文件）"))
        return issues


# 注册第一版契约
register("critic", CriticContract())
register("prd", PrdContract())
