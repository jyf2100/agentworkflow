## Why

pa 所有 LLM 调用刻意不传 model → 统一走 roc LiteLLM 代理默认（`ANTHROPIC_MODEL=glm-5.2`）。这保证了模型一致与单一控制点，但锁死了 per-agent 差异化能力：

- **现状**：全代码库零 `--model`（`persona_call.py:112` / `run_daily.py:408` 两处 base_cmd 均不含）+ `dev-agent.py:834` 注释明写「model 刻意省略 → 走 roc 代理默认」。要换模型只能改 roc 全局默认 → 影响**所有**调用，无法按 agent 差异化。
- **场景**：某些 persona 是轻量结构化判断（pa-progress 方向抽查、pa-radar 信号抽取），用全量 glm-5.2 是成本浪费；另一些对抗性任务（pa-prd-critic、pa-verify）想试更强模型；dev loop 在特定目标仓想换模型做实验。这些诉求当前无出口。
- **前置已满足**：roc fast alias 现成（`~/.claude/settings.json` 实证：`ANTHROPIC_DEFAULT_HAIKU_MODEL=glm-5.1`、sonnet/opus/fable=glm-5.2[1M]），`ClaudeAgentOptions.model` 字段存在（SDK 0.2.128 types.py:1820）。

用户要「**默认不动，允许给每一个 agent 配不同模型**」。

## What Changes

- **默认零行为变更**：不设任何 env → base_cmd 不加 `--model` / `ClaudeAgentOptions.model=None` → 与现状 byte-identical（走 roc 默认 glm-5.2）。这是本 change 的头号不变式。
- **per-persona env 查表**：在两处 base_cmd（`persona_call.py:112` + `run_daily.py:408`）各加 env 查表 `PA_PERSONA_MODEL_<AGENT>` → `--model`。两处必须镜像（双胞胎契约）。
- **dev loop 双通道**：`dev-agent.py` parse_args 加 `--model` flag（手动/canary 用）+ `_build_options` 读 `PA_DEV_MODEL` env（cron 继承）；优先级 flag > env > roc 默认。
- **CLAUDE.md 模型约定同步**：文档化 env key 映射表 + roc alias 约束（勿传裸 Anthropic id）。

## Capabilities

### New Capabilities

- `per-agent-model-routing`: per-agent 模型路由——默认零变更（byte-identical baseline）+ env 查表 per-persona 注入 + dev loop 双通道（flag/env/roc-default 优先级）+ twin-mirror 对齐（persona_call 与 run_daily base_cmd 同形为）。

### Modified Capabilities

- 无既有 spec delta。所有既有 capability（`in-loop-semantic-checkpoint` / `verified-dev-execution` / 各 persona 调用）在 env 未设时行为 byte-identical；model 路由是横切配置层，独立成新 capability，不污染既有规约。

## Impact

- **代码**：
  - `scripts/persona_call.py`: base_cmd 加 env 查表（~3 行，`:112-115`）
  - `scripts/run_daily.py`: `run_persona` base_cmd 镜像（~3 行，`:408-411`）
  - `scripts/dev-agent.py`: `parse_args` 加 `"model": None` + `--model` elif（`:86-128`）；`_build_options` 注释行 → `model=args.get("model") or os.environ.get("PA_DEV_MODEL")`（`:834`，~5 行）
  - `scripts/test_persona_call.py`: 加 env 查表测试（设→cmd 含 `--model` / 不设→不含 / key 转换 pa-progress→PA_PERSONA_MODEL_PA_PROGRESS）
- **既有 spec**：无 delta（默认零变更，守基线）。
- **测试基线**：`test_persona_call` / `test_run_persona_contract` 现有断言均 mock `subprocess.run` 返回值、不捕获 cmd → 加 `--model` 不破；`test_dev_agent_source.py` 反 invariant 不破（persona_call 仍零依赖、run_daily 仍不连带加载 SDK）。
- **运维**：cron env（`run_cron.sh`）当前不设任何 `PA_*_MODEL` → 行为不变；未来要差异化时在该层 export（无需改代码）。env 继承链：run_cron.sh → run_daily.py（`_load_claude_settings_env` 注 ANTHROPIC_*）→ dev-agent subprocess（`build_env_for_sdk` 的 `dict(os.environ)`）。
- **成本**：默认不变；启用 per-agent 路由后随所选 alias 变化（haiku=glm-5.1 更轻 / sonnet=glm-5.2 同档）。
