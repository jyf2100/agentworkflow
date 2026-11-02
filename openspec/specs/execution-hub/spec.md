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
hub SHALL 在起容器前向 state 卷 `runs/<proj>/` 写入 `<stamp>_<slug>.meta.json`（字段：initiator / queue_item_id / image_tag / written_at）。hub 对 state 卷的写面 MUST 钉死为**两类枚举**：① meta 署名文件——写入口只接受数据卷根 + project + stamp + slug 四元组拼路径，文件名必须匹配 `^\d{8}(m\d*)?_[a-z0-9-]+\.meta\.json$`（stamp 形态 = 8 位日期 + 可选 mN 避碰后缀，对齐 run_daily `_bump_stamp_suffix` 事实）；② 投递 PRD 落盘（add-hub-slice-3）——写入口只接受 `<卷根>/<project>/prd/<文件名>` 单一拼法，文件名必须匹配**两形态之一**：`^\d{8}(m\d*)?_[a-z0-9-]+(-[a-f0-9]{8})?\.md$`（vault 侧 PRD 产出名 `<stamp>_<slug>.md` + 撞名异容 `-<sha8>` 后缀形态），或 `^mm-[a-f0-9]{8}(-[a-f0-9]{8})?\.md$`（MM 入站确定性文件名，本 change D5：`mm-<sha256(wrapped)[:8]>.md` 由 wrap 内容衍生，**零时间戳零随机成分**——同输入 byte-identical 是 deliver_key 幂等的前提；与 stamp 前缀形态**有意偏离**，勿「顺手统一」加回日期/uuid；可选 `-<sha8>` 为撞名异容避碰后缀）；落盘前校验 `sha256(content)` 与幂等键声明一致；同文件名已存在且 sha 相同（同名同容）幂等复用不重写，sha 不同（同名异容）时落盘名追加 `-<sha8>` 后缀。两类之外 hub MUST NOT 写 journal、删改 state 其他内容（不存在 state 卷通用写函数）。run 名 SHALL 由 hub 派发时预分配：stamp 为派发时分配（当日 8 位日期，扫卷撞名自增 mN 后缀直到 fresh）、slug 为 PRD 文件名 stem 的规范化（下划线→横线；stamp 形态 stem 即 `<stamp>_<slug>` 原样、`mm-<sha8>` 形态 stem 整体作 slug），经 run_daily 既有 `--stamp` 参数传入容器——依赖契约「inject 段仅在撞名时 bump」（见 durable-loop-state delta），由此 meta 文件名、`--stamp`、实际 run 目录三者对齐。容器内执行链经既有 inject 桥消费投递 PRD（挂载 `/input/` + `--from-stage inject --inject-prd`，见 spec: prd-delivery）——容器内 pa 写自己的 state（含 inject 规范化落 `state/prd/<project>/` 与 manifest）是 pa 既有写面，不属 hub 写面枚举管辖。hub DB 的 runs 表是索引而非真相，可从卷上 meta 重建。

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

#### Scenario: MM 入站确定性文件名落盘
- **WHEN** MM 频道入站需求经确定性 wrap 后走 `/panel/prd` 同源链落盘
- **THEN** 文件名匹配 `mm-[a-f0-9]{8}(-[a-f0-9]{8})?\.md` 形态被写面②接受（sha 校验与幂等复用规则与 stamp 形态同权）；同输入重投递落同名同容（确定性前提），不产生日期/uuid 类漂移文件名

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
hub SHALL 以凭据保险箱（hub-credential-vault capability）为容器注入的凭据来源：派发时按发起 user 从保险箱取必填两值（ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN）与可选模型路由，以及按**目标项目 forge 声明**要求的对应平台 token（github/未声明 → `GH_TOKEN`；`forge: gitlab` → `GITLAB_TOKEN`；`forge: gitcode` → `GITCODE_TOKEN`；GH_TOKEN 仅 github/未声明项目必需），起容器时写 0600 权限临时 env-file 注入、容器即弃后删除。容器凭据为**派发时点快照**：起容器后 vault 记录变更不影响在跑 run，只影响后续派发。单用户过渡形态 SHALL 保留 env 直注兜底：保险箱无该 user 记录时降级读 hub 进程 env（warn 日志可见），保证切片 1 部署形态不破。凭据 MUST NOT 落入 hub DB、日志或 meta 文件。

