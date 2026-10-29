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

`declared_suite_cmds_for` 增分支：**`playwright test <rel>`（全局 bin 直调，非 `npx`）**。理由（本体论审核 F2）：执行镜像运行时 clone 的目标仓 `node_modules` 无 `@playwright/test`，`npx playwright` 在本地无包时走 npx cache 下载（外网，违 D3 零外网）或交互提示卡死（headless 无 stdin）——**「镜像全局装」与「仓内可 resolve」是两层环境，耦合必须显式**：镜像层全局 `npm i -g playwright@<锁区间>` + `NODE_PATH=/usr/local/lib/node_modules` 注入，使 `import '@playwright/test'` 从全局 resolve、`playwright` CLI 走全局 bin——零仓内依赖、零网络。判定依据与 prompt 指令同一来源的不变量保持（`verify_agent.py:73` 契约）。E2E 输出锚：playwright 失败输出 `Error:` + `at e2e_x.spec.ts:LINE:COL` 行形态，`verify_anchors.parse_failing_anchors(testout, runner)` 增 runner 分支（jest 现分支为 `● name + at (file:line:col)`，适配点真实存在——审核 F7 复核通过）。**flaky 判定语义**：`retries=1` 后绿（exit 0）= 绿，连续两红 = 真红——match_declared 全等匹配只看 declared 命令 exit code，flaky 标记不映射 invalid。

## Decision: D3 确定性纪律 E2E 专则（test-author prompt 增补）

E2E 天然 flaky，纪律显式收紧，写进 prompt（违反 = 结构自检可拒）：

1. **禁 sleep/固定等待**——一律 `toBeVisible`/`waitForSelector` 条件等待。
2. **本地回环 + webServer 声明**——playwright.config 必须声明 `webServer`：`command` 取自 **profile 声明面新键 `e2e_dev_cmd`**（如 `npm run dev`；test-author Read-only 不读仓，dev 命令只能 profile 声明——审核 F3：dev server 生命周期主体归 playwright webServer 管，起/复用/杀全自动，dev 与 verify 会话零额外步骤），`port` 取固定段 4173-4199（按 PRD sha 取模分配）、`reuseExistingServer: true`（dev 会话已起时复用不重起）。零外网依赖（2026-10-28 run #75 github 抖动教训）。
3. **retries=1**——playwright.config 必含；时序偶发一次重试，两次红 = 真红。
4. **单浏览器 chromium**——不声明多浏览器工程（体积与确定性双收窄）。

## Decision: D4 executor 镜像（template 发版路径）

镜像改动**不走** `hub/deploy/Dockerfile`——那是 hub thin 镜像（控制面常驻物，与执行镜像零共享层，审核 F1）；且 `executor-image` spec 咬死 **build context 唯一来源 = template tag checkout（tag IS the build context）**。正解：template repo 投影内改——Dockerfile 增两层：`npm i -g playwright@<锁区间>` + `playwright install chromium --with-deps`（系统依赖由 --with-deps 带）、`ENV NODE_PATH=/usr/local/lib/node_modules`——经 **template tag 发版**（release note 注记），`pa-executor` tag 随发版咬合。hub 侧 `image_tag` 引用不变（deploy 配置单点）。

## Decision: D5 测试门同权

发布门不变式（无新绿不 PR）对 harness 无感知——判据是「目标仓测试命令新绿」，目标仓声明 playwright 后其测试命令即含 playwright。零代码改动，spec 注记即可。

## Decision: D6 混合验收点场景语义（审核 F6）

单 PRD 含纯逻辑验收点（适合单测）+ 交互验收点（适合 E2E）时，单 harness 声明强制验收面全走 E2E——可用（E2E 能断言 DOM/数据，纯逻辑点走 E2E 只是慢）但次优。混合的自然吸收点在 **dev 侧**：dev 实现时自写仓内单测（jest/pytest）过测试门（新绿不限于验收套件形态），验收面保持单形态判据清晰。多 harness 声明 v2 再议前，此场景语义按本条执行。

## Risks / Trade-offs

- **镜像体积 +~400MB（chromium）**：接受（executor 镜像本就容器化隔离，按需拉）。
- **E2E 慢（秒级/测试）**：dev 循环 verify 全量跑会慢——接受，收两道：E2E 仅对声明 playwright 的项目生效；retries=1 封顶。
- **行锚解析适配面**：playwright 输出格式版本间有差——钉 playwright 版本区间（镜像层锁版），解析器对锁版输出负责。
- **profile 声明面 +1 键（e2e_dev_cmd）**：声明 playwright 的项目必填（缺失 = test-author refused，回执指引补声明）——声明成本换 server 生命周期确定性。

## Migration Plan

flag 无需（声明驱动：不声明 `harness: playwright` 的项目零行为变化，`harness_invalid` 拦截面自然放行新值）。生效点两处：pa 代码随主线；镜像能力随下次 template 发版（未发版前声明 playwright 的项目 test-author 产物会在容器内跑不动——发版顺序为实施前置，见 tasks 3.x）。

## Open Questions

无（Python E2E、快照对比、混合 harness 已显式 out-of-scope 立项后议）。
