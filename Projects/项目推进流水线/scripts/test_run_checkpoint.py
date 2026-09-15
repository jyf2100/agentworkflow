#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_run_checkpoint.py — pa-hardening P0-2：run 级 checkpoint 快照与当日断点续跑推导单测。

覆盖矩阵（方案 §5.1）：
  * flag 门控（PA_CHECKPOINT off = 全部写操作 no-op，零新文件）；
  * mark_done 幂等累积 / actual_stamp 跟踪（inject 自增边界）/ mark_terminal 四态；
  * P2-1 add_cost 累加 + 非法值跳过 + total_cost；
  * 损坏 JSON fail-open（load → None，后续写入自愈）；
  * 产物互证（说 done 但产物缺 → 视为未完成）；
  * 7 段边界 crash 注入矩阵的推导等价物：stages_done → derive_resume_from_stage 各边界
    （含 inject 旁路跳过、空 done 不续、全 done 不续）；
  * maybe_auto_resume 守卫矩阵（interrupted+默认起点才续；completed/halted/running 不续；
    显式起点不抢；inject_prd 不续；flag off 不续）；
  * 不变式守卫：auto-resume 只改 args.from_stage，不触及其它字段（不重投历史 pending PRD 的
    机制边界——resume 语义仅是段起点推导）。

时间注入：固定 now_fn（无系统时间依赖，确定性）。
跑：python3 -m pytest scripts/test_run_checkpoint.py -q
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import run_checkpoint as CK  # noqa: E402

STAMP = "20260915"
NOW = "2026-09-15T03:17:00+00:00"
_now = lambda: NOW  # noqa: E731  固定时间注入（保确定性）


def _on():
    return {"PA_CHECKPOINT": "1"}


def _mk_artifact(state_dir, name: str) -> None:
    (Path(state_dir) / name).write_text("{}", encoding="utf-8")


# ─── flag 门控：off = 零写入零文件 ─────────────────────────────────────────
def test_flag_off_all_writes_noop(tmp_path, monkeypatch):
    monkeypatch.delenv("PA_CHECKPOINT", raising=False)
    assert CK.enabled({"PA_CHECKPOINT": "0"}) is False
    assert CK.mark_done(tmp_path, STAMP, "fetch", now_fn=_now) is None
    assert CK.mark_terminal(tmp_path, STAMP, status="completed", now_fn=_now) is None
    assert CK.add_cost(tmp_path, STAMP, "radar", 0.5, now_fn=_now) is None
    assert not (tmp_path / f"checkpoint_{STAMP}.json").exists()


# ─── mark_done / mark_terminal ─────────────────────────────────────────────
def test_mark_done_accumulates_and_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("PA_CHECKPOINT", "1")
    CK.mark_done(tmp_path, STAMP, "fetch", now_fn=_now)
    CK.mark_done(tmp_path, STAMP, "radar", now_fn=_now)
    CK.mark_done(tmp_path, STAMP, "radar", now_fn=_now)      # 重复 mark 不重复 append
    ck = CK.load_checkpoint(tmp_path, STAMP)
    assert ck["stages_done"] == ["fetch", "radar"]
    assert ck["last_stage"] == "radar"
    assert ck["status"] == "running"


def test_mark_done_tracks_actual_stamp(tmp_path, monkeypatch):
    """inject 自增 stamp 边界：文件名跟 run 起始 stamp，actual_stamp 跟踪自增后值（产物互证用）。"""
    monkeypatch.setenv("PA_CHECKPOINT", "1")
    CK.mark_done(tmp_path, STAMP, "fetch", now_fn=_now)
    CK.mark_done(tmp_path, STAMP, "inject", now_fn=_now, actual_stamp=STAMP + "m")
    ck = CK.load_checkpoint(tmp_path, STAMP)
    assert ck["actual_stamp"] == STAMP + "m"
    assert not (tmp_path / f"checkpoint_{STAMP}m.json").exists()   # 不分叉第二文件


def test_mark_terminal_statuses(tmp_path, monkeypatch):
    monkeypatch.setenv("PA_CHECKPOINT", "1")
    CK.mark_done(tmp_path, STAMP, "fetch", now_fn=_now)
    CK.mark_terminal(tmp_path, STAMP, status="interrupted", reason="boom", now_fn=_now)
    ck = CK.load_checkpoint(tmp_path, STAMP)
    assert ck["status"] == "interrupted" and ck["interrupted_reason"] == "boom"
    CK.mark_terminal(tmp_path, STAMP, status="halted", reason="fuse", now_fn=_now)
    ck = CK.load_checkpoint(tmp_path, STAMP)
    assert ck["status"] == "halted" and ck["tripped_reason"] == "fuse"
    try:
        CK.mark_terminal(tmp_path, STAMP, status="bogus", now_fn=_now)
        raised = False
    except ValueError:
        raised = True
    assert raised


