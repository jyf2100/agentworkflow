## MODIFIED Requirements

### Requirement: 最小 API 面
hub SHALL 提供 FastAPI 既有三端点：`POST /queue`（入队：PRD 路径 + project + 可选 image_tag）、`GET /queue`（队列状态）、`GET /runs/{id}`（run 详情），并新增 auth 端点群：`GET /auth/authorize`、`GET /auth/callback`（OAuth2 登录）、`GET /auth/me`（会话身份，强制会话）、`GET /auth/logout`（清会话）、`PUT /auth/credentials`（会话凭据录入，强制会话）。守卫边界：`POST /queue` 会话可选（有则真实发起人身份、无则单用户兜底）；GET 两端点内网读面暂无守卫（收紧归切片 4）。MUST NOT 引入投递 API（切片 3）、面板 UI（切片 4）或任何语义判定端点。

#### Scenario: CLI 驱动闭环
- **WHEN** 维护者经 hub CLI（或 curl 三端点）入队一份本地 PRD 并轮询状态
- **THEN** 可观察 pending → running → 终态全链（FakeRunner 下无需 docker）

#### Scenario: auth 群端点可巡检
- **WHEN** curl 依次访问 auth 五端点（未登录态）
- **THEN** authorize 正常 302、me/credentials 401、logout 200 清 cookie；未配置 MM 时 authorize/callback/me/credentials 503 明确报「未配置」，logout 仍 200（幂等清 cookie 无 MM 依赖）

## ADDED Requirements

### Requirement: 发起人身份来源（会话优先、单用户兜底）
入队 `enqueued_by` SHALL 会话优先：有效会话 → 以 mm_user_id 为稳定 key（MM username 可改、id 不变），username 供人读面（F10 meta initiator、状态渲染）；无会话（CLI / 内部调用）→ 兜底配置单用户（2a 形态不破）。派发时凭据解密 SHALL 按 enqueued_by 的 key 取保险箱记录（无记录降级 hub env，既有逻辑零变化）。

#### Scenario: 会话入队真实身份
- **WHEN** 已登录用户 POST /queue
- **THEN** 队列项 enqueued_by 记该用户 mm_user_id，meta initiator 记其 username，容器注入按其保险箱记录解密

#### Scenario: 无会话入队单用户兜底
- **WHEN** 无会话（CLI / 内部）POST /queue
- **THEN** enqueued_by 兜底配置单用户，meta initiator 同源（默认兜底路径 2a 行为零变化；显式传 enqueued_by 路径 = 语义对齐修正——meta 署名改为与队列项发起人一致，D4）

#### Scenario: 按发起人解密凭据
- **WHEN** 会话用户发起的 run 派发起容器且保险箱有其记录
- **THEN** env-file 取自该用户记录解密结果；无其记录时降级 hub env 并记 warn（可追溯）
