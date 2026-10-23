# hub-notifications delta（add-hub-slice-4b）

## ADDED Requirements

### Requirement: 语义事件通知（events.jsonl 机械转发）
hub SHALL 在对账 running run 时机械读取该 run state 卷出口的 `events.jsonl` 增量事件行，经通知通道转发：critic_verdict / verify_verdict / pr_opened 各转发一条私信给队列项发起人（收件人三代分流复用既有 requirement）；其中**失败/警示类**（critic drop、verify revise/红）SHALL 同时进项目频道（对齐既有「频道广播仅失败类」噪音控制）；pass/绿类仅私信。转发文本 SHALL 为事件行事实字段的机械拼接（hub 零语义生成——不总结不评级）。读取范围 SHALL 限于 running run 的文件（run 终态后不再读——重启重发范围天然限于崩溃时活跃 run 的事件行，重复通知可接受；去抖为进程内存按 `(stamp, seq)` 键，通知零持久写面不变式不动）。契约版本不匹配或未知事件类型按 spec: pa-event-outlet 跳过。

#### Scenario: critic drop 转发
- **WHEN** 某 running run 的 events.jsonl 追加 `{event:"critic_verdict", verdict:"drop"}`
- **THEN** 发起人收到 critic drop 私信；该项目配置了频道映射时频道同步收到

#### Scenario: PR 开出转发
- **WHEN** events.jsonl 追加 `pr_opened`（含 pr_url）
- **THEN** 发起人收到含 pr_url 的私信

#### Scenario: 已终态 run 不再读事件
- **WHEN** 某 run 已转终态（succeeded/failed）后 events.jsonl 再追加行（不该发生——防回归）
- **THEN** hub 不读不转发（读取面只在 running 扫描集内）

#### Scenario: 通知零持久写面不动
- **WHEN** 语义事件转发全过程
- **THEN** 无新 DB 表、无新卷写面（事件文件是 pa 写的；hub 只读）

### Requirement: 无 run 放弃告警（派发前失败可见化）
hub SHALL 在队列项**无 run 行放弃**（create_run 前的 fail_item 路径：project 非法、PRD 缺失、必读面缺失、凭据缺失、meta 署名失败等）时经通知通道向配置单用户告警（notify_admin；含 project、原因摘要）。此类多为一次性部署配置错——告警使「队列项悄悄 failed」变为主动可见；失败详情仍以 stderr 与读面为准（告警是摘要非全文）。未配置通道时降级 stderr（可见的丢，对齐既有镜像陈旧告警降级语义）。

#### Scenario: 必读面缺失告警
- **WHEN** 队列项派发时 project 必读面缺失（profiles/sources.yaml 不在），fail_item 无 run 行放弃
- **THEN** 配置单用户收到告警私信（含 project 与原因）；未配 bot token 时 stderr 可见
