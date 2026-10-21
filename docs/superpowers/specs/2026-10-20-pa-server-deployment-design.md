# pa 服务器集中式部署形态设计（阶段 2）

> 日期：2026-10-20 ｜ 状态：已批准（S1 + per-user 凭据修订 + 本体论裁决 F1-F7 并入；spec 见 issue #3。落地裁决 F8-F9 已补：hub=vault 顶层 `hub/`、部署=本服务器 compose、凭据 env 注入验证通过）
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

### F8 裁决（2026-10-20 探索会话）：hub 位置 + 部署形态
- **hub 代码位置 = vault 顶层 `hub/` 目录**（候选 B）：hub 是独立部署物，与 pa 的 import 依赖刻意保持极薄（ContainerRunner 协议语义 + journal 只读渲染；流水线在容器内跑，hub 不直接跑 run_daily）——独立目录与独立部署物互相印证。
- **连锁债**：vault「除 pa 外无可执行代码」的约定破例 → CLAUDE.md 仓库描述随实施同步修订（登记，不丢）。
- **部署 = 本服务器 docker-compose**（docker 28.3.2 + compose v2.38.2 + 当前用户在 docker 组，前提已验证）：compose 含两个服务——pa-hub（build context = vault `hub/`）+ 执行容器池（image = template tag + Dockerfile）；数据卷 `/data/pa/projects/<proj>/state`。
- **hub 镜像分发细节（本地 build vs 推 ghcr.io）**留 change 细化（thin 镜像 + ghcr.io 方向已有基调）。

### F9 验证消解（2026-10-20）：per-user 凭据注入假设成立
- **认证形态 = 纯 env**（三层证据）：① 本机实证 `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN` 走 LiteLLM 代理、无 OAuth credentials 文件，生产 roc cron 同模式；② 调用链无文件级认证依赖（persona_call 起 `claude -p` 子进程全靠 env 继承；`_load_claude_settings_env` 的存在意义即 SDK 子进程 env 认证）；③ F1 裁决下 hub 只跑执行链，涉及 persona（critic/progress/verify）仅用 Read/Bash、无 MCP 依赖。
- **保险箱每条 per-user 记录 = 3 值**：`ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` / `GH_TOKEN`（开 PR + 拉目标仓一份双用）；容器经 docker 原生 `--env-file`/secrets 注入，**零 credentials 文件挂载**。
- **残留项（非凭据问题）**：容器内 claude CLI 首跑状态（onboarding/config 预置）归切片 0 镜像脚手验收。
- **注入面实测补记（切片 0 E2E，2026-10-20 闭环）**：容器运行时注入面 = 三值 + **模型路由**——roc 代理拒裸 Anthropic id（CLI 默认被拒；`--model sonnet` 被正规化为 claude-sonnet-5 仍拒；`--model glm-5.2` 直传全绿）。模型映射非凭据（无秘密性），F9「3 值」结论仍成立；镜像中立不预置（roc 特定形态）→ hub 保险箱每条 per-user 记录建议为「3 值 + 可选模型路由」，容器经 `--env-file` 同通道注入（`ANTHROPIC_DEFAULT_*_MODEL` env 或显式 `--model`）。证据：openspec/changes/add-executor-image/verification-4.2-5.1.md §2。

### F10 裁决（2026-10-21 探索会话）：run 发起人维度 = 卷上署名 meta（T1）
- **journal 保持 user-agnostic**——journal 的本体是流水线事实流（恢复/reconcile 判据源），「谁发起」是因果上层；恢复时 user 无语义作用，进 journal 即死字段。pa 零侵入、本机形态零概念污染。
- **hub 起容器前往 `state/runs/<proj>/` 写 `<stamp>_<slug>.meta.json`**（发起人 / 队列项 id / 镜像 tag）。审计自包含在卷上，hub DB 只做索引、可从 meta 重建。
- **hub 对 state 卷的写面钉死为「仅 meta 署名文件」**——不写 journal、不删 state（防 hub 膨胀滑坡的不变式；滑坡正是从「顺手写一个小文件」开始）。
- **孤儿 meta（journal 缺失）= failed-to-start 的准确审计标记**，优于事后水位扫描。
- **连带契约：run 目录命名 `<stamp>_<slug>` 升格为 journal 契约的一部分**（hub 靠它与 DB 对齐），pa 侧改格式须过 change 评审。
- 弃案记录：a) journal 加 user 字段（自包含最彻底但污染事实流、工具链全跟、本机形态背空字段）；b) 纯 hub DB 映射（零侵入但 DB 丢审计断链、failed-to-start 不可辨）。

