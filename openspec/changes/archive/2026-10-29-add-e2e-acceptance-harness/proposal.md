# Proposal: add-e2e-acceptance-harness

## Why

验收测试 harness 面 v1 刻意收窄到 jest/pytest（行锚解析器支持集，`run_daily.py:950`），agent（pa-test-author）只能规划**断言级**测试（jsdom/纯 Python），规划不了「起真实服务 + 浏览器操作」的集成测试。后果：

- 验收体系对**交互/视觉/端到端行为**类需求（恰是产品侧最高频的需求形态——2026-10-28 真机 run #74/#75 的赛博朋克主题需求即此类）只有一条路：人把主观描述手工落成断言级指标（#75 的做法）。agent「自己规划集成测试自己跑」的能力被 harness 面挡死。
- `declared_suite_cmds_for`（`verify_agent.py:78`）对集外 harness 返回空 → 即使 test-author 写了 E2E 文件，verify 的机械锚判定面也不认。

用户已确认方向（2026-10-28 会话）：验收测试面要扩到 E2E（playwright 级），让 agent 能对交互/视觉需求规划真集成测试并自己执行。

## What Changes

- harness 声明面从 `{jest, pytest}` 扩为 `{jest, pytest, playwright}`（profile 单值声明不变，一项目一 harness）。
- `declared_suite_cmds_for` 增 playwright 映射（`npx playwright test <file>`）；verify 机械锚判定面同步认 E2E 输出。
- pa-test-author prompt 增 playwright 形态分支：文件 `e2e_*.spec.ts`，纪律 = 全覆盖验收点 + criterion id 标注 + **确定性纪律 E2E 专则**（禁 sleep 等待、可见性断言优先、`retries=1` 注入、本地 dev server 固定端口——仅 localhost，零外网依赖）。
- executor 镜像（`hub/deploy/Dockerfile`，pa-executor tag 升版）装 playwright + chromium。
- 行锚解析器（`_JEST_FILE_RE` 同族）增 `_E2E_FILE_RE`。
- 测试门口径不变量保持：playwright 新绿 = 新绿（与 jest/pytest 同权，发布门认）。

## Out of Scope

- **Python 项目的 Web E2E**（pytest-playwright 形态）——v1 playwright 声明钉死 Node 工具链（`e2e_*.spec.ts` + node/playwright CLI），Python 项目（aigw 类）的 E2E 需求 v1 覆盖不到，v2 另议。
- 多 harness 混合声明（一 profile 同时 jest + playwright）——v2 再议；混合验收点场景语义见 design D6。
- 视觉快照对比（screenshot diff）专项——playwright `toHaveScreenshot` 属确定性坑（字体/AA 渲染差），本 change 只开 `toBeVisible`/属性/DOM 断言面，快照对比另行立项。
- B 环（验收指标预确认，见 `add-acceptance-spec-confirmation`）——正交，可独立交付。
