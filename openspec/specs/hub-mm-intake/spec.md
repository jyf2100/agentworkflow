# hub-mm-intake

## Purpose

hub 的 Mattermost 入站需求通道：成员在绑定频道经 `/pa` slash command 直接向 pa 流水线发开发需求，hub 由请求自带 channel_id 反查 `project_channels` 绑定表推断 project（频道即项目，用户不打 project 名）。入站链含 slash token 独立凭据校验（未配置零行为）、频道成员即授权（三态 fail-closed）、evidence 必填闸、幂等去重与 ephemeral+频道双回执，并复用 `/panel/prd` 同源确定性 wrap 与落盘入队链；`/pa bind`/`/pa unbind` 管理命令维护 db 绑定实体（`HUB_MM_PROJECT_CHANNELS` env 一次性 seed 迁移后废止）。出站通知的频道路由与全事件广播归 spec: hub-notifications。

## Requirements

### Requirement: slash command 入站端点（slash token 独立凭据 + 未配置零行为 + 子命令分流）
hub SHALL 提供 `POST /mm/intake` 端点接收 MM slash command（`/pa`）转发（`application/x-www-form-urlencoded`：token/channel_id/user_id/user_name/text 等）。请求 token SHALL 经 `HUB_MM_SLASH_TOKEN` 恒时比对校验（`hmac.compare_digest`，对齐 `/deliver` 投递 token 校验风格）；slash token 是独立于 OAuth app（登录）与 bot token（出站推递）的第三个 MM 凭据实体（F14 延续）。未配置 `HUB_MM_SLASH_TOKEN` 时端点 SHALL 返回 503（对齐 auth 群未配置回退态），入站关闭零行为——出站通知面完全不变；token 不匹配 SHALL 返回 401 且不入队。命令面 SHALL 按 text 首 token 分流子命令：`run`（需求入队，余量为需求正文）、`bind`/`unbind`（管理命令，归管理 requirement）；未知子命令 SHALL ephemeral 回执命令用法清单（教育不猜测）。

#### Scenario: 未配置入站关闭
- **WHEN** 未配 `HUB_MM_SLASH_TOKEN` 时收到 `POST /mm/intake`
- **THEN** 返回 503，零入队零副作用，出站通知面不受影响

#### Scenario: token 不匹配拒绝
- **WHEN** 请求 token 与 `HUB_MM_SLASH_TOKEN` 不匹配
- **THEN** 返回 401，不进入任何后续校验，零入队

#### Scenario: 未知子命令教育回执
- **WHEN** text 首 token 非 run/bind/unbind（如 `/pa foo ...`）
- **THEN** ephemeral 回执子命令用法清单（run 需求 + 出处格式 / bind / unbind），零入队零变更

### Requirement: 频道上下文推断 project
hub SHALL 由请求自带 `channel_id` 反查 `project_channels` 绑定表推断 project（用户不打 project 名）。频道未绑定任何项目时 SHALL 以 ephemeral 回执拒绝并提示「本频道未绑定项目——admin 执行 /pa bind <project>」，不猜测不缺省。

#### Scenario: 绑定频道推断成功
- **WHEN** 频道已绑定 project-a，成员在该频道发 `/pa run <需求>`
- **THEN** project 推断为 project-a，需求文本作为 PRD 正文进入落盘入队链

#### Scenario: 未绑定频道拒绝并教学
- **WHEN** 频道未绑定任何项目时收到 `/pa run`
- **THEN** ephemeral 回执「本频道未绑定项目 + bind 命令用法」，零入队

### Requirement: 频道成员即授权（三态 fail-closed，403=判据不可用非未授权）
hub SHALL 经 bot token 调 `GET /channels/{channel_id}/members`（分页取全）校验发起人 `user_id` 在成员集内；频道成员管理即授权管理（hub 零白名单配置，粒度 per-project）。成员查询 SHALL 三态分流（对齐 fail-safe-dispatch 不变式，**范畴钉死：判据可得性 ≠ 授权结论**）：
- **FOUND**（成员集可得）：发起人在集 → 授权通过；不在集 → 未授权（ephemeral 回执「非本频道成员」，零入队）；
- **UNKNOWN**（判据不可用）：网络错、5xx、**403（bot 无权限查成员集——bot 被踢出频道的形态；这是 hub 侧通道故障，绝不解读为发起人未授权）** → fail-closed 拒绝入站（绝不把 UNKNOWN 当授权通过），ephemeral 回执「授权校验暂不可用，稍后重试」；
- **404**（频道不存在/已删除）：确定性配置错，ephemeral 回执「频道不存在——检查绑定（/pa unbind 后重绑）」，零入队。