### F11 裁决（2026-10-21 探索会话）：投递 API = state ownership 交接协议（T2）
- **前提修正（staging 事实）**：`stage_inject` 不是 prd 段的下游而是**平行入口**——「把手写 PRD md 转成标准 manifest，替 radar→prd 自动路径」（run_daily.py docstring），显式 `--inject-prd` 手动触发。故**分家线在 critic 前，不在 inject 前**：自动 PRD（vault prd 段产出的标准 manifest）经投递 API 直达 `--from-stage critic`；手写 PRD（同事面板/Mattermost 提交）不经 vault，由 hub 落盘后在容器内走 `--from-stage inject --inject-prd`（YAML frontmatter / project 已知性 / manifest 标准化等机械活复用 inject 段，hub 不重写）。两入口在服务器汇流，critic 统一把关——即 T3 的解法雏形。
- **裁决：执行链分家机制 = `--to-stage` 物理分家（方案 a）**。mac cron 显式 `run_daily --to-stage prd`（新参数，对称既有 `--from-stage` 断点续跑机制——**分家 = 既有机制升格为部署边界，最小新概念**）；投递器为独立脚本 `deliver_prd.py`（扫未 delivered 的 PRD → 逐个 POST → 成功写标记）。不变式「一个 project 的执行链运行权威必须唯一」由机制物理保证（命令行层面就没有后半段）而非纪律保证。
- **幂等交接协议（exactly-once 投递）**：崩溃窗口 ①POST前 ②POST后未收响应(UNKNOWN) ③收200未写标记 ④hub收到未落队 ⑤落队未响应——全部靠「vault 重扫重投 + hub 幂等去重」收敛。三态远程查询不变式的推广：投递响应 UNKNOWN 绝不当成功处理（无回执不标 delivered），幂等键让重试安全。幂等键建议 `(project, 文件名, sha256)`（stamp 前缀天然唯一，sha 检测同名异容），细节切片 3 定。
- **PRD 生命周期（vault 视角）**：`produced → delivered`，止步。critic pass/drop/revise 结果在服务器 state，vault 不知道也不需要知道。
- **衍生张力登记（T2a，未裁）**：report 段产物（报告文件）服务器形态下落服务器卷，vault「大脑」的报告沉淀职责被劈开一半——roc 侧可见性靠 Mattermost 通知 + 面板查询。待后续裁决，不阻塞切片 1-3。
- **去重边界劈开实害盘点：实害 = 无**。radar 去重清单完整留 vault 信号链；执行链逐段检验（critic 无 dedup 需求 / dispatch exactly-once 是 journal-based reconcile 不依赖 radar dedup / report 无）。手写 PRD 不过 radar dedup 是既有形态非新债。
- 弃案记录：b) 投递钩子嵌 run_daily（prd 段后自动投递并跳过已投 PRD 后续段）——delivered 标记写失败时存在双跑风险窗口，且投递职责耦合进编排器；c) project 白名单分流——最灵活但 `.project-auto` 是 gitignore 本地态、两机配置无同步机制，配置真源分裂成新问题。

### F12 裁决（2026-10-21 探索会话）：critic「有据」单标准推广 + 手写入口必填（T3）
- **裁决**：「有据」定义推广为**可追溯依据**——信号出处（自动 PRD 的 source_path）**或** PRD 自带依据链接/背景（手写 PRD），critic **不看出身**、只判依据是否可追溯；hub 面板提交手写 PRD 时 evidence 字段**机械必填**（入口把空依据挡在闸外，省一轮 critic）。单标准 + 双层把守。
- 关键本体判断：**不是两套标准按出身分流**（反对——分裂把关标准是滑坡），是同一个「有据」概念的两种满足方式。伏笔：run_daily.py inject 段已有「PRD 无 source_path——critic 可能因『无据』revise/drop」警告（本机形态手写是罕见路径；服务器形态是同事主通道，不推广标准则通道形同虚设）。
- 弃案记录：仅 prompt 适配（入口不强制——依赖自觉，空 evidence 进 critic 浪费一轮闸）；手写免据（间接降低闸口水位）。
- 落地：critic persona prompt 一处适配 + 面板入口校验，切片 3/4。

### F13 裁决（2026-10-21 探索会话）：镜像版本链条三条（T4，均为既有裁决连锁）
- **队列项入队时钉镜像 tag**：F10 meta 已含 image tag 字段——run 与镜像版本的关系是 run 的属性，入队即锁定（审计可复现 + 一个队列项从入队到执行版本意图不变）。
- **镜像缺失 fail 不回退**：hub 起容器时本地无该 tag → 报错暴露，**绝不静默回退 `:latest`**（`container_sandbox.py` F4 注释已钉此语义；镜像缺失是运维事件，静默漂移是事故）。
- **build 触发 = 手动（YAGNI）**：发布 template tag 后 ssh 服务器跑 build 脚手（一条命令，已验证 BUILD_RC=0）。发布频率周级，webhook 自动化是过度工程。陈旧检测（remote 最新 tag vs 本地镜像比对 → Mattermost 通知）推切片 4。

### F14 裁决（2026-10-21 探索会话）：Mattermost 双角色显式分离（T5）
- **两个独立集成点/凭据实体**：OAuth app（身份——同事登录面板，识别发起人喂 F10 meta）与 notify bot（通道——run 完成/失败通知）。凭据、生命周期、失败模式各自独立；落地时序：切片 2 OAuth、切片 4 bot。
- **显式不变式：自动化主干（投递→队列→容器→报告落卷）零依赖 Mattermost**——OAuth 挂 = 登不了面板但 vault cron 投递不经 Mattermost、执行链照跑；bot 挂 = 通知丢、正确性零影响。两个集成点都是外围（身份入口便利 + 通知），可分别失效而不伤主干。

## 10. 风险登记
1. **LLM 端点容量**：5-10 并发 dev loop 对各用户 LLM 端点的压力——试点观察
2. **服务器单点**：接受（规模决定）；缓解 = 备份（见 6）
3. ~~服务凭据共享的成本归属待定~~ **已消解**（per-user 凭据修订）
4. **Mattermost OAuth app 注册**需管理员配合（运营依赖）
5. **身份源单点**（F7）：Mattermost 宕机 = 无新登录；已发会话与进行中 run 不受影响——影响面可接受，登记备查
6. **快照一致性**（F7）：state 卷与 hub DB（队列/审计）分别快照会撕裂（队列项指向不存在的 state）——备份策略须做一致性快照（停写窗口或先卷后库的有序恢复校验）
