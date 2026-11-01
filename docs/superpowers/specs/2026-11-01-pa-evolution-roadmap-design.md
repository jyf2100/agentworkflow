---
date: 2026-11-01
type: design
tags:
  - design
  - evolution-roadmap
  - pa
  - hub
  - continuous-agent
uid: pa-evolution-roadmap-design
status: approved-draft
ai-first: true
---

# 演进路线设计：pa 从单任务执行深度走向连续·主动 Agent（P0–P3）

> 基于 DeepSeek Super-agent（dsh-superagent-prototype，2026-10-31 开源）研究 + 2026-10-31 两起真机事故实证，给 pa（项目推进流水线 + hub）定四阶段演进路线。核心判断一句话：**pa 已有 dsh 原型没有的单任务执行深度（质量门/durable runtime/runner 池），缺 dsh 已示范的「连续」与「主动」——向 dsh 借连续性骨架，不学它放弃质量门。**
>
> 素材为主会话与用户对齐的完整结论（骨架而非逐字稿），本文重组为正式设计文档。P0 两个 change（`add-config-drift-watchdog` / `add-runner-image-sync`）以**已立项**引用——change 目录已于 2026-11-01 建成（proposal/design/tasks/spec delta），两者 `openspec validate --strict --no-interactive` 均通过（rc=0，本次落档实测）；本文档 §2 的要点叙述是立项依据，**下游细节以 change proposal/design 为准**。P1–P3 为方向章节（不立项）；不采纳清单与度量独立成节（§7/§8）。dsh 侧事实均为主会话研究实证（外部仓，vault 内无克隆——已核实 `/mnt/disk01/workspaces/worksummary/` 下无 dsh 目录）；真机事故为主会话 2026-10-31 实证，可引用、不臆改。

## 0. 定位判断与决策记录

**dsh 命题**：单任务编码 Agent 已成熟，瓶颈从「Agent 能不能干活」转向**人的注意力**——人盯不住连续产出的流水线，成为新的稀缺资源。dsh 的回应是四特征：**连续**（事件驱动不断线）、**高效**（上下文经济学）、**主动**（观察找活而非等人派活）、**持久**（长程状态）。

**pa 与 dsh 的互补位**：

| 维度 | dsh 原型 | pa 现状（证据） |
|---|---|---|
| 单任务执行深度 | **没有**——无质量门、无验收注入、无 durable runtime | **已有**：对抗闸（critic 三态判据，`Projects/项目推进流水线/scripts/run_daily.py:767`）、验收双注入（`openspec/specs/acceptance-test-injection/` + `openspec/specs/acceptance-harness-e2e/`）、verified-dev-execution（`openspec/specs/verified-dev-execution/`）、durable runtime（`openspec/specs/durable-loop-state/` + `openspec/specs/durable-runtime-integration/`）、runner 池（`openspec/changes/add-hub-runner-pool/`，tasks 全勾含 7.2 远程真机 2026-10-30 实证，归档未走） |
| 连续（事件驱动/任务订阅/全双工） | 已示范 | **缺**——信号链是 cron 拉取型（`bootstrap/samples/sources.yaml:10` 的 kind 枚举 directory/github-repo/forge-repo/wechat-url/agent-deepresearch，无订阅型 Trigger）；hub↔pa 语义事件是单向文件接缝（`openspec/specs/pa-event-outlet/spec.md`，pa→hub 只出不相会） |
| 主动（观察找活） | 已示范 | **缺**——无目标仓观察者 |
| 持久（长程记忆） | 部分示范 | 半有——lessons 晋升（`openspec/specs/cross-prd-learning-memory/`）+ repo_facts 轨道 2 在途（`openspec/changes/add-rsi-repo-facts-loop/proposal.md`） |

**决策记录**：

