# Design: add-e2e-acceptance-harness

## Context

agent-verify-injection（未归档，19/20）落了验收工件链：pa-test-author 写可执行验收 bundle → content-addressed 入 acceptance_store → dev/verify 双注入 → verify 机械锚判绿红。harness 面 v1 = jest/pytest 是当时「行锚解析器支持集」的刻意收窄。本 change 是该面的**第一次扩容**，不动工件链本体。

## Goals / Non-Goals

**Goals**：playwright 进 harness 面（声明/文件形态/命令映射/镜像环境/确定性纪律五处同扩）；测试门对 playwright 新绿同权。
**Non-Goals**：快照对比、多 harness 混合、hub 侧任何改动（执行环境全在 executor 容器内）。

## Decision: D1 声明值与文件形态

- profile `harness: playwright`（单值，v3 前不放开混合）。
- test-author 文件形态 `e2e_*.spec.ts`（playwright 约定 `*.spec.ts`；前缀 `e2e_` 保证与仓内既有 jest 文件不撞名）。
- `_E2E_FILE_RE = re.compile(r"e2e_[\w\-]+\.spec\.ts$")`，`_structural_self_check` 增同族分支（形态不匹配 = 结构自检失败，同 jest/pytest 现行纪律）。

## Decision: D2 命令映射与机械锚

`declared_suite_cmds_for` 增分支：`npx playwright test <rel>`。判定依据与 prompt 指令同一来源的不变量保持（`verify_agent.py:73` 注释的契约）。E2E 输出锚：playwright 的失败输出含 `Error:` + `at e2e_x.spec.ts:LINE` 行形态，行锚解析器按既有 testout→anchor 管道适配（新 formatter 分支，不新造管道）。

## Decision: D3 确定性纪律 E2E 专则（test-author prompt 增补）

E2E 天然 flaky，纪律显式收紧，写进 prompt（违反 = 结构自检可拒）：

1. **禁 sleep/固定等待**——一律 `toBeVisible`/`waitForSelector` 条件等待。
2. **本地回环**——dev server 起在 `127.0.0.1:固定端口`（prompt 给定端口段 4173-4199 取模分配），零外网依赖（规避 github 抖动类基础设施风险，2026-10-28 run #75 教训）。
3. **retries=1**——test-author 写 `playwright.config` 覆盖时必须含；网络/时序偶发一次重试，两次红 = 真红。
4. **单浏览器 chromium**——不声明多浏览器工程（体积与确定性双收窄）。

## Decision: D4 executor 镜像

`hub/deploy/Dockerfile` 增层：`npx playwright install chromium --with-deps`（apt 依赖由 --with-deps 带）。镜像 tag 升版（v0.1.19 起）。hub 侧 `image_tag` 引用不变（deploy 配置单点）。

## Decision: D5 测试门同权

发布门不变式（无新绿不 PR）对 harness 无感知——判据是「目标仓测试命令新绿」，目标仓声明 playwright 后其测试命令即含 playwright。零代码改动，spec 注记即可。

## Risks / Trade-offs

- **镜像体积 +~400MB（chromium）**：接受（executor 镜像本就容器化隔离，按需拉）。
- **E2E 慢（秒级/测试）**：dev 循环 verify 全量跑会慢——接受，收两道：E2E 仅对声明 playwright 的项目生效；retries=1 封顶。
- **行锚解析适配面**：playwright 输出格式版本间有差——钉 playwright 版本区间（镜像层锁版），解析器对锁版输出负责。

## Migration Plan

flag 无需（声明驱动：不声明 `harness: playwright` 的项目零行为变化，`harness_invalid` 拦截面自然放行新值）。镜像升版即全量生效点。

## Open Questions

无（快照对比、混合 harness 已显式 out-of-scope 立项后议）。
