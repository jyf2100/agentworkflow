## 1. db 层：runners 表 + run 快照列

- [x] 1.1 `hub/db.py` 增 runners 表（name UNIQUE / docker_host / runner_mount_root / capacity / enabled / 探针缓存列；部分唯一索引保至多一 active 的设计已废——无 active 列）+ 注册/编辑/禁用/删除（校验：`tcp://` 前缀、绝对路径、capacity≥1、name `[a-z0-9][a-z0-9_-]*` 查重；禁用/删除对任何行放行——软下线）+ `pick_runner_for_dispatch()`（候选 = enabled 且探针 ok，负载 = runs 按 runner_name 快照统计 running，未满载中取最小、平局按登记序）+ seed 迁移（首启表空且 `HUB_DOCKER_HOST` 有值 → 灌一行，幂等）
- [x] 1.2 runs 表增快照列 `runner_name` / `runner_docker_host`（派发时写入；旧行 NULL = 本机形态），`create_run` 加参数
- [x] 1.3 单测：注册校验拒绝形态 / 调度选择（最低负载、平局登记序、满载排除、红机排除、全候选红返 None、无 enabled 行返 None=本机）/ seed 幂等（表空才灌、表非空 env 静默无效）/ 旧行 NULL 兼容

## 2. runner 层：docker_host 从构造固化改 per-call

- [x] 2.1 `hub/runner.py` `DockerRunner` 构造去掉 docker_host 固定持有；`image_exists` / `start` / `status` / `stop` 增可选 `docker_host` 参数（None = 本机，语义同现状 `_cli_env`），`_cli_env` 按参数现算；`ContainerRunner` Protocol 与 `FakeRunner` 同步（fake 记录 docker_host 供断言）
- [x] 2.2 单测：per-call 参数路由（同一 DockerRunner 实例两次调用不同 docker_host 各自注入）、不传 = 无 DOCKER_HOST、`--env` 面不泄漏（沿既有三测试改写）

## 3. lifecycle 层：派发选机 + 对账按快照

- [x] 3.1 `hub/lifecycle.py` 派发路径：`pick_runner_for_dispatch()` 选机（None = 本机 + `data_root` 投影，byte-identical）→ docker_host / 投影根来自选中行 → 快照 runner_name / runner_docker_host 入 run 行；无候选远程行但池非空且全红/满载 → 本轮不派发（item 留 pending，与「无可派」同径）；投影换算逻辑不变（树外 PRD abandon 语义保留，触发条件 = 选中远程行）
- [x] 3.2 tick 对账：running run 的 status / stop 按行内快照 docker_host 路由（NULL = 本机）——多机并跑、禁用软下线不断账
- [x] 3.3 单测：无池 byte-identical / 选中行参数进派发 CLI env 与挂载源 / 快照入行 / 禁用后在飞 run 对账仍路由原机 / 全红池不派发不 abandon

## 4. 健康探针

- [x] 4.1 新 `hub/health_probe.py`：三项探针（daemon 可达 `docker version` / 镜像在场 `image_exists` / 双挂——data_root 写 nonce 探针文件 + 经该 daemon 起一次性 `--rm` 容器用 pa-executor 镜像 cat 回读比对），结果（各 item 绿红 + 时间戳）写表行缓存；定期挂 daemon tick（N tick 计数，复用 stale_check 模式，cfg 增 `probe_ticks`）；绿 = 调度候选必要条件
- [x] 4.2 单测：三项各自独立成败（fake runner / fake subprocess 形态）/ nonce 不匹配报红 / 结果缓存写入 / 探针异常不炸 daemon tick

## 5. API + 面板

- [x] 5.1 `hub/api.py` 池端点：GET 池列表+负载+健康（观察面，任何有效会话）；POST 注册/编辑/禁用/启用/删除、POST 手动探针（挂 `HUB_PANEL_ADMINS` 门）；面板 UI 增服务器池节（列表 + 红绿灯 + 各机 running/capacity + 注册/禁启用表单）
- [x] 5.2 单测：权限分层（非白名单改行为拒绝、观察面放行）/ 注册校验透传拒绝 / 禁用软下线放行（有在飞 run 亦放行）

## 6. 配置与部署面

- [x] 6.1 `hub/config.py`：`tcp://` 限形校验从 `load_config` 移至注册/seed 入口（env 值形态错误 = 启动报错拒灌 seed）；两字段保留解析（seed 消费）+ `probe_ticks` 字段；`hub/serve.py` 接线（seed 调用 + DockerRunner 构造去 docker_host）；`hub/deploy/hub.env.example` 两键注释改 seed 语义
- [x] 6.2 RUNBOOK：§22 补「面板注册入池 + seed 消费注记 + 探针/软下线/自动调度说明」；回滚注记（旧代码不读表，env/表残留无害）
- [x] 6.3 `bash hub/quality.sh` 全绿（新单测并入）——401 passed + ruff 绿

## 7. 验证

- [x] 7.1 本机回归：空池干跑全链 byte-identical + 注册两行（fake 健康）后派发参数断言与负载路由断言
- [ ] 7.2 远程真机：183 注册入池 → 探针三项绿 → `/pa run` 容器出现在目标机、负载计数正确（依赖 add-hub-remote-runner 5.2 同批 ops 前提，单独追踪不阻塞归档）
