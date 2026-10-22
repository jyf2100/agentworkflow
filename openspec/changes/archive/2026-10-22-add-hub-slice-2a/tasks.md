## 1. 凭据保险箱存储（hub-credential-vault）

- [x] 1.1 `vault_store.py`：Fernet 整 payload 加密的 load/save（0600 文件 / 0700 目录、tmp+rename 原子写、master key 缺失或错误明确报错不猜）；TDD：密文落盘 / 原子性 / key 错误三测先行
- [x] 1.2 `get_user_env(user)` 读取面：按 user 解密取 env 字典（三值 + 可选模型路由）；确认无「列出全部明文」入口（源码审视断言写入测试）
- [x] 1.3 `cli.py vault-import --from-env`：单用户导入（幂等覆盖、确认输出不回显明文值）；TDD：导入→落密文→重复导入覆盖

## 2. lifecycle 凭据来源改造（execution-hub delta）

- [x] 2.1 派发取 env 改为 vault 优先 + env 兜底（保险箱无该 user 记录 → cfg.user_env() + warn 日志）；TDD：vault 命中不经 env / 兜底路径 warn 可见 / 双缺明确报错三测
- [x] 2.2 全量回归：hub 单测 + quality 绿（切片 1 行为零破坏实证——env-only 形态测试原样通过）

## 3. 排度常驻服务（execution-hub delta）

- [x] 3.1 `daemon.py`：`run_forever(lc, interval, stop_event)` 循环（启动先 reconcile_startup、迭代 = tick_dispatch + reconcile_running、单次异常捕获记日志续跑 + 连续异常计数）；TDD：fake clock 驱动的循环/异常续跑/计数三测
- [x] 3.2 信号优雅退出：SIGTERM/SIGINT → stop_event → 完成当前迭代退出、不杀在跑容器；TDD：stop_event 置位后当前迭代完成、容器状态不变
- [x] 3.3 sqlite 多线程设置：连接 `check_same_thread=False` + WAL + busy_timeout；TDD：并发 enqueue × tick 无死锁无丢写；同步更新 `queue_engine.py` 头注释「单线程 tick（排度器单写者）」为 daemon 多线程事实

## 4. API 进程整合

- [x] 4.1 FastAPI startup 挂 daemon 线程（HUB_TICK_INTERVAL 默认 5s 可配）+ **lifespan shutdown 置 stop_event 并 join(timeout)**（PID 1 是 uvicorn，SIGTERM 先到它——路由链见 design D4）；TDD：TestClient 启动后 tick 循环可观测、shutdown 后循环停止（fake lifecycle 注入）
- [x] 4.2 服务入口 `serve.py`（uvicorn + create_app + daemon 线程装配）；手动冒烟一次即算过（真 uvicorn 起停）

## 5. compose 部署（hub-deployment）

- [x] 5.1 `hub/deploy/Dockerfile`：thin 镜像（python:3.12-slim + fastapi/uvicorn/cryptography/pypinyin + docker CLI + hub/ 投影）；本机 build 通过
- [x] 5.2 `hub/deploy/docker-compose.yml`：单常驻 service pa-hub（user 1001:1001、docker.sock 挂载、data 卷、env_file、restart: unless-stopped、stop_grace_period: 30s——docker stop 默认 10s 会 SIGKILL 斩断优雅退出）；`compose config` 校验通过
- [x] 5.3 属主对齐验证：compose 环境下执行容器真写 state 卷成功（uid=1001 对齐实证——镜像契约收口测试的 compose 形态复跑）
- [x] 5.4 `vault.key` 与 env 文件的 gitignore 钉死（凭据面绝不入仓复核）

## 6. 文档与收口

- [x] 6.1 RUNBOOK 新节「hub compose 部署」：vault-import → chown → compose up 三步（顺序不可倒：import 时 data_root 尚属宿主用户，chown 后宿主写不进）+ 回退路径（compose down 后切片 1 手工形态可用）
- [x] 6.2 全量收口：hub quality 绿 + 真容器/compose 冒烟证据（含一次 kill/重启自愈观察——restart 拉起后 reconcile_startup 幂等对账实证）落 change verification 文件 + tasks 全勾
