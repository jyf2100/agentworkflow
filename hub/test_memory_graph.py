"""memory_graph 单测——图构建/邻域/Top-K/dormant/冲突对（add-long-term-memory-graph 1.4）。"""
from __future__ import annotations

import memory_graph as MG


def _les(lid, refs=None, tags=None, trigger="", action="do X"):
    return {"lesson_id": lid, "refs": refs or [], "applies_when_tags": tags or [],
            "trigger": trigger, "corrective_action": action}


def test_build_graph_and_neighborhood():
    lessons = [_les("L1", refs=["L2", "acN"]), _les("L2", refs=["L3"]), _les("L3")]
    g = MG.build_graph(lessons)
    assert "L1" in g and "L2" in g and "L3" in g and "acN" in g
    assert g["L2"]["in"] == {"L1"} and g["L1"]["out"] == {"L2", "acN"}
    nb = MG.neighborhood(g, "L1", depth=1)
    assert nb == {"L1", "L2", "acN"}


def test_top_k_keyword_and_centrality():
    lessons = [
        _les("L1", tags=["import-error"], trigger="import foo fails"),
        _les("L2", tags=["ci-red"]),                     # 无关键词命中
        _les("L3", tags=["import-error"]), _les("L4", refs=["L3"]),  # L3 入度 1
        _les("L5", tags=["other"]),
    ]
    g = MG.build_graph(lessons)
    top = MG.top_k(g, "遇到 import-error 问题要怎么处理", k=2)
    assert len(top) == 2
    assert "L1" in top  # trigger 命中 +3 + kw +3 = 6 分


def test_top_k_zero_is_full_baseline():
    g = MG.build_graph([_les("L1"), _les("L2")])
    assert MG.top_k(g, "x", k=0) == ["L1", "L2"]  # k=0 = 全量


def test_dormant_and_revive():
    g = MG.build_graph([_les("L1"), _les("L2"), _les("L3")])
    for _ in range(MG.DORMANT_THRESHOLD):
        g = MG.mark_miss_and_prune(g, selected=["L1"])  # L2 L3 持续未选
    assert g["L2"]["dormant"] is True and g["L1"]["dormant"] is False
    assert "L2" not in MG.top_k(g, "", k=10)            # dormant 不入 top_k
    revived = MG.revive_dormant(g, {"L2"})
    assert "L2" in revived and g["L2"]["dormant"] is False


def test_conflict_detection():
    lessons = [
        _les("A", refs=["ac1"], action="fix by updating config file to use correct path"),
        _les("B", refs=["ac1"], action="fix by updating config file to use correct env var"),
    ]
    conflicts = MG.detect_conflicts(lessons)
    assert ("A", "B") in conflicts                       # 同 ref + 前 20 字同 + 后文异


def test_no_conflict_when_identical():
    lessons = [
        _les("A", refs=["ac1"], action="same action exactly"),
        _les("B", refs=["ac1"], action="same action exactly"),
    ]
    assert MG.detect_conflicts(lessons) == []             # 全文同 → 不冲突（等效）