#### Scenario: vault 优先注入
- **WHEN** 派发起容器且保险箱存在该 user 的凭据记录
- **THEN** env-file 内容取自保险箱解密结果（必填两值 + 可选模型路由 + 按项目 forge 的平台 token），不经 hub 进程 env

#### Scenario: 跑中 vault 变更不影响在跑容器
- **WHEN** run 执行中用户重提交凭据（vault 记录变更）
- **THEN** 在跑容器继续使用派发时点的 env-file 快照；变更自下一次派发生效

#### Scenario: gitlab 项目缺平台 token
- **WHEN** 项目声明 `forge: gitlab` 而保险箱记录（及 env 兜底）均无 GITLAB_TOKEN
- **THEN** 派发 fail-fast abandon 并明确列出缺失键（与缺三值同口径），不起容器不烧配额

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
hub SHALL 提供 FastAPI 既有 REST 端点：`POST /queue`（入队：PRD 路径 + project + 可选 image_tag）、`GET /queue`（队列状态）、`GET /runs/{id}`（run 详情），auth 端点群：`GET /auth/authorize`、`GET /auth/callback`（OAuth2 登录）、`GET /auth/me`（会话身份，强制会话）、`GET /auth/logout`（清会话）、`PUT /auth/credentials`（会话凭据录入，强制会话），投递端点：`POST /deliver`（state ownership 交接：PRD 内容投递 + 幂等键去重 + 落盘入队，协议见 spec: prd-delivery），回退原语：`DELETE /queue/{item_id}`（回退序列人工清除：仅 pending 可清，清除 = 转终态 failed + last_error 记原因，行保留审计不物理删除），以及**面板端点群（add-hub-slice-4b）**：`GET /panel/*`（面板只读页：队列 / run 列表 / run 详情，强制会话）、`POST /panel/prd`（面板提交：PRD 内容 + project + evidence 必填，强制会话，见 spec: hub-panel）。守卫边界：`POST /queue` 会话可选（有则真实发起人身份、无则单用户兜底）；`POST /deliver` 为 vault→hub 机间通道（守卫口径见 prd-delivery 设计）；`GET /queue`、`GET /runs/{id}`、`DELETE /queue/{item_id}` 与面板端点群在 auth 已配置态 SHALL 强制有效会话（无会话 401/302），auth 未配置回退态：REST 读面保持无守卫现状、**面板端点群 503 不可用**（面板以会话为前提，无会话形态无意义）；公网/多租户暴露前的 token + socket proxy 硬门槛仍属部署期门。hub MUST NOT 提供任何语义判定端点（面板渲染为机械展示，见 spec: hub-panel）。

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
- **THEN** 端点行为与收紧前完全一致（无守卫检查，内网单用户形态照跑）；面板端点群 503（面板以会话为前提）

#### Scenario: 面板端点群会话守卫
- **WHEN** auth 已配置态无有效会话访问 `GET /panel/*` 或 `POST /panel/prd`
- **THEN** 302 到登录（GET）/ 401（POST）；携带有效会话则正常渲染与提交

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

### Requirement: 执行链段范围（inject→report）
hub 派发命令 SHALL 显式传 `--from-stage inject --to-stage report`：执行链 SHALL 跑满 pa 后半段 inject→critic→dispatch→report（而非止于 `--to-stage` 默认值 dispatch）——report 段经 `PA_REPORT_DIR`（派发时注入容器 env）双写产物至 state 卷 `state/reports/`，供面板 run 详情只读渲染（见 hub-panel 报告面与 pa-event-outlet report 双写）。report 段内 SMTP 通知在容器内 MUST 走既有 fail-open 语义（失败退化为告警、不阻塞终态）——容器形态通知面是 hub-notifications，MUST NOT 为此透传 SMTP env。`run_daily --to-stage` 默认值（dispatch）MUST 保持零变化（mac 本机 cron 形态不受影响）。

#### Scenario: 执行容器跑满后半段产 report
- **WHEN** hub 派发一个队列项、执行容器正常跑完
- **THEN** 容器命令含 `--from-stage inject --to-stage report`；dispatch 终态持久化后 report 段执行，state 卷 `state/reports/` 出现该 stamp 的报告产物，面板 run 详情可渲染报告

