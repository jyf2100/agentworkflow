## 0. 勘察（实装前置）

- [ ] 0.1 MM bot token 读频道历史权限面实证；水位存储形态（db 列 vs 小表）
- [ ] 0.2 human_notes → --handoff-artifact 装配接缝（dev handoff 机制的 orchestration 侧消费点）
- [ ] 0.3 hub/events.py 事件枚举扩展口径（回执事件两侧同步注册）

## 1. 实现

- [ ] 1.1 HttpMMClient 读频道消息（since 水位）+ (channel, run) 关联 + Gate 授权筛
- [ ] 1.2 human_notes 落盘（署名/幂等去重）+ 下一 iteration 装配注入
- [ ] 1.3 采纳回执事件（频道，去抖）+ daemon 低频槽 + RUNBOOK 节

## 2. 测试与验证

- [ ] 2.1 单测：关联判据/Gate/幂等/水位（FakeMMClient 既有先例）
- [ ] 2.2 真机：在飞 run 频道回一句 → human_notes 落盘 → 下一 iteration 注入可见 → 回执一条
