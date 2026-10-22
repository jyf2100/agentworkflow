## Context

切片 1（add-hub-skeleton，已归档）交付了被动骨架：`QueueEngine`（sqlite 状态机）+ `Lifecycle`（tick_dispatch/reconcile 手动驱动）+ `DockerRunner`（docker CLI 接缝）+ FastAPI 最小面 + 单用户 env 直注。2026-10-21 镜像契约收口实测（真容器 inject-to-inject 端到端）固化了部署事实：执行镜像 vault 投影 `/opt/pa`、ENTRYPOINT 收敛 run_daily、运行用户 `pa(uid=1001)`、hub 挂载面 = state(写) + profiles/sources.yaml(读) + PRD(读)。F8 已裁 hub 本体也容器化（compose 部署）。缺口三块：凭据保险箱（现在裸 env）、排度常驻（现在手动 tick）、compose 编排（现在手工拼命令 + 属主不对齐实测会 PermissionError）。

## Goals / Non-Goals

**Goals:**
- 凭据落加密保险箱，容器注入面零变化（`--env-file` 0600 即弃即删通道不动）
- lifecycle 成为可常驻循环（tick + reconcile 周期驱动、信号优雅退出、重启对账幂等）
- 一条命令起整个 hub 服务（compose up），data 卷属主与执行镜像 uid=1001 对齐
- 单用户形态无身份系统依赖，2b OAuth 可后置叠加

**Non-Goals:**
- OAuth/Mattermost 身份面（切片 2b）、投递 API（切片 3）、面板/bot（切片 4）
- 凭据轮换体系、多 master key（登记，YAGNI）
- ghcr.io 镜像分发（本切片本地 build；F8 已留后续）
- 执行容器进 compose service 定义（见 D1 范畴澄清）

## Decisions

### D1：compose 唯一常驻 service = pa-hub；执行容器是 hub 经 socket 起的兄弟容器
F8 措辞「compose 含两个服务——pa-hub + 执行容器池」在切片 1 落地后需范畴澄清：执行容器的挂载/env-file/stamp 全是**每 run 动态参数**（镜像契约收口已固化），compose 静态 service 定义表达不了。执行「池」的真实形态 = 镜像在部署机预 build（F13 手动，`image_exists` fail 暴露）+ hub 容器挂 `/var/run/docker.sock` 经 `docker run -d` 起兄弟容器。compose 里只有一个常驻 service：pa-hub。替代方案（docker-in-docker）被否：dind 需要 privileged、卷映射语义扭曲。

### D2：保险箱 = 整文件 Fernet 加密的 JSON + master key 经 compose env 注入
- 存储：`<data_root>/vault/credentials.json`（0600，目录 0700）——`{"users": {"<user>": {"ANTHROPIC_BASE_URL": …, "ANTHROPIC_AUTH_TOKEN": …, "GH_TOKEN": …, "ANTHROPIC_DEFAULT_MODEL": …?}}}`，整 payload 单 Fernet token（字段级加密无增益——解密即全量进内存）。
- 原语：`cryptography` Fernet（AES128-CBC+HMAC，标准库无对称加密；hub 是常驻服务非 cron，可引入依赖；备选 openssl 子进程被否——错误面丑且非原子写）。
- master key：宿主 `hub/deploy/vault.key`（0600，gitignore）→ compose `env_file` 注入 `HUB_VAULT_MASTER_KEY`。轮换 YAGNI 登记。
- 写入路径：原子写（tmp + rename）。明文只在内存与既有 0600 env-file 短暂存在——不新增明文落点。

### D3：凭据来源优先级 = vault → env 兜底（单用户过渡延续）
`lifecycle` 派发时取 env：vault 有该 user 记录用之；缺则降级 `cfg.user_env()`（warn 日志）。保险箱为空的部署（切片 1 形态）不破。bootstrap：`cli.py vault-import --from-env` 把 hub 进程 env 三值+路由写入保险箱（单用户；2b 后加交互录入）。API/语义零变化——`RunSpec.env_file` 通道不动。

