# pa 服务器集中式部署形态设计（阶段 2）

> 日期：2026-10-20 ｜ 状态：已批准（S1 + per-user 凭据修订 + 本体论裁决 F1-F7 并入；spec 见 issue #3）
> 前置：`2026-10-20-productize-pa-design.md`（方案 B template repo，阶段 1）+ `openspec/changes/productize-pa-template/`
> 关系：**演进路线**——阶段 1 照跑不冻结（template/bootstrap/admit/webapp 试点照做），本设计是阶段 2 团队服务器形态。
> 决策画像：四动机全要（上手成本/团队可见性/凭据集中管控/Mattermost 协作流）；dev loop 在服务器容器内；Mattermost OAuth 登录 + bot 通知；中等规模（5-15 人、十来个仓、峰值并发 run 5-10）。

## 0. 方案选择

三方案对比后取 **S1：单机 docker-compose「pa-hub + 执行容器池」**：

- **S1（选定）**：一台服务器，pa-hub 常驻服务 + 每 run 一个即弃执行容器 + 数据卷。无 K8s、无微服务拆分。
- S2（否决）：K8s 多租户平台——5-15 人规模过度设计，团队无平台工程带宽；YAGNI。
- S3（否决）：控制面集中 + 执行分散（self-hosted runner）——与「上手成本」动机相悖（同事仍要装 runner、机器要常开），双向回连拓扑更复杂。

S1 核心论据：演进路线下复用面最大——template 投影=镜像内容源、webapp=hub 雏形、ContainerSandbox=隔离语义、journal 恢复=容器即弃的兜底。

## 1. 架构总览

```
┌────────────────────── 服务器（一台，docker-compose）──────────────────────┐
│                                                                            │
│  pa-hub（常驻，FastAPI 单服务）                                             │
│  ├─ Mattermost OAuth2 登录 → 会话                                          │
│  ├─ Web 面板：上传 PRD / 项目状态 / PR 列表（方案 B webapp 的多租户升级）    │
│  ├─ 执行队列（per-project 串行，跨项目并行，峰值 5-10）                      │
│  ├─ 凭据保险箱（per-user，加密存储）                                        │
│  └─ Mattermost bot 通知（过闸/PR/验证结果 → 频道或私信）                     │
│         │ 起容器（每 run 一个，跑完即弃）                                    │
│         ▼                                                                  │
│  执行容器池：image = 方案 B template 投影 + Dockerfile                      │
│  └─ 容器内跑 run_daily 断点续跑入口（--from-stage inject 同款）              │
│     · claude CLI + persona 全在镜像里                                       │
│     · 非 root + 网络白名单（NetworkPolicy 语义）+ CPU/内存限额               │
│     · 目标仓 clone 进容器 worktree，PR 经 git 远端回同事的仓                 │
│                                                                            │
│  数据卷：/data/pa/projects/<proj>/state（与本机 .project-auto/state 同构）  │
└────────────────────────────────────────────────────────────────────────────┘
        ▲ Mattermost OAuth                ▲ bot 通知
      同事（浏览器，零安装）           Mattermost 频道/私信
```

关键咬合点：阶段 1 的 `release_template.sh` 产出的 template tag 就是执行镜像的 build context——投影直接变成镜像内容源，两阶段零浪费。

## 2. 组件

### pa-hub（唯一常驻服务）
- FastAPI 单服务（方案 B webapp 的进化形态，非同一文件），vault 侧新顶层模块
- 职责六项：OAuth 会话、面板 API、执行队列、凭据保险箱、bot 通知、容器生命周期管理
- 持久化：SQLite（单机中等规模够用）+ 数据卷
- **不变式沿用**：hub 只做机械活（排队、起容器、转发通知），不产生任何语义判定——机械/语义切分在服务器形态不动摇