成员查询结果按 `(channel_id, user_id)` 记忆化缓存（TTL 短——入站频率低，缓存仅为免重复翻页；仅 FOUND 形态可缓存，UNKNOWN 不缓存）。

#### Scenario: 频道成员放行
- **WHEN** 发起人 user_id 在绑定频道的成员集内
- **THEN** 授权通过，进入 evidence 校验

#### Scenario: 非成员拒绝
- **WHEN** 发起人不在成员集（如已被移出频道后残留会话重试）
- **THEN** ephemeral 回执「非本频道成员，无权发需求」，零入队

#### Scenario: 成员查询 UNKNOWN 拒绝
- **WHEN** 成员查询抛网络异常或返回 5xx
- **THEN** ephemeral 回执「授权校验暂不可用」，零入队（fail-closed 不旁路）

#### Scenario: bot 无权限不等同未授权
- **WHEN** 成员查询返回 403（bot 已被移出频道，发起人是真实成员）
- **THEN** ephemeral 回执「授权校验暂不可用（bot 通道故障），稍后重试」——绝不回执「非成员」（403 是判据不可用，非授权结论）

#### Scenario: 频道不存在
- **WHEN** 绑定指向的频道已删除（成员查询 404）
- **THEN** ephemeral 回执「频道不存在——检查绑定」，零入队

### Requirement: evidence 必填闸（对齐面板）
入站需求 SHALL 与 `/panel/prd` **同语义判据**（evidence 必填「空依据闸前拒」——机械形态各自适配：面板 = 表单字段判据，mm 入站 = 正文文本标记判据，非实现复用）：text 中 MUST 含 evidence（出处链接或说明）。evidence 缺失时 SHALL ephemeral 拒绝并给格式示例（多人场景下是成本护栏——每个 run = 容器 + LLM token + 目标仓真实开发）。格式约定：`出处: <链接或说明>` 或 `出处：<链接或说明>`（**半角/全角冒号两形态皆受**——中文输入法高频全角形态不误拒）；与需求正文同行或末行，机械可判。

#### Scenario: 无出处拒绝
- **WHEN** `/pa run 把按钮改蓝`（无 `出处:` / `出处：` 标记）
- **THEN** ephemeral 回执「缺 evidence（出处: <链接或说明>）+ 格式示例」，零入队

#### Scenario: 全角冒号形态通过
- **WHEN** `/pa run 把按钮改蓝，出处：issue#42`（全角冒号）
- **THEN** evidence 判据命中，提取进 wrap frontmatter，进入落盘入队链

#### Scenario: 带出处通过
- **WHEN** `/pa run 把按钮改蓝，出处: issue#42`（半角冒号）
- **THEN** evidence 提取进 wrap frontmatter，进入落盘入队链

### Requirement: 确定性 wrap 与落盘入队链复用
入站 text SHALL 经确定性 wrap（frontmatter 含 project/来源 `mm-channel`/发起人/频道与 evidence，**不含时间戳/uuid 等随机成分**——与面板 wrap 的差异点，幂等的前提）后走 `/panel/prd` 同源链：prd_store 落盘（文件名以 wrap 内容 sha256 衍生，确定性——`mm-<sha256(wrapped)[:8]>.md` 形态，与面板 `{yyyymmdd}_hand-{uuid8}` 日期/uuid 前缀**有意偏离**：确定性是幂等前提，已验证无机械消费冲突（stamp 消费取自 run 目录名非 PRD 文件名）；PRD 时间线由 db `created_at` 承载非文件名）→ `engine.enqueue`（`enqueued_by` = 发起人 `user_id`，26-hex 直发路由现成）。超长 text（超 `_DELIVER_MAX_BYTES` 或 slash text 上限护栏）SHALL ephemeral 拒绝并提示「长需求走面板」，不截断（截断的 PRD 是语义损坏）。

