## Context

2a 收口态：hub 单常驻 compose 服务，保险箱 per-user schema 已就位但录入面只有 `vault-import --from-env`（运维单用户），`enqueued_by` 兜底 `cfg.user_id`，最小 API 面 = 三端点。总设计已裁：MM OAuth2 做 IdP、不新建用户系统（§3）、F14 双角色分离（OAuth 身份归本切片、bot 归 4）、F7 身份源单点影响面可接受、测试用 OAuth mock（§7）。用户裁决（2026-10-22）：交付面纯 API 层；F2 两笔挂账复审+按结论定。

## Goals / Non-Goals

**Goals:**
- 同事经 MM OAuth2 登录 → 签名会话 → 本人身份录入凭据入保险箱 → 入队 run 按发起人身份与凭据走全链
- 2a 形态完整保留：无 MM 配置/无会话时系统照跑（单用户兜底）
- F2 两笔安全边界复审出结论（兑现挂账，不再顺延）

**Non-Goals:**
- 面板 UI / 任何 HTML（切片 4）；bot 通知（切片 4）；投递 API（切片 3）
- 凭据轮换/多 master key、用户管理（MM 即用户系统）、授权粒度（团队内同权）
- socket proxy / secrets 方案落实（按复审结论登记，见 D6）

## Decisions

### D1：OAuth 流程 = 标准 authorization code（confidential client）
`GET /auth/authorize` → 302 到 MM 授权页（client_id + redirect_uri + state）；MM 回跳 `GET /auth/callback?code&state` → hub 以 code + client_secret 调 MM token 端点换 access token → 调 MM `/api/v4/users/me` 拉 `mm_user_id` + `username` → 发会话 cookie。state 签名短期 cookie（10 min）往返校验（OAuth CSRF 基线）。无 PKCE——hub 是 confidential client（secret 服务端持有），PKCE 防的公共客户端泄露面不存在。

### D2：会话 = 自包含 HMAC 签名 cookie（无服务端状态）
payload `{mm_user_id, username, exp}` + HMAC-SHA256（用已依赖的 `cryptography` 手写，~30 行，零新包）签名，key 经 env `HUB_SESSION_SECRET` 注入。理由：① hub 重启会话不丢（状态在客户端）② MM 宕机已发会话可用（F7 字面要求）③ 不加 sqlite 会话表（排度器单写者约束不动）。代价：无法服务端吊销（登出 = 清 cookie，客户端责任）——团队 5-15 人内网形态接受。有效期默认 7d 可配。

### D3：出站 HTTP = httpx（sync）
code↔token 与 MM API 调用经 httpx 同步客户端，端点写 `def`（FastAPI 自动线程池）——与 hub 全同步代码基线（daemon 线程/sqlite）一致，不引 async 传染。hub 镜像 +1 纯 py wheel。

### D4：身份键 = mm_user_id 稳定主键，展示 = username
保险箱 per-user 记录 key 与入队 `enqueued_by` 用 `mm_user_id`（MM username 可改、id 不变）；F10 meta `initiator` 与一切人读面用 `username`。2a 既有记录 key（`cfg.user_id` 如 `roc`）天然共存——key 空间不同不冲突，运维通道照旧。

### D5：值来源切换 = 会话优先、单用户兜底（meta schema 零变化；DB 层加人读名列）
`POST /queue`：有会话 → `enqueued_by = {mm_user_id, username}`；无会话（CLI/curl 内部）→ 兜底 `cfg.user_id`（2a 形态不破）。派发解密按 `enqueued_by` 取 vault，无记录降级 hub env（2a 逻辑不动）。F10 meta `initiator` 随 `enqueued_by` 流动——「schema 即终态，只换值来源」兑现（此处的「schema 零变化」指 meta/F10 JSON 字段面）。**修订（2026-10-22 实施收口）**：DB 层 `queue_items` 加 `initiator` 可空列（+既有库 ALTER 迁移，2a 队列不丢）——enqueued_by 单列装稳定 key（mm_user_id）后，人读名需独立存储位；无会话入队该列为 NULL，派发时回落 enqueued_by。GET 两端点维持无守卫（内网读面），守卫收紧归切片 4 面板权限。

### D6：F2 复审结论（挂账兑现）——两笔均「维持现状 + 登记外网门槛」
- **socket 挂载等效 root**：2b 后 hub 攻击面 = 团队内登录用户（5-15 人）+ 内网 compose 形态。socket proxy（docker-socket-proxy 白名单化）收益在公网暴露场景。**结论：维持挂载；登记新债——任何公网/外网暴露前必须先上 socket proxy**（外网暴露门槛条件，不是时间表）。
- **master key 经 compose env**：`docker inspect` 可见面 = 已有宿主 root 权限者，而 root 本可挂载宿主任意路径——收紧（docker secrets/age）对该威胁模型收益边际。**结论：维持 env 注入；登记后手——master key 轮换工具（re-encrypt 全量凭据）归后续 YAGNI**。

### D7：端点守卫边界
公开：`/auth/authorize`、`/auth/callback`、`/auth/logout`、既有 GET 两端点（内网读面）。强制会话：`GET /auth/me`、`PUT /auth/credentials`。可选会话：`POST /queue`（有则真实身份、无则单用户兜底——保 F14 主干零依赖：认证挂了执行链照跑）。未配置 MM env 时 auth 群返回明确「未配置」503，不静默空转。

### D8：MM 接缝可注入 = base URL 可配 + httpx mock
`HUB_MM_BASE_URL` / `HUB_MM_CLIENT_ID` / `HUB_MM_CLIENT_SECRET` / `HUB_SESSION_SECRET` 四 env。测试以 monkeypatch 注入假 token/API 响应（成功/用户拒绝/code 无效/token 端点 5xx/MM 宕机全分支离线可测，§7 定调兑现）；真 MM 联调归真身机部署。

## Risks / Trade-offs

- [MM OAuth app 注册需管理员配合（运营依赖）] → 不阻塞开发/测试（mock）；真部署前置清单列明（RUNBOOK 补节）。
- [会话不可服务端吊泄] → 内网团队规模接受；公网暴露门槛（D6）连带收紧。
- [httpx +1 依赖] → 纯 py wheel 无编译链；版本钉 pyproject。
- [cookie key 与 master key 同 env 通道] → 同威胁模型（宿主 root），随 D6 结论走。
- [username 改名致展示漂移] → key 用 mm_user_id 隔离（D4），meta 历史记录保留当时 username（审计事实不改写）。

## Migration Plan

- 部署：compose env 追加 MM 四项（不配 = auth 群 503「未配置」，其余零影响 = 2a 形态即回退态，无需专门回退路径）。
- 真身机前置：MM 管理员注册 OAuth app（redirect_uri 指向 hub）；RUNBOOK §13 补 auth 配置段。

## Open Questions

无——会话有效期默认 7d（可配）、其余关键分叉均已裁；遗留仅 D6 两笔登记债（有明确门槛条件）。
