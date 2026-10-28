# hub-notifications

## Purpose

hub 侧通知能力：Mattermost bot 通道（独立凭据实体 + 注入接缝 + 未配置零行为）、run 终态 per-user 私信（事件面钉死为 hub DB 自有事实的 run 终态行，收件人按 enqueued_by 值形态三代分流）、通知 fail-open（观察面不阻断执行面、零新增持久化写面）、可选 per-project 失败频道广播、镜像陈旧告警（F13 推迟项：remote release tag 周期比对 + 三态分流 + 去抖）。

## Requirements

### Requirement: bot 通道（独立凭据实体 + 注入接缝）
hub SHALL 经 Mattermost bot token（`HUB_MM_BOT_TOKEN`，独立凭据实体——与 OAuth app 四项分离，F14「身份与通道两范畴」）提供推通知通道。MM API 推递 SHALL 经注入式 client 接缝（生产 httpx 同鸭子面、测试 Fake 同判，对齐 auth.D8 模式）；bot token 绝不进错误消息与日志。未配置 bot token（通道关闭态）SHALL 零通知零行为——流水线与端点面完全不变；复用的 `HUB_MM_BASE_URL` 未配置时通道同样关闭。

#### Scenario: 未配置零行为
- **WHEN** hub 启动时未配 `HUB_MM_BOT_TOKEN`（或未配 `HUB_MM_BASE_URL`）
- **THEN** 通知模块落位为关闭态，run 照常走完全生命周期，无任何出站调用、无错误

#### Scenario: 推递经注入接缝
- **WHEN** 测试以 Fake MM client 注入并触发一次通知
- **THEN** Fake 记录到一次推递调用（收件人 + 消息文本），消息含 run 标识与终态事实

### Requirement: run 终态通知（hub 自有事实面）
hub SHALL 在 run 到达终态（succeeded / failed / failed_to_start）时经通道推 per-user 私信通知。事件面 MUST 钉死为 hub DB 自有事实的 **run 终态行**；无 run 行的队列项放弃（入队后派发前失败：project 非法、PRD 缺失、必读面缺失、凭据缺失等）不在事件面——多为一次性部署配置错，stderr 日志与读面查询可见，与语义事件一并归后续切片事件出口。hub MUST NOT 解析容器内 pa 的 journal、report 或任何产物格式来生成通知（语义事件——critic pass/drop、PR 开出、verify verdict——须待容器侧事件出口契约，归后续切片）。收件人解析 SHALL 按队列项发起人（enqueued_by）**值形态三代**分流：mm_user_id（2b 会话入队形态，26 字符十六进制）直发该用户；username（2a env 兜底形态 = 配置单用户，含机机回落目标）经 `users/username/{name}` 解析 mm id 后发（解析结果内存缓存；解析失败记日志 fail-open，可见的丢而非静默吞）；`vault-deliver`（机机通道字面量）回落配置单用户（走 username 解析路径）。每个 run 终态 SHALL 至多通知一次（挂点在终态落库的单次执行处，非轮询重发）。

#### Scenario: 容器失败通知发起人
- **WHEN** 某会话用户入队的队列项派发后容器 exited 非零（对账判 failed）
- **THEN** 该用户收到 run 失败私信（含 project、run 标识、exit code 或失败类别）

#### Scenario: 成功通知
- **WHEN** 容器 exited 0（对账判 succeeded）
- **THEN** 发起人收到 run 成功通知

#### Scenario: 机机通道投递项回落配置单用户
- **WHEN** `vault-deliver` 投递项的 run 到达终态（enqueued_by 为机机字面量）
- **THEN** 通知收件人回落配置单用户（username 解析路径），消息可辨识来源为投递通道

#### Scenario: 2a 兜底形态经 username 解析
- **WHEN** 无会话入队（enqueued_by = 配置单用户 username，非 mm_user_id）的 run 到达终态
- **THEN** Notifier 经 username 解析出 mm id 后投递；解析失败时记日志（fail-open），run 终态不受影响

#### Scenario: 派发失败即通知
- **WHEN** 派发时镜像缺失（ImageMissingError → run failed_to_start + 队列项 failed）
- **THEN** 发起人收到 failed_to_start 通知（含缺失 tag 与 build 提示口径）

