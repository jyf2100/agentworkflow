# add-e2e-acceptance-harness — tasks

## 1. pa 编排器 harness 面

- [ ] 1.1 `run_daily.py`：harness 拦截面（:950）集扩 `("playwright",)`；profile 读面增 `e2e_dev_cmd` 键（声明 playwright 时必填，缺失 = test-author 调用前置拦截、回执指引补声明）；`_test_author_prompt` 增 playwright 分支（文件形态 `e2e_*.spec.ts` + D3 纪律专则：条件等待/webServer（command=profile e2e_dev_cmd、端口段 4173-4199 sha 取模、reuseExistingServer）/retries=1/单 chromium）
- [ ] 1.2 `run_daily.py`：`_structural_self_check` 增 `_E2E_FILE_RE` 分支（`e2e_[\w\-]+\.spec\.ts` 形态不匹配拒收 + `page.waitForTimeout(` 出现即拒收 + playwright.config 无 `webServer` 节拒收——纪律 1/2 机械面）
- [ ] 1.3 `verify_agent.py`：`declared_suite_cmds_for` 增 `playwright test <rel>` 分支（全局 bin 直调非 npx，docstring 支持集同步 v2 + NODE_PATH 全局 resolve 依据）；`verify_anchors.parse_failing_anchors` 增 playwright runner 分支（`Error:` + `at e2e_x.spec.ts:LINE:COL` 形态；flaky retries 后绿 exit 0 计绿注记）

## 2. test-author persona

- [ ] 2.1 `.claude/agents/pa-test-author.md`：harness 声明面更新为三值（jest/pytest/playwright，v1 playwright = Node 工具链项目专属）；增 playwright 形态说明（`e2e_*.spec.ts`、criterion id 标注纪律不变、refused 三态不变——缺 `e2e_dev_cmd` 声明为 refused 典型因）

## 3. executor 镜像（template 发版路径——MUST NOT 改 hub/deploy/Dockerfile）

- [ ] 3.1 template repo 投影 Dockerfile 增层：`npm i -g playwright@<锁区间>` + `playwright install chromium --with-deps` + `ENV NODE_PATH=/usr/local/lib/node_modules`；template tag 发版（release note 注记体积影响 +~400MB 与 playwright 锁版区间），`pa-executor` tag 随发版咬合
- [ ] 3.2 发版镜像容器内冒烟：`playwright --version` + 最小 `e2e_smoke.spec.ts`（`import '@playwright/test'` 从全局 resolve + data URL 断言标题）就地跑绿——**目标仓 node_modules 无 playwright 前提下**零外网零仓内依赖

## 4. 测试与回归

- [ ] 4.1 单测：harness=playwright 全链（声明放行/缺 e2e_dev_cmd 前置拦/file 形态自检/sleep 与无 webServer 拒收/命令映射 `playwright test` 非 npx/flaky 计绿/未声明项目零变化）——离线 fixture
- [ ] 4.2 `bash scripts/quality.sh` + `bash hub/quality.sh` 双绿
- [ ] 4.3 真机坐实（发版后）：cc-web-control profile 临时声明 `harness: playwright` + `e2e_dev_cmd`，发一条 E2E 可测需求（如「主题切换按钮点击后 body data-theme 变更」），全链入队→test-author 产 e2e 套件（webServer 起停自动）→dev→verify 机械锚→终态回流频道；坐实后 profile 还原与否听用户
