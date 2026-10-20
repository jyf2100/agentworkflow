# productize-pa-template

## Why

pa（项目推进流水线）的 fail-safe 机制（三态查询、测试发布门、journal 恢复、run 锁互斥）已是产品级，但它是 roc 个人专用形态：个人路径硬编码、个人 profile、单人部署拓扑。同事要使用只能整个复制 vault——个人历史扩散 + 升级地狱。产品化做的是**包装与去个人化**，不是重写（设计定稿：`docs/superpowers/specs/2026-10-20-productize-pa-design.md`，方案 B 模板仓分离，含本体论审查修正 D1-D5 与探索轮补充 E1-E3）。

## What Changes

- 新增 **release 发布链**：`release_template.sh` 从 vault 抽取 + 转换（去 roc 化）+ 自检（quality.sh 全绿 + 去个人化 grep 门禁）→ 生成团队 git 上的 `pa-pipeline-template` 仓（版本化只读投影，打 tag v0.x）；
- 新增 **bootstrap.sh**：同事 clone → 初始化各自控制面仓（建 config、装依赖、检测已有 `.claude/` 环境冲突不静默合并、可选 cron、claude CLI 前置冒烟）；升级 = `git pull` + bootstrap 重跑（含本地改动检测，防 scripts 分叉）；
- 新增 **admit_repo.py**：目标仓准入检查器（branch protection 三态、测试入口、commit 身份；经 `gh api` 配置 protection 属平台操作；**不写目标仓任何文件**——ADR-0001 边界预声明）；
- 新增 **webapp/**（极简面板）：FastAPI 单文件 + 一页静态 HTML，localhost-only，只做三件事（上传 PRD 触发 `--from-stage inject`、当日状态视图、PR 列表）；复用 `.run.lock` 互斥；fail-open（面板崩溃不影响流水线）；不产生任何 verdict（机械/语义切分不变式）；
- 新增 template **产品文档**：使用视角 CLAUDE.md（含 E1 任务通道指导：需新测试证据才走流水线）、RUNBOOK 部署拓扑节；
- **发布前置文档诚实性修复**（F1/F2/F3/F5，在 vault 完成）：CONTEXT.md drop/triaged 词条现状标注、SPEC.md 历史化 superseded、RUNBOOK/CLAUDE.md 部署拓扑节（本机双 checkout 真相）、persona 表补第 8 个；
- **不动**现有 `scripts/` 行为、不引入新依赖面到编排器（webapp 依赖独立于 pa 核心依赖）。

## Capabilities

### New Capabilities

- `template-release`: vault → template 的版本化只读投影发布链——本体身份（vault=开发真源 / template=只读投影 / 同事仓=投影实例）、文档分层（规约层永不 fork、叙事层双视角）、发布门（quality + 去个人化 grep + release note 注明上游中间态）、模板内容契约（含通道指导节）
- `template-bootstrap`: 同事实例化与升级——环境冲突检测（已有 persona/settings 默认拒绝覆盖）、本地改动检测（scripts/agents/openspec 漂移警告不静默覆盖）、claude CLI 前置检查、可选项（cron/通知）缺省关闭
- `repo-admission`: 目标仓准入边界——三态检查（FOUND/NOT_FOUND/UNKNOWN 沿用 fail-safe 语义）、`gh api` 平台操作白名单、控制面不写目标面文件的不变式
- `prd-injection-console`: 手动 PRD 注入入口——webapp 与 CLI 共享注入路径、`.run.lock` 互斥复用、只读状态视图、fail-open 故障隔离、无 verdict 产出

### Modified Capabilities

（无——本变更不修改任何现有 capability 的 requirement；vault 内 pa 架构零变化，LangGraph 双轨现状原样。）

## Impact

- **新增开发位置**（vault 侧真源，均在 `Projects/项目推进流水线/` 内）：`release_template.sh` + 转换脚本、`bootstrap/`、`scripts/admit_repo.py`（或并列位置，design 定）、`webapp/`；随 `scripts/` 扁平模块约定与 ruff E9+F 口径
- **文档变更**：CONTEXT.md（drop/triaged 词条 + 大脑范畴多实例迁移）、SPEC.md（superseded）、RUNBOOK.md（部署拓扑节）、CLAUDE.md（persona 表）；SPEC.md/docs/reviews/ 等历史件**不进 template**
- **外部系统**：团队 git（template 仓创建，人工触发）；同事机（Python ≥3.11 + 可用 claude CLI 为前置）
- **零影响面**：run_daily.py / graph_pa.py / dev-agent.py 行为、11 个现有 capability specs、cron 真身（vault 内不动）
- **测试**：webapp/admit pytest 单测、bootstrap 冒烟（干净目录 + 篡改检测）、release 自检在 template 副本跑 quality.sh；试点验收 = 1 真同事 + 1 真仓 + 1 手写 PRD → 1 真 PR 且零 scripts 本地改动
