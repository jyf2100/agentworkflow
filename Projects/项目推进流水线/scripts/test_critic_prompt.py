"""critic_prompt 渲染契约：source_path 空时不留裸空白，显式交代手写形态语义。

背景（2026-10-27 cc-web-control 三连 drop）：面板手写 PRD source_path 天然为空，
旧模板渲染「信息源原文：」尾随空白，诱导 critic 判无据（违反 F12 单标准「不看出身」）。
"""
from run_daily import critic_prompt

PROF = {"name": "demo", "goal": "g", "match_surface": {"one_liner": "o"}}


def test_prompt_with_source():
    p = critic_prompt("a.md", "src.md", PROF)
    assert "信息源原文：src.md" in p
    assert "常态形态" not in p  # 有源走常规形态，不加手写交代


def test_prompt_empty_source_no_bare_blank():
    p = critic_prompt("a.md", "", PROF)
    assert "信息源原文：\n" not in p  # 不许尾随裸空白
    assert "常态形态" in p and "不看出身" in p  # 显式交代：空源=手写常态，看正文背景交代


def test_prompt_profile_line_stable():
    p = critic_prompt("a.md", "", PROF)
    assert '项目 profile：name=demo goal="g" match_surface.one_liner="o"' in p
