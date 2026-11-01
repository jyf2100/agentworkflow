## 0. 勘察（实装前置）

- [x] 0.1 MM bot token 读频道历史权限面实证；水位存储形态（db 列 vs 小表）
- [x] 0.2 human_notes → --handoff-artifact 装配接缝（dev handoff 机制的 orchestration 侧消费点）
- [x] 0.3 hub/events.py 事件枚举扩展口径（回执事件两侧同步注册）

## 1. 实现

- [ ] 1.1 HttpMMClient 读频道消息（since 水位）+ (channel, run) 关联 + Gate 授权筛
- [ ] 1.2 human_notes 落盘（署名/幂等去重）+ 下一 iteration 装配注入
- [ ] 1.3 采纳回执事件（频道，去抖）+ daemon 低频槽 + RUNBOOK 节

## 2. 测试与验证

- [ ] 2.1 单测：关联判据/Gate/幂等/水位（FakeMMClient 既有先例）
- [ ] 2.2 真机：在飞 run 频道回一句 → human_notes 落盘 → 下一 iteration 注入可见 → 回执一条

## 勘察证据（2026-11-01）

- 0.1：MM bot token 实测 GET channels/{id}/posts = 200 可读（2026-11-01 绑定频道 2 帖探通）——读取通道现成零新权限；水位形态建议 db 小表（runners 同款先例，勘察定案在实装时）
- 0.2：装配接缝定位：run_daily.py:2620 --handoff-artifact 组装透传（镜像 --feedback-artifact 模式）——human_notes 在此点并入注入通道（或同 flag 并列注入），零新机制
- 0.3：hub/events.py 事件枚举封闭（:13-14），pa 侧 event_outlet 写、hub 读转发——回执事件需两侧同步注册（pa-event-outlet 教训已有；实装时按 add 步骤双侧登记）
