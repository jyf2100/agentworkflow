# hub-notifications（add-hub-slice-4a 新 capability）

## ADDED Requirements

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
