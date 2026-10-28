# add-e2e-acceptance-harness — tasks

## 1. pa 编排器 harness 面

- [ ] 1.1 `run_daily.py`：harness 拦截面（:950）集扩 `("playwright",)`；`_test_author_prompt` 增 playwright 分支（文件形态 `e2e_*.spec.ts` + D3 确定性纪律专则文本：条件等待/本地回环端口段/retries=1/单 chromium）
- [ ] 1.2 `run_daily.py`：`_structural_self_check` 增 `_E2E_FILE_RE` 分支（`e2e_[\w\-]+\.spec\.ts` 形态不匹配拒收 + `page.waitForTimeout(` 出现即拒收——纪律 1 的机械面）
- [ ] 1.3 `verify_agent.py`：`declared_suite_cmds_for` 增 `npx playwright test <rel>` 分支（docstring 支持集同步 v2）；行锚解析器增 playwright 输出 formatter 分支（`Error:` + `at e2e_x.spec.ts:LINE` 形态）

## 2. test-author persona

- [ ] 2.1 `.claude/agents/pa-test-author.md`：harness 声明面更新为三值（jest/pytest/playwright）；增 playwright 形态说明（`e2e_*.spec.ts`、criterion id 标注纪律不变、refused 三态不变）

## 3. executor 镜像

- [ ] 3.1 `hub/deploy/Dockerfile`：增 `npx playwright install chromium --with-deps` 层（锁 playwright 版本区间）；镜像 tag 升 v0.1.19；`hub/deploy/README` 或 RUNBOOK 注记体积影响（+~400MB）
- [ ] 3.2 真机重建镜像 + 容器内冒烟：`npx playwright --version` + 最小 `e2e_smoke.spec.ts`（开 data URL 断言标题）就地跑绿——零外网

## 4. 测试与回归

- [ ] 4.1 单测：harness=playwright 全链（声明放行/文件形态自检/sleep 拒收/命令映射/未声明项目零变化）——离线 fixture
- [ ] 4.2 `bash scripts/quality.sh` + `bash hub/quality.sh` 双绿
- [ ] 4.3 真机坐实：cc-web-control profile 临时声明 `harness: playwright`，发一条 E2E 可测需求（如「主题切换按钮点击后 body data-theme 变更」），全链入队→test-author 产 e2e 套件→dev→verify 机械锚→终态回流频道；坐实后 profile 还原与否听用户
