---
project: cc-web-control
title: "canary 故意红：验证 post-merge auto-revert 闭环（判据 b）"
source_path: ''
date: '2026-07-30'
signal: cc-web-control-canary-red
round: 1
slug: cc-web-control-canary-red
---

## 背景（canary 专用，破坏性意图明确）

single-flight-auto-merge §8.1 canary 判据 (b)：验证「main 合红后 post-merge 全量测试判 FAIL → auto-revert REVERTED → main 回绿」闭环。
本 PRD 是该判据的**确定性投递载体**（`run_daily.py --from-stage inject --inject-prd <本文件>`）。

## ⚠️ 双绿约束下的真相（操作者必读）

dispatch 的前置是「dev + verify 双绿 → 才 merge」。本 PRD 的产出是**故意失败**的测试，
故走 `--inject-prd` 经 dev loop 时，**verify 闸会判红 → 不会 merge → 到不了 post-merge**。
即：经 dev loop 自然路径**无法**触发判据 (b)。

因此判据 (b) 的**主路径不是本 PRD 经 dev loop**，而是 `canary_automerge.sh inject-red`：
正常闭环 (a) 后，**直接向 cc-web-control main 注入红 commit**（绕过 dev loop，对齐离线 drill
`test_post_merge_fail_then_revert_restores_green` 的 fixture 模式），再触发 `--phase post-merge-test`
→ FAIL → `--phase revert` → 观察 main 回绿。

本 PRD 仅作：① 主路径注入红 commit 时，红测试文件的内容模板；② 文档化双绿约束的边界。
**不要**期望本 PRD 经 `--inject-prd` 单独跑出 post-merge auto-revert。

## 目标

在 cc-web-control 新增一个**故意失败**的测试文件，作为 post-merge 红 commit 的载体。

## 验收标准

1. 新增 `test/canary-red.test.cjs`，使用 `node:test` + `node:assert/strict`（与仓内现有测试风格一致，
   见 `test/config_loader.test.cjs`）。
2. 文件含**一个故意失败的断言**，例如：
   ```js
   const test = require('node:test');
   const assert = require('node:assert/strict');
   test('canary: 故意红——验证 post-merge auto-revert（判据 b），勿修复', () => {
     assert.strictEqual(1, 2, 'canary 故意红：此断言预期失败，用于触发 auto-revert 闭环');
   });
   ```
3. **不修复该失败**——本测试的语义就是「红」。canary 观察的是流水线对「红」的处置（auto-revert），
   不是让测试变绿。
4. 不修改任何现有源码、配置或既有测试文件；改动仅限新增 `test/canary-red.test.cjs`。

## 范围约束

- 本任务为 canary auto-revert 验证，**不实现新功能、不重构、不改动既有逻辑**。
- 红测试的 `slug`（`cc-web-control-canary-red`）是判据 (c) 熔断的幂等键：同 slug 再投，
   circuit breaker（`circuit_breaker.is_in_cooldown`）应命中 → triage(cooldown_revert_loop)。
- canary 结束后，`test/canary-red.test.cjs` 与任何因它产生的 merge/revert commit 都应被清理
   （main 回绿 + 无残留红 commit = 通过判据）。
