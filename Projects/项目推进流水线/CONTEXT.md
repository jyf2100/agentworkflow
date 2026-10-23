# 项目推进流水线

本上下文定义「项目推进流水线」的专属术语——一条全自动研发流水线：每天从**大脑**里的爬取内容挖掘**需求**，生成 **PRD**，派发到**白名单项目**（roc 自有的 workspace 仓），在各项目上自治实现并开真 **PR**，最后回大脑汇聚报告。本文件是术语表，不含实现细节。

## 角色与拓扑

**项目 (Project)**:
roc 自有的代码仓库（github.com/jyf2100/ 或 okwinds/ 下），位于 /Users/roc/workspace/，有 GitHub remote，经白名单准入。是 [[开发 agent]] 唯一会改动、开 PR 的对象。
_Avoid_: 仓/代码库（太泛）、vault 子目录（那些不是项目）、上游 fork 作为目标。

**大脑 (Brain)**:
控制面仓——承载雷达内容源、PRD 生成、报告汇聚的 Obsidian vault。范畴为**多实例**（2026-10 产品化设计 D3）：每个流水线部署有自己的控制面仓实例；本 vault（agentworkflow）是开发真源的**第一个实例**，不是唯一合法形态。dev 永不改动它。
_Avoid_: hub、中心、vault（当指枢纽角色时统一用「大脑」）。

**上游仓 (Upstream repo)**:
为参考而克隆进 workspace 的第三方仓（如 bytedance/deer-flow、HKUDS/DeepTutor）。只读，永不是 dev 目标。
_Avoid_: 参考仓、贡献目标。

**白名单 (Whitelist)**:
`admission: true` 的项目集合。只有白名单项目接收派发的 PRD。
_Avoid_: 项目列表、注册表。

**控制面 (Control Plane)**:
vault 大脑 + 流水线全部状态/产物（PRD、报告、run log、配置、worktree scratch）。一切流水线副产物只准存在于这里或中性 scratch 目录，绝不进目标仓。
_Avoid_: pipeline-home、orchestrator-state。

**目标面 (Target Plane)**:
被开发的 workspace 项目仓（如 cc-web-control）。只接收「意图代码改动的 PR」+ 仓自己的开发结构，绝不接收控制面运行态产物（见 ADR-0001）。
_Avoid_: target-repo（用「目标面」强调与控制面的隔离）。

**开发 agent (dev)**:
**控制面标准执行器**：单一 `vault/Projects/项目推进流水线/scripts/dev-agent.py`（Python + `claude-agent-sdk` `query()`）服务**所有**被控仓——源码归控制面、随 vault 走（ADR-0006 上收；修订 ADR-0003 #1/#2 的「仓自带脚本」）。被控仓**零 dev-agent 脚本**（灰度后删 `<仓>/scripts/dev-agent.{py,mjs}`）；仓特定知识（目录/测试入口/受保护路径/环境名）由各仓根 CLAUDE.md 承载（SDK `setting_sources=["project"]` 加载）。运行时仍贴目标仓：dispatch 以 `cwd=<目标仓> worktree` 调起，执行器就地操作该仓、自治跑完整 dev loop（需求→设计→开发→review→回归→自己开 PR）——**源码在 vault、执行贴目标仓**，运行时平面隔离与 ADR-0001 一致。见 ADR-0006（+ 落地状态）、ADR-0003。
_Avoid_: pa-dev（暗示 vault 侧 persona，已弃用）、markdown dev persona（已弃）、`<仓>/scripts/dev-agent.*`（仓内遗留脚本，灰度后删；`dev_agent_source` profile 字段容忍但已忽略——选源逻辑随全量切 vault 移除）。

**fail-safe 投递 (fail-safe-dispatch)**:
dispatch 准入与对账对 GitHub/Git 远程态的查询一律三态——`FOUND`（确定存在）/ `NOT_FOUND`（确定不存在）/ `UNKNOWN`（查询本身失败：超时/非零/缺凭证/坏 JSON）。**UNKNOWN 是 fail-safe 信号**：准入见之即记 `blocked_external_state` 不起 dev loop（不超额、不重复投递）；对账见之即保留分支、不创建/删除/覆盖 PR。诊断上下文经 `external_state.sanitize()` 脱敏（抹 PAT/Bearer/basic-auth）后落 state 记录与报告。模块 `scripts/external_state.py`（零依赖，cron 安全）。

