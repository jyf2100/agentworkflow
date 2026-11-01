## 0. 勘察（实装前置）

- [ ] 0.1 fetch 段 inbox/discovery/去重机制现状与事件文件落点对齐（机械伴生形态可行性）
- [ ] 0.2 gh api 事件端点（issues/comments/statuses）限流与三态分流口径；成员三态授权面读取接缝

## 1. 实现

- [ ] 1.1 `hub/event_poller.py`：profiles `events:` 声明解析（未声明零行为）+ daemon 低频槽 + gh api 轮询三态
- [ ] 1.2 Fast Path 机械筛（作者授权面/类型白名单/sha 去重）+ 事件文件落盘（frontmatter 机械拼接）
- [ ] 1.3 `HUB_EVENT_TICKS` 配置 + hub.env.example + RUNBOOK 节

## 2. 测试与验证

- [ ] 2.1 单测：声明解析/三态/筛判据/去重/落盘形态（fake gh client）
- [ ] 2.2 真机：白名单仓造一条 issue 评论 → 轮询周期内落事件文件 → 次日 fetch/radar 链路消费到 PRD
