## 1. 配置面（HubConfig）

- [x] 1.1 HubConfig 增 `docker_host` / `runner_mount_root` 两字段：env `HUB_DOCKER_HOST` / `HUB_RUNNER_MOUNT_ROOT` 解析，缺省 None；`docker_host` 值形态校验（None 或 `tcp://` 前缀，否则 ConfigError 列合法形态 fail fast）
- [x] 1.2 单测：缺省零变化（两字段 None）；`tcp://` 合法通过；`ssh://` / 裸主机名 → ConfigError

## 2. runner CLI env 注入（D1）

- [x] 2.1 `DockerRunner` 持有 `docker_host`（构造参数，缺省 None）；CLI 子进程 env 构造：不设时与父进程等价（不注入键），设置时恰注入一个 `DOCKER_HOST=<值>`——`image_exists` / `start` / `status` / `stop` 四调用点全覆盖
- [x] 2.2 单测（Fake CLI 捕获 env）：不设时子进程 env 无 `DOCKER_HOST`；设置时四调用点 env 均含该值且唯一；run 容器 `--env` 面不含 `DOCKER_HOST`（argv 断言）

## 3. 挂载源两范畴分离（D2）

- [x] 3.1a 派生类：lifecycle 派发时 RunSpec 三挂载源（state 写面 / profiles / sources.yaml）用投影根 `runner_mount_root or data_root` 作 `<project>` 父目录；mkdir / stamp 防撞 / run_dir 记账 / 启动必读面检查恒走 `data_root`（hub 侧文件系统操作零改动）
- [x] 3.1b 固化类（投影模型）：队列项 `prd_path` 恒存 hub 视角（四处落盘点 `mm_intake.py`×2 / `api.py`×2 零改动）；派发时 `tick_dispatch` 一处投影换算 `mount_root / prd.relative_to(data_root)`（不在 `data_root` 下 → abandon fail loud 带原因）；重跑 / POST /queue / CLI 天然兼容
- [x] 3.2 单测：不设 `runner_mount_root` 时 RunSpec 挂载源与现状 byte-identical；设置时派生类三源换投影根 + PRD 投影换算正确（hub 侧 prd 检查 / stamp / run_dir 仍走 data_root）+ 路径不在 data_root 下 abandon fail loud

## 4. 部署面与文档

- [x] 4.1 `hub/deploy/hub.env.example` 增两 env 注释样例（含共享存储前提与仅内网声明）
- [x] 4.2 RUNBOOK §22 增启用 checklist：目标机开 2375 + 防火墙限源（粗体声明绝不上公网）→ 预载同 tag 镜像 → 共享存储双挂验证 + **运行期监控项**（断链不 fail loud，stamp 防撞依赖共享数据面持续有效）→ hub.env 配置 → 重启；**变更契约**：daemon 切换/回滚以无 running run 为前提（drain 防双跑，容器 id 是 daemon 相对）、`HUB_RUNNER_MOUNT_ROOT` 变更派发时即时生效（投影参数，无排空队列契约——apply 期投影模型修正）；附回滚（确认无在飞 run → 删两 env 重启 → 远程遗留容器人工 `docker stop`）；§13「同路径 bind」注记补远程形态指针
- [x] 4.3 `bash hub/quality.sh` 全绿（370 passed + compileall + ruff 全绿，新单测并入）

## 5. 验证

- [x] 5.1 本机回归：不设两 env 起一次干跑（FakeRunner 全链：入队树内 PRD → tick_dispatch → 四路挂载源 = data_root 派生 byte-identical、prd_path 存 hub 视角、docker_host=runner_mount_root=None）——已实证
- [ ] 5.2 远程真机验证（需目标机 ops 前提就绪，可与代码合入解耦）：183 开 2375 + 预载镜像 + 共享存储双挂后 `/pa run` 一次，确认容器出现在目标机、通知正常回流（此项 ops 依赖外部，单独追踪不强阻塞归档）