# ─── P2-1 成本聚合 ─────────────────────────────────────────────────────────
def test_add_cost_accumulates_per_stage(tmp_path, monkeypatch):
    monkeypatch.setenv("PA_CHECKPOINT", "1")
    CK.add_cost(tmp_path, STAMP, "radar", 0.1, now_fn=_now)
    CK.add_cost(tmp_path, STAMP, "radar", 0.2, now_fn=_now)
    CK.add_cost(tmp_path, STAMP, "prd", 0.3, now_fn=_now)
    ck = CK.load_checkpoint(tmp_path, STAMP)
    assert abs(ck["cost_usd"]["radar"] - 0.3) < 1e-9
    assert abs(CK.total_cost(ck) - 0.6) < 1e-9


def test_add_cost_skips_invalid_amounts(tmp_path, monkeypatch):
    monkeypatch.setenv("PA_CHECKPOINT", "1")
    for bad in (None, "0.5", True, -1):
        assert CK.add_cost(tmp_path, STAMP, "radar", bad, now_fn=_now) is None
    assert CK.total_cost(CK.load_checkpoint(tmp_path, STAMP)) == 0.0


def test_total_cost_none_and_empty():
    assert CK.total_cost(None) == 0.0
    assert CK.total_cost({"cost_usd": {}}) == 0.0


# ─── 损坏 fail-open + 自愈 ─────────────────────────────────────────────────
def test_load_corrupt_json_returns_none(tmp_path):
    (tmp_path / f"checkpoint_{STAMP}.json").write_text("{not json", encoding="utf-8")
    assert CK.load_checkpoint(tmp_path, STAMP) is None


def test_corrupt_self_heals_on_next_write(tmp_path, monkeypatch):
    monkeypatch.setenv("PA_CHECKPOINT", "1")
    (tmp_path / f"checkpoint_{STAMP}.json").write_text("{not json", encoding="utf-8")
    CK.mark_done(tmp_path, STAMP, "fetch", now_fn=_now)
    ck = CK.load_checkpoint(tmp_path, STAMP)
    assert ck["stages_done"] == ["fetch"]     # 全新重开（不信任损坏文件的 stages_done）


# ─── 产物互证 + resume 推导矩阵 ────────────────────────────────────────────
def _ck_with(state_dir, done, actual=STAMP):
    monkey_env_ck = {"stamp": STAMP, "actual_stamp": actual, "status": "interrupted",
                     "stages_done": list(done), "last_stage": (list(done)[-1] if done else None),
                     "started": NOW, "updated": NOW, "cost_usd": {},
                     "tripped_reason": None, "interrupted_reason": "x"}
    (Path(state_dir) / f"checkpoint_{STAMP}.json").write_text(
        json.dumps(monkey_env_ck, ensure_ascii=False), encoding="utf-8")
    return monkey_env_ck


def test_derive_resume_each_stage_boundary(tmp_path):
    """段边界矩阵：done=[..前k段]（k≥1）→ 推导第 k+1 段（inject 从自动链跳过）。"""
    chain = ["fetch", "radar", "prd", "critic", "dispatch"]
    artifacts = {"fetch": "fetch_20260915.json", "radar": "candidates_20260915.json",
                 "prd": "prd_manifest_20260915.json", "critic": "prd_gate_20260915.json",
                 "dispatch": "dispatch_20260915.json"}
    for name in artifacts.values():
        _mk_artifact(tmp_path, name)
    # k=0（空 done）：无从推断原意图 → None（from_stage 维持默认 radar，等价从头跑，不抢）
    assert CK.derive_resume_from_stage(_ck_with(tmp_path, []), tmp_path) is None
    for k in range(1, len(chain)):
        ck = _ck_with(tmp_path, chain[:k])
        assert CK.derive_resume_from_stage(ck, tmp_path) == chain[k], f"done={chain[:k]}"
    assert CK.derive_resume_from_stage(_ck_with(tmp_path, chain), tmp_path) == "report"


def test_derive_resume_skips_inject_bypass(tmp_path):
    """inject 在 done 里（手动注入 run 中断）→ 从 critic 续（inject 无法自动补 --inject-prd）。"""
    for name in ("fetch_20260915.json", "candidates_20260915.json",
                 "prd_manifest_20260915.json", "prd_gate_20260915.json"):
        _mk_artifact(tmp_path, name)
    ck = _ck_with(tmp_path, ["fetch", "radar", "prd", "inject"])
    assert CK.derive_resume_from_stage(ck, tmp_path) == "critic"


