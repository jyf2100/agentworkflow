# per-agent-model-routing

> Capability：per-agent 模型路由——默认零变更（不设 env = byte-identical baseline，走 roc 默认 glm-5.2），
> 允许经 env（per-persona）/ flag+env（dev loop）给每个 agent 单独配模型。横切配置层，不改变既有
> capability 在 baseline 下的任何行为。env 查表覆盖 8 个 persona 子进程（persona_call + run_daily 双胞胎 base_cmd）
> + dev loop（SDK ClaudeAgentOptions.model）。model 值必须 roc 认的 alias（`haiku`/`sonnet`/`opus`/`fable`/`glm-*`），
> 裸 Anthropic id 被 roc 拒（运行时约束，代码不校验）。

## ADDED Requirements

### Requirement: Default-zero routing (no env = byte-identical baseline)

model 路由 SHALL 默认关闭：未设任何 `PA_*_MODEL` env / 未传 `--model` flag 时，persona base_cmd MUST NOT 含 `--model`，dev loop `ClaudeAgentOptions.model` MUST 为 None。此为头号不变式——baseline 下所有调用走 roc 代理默认（`ANTHROPIC_MODEL=glm-5.2`），与变更前 byte-identical，既有测试无需改预期。

#### Scenario: 未设 persona model env → base_cmd 不含 --model

WHEN `run_persona_subproc`（persona_call）/ `run_persona`（run_daily）构造 base_cmd 且 `os.environ` 无任何 `PA_PERSONA_MODEL_*`
THEN 拼出的 cmd 数组不含 `"--model"` 元素
AND claude CLI 走 roc 默认（glm-5.2），与 baseline 行为一致。

#### Scenario: dev loop 无 flag 无 env → ClaudeAgentOptions.model=None

WHEN `_build_options` 构造 ClaudeAgentOptions 且 `args["model"]` 为 None 且 `PA_DEV_MODEL` env 未设
THEN 传入的 `model=None`
AND dev SDK loop 走 roc 默认 glm-5.2，与 baseline 一致。

### Requirement: Per-persona model injection via env

persona 子进程的 base_cmd SHALL 在拼装后查 env `PA_PERSONA_MODEL_<AGENT>`，其中 `<AGENT>` = `agent_name.upper().replace('-', '_')`（env 变量名禁连字符）；env 有值时 MUST 追加 `--model=<value>`（equals 单 token 形式，review follow-up D7）。查表 SHALL 在 `persona_call.run_persona_subproc`（变量 `agent_name`）与 `run_daily.run_persona`（变量 `name`）两处 base_cmd 同形镜像（双胞胎契约）。env 设为**空串**（`""`）SHALL 经 log 回调 warn（配错反馈，review follow-up）；env 命中非空值 SHALL 经 log 记审计行「model route → X」（可观测性）。

- 8 个 persona 覆盖：pa-fetch-{deepresearch,wechat-url,github-repo} / pa-radar / pa-prd / pa-prd-critic / pa-verify（经 run_daily）+ pa-progress（经 persona_call）。

#### Scenario: 设 per-persona env → cmd 含 --model=X（equals 形式）

WHEN `os.environ["PA_PERSONA_MODEL_PA_PROGRESS"] = "haiku"` 且调 `run_persona_subproc("...", "pa-progress", ...)`
THEN 拼出的 cmd 含 `"--model=haiku"`（equals 单 token）
AND 该 persona 经 roc 路由到 `glm-5.1`（haiku alias）。

#### Scenario: env key 连字符转下划线 + 大写

WHEN 调 `run_persona("pa-fetch-github-repo", ...)` 且 `os.environ["PA_PERSONA_MODEL_PA_FETCH_GITHUB_REPO"] = "sonnet"`
THEN cmd 含 `"--model=sonnet"`
AND 连字符被替换为下划线、整体大写（env 变量名合法）。

#### Scenario: 不同 persona 互不影响

WHEN `PA_PERSONA_MODEL_PA_PROGRESS=haiku` 已设但 `PA_PERSONA_MODEL_PA_RADAR` 未设
THEN 调 pa-progress → cmd 含 `--model=haiku`
AND 调 pa-radar → cmd 不含 `--model`（各自独立查表）。

#### Scenario: 空串 env 经 log warn 且不注入（review follow-up）

WHEN `os.environ["PA_PERSONA_MODEL_PA_PROGRESS"] = ""`（空串）
THEN 经 log 回调 warn「设为空串→走 roc 默认」
AND cmd 不含任何 `--model*`（走 roc 默认 glm-5.2）。

#### Scenario: env 命中经 log 记审计行（review follow-up）

WHEN `PA_PERSONA_MODEL_PA_PROGRESS=haiku` 且调 run_persona_subproc（传 log 回调）
THEN log 含「model route → haiku（env）」审计行。

