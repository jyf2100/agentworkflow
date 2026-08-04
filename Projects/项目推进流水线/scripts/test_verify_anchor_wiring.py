# -*- coding: utf-8 -*-
"""test_verify_anchor_wiring.py — anchor 供应链接入测试（harden-pa-verify-determinism task 5.3）。

验 run_daily 侧的 wiring：verify_prompt 锚点注入（flag-on）/ baseline（flag-off 或空）、
_derive_verify_anchors 的 flag-gated + green-path 不 derive + 红 derive。hermetic（monkeypatch
resolve_flags，不触真实 flag 解析；canned testout/diff）。
"""
import pathlib
import sys
import types

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from verify_anchors import Anchor  # noqa: E402
from verify_bundles import Bundle, split_bundles  # noqa: E402
import run_daily  # noqa: E402


# ── verify_prompt 注入（task 2.1 / 5.3）─────────────────────────────────────────
def test_verify_prompt_with_anchors_injects_block():
    anchors = [Anchor(test_name="test_x", status="ok", file="foo.py", line=12,
                      hunk="@@ -8,5 +10,7 @@")]
    p = run_daily.verify_prompt("/prd.md", "auto/b", "main", pathlib.Path("/d.diff"),
                                {"test_rc": 1, "test_log": "/t.testout"}, 1,
                                {"name": "proj"}, anchors=anchors)
    assert "[ok]" in p and "test_x" in p and "foo.py:12" in p
    assert "引上方机械锚点" in p   # locate_hint 切到「引锚点」


def test_verify_prompt_baseline_when_no_anchors():
    p = run_daily.verify_prompt("/prd.md", "auto/b", "main", pathlib.Path("/d.diff"),
                                {"test_rc": 1, "test_log": "/t.testout"}, 1,
                                {"name": "proj"}, anchors=[])
    assert "[ok]" not in p
    assert "①定位（文件/测试/断言行）" in p   # baseline locate_hint（无锚点块）


def test_verify_prompt_base_side_anchor_rendered():
    anchors = [Anchor(test_name="test_y", status="base-side", file="bar.py", line=5,
                      reason="bar.py:5 不在本轮增量 diff hunk（base-side 回归）")]
    p = run_daily.verify_prompt("/p.md", "b", "main", pathlib.Path("/d"),
                                {"test_rc": 1}, 2, {"name": "p"}, anchors=anchors)
    assert "[base-side 回归]" in p and "bar.py:5" in p


# ── _derive_verify_anchors flag-gated（task 4.0 / 4.1 / green-path 不 derive）──
def _flags(on: bool):
    return types.SimpleNamespace(verify_anchor_evidence=on)


def test_derive_flag_off_returns_empty(monkeypatch):
    monkeypatch.setattr(run_daily, "resolve_flags", lambda profile=None: _flags(False))
    rec = {"verify": {"test_rc": 1, "test_cmd": "pytest"}}
    assert run_daily._derive_verify_anchors(rec, pathlib.Path("/no.diff"), {}) == []


def test_derive_green_path_returns_empty(monkeypatch):
    # flag 开但测试绿（test_rc=0）→ green-path 不 derive（design 方向 A）
    monkeypatch.setattr(run_daily, "resolve_flags", lambda profile=None: _flags(True))
    rec = {"verify": {"test_rc": 0}}
    assert run_daily._derive_verify_anchors(rec, pathlib.Path("/no.diff"), {}) == []


def test_derive_flag_on_red_derives(monkeypatch, tmp_path):
    monkeypatch.setattr(run_daily, "resolve_flags", lambda profile=None: _flags(True))
    testout = "FAILED foo.py::test_x - assert False\nfoo.py:12: AssertionError\n"
    tlog = tmp_path / "t.testout"
    tlog.write_text(testout)
    diff = tmp_path / "d.diff"
    diff.write_text("diff --git a/foo.py b/foo.py\n+++ b/foo.py\n@@ -8,5 +10,7 @@\n")
    rec = {"verify": {"test_rc": 1, "test_log": str(tlog), "test_cmd": "pytest"}}
    a = run_daily._derive_verify_anchors(rec, diff, {})
    assert a and a[0].status == "ok" and a[0].file == "foo.py" and a[0].line == 12


def test_derive_failopen_on_exception(monkeypatch):
    # resolve_flags 抛异常 → fail-open 返回 []（不拖垮 verify 闭环）
    def boom(profile=None):
        raise RuntimeError("flag 解析崩")
    monkeypatch.setattr(run_daily, "resolve_flags", boom)
    rec = {"verify": {"test_rc": 1}}
    assert run_daily._derive_verify_anchors(rec, pathlib.Path("/x"), {}) == []


# ── bundle 接入（task 3.3 / 3.4 / 3.5）─────────────────────────────────────────
def _multi_diff(n: int = 12) -> str:
    files = [f"src/m{i}.py" for i in range(n)]
    return "".join(
        f"diff --git a/{f} b/{f}\n--- a/{f}\n+++ b/{f}\n@@ -1,2 +1,3 @@\n ctx\n+new\n" for f in files)


def test_verify_prompt_multi_bundle_injects_block():
    bundles = split_bundles(_multi_diff(), file_threshold=10, line_threshold=100000)
    assert len(bundles) > 1
    p = run_daily.verify_prompt("/p.md", "b", "main", pathlib.Path("/d"),
                                {"test_rc": 1}, 1, {"name": "p"}, bundles=bundles)
    assert "[bundle" in p and "criteria_coverage" in p and "逐 bundle" in p


def test_verify_prompt_single_bundle_is_baseline():
    # 单 bundle（小 diff）→ prompt 不显示 bundle 块 = baseline 不变
    bundles = [Bundle(("foo.py",), "single-bundle（threshold 未超）")]
    p = run_daily.verify_prompt("/p.md", "b", "main", pathlib.Path("/d"),
                                {"test_rc": 1}, 1, {"name": "p"}, bundles=bundles)
    assert "[bundle" not in p


def test_derive_bundles_flag_off_empty(monkeypatch):
    monkeypatch.setattr(run_daily, "resolve_flags", lambda profile=None: _flags(False))
    assert run_daily._derive_verify_bundles(pathlib.Path("/no.diff"), {}) == []


def test_derive_bundles_flag_on_splits(monkeypatch, tmp_path):
    monkeypatch.setattr(run_daily, "resolve_flags", lambda profile=None: _flags(True))
    diff = tmp_path / "d.diff"
    diff.write_text(_multi_diff(12))   # >10 文件 → split
    b = run_daily._derive_verify_bundles(diff, {})
    assert len(b) > 1
