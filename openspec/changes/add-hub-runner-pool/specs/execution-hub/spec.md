## MODIFIED Requirements

### Requirement: run 容器宿主可配（远程 daemon + 挂载源分离）

hub SHALL 允许把 run 容器派发到远程 docker daemon，派发目标来自**服务器池忙闲自动调度**（`add-hub-runner-pool` 起；原 `HUB_DOCKER_HOST` / `HUB_RUNNER_MOUNT_ROOT` 两 env 键降级为首次启动 seed，见「服务器池与忙闲自动调度」）：docker CLI 子进程注入 `DOCKER_HOST=<选中行 docker_host>`（值形态限 `tcp://` 前缀，第一期限明文内网 transport），挂载源投影根 = `<选中行 runner_mount_root>`（与 hub 侧读写根 `HUB_DATA_ROOT` 分离）。挂载源分两范畴（投影模型不变）：**派生类**（state 写面 / profiles / sources.yaml——派发时从投影根现算）与**固化类**（PRD input——队列项恒存 hub 视角路径，派发时投影为挂载视角交付 docker）。无 enabled 远程行（表空/全禁用且 seed 未发生）时全部行为 MUST 与现状 byte-identical（回落本机 daemon）。容器署名、重派、崩溃对账、F10 写面钉死语义在远程形态下 MUST 不变。

**per-run 快照路由**：派发时选中行的 runner 名与 docker_host MUST 快照进 run 记录；此后 runner 行修改或禁用 MUST NOT 影响在飞 run 的 status / stop 路由（tick 对账按行内快照查对应 daemon）——多机并跑与禁用软下线的记账正确性由此保证。部署前提（hub 不在代码层闭合、配置期错误 MUST fail-loud 可见而非静默）：选中行 `runner_mount_root`（daemon 侧路径）与 `HUB_DATA_ROOT`（hub 侧路径）须指同一共享数据面，且该前提**运行期持续有效**（断链属部署事故，stamp 防撞依赖它；健康探针提供可见性）；pa-executor 镜像须在目标 daemon 预载（验证时点 = 派发时 `DockerRunner.start()` 首步复查，检查本身路由到目标 daemon）。

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
- **THEN** 沿「入队钉镜像 tag（F13）」既有「镜像缺失 fail」语义置 failed-to-start，不误判为 daemon 不可达（该区分既有 scenario 继续成立）

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

## ADDED Requirements

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
