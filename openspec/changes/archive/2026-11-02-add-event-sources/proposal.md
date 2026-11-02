# Proposal: add-event-sources

## Why

pa 的 fetch 是每日批处理（cron 03:17 一批 + MM 命令式入队）——环境事件（目标仓 issue/PR 评论/CI 红）不驱动任何东西，发现延迟以天计。演进路线 P1-⑤（dsh-superagent 事件驱动 Trigger 的 pa 翻译）：事件即 fetch 的增量形态，radar 之后的链路（打分→PRD→critic→dispatch）原样复用，「机械活/语义活」教义在 Trigger 层的两级过滤体现。

## What Changes

1. **hub 事件轮询器**（daemon 又一低频槽）：对白名单仓轮询 GitHub 事件（issues/comments/CI 状态，gh api——stale_check 同依赖），落**事件增量文件**进项目语料目录（fetch 段既有 discovery 形态的增量伴生）。
2. **两级过滤**：Fast Path 机械筛（hub 侧：作者 ∈ 成员三态授权面、事件类型白名单、内容去重——零 LLM）→ 通过者落盘；Slow Path 语义筛不新增（radar 既有职责，消费增量文件即完成）。
3. **配置**：profiles 增 `events:` 声明（仓列表 + 事件类型 + 轮询周期 HUB_EVENT_TICKS）；未声明 = 零行为。

## Non-Goals

- 不做 webhook/公网入口（hub 无公网面，轮询先行）。
- 不做多平台（v1 GitHub 单源；飞书/微信另案）。
- 不改 radar/prd/critic 链路一字（事件以语料增量形态进入，编排零改动）。
- 不做事件→直接派发的短路（必须过 radar→PRD→critic 全链——授权链不动）。
