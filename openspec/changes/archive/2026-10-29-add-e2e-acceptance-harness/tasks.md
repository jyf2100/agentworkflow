# add-e2e-acceptance-harness — tasks

## 1. pa 编排器 harness 面

- [x] 1.1 `run_daily.py`：harness 拦截面（:950）集扩 `("playwright",)`；profile 读面增 `e2e_dev_cmd` 键（声明 playwright 时必填，缺失 = test-author 调用前置拦截、回执指引补声明）；`_test_author_prompt` 增 playwright 分支（文件形态 `e2e_*.spec.ts` + **playwright.config 为 authored file 之一**（随 bundle content-addressed 注入 worktree，覆盖目标仓既有 config）+ D3 纪律专则：条件等待/webServer（command=profile e2e_dev_cmd、端口段 4173-4199 sha 取模、reuseExistingServer）/retries=1/单 chromium）
- [x] 1.2 `run_daily.py`：`_structural_self_check` 增 `_E2E_FILE_RE` 分支（`e2e_[\w\-]+\.spec\.ts` 形态不匹配拒收 + `page.waitForTimeout(` 出现即拒收 + playwright.config（authored file，自检可见）无 `webServer` 节拒收——纪律 1/2 机械面）
- [x] 1.3 `verify_agent.py`：`declared_suite_cmds_for` 增 `playwright test <rel>` 分支（全局 bin 直调非 npx，docstring 支持集同步 v2 + NODE_PATH 全局 resolve 依据）；`verify_anchors.detect_runner` 增 playwright 映射（`"playwright" in cmd → "playwright"`——**入口不改则 parse 分支永不被调用**，现状 `playwright test` 命中 `return "unknown"`）；`verify_anchors.parse_failing_anchors` 增 playwright runner 分支（锚定主形态 = 失败头行 `[chromium] › path/e2e_x.spec.ts:LINE:COL › test name`，`at file:line:col` 栈帧行仅佐证——fixture 以镜像锁版实测输出为准；flaky retries 后绿 exit 0 计绿注记）

## 2. test-author persona

- [x] 2.1 `.claude/agents/pa-test-author.md`：harness 声明面更新为三值（jest/pytest/playwright，v1 playwright = Node 工具链项目专属）；增 playwright 形态说明（`e2e_*.spec.ts`、criterion id 标注纪律不变、refused 三态不变——缺 `e2e_dev_cmd` 声明为 refused 典型因）

## 3. executor 镜像（template 发版路径——MUST NOT 改 hub/deploy/Dockerfile）

- [x] 3.1 template repo 投影 Dockerfile 增层：`npm i -g playwright@<锁区间>` + `playwright install chromium --with-deps` + `ENV NODE_PATH=/usr/local/lib/node_modules`；template tag 发版（release note 注记体积影响 +~400MB 与 playwright 锁版区间），`pa-executor` tag 随发版咬合
      （实证 2026-10-28：release/Dockerfile 与 hub/deploy/pa-executor.Dockerfile 双投影层落地（锁 1.64.1）；镜像 pa-executor:v0.1.19 三轮 build 迭代真机 hub 派发跑通（#77 model-routing 污染、#78 /opt/pa 属主两缺陷修复后 build3 定版，commit 2433009 chown /opt/pa 整树））
- [x] 3.2 发版镜像容器内冒烟：`playwright --version` + 最小 `e2e_smoke.spec.ts`（`import '@playwright/test'` 从全局 resolve + data URL 断言标题）就地跑绿——**目标仓 node_modules 无 playwright 前提下**零外网零仓内依赖
      （实证 2026-10-28 超额：非最小冒烟——本机 v0.1.19 容器内空 node_modules 目标仓注入套件真跑 2 passed EXIT=0（webServer 自动起 4174 + 故意红锚解析全过）；真机 #79 容器内 test-author 产套件注入 dev 会话）

## 4. 测试与回归

- [x] 4.1 单测：harness=playwright 全链（声明放行/缺 e2e_dev_cmd 前置拦/file 形态自检/sleep 与无 webServer 拒收/命令映射 `playwright test` 非 npx/detect_runner 映射与 playwright 失败输出锚解析（锁版实测 fixture）/flaky 计绿/未声明项目零变化）——离线 fixture
- [x] 4.2 `bash scripts/quality.sh` + `bash hub/quality.sh` 双绿
- [x] 4.3 真机坐实（发版后）：cc-web-control profile 临时声明 `harness: playwright` + `e2e_dev_cmd`，发一条 E2E 可测需求（如「主题切换按钮点击后 body data-theme 变更」），全链入队→test-author 产 e2e 套件（webServer 起停自动）→dev→verify 机械锚→终态回流频道；坐实后 profile 还原与否听用户
  - **坐实（2026-10-28，hub run #85 / 容器 m16 / 镜像 v0.1.24，PRD = e2e-login-version-footer）**：全链一次过——入队 → critic PASS → test-author 在 playwright harness 产 e2e 套件（2 文件，3 criteria 全覆盖 uncovered=0，digest 33a261c9…）→ dev 双注入落地（e2e spec 原样落盘一字未改 + playwright.config.ts 环境对接修正：CC_WEB_PORT/webServer command 重写/chromium-1248 对 /opt/ms-playwright）→ verify agent 轨执行（注入件 sha256 复核过，e2e declared 命令 exit 1 → 红，见 agent-verify-injection 3.3 注记）→ PR #60 开、红标 failing 留 review、report 段正确分类（failing 1 + 🔴标注，非静默全绿）。终态已回流（run 容器日志 + 项目推进报告_20261028m16.md）。profile 还原与否听用户（备份在 /tmp/cc-web-control.yaml.bak-e2e）。