**测试发布门 (verified-dev-execution)**:
dev-agent 的发布硬门——`commit`/`push`/`gh pr create` 之前，须有**新鲜绿色结构化测试证据**（`evidence.TestEvidence` + `evaluate_gate()`，无新鲜绿证据 → 不发布）。门拦即 `exit 14` 吐 `blocked_by_gate` JSON：`gate_status ∈ {test_not_run, test_failed, test_stale}`。dispatch 见之即记 `blocked_test_gate` 终态短路（不验证、不开 PR，分支保留待运维 triage）。模块 `scripts/evidence.py`（零依赖）。独立验证（dispatch 侧 `npm test`/`pytest` 比对）继续兜底抓「自报绿、实测红」。

**项目自治 (Project Autonomy)**:
pipeline 对项目的唯一契约 = 投递 [[PRD]] + [[信息源]]；之后项目用自己的 [[开发 agent]] 跑完整闭环、自管 scope 与质量。pipeline 只保留投递层机械刹车（branch-only / 永不 merge / max PRs / wall-clock / 不污染 / 独立验证闸），不画 boundaries、不规定开发流程。见 ADR-0002。
_Avoid_: boundaries（pipeline 不再对项目设此概念）、护栏（须区分"投递层刹车" vs "项目自管"）。

**PRD 质量闸 (Quality Gate)**:
[[PRD]] 投递前必过的闸，由对抗 persona `pa-prd-critic` 把关。闸只判"**有据 + 可执行**"——每条断言能追溯 [[信息源]]、验收标准可测试、贴合 `match_surface`、单 PR scope；**不判"值不值得做"**（价值留给人 review PR）。未过则 drop（borderline 给 pa-prd 1 次修订）。
_Avoid_: 审批（暗示人介入）、value-gate、review（太泛，且与项目的 dev-loop 内部 review 区分）。

**在途 PR 上限 (In-flight PR Limit)**:
一个项目同时允许存在的所有未关闭 PR 数上限，不区分人工或流水线创建；达到上限时，新 PRD 暂不投递。它限制评审积压，不是每日产量配额。
_Avoid_: 每日 PR 上限、每日配额、PR/日。

**stage 输出契约 (stage_contracts)**:
所有走 `run_persona` 的 persona 输出的机械契约层。`scripts/stage_contracts.py`（纯 stdlib）维护 `CONTRACTS[stage]` 注册表 + `validate(payload)→[Issue]`：第一版硬契约 critic（`verdict∈{pass,drop,revise}` + `prd_path`）与 prd（`prds[i].path`），其余字段 warning。`run_persona` 语法 parse 成功后校验：`error`→带诊断 `render_repair_hint` 重试一轮，`warning`→记 log 不改行为，契约层故障→fail-open 降级（不改 stage 终态，对齐 [[学习记忆]] design 决策#7）。与 [[学习记忆]]（跨 PRD 领域经验，慢回路）是两个不同对象——契约是当轮可验机械事实，无需 recurrence。
_Avoid_: schema 校验（太泛）、learning memory（对象不同）。

## 流水线产物

**技术信号 (Signal)**:
pa-radar 从一篇爬取文章里抽取的趋势 / 技术 / 能力，项目无关，尚未绑定具体项目。承认公众号是趋势综述而非 backlog，故 radar 只抽信号、不硬编需求。
_Avoid_: 需求（太强）、feature、点子、requirement。

**候选 (Candidate)**:
一条信号经 pa-radar 与某项目 match_surface 打分、去重后，判定值得翻译成 PRD 的产物。
_Avoid_: lead、idea。

**PRD**:
在大脑里由 pa-prd 把「一条候选 × 一个项目 profile」**翻译**成的项目专属需求文档，含验收标准；与 [[信息源]] 一起投递给项目。它是项目的**起点提案**，项目可基于信息源自行细化。这道翻译是流水线的第二道命门（第一道是信号）。
_Avoid_: 信号（太早）、spec、ticket、pull request。

**待投递 PRD (Pending PRD)**:
已通过 [[PRD 质量闸]]、但因临时条件尚未成功交给项目的 PRD。流水线保留它的状态，但后续日跑不自动重试；只有 roc 明确要求时才重新投递。
_Avoid_: 已跳过 PRD、失败 PRD、当日遗留。