### 执行容器（每 run 一个，跑完即弃）
- 镜像 = template 投影 + Dockerfile（阶段 2 给 release 链加 Dockerfile 产物，template tag 即 build context）
- 容器内：`run_daily --from-stage inject` 同款入口；隔离复用 ContainerSandbox 语义（非 root、NetworkPolicy 域名白名单：git 远端 + LLM 端点 + 声明域、CPU/内存限额、凭据最小注入）
- run 与容器是 **1:N** 而非 1:1——journal run id 是持续身份，容器是即弃载体，崩溃续跑=同一 run 的新容器（见 §9）

### 并发模型
- **per-project 串行**（同项目不并发），跨项目并行，峰值 5-10 容器
- 互斥双层主从：**hub 队列为权威层，容器内 `.run.lock` 文件锁为防御层**（防绕过队列直接起容器；见 §9）

### 数据层
- 服务器卷 `/data/pa/projects/<proj>/state` 与本机 `.project-auto/state` 同构——journal、recovery、reconcile 语义零变化
- hub DB（SQLite）只管：用户（Mattermost id ↔ 凭据引用 ↔ 通知偏好）、队列、审计
- PRD 与报告落卷，面板只读渲染

## 3. Mattermost 集成
- **登录**：Mattermost OAuth2 做 IdP（需 MM 管理员注册 OAuth app），hub 发会话 cookie；不新建用户系统
- **bot**：bot token 推通知，事件 = critic pass/drop、PR 开出、verify verdict、run 失败；路由 per-user 私信默认 + per-project 频道可选
- 通知失败**不阻断流水线**（观察面 fail-open）

## 4. 凭据面（per-user 修订版）

| 凭据 | 归属 | 说明 |
|---|---|---|
| LLM/claude CLI 凭据 | **per-user** | 首次登录录入，加密存保险箱；容器按**发起人**注入 |
| gh PAT | **per-user** | 一份同时覆盖：开 PR（真实归属）+ 拉目标仓（私有仓权限） |
| 目标仓访问 | 复用同一 per-user PAT | 不需要 hub 侧 deploy key |

- 语义：**服务器不持有任何共享服务凭据**——服务器是保管者而非共享提供者；每人一条凭据记录，run 容器按发起人注入全套
- 同一用户多个 run 共享其个人配额（per-user 限流内部竞争；per-project 串行已压并发，可接受）
- 收益：成本归属天然明确、限流隔离。代价：首次登录多录入一样凭据（与「凭据集中」动机一致）

## 5. 数据流（一份 PRD 的旅程）

PRD 两个入口汇入同一队列：① 同事浏览器 OAuth → 面板上传；② 维护者 vault 信号链（fetch→radar→prd）产出 → 投递 API（见 §9 F1 裁决）。随后：hub 入队（选定 project）→ per-project 串行起容器（挂 state 卷 + PRD + 发起人凭据最小注入）→ 容器内 inject→critic→dispatch（目标仓 clone 进容器 worktree，PR 经远端回同事仓）→verify→report → journal 实时落卷，面板只读渲染 → bot 推 Mattermost → 容器即弃、数据留卷。

## 6. 错误处理（fail-safe 推广）
- **容器崩溃** → hub 检测非零退出 → recovery_cli 语义不变（journal 单写、中部损坏 fail-closed）→ 面板一键续跑（复用 state）
- **hub 崩溃** → 执行容器是独立进程树，run 不被杀（反向故障域隔离，与方案 B「面板死不影响 run」对称）
- **三态查询**：容器内零变化；hub 起容器前的目标仓可达性检查同样三态，UNKNOWN 不起 run
- **队列**：DB 持久，hub 重启不丢

## 7. 测试接缝
- hub API：FastAPI TestClient in-process（OAuth mock、per-project 串行断言）
- 容器编排：ContainerRunner Protocol 注入 Fake（复用既有解耦模式——无 docker 也能测全分支）
- 通知：mock Mattermost webhook，断言事件→payload 映射
- 端到端：compose 起 hub + fake runner + 机械段 dry-run
- Prior art：scripts/ 1961 pytest 用例、ContainerSandbox 测试、canary 双源

