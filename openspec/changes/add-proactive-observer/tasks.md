## 0. 勘察（实装前置）

- [ ] 0.1 load_profiles 对未知键容忍度核验（proactive 块零 pa 影响）；gh api actions/runs
  端点形态与 token scope 实证（GITHUB_TOKEN 对公开仓 runs 可读性）
- [ ] 0.2 default_branch 获取路径（repo meta 缓存）；event_poller 拉取形态复用点确认

## 1. 实现

- [ ] 1.1 `hub/proactive_observer.py`：声明装载（容错/规则键校验 fail loud/mode auto 降级 warn）
  + daemon 第七低频槽 `HUB_OBSERVER_TICKS`（config 同型校验）
- [ ] 1.2 T1/T2 判据（D2 参数面）+ gh api 三态（fake client 注入接缝，event_poller 同模式）
- [ ] 1.3 ShadowProposal 落盘（D3 schema/幂等 hash/streak 锚定）+ digest 影子节（计数+top1+采纳比）
- [ ] 1.4 hub.env.example 键 + RUNBOOK §26（启用块教学 + 采纳关联教学 + 与 event-sources 边界表）

## 2. 测试与验证

- [ ] 2.1 单测：声明容错/T1 连红判据（含 streak 锚幂等）/T2 速率判据（基线×K+绝对下限）/
  三态 UNKNOWN 跳轮/提案 schema 断言/auto 降级 warn/digest 节
- [ ] 2.2 真机：白名单仓（jyf2100/agentworkflow 自己）注入 CI 红（手动 fail 一次 workflow ×2）
  → 提案落盘 → 日报节可见 → evidence 关联试一次采纳链