**采集源 (Ingest)**:
pa-radar 扫描的**输入**通道——`sources.yaml` 里的一条配置（如 wechat 目录、指定 github 仓库、deep-research agent），由 fetcher 把外部内容 normalize 成 `YYYYMMDD_*.md` 落盘，radar 按 `target_projects` 路由读取。方向与「信息源」**相反**（信息源是 radar 抽出信号后、随 PRD 投递给 dev 的**输出**侧原始材料）。见 ADR-0007。
_Avoid_: source（与「信息源」撞名）、feed、输入源、内容源。

**信息源 (Source)**:
与 PRD 一起投递给项目的**原始材料**——PRD 所依据的那篇公众号/deep-research 文章或其信号片段。给项目是为了让它独立做需求分析、不只能盲信 PRD。属控制面，随 PRD 经 headless 输入投递，不写进目标仓。注意是 radar **输出**侧（与「采集源」输入侧方向相反）。
_Avoid_: 原文、reference、引用（太泛）。

**PR**:
[[开发 agent]] 在项目自己的 GitHub 仓（其自身 main）上开的真实 pull request，绝不是回上游的贡献。
_Avoid_: merge request、patch、diff、变更说明。

**学习记忆 (Learning Memory)**:
控制面在 terminal PRD 之后做的「经验沉淀 + 跨 PRD 复用」子系统。terminal 后跑 read-only SDK reflection 抽 candidate lesson → cross-PRD 等价 recurrence ≥2 后 promote → 之后相关 PRD dispatch 时注入 ≤5 条 lesson 进 dev prompt → terminal 后用 effectiveness loop 评估（followed/contradicted/unknown）→ 据此调整 confidence 与 active/retired 状态。**state 全在 ``.project-auto/state/lessons/``（candidates/events/usage JSONL + catalog projection），绝不入目标仓/commit/PR/immutable PRD**（ADR-0001 控制/目标面隔离）。**fail-open by construction**：任何 reflection/injection/catalog/effectiveness 故障都不改 PRD 结果/dispatch terminal outcome/verify verdict/publish gate。两个 disabled-by-default flag（``PA_LEARNING_SHADOW`` / ``PA_LEARNING_INJECTION``）+ profile ``learning_memory.enabled`` 项目级 canary 标记共同 gate（V1 project-only scope）。两级 rollback：关 injection 保留 shadow candidate generation；关 shadow 停 reflection（existing candidate facts inert + rebuildable）。
_Avoid_: memory、cache、knowledge base（太泛）、RAG（V1 明确 Non-Goal）。

## 编排拓扑（LangGraph 重构后引入）

> 以下术语随 `langgraph-workflow-upgrade` 重构落地引入（见 `openspec/changes/langgraph-workflow-upgrade/`）。重构前 `drop`/`triaged` 已用于 `run_daily.py`，此处为机械/语义边界精化。

**node 内回环 (intra-node loop)**:
回环的「判官」= 同一 node 的输出契约校验（机械、确定性）时，回环走节点函数体内部（普通 Python 循环），graph 只看最终 NodeOutput。例：PersonaNode 契约不合规补 repair-hint 重调 1 次；DevLoopNode 整个 dev loop（含 in-loop checkpoint / session retry / off_track break+重发，`dev-agent.py` 进程黑盒）。
_Avoid_: 把 dev loop 内部循环拆成 graph 边（违 ADR-0006 + claude runtime 零改动）。

**跨节点回环 (cross-node loop)**:
回环的「判官」= 不同语义角色（critic 判 prd / verify 判 dev）时，回环走 graph 条件边（router 读 state 轮次计数器判上限）。例：critic revise（prd↔critic）/ verify revise（dispatch↔verify）。跨节点回环节点必须幂等（靠 [[reconcile]] + idempotency_key）。
_Avoid_: 用 node 内循环表达跨语义角色的回环（掩埋拓扑、损 graph 可读性）。

**drop**:
[[PRD 质量闸]] / verify 闭环中，**对抗 persona 独占**的语义排除判决（PRD 不进 dispatch / dev 产出判死）。编排器（graph node/edge）**从不产生 drop**——遇 persona 输出残缺只产 [[triaged]]（升人工，不替判死）。守「机械层不替语义层判结论」。
**现状标注（2026-10）**：「编排器从不产 drop」是 langgraph 重构目标态，task 4.2（3 处编排器 drop 改 triaged）**未完成**——现行 `run_daily.py` stage_critic 三边界仍产 drop（缺 path / 漏吐 verdict / revise 异常；熔断终态 raise 穿透除外），`graph_pa_aggregate.py` 聚合层全镜像同语义。file:line 证据见 change `productize-pa-template` 验证笔记。
_Avoid_: 编排器硬判 drop（边界滑落，见 `orchestrator-boundary-audit-2026-08-11.md` 层 2）。