def test_derive_resume_empty_done_returns_none(tmp_path):
    assert CK.derive_resume_from_stage(_ck_with(tmp_path, []), tmp_path) is None


def test_derive_resume_missing_artifact_distrusted(tmp_path):
    """产物互证：checkpoint 说 radar done 但 candidates 文件缺 → 视为未完成，从 radar 重推。"""
    _mk_artifact(tmp_path, "fetch_20260915.json")            # 只有 fetch 产物
    ck = _ck_with(tmp_path, ["fetch", "radar"])              # 声称 radar 也完成
    assert CK.derive_resume_from_stage(ck, tmp_path) == "radar"


def test_derive_resume_report_no_artifact_check(tmp_path):
    """report 无 state 产物（恒可信）——done 含 report 即全完成 → None。"""
    for name in ("fetch_20260915.json", "candidates_20260915.json", "prd_manifest_20260915.json",
                 "prd_gate_20260915.json", "dispatch_20260915.json"):
        _mk_artifact(tmp_path, name)
    assert CK.derive_resume_from_stage(_ck_with(tmp_path, CK.AUTO_CHAIN), tmp_path) is None


# ─── maybe_auto_resume 守卫矩阵 ────────────────────────────────────────────
def _args(**kw):
    base = {"stamp": STAMP, "from_stage": "radar", "inject_prd": None}
    base.update(kw)
    return argparse.Namespace(**base)


def test_auto_resume_interrupted_sets_from_stage(tmp_path, monkeypatch):
    monkeypatch.setenv("PA_AUTO_RESUME", "1")
    _mk_artifact(tmp_path, "fetch_20260915.json")
    _ck_with(tmp_path, ["fetch"])
    logs: list[str] = []
    a = _args()
    assert CK.maybe_auto_resume(tmp_path, a, log_fn=logs.append) is True
    assert a.from_stage == "radar"   # done=[fetch] → 续跑 radar
    assert any("auto-resume" in m for m in logs)


def test_auto_resume_only_touches_from_stage(tmp_path, monkeypatch):
    """不变式守卫：resume 只改 from_stage（段起点推导），不触碰其它 args 字段/不重投历史 pending。"""
    monkeypatch.setenv("PA_AUTO_RESUME", "1")
    _mk_artifact(tmp_path, "fetch_20260915.json")
    _mk_artifact(tmp_path, "candidates_20260915.json")
    _ck_with(tmp_path, ["fetch", "radar"])
    a = _args(dry_run=True, limit=3)
    before = vars(a).copy()
    CK.maybe_auto_resume(tmp_path, a, log_fn=lambda *_: None)
    after = vars(a)
    assert a.from_stage == "prd"
    assert {k: v for k, v in after.items() if k != "from_stage"} == \
           {k: v for k, v in before.items() if k != "from_stage"}


def test_auto_resume_guards(tmp_path, monkeypatch):
    _mk_artifact(tmp_path, "fetch_20260915.json")
    _ck_with(tmp_path, ["fetch"])
    # flag off
    monkeypatch.delenv("PA_AUTO_RESUME", raising=False)
    a = _args()
    assert CK.maybe_auto_resume(tmp_path, a, env={}, log_fn=lambda *_: None) is False
    # 非 interrupted（completed / halted / running）
    monkeypatch.setenv("PA_AUTO_RESUME", "1")
    for status in ("completed", "halted", "running"):
        ck = _ck_with(tmp_path, ["fetch"])
        ck["status"] = status
        (tmp_path / f"checkpoint_{STAMP}.json").write_text(json.dumps(ck), encoding="utf-8")
        a = _args()
        assert CK.maybe_auto_resume(tmp_path, a, log_fn=lambda *_: None) is False
        assert a.from_stage == "radar"
    # 显式非默认起点（用户手动控制）不抢
    _ck_with(tmp_path, ["fetch"])
    a = _args(from_stage="prd")
    assert CK.maybe_auto_resume(tmp_path, a, log_fn=lambda *_: None) is False
    # inject run 不自动续
    a = _args(inject_prd="/tmp/x.md")
    assert CK.maybe_auto_resume(tmp_path, a, log_fn=lambda *_: None) is False
    # 无 checkpoint 文件
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    a = _args()
    assert CK.maybe_auto_resume(empty_dir, a, log_fn=lambda *_: None) is False
