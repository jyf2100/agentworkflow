# Design: productize-pa-template

> 真相源：`docs/superpowers/specs/2026-10-20-productize-pa-design.md`（12 节定稿，D1-D5 + E1-E3）。本文件是其在 OpenSpec change 下的实现设计——本体论角度贯穿：每层发布拓扑的身份显式声明、不变式映射、术语对齐 CONTEXT.md。

## Context

pa 当前是 roc 个人专用形态（个人路径、个人 profile、单人双 checkout 部署）。产品化目标：团队内部推广，同事获取**流水线本体**，首发主打手动 PRD 注入。已定方案 B（模板仓分离），否决 A（原仓演化——个人历史扩散）与 C（pip 抽包——工程量过重，B 是 C 的第一步）。约束：vault 内 pa 架构零变化（含 LangGraph 双轨 flag-off 现状）；`scripts/` 保持扁平模块不抽包；ruff 仅 E9+F；线性 git 历史。

## Goals / Non-Goals

**Goals:**
- vault → `pa-pipeline-template`（团队 git，版本化只读投影）→ 同事控制面仓 的三层单向发布流
- 同事零 scripts 本地改动即可跑通：clone → bootstrap → admit 目标仓 → 上传/注入 PRD → 真 PR
- 去个人化由**机械转换脚本**完成（可 diff、可审计），不靠手工改写
- 发布即门：template 内容全绿（quality.sh）+ 无个人痕迹（grep 门禁）才可打 tag

**Non-Goals:**
- 不动 run_daily.py / graph_pa.py / dev-agent.py 行为与 11 个现有 capability specs
- 不做 pip 包 / 多用户 / 鉴权 / 历史看板 / 雷达链插件化（设计 §11）
- 不建学习回流机制（E3 仅登记 + 试点人工回流）
- 不建度量系统（E2 仅试点复盘人工聚合）

## Decisions

### D0 方案 B：模板仓分离
vault 保持开发主仓；template 是发布产物而非第二个开发地。替代：A 原仓演化（个人历史/路径随 clone 扩散，升级=merge 地狱）；C pip 抽包（需把扁平 scripts 重构为包结构，违反当前约定，工程量与收益不匹配）。

### D1 本体身份：投影非真源
| 层 | 身份 | 权限 |
|---|---|---|
| vault | 开发真源 | 唯一可演化 pa 行为 |
| pa-pipeline-template | 版本化只读投影 | 只经 release 更新，tag v0.x |
| 同事控制面仓 | 投影实例 | 本地改动仅 `config/`、`profiles/`、`.project-auto/`；scripts 改动 = 分叉须回流 |

机制化（code-gate 优于 prompt-gate）：bootstrap 升级时 diff 同事仓 `scripts/`、`.claude/agents/`、`openspec/` vs template tag，漂移即警告并列 diff 摘要，不静默覆盖。回流路径声明进 template CLAUDE.md 首节。

### D2 文档分层：规约层永不 fork
规约层（`openspec/specs/`、`docs/adr/`、CONTEXT.md 术语语义）单版本随行，release 自检 diff 校验；叙事层（CLAUDE.md）允许双视角（vault=开发视角 / template=使用视角），但只能引用规约层。去 roc 化（路径、SMTP、profile generic 化、「大脑」词条范畴迁移 D3）全部由转换脚本完成，转换逻辑进 release 自检。

### D3-D5 沿设计定稿
D3 大脑范畴单例→多实例（「控制面仓，每实例一个」；vault 降格为上游真源仓+实例之一）；D4 bootstrap 环境冲突检测（同名 persona / settings 交互点 → 列出 + 默认拒绝覆盖）；D5 admit 边界（`gh api` 配 branch protection 属平台操作**可做**；写目标仓任何文件**不做**，指引文本待确认后人工应用）。

### D6 新组件的 vault 侧开发位置（本设计新增落位决策）
| 组件 | vault 位置 | 进 template 后 |
|---|---|---|
| `release_template.sh` + 转换脚本 | `Projects/项目推进流水线/release/` | 不进（发布工具只在 vault） |
| `bootstrap.sh` + helpers | `Projects/项目推进流水线/bootstrap/` | template 根 `bootstrap.sh` |
| `admit_repo.py` | `Projects/项目推进流水线/scripts/`（复用扁平模块与三态查询封装，pytest pythonpath 直接受益） | template `scripts/` 随行 |
| webapp | `Projects/项目推进流水线/webapp/`（`app.py` 单文件 + `static/`） | template `webapp/` 随行 |

理由：release/bootstrap 是发布链工具非编排器，独立目录守职责边界；admit 需要复用 `scripts/` 内 gh 三态封装，入扁平约定。FastAPI 是 webapp 独立依赖（`pyproject.toml` optional extras `webapp`），不进编排器核心依赖面。

### D7 webapp = 触发器 + 视图，进程外触发
注入经 subprocess 起 `run_daily.py --from-stage inject`（复用 `.run.lock` 互斥），不做进程内调用——故障域隔离（面板崩、流水线不受影响，fail-open）；面板不产生任何 verdict（机械/语义切分不变式）；端点只收文件不收命令；默认 bind 127.0.0.1 无鉴权（localhost 单人）。

### E1-E3 沿设计定稿
E1 通道指导进 template 产品文档（「需新测试证据才走流水线」+ 上下文准入两问）；E2 试点复盘人工聚合 journal/dispatch 一页纸；E3 学习回路缺口显式登记（本期人工回流，触发条件 ≥3 实例再机制化）。

## Risks / Trade-offs

- [同事仍就地改 scripts 分叉] → D1 本地改动检测 + 回流首节声明 + 试点验收含「零本地改动」
- [template 与 vault 规约漂移] → D2 规约层单版本 + release diff 校验（发现差异即发布失败）
- [同事 claude CLI 配置不可用] → bootstrap 前置冒烟（--version + 端点连通）
- [发布脚本自身腐化] → release 自检每次全量跑；发布动作人工触发留痕；模板内容可随时重建
- [template 定格 in-progress change 中间态造成困惑] → release note 义务（注明上游状态，如 LangGraph flag-off）
- [FastAPI 引入扩大依赖面] → optional extras 隔离，编排器核心零新依赖

## Migration Plan

纯增量变更：vault 侧新增目录/文件 + 既有文档编辑，无行为改动 → 回滚 = 删除新增目录 + revert 文档。发布流：手动跑 `release_template.sh` → 自检门 → 生成 template 工作副本 → 推团队 git 打 tag（人工操作留痕）。同事侧升级 = `git pull` + bootstrap 重跑（本地改动检测护住分叉）。

## Open Questions

- template 仓托管位置与可见性（团队 git 具体 org/repo、private？）——发布时由用户决定，不阻塞开发
- 试点同事与目标仓人选——运营决策，试点前定
