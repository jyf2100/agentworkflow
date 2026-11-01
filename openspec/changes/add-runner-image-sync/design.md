## Context

add-hub-runner-pool 引入远程 runner 服务器池：runners 表（name / docker_host（`tcp://`）/ runner_mount_root / capacity / enabled + 探针缓存列）+ 忙闲自动调度（候选 = enabled 且 `probe_ok=1`，hub/db.py:604-615）+ 健康探针（三项：daemon 可达 / 镜像在场（`cfg.image_tag_default`）/ 双挂 nonce 回读，各 60s 上限，hub/health_probe.py:28-77；每 `probe_ticks`（缺省 60 ≈ 5 分钟，hub/config.py:58）一轮挂 daemon tick，hub/daemon.py:62-68）。红 = 不进候选 → pending item 等待（hub/lifecycle.py:279-283）。

缺口在「镜像在场」项的修复动作：pa-executor 镜像只在 hub 本机 daemon（184）本地构建、不在任何 registry（2026-10-31 实证，主会话简报事实：#110 因 183 缺 v0.1.35 探针报「镜像缺失」+ 目标侧 `docker pull` 被拒，item 挂起约 10 分钟，人工 `docker save | ssh root@183 docker load`（2.24GB LAN 流式）修复）。探针把「缺失」变成可见的红绿灯，但绿灯恢复纯人工——每换一台机 / 每滚一版镜像都要重演一遍。本 change 把发现→修复闭合为自动分发。

## Goals / Non-Goals

**Goals:**
- 探针报「daemon 可达 + 镜像缺失」→ 自动分发默认 tag 镜像到该 runner → 复测转绿回候选——无人介入的红转绿闭环
- 零新基础设施（不建 registry、不配 SSH 凭据）——通道复用既有 docker CLI + `DOCKER_HOST` per-call 面
- 失败绝不静默：MM 管理面告警 + runner 保持红 + 队列保持 pending
- 与 in-flight 的 add-hub-runner-pool 只读咬合（其探针判据 / 候选门 / 缓存列零改动）

**Non-Goals:**
- 内网 registry（层去重 / 跨机房分发 / 多 hub 共享镜像源——池规模上量或跨 WAN 时的再立项案，见 D2）
- 非默认 tag 分发（显式钉版入队是罕见显式行为，沿 F13 人工语义）
- 自动 build（hub 本机 daemon 也缺源 = fail loud 告警「先 build」，绝不公网拉取顶替）
- 其他探针红项的自动修复（daemon 断连 / 双挂断链是人肉 ops——分发只救「可达而缺镜像」这一种红）
- TLS（2376）/ 非 `tcp://` transport（沿 runner-pool 第一期内网明文前提）

## Decisions

### D1 触发时机：探针报缺失时（选）——派发前预检（拒）

勘察两处 code anchor 后定案：

- **探针已是缺失的权威发现点 + 安全语义同源**：镜像项报文「镜像缺失: {tag}」（hub/health_probe.py:51-56）；探针红 ⇒ 不进候选（hub/db.py:604-615）⇒ item 天然留 pending（hub/lifecycle.py:279-283，pick_next 只读）——「发现缺失」与「item 不被烧」已经由同一份探针状态保证，触发分发只是给既有的红加一条恢复路径，零新调度语义。
- **fleet 覆盖 vs 单机覆盖**：探针每轮扫全部 enabled 行（hub/health_probe.py:98-115）——新版镜像滚出后所有缺片机在一轮内各自触发；派发前预检只覆盖「本轮被选中的那一台」，其余机要等被选中才发现。
- **tick 路径纪律**：预检的两个形态都违反既有哲学——同步等传输 = 把 2.24GB 量级搬上排度 tick（探针自己都恪守「占 tick 路径宁可红不可拖死循环」，hub/health_probe.py:28；派发基础设施异常也只有 note_error + 退避不阻塞，hub/lifecycle.py:380-385）；异步触发+跳过本轮 = 把探针镜像项逐派发重做一遍且与 item 生命周期耦合。
- **代价**：恢复延迟 ≤ 一个探针周期（缺省 5 分钟）+ 传输时长。对 #110 形态（人工 10 分钟）是纯改善；窗口内 dispatch 侧撞缺失沿 F13 fail 终态（见 Risks 第 1 条，不扩 scope）。

触发判据：**daemon 项 ok 且 image 项非 ok**（三项独立捕获是 runner-pool 既有语义，hub/health_probe.py:32-35）。对断连机烧传输无意义（load 也会失败）——分发只救「可达而缺镜像」。触发挂点 = `lifecycle.probe_runners()` 尾部消费本轮结果（hub/lifecycle.py:501-506），fail-open（观察面纪律，同 probe 自身）。开工时以 `DockerRunner.image_exists` 复测为权威判据（hub/runner.py:159-169 三态：不可达 / 在场 / 缺失——不复用报文字符串做机器判据，避免对 runner-pool 报文格式的字符串耦合）。

