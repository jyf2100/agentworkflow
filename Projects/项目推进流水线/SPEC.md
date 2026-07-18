---
date: 2026-07-15
type: project
tags:
  - project
  - agent
  - automation
  - spec
  - 项目推进
  - full-auto
ai-first: true
uid: project-advance-pipeline
status: spec-draft
---

> **For future Claude：** 这是一条「全自动研发流水线」的设计 Spec。拓扑分两面：**控制面** = vault 大脑（承载公众号/deep-research 内容、生成 PRD、汇聚报告），**目标面** = roc 自有的 workspace 代码仓（如 cc-web-control）。每天一次，控制面从内容里抽**技术信号**→ 翻译成**项目专属 PRD** → **过质量闸** → **只把 PRD + 信息源投递给白名单目标仓** → **各目标仓自带的开发 agent 自治跑完整 dev loop（需求分析→设计→开发→review→回归→开 PR）** → dispatch 独立验证 → 回控制面汇总成待人 review 的报告。全程无人，人只看报告决定合哪些 PR。
>
> 四条铁律：① vault 是 PRD+报告中心，**永不作 dev 目标**；② **控制面不污染目标仓**（ADR-0001）；③ **项目自治**——pipeline 只投递 PRD+信息源，不画 boundaries、不规定开发流程（ADR-0002）；④ **PRD 投递前必过质量闸**（`pa-prd-critic`，只判有据+可执行，不管价值）。
>
> 本文件是**设计稿（spec-draft）**，尚未实现；术语见 `CONTEXT.md`，架构决定见 `docs/adr/`。实现入口在文末「v1 任务拆解」，需用户明确说"开建"才动工。关联 [[cc-web-control]]、[[project-workbench-agents]]、[[feedback-workspace-external-offlimits]]、[[wechat-articles]]、[[deep-research]]。

---

## 0. 一句话定义

> 每天一次，控制面编排器串起 3 个 headless persona（`pa-radar` → `pa-prd` → `pa-prd-critic`(闸)）+ 2 个机械 stage（dispatch → report），用 **consumed-marker + 文件名日期**取今日新内容 → 抽**技术信号** → 翻译成**项目专属 PRD** → **过质量闸** → **只投递 PRD + 信息源**给白名单目标仓 → **各目标仓自带的开发 agent `dev`**（dispatch 建 worktree 后触发仓内 SDK 脚本 `dev-agent.* --prd … --source …`，并行）**自治跑完整 dev loop 并自己开 PR** → dispatch 独立验证（failing 留作 PR 标红）→ report 机械汇总成 `项目推进/项目推进报告_YYYYMMDD.md`。全程无人，人看报告决定合哪些 PR。

---

## 1. 背景与目标

**背景**：用户（roc，AI 技术团队管理者，Claude Code 重度用户）已有大量爬取内容（`Knowledge/微信/` 2300+ 篇公众号）与多个自有 workspace 代码仓。但「内容里发现的可落地技术 → 落到目标仓代码」这一段是纯手工，且希望把"挖信号 + 投递需求"全自动化。

**两面拓扑**（核心）：
- **控制面 = vault 大脑**：本地笔记仓，无 remote。承载雷达内容源、PRD 生成（含质量闸）、报告汇聚。**永不作 dev 目标**。
- **目标面 = workspace 仓**：roc 自有代码仓（github.com/jyf2100/ 或 okwinds/，有 GitHub remote），位于 `/Users/roc/workspace/`。开发 agent 在其上自治开发、开 PR。v1 仅 cc-web-control。

**目标**：构建一条**全自动、无人值守**的研发流水线，控制面把内容洞察（技术信号）自动翻成各目标仓的 PRD、过闸、投递，目标仓自治实现，人只在报告层 review/合并。

**非目标（v1 明确不做）**：
- 不自动合并到主干、不自动部署
- 不自动发起 deep-research（v1 只消费 wechat-articles；deep-research 输入待产物位置钉死再接）
- 不管成本上限（仅留 wall-clock 防挂死）
- 不覆盖团队生产仓（白名单制，v1 只含自有低风险仓）
- **vault 永不作 dev 目标**
- **pipeline 不规定项目的开发流程 / boundaries / 代码质量**（项目自治，ADR-0002）

---

## 2. 核心决策记录（grilling + grill-with-docs 共识）

