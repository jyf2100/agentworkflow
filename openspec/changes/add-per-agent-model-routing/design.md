## Context

pa 两类 LLM 调用路径均刻意省略 model，走 roc 默认：

- **CLI persona 子进程**（8 个 persona：pa-fetch-{deepresearch,wechat-url,github-repo} / pa-radar / pa-prd / pa-prd-critic / pa-verify / pa-progress）。base_cmd 拼装在两处刻意复制的「双胞胎」：
  - `persona_call.py:112` —— 零依赖共享模块（dev 内循环 pa-progress 用，模块头注释明写「与 run_daily.run_persona 行为对齐，行为基线 = test_persona_call」）。变量名 `agent_name`。
  - `run_daily.py:408` —— 编排器其余 7 个 persona 用。变量名 `name`。
  两处 base_cmd 同形：`[claude_bin, "--agent", <name>, "--output-format", "json", "--max-turns", <N>]` + 可选 `--allowedTools`。全代码库零 `--model`。
- **SDK dev loop**：`dev-agent.py:831` `_build_options(*, resume, fork_session)` 工厂构造 `ClaudeAgentOptions(...)`，`:834` 注释「model 刻意省略 → 走 roc 代理默认（glm-5.2）；勿传裸 Anthropic model id」。`ClaudeAgentOptions.model` 字段存在（claude_agent_sdk 0.2.128 types.py:1820，`str | None = None`），传 None = 走 roc 默认。

**roc fast alias 已现成**（`~/.claude/settings.json` 实证）：`ANTHROPIC_DEFAULT_HAIKU_MODEL=glm-5.1`、`ANTHROPIC_DEFAULT_SONNET_MODEL` / `_OPUS` / `_FABLE` = `glm-5.2[1M]`。故 CLI `--model haiku` → 路由 glm-5.1、`--model sonnet|opus|fable` → glm-5.2。`in-loop-semantic-checkpoint` design OQ#3「需 roc 支持 fast alias」的前置由此解除。

**env 继承链已通**：`run_cron.sh`（cron wrapper，已处理 nvm/PATH）→ `run_daily.py`（`_load_claude_settings_env` 把 ANTHROPIC_* 注入 `os.environ`）→ `dev-agent.py` subprocess（`build_env_for_sdk` 的 `dict(os.environ)` 为 SDK 子进程预置环境）。故 dev-agent 读 `PA_DEV_MODEL` 无需 run_daily 显式透传，env 继承即达。

## Goals

- **默认零行为变更**（byte-identical baseline）——头号不变式。
- 每个 persona + dev loop 可独立配模型（per-agent 粒度）。
- 守现有行为基线（`test_persona_call` / `test_run_persona_contract` 断言不破）。
- 守 SDK-隔离 invariant（`persona_call` 仍零依赖、`run_daily` 仍不连带加载 SDK）。

## Non-Goals

- **`learning_memory_reflection.py` 不覆盖**（review follow-up，architect MED 修正论据）：进程内 cross-PRD 反思——是 pa 的**第三条独立 LLM 调用路径**（persona CLI 子进程 + dev SDK loop 之外的进程内 `query()`），改动点为 `options_kwargs`（`:118-123` 单一点，**非原 design 所称「多处」**）。差异化价值真实（反思宜用强模型）。本 change 仍排除以控范围（用户原始范围 = persona + dev）；**单列 follow-up `add-reflection-model-routing`**（加 `PA_REFLECTION_MODEL` env，对称 dev loop 语义，~5 行 + 1 测试）。
- **frontmatter `model:` 渠道**：Rejected Alternative（见下）。
- **run_daily 显式 `--model` 透传给 dev-agent**：env 继承已够（KISS）；CLI flag 留给手动/canary。
- **改 roc 代理配置**：alias 已现成，不动代理。
- **决定哪个 agent 配什么模型**：本期只建机制，值留空（用户「先不管具体值」）；上线后观察再定。

## Decisions

### D1：env 查表（用户已选）而非 frontmatter `model:`

- **env 查表**（采用）：`os.environ.get(f"PA_PERSONA_MODEL_{agent_name.upper().replace('-', '_')}")`。零签名改动（不动 `run_persona` / `run_persona_subproc` 参数、不改 8 个调用点）；显式契约（env key 含 agent_name，一目了然）；贴合 pa 环境变量域切割惯例（同 `PA_LOOP_*` / `PA_VERIFY_*` / `PA_SINGLE_FLIGHT_*` / `PA_LEARNING_*`）；统一覆盖 persona + dev（`PA_DEV_MODEL`）。
- **frontmatter `model:`**（拒，见 Rejected Alternatives）：证据强但 headless `-p` 是否读未实证，且 dev 无 frontmatter 仍要 env → 两套机制不统一。

