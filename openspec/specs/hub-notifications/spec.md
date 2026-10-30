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
hub SHALL 在 run 到达终态（succeeded / failed / failed_to_start）时按**归属路由**推送：project 已绑定频道（hub-mm-intake `project_channels` 表）→ 推该项目频道（消息含 `<@发起人>` 提及——MM 提及通知强度替代私信，且协作可见）；未绑定 → 既有 per-user 私信（回落形态不变）。事件面 MUST 钉死为 hub DB 自有事实的 **run 终态行**；无 run 行的队列项放弃（入队后派发前失败：project 非法、PRD 缺失、必读面缺失、凭据缺失等）不在本事件面（归「无 run 放弃告警」requirement）。hub MUST NOT 解析容器内 pa 的 journal、report 或任何产物格式来生成通知（语义事件——critic pass/drop、PR 开出、verify verdict——归 events.jsonl 机械转发面）。DM 形态收件人解析 SHALL 按队列项发起人（enqueued_by）**值形态三代**分流（不变）：mm_user_id（26 字符十六进制）直发；username（2a env 兜底形态 = 配置单用户，含机机回落目标）经 `users/username/{name}` 解析 mm id 后发（解析结果内存缓存；解析失败记日志 fail-open，可见的丢而非静默吞）；`vault-deliver`（机机通道字面量）回落配置单用户。绑定频道路由的提及目标 SHALL 同源解析：enqueued_by 为 26-hex 直拼 `<@id>`；非 26-hex 经 username 解析（同缓存同 fail-open——解析失败降级为无提及纯文本，消息照发）。每个 run 终态 SHALL 至多通知一次（挂点在终态落库的单次执行处，非轮询重发）；绑定频道项目的频道 post 失败 fail-open（warn 可见），**不回退私信**（频道与 DM 互不依赖，双通道同事件是噪音源）。

#### Scenario: 绑定频道终态进频道不私信
- **WHEN** project-a 已绑定频道，其 run 以 failed 终态
- **THEN** 频道收到含 <@发起人> 提及的失败消息（project、run 标识、exit code）；发起人无重复私信

#### Scenario: 未绑定项目维持私信
- **WHEN** 未绑定频道的 project 的 run 到达终态
- **THEN** 既有 per-user 私信形态（三代分流），零变化

#### Scenario: 容器失败通知发起人
- **WHEN** 某会话用户入队的队列项派发后容器 exited 非零（对账判 failed）
- **THEN** 发起人收到 run 失败通知（含 project、run 标识、exit code 或失败类别）

#### Scenario: 成功通知
- **WHEN** 容器 exited 0（对账判 succeeded）
- **THEN** 发起人收到 run 成功通知

#### Scenario: 机机通道投递项回落配置单用户
- **WHEN** `vault-deliver` 投递项的 run 到达终态（enqueued_by 为机机字面量）
- **THEN** 通知收件人回落配置单用户（username 解析路径），消息可辨识来源为投递通道

#### Scenario: 2a 兜底形态经 username 解析
- **WHEN** 无会话入队（enqueued_by = 配置单用户 username，非 mm_user_id）的 run 到达终态（未绑定项目形态）
- **THEN** Notifier 经 username 解析出 mm id 后投递；解析失败时记日志（fail-open），run 终态不受影响

#### Scenario: 派发失败即通知
- **WHEN** 派发时镜像缺失（ImageMissingError → run failed_to_start + 队列项 failed）
- **THEN** 发起人收到 failed_to_start 通知（含缺失 tag 与 build 提示口径）

### Requirement: 通知 fail-open（观察面不阻断执行面）
通知推递失败（MM 宕机、网络错、收件人不存在）SHALL NOT 影响流水线状态机——run 终态照常落库、队列项照常流转，失败仅记日志（stderr 可见，无静默吞）。通知 MUST NOT 引入新的持久化写面（不落 db、不落卷——通知丢失的兜底是读面查询）。

#### Scenario: MM 宕机不阻断
- **WHEN** run 终态落库后通知推递抛异常（Fake 注入崩溃）
- **THEN** run 终态与队列项状态不受任何影响，异常记日志（含 run 标识），下个事件照常处理

### Requirement: 项目频道全事件广播
项目绑定频道（hub-mm-intake `project_channels` 表）时，hub SHALL 将该 project 的**全事件**推送至频道：run 终态（成败皆进——频道 = 工作区，成功也是项目状态）、语义事件（critic/verify 裁决、PR 开出、**acceptance_suite**——四类全量，口径裁决见「语义事件通知」requirement）、无 run 放弃告警；绑定频道的项目其事件不再重复私信（频道 + @提及已覆盖个人触达——双通道同事件是噪音）。频道 post 失败 SHALL fail-open（warn 可见，不阻断不回退 DM）。未绑定项目 = 仅 DM 形态（默认形态，既有行为不变）。**本 requirement 的 env 映射语义已废止**：原 `HUB_MM_PROJECT_CHANNELS` env 键经 seed 迁移后由绑定表取代（迁移语义归 hub-mm-intake），频道关系唯一真相是绑定表。

#### Scenario: 成功终态进频道
- **WHEN** 绑定频道的 project 的 run 以 succeeded 终态
- **THEN** 频道收到成功消息（run 标识），发起人无重复私信

#### Scenario: 失败终态进频道含提及
- **WHEN** 绑定频道的 project 的 run 以 failed 终态
- **THEN** 频道收到含 <@发起人> 的失败消息（与入队时间线同频道可追溯）

