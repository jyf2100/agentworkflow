## Context

MM 入站现状：slash `/pa` → mm_intake 硬编码分流；**非 slash 消息完全无处理**（bot 只读不回）。
hub 数据面全结构化：runs 表 / dispatch_*.json（state 卷）/ 影子提案目录 / 漂移 seen。
dsh「Router 只分派不干活（轻模型）+ Worker 重模型」的双层思想，在 pa 的正确投影是：
**确定性调度（hub 六槽）之上、人机语义交互之处**挂轻模型——不是把调度交给 LLM。

## 本体论声明

- **Router persona = 解释者（interpreter）**，范畴：交互面观察层的语义组件。与调度者（scheduler，
  hub 确定性代码）是两个范畴——本 change 的 spec 把这条边界写成硬约束（行动权=零）。
- **问答的同一性**：一问一会话（无记忆）——上下文不靠对话史靠**注入块**（机械拼装的事实快照），
  同问可复现（与 durable runtime 的可重放精神一致）。
- **机械预筛 = 交互面的 Fast Path**（与 event-sources 同构）：零 LLM 判定「要不要花一次
  haiku 调用」——防频道闲聊烧钱。

## Decisions

### D1 persona 契约（.claude/agents/pa-router.md 要点）
- 角色：pa-hub 的只读值班解释员；答 run 状态/阻断原因/提案内容/今日计数。
- tools: Read（prompt 内声明仅可读注入块给出的路径白名单）；无 Bash/Write/Agent/Grep。
- 纪律三条：只引事实（引用文件名）；查不到就明说；行动建议 = 命令文本（人复制执行）。
- 输出：≤200 词中文；开头一行直接答案，证据随后（频道礼仪）。
- per-agent model：`PA_PERSONA_MODEL_PA_ROUTER`（hub.env 可配 haiku——容器形态裸名纪律）。

### D2 挂点：mm_intake 的旁路消费
daemon 复用 relay 槽（HUB_RELAY_TICKS 同轮）：thread_relay 读到的人话里，**未被直通采走的**
（授权面外/无在飞 run/窗口外）不丢弃——进 router 预筛（D3）。两消费共享一次 MM 读（水位
不变——router 消费不推进水位？NO：直通水位按帖推进，router 看同批已处理帖。实现：relay
循环里对被拒/窗外帖转发给 router 管道（in-process list），不在本槽二次拉取）。
 Slash 消息（command 字段）永不进 router。
- 回帖：notifier 既有 `_post_bound_channel`（fail-open）。

### D3 机械预筛（零 LLM 判定调不调）
正则三选一命中才调 router（缺省，`HUB_ROUTER_PATTERN` 可覆盖）：
- `#\d+`（run 引用）② `sp-[0-9a-f]{4,}`（提案）③ 问句关键词集（为什么|怎么|多少|状态|blocked|triage）。
全不命中 → 静默（dsh「不需要时保持安静」）。**bot 自己的消息永不进**（防自问自答循环——
bot_id 排除复用 relay 的）。

### D4 上下文注入块（机械拼装，值不入面纪律全承）
```
## 值班上下文（机械注入，非对话记忆）
- 被引用 run #110：status=blocked_test_gate / gate=test_stale / dev_cost=$3.9 / turns=24
  详情文件：/data/pa/cc-web-control/state/dispatch_20261101.json（可 Read）
- 活跃影子提案：sp-a1b2（ci_red_streak, 2h 前）× 1
- 今日：pending 0 / running 1 / 24h succeeded 2 failed 1
```
拼装源全既有（queue_snapshot / runs 表 / dispatch json 路径 / shadow_proposals 目录 ls）。
凭据/token 绝不入块（Read 白名单限定 state 面文件）。

### D5 调用与预算
headless `claude --agent pa-router -p <prompt> --output-format json --max-turns 4`
（persona_call 既有壳，wall-clock 60s 上限）；每频道每小时至多 N=6 次（`HUB_ROUTER_RATE`
缺省 6——防频道刷屏烧钱，超限静默）。成本面：haiku 档单答 ~$0.001 级。

### D6 与 thread-subscription 的边界
同一批人话两个消费面互斥分流：在飞 run 窗口内 + 授权面内 → **直通**（人话给 dev 上下文）；
其余（无在飞 run / 窗口外 / 授权外）→ **router 候选**（预筛后问答）。一个人话绝不双消费
（范畴互斥：上下文素材 vs 问答对象——前者属于 run，后者属于频道氛围）。

## 状态机（单轮）

```
relay 读帖 → 直通分流（既有）→ 拒/窗外帖集 → 预筛正则 → 未命中：静默
                                              → 命中：预算闸 → 组装注入块 → headless 调用
                                              → 60s 内有答 → 频道回帖（fail-open）
                                              → 超时/失败 → stderr 留痕静默
```

## Rollout 与回滚

flag `PA_ROUTER_PERSONA`（feature_flags 域切割，loop 域前缀——persona 面惯例），默认 off
（byte-identical）；启用 = hub.env PA_* 透传链。回滚 = 关 flag。persona md 进仓（.claude/agents
随 git，executor 镜像已带 agents 目录投影——需进 Dockerfile COPY 面核验，任务 0.2）。

## Metrics

- 问答命中率（回帖 / 预筛命中）、零答案率（「查不到」比例——注入块覆盖度信号）。
- 与 P2 北极星的关系：router 省下的「人翻面板时间」不直接计量——列定性收益，不计入注意力
  预算公式（防不可测指标污染）。

## Open Questions（不阻塞起案）

- 授权面外的人话是否也答？（v1 答——问答无副作用，授权门只守行动与直通；若被滥用再收紧。）
- 私信（DM）面是否接 router（v1 否——只绑定频道）。
