"""test_critic_four_state.py — add-acceptance-spec-confirmation 1.4：critic 第四态 needs_metrics。

覆盖（spec delta scenario 对位）：
- 来源分叉：_mm_source_of（PRD frontmatter source: mm-channel 判据）+ critic_prompt
  four_state 尾行分叉（三态行 byte-identical 零变化锁——radar/panel 源零回归）
- needs_metrics 路由：spec-author 成功产 metrics_proposed 事件（含 metrics/prd_sha/initiator）/
  refused/空清单/调用异常三路降级 drop / entry verdict≠pass 天然不进 dispatch
- 非 MM 源违规吐 needs_metrics → 保守降级 drop（prompt 未声明四态 = 契约违规防御）
- #74（MM 模糊原文）/ #75（全指标原文）形态 fixture 锁路由层；LLM 真机判定归 3.1 真机坐实。
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent))
import run_daily  # noqa: E402

STAMP = "20261029"
# #74 真机形态：MM 源 + 主观描述无指标（critic 应判 needs_metrics 的输入面）
_MM_PRD = """---
project: "demo"
source_path: ''
source: mm-channel
user_id: "u-rocju"
username: "rocju"
channel_id: "ch-1"
evidence: "频道原文"
---

给首页增加赛博朋克风格样式（暗底、霓虹高亮），出处：今天频道讨论。
"""
# #75 真机形态：MM 源 + 验收指标齐全（应直接 pass 的输入面）
_METRICS_PRD = """---
project: "demo"
source_path: ''
source: mm-channel
user_id: "u-rocju"
username: "rocju"
channel_id: "ch-1"
evidence: "频道原文"
---

