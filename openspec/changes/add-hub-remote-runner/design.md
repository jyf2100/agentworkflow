# Design: add-hub-remote-runner

## Context

`hub/runner.py` 的 `DockerRunner` 全部经 docker CLI（`subprocess.run`，未显式传 env → 继承 hub 进程 env）操作容器：`image_exists` / `start` / `status` / `stop`。四个 bind mount 挂载源在 `hub/lifecycle.py` 从 `cfg.data_root / <project>/...` 派生：state 写面、profiles 只读、sources.yaml 单文件、PRD input 只读。hub 通知面 / 面板 / 投递 API 也读本机 `data_root`。今天 hub 容器挂宿主 `/var/run/docker.sock`，一切发生在 hub 宿主机。

docker CLI 原生支持 `DOCKER_HOST` env 把调用路由到远程 daemon，但远程化有三个断点必须在设计层回答：bind mount 源路径在 **daemon 侧**解析、hub 本机读工件的数据链、镜像须在目标 daemon 上存在。另：目标机 183 的 dockerd 由 kube 面管理（`ExecStart=/apphome/kube/bin/dockerd`），2375 未开，开法属目标机 ops。

## 术语（本 change 锐化，防多义漂移）

| 术语 | 本 change 内的专指 | 防混 |
|---|---|---|
| **daemon 机** | 目标 docker daemon 所在机器（即「run 容器宿主」） | 勿与 hub 机（hub 进程所在机）混——既有代码注释里 "host 侧" 一律指 hub 机 |
| **挂载源·派生类** | state 写面 / profiles / sources.yaml——**派发时**从配置根现算 | 跟 `HUB_RUNNER_MOUNT_ROOT` 即时走 |
| **挂载源·固化类** | PRD input——**入队时**落盘绝对路径固化进队列项（`item["prd_path"]`） | 不跟配置即时走，见 D2 变更契约 |
| **daemon 侧路径** | `-v` 挂载源——在 daemon 机上解析 | `--env-file` 是 CLI 侧读（hub 机 tempfile），路径语义相反 |
| **共享数据面** | 两配置根（挂载源根 / data_root）双挂的同一存储——**部署期断言 + 运行期持续有效前提**（非运行时保证，见 D4） | 不是 hub 可验证的不变式 |

（"runner" 一词在仓库中已多义：`runner.py` 模块 / `DockerRunner` 类 / spec 的 ContainerRunner 接缝 / 本 change 名。不另造词，依赖上下文限定。）

## Goals / Non-Goals

**Goals:**
- hub 可配置 run 容器跑在远程 docker daemon（第一期全局单值，per-hub）。
- bind mount 挂载源路径可配置，与 hub 侧读路径（`HUB_DATA_ROOT`）分离。
- 两项配置不设时全部行为 byte-identical（零回归面）。
- 三个断点（数据链 / 镜像 / transport）显式决策，不隐式假设。

**Non-Goals:**
- per-project / per-run 宿主路由（全局单值先行；`RunSpec` 已是 per-run 对象，未来加字段平滑）。
- `ssh://` transport（hub 容器无 ssh 客户端 + 私钥注入面，留后续 change）。
- 自动 `docker pull` / registry 集成（镜像预载是部署 ops 活）。
- hub 经 docker API / `docker cp` 远程读回工件（共享存储假设替代，见 D4）。

## Decisions

### D1: `HUB_DOCKER_HOST` 显式注入，非裸 `DOCKER_HOST` env 继承

- **选**：HubConfig 新字段 `docker_host`（env `HUB_DOCKER_HOST`，缺省 None）；`DockerRunner` CLI 子进程 env 显式注入 `DOCKER_HOST=<值>`（仅当配置非 None）。值形态校验：None 或 `tcp://` 前缀，否则 ConfigError fail fast。
- **弃**：hub.env 直接设 `DOCKER_HOST` 让子进程自然继承——零代码但隐式、全局污染 hub 进程 env、无形态校验、与 hub.env 的 `HUB_*` 语系不一致。
- **注意**：注入只发生在 CLI 子进程 env；run 容器的 `--env` 面不含它，pa 容器内进程看不到 DOCKER_HOST。

### D2: 挂载源两范畴——派生类换单前缀 `HUB_RUNNER_MOUNT_ROOT`，固化类靠变更生效契约

代码现实（本体审核 O-2）：四个挂载源不同构——