### D4：daemon = FastAPI 同进程后台线程（不是独立 service/进程）
`daemon.py`：`run_forever(lc, interval, stop_event)`——循环 `tick_dispatch()` + `reconcile_running()`，启动时先 `reconcile_startup()`（幂等已测）；SIGTERM/SIGINT → set stop_event → 完成当前迭代退出（在跑容器不杀——docker -d 进程树分离，F4/F5 语义不变）。挂在 FastAPI startup（threading.Thread, daemon=True）。
- 备选「api/scheduler 两 service」被否：sqlite 单写者约束（queue_engine「排度器单写者」）在同进程内靠线程串行化最薄；两进程写 sqlite 要重审整个写面。
- sqlite 多线程：连接 `check_same_thread=False` + WAL + 短事务（tick 与 enqueue 都是短事务，busy_timeout 兜底）。
- interval：`HUB_TICK_INTERVAL`（默认 5s）。
- **SIGTERM 路由链（容器形态必经）**：compose 下 PID 1 是 uvicorn，`docker stop` 的 SIGTERM 先到 uvicorn 而非 daemon 线程；`threading.Thread(daemon=True)` 随进程退出会被硬杀，「完成当前迭代」落空。路由：uvicorn lifespan **shutdown** 钩子置 stop_event + `join(timeout)`（等当前迭代收尾）→ 进程退出；compose service 配 `stop_grace_period: 30s`（docker 默认 10s，覆盖一次慢迭代）。

### D5：属主对齐 = pa-hub 容器以 `1001:1001` 跑 + 宿主 data_root chown 1001
镜像契约收口实测：执行镜像 `pa(uid=1001)` 写 state 卷需宿主目录可写。对齐方案选「hub 也用 1001」（compose `user: "1001:1001"`）而非 chmod 777（多用户服务器上 777 是权限面洞）或宿主服务（F8 已裁容器化）。data_root（`/data/pa/`）部署时一次性 `chown -R 1001:1001`——部署文档钉入 RUNBOOK。副产品：hub 进程与执行容器同 uid，vault 文件（0600 属主 1001）hub 可读写、宿主其他账号不可读。

### D6：hub 镜像 = thin 独立 Dockerfile（与执行镜像零共享层）
`hub/deploy/Dockerfile`：python:3.12-slim + fastapi/uvicorn/cryptography/pypinyin + docker CLI（socket 客户端）+ vault 投影 `hub/`。ENTRYPOINT = uvicorn + daemon 线程（D4 同进程）。与执行镜像（vault 全投影 + claude runtime）职责正交：hub 是控制面常驻物，执行镜像是即弃目标面载体——不共用 build context（F8「import 依赖极薄」的镜像层对应）。

## Risks / Trade-offs

- [docker.sock 挂载 = hub 容器内等效 root] → 单机单用户形态接受（F2 最小授权的阶段性边界）；2b 身份面落地时复审（socket proxy 细化是已知后手）。
- [master key 经 compose env 注入，`docker inspect` 可见] → 单机单用户接受；登记 2b 收紧（secrets 面板/轮换）。
- [sqlite 双线程（API enqueue + daemon tick）] → `check_same_thread=False` + WAL + 短事务 + busy_timeout；写冲突窗口极窄（tick 间歇 5s）；测试覆盖并发 enqueue×tick。
- [cryptography 依赖引入] → hub 镜像 +1 包；语义标准（Fernet），无编译链（wheel）。
- [tick 线程崩了 hub 不自知] → 循环体 try/except 全捕获记日志续跑（无静默失败）；连续异常计数告警日志。

## Migration Plan

部署机（一次性，顺序不可倒——import 时 data_root 尚属宿主用户可写，chown 后宿主即写不进）：① `vault-import --from-env`（宿主 env 或 0600 env 文件）② `chown -R 1001:1001 /data/pa`（连 vault 递归）③ `docker compose up -d --build`。回退：`compose down` 后切片 1 手工形态仍完整可用（D3 env 兜底保证代码向后兼容）；data 卷属主 chown 回滚无害。

## Open Questions

（无阻塞性——hub 镜像推 ghcr.io 的分发细节按 F8 基调留后续切片。）
