## MODIFIED Requirements

### Requirement: Config via independent file (primary) + env override (canary)

per-agent 模型路由的**主配置源** SHALL 是独立文件 `Projects/项目推进流水线/config/model-routing.json`（独立于 `~/.claude/settings.json` 与认证 env，解耦——日常配置不经认证注入函数 `_load_claude_settings_env`）。零依赖模块 `scripts/model_routing.py` SHALL 解析之：`resolve_persona_model(agent_name)` / `resolve_dev_model()`；文件不存在 / 空 / key 缺 / null / 空串 → None（roc 默认，零变更 baseline）。

env `PA_PERSONA_MODEL_<AGENT>` / `PA_DEV_MODEL` SHALL 保留作 canary 覆盖（临时覆盖主配置）。persona 优先级 SHALL 为 **env > 文件 > roc**；dev 经 `run_daily._dev_cmd` 解析 `env or resolve_dev_model()` 透传 `--model` flag（dev-agent 是 ADR-0006 纯调度器，不读控制面文件）。`_load_claude_settings_env` 的 `PA_*_MODEL` 注入保留作可选 canary 通道（env 配在 settings.json 时生效），日常用文件则不触发。

**容器形态（execution-hub / pa-executor 镜像）限定**：文件主点 SHALL 视为 vault 本机形态专属配置面——executor 镜像 MUST NOT 含 `config/` 目录（patch.Dockerfile 不 COPY），hub 派发的执行容器内路由 SHALL 仅经 env 点（`PA_PERSONA_MODEL_<AGENT>` / `PA_DEV_MODEL`，经 hub 进程 env `PA_*` 透传注入容器）。这是有意的部署面选择（裁决 2026-10-27）：路由随 hub.env 差异化、改后即刻生效，烤进镜像会把路由钉死在发布时点。文件主点在容器内缺失 SHALL 走既有 None 降级（零变更 baseline），MUST NOT 告警——镜像内本就不预期存在该文件。

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

#### Scenario: 容器形态文件主点缺失 → env-only 降级零告警

WHEN pa-executor 镜像内 `config/model-routing.json` 不存在且 hub.env 配 `PA_PERSONA_MODEL_PA_PROGRESS=haiku`
THEN 执行容器内 resolve_* 返回 None（零变更 baseline，不告警镜像内文件缺失）
AND persona cmd 含 `--model=haiku`（env 点经 `PA_*` 透传生效——容器形态唯一配置点）。
