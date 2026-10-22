## Why

切片 2a 收口后 hub 已可常驻运行，但仍是**单用户形态**：所有 run 的发起人都是配置单用户（`enqueued_by` 兜底 `cfg.user_id`）、凭据只能运维经 `vault-import --from-env` 一条通道录入（保险箱 schema 是 per-user 的，录入面不是）。切片 2 拆分时已裁决：2b = OAuth 身份——让同事以真实身份进系统、以本人身份录入凭据、run 按发起人注入凭据（F10 meta 的「schema 即终态，切片 2 只换值来源」预留至此兑现）。前置依赖（保险箱/排度常驻/compose）全部就绪，2a 两笔安全边界挂账也钉「2b 落地时复审」。

## What Changes

- **Mattermost OAuth2 登录**：MM 做 IdP（管理员注册 OAuth app），hub 提供 authorize redirect / callback 端点，code 换 token 拉用户身份，发签名会话 cookie；不新建用户系统（总设计 §3）。
- **会话生命周期**：cookie 会话（签名/可配有效期/登出端点）；MM 宕机不影响已发会话与进行中 run（F7 身份源单点边界，登记接受）。
- **per-user 凭据录入 API**：登录会话内提交三值 + 可选模型路由 → 写保险箱本人记录（加密复用 2a 通道；不回显明文）。`vault-import --from-env` 保留为运维兜底通道。
- **发起人身份接线**：入队 `enqueued_by` / F10 meta `initiator` 从会话取真实 MM 用户（会话缺失回落配置单用户——切片 1 形态不破）；凭据按发起人解密注入。
- **F2 复审（挂账兑现，复审+按结论定）**：socket 挂载等效 root、master key 经 compose env 可见两笔边界在 design 阶段复审，结论与后手（socket proxy、secrets 收紧）记录；落实动作若裁「推后」则登记新债不实施。
- **F14 不变式贯彻**：自动化主干（投递→队列→容器→报告落卷）零依赖 Mattermost——OAuth 挂 = 登不了面板/录不了凭据，执行链与 vault cron 投递照跑。
- **交付面 = 纯 API 层**（用户裁决 2026-10-22）：无任何 HTML/前端——面板 UI 归切片 4，届时直接消费本切片会话。

## Capabilities

### New Capabilities
- `hub-oauth-session`: Mattermost OAuth2 登录流程、签名会话生命周期、per-user 凭据录入 API（会话守卫）、F7/F14 边界（身份源单点影响面、主干零依赖 Mattermost）。

### Modified Capabilities
- `hub-credential-vault`: 录入面扩展——新 requirement「会话用户凭据录入」（登录会话内 per-user 写入，与 vault-import 运维通道并存）；读取面/加密不变式零变化。
- `execution-hub`: 「最小 API 面」requirement 扩展（+auth 群端点：authorize/callback/me/logout/credentials，仍无面板无投递）；F10 meta initiator 与凭据解密的值来源从「配置单用户」升级为「会话优先、单用户兜底」（schema 不变）。

## Impact

- **代码**：`hub/` 新增 auth 模块（OAuth 流程 + 会话签名）；`api.py` 挂 auth 端点群；`lifecycle`/入队路径接会话身份；`vault_store` 复用（写入通道已有）。
- **依赖**：hub 镜像新增 OAuth code↔token 与 MM API 调用所需 HTTP 客户端（design 阶段定具体包与形态）。
- **运营依赖（非代码）**：MM 管理员注册 OAuth app（client id/secret；风险登记 #4 已列）；roc 真身机部署时配 MM 端点 env。
- **测试**：TestClient in-process + MM mock（总设计 §7 已定调）；无 MM 实例可全分支离线测；真 MM 联调归真身机部署阶段。
- **不改**：队列排度/容器生命周期/崩溃对账（切片 1-2a 契约）、投递 API（切片 3）、面板/bot（切片 4）、执行镜像。
