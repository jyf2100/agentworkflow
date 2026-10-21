## Why

阶段 2（服务器集中式部署）切片 1。前置已全部就绪：切片 0 执行镜像（pa-executor v0.1.3，add-executor-image 已归档）闭环；本体论裁决 F10-F14（design doc §9，2026-10-21 commit 5c363da）固化了发起人署名、镜像版本咬合、写面边界等核心不变式——hub 骨架开工零悬空。

没有骨架，后续切片（身份+保险箱、投递 API、通知+面板）没有任何可挂靠的运行时：队列、run 记录、容器生命周期是执行面的地基，全部后续能力都长在这上面。

## What Changes

新建 vault 顶层 `hub/` 模块（design doc §9 F8：候选 B，独立部署物与独立目录互相印证），交付执行面最小闭环：

- **执行队列**：SQLite 持久（hub 重启不丢），per-project 串行、跨项目并行，峰值并发上限可配
- **run 记录 + F10 meta 署名**：hub 起容器前往 state 卷 `runs/<proj>/` 写 `<stamp>_<slug>.meta.json`（发起人/队列项 id/镜像 tag）；hub 对 state 卷的写面钉死为仅 meta 署名文件（不写 journal、不删 state）；孤儿 meta = failed-to-start 审计标记
- **容器生命周期**：run 与容器 1:N（容器即弃、崩溃续跑=同 run 新容器）、非零退出检测、容器统一 `--from-stage inject --inject-prd` 入口——PRD 经**只读输入卷**挂给容器，写 state 卷的是容器内 pa 而非 hub（写面钉死由此成立；切片 3 投递链就绪后自动 PRD 才走 critic 快路径）。hub 预分配 fresh run 名（扫卷撞名自增后缀）经 `--stamp` 传入，meta 与 run 目录同名对齐（防 inject 段 bump 漂移）
- **F13 镜像咬合**：队列项入队时钉镜像 tag；起容器时本地无该 tag → fail 暴露，绝不静默回退 `:latest`
- **ContainerRunner Protocol**：注入 Fake（无 docker 测全分支）+ 真实 docker CLI 实现（state 卷挂载 + env-file 凭据注入 + 非 root + 资源限额）
- **单用户 env 直注 bootstrap**：无身份系统（切片 2 前过渡形态）——hub 进程 env 读单用户三值+模型路由，起容器写 env-file
- **FastAPI 最小 API**：入队（本地 PRD 文件路径）+ 队列/run 状态查询——只够驱动骨架闭环，非投递 API（切片 3）、非面板（切片 4）

明确不在本切片：投递 API（F11，切片 3）、身份/OAuth/凭据保险箱（切片 2）、Mattermost 双集成（F14，切片 2 OAuth/切片 4 bot）、面板 UI（切片 4）、镜像 build 自动化（F13 裁决手动）。

## Capabilities

### New Capabilities
- `execution-hub`: 执行面常驻服务的骨架契约——队列（per-project 串行）、run 记录与 F10 卷上署名 meta（写面钉死）、容器生命周期（1:N 即弃）、F13 入队钉镜像 tag 与缺失 fail、单用户 env 直注 bootstrap、ContainerRunner 注入接缝

### Modified Capabilities
- `durable-loop-state`: run 目录命名 `<stamp>_<slug>` 由实现惯例升格为 journal 契约的一部分（F10 连带裁决：hub 靠它与 DB 索引对齐，pa 侧改格式须过 change 评审）——本 change 不改格式，只升格其契约地位

## Impact

- 新增 `hub/` 顶层目录：vault 继 `Projects/项目推进流水线/` 后第二个代码区（F8 已裁；CLAUDE.md 的 hub 破例描述修正是 F8 连锁债，另行处理）
- 与 pa 的依赖刻意极薄（design doc:128）：ContainerRunner 协议语义参照 + journal 只读渲染；hub 不 import pa 编排逻辑，流水线在容器内跑
- 新依赖：SQLite（标准库 sqlite3）、FastAPI + TestClient（测试接缝照 design doc §7）
- 不改 pa `scripts/` 任何行为（run 目录命名契约升格为声明性 delta，零代码变化；quality 接线选并列命令，pa scripts/ 零改动）
- 服务器部署物形态：hub 进程 + 数据卷（`/data/pa/` 同构约定）+ 本机 docker daemon
