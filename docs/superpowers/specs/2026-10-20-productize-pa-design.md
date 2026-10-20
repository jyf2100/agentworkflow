---
date: 2026-10-20
type: design
tags:
  - design
  - productize
  - pa
  - template-release
uid: productize-pa-template-design
status: approved-draft
ai-first: true
---

# 产品化设计：pa-pipeline-template（模板仓分离）

> 把 pa（项目推进流水线）从 roc 个人专用流水线产品化为团队可复用资产。方案 B：**模板仓分离**——vault 保持开发主仓，经发布脚本生成团队 git 上的 `pa-pipeline-template` 仓；同事 clone template + bootstrap 初始化各自控制面仓。
>
> 本设计经本体论审查修正（D1–D5，见 §0 与各节标注）；2026-10-20 探索轮（对照千人规模同题落地实践）补入三项：任务通道指导（§5）、试点价值度量（§9）、组织学习回路缺口登记（§10）。前置审核见本会话 2026-10-20 本体论审核报告（findings F1–F5）。

## 0. 澄清结论（决策记录）

| # | 决策点 | 结论 |
|---|---|---|
| 1 | 受众 | 团队内部推广（同事对自己的仓跑各自实例） |
| 2 | 获取形态 | 同事个人获取的是流水线本体（非托管服务） |
| 3 | 入口场景 | 首发主打手动 PRD 注入（inject→critic→dispatch→PR）；信号雷达链（fetch/radar）为进阶，不在首发热点路径 |
| 4 | 交互形态 | CLI + 极简 Web 面板 |
| 5 | 路径方案 | 方案 B 模板仓分离（否决 A 原仓演化——个人历史扩散+升级地狱；否决 C pip 抽包——工程量过重，B 是 C 的第一步） |

**核心论点**：pa 的 fail-safe 机制（三态查询、测试发布门、journal 恢复、run 锁互斥）已是产品级；产品化做的是**包装与去个人化**，不是重写。

## 1. 本体与真理源策略（D1+D2 修正，发布流的身份声明）

三层拓扑中每层的本体身份**必须显式声明**，防止 template 演化成平行真源（重演 SPEC.md/CONTEXT.md 分叉漂移）：

| 层 | 本体身份 | 权限语义 |
|---|---|---|
| vault（开发主仓） | **开发真源** | 唯一可演化 pa 行为的位置 |
| pa-pipeline-template（团队 git） | **版本化只读投影**（下游快照） | 只经 release 脚本更新，打 tag（v0.x） |
| 同事控制面仓 | **投影实例** | 本地改动**仅允许** `config/`、`profiles/`、`.project-auto/`；`scripts/` 改动 = 分叉，须回流 vault |

- **贡献回流路径**：同事发现 bug → 上报 → vault 修复 → 下次 release。template 版 CLAUDE.md 首节声明此约定。
- **约定机制化**（code-gate 优于 prompt-gate）：`bootstrap.sh` 与升级脚本内置**本地改动检测**——diff 同事仓 `scripts/`、`.claude/agents/`、`openspec/` vs template tag，检出漂移即警告并列出 diff 摘要，不静默覆盖。
- **文档分层策略**：
  - **规约层**（`openspec/specs/`、`docs/adr/`、CONTEXT.md 术语语义）：**单一版本**，vault 维护，release 自动携带，永不 fork；
  - **叙事层**（CLAUDE.md）：允许双视角——vault 版 = 开发视角、template 版 = 使用视角，但只能引用规约层，不得自创行为声明；
  - 去 roc 化由**转换脚本**完成（机械、可 diff、可审计），转换逻辑进 release 自检，不靠手工改写。

## 2. 总体架构（三层单向流）

```
vault（开发主仓，pa 架构不动）
   │  release_template.sh（抽取 + 转换 + 自检 + 打 tag）
   ▼
pa-pipeline-template（团队 git 仓 = 产品本体，只读投影）
   │  同事 clone + bash bootstrap.sh
   ▼
同事控制面仓（每人一个实例：config/profiles/白名单/可选 cron/webapp）
   │  dispatch（dev-agent.py 在目标仓 worktree 内跑 dev loop）
   ▼
同事自己的目标仓（白名单准入）
```

控制面/目标面隔离（ADR-0001）不受影响：三层均为控制面内部发布拓扑；目标仓仍只接收 PRD 意图 + 平台配置。

## 3. template 仓内容（发布单元与文档分策略）

