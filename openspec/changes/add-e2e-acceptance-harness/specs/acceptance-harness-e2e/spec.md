## ADDED Requirements

### Requirement: playwright harness 声明面

pa 编排器 SHALL 接受 profile `harness: playwright` 声明并与 jest/pytest 同权处理（`harness_invalid` 拦截面放行；test-author 链、acceptance_store、双注入、verify 机械锚全链对该值生效）。v1 该声明为 **Node 工具链项目专属**（Python 项目 E2E 另行立项）。声明 playwright 的项目 profile SHALL 同时声明 `e2e_dev_cmd`（dev server 启动命令）。

#### Scenario: 声明 playwright 的项目走全链

- **WHEN** 项目 profile 声明 `harness: playwright` + `e2e_dev_cmd: npm run dev` 且 critic 过闸 PRD 含验收点
- **THEN** pa-test-author 被以 harness=playwright 调起，产出 `e2e_*.spec.ts` 套件（playwright.config 含 webServer/retries=1/chromium 单浏览器）content-addressed 入 acceptance_store
- **AND** dev/verify 双注入携带该套件，verify 机械锚按 `playwright test <file>` 输出判定

#### Scenario: 缺 e2e_dev_cmd 声明拒收

- **WHEN** profile 声明 `harness: playwright` 但无 `e2e_dev_cmd`
- **THEN** test-author 拒收（refused，reason 注「缺 e2e_dev_cmd 声明」），回执指引补声明，不产出半套件

#### Scenario: 未声明项目零行为变化

- **WHEN** profile 不声明或声明 jest/pytest
- **THEN** 既有行为逐字节不变（v1 面零回归）

### Requirement: E2E 确定性纪律

pa-test-author 对 playwright 形态 SHALL 强制：条件等待（禁固定 sleep）、playwright.config 声明 webServer（command 基于profile `e2e_dev_cmd` 改写至**实际监听**机械分配端口、127.0.0.1 固定端口段、`reuseExistingServer`）、`retries=1`、单 chromium。结构自检对违反前两条的产出 SHALL 拒收，且对 config `webServer.port` 与该 PRD 机械分配端口不符的产出 SHALL 拒收（command 照抄 e2e_dev_cmd 而端口错位 = webServer 死等超时；command 实际监听端口的运行时对齐由 prompt 纪律 + verify 面真跑兜底，机械面只单侧校验 port 字段）。

#### Scenario: sleep 写法被拒

- **WHEN** test-author 产出文件含 `page.waitForTimeout(`
- **THEN** 结构自检失败，套件拒收并回执原因

#### Scenario: 无 webServer 声明被拒

- **WHEN** 产出套件的 playwright.config 无 `webServer` 节
- **THEN** 结构自检失败，套件拒收并回执原因

#### Scenario: config port 与机械分配端口错位被拒

- **WHEN** 产出套件的 playwright.config `webServer.port` 非 PRD 机械分配端口（如 command 照抄 e2e_dev_cmd 自带异端口）
- **THEN** 结构自检失败，套件拒收并回执原因

### Requirement: executor 镜像浏览器环境（template 发版路径）

镜像改动 SHALL 走 template repo 投影（build context 唯一来源 = template tag checkout，`executor-image` spec 契约），MUST NOT 改 `hub/deploy/Dockerfile`（hub thin 镜像零共享层）。投影 Dockerfile SHALL 全局安装 playwright（锁版本区间）+ chromium（含系统依赖）并设 `NODE_PATH=/usr/local/lib/node_modules`，经 template tag 发版与 `pa-executor` tag 咬合。

#### Scenario: 容器内零仓内依赖可执行

- **WHEN** 执行容器内对运行时 clone 的目标仓（node_modules 无 `@playwright/test`）执行注入套件 `playwright test e2e_x.spec.ts`
- **THEN** `import '@playwright/test'` 经 NODE_PATH 从镜像全局 resolve，chromium 就地可用——零下载、零外网、零目标仓依赖改动

### Requirement: dev server 生命周期归 playwright webServer

E2E 套件执行期的 dev server 起停 SHALL 由 playwright.config `webServer` 管（起/复用/杀全自动），dev 与 verify 会话 SHALL NOT 各自手工起停 server。

#### Scenario: verify 会话免起 server

- **WHEN** verify agent 在未预起 dev server 的容器内执行 declared 命令 `playwright test <file>`
- **THEN** playwright 按 config webServer 自动起 server（固定端口）跑套件，结束自动杀进程

### Requirement: 测试门同权

发布门 SHALL 将 playwright 新绿与 jest/pytest 新绿同权计为「新的绿色测试证据」。flaky（retries=1 后绿，exit 0）SHALL 计绿；连续两红 SHALL 计真红。

#### Scenario: E2E 新绿过门

- **WHEN** dev 产出仅含 e2e 套件新绿（无 jest 测试新增）且目标仓声明 playwright
- **THEN** 测试门按新绿放行，不因 harness 形态降权