#### Scenario: 入队成功走直发路由
- **WHEN** 合法 `/pa run` 通过全部校验
- **THEN** PRD 确定性落盘 + 入队，队列项 enqueued_by = 发起人 mm_user_id，后续通知走既有三代分流

#### Scenario: 超长拒绝不截断
- **WHEN** text 超长度护栏
- **THEN** ephemeral 回执「内容超限，长需求走面板表单」，零入队零截断

### Requirement: 幂等去重（内容键小时窗，deliver_key 先例）
hub SHALL 以 `deliver_key = mm:{project}:{user_id}:{sha256(wrapped)[:16]}:{yyyymmddhh}`（小时窗掺入，**时间基准钉 UTC**——对齐 hub 既有 stamp UTC 先例，机械判据不随部署本地时漂移）复用 `/deliver` 的 db 层幂等模式：同键重复请求（MM webhook 超时重试）SHALL 回既有队列项幂等回执（「近期同内容已入队 #id」），不双入队；跨小时窗同文本 SHALL 视为新需求（有意重发不被拦）。

#### Scenario: webhook 重试幂等
- **WHEN** MM 对同一 `/pa run` 请求超时重试（同 user 同 text 同小时）
- **THEN** 第二次请求回执既有队列项 #id，不产生新队列项

#### Scenario: 跨小时有意重发
- **WHEN** 同用户隔天再发完全相同的需求文本
- **THEN** 小时窗不同 → 新队列项正常入队

### Requirement: 双回执（ephemeral 即时 + 频道时间线后置）
入站结果 SHALL 双回执：①slash HTTP 响应 JSON（`response_type: ephemeral`）即时回发起人（成功含队列项 #id；拒绝含原因 + 可操作信息：可用 project / evidence 格式 / bind 用法）；②入队成功后 hub SHALL 经 bot 另 post 一条非 ephemeral 消息进频道（@发起人 + #id + 需求首行摘要）——团队时间线。**② SHALL 后置于 ①**（HTTP 响应先回——MM 对 slash 响应有秒级超时，频道 post 拉长处理链会放大超时→重试面；频道 post 在响应后的后台执行），失败 fail-open（不阻断已完成的入队）。拒绝 SHALL 只 ephemeral 不进频道（个人错误不公开广播）。

#### Scenario: 成功双回执
- **WHEN** `/pa run` 入队成功
- **THEN** 发起人收 ephemeral「已入队 #id」（HTTP 响应先行、不被频道 post 阻塞）；频道随后出现「@发起人 已入队 #id：<摘要>」时间线消息

#### Scenario: 拒绝不广播
- **WHEN** `/pa run` 因缺 evidence 被拒
- **THEN** 仅发起人收 ephemeral 拒绝回执，频道零消息

### Requirement: /pa bind 管理命令（admin 白名单 + 1:1 + 必读面校验）
`/pa bind <project>` SHALL 在频道内执行绑定：授权**仅** `HUB_PANEL_ADMINS`（MM username 白名单，与面板 retry/abandon 同键同白名单判据；**空白名单语义与面板显式分立**——本体论审核 F2：面板 = OAuth 会话回退态放行（hub-panel-console D4「单用户回退态」，`_require_admin` 现行语义），slash 通道无会话判据、空白名单时无任何附加授权面 → fail-closed 全拒防误开放——同键不同空态，部署者须知）——**管理面判据，与协作面（run 的频道成员判据）分立，不串联**（bind 不做发起人频道成员校验：admin 拉起绑定是治理动作，不要求发起人已在频道成员集）；非白名单 ephemeral 拒绝。project SHALL 校验在 data_root 必读面（`_panel_projects()` 同源——防手滑绑错名）；绑定严格 1:1；**双向换绑均允许且对称**（project 侧换频道：project-a 已绑 X，admin 在 Y 绑 project-a → 改归 Y；频道侧顶绑：频道 X 已绑 project-a，admin 在 X 绑 project-b → X 改归 project-b——覆盖语义两方向同权，不留「某方向拒绝换绑」的不对称暗规），换绑 SHALL 单事务原子完成（旧绑定必被清除，不出现双绑或空窗中间态），回执均明示被替换项；通知 SHALL 按当前绑定（rebind 后新事件进新频道，不追历史）。bind 时 hub SHALL 顺带校验 bot 可达该频道（bot 未入频道则回执提示「先把 bot 拉进本频道」——成员查询与出站 post 的前提；403 形态同「判据不可用」语义）。`/pa unbind` SHALL 解绑当前频道（admin 同判据），解绑后该频道入站拒绝、项目出站回落 DM 形态；在未绑定任何项目的频道上执行 SHALL 回教育回执「本频道未绑定项目」（零变更零异常——误操作教学而非静默成功）。

