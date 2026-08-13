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

### LangGraph 编排层（渐进迁移，`openspec/changes/langgraph-workflow-upgrade`）
`graph_pa.py` 是 LangGraph 版主图入口（7 阶段 `StateGraph` 严格线性组装，byte-identical 复刻 `run_daily.py` 编排），与 `run_daily.py`（legacy）**并行存在、flag-gated 渐进 cutover**，非替换。核心积木：
- **主图 + 聚合**：`graph_pa.py`（`build_main_graph` 7 node 线性拓扑 + `main()` 入口镜像 run_daily argparse）+ `graph_pa_aggregate.py`（2 聚合 node：critic 顺序 for-prd / dispatch ThreadPool per-PRD 真循环；5 包装 node：fetch/radar/prd/inject/report 直调 `stage_X`）。子图：`graph_pa_{contracts,state,nodes,critic,verify,dispatch,recovery}.py` + `check_boundary`。
- **物理隔离（D7）**：`PA_GRAPH_SHADOW` / `PA_GRAPH_ORCHESTRATOR` 两 env flag（`feature_flags.py`）。flag off = `run_daily.py` 完整保留，`graph_pa.py` **不被 run_daily import**（零耦合，卸 flag 秒回退）。`run_cron.sh` 分流：`PA_GRAPH_ORCHESTRATOR=1` → `graph_pa.py`，else `run_daily.py`。
- **不动 claude runtime（D1）/ 编排器侧不引 asyncio（asyncio 只在 dev-agent.py 子进程内）/ 不用 Checkpointer（D2）**。持久化走 journal 单写：commit_node 接 `append_event`+`fsync`，崩溃恢复 `recovery_cli.py` over journal（非 graph state 重建）。
- **shadow parity canary（`canary_graph_cutover.sh`）**：双源（legacy `run_daily` vs `graph_pa`）同 PRD 输入下 dispatch 终态 byte-identical 验证；默认 `--dispatch-skip-dev`（零 outward + 零 LLM 成本）。≥3 cron 周期坐实 `matched=True` 后移硬 gate。
- **cutover 发布门（`run_full_cutover_suite`，9 维度）**：`graph_shadow_parity` 条件性硬 gate——真 `ShadowParityReport` matched=False（双源都产 dispatch.json 但终态分布漂移 = 真实编排回归）硬阻断 overall；`LoadFailureReport`（baseline flag off 无真双源）软 open。详见 `openspec/changes/langgraph-workflow-upgrade/`。

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
`dev-agent.py` 是驱动**所有**目标仓的唯一执行器，在目标仓 worktree 内经 `claude-agent-sdk` 的 `query()` 跑 dev loop。**SDK 版本钉 `>=0.2.128,<0.2.130`**（对齐 `pyproject.toml`，实装 0.2.128）：streaming 模式要求已由 `prompt_stream.py` 满足（prompt 包成 AsyncIterable，非 string-prompt，解决 0.2.123+ 的 `can_use_tool` streaming 冲突）；0.2.128 配本地 `sdk_compat_patch.py`（#1106 keep-alive 定向 ast patch，上游 PR OPEN 未合，合并后移除并放宽上界，独立 follow-up）。

### Fail-safe 分发 + 验证开发执行
两条贯穿性不变式（见 `openspec/specs/fail-safe-dispatch` / `verified-dev-execution`）：
- **三态远程查询**（FOUND / NOT_FOUND / **UNKNOWN**）：任何 GitHub/Git 远程查询的 **UNKNOWN = fail-safe 信号**，绝不当成功处理。
- **测试门**：commit / push / PR 前必须有**新的绿色测试证据**；无新绿则阻断。

### Durable runtime（日志驱动调度）
cron 会崩，所以调度是 journal-driven 的：`journal.py`（事件流）、`recovery_cli.py`（崩溃恢复，中部损坏 fail-closed 不静默跳过）、`reconcile.py`（crash 后 reconcile 远端副作用到 exactly-once）、`cutover.py`（feature flag 切换前的 shadow parity 证据）、`feature_flags.py` + `hook_adapter.py` + `container_sandbox.py` + `retry_policy.py`（会话感知重试）。`runtime_evidence.py` / `quality_evidence.py` 产出可复核的 rollout 证据。改这套要对照 `RUNBOOK.md` 和 `openspec/specs/durable-*`。

