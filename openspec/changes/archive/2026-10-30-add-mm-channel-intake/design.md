# add-mm-channel-intake — design

## Context

hub 出站通知（hub-notifications）已真机打通：MM bot PAT 推递（run 终态私信、失败频道广播、语义事件转发、无 run 放弃告警），`HUB_MM_PROJECT_CHANNELS` env 承载 project→频道的单向广播映射。需求侧入口三个（面板 `/panel/prd` 表单、`POST /queue`、vault `POST /deliver`），均为「人找系统」形态。部门多人要在 MM 里直接丢需求——本 change 补入站面，采用「频道即项目」组织形态：每个项目绑定一个 MM 频道，频道成员即协作者，频道即团队时间线。

hub 在多人维度已就绪（探索实证）：per-project 串行位（`oldest_pending_excluding_running_projects`）、enqueued_by 三代分流通知路由、per-user vault 凭据 → 共享 env 兜底、容器峰值上限、meta 署名审计——全部复用，执行面零改动。

## Goals / Non-Goals

**Goals:**
- 同事在项目频道里 `/pa run <需求正文+出处>` 一句话入队，project 由频道推断
- 授权零配置：频道成员即授权，MM 侧拉人踢人即权限管理，粒度 per-project
- 团队时间线：入队回执、终态、PR 链接、裁决全事件进频道（@发起人提及 = 个人触达）
- 幂等：MM webhook 重试不产生重复入队
- 未配置 slash token = 入站关闭零行为（对齐既有模式）

**Non-Goals:**
- 对话式多轮 bot（WebSocket 常驻收 DM）——slash command 单轮 + 错误回执教育已覆盖；长连接组件是 hub 至今没有的新故障类别
- `/pa status` 等查询命令——后续增量
- 多 bot / 多租户（多部门独立入口）——单团队单 bot 是正解（通知路由维度是发起人非 bot 身份）
- 经 MM 的改行为操作（retry/abandon）——面板保留
- 超 slash text 上限的长 PRD 通道——拒绝并提示走面板

## Decisions

### D1 入站技术形态：slash command → hub HTTP 端点（弃 WebSocket / 轮询）

MM 管理台创建 `/pa` slash command 指向 hub 新端点（`POST /mm/intake`）。MM 的 slash command 是 MM→hub 的普通 HTTP POST（`application/x-www-form-urlencoded`，字段含 `token/channel_id/user_id/user_name/command/text`），hub 已是 FastAPI HTTP 服务器——**零新运行时组件、零新依赖**。slash 的 HTTP 响应体直接成为 MM ephemeral 回执（仅发起人可见），天然即时闭环。备选 WebSocket（bot 常驻收 DM，可多轮但引入长连接故障面 + websockets 新依赖）与轮询（tick 内拉 DM，B 的劣化版）均弃。

### D2 绑定映射 db 唯一真相 + env 一次性 seed 迁移

新表 `project_channels`（`project TEXT UNIQUE`、`channel_id TEXT UNIQUE`、`bound_by TEXT`、`bound_at TEXT`——审计列齐全，值形态全钉死：bound_by = **username**（与 `HUB_PANEL_ADMINS` 同形——审计可读）；bound_at = **ISO8601 UTC**（对齐 hub db 既有 ts 列形态；seed 行 = seed 执行时刻）；channel_id = **MM 26 字符 id**（`[0-9a-z]{26}`，与 user_id 同形态族——落库与 seed 迁移带机械判据，防频道名/URL 误填））。`HUB_MM_PROJECT_CHANNELS` env 在 hub 启动时一次性 seed 进表（幂等：表已有行则跳过），此后该 env 键**废止**（不再读取）——不留双源。备选纯 env（改动要重建容器，部门自治打折）与纯 db 双轨（两处配置源、真相不明）均弃。`/pa bind` 是唯一写入口。

### D3 bind 管理与授权

- `/pa bind <project>`：在频道里执行，权限复用 `HUB_PANEL_ADMINS`（与面板 retry/abandon 同一 MM username 白名单——一个判据管所有管理面操作；**空白名单语义分立**：面板空 = OAuth 会话回退态放行（D4 现行），slash 通道无会话判据空 = fail-closed 全拒——同键不同空态，spec 已显式声明）。校验 project 在 data_root 必读面（`_panel_projects()` 同源扫描）；绑定严格 **1:1**（一频道一项目、一项目一频道）；**双向换绑均允许且对称**（project 换频道 / 频道顶绑新 project 皆覆盖旧绑定，单事务原子——旧绑定必清，不留「某方向拒绝换绑」的不对称暗规），通知按**当前绑定**（通知是当下事件不追历史）。
- 入站授权：**频道成员即授权**——hub 经 bot token 调 `GET /channels/{channel_id}/members`（分页，per_page=200 翻页取全）校验发起人 `user_id` 在成员集。成员查询按 `(channel_id, user_id)` 记忆化缓存（TTL 60s，量级：入站频率低；仅 FOUND 可缓存）。**三态对齐 fail-safe-dispatch 不变式，范畴钉死「判据可得性 ≠ 授权结论」**：FOUND（成员集可得）→ 在集授权通过/不在集未授权拒绝；UNKNOWN（网络错、5xx、**403 bot 无权限——hub 侧通道故障，绝不解读为发起人未授权**）→ fail-closed 拒绝入站（绝不把 UNKNOWN 当授权通过——否则 MM 抖动 = 授权旁路），回执「校验暂不可用」；404（频道不存在）→ 确定性配置错回执检查绑定。
- bind 授权与管理面/协作面**双轨分立**：bind/unbind 仅 admin 白名单判据，不串联发起人频道成员校验（治理动作不要求发起人已在成员集）；run 仅频道成员判据。
- 部署前提：bot 必须已入频道（否则查成员 403）。bind 时顺带校验 bot 可达该频道，不可达则回执提示「先把 bot 拉进本频道」。

