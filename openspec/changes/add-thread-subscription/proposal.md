# Proposal: add-thread-subscription

## Why

hub↔MM 是单向通知面：run 进行中 events.jsonl 只读转发到频道，人在频道里说的话（「改用方案 B」「先跳过第 2 条验收」）进不了任务上下文——只能等 run 终态后再入队一个新需求。演进路线 P1-⑥（dsh「订阅直通」的 pa 翻译）：已归属任务的后续人话经 Gate 直发该 run 的上下文面，不新开 PRD、不新增授权。

## What Changes

1. **频道回复捕获**：绑定频道内、在飞 run 生命周期窗口中的 bot 消息之后的人话（MM 读 API——HttpMMClient 已有通道基础），按 (channel, run) 关联到最老在飞 run。
2. **Gate（授权不扩大）**：仅成员三态授权面内作者的话被采纳；其他人话忽略（与 /pa 入站同门）。
3. **落点两档（v1 取安全档）**：排队面——人话落 run 伴生 `human_notes/`，dev handoff 机制既有 `--handoff-artifact` 注入面在**下一 iteration** 带入（仅上下文非判据，handoff 语义既有）；不中断不改写在飞 run。
4. **回执**：采纳后经既有事件面向频道回一条「已记入 #run 下一轮上下文」（去抖）。

## Non-Goals

- 不做在飞 run 的实时中断/改写（dsh 全双工的完整形态是长期方向，v1 只做安全的排队注入）。
- 不做跨信道身份映射（dsh rejected-concepts 同款拒绝——IM 身份与 git 身份的映射是提示词/人工面）。
- 不新增派发授权（订阅直通只路由上下文；派发权仍在 /pa run 确认环）。
- 不依赖 LangGraph 收尾（可与 add-event-sources 并行起草；实施顺序听路线注记）。