#### Scenario: SMTP 容器内 fail-open 不阻塞
- **WHEN** report 段 SMTP 通知在容器内因无 SMTP env 而失败
- **THEN** 退化为告警日志，run 终态不受影响（不为 SMTP 向容器透传 env）

#### Scenario: 历史无 report 产物 → 面板显「无报告」
- **WHEN** 面板查看本 change 之前的历史 run（state/reports/ 无其产物）
- **THEN** 面板按 hub-panel 既有语义显示「无报告」（非错误），不回填历史产物

### Requirement: run 容器宿主可配（远程 daemon + 挂载源分离）

hub SHALL 允许把 run 容器派发到远程 docker daemon，派发目标来自**服务器池忙闲自动调度**（`add-hub-runner-pool` 起；原 `HUB_DOCKER_HOST` / `HUB_RUNNER_MOUNT_ROOT` 两 env 键降级为首次启动 seed，见「服务器池与忙闲自动调度」）：docker CLI 子进程注入 `DOCKER_HOST=<选中行 docker_host>`（值形态限 `tcp://` 前缀，第一期内网明文 transport），挂载源投影根 = `<选中行 runner_mount_root>`（与 hub 侧读写根 `HUB_DATA_ROOT` 分离）。挂载源分两范畴（投影模型不变）：**派生类**（state 写面 / profiles / sources.yaml——派发时从投影根现算）与**固化类**（PRD input——队列项恒存 hub 视角路径，派发时投影为挂载视角交付 docker）。无 enabled 远程行（表空/全禁用且 seed 未发生）时全部行为 MUST 与现状 byte-identical（回落本机 daemon）。容器署名、重派、崩溃对账、F10 写面钉死语义在远程形态下 MUST 不变。

**per-run 快照路由**：派发时选中行的 runner 名与 docker_host MUST 快照进 run 记录；此后 runner 行修改或禁用 MUST NOT 影响在飞 run 的 status / stop 路由（tick 对账按行内快照查对应 daemon）——多机并跑与禁用软下线的记账正确性由此保证。部署前提（hub 不在代码层闭合、配置期错误 MUST fail-loud 可见而非静默）：选中行 `runner_mount_root`（daemon 侧路径）与 `HUB_DATA_ROOT`（hub 侧路径）须指同一共享数据面，且该前提**运行期持续有效**（断链属部署事故，stamp 防撞依赖它；健康探针提供可见性）；pa-executor 镜像须在目标 daemon 在场——人工预载或 hub 自动分发（自动分发的触发判据 / 通道 / 降级语义见「runner 池镜像自动分发」；验证时点 = 探针周期 + 派发时 `DockerRunner.start()` 首步复查，检查本身路由到目标 daemon）。

#### Scenario: 无 enabled 远程行零变化

- **WHEN** runners 表为空或全部 disabled 且 seed 未发生（历史形态：两 env 键亦未配）
- **THEN** runner CLI 子进程 env 不注入 `DOCKER_HOST`（与父进程等价），四个挂载源父目录 = `HUB_DATA_ROOT`，全部派发参数与现状 byte-identical

#### Scenario: 选中行派发参数派发时读取

- **WHEN** 调度选中行为 `tcp://172.32.153.183:2375` + `runner_mount_root=/mnt/nfs/pa` 且一个 pending run 被派发
- **THEN** 该次派发的 image_exists / start CLI 子进程 env 含 `DOCKER_HOST=<该值>`，容器创建在目标 daemon 上；容器命名、卷署名 meta、非零退出重派语义与既有 requirement 完全一致；此后该 runner 被禁用或调度选中别的机 MUST NOT 改变本次已派发 run 的后续 status / stop 路由（按本 run 行内快照）

#### Scenario: 非法 transport 形态在登记入口 fail fast

- **WHEN** 登记或 seed 环节提交非 `tcp://` 前缀的 docker_host（如 `ssh://...` 或裸主机名）
- **THEN** 登记动作拒绝并列出合法形态（env seed 场景 = hub 启动即拒），不产生半配置行、不进入半配置运行

#### Scenario: 挂载源前缀分离（两范畴·投影）