### Requirement: Twin-mirror base_cmd alignment

`persona_call.run_persona_subproc` 与 `run_daily.run_persona` 的 base_cmd 是刻意复制的双胞胎（行为对齐契约：persona_call 模块头注释「与 run_daily.run_persona 行为对齐，行为基线 = test_persona_call」）。env 查表 `--model` 注入 MUST 在两处同形（仅变量名差异：`agent_name` vs `name`），不得只改一处。

#### Scenario: 两处查表表达式同形

WHEN 审查 persona_call.py 与 run_daily.py 的 base_cmd 拼装段
THEN 两处都有 `os.environ.get(f"PA_PERSONA_MODEL_{<var>.upper().replace('-', '_')}")` → 条件追加 `--model`
AND `<var>` 在 persona_call 为 `agent_name`、在 run_daily 为 `name`（各自对齐本函数参数）。

### Requirement: Dev loop dual-channel model routing

dev SDK loop 的 model SHALL 经双通道解析，优先级 flag > env > roc 默认。**flag 精确语义**（review follow-up D4）：`_build_options` 用 `_dev_flag = args.get("model"); _model = _dev_flag if _dev_flag is not None else os.environ.get("PA_DEV_MODEL")`。flag 设了（哪怕空串 `""`）= flag 胜（空→model="" → SDK truthiness 跳过 → roc 默认）；仅 flag 缺省（None）才回退 env。`--model` flag（parse_args）给手动/canary 用；`PA_DEV_MODEL` env 给 cron（经 subprocess `dict(os.environ)` 继承，无需 run_daily 显式透传）。两者都无 → None（roc 默认）。

#### Scenario: --model flag 优先于 env

WHEN `dev-agent.py --model opus` 且 `PA_DEV_MODEL=haiku` 同时存在
THEN `ClaudeAgentOptions.model = "opus"`（flag 胜出）
AND dev loop 路由到 glm-5.2[1M]（opus alias）。

#### Scenario: --model 空串 flag 胜出不回退 env（review follow-up）

WHEN `dev-agent.py --model ""`（空串 flag）且 `PA_DEV_MODEL=haiku` 同时存在
THEN `ClaudeAgentOptions.model = ""`（is-not-None 语义：flag 设了即胜，非经 `or` 回退 env）
AND SDK `if self._options.model:` truthiness 跳过空串 → 走 roc 默认 glm-5.2。

#### Scenario: 无 flag 时 env 兜底

WHEN `dev-agent.py`（无 `--model`）且 `PA_DEV_MODEL=haiku`
THEN `ClaudeAgentOptions.model = "haiku"`（env 兜底）
AND dev loop 路由到 glm-5.1。

#### Scenario: cron 经 env 继承生效无需 run_daily 透传

WHEN run_daily.py 经 subprocess 启动 dev-agent 且父进程 env 含 `PA_DEV_MODEL=sonnet`
THEN dev-agent subprocess 经 `dict(os.environ)` 继承到 `PA_DEV_MODEL`
AND `_build_options` 读到 → model="sonnet"，无需 run_daily 在 cmd 里显式传 `--model`。

### Requirement: Model value is roc-recognized alias (runtime constraint)

`--model` / `PA_DEV_MODEL` 的值 MUST 是 roc 认的 alias（`haiku` / `sonnet` / `opus` / `fable`）或裸 `glm-*`。**裸 Anthropic model id（如 `claude-sonnet-5`）会被 roc 代理拒绝**。本约束是 roc 层运行时校验，代码层 MUST NOT 维护 alias 白名单（避免随 roc 升级腐化）；非法值经 persona_call 现有 `is_error` / 非零退出 raise 路径冒泡。

#### Scenario: 合法 alias 路由到对应 glm 模型

WHEN `PA_PERSONA_MODEL_PA_PROGRESS=haiku` 且 pa-progress 跑通
THEN roc 把 `--model haiku` 路由到 `glm-5.1`（`ANTHROPIC_DEFAULT_HAIKU_MODEL`）
AND persona_call `meta["model"]`（= outer `modelUsage`）反映 glm-5.1。

#### Scenario: 非法裸 Anthropic id 被 roc 拒并冒泡

WHEN `PA_DEV_MODEL=claude-sonnet-5`（裸 Anthropic id）
THEN roc 代理拒绝该请求
AND dev-agent 经现有非零退出 / is_error 路径 raise（不静默吞），错误可见。

### Requirement: Config via independent file (primary) + env override (canary)

per-agent 模型路由的**主配置源** SHALL 是独立文件 `Projects/项目推进流水线/config/model-routing.json`（独立于 `~/.claude/settings.json` 与认证 env，解耦——日常配置不经认证注入函数 `_load_claude_settings_env`）。零依赖模块 `scripts/model_routing.py` SHALL 解析之：`resolve_persona_model(agent_name)` / `resolve_dev_model()`；文件不存在 / 空 / key 缺 / null / 空串 → None（roc 默认，零变更 baseline）。

