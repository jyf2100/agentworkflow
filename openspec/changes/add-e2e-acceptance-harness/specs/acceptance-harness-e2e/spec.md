## ADDED Requirements

### Requirement: playwright harness 声明面

pa 编排器 SHALL 接受 profile `harness: playwright` 声明并与 jest/pytest 同权处理（`harness_invalid` 拦截面放行；test-author 链、acceptance_store、双注入、verify 机械锚全链对该值生效）。

#### Scenario: 声明 playwright 的项目走全链

- **WHEN** 项目 profile 声明 `harness: playwright` 且 critic 过闸 PRD 含验收点
- **THEN** pa-test-author 被以 harness=playwright 调起，产出 `e2e_*.spec.ts` 套件 content-addressed 入 acceptance_store
- **AND** dev/verify 双注入携带该套件，verify 机械锚按 `npx playwright test <file>` 输出判定

#### Scenario: 未声明项目零行为变化

- **WHEN** profile 不声明或声明 jest/pytest
- **THEN** 既有行为逐字节不变（v1 面零回归）

### Requirement: E2E 确定性纪律

pa-test-author 对 playwright 形态 SHALL 强制：条件等待（禁固定 sleep）、仅 127.0.0.1 本地 dev server（给定端口段）、`retries=1`、单 chromium。结构自检对违反前两条的文件 SHALL 拒收（同 jest/pytest 形态不匹配拒收的现行纪律）。

#### Scenario: sleep 写法被拒

- **WHEN** test-author 产出文件含 `page.waitForTimeout(`
- **THEN** 结构自检失败，套件拒收并回执原因

### Requirement: executor 镜像浏览器环境

executor 镜像 SHALL 预装 playwright（锁版本区间）+ chromium（含系统依赖），使 E2E 套件在容器内零网络安装可执行。

#### Scenario: 容器内直接跑

- **WHEN** 执行容器内对注入的 e2e 套件执行 `npx playwright test`
- **THEN** chromium 就地可用，无下载、无外网访问

### Requirement: 测试门同权

发布门 SHALL 将 playwright 新绿与 jest/pytest 新绿同权计为「新的绿色测试证据」。

#### Scenario: E2E 新绿过门

- **WHEN** dev 产出仅含 e2e 套件新绿（无 jest 测试新增）且目标仓声明 playwright
- **THEN** 测试门按新绿放行，不因 harness 形态降权
