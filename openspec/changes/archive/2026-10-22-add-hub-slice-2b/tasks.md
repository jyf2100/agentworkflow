## 1. 会话基元（auth 模块地基）

- [x] 1.1 `hub/auth.py`：HMAC 签名 cookie 基元（sign/verify，payload {mm_user_id, username, exp}；cryptography 手写零新依赖）+ TDD（有效/过期/伪造/缺字段全分支）
- [x] 1.2 会话配置接入：`HUB_SESSION_SECRET` / `HUB_SESSION_TTL`（默认 7d）读入 cfg；secret 未配时 auth 群明确「未配置」（不崩 serve）
- [x] 1.3 httpx 依赖钉入 hub `pyproject.toml`（或依赖清单）+ Dockerfile 装包，镜像 build 通过

## 2. OAuth 登录流程（MM mock 下 TDD）

- [x] 2.1 `GET /auth/authorize`：302 至 MM 授权页（client_id/redirect_uri/state），state 签名短期 cookie（10 min）；未配置 MM → 503 明确报错
- [x] 2.2 `GET /auth/callback`：code+secret 换 token → MM `/api/v4/users/me` 拉身份 → 发会话 cookie；失败路径（code 无效/state 不匹配/MM 宕机/用户拒绝）明确报错不产半会话
- [x] 2.3 `GET /auth/me`（强制会话，401 分支）与 `GET /auth/logout`（清 cookie）
- [x] 2.4 MM 接缝 mock：测试注入假 token/用户响应（monkeypatch httpx），登录全分支离线可测（spec 三 scenario 逐条对应测试）

## 3. 凭据录入 + 发起人接线

- [x] 3.1 `PUT /auth/credentials`：会话守卫（401）→ 字段校验（缺三值列缺失项，原子不落部分）→ 以 mm_user_id 为 key 写保险箱（复用 vault_store）→ 响应只含确认与字段名
- [x] 3.2 `POST /queue` 会话优先接线：有会话 → enqueued_by={mm_user_id, username}；无会话 → cfg.user_id 兜底（2a 行为零变化回归测试）
- [x] 3.3 派发链贯通测试：会话用户入队 → meta initiator=username → 容器 env-file 按其 vault 记录解密；无记录降级 env + warn（FakeRunner 全链）

## 4. 部署物 + 文档收口

- [x] 4.1 compose/hub.env.example 补 MM 四项（HUB_MM_BASE_URL / HUB_MM_CLIENT_ID / HUB_MM_CLIENT_SECRET / HUB_SESSION_SECRET），注释钉「不配 = 2a 回退态」
- [x] 4.2 RUNBOOK §13 补 auth 配置段（MM 管理员注册 OAuth app 前置 + redirect_uri 指向 + 四 env 说明 + 排障：503 未配置/401 无会话）
- [x] 4.3 F2 复审结论登记：D6 两笔（socket proxy 外网门槛、master key 轮换后手）落到风险/债清单处（若仓内有集中债清单则入清单，无则 RUNBOOK §13 注记）
- [x] 4.4 全量质量门 `bash hub/quality.sh` 绿（compileall + pytest + ruff）+ openspec validate 通过
