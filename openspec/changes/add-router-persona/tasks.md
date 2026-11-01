## 0. 勘察（实装前置）

- [ ] 0.1 mm_intake/thread_relay 的帖集分流点确认（D2 旁路管道形态）；notifier 频道回帖
  复用面；persona_call 壳的 per-agent 路由透传确认（PA_PERSONA_MODEL_PA_ROUTER）
- [ ] 0.2 executor 镜像 .claude/agents 投影面核验（新 persona md 进镜像的 COPY 路径）；
  Read 白名单在 headless 形态的约束力实证（tools 收窄是否真限制 Read 范围）

## 1. 实现

- [ ] 1.1 `.claude/agents/pa-router.md` persona（D1 契约）；feature_flags `router_persona`
  → `PA_ROUTER_PERSONA`（默认 off）
- [ ] 1.2 hub/router_relay.py：预筛正则（HUB_ROUTER_PATTERN 可配）+ 预算闸（HUB_ROUTER_RATE）
  + 注入块拼装（queue_snapshot/runs/dispatch 路径/提案 ls）+ headless 调用 + 频道回帖 fail-open
- [ ] 1.3 thread_relay 分流接缝（D6 互斥）；hub.env.example 三键 + RUNBOOK §27

## 2. 测试与验证

- [ ] 2.1 单测：预筛三形态/预算闸超限静默/注入块拼装（值不入面断言）/互斥分流（直通帖不进
  router）/失败静默 fail-open
- [ ] 2.2 真机：flag 开 → 绑定频道发「#110 为什么阻断」→ 预筛命中 → 回帖含 gate_reason 事实；
  闲聊消息零调用（日志核验）
