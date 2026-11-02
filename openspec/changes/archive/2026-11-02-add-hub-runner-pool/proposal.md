## Why

add-hub-remote-runner 把 run 容器派发远程化的形态定为**单机 env 两键**（`HUB_DOCKER_HOST` + `HUB_RUNNER_MOUNT_ROOT` 指一台机）——换目标机 = 改 hub.env + 重建容器，且部署前提核实（2375 限源 / 预载镜像 / 共享存储双挂）全靠人肉 RUNBOOK checklist，共享存储断链**不报错**的故障只能靠巡检发现。GPU 机不止一台：把「登记」与「调度」分离——每台机核实一次后在面板注册（主机 + 挂载路径），运行时 hub 按**忙闲自动调度**（负载最低的绿机优先，无需重启、无需人工选机），checklist 固化成健康探针红绿灯兼调度候选门。

**依赖**：add-hub-remote-runner 落地（本 change 的投影模型与 `docker_host` CLI 路由是其延伸；两 env 键在本 change 降级为 seed）。

## What Changes

- **runners 登记表**：新表（name / docker_host（限 `tcp://`）/ runner_mount_root / enabled / capacity（每机并发上限，缺省 1）），登记动作是低频人肉 ops（前提核实走 RUNBOOK §22 checklist），经面板注册「主机 + 挂载路径」录入
- **忙闲自动调度（替代 active 单选）**：派发那一刻在 enabled 且探针绿的候选机里选**负载最低**（该机 running run 数 < capacity 中取最小，平局按池序）——无 active 无手动切换，禁用即软下线（在飞 run 按派发时快照跑完，不再派新）；全局 `max_containers` 总天花板保留（queue_engine 零改动，调度叠在其上）；**无 enabled 远程行 = 回落本机 daemon**（byte-identical 现状，本机是兜底不是并行候选——hub 宿主不掺和重活）
- **面板服务器池面**：池列表（各机 running/capacity 负载 + 健康红绿灯）+ 注册/编辑 + 禁用/启用（挂 `HUB_PANEL_ADMINS` 白名单门）；**全部候选红 → 保持 pending 等恢复，不 abandon**（自动调度不选红机——选红机即白烧失败；探针红=可见+不入选，非「阻断」语义）
- **健康探针**：定期（N tick）+ 面板手动触发，三项全 hub 侧可测——daemon 可达（`docker version` 经该 docker_host）、镜像 tag 在场（image_exists）、共享存储双挂验证（hub 写探针文件后**经该 daemon 起一次性容器 cat 回读比对**——hub 无远程 shell，容器回读是唯一通道）；绿 = 进调度候选
- **env 两键降级 seed**：`HUB_DOCKER_HOST` / `HUB_RUNNER_MOUNT_ROOT` 仅在首次启动且表空时灌一行 seed runner（先例：`HUB_MM_PROJECT_CHANNELS` seed 化，add-mm-channel-intake D2），此后 runners 表是唯一真相、env 继续配置静默无效；seed 后池内唯一 enabled 机承担全部派发，行为与 seed 前等价

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `execution-hub`: 新增「服务器池与忙闲自动调度」Requirement（登记实体 / 负载最低派发 / 禁用软下线 / 探针候选门 / env seed 化场景），容器生命周期与配置面 Requirement 的派发参数来源从 env 固化改为调度选中行

## Impact

- **代码**：`hub/db.py`（runners 表 + CRUD + 负载统计查询 + seed 迁移）、`hub/config.py`（两键语义降级 seed）、`hub/runner.py`（docker_host 从构造固化改为 per-call 参数）、`hub/lifecycle.py`（派发选机 + 快照入行）、`hub/api.py`（池列表/注册/禁启用/手动探针端点 + 面板 UI 节）、新 `hub/health_probe.py`（探针模块）
- **契约**：ADR-0009 投影模型不变（仍派发时投影），投影参数来源 env → 调度选中行；在飞 run 按派发时快照路由原机（禁用/多机下对账不断）
- **部署**：RUNBOOK §22 补「面板注册入池」路径与 seed 迁移；`hub/deploy/hub.env.example` 两键注释改 seed 语义
- **测试**：db CRUD/seed/负载查询、调度选择（最低负载/平局/红机不选/全红保持 pending）、快照路由、探针三项、面板端点权限