### D2：默认零变更（byte-identical baseline）

不设任何 `PA_*_MODEL` env → 查表返 None → base_cmd 不加 `--model` / `ClaudeAgentOptions.model=None`。这与现状 byte-identical：CLI 不带 `--model` → claude 走 roc 代理 `ANTHROPIC_MODEL=glm-5.2`；SDK model=None → 同走 glm-5.2。头号不变式，所有既有测试无需改预期。

### D3：双胞胎镜像（persona_call + run_daily base_cmd 同形为）

`persona_call.py:112` 与 `run_daily.py:408` 是刻意复制的双胞胎（模块头注释明写行为对齐契约）。加 env 查表 `--model` 必须**两处都加、同形**，否则破坏「行为基线 = test_persona_call」对齐。注意变量名差异：persona_call 用 `agent_name`，run_daily 用 `name`——查表表达式各自对齐本函数变量名。两处覆盖全部 8 个 persona（pa-progress 经 persona_call，其余 7 个经 run_daily）。

### D4：dev loop 双通道（flag > env > roc 默认，flag 精确语义）

- **`--model` flag**（parse_args）：手动跑 / canary 用，显式覆盖。
- **`PA_DEV_MODEL` env**：cron 经 subprocess 继承父 env，dev-agent `_build_options` 读；无需 run_daily 透传（env 继承即达，KISS）。
- **优先级 + flag 精确语义**（review follow-up，3 人共识）：
  ```python
  _dev_flag = args.get("model")
  _model = _dev_flag if _dev_flag is not None else os.environ.get("PA_DEV_MODEL")
  ```
  flag 设了（哪怕空串 `""`）= flag 胜（空→`model=""` → SDK truthiness 跳过 → roc 默认）；仅 flag 缺省（None）才回退 env；都无 → None（roc 默认）。**`is not None` 替代原 `or`**：避免空 flag 经 `or` 静默回退 env（违 flag 优先，3 人 review 共识）。
- run_daily.py 零改动（`_build_dev_agent_cmd` 不透传 `--model`；env 继承已够）。

### D5：env key 命名 = `PA_PERSONA_MODEL_<AGENT>` / `PA_DEV_MODEL`

- persona：`PA_PERSONA_MODEL_` + `agent_name.upper().replace('-', '_')`（env 变量名禁连字符，见 key 映射表）。
- dev：单一 `PA_DEV_MODEL`（dev loop 只一条路径，无需 per-xxx 切分）。
- 前缀 `PA_` 对齐 pa 域切割惯例；`PERSONA_MODEL` / `DEV_MODEL` 语义自明。

**env key 映射表**：

| agent | env key |
|---|---|
| pa-fetch-deepresearch | `PA_PERSONA_MODEL_PA_FETCH_DEEPRESEARCH` |
| pa-fetch-wechat-url | `PA_PERSONA_MODEL_PA_FETCH_WECHAT_URL` |
| pa-fetch-github-repo | `PA_PERSONA_MODEL_PA_FETCH_GITHUB_REPO` |
| pa-radar | `PA_PERSONA_MODEL_PA_RADAR` |
| pa-prd | `PA_PERSONA_MODEL_PA_PRD` |
| pa-prd-critic | `PA_PERSONA_MODEL_PA_PRD_CRITIC` |
| pa-verify | `PA_PERSONA_MODEL_PA_VERIFY` |
| pa-progress | `PA_PERSONA_MODEL_PA_PROGRESS` |
| dev loop | `PA_DEV_MODEL` |

### D6：model 值必须 roc 认的 alias（运行时约束，代码不校验）

- 合法值：roc fast alias（`haiku` / `sonnet` / `opus` / `fable`）或裸 `glm-*`。
- **裸 Anthropic id（`claude-sonnet-5` 等）会被 roc 代理拒绝**（CLAUDE.md 已警告）。这是 roc 层运行时约束，**代码不校验**（KISS：env 查表只管「有/无」，值合法性由 roc 在请求时拒，错误经 persona_call 现有 `is_error` / 非零退出 raise 路径冒泡）。
- canary 验证（§验证）确认所配 alias 真切换到对应 glm 模型。
- **⚠ review 注（silent-failure MED）**：「裸 Anthropic id 被 roc 拒」是**运行时假设**——若 roc 对未知 model 改宽容（静默回退默认 + warn），配错会以 exit 0 静默路由错模型。当前靠 canary 间接证 roc 对 haiku/sonnet 别名行为符合预期；裸 id 拒绝行为未单独实证。**字符集早拒**（拦极端值）列 follow-up（与 stderr 脱敏同批 defense-in-depth），不阻塞本 change。

