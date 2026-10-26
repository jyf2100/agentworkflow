#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_dispatch_handoff.py — add-dev-handoff-artifact 组4：编排器（TDD，先红后绿）。

覆盖（tasks 4.1/4.2/4.3/4.4 的可单测面）：
    - ``_store_iteration_handoff``：dev 亲笔交接 → content-addressed artifact + journal 事件
      ``iteration_handoff``（payload 引用 digest/size/type=handoff/path；adopted_from_snapshot 标记
      收养路径）；None → 无事件（正常缺失）；同 content 重存同 path/digest（reconcile 幂等锚——
      crash 重放不双份）；store/emit 失败吞异常（观测层契约，不拖垮 dispatch）。
    - ``_dev_cmd`` 透传：``expect_handoff`` → ``--expect-handoff``；``handoff_artifact``+``handoff_digest``
      → 成对透传（round≥2 注入）；baseline 缺省 → cmd 无任何 handoff 参数（回归锚）。
    - ``ArtifactKind.HANDOFF`` 注册 + ``artifact_store.store(kind="handoff")`` 可用。
    - preflight 依赖：``dev_handoff`` ⇒ ``journal_shadow``（journal 引用是断点③跨进程收养的载体）。

dispatch_one 内的插入点接线（_run_dev_agent 后分支 + 收养分支）由 6.1 端到端冒烟覆盖
（巨函数，repo 惯例单测只锁纯函数面——同 test_dev_cmd_retry 的边界声明）。跑：
python3 -m pytest scripts/test_dispatch_handoff.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import artifact_store  # noqa: E402
import handoff  # noqa: E402
import loop_state  # noqa: E402
import run_daily  # noqa: E402
from coordinator import preflight  # noqa: E402
from feature_flags import LoopFlags  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_dev_model_routing(tmp_path, monkeypatch):
    """隔离 dev model 路由（同 test_dev_cmd_retry：env 清 + config 指向不存在）。"""
    monkeypatch.delenv("PA_DEV_MODEL", raising=False)
    monkeypatch.setattr(run_daily.model_routing, "_DEFAULT_PATH", tmp_path / "isolated-empty.json")


_H = {"done": ["r1 完成"], "in_progress": [], "blockers": [], "tried_and_failed": [],
      "next": ["N1"], "rationale": "why"}


class _FakeSJ:
    """最小 ShadowJournal 替身：enabled + emit 落内存 list（payload-only 事件面）。"""

    def __init__(self):
        self.enabled = True
        self.events: list[tuple] = []

    def emit(self, event_type, iter_id, prd_id, payload=None):
        self.events.append((event_type, iter_id, prd_id, payload or {}))


# ─── 4.1 正常路径：store + 事件 ─────────────────────────────────────────
def test_store_iteration_handoff_artifact_and_event(tmp_path):
    sj = _FakeSJ()
    ref = run_daily._store_iteration_handoff(sj, "it-1", "prd-1", tmp_path, _H, round_n=1)
    assert ref is not None and ref["digest"].startswith("sha256:")
    p = tmp_path / ref["path"]                 # ArtifactRef.path 相对 root
    assert p.exists()
    assert json.loads(p.read_text(encoding="utf-8"))["done"] == ["r1 完成"]
    et, it, pid, pl = sj.events[-1]
    assert et == "iteration_handoff" and it == "it-1" and pid == "prd-1"
    assert pl["digest"] == ref["digest"] and pl["size"] == ref["size"]
    assert pl["type"] == "handoff" and pl["path"] == ref["path"]
    assert pl["adopted_from_snapshot"] is False and pl["round"] == 1


def test_store_digest_matches_artifact_store_format(tmp_path):
    """ref.digest ⇄ dev-agent 注入侧 --handoff-digest 校验闭环（同格式同值）。"""
    sj = _FakeSJ()
    ref = run_daily._store_iteration_handoff(sj, "it", "prd", tmp_path, _H)
    raw = (tmp_path / ref["path"]).read_text(encoding="utf-8")
    assert handoff.verify_digest(raw, ref["digest"]) is True


