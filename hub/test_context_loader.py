"""context_loader 单测——stub 渲染/预算截断/引用链/flag 关 byte-identual（add-context-loading-protocol 1.3）。"""
from __future__ import annotations



import context_loader as CL


def test_flag_off_empty(monkeypatch):
    monkeypatch.delenv("PA_CONTEXT_STUB", raising=False)
    assert CL.render_stub({}, "PRD 全文") == ""
    assert not CL.stub_enabled()


def test_flag_on_stub_render(monkeypatch, tmp_path):
    monkeypatch.setenv("PA_CONTEXT_STUB", "1")
    # 构造 lessons artifact
    la = tmp_path / "lessons.md"
    la.write_text("# Lessons\n- trigger: import 错误\n- action: 先读 CLAUDE.md\n" + "x" * 200, encoding="utf-8")
    args = {"prd": "/opt/pa/prd/x.md", "lessons_artifact": str(la)}
    result = CL.render_stub(args, "PRD 正文内容。验收标准 1/2/3。" + "y" * 150)
    assert "## 📋 PRD" in result
    assert "[展开: /opt/pa/prd/x.md]" in result
    assert "## 📚 历史经验教训" in result
    assert "[展开:" in result                       # lessons 的展开标记
    assert "x" * 200 not in result                  # 全文不在
    assert "y" * 150 not in result                  # PRD 全文不在
    assert len(result) < 1000                       # 远小于全量


def test_budget_truncation(monkeypatch, tmp_path):
    """极小预算（50）→ 只有 PRD 存活，其余全截断（PRD 永不截断是 D2 硬约束）。"""
    monkeypatch.setenv("PA_CONTEXT_STUB", "1")
    monkeypatch.setenv("PA_CONTEXT_BUDGET", "50")    # 极小——任何路径长度下仅 PRD 能活
    la = tmp_path / "lessons.md"
    la.write_text("L" * 200, encoding="utf-8")
    fb = tmp_path / "feedback.md"
    fb.write_text("F" * 200, encoding="utf-8")
    args = {"prd": "/x.md", "lessons_artifact": str(la), "feedback_artifact": str(fb)}
    result = CL.render_stub(args, "PRD")
    assert "## 📋 PRD" in result                     # PRD 不截
    assert "[预算截断:" in result                     # 至少一个低优先级被截


def test_stub_limit_80():
    s = CL._stub("A" * 200, "标题", "/path", "")
    assert "A" * 81 not in s                         # ≤80 字截断
    assert "…" in s


def test_refs_extraction():
    assert "verify" in CL._refs_for("feedback", "verify said fail")
    assert "trigger" in CL._refs_for("lessons", "trigger: import error")