| # | 决策点 | 结论 | 理由 |
|---|---|---|---|
| 1 | 核心职责 | 自治研发流水线 | 用户要"替我推进开发任务" |
| 2 | 自治程度 | 全程无人，只看报告 | 用户明确要最大自治 |
| 3 | 项目范围 | 白名单逐个圈 + 每项目 profile | 控 blast radius |
| 4 | 落点 | 分支隔离 + 开 PR，永不碰主干 | full-auto 唯一安全落点 |
| 5 | "贴合"判定 | 独立 agent 分析 | 每段一个专家 agent |
| 6 | 运行形态 | 编排器串联 headless persona（非 subagent） | 复用工作台模式 |
| 7 | 文章来源 | 公众号（+ deep-research 待接） | 少而精 |
| 8 | 频率 | 每天批量一次 | 鲜活 |
| 9 | "pr" 含义 | = PRD（产品需求），非 pull request | "发给项目去执行"前的产物 |
| 10 | 大脑角色 | vault = PRD 生成 + 报告汇聚中心，永不作 dev 目标 | vault 无 remote、是笔记仓 |
| 11 | dev 目标 | roc 自有 workspace 仓，非 vault 子目录 | vault 子目录无 remote、不能开真 PR |
| 12 | v1 目标 | cc-web-control 单仓（有 `npm test` + GitHub remote） | 用户选定 |
| 13 | radar 产物 | 抽「技术信号」非「需求」；pa-prd 把信号翻译成 PRD | 公众号是趋势综述、不是 backlog |
| 14 | 开发 agent 归属 | 归属目标面：每仓**自带 SDK 脚本** `scripts/dev-agent.*`（query() 跑 dev loop）+ CLAUDE.md（ADR-0003；原 markdown persona 已弃） | 仓自带上下文、随仓走；"项目仓走 SDK 流程" |
| 15 | 代码安全机制 | 项目自治 + 独立验证闸 + run log（GitNexus 已移除） | GitNexus 只索引 vault，对目标仓 N/A |
| 16 | 隔离原则 | 控制面 ⟂ 目标面：运行态绝不写进目标仓（ADR-0001） | "当前仓库不应该污染目标仓" |
| 17 | 投递契约 / 项目自治 | pipeline 只投递 PRD + 信息源；项目自治跑完整 dev loop，自管 boundaries/质量/流程（ADR-0002） | "允许项目自治…自己分析实现需求设计-开发-review-回归" |
| 18 | 独立验证闸 | dispatch 收 PR 后独立跑项目测试，红→failing（方案 A） | full-auto 机械兜底；验证非干预 |
| **19** | **PRD 质量闸** | **对抗 persona `pa-prd-critic` 把关"有据+可执行"（不管价值）；未过 drop，borderline 1 次修订（方案 B）** | full-auto 下机械保证不投喂垃圾；价值留给人 review PR |
| **20** | **PR mechanics** | **开发 agent 自己开 PR；验证 failing 留作 GitHub PR 标红**；分支 `auto/<YYYYMMDD>-<slug>`；验证读 `package.json scripts.test` | 项目自治（dev loop 含 PR）；报告是主界面，红 PR 保留失败信号 |
| **21** | **今日新判定** | **控制面 consumed-marker + 文件名 YYYYMMDD 前缀**（方案 A）；读 分类总结/深度解读/单篇，跳 meta | 爬取 sporadic；幂等由 run 锁 + GitHub 去重保证（ADR-0004），marker 仅快路径 |
| R1 | PR 量 | 每项目同时 ≤ 2 个未关闭 PR（人工 + 流水线） | 限制评审积压，`max_prs_in_flight: 2`；不是每日配额 |
| R2 | 成本 | 先不管 | 仅留 wall-clock 防挂死 |
| R3 | deep-research | v1 只读 wechat；deep-research 待产物位置钉死 | v1 不自动发起 |
| **22** | **混合拓扑** | **控制面 3 persona = CLI markdown + dispatch/report = 机械 stage；目标面 dev agent = 仓自带 SDK 脚本（ADR-0003、ADR-0005）** | 项目仓走 SDK 重机器；控制面有语义的三段用 CLI，确定性段用机械逻辑 |
| **23** | **dev agent permissionMode** | **`acceptEdits` + 定向 allowedTools + PreToolUse hook + worktree** | dev 机非容器，bypassPermissions 不符；acceptEdits 摩擦≈bypass 且 fail-safe |
| **24** | **刹车强制位置** | **(b) GitHub branch protection 平台兜底 + dispatch wall-clock + 独立验证；信 repo budget/turns/hook** | 不可逆的平台兜最强；budget/turns 属自治 + R2 缓做 |
| **25** | **dispatch↔dev-agent 契约** | **dispatch 建 worktree→触发 `dev-agent.* --prd --source`（只读）+ wall-clock；脚本自开 PR + 输出 JSON；dispatch 独立 `npm test` 比对** | PRD 不落盘；dev 自开 PR；独立验证不重开 PR |
| **26** | **dispatch↔dev 真源与部分失败恢复** | **GitHub 为 PR 真源；脚本返回后（含 kill/崩/自中止）dispatch 对账：有 PR 录入、无 PR 有 commit 补开"⏸ 中断"PR、无 commit 删孤儿分支** | full-auto 下无"报告漏报、GitHub 实有"的静默漂移（ADR-0004） |
| **27** | **dev loop 可观测 + 仓内主动刹车**（具体化 #24 R2「repo budget/turns/hook 缓做」；2026-07-16 grill 共识修订） | **监控**：补 `for await` 内 user msg 处理（拿 `tool_result`）+ **per-turn** 落盘 `state/runs/<branch>-<stamp>.jsonl`，每行 = `tool_use`（name + input 截断 500 字符）+ `diff_stat` + test 红/绿（配对 Bash `npm test` 的 tool_result 文本识别 `passing`/`failed`）；`.gitignore` 放行 `/state/`。**无进展刹车（主力，口径修订）**：`verifiedRed` = 当前最近一次 test 红（动态、修绿回退）后，连续 **N=3 轮无写类 tool_use**（`Edit`/`Write`/`MultiEdit`；`Bash` 不算）→ break + 标 `stalled`。**口径从原「working tree 无文件改动」改为「无写类 tool_use」**——避「读代码修 bug」（连续多轮 Read/Grep 不改文件）被误杀。**预算刹车（降级兜底、不依赖）**：接 SDK 原生 `maxBudgetUsd:10` + 自检打印 `result.total_cost_usd`（0/null 标「未生效，仅 maxTurns 兜底」）；**双重不确定**（LiteLLM/glm-5.2 usage 透传与否 + 单价准与否）致触发时机不可预测，故只当保险、主力靠无进展刹车。**stalled 出口**：dev-agent 吐 `{ok:false, stalled:true, branch, run_log}` + **exit 12**，**不 commit/不开 PR**（半成品可能不可用、靠 run_log 留痕）；`run_daily.py dispatch_one` 读 stalled 进 rec，报告 `dev_killed`（超时）/`stalled`（主动刹车）分开标，`reconcile_pr` 走「无 commit 删分支」并标 `status=stalled`（区分 `orphan_deleted`） | 纯 wall-clock 是被动兜底；无进展刹车信号明确可靠 = 主力主动刹车，预算因双重不确定降为兜底；监控一鱼两吃（调试 + 喂刹车）。口径改无写类 tool_use 避读代码误杀。对照《全自动软件开发方案指南》(2026-07-16) 方案三——原则可借鉴，其 Python SDK 代码 API（sub_agents / HookMatcher）存疑、不抄（`max_budget_usd` 官方核实为真、已采用，见上）。实现见 PRD `20260716_dev-agent-observability-brakes.md` |
| **28** | **dev agent skill scope** | **project-only**（`settingSources: ["project"]` 不加 `"user"`；`allowedTools` 默认不含 `"Skill"`）。若某 PRD 需 skill 辅助 → 把 skill 放**目标仓 `.claude/skills/`**（+ `.gitignore` 放行 `skills/`，类比 hooks 放行）+ `allowedTools` 加 `"Skill"`。**不开 user scope** | 开 `"user"` 会把 roc 全局 `~/.claude/`（hooks/CLAUDE.md/settings）带进 dev agent，污染隔离、可能冲 scope-bash；project-only 保 dev agent 干净（只受目标仓约束）。代价：全局 skill（superpowers/gstack/ecc:*）不可用，需时手动投递进仓。据 [promptfoo](https://www.promptfoo.dev/docs/providers/claude-agent-sdk/)：setting_sources 从 project/user scope 发现 skill；默认不加载 |
| **29** | **dev agent 子代理分工** | 放行 `Agent` 工具（`allowedTools` 加 `"Agent"`）+ prompt 分工纪律：主 agent 自判何时分工——多关注点 / 估摸 30+ turn 才 spawn 子代理，否则单干。子代理单一职责（写测试/实现/审查）、产出回 parent 整合、parent 独守 git（子代理不碰 commit/push） | 复杂 PRD 子代理分工省主 agent context、提质量；简单 PRD 单 loop 更省、不强制。**v1 不预设 `subagents`/`AgentDefinition` 模板**——`options.subagents` 完整形态官方文档只确认 `AgentDefinition.effort`，余待核实，不抄存疑 API（对照 #27 末尾指南存疑纪律）；需预设模板时再核实官方 schema 补 |
| **30** | **Phase 4：并行 + 限量 + run 级互斥**（具体化 §6 并行 / run 锁 / R1 限量；2026-07-16 grill 共识） | **① per-project 串行**（修 `count_inflight_prs` TOCTOU）：`dispatch_one` 按 `owner_repo` 取 `threading.Lock`（size=1），同仓串行→在途 PR 数仓内恒新鲜、现有 check-then-act 一行不改即正确；跨仓仍并行（同仓不并发开多 PR 本即期望：评审连贯/避 push 竞争）。**② 并行原语**：`_run_capture` 同步 `subprocess.run`（GIL 释放）→ `ThreadPoolExecutor` + `threading.Lock`（非 asyncio/Popen）；全局上限 `--max-concurrent`（默认 4，= ThreadPool size，R1 单仓与不设上限等价）；records 写盘前按 `project+slug` 排序保 diff 稳定；per-future `future.result()` 各自捕获异常（#26 故障隔离不变）。**③ run 级互斥 `.run.lock`**（落地 ADR-0004 §4）：`main()` 起手 `O_CREAT+O_EXCL` 原子获取（避锁自身 TOCTOU），内容 PID+ISO8601 ts，包整个 main；**陈旧 = PID 失活（`os.kill(pid,0)` 抛错）或 锁龄 > `MAX_RUN_WALL=DEV_LOOP_TIMEOUT+1800`（≈90min，绑常量）**→ 自动接管（删+重建+log）；**活锁→拒绝+exit 2**，`--break-lock` 显式强拆（不复用 `--force`）；`try/finally` 全路径释放。**④ per-PRD 幂等前置闸**（兑现 ADR-0004 §4 明文、修 spec↔code 矛盾；选型 ii 纯控制面）：`dispatch_one` 投递前复刻 dev-agent.mjs slugify 算法（`scripts/dev-agent.mjs:259`）算 `devslug`，按 **slug 子串**查 `gh pr list --state all`（`headRefName` 含 devslug）+ `git ls-remote --heads origin` 的 `auto/*` 分支，命中→`skip`（已投递）、不起 dev loop（省 SDK 启动+$）。用 slug 子串而非精确 `--head <branch>`，因 dev-agent.mjs `stamp()=YYYYMMDD-HHMM`（含时分）run_daily.py 不可预测——为精确查去改 dev-agent.mjs（选型 i）会拖一个 cc-web-control PR（L15 用户终端）、把控制面改动拖成跨面，已否；R1 slug=date+24 字描述够特异、子串误命中可忽略（已对真 PR #31 验 hit=True）。⚠️残留耦合：slugify 算法须与 dev-agent.mjs 同步漂移。并行下崩溃重跑概率更高，前置闸堵重复投递。**⑤ `log()` 线程安全**：加模块级 `threading.Lock`（多 worker 并发 print 不交错乱码） | 顺序 for 无并行；`count_inflight_prs` 顺序下无竞态、并行下 check-then-act 被同仓多 PRD 击穿→per-project 锁最小修复保 R1 限量诚实。ThreadPool 是 sync subprocess 唯一自然选型（GIL 释放，免 asyncio 重写/Popen 手搓）。run-lock 陈旧规则补全 ADR-0004 §4 只写「PID+陈旧检测」未给的 concrete rule：PID 失活主信号 + 锁龄兜底抓「活着但挂死」；活锁 fail-safe 防 state/ 产物撞车。实现见 PRD `docs/phase4-parallel-rate-limit.md` |
| **31** | **在途 PR 查询失败的处置**（具体化 R1；2026-07-16 grill 共识） | **fail closed**：GitHub 查询异常、超时、返回非法或非成功时，在途 PR 数为「未知」，本次 PRD 跳过投递并记录「在途 PR 数未知」；不得将失败解释为 0 | R1 是无人值守的容量闸；未知放行会在 GitHub 不可用时绕过上限。跳过后可由下次日跑自然重试 |
| **32** | **临时跳过的 PRD 仅手动重试**（具体化断点续跑；2026-07-16 grill 共识修订） | 已过质量闸但因临时条件未投递的 PRD 进入**待投递**状态；后续日跑**不得自动重试**，只有 roc 明确要求时才重新投递。一次明确重试包含同项目多份 PRD 时，按过闸时间 FIFO（旧→新）；重试前仍重新检查准入、在途上限和幂等 | 保留状态避免静默丢失；禁止自动重试避免旧 PRD 在无人确认时反复消耗资源或意外占用后续名额 |
| **33** | **consumed-marker 推进时机**（2026-07-17 grill 共识） | marker 只表示「信息源已被 radar 成功消费」。radar 成功产出并持久化 `candidates_<stamp>.json` 后立即推进 marker，不等待 PRD / critic / dispatch / report 完成；下游失败基于已落盘 state 手动 `--from-stage` 续跑 | 避免因下游故障重复读取、重复分析同一信息源；同时候选文件已是可续跑的持久产物 |

---

## 3. 系统架构

### 3.1 流水线全景

```
            ┌───────── cron (daily) ─────────┐
            ▼
   ┌──────────────────┐  candidates.json   ┌──────────────┐  PRD+信息源  ┌──────────────────┐  过闸PRD  ┌────────────────┐
   │  pa-radar        │ ──────────────────▶│  pa-prd      │ ───────────▶│  pa-prd-critic   │ ────────▶│  pa-dispatch   │
   │  信号雷达        │                     │  信号→PRD    │             │  质量闸(对抗)    │  未过drop │  投递+独立验证 │
   │  (控制面)        │                     │  (控制面)    │             │  (控制面)        │           │  (控制面)      │
   └──────────────────┘                     └──────────────┘             └──────────────────┘           └───────┬────────┘
   输入: 今日新(consumed-marker                                                                                       │ cd <目标仓>
   + 文件名YYYYMMDD) +                                                                                               │ && claude --agent dev -p "<PRD+信息源>"
   全部 profile.match_surface                                                                                        ▼
                                                                                            ┌─────────────────────────────────────────────┐
                                                                                            │  开发 agent (dev) × N —— 各在目标仓内自治   │
                                                                                            │  <仓>/scripts/dev-agent.mjs             │
                                                                                            │  需求分析→设计→开发→review→回归→自己开 PR  │
                                                                                            └─────────────────────┬───────────────────────┘
                                                                                                                  │ PR
                                                                                                                  ▼
                                                                          dispatch 读 package.json scripts.test，独立 worktree checkout 跑 → 红则标红
                                                                                                                  │
                                                                                                                  ▼
                                                                                                          ┌────────────────┐
                                                                                                          │  stage_report  │
                                                                                                          │  聚合报告      │
                                                                                                          └───────┬────────┘
                                                                                                                  ▼
                                                                                              项目推进/项目推进报告_YYYYMMDD.md + 日报指针
```

### 3.2 运行形态（关键）

- **编排器**：一个脚本（建议 Python，与现有 `weekly_report.py` 等同栈），按顺序串起控制面各段。
- **控制面运行形态**：有语义判断的 radar / prd / critic 是独立 headless persona，用 `claude -p --agent pa-xxx "<input>"` 非交互调用；dispatch / report 是编排器内确定性机械 stage，不立 persona（ADR-0005）。**vault 不含开发 agent**。
- **混合拓扑（控制面 CLI + 机械 stage / 目标面 SDK，ADR-0003、ADR-0005）**：控制面 3 persona = CLI markdown（`claude -p --agent`），dispatch/report = Python 机械逻辑；**目标面 dev agent = 仓自带 SDK 脚本**（`claude-agent-sdk` 的 `query()` 跑 dev loop）。dispatch 对每个目标仓建 worktree（`auto/<YYYYMMDD>-<slug>`）→ `cd <worktree> && node <仓>/scripts/dev-agent.* --prd <绝对路径> --source <路径>`（PRD/信息源只读、不落盘），并行起 N 个，外包 wall-clock。
- **段间传结构化产物**（控制面 JSON/MD），不靠会话上下文；投递给开发 agent 的 PRD+信息源经 `-p` 输入或控制面路径（只读），**绝不复制进目标仓**。
- **独立验证闸**：开发 agent 开完 PR 后，dispatch 读目标仓 `package.json` 的 `scripts.test`，在 **PR 分支的全新 checkout worktree + `npm ci` 干净依赖**里重跑（非 dev 热乎树），红则标 failing。**盲区**：抓不到"测试篡改"（dev 改既有测试让失败用例过→干净重跑仍绿）——靠报告"📝 改测试文件"标记暴露给人 review（§8）。
- **不是 subagent**：编排器是脚本/cron。

> ⚠️ **关键假设（Phase-0 必须先验证）**：① `claude -p --agent <name>` 加载 persona + 非交互返回结构化结果（控制面各段）；② 从目标仓 cwd 内 `claude --agent dev -p "<PRD+信息源>"` 加载该仓自带 dev persona。不通则降级 **Claude Agent SDK**，架构不变。

### 3.3 目录与产物布局（提案）

```
控制面（vault / 流水线 home）：
  .claude/agents/pa-radar.md, pa-prd.md, pa-prd-critic.md   # 控制面语义 persona（dispatch/report 为机械 stage；无 pa-dev）
  .project-auto/                              # 流水线 home（机器状态，不入 Obsidian 可见树）
    profiles/<project>.yaml                   # 白名单 + profile（无 boundaries/test_cmd）
    sources.yaml                             # pa-radar 输入源集合（source set：[{name,root,content_glob,marker}]，v1 仅 wechat）
    state/
      consumed_wechat_date                    # 今日新判定的 consumed-marker（最后处理到的 YYYYMMDD）
      candidates_YYYYMMDD.json                # pa-radar 产出（技术信号 + source_path）
      prd_gate_YYYYMMDD.json                  # pa-prd-critic 过闸记录（过/drop/修订）
      dispatch_YYYYMMDD.json                  # pa-dispatch 投递 + 验证记录
      runs/<project>/YYYYMMDD.log             # 开发 agent 运行日志（编排器捕获）
      prd/<project>/YYYYMMDD_<slug>.md        # 过闸 PRD 副本（+ 信息源指针，供 dev agent 只读）
  项目推进/                                    # 人可读产物（入 vault 可见树）
    项目推进报告_YYYYMMDD.md                   # 每日报告

目标面（各 workspace 仓，如 /Users/roc/workspace/cc-web-control）：
  scripts/dev-agent.mjs                       # 该仓自带的开发 agent = SDK 脚本（query() 跑 dev loop；语言跟仓栈；准入时 roc 人工写进）
  CLAUDE.md                                   # 该仓自治的 scope/质量/review 流程 + dev persona（准入时人工写进）
  package.json                                # 须含 claude-agent-sdk 依赖 + scripts.test（独立验证用）
  .worktrees/                                 # dispatch 建 worktree 的目录（dev loop + 独立验证复用）
  <代码改动>                                   # 开发 agent 的 PR 只含这部分
  ※ GitHub branch protection（禁直推 main / 禁 force-push / 必须 PR）准入时配——平台级机械兜底（ADR-0003）
```

> 隔离铁律（ADR-0001）：控制面 `.project-auto/`、PRD 副本、run log **只在控制面**；目标仓只多出它自己的 `.claude/agents/dev.md` + CLAUDE.md（仓自己的开发结构，不算污染）+ 开发 agent 的代码 PR。

> 注：新建 `项目推进/` 文件夹时，需在 `_CLAUDE.md` 的 Folder Map 补一条约定（开建任务）。

---

## 4. 各 Persona 定义草案

> 沿用 [[project-workbench-agents]]（[[wkw]]/[[wkr]]/[[wkp]]）的 persona 结构。控制面 radar / prd / critic 为 **headless 批量** persona，dispatch / report 为机械 stage；开发 agent `dev` 归各目标仓、自治完整 dev loop。

### 4.1 `pa-radar`（信号雷达，控制面）

- **角色**：从**今日新**内容里抽**技术信号**（项目无关的趋势/技术/能力），与白名单项目 `match_surface` 比对、生成**候选**。公众号是趋势综述非 backlog，故只抽信号、不硬编需求。
- **今日新判定**（方案 A）：读 `state/consumed_wechat_date`，取 `Knowledge/微信/YYYY/MM/` 下文件名 YYYYMMDD 前缀 > marker 的；只读内容类（`_分类总结` / `_深度解读` / 单篇深度如 `YYYYMMDD_<主题>.md`），跳 meta（`_审校报告`/`_URL参考列表`/`_文章清单`）。radar 成功写入 `candidates_<stamp>.json` 后立即 bump marker；marker 只表示 radar 已消费信息源，不等待下游段，下游失败按 state 手动续跑（**date-marker 仅快路径、不扛幂等**——幂等由 §6 run 锁 + dispatch 时 GitHub 去重保证，见 ADR-0004）。> 注：爬取 sporadic，故按"所有 > marker"取而非"昨天"。
- **输入**：今日新内容（经 **source set 配置** `.project-auto/sources.yaml`：`[{name, root, content_glob, marker}]`，v1 仅 1 条 = wechat；**不硬编码 wechat 路径**，日后接 deep-research 加一条即可、radar 不重写）+ 全部 `profile.match_surface` + 各项目未关闭 PR/PRD 清单（去重用）。
- **处理**：抽信号 → 与各项目 `match_surface` 比对打分 → 去重（命中未关闭 PR/PRD 则丢弃）→ 相关度 ≥ 阈值才保留。
- **输出**：`state/candidates_YYYYMMDD.json` = `[{signal, project, relevance, source_path, dedup_note}]`（`source_path` 即投递的信息源）。
- **工具**：Read, Grep, Glob（只读 vault）。
- **硬约束**：只读 vault；无项目命中或低分一律丢弃；每个信号标注 `source_path`；不编造信号。
- **禁区**：不生成 PRD；不碰任何代码仓。

### 4.2 `pa-prd`（信号→PRD，控制面）

- **角色**：把「一条候选信号 × 一个项目 profile」**翻译**成项目专属 PRD（含验收标准），作为投递给项目的**起点提案**。
- **输入**：`candidates_YYYYMMDD.json` + 项目 profile + 信息源原文。
- **处理**：为每个候选生成 PRD：背景 / 目标 / 范围 / **验收标准（可验证，能变成测试）** / 参考资料；附信息源指针。
- **输出**：`state/prd/<project>/YYYYMMDD_<slug>.md`（待过闸）。
- **工具**：Read, Write（限 `.project-auto/state/prd/`）。
- **硬约束**：必须含可验证验收标准（供验证闸 + 项目判 done）；外部事实标时效 + 内联来源 URL；事实与分析分开标注（[[feedback-extract-cite-source]]）。
- **禁区**：不动代码；PRD 绝不写进目标仓。

### 4.2b `stage_inject`（手动注入 PRD，控制面机械 stage）

- **角色**：手动入口——roc 写一份 PRD md，编排器照 pa-prd **同一契约**（落 `state/prd/` + 同 manifest 格式）注入，替 radar→prd 自动路径。下游 critic/dispatch/report **零改动**消费。
- **动机**：自动路径依赖「今日新内容→信号→candidates」；roc 已想清楚要做什么时，绕过 radar/prd 直接喂手写 PRD，省 LLM、可即时投递。
- **输入**：`--inject-prd <md 路径>`（YAML frontmatter + 正文）。frontmatter 必填 `project`（须为已知 profile，硬性）；可选 `slug`/`source_path`/`signal`，缺失则派生（标题转拼音 slug；source 缺则告警放行）。
- **处理**：校验 `project ∈ profiles`（未知即拒）→ 标题（首个 `# `）经 pypinyin 转 ASCII slug（≤24，与 `dev_slugify` 一致）→ 复制正文 + 补全 frontmatter（`date`/`round=1`/`slug`）到 `state/prd/<project>/<stamp>_<slug>.md` → 吐单行 `prd_manifest_<stamp>.json`（与 pa-prd 同格式）。stamp 若已被占（今天已有自动跑）自增 `m/m2/...`，保证 critic/dispatch 必跑（不复用旧 gate/dispatch，避 §6 重用陷阱）。
- **pypinyin 惰性 import**：cron 的 `/usr/bin/python3` 未装；inject 仅手动触发、cron 不跑，故 import 放函数体而非模块顶（不拖垮每晚 cron）。
- **输出**：`state/prd/<project>/<stamp>_<slug>.md` + `state/prd_manifest_<stamp>.json`，返回 `(manifest, actual_stamp)`。
- **工具**：Read, Write（限 `.project-auto/state/prd/`）。
- **硬约束**：同 ADR-0001——Write 仅限 `state/prd/`，绝不写目标仓；用户原文件只 copy 不挪动；语义（有据/可执行）留给 critic 闸。
- **用法**：`python3 run_daily.py --from-stage inject --to-stage dispatch --inject-prd <path>`（inject→critic→dispatch 一条龙）。

### 4.3 `pa-prd-critic`（PRD 质量闸，控制面，对抗）

- **角色**：独立对抗 persona，**专司反驳** PRD。只判"**有据 + 可执行**"，**不判价值**（价值留给人 review PR）。
- **输入**：一份待过闸 PRD + 其信息源 + 项目 profile。
- **处理**：逐条验：① 每条断言能否追溯到信息源（防编造/跑题）？② 验收标准是否具体到可变成测试？③ 是否贴合 `match_surface`？④ scope 是否单 PR 可完成？输出 过/drop + 理由。
- **输出**：`state/prd_gate_YYYYMMDD.json`（过/drop/修订记录）。
- **工具**：Read（只读 PRD + 信息源 + profile）。
- **硬约束**：**默认怀疑**，宁可 drop 不放行垃圾；borderline（仅 1 项轻微不过）给 pa-prd **1 次修订机会**，二次仍不过则 drop；不判"值不值得做"。
- **禁区**：不生成 PRD；不碰代码；不替人判价值。

### 4.4 `pa-dispatch`（投递 + 独立验证，控制面，编排器逻辑）

- **角色**：把过闸 PRD + 信息源投递给目标仓的 dev-agent 脚本；脚本开完 PR 后独立验证。**刹车架构 = 平台兜底 + 触发层 wall-clock + 独立验证（信 repo 其余）**（ADR-0003）。
- **输入**：当日新过闸 PRD；仅当 roc 明确要求重试时，才另行读取指定的待投递 PRD + 信息源 + 白名单 + 各项目 in-flight PR 计数。
- **处理**：当日新过闸 PRD 正常投递；待投递 PRD 不被日跑自动拾取。明确重试一个项目的多份 PRD 时，按过闸时间 FIFO（旧→新） → 每项投递前重新检查准入与幂等 → 校验 `admission: true` 且 `dev_agent_ready: true` 且 `in-flight < max_prs_in_flight` → 在目标仓建 worktree、切 `auto/<YYYYMMDD>-<slug>` 分支 → `cd <worktree> && node <仓>/scripts/dev-agent.* --prd <控制面PRD绝对路径> --source <信息源路径>`，外包 wall-clock 超时（PRD/信息源**只读、不落盘进仓**）→ 捕获脚本 stdout JSON → **对账（ADR-0004，GitHub 为真源）**：`gh pr list --head auto/<slug> --state all` + `git log` 查分支——有 PR 按 PR 录入（哪怕 JSON 缺失），无 PR 有 commit 则 **dispatch 补开"⏸ dev loop 中断"PR**，无 commit 删孤儿 `auto/*` 分支 → **独立验证**：读 `package.json` `scripts.test`，在 **PR 分支的全新 checkout worktree + `npm ci` 干净依赖**里重跑（**非** dev 刚工作的热乎树——后者只抓"压根没跑"，全新 checkout 才是真第二意见，能抓谎报/未提交 hack/环境差异），与脚本自报 `self_test_pass` 比对，不一致/红则标 failing。因临时条件未能启动 dev loop 的 PRD 保留待投递状态，等待 roc 后续明确重试。
- **输出**：`state/dispatch_YYYYMMDD.json`（投递 + 验证记录）+ 脚本 JSON 入 `state/runs/`。
- **工具**：Bash（git worktree + 触发脚本 + `npm test` + 只读查 GitHub PR 计数）, Read, Write（限 `.project-auto/`）。
- **硬约束**：超 `max_prs_in_flight` 不投递（登记"跳过-超额"）；在途 PR 查询失败则 **fail closed**（登记"跳过-在途 PR 数未知"，不得当作 0）；**投递前 `gh api repos/<owner>/<repo>/branches/main/protection` 实时校验，404 即拒投并记"跳过-未保护"**（protection 是平台态、可被外部改动，故运行时实查、**不引入静态 profile 字段**，与 `dev_agent_ready` 解耦）；**绝不把控制面产物写进目标仓**；failing PR 留作 GitHub PR、报告标红（不自动关）；**never-merge / never-touch-main 由 GitHub branch protection 平台兜底**，不依赖脚本。

### 4.5 开发 agent（归属目标面，每仓一个 SDK 脚本，自治完整 dev loop）

> 落点：「项目仓走 Agent SDK 流程」。dev agent **不再是 markdown persona，而是仓自带的 SDK 脚本**（ADR-0003）。控制面的 3 个语义 persona 仍是 CLI markdown，dispatch/report 为机械 stage。

- **角色**：接收 PRD + 信息源，用 `claude-agent-sdk` 的 `query()` 在目标仓 worktree 内**自治跑完整 dev loop**：需求分析 → 设计 → 开发 → review → 回归 → **自己 push + 开 PR**。
- **定义位置**：`<仓>/scripts/dev-agent.*`（语言跟仓栈；cc-web-control = `dev-agent.mjs`）+ 该仓 CLAUDE.md（dev persona + 自治 scope/质量/review）。**仓自己 owning，随仓走**（ADR-0001 原则不变，产物从 dev.md 换成脚本，不算污染）。
- **自治范围**：文件 scope、设计、代码质量、测试、review/回归流程 + **budget(`max_budget_usd`)/maxTurns/model/effort 自选**（项目自治，pipeline 不强制；文档推荐 Sonnet 5 + xhigh 作默认）。
- **permissionMode**：`acceptEdits` + 定向 `allowedTools`（`Bash(git push *)`/`Bash(gh pr create *)`/`Bash(npm test)`/Read/Edit/Write/Grep/Glob）+ **PreToolUse hook**（拦 never-merge / 碰主干 / `rm -rf` / 读密钥）。
- **输入**：dispatch 传 `--prd <abs>` + `--source <abs>`（只读）+ worktree cwd。
- **启动前置**：断言 `git config user.email` 非空（仓本地身份，见 Phase-1 准入④），空则 **fail-fast**（绝不静默用兜底身份乱提交）。
- **输出**：push 分支 + `gh pr create` 开真 PR（正文含 PRD 链接 + 信息源 + 验收标准 + `🤖 auto-generated by 项目推进流水线，待人 review`）+ stdout 一行 JSON `{pr_url, branch, success, self_test_pass, files_modified, cost_usd, turns, session_id, summary}`（**不写控制面文件进仓**）。
- **刹车强制（ADR-0003，方案 b）**：不可逆的（never-merge/never-touch-main/force-push）由 **GitHub branch protection 平台兜底**；可挂死的由 dispatch wall-clock 兜；PR 质量由 dispatch 独立验证兜；budget/turns/危险命令 hook 信仓（残险 = dev 机破坏性 shell，已被 worktree + branch protection + 自有仓限损）。
- **禁区**：不 merge；不动主干；不写控制面文件进仓。（scope 内部禁区由仓 CLAUDE.md 自定。）

### 4.6 `stage_report`（报告聚合，控制面机械 stage）

- **角色**：聚合全天产出成报告。
- **输入**：各项目 PR + 验证结果 + run log + dispatch 记录 + 质量闸 drop 清单 + radar 未匹配信号。
- **待投递可见性**：每日报告持续列出全部待投递 PRD（项目 / PRD / 首次跳过日期 / 原因）；仅展示状态，不因列入报告而自动重试。
- **输出**：`项目推进/项目推进报告_YYYYMMDD.md` + 日报一行指针。
- **投递（仅"有活"触发）**：若"✅ 待 review ≥ 1 或 🔴 failing ≥ 1"，用 **SMTP 直发**（`smtp.newland.com.cn:587` + starttls，复用既有 `邮件跟踪/小组周报/.../发送邮件.py` 的 smtplib 模式；**密码存 macOS Keychain、不进 git、不交互**）一封简讯到 `juyf@newland.com.cn`（收件人配置在 `.project-auto/config`，**不进 git**）：标题=「项目推进 YYYYMMDD｜N 待 review / M failing」，正文=报告路径 + 各 PR 链接。全绿/无产出**不投递**（免噪音）。**无 Foxmail GUI 依赖**（见 §10 SMTP 风险 + Phase-5 自测）。
- **工具**：Read, Write, **Bash（调 SMTP-send helper：smtplib 脚本，密码从 Keychain 读）**。
- **硬约束**：事实留痕可追溯；**待 review PR 单列**（含验证绿/红）；drop/异常/超限/未匹配 各自分区；不夸大成果。

### 4.7 `wka`（控制台，可选 / Phase-6）

- **角色**：交互式 workbench agent（`claude --agent wka`），非流水线本体。
- **职能**：手动触发一次 run / 查最新报告 / 管白名单（增删 profile）。
- 入口函数加到 `~/.claude/workbench-aliases.sh`。

---

## 5. 项目 Profile Schema（定稿）

存控制面 `.project-auto/profiles/<project>.yaml`。**不含 `boundaries` / `test_cmd`**——scope 与测试归项目自治（ADR-0002）；验证闸测试命令由 dispatch 从目标仓 `package.json` 自行发现。

```yaml
name: cc-web-control               # 项目标识
repo: /Users/roc/workspace/cc-web-control   # 目标仓绝对路径（workspace 仓）
type: code                         # code | doc（doc 类不进 dev loop）
admission: true                    # 白名单开关
dev_agent_ready: true              # 该仓已自带 scripts/dev-agent.* + CLAUDE.md（准入前置）
goal: "通过 Web 对话框控制本地 Claude Code（tmux 双向同步）+ hub 多机聚合看板"
tech_stack: [node, javascript, express, websocket, tmux, mcp]   # 据 package.json
current_focus: [hub 多机聚合, 配置文件体系]   # 据 README 重点，dry-run 可调
merge_policy: branch-only          # 固定：永不开主干
max_prs_in_flight: 2               # 该项目所有未关闭 PR 上限（人工 + 流水线，R1）
match_surface:                     # 喂 pa-radar 做"贴合"判断
  one_liner: "用浏览器/Web 对话控制本地 Claude Code 与 tmux 会话；多机 hub 聚合看板与广播"
  keywords: [Claude Code, tmux, 远程控制, WebSocket, 浏览器控制, 对话式, hub, 多机, 看板, 批量广播]
```

**字段说明**：`match_surface` = 信号核心；`max_prs_in_flight` = 在途 PR 上限（所有未关闭 PR 数，人工与流水线创建的都统计；非每日配额）；`dev_agent_ready` = 准入前置；`goal`/`tech_stack`/`current_focus` 供 pa-prd 翻译参考。**scope/质量/测试/review 流程不在 profile，归项目自治**。

---

## 6. 编排器与调度

- **substrate**：Python 脚本（`scripts/project_advance/run_daily.py`）。Phase-0 验证 `claude -p --agent` 可用则用它；否则 Agent SDK。
- **调度**：cron，每天凌晨一次（如 `17 3 * * *`，避开整点）。v1 先手动触发，稳了接 cron。**注**：报告经 SMTP 直发（**不依赖 Foxmail GUI 会话**），cron 无需 Mac 常驻登录——只要 run host 能连 `smtp.newland.com.cn:587`（见 §10）。
- **并行**：dispatch 为每个被投递目标仓并行起开发 agent（各在各自 cwd，互不干扰；总并发受 `max_prs_in_flight` 约束）。
- **run 级互斥**：编排器启动写 `state/.run.lock`（PID + 时间戳；启动检测陈旧并接管），cron 与 wka 不得并发跑同一日（ADR-0004）。
- **手动注入**：`--from-stage inject --inject-prd <md>` 把手写 PRD 直接注入（替 radar→prd，见 §4.2b）；与 cron 自动跑共用同一 run 锁（不并发），**不进 cron**——cron 仍走 radar→prd 全自动。
- **质量闸 + 独立验证**：PRD 过闸才投递；PR 开出后各跑独立 `npm test`，红标 failing。
- **错误策略**：单项目失败不阻断其他；失败进 run log + 报告"异常"区；编排器崩溃按 `state/` 产物断点续跑；临时跳过的过闸 PRD 保留为待投递，日跑不自动重试，只在 roc 明确要求时重试。
- **防挂死**：每个 headless 调用包 wall-clock 超时（如单项目 dev loop ≤ 30 min），超时 kill + 记日志。**非成本控制。**

---

## 7. 安全护栏总览（full-auto 的机械刹车）

分两层（ADR-0002）：**投递层**（pipeline 不可谈）+ **项目层**（自治）。

| 投递层护栏 | 机制 |
|---|---|
| 永不自动合并 / 永不碰主干 | **GitHub branch protection 平台兜底**（禁直推 main / 禁 force-push / 必须 PR）+ 隔离 worktree 分支 `auto/<YYYYMMDD>-<slug>`（ADR-0003 方案 b） |
| 白名单准入 | 只 `admission: true` 且 `dev_agent_ready: true` 的目标仓被投递 |
| PR 限量 | 每项目同时最多 `max_prs_in_flight: 2` 个未关闭 PR（人工 + 流水线；R1，非每日配额） |
| PRD 质量闸 | `pa-prd-critic` 把关有据+可执行，未过 drop（borderline 1 次修订） |
| 独立验证闸 | dispatch 收 PR 后在**全新 checkout + `npm ci`** 独立跑 `npm test`（读 package.json），红→failing 标红。**盲区**：抓不到测试篡改（改测试让用例过），靠报告"📝 改测试文件"标记交人 review |
| 防挂死 | wall-clock 超时 abort（R2） |
| 改动留痕 | 全程 `state/` 产物 + run log |
| 控制面/目标面隔离 | 运行态绝不写进目标仓（ADR-0001） |

| 项目层（自治，仓 CLAUDE.md 自管） | 说明 |
|---|---|
| 文件 scope / boundaries | 仓自己定，pipeline 不规定 |
| 设计 / 代码质量 / review / 回归 | 仓自己定 |

> 代码安全（GitNexus 已移除）：项目自治（仓自己的 review/回归）+ 投递层质量闸 & 独立验证闸 + 人最终 review PR。

---

## 8. 报告格式（`项目推进/项目推进报告_YYYYMMDD.md`）

```markdown
# 项目推进报告 2026-07-15

## 概览
- 今日新内容：N 篇｜技术信号：S｜候选：M｜过闸 PRD：G（drop D）｜投递目标仓：K｜产出 PR：P｜验证 failing：F｜失败/超时：T

## ✅ 待你 review 合并的 PR（验证绿）
| 目标仓 | PR | 分支 | PRD |

> 📝 若某 PR 触碰了既有 `test/*` 文件，本行追加 **📝 改测试文件**——独立验证抓不到测试篡改（§7 盲区），review 请重点看测试 diff。

## 🔴 验证 failing（项目自报绿但独立测试红，慎合）
| 目标仓 | PR | 失败测试 | 说明 |

## 🗑 PRD 未过质量闸（drop）
| 项目 | PRD | critic 理由 |

## ⚠️ 异常 / 超时 / 跳过
- [目标仓] 原因（超 max_prs_in_flight / wall-clock 超时 / 开发 agent 异常）

## ⏳ 待投递 PRD（仅 roc 明确要求时重试）
| 项目 | PRD | 首次跳过日期 | 原因 |

## 📭 未匹配信号（无目标仓命中）

## 📊 各目标仓详情
### cc-web-control
- 今日 PRD / PR / run log 链接
```

日报（`日报/work-daily-YYYY-MM-DD.md`）加一行指针指向本报告。

**投递**：有活（待 review ≥1 或 failing ≥1）时，`stage_report` 经 SMTP 直发简讯到 `juyf@newland.com.cn`（标题=N 待 review / M failing + 报告链接），全绿不发。详见 §4.6 + §10（SMTP 凭据/连通性）。

---

## 9. v1 任务拆解（Phase 0–6）

> ⚠️ 以下为"开建"才执行。每 Phase 有 Definition of Done。

- **Phase 0｜可行性 spike**（先行）
  - 验证控制面 `claude -p --agent <name>` headless 加载 markdown persona + 非交互返回结构化结果（目标仓 SDK 已由用户验证可用）；确定控制面 substrate（CLI flag 链 vs SDK，倾向 CLI）。
  - DoD：headless 跑通一个控制面 persona、拿到结构化输出；确认 `claude -p` 暴露的 per-stage 旋钮（max-turns / permission-mode / allowed-tools）够用。
- **Phase 1｜profile + 目标仓准入**
  - 落 profile schema + cc-web-control profile（无 boundaries/test_cmd）。
  - **准入动作**：① 在 `/Users/roc/workspace/cc-web-control` 写入 `scripts/dev-agent.mjs`（SDK 脚本：query() dev loop，acceptEdits + 定向 allowedTools + PreToolUse hook）+ 该仓 CLAUDE.md（dev persona + 自治 scope/质量/review）；② `package.json` 加 `claude-agent-sdk` 依赖；③ **配 GitHub branch protection**（禁直推 main / 禁 force-push / 必须 PR）；④ **配仓本地 git commit 身份**：`git -C <repo> config user.name "roc (项目推进流水线)"` + `user.email 9880962+jyf2100@users.noreply.github.com`——实测 global 与 repo 均为空（当前会直接 commit 失败）；带 id 的 noreply 可获 GitHub **verified + 归属 jyf2100**；**只配仓本地、不进 global**，随仓走（ADR-0003 仓 owning 原则）。
  - DoD：profile 过校验；cc-web-control 自带可跑的 dev-agent.mjs；branch protection 生效；**`pa-dispatch` 的 branch-protection 运行时校验已实现并通过（投递未保护仓 → 拒投 + 记日志）**；commit 身份就绪 → **仓本地 commit 身份已设**（name=`roc (项目推进流水线)` / email=roc-noreply）**且 `dev-agent.mjs` 启动断言通过**。
- **Phase 2｜前半段（今日新→信号→PRD→过闸）**
  - 建 `pa-radar`（含 consumed-marker 今日新判定）+ `pa-prd` + `pa-prd-critic` + 编排器前半段；dry-run 看产出 + 过闸质量。
  - DoD：跑一次产出 candidates + 过闸 PRD，人工抽检信噪比 + 闸的 drop 是否合理。
- **Phase 3｜单目标仓端到端**
  - 建 `pa-dispatch`；从 cc-web-control cwd 端到端跑通：投递 PRD+信息源 → dev agent 自治 dev loop → 自己开 PR → dispatch 读 `package.json` 独立 `npm test` 验证（failing 标红）。
  - DoD：一个真实过闸 PRD → 一个分支 PR（独立验证绿），主干未动，控制面产物未泄漏进目标仓。
- **Phase 4｜并行 + 限量**
  - 多目标仓并行 + `max_prs_in_flight` 控制。
  - DoD：2 目标仓在各自所有未关闭 PR 未达上限时可并行投递；任一仓达上限后仅跳过该仓，不影响另一仓。
- **Phase 5｜报告 + cron** ✅ 已落地（2026-07-16）
  - ~~建 `pa-report`~~ + 报告格式 + 接 cron。**报告落地为编排器机械 `stage_report`（不立 pa-report persona）——纯 JSON state 聚合、零 LLM/零成本，与 dispatch 同理；决策见 ADR-0005。** SMTP-send helper = `scripts/smtp_send.py`（Keychain 取密、starttls、`--self-test`、全绿不发）已落地。
  - DoD：每日自动出报告，日报有指针（✅ `项目推进/项目推进报告_<stamp>.md` + 日报指针行，已对真 state 20260715 验 7 节渲染 + 全绿不发 + `--no-notify` 分支）；**待补验**：报告持续展示全部待投递 PRD 的项目 / PRD / 首次跳过日期 / 原因，且不自动重试；**SMTP-send helper 可靠**（✅ helper 已落地并对 `--no-notify`/全绿两分支验通；**真发自测 = 外发动作，交用户终端**：`security add-generic-password -s newland-smtp -a juyf@newland.com.cn -w` 写 Keychain → `smtp_send.py --self-test` 发一封中文自测邮件验 `smtp.newland.com.cn:587` 连通 + starttls + 非交互）。cron = `scripts/run_cron.sh`（nvm/PA_CLAUDE_BIN 包装）+ `scripts/install_cron.sh`（`17 3 * * *`，幂等去重，**用户终端装**——系统级改动 + macOS「完全磁盘访问权限」）。
- **Phase 6｜（可选）wka 控制台** ✅ 已落地（2026-07-16）
  - workbench agent + alias。`.claude/agents/wka.md`（触发/查报告/查 state/管白名单/排障 run 锁/SMTP 自测，意图→命令表）+ `~/.claude/workbench-aliases.sh` 加 `wka()`。
  - DoD：`wka` 能触发/查报告/管白名单（✅ 能力表覆盖 run_daily 各 stage、查 `项目推进报告_*`/`dispatch_*`/`prd_gate_*`、管 `profiles/*.yaml`+`sources.yaml`、`--break-lock`、SMTP 自测）。

---

## 10. 待决 / 风险登记（开建前/中持续更新）

| 项 | 状态 | 说明 |
|---|---|---|
| 控制面 `claude -p --agent` 旋钮够不够 | 待 Phase-0 验证 | 目标仓 SDK 已验证可用；控制面倾向 CLI，旋钮不够再切 SDK |
| cc-web-control 仓 CLAUDE.md 的自治 scope/质量/review 流程 | 准入时 roc 撰写 | 项目自治内容，非 pipeline 规定 |
| **deep-research 输入接入** | **待定** | deep-research 是 skill，产物落点未钉死；v1 先 wechat-only，位置明确后再接（radar 已 source-agnostic——届时只需加一条 source set，不重写） |
| 相关度阈值取值 | 待 Phase-2 调参 | dry-run 后定 |
| 质量闸 critic 的过/drop 阈值 | 待 Phase-2 调参 | dry-run 看 drop 是否合理 |
| ~~去重粒度~~ | **已决（ADR-0004）** | 去重 = dispatch 投递前 `gh pr list --head auto/* --state all` 命中（per source+project），非标题相似度/diff |
| `_CLAUDE.md` Folder Map 补 `项目推进/` | 开建任务 | 新文件夹需登记 |
| 未关闭 PR 是否积压 | 观察项 | R1 统计人工与流水线创建的全部未关闭 PR，每项目同时上限 2 个；仍嫌多则调低在途 PR 上限；若需日产量限制，另设独立配额 |
| **SMTP 直发连通/凭据** | **Phase-5 自测** | 复用既有 `邮件跟踪/小组周报/.../发送邮件.py`（smtplib + starttls + `smtp.newland.com.cn:587`，发件=收件 `juyf@newland.com.cn`，**已跑通**）；通用化为 cron 工具只需把密码从 `getpass` 交互改为 **macOS Keychain 读取**（不进 git、不交互）；**无 Foxmail GUI 依赖、无 3am 常驻登录要求**；风险=公司 SMTP 从 run host 不可达 / 凭据失效，不可达则退化为"报告落盘 + 日志告警"，不阻塞流水线；现有 foxmail-send **保留作审阅闸**（roc 要验证/改内容才发），与本自通知直发两不相干 |

> 已结案（不再悬而决）：boundaries（→项目自治，ADR-0002）、PRD 质量闸（→critic，B）、PR mechanics（→dev agent 开 PR + failing 标红）、今日新判定（→consumed-marker，A）、dev agent 形态（→仓自带 SDK 脚本，ADR-0003）、permissionMode（→acceptEdits）、刹车强制（→平台兜底 b）、dispatch↔dev 契约（→worktree + 只读路径 + JSON）、去重粒度（→GitHub 去重，ADR-0004）、报告投递（→SMTP 直发 smtp.newland.com.cn:587，仅有活触发，密码存 Keychain）。

---

## 11. 关联

- 术语表：`CONTEXT.md`；架构决定：`docs/adr/0001-vault-target-isolation.md`、`docs/adr/0002-pipeline-project-contract.md`、`docs/adr/0003-target-dev-agent-sdk.md`、`docs/adr/0004-dispatch-devagent-source-of-truth.md`
- 工作台模式与 persona 规范：[[project-workbench-agents]]、[[wkw]]、[[wkr]]、[[wkp]]
- v1 试点目标仓：[[cc-web-control]]（workspace 仓，`feedback-workspace-external-offlimits` 白名单例外）
- 输入源 skill：[[wechat-articles]]、[[deep-research]]
- 提炼规范：[[feedback-extract-cite-source]]、[[feedback-backup-before-modify]]

---

*本 Spec 由 grilling + grill-with-docs 共识于 2026-07-15 生成。修订史：pass #1 回写 7 处共识（大脑角色/dev 目标/v1 目标/信号模型/开发 agent 归属/隔离原则/GitNexus 移除）；pass #2 回写"薄投递 + 项目自治 + 独立验证闸"（ADR-0002）；pass #3 回写"PRD 质量闸(critic B) + PR mechanics + 今日新判定(consumed-marker A)"；pass #4 回写「Agent SDK grill：dev agent 仓自带 SDK 脚本(A) + acceptEdits + 刹车平台兜底(b) + dispatch↔dev 契约，控制面留 CLI（ADR-0003）」；**pass #5 回写「branch protection 运行时实查（dispatch 投递前 `gh api` 校验、不加静态字段）」+「commit 身份=仓本地 roc-noreply（选项 A），dev-agent.mjs 启动断言」+「ADR-0004：dispatch↔dev 真源=GitHub + 部分失败对账恢复（有 PR 录入/无 PR 有 commit 补开中断 PR/无 commit 删孤儿分支）」+「独立验证=PR 分支全新 checkout + `npm ci` 干净依赖（非 dev 热乎树）；承认测试篡改盲区 + 报告加 📝 改测试文件 标记交人 review」+「幂等真源=GitHub 去重 + run 锁；date-marker 降为快路径（并进 ADR-0004）」+「报告投递=本地 Foxmail GUI 直发（复用其账号免 SMTP 凭据），仅有活触发，收件 juyf@newland.com.cn；Phase-5 自测 + 新建 GUI-send helper」+「pa-radar 输入薄抽象=source set 配置（v1 仅 wechat，日后加 deep-research 不重写 radar）」**。pass #5 针对 spec↔真实仓缝隙 + 运行时失败模式追问。**pass #6 回写「报告投递反转：pass#5 原定 Foxmail GUI 真自动发 → 改 SMTP 直发（`smtp.newland.com.cn:587` + starttls + Keychain 密码）；依据=vault 内发现既有 `发送邮件.py` 已跑通，证明 SMTP 凭据已存在、'免 SMTP 凭据'优势不成立；SMTP 无 GUI 会话/3am 登录依赖、更可靠；foxmail-send 保留作审阅闸」**。状态：spec-draft。*
*下一步：用户 review 整份 spec → 说"开建" → 从 Phase 0 起执行。*
