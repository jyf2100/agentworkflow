# Proposal — 2026-07-28-add-stage-output-contract-repair

> Originated in `/opsx:explore` (2026-07-28) 头脑风暴「在出错的地方总结经验教训，再重试当前步骤」。
> 探索收窄到一类具体失败：**persona 输出语义不合规**（合法 JSON 但缺 required 字段 / 受控值越界 / 类型错）。
> 契约现状由只读反推得出（见 `## Why` 证据），落地前须亲自核验所引行号。

## Why

`run_daily.py:358 run_persona()` 是所有 persona 调用（fetch / radar / prd / critic / verify）的唯一入口，返回 `(payload, meta)`。现状**只校验语法**（两层 `json.loads` + `_extract_first_json` 容错 + 1 次静态 `_JSON_RETRY_SUFFIX` 重试），**不校验语义**——payload 能 parse 即返回，字段缺失/类型错/受控值越界全部散落在各调用方的 `.get(key, default)` 三件套里静默处理。

反推 6 个 stage 的输出现状（契约表初稿，证据见下）得到三个结论：

1. **唯一会 CRASH 的 stage 是 critic**——其余 fetch/radar/prd/verify/dev-agent 全软（0 CRASH，`.get()` 降级继续）。critic 有两个 CRASH 点，都是 KeyError/TypeError，**穿透 `_run_pipeline` 的 `except RuntimeError` 屏障**（`run_daily.py:2822` 只接 RuntimeError）→ 当晚整条 cron 流水线 abort（所有项目都不推进）：
   - `entry["verdict"]` 直接下标（`run_daily.py:798-800`，不在 try 内）→ critic persona 漏吐 `verdict` → KeyError → 整晚 abort。
   - `Path(prd.get("path")).stem`（`run_daily.py:766/794`）→ 上游 prd persona 漏吐 `prds[i].path` → `Path(None)` TypeError → 整晚 abort。

2. **一个脏数据通道**：critic payload 的 `prd_path` 不 `setdefault` → `entry.get("prd_path","")` → `prd_abs = str(VAULT_ROOT / "")` = vault 根（`run_daily.py:1836`，已第一手核验）→ dev-agent `read_text` 读目录失败 → `return 10` → **静默 `dev_killed` 降级**（项目当晚没推进，原因藏在 critic 漏吐一个字段，谁也看不见）。注：非 vault 内容泄漏——`read_text` 读目录会失败挡住；后果是「可重试的契约违反变成静默失败」。

3. **现状重试的「教训」是静态、无诊断的**：`run_persona` 重试时只追加固定 `_JSON_RETRY_SUFFIX`（「加强 JSON-only 契约」），不告诉 persona 第一次到底错在哪。而 `last_err`（如 `Expecting ',' delimiter: line 5`）其实已精确生成，**只进了 log/raise，没接回重试 prompt**。

**根因**：persona 输出契约从未被显式定义与校验。语法层有 `run_persona` 中心化兜底，语义层是「调用方各自 `.get()` 默认」的隐式、散落、无诊断的防御——缺了就填默认往下走，唯独 critic 两处破例用了直接下标/构造，于是成了定时炸弹。

这与已有的 `learning_memory`（cross-PRD 领域经验，慢回路、需 ≥2 PRD recurrence）是**两个不同对象**：本 change 沉淀的是 **persona 输出契约本身**（机械可验证事实，非 LLM 语义猜测，无需 recurrence，可当轮即时回流）。

## What Changes

分两层，**止血先行、治本叠上**：

### 止血（P0，~5 行，当天可上）

消灭「整晚 abort」——critic 两处破例改成与全流水线一致的防御：
- `entry["verdict"]` → `.get("verdict")` + 缺失显式降级（该 PRD 标 drop/gate-fail，不拖垮整条流水线）。
- `Path(prd.get("path"))` → 先判 None，缺失降级跳过该 PRD。

止血只防崩，仍是「降级」（没推进），不纠正。

### 治本（契约校验 + 诊断 + 纠正重试层）

新增纯 stdlib 模块 `scripts/stage_contracts.py`（与 `failure_analysis.py` / `recovery_context.py` 同族，cron 隔离友好）：

