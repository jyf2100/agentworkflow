# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 这个仓库是什么

这不是一个普通应用仓库，而是 **控制面大脑**（Obsidian vault），同时托管一个自动化的 R&D 编排器。严格的角色划分：

- **vault 根**：大脑（笔记、报告、日报、所有流水线**状态**）。除 `Projects/项目推进流水线/` 外几乎没有可执行代码。
- **`Projects/项目推进流水线/`**（下称 **pa**）：唯一代码区 —— 全自动研发流水线编排器 + 控制面标准执行器。所有命令、测试、脚本都在这里。
- **`openspec/`**：规约即设计 —— 用 capability spec + 多轮评审（`docs/reviews/`）驱动变更，而非即兴改代码。改 pa 行为前先看相关 spec。
- **`docs/reviews/`**：评审记录（add → tasks → r2 → r3 → r3-response 演进），是理解决策「为什么」的入口。

`Knowledge/`（雷达抓取语料）、`.project-auto/`（流水线本地状态）在 `.gitignore` 中，**绝不入仓**。

## 常用命令

所有命令从仓库根出发。**质量命令是单一真理源**：CI 与本地同一条命令。

```bash
# 一次性装齐依赖（Python ≥3.11）
cd Projects/项目推进流水线 && pip install -e ".[dev]"

# 单一质量命令（compileall + pytest + ruff，任一失败非零退出；CI 与本地共用）
cd Projects/项目推进流水线 && bash scripts/quality.sh

# 单测（testpaths=scripts，pythonpath=scripts，故从 pa 根跑）
cd Projects/项目推进流水线 && python -m pytest scripts -q
cd Projects/项目推进流水线 && python -m pytest scripts/test_foo.py::test_bar -q   # 单个测试

# lint（仅 E9 致命错 + F 实缺陷；刻意不开 E7/E4 纯风格——test_* 的紧凑写法是有意的）
cd Projects/项目推进流水线 && ruff check scripts

# 跑全流程编排器（⚠️ 必须从 vault 根跑——run_daily.py 以 VAULT_ROOT=parents[3] 推导路径）
python3 Projects/项目推进流水线/scripts/run_daily.py
python3 Projects/项目推进流水线/scripts/run_daily.py --limit 2     # dry-run 封顶今日新内容
python3 Projects/项目推进流水线/scripts/run_daily.py --from-stage prd  # 断点续跑（复用已有 state 产物）
```

**运维 / 恢复**（`cd Projects/项目推进流水线/scripts`，`<state_dir>` 默认 `.project-auto/state`；详见 `RUNBOOK.md`）：

```bash
python recovery_cli.py <state_dir>/runs/<proj>/<stamp>_<slug>.journal.jsonl   # exit 0=恢复 / exit 2=manual_block
python quality_evidence.py        # exit 0 + readiness=True ⇔ compile+tests+ruff 全过
bash install_cron.sh              # 系统级改动，须用户本人在终端执行（每天 03:17）
```

## 高层架构（big picture）

要高效改 pa，必须先读 `Projects/项目推进流水线/CONTEXT.md`（术语表）和 `SPEC.md`。下面是需要跨多文件才能拼出的全貌：

### 控制面 / 目标面严格分离（ADR-0001）
**控制面** = 本 vault（编排逻辑 + 所有流水线状态 + persona）。**目标面** = 各被控项目仓库（dev-agent 在其中干活）。两者绝不混 —— 控制面代码不直接改目标面文件，目标面不依赖控制面。这一边界是大多数设计的出发点。

### 单一编排器，7 段流水线
`run_daily.py` 是唯一入口（cron 经 `run_cron.sh` 包装）。`STAGES = ["fetch","radar","prd","inject","critic","dispatch","report"]`：
- **fetch**：采集源（GitHub 仓、微信文章、深研）→ 今日新内容
- **radar**：从新内容抽项目无关技术信号，与白名单项目 `match_surface` 打分去重 → candidates
- **prd**：candidates × 项目 profile → 项目专属 PRD（含可验证验收标准）
- **critic**：对抗质量闸（pa-prd-critic），pass / drop / revise + 1 次修订回环
- **dispatch**：触发目标仓 `dev-agent.py`（SDK dev loop）+ 独立验证；后接 report（落报告 + SMTP 简讯）

