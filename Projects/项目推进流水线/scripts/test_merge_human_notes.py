#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_merge_human_notes.py — 频道人话谱系合并读面单测（OpenSpec harden-human-notes-lineage task 2.2）。

审核 P1-2：notes 归 run 实例（重派 attempt+1 新树 → 旧树死信）与 handoff「同 PRD 跨 run 连续」
承诺冲突。修复后归属键 = queue_item_id（hub 署名 meta.json 回链，state 挂载 pa 可读），读面 =
``<state>/human_notes/qi-<id>/*.md`` 按文件名序合并；meta 缺失/目录不存在 → None（``--human-notes``
零参数 baseline，fail-open 不拦派发）。纯函数面（glob+json+md 拼接），不触 SDK。

跑：python3 -m pytest scripts/test_merge_human_notes.py -q
AAA 结构（Arrange / Act / Assert）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import run_daily  # noqa: E402


def _hub_run_tree(state: Path, project: str, dirname: str, qi: int) -> Path:
    """造一棵 hub 署名 run 树：runs/<proj>/<stamp>_<slug>/<stamp>_<slug>.meta.json（meta_sign 拼法）。"""
    d = state / "runs" / project / dirname
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{dirname}.meta.json").write_text(
        json.dumps({"initiator": "u1", "queue_item_id": qi, "image_tag": "v0.1.36",
                    "written_at": "2026-11-01T10:00:00+00:00"}), encoding="utf-8")
    return d


def _note(qi_dir: Path, name: str, text: str) -> None:
    qi_dir.mkdir(parents=True, exist_ok=True)
    (qi_dir / name).write_text(f"---\nmessage_id: \"{name}\"\n---\n\n{text}\n", encoding="utf-8")


# ════════════════════════════════════════════════════════════════════════
# _queue_item_id_for_stamp：hub meta 回链取谱系键
# ════════════════════════════════════════════════════════════════════════
def test_queue_item_id_via_meta_link(tmp_path, monkeypatch):
    """本 stamp 树的 meta.queue_item_id → 谱系键（F10 署名回链）。"""
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path)
    _hub_run_tree(tmp_path, "proj-a", "20261101_xiu-fu", qi=5)
    assert run_daily._queue_item_id_for_stamp("proj-a", "20261101") == 5


def test_queue_item_id_stamp_prefix_not_fooled_by_redispatch_suffix(tmp_path, monkeypatch):
    """同日重派树 `<stamp>m_*` 不被 `<stamp>_` 前缀误吞——两代 dispatch 各归各键。"""
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path)
    _hub_run_tree(tmp_path, "proj-a", "20261101_aaa", qi=5)     # attempt 1
    _hub_run_tree(tmp_path, "proj-a", "20261101m_bbb", qi=5)    # attempt 2（同 item 重派）
    _hub_run_tree(tmp_path, "proj-a", "20261101m2_ccc", qi=6)   # 另一需求同日两次重派
    assert run_daily._queue_item_id_for_stamp("proj-a", "20261101") == 5
    assert run_daily._queue_item_id_for_stamp("proj-a", "20261101m") == 5
    assert run_daily._queue_item_id_for_stamp("proj-a", "20261101m2") == 6


def test_queue_item_id_absent_meta_is_none(tmp_path, monkeypatch):
    """无 runs 树 / 无匹配目录 / meta 坏 JSON → None（fail-open，调用方零参数 baseline）。"""
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path)
    assert run_daily._queue_item_id_for_stamp("proj-a", "20261101") is None   # 无 runs 根
    d = _hub_run_tree(tmp_path, "proj-a", "20261101_x", qi=5)
    (d / "20261101_x.meta.json").write_text("{坏 json", encoding="utf-8")
    assert run_daily._queue_item_id_for_stamp("proj-a", "20261102") is None   # stamp 不匹配
    assert run_daily._queue_item_id_for_stamp("proj-a", "20261101") is None   # 坏 meta 不连坐


# ════════════════════════════════════════════════════════════════════════
# _merge_human_notes：谱系合并（P1-2 重派连续性主断言）
# ════════════════════════════════════════════════════════════════════════
def test_merge_reads_lineage_dir_across_attempts(tmp_path, monkeypatch):
    """**主断言**：attempt 1 窗口的旧人话在 attempt 2（新 stamp 新树）组装时同树可见——不死信。"""
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path)
    _hub_run_tree(tmp_path, "proj-a", "20261101_xiu-fu", qi=5)    # attempt 1 树（卷上残留）
    _hub_run_tree(tmp_path, "proj-a", "20261101m_xiu-fu", qi=5)   # attempt 2 树（当前 dispatch）
    qi_dir = tmp_path / "human_notes" / "qi-5"
    _note(qi_dir, "1001_old.md", "旧iteration：改用方案B")
    _note(qi_dir, "2002_new.md", "重派后追加：先跳过第2条验收")
    out = run_daily._merge_human_notes("proj-a", "/vault/x/20261101m_xiu-fu.md", "20261101m")
    assert out is not None and Path(out).is_file()
    body = Path(out).read_text(encoding="utf-8")
    assert "改用方案B" in body and "先跳过第2条验收" in body          # 新旧共栖一棵谱系树
    assert body.index("1001_old") < body.index("2002_new")           # 文件名序（时间序）
    assert "qi-5" not in out and Path(out).name.startswith("human_notes_merged_proj-a_")


def test_merge_none_when_no_lineage_or_empty(tmp_path, monkeypatch):
    """meta 缺失 / 谱系目录不存在 / 目录空 → None（--human-notes 零参数 baseline，不拦派发）。"""
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path)
    prd = "/vault/x/20261101m_xiu-fu.md"
    assert run_daily._merge_human_notes("proj-a", prd, "20261101m") is None      # 无 meta 回链
    _hub_run_tree(tmp_path, "proj-a", "20261101m_xiu-fu", qi=5)
    assert run_daily._merge_human_notes("proj-a", prd, "20261101m") is None      # 有键无目录
    (tmp_path / "human_notes" / "qi-5").mkdir(parents=True)
    assert run_daily._merge_human_notes("proj-a", prd, "20261101m") is None      # 目录空