| 挂载源 | 范畴 | 现实代码 |
|---|---|---|
| state 写面 / profiles / sources.yaml | **派生类**（派发时现算） | `lifecycle.py` `cfg.data_root / <project>/...` |
| PRD input | **固化类**（入队时绝对路径进 SQLite） | `mm_intake.py:266` 落盘 → `str(target)` 入 `prd_path` 列 |

- **选**：HubConfig 新字段 `runner_mount_root`（env `HUB_RUNNER_MOUNT_ROOT`，缺省 None）。派生类三个在 lifecycle 用 `runner_mount_root or data_root` 作父目录；固化类在**入队落盘时**就按当时生效的挂载根落（PRD 内容由 hub 写，落盘路径 = 挂载视角路径），派发时直取队列项路径、不重写。
- **弃**：派发时对固化路径做前缀重写——字符串前缀替换脆弱（路径非纯前缀派生时错位），且掩盖「入队与派发间配置变更」的语义；四个挂载点各配一个 env——YAGNI。
- **变更生效契约（必须显式）**：`HUB_RUNNER_MOUNT_ROOT` 变更 = **排空 pending 队列后生效**——存量队列项固化的是旧前缀路径，跨变更派发必挂错。`HUB_DOCKER_HOST` 变更无此约束（路径不随 daemon 变），但受 D-回滚前提约束（见 Migration）。
- **hub 侧读写不变**：投递、通知面、vault 继续走 `data_root`。共享数据面（D4）下两者是同一存储。

### D3: 镜像不自动拉取——预载声明 + 既有 fail 语义

- **选**：镜像须在目标 daemon 预载（ops：`docker save|load` / 目标机 build / registry 手动 pull）。`image_exists` 本就路由到同一 daemon（inspect 走 DOCKER_HOST），查的就是目标机，语义自然正确；缺失走 F13 既有「镜像缺失 fail」→ failed-to-start，与「daemon 不可达」区分（既有 scenario）。
- **验证时点声明（本体审核 O-5）**：镜像存在性在**派发时**复查——`DockerRunner.start()` 首步 `image_exists(spec.image_tag)`（`runner.py:167` 现状，无需新代码）。推论：`HUB_DOCKER_HOST` 变更后，存量队列项派发时对新 daemon 自动复验，入队-派发时间窗**自愈**（挂载路径无此等价物，故 D2 需排空契约）。
- **弃**：起容器前自动 pull——引入 registry 依赖，且 `latest` 漂移 undermines F13 钉死 tag 的内容确定性。

### D4: 数据链闭合靠共享数据面前提，非远程读回——部署期断言 + 运行期持续有效

- **选**：显式部署前提——`HUB_RUNNER_MOUNT_ROOT`（daemon 侧路径）与 `HUB_DATA_ROOT`（hub 侧路径）指**同一数据面**（NFS / 同盘双挂载）。run 容器在远程机写 state，hub 在本机路径读，链路由存储层闭合。
- **弃**：hub 经 docker API / `docker cp` 从远程读回工件——通知面读路径遍布 `data_root`（pipeline_outcome / 报告落卷 / dispatch record），逐点改造面大，第一期不做。
- **前提的双重时间性（本体审核 O-1）**：共享数据面在**配置时**要对，且在**运行期持续有效**——前提不是一次性断言。运行中断链（NFS 掉挂/只读降级）不属「误配」范畴且**不 fail loud**：`allocate_fresh_stamp(state_root)` 的 stamp 防撞依赖 hub 侧能看见 daemon 侧新写入的 run 目录，断链时可能分配撞 stamp → 两个 run 同 `run_dir` 互踩 state——静默数据竞争。RUNBOOK 部署 checklist 须含共享存储监控项。
- **fail 机理如实声明（本体审核 O-3）**：误配（无共享存储）时 dockerd 对缺失挂载源**自动创建目录**（文件级挂载也建成目录），fail 点**后移**到 pa 运行时读不到 PRD/state 断链——run failed 仍可见，但不是「容器启动失败」。真正的防 drift 防线 = **确定性 hash 文件名（`mm-<sha8>.md`）+ 文件级挂载**：内容变名必变，daemon 侧 stale 树不可能存在同名不同容文件。⚠️ 该确定性今天是 `deliver_key` 幂等的刻意设计（`mm_intake.py:14` 注释「勿顺手统一加日期/uuid」）——本 change 将它升级为**双重依赖**（幂等 + 远程完整性），任何文件名形态改动须同时审这两面。

### D5: transport 第一期限 `tcp://`（2375）

