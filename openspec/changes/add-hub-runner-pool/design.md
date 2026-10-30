## Context

add-hub-remote-runner 落地了远程 runner 单机形态：`HUB_DOCKER_HOST` + `HUB_RUNNER_MOUNT_ROOT` 两 env 键在 hub 启动时读入 `HubConfig`，`DockerRunner` 构造时固化 `docker_host`，lifecycle 派发时按投影模型换算挂载源。局限：一台机、换机须改 env + 重建容器、部署前提核实全人肉、共享存储断链不报错。本 change 在其上引入**服务器池 + 忙闲自动调度**（apply 前范围扩容：起案时为 active 单选 + 手动切换，用户定稿为面板注册 + 自动调度——切换/active 概念整体移除）：多台机面板注册入表，每 tick 派发时自动选负载最低的绿机；探针把 RUNBOOK §22 checklist 固化成代码兼调度候选门。前提不变式沿袭 ADR-0009：挂载视角 = 派发时投影参数；hub 侧文件系统操作恒走 `data_root`。

## Goals / Non-Goals

**Goals:**
- 多台 GPU 机面板注册（主机 + 挂载路径），运行时忙闲自动调度——无人选机、不重启 hub
- 部署前提（daemon 可达 / 镜像在场 / 共享存储双挂）从人肉 checklist 变探针红绿灯，兼调度候选门
- env 两键降级 seed，消灭「换机要 ssh 改文件重建容器」路径
- 在飞 run 的记账与调度解耦（禁用/多机不断账）

**Non-Goals:**
- **高级调度器特性**：不做亲和性（project↔机绑定）、抢占迁移、优先级队列、跨机负载预估——只做「running 数 / capacity 最低者胜」的机械忙闲判据；派发即绑定（run 不迁移机）
- 探针失败的自动修复/自动禁用（红 = 不入选 + 可见；机器修复是人肉 ops 后手动或下轮探针转绿）
- TLS（2376）/ 非 tcp:// 形态、探针自动化部署前提（开 2375/防火墙/双挂本身仍是人肉 ops）

## Decisions

### D1 登记与调度分离
登记 = 低频人肉 ops（照 RUNBOOK §22 checklist 核实后面板录入），调度 = 高频机械动作（每 tick 派发时自动选机）。**拒绝**面板自由填 docker_host 直配派发：自由文本让配置脱离部署前提独立漂移，且 env/库两真相源冲突无解。

### D2 忙闲自动调度 + per-run 快照（起案 active 单选的范围扩容）
- **候选集**：enabled 且探针绿（probe ok）的行；**选择**：候选中「该机 running run 数 < capacity」里取负载最小（平局按池登记序）——负载 = `runs` 表按 `runner_name` 快照列统计 status='running'，纯 SQL 机械事实。
- **无 enabled 远程行 = 回落本机 daemon**（byte-identical 现状）。本机是兜底不是并行候选——hub 宿主不掺和重活，避免「本机容量」新概念；**全部候选红或满载 = 不派发**（item 留 pending 等下轮，不 abandon——等待是安全行为，烧失败不是）。
- 全局 `max_containers` 总天花板保留不动（queue_engine 零改动）——调度叠在其上，per-runner capacity 只做机内细分。
- **派发参数快照进 run 行**：runs 表增 `runner_name` / `runner_docker_host`（快照而非外键引用）——此后 runner 行被改、被禁用，**不影响在飞 run 的 status/stop 路由**（tick reconcile 按行内快照查对应 daemon）。多机并跑的对账正确性由快照直接保证（本 change 把起案时的 active 单选扩容为自动调度，快照设计原样承载）。
- `DockerRunner` 的 `docker_host` 从构造固化改为 **per-call 参数**；CLI 子进程 env 注入语义不变（add-hub-remote-runner D1）。
- **禁用即软下线**：disabled 行不进候选；其上在飞 run 按快照跑完——无切换确认、无 drain 概念（起案的 D3 切换门随 active 概念一并消失）。

