## Context

阶段 2 设计定稿于 `docs/superpowers/specs/2026-10-20-pa-server-deployment-design.md`（§1-§8 架构 + §9 本体论裁决 F1-F14）。切片 0（执行镜像 pa-executor:v0.1.3）已闭环归档。本 change 是切片 1：执行面骨架——队列、run 记录、容器生命周期、fake runner、单用户 bootstrap。

关键前置事实：
- hub 代码位置 = vault 顶层 `hub/`（F8 候选 B）；与 pa import 依赖刻意极薄（ContainerRunner 协议语义参照 + journal 只读渲染），流水线在容器内跑、hub 不直接跑 run_daily
- 执行容器镜像已存在（pa-executor:<template-tag>，本机 daemon；build 手动——F13）
- 数据卷与 `.project-auto/state` 同构；hub DB 只管队列/审计（可从卷重建——F10）
- 切片 1 无身份系统：单用户凭据从 hub 进程 env 直注（切片 2 引入保险箱后替换）

## Goals / Non-Goals

**Goals:**
- 入队 → 排度 → 起容器 → run 完成/失败 → 续跑的最小闭环（fake runner 可全离线跑通；真 runner 可对真镜像跑通）
- F10 三条落地：meta 署名写入、写面钉死、孤儿 meta = failed-to-start
- F13 落地：入队钉 tag、缺失 fail 不回退
- 并发模型落地：per-project 串行（DB 为权威层）、跨项目并行带峰值上限
- ContainerRunner Protocol 注入接缝（复用 pa 的解耦模式：无 docker 测全分支）
- hub 崩溃不杀 run（反向故障域隔离）；hub 重启队列不丢（DB 持久）

**Non-Goals:**
- 投递 API / delivered 标记（F11，切片 3）
- OAuth、凭据保险箱、per-user 凭据（切片 2；本切片 env 直注是过渡形态，env 读取集中在单一模块便于替换）
- Mattermost 任何集成（F14，切片 2/4）
- 面板 UI（切片 4；本切片只有 JSON API）
- 镜像 build 自动化/陈旧检测（F13 手动；陈旧通知切片 4）
- pa `scripts/` 行为改动（零变化；durable-loop-state delta 是声明性升格）

## Decisions

### D1：目录与模块结构——扁平同仓，不进 pa

```
hub/
├── config.py        # env 读取（单用户三值+模型路由+镜像 tag 默认+数据卷根+并发上限）——切片 2 替换点
├── db.py            # SQLite schema + 迁移（queue_items / runs 两表）
├── queue_engine.py  # 排度：per-project 串行 + 跨项目并行 + 峰值上限（DB 权威层）
├── meta_sign.py     # F10 署名 meta 写入 + 写面钉死（路径校验：仅 <stamp>_<slug>.meta.json 模式）
├── runner.py        # ContainerRunner Protocol + DockerRunner（docker CLI 包装）+ FakeRunner
├── lifecycle.py     # run 1:N 容器编排：起容器前 meta、非零退出检测、续跑、孤儿 meta 处理
├── api.py           # FastAPI：POST /queue + GET /queue + GET /runs/{id}（最小三端点）
└── cli.py           # 手动入队/查状态（面板前的人肉驱动面）
```

理由：hub 是独立部署物（F8），目录独立于 pa；但仍在 vault 单仓内（控制面大脑托管执行面代码，CLAUDE.md 破例已裁）。同目录扁平模块沿用 pa 惯例（非可安装包）。

### D2：队列模型——SQLite 单写者 + 轮询排度

- 表 `queue_items(id, project, prd_path, image_tag, status, attempt, enqueued_by, created_at)`，status: `pending → running → done|failed`；**重派语义（本体论审核 A3）**：容器失败终态且 attempt 未达上限 → 队列项**回 pending**（attempt+1 保留计数），attempt 达上限才 failed——run 的 failed 是容器事实，队列项的 failed 是放弃决定，两个生命周期分离；同 project 派发取最老 pending（FIFO，重派项因入队更早天然优先于后入队项）
- 表 `runs(id, queue_item_id, project, run_dir, container_ref, status, started_at, ended_at, exit_code)`
- 排度器：单线程事件循环（asyncio 心跳或线程轮询，间隔可配）。每 tick：找无 running 队列项的 project 的最老 pending → 若活跃容器数 < 峰值上限 → 起容器
- per-project 串行的权威判据 = DB 查询（该 project 无 running 项才派发）；容器内 `.run.lock` 文件锁为防御层（防绕过队列直接起容器，复用既有锁语义）
- 不引入外部队列/redis/celery：单机规模（design doc §2「单机中等规模够用」）+ YAGNI

### D3：F10 meta 署名——hub 预分配 fresh run 名、写前生成、写面钉死、DB 只索引