- tcp:// 无 TLS 无认证——**仅内网** + 部署侧防火墙限源（如仅放行 hub 宿主机 IP），写进 RUNBOOK 部署 checklist。2376（TLS）需要证书分发面，out of scope。
- TLS 相关 env（`DOCKER_TLS_VERIFY` / `DOCKER_CERT_PATH`）**不透传不声明支持**：runner 子进程只注入 `DOCKER_HOST` 一个键；若 daemon 配了 TLS，CLI 将连接失败报错可见（fail fast 可诊断）。
- 目标机开 2375 属 ops 前提（183 的 dockerd 由 kube 面管理，daemon.json `hosts` vs systemd override 届时定），不在本 change 代码面内。

## Risks / Trade-offs

- [无共享存储误配：挂载源缺失] → dockerd 自动建目录使 fail 点后移至 pa 运行时（读不到 PRD / state 断链）→ run failed 仍可见（O-3 机理，非「启动失败」）；hash 文件名 + 文件级挂载防 stale 同名。RUNBOOK 部署 checklist 逐项核对（2375 + 防火墙 + 镜像预载 + 双挂验证）。
- [共享存储**运行中**断链（NFS 掉挂/降级）] → 不 fail loud：stamp 防撞失效可能撞 `run_dir` → 双 run 互踩 state 的静默竞争（O-1）→ 共享存储监控列入部署前提；断链恢复后核对最近 run 的 state 一致性。
- [daemon 切换/回滚时在飞 run 双跑] → 容器标识 = container id，daemon 相对——切回本机后 id 查无 → 孤儿判定 → 重派，远程那份在新视野外继续跑（烧钱/可能开重复 PR）（O-4）→ **切换/回滚硬前提 = 无 running run**（见 Migration），RUNBOOK 粗体。
- [挂载根变更穿越队列：存量项固化旧前缀路径] → D2 变更生效契约：排空 pending 后再变更；hub 可在启动时对存量项与当前前缀做一致性告警（观测面，不阻断）。
- [2375 无认证 = 连上即目标机 root] → 仅内网 + 防火墙限源为硬前提，RUNBOOK 用粗体声明绝不可暴露公网。
- [目标机镜像缺失] → `image_exists` fail 语义既有（F13），failed-to-start 可见，重派止损既有。
- [CLI 子进程 env 与父进程漂移] → runner 单测断言：不设 `docker_host` 时子进程 env 与父进程等价（不注入键）；设置时恰注入一个 `DOCKER_HOST`。
- [全局单值限制] → 想混布（某项目 GPU 机、其余本机）时需 per-project 路由——已列 Non-Goal，`RunSpec` per-run 对象留了扩展位。

## Migration Plan

1. 合入后默认零变化（两 env 不设 = 现状）。
2. 启用 checklist（RUNBOOK 同步）：目标机开 2375 + 防火墙限源 → 预载 pa-executor 镜像（同 `HUB_IMAGE_TAG_DEFAULT`）→ 共享存储双挂（daemon 侧路径 = `HUB_RUNNER_MOUNT_ROOT`，hub 侧 = `HUB_DATA_ROOT`）→ `hub.env` 配 `HUB_DOCKER_HOST` + `HUB_RUNNER_MOUNT_ROOT` → `docker compose up -d` 重启 hub。
3. 验证：`/pa run` 一次真机（run 容器 `docker ps` 出现在目标机而非 hub 宿主机；通知正常回流）。
4. **切换 / 回滚硬前提：无 running run**（drain——等在飞 run 终态或确认放弃后才变更 `HUB_DOCKER_HOST`，本体审核 O-4：容器 id 是 daemon 相对的，切换后 status 在新 daemon 查无 → 孤儿判定 → 重派 → 远程那份在新视野外双跑）。
5. 回滚：确认无在飞 run → 删两 env 重启 hub（回本机 socket 形态；队列与 run 记录不受影响；远程遗留容器若有人工放弃的，`docker stop` 手工清理）。
6. `HUB_RUNNER_MOUNT_ROOT` 单独变更（不切 daemon）：排空 pending 队列后再变（D2 固化类契约）；`HUB_DOCKER_HOST` 单独变更：无在飞 run（第 4 条），存量 pending 项派发时对新 daemon 复验镜像（D3 时间窗自愈）。

## Open Questions

- per-project 宿主路由的真实需求何时出现？（第一期全局单值，出现时再起 change。）
- 183 dockerd 开 2375 的机制（kube 面管理下 daemon.json vs systemd override 孰可行）——目标机 ops 时定，不阻塞本 change。
