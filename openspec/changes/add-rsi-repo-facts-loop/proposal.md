# Proposal: add-rsi-repo-facts-loop

## Why

2026-10-28 三次真机 run 暴露同一根因家族的连环失败：**上游语义链盲写指标，只有 dev 掉进仓里才知道现实**。

| run | 结局 | 根因 |
|---|---|---|
| #74 | critic drop | 需求方不懂「指标化」义务（B 环解） |
| #75 | critic pass → clone 网络死 | 基础设施（偶发，不在本环） |
| #76 | critic pass → dev 60 turns **off_track 止损** | PRD 验收第 4 条「jsdom 断言 computed style」脱离仓现实：仓无 DOM 测试库（纯函数+DI 风格）、jsdom 不解析 CSS `var()`（技术不可测）。critic 判「具体可测」时**不知道仓事实**，dev 才发现，60 turns 后止损 |

既有 cross-prd-learning-memory（8 模块已落地）只把教训注入 **dev prompt**（下游执行者），且晋升门槛 ≥2 PRD 复现——**上游（prd 生成 / critic 判据 / test-author / spec-author）永远收不到仓事实**。同一个「指标 vs 仓现实」错配会反复发生：这正是 RSI（递归自我修正）缺的上半环。

## What Changes

教训**分轨**——「失败模式」与「结构事实」两类知识的成因、置信、生效速度根本不同，混用同一晋升策略是本体错位：

- **轨道 1（既有，保持）**：失败模式（偶发、需复现确证）→ ≥2 PRD equivalence 晋升 → dev prompt 注入（现状零变化）。
- **轨道 2（本 change 新增）：repo_facts 仓事实轨**——结构性强事实（测试基建能力/风格/依赖现实/技术不可测点）**一次即候选**（仓没有 DOM 库是事实不是 pattern，无需两次复现），经 **MM 频道人审 confirm**（复用 B 环确认交互形态）后沉淀进 profile `repo_facts` 节（版本化 + evidence 溯源 + 条数上限），**注入上游语义链输入面**：
  - prd 生成 prompt（MM 渠道 wrap 与 radar-PRD 路径）——指标写法贴仓现实
  - critic 判据——PRD 指标与 repo_facts 冲突 = drop/needs_metrics 的新机械理由（连 B 环）
  - test-author / spec-author prompt——测试形态选择贴仓 harness 现实

终态提取源：既有 lesson candidate 提取管道增「仓事实候选」schema 类（同 curated evidence bundle：dev 独白 log、off_track 判定、verify-report 均已落盘）。

## Out of Scope

- 轨道 1（失败模式轨）策略调整——现状保持，另案观察。
- 面板路径人审（v1 MM 渠道；面板 PRD 的仓事实候选暂存待 MM admin 审）。
- repo_facts 自动撤销/衰减——v1 仅人工 unbind 式管理（profile 编辑）。
- B 环（add-acceptance-spec-confirmation）本体——本 change 只复用其确认交互形态，无硬依赖。
