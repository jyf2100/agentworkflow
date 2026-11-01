"""长程记忆引用图——lessons/repo_facts 的邻域 Top-K + dormant 修剪 + 冲突对（add-long-term-memory-graph）。

本体（change design）：引用图 = lessons/facts 间的有向结构关系（frontmatter refs 为边）。
邻域 = depth 步可达子图。中心度 = 入度。Dormant = 持续未选中降级（不删——同一性保持）。
flag PA_MEMORY_GRAPH 默认 off = 全量注入 byte-identical。
"""
from __future__ import annotations

import os
import re
import sys
from collections import defaultdict
from pathlib import Path

TOP_K_DEFAULT = 3
DORMANT_THRESHOLD = 10


def _flag_on() -> bool:
    return os.environ.get("PA_MEMORY_GRAPH", "").strip().lower() in ("1", "true", "yes", "on")


def _top_k() -> int:
    try:
        return int(os.environ.get("PA_LESSONS_TOP_K") or TOP_K_DEFAULT)
    except ValueError:
        return TOP_K_DEFAULT


def build_graph(lessons: list[dict]) -> dict:
    """从 lessons 的 frontmatter refs 构建邻接表。返回 {node_id: {in: set, out: set, dormant: bool, miss: int}}。"""
    graph: dict[str, dict] = {}
    for les in lessons:
        lid = les.get("lesson_id") or les.get("id") or ""
        if not lid:
            continue
        refs = les.get("refs") or []
        node = graph.setdefault(lid, {"in": set(), "out": set(), "dormant": False, "miss": 0, "data": les})
        for ref in refs:
            node["out"].add(str(ref))
            graph.setdefault(str(ref), {"in": set(), "out": set(), "dormant": False, "miss": 0, "data": {}})
            graph[str(ref)]["in"].add(lid)
    return graph


def top_k(graph: dict, prd_text: str, k: int | None = None) -> list[str]:
    """按中心度 + 关键词匹配选 Top-K（机械——in_degree × 2 + keyword_match × 3）。"""
    if k is None:
        k = _top_k()
    if k <= 0:
        return list(graph.keys())  # k=0 = 全量注入 baseline

    def score(lid: str) -> int:
        node = graph[lid]
        if node["dormant"]:
            return -1  # dormant 不选（但可被邻域复活）
        data = node.get("data") or {}
        kw = 0
        for tag in (data.get("applies_when_tags") or []):
            if str(tag).lower() in prd_text.lower():
                kw += 1
        trigger = str(data.get("trigger") or "")
        if trigger and trigger[:20].lower() in prd_text.lower():
            kw += 2
        return len(node["in"]) * 2 + kw * 3

    active = [lid for lid in graph if not graph[lid]["dormant"]]
    return sorted(active, key=score, reverse=True)[:k]


def mark_miss_and_prune(graph: dict, selected: list[str]) -> dict:
    """dispatch 后：未选中的 miss+1，达到阈值降级 dormant；选中的 miss 清零。"""
    for lid in graph:
        if lid in selected:
            graph[lid]["miss"] = 0
        else:
            graph[lid]["miss"] += 1
            if graph[lid]["miss"] >= DORMANT_THRESHOLD:
                graph[lid]["dormant"] = True
    return graph


def revive_dormant(graph: dict, neighborhood_ids: set[str]) -> set[str]:
    """引用图邻域命中 dormant → 复活（miss 清零，回 active）。返回复活集。"""
    revived = set()
    for lid in neighborhood_ids:
        if lid in graph and graph[lid]["dormant"]:
            graph[lid]["dormant"] = False
            graph[lid]["miss"] = 0
            revived.add(lid)
    return revived


def neighborhood(graph: dict, start: str, depth: int = 1) -> set[str]:
    """BFS depth 步可达子图（含 start）。"""
    visited = {start}
    frontier = [start]
    for _ in range(depth):
        next_frontier = []
        for node in frontier:
            for neighbor in graph.get(node, {}).get("out", set()) | graph.get(node, {}).get("in", set()):
                if neighbor not in visited:
                    visited.add(neighbor)
                    next_frontier.append(neighbor)
        frontier = next_frontier
    return visited


def detect_conflicts(lessons: list[dict]) -> list[tuple[str, str]]:
    """同验收点引用 + action 前 20 字同但后文异 → 冲突对。返回 (id_a, id_b) 列表。"""
    by_ref: dict[str, list[dict]] = defaultdict(list)
    for les in lessons:
        for ref in (les.get("refs") or []):
            by_ref[str(ref)].append(les)
    conflicts = []
    for ref, group in by_ref.items():
        if len(group) < 2:
            continue
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                ca = str(a.get("corrective_action") or "")[:20]
                cb = str(b.get("corrective_action") or "")[:20]
                if ca and cb and ca == cb:
                    if str(a.get("corrective_action")) != str(b.get("corrective_action")):
                        conflicts.append((a.get("lesson_id", "?"), b.get("lesson_id", "?")))
    return conflicts