### D4 健康探针：三项一通道 + 调度候选门
- **daemon 可达**：`docker version` 经该 runner 的 docker_host。
- **镜像在场**：`image_exists(image_tag_default)` 经同通道。
- **共享存储双挂**：hub 在 `data_root` 下写探针文件（内容=随机 nonce），**经该 daemon 起一次性容器** `--rm -v <mount_root>/probe:/probe <image_tag_default> cat /probe` 回读比对。hub 无远程 shell，容器回读是唯一通道；**用 pa-executor 自身镜像**而非 alpine（免额外预载要求，cat 是基础命令任何镜像都有）。此项同时验证 daemon 可达 + 挂载视角换算正确 + 存储链路闭合三件事。
- 调度：定期（N tick，复用 stale_check 的 tick 计数模式）+ 面板手动触发；结果（绿/红 + 各项明细 + 时间戳）入 runners 表行内缓存，面板读缓存。
- **语义修正（对起案）**：起案定「红不阻断派发」针对手动 active（人拍板担责）；自动调度下调度器自己选机，**选红机 = 白烧失败**——红 = 不进候选。与「全部候选红 → 保持 pending 不 abandon」配合，等待而非烧错。

### D5 env 两键降级 seed（先例：`HUB_MM_PROJECT_CHANNELS`）
仅首次启动且 runners 表空时，`HUB_DOCKER_HOST` 有值 → 灌一行 seed runner（mount_root 键可选同灌）；此后表是唯一真相，env 继续配置**静默无效**（面板行内注记提示）。seed 后池内唯一 enabled 机承担全部派发（调度下唯一候选 = 必然选中），行为与 seed 前等价。`tcp://` 限形校验从 `load_config` 移到**登记/seed 入口**（env 语义变了：值不合形 = 拒灌 seed + 启动报错，地址非凭据可入消息）。

### D6 登记行约束
- 登记必填 name（UNIQUE，`[a-z0-9][a-z0-9_-]*` 同 project 判据）/ docker_host（`tcp://` 前缀）/ runner_mount_root（绝对路径）/ capacity（≥1 整数，缺省 1）；enabled 缺省 true。
- 禁用/删除对任何行放行（软下线语义，在飞 run 快照解耦）；删除仅建议先禁用观察一轮（RUNBOOK 注记，非硬门）。

## Risks / Trade-offs

- [per-run 快照与 runner 行漂移] → 快照即解耦：在飞 run 永远按派发那一刻的参数记账；代价是 runner 行改名后 run 行内 name 是历史名（记账用 docker_host 快照，name 仅展示）→ 可接受
- [探针误报（网络抖动）打死调度] → 全部候选红 = 保持 pending 等下轮（等待安全，不 abandon）；面板显示上次绿时间，抖动肉眼可辨；探针单次失败重试一轮再标红
- [seed 语义迁移混淆] → RUNBOOK §22 补「seed 已消费」排查行；面板池列表常驻显示数据来源是表非 env
- [禁用后旧机维护杀在飞 run] → 禁用是软下线语义（RUNBOOK 注记）；终态以 exited/gone 正常记账不静默
- [一次性探针容器失败形态] → 三种失败（daemon 不可达 / 镜像缺 / 回读不匹配）分别报红并注明哪项——排查起点不丢

## Migration Plan

1. 部署即生效：db 迁移自动建 runners 表 + runs 表加列（启动路径，幂等）；env 两键已配的现存部署首启自动 seed 一行（池内唯一 enabled 机 = 调度唯一候选，派发行为与 seed 前等价）。
2. 回滚（代码退回 add-hub-remote-runner 版）：runners 表与 runs 新列留存无害（旧代码不读）；env 两键若仍在则恢复 env 生效语义，若已删则回本机形态——两者均安全。
3. RUNBOOK §22 更新：面板注册入池路径、seed 消费注记、探针面板与软下线说明。

## Open Questions

（无阻塞项）面板池管理的 UI 形态（列表 + 登记表单页 vs 行内编辑）与既有面板风格咬合，apply 期对齐 `hub/api.py` 现有面板节再定。