### D4 通知路由按归属维度（频道优先 / DM 兜底）

路由判据是**事件归属**而非紧迫度：

```
事件 → 有所属 project 且该 project 已绑定频道？
        ├─ 是 → 项目频道（终态/PR/裁决类含 @发起人：<@mm_user_id> 语法）
        └─ 否 → admin DM 兜底（cfg.user_id 单人，D4 零改动）
                 （hub 级运维事件：镜像陈旧；未绑定项目的项目事件：含无 run 放弃）
```

项目频道出站从「仅 failed/failed_to_start 广播」升级为**全事件**（入队回执、run 终态成败、PR 链接、critic/verify 裁决）——频道 = 工作区，成功也是项目状态。run 终态对发起人的**私信取消**（绑定频道项目），由频道内 @提及替代（MM 提及通知强度 ≈ DM）；未绑定项目维持既有 per-user 私信（回落形态不变）。频道 post 失败 fail-open warn（既有语义），残留绑定（频道被删）无害，无主动清理。

### D5 幂等：内容键复用 deliver_key 模式（弃时间窗内存去重）

MM 对超时 webhook 重试 → 同 `(user_id, text)` 重复 POST。方案：**wrap 后 PRD 全文 sha256 作内容键**，复用 `/deliver` 的 db 幂等先例（queue_items.deliver_key 撞键回既有项）。要点：
- MM 入站的 wrap **确定性**（frontmatter 不含时间戳/uuid——与面板 wrap 的差异点：`source: mm-channel`，含 user/频道/project，不含随机成分）
- `deliver_key = f"mm:{project}:{user_id}:{sha256(wrapped)[:16]}:{yyyymmddhh}"`（小时窗掺入，**UTC**——对齐 hub 既有 stamp UTC 先例）→ 同小时内重试幂等回执「近期同内容已入队 #id」；跨小时窗允许**有意重发**（用户隔天重跑同需求不被拦）
- 备选纯内存时间窗去重弃：重启丢窗口 + hub 风格是 db 层幂等（/deliver 先例）

### D6 slash token 第三个独立凭据实体（F14 延续）

`HUB_MM_SLASH_TOKEN`（MM 管理台建 slash command 时下发）。请求校验 `hmac.compare_digest`（对齐 `/deliver` 的 Bearer 比对风格——恒时比较）。凭据三实体清晰分职：OAuth app（登录身份）/ bot token（出站推递）/ slash token（入站校验）。未配 = 端点 503（对齐 auth 群未配置回退态形态），入站关闭零行为。

### D7 双回执

- **ephemeral**：slash HTTP 响应 JSON（`{"response_type":"ephemeral","text":...}`）——仅发起人可见，即时（成功含队列项 #id；拒绝含原因 + 可操作信息：可用 project 清单 / evidence 格式示例 / bind 提示）
- **频道时间线**：入队成功后 hub 经 bot 另 post 一条非 ephemeral 消息进频道（「@发起人 已入队 #id：<需求首行摘要>」）——团队可见性。**后置于 HTTP 响应**（MM 对 slash 响应有秒级超时——频道 post 拉长处理链会放大超时→重试面；响应先回、频道 post 背景执行、失败 fail-open）。拒绝不进频道（个人错误不公开广播；evidence 拒绝的公开教育价值由 ephemeral 回执承担）

## Risks / Trade-offs

- [slash text 长度上限（MM 量级 ~2000 字符）] → 入站护栏：超 `_DELIVER_MAX_BYTES`/text 上限直接 ephemeral 拒绝并提示「长需求走面板」；不截断（截断的 PRD 是语义损坏）
- [MM 宕机/网络抖动使频道成员查询 UNKNOWN] → fail-closed 拒绝入站（宁拒勿旁路）；ephemeral 回执「授权校验暂不可用，稍后重试」
- [同小时窗有意重发被幂等拦] → 回执明示「近期同内容已入队 #id」+ 窗口仅 1 小时；改措辞即绕过——摩擦可接受
- [env seed 迁移窗口配置漂移] → seed 幂等（表非空跳过）+ 迁移后 env 键忽略；回滚 = 清表重配 env（映射量级：个位数）
- [bot 被踢出频道后出站静默] → 既有 fail-open warn 覆盖；无 run 放弃告警里有 stderr 兜底
- [绑定表与 MM 侧频道现实的漂移（改名/删除）] → 残留绑定无害（入站随频道消失、出站 fail-open）；`/pa bind` rebind 即修正

## Migration Plan

1. 部署 hub 新版本（含表迁移 + 端点），`hub.env` 配 `HUB_MM_SLASH_TOKEN`（可先不配——入站关闭态不影响出站）
2. MM 管理台：创建 `/pa` slash command（callback URL = `http://<hub>:<port>/mm/intake`，记下下发 token 回填 env，重建容器）
3. 把 bot 拉进目标项目频道 → 频道内 admin 执行 `/pa bind <project>` → 回执确认
4. 频道内成员试发 `/pa run <需求+出处>` → ephemeral 回执 + 频道时间线 + run 跑完终态回频道

回滚：unset `HUB_MM_SLASH_TOKEN`（入站关闭）；绑定表 inert（出站回落既有 per-user 私信形态需同时清绑定表——`/pa unbind` 或运维清表）。

## Open Questions

（无——探索阶段已全部定案；实现期仅余频道成员缓存 TTL 等参数微调）