| 路径 | 内容 | 处理方式 | 文档分层 |
|---|---|---|---|
| `scripts/` | 编排器 + durable runtime 全量 | 原样复制（扁平结构不动，1961 测试随行） | 规约层随行 |
| `.claude/agents/` | 8 个 persona（含 pa-progress） | 原样 | 规约层随行 |
| `openspec/specs/` | 11 个 capability spec | 原样 | 规约层 |
| `docs/adr/` | ADR-0001~0007 | 原样 | 规约层 |
| `CONTEXT.md` | 术语表 | 转换脚本去 roc 化；「大脑」词条范畴迁移（见 §4） | 规约层（单版本+转换） |
| `CLAUDE.md` | 产品视角使用文档 | **新写**（安装/配置/FAQ/回流约定首节） | 叙事层·使用视角 |
| `RUNBOOK.md` | 运维 + 部署拓扑节 | 修订（F3 修复随行） | 叙事层·使用视角 |
| `config/` | generic 演示 profile、sources.yaml 空模板、通知开关样例 | 新写 | — |
| `webapp/` | 极简面板（§6） | 新增 | — |
| `bootstrap.sh` + `admit_repo.py` | 初始化 + 目标仓准入检查器（§5） | 新增 | — |

**不进 template**：SPEC.md（历史设计稿，F2 修复后留 vault 标注 superseded）、`docs/reviews/`、个人报告与评审历史、`Knowledge/`（本就不在 git）。

## 4. 去个人化清单（转换脚本职责）

- `profiles/`：示例项目改 generic 演示 profile；
- SMTP（`smtp.newland.com.cn`/Keychain）→ env 驱动可选配置，**缺省关闭通知**（报告落盘即止）；
- 路径硬编码审计：`/Users/roc`、`worksummary` 等——release 自检 grep 门禁，命中即发布失败；
- 模型层：**架构不变**——persona/dev loop 均经同事自己的 claude CLI，模型配置天然解耦；文档写明前置「你有可用的 claude CLI」；
- cron 变为**可选项**（手动注入场景不需要每日跑）；
- **术语范畴迁移**（D3 修正）：「大脑」从 roc 单例改为「控制面仓（每实例一个）」，vault 降格为「上游真源仓 + 实例之一」；顺带修正「无 remote」过时声明（改为「不依赖自身 remote 作流水线真源」）。

## 5. 同事上手动线（含 D4 环境检测 + D5 admit 边界）

```bash
git clone <template> && bash bootstrap.sh     # 建 config、装依赖、检测已有环境、可选装 cron
python admit_repo.py <同事的目标仓路径>        # 准入检查 + 修复指引
python -m webapp.app                          # 面板；或直接 CLI 注入第一份 PRD
```

**bootstrap 环境检测**（D4 修正）：同事机大概率已有自己的 `.claude/`（agents/settings/hooks）。bootstrap 必须：逐个检测同名 persona 文件冲突 → 列出、默认拒绝覆盖；检测 settings 交互点（hooks/env）→ 提示。不静默合并。

**admit_repo.py 边界**（D5 修正，预声明防边界滑落）：
- **可做**：经 `gh api` 配置 branch protection（平台操作，与 dispatch 运行时实查对称）；检查测试入口（`scripts.test`）、git commit 身份、仓 CLAUDE.md 存在性；
- **不做**：直接写目标仓任何文件——CLAUDE.md/scripts 由同事手写，或由 admit 生成待确认的指引/补丁文本；
- 依据：ADR-0001 控制面不写目标面；admit 不得成为新通道。

**任务通道指导**（E1，template 产品文档必备节——叙事层零机制）：

流水线的净收益 = PR + 独立验证 + 测试证据 + journal 追溯。通道标准一句话：**改动需要新的绿色测试证据才值得发 PR 的，走流水线；不需要测试的（文案、配置、单行修复），直接用 claude CLI**。配套上下文准入门槛：同事往 `config/profiles/` 加规范前自问「这条在什么条件下注入？预期命中率多少？」——答不上来的不加，加进去就是噪声。
（依据：千人规模实践证实无通道分流的 Agent 全流程会被简单任务拖垮（又慢又烧 token，用户用脚投票），且「能一眼判断的标准比描述性定义管用」。）

## 6. Web 面板（极简，刻意收窄）

- **技术栈**：FastAPI 单文件 + 一页静态 HTML；零构建链、零 node 依赖（面板机只需 Python）；
- **只做三件事**：① 上传 PRD md → 触发 `run_daily.py --from-stage inject` 子进程（走**已有 `.run.lock` 互斥**）；② 读 `dispatch_*.json` 展示当日状态；③ PR 列表 + 报告链接；
- **本体定位**：state 的只读视图 + 注入触发器——不产生任何 verdict（机械/语义切分不变式），崩溃不影响流水线（fail-open）；
- **安全边界**：默认 bind `127.0.0.1`、无鉴权（localhost 单人）、触发端点只收文件不收命令；
- **不做**（YAGNI）：登录、多用户、历史图表、在线编辑 PRD、雷达链管理。

## 7. 发布前置：文档诚实性修复（F1/F2/F3/F5）

文档即产品，修复纳入发布门槛（在 vault 完成、随 release 携带）：
- **F1**：CONTEXT.md `drop`/`triaged` 词条加现状标注（critic 三边界仍产 drop，graph tasks 4.2 未完成）；
- **F2**：SPEC.md 历史化（frontmatter 改 `superseded`，留 vault 不进 template）；
- **F3**：RUNBOOK/CLAUDE.md 加部署拓扑节（本机双 checkout、cron 真身在 vault、路径真相）；
- **F5**：CLAUDE.md persona 表补第 8 个（pa-progress）。