| # | 决策点 | 结论 |
|---|---|---|
| 1 | 借什么 | dsh 的**连续性骨架**：事件驱动入队、订阅直通、注意力分层（「只显示需要人的地方」） |
| 2 | 不借什么 | dsh **放弃质量门**的取舍——pa 的闸门/三态/durable runtime 是护城河，演进全程不弱化（见 §7 不采纳清单） |
| 3 | 节奏 | 四阶段：P0 运维闭环硬化（1–2 周）→ P1 事件驱动化（1–2 月）→ P2 主动 Agent（2–3 月）→ P3 上下文经济学与长程记忆（长线） |
| 4 | 放量依据 | 度量先行（§8）：主动能力（P2）放量唯一依据是影子命中率，不拍脑袋切 auto |

## 1. 四阶段总览

```
P0 运维闭环硬化 ──► P1 事件驱动化 ──► P2 主动 Agent ──► P3 上下文经济学·长程记忆
  (1–2周)             (1–2月)           (2–3月)            (长线)
  事故教训变现         连续：订阅入队      主动：观察找活       高效+持久
  已立项 2 change     方向 3 项          方向 3 项           方向 3 项
```

| 阶段 | 主题 | 周期 | 立项状态 | 北极星 |
|---|---|---|---|---|
| P0 | 运维闭环硬化（本周实战教训变现） | 1–2 周 | **已立项**：`add-config-drift-watchdog`、`add-runner-image-sync`（§2.1/§2.2）；候选 2 项（§2.3/§2.4） | 事故根因机械可见，人工介入次数下降 |
| P1 | 事件驱动化（连续） | 1–2 月 | 方向（§3） | 信号→run 的时延从「下一 cron 周期」到「分钟级」 |
| P2 | 主动 Agent（主动） | 2–3 月 | 方向（§4） | **人的注意力预算**（每周人工介入次数） |
| P3 | 上下文经济学与长程记忆（高效+持久） | 长线 | 方向（§5） | 每决定分摊成本下降、记忆可修剪不腐化 |

## 2. P0 运维闭环硬化（1–2 周，已立项两 change）

P0 全部来自 2026-10-31 两起真机事故的教训变现：事故的共性不是机制缺陷，而是**六层配置/多机镜像各自独立存在、互不知晓，漂移不可见**——「改了无数次都没改回来」。

### 2.1 事故实证一：配置六层漂移（→ `add-config-drift-watchdog`，已立项）

模型路由/凭据在六层各自独立存在（主会话 2026-10-31 真机事故实证）：

| 层 | 载体 | 事实与锚点 |
|---|---|---|
| L1 | `~/.claude/settings.json` env 块 | 本机 CLI 直调形态（ANTHROPIC_BASE_URL 指 roc LiteLLM `172.32.153.184:4000` + token）；与 L2 的解耦关系见 `openspec/specs/per-agent-model-routing/spec.md:128` |
| L2 | `Projects/项目推进流水线/config/model-routing.json` | 本机形态 per-agent 路由文件（`config/model-routing.json:1-11`，dev=qwen3.8-27b-fast）；`.dockerignore:2-6` 固化排除出镜像（run 93 教训：alias 值进镜像 → roc 400 拒——容器形态不读它）；git HEAD 曾长期停留死值 |
| L3 | `hub/deploy/hub.env` | hub 容器 env_file（`hub/deploy/docker-compose.yml:25-27`），**只在容器创建时读取**；`docker compose restart` 不重读，改后必须 `up -d` 重建——本次事故主因之一 |
| L4 | hub 凭据保险箱 | `hub/vault_store.py:118,169`（CredentialVault/get_user_env）；run 容器凭据 **vault 优先、env 兜底**（`hub/lifecycle.py:147-197`）；曾停留 `4000+glm-5.3` 老快照，需 `hub/cli.py:35-36,209` 的 `vault-import --from-env`（merge 语义）重导入 |
| L5 | roc LiteLLM 注册 | 本机容器 litellm-litellm-1 + litellm_db postgres 持久化；模型名按网关分家（9001 直连认 `qwen3.8-27b-fast`；roc 曾只认 `Qwen3.8-27B`，后经 admin API 注册 fast）；virtual key 只能 `llm_api_routes`，`/model/new` 需 master key（容器 env LITELLM_MASTER_KEY）——外部基础设施，无仓内锚点 |
| L6 | run 容器实况 | 最终生效面，`docker exec printenv` 可验；注入通道 = `hub/lifecycle.py:320`（凭据解析）→ `hub/lifecycle.py:351`（env_file → 容器） |