### 模型约定
persona 调用**默认省略 model 参数** → 走 roc LiteLLM 代理默认（`glm-5.2`）。**切勿传裸 Anthropic model id**（会被代理拒绝）。

**per-agent 模型路由**（`add-per-agent-model-routing`，默认零变更）：每个 agent 可经 env 单独配模型，不设 = 走 roc 默认（baseline 不变）。
- **persona**（8 个）：env `PA_PERSONA_MODEL_<AGENT>` → CLI `--model=<值>`（equals 单 token）。`<AGENT>` = `agent_name.upper().replace('-', '_')`（如 pa-progress → `PA_PERSONA_MODEL_PA_PROGRESS`、pa-fetch-github-repo → `PA_PERSONA_MODEL_PA_FETCH_GITHUB_REPO`）。两处 base_cmd 镜像（`scripts/persona_call.py` + `scripts/run_daily.py`）；空串 env warn、命中记 route 审计 log。
- **dev loop**：`PA_DEV_MODEL` env（cron 经 subprocess 继承）或 `dev-agent.py --model`（手动/canary）；优先级 flag > env > roc 默认（flag 精确语义 `is not None`：空 flag 胜出不回退 env）。
- **值约束**：roc fast alias（`haiku` / `sonnet` / `opus` / `fable`）或裸 `glm-*`。`haiku` = glm-5.1（更轻），其余 = glm-5.2[1M]。裸 Anthropic id 被 roc 拒（运行时）。详见 `openspec/changes/add-per-agent-model-routing/`。
- **配置点（主）**：`Projects/项目推进流水线/config/model-routing.json`（独立文件，纯 JSON）——`model_routing.py` 解析，3 消费点（`persona_call`/`run_persona`/`_dev_cmd`）读它。例：`{"pa-progress": "haiku", "dev": "sonnet"}`。不设 = 默认 glm-5.2。
- **配置点（canary 覆盖）**：env `PA_*_MODEL` 优先于文件（临时覆盖，仍可经 `~/.claude/settings.json` env block 配，`_load_claude_settings_env` 注入）。优先级 **env > 文件 > roc**。

## 开发约定

- **线性 git 历史**（无 merge commits）；rebase 是标准做法，不要用 merge。
- **ruff 只开 `E9` + `F`**（`line-length=120`, py311）。不要顺手开 E7/E4 风格规则 —— `test_*.py` 的紧凑 `a; b` 写法是有意的，开了会产生大量噪声。
- **路径同目录 import**：`scripts/` 下扁平模块互相 `sys.path.insert` 引用（pytest 的 `pythonpath=["scripts"]` 支持），**不是**可安装包（`pyproject.toml` 显式 `py-modules = []`）。不要改成包结构。
- **`cron` 非 login shell**：`run_cron.sh` 已处理 nvm/PATH；改 cron 链路时记住 PATH 极简，找不到 node/claude 之外的依赖会静默失败。
- **并发写风险**：常有长驻 `claude` web 会话在编辑 pa 脚本。对 pa 做 rebase / push / commit 前先 `git status -sb` 确认工作区稳定；若反复变脏且自己没改，多半是并发会话在写（见 `ps -eo pid,etime,cmd | grep claude`）。

### 错误处理与调试

-  **诊断，不要猜测：** 当遇到错误或测试失败时，首先**逐步解释**可能的原因。检查假设、输入和相关的代码路径。
-  **优雅处理：** 代码应**优雅地处理错误**。例如，对异步调用使用 `try/catch`，并在适当时返回用户友好的错误消息或回退值。
-  **日志记录：** 为关键故障包含**有帮助**的控制台日志或错误日志（但避免在生产代码中记录过多日志）。
-  **无静默失败：** **不要默默地吞噬异常。** 始终通过抛出或记录它们来暴露错误。