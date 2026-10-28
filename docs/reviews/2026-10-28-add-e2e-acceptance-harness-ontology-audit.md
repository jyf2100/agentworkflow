# add-e2e-acceptance-harness 本体论审核（全方位 · 场景测试需求矩阵）

- 日期：2026-10-28
- 对象：`openspec/changes/add-e2e-acceptance-harness/` 四工件（立项后审核，工件已按本报告修复）
- 方法：声明本体（实体/关系/不变式/判定权）× 代码现实核验 × 场景矩阵压测
- 代码现实锚点：`verify_anchors.py:74`（runner 分支解析）、`verify_agent.py:73-82`（declared 同源契约）、`hub/deploy/Dockerfile`（hub thin 镜像）、`openspec/specs/executor-image/spec.md`（build context 唯一来源）、`openspec/specs/template-release/spec.md`（tag 即 build context）

## 一、场景矩阵 × 本体覆盖

| 场景维度 | 具体场景 | v1 覆盖 | 判定 |
|---|---|---|---|
| 测试层次 | 单元/组件 | jest/pytest（既有） | ✓ |
| | 集成/API（同进程） | jest/pytest | ✓ |
| | Web E2E（起服务+浏览器） | **本 change 新增** | ✓（修复后） |
| | 视觉快照对比 | 显式 out-of-scope | ✓ 边界清晰 |
| | 性能/压测 | 无 | — 立项外 |
| 项目类型 | Node 工具链项目 | playwright v1 | ✓ |
| | Python 项目 Web E2E | **不覆盖** | F4：显式声明边界 |
| 验收点形态 | 纯逻辑 | 走 E2E 可用但次优 | F6：D6 场景语义 |
| | 交互/流程/认证态/多页 | playwright 能力面内 | ✓ |
| 执行环境 | 镜像内零外网 | NODE_PATH 方案（修复后） | F2：Critical 已修 |
| | dev server 起停 | webServer 声明（修复后） | F3：High 已修 |
| 判定 | flaky/retries | exit code 判据 + 计绿语义 | F5：已声明 |

## 二、Findings

### F1（Critical · 声明-现实错位）镜像实体锚错

tasks 原文「`hub/deploy/Dockerfile` 增 playwright 层」。现实：该文件是 **hub thin 镜像**（控制面常驻物，注释明言与执行镜像零共享层）；且 `executor-image` spec 咬死 **build context 唯一来源 = template tag checkout**。按原文实施 = 错镜像装层 + 违 spec 双重错误，E2E 在执行容器照样跑不动而 hub 镜像无谓膨胀。
**修复**：design D4 / specs「executor 镜像浏览器环境」/ tasks 3.x 全部改 template repo 投影路径（npm i -g + chromium --with-deps + NODE_PATH），经 template tag 发版咬合 pa-executor tag；tasks 显式「MUST NOT 改 hub/deploy/Dockerfile」。

### F2（Critical · 本体断层）「镜像全局装」≠「仓内可 resolve」两层环境耦合未声明

执行镜像运行时 clone 目标仓（spec 明言目标仓代码 MUST NOT 打进镜像）→ 仓 `node_modules` 无 `@playwright/test`。原 D2 declared 命令 `npx playwright test`：npx 本地无包时走 cache 下载（外网，自违 D3 零外网纪律）或交互提示卡死（headless 无 stdin）。「镜像全局 playwright」与「仓内 import resolve」是两个本体层，工件未声明其耦合。
**修复**：declared 命令改 `playwright test <rel>`（全局 bin 直调）；镜像层 `NODE_PATH=/usr/local/lib/node_modules` 使 `import '@playwright/test'` 全局 resolve——零仓内依赖零网络。specs 新增 Scenario「容器内零仓内依赖可执行」，tasks 3.2 冒烟以此为前提。

### F3（High · 本体缺失）dev server 生命周期主体缺位

D3 原文「dev server 起在 127.0.0.1 固定端口」——谁起、怎么起、谁杀，本体里没有主体。test-author Read-only（不读仓）无从得知目标仓 dev 命令（`npm run dev`? vite? 端口?）——E2E 最核心前置无着落。
**修复**：profile 声明面扩 `e2e_dev_cmd` 键（声明 playwright 必填，缺失前置拦收回执指引）；playwright.config `webServer`（command=profile 声明、固定端口段 sha 取模、reuseExistingServer）——起/复用/杀全自动归 playwright，dev 与 verify 会话零手工起停。specs 新增 Requirement「dev server 生命周期归 playwright webServer」。

### F4（High · 场景缺口）Python 项目 E2E 边界未声明

playwright 声明钉死 TS 形态（`e2e_*.spec.ts` + node 工具链）——Python 项目（aigw 类）的 Web E2E 需求 v1 覆盖不到，但 proposal out-of-scope 未提，读者会误以为通用。
**修复**：proposal/specs 显式「v1 playwright = Node 工具链项目专属；Python E2E（pytest-playwright）v2 另议」。

### F5（Medium · 判定契约缺口）flaky 判定语义未声明

D3 `retries=1` 存在但「重试后绿算什么」未声明——实施者可能把 flasy（首红重试绿）当 invalid 或红。
**修复**：design D2 / specs 测试门 requirement 显式：retries 后绿（exit 0）计绿、连续两红计真红（match_declared 只看 declared 命令 exit code）。

### F6（Medium · 场景注记）混合验收点单形态代价

单 PRD 纯逻辑 + 交互验收点并存时，单 harness 声明强制验收面全走 E2E（可用但慢）；混合自然吸收点在 dev 侧（dev 自写仓内单测过测试门，验收面单形态保判据清晰）。
**修复**：design 新增 D6 场景语义；多 harness 声明维持 out-of-scope（v2）。

### F7（复核通过项）行锚解析与 node 面适配点真实

`verify_anchors.parse_failing_anchors(testout, runner)` 已 runner 分支（jest `● name + at (file:line:col)`）——playwright formatter 是加分支非新管道 ✓；executor 镜像 node + claude CLI 已在（executor-image spec「执行链运行全集」）✓。tasks 1.3 方向成立。

## 三、结论

本体断层两处 Critical（F1 镜像实体、F2 环境耦合）若带进实施，产物是「hub 镜像膨胀 + 执行容器内 E2E 跑不动 + 违 spec 契约」的静默坏链——与 run #75 首版事件寻址 bug 同型（单测真空绿掩盖真机断裂）。修复后本体自洽：三层环境（镜像全局 / 仓内 / profile 声明）边界显式，场景边界（Python/快照/混合）显式，判定语义（flaky/测试门）显式。工件已按 F1-F6 全量修复，可进实施。
