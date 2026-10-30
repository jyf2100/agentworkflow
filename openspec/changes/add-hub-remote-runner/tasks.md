## 1. 配置面（HubConfig）

- [ ] 1.1 HubConfig 增 `docker_host` / `runner_mount_root` 两字段：env `HUB_DOCKER_HOST` / `HUB_RUNNER_MOUNT_ROOT` 解析，缺省 None；`docker_host` 值形态校验（None 或 `tcp://` 前缀，否则 ConfigError 列合法形态 fail fast）
- [ ] 1.2 单测：缺省零变化（两字段 None）；`tcp://` 合法通过；`ssh://` / 裸主机名 → ConfigError

## 2. runner CLI env 注入（D1）

- [ ] 2.1 `DockerRunner` 持有 `docker_host`（构造参数，缺省 None）；CLI 子进程 env 构造：不设时与父进程等价（不注入键），设置时恰注入一个 `DOCKER_HOST=<值>`——`image_exists` / `start` / `status` / `stop` 四调用点全覆盖
- [ ] 2.2 单测（Fake CLI 捕获 env）：不设时子进程 env 无 `DOCKER_HOST`；设置时四调用点 env 均含该值且唯一；run 容器 `--env` 面不含 `DOCKER_HOST`（argv 断言）

## 3. 挂载源两范畴分离（D2）

- [ ] 3.1a 派生类：lifecycle 派生挂载源改 `runner_mount_root or data_root` 作 `<project>` 父目录（三挂载源：state 写面 / profiles / sources.yaml）；hub 侧通知面读、vault 路径全部保持 `data_root` 不动
- [ ] 3.1b 固化类：PRD 入队落盘路径（`mm_intake.py` 落盘处 + `/deliver` 面如有同构）按当时生效挂载源根落（`<mount_root|data_root>/<project>/mm-<sha8>.md`）；派发时直取队列项路径不重写
- [ ] 3.2 单测：不设 `runner_mount_root` 时挂载源 argv 与现状 byte-identical；设置时派生类三源前缀替换 + 入队落盘路径带新前缀、hub 侧通知/vault 路径不变

## 4. 部署面与文档

- [ ] 4.1 `hub/deploy/hub.env.example` 增两 env 注释样例（含共享存储前提与仅内网声明）
- [ ] 4.2 RUNBOOK/hub 部署说明增启用 checklist：目标机开 2375 + 防火墙限源（粗体声明绝不上公网）→ 预载同 tag 镜像 → 共享存储双挂验证 + **运行期监控项**（断链不 fail loud，stamp 防撞依赖共享数据面持续有效）→ hub.env 配置 → 重启；**变更契约**：daemon 切换/回滚以无 running run 为前提（drain 防双跑，容器 id 是 daemon 相对）、`HUB_RUNNER_MOUNT_ROOT` 变更排空 pending 队列后生效；附回滚（确认无在飞 run → 删两 env 重启 → 远程遗留容器人工 `docker stop`）
- [ ] 4.3 `bash hub/quality.sh` 全绿（新单测并入）

## 5. 验证

- [ ] 5.1 本机回归：不设两 env 起一次干跑（Fake / 真镜像 dry-run 路径），确认派发参数与现状一致
- [ ] 5.2 远程真机验证（需目标机 ops 前提就绪，可与代码合入解耦）：183 开 2375 + 预载镜像 + 共享存储双挂后 `/pa run` 一次，确认容器出现在目标机、通知正常回流（此项 ops 依赖外部，单独追踪不强阻塞归档）
