## MODIFIED Requirements

### Requirement: 最小 API 面
hub SHALL 提供 FastAPI 既有三端点：`POST /queue`（入队：PRD 路径 + project + 可选 image_tag）、`GET /queue`（队列状态）、`GET /runs/{id}`（run 详情），auth 端点群：`GET /auth/authorize`、`GET /auth/callback`（OAuth2 登录）、`GET /auth/me`（会话身份，强制会话）、`GET /auth/logout`（清会话）、`PUT /auth/credentials`（会话凭据录入，强制会话），以及投递端点：`POST /deliver`（state ownership 交接：PRD 内容投递 + 幂等键去重 + 落盘入队，协议见 spec: prd-delivery）。守卫边界：`POST /queue` 会话可选（有则真实发起人身份、无则单用户兜底）；`POST /deliver` 为 vault→hub 机间通道（守卫口径见 prd-delivery 设计）；GET 两端点内网读面暂无守卫（收紧归切片 4）。MUST NOT 引入面板 UI（切片 4）或任何语义判定端点。

#### Scenario: CLI 驱动闭环
- **WHEN** 维护者经 hub CLI（或 curl 三端点）入队一份本地 PRD 并轮询状态
- **THEN** 可观察 pending → running → 终态全链（FakeRunner 下无需 docker）

#### Scenario: auth 群端点可巡检
- **WHEN** curl 依次访问 auth 五端点（未登录态）
- **THEN** authorize 正常 302、me/credentials 401、logout 200 清 cookie；未配置 MM 时 authorize/callback/me/credentials 503 明确报「未配置」，logout 仍 200（幂等清 cookie 无 MM 依赖）

#### Scenario: 投递端点幂等可巡检
- **WHEN** curl 对同一 `(project, 文件名, sha256)` 连续两次 `POST /deliver`
- **THEN** 第一次 2xx 返回新队列项标识；第二次 2xx 返回同一标识且标记重复（不重复入队）

### Requirement: F10 卷上署名 meta 与写面钉死
hub SHALL 在起容器前向 state 卷 `runs/<proj>/` 写入 `<stamp>_<slug>.meta.json`（字段：initiator / queue_item_id / image_tag / written_at）。hub 对 state 卷的写面 MUST 钉死为**两类枚举**：① meta 署名文件——写入口只接受数据卷根 + project + stamp + slug 四元组拼路径，文件名必须匹配 `^\d{8}(m\d*)?_[a-z0-9-]+\.meta\.json$`（stamp 形态 = 8 位日期 + 可选 mN 避碰后缀，对齐 run_daily `_bump_stamp_suffix` 事实）；② 投递 PRD 落盘（add-hub-slice-3）——写入口只接受 `<卷根>/<project>/prd/<文件名>` 单一拼法，文件名必须匹配 `^\d{8}(m\d*)?_[a-z0-9-]+(-[a-f0-9]{8})?\.md$`（vault 侧 PRD 产出名 `<stamp>_<slug>.md` + 撞名异容 `-<sha8>` 后缀形态），落盘前校验 `sha256(content)` 与幂等键声明一致；同文件名已存在且 sha 相同（同名同容）幂等复用不重写，sha 不同（同名异容）时落盘名追加 `-<sha8>` 后缀。两类之外 hub MUST NOT 写 journal、删改 state 其他内容（不存在 state 卷通用写函数）。run 名 SHALL 由 hub 派发时预分配：基于 PRD 文件名的 `<stamp>_<slug>` 扫卷撞名则自增后缀直到 fresh，经 run_daily 既有 `--stamp` 参数传入容器——依赖契约「inject 段仅在撞名时 bump」（见 durable-loop-state delta），由此 meta 文件名、`--stamp`、实际 run 目录三者对齐。容器内执行链经既有 inject 桥消费投递 PRD（挂载 `/input/` + `--from-stage inject --inject-prd`，见 spec: prd-delivery）——容器内 pa 写自己的 state（含 inject 规范化落 `state/prd/<project>/` 与 manifest）是 pa 既有写面，不属 hub 写面枚举管辖。hub DB 的 runs 表是索引而非真相，可从卷上 meta 重建。

#### Scenario: 起容器前署名
- **WHEN** 排度器决定为某队列项起容器
- **THEN** 先预分配 fresh run 名并写 meta 署名文件，再以同一名触发 ContainerRunner.start（`--stamp` 传入）；meta 中的 initiator 在单用户 bootstrap 形态下为配置单用户（schema 即终态，切片 2 只换值来源）

#### Scenario: 重复入队分配新 run
- **WHEN** 同一 PRD 文件在既有 run（含非终态）之后再次入队
- **THEN** 预分配撞名自增出新的 fresh run 名，两次入队为独立 run、各自 meta——不拒绝重复入队（幂等防重是切片 3 投递 API 层语义，人工再跑是合法意图）

#### Scenario: 写面钉死（两类枚举外拒绝）
- **WHEN** hub 内任何代码路径尝试向 state 卷写入两类枚举以外的路径或文件名，或投递落盘路径逃逸 `prd/` 目录
- **THEN** 写入口拒绝并报错；hub 模块结构中不存在 state 卷的通用写函数

#### Scenario: 投递 PRD 落盘模式钉死
- **WHEN** `POST /deliver` 落盘一份新 PRD 至 `<卷根>/<project>/prd/<文件名>`
- **THEN** 落盘前 sha256 校验通过才写；同文件名同容（sha 相同）幂等复用不重写，异容时落盘名带 `-<sha8>` 后缀；journal 与其他 state 内容零触碰

#### Scenario: 孤儿 meta = failed-to-start
- **WHEN** 恢复对账发现某 run 目录存在 meta 署名文件但 journal 缺失
- **THEN** 该 run 标记为 failed_to_start 并保留 meta 作审计证据，不静默清理