### D7：equals 形式 `--model=X` + 空串 warn + 审计 log（review follow-up）

- **equals 单 token 形式**（review follow-up，silent-failure LOW + security 可达性）：persona base_cmd 用 `f"--model={_model}"`（非两 token `["--model", _model]`）。跟随 SDK 自身惯例（`subprocess_cli.py:530-535` 对 `--resume` 用 `--resume=value`，注释明写两 token 形式让 dash-leading value 可注入 flag）。subprocess list-form 已堵 shell 注入，equals 形式消除 parser 把 `-foo` model value 当独立 flag 的灰色地带（确定性）。
- **空串 env 显式 warn**（review follow-up，3 人共识）：`PA_PERSONA_MODEL_X=""` 被 `if _model:` 跳过 = 配错无反馈。现 `_model == ""` 时经 log 回调（persona_call）/ 全局 log（run_daily）warn「设为空串→走 roc 默认」。
- **审计 log**（review follow-up，architect LOW + security LOW）：env 命中时 log「model route → X（per-agent env）」—— cron 调试可观测性（配了什么模型 / 走没走路由）。

### D8：配置入口 = settings.json env block（_load_claude_settings_env 注入 PA_*_MODEL）

review 后续发现（用户问「env 表配置在哪」暴露）：机制读 `os.environ`，但 `_load_claude_settings_env`（`run_daily.py:3277`）原本只注入 `ANTHROPIC_` 前缀 → settings.json 里写 `PA_PERSONA_MODEL_*` 读不到。**扩注入条件**为 `(k.startswith("ANTHROPIC_") or (k.startswith("PA_") and "_MODEL" in k))`，让 settings.json env block 成为 per-agent 路由的**统一配置入口**（与 `ANTHROPIC_*` 认证同处一处）。仅 `PA_*_MODEL*` 模式（避开 `PA_HEARTBEAT` / `PA_CLAUDE_BIN` / `OBSIDIAN_VAULT_PATH` 等非路由项）。setdefault 语义不变。

**配置样例**（`~/.claude/settings.json` 的 `env` block，纯 JSON 无注释——`json.loads` 遇注释会失败跳过注入）：
```json
{ "env": { "PA_PERSONA_MODEL_PA_PROGRESS": "haiku", "PA_DEV_MODEL": "sonnet" } }
```
不设任何 `PA_*_MODEL` = 默认全走 glm-5.2（零变更 baseline）。值须 roc 别名（haiku=glm-5.1 / sonnet|opus|fable=glm-5.2[1M]）；裸 Anthropic id 被 roc 拒。cron 经 run_cron.sh → run_daily（注入）→ dev-agent subprocess（`dict(os.environ)` 继承）天然生效，无需 run_daily 显式透传。

## Open Questions

1. **哪些 persona 实际该配什么模型** —— 本期只建机制（值留空），上线后按任务类型 + 成本观察再定。候选直觉：pa-progress / pa-radar（轻量结构化）试 `haiku`=glm-5.1；pa-prd-critic / pa-verify（对抗）试 `sonnet`=glm-5.2（同档，可能无差异）。
2. **`learning_memory_reflection.py` 覆盖** —— follow-up（改动点多、价值低）。
3. **run_daily.run_persona 是否最终迁移到 persona_call 共享模块** —— in-loop OQ#5 遗留；若迁移则消除双胞胎，本 change 的 D3 镜像约束自然消失。本期不强制。

## Rejected Alternatives

- **frontmatter `model:` 渠道**：每个 `.claude/agents/pa-*.md` frontmatter 加 `model:`。证据强（40+ 全局 agent 用此 + 官方文档支持 `--agent` 读 frontmatter），但：(1) headless `-p`（print mode）是否读 frontmatter model 未实证；(2) dev loop 无 frontmatter（dev-agent.py 不是 agent 定义），仍要 env → 两套机制不统一、契约隐式；(3) 改 8 个 persona 文件 + 不可测。env 查表更统一、显式、可测。
- **run_daily 显式 `--model` 透传给 dev-agent**：`_build_dev_agent_cmd` 加 `--model`。多余——env 经 `dict(os.environ)` 已继承到 dev-agent subprocess，`PA_DEV_MODEL` 直达；CLI flag 只增加一条透传路径（KISS 违反）。`--model` flag 留给手动/canary 一次性覆盖。
- **覆盖 `learning_memory_reflection.py`**：进程内反思非独立 persona 子进程，多处刻意不传 model 注释；per-agent 差异化价值低、改动点散。本期排除，列 follow-up。
- **代码层校验 model 值合法性**：维护 alias 白名单 → roc 升级 alias 时代码要同步改、易腐。运行时让 roc 拒（错误经现有 raise 路径冒泡）更解耦。
