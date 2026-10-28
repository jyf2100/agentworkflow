## MODIFIED Requirements

### Requirement: 语义事件通知（events.jsonl 机械转发）
hub SHALL 在对账 running run 时机械读取该 run state 卷出口的 `events.jsonl` 增量事件行，经通知通道转发：critic_verdict / verify_verdict / pr_opened / acceptance_suite 各转发一条私信给队列项发起人（收件人三代分流复用既有 requirement）；其中**失败/警示类**（critic drop、verify revise/invalid、acceptance absent）SHALL 同时进项目频道（对齐既有「频道广播仅失败类」噪音控制；acceptance stored 与 pass/绿类仅私信——stored 属正常落账非警示）。verify_verdict invalid 的转发文本 SHALL 机械附 `reason` 事实字段（枚举值域见 spec: pa-event-outlet——triage 排障入口）；acceptance_suite 转发文本 SHALL 机械附 `status` 与 reason/digest/supersedes 事实字段。转发文本 SHALL 为事件行事实字段的机械拼接（hub 零语义生成——不总结不评级）。读取范围 SHALL 限于 running run 的文件（run 终态后不再读——重启重发范围天然限于崩溃时活跃 run 的事件行，重复通知可接受；去抖为进程内存按 `(stamp, seq)` 键，通知零持久写面不变式不动）。契约版本不匹配或未知事件类型按 spec: pa-event-outlet 跳过。

#### Scenario: critic drop 转发
- **WHEN** 某 running run 的 events.jsonl 追加 `{event:"critic_verdict", verdict:"drop"}`
- **THEN** 发起人收到 critic drop 私信；该项目配置了频道映射时频道同步收到

#### Scenario: PR 开出转发
- **WHEN** events.jsonl 追加 `pr_opened`（含 pr_url）
- **THEN** 发起人收到含 pr_url 的私信

#### Scenario: verify invalid 转发附原因
- **WHEN** events.jsonl 追加 `{event:"verify_verdict", verdict:"invalid", reason:"anchor_violation"}`
- **THEN** 发起人收到含 invalid 与 reason 的私信；该项目配置了频道映射时频道同步收到（invalid 属警示类，triage 升人工须有可送达出口）

#### Scenario: acceptance 缺席警示进频道
- **WHEN** events.jsonl 追加 `{event:"acceptance_suite", status:"absent", reason:"no_harness"}`
- **THEN** 发起人收到私信，配置了频道映射的项目频道同步收到（无验收保护 dispatch 属团队警示）

#### Scenario: acceptance 落账仅私信
- **WHEN** events.jsonl 追加 `{event:"acceptance_suite", status:"stored", digest:"sha256:...", supersedes:null}`
- **THEN** 发起人收到私信（含 digest 摘要字段），频道无广播（stored 非警示）；supersedes 非 null（换版）时文本机械附换版链信息

#### Scenario: 已终态 run 不再读事件
- **WHEN** 某 run 已转终态（succeeded/failed）后 events.jsonl 再追加行（不该发生——防回归）
- **THEN** hub 不读不转发（读取面只在 running 扫描集内）

#### Scenario: 通知零持久写面不动
- **WHEN** 语义事件转发全过程
- **THEN** 无新 DB 表、无新卷写面（事件文件是 pa 写的；hub 只读）
