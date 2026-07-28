# Tasks — 2026-07-28-add-stage-output-contract-repair

> 分两层：**Phase 0 止血**（P0，消灭整晚 abort，当天可上）→ **Phase 1-4 治本**（契约校验 + 诊断 + 纠正重试层）。
> TDD：每条实现先写 test（RED）再实现（GREEN），参照 `test_learning_memory_schema.py` 模式。纯 stdlib。

## 落地进度（2026-07-28）

- ✅ **Phase 0 止血**（见下）：`run_daily.py:766-781`，quality 绿。
- ✅ **Phase 1+3 模块**：`scripts/stage_contracts.py`（Issue/Contract/CONTRACTS/validate_stage/render_repair_hint + CriticContract/PrdContract 注册）+ `test_stage_contracts.py`（13 例）。
- ✅ **Phase 2 接线**：`run_daily.py:399-422` run_persona 语义层 + `test_run_persona_contract.py`（2 例）。
- ⚠️ **Phase 2 决策注记**：tasks 2.3 原「扩展 cap 至 3」改为「复用现有 `for attempt (1,2)` 作语法+语义共享预算 cap=2」——最小侵入、fail-open 兜底；error→render_repair_hint 诊断重试，预算尽 fail-open 降级返回，warning 记 log 不改行为。若实测契约违反率高再扩 cap。
- ✅ **Phase 4.1/4.4**：log 事件（warning/error 都记）+ quality.sh 全绿（1274 passed / 5 xfailed / ruff clean）+ openspec validate 通过。
- ✅ **Phase 4.2/4.3**：RUNBOOK 第 7 节「Persona 输出契约违反」（识别关键词 / degraded 路径 / 反复违反的运营动作）+ CONTEXT.md 入档「stage 输出契约 (stage_contracts)」术语。CLAUDE.md 不再单列（CONTEXT 是术语真理源、CLAUDE.md 已指向「先读 CONTEXT.md」）。

## Phase 0 — 止血（P0，~5 行）

- [x] 0.1 **核验**（2026-07-28 落地）实际行号：verdict crash 在 `run_daily.py:772`（非初稿 798-800）、path crash 路径 `768 _critic_one(None) → 794 Path(path).stem`、`_run_pipeline except RuntimeError` 屏障在 2822、`prd_abs` 脏数据通道在 1836。工作区安全（未提交 diff 是 `stage_dispatch` 的 DISPATCH_SKIP_PROJECTS 降噪，与 critic 段不重叠）。
- [x] 0.2 **止血 critic `verdict`**：`run_daily.py:773-781` —— `if "verdict" not in entry` 缺失时 setdefault 补 `drop` + summary，不 `entry["verdict"]` KeyError 穿透。test：`test_critic_tourniquet.py::test_critic_missing_verdict_degrades_not_crash`。
- [x] 0.3 **止血 critic `path`**：`run_daily.py:766-771` —— `if not path` 缺失降级 drop + `continue`，不调 `_critic_one`（避 794 TypeError）。test：`test_critic_missing_prd_path_degrades_not_crash`（stub 还原 `Path(None)` 崩溃证 RED）。
- [x] 0.4 `bash scripts/quality.sh` 全绿（1259 passed, 5 xfailed, ruff clean），止血不回归。新增 `test_critic_normal_verdicts_unaffected` 防 pass/drop 被误降级。

## Phase 1 — `stage_contracts.py` 模块（纯 stdlib，零 SDK）