#### Scenario: 频道 post 失败不回退
- **WHEN** 绑定频道 post 抛异常（Fake 注入崩溃）
- **THEN** run 终态与队列项状态不受影响，异常记日志（fail-open），不补发 DM

#### Scenario: 未绑定项目零频道行为
- **WHEN** project 未绑定频道
- **THEN** 无频道推送，DM 形态照旧

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
hub SHALL 在对账 running run 时机械读取该 run state 卷出口的 `events.jsonl` 增量事件行，经通知通道转发：critic_verdict / verify_verdict / pr_opened / **acceptance_suite** 各转发一条，按**归属路由**：project 绑定频道 → 该频道（含 `<@发起人>` 提及——个人触达由提及承载，发起人无重复私信）；未绑定 → 私信发起人（收件人三代分流复用既有 requirement）。**口径裁决（本体论审核 F1，取代 fix-event-outlet-verify-parity 的 per-event 广播判定）**：绑定频道的项目四类事件**全量**进频道（失败/警示类与 pass/绿类皆进——全事件时间线语义），fix-event-outlet 定的「警示类才进频道、stored/pass 仅私信」噪音控制判读废止——噪音控制由「频道=项目工作区」的组织形态承担（频道本就是该项目协作者的工作区，非全公司大厅）；未绑定项目无频道面，per-event 判定无处生效自然退役。**事件文本拼接契约零变化继承**（fix-event-outlet-verify-parity）：verify_verdict invalid 转发文本机械附 `reason`（枚举值域见 spec: pa-event-outlet——triage 排障入口）；acceptance_suite 转发文本机械附 `status` 与 reason/digest/supersedes 事实字段；转发文本 SHALL 为事件行事实字段的机械拼接（hub 零语义生成——不总结不评级）。读取范围 SHALL 限于 running run 的文件（run 终态后不再读——重启重发范围天然限于崩溃时活跃 run 的事件行，重复通知可接受；去抖为进程内存按 `(stamp, seq)` 键，通知零持久写面不变式不动）。契约版本不匹配或未知事件类型按 spec: pa-event-outlet 跳过。

#### Scenario: critic drop 进绑定频道
- **WHEN** 绑定频道的 running run 的 events.jsonl 追加 `{event:"critic_verdict", verdict:"drop"}`
- **THEN** 该频道收到 critic drop 转发（含 @发起人）；发起人无重复私信

#### Scenario: pass 类也进绑定频道（新口径）
- **WHEN** 绑定频道的 running run 的 events.jsonl 追加 `{event:"critic_verdict", verdict:"pass"}` 或 `{event:"pr_opened", pr_url:"..."}`
- **THEN** 该频道收到转发（全事件时间线——成功也是项目状态）；发起人无重复私信

#### Scenario: acceptance_suite 进绑定频道（文本契约继承）
- **WHEN** 绑定频道的 running run 的 events.jsonl 追加 `{event:"acceptance_suite", status:"stored", digest:"sha256:..."}` 或 `status:"absent", reason:"no_harness"`
- **THEN** 该频道收到转发（stored 附 digest/换版链、absent 附原因——拼接契约同 fix-event-outlet-verify-parity 零变化）；发起人无重复私信

#### Scenario: verify invalid 附原因
- **WHEN** events.jsonl 追加 `{event:"verify_verdict", verdict:"invalid", reason:"anchor_violation"}`
- **THEN** 转发文本含 invalid 与 reason（triage 升人工须有可送达出口）；绑定频道进频道、未绑定私信

#### Scenario: 未绑定项目私信转发
- **WHEN** 未绑定频道的 running run 的 events.jsonl 追加 `pr_opened`（含 pr_url）
- **THEN** 发起人私信收到含 pr_url 的转发（四类事件全量私信，三代分流）

#### Scenario: 已终态 run 不再读事件
- **WHEN** 某 run 已转终态（succeeded/failed）后 events.jsonl 再追加行（不该发生——防回归）
- **THEN** hub 不读不转发（读取面只在 running 扫描集内）

#### Scenario: 通知零持久写面不动
- **WHEN** 语义事件转发全过程
- **THEN** 无新 DB 表、无新卷写面（事件文件是 pa 写的；hub 只读）

### Requirement: 无 run 放弃告警（派发前失败可见化）
hub SHALL 在队列项**无 run 行放弃**（create_run 前的 fail_item 路径：project 非法、PRD 缺失、必读面缺失、凭据缺失、meta 署名失败等）时按**归属路由**告警：project 绑定频道 → 该频道（团队可见「本 project 的需求没跑起来」）；未绑定 → 配置单用户 DM（notify_admin；含 project、原因摘要）。此类多为一次性部署配置错——告警使「队列项悄悄 failed」变为主动可见；失败详情仍以 stderr 与读面为准（告警是摘要非全文）。未配置通道时降级 stderr（可见的丢，对齐既有镜像陈旧告警降级语义）。

#### Scenario: 绑定频道放弃告警进频道
- **WHEN** 队列项派发时 project 必读面缺失（profiles/sources.yaml 不在），该 project 已绑定频道
- **THEN** 频道收到放弃告警（含 project 与原因摘要）

#### Scenario: 未绑定项目回落 admin DM
- **WHEN** 同类无 run 放弃但 project 未绑定频道
- **THEN** 配置单用户收到告警私信（含 project 与原因）；未配 bot token 时 stderr 可见