### Requirement: 通知 fail-open（观察面不阻断执行面）
通知推递失败（MM 宕机、网络错、收件人不存在）SHALL NOT 影响流水线状态机——run 终态照常落库、队列项照常流转，失败仅记日志（stderr 可见，无静默吞）。通知 MUST NOT 引入新的持久化写面（不落 db、不落卷——通知丢失的兜底是读面查询）。

#### Scenario: MM 宕机不阻断
- **WHEN** run 终态落库后通知推递抛异常（Fake 注入崩溃）
- **THEN** run 终态与队列项状态不受任何影响，异常记日志（含 run 标识），下个事件照常处理

### Requirement: per-project 频道广播（可选）
配置 project→channel 映射时，hub SHALL 将该 project 的 run 失败类终态（failed / failed_to_start）额外广播至对应频道；成功终态默认不进频道（噪音控制）。未配置映射 = 仅 per-user 私信（默认形态零变化）。

#### Scenario: 失败广播至频道
- **WHEN** 某 project 配置了频道映射，其 run 以 failed 终态
- **THEN** 该频道收到广播（与发起人私信并行，两者互不依赖——频道失败不影响私信）

#### Scenario: 成功不进频道
- **WHEN** 某 project 配置了频道映射，其 run 以 succeeded 终态
- **THEN** 仅发起人私信，频道无广播

### Requirement: 镜像陈旧告警（F13 推迟项）
配置比对源（`HUB_IMAGE_TEMPLATE_REPO`，`owner/repo` 形态）时，hub SHALL 周期比对 remote 最新 release tag 与配置默认镜像 tag（`HUB_IMAGE_TAG_DEFAULT`），不一致时经通道向配置单用户告警（含两者值与升级提示）；通知通道关闭态（bot token 未配置）SHALL 降级 stderr——可见的丢而非静默，升级通道配置后自动恢复私信。remote 查询 SHALL 三态分流（对齐 fail-safe-dispatch 三态不变式）：查得 tag（FOUND）参与比对；**确定性 4xx**（404 等——repo 名配错或无 release，配置错而非瞬时故障）SHALL 高声告警（stderr，去抖一次）提示检查 `HUB_IMAGE_TEMPLATE_REPO`——绝不静默（否则配置错使检测整体失效且无人知）；**瞬时失败**（网络错、5xx，UNKNOWN）不告警不误报，记日志下轮再查。同一差异 SHALL 去抖（进程生命周期内同一 `(remote_tag, local_tag)` 组合只告警一次）。未配置比对源 = 检测关闭（零行为）。比对 MUST NOT 触发自动拉取或重建镜像（告警是唯一动作——升级是人工决定）。

#### Scenario: remote 更新告警
- **WHEN** fake remote 报最新 release tag 为 `pa-executor:v0.2.0` 而配置默认 tag 为 `pa-executor:v0.1.3`
- **THEN** 配置单用户收到陈旧告警一次；后续周期同组合差异不重复告警

#### Scenario: repo 配错高声告警
- **WHEN** fake remote 对配置的 repo 返回 404（repo 不存在或无 release）
- **THEN** stderr 高声告警一次（提示检查 `HUB_IMAGE_TEMPLATE_REPO`），不进陈旧比对、不重复告警

#### Scenario: remote 不可达不误报
- **WHEN** fake remote 查询抛网络异常或 5xx（UNKNOWN）
- **THEN** 无告警，日志记录查询失败，下轮照常再查

#### Scenario: 未配置检测关闭
- **WHEN** 未配 `HUB_IMAGE_TEMPLATE_REPO`
- **THEN** 无比对无告警，零行为

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

### Requirement: 无 run 放弃告警（派发前失败可见化）
hub SHALL 在队列项**无 run 行放弃**（create_run 前的 fail_item 路径：project 非法、PRD 缺失、必读面缺失、凭据缺失、meta 署名失败等）时经通知通道向配置单用户告警（notify_admin；含 project、原因摘要）。此类多为一次性部署配置错——告警使「队列项悄悄 failed」变为主动可见；失败详情仍以 stderr 与读面为准（告警是摘要非全文）。未配置通道时降级 stderr（可见的丢，对齐既有镜像陈旧告警降级语义）。

#### Scenario: 必读面缺失告警
- **WHEN** 队列项派发时 project 必读面缺失（profiles/sources.yaml 不在），fail_item 无 run 行放弃
- **THEN** 配置单用户收到告警私信（含 project 与原因）；未配 bot token 时 stderr 可见