**事故链**：L4 旧值 + L3 未重建 + L5 死链（qwen3.6-35b→8896）叠加 → run 全挂，且因六层无一处能看全貌，「改了无数次都没改回来」。

**change 要点**（`add-config-drift-watchdog`，已立项；下游细节以 `openspec/changes/add-config-drift-watchdog/proposal.md` 为准）：
- **断言范围 = hub 可达四层（L3–L6）**，最小规则集 4 条起步（全部机械判据、零误报设计）：R1 L3 意图文件 vs 运行快照（「改了 hub.env 没重建」）；R2 L4 vault 记录 vs L3 兜底源（「vault 旧快照」——派发 vault 优先，正是「改了没生效」主因）；R3 L5 网关模型注册 membership（三态 UNKNOWN 不误报）+ claude alias 负向黑名单预检（容器形态必死形态，run 93 类）；R4 L6 运行容器凭据键在场 + 形态合法（注入链断裂可见）。**L1/L2 显式排除**——hub 容器不可达 L1，容器形态按 spec 有意不读 L2（`openspec/specs/per-agent-model-routing/spec.md:168` 容器形态 env-only 场景）。
- **报告面只报漂移项**：每项 = 规则 + 层对 + 键名（+长度差）+ 修复指引，**值绝不入面**——dsh「只显示需要人的地方」原则：绿的不说，人只看差异。只报告，绝不自动修复、绝不阻断派发（对齐 `hub/stale_check.py:10`「告警是唯一动作」先例）。
- **落点 = daemon 第三低频槽**：stale_check / runner 池探针同型 N-tick 计数（`HUB_DRIFT_TICKS` 默认 720 ≈ 每小时；`hub/daemon.py:62-68` probe 挂点同型，`hub/config.py:119` probe_ticks 配对）；逻辑归新模块 `hub/config_drift.py`，fail-open 纪律全承——观察面绝不崩排度循环、绝不进派发路径。

### 2.2 事故实证二：runner 池镜像缺失（→ `add-runner-image-sync`，已立项）

add-hub-runner-pool 引入远程 runner 后暴露：runners 表（`hub/db.py:72`，docker_host 如 `tcp://172.32.153.183:2375`、mount_root `/mnt/nfs_jyf/pa`、probe_ticks 默认 60≈5 分钟，`hub/config.py:119`）健康探针 `probe_runners`（`hub/lifecycle.py:501`）报「镜像缺失: pa-executor:vX」（`hub/health_probe.py:54`）+ 拉取被拒——**本地构建镜像不在任何 registry**。拓扑：hub 跑在 184，NFS 182 共享 `/data/pa`，镜像只在 184 本地 daemon。#110 因 183 缺 v0.1.35 挂起约 10 分钟，手工 `docker save | ssh root@183 docker load`（2.24GB LAN 流式）修复；该手工路径已在 `Projects/项目推进流水线/RUNBOOK.md:730` 记载。hub thin 镜像内置 docker-cli（`hub/deploy/Dockerfile`），分发机制有物质基础。

