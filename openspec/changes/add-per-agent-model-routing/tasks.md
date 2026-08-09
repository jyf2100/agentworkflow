# tasks — per-agent-model-routing

TDD 顺序：先写测试（RED）→ 最小实现（GREEN）→ ruff 干净（仅 E9+F）。

> **实现注记**：本 change 极小（核心代码 ~11 行 + review 修订 ~10 行）。关键约束 = **双胞胎镜像**（persona_call.py 与 run_daily.py base_cmd 是刻意复制，加 `--model` 必须两处同形）+ **默认零变更**（不设 env = byte-identical baseline）。

## 1. persona 端 env 查表（双胞胎 #1：persona_call）

- [x] T1 `test_persona_call.py` 加 env 查表测试（含 review 修订的 equals 断言 + 空串 warn + 审计 log）
- [x] T2 实现 `scripts/persona_call.py` base_cmd：env 查表 → `--model=X`（equals）+ 空串 warn + route 审计 log
- [x] `ruff check` 干净

## 2. persona 端 env 查表（双胞胎 #2：run_daily 镜像）

- [x] T3 实现 `scripts/run_daily.py` run_persona base_cmd 镜像（变量 `name`；equals + 空串 warn + route 审计 log，用全局 log）
- [x] twin 镜像守：`test_run_persona_contract.py` 加 cmd 捕获测试（review HIGH，3 人共识）—— 守 run_daily 端（7 persona）注入

## 3. dev loop 双通道（flag > env > roc 默认）

- [x] T4 `scripts/dev-agent.py`：
  - `parse_args`：`out` 加 `"model": None`；while 链加 `--model` 解析
  - `_build_options`：`is not None` 精确语义（review follow-up，替代 `or`）—— flag 设了（哪怕空串）= flag 胜，仅 flag 缺省才回退 env
  - ~~dev-agent.py 不可 import~~ → **review 更正（code-review MED）**：`test_verify_loop._dev_agent()` 已用 importlib lazy 加载，parse_args `--model` 可直测（推翻原「不可 import → 只能 compile+静态」论据）
- [x] `test_verify_loop.py` 加 `test_dev_agent_parse_args_model`（含空 flag `--model ""` → model="" 断言）
- [x] `python -m py_compile scripts/dev-agent.py` 通过

## 4. 文档同步

- [x] T5 `CLAUDE.md` 模型约定段补 per-agent routing 说明 + review 注（equals / is-not-None）

## 5. 全绿 + canary

- [x] T6 `bash scripts/quality.sh` 全绿：**1545 passed / 5 xfailed** + ruff clean（baseline 1540 +5 新测试）
- [x] `test_dev_agent_source.py` 反 invariant 不破
- [x] canary（守 pa-test-no-dirty-data，2026-08-09 真实 roc 端到端）：
  - persona 双向：设 `PA_PERSONA_MODEL_PA_PROGRESS=haiku` → modelUsage=`glm-5.1`（切换确认）；不设 → modelUsage=`glm-5.2`（默认零变更确认）
  - dev None 路径：经 SDK 源码 `subprocess_cli.py:509-510 if self._options.model:` 静态实证 model=None = byte-identical baseline（silent-failure review 确认）；dev 真设 PA_DEV_MODEL 全链 canary 列 follow-up（SDK 译 model→--model CLI flag 已源码可证 + roc 接受 haiku 已 persona canary 证）

## 6. review 修订（2026-08-09，5 专家团队 review 后）

- [x] R1 twin 镜像守（HIGH，3 人共识）：`test_run_persona_contract.py` 加 cmd 捕获测试
- [x] R2 dev parse_args 测试 + 论据更正（code-review MED）：importlib 可测，推翻「不可 import」
- [x] R3 空串/空flag 精确语义 + warn（3 人共识）：dev `is not None`；persona 空串 env warn
- [x] R4 equals 形式（silent-failure LOW + security）：`--model=X` 单 token（跟随 SDK subprocess_cli.py 惯例）
- [x] R5 审计 log（architect LOW + security LOW）：env 命中 log route 行
- [x] R6 文档更正：design D4/D6/D7 + Non-Goals reflection 论据修正 + spec scenario + tasks
- [x] R7 配置入口（用户问「env 表在哪」暴露的缺口）：`_load_claude_settings_env` 扩注入条件含 `PA_*_MODEL*` → settings.json env block 成统一配置入口；+ `test_load_claude_settings_env.py` 3 测试（注入/非路由项跳过/setdefault）

### 列 follow-up（不本 change 做）

- `add-reflection-model-routing`：`PA_REFLECTION_MODEL`（第三条 LLM 路径 learning_memory_reflection.py，architect MED）
- model 值字符集早拒正则（security MED + silent-failure D6 MED，defense-in-depth）
- stderr 透传脱敏（security MED，既有债务，与 external_state.sanitize 统一）
- dev 全链 canary（silent-failure MED，SDK 源码 + persona canary 已间接证）