env `PA_PERSONA_MODEL_<AGENT>` / `PA_DEV_MODEL` SHALL 保留作 canary 覆盖（临时覆盖主配置）。persona 优先级 SHALL 为 **env > 文件 > roc**；dev 经 `run_daily._dev_cmd` 解析 `env or resolve_dev_model()` 透传 `--model` flag（dev-agent 是 ADR-0006 纯调度器，不读控制面文件）。`_load_claude_settings_env` 的 `PA_*_MODEL` 注入保留作可选 canary 通道（env 配在 settings.json 时生效），日常用文件则不触发。

#### Scenario: config 文件配 pa-progress → persona cmd 含 --model

WHEN `config/model-routing.json` 含 `{"pa-progress": "haiku"}` 且 env 未设
THEN persona_call / run_persona 读文件 → cmd 含 `--model=haiku`（路由 glm-5.1）。

#### Scenario: env 覆盖 config 文件（canary）

WHEN env `PA_PERSONA_MODEL_PA_PROGRESS=opus` 且 config 配 `pa-progress=haiku`
THEN cmd 含 `--model=opus`（env 胜，canary 覆盖主配置）。

#### Scenario: dev model 经 _dev_cmd 文件透传

WHEN config 配 `{"dev": "sonnet"}` 且 env `PA_DEV_MODEL` 未设
THEN run_daily._dev_cmd 读文件 → cmd 含 `--model=sonnet`（dev-agent flag，路由 glm-5.2[1M]）。

#### Scenario: 文件缺/空 → 走 roc 默认（零变更 baseline）

WHEN `config/model-routing.json` 不存在 / `{}` / key 缺 / value null 或空串
THEN resolve_* 返回 None → cmd 无 `--model` → 走 roc 默认 glm-5.2。

#### Scenario: dev model 命中 → _dev_cmd 记 route 审计 log（对称 persona；review ⑤）

WHEN `_dev_cmd` 解析到 dev model（env 或 config 文件）
THEN run_daily log 含 `[dev] model route → <model>（env|config）` 审计行
AND dev 是最昂贵的调用（完整 dev loop），route 行供 cron 后重建所用模型。

#### Scenario: PA_DEV_MODEL 空串 → _dev_cmd 记 warn（对称 persona；review ⑥）

WHEN env `PA_DEV_MODEL=""`（空串）
THEN run_daily log 含 `⚠ PA_DEV_MODEL 设为空串` warn（配错反馈，对称 persona 空串 warn）
AND cmd 不含 `--model*`（空串经 `or` 降级文件/roc 默认）。

### Requirement: Degrade-without-raise contract + misconfig feedback (review 2026-08-09)

`model_routing._load` SHALL 对**任意**损坏输入降级到 roc 默认且**不 raise**：缺文件 / IO 错（`OSError`）/ JSON 语法错（`ValueError`）/ 深嵌套（`RecursionError`，实测 `[`×10000 触发，须显式捕——不被 `ValueError` 覆盖）/ 超大文件（> `_MAX_BYTES` 64KB cap，防 `MemoryError`）/ 顶层非 object / value 非字符串。消费点（`run_persona` / `_dev_cmd` / `persona_call`）裸调用无 try/except 兜底，`_load` raise 会穿透致整晚 pipeline abort。

配错 SHALL 经 `_log.warning` 反馈（无静默失败）：顶层非 object（对称语法错 warn）/ 未知 key（typo/大小写，如 `pa-progres` / `DEV`，比对 `_KNOWN_KEYS` 9 个已知 key）/ 超大文件。value 非字符串（int/bool/object 真值）SHALL 经 `_coerce`（`isinstance(v, str) and v`）归一为 None——不穿透成 `--model=123`，守 `str | None` 返回契约。

#### Scenario: 深嵌套 JSON 触发 RecursionError → 降级不 raise

WHEN `config/model-routing.json` 含 `[`×10000 + `]`×10000（深嵌套，触发 `RecursionError`）
THEN `_load` 的 `except (OSError, ValueError, RecursionError)` 捕获 → warn + 返 `{}`
AND resolve_* 返回 None → 走 roc 默认，**不穿透 raise 致 pipeline abort**。

#### Scenario: value 非字符串 → None（守 str|None 契约）

WHEN config 含 `{"dev": 123}` / `{"dev": true}` / `{"dev": {"x": 1}}`（非字符串真值）
THEN `_coerce` 归一为 None（不穿透成 `--model=123` / `--model=True`）
AND resolve_* 返回 None → 走 roc 默认。
