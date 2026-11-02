# Proposal: add-hub-remote-runner

## Why

hub 目前把 run 容器派发到**hub 进程所在宿主机**的 docker daemon（经 `/var/run/docker.sock`）。团队需要把 run 容器跑在**另一台机器**上——典型场景：GPU 机（如 172.32.153.183，带 nvidia runtime）跑需要算力的 run，或把执行负载从 hub 宿主机剥离。`hub/runner.py` 全走 docker CLI（subprocess），docker CLI 原生支持 `DOCKER_HOST` env 路由远程 daemon，但 hub 今天没有任何配置面暴露它，且远程化后有三个断点（bind mount 在 daemon 侧解析、hub 本机读工件的数据链、镜像须在目标机存在）需要显式设计，不能只透传一个 env 了事。

## What Changes

- **run 容器宿主可配**：新配置 `HUB_DOCKER_HOST`——设置后 runner 的全部 docker CLI 调用（image_exists / start / status / stop）路由到该 daemon；不设 = 本机 socket，现状 byte-identical 零变化。
- **bind mount 挂载源可配（两范畴·投影模型）**：新配置 `HUB_RUNNER_MOUNT_ROOT`——挂载源（daemon 侧解析）与 hub 侧 `HUB_DATA_ROOT`（读工件、通知面、面板）分离；不设 = `HUB_DATA_ROOT`，现状 byte-identical 零变化。挂载视角是**派发时投影参数**：**派生类**（state 写面 / profiles / sources.yaml——派发时从投影根现算）与**固化类**（PRD input——队列项恒存 hub 视角路径，派发时一处投影换算成挂载视角；hub 侧消费与 POST /queue / CLI / 重跑通道零改动，apply 期修正原「入队固化 daemon 视角」案——该案与 `prd_path` 的 hub 侧 5 处消费及三外部通道冲突）。`HUB_RUNNER_MOUNT_ROOT` 变更即时生效、无排空队列契约。
- **共享存储假设显式声明**：`HUB_RUNNER_MOUNT_ROOT` 与 `HUB_DATA_ROOT` 必须指**同一数据面**（NFS / 同盘双挂载，即两台机各自路径名下的同一份数据）。run 容器在远程 daemon 写 state，hub 在本机路径读 pipeline_outcome / 报告——数据链靠部署层共享存储闭合，hub 代码不新增远程读通道。
- **镜像分发约束声明**：pa-executor 镜像须在目标 daemon 上存在。验证时点 = **派发时**（`DockerRunner.start()` 首步 `image_exists` 复查，`runner.py:167`）且检查本身路由到目标 daemon——daemon 配置变更后存量队列项派发时对新 daemon 自动复验，时间窗自愈；缺失沿 F13 既有「镜像缺失 fail」语义走 failed-to-start，不误判 daemon 不可达。
- **transport 第一期限 tcp://**：`DOCKER_HOST` 值限定 `tcp://host:port` 形态（2375 明文，仅内网，部署侧须防火墙限源）。`ssh://` 形态明确 out of scope（hub 容器内无 ssh 客户端，且需私钥注入面，留后续 change）。
- hub.env.example 增补两个新 env 的注释样例。

**不改**：容器署名 / 重派 / 崩溃对账语义、队列排度、通知面、F10 写面钉死——全部不变。

## Capabilities

### New Capabilities

（无——本变更是 execution-hub 既有容器派发能力的宿主位置扩展）

### Modified Capabilities

- `execution-hub`：容器生命周期 requirement 增「run 容器宿主可配」——不设新 env 时全部行为 byte-identical；设置后 CLI 调用路由远程 daemon + 挂载源前缀分离 + 共享存储/镜像存在两个部署前提显式化（fail 语义沿用既有 scenario）。

## Impact

- **代码**：`hub/config.py`（HubConfig 增 `docker_host` / `runner_mount_root` 两字段 + env 解析）、`hub/runner.py`（CLI 子进程 env 注入 DOCKER_HOST；挂载源改由 lifecycle 传入的显式路径构造）、`hub/lifecycle.py`（派发时 RunSpec 挂载源四路投影到 `runner_mount_root or data_root`——派生类三源现算 + 固化类 PRD 一处 `relative_to` 换算；mkdir / stamp 防撞 / run_dir 记账 / 启动必读面检查恒走 `data_root`）。
- **部署**：`hub/deploy/hub.env.example` 两个新 env 注释样例；目标 daemon 侧 ops 前提（开 2375 + 防火墙限源 + 镜像预载 + 共享存储双挂）写入 RUNBOOK/部署说明。
- **测试**：runner 单测覆盖 env 注入路径与挂载源前缀替换；不设 env 的回归断言现状参数 byte-identical。
- **依赖**：零新依赖（docker CLI 原生能力）。
