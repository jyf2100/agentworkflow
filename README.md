# agentworkflow

> 自动化 R&D 编排器的**控制面大脑** —— 一个 Obsidian vault，托管 **pa**（项目推进流水线）：全自动研发流水线编排器 + 控制面标准执行器。

[![pa-pipeline](https://img.shields.io/badge/pa--pipeline-v1.0.0-blue)](Projects/项目推进流水线/CHANGELOG.md)
[![tests](https://img.shields.io/badge/quality.sh-passing-brightgreen)](Projects/项目推进流水线/scripts/quality.sh)
[![python](https://img.shields.io/badge/python-%E2%89%A53.11-3776AB)](Projects/项目推进流水线/pyproject.toml)
[![style](https://img.shields.io/badge/ruff-E9%2BF-261230)](Projects/项目推进流水线/pyproject.toml)

## 这是什么

**pa**（项目推进流水线）每日自动跑一条 7 段流水线：从信号源采集技术内容 → 抽取项目无关的技术信号 → 与白名单项目匹配生成 PRD → 对抗质量审查 → 触发目标项目仓库的 AI 开发（dev-agent）→ 独立验证 → 开 PR + 发简讯。

本仓库是**控制面**：编排逻辑、所有流水线状态、persona 定义都在这里。各被控项目仓库是**目标面**（dev-agent 在其中干活）。两者严格分离（ADR-0001）——控制面代码不直接改目标面文件，目标面不依赖控制面。

## 7 段流水线

```
 fetch ─→ radar ─→ prd ─→ inject ─→ critic ─→ dispatch ─→ report
   │        │       │        │         │         │          │
   │        │       │        │         │         │          └ 落报告 + SMTP 简讯
   │        │       │        │         │         └ dev-agent 实现 + 独立验证 → 开 PR
   │        │       │        │         └ 对抗质量闸（pass / drop / revise）
   │        │       │        └ 注入学习记忆 / 反馈
   │        │       └ candidates × 项目 profile → 项目专属 PRD
   │        └ 抽技术信号 + 白名单项目打分去重
   └ 采集（GitHub / 微信 / 深研）→ 今日新内容
```

| stage | 职责 |
|---|---|
| **fetch** | 采集源（GitHub 仓 / 微信文章 / 深研）→ 今日新内容（机械活，纯 Python） |
| **radar** | 抽项目无关技术信号 + 白名单项目 `match_surface` 打分去重 → candidates |
| **prd** | candidates × 项目 profile → 项目专属 PRD（含可验证验收标准） |
| **critic** | 对抗质量闸（pa-prd-critic），pass / drop / revise + 1 次修订回环 |
| **dispatch** | 触发目标仓 `dev-agent.py`（SDK dev loop）+ 独立验证 → 开 PR |
| **report** | 落报告 + SMTP 简讯 |

**机械活**（确定性、零 LLM、纯 Python：文件发现 / 去重 / journal / reconcile）与**语义活**（headless persona：抽信号 / 翻译 PRD / 对抗审）严格切分。

## 快速开始

```bash
# 装依赖（Python ≥3.11）
cd Projects/项目推进流水线 && pip install -e ".[dev]"

# 单一质量命令（compileall + pytest + ruff，任一失败非零退出；CI 与本地共用）
cd Projects/项目推进流水线 && bash scripts/quality.sh

# 跑全流程（⚠️ 从 vault 根跑——run_daily.py 以 VAULT_ROOT 推导路径）
python3 Projects/项目推进流水线/scripts/run_daily.py
python3 Projects/项目推进流水线/scripts/run_daily.py --limit 2        # dry-run 封顶今日新内容
python3 Projects/项目推进流水线/scripts/run_daily.py --from-stage prd  # 断点续跑（复用已有 state）
```

cron 每日 03:17 经 [`run_cron.sh`](Projects/项目推进流水线/scripts/run_cron.sh) 包装触发全流程。

## 核心设计

| 设计 | 说明 |
|---|---|
| **控制面 / 目标面分离**（ADR-0001） | 本 vault = 控制面（编排 + 状态 + persona）；被控项目仓 = 目标面。控制面代码不直接改目标面文件。 |
| **机械活 vs 语义活** | 确定性零 LLM 的 Python（文件发现 / 去重 / journal / reconcile）vs headless persona（抽信号 / 翻译 PRD / 对抗审）。 |
| **7 个 headless persona** | `.claude/agents/pa-*.md`，经 `claude --agent` 链式调用；stdout 是信封 JSON（两层解析）。 |
| **控制面标准执行器**（ADR-0006） | `dev-agent.py` 驱动所有目标仓的唯一执行器，目标仓 worktree 内经 `claude-agent-sdk` 跑 dev loop。 |
| **fail-safe 分发** | 三态远程查询（FOUND / NOT_FOUND / **UNKNOWN**），UNKNOWN = fail-safe 信号绝不当成功。 |
| **验证开发执行** | commit / push / PR 前须有新的绿色测试证据；无新绿则阻断。 |
| **durable runtime** | journal-driven 调度：崩溃恢复（fail-closed 不静默跳过）+ exactly-once 副本对账 + 会话感知重试。 |
| **stage 输出契约层** | `stage_contracts.py` 横切 persona 输出校验 + 诊断重试；critic 段止血消灭整晚 abort。 |

## 文档

| 文档 | 内容 |
|---|---|
| [CLAUDE.md](CLAUDE.md) | 仓库全貌 + 命令 + 架构（Claude Code 工作指南） |
| [CONTEXT.md](Projects/项目推进流水线/CONTEXT.md) | pa 术语表 |
| [SPEC.md](Projects/项目推进流水线/SPEC.md) | pa 规约 |
| [RUNBOOK.md](Projects/项目推进流水线/RUNBOOK.md) | 运维 / 崩溃恢复手册 |
| [CHANGELOG.md](Projects/项目推进流水线/CHANGELOG.md) | 版本记录 |
| [openspec/specs/](openspec/specs/) | capability spec（规约即设计） |
| [docs/reviews/](docs/reviews/) | 评审记录（理解决策「为什么」） |
| [docs/superpowers/](docs/superpowers/) | 设计文档 + plans |

## 目录结构

```
.
├── CLAUDE.md                      # 仓库指南（命令 + 架构）
├── Projects/
│   └── 项目推进流水线/             # pa —— 唯一代码区
│       ├── scripts/               # run_daily.py / dev-agent.py / durable runtime / 测试
│       ├── CONTEXT.md / SPEC.md / RUNBOOK.md / CHANGELOG.md
│       └── pyproject.toml         # pa-pipeline v1.0.0
├── openspec/                      # 规约即设计（specs + changes + 评审）
├── docs/                          # 评审记录（reviews/）+ 设计文档（superpowers/）
├── .claude/agents/                # 7 个 headless persona 定义（pa-*.md）
├── .github/workflows/             # canary CI（真实 SDK 回归网）
├── 项目推进/                       # 流水线产出的项目报告
└── 日报/                           # 工作日报
```

> `.project-auto/`（流水线本地状态）与 `Knowledge/`（雷达语料）在 `.gitignore` 中，**不入仓**。

---

**pa-pipeline v1.0.0** · Python ≥3.11 · [CHANGELOG](Projects/项目推进流水线/CHANGELOG.md)
