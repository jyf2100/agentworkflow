"""panel attention 视图单测（add-panel-attention 2.1）。"""
from __future__ import annotations

import panel


def test_attention_empty_state():
    html = panel.render_attention([])
    assert "待办（0）" in html and "无事需要你" in html and "实时视图" in html


def test_attention_sections_hide_when_empty():
    html = panel.render_attention([{"kind": "blocked", "rows": []},
                                   {"kind": "confirm", "rows": [
                                       {"title": "#5 proj", "detail": "指标确认",
                                        "action_html": "<code>/pa confirm</code>"}]}])
    assert "确认环待决（1）" in html
    assert "阻断待 triage" not in html          # 空类整节隐藏


def test_attention_full_four_and_badge():
    rows = [{"title": "t", "detail": "d", "action_html": "x"}]
    html = panel.render_attention([{"kind": "blocked", "rows": rows},
                                   {"kind": "confirm", "rows": rows},
                                   {"kind": "proposal", "rows": rows},
                                   {"kind": "drift", "rows": rows}])
    assert "待办（4）" in html
    for sec in ("阻断待 triage", "确认环待决", "影子提案待采纳", "漂移未消解"):
        assert sec in html, sec
    assert 'href="/panel/attention"' in html      # 导航徽标
