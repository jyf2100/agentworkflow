#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_handoff.py — add-dev-handoff-artifact 组1：handoff 契约纯函数（TDD，先红后绿）。

覆盖（tasks 1.1/1.2/1.3 + 2.1/2.2 的纯函数面）：
    - parse_handoff_block：从 assistant 回复提取 ```handoff``` 围栏 JSON；坏 JSON → None；
      超长字段截断（列表项 ≤200 / rationale ≤500）；多块取最新；总量 >4KB 拒收
    - validate_handoff：D1 契约字段规整；缺失字段补空值（不伪造内容）；未知字段丢弃
    - write_snapshot / load_snapshot：run_dir/handoff-latest.json 原子写（tmp+rename）；无 run_dir 不炸
    - absorb_assistant_text：AssistantMessage 文本流 → state["handoff_latest"] 持最新块；无块保持
    - digest_of：与 artifact_store.compute_digest 同格式（控制面 ref.digest ⇄ dev-agent 校验闭环）
    - render_block：注入块固定框架（untrusted 标记）

纯 stdlib 零 SDK 导入（同 evidence/slug_utils 既定模式）。跑：python3 -m pytest scripts/test_handoff.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import handoff  # noqa: E402
# digest 格式一致性断言用（handoff.digest_of ⇄ artifact_store.compute_digest）
import artifact_store  # noqa: E402


# ─── 1.1 parse_handoff_block ───────────────────────────────────────────
def test_parse_extracts_fenced_json_block():
    """assistant 回复含单个 ```handoff``` 围栏 → 解析出 D1 契约 dict。"""
    text = ("我先分析一下……\n"
            "```handoff\n"
            '{"done": ["A 完成"], "next": ["做 B"]}\n'
            "```\n"
            "继续干活")
    got = handoff.parse_handoff_block(text)
    assert got is not None
    assert got["done"] == ["A 完成"]
    assert got["next"] == ["做 B"]
    # 缺失字段补空值（不伪造）
    assert got["in_progress"] == []
    assert got["blockers"] == []
    assert got["tried_and_failed"] == []
    assert got["rationale"] == ""


def test_parse_ignores_other_fenced_blocks():
    """其他围栏语言（```python 等）不误取；无 handoff 块 → None。"""
    text = "```python\nprint('hi')\n```\n\n```json\n{\"done\": [\"x\"]}\n```"
    assert handoff.parse_handoff_block(text) is None
    assert handoff.parse_handoff_block("纯文本无围栏") is None
    assert handoff.parse_handoff_block("") is None


def test_parse_bad_json_returns_none():
    """围栏内坏 JSON → None（静默跳过，下个检查点补）。"""
    text = "```handoff\n{done: [未加引号的键]}\n```"
    assert handoff.parse_handoff_block(text) is None


def test_parse_multiple_blocks_takes_latest():
    """多个 handoff 块 → 取最新（dev 阶段性更新，末版为准）。"""
    text = ('```handoff\n{"done": ["旧版"]}\n```\n'
            "中间干了些活\n"
            '```handoff\n{"done": ["新版"]}\n```')
    got = handoff.parse_handoff_block(text)
    assert got is not None and got["done"] == ["新版"]


def test_parse_truncates_overlong_items():
    """列表项 >200 / rationale >500 → 截断到上限（防叙事倾泻）。"""
    long_item = "x" * 500
    long_rationale = "r" * 900
    got = handoff.parse_handoff_block(
        f'```handoff\n{{"done": ["{long_item}"], "rationale": "{long_rationale}"}}\n```')
    assert got is not None
    assert len(got["done"][0]) <= handoff.ITEM_MAX
    assert got["done"][0] == "x" * handoff.ITEM_MAX
    assert len(got["rationale"]) <= handoff.RATIONALE_MAX


def test_parse_total_over_4kb_rejected():
    """规整后总量 >4KB → 整块拒收（None）——防污染 prompt。"""
    many = [f"handoff-item-{i:04d}" for i in range(400)]   # 每项 ≤200 合法，但总量远超 4KB
    payload = json.dumps({"done": many}, ensure_ascii=False)
    assert len(payload.encode("utf-8")) > handoff.TOTAL_MAX
    got = handoff.parse_handoff_block(f"```handoff\n{payload}\n```")
    assert got is None


# ─── 1.2 validate_handoff ──────────────────────────────────────────────
def test_validate_fills_missing_fields_empty_never_fabricates():
    """缺失字段补空值——补的是空容器/空串，绝不伪造内容。"""
    got = handoff.validate_handoff({"done": ["仅有一个字段"]})
    assert got == {"done": ["仅有一个字段"], "in_progress": [], "blockers": [],
                   "tried_and_failed": [], "next": [], "rationale": ""}
    # 全空输入 → 全空契约（诚实缺失）
    assert handoff.validate_handoff({}) == {"done": [], "in_progress": [], "blockers": [],
                                           "tried_and_failed": [], "next": [], "rationale": ""}


