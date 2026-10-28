# add-mm-channel-intake

## Why

部门多人要经 MM 直接给 pa 流水线发开发需求。hub 出站通知（hub-notifications，spec `openspec/specs/hub-notifications/`）已于 2026-10-27 真机打通（MM bot PAT 通道自检三步全绿），需求侧入口仍只有面板表单 / HTTP API / vault deliver——同事的入口摩擦在于：要 OAuth 登录面板、要记 project 名、要懂表单字段。缺的只有**入站面**；且探索确认 hub 在多人维度已就绪（per-project 串行位、enqueued_by 三代分流、per-user 凭据兜底、容器峰值上限、meta 署名审计全部现成），入站通道是纯增量。

## What Changes

- **「频道即项目」双向绑定**：每个项目绑定一个 MM 频道。入站时 hub 由 slash command POST 自带 channel_id 反查绑定推断 project（用户不打 project 名）；出站时项目事件从「仅失败广播」升级为「全事件进频道」（入队回执 / run 终态 / PR 链接 / critic-verify 裁决——频道 = 工作区，成功也是项目状态）。绑定映射升级为 db 实体（`HUB_MM_PROJECT_CHANNELS` env 一次性 seed 迁移后废止，db 唯一真相）。
- **MM slash command 入站端点**：`/pa run <需求正文+evidence 出处>` → hub 新端点 → 频道成员即授权（hub 经 bot token 查 channel members，零白名单配置、粒度 per-project）→ evidence 必填对齐面板（「空依据闸前拒」）→ 复用 `/panel/prd` 的 wrap frontmatter → prd_store 落盘 → engine.enqueue 链（enqueued_by = 发起人 mm_user_id 走既有直发路由）。slash HTTP 响应直接成为 MM ephemeral 回执；hub 另 post 非 ephemeral 回执进频道（团队时间线）。
- **`/pa bind` 管理命令**：绑定/换绑频道，权限复用 `HUB_PANEL_ADMINS` 白名单；bind 时校验 project 在 data_root 必读面；绑定严格 1:1。
- **通知路由按归属维度**：有所属项目的事件 → 该项目频道（@发起人 提及实现个人触达，替代个人 DM）；无频道可归（hub 级运维事件 + 未绑定项目的项目事件含无 run 放弃）→ admin DM 兜底（`cfg.user_id` 单人，零改动）。DM 从「一切终态私信发起人」收窄为兜底。
- **新凭据实体 slash token**：MM→hub 请求校验用 slash command 自带 token，为第三个独立 MM 凭据实体（OAuth 登录 / bot token 出站 / slash token 入站，延续 F14「身份与通道两范畴」）。不配 = 入站关闭零行为（对齐既有「未配置零行为」模式）。
- **幂等去重**：MM 对超时 webhook 重试会产生重复 POST——按 `(user_id, text)` hash + 时间窗去重（对齐 `/deliver` 幂等先例的精神：重复请求回既有项，不双入队）。

执行面（runner/lifecycle/队列语义/凭据/审计）零改动。

## Capabilities

### New Capabilities
- `hub-mm-intake`: MM 入站需求通道——slash command 端点（token 校验/未配置零行为/子命令分流）、频道上下文推断 project、频道成员授权（三态 fail-closed，403=判据不可用）、evidence 必填闸（全/半角冒号两形态）、幂等去重（UTC 小时窗内容键）、ephemeral+频道双回执（时间线后置）、`/pa bind` 管理命令（admin 白名单双轨授权 + db 绑定表与 env seed 迁移）。

### Modified Capabilities
- `hub-notifications`: 项目频道语义从「可选失败广播」（`HUB_MM_PROJECT_CHANNELS` 单向 env 映射）升级为「双向绑定 + 全事件广播」（db 实体、入站推断消费同一映射、成功终态/PR/裁决也进频道、@发起人 提及）；run 终态 per-user 私信面收窄（绑定频道的事件不再私信，DM 兜底无频道可归事件）。

## Impact

- **代码**：`hub/api.py`（新 slash 入站端点 + bind 端点）、`hub/db.py`（project_channels 表 + seed 迁移）、`hub/notify.py`（频道全事件广播 + @提及 + DM 收窄路由）、`hub/config.py`（slash token 配置项）、`hub/auth.py` 或新模块（MM 频道成员查询）、对应 test_*。
- **API**：hub 新增 2 个 POST 端点（slash 入站 + bind）；既有端点零变化。
- **部署**：hub.env 新增 slash token 配置（MM 管理台建 slash command 时获得）；MM 侧需管理员创建 `/pa` slash command 指向 hub 入站端点。
- **依赖**：零新运行时依赖（slash command 是 MM→hub 的普通 HTTP POST，复用 FastAPI + httpx；不引入 WebSocket/长连接组件）。
- **兼容**：`HUB_MM_PROJECT_CHANNELS` env 首次启动 seed 迁移进 db 后废止——部署面一次性动作；未配 slash token 时入站关闭，出站行为在迁移前不变。
- **联调事实**：本 MM 实例（172.32.153.184:8065）PAT 生效形态为单段访问令牌（非 `id.secret` 拼接），2026-10-27 实证，已记 RUNBOOK §16。
