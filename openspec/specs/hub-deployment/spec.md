# hub-deployment

## Purpose

hub 的 docker compose 部署形态与运维口径。

## Requirements

### Requirement: compose 单常驻服务
hub SHALL 以 docker compose 部署：唯一常驻 service 为 pa-hub（build context = vault `hub/`，thin 镜像：Python + API/daemon 依赖 + docker CLI）。pa-hub MUST 挂载 `/var/run/docker.sock`（起兄弟执行容器）与 data 卷根；socket 属主 SHALL 对齐：宿主 socket 通常 `root:docker 0660`，容器用户 uid=1001 MUST 经 compose `group_add` 注入宿主 docker 组 gid（可配，如 `HUB_DOCKER_GID`）获得连接权，未对齐时连接报 permission denied 而非静默降级（换机部署核宿主 gid）。执行容器 MUST NOT 出现在 compose service 定义中（每 run 动态挂载/env-file/stamp 由 hub 经 docker run 生成，静态 service 表达不了；执行镜像存在性由 F13 手动 build + hub image_exists fail 暴露保证）。

#### Scenario: 一条命令起服务
- **WHEN** 部署机执行 `docker compose up -d --build`
- **THEN** pa-hub 单常驻服务就绪（API + 排度循环同进程），执行镜像缺失时首个派发 fail 暴露而非静默

#### Scenario: socket 属主对齐补组
- **WHEN** 宿主 `/var/run/docker.sock` 为 `root:docker 0660` 而 pa-hub 以 uid=1001 运行
- **THEN** compose `group_add` 注入宿主 docker 组 gid 后 hub 可连 daemon 起兄弟容器；缺补组时连接报 permission denied（部署配置问题暴露，非镜像/网络故障）

#### Scenario: 兄弟容器而非嵌套
- **WHEN** hub 派发执行容器
- **THEN** 容器经宿主 docker daemon 兄弟启动（共享 socket），无 docker-in-docker 嵌套层

### Requirement: 卷属主对齐（uid=1001）
部署 SHALL 保证 data 卷对执行镜像运行用户 `pa(uid=1001)` 可写：pa-hub 容器以 `user: "1001:1001"` 运行，宿主 data_root 部署时一次性 `chown -R 1001:1001`。MUST NOT 以 world-writable（777）方式对齐。保险箱文件属主随此对齐天然仅 hub 可读写。

#### Scenario: 执行容器可写 state 卷
- **WHEN** 执行容器以镜像默认用户（pa, uid=1001）写挂载的 state 卷
- **THEN** 写入成功（属主对齐），无 PermissionError

#### Scenario: 属主不对齐时部署检查暴露
- **WHEN** data 卷属主非 1001 且 hub 尝试派发
- **THEN** 首个写入点明确报错（权限拒绝信息可见），不以放宽权限方式绕过

### Requirement: 常驻服务生命周期
pa-hub service SHALL 配置 `restart: unless-stopped`：崩溃自动拉起，拉起后先跑重启对账（幂等归档/续跟踪）再进排度循环。hub 镜像版本 SHALL 随 vault 代码演进由部署机本地 build（F8 基调；ghcr.io 分发留后续）。执行镜像版本钉定沿用 F13 入队钉 tag 契约（部署机显式 tag 预 build + hub `image_exists` 检查 fail 暴露），不在 compose 层表达。

#### Scenario: 崩溃自愈
- **WHEN** pa-hub 进程异常退出
- **THEN** restart 策略拉起新进程，reconcile_startup 幂等对账（在跑容器续跟踪、孤儿 meta 归档），无重复派发

#### Scenario: 部署文档可复核
- **WHEN** 运维者按 RUNBOOK 部署节操作
- **THEN** vault-import、chown、compose up 三步可从零复现运行态（顺序不可倒：import 时 data_root 尚属宿主用户，chown 后宿主写不进），且回退路径明确（compose down 后切片 1 手工形态仍可用）
