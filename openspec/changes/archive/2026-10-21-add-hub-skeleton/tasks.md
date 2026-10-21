## 1. 脚手架与配置（单用户 bootstrap 替换点）

- [x] 1.1 建 `hub/` 目录与测试基座：`hub/pytest.ini`（或等效配置）使 vault 根 `python -m pytest hub -q` 可独立运行；ruff 配置沿用 pa 口径（E9+F、line-length=120）；空模块占位（config/db/queue_engine/meta_sign/runner/lifecycle/api/cli）先落位
- [x] 1.2 TDD `config.py`：测试先行（HUB_USER 三值 + 模型路由 + HUB_IMAGE_TAG_DEFAULT + HUB_DATA_ROOT + HUB_MAX_CONTAINERS 的读取/默认值/缺失明确报错列出缺项）→ 实现；env 读取全部集中此文件（切片 2 保险箱唯一替换点）

## 2. 数据层（队列持久化）

- [x] 2.1 TDD `db.py`：测试先行（queue_items/runs 两表 schema、status 枚举迁移、插入/查询/更新、DB 文件跨连接持久）→ 实现（sqlite3 标准库，无 ORM）

## 3. 队列引擎（排度核心）

- [x] 3.1 TDD per-project 串行：测试先行（project A running 时同 project 第二项保持 pending、其他 project 照常派发；以注入时钟/即时 tick 构造）→ 实现 `queue_engine.py` 排度循环
- [x] 3.2 TDD 峰值上限：测试先行（活跃容器达上限时新 project 项等待、容器终态释放后派发）→ 实现
- [x] 3.3 TDD 重启不丢：测试先行（排度器持有 DB 重开 → pending 完整、可继续派发）→ 实现恢复路径

## 4. F10 署名 meta（写面钉死）

- [x] 4.1 TDD `meta_sign.py` + run 名预分配：测试先行（四元组拼路径写 `<stamp>_<slug>.meta.json`、字段 initiator/queue_item_id/image_tag/written_at、文件名模式 `^\d{8}(m\d*)?_[a-z0-9-]+\.meta\.json$` 外一律拒绝、预分配扫 `prd_manifest_*.json` 撞名自增 m 后缀直到 fresh——对齐 `_bump_stamp_suffix` 形态与 inject 实际撞名判据）→ 实现
- [x] 4.2 TDD 写面钉死不变式：测试先行（越界路径/../ 注入、非 meta 模式文件名、目录外写全部拒绝；模块不暴露通用写函数——以 import 面断言）→ 实现或收紧

## 5. F13 镜像咬合

- [x] 5.1 TDD 入队固化 tag：测试先行（入队不传 tag → 取 config 默认并写入队列项；派发与 meta 用固化值）→ 实现
- [x] 5.2 TDD 缺失 fail 不回退：测试先行（tag 本地不存在 → 队列项 failed + 缺失 tag 与 build 提示；断言无任何 latest 回退路径）→ 实现（镜像存在性检查进 Runner 接缝，Fake/Docker 同判）

## 6. ContainerRunner 接缝

- [x] 6.1 TDD Protocol + FakeRunner：测试先行（start/status/stop 三方法 + RunSpec 载荷；Fake 可编程注入崩溃/延迟/退出码/镜像缺失）→ 实现 `runner.py`
- [x] 6.2 DockerRunner：实现 docker CLI 包装（state 卷挂载映射镜像内 pa 期望路径 + PRD 只读输入卷 + --env-file + --cpus/--memory + 非 root 默认 + docker inspect 状态轮询；容器命令 = `--from-stage inject --inject-prd /input/<prd>.md --stamp <预分配>`）；real-docker 标记测试（本机有 daemon 才跑：真 pa-executor 镜像起容器跑机械自检段）
- [x] 6.3 env-file 注入：0600 临时文件、容器退出后删除、与切片 2 同通道（--env-file）——单测 + FakeRunner 断言

## 7. 容器生命周期（1:N + 对账）

- [x] 7.1 TDD 非零退出与重派：测试先行（exit_code 记录；attempt 计数、失败回 pending 不占 running 名额、FIFO 保序重派优先于后入队项、重派上限内续跑=同 run 新容器复用卷 state、attempt 达上限才转 failed 终态——run failed 与队列项 failed 两生命周期分离）→ 实现 `lifecycle.py`
- [x] 7.2 TDD 孤儿 meta = failed_to_start：测试先行（有 meta 无 journal 的 run 目录 → 标记 failed_to_start、保留 meta 不清理）→ 实现
- [x] 7.3 TDD hub 崩溃对账：测试先行（重启后 running 项经 Runner.status 对账——容器在跑续跟踪/已消失按卷归档终态；hub 崩溃不杀容器的进程树分离以 Fake 模拟断言）→ 实现恢复循环

## 8. API 与 CLI

- [x] 8.1 TDD `api.py`：TestClient 测试先行（POST /queue 入队校验、GET /queue 状态、GET /runs/{id} 详情、错误响应形态）→ 实现 FastAPI 三端点（仅此三端点，无语义判定面）
- [x] 8.2 `cli.py`：入队/查状态两命令（面板前的人肉驱动面）——走 api 同一内部函数，冒烟测试

## 9. E2E 干跑与收尾

- [x] 9.1 E2E 干跑：FakeRunner + 机械段 dry-run 全链测试（入队 → 排度 → meta 署名 → 容器 → 终态 → DB 记录断言；含一次崩溃-重启-对账旅程）；零 docker、零 LLM 成本
- [x] 9.2 quality 接线：hub 测试纳入统一质量命令可跑范围（quality.sh 覆盖或并列命令，CI 与本地同源）；ruff 对 hub/ 零告警
- [x] 9.3 文档：RUNBOOK 增 hub 骨架节（部署形态/env 清单/运维口径）+ CONTEXT.md「执行面 (hub)」词条（含与控制面/目标面的范畴区分）；核对 durable-loop-state delta 的声明性（本 change 零 pa 代码改动）