**triaged**:
编排器（机械层）的终端状态——遇 persona 输出残缺（prd 缺 path / critic 漏吐 verdict / revise 异常）或循环用尽（verify 用满 / dev off_track 连续 2 次）时，编排器**不替判死**，defer 给人 review。与 [[drop]]（persona 语义判决）严格区分：triaged = 升人工待审，drop = 已判排除。
**现状标注（2026-10）**：残缺输入路径的 triaged 化依赖 langgraph task 4.2（未完成，现仍落 drop）；循环用尽路径已生效（verify 用满 → 对账降级 `interrupted_pr` 不 drop，半成品留 review）。
_Avoid_: 把 triaged 当 drop（编排器替判死）。

**node（消歧）**:
重构后「node」指 LangGraph StateGraph 图节点（4 类配置实例：PersonaNode/DevLoopNode/MechanicalNode/GatewayNode）。**与 pa 现有「node」= Node.js/npm 项目无关**（后者见 `orchestrator-boundary-audit-2026-08-11.md`，指被控仓的技术栈）。二者仅同词，无语义关联。

## 产品化拓扑（2026-10 产品化引入）

**投影 (Projection)**:
vault 开发真源 → pa-pipeline-template 仓的版本化只读拷贝（release_template.sh：manifest 内容契约 + transform 机械转换 + 去个人化/规约层双门禁）。投影单向、可随时重建、绝无反向同步；template 的一切内容以 vault 为唯一真源。
_Avoid_: fork（含双向同步语义，投影只读）、模板仓当独立仓讨论（脱离 vault 真源谈它的内容会漂移）。

**template 实例 (Template Instance)**:
同事 clone 投影仓 + `bootstrap.sh` 实例化后的控制面仓部署——[[大脑]] 多实例范畴的实例形态。仓根 `.project-auto/` 持实例数据（profiles、sources.yaml、state），cron/SMTP 缺省关；升级 = `git pull` + 重跑 bootstrap（本地改动检测护分叉）。本 vault（agentworkflow）是开发真源 + 第一个实例。
_Avoid_: 把实例与 vault 真源混称（实例不反向改 `scripts/`）；「部署」单用（太泛——一个实例是一台机上仓+实例数据+目标仓 worktree 的整体）。

**回流 (Backport)**:
实例侧 bug/改进的唯一合法反向通道：提 issue 给 template 维护者 → vault 修复 → 下次 release 投影随行。实例本地改受检路径（`scripts/`、`.claude/agents/`、`openspec/`）即与上游分叉——bootstrap 漂移警告（不阻断），修复责任在实例。
_Avoid_: fork-and-fix（分叉后升级自担、违背单真源）、把回流当代码依赖（它是人的流程，投影内零反向机制）。

**执行镜像 (pa-executor)**:
[[投影]] 的物化运行形态——template tag 经 docker build 产出的执行容器镜像。内容唯一源 = template tag（tag 即 [[build context]]；build 脚手产 `pa-executor:<template-tag>` 并打 upstream commit / 组件版本 label）。pa-sandbox 的正式化替代（D9）：名字维度与 tag 策略正交，`:latest` 仅本机兜底、生产显式引用 tag 版本。凭据零入镜像（per-user 三值 + 模型路由运行时 env 注入）。
_Avoid_: 脱离 template tag 谈镜像内容（会漂移）、与 pa-sandbox 并称（旧名已废）、镜像层与运行时隔离语义混谈（后者归 isolated-observable-execution capability）。

**build context**:
docker build 的构建输入目录 = template tag 的 checkout（或等价导出）——投影的物化形态（D3 规范术语）。内容清单 = tag 的 git 树；混入「未跟踪未忽略」文件即内容偏离（build 脚手 git 差集校验拦截，等价导出场景 review 把守）。
_Avoid_: 与「目标仓 worktree」混淆（那是 dev-agent 运行时 clone 目标项目的目录，容器内 /workspace——[[大脑]] 的目标面投影，非构建输入）。

