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
每个白名单目标仓**自带**的开发 agent = 仓内的 **SDK 脚本**（`<目标仓>/scripts/dev-agent.*` + 仓自己的 CLAUDE.md），归属目标面、随仓走。用 `claude-agent-sdk` 的 `query()` 接收 dispatch 投递的 PRD + [[信息源]]，**自治跑完整 dev loop**（需求分析 → 设计 → 开发 → review → 回归 → 自己开 PR）。文件范围/设计/质量/测试/review 流程 + budget/turns/model 全归仓自管（[[项目自治]]），pipeline 不规定。见 ADR-0003。
_Avoid_: pa-dev（暗示 vault 侧 persona，已弃用）、markdown dev persona（已弃，换 SDK 脚本）。

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
