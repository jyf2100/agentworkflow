# execution-hub

## Purpose

执行面常驻服务的骨架契约：SQLite 持久执行队列（per-project 串行、跨项目并行）、run 记录与 F10 卷上署名 meta（写面钉死为仅 meta 署名文件）、容器生命周期（1:N 即弃 + 续跑 + 崩溃对账）、F13 入队钉镜像 tag 与缺失 fail 暴露、凭据来源 env 直注兜底 + 会话录入优先（spec: hub-oauth-session）、ContainerRunner 注入接缝（Docker/Fake 双实现）。hub 只做机械活——排队、起容器、meta 署名、状态渲染；语义工作全部发生在容器内的 persona 链。

## Requirements

### Requirement: 队列排度（per-project 串行 + 跨项目并行）
执行队列 SHALL 持久化于 SQLite（hub 重启不丢），并按 per-project 串行、跨项目并行排度：同一 project 同时至多一个 running 队列项（DB 查询为权威判据），全局活跃容器数不超过可配峰值上限。容器内 `.run.lock` 文件锁为防御层（防绕过队列直接起容器）。

#### Scenario: 同项目第二项等待
- **WHEN** project A 已有 running 队列项，同 project 新项入队
- **THEN** 新项保持 pending，直至前项终态；期间其他 project 的 pending 项照常派发

#### Scenario: hub 重启队列不丢
- **WHEN** hub 进程崩溃后重启
- **THEN** pending 队列项完整保留并可继续派发，running 项经对账恢复跟踪（见容器生命周期 requirement）

#### Scenario: 峰值上限约束
- **WHEN** 活跃容器数已达上限且有新 project 的 pending 项
- **THEN** 该项等待已有容器终态释放名额后才派发

### Requirement: 入队钉镜像 tag（F13）
队列项 SHALL 在入队时记录确定的镜像 tag（显式传入或取配置默认并固化进队列项），MUST NOT 在派发时解析「最新」。起容器时本地无该 tag 镜像 SHALL fail 暴露（队列项转 failed 并给出手动 build 提示），MUST NOT 静默回退 `:latest` 或任何其他 tag。镜像存在性检查 SHALL 三态分流（对齐 fail-safe-dispatch 三态不变式）：确无该 tag（NOT_FOUND）→ fail 终态；docker daemon 不可达（UNKNOWN：cannot connect / permission denied / 守护进程未跑）→ 基础设施异常走记日志重试路径，MUST NOT 误判为镜像缺失进 fail 终态。

#### Scenario: 入队固化 tag
- **WHEN** 入队请求未显式指定 image tag
- **THEN** 取配置默认 tag 并将其写入队列项记录；派发与 meta 署名均使用该固化值

#### Scenario: 镜像缺失 fail
- **WHEN** 派发时 `docker image inspect` 发现队列项钉定的 tag 本地不存在（FakeRunner 对 fake 镜像清单同判）
- **THEN** 队列项转 failed 并报告缺失 tag 与 build 提示，不产出容器、不回退其他 tag

#### Scenario: daemon 不可达不是镜像缺失
- **WHEN** 派发时镜像检查因 docker daemon 不可达而失败（cannot connect / permission denied）
- **THEN** 该项按基础设施异常记日志并走重试路径，不转 failed 终态、不给 build 提示（UNKNOWN ≠ NOT_FOUND）

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

### Requirement: 容器生命周期（1:N 即弃 + 续跑 + 崩溃对账）
run 与容器 SHALL 为 1:N：容器即弃载体，崩溃续跑 = 同一 run 的新容器（journal 在卷上延续）。hub SHALL 检测容器非零退出并记录 exit_code；同一队列项的重派受可配上限约束，超限转 failed。hub 崩溃 MUST NOT 杀执行容器（独立进程树）；hub 重启后 SHALL 经 docker inspect 对账恢复 running 项跟踪（容器仍在跑 = 继续跟踪；容器已消失 = 按执行证据归档终态：存在任一执行证据——docker 终态或卷上 journal——即 failed，两者皆无即 failed_to_start。failed_to_start 判据统一为「无任何执行证据」，不以 container_ref 有无区分，本体论审核 B1）。

#### Scenario: 容器非零退出
- **WHEN** 执行容器以非零码退出
- **THEN** run 记录 exit_code 与 failed 状态；队列项在重派上限内可续跑（同 run 新容器、复用卷上 state）

#### Scenario: 重派回 pending 保序
- **WHEN** 某队列项的 run 因容器非零退出而 failed 且 attempt 未达重派上限
- **THEN** 队列项回 pending（attempt 计数保留，不占 running 名额）；同 project 无 running 项时按 FIFO 取最老 pending 派发——重派项因入队更早天然优先于后入队项；attempt 达上限才转 failed 终态（run 的 failed 是容器事实，队列项的 failed 是放弃决定，两个生命周期分离）