## 8. 与方案 B 的衔接
- 阶段 1 照跑不冻结：template/bootstrap/admit/webapp(localhost) 试点照做
- 阶段 2 增量 = release 链加 Dockerfile 产物 + pa-hub 新模块（vault 侧）+ Mattermost 组件
- webapp(localhost) 与 pa-hub **并存不冲突**：前者是 template 内容（本机模式保留给单机/私有场景），后者是团队部署物

## 9. 本体论裁决与范畴澄清（F1-F7）

> 源自 2026-10-20 对本文档的本体论审核；spec 镜像见 issue #3。裁决原则：不修改任何既有不变式者优先。

### F1 裁决：hub 是执行面服务，信号链留 vault
- 信号链（fetch/radar/prd）保留在维护者 vault 本机 cron；hub 只覆盖执行链（inject→report）
- **run 范畴不跨拓扑**：vault 的 run 产 PRD；hub 起的**新 run** 从 `--from-stage inject` 起步、自带独立 journal stamp——各自 journal 单写，真理源不割裂（不违反 durable-loop-state）
- 接缝 = hub 接收 PRD 的投递 API（面板上传与 vault 投递两入口同一队列）
- 由此 §4「服务器不持有任何共享凭据」不变式原样成立（无系统身份），且避开采集语料（Knowledge/，绝不入仓）的服务器持久化问题
- **信号链整体上服务器 = 远期开放问题**（届时再议只读系统身份例外）

### F2 裁决：最小授权模型
- 默认：团队全员可见、可注入所有 project；任意 project 可选设**私有白名单**（owner + 被邀者，仅两级，无 viewer/contributor/admin 细分——YAGNI）
- 配置分界沿用方案 B 本体：项目 **profiles 的真源 = 上游发布链**（改动=回流，随 template tag 更新）；项目**实例配置（通知路由、可选开关）= 实例数据**，owner 可改

### F3-F6 范畴澄清
- **F3 控制面联合体**：控制面 = vault（信号与决策上游）+ hub（执行与团队状态）联合体；「控制面」范畴从仓级（方案 B D3 的「控制面仓每实例一个」）再裂变为部署拓扑级——**CONTEXT.md 术语第三次修订的债要登记**（随阶段 2 实施一并做）
- **F4 run:容器 = 1:N**：journal run id 是持续身份；容器是即弃执行载体。实现约束：续跑必须复用同一 run id（起新 run = 毁断点续跑语义）
- **F5 互斥主从**：hub 队列是权威层；容器内 `.run.lock` 退化为防御层。两层都不许「优化」掉——去掉队列=绕过串行，去掉锁=绕过队列直起容器无防线
- **F6 ADR-0001 不被推翻**：目标面分离是**权威/身份分离**（目标面真理源 = 其 git 远端，容器内 worktree 是目标面在执行环境中的投影），从来不是物理位置隔离——与 vault 本机双 checkout 拓扑同构，服务器形态只是把双 checkout 搬进容器

## 10. 风险登记
1. **LLM 端点容量**：5-10 并发 dev loop 对各用户 LLM 端点的压力——试点观察
2. **服务器单点**：接受（规模决定）；缓解 = 备份（见 6）
3. ~~服务凭据共享的成本归属待定~~ **已消解**（per-user 凭据修订）
4. **Mattermost OAuth app 注册**需管理员配合（运营依赖）
5. **身份源单点**（F7）：Mattermost 宕机 = 无新登录；已发会话与进行中 run 不受影响——影响面可接受，登记备查
6. **快照一致性**（F7）：state 卷与 hub DB（队列/审计）分别快照会撕裂（队列项指向不存在的 state）——备份策略须做一致性快照（停写窗口或先卷后库的有序恢复校验）
