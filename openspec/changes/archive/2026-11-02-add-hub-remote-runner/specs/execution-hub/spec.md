## ADDED Requirements

### Requirement: run 容器宿主可配（远程 daemon + 挂载源分离）

hub SHALL 允许经两个配置项把 run 容器派发到远程 docker daemon：`HUB_DOCKER_HOST`（docker CLI 子进程注入 `DOCKER_HOST`，值形态限 `tcp://` 前缀，第一期限明文内网 transport）与 `HUB_RUNNER_MOUNT_ROOT`（run 容器 bind mount 挂载源根，与 hub 侧读写根 `HUB_DATA_ROOT` 分离）。挂载源分两范畴（投影模型）：**派生类**（state 写面 / profiles / sources.yaml——派发时从投影根现算）与**固化类**（PRD input——队列项恒存 hub 视角路径，派发时投影为挂载视角交付 docker）。两项缺省（不设）时全部行为 MUST 与现状 byte-identical。容器署名、重派、崩溃对账、F10 写面钉死语义在远程形态下 MUST 不变。

部署前提（hub 不在代码层闭合、配置期错误 MUST fail-loud 可见而非静默）：`HUB_RUNNER_MOUNT_ROOT`（daemon 侧路径）与 `HUB_DATA_ROOT`（hub 侧路径）须指同一共享数据面，且该前提**运行期持续有效**（断链属部署事故，stamp 防撞依赖它）；pa-executor 镜像须在目标 daemon 预载（验证时点 = 派发时 `DockerRunner.start()` 首步复查，检查本身路由到目标 daemon）。

#### Scenario: 不设新 env 零变化

- **WHEN** `HUB_DOCKER_HOST` 与 `HUB_RUNNER_MOUNT_ROOT` 均未设置
- **THEN** runner CLI 子进程 env 不注入 `DOCKER_HOST`（与父进程等价），四个挂载源父目录 = `HUB_DATA_ROOT`，全部派发参数与现状 byte-identical

#### Scenario: 配置远程 daemon 后 CLI 调用路由

- **WHEN** `HUB_DOCKER_HOST=tcp://172.32.153.183:2375` 且一个 pending run 被派发
- **THEN** runner 的 image_exists / start / status / stop 全部 CLI 子进程 env 含 `DOCKER_HOST=<该值>`，容器创建在目标 daemon 上；容器命名、卷署名 meta、非零退出重派语义与既有 requirement 完全一致

#### Scenario: 非法 transport 形态 fail fast

- **WHEN** `HUB_DOCKER_HOST` 设为非 `tcp://` 前缀的值（如 `ssh://...` 或裸主机名）
- **THEN** hub 启动即抛 ConfigError 拒绝起服（列出合法形态），不进入半配置运行

#### Scenario: 挂载源前缀分离（两范畴·投影）

- **WHEN** `HUB_RUNNER_MOUNT_ROOT=/mnt/nfs/pa` 而 `HUB_DATA_ROOT=/data/pa`
- **THEN** 派生类三挂载源（state 写面 / profiles / sources.yaml）派发时以 `/mnt/nfs/pa/<project>/...` 现算；固化类（PRD input）队列项仍存 `/data/pa/<project>/mm-<sha8>.md`（hub 视角），派发时投影为 `/mnt/nfs/pa/<project>/mm-<sha8>.md` 交付 docker `-v`；hub 侧投递落盘、通知面读工件、vault 读写、队列项路径、stamp 防撞与 run 目录记账全部走 `/data/pa/<project>/...`

#### Scenario: 目标 daemon 镜像缺失沿既有 fail 语义

- **WHEN** 目标 daemon 上不存在入队钉死的镜像 tag（含 daemon 变更后存量队列项派发场景——派发时 `start()` 首步对新 daemon 复查）
- **THEN** 沿「入队钉镜像 tag（F13）」既有「镜像缺失 fail」语义置 failed-to-start，不误判为 daemon 不可达（该区分既有 scenario 继续成立）

#### Scenario: 共享数据面误配 fail loud（机理如实）

- **WHEN** 两根未指同一数据面（如目标机无该路径或路径为空）
- **THEN** daemon 侧对缺失挂载源自动创建目录，fail 点后移至容器内 pa 运行时（PRD 读不到 / state 断链），run 置 failed 可见于队列与通知，绝不静默成功；确定性 hash 文件名 + 文件级挂载保证 stale 树不可能同名不同容（该确定性同时是 deliver_key 幂等前提，双重依赖）

#### Scenario: daemon 切换/回滚以无在飞 run 为前提

- **WHEN** `HUB_DOCKER_HOST` 变更或回滚（含删除配置回本机 socket）时存在 running run
- **THEN** 视为违规操作——容器标识（container id）是 daemon 相对的，切换后 status 查无 → 孤儿判定 → 重派将导致同一 run 双机双跑；RUNBOOK 变更流程 MUST 先确认无 running run（drain）

#### Scenario: 挂载源根变更派发时即时生效（投影参数）

- **WHEN** `HUB_RUNNER_MOUNT_ROOT` 变更时 pending 队列非空
- **THEN** 存量队列项派发时按新根投影挂载源（队列项路径是 hub 视角稳定坐标，挂载根是派发时投影参数——无排空队列契约；部署前提不变：新根与 `HUB_DATA_ROOT` 仍须指同一共享存储）

#### Scenario: 远程形态下外部路径不在数据根内 fail loud

- **WHEN** 配置了 `HUB_RUNNER_MOUNT_ROOT` 且队列项 `prd_path` 不在 `HUB_DATA_ROOT` 树内（POST /queue / CLI 外部传入的路径）
- **THEN** 派发时投影换算失败 → 该项 abandon 并带明确原因（fail loud，绝不静默用 hub 视角路径挂错 daemon）

#### Scenario: DOCKER_HOST 不泄漏进 run 容器

- **WHEN** 配置了 `HUB_DOCKER_HOST` 且 run 容器启动
- **THEN** 注入只发生在 CLI 子进程 env；run 容器的 `--env` 面与现状一致，pa 容器内进程看不到 `DOCKER_HOST`
