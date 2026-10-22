## ADDED Requirements

### Requirement: Mattermost OAuth2 登录流程
hub SHALL 以 Mattermost 为 OAuth2 IdP（confidential client）提供登录：`GET /auth/authorize` 302 至 MM 授权页（携带 client_id / redirect_uri / state），`GET /auth/callback` 以 code + client_secret 调 MM token 端点换 access token，再调 MM 用户 API 拉取 mm_user_id 与 username，随后发会话 cookie。state SHALL 签名短期往返校验（防 OAuth CSRF）；state 是一次性 CSRF nonce 载体（payload 仅 nonce），非身份、非会话——与会话同签名机制但不同范畴。code 无效、用户拒绝、MM 端点失败 SHALL 明确报错且不产生半成品会话。

#### Scenario: 完整登录
- **WHEN** 未登录用户经 `/auth/authorize` 到 MM 授权并回跳 callback（code 有效、state 校验通过）
- **THEN** hub 换 token 拉得 mm_user_id 与 username，响应 Set-Cookie 会话，重定向至可达目标

#### Scenario: code 无效明确报错
- **WHEN** callback 收到无效/过期 code（MM token 端点拒绝）
- **THEN** 明确报错（不静默、不发会话 cookie）

#### Scenario: state 校验失败拒绝
- **WHEN** callback 的 state 与发起时不匹配或缺失
- **THEN** 拒绝处理该 code（CSRF 防御），报错不发会话

#### Scenario: MM 宕机登录失败
- **WHEN** 授权/token/用户 API 任一调用因 MM 不可达失败
- **THEN** 登录失败并明确报错，不产生会话（影响面 = 无新登录，F7）

### Requirement: 签名会话生命周期
会话 SHALL 为自包含 HMAC 签名 cookie（payload：mm_user_id / username / exp；签名 key 经 env 注入，MUST 与保险箱 master key 分离）。有效期默认 7 天、可配。已发会话 SHALL 不依赖 MM 可达（hub 重启、MM 宕机均不影响持有者的既有会话）。`GET /auth/me` SHALL 返回当前会话身份（无有效会话 401）；`GET /auth/logout` SHALL 清会话 cookie。会话不可服务端吊销（自包含形态），内网团队规模接受、公网暴露前收紧（F2 复审结论）。

#### Scenario: hub 重启会话仍有效
- **WHEN** 用户持有效会话 cookie 而 hub 进程重启后再访问 `/auth/me`
- **THEN** 身份照常返回（无服务端会话状态）

#### Scenario: MM 宕机已发会话可用
- **WHEN** MM 完全不可达而用户持有效会话操作（录入凭据、入队）
- **THEN** 会话操作不受影响（只有新登录不可用）

#### Scenario: 会话过期与伪造拒绝
- **WHEN** cookie 超过有效期或签名不合法
- **THEN** 视同无会话（401 / 兜底单用户），不产生报错副作用

### Requirement: 主干零依赖 Mattermost（F14 贯彻）
自动化主干（入队 → 排度 → 容器执行 → 报告落卷 → 对账）SHALL 零依赖 Mattermost / OAuth 面可用性：认证失效只影响新登录与凭据录入端点，队列、执行、崩溃对账照跑。未配置 MM env 时 auth 群端点 SHALL 返回明确「未配置」错误（503）而非静默空转（logout 除外：幂等清 cookie、无 MM 依赖，未配置时 200——不制造伪故障信号）；其余端点与既有行为零差异。

#### Scenario: OAuth 挂执行链照跑
- **WHEN** MM/OAuth 面完全不可用而队列有待执行项
- **THEN** 派发、容器执行、终态记录与无认证时完全一致

#### Scenario: 未配置 MM 时 auth 群明确 503
- **WHEN** 未配 MM 相关 env 而访问任一 auth 端点
- **THEN** 返回 503 与「未配置」明确信息；其余端点行为不变（2a 形态即回退态）
