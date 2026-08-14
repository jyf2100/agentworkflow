#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_graph_worktree.py — worktree MechanicalNode 测试（任务 3.5d）。

验证（对齐 dispatch_one L2149-2161）：
① 配置（KIND_MECHANICAL, stage=dispatch）
② 成功 → _worktree_abs 覆盖为 wt 路径（<repo>/.worktrees/<stamp>-<slug>，dev cwd 用）
③ _run_capture RuntimeError → terminal=fail（基建异常升人工，L2159-2161）
④ wt.exists → git worktree remove --force 调（幂等，L2153-2155）
⑤ log_file 不存在 → mkdir parent（L2150-2151）
"""
import os
import sys
import subprocess
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import graph_pa_nodes as GN


_BASE = {"run_id": "r", "stamp": "20260811", "config": {},
         "_slug": "x", "_worktree_abs": "/repo", "_base": "main", "_dev_log_file": None}


def _mock_capture(monkeypatch, raises=None):
    import run_daily
    if raises:
        def boom(*a, **kw):
            raise raises
        monkeypatch.setattr(run_daily, "_run_capture", boom)
    else:
        monkeypatch.setattr(run_daily, "_run_capture", lambda *a, **kw: (0, "", ""))


# ── 配置 ────────────────────────────────────────────────────────────────
def test_worktree_config():
    assert GN.node_worktree._kind is GN.KIND_MECHANICAL
    assert GN.node_worktree._cfg["stage"] == "dispatch"


# ── 成功（覆盖 _worktree_abs）─────────────────────────────────────────────
def test_worktree_success_overrides_worktree_abs(monkeypatch, tmp_path):
    repo = tmp_path / "repo"; repo.mkdir()
    _mock_capture(monkeypatch)
    s = dict(_BASE); s["_worktree_abs"] = str(repo)
    upd = GN.node_worktree(s)
    expected_wt = str(repo / ".worktrees" / "20260811-x")
    assert upd["_worktree_abs"] == expected_wt
    assert not upd.get("terminal")


def test_worktree_fail_terminal(monkeypatch):
    _mock_capture(monkeypatch, raises=RuntimeError("git worktree add 失败"))
    upd = GN.node_worktree(dict(_BASE))
    assert upd["terminal"] == "fail"
    assert upd["_exit_status"] == "fail"
    assert "建 worktree 失败" in upd["_skip_reason"]


# ── 幂等 remove（wt.exists）──────────────────────────────────────────────
def test_worktree_existing_removed(monkeypatch, tmp_path):
    repo = tmp_path / "repo"; repo.mkdir()
    wt = repo / ".worktrees" / "20260811-x"; wt.mkdir(parents=True)   # 旧 wt 存在
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: calls.append(a[0]))
    _mock_capture(monkeypatch)
    s = dict(_BASE); s["_worktree_abs"] = str(repo)
    GN.node_worktree(s)
    assert calls                                                   # git worktree remove --force 调
    assert any("remove" in str(c) for c in calls[0])


def test_worktree_no_remove_when_absent(monkeypatch, tmp_path):
    """wt 不存在 → 不调 subprocess.run remove（避免无谓 git 调用）。"""
    repo = tmp_path / "repo"; repo.mkdir()
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: calls.append(a[0]))
    _mock_capture(monkeypatch)
    s = dict(_BASE); s["_worktree_abs"] = str(repo)
    GN.node_worktree(s)
    assert calls == []                                             # wt 不存在 → 不 remove


def test_worktree_log_file_mkdir(monkeypatch, tmp_path):
    """_dev_log_file 不存在 → mkdir parent（对齐 L2150-2151）。"""
    repo = tmp_path / "repo"; repo.mkdir()
    log_file = tmp_path / "runs" / "deep" / "x.log"                # parent 不存在
    _mock_capture(monkeypatch)
    s = dict(_BASE); s["_worktree_abs"] = str(repo); s["_dev_log_file"] = str(log_file)
    GN.node_worktree(s)
    assert log_file.parent.exists()                                # mkdir parents


# ── 回归：state._dev_log_file(str) → 运行期 Path（langgraph state 序列化 vs run_daily 签名要 Path）──
def test_worktree_passes_path_to_run_capture(monkeypatch, tmp_path):
    """回归（切轨真跑暴露）：state._dev_log_file 是 str（langgraph state 序列化要求，aggregate
    _build_dispatch_shell L229 存 str），worktree op 必须转 Path 传 run_daily._run_capture（签名要
    Path；内部 log_file.parent.mkdir 在 str 上崩 'str' object has no attribute 'parent'）。
    旧测 _mock_capture 替换整个 _run_capture 故漏（mock 不调 .parent）；真跑 dispatch 才暴露。
    dev/dev_post/publication 同款经 _state_log_path helper 同治。"""
    import run_daily
    repo = tmp_path / "repo"; repo.mkdir()
    log_file = tmp_path / "runs" / "x.log"
    received = {}

    def spy(*a, **kw):
        received["log_file"] = a[4] if len(a) > 4 else kw.get("log_file")   # _run_capture 第 5 参
        return (0, "", "")
    monkeypatch.setattr(run_daily, "_run_capture", spy)
    s = dict(_BASE); s["_worktree_abs"] = str(repo); s["_dev_log_file"] = str(log_file)
    GN.node_worktree(s)
    assert isinstance(received["log_file"], Path), \
        f"_run_capture 须收 Path 非 str（实际 {type(received.get('log_file')).__name__}）"


def test_state_log_path_helper():
    """_state_log_path：str→Path（langgraph state 序列化值）/ None→None / 缺 key→None。
    graph 轨固有边界单点（4 消费点共用：dev/dev_post/worktree/publication）。"""
    assert GN._state_log_path({"_dev_log_file": "/a/b.log"}) == Path("/a/b.log")
    assert GN._state_log_path({"_dev_log_file": None}) is None
    assert GN._state_log_path({}) is None