#### Scenario: hub 崩溃不伤 run
- **WHEN** hub 进程在 run 执行中崩溃后重启
- **THEN** 执行容器未被动过；对账循环将其重新纳入跟踪并最终记录终态

#### Scenario: 重派超限止损
- **WHEN** 同一队列项的容器失败次数达到重派上限
- **THEN** 队列项转 failed 终态，不再派发

### Requirement: 单用户凭据来源（vault 优先 + env 兜底）
hub SHALL 以凭据保险箱（hub-credential-vault capability）为容器注入的凭据来源：派发时按发起 user 从保险箱取三值（ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN / GH_TOKEN）与可选模型路由，起容器时写 0600 权限临时 env-file 注入、容器即弃后删除。单用户过渡形态 SHALL 保留 env 直注兜底：保险箱无该 user 记录时降级读 hub 进程 env（warn 日志可见），保证切片 1 部署形态不破。凭据 MUST NOT 落入 hub DB、日志或 meta 文件。

#### Scenario: vault 优先注入
- **WHEN** 派发起容器且保险箱存在该 user 的凭据记录
- **THEN** env-file 内容取自保险箱解密结果（三值 + 可选模型路由），不经 hub 进程 env

#### Scenario: 保险箱缺记录时 env 兜底
- **WHEN** 保险箱无该 user 记录但 hub 进程 env 具备必需值
- **THEN** 降级用 env 直注并记 warn 日志（可追溯降级发生），容器正常起

#### Scenario: 缺凭据明确报错
- **WHEN** 保险箱与 env 均无必需凭据
- **THEN** 明确报错列出缺失项，不静默降级或空值注入

### Requirement: ContainerRunner 注入接缝
容器操作 SHALL 经 ContainerRunner Protocol（start/status/stop 三方法 + RunSpec 载荷）抽象，DockerRunner（docker CLI）与 FakeRunner（进程内可编程模拟：可注入崩溃/延迟/退出码/镜像缺失）为两实现。排度与生命周期逻辑 MUST 只依赖 Protocol——无 docker 环境可测全分支；FakeRunner MUST 与 DockerRunner 保持语义同构（含镜像存在性校验）。运行时隔离语义（非 root、资源限额、网络策略）的契约归 `isolated-observable-execution` capability——hub 职责仅限传参与施加（--cpus/--memory/--env-file 等），MUST NOT 在本 capability 内另行定义隔离语义。

#### Scenario: Fake 全分支离线可测
- **WHEN** 测试以 FakeRunner 注入排度器
- **THEN** 队列/meta/生命周期/失败路径全部分支无需 docker daemon 即可覆盖

#### Scenario: 真镜像干跑
- **WHEN** 本机有 docker daemon 与 pa-executor 镜像（real-docker 标记测试）
- **THEN** DockerRunner 对真镜像起容器跑机械自检段并正确报告终态

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

### Requirement: hub 只做机械活
hub SHALL 只做机械活（排队、起容器、meta 署名、状态渲染），MUST NOT 产生任何语义判定（不评 PRD 质量、不判信号、不审 diff）——机械/语义切分在服务器形态不动摇；语义工作全部发生在容器内的 persona 链。

#### Scenario: 语义判定无入口
- **WHEN** 审视 hub 的 API 面与模块职责
- **THEN** 不存在任何 PRD 质量评判、信号打分或 diff 审查的端点或模块

### Requirement: 排度常驻服务
hub SHALL 提供常驻排度循环：启动时先跑一次重启对账（reconcile_startup，幂等），随后按可配间隔（HUB_TICK_INTERVAL，默认 5s）循环执行派发 tick 与在跑对账（reconcile_running）。进程收到 SIGTERM/SIGINT SHALL 优雅退出：有界等待当前迭代收尾（daemon 线程 join 上限，与容器编排 stop_grace_period 配合）；迭代超出有界等待时随进程退出、不保证迭代完整——悬挂 run 行由重启对账归档兜底（durable 口径：退出与崩溃同靠对账收敛，不靠「迭代不可中断」强保证）。MUST NOT 杀在跑容器（docker -d 进程树分离，容器由崩溃对账状态机接管）。循环体异常 MUST 捕获记日志并续跑（无静默失败），连续异常计数告警。

#### Scenario: 常驻循环驱动派发
- **WHEN** 常驻服务运行且有可派队列项
- **THEN** 每个间隔至多派发一个容器并轮询在跑容器终态（与手动 tick 语义一致）

#### Scenario: 优雅退出不动容器
- **WHEN** 服务收到 SIGTERM 且当前迭代进行中
- **THEN** 有界等待内迭代完成则干净退出；迭代超界时随进程退出（悬挂 run 由重启对账归档，不阻塞退出）。在跑容器始终不受影响，重启后经 reconcile_startup 续跟踪或归档

#### Scenario: 循环异常不静默
- **WHEN** 单次迭代抛出异常（如 docker CLI 瞬时失败）
- **THEN** 异常记日志（含计数），下个间隔照常迭代；不因单次异常退出常驻循环