- **WHEN** 选中行 `runner_mount_root=/mnt/nfs/pa` 而 `HUB_DATA_ROOT=/data/pa`
- **THEN** 派生类三挂载源（state 写面 / profiles / sources.yaml）派发时以 `/mnt/nfs/pa/<project>/...` 现算；固化类（PRD input）队列项仍存 `/data/pa/<project>/mm-<sha8>.md`（hub 视角），派发时投影为 `/mnt/nfs/pa/<project>/mm-<sha8>.md` 交付 docker `-v`；hub 侧投递落盘、通知面读工件、vault 读写、队列项路径、stamp 防撞与 run 目录记账全部走 `/data/pa/<project>/...`

#### Scenario: 目标 daemon 镜像缺失沿既有 fail 语义

- **WHEN** 目标 daemon 上不存在入队钉死的镜像 tag（含调度选中别的机后，存量在飞 run 的对账场景——status 按行内快照路由原 daemon）
- **THEN** 沿「入队钉镜像 tag（F13）」既有「镜像缺失 fail」语义置 failed-to-start，不误判为 daemon 不可达（该区分既有 scenario 继续成立）；缺失为默认 tag（`HUB_IMAGE_TAG_DEFAULT`）形态时，该终态后「runner 池镜像自动分发」按探针周期自动补齐目标侧镜像、重新入队即可成功——显式钉版非默认 tag 不入分发范围（沿 F13 人工语义：重新入队仍会再 fail，须人工补片）；本 scenario 的 fail 语义本身不变

#### Scenario: 共享数据面误配 fail loud（机理如实）

- **WHEN** 选中行两根未指同一数据面（如目标机无该路径或路径为空）
- **THEN** daemon 侧对缺失挂载源自动创建目录，fail 点后移至容器内 pa 运行时（PRD 读不到 / state 断链），run 置 failed 可见于队列与通知，绝不静默成功；确定性 hash 文件名 + 文件级挂载保证 stale 树不可能同名不同容（该确定性同时是 deliver_key 幂等前提，双重依赖）

#### Scenario: 禁用软下线后旧机在飞 run 按快照跑完

- **WHEN** 某机存在 running run 时该机 runner 行被禁用（软下线）
- **THEN** 在飞 run 的对账与终止按各自行内快照路由该机继续跑完（终态正常记账）；新派发不再选该机——禁用不断账、无双跑（同一 run 的容器标识只属于其快照 daemon）

#### Scenario: 投影根变更派发时即时生效（投影参数）

- **WHEN** 某 runner 行 `runner_mount_root` 被修改时 pending 队列非空
- **THEN** 存量队列项派发时按选中行新值投影挂载源（队列项路径是 hub 视角稳定坐标，投影根是派发时参数——无排空队列契约；部署前提不变：新根与 `HUB_DATA_ROOT` 仍须指同一共享存储）

#### Scenario: 远程形态下外部路径不在数据根内 fail loud

- **WHEN** 调度选中远程行且队列项 `prd_path` 不在 `HUB_DATA_ROOT` 树内（POST /queue / CLI 外部传入的路径）
- **THEN** 派发时投影换算失败 → 该项 abandon 并带明确原因（fail loud，绝不静默用 hub 视角路径挂错 daemon）

#### Scenario: DOCKER_HOST 不泄漏进 run 容器

- **WHEN** 选中行配置了 docker_host 且 run 容器启动
- **THEN** 注入只发生在 CLI 子进程 env；run 容器的 `--env` 面与现状一致，pa 容器内进程看不到 `DOCKER_HOST`

### Requirement: 服务器池与忙闲自动调度