- [ ] 1.1 `Issue` 数据类（frozen）：`field: str`（字段路径，如 `verdict` / `prds[2].path`）、`severity: str`（`"error"` | `"warning"`）、`diagnosis: str`（人读 + 喂 repair_hint）。
- [ ] 1.2 `Contract` 协议/抽象：`validate(payload) -> list[Issue]`（纯函数；malformed payload 不抛，返 error Issue）。
- [ ] 1.3 `CONTRACTS: dict[str, Contract]` 注册表 + `get_contract(stage) -> Contract | None`（None = 该 stage 第一版无契约，跳过校验 = 现状行为）。
- [ ] 1.4 `render_repair_hint(issues, bad_excerpt, *, attempt) -> str`：把 error Issue 渲染成「中颗粒度」纠正提示（指明字段路径 + 问题，不给答案；明确要求重新输出完整合规 JSON，禁止只给补丁）。空 issues / 全 warning → 返 `""`（no-op）。第二 attempt 起加「这是第 N 次，你上次已被告知 X」。
- [ ] 1.5 `validate_stage(stage, payload) -> list[Issue]`：fail-open wrapper——registry 缺/validator 异常 → 返 `[]`（不校验，降级现状），绝不抛主路径（对齐 learning_memory design 决策#7）。
- [ ] 1.6 测试：malformed payload / 缺字段 / 受控值越界 / 类型错 / warning-only / fail-open（validator 抛 → []）。参照 `test_learning_memory_schema.py`。

## Phase 2 — `run_persona` 接线（语义层叠在语法层上）

- [ ] 2.1 语法 parse 成功后、返回前插 `issues = validate_stage(stage, payload)`。
- [ ] 2.2 有 `error` Issue → 不返回，构造 `render_repair_hint(issues, excerpt, attempt)` append 进 `cur_prompt`，进下一 attempt。
- [ ] 2.3 attempt 预算：现状 `for attempt in (1,2)` → 扩展为「语法失败 + 语义 error 共享重试预算」，cap（如 3）防「诊断→重试→再诊断」慢循环。
- [ ] 2.4 `last_err`（语法诊断，现状只进 log）并进同一 repair_hint 通道——语法 error 也带诊断重试（现状的 `_JSON_RETRY_SUFFIX` 静态串升级为带诊断）。
- [ ] 2.5 仅 `warning` Issue → 记 log（可观测），**不改行为**（payload 照常返回，现状宽容语义字节不变）。
- [ ] 2.6 测试：mock persona 第一次吐缺字段 payload、第二次吐合规 → 第二次返回成功；error 超预算 → raise；warning → 正常返回 + log。

## Phase 3 — critic + prd 契约定义与接线

- [ ] 3.1 `CriticContract`：`verdict` error（必填 + `∈ {pass, drop, revise}` 受控值，越界 = error Issue「verdict 必须 ∈{pass,drop,revise}，实际 <x>」）；`prd_path` error（必填 + 非空）；其余字段 warning。
- [ ] 3.2 `PrdContract`：`prds[i].path` error（每个 prd 必填 + 非空）；其余 warning。
- [ ] 3.3 注册 `CONTRACTS = {"critic": CriticContract(), "prd": PrdContract()}`。
- [ ] 3.4 接线点核验：critic 走 `run_persona(..., "critic", ...)`（~795）、prd 走 `run_persona("pa-prd", ..., "prd", ...)`（~742）——确认 `stage` 参数能命中 CONTRACTS。
- [ ] 3.5 测试：critic payload 缺 verdict / verdict="unknown" / 缺 prd_path → error Issue → 带诊断重试；prd payload 缺 path → error → 重试 prd。

## Phase 4 — 可观测性 + 文档

- [ ] 4.1 契约违反事件记 log（stage / field / severity / diagnosis / attempt / 是否重试成功），供运维定位「哪个 persona 反复漏吐哪个字段」。
- [ ] 4.2 RUNBOOK 补「persona 输出契约违反」排查节（degraded 路径 + 反复违反的运营动作）。
- [ ] 4.3 CLAUDE.md / CONTEXT.md 补 `stage_contracts.py` 与 CONTRACTS[stage] 的约定（新模块入档）。
- [ ] 4.4 `bash scripts/quality.sh` 全绿；`openspec validate` 通过。

## Follow-ups（明确 Non-goal，不在此 change）

- [ ] dev-agent（dispatch 段，不走 run_persona）的 L1 格式校验接入。
- [ ] fetch / radar / verify 的 CONTRACTS（全软，按需再上）。
- [ ] persistent 闭环：反复违反聚合 → persona 定义补丁报告（谁改 `.claude/agents/pa-*.md` 是独立治理问题）。
