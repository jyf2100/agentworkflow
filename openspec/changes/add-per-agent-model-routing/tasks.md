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
- [x] R8 独立文件解耦（用户「不做强耦合」：主配置塞 settings.json → 认证注入函数知道路由 `_MODEL` 命名 = 职责混合/知识泄漏）：主配置改 `config/model-routing.json` + 零依赖 `model_routing.py`（`resolve_persona_model`/`resolve_dev_model`，路径 `parents[1]/config/...` 自定位）；3 消费点 env 查表 → `env or 文件`（env 降级 canary 覆盖，文件为主）；dev 经 `_dev_cmd` 解析 `env or resolve_dev_model()` 透传 `--model`（dev-agent 纯调度器不读文件）；env 注入保留作 canary 通道；+ `test_model_routing.py` 6 测试 + persona/twin/dev_cmd 文件兜底测试
- [x] R9 review 修复（5 专家团队 review 阶段 E，用户选「核心 7 项全修」；architect 维度输出被 harness 安全 hook 中和——settings-json pattern——无有效结论）：
  - ① RecursionError 契约（security MED）：`_load` 扩 except `RecursionError` + read_text 64KB cap（`_MAX_BYTES`）→ 深/超大 JSON 降级不 raise（守「不 raise」契约，防 pipeline abort；实测 `[`×10000 触发）
  - ② value 类型校验（python MED + security LOW-2 共识）：`_coerce`（`isinstance str`）守 `str|None` 契约，非字符串真值（int/bool/object）不穿透成 `--model=123`
  - ③ 非 dict JSON warn（silent-failure HIGH）：顶层非 object → warn（symmetry 语法错 warn）
  - ④ 未知 key warn（silent-failure HIGH）：`_KNOWN_KEYS`（9 key 闭集），typo/大小写 → warn（主配置通道配错反馈）
  - ⑤ dev route log（silent-failure MED）：`_dev_cmd` 加 `[dev] model route` 审计 log（对称 persona run_persona route log，dev 最贵调用零审计补齐）
  - ⑥ dev 空串 warn（python MED + silent-failure HIGH 共识）：`PA_DEV_MODEL=""` → log warn（对称 persona 空串 warn）
  - ⑦ spec 文档（code-reviewer LOW）：route log 文本「(per-agent env)」→「(env)」对齐代码
  - +7 测试（5 `test_model_routing` + 2 `test_dev_cmd_retry`），quality 1568 passed；spec 加 `Degrade-without-raise contract + misconfig feedback` requirement + dev route/空串 scenario

### 列 follow-up（不本 change 做）

- `add-reflection-model-routing`：`PA_REFLECTION_MODEL`（第三条 LLM 路径 learning_memory_reflection.py，architect MED）
- model 值字符集早拒正则（security MED + silent-failure D6 MED，defense-in-depth）
- stderr 透传脱敏（security MED，既有债务，与 external_state.sanitize 统一）
- dev 全链 canary（silent-failure MED，SDK 源码 + persona canary 已间接证）
- review R9 新增：文件值空串 warn（silent-failure M-1）/ `_load` 类型注解 `dict`→`dict[str,object]`（LOW）/ persona `log=None` 时 logging 兜底（LOW）/ `resolve()` 跟 symlink 自定位 defense-in-depth（security LOW-1）/ `build_env_for_sdk` 泄 `PA_DEV_MODEL` 到目标面 SDK（ADR-0001 边界，security LOW-3）
- review architect 解耦演进（非阻断，本 change 外，architect 补派确认「主路径隔离」达成、「彻底职责剥离」是后续）：(a) 彻底拆 `PA_*_MODEL` 注入出 `_load_claude_settings_env`（当前认证函数仍持 `PA_*_MODEL` 命名规约 → 拆独立注入器或纯文件 + cron 显式 export，MED）；(b) `_KNOWN_KEYS` 从 `.claude/agents/pa-*.md` frontmatter 自动枚举 agent_name（当前硬编码 9 key 闭集 + 注释标明，新增路由要手改，MED）；(c) env→warn→fallback→log 解析模板抽 `resolve_route_with_override(env_key, file_value, label, log)` helper（dev 路径先 adopt，双胞胎 persona 保留显式同形可审计，MED）
