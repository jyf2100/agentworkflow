"""evidence.py — 结构化测试证据 + 发布硬门（OpenSpec verified-dev-execution / 本变更 tasks 2.x）。

把 dev-agent.py 原先靠文本 ``state["last_test"] == "green"`` 判定、且并不真正拦截发布的
"测试绿"，升级为：结构化 ``TestEvidence`` + 发布动作（commit/push/PR）的机械前置门。
证据缺失 / 失败 / 过期（绿后又有写）→ 不发布，吐结构化原因（dev-agent exit 14）。

抽到独立无依赖模块（同 ``slug_utils`` / ``bash_allowlist`` 既定模式）：dev-agent.py 顶部
``from claude_agent_sdk import ...`` 是顶层加载，纯逻辑放本模块 → 单测可零 SDK 导入锁定红绿分支
（design.md Risks 第 4 条「导入 dev-agent.py 会触发 SDK 副作用」的落地）。

新鲜度模型（design Open Question #1 的选择）：采用「写事件标记」——绿测试采集证据后，dev loop
一旦再发生候选写（Edit/Write/MultiEdit），证据 ``fresh``→False（过期）。这精确覆盖 spec 场景
「绿后又改 → 证据过期」；commit-SHA 不适用（测试发生在 commit 前），diff-hash 签名绑定留作后续
硬化（YAGNI，不在本变更引入）。任何「无法证明绿且新鲜」的状态都不放行——保守，宁拦勿错放。
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import ClassVar

# 发布门裁定（→ dev-agent exit 14 的 JSON.gate_status / dispatch 侧状态分类）
GATE_PUBLISH = "publish"           # 证据新鲜且绿 → 允许发布
GATE_NOT_RUN = "test_not_run"      # dev loop 结束无结构化证据（没跑到被识别的干净测试）
GATE_FAILED = "test_failed"        # 最近一次干净测试非零退出
GATE_STALE = "test_stale"          # 绿测试后又发生候选写，证据已不反映当前待提交内容


@dataclass(frozen=True)
class TestEvidence:
    """一次「干净测试」（is_clean_test_cmd 过滤后的裸命令）的结构化证据。

    ``fresh=False`` 表示采集后 dev loop 又发生了候选写——此时该证据不再代表当前待提交内容，
    发布门判 stale。每次新一轮干净测试结果到来，整体替换为 ``fresh=True`` 的新证据（即便是
    上一轮绿的后续红测试也整体替换，确保「最新一次测试」始终是裁定依据）。
    """
    __test__: ClassVar[bool] = False   # 类名 Test* 命中 pytest 收集规则；声明非测试类，免收集告警

    command: str            # 干净测试命令（dev-agent 记的 last_test_cmd）
    exit_code: int          # 0=绿，非 0=红
    completed_at: str       # 完成时点 stamp（dev-agent.runtime stamp()）
    fresh: bool = True      # 采集后是否又发生候选写


def mark_stale(evidence: TestEvidence | None) -> TestEvidence | None:
    """绿后发生候选写 → 证据转过期（不可变 dataclass，返回 fresh=False 的新副本）。None 原样返回。"""
    if evidence is None:
        return None
    return replace(evidence, fresh=False)


def evaluate_gate(evidence: TestEvidence | None) -> tuple[str, str]:
    """裁定发布门：给定当前最新证据，返回 ``(verdict, reason)``。

    判定优先级：None→test_not_run；exit_code!=0→test_failed；fresh=False→test_stale；否则 publish。
    """
    if evidence is None:
        return GATE_NOT_RUN, "dev loop 结束未采集到结构化绿色测试证据（没跑到被识别的干净测试）"
    if evidence.exit_code != 0:
        return GATE_FAILED, f"最近一次干净测试非零退出（exit={evidence.exit_code}, cmd={evidence.command}）"
    if not evidence.fresh:
        return GATE_STALE, "绿色测试后又发生候选写，证据已不反映当前待提交内容"
    return GATE_PUBLISH, f"证据新鲜且绿（{evidence.completed_at}, cmd={evidence.command}）"
