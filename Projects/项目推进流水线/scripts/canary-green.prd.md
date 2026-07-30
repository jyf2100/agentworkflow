---
project: cc-web-control
title: "canary 绿 smoke：验证正常 auto-merge 闭环（判据 a）"
source_path: ''
date: '2026-07-30'
signal: cc-web-control-canary-green
round: 1
slug: cc-web-control-canary-green
---

## 背景（canary 专用）

single-flight-auto-merge §8.1 canary 判据 (a)：验证「dev+verify 双绿 → rebase CLEAN → --no-ff merge 进 main
+ ff-only push → post-merge main 全量测试 PASS → 保留 merged」正常闭环。本 PRD 是该判据的确定性投递载体
（`run_daily.py --from-stage inject --inject-prd <本文件>`），产出**trivial 绿**测试，确保 dev loop + post-merge 都绿。

## 目标

在 cc-web-control 新增一个最小且**必然通过**的 smoke 测试文件，走完 auto-merge 正常闭环。

## 验收标准

1. 新增 `test/canary-green.test.cjs`，使用 `node:test` + `node:assert/strict`（与仓内现有测试风格一致，
   见 `test/config_loader.test.cjs`）。
2. 文件含**一个必然通过**的断言，例如：
   ```js
   const test = require('node:test');
   const assert = require('node:assert/strict');
   test('canary: 绿 smoke——验证 auto-merge 正常闭环（判据 a）', () => {
     assert.ok(true, 'canary 绿 smoke：trivial passing，确保 dev loop + post-merge 都绿');
   });
   ```
3. 运行 `npm test`（即 `node --test test/*.test.cjs`）退出码 0，全部测试通过（含新 smoke 测试）。
4. 不修改任何现有源码、配置或既有测试文件；改动仅限新增 `test/canary-green.test.cjs`。

## 范围约束

- 本任务为 canary 正常闭环验证，**不实现新功能、不重构、不改动既有逻辑**。
- 遵循仓内 CLAUDE.md 与现有测试风格（.cjs 扩展名、node:test）。
- canary 结束后，`test/canary-green.test.cjs` 可保留（trivial 绿，无害）或随 canary 清理一并移除。
