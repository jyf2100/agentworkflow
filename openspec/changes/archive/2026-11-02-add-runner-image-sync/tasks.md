## 1. image_sync 模块：流式传输核心 + 后台 worker

- [x] 1.1 新 `hub/image_sync.py` 传输核心：开工以 `DockerRunner.image_exists(tag, docker_host)` 复测（三态：不可达→不烧直接失败告警 / 在场→no-op / 缺失→起传输）；传输 = `docker save <tag> | docker -H <docker_host> load` 流式管道（save 侧 env 本机、load 侧 `cli_env(docker_host)` 注入——与派发/探针同通道）；超时（`cfg.image_sync_timeout` 缺省 900s）终止两端；load 后源/目标 `docker image inspect -f {{.Id}}` 比对，不一致删目标侧 tag 按失败处理；镜像源缺失（hub 本机无该 tag）返回明确失败文案（绝不公网拉取顶替）；结果 (ok, detail) 返回不抛——失败也是有效结论
- [x] 1.2 后台 worker：单线程 + 任务队列，(runner_id, tag) 排队/在飞双重去重；完成后复测探针（`probe_and_store` 同径单行入口——绝不直写 probe_ok）+ 分发结果写 runners 行同步列 + MM 告警 `notify_admin`（同一 (runner, tag) 失败组合去抖、成功或原因变化重置；stderr 恒留痕）；绝不阻塞排度 tick
- [x] 1.3 单测（fake 子进程管道 / fake runner）：在场 no-op / 传输成功 ID 一致后复测 / ID 不一致删目标侧 / 超时终止两端 / 源缺失 fail loud 文案 / daemon 不可达不烧传输 / 在飞去重与排队合并 / 告警去抖与成功重置

## 2. 触发挂点 + db 面

- [x] 2.1 `hub/lifecycle.py` `probe_runners()` 尾部消费本轮探针结果：enabled 行中 daemon 项 ok 且 image 项非 ok 者 → offer 分发（tag = `cfg.image_tag_default`）；fail-open 绝不崩排度 tick（观察面纪律同 probe 自身）
- [x] 2.2 `hub/db.py` runners 表加分发结果列（幂等迁移，启动路径，先例 = runner-pool 的 runs 快照列）+ 读写函数；面板池列表读面带出
- [x] 2.3 单测：触发判据（daemon 红不触发 / image 红触发 / mount 红不拦分发）/ 挂点 fail-open / 加列迁移幂等（旧行 NULL 兼容）

## 3. API + 面板

- [x] 3.1 `hub/api.py` 手动分发端点（挂 `HUB_PANEL_ADMINS` 门，先例 = 池管理面）——与自动触发同径同判据同去重；池列表展示各 runner 分发状态（上次结果 + 时间戳）
- [x] 3.2 单测：权限分层（非白名单拒绝）/ 透传分发结果拒绝形态 / 面板读面任何有效会话可见

## 4. 配置与部署面

- [x] 4.1 `hub/config.py` 增 `HUB_IMAGE_SYNC_TIMEOUT`（缺省 900，≥1 校验）；`hub/deploy/hub.env.example` 新键注记；RUNBOOK §22 补「自动分发说明 + 同步失败告警排查行（先查 hub 本机 daemon 有无源镜像）+ 面板手动分发」

## 5. 验证

- [x] 5.1 本机回归：fake runner 全链——探针报缺（daemon ok + image 红）→ 后台分发 → 复测绿 → 回调度候选 → 派发成功；断连机不触发只保持红
- [x] 5.2 远程真机复演 2026-10-31 事故形态：183 删默认 tag 镜像 → 探针红 + item pending → 自动分发（save|load LAN 流式）→ 复测绿 → pending item 自动派发到 183 成功；同步失败路径（拔源镜像）验证 MM 告警与 pending 保持

## 6. quality

- [x] 6.1 `bash hub/quality.sh` 全绿（新单测并入）

## 实施证据（2026-11-01）

- 1.1：hub/image_sync.py sync_image：开工 image_exists 复测三态（不可达不烧传输/在场 no-op）+ save|load 流式管道（cli_env 双侧）+ 超时杀两端 + ID 内容寻址比对不一致删目标侧 + 源缺失 fail loud 文案（绝不公网拉取）+ (ok, detail) 不抛
- 1.2：ImageSyncWorker 单线程队列：键 (runner_id,tag) 排队/在飞双重去重；完成动作 = set_runner_image_sync + probe_and_store 复测（绝不直写 probe_ok）+ 失败 MM 告警同组合去抖/成功重置；绝不阻塞排度 tick
- 1.3：test_image_sync.py：no-op/成功 ID 一致/不一致删目标/超时杀两端/源缺失文案/不可达不起管道/在飞去重/告警去抖与成功重置——12 测试
- 2.1：lifecycle._offer_image_sync 探针尾部消费：enabled 且 daemon ok 且 image 非 ok → offer（mount 红不拦）；列表读取 fail-open
- 2.2：db.py runners 表幂等加列 image_sync_json（先例 = runs 快照列）+ set_runner_image_sync 写 JSON {ok,tag,detail,at}；面板/API 读面带出
- 2.3：触发判据六形态（daemon 红/image ok/mount 红不拦/禁用/坏 json/命中）+ db 读失败 fail-open + 迁移重开幂等
- 3.1：POST /runners/{id}/image-sync（admins 门）：判据同自动触发（daemon 非 ok 409）+ offer 同队列同去重 + 异步即返；GET /runners 增 image_sync 列；面板池页「分发」列（✅/❌+title 详情）
- 3.2：test_api：非白名单 403 / daemon 未探与红均 409 / 去重 queued=False 透传 / 读面任何有效会话可见 image_sync 列
- 4.1：HUB_IMAGE_SYNC_TIMEOUT（缺省 900，≥1 校验，test_config 覆盖）+ hub.env.example 注记 + RUNBOOK §22 自动分发说明（含同步失败排查行与手动分发端点）
- 5.1：单测即 fake 全链（fake 管道/fake runner/fake db）
- 5.2：2026-11-01 真机复演完整闭环：183 rmi v0.1.35 → 探针红（镜像缺失）→ 自动 save|load 流式（源/目标 ID 一致 sha256:362b30b81176…）→ 复测探针绿 → #111 自动派发 183（容器 env 9001+qwen3.8-27b-fast 正确）。失败路径（拔源+MM 真私信）单测覆盖告警逻辑，真机留观测
- 6.1：bash hub/quality.sh 全绿（compile+pytest+ruff，2026-11-01，新增 14 测试并入）