**change 要点**（`add-runner-image-sync`，已立项；下游细节以 `openspec/changes/add-runner-image-sync/proposal.md` 为准）：
- **触发 = 探针镜像项报缺失**（判据 = daemon 项 ok 且 image 项非 ok——对断连机不烧传输）；拒绝「派发前预检」（多 GB 传输不上排度 tick 路径）。同步全程 item 保持 pending（等待安全，不烧失败），失败降级 MM 告警绝不静默——消除「入队→派发失败→挂起等人」的闭环缺口（现状 `hub/runner.py:42,117-118,194-195` ImageMissingError → 人工介入）。
- **通道定夺 = hub 容器内 docker CLI 流式 `docker save <tag> | docker -H tcp://<runner> load`**（`DOCKER_HOST` per-call 注入，与派发/探针同通道同权；2.24GB LAN 实测可行）；load 后源/目标 image ID 比对（内容寻址），不一致删目标侧 tag 按失败处理。**显式拒绝 SSH**（hub 无远程 shell 无凭据——事故里的人肉 ssh 动作不可自动化沿用）与**内网 registry**（常驻新设施 + 新安全面，与 hub 最小依赖面冲突；跨机房/规模上量再议）。
- **原则区分（与既有不变式的关系，显式立此存照）**：镜像**缺失补齐**是确定性机械活，可自动化；镜像**升级**（remote release tag vs 本地 tag 不一致）仍是人工决定——`hub/stale_check.py:10`「告警是唯一动作」与 RUNBOOK「hub 绝不自动拉取」针对的是升级面，本 change 不触碰；F13「镜像缺失 fail 不回退其他 tag」（`docs/superpowers/specs/2026-10-20-pa-server-deployment-design.md` §9）也不触碰——范围钉死 `HUB_IMAGE_TAG_DEFAULT` 同 tag 补齐，显式钉版 tag 沿 F13 既有 fail 语义。
- 完成后 RUNBOOK §22 补自动分发说明/告警排查行，「预载同 tag 镜像」人肉路径降级为兜底说明。

### 2.3 add-test-gate-auto-triage（候选，非本期立项）

test_stale 阻断（`Projects/项目推进流水线/scripts/evidence.py:25` GATE_STALE；`scripts/dev-agent.py:791` 发布门语义）当前一律降级人工。P0 候选：**阻断时先自动重验一次**（投机执行迷你版——stale 的成因是「绿测试后又发生候选写」，重验一次即可分辨「写入后仍绿」与「真退化」），仍 stale 才降级人工。只动 triage 路径，发布门语义（无新绿不发布）不变。

### 2.4 watchdog 日报骨架（候选，随 2.1 顺路）

otlp_export 骨架已在（`Projects/项目推进流水线/scripts/otlp_export.py:3,37,43`——OTLP exporter 抽象 + bounded timeout + 降级事件）。P0 候选：watchdog 漂移项 + 探针红 + 队列水位汇成每日一报——「只显示需要人的地方」的日报形态，为 P2 北极星（人工介入次数）预埋采集点。**注意**：watchdog change 本体范围不含日报（其 proposal 显式「不建日报/面板面」）——日报骨架是其后的顺路候选，立项时另案；未立项期间度量采集走既有落盘面（见 §6 推进纪律的条件式）。

## 3. P1 事件驱动化（1–2 月，方向章节·不立项）

### 3.1 add-event-sources：fetch 源升级订阅型 Trigger

现状信号链是 cron 拉取型（`bootstrap/samples/sources.yaml:10` kind 枚举全为拉取语义）。方向：fetch 源升级**订阅型 Trigger**（GitHub issues / PR 评论 / CI 事件），事件到达即入队，不等下一 cron 周期。

**两级过滤 = 机械活/语义活教义的 Trigger 翻译**：
- **Fast Path（机械筛）**：复用 repo-admission 三态准入（`openspec/specs/repo-admission/spec.md:8-9`）与成员三态授权（`hub/mm_intake.py:6-8`——FOUND/NOT_FOUND/UNKNOWN，UNKNOWN fail-closed）做源可信/身份/格式的确定性过滤，不过模型；
- **Slow Path（语义筛）**：过 Fast Path 的进 pa-radar（`.claude/agents/pa-radar.md:3`，控制面 headless persona）打分去重，产出 candidates。
不变式沿用：hub 只做机械活不产语义判定（hub 既有纪律），语义判定仍归 persona 面。