**执行面 (hub)**:
服务器集中式部署形态的第三范畴（阶段 2）——[[大脑]]控制面（编排+状态+persona）与目标面（被控项目仓）之外的**执行调度层**。hub/ 骨架（add-hub-skeleton 切片 1）：SQLite 队列（per-project 串行+峰值上限+重派状态机）+ F10 卷上署名 meta（写面钉死仅 meta）+ ContainerRunner 接缝（Fake/Docker 同判，镜像缺失 fail 不回退）+ 最小 API 三端点。只做机械活（排度/署名/记账）；语义活仍归 pa persona。切片 1 单用户 env 直注，身份系统是切片 2 凭据保险箱的替换面。
_Avoid_: 与控制面混称（hub 不审 PRD 不产报告——它是调度器不是大脑）；当作 pa 的一部分（hub 不 import pa，两部署物零耦合，语义漂移由 spec 把关）；「执行面」与「目标面」互换（目标面是被控项目仓；执行面是起容器的调度层）。

**投递 (Deliver)**:
PRD state ownership 的跨机交接动作（add-hub-slice-3，F11）——mac cron `--to-stage prd` 产出 PRD 后由 `deliver_prd.py` POST 至 [[执行面 (hub)]]`/deliver`，hub 落盘入队、容器内经既有 inject 桥消费。**delivered 标记**是 vault 视角 PRD 生命周期终点（`produced → delivered` 止步——critic pass/drop/revise 结果在服务器 state，vault 不知道也不需要知道）。执行链权威由 `--to-stage` 取值物理保证（mac 侧根本没有 critic 后半段可跑），非纪律保证。
_Avoid_: 与 dispatch 混称（dispatch 是控制面本机派发 dev loop 到目标仓；deliver 是跨机交接 PRD 所有权——物理分家后 vault 不再 dispatch）；「投递失败重试」当 run_daily 职责（投递器独立脚本独立退出码，崩溃窗口靠重扫重投+幂等收敛，非进程内重试）。

**deliver_key**:
投递幂等键 `(project, 文件名, sha256)`——hub 队列项 UNIQUE 列，同一 PRD 重投恰好一个队列项（exactly-once 入队）。与文件层同名同容复用构成 **db/文件双层幂等**：任一层先到，另一层补齐。
_Avoid_: 当 run 标识（它标识「一份投递内容」，不标识执行——落盘撞名异容时文件带 `-<sha8>` 后缀而 key 不变）；当去重万能键（它只管投递交接；GitHub 远端查重 already_dispatched 是 dispatch 段另一层）。

**投递台账 (deliver_ledger)**:
vault 侧 append-only 标记台账 `<state_dir>/deliver_ledger.jsonl`——delivered 标记的**唯一写面**（PRD 产出后不可变，标记绝不写进 PRD 文件）。行三态：delivered（2xx 回执后）/ unknown（UNKNOWN 计数行，非终态）/ failed（死信）。半行损坏跳过 = 该 PRD 视为未投递重投（hub 幂等兜底）。
_Avoid_: 当通用日志（它是扫描幂等的真相源，行形态受三态约束）；扫描时把 unknown 行当已投（UNKNOWN 绝不当成功）。

**死信 (dead letter) 与 error_class**:
不再自动重投的终态投递记录（ledger failed 行），`error_class` 字段分流运维动作：`config_4xx`（401/403/404/405——token 失配/URL 错/hub 未升级，高声告警 exit 2，修部署）、`data_4xx`（sha 不符/超限/文件名非法，修数据）、`unknown_exhausted`（连续 K=5 次 UNKNOWN，查网络/hub 5xx）。人工修后 `--retry` 显式重投。
_Avoid_: 把 UNKNOWN 当死信（UNKNOWN 是可恢复态：不写标记下轮重投，只有超限才转死信）；见 exit 2 就去改 PRD（config 类死信批量出现几乎必是部署/凭据错，不是数据错）。

**file-first**:
hub 落盘顺序钉死：先落盘 PRD（含 sha 校验）再持久化队列项——若 db-first，中间崩溃产生「队列项存在但 PRD 永不补写」不可恢复态。重复投递命中键时校验落盘完整（缺失/异容补写回原位），是双层幂等的文件侧执行点。
_Avoid_: 当性能优化（是崩溃窗口收敛语义：六窗口全靠重扫重投+双层幂等收敛，零协调机制）。