#### Scenario: admin 绑定成功
- **WHEN** 白名单 admin 在 #proj-a 频道执行 `/pa bind project-a`（bot 已入频道、project 在必读面）
- **THEN** 绑定落库，回执「project-a ↔ 本频道」；此后该频道 /pa run 推断 project-a

#### Scenario: 非 admin 拒绝
- **WHEN** 非白名单成员执行 `/pa bind project-x`
- **THEN** ephemeral 回执「仅 admin（HUB_PANEL_ADMINS）可绑定」，零变更

#### Scenario: rebind 覆盖明示
- **WHEN** project-a 已绑频道 X，admin 在频道 Y 执行 `/pa bind project-a`
- **THEN** 绑定改写为 Y，回执明示「已从 X 换绑至 Y」，后续事件进 Y

#### Scenario: 频道侧顶绑
- **WHEN** 频道 X 已绑 project-a，admin 在频道 X 执行 `/pa bind project-b`（project-b 原未绑定任何频道）
- **THEN** 单事务内 project-a 的绑定被清除、X 改绑 project-b，回执明示「本频道已从 project-a 改绑 project-b」；project-a 出站回落 DM 形态

#### Scenario: bind 校验 bot 在频道
- **WHEN** admin 执行 `/pa bind project-a` 但 bot 不在该频道
- **THEN** ephemeral 回执「先把 bot 拉进本频道再 bind」，绑定不落库

#### Scenario: unbind 未绑定频道教育回执
- **WHEN** admin 在未绑定任何项目的频道执行 `/pa unbind`
- **THEN** ephemeral 回执「本频道未绑定项目」，零变更零异常

### Requirement: 绑定 db 实体与 env seed 一次性迁移
频道↔项目绑定 SHALL 存 db 表 `project_channels`（project UNIQUE、channel_id UNIQUE、bound_by、bound_at——审计列齐全，值形态全钉死：**bound_by = username**（与 `HUB_PANEL_ADMINS` 同形——审计可读，不留值形态歧义后患）；**bound_at = ISO8601 UTC**（如 `2026-10-27T03:17:00Z`——对齐 hub db 既有时间戳列形态，不随部署本地时漂移）；**channel_id = MM 26 字符 id**（`[0-9a-z]{26}`，与 user_id 同形态族——bind 落库与 seed 迁移均带此机械判据，频道名/URL 误填即拒）），`/pa bind`/`/pa unbind` 是唯一写入口。hub 启动时 SHALL 将 `HUB_MM_PROJECT_CHANNELS` env 值一次性 seed 迁移进表（幂等：表已有数据则跳过整批 seed），此后该 env 键 SHALL 废止（不再读取——db 唯一真相，不留双源）。

#### Scenario: 首次启动 seed
- **WHEN** 表为空且 env 配有 `HUB_MM_PROJECT_CHANNELS=proj-a:chan1`
- **THEN** 启动时 seed 进表（bound_by 记 seed 迁移来源、bound_at = seed 执行时刻 ISO8601 UTC；channel_id 形态校验不过的条目拒入并 stderr 可见），env 键此后不再读取

#### Scenario: 表非空跳过 seed
- **WHEN** 表已有绑定行（含人工 bind 的），env 仍留旧值
- **THEN** seed 整批跳过，db 值不受 env 影响（唯一真相）