### 3.2 add-thread-subscription：订阅直通

run 进行中绑定频道里的人话（MM 频道讨论），经 **Gate 直发该 run 的上下文面**——落点两选一：dev handoff 注入（`openspec/specs/durable-loop-state/spec.md:75-76` dev 亲笔交接工件契约 + `Projects/项目推进流水线/scripts/coordinator.py:354-357` journal 事件载体）或下一 iteration 排队（`scripts/loop_state.py` 迭代状态机）。**不新开 PRD、不新增授权**——订阅即授权面收窄到「已绑定 run 的频道」，成员三态仍把门。注意此通道是 hub→run 注入（与 events.jsonl 的 pa→hub 单向出口反向），接缝边界（`hub/events.py:1-5` hub 只读不解析 journal 的对称纪律）在 change 期细化。

### 3.3 依赖：LangGraph 迁移收尾

`openspec/changes/langgraph-workflow-upgrade/proposal.md` 的 feature_flag 渐进 cutover 收尾是 P1 依赖——事件驱动入队后编排层的图表达/类型守边界（verdict 只在 PersonaNode 暴露写入）直接消费高频事件流，`scripts/run_daily.py` 命令式状态机（上游 change 立项时 ~3400 行，本次落档实测 5502 行）的维护成本会随事件频率上升。

## 4. P2 主动 Agent（2–3 月）

> **2026-11-01 立项注记**：P2 三件已起草为极细 change 包（`add-proactive-observer` / `add-router-persona` / `add-panel-attention`，各含本体论声明/状态机/schema/拒绝清单/验收场景与勘察前置 0.x）。实施顺序：observer → attention（消费提案面）→ router（解释一切面）；observer 的 auto 模式钉死为后续 change（预算闸+置信门+全链 critic 三重前置，§4.1 防线已在 spec 落档）。

### 4.1 add-proactive-observer：目标仓观察者

目标仓观察者盯三类信号（CI 红 / issue 激增 / 依赖告警），**observe 影子模式**起步：只记「本来会做什么」（would-do 提案 + 证据）进日报，不自动开 run。**profiles 写 policy 逐项目切 auto**——放量唯一依据是影子命中率（§8），项目级灰度而非全局开关。与 `add-rsi-repo-facts-loop` 协同：观察者发现的仓结构性事实走**轨道 2**（repo_facts，`openspec/changes/add-rsi-repo-facts-loop/proposal.md`——一次即候选 + MM 人审 confirm + facts sidecar）沉淀，不走 lessons 轨道 1 的 ≥2 PRD 复现门槛。

### 4.2 add-router-persona：MM 交互面轻模型 Router

MM 交互面加轻模型 Router（roc haiku 档——轻模型先例已有：`Projects/项目推进流水线/config/model-routing.json:5,9` 的 pa-radar/pa-progress 均路由 haiku），做自然语言问答与 triage 对话化（「#74 为什么 drop？」→ 查 events/journal 摘要作答）。**确定性 Gate 不动**——Router 只在交互面说话，不进调度链、不产 verdict（见 §7 第 1 条的边界内允许项：交互面 ≠ 调度面）。

### 4.3 面板升级：「只显示需要人的地方」

hub 面板（`hub/panel.py`）从「全量状态视图」升级为注意力分层：需要人决定的置顶（确认环待决/探针红/漂移项），其余折叠。dsh 同名原则的 pa 化——判据是「不置顶会不会漏掉必须人的决定」，不是视觉改版。

## 5. P3 上下文经济学与长程记忆（长线，方向章节·不立项）

> **2026-11-01 立项注记**：P3 三件已起草为极细 change 包（`add-context-loading-protocol` / `add-effects-queue` / `add-long-term-memory-graph`）。实施顺序：context-loading（最大价值面）→ effects-queue（durable 升级）→ memory-graph（与 loading 正交可并行）。