首页改版：body 背景色 #0a0a12；标题文字色 #00f0ff；按钮 hover 300ms 过渡。
"""
# radar 源形态：无 frontmatter（stage 产物）
_PLAIN_PRD = "# 雷达信号 PRD\n优化某库性能。\n"

_METRICS = [
    {"id": "m1", "metric": "body 背景色为 #0a0a12", "from": "暗底", "coverage": "proxy"},
    {"id": "m2", "metric": "标题文字色 #00f0ff", "from": "霓虹高亮", "coverage": "full"},
]


def _args(**over):
    base = dict(force=True)
    base.update(over); return SimpleNamespace(**base)


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def _stub_critic(verdict, prd_path, project="p1"):
    return {"verdict": verdict, "prd_path": prd_path, "project": project,
            "summary": "stub", "reason": "no_metrics"}


# ── _mm_source_of：来源分叉判据 ─────────────────────────────────────────────

def test_mm_source_of_parses_mm_frontmatter(tmp_path):
    p = _write(tmp_path, "mm.md", _MM_PRD)
    src = run_daily._mm_source_of(p)
    assert src == {"user_id": "u-rocju", "username": "rocju", "channel_id": "ch-1"}


def test_mm_source_of_rejects_non_mm_shapes(tmp_path):
    plain = _write(tmp_path, "plain.md", _PLAIN_PRD)
    assert run_daily._mm_source_of(plain) is None            # 无 frontmatter（radar 形态）
    no_user = _write(tmp_path, "nou.md", _MM_PRD.replace('user_id: "u-rocju"', "user_id: \"\""))
    assert run_daily._mm_source_of(no_user) is None          # 缺 user_id → 不可确认，不进四态
    assert run_daily._mm_source_of(str(tmp_path / "absent.md")) is None  # 文件不存在 → None 不抛


# ── critic_prompt：三态行零变化锁 + 四态行分叉 ─────────────────────────────

def test_critic_prompt_three_state_line_unchanged():
    prof = {"name": "demo", "goal": "g", "match_surface": {"one_liner": "x"}}
    p3 = run_daily.critic_prompt("a.md", "s.md", prof)
    assert "verdict=pass|drop|revise）" in p3
    assert "needs_metrics" not in p3                          # radar/panel 源零回归（spec scenario 3）
    p4 = run_daily.critic_prompt("a.md", "s.md", prof, four_state=True)
    assert "verdict=pass|drop|revise|needs_metrics" in p4
    assert "reason=\"no_metrics\"" in p4


# ── stage_critic：needs_metrics 路由全路径 ──────────────────────────────────

def test_needs_metrics_routes_to_spec_author_and_emits_event(tmp_path, monkeypatch):
    """MM 源 needs_metrics → spec-author 拟指标 → metrics_proposed 事件（hub 建行依据）→ 不 dispatch。"""
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path)
    prd = _write(tmp_path, f"{STAMP}_a.md", _MM_PRD)          # 真文件（frontmatter + sha 都要读）
    manifest = {"prds": [{"project": "p1", "path": prd, "source_path": ""}]}
    monkeypatch.setattr(run_daily, "_critic_one",
                        lambda path, src, prof, mm_src=None: _stub_critic("needs_metrics", path))
    calls = []
    def _fake_spec_author(name, prompt, stage, label, allowed_tools=None, env=None):
        calls.append((name, stage))
        return ({"metrics": _METRICS, "refused": None}, {"cost": 0.01})
    monkeypatch.setattr(run_daily, "run_persona_with_backoff", _fake_spec_author)
    gate = run_daily.stage_critic(_args(), manifest, {"p1": {"name": "p1"}}, STAMP)

    assert calls == [("pa-spec-author", "spec-author")]       # 走 spec-author 管道（SB 记账面）
    e = gate[0]
    assert e["verdict"] == "needs_metrics"                    # ≠pass → dispatch verdict==pass 过滤天然不投
    assert e["metrics"] == _METRICS                           # gate.json 可审计
    import hashlib
    sha = hashlib.sha256(Path(prd).read_bytes()).hexdigest()
    ev = tmp_path / "runs" / "p1" / f"{STAMP}_{STAMP}_a.events.jsonl"
    rows = [json.loads(ln) for ln in ev.read_text(encoding="utf-8").splitlines()]
    mp = [r for r in rows if r["event"] == "metrics_proposed"]
    assert len(mp) == 1                                        # 事件恰好一条
    assert mp[0]["metrics"] == _METRICS and mp[0]["prd_sha"] == sha
    assert mp[0]["initiator"] == "u-rocju"                     # D3 字段映射：initiator=enqueued_by 同源


def test_needs_metrics_spec_author_failure_degrades_drop(tmp_path, monkeypatch):
    """spec-author 调用异常 → 降级 drop + summary 注原因（fail-safe 最坏退回现状 drop）。"""
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path)
    prd = _write(tmp_path, f"{STAMP}_a.md", _MM_PRD)
    manifest = {"prds": [{"project": "p1", "path": prd, "source_path": ""}]}
    monkeypatch.setattr(run_daily, "_critic_one",
                        lambda path, src, prof, mm_src=None: _stub_critic("needs_metrics", path))
    def _boom(name, prompt, stage, label, allowed_tools=None, env=None):
        raise RuntimeError("persona 输出非 JSON")
    monkeypatch.setattr(run_daily, "run_persona_with_backoff", _boom)
    gate = run_daily.stage_critic(_args(), manifest, {"p1": {"name": "p1"}}, STAMP)
    assert gate[0]["verdict"] == "drop"
    assert "spec-author" in gate[0]["summary"]                 # 降级原因可观测


def test_needs_metrics_refused_or_empty_degrades_drop(tmp_path, monkeypatch):
    """spec-author refused / 空清单 → drop（绝不半拟指标入队）。"""
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path)
    prd = _write(tmp_path, f"{STAMP}_a.md", _MM_PRD)
    manifest = {"prds": [{"project": "p1", "path": prd, "source_path": ""}]}
    monkeypatch.setattr(run_daily, "_critic_one",
                        lambda path, src, prof, mm_src=None: _stub_critic("needs_metrics", path))
    for payload in ({"metrics": [], "refused": "原文信息量不足"},
                    {"metrics": [], "refused": None}):
        monkeypatch.setattr(run_daily, "run_persona_with_backoff",
                            lambda *a, payload=payload, **k: (payload, {"cost": 0.0}))
        gate = run_daily.stage_critic(_args(force=True), manifest, {"p1": {"name": "p1"}}, STAMP)
        assert gate[0]["verdict"] == "drop", payload
    ev = tmp_path / "runs" / "p1" / f"{STAMP}_{STAMP}_a.events.jsonl"
    rows = [json.loads(ln) for ln in ev.read_text(encoding="utf-8").splitlines()]
    assert not [r for r in rows if r["event"] == "metrics_proposed"]   # 拒拟绝不发事件


def test_needs_metrics_from_non_mm_source_degrades_drop(tmp_path, monkeypatch):
    """非 MM 源（prompt 未声明四态）违规吐 needs_metrics → 保守降级 drop（契约违规防御）。"""
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path)
    prd = _write(tmp_path, f"{STAMP}_r.md", _PLAIN_PRD)        # radar 形态，_mm_source_of → None
    manifest = {"prds": [{"project": "p1", "path": prd, "source_path": ""}]}
    monkeypatch.setattr(run_daily, "_critic_one",
                        lambda path, src, prof, mm_src=None: _stub_critic("needs_metrics", path))
    monkeypatch.setattr(run_daily, "run_persona_with_backoff",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应调 spec-author")))
    gate = run_daily.stage_critic(_args(), manifest, {"p1": {"name": "p1"}}, STAMP)
    assert gate[0]["verdict"] == "drop"                         # 不路由、不烧 spec-author
    assert "非 MM 源" in gate[0]["summary"]


def test_pass_path_unaffected_for_metrics_complete_prd(tmp_path, monkeypatch):
    """#75 形态（指标齐全 MM 源）pass 路径零回归：不触发 spec-author、不产 metrics_proposed。"""
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path)
    prd = _write(tmp_path, f"{STAMP}_b.md", _METRICS_PRD)
    manifest = {"prds": [{"project": "p1", "path": prd, "source_path": ""}]}
    monkeypatch.setattr(run_daily, "_critic_one",
                        lambda path, src, prof, mm_src=None: _stub_critic("pass", path))
    monkeypatch.setattr(run_daily, "run_persona_with_backoff",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("pass 不应调 spec-author")))
    gate = run_daily.stage_critic(_args(), manifest, {"p1": {"name": "p1"}}, STAMP)
    assert gate[0]["verdict"] == "pass"
