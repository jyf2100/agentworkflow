## MODIFIED Requirements

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

## ADDED Requirements

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