### 5.1 add-context-loading-protocol：stub + 按需展开

persona/dev 的上下文注入从「整块拼 prompt」升级为 **stub + 按需展开**（dsh admission-context 参照）：注入面先给摘要桩，agent 按需取全文。与 HelixHarness 的 context loading protocol 合流（vault 外参考克隆 `/mnt/disk01/workspaces/worksummary/helixharness/helixharness/reference/loading-protocol.md`；引用出处 = 仓库**顶层** `/mnt/disk01/workspaces/worksummary/helixharness/README.md:37`，该行定义 Harness 约束「what to read, which boundaries to respect and what evidence is required to finish」并链接 loading protocol——非嵌套的 `helixharness/helixharness/README.md`，后者 :37 是项目结构节、无此引文）。收益面：dev loop 长会话的 token 成本 + 跨 persona 的重复注入。

### 5.2 Effects 队列细化：先落账再执行

现状 side_effects 是**事后对账**（`Projects/项目推进流水线/scripts/graph_pa_contracts.py:128,131`——side_effects 列表 + idempotency_key exactly-once reconcile；`scripts/loop_state.py:47-48,116-124` reconcile 状态机，UNKNOWN 阻断不创 PR）。方向：外向副作用（commit/push/PR）升级**先落账再执行**（write-ahead intent），对账从「发现不一致」前移到「执行前可审计」。durable 特化容错层是 graph 边界纯函数库的定位不变（`openspec/changes/langgraph-workflow-upgrade/proposal.md` 护城河条款）。

### 5.3 lessons/repo_facts 引用图化与修剪

两轨记忆（`openspec/specs/cross-prd-learning-memory/` lessons + add-rsi-repo-facts-loop repo_facts）条目增长后，注入面需要引用图（谁引用谁/命中率/年龄）与修剪策略（长期不被引用/被证伪的条目降权或下架）——对齐上游「spec 用完打分回写（命中率/误导率 → 修订/下架）」形态（`docs/superpowers/specs/2026-10-20-productize-pa-design.md` §10 E3 远期机制化的记忆面续篇）。

## 6. 阶段间依赖与推进纪律

- **P0 → P1**：watchdog 的六层一致性面是事件驱动化的前置——订阅型 Trigger 引入新配置层（webhook secret/token），不先有漂移可见性就是给六层加第七层。
- **P1 → P2**：观察者的 would-do 提案需要事件面（CI 红就是 CI 事件）+ 订阅直通的注入通道。
- **P2 → P3**：影子命中率数据积累足够（提案样本量）才谈引用图修剪的判据。
- **推进纪律**：每阶段收口即归档 openspec change（spec 同步进 main）。跨阶段度量采集（§8）从 P0 即开始，起点**条件式**：日报骨架（§2.4）若立项则以日报为采集面；若不立项，注意力预算从既有落盘面（确认环记录 / events.jsonl / journal）聚合起步——两种起点都不 retroactive 伪造历史，采集缺口显式登记。

## 7. 不采纳清单（显式立碑）

学 dsh rejected-concepts 的方法论：**否决项显式立碑**，防止后续「顺手引入」。以下五条是本路线的边界，推翻需新设计文档 + 显式论证：

