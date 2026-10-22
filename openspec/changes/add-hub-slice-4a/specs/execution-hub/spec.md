# execution-hub delta（add-hub-slice-4a）

## MODIFIED Requirements

### Requirement: 最小 API 面
hub SHALL 提供 FastAPI 既有三端点：`POST /queue`（入队：PRD 路径 + project + 可选 image_tag）、`GET /queue`（队列状态）、`GET /runs/{id}`（run 详情），auth 端点群：`GET /auth/authorize`、`GET /auth/callback`（OAuth2 登录）、`GET /auth/me`（会话身份，强制会话）、`GET /auth/logout`（清会话）、`PUT /auth/credentials`（会话凭据录入，强制会话），投递端点：`POST /deliver`（state ownership 交接：PRD 内容投递 + 幂等键去重 + 落盘入队，协议见 spec: prd-delivery），以及回退原语：`DELETE /queue/{item_id}`（回退序列人工清除：仅 pending 可清，清除 = 转终态 failed + last_error 记原因，行保留审计不物理删除）。守卫边界：`POST /queue` 会话可选（有则真实发起人身份、无则单用户兜底）；`POST /deliver` 为 vault→hub 机间通道（守卫口径见 prd-delivery 设计）；读面与回退原语收紧（F2，add-hub-slice-4a）——`GET /queue`、`GET /runs/{id}`、`DELETE /queue/{item_id}` 在 auth 已配置态（MM 四项齐）SHALL 强制有效会话（无会话 401），auth 未配置回退态 SHALL 保持无守卫现状（2a 形态零变化）；公网/多租户暴露前的 token + socket proxy 硬门槛仍属部署期门，不在本端点面。MUST NOT 引入面板 UI（归切片 4b）或任何语义判定端点。

#### Scenario: CLI 驱动闭环
- **WHEN** 维护者经 hub CLI（或 curl 三端点）入队一份本地 PRD 并轮询状态
- **THEN** 可观察 pending → running → 终态全链（FakeRunner 下无需 docker）

#### Scenario: auth 群端点可巡检
- **WHEN** curl 依次访问 auth 五端点（未登录态）
- **THEN** authorize 正常 302、me/credentials 401、logout 200 清 cookie；未配置 MM 时 authorize/callback/me/credentials 503 明确报「未配置」，logout 仍 200（幂等清 cookie 无 MM 依赖）

#### Scenario: 投递端点幂等可巡检
- **WHEN** curl 对同一 `(project, 文件名, sha256)` 连续两次 `POST /deliver`
- **THEN** 第一次 2xx 返回新队列项标识；第二次 2xx 返回同一标识且标记重复（不重复入队）

#### Scenario: 读面会话守卫（auth 已配置态）
- **WHEN** MM OAuth 已配置（auth 群可用态），无有效会话 cookie 访问 `GET /queue`、`GET /runs/{id}` 或 `DELETE /queue/{item_id}`
- **THEN** 三端点均 401 拒绝；携带有效会话 cookie 则照常 200/2xx

#### Scenario: 读面回退态零变化（auth 未配置）
- **WHEN** 未配置 MM（2a 回退态），无会话访问 `GET /queue`、`GET /runs/{id}`、`DELETE /queue/{item_id}`
- **THEN** 端点行为与收紧前完全一致（无守卫检查，内网单用户形态照跑）