### D2 传输通道：hub 容器内 `save | load` 流式（选）——registry / 仅告警（拒）

- **A（选）`docker save <tag> | docker -H tcp://<runner> load`**：两端 CLI 都在 hub 容器内（thin 镜像内置 docker-cli——hub/deploy/Dockerfile:7-11，注释明记装 docker-cli 而非 docker.io 的真机教训）；load 侧经 `cli_env(docker_host)` per-call 注入（hub/runner.py:73-81，探针/派发同通道同权，不进 run 容器 `--env` 面）；save 侧读 hub 本机 daemon（`/var/run/docker.sock` 挂载，hub/deploy/docker-compose.yml:21）。物理动作与 2026-10-31 人工修复完全同构（save|load LAN 流式 2.24GB 已实证可行），只是把 ssh 传输段换成既有 2375 面（hub 无远程 shell、无 SSH 凭据——hub/health_probe.py:3-4 的事实约束，人肉的 ssh 通道不可自动化沿用）。零新设施、零新凭据。
- **B（拒，现期）内网 registry**：184 push、各 runner pull。优点是层级去重（滚版只传增量层）与镜像源高可用；代价是常驻新基础设施（部署/运维/故障面）+ 新安全面（registry 凭据与可达性），与 hub「依赖极薄」的既有立场冲突（hub/runner.py:147-150 无 SDK 依赖的教训）。保留再立项条件：池跨机房/WAN、或镜像数量与滚版频率让层去重收益显著。
- **C（拒为常态）仅告警人工**：即现状——#110 已证是 10 分钟级挂起 + 人肉救火；保留为 A 失败后的**降级终态**（告警仍达人工，人工通道不变），不是常态方案。

### D3 幂等与并发

- **单一后台 worker 线程 + 任务队列**，键 (runner_id, tag)：排队去重 + 在飞去重（同一键传输中，新触发不重不排）。多 runner 同时缺片（新版滚动典型形态）→ 各自入队，worker 串行传输（LAN 带宽共享，串行可预期；池规模大再议并行度）。
- **开工复测在场即 no-op**：人工修复与自动触发赛跑安全；`docker load` 同 tag 重放本身幂等（内容寻址覆盖），即便重复传输也无害结果。
- **进程内状态（无持久锁）**：hub 单实例是既有前提（`_event_seen` 同型先例，hub/lifecycle.py:141-143）；重启丢在飞任务无害——目标侧仍缺 → 下轮探针仍红 → 自然重触发。中途被杀的 load 不产生半镜像（流不完整 load 报错不落 tag）。
- **不阻塞排度 tick**：worker 独立线程，探针/派发/对账照常。

### D4 超时与失败降级：MM 告警 + 保持 pending，绝不静默跳过

- 传输超时 env 可配（`HUB_IMAGE_SYNC_TIMEOUT`，缺省 900s——2.24GB LAN 流式的宽裕上限；与 PROBE_TIMEOUT=60 量级不同物，不共用）。超时/失败动作 = 终止两端管道 → runner 保持红（不进候选）→ 队列保持 pending → MM 管理面告警（`notify_admin` 管理面告警通道，hub/notify.py:286-293）+ stderr 恒留痕。
- **告警去抖**：同一 (runner, tag) 失败组合只告警一次（进程内 set 先例：stale_check 去抖 + daemon 的 stale_seen，hub/stale_check.py:11-12 / hub/daemon.py:45），成功或失败原因变化后重置；重启重告可接受（同先例语义）。绝不因「告警噪音」退化为不告警。
- **重试 = 探针周期即重试周期**：无独立重试器/退避器——下轮探针（≤5 分钟）仍报缺失即再触发（D3 去重保证不并发）。持续失败 = 周期性告警去抖后一条 + 面板同步状态可见，人工介入通道与 C 案相同。
- **绝不静默跳过**的三个面：不悄悄放弃同步（失败必告警）、不悄悄改道（绝不回落本机或选红机赶进度——红机不进候选是 runner-pool 语义，本 change 不碰）、不悄悄烧失败（item 永远 pending 等绿，镜像缺失导致的 F13 fail 只可能发生在 dispatch 侧 race，与本分发路径无关）。

### D5 内容校验与安全

