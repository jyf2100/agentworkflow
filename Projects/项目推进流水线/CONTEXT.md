# 项目推进流水线

本上下文定义「项目推进流水线」的专属术语——一条全自动研发流水线：每天从**大脑**里的爬取内容挖掘**需求**，生成 **PRD**，派发到**白名单项目**（roc 自有的 workspace 仓），在各项目上自治实现并开真 **PR**，最后回大脑汇聚报告。本文件是术语表，不含实现细节。

## 角色与拓扑

**项目 (Project)**:
roc 自有的代码仓库（github.com/jyf2100/ 或 okwinds/ 下），位于 /Users/roc/workspace/，有 GitHub remote，经白名单准入。是 [[开发 agent]] 唯一会改动、开 PR 的对象。
_Avoid_: 仓/代码库（太泛）、vault 子目录（那些不是项目）、上游 fork 作为目标。

**大脑 (Brain)**:
Obsidian vault 本身——本地仓，无 remote。承载雷达内容源、PRD 生成、报告汇聚。pa-dev 永不改动它。
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
