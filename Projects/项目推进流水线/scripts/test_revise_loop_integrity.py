"""test_revise_loop_integrity.py — revise 回环假通防御（2026-10-29 run #109 实证修复）。

#109 真机形态：critic r1 判 revise（米白无色值）→ pa-prd r2 被指示重写原路径但**文件未变**
（frontmatter round 仍 1）→ critic r2 盲审原文件，判定抖动放行 pass = 假通（下游 no_criteria）。

覆盖：
- 重写未发生（原文件 sha 未变）→ 按 drop，不进 critic r2（防盲审抖动假 pass）
- 重写落盘（文件真变）→ 进 r2，且 r2 收到 round1 revisions_needed（复审非盲审）
- critic_prompt：revisions 注入「round=2 复审」块；不传零变化（三态行锁不回归）
"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent))
import run_daily  # noqa: E402

STAMP = "20261029"
_PRD = """---
project: "demo"
---

给页面加秋天配色：主色 #c8741a，背景走米白色。出处：roc
"""
_REVISE_EXTRA = {"summary": "米白无色值",
                 "revisions_needed": ["背景米白需给具体色值（如 #f6f1e7）"]}


def _args(**over):
    base = dict(force=True)
    base.update(over); return SimpleNamespace(**base)


def _critic_stub(verdict, prd_path, project="p1", **extra):
    e = {"verdict": verdict, "prd_path": prd_path, "project": project,
         "summary": "stub", "reason": "no_metrics"}
    e.update(extra); return e


# ── 假通防御：重写未发生 → drop，绝不进 r2 盲审 ───────────────────────────

def test_rewrite_not_happened_drops_without_round2(tmp_path, monkeypatch):
    """pa-prd r2 声称重写但文件未变（#109 实证形态）→ 按 drop + 出路文案，critic 不再审第二次。"""
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path)
    prd = tmp_path / f"{STAMP}_r.md"; prd.write_text(_PRD, encoding="utf-8")
    manifest = {"prds": [{"project": "p1", "path": str(prd), "source_path": ""}]}
    calls = {"critic": 0}
    monkeypatch.setattr(run_daily, "_critic_one",
                        lambda path, src, prof, mm_src=None, revisions=None:
                        calls.__setitem__("critic", calls["critic"] + 1) or _critic_stub("revise", path, **_REVISE_EXTRA))
    # pa-prd r2：吐 manifest 说写了原路径，但文件根本没动（#109 实证）
    monkeypatch.setattr(run_daily, "run_persona_with_backoff",
                        lambda *a, **k: ({"prds": [{"path": str(prd)}]}, {"cost": 0.01}))
    gate = run_daily.stage_critic(_args(), manifest, {"p1": {"name": "p1"}}, STAMP)

    assert calls["critic"] == 1                                  # 只审了 round1，未进 r2 盲审
    assert gate[1]["verdict"] == "drop" and gate[1]["round"] == 2
    assert "重写未发生" in gate[1]["summary"]
    assert "重新 /pa run 提交" in gate[1]["summary"]              # 出路可操作（#103 同款）


def test_rewrite_to_new_file_passes_the_sha_gate(tmp_path, monkeypatch):
    """pa-prd r2 写到新路径（内容不同）→ sha 校验过，正常进 r2 复审。"""
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path)
    prd = tmp_path / f"{STAMP}_n.md"; prd.write_text(_PRD, encoding="utf-8")
    manifest = {"prds": [{"project": "p1", "path": str(prd), "source_path": ""}]}
    r2 = tmp_path / f"{STAMP}_n_r2.md"; r2.write_text(_PRD + "\n背景色定为 #f6f1e7。", encoding="utf-8")
    monkeypatch.setattr(run_daily, "_critic_one",
                        lambda path, src, prof, mm_src=None, revisions=None:
                        _critic_stub("revise", str(prd), **_REVISE_EXTRA)
                        if revisions is None else _critic_stub("pass", path))
    monkeypatch.setattr(run_daily, "run_persona_with_backoff",
                        lambda *a, **k: ({"prds": [{"path": str(r2)}]}, {"cost": 0.01}))
    gate = run_daily.stage_critic(_args(), manifest, {"p1": {"name": "p1"}}, STAMP)
    assert gate[1]["verdict"] == "pass" and gate[1]["revised"] is True


# ── r2 复审非盲审：round1 反馈注入 ────────────────────────────────────────

def test_round2_gets_round1_revisions_not_blind(tmp_path, monkeypatch):
    """重写真发生后，critic r2 收到 round1 revisions_needed（复审语义，非独立盲审）。"""
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path)
    prd = tmp_path / f"{STAMP}_f.md"; prd.write_text(_PRD, encoding="utf-8")
    manifest = {"prds": [{"project": "p1", "path": str(prd), "source_path": ""}]}
    seen = []
    prd_tools = []
    def _fake_critic(path, src, prof, mm_src=None, revisions=None):
        seen.append(revisions)
        if revisions is None:                                    # round1
            return _critic_stub("revise", path, **_REVISE_EXTRA)
        return _critic_stub("pass", path)                        # round2 复审（文件已变，过 sha 门）
    monkeypatch.setattr(run_daily, "_critic_one", _fake_critic)
    # 顺序：_critic_one(r1) → pa-prd r2 改文件 → sha 门过 → _critic_one(r2 带 revisions)
    def _fake_prd(*a, **k):
        prd_tools.append(k.get("allowed_tools"))
        prd.write_text(_PRD + "\n背景色 #f6f1e7。", encoding="utf-8")
        return ({"prds": [{"path": str(prd)}]}, {"cost": 0.01})
    monkeypatch.setattr(run_daily, "run_persona_with_backoff", _fake_prd)
    gate = run_daily.stage_critic(_args(), manifest, {"p1": {"name": "p1"}}, STAMP)

    assert seen[0] is None and seen[1] == _REVISE_EXTRA["revisions_needed"]  # r1 盲/r2 带反馈
    assert prd_tools == [["Read", "Write"]]      # headless Write 预批准（run #98 容器实证漏挂必拒）
    assert gate[1]["verdict"] == "pass"


def test_critic_prompt_revisions_block_injection_and_default_unchanged():
    """prompt 契约：revisions 注入 round=2 复审块；不传（默认 None）byte-identical 零变化。"""
    prof = {"name": "demo", "goal": "g", "match_surface": {"one_liner": "x"}}
    base = run_daily.critic_prompt("a.md", "s.md", prof)
    assert "round=2" not in base                                 # 默认形态零变化
    p2 = run_daily.critic_prompt("a.md", "s.md", prof, revisions=["补色值 #f6f1e7"])
    assert "revise 回环 round=2 复审" in p2 and "补色值 #f6f1e7" in p2
    assert run_daily.critic_prompt("a.md", "s.md", prof, revisions=None) == base