- **镜像内容 hash 校验**：load 完成后取源 / 目标两侧 `docker image inspect -f {{.Id}}` 比对（config digest，内容寻址——传输损坏或内容被改必变 ID）；不一致 → 删除目标侧该 tag（防污染派发面：探针绿后 dispatch 会真用它起容器）+ 按 D4 失败降级（告警带两侧 ID 摘要）。
- **DOCKER_HOST 网络面**：通道即既有 `tcp://2375` 面——只对**登记表内的行**发起（`docker_host` 限形 `tcp://`，与派发/探针同一面同一权限级别），不新增任何可达目标；分发写目标 daemon（load 落镜像）相对派发写（起容器）是更小的权限动作，不构成新权限等级。明文 transport 的篡改风险由 D5 ID 比对兜底（内容寻址自带完整性验证）。
- **信任链**：镜像内容源恒 = hub 本机 daemon（184 构建管线的产物）；绝不从公网 registry 拉取顶替（本地构建镜像不在任何 registry——2026-10-31 实证的 pull 被拒正是同一事实）。hub 本机也缺源 → fail loud 告警「先在 hub 本机 build」。`DOCKER_HOST` 沿 `cli_env` 契约绝不进 run 容器 `--env` 面（hub/runner.py:73-81）。

### D6 与 add-hub-runner-pool 的边界（in-flight 只读咬合）

- **只读消费其面**：探针三项判据与独立捕获、probe 缓存列语义（probe_ok / probe_json / probed_at，hub/db.py:616-623）、候选门（`pick_runner_for_dispatch`）、`probe_and_store` 单行入口（hub/health_probe.py:84-95）全部零改动；**绝不改其 tasks.md 与 spec delta**。
- **同步绝不直写 probe_ok**：分发成功后的转绿只能经复测探针（`probe_and_store` 同径——与面板手动触发同一入口同一写缓存路径）发生，探针是唯一绿灯权威；分发结果自记新列（runners 表加同步结果列，本 change 自己的 db 迁移，幂等加列先例 = runner-pool 的 runs 快照列）。
- **归档顺序**：runner-pool 先归档——本 change MODIFIED 的「run 容器宿主可配」requirement 基线是其 delta 后文本（先例：runner-pool MODIFIED add-hub-remote-runner 的 ADDED requirement，当时同样 in-flight 叠加）。proposal 依赖行已声明。

### D7 范围钉死：仅默认 tag

分发对象 = `cfg.image_tag_default`（与探针镜像项同 tag——hub/health_probe.py:53，触发与范围同源，不引入第二判据）。队列项显式钉非默认 tag 且目标缺片 → 沿 F13 既有「镜像缺失 fail」语义（hub/lifecycle.py:373-379）不接管；本机形态（池空回落本机 daemon）镜像即 hub 自己的 daemon，不涉及分发（本机缺片 = hub 部署错误，stale_check / F13 已覆盖）。

## Risks / Trade-offs

- [默认 tag 滚动窗口内的 dispatch race：探针绿缓存过期（tag 已换新）→ 派发时缺片 F13 fail 终态] → 这是 runner-pool 既有的窗口（探针周期内缓存不失效），本 change 不改 F13；窗口 ≤ 探针周期且事后自动补齐（下轮探针触发分发），重新入队即成功——不扩 scope 去 soften F13（那是「入队钉镜像 tag（F13）」requirement 的面）
- [分发与人工修复赛跑（同 tag 双写）] → 开工复测在场即 no-op + load 幂等覆盖，双写结果收敛同内容
- [多 runner 同时缺片串行传输拖长恢复（LAN 拥塞）] → 单 worker 串行是有意为之（带宽可预期）；每台完成后即复测即回候选（不必等全队）
- [告警风暴] → (runner, tag) 失败组合去抖 + 成功重置；stderr 恒留痕保证不因去抖丢事实
- [分发进行中该 runner 行被编辑/禁用] → 分发只是落镜像到某 daemon，行编辑后探针自会重判（编辑即清探针缓存——hub/db.py:550-558）；在飞分发完成无害（多一片镜像不是错误状态）
- [runner-pool 归档竞争（本 change 先归档则 MODIFIED 基线缺位）] → proposal 依赖行 + D6 归档顺序声明；真发生时归档动作按依赖序人工纠偏

## Migration Plan

1. 部署即生效：runners 表幂等加同步结果列（启动路径迁移，先例 = runner-pool）；`HUB_IMAGE_SYNC_TIMEOUT` 可选（缺省 900s）；无既有行为变化——首次探针报缺失前零动作。
2. 回滚：代码退回 = 分发不再发生，红灯/人工修复现状即恢复（runner-pool 面从未动过）；新列残留无害（旧代码不读）。
3. RUNBOOK §22 更新：自动分发说明 + 「同步失败告警」排查行（先查 hub 本机 daemon 有无源镜像）+ 面板手动同步入口。

## Open Questions

（无阻塞项）面板同步状态的展示粒度（池列表行内摘要 vs 详情展开）apply 期对齐 `hub/api.py` 既有面板节再定。