# ─── 4.2 收养路径 + 正常缺失 ────────────────────────────────────────────
def test_store_adopted_marks_snapshot(tmp_path):
    """收养（dev_killed 后 run_dir 快照）→ 事件 adopted_from_snapshot=true。"""
    sj = _FakeSJ()
    ref = run_daily._store_iteration_handoff(sj, "it", "prd", tmp_path, _H,
                                             round_n=2, adopted=True)
    assert ref is not None
    assert sj.events[-1][3]["adopted_from_snapshot"] is True


def test_store_none_handoff_no_event(tmp_path):
    """无 handoff（dev_out 缺 + 无快照）→ 无 artifact 无事件（正常缺失，非错误）。"""
    sj = _FakeSJ()
    assert run_daily._store_iteration_handoff(sj, "it", "prd", tmp_path, None) is None
    assert sj.events == []


# ─── 4.4 幂等 + 观测层契约 ──────────────────────────────────────────────
def test_store_idempotent_same_content_same_path(tmp_path):
    """同 content 重存 → 同 path/digest（content-addressed 去重；crash 重放不双份）。"""
    sj1, sj2 = _FakeSJ(), _FakeSJ()
    r1 = run_daily._store_iteration_handoff(sj1, "it", "prd", tmp_path, _H)
    r2 = run_daily._store_iteration_handoff(sj2, "it", "prd", tmp_path, _H)
    assert r1["path"] == r2["path"] and r1["digest"] == r2["digest"]


def test_store_failure_swallowed(tmp_path, monkeypatch):
    """store/emit 失败 → None + 无事件（观测层契约：handoff 是增补非判据，不拖垮 dispatch）。"""
    sj = _FakeSJ()

    def _boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(run_daily.artifact_store, "store", _boom)
    assert run_daily._store_iteration_handoff(sj, "it", "prd", tmp_path, _H) is None
    assert sj.events == []


# ─── 4.3 _dev_cmd 透传 ──────────────────────────────────────────────────
def _cmd(**kw) -> list:
    return run_daily._dev_cmd({"conda_env": ""}, "/tmp/x.prd", "main", "/tmp/src", **kw)


def test_dev_cmd_baseline_no_handoff_args():
    """回归锚：baseline 缺省 → cmd 无任何 handoff 参数（与 pre-change 零差异）。"""
    cmd = _cmd()
    for f in ("--expect-handoff", "--handoff-artifact", "--handoff-digest"):
        assert f not in cmd, f"baseline cmd 不应含 {f}: {cmd}"


def test_dev_cmd_expect_handoff_flag():
    cmd = _cmd(expect_handoff=True)
    assert "--expect-handoff" in cmd


def test_dev_cmd_handoff_injection_pair():
    dg = "sha256:" + "0" * 64
    cmd = _cmd(handoff_artifact="/a/h.json", handoff_digest=dg)
    assert cmd[cmd.index("--handoff-artifact") + 1] == "/a/h.json"
    assert cmd[cmd.index("--handoff-digest") + 1] == dg


# ─── kind 注册 + preflight 依赖 ─────────────────────────────────────────
def test_artifact_kind_handoff_registered():
    assert loop_state.ArtifactKind.HANDOFF.value == "handoff"


def test_artifact_store_accepts_handoff_kind(tmp_path):
    ref = artifact_store.store(tmp_path, '{"done": []}', kind="handoff", sensitivity="internal")
    assert ref.kind == "handoff"


def test_preflight_dev_handoff_requires_journal_shadow():
    """dev_handoff on 但 journal_shadow off → preflight 阻断（journal 引用是断点③跨进程载体）。"""
    res = preflight(LoopFlags(dev_handoff=True))
    assert res.ok is False
    assert any("dev_handoff" in v for v in res.blocked.violations)
    assert preflight(LoopFlags(journal_shadow=True, dev_handoff=True)).ok is True
