# 0002 — 投递契约：pipeline 只送 PRD+信息源，项目自治完整 dev loop

## 决定

**pipeline 是薄的"投递员"，项目是自治的完整 dev loop。** pipeline 对每个白名单项目的唯一契约 = 投递两样东西：① [[PRD]] 文件 ② [[信息源]]（PRD 所依据的原始文章/信号）。投完即撤，不定义、不插手项目内部。

项目用自己的 [[开发 agent]] **自治跑完整闭环**：需求分析 → 设计 → 开发 → review → 回归 → 产出 PR。**文件范围（boundaries）、设计、代码质量、测试、review/回归流程 —— 全归项目自管**（写在该仓的 CLAUDE.md + dev agent，准入时由 roc 定）。pipeline profile **不再有 `boundaries` 字段**。

## 两层安全（重划）

| 层 | 归属 | 内容 |
|---|---|---|
| **投递层机械刹车**（不可谈） | pipeline | branch-only / 永不 merge / 永不碰主干 / `max_prs_in_flight` / wall-clock / 不污染目标仓（ADR-0001）/ **独立验证闸**（收 PR 后独立跑项目测试，红→failing） |
| **项目自治** | 目标仓 | 文件范围、设计、质量、测试、review/回归流程 |

## 独立验证闸（方案 A）

dispatch 收到项目产出的 PR 后，**独立再跑一次项目自己的测试**（`npm test`，或从目标仓 `package.json` 的 `scripts.test` 自行发现命令），红则标 failing 入报告。这是**验证**（验项目自报的"绿"是否属实），**不是 scope 规定**——不违背项目自治。

## 背景

用户明确："1、允许项目自治。2、docs/ 是项目自己的文件组织，你只是推送一个prd，项目仓根据你推送的prd文件和信息源，自己分析实现需求设计-开发-review-回归。"

## 考虑过的替代

- **pipeline 给项目画 boundaries + 强制 test gate**（更多控制、更少自治）—— 拒绝：违背"项目自治"，且 pipeline 不该替项目决定能改哪些文件。
- **纯信任项目自评、不做独立验证**（方案 B）—— 拒绝：full-auto 下缺一道机械兜底。保留独立验证闸（A）因为它是验证非干预。
