# stage-output-contracts

> Capability：persona / stage 输出契约的定义、校验、诊断与纠正重试。
> 横切所有走 `run_persona` 的 stage。第一版覆盖 `critic` + `prd` 两个 stage 的硬契约字段，
> 其余字段与 stage 为 warning / 未注册（保持现状宽容语义）。

## ADDED Requirements

### Requirement: Stage output contract registry and validation

每个走 `run_persona` 的 stage，其输出 payload 在语法 parse 成功后、返回前，MUST 先交给该 stage 已注册的 `Contract` 校验并产出 `Issue` 列表；未注册 stage MUST 跳过校验，行为与现状字节一致。

- 契约是**机械可验证的事实**（字段存在性 / 受控值 / 类型），不是 LLM 语义判断——故无需 cross-PRD recurrence，可当轮即时回流。
- `Issue` MUST 含 `field`（字段路径，如 `verdict` / `prds[2].path`）、`severity`（`error` | `warning`）、`diagnosis`（人读 + 喂重试提示）。

#### Scenario: 已注册 stage 的硬契约字段缺失被检出

WHEN critic persona 返回合法 JSON 但缺 `verdict` 字段
THEN `validate_stage("critic", payload)` 返回含 `Issue(field="verdict", severity="error", diagnosis=...)` 的列表
AND 该 payload 不被当作合规返回。

#### Scenario: 未注册 stage 不校验，行为不变

WHEN radar persona 返回 payload 且 `CONTRACTS` 无 `"radar"` 注册
THEN `validate_stage("radar", payload)` 返回 `[]`（no-op）
AND `run_persona` 按现状直接返回 payload，不重试。

### Requirement: Error vs warning severity (字段级严格度)

严格度 MUST 是字段级，不是 stage 级一刀切。
`error` 字段（硬契约：缺失/越界让 stage 无法继续或产生脏数据）MUST 触发纠正重试；
`warning` 字段（软契约：现状 `.get()` 降级能继续）MUST 只记可观测事件，MUST NOT 改 stage 行为（payload 照常返回，宽容语义字节不变）。

- 第一版硬契约：`CONTRACTS[critic] = {verdict (含受控值 ∈{pass,drop,revise}), prd_path}`、`CONTRACTS[prd] = {prds[i].path}`；其余字段 warning。
- 这保证语义校验 MUST NOT 把「现状能跑通的宽容场景」判红降级。

#### Scenario: 受控值越界判 error

WHEN critic persona 返回 `verdict="unknown"`（合法 JSON，但非受控值）
THEN `validate` 返回 `Issue(field="verdict", severity="error", diagnosis="verdict 必须 ∈{pass,drop,revise}，实际 'unknown'")`
AND 触发纠正重试（不只查非空，查受控值）。

#### Scenario: 软契约字段缺失只记 warning 不改行为

WHEN critic persona 缺 `issues` 字段（软契约）
THEN `validate` 返回 `Issue(field="issues", severity="warning", ...)`
AND `run_persona` 照常返回 payload（现状 `payload.get("issues", [])` 语义不变），仅记 log。

### Requirement: Contract-violation diagnosis and repair retry

`error` Issue MUST 触发带诊断的纠正重试：把 Issue 诊断渲染成 `repair_hint`，append 进下一 attempt 的 prompt（与 dev-agent `--feedback-artifact` 范式对称）。
重试 MUST 与语法层失败共享有上限的预算（防「诊断→重试→再诊断」慢循环）；超预算 MUST 照旧 raise / 降级。
`repair_hint` MUST 取「中颗粒度」——指明字段路径 + 问题，不给答案（防 persona 机械填字段应付、下次还犯）。

#### Scenario: 缺字段 persona 被诊断后重试成功

WHEN pa-prd 第一次返回缺 `prds[i].path` 的 payload
THEN `run_persona` 不返回，构造 repair_hint（「prds[2] 缺 required field 'path' …请重新输出完整合规 JSON」）append 进 prompt 重试
WHEN 第二次返回合规 payload
THEN `run_persona` 返回该 payload，记一次「重试成功」可观测事件。

#### Scenario: 重试超预算照旧失败

WHEN persona 连续 N 次（达预算 cap）仍吐 error payload
THEN `run_persona` 按现状 raise RuntimeError（不无限重试）
AND 记录失败诊断供运维定位。

### Requirement: Fail-open contract layer

契约层自身故障（registry 读不到 / validator 抛异常 / repair_hint 渲染失败）MUST NOT 改 stage 终态——
MUST 降级回现状行为（不校验、不重试、按现有 `.get()` 走），对齐 `cross-prd-learning-memory` design 决策#7「fail-open for delivery」。
重试成功 = 本该成功；重试失败 = 照旧 raise / 降级。契约层是纯增益，不是新依赖。

#### Scenario: validator 抛异常降级不崩

WHEN 某 stage 的 `Contract.validate` 实现抛异常
THEN `validate_stage` 捕获并返回 `[]`（fail-open）
AND `run_persona` 按现状返回 payload，stage 终态不受契约层故障影响。