- **hub 预分配 fresh run 名（本体论审核 A2）**：派发时扫卷上 **`state/prd_manifest_*.json`**（inject 段的实际撞名判据——`prd_manifest_<stamp>.json` 文件存在才 bump，且为全局 stamp 命名空间，非 run 目录），基于 PRD 文件名的 stamp 撞名则自增后缀（`m`/`m2`/… 同 `_bump_stamp_suffix` 形态）直到 fresh，经 run_daily 既有 `--stamp` 参数传入容器。依赖的契约事实：**inject 段仅在 manifest 撞名时 bump**（传入 fresh stamp 则沿用，写入 durable-loop-state delta）——由此 meta 文件名、`--stamp`、实际 run 目录三者天然对齐，无 bump 漂移。重复入队同一 PRD = 分配新 run（独立 run，各自 meta），不拒绝——幂等防重是切片 3 投递 API 层语义（F11），人工再跑是合法意图
- 起容器**前**由 lifecycle 调 meta_sign 写 `runs/<proj>/<stamp>_<slug>.meta.json`：`{initiator, queue_item_id, image_tag, written_at}`
- 写面钉死实现：meta_sign 只接受「数据卷根 + project + stamp + slug」四元组拼路径，且文件名必须匹配 `^\d{8}(m\d*)?_[a-z0-9-]+\.meta\.json$`（stamp 实际形态：8 位日期 + 可选 mN 避碰后缀——run_daily `_bump_stamp_suffix` 事实，本体论审核 A1 修正）；hub 其余模块无 state 卷写入口（不暴露通用写函数）
- hub DB 的 runs 表是索引不是真相：meta 文件在卷上，DB 丢可重建（对齐 F10「DB 只做索引」）

### D4：镜像咬合（F13）——入队钉 tag、缺失 fail

- `POST /queue` 必传 `image_tag`（或显式取 config 默认 tag 并**记进队列项**——不允许运行时解析「最新」）
- DockerRunner 起容器前 `docker image inspect <tag>`，缺失即 queue item → failed + 明确错误（提示手动 build），**不回退 latest**
- FakeRunner 同样校验 tag 存在性（对 inject 的 fake 镜像清单），保证测试与真实语义一致

### D5：ContainerRunner Protocol——起/查/杀三方法

```python
class ContainerRunner(Protocol):
    def start(self, spec: RunSpec) -> str: ...      # 返回 container_ref
    def status(self, ref: str) -> ContainerStatus:  # running|exited(rc)|missing
    def stop(self, ref: str) -> None: ...
```

- `RunSpec`: run_dir 挂载、env_file 路径、镜像 tag、命令（`--from-stage inject --inject-prd /input/<prd>.md --stamp <预分配>` + PRD 只读输入卷挂载）、资源限额参数
- DockerRunner：`docker run -d` + volume + `--env-file` + `--cpus/--memory` + 非 root 镜像默认；状态经 `docker inspect` 轮询
- FakeRunner：进程内字典模拟容器生命周期，可编程注入崩溃/延迟/退出码——E2E 与单测的主力
- 与 pa `container_sandbox.py` 的关系：协议语义参照（挂载/env/限额三件事同构），但 hub 不 import pa（两部署物零耦合；语义漂移由 spec 把关）

### D6：单用户 env 直注——集中单文件，切片 2 替换点

- config.py 读 `HUB_USER_*`（三值+模型路由）与 `HUB_IMAGE_TAG_DEFAULT`、`HUB_DATA_ROOT`、`HUB_MAX_CONTAINERS`
- 起容器时写临时 env-file（0600，容器即弃后删）——与切片 2 保险箱的 `--env-file` 同通道，替换面收敛在 config+runner 两处
- 切片 1 的 `enqueued_by` 恒为该单用户（F10 meta 的 initiator 字段照写——schema 从第一天就是终态，切片 2 只换值来源）

### D7：API 与测试接缝

- FastAPI 三端点（入队/查队列/查 run）+ TestClient in-process 测试（design doc §7 既有裁决）
- 排度器以可注入时钟/间隔构造，测试秒级跑完生命周期
- E2E（干跑级）：FakeRunner + 机械段 dry-run（不起真 docker、零 LLM 成本），断言队列→meta→容器→终态→DB 全链
- 真 docker 验证单独一层（mark real-docker，本地有 daemon 才跑）：对 pa-executor 真镜像起容器跑机械自检段

### D8：错误处理（design doc §6 落地）

- 容器非零退出：run 记 failed + exit_code；同一 queue_item 可重派（续跑=同 run 新容器，journal 在卷上延续）——重派上限可配，超限 queue item → failed
- hub 崩溃重启：恢复循环扫 running 容器 → 经 docker inspect 对账（容器在跑=继续跟踪；容器没了=按 journal/孤儿 meta 归档终态）
- 孤儿 meta（有 meta 无 journal）：run 标 failed_to_start（F10 审计语义），不静默清理
- 起容器前目标仓可达性检查：三态，UNKNOWN 不起 run（fail-safe 推广）——随切片 3 投递链路引入（切片 1 不做，YAGNI：FakeRunner 场景无真远程可查）

## Risks / Trade-offs

- **SQLite 单写者**：单机规模足够；排度器单线程是吞吐上限（接受——峰值 5-10 容器，瓶颈在容器不在 DB）
- **轮询而非事件驱动**：tick 间隔引入秒级派发延迟（接受——cron 级工作负载）；换事件驱动是后续优化不是骨架需求
- **hub 与 pa 的语义耦合靠 spec 把关而非 import**（D5）：镜像 tag 契约、run 目录命名契约两头各写一份，漂移风险由两个 spec（executor-image / durable-loop-state delta）+ review 承担——比 import 耦合更符合部署物分离（F8），代价明确
- **单用户 env 直注是临时形态**：凭据经 hub 进程 env（进程环境可见性弱于保险箱加密存储）——切片 1 的接受面 = 单人运维期；切片 2 必须落地保险箱后才引入第二用户
- **真 docker 测试依赖本机 daemon**：CI 无 docker 时 real-docker 层跳过（Fake 层保覆盖），真实形态靠本地验收补——与 canary/干跑分层先例一致
