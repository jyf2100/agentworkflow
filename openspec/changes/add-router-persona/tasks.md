## 0. 勘察（实装前置）

- [x] 0.1 mm_intake/thread_relay 的帖集分流点确认（D2 旁路管道形态）；notifier 频道回帖
  复用面；persona_call 壳的 per-agent 路由透传确认（PA_PERSONA_MODEL_PA_ROUTER）
- [x] 0.2 executor 镜像 .claude/agents 投影面核验（新 persona md 进镜像的 COPY 路径）；
  Read 白名单在 headless 形态的约束力实证（tools 收窄是否真限制 Read 范围）

## 1. 实现

- [x] 1.1 `.claude/agents/pa-router.md` persona（D1 契约）；feature_flags `router_persona`
  → `PA_ROUTER_PERSONA`（默认 off）
- [x] 1.2 hub/router_relay.py：预筛正则（HUB_ROUTER_PATTERN 可配）+ 预算闸（HUB_ROUTER_RATE）
  + 注入块拼装（queue_snapshot/runs/dispatch 路径/提案 ls）+ headless 调用 + 频道回帖 fail-open
- [x] 1.3 thread_relay 分流接缝（D6 互斥）；hub.env.example 三键 + RUNBOOK §27

## 2. 测试与验证

- [x] 2.1 单测：预筛三形态/预算闸超限静默/注入块拼装（值不入面断言）/互斥分流（直通帖不进
  router）/失败静默 fail-open
- [ ] 2.2 真机：flag 开 → 绑定频道发「#110 为什么阻断」→ 预筛命中 → 回帖含 gate_reason 事实；
  闲聊消息零调用（日志核验）

## 实施证据（2026-11-01）

- 0.1：persona_call per-agent 路由既有面确认（PA_PERSONA_MODEL_PA_ROUTER env 键自动匹配）；executor 镜像 COPY /opt/pa/.claude/agents/*.md → /home/pa/.claude/agents/（新 persona 随 build 投影）；notifier._post_bound_channel fail-open 回帖面
- 0.2：thread_relay 分流点：直通拒帖（窗外/授权外）收集为 router 候选列表（一帖绝不双消费 D6 互斥）；Read 白名单在 headless --agent 形态下 tools:Read 收窄即可（persona md 声明面）
- 1.1：.claude/agents/pa-router.md persona（tools 仅 Read/haiku/四条纪律/输出格式）；flag = PA_ROUTER_PERSONA env（feature_flags 面外——hub 侧 env 直读，路由面专属键）
- 1.2：hub/router_relay.py：prescreen 三形态正则 + build_context_block（值不入面）+ answer_question（subprocess headless 60s）+ relay_router_answers（预算闸 + 频道回帖 fail-open）
- 1.3：thread_relay 返回值改 list[dict]（直通拒帖集给 router）+ lifecycle 挂点串联（直通→候选→router）+ hub.env.example + RUNBOOK §27
- 2.1：test_router_relay 5 测试（预筛三形态/注入块值不入面/预算闸/flag 关零行为/失败静默）+ test_thread_relay 4 测试适配新返回值；hub/quality.sh 全绿
- 2.2 真机：flag 开后绑定频道发「#111 为什么」→ 预筛命中 → router 回帖含 dispatch 事实——列观测项