### 机械活 vs 语义活（关键职责切分）
- **机械活**（确定性、零 LLM、纯 Python）：今日新文件发现、date-marker、文件读写、去重清单、journal 事件、副作用的 exactly-once reconcile。
- **语义活**（交给 headless persona）：抽信号 / 翻译 PRD / 对抗审 PRD / 审 dev 产出。

### 7 个 headless persona（`.claude/agents/pa-*.md`）
每个 persona 由编排器经 `claude --agent <persona> -p --output-format json --max-turns N` 链式调用；stdout 是信封 JSON，**两层解析**：`json.loads(stdout)["result"]` 再 `json.loads` 一次得 payload。

| persona | 角色 |
|---|---|
| pa-fetch-{github-repo,wechat-url,deepresearch} | 采集（Bash / web_reader / exa；github MCP 在 headless 不可用，故走 gh CLI） |
| pa-radar | 信号雷达（抽技术信号 + 比对打分去重） |
| pa-prd | 信号 × profile → PRD |
| pa-prd-critic | PRD 对抗质量闸（默认怀疑，只判「有据+可执行」） |
| pa-verify | dev 产出验证闸（审 diff + 全量测试，判绿/红，2 次重做机会） |

### 控制面标准执行器（ADR-0006）
`dev-agent.py` 是驱动**所有**目标仓的唯一执行器，在目标仓 worktree 内经 `claude-agent-sdk` 的 `query()`（string-prompt）跑 dev loop。**SDK 版本钉死 `>=0.2.121,<0.2.123`**：0.2.123 起 `can_use_tool` 回调要求 streaming 模式，与本执行器的 string-prompt `query()` 冲突。迁移到 streaming 是已知 follow-up。

### Fail-safe 分发 + 验证开发执行
两条贯穿性不变式（见 `openspec/specs/fail-safe-dispatch` / `verified-dev-execution`）：
- **三态远程查询**（FOUND / NOT_FOUND / **UNKNOWN**）：任何 GitHub/Git 远程查询的 **UNKNOWN = fail-safe 信号**，绝不当成功处理。
- **测试门**：commit / push / PR 前必须有**新的绿色测试证据**；无新绿则阻断。

### Durable runtime（日志驱动调度）
cron 会崩，所以调度是 journal-driven 的：`journal.py`（事件流）、`recovery_cli.py`（崩溃恢复，中部损坏 fail-closed 不静默跳过）、`reconcile.py`（crash 后 reconcile 远端副作用到 exactly-once）、`cutover.py`（feature flag 切换前的 shadow parity 证据）、`feature_flags.py` + `hook_adapter.py` + `container_sandbox.py` + `retry_policy.py`（会话感知重试）。`runtime_evidence.py` / `quality_evidence.py` 产出可复核的 rollout 证据。改这套要对照 `RUNBOOK.md` 和 `openspec/specs/durable-*`。

### 模型约定
persona 调用**省略 model 参数** → 走 roc LiteLLM 代理默认（`glm-5.2`）。**切勿传裸 Anthropic model id**（会被代理拒绝）。

## 开发约定

- **线性 git 历史**（无 merge commits）；rebase 是标准做法，不要用 merge。
- **ruff 只开 `E9` + `F`**（`line-length=120`, py311）。不要顺手开 E7/E4 风格规则 —— `test_*.py` 的紧凑 `a; b` 写法是有意的，开了会产生大量噪声。
- **路径同目录 import**：`scripts/` 下扁平模块互相 `sys.path.insert` 引用（pytest 的 `pythonpath=["scripts"]` 支持），**不是**可安装包（`pyproject.toml` 显式 `py-modules = []`）。不要改成包结构。
- **`cron` 非 login shell**：`run_cron.sh` 已处理 nvm/PATH；改 cron 链路时记住 PATH 极简，找不到 node/claude 之外的依赖会静默失败。
- **并发写风险**：常有长驻 `claude` web 会话在编辑 pa 脚本。对 pa 做 rebase / push / commit 前先 `git status -sb` 确认工作区稳定；若反复变脏且自己没改，多半是并发会话在写（见 `ps -eo pid,etime,cmd | grep claude`）。