## 8. 数据流与错误处理

同事上传 PRD → inject 落盘 → critic 闸（revise 1 次回环）→ dispatch 准入（白名单 / branch protection 三态 / 在途上限；UNKNOWN 一律 block）→ worktree + dev loop → 测试门（无新绿不发布，exit 14 短路）→ PR → 独立验证 → report。

**零新错误处理逻辑**——全部复用现有 fail-safe 机制；面板是触发器 + 视图，其故障域与流水线隔离。

## 9. 测试与发布节奏

- **发布自检**：`release_template.sh` 在 template 副本里跑 `quality.sh`（compileall + pytest + ruff）全绿 + 去个人化 grep 门禁通过，才允许打 tag；
- **bootstrap 冒烟**：干净目录初始化 → 机械段 dry-run 走通 → 本地改动检测对「篡改过的 scripts」能告警；
- **webapp**：pytest 单测（状态解析 / 注入触发 / 锁互斥行为）；
- **admit_repo**：对 mock 仓（无 protection / 无 test script / 身份缺失）三态检查单测；
- **试点验收（成功标准，工程可行性）**：1 位真实同事 + 1 个真实目标仓 + 1 份手写 PRD → 产出 1 个真 PR，且同事仓零 scripts 本地改动；
- **试点复盘（价值证据，E2——零新机制）**：试点结束时人工从既有 journal / `dispatch_*.json` / obs 聚合一页纸——每 PRD 的 revise 轮次、critic drop/revise 率、verify verdict 分布、PR 合入后 30 天 revert/rework 情况、同事一句话体感。工程验收只证明跑得通，这条回答「同事用 pa 比直接用 claude CLI 好在哪」——不建设度量系统（YAGNI 边界不变，纯人工聚合）；
- **升级节奏**：vault 改进 → 手动 release → template tag（v0.x）→ 同事升级 = `git pull` + bootstrap 重跑（含本地改动检测）；
- **release note 义务**：每个 template 版本须注明所截取的上游状态（如「LangGraph 双轨 flag-off，graph cutover 上游演进中」）——template 会定格 in-progress change 的中间态，不注明则同事对照 vault 文档时会产生「为什么我这里没有 graph_pa」的困惑（§7 F3 部署拓扑修复的变体）。

## 10. 已知缺口登记（远期演化，非本期范围）

- **组织学习回路缺口（E3）**：template 分发把 vault 的学习机制复制成 N 台互不通信的单机——同事实例产生的 lesson、调优后的 profile、扩充的 whitelist 均留本地。后果：同事 A 踩过的坑同事 B 重踩一遍；generic profile 停留在发布日假设，不随实例的真实使用进化。
  - **本期处置（人工回流）**：试点期同事把高价值 lesson / profile 调优以 issue 或消息贴回 vault，维护者判断是否吸收进 generic profile 与文档；
  - **远期机制化（触发条件：≥3 实例且人工回流成为瓶颈）**：lesson 上行通道 + 使用反馈聚合（对齐上游「spec 用完打分回写」形态：命中率/误导率 → 修订/下架）；
  - **显式登记的理由**：千人规模同题实践把「使用数据回流规范库」列为核心机制之一；本设计选择去中心化实例拓扑（D1），代价就是这条回路天然断开——显式登记，防止沉默缺口演化为下一代文档/产品漂移（F1 教训）。

## 11. 非目标（YAGNI）

- 不做 pip 包 / `pa init`（C 方案远期演化，待需求坐实）；
- 不做多用户 / 鉴权 / 历史看板；
- 不做雷达链（fetch/radar）的团队级源插件化——进阶能力，需求出现再设计；
- 不动 vault 内 pa 架构（含 LangGraph 双轨现状——产品化与 graph cutover 正交并行）。

## 12. 风险登记

| 风险 | 缓解 |
|---|---|
| 同事仍就地改 scripts 产生分叉 | 本地改动检测 + 回流路径首节声明 + 试点验收标准含此项 |
| template 与 vault 规约漂移 | 规约层永不 fork；release 自检 diff 校验 |
| 同事 claude CLI 配置不可用 | bootstrap 前置检查（claude --version / 端点连通冒烟） |
| 面板触发与手动 CLI 并发 | 复用 .run.lock，天然互斥 |
| 发布脚本自身腐化 | release 自检进 CI 可选；发布动作人工触发留痕 |

---

*本设计由 brainstorming 流程于 2026-10-20 产生：4 轮澄清（受众/形态/入口/交互）→ 三方案对比选 B → 8 节设计 → 本体论审查 5 修正（D1 真理源声明、D2 文档分层、D3 大脑多实例、D4 环境检测、D5 admit 边界）→ 用户批准 → 探索轮 3 补充（E1 任务通道指导 §5、E2 试点价值度量 §9、E3 组织学习回路缺口登记 §10，对照千人规模同题实践交叉审视）→ 用户确认「都加」。实现计划见后续 writing-plans 产物。*
