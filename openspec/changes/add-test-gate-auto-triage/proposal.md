# Proposal: add-test-gate-auto-triage

## Why

verified-dev-execution 发布门的 `test_stale` 分支（绿测后又有候选写 → 证据过期阻断）真机高频触发：#110/#111（2026-10-31/11-01）均以 `blocked_test_gate: test_stale` 收场，dispatch 记录「待运维 triage」——实际场景多为 dev 在绿测后做了无害收尾写（如 package-lock 被 npm 顺带 touch、注释微调），证据语义上过期但代码本身可发布。每次都人工 triage 是注意力的纯损耗（演进路线 P0-③）。

## What Changes

1. **编排器侧自动重验（一次）**：`test_stale` 阻断时，dev-agent 在 worktree 内重跑**同一条**干净测试命令（`TestEvidence.command` 原文）取新证据——新证据采集于全部候选写之后（dev loop 已结束、期间零写），`fresh=True` 按构造成立，`evaluate_gate` 复评为 publish 则照常发布。
2. **flag-gated 默认关**：`PA_TEST_GATE_AUTO_TRIAGE`（feature_flags 域切割前缀），默认 off 零行为。
3. **重验记录入 JSON**：`auto_triage` 增量字段（attempted/command/exit_code/outcome）进成功与阻断两条路径的最终 JSON——人审可见「已自动重验过、结果如何」。
4. **范围钉死**：仅 `test_stale` 分支；`test_not_run`（dev 根本没跑测试）与 `test_failed`（最近证据是红）不自动重验——前者是 dev 行为问题、后者重跑红测试无意义（非 flaky 诊断器）。一次为上限，绝不循环。
5. **超时**：`PA_TEST_GATE_TRIAGE_TIMEOUT`（默认 600s），超时按 red 记录（outcome=timeout）走原阻断。

## Non-Goals

- 不放松门语义：无新绿照样拦——重验是新绿的一种采集方式（spec 场景原文「blocked until a new green test completes」，重验即一次新的绿色测试完成）。
- 不做 flaky 重试/多轮重验/诊断。
- 不动 `test_not_run` / `test_failed` 分支。
- 不动 verify 面与 single-flight auto-merge 的门。
