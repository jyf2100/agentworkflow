## Why

add-hub-runner-pool 把远程 runner 的部署前提核实固化成探针红绿灯兼调度候选门，但三项里「镜像在场」的修复动作仍是纯人工：pa-executor 镜像只在 hub 本机 daemon 本地构建、不在任何 registry（拉取被拒）——2026-10-31 实证：#110 因 183 缺 v0.1.35 探针报红挂起约 10 分钟，靠人肉 `docker save | ssh root@183 docker load`（2.24GB LAN 流式）修复。本 change 把「发现→修复」闭合为自动：探针对某 runner 报「daemon 可达 + 镜像缺失」→ hub 后台把默认 tag 镜像从本机 daemon 流式分发到该 runner → 复测转绿即回调度候选——全程 item 保持 pending（等待安全，不烧失败），失败降级 MM 告警绝不静默。

**依赖**：add-hub-runner-pool 落地（探针三项 / probe 缓存列 / 调度候选门是其面——本 change 只读消费零改动）；归档顺序 = runner-pool 先（先例：runner-pool 之于 add-hub-remote-runner 的 delta 叠加）。

## What Changes

- **触发 = 探针镜像项报缺失**（定期/手动探针同径；判据 = daemon 项 ok 且 image 项非 ok——对断连机不烧传输）。拒绝「派发前预检」：把多 GB 传输或逐派发镜像复查放上排度 tick 路径，与探针既有「占 tick 路径宁可红不可拖死循环」哲学冲突，且单次只覆盖被选中的一台（design D1 对比）
- **通道 = hub 容器内 docker CLI 流式 `save | load`**：`docker save <tag> | docker -H tcp://<runner> load`——hub thin 镜像内置 docker-cli（`hub/deploy/Dockerfile` 实证）、`DOCKER_HOST` per-call 注入与派发/探针同通道同权。拒绝内网 registry（常驻新设施 + 新安全面，与 hub 最小依赖面冲突；跨机房/规模上量再议）与 SSH（hub 无远程 shell 无凭据，事故的人肉 ssh 动作不可自动化沿用）——design D2 三案对比
- **幂等与并发**：单一后台 worker + (runner, tag) 在飞/排队双重去重；开工复测目标在场即 no-op（与人工修复赛跑安全）；绝不阻塞排度 tick
- **超时与失败降级**：传输超时（缺省 900s，env 可配）→ 终止管道 + runner 保持红（不进候选）+ 队列保持 pending + MM 管理面告警（同一 (runner, tag) 失败去抖）——绝不静默跳过、绝不 abandon；重试 = 下一轮探针自然重触发
- **内容校验**：load 后源/目标 image ID 比对（内容寻址），不一致删目标侧 tag 按失败处理；镜像源恒 = hub 本机 daemon（`/var/run/docker.sock`），绝不从公网 registry 拉取顶替
- **范围钉死**：仅 `HUB_IMAGE_TAG_DEFAULT`（与探针镜像项同 tag，触发与范围同源）；显式钉版 tag 沿 F13 既有 fail 语义；本机形态（池空）不涉及
- **面板与部署面**：runners 表同步结果列 + 池列表展示 + 面板手动同步（`HUB_PANEL_ADMINS` 门）；RUNBOOK §22 更新

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `execution-hub`：新增「runner 池镜像自动分发」Requirement（触发判据 / 通道 / 幂等去重 / 降级告警 / 内容校验场景）；「run 容器宿主可配（远程 daemon + 挂载源分离）」的部署前提从「镜像须在目标 daemon 预载」放宽为「人工预载或 hub 自动分发」——派发时 `start()` 首步复查的镜像缺失 fail 语义不变

## Impact

- **代码**：新 `hub/image_sync.py`（流式传输核心 + 后台 worker）、`hub/lifecycle.py`（`probe_runners` 尾部触发挂点，fail-open）、`hub/db.py`（runners 表同步结果列幂等迁移）、`hub/api.py`（手动同步端点 + 池列表带同步状态）、`hub/config.py`（`HUB_IMAGE_SYNC_TIMEOUT`）
- **契约（只读面零改动）**：探针三项判据与独立捕获、probe 缓存列语义（probe_ok / probe_json / probed_at）、调度候选门（`pick_runner_for_dispatch`）、F13 镜像缺失 fail 终态、`image_exists` 三态判据全部不动——本 change 只消费不重定义；同步绝不直写 probe_ok（复测经既有单行探针入口，探针是唯一绿灯权威）
- **部署**：`hub/deploy/hub.env.example` 新键注记；RUNBOOK §22 补自动分发说明 / 告警排查行 / 面板手动同步
- **测试**：image_sync 单测（fake 子进程管道：在场 no-op / 成功 / ID 不一致删目标 / 超时终止 / 源缺失文案 / 断连不烧传输）、去重与告警去抖、触发判据、面板权限；远程真机复演事故形态