1. **不引入常驻 LLM Router 接管调度**——hub 的确定性队列是单写者核心（`hub/queue_engine.py:4-8`：tick 单循环串行、pick→start→mark_running 无并发窗口），调度链不进模型。LLM 只允许出现在交互面（§4.2 Router）与语义筛（§3.1 Slow Path）。
2. **不迁移 DSH 宿主 / 不引入新 Agent 运行时**——pa 的 claude runtime（persona 子进程 + SDK dev loop）+ LangGraph 编排是既定栈；换宿主是重写不是演进。
3. **不做 dev loop 异步 start/stop 工具改造**——dsh 的工具面异步化适配其宿主，pa 的 dev loop 语义（journal/handoff/证据门）不依赖它。
4. **不追 BYOM 多设备叙事**——pa 的部署拓扑（hub 服务器 + runner 池）已定（`docs/superpowers/specs/2026-10-20-pa-server-deployment-design.md`），多设备 bring-your-own-model 与 per-user 凭据面（F9）正交且无需求。
5. **dsh 文章实验数据不当依据**——其评测为合成负载 + 部分机制在原型中未实现；本路线只取**原型实况层**（代码可核的机制形态），实验数字一律不引用。

## 8. 度量

度量是 P2 放量的唯一依据（§0 决策 4），四项：

| 度量 | 定义 | 用途 |
|---|---|---|
| **人的注意力预算** | 每周人工介入次数（triage/修复/确认/拍板，从 watchdog 日报 + 确认环记录采集） | **P2 北极星**：主动 Agent 的存在意义就是把这条曲线压下去 |
| **端到端时延分布** | **信号→入队**与**入队→PR** 两段分开采（非均值）——事件驱动化消除的正是「信号→入队」段的 fetch 拉取等待（等下一 cron 周期才扫）；**每决定分摊成本**（dsh Router batch 思想：一轮人的注意力批量消化多少决定） | P1 验收：「信号→入队」从 cron 周期级压到分钟级（§1 北极星同口径） |
| **影子命中率** | observe 的 would-do 提案被采纳率 | **主动 Agent 放量唯一依据**：命中率不达标不切 auto（§4.1） |
| **docs-drift 门** | openspec spec 与代码面的同步断言进 `quality.sh`（`Projects/项目推进流水线/scripts/quality.sh:4-7` 现三项：compileall/pytest/ruff；dsh docs.spec.ts 思想——文档即被测面） | 全阶段：spec 漂移在 CI 可见，防 F1 式文档腐化复发 |

## 9. 风险登记

| 风险 | 缓解 |
|---|---|
| watchdog 六层读取自身引入新故障面（读 L5 需 master key 等敏感面） | 观察面纪律沿用（daemon 既有「绝不崩执行面」+ stale_check fail-open 先例）；敏感层只读非秘密形态（模型名/URL host），报告面**值绝不入面**（键名+长度差，change 显式条款） |
| image-sync 自动 save\|load 占满 LAN/磁盘（2.24GB/次） | 传输超时缺省 900s（env 可配）+ (runner, tag) 在飞/排队双重去重 + 开工复测在场 no-op；跨机房/上量再议 registry（change D2 显式拒绝为默认通道） |
| auto-triage 重验烧 token | 只重验一次（迷你版投机执行），仍 stale 即降级人工；不做无限重试 |
| 订阅型 Trigger 引入外部攻击面（webhook 伪造） | Fast Path 机械筛复用三态语义（UNKNOWN fail-closed）；secret 校验属确定性配置错不归 UNKNOWN |
| 订阅直通破坏「hub 不产语义」边界 | 通道语义 = 透传人话到 run 上下文面，判定仍归 run 内闸门；接缝边界在 change 期按 events.jsonl 对称纪律细化 |
| 影子模式日报被当噪音忽略 | 日报形态受「只显示需要人的地方」约束（§2.4），命中率采集与日报同源 |
| 四阶段并行诱惑导致全线半成品 | 推进纪律（§6）：P0 两 change 收口归档后才开 P1 立项 |

---

*本设计 2026-11-01 产生：主会话完成 dsh-superagent-prototype 研究 + 2026-10-31 两起真机事故复盘，四阶段/不采纳清单/度量与用户对齐后成文。P0 两 change（add-config-drift-watchdog / add-runner-image-sync）以此为立项依据，目录同日建成且 `openspec validate --strict` 通过（rc=0）；P1–P3 各项立项时另起 change proposal，本文档的章节为其 Why 上游。*
