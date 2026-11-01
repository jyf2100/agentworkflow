"""上下文按需加载层——五通道注入的 stub 化 + 预算截断 + 引用链（add-context-loading-protocol）。

本体（change design）：注入物=已落盘持续物（PRD/lessons/acceptance/handoff/feedback）；stub=
其在 prompt 内的 ≤80 字投影（派生）；预算=投影面的机械约束（token 近似）。绝不改源实体。
verify 面注入 MUST NOT stub 化（考卷不可截断——dev-agent 的 acceptance_injection 不走此层）。

flag PA_CONTEXT_STUB 默认 off = 全量注入 byte-identical。
"""
from __future__ import annotations

import os
from pathlib import Path

# 通道优先级（高→低——超预算从低截断；PRD 永不截断除非单通道超限）
PRIORITY = ("prd", "acceptance", "feedback", "human_notes", "handoff", "lessons")
# acceptance 通道名实际不进此层（verify 考卷不截断）——但保留在优先级表供引用链标注
STUB_CHANNELS = ("prd", "feedback", "human_notes", "handoff", "lessons")

STUB_LIMIT = 80       # 摘要字符数
BUDGET_DEFAULT = 4000  # stub 总量字符近似（中文 1 字≈1 token 近似）
BUDGET_ENV = "PA_CONTEXT_BUDGET"


def _budget() -> int:
    try:
        return int(os.environ.get(BUDGET_ENV) or BUDGET_DEFAULT)
    except ValueError:
        return BUDGET_DEFAULT


def _stub(text: str, title: str, path: str | None = None, refs: str = "") -> str:
    """单通道 stub：标题 + 引用链 + 展开标记 + ≤80 字摘要。"""
    lines = [f"## {title}"]
    if refs:
        lines.append(f"→ {refs}")
    if path:
        lines.append(f"[展开: {path}]")
    summary = text.strip().replace("\n", " ")[:STUB_LIMIT]
    lines.append(f"{summary}{'…' if len(text.strip()) > STUB_LIMIT else ''}")
    return "\n".join(lines)


def _refs_for(channel: str, content: str) -> str:
    """机械提取引用链（从内容 frontmatter/结构提取，零 LLM）。"""
    refs: list[str] = []
    if channel == "lessons":
        # lessons 的 trigger 词 → PRD 验收点
        for word in ("trigger", "applies_when", "non_applicability"):
            if word in content:
                refs.append(f"含 {word}")
    if channel == "handoff":
        if "prior_iteration" in content or "round" in content:
            refs.append("上轮 iteration 交接")
    if channel == "feedback":
        if "verify" in content.lower():
            refs.append("verify 反馈")
    return " ｜ ".join(refs)


def render_stub(args: dict, prd_text: str) -> str:
    """五通道 stub 化渲染（flag 开时替代全量拼贴；flag 关时返回空串=调用方走既有路径）。

    返回完整 stub 块（含预算截断），调用方把它替换 build_prompt 的 prd_text + 各注入 block。
    """
    if os.environ.get("PA_CONTEXT_STUB", "").strip().lower() not in ("1", "true", "yes", "on"):
        return ""  # flag 关——调用方走全量注入

    stubs: dict[str, str] = {}
    # PRD（永不截除非单通道超限）
    stubs["prd"] = _stub(prd_text, "📋 PRD（验收标准）",
                         args.get("prd"), _refs_for("prd", prd_text))
    # 其他通道
    chan_map = {
        "feedback": ("--feedback-artifact", "⚠️ 上轮 verify 反馈"),
        "human_notes": ("--human-notes", "📣 频道补充上下文"),
        "handoff": ("--handoff-artifact", "🔄 上轮 dev 交接"),
        "lessons": ("--lessons-artifact", "📚 历史经验教训"),
    }
    for chan, (arg_key, title) in chan_map.items():
        path = args.get(arg_key.replace("--", "").replace("-", "_"))
        if path and Path(path).is_file():
            content = Path(path).read_text(encoding="utf-8", errors="replace")
            stubs[chan] = _stub(content, title, str(path), _refs_for(chan, content))

    # 预算截断：按优先级高→低分配预算，低优先级先被截（PRD 永不截）
    budget = _budget()
    result: list[str] = []
    used = 0
    for chan in PRIORITY:  # 高优先级先分配
        if chan not in stubs:
            continue
        stub_len = len(stubs[chan])
        if used + stub_len > budget and chan != "prd":
            result.append(f"[预算截断: {chan}——全文在 {args.get(chan, '?')}]")
        else:
            result.append(stubs[chan])
            used += stub_len
    return "\n\n".join(result)


def stub_enabled() -> bool:
    """flag 状态查询（dispatch 侧决定走全量还是 stub 路径）。"""
    return os.environ.get("PA_CONTEXT_STUB", "").strip().lower() in ("1", "true", "yes", "on")