- **`CONTRACTS[stage]` 注册表**：每个 stage 注册一个 `validate(payload) -> list[Issue]`，`Issue = (field, severity, diagnosis)`。参照 `learning_memory_schema.candidate_from_model_output()` 的手写 validator 范式（带精确字段路径诊断 + 受控词表 + schema 边界 redaction）。
- **`render_repair_hint(issues, bad_excerpt)`**：把诊断渲染成纠正提示，append 进重试 prompt。范式与 dev-agent `--feedback-artifact`（verify 反馈注入下轮 prompt，`dev-agent.py:318`）完全对称——pa 早有「把上轮反馈注入下轮」的成熟模式，只是没用在大 persona 自身的输出修复上。
- **`run_persona` 接线**：在「语法 parse 成功后、返回前」插入 `CONTRACTS[stage].validate(payload)`；有 `error` issue → 构造 repair_hint 进下一 attempt（现有 `for attempt in (1,2)` 扩展，cap 防慢循环）；仅 `warning` issue → 记一笔不改行为（保持现状宽容语义）。`last_err`（语法诊断）也并进同一 repair_hint 通道。

### 第一版边界（用户拍板）

- `CONTRACTS[critic] = {verdict, prd_path}`：`verdict` 硬契约含受控值校验 `∈ {pass, drop, revise}`（不只非空——现状吐 `"unknown"` 能 parse 却静默漏下游）；`prd_path` 硬契约（堵脏数据通道）。
- `CONTRACTS[prd] = {path}`：`prds[i].path` 硬契约。**关键归属**——`path` 是 prd 的输出、critic 的输入，重试 critic 修不了（prd 没吐 path），必须在 prd 段出口校验 + 重试 prd 才是对的根治。
- 其余字段（issues / round / revised / project / source_path / stats / candidates 子结构 …）= `warning`，保持现状 `.get()` 降级，**不破坏宽容语义**。
- fetch / radar / verify / dev-agent 第一版**不上 CONTRACTS**（全软，0 CRASH）；dev-agent 不走 `run_persona`，留第二阶段。

## Capabilities

### New Capabilities

- `stage-output-contracts`：persona/stage 输出契约的定义、校验、诊断与纠正重试。横切所有走 `run_persona` 的 stage；error/warning 字段级严格度；fail-open（契约层故障不改 stage 终态，降级回现状）。

### Modified Capabilities

None（第一版不触碰 `fail-safe-dispatch` / `verified-dev-execution` 等现有 capability 的 requirement；止血的 critic 防崩是其内部实现加固，不改 spec 文字）。

## Impact

- 新增 `Projects/项目推进流水线/scripts/stage_contracts.py` + `test_stage_contracts.py`（纯 stdlib，参照 `test_learning_memory_schema.py` 测试模式）。
- 改 `run_daily.py`：`run_persona` 插入契约校验 + repair_hint 重试；critic 段两处止血；prd 段出口接 `CONTRACTS[prd]`。
- **fail-open 不变量**（对齐 `learning_memory` design 决策#7）：契约层自身故障（validator 异常 / repair_hint 渲染失败 / catalog 式的 registry 读不到）→ 降级回现状行为（不校验、不重试、按现有 `.get()` 走），**绝不改 stage 终态**。重试成功 = 本该成功；重试失败 = 照旧 raise/降级。
- **风险①**：语义校验可能把「现状能跑通的宽容场景」判红 → 用 error/warning 字段级区分兜底（软契约字段只 warning 不重试，行为字节不变）。
- **风险②**：repair_hint 喂太满 → persona 机械填字段应付、下次还犯 → 第一版取「中」颗粒度（指明问题 + 字段路径，不给答案）；persistent 闭环（反复违反 → 改 persona 定义）为明确 Non-goal，留 follow-up。
- 无控制面/目标面边界变化；无 immutable PRD / target-worktree state 变化；无 SDK 版本钉版变化。

## Non-goals

- 不做 persistent「契约违反 → 自动改 persona 定义」闭环（ephemeral 起步；谁改 persona 定义是独立治理问题）。
- 不覆盖 dev-agent（dispatch 段，不走 run_persona）；其 L1 格式校验留第二阶段。
- 不做 cross-PRD 领域经验沉淀（那是 `cross-prd-learning-memory` 的职责，对象不同）。
- 不改 `run_persona` 的语法层两层容错（`_extract_first_json` / `_JSON_RETRY_SUFFIX` 保留，语义层叠在其上）。