hub SHALL 维护一张 runners 登记表（name UNIQUE / docker_host（`tcp://` 前缀）/ runner_mount_root（绝对路径）/ capacity（每机并发上限，≥1 整数，缺省 1）/ enabled），登记是低频人肉 ops（前提核实照 RUNBOOK §22 checklist，面板注册「主机 + 挂载路径」）；运行时每 tick 派发 SHALL 自动选机：候选 = enabled 且探针绿的行，选中 = 候选中「该机 running run 数 < capacity」取负载最小（平局按登记序）。全部候选红或满载时该轮 MUST 保持 pending 等待（绝不 abandon、绝不选红机白烧）；无 enabled 远程行时回落本机 daemon。登记与调度分离——面板 MUST NOT 提供绕过登记的自由 docker_host 填配派发。健康探针 SHALL 定期（N tick）+ 手动触发检测三项：daemon 可达（`docker version` 经该 runner）、镜像在场（`image_exists` 同通道）、共享存储双挂（hub 写 nonce 探针文件后经该 daemon 起一次性 `--rm` 容器以 pa-executor 自身镜像 cat 回读比对）；结果缓存于表行供面板展示，绿 = 进调度候选。env 两键（`HUB_DOCKER_HOST` / `HUB_RUNNER_MOUNT_ROOT`）SHALL 仅在首次启动且表空时 seed 一行 runner（先例 `HUB_MM_PROJECT_CHANNELS`），此后表是唯一真相、env 继续配置静默无效。

#### Scenario: 面板注册入池

- **WHEN** 管理员提交注册（name=docker-183, docker_host=tcp://172.32.153.183:2375, runner_mount_root=/mnt/nfs/pa, capacity=2）
- **THEN** 行入表（enabled=true）立即可被调度选中；name 重复或字段非法（非 `tcp://` / 非绝对路径 / capacity<1）→ 拒绝并报因

#### Scenario: 忙闲自动调度选机

- **WHEN** 派发时池内两台绿机——A 机 running 1/capacity 2、B 机 running 0/capacity 1
- **THEN** 选中 B（负载 0<1 且未满载）；若 B 也满载（running=capacity）而 A 有余量则选 A；全部满载 → 本轮不派发、item 留 pending 等下轮

#### Scenario: 探针红机不进候选

- **WHEN** 池内唯一 enabled 机的探针为红（daemon 不可达 / 镜像缺 / 回读不匹配任一）
- **THEN** 该机不进调度候选；pending item 保持等待（不 abandon、不烧失败、不回落本机——回落仅适用「无 enabled 远程行」，单机红是部署事故须人肉介入而非静默改道本机）

#### Scenario: 禁用即软下线

- **WHEN** 对某 runner 行发起禁用且该机存在 running run
- **THEN** 放行——行退出候选，在飞 run 按快照跑完（见「run 容器宿主可配」对应 scenario）；重新启用后立即回候选（下轮探针绿即参选）

#### Scenario: 表空且无 env 等价本机形态

- **WHEN** runners 表空且两 env 键未配
- **THEN** 无 enabled 远程行——派发走 hub 本机 daemon + `HUB_DATA_ROOT` 投影，行为与 add-hub-remote-runner 之前的现状 byte-identical

#### Scenario: env 两键 seed 一次性消费

- **WHEN** 首次启动且表空而 `HUB_DOCKER_HOST` 有值
- **THEN** 灌一行 seed runner（mount_root 键可选同灌；池内唯一 enabled 机 = 调度唯一候选，派发行为与 seed 前的 env 直配形态等价）；此后修改/保留 env MUST 静默无效（面板池列表标注数据来源是表）

#### Scenario: 健康探针三项与红绿灯

- **WHEN** 探针对某 runner 执行
- **THEN** 三项各自独立报绿/红（daemon 可达 / 镜像 tag 在场 / 探针文件经一次性容器回读匹配）；单项失败注明是哪项；结果与时间戳缓存表行、面板红绿灯可见；绿是进调度候选的必要条件

#### Scenario: 面板权限分层

- **WHEN** 非白名单会话访问池管理面（注册/编辑/禁用启用/删除/手动探针）
- **THEN** 拒绝（挂 `HUB_PANEL_ADMINS` 门，与建项目/重跑/放弃同面）；池列表、负载与红绿灯作为观察面对任何有效会话开放

### Requirement: runner 池镜像自动分发