def test_validate_drops_unknown_fields_and_non_list():
    """未知字段丢弃（契约封闭）；列表字段给非 list → 空列表（不猜形状）；None 项剔除。"""
    got = handoff.validate_handoff({"done": ["a", None, "b"], "next": "不是列表",
                                    "hacker_field": "should drop", "rationale": None})
    assert got["done"] == ["a", "b"]
    assert got["next"] == []
    assert "hacker_field" not in got
    assert got["rationale"] == ""


def test_validate_non_dict_returns_none():
    assert handoff.validate_handoff(None) is None
    assert handoff.validate_handoff(["list"]) is None
    assert handoff.validate_handoff("str") is None


# ─── 2.1/2.2 absorb_assistant_text + write_snapshot ────────────────────
def test_absorb_updates_state_latest_block():
    """AssistantMessage 文本流经 absorb → state["handoff_latest"] 持最新块。"""
    state = {"handoff_latest": None}
    r1 = handoff.absorb_assistant_text(state, '阶段性：```handoff\n{"done": ["v1"]}\n```')
    assert r1 is not None and state["handoff_latest"]["done"] == ["v1"]
    r2 = handoff.absorb_assistant_text(state, '更新：```handoff\n{"done": ["v2"]}\n```')
    assert r2 is not None and state["handoff_latest"]["done"] == ["v2"]


def test_absorb_no_block_keeps_previous():
    """无块文本 → state 不动（保持上一最新块）；坏块同样不动。"""
    state = {"handoff_latest": {"done": ["v1"], "in_progress": [], "blockers": [],
                                "tried_and_failed": [], "next": [], "rationale": ""}}
    assert handoff.absorb_assistant_text(state, "普通文本无块") is None
    assert state["handoff_latest"]["done"] == ["v1"]
    assert handoff.absorb_assistant_text(state, "```handoff\n{broken\n```") is None
    assert state["handoff_latest"]["done"] == ["v1"]


def test_absorb_missing_handoff_key_stays_none():
    """无块全程 → state["handoff_latest"] 保持 None（诚实缺失基线）。"""
    state = {"handoff_latest": None}
    handoff.absorb_assistant_text(state, "第一轮\n第二轮\n都没有块")
    assert state["handoff_latest"] is None


def test_write_snapshot_atomic_and_loadable(tmp_path):
    """解析到新 handoff → 原子写 run_dir/handoff-latest.json（tmp+rename）；可读回。"""
    run_dir = tmp_path / "state" / "runs"
    h = {"done": ["快照内容"], "in_progress": [], "blockers": [],
         "tried_and_failed": [], "next": ["N1"], "rationale": "why"}
    assert handoff.write_snapshot(run_dir, h) is True
    snap = run_dir / "handoff-latest.json"
    assert snap.exists()
    assert handoff.load_snapshot(run_dir) == h
    # 目录内无 .tmp 残留（rename 干净）
    assert list(run_dir.glob("*.tmp")) == []
    # 二次写覆盖为最新
    h2 = {**h, "done": ["第二版"]}
    assert handoff.write_snapshot(run_dir, h2) is True
    assert handoff.load_snapshot(run_dir)["done"] == ["第二版"]


def test_write_snapshot_no_dir_target_unwritable_skips():
    """无 run_dir / 不可写 → 返回 False 不炸（快照尽力而为，不掩主流程）。"""
    assert handoff.write_snapshot(Path("/nonexistent/definitely/not/here"), {"done": []}) is False


def test_absorb_with_run_dir_writes_snapshot(tmp_path):
    """absorb 带 run_dir → 同步落快照（K-turn 检查点路径，断点③兜底）。"""
    state = {"handoff_latest": None}
    handoff.absorb_assistant_text(state, '```handoff\n{"done": ["落盘"]}\n```', run_dir=tmp_path)
    assert handoff.load_snapshot(tmp_path)["done"] == ["落盘"]


def test_load_snapshot_missing_returns_none(tmp_path):
    assert handoff.load_snapshot(tmp_path) is None


# ─── digest / render（组3 读侧的纯函数面）───────────────────────────────
def test_digest_of_matches_artifact_store_format():
    """digest_of 与 artifact_store.compute_digest 同格式同值（编排器 ref.digest ⇄ dev-agent 校验闭环）。"""
    for sample in ("", "hello 交接", '{"done": ["x"]}'):
        assert handoff.digest_of(sample) == artifact_store.compute_digest(sample.encode("utf-8"))
        assert handoff.digest_of(sample).startswith("sha256:")


def test_verify_digest_ok_and_mismatch():
    content = '{"done": ["a"]}'
    d = handoff.digest_of(content)
    assert handoff.verify_digest(content, d) is True
    assert handoff.verify_digest('{"done": ["tampered"]}', d) is False
    assert handoff.verify_digest(content, "sha256:" + "0" * 64) is False


def test_render_block_fixed_frame():
    """注入块渲染：固定标记框架 + untrusted 边界声明（仅上下文，非判据）。"""
    block = handoff.render_block('{"done": ["x"]}')
    assert "上一轮 dev 交接" in block
    assert "仅上下文" in block and "非判据" in block
    assert '{"done": ["x"]}' in block
    assert block.startswith("\n\n## ")   # 独立段落块（与 feedback_block 同构）