**通知 (Notify)**:
hub 观察面的 run 终态事件出口（add-hub-slice-4a，F14）——事件面钉死为 hub DB **自有 run 终态行**（succeeded/failed/failed_to_start），机械读行转发，零 pa 格式依赖；语义事件（critic pass/drop、PR 开出、verify verdict）与无 run 的队列放弃**不在事件面**（归后续切片事件出口）。「至多一次」由挂点结构保证（`_finish_run` 收敛点，无标记列无轮询）；fail-open（通知异常绝不阻断落库与状态机）；零持久写面（丢失兜底是读面查询）。
_Avoid_: 与 SMTP 简讯混称（那是 vault 侧 report 段动作；hub 通知只认 run 终态行）；「通知失败阻断执行」的直觉（观察面永不阻断执行面——fail-open 是钉死语义）。

**bot 通道 (notify bot)**:
与 OAuth 2.0 应用（登录身份）分离的**第二个 MM 凭据实体**（F14）——bot token 只司推递，不做登录；未配（或 MM base_url 缺）= 通道关闭零行为。收件人值形态三代分流：enqueued_by 为 26-hex mm_user_id（2b 会话入队）直发 / username（2a env 兜底）经 `users/username/{name}` 解析后发（内存缓存，解析失败可见丢）/ `vault-deliver`（机机字面量）回落 cfg.user_id 走 username 路径。频道广播仅失败类（噪音控制）。
_Avoid_: bot token 与 OAuth client_secret 混用（两实体两用途，spec 钉死分离）；把 username 解析失败当 bug（是「可见的丢」——stderr 留痕，修法是 MM 侧加 bot 联系人/核对 username）。

**陈旧检测 (stale check)**:
remote release tag vs `HUB_IMAGE_TAG_DEFAULT` 的周期比对（daemon 每 N tick，F13 推迟项）——remote 查询三态分流对齐 fail-safe 三态不变式：FOUND 参与比对（不一致 → bot 私信 + stderr，去抖一次/组合）；**config_4xx**（repo 配错 404）高声告警绝不静默；UNKNOWN（网络/5xx）不告警下轮再查。告警是唯一动作——**绝不自动拉取或重建镜像**（升级是人工决定）。
_Avoid_: 给检测加自动 `docker pull` 的「优化」（告警-only 是 spec 钉死语义）；把 404 归 UNKNOWN 静默（配置错使检测整体失效且无人知——slice-3 `_CONFIG_4XX` 同型教训）。

**面板 (Panel)**:
hub 的手写 PRD 提交入口 + 只读观察页（add-hub-slice-4b，`/panel` 群）——登录走 OAuth 会话（与 REST 读面同守卫），提交 = 落盘 `prd/<stamp>_hand-<8hex>.md` + 入队（enqueued_by = 会话 mm_user_id、initiator = 显示名）；**evidence 必填**（空则 422——无据 PRD 在闸前被拒，与流水线 critic 哲学同源）。project 清单来自 data_root 必读面扫描（profiles/ + sources.yaml，与派发 fail fast 同判据——面板可见即派发可跑）。零新依赖（f-string 拼 HTML + `_esc` 唯一转义入口）；auth 未配置整群 503。run 详情页 = journal 尾部 + 报告原文（按 stamp 对齐）纯展示零语义。
_Avoid_: 把面板当管理面（无取消/重试/改态操作——hub 操纵面钉死在 CLI）；期待文件上传（粘贴 only，multipart 会破零新依赖面）。

**事件出口 (event outlet)**:
pa → hub 的**唯一语义事件接缝**（add-hub-slice-4b）——pa 侧 `event_outlet.py` 在 critic/PR/verify 落点机械追加 `runs/<project>/<stamp>_<PRD stem>.events.jsonl`（O_APPEND+fsync 对齐 journal 写纪律，fail-open 绝不炸流水线）；hub 侧 reconcile_running 读新行经 bot 通道转发（critic drop/verify 红进频道 + 私信，其余仅私信），`(run_id, seq)` 进程内存去重、终态即清。**hub 绝不解析 pa journal 内部格式**（4a D2 边界不动）；`v≠1` 整文件跳过（不转发半懂的形态）。寻址从 queue_item.prd_path 拼stem（不用 run 目录名——hub `_slugify` 下划线→横线与 pa stem 原样天然错位）。
_Avoid_: 期待事件通知「至多一次」（进程内存去重 + hub 重启重发一轮是接受的语义——文本幂等）；把 events.jsonl 当 pa 内部状态（是单向观察面输出，容器即弃后文件留卷）。