hub SHALL 在健康探针对某 enabled runner 报「daemon 可达 + 镜像缺失」（image 项非 ok 且 daemon 项 ok——三项独立捕获，对断连机 MUST NOT 烧传输）时，自动把 `HUB_IMAGE_TAG_DEFAULT` 指定镜像从 hub 本机 daemon 分发到该 runner：通道 = hub 容器内 docker CLI 流式 `save | load`（`DOCKER_HOST` per-call 注入目标行——与派发/探针同通道，MUST NOT 引入 registry / SSH 等新基础设施面或新凭据）。分发 SHALL 在后台执行（MUST NOT 阻塞排度 tick），以 (runner, tag) 去重（在飞不重复、排队不重复），开工 MUST 复测目标在场（在场即 no-op——与人工修复赛跑安全）。分发成功 MUST 校验源 / 目标镜像 ID 一致（内容寻址比对）后才复测探针转绿——同步本身 MUST NOT 直写探针缓存（探针是唯一绿灯权威，复测经既有单行探针入口）。失败 / 超时 MUST 降级：终止传输、runner 保持红（不进调度候选）、队列保持 pending（绝不 abandon、绝不静默跳过、绝不为赶进度选红机）、MM 管理面告警（同一 (runner, tag) 失败组合去抖，成功后重置）；重试由下一轮探针自然重触发。分发范围 MUST 钉死为入队默认 tag（显式钉版 tag 沿 F13 既有语义），镜像源恒为 hub 本机 daemon（绝不从公网 registry 拉取顶替——本地构建镜像不在任何 registry）。

#### Scenario: 探针报缺失自动分发转绿

- **WHEN** 定期探针对 enabled runner R 报 daemon ok + 「镜像缺失: pa-executor:v0.1.35」且队列有 pending item
- **THEN** 后台对 R 起分发（源 = hub 本机 daemon 同 tag；save|load 流式经 R 登记行的 docker_host），完成且镜像 ID 比对一致后复测 R 探针 → 三项绿 → R 回调度候选；期间 R 保持红、pending item 保持等待（等待而非烧失败，绝不 abandon）

#### Scenario: 在场即 no-op（幂等）

- **WHEN** 分发开工复测时目标 daemon 已有同 tag 镜像（人工已修复或上一轮分发已完成）
- **THEN** 不执行传输，直接复测探针收敛状态（重复 load 亦无害——同 tag 内容寻址覆盖）

#### Scenario: 在飞去重

- **WHEN** 同一 (runner, tag) 传输进行中而下一轮探针仍报镜像缺失（或多来源同时触发）
- **THEN** 不起重复传输、不重复排队；本轮完成后由复测 / 下轮探针收敛，其余 runner 的分发不受影响（各自独立键）

#### Scenario: 断连机不烧传输

- **WHEN** 探针对某 runner 报 daemon 不可达（此时 image 项结论不可信）
- **THEN** 不对其起分发——分发只救「可达而缺镜像」这一种红；daemon 断连的修复是人肉 ops，下轮探针绿转后再议

#### Scenario: 传输失败或超时降级告警

- **WHEN** save|load 流超时（`HUB_IMAGE_SYNC_TIMEOUT`）或任一端失败
- **THEN** 终止两端管道、该 runner 保持红不进候选、队列 pending 不动；MM 管理面告警含 runner 名 / tag / 失败原因（同一 (runner, tag) 失败组合只告警一次，成功或原因变化后重置；stderr 恒留痕）；下一轮探针仍报缺失则自然重试

#### Scenario: 镜像 ID 不一致删目标侧

- **WHEN** load 完成后目标侧该 tag 的 image ID ≠ 源侧（内容寻址比对失败）
- **THEN** 删除目标侧该 tag（防污染派发面——探针绿后派发会真用它起容器）并按失败降级处理（告警带两侧 ID 摘要）

#### Scenario: 镜像源缺失 fail loud

- **WHEN** hub 本机 daemon 也无该 tag（分发源头缺片）
- **THEN** 分发失败并告警明示「镜像源缺失——先在 hub 本机 build 该 tag」（绝不从公网 registry 拉取顶替）

#### Scenario: 面板手动分发同径

- **WHEN** 管理员在服务器池面板对某 runner 触发手动分发
- **THEN** 与自动触发同判据同径同去重（开工复测在场即 no-op；挂 `HUB_PANEL_ADMINS` 门）；非白名单会话拒绝；同步状态与结果在池列表可见

#### Scenario: 显式钉版 tag 不入分发范围

- **WHEN** 队列项 pin 了非默认 tag 且目标 daemon 缺该片
- **THEN** 沿「入队钉镜像 tag（F13）」既有「镜像缺失 fail」语义（本 requirement 只接管 `HUB_IMAGE_TAG_DEFAULT`——与探针镜像项同 tag，触发与范围同源）

