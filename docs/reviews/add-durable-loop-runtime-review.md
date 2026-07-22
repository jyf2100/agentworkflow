# add-durable-loop-runtime 最新实现评审

评审日期：2026-07-22
评审基线：本地 `main`，提交 `cef8191`
远端实现范围：`7860a10..3ceb052`
设计依据：`openspec/changes/archive/2026-07-22-add-durable-loop-runtime/`
评审结论：**不通过。模块级实现和单元测试覆盖较完整，但生产控制面没有完成 cutover，OpenSpec 归档及“49 项全部完成”的声明过早。**

## 一、验证结果

使用 Python 3.12 隔离环境安装 `pyproject.toml` 声明的依赖，并执行统一质量命令：

```bash
PATH=/tmp/pa-review-venv312b/bin:$PATH \
PYTHON=/tmp/pa-review-venv312b/bin/python \
bash scripts/quality.sh
```

结果：

- `compileall`：通过；
- `pytest`：`500 passed in 3.80s`；
- `ruff`：失败，11 个 `F401` 未使用导入错误；
- 统一质量命令退出非零。

因此 task 8.8 所声明的“full repository quality command 通过”不成立。归档目录也没有保存测试、sandbox、recovery 或 telemetry 的实际通过凭证。

## 二、阻断性发现

### P0-1：六个能力开关中只有 journal shadow 接入生产 dispatch

`feature_flags.py` 定义了：

- `journal_shadow`；
- `journal_driven_dispatch`；
- `session_aware_retry`；
- `lifecycle_hooks`；
- `container_sandbox`；
- `telemetry_export`。

但生产入口 `run_daily.py` 只读取并使用 `journal_shadow`：

- `run_daily.py:48-51` 只导入 flags、IDs、ShadowJournal 和 artifact store；
- `run_daily.py:1238-1239` 只解析 `resolve_flags(...).journal_shadow`；
- `run_daily.py` 没有调用 `journal_driven_dispatch`、RetryPolicy、HookAdapter、ExecutionSandbox、TelemetrySink 或 OTLP exporter。

`dev-agent.py` 也没有把 HookAdapter 注入 `ClaudeAgentOptions`，仍只有第一阶段 `can_use_tool` 回调。

`cutover.py:315-341` 的 `run_dispatch_cutover_drill()` 是独立测试 helper，不在 `run_daily.py` 的真实 dispatch 路径上。设置 `PA_LOOP_JOURNAL_DRIVEN_DISPATCH=true` 不会让 journal reducer 接管生产决策。

**影响**：任务 4、5、6、7 和 8.6 的模块虽然存在，但真实 cron/dispatch 不会使用它们。系统运行语义仍主要是第一阶段实现。

### P0-2：journal 不是当前恢复真源，PRD 仍被修改

设计要求 journal 成为恢复真源，并保持原始 PRD 不可变。当前实现明确仍处于双写阶段：

- `run_daily.py:1137-1143` 注释承认 PRD 追加的摘除留给 driven 阶段；
- `run_daily.py:1156-1157` 仍然直接追加 PRD；
- `run_daily.py:1392-1394` verify revise 路径继续调用该函数；
- `run_daily.py:1237` 所有 verify round 共用固定 `iteration_id(..., 0)`。

此外 `prd_id()` 支持内容 hash，但生产调用 `loop_ids.prd_id(path)` 未传内容 hash。PRD 被追加后 ID 仍不变，无法证明恢复上下文来自同一份不可变输入。

**影响**：journal 无法独立重建真实 retry 上下文，session-aware recovery 不能在生产路径成立。

### P0-3：OpenSpec 归档缺少真实 cutover 和质量证据

归档 tasks 将 49 项全部勾选，但：

- task 8.1 要求一个真实 dry-run，归档中没有对应输出；
- task 8.2 要求白名单项目 hook canary，生产 SDK 未接 HookAdapter；
- task 8.5 要求 Node/Python container canary，当前只有 FakeContainerRunner 测试；
- task 8.6 要求 journal-driven dispatch，生产入口没有接管；
- task 8.8 要求统一质量命令通过并归档凭证，本轮实跑失败且归档无凭证。

`cutover.py:356-376` 的 quality gate 只聚合调用方传入的测试数字；`test_cutover.py:303-337` 使用人工构造的 `passed=100`，没有执行 `quality.sh`。

**影响**：归档状态和实际交付成熟度不一致，不能将变更视为已完成。

## 三、高优先级问题

### P1-1：语义 verify 判红可能被 journal 误记为 published

`run_daily.py:1372` 将 `pa-verify` 结果放在 `verify_verdict`，而 `_sj_terminal()` 在 `run_daily.py:1199-1203` 只检查独立测试结果 `rec["verify"]["pass"]`。

当独立测试绿色、但 `pa-verify` 因需求未满足返回 `revise` 且轮次耗尽时：

1. `reconcile_pr(..., interrupted=True)` 产生 `interrupted_pr`；
2. `rec["verify"]["pass"]` 仍为 `True`；
3. `_sj_terminal()` 发出 `published`，忽略 `verify_verdict="revise"`。

现有测试只覆盖 `interrupted_pr + verify.pass=False`，没有覆盖“测试绿但语义验收红”。

**影响**：shadow parity 可能产生假绿终态；未来一旦 journal-driven 真正接管，会把未通过语义验收的产出标记为成功发布。

### P1-2：container network allowlist 没有形成真实 egress 边界

`container_sandbox.py:131-136` 对非空 allowlist 的处理是：

```text
--network pa-egress
--label pa.network_allowlist=...
```

Docker label 不会执行域名过滤。代码注释也承认 egress 规则依赖“部署侧”实现，但仓库中没有创建或校验这些规则。

`container_sandbox.py:262-280` 只检查调用者传入的 `requested_hosts`。Agent 在 Bash 内直接执行 `curl`、包管理器或其他网络命令时，不会自动向该参数报告真实目标。

**影响**：代码把 tier 标记为 higher assurance，但非空 allowlist 下的网络限制并未由该实现强制保证，安全声明高于实际能力。

### P1-3：journal 尾部损坏检测过度宽松

`journal.py:107-116` 将最后一条非空行的任何解析或模型构造错误都视为“尾部截断”并静默丢弃。

设计只允许忽略“不完整的最后一条记录”。当前实现还会忽略：

- 完整但非法的 JSON；
- 缺失必填字段的完整 JSON；
- 类型错误或 schema 不合法的完整尾记录。

**影响**：最后一条已完整写入但损坏/被篡改的 committed event 可能被当作正常崩溃截断，恢复到错误的前态。

### P1-4：测试证据 artifact 写入失败仍可形成 fresh green

`hook_adapter.py:180-189` 在 artifact store 写入失败时吞异常并令 `artifact_ref=None`；随后 `hook_adapter.py:193-198` 仍可把退出码 0 的测试更新为 fresh green TestEvidence。

这与“证据完整性失败不得伪装为验证绿”的设计目标不一致。至少测试输出工件或其 digest 持久化失败时，应阻止 Stop hook 放行或产生显式 evidence-integrity failure。

## 四、中优先级问题

### P2-1：runbook 包含不存在的恢复命令

`operator-runbook.md:44` 指示运行：

```bash
python3 scripts/recover_from_snapshot.py --run <run_id> --snapshot <digest>
```

仓库没有 `recover_from_snapshot.py`。发生 journal 中部损坏时，运维无法按文档执行恢复。

### P2-2：部分终态仍没有 journal 映射

`run_daily.py:1183-1184` 和 `test_shadow_dispatch.py:64-68` 明确保留：

- `orphan_deleted`；
- `stalled`；
- 部分 planned smoke。

这些状态不发终态 event。task 8.6 已勾选完成后，该 first-cut 缺口不应继续存在。

### P2-3：质量基线自身不绿

Ruff 报告 11 个可修复的未使用导入，涉及：

- `container_sandbox.py`；
- `cutover.py`；
- `hook_adapter.py`；
- `otlp_export.py`；
- `reconcile.py`；
- `recovery_context.py`；
- 三个测试模块。

这不是核心运行 bug，但意味着归档时没有实际运行仓库声明的唯一质量命令。

## 五、已确认有效的实现

本轮不是否定所有第二阶段工作。以下模块级能力有明确实现且对应单元测试通过：

- versioned journal event、状态 reducer 和非法迁移检测；
- SHA-256 artifact store、digest 校验和基础脱敏；
- failure fingerprint、progress signals 和 RetryPolicy 决策表；
- session metadata、recovery context 和 reconcile helper；
- HookAdapter 六类事件的 mock contract；
- local/container sandbox 接口及 fake runtime 行为；
- metadata-only telemetry、trace context 和 exporter 降级模型；
- compatibility reader、shadow parity helper 和 crash drill harness。

因此成熟度判断应为：

```text
模块与单元测试：约 70%
生产集成与真实 canary：约 20%
整体 OpenSpec 可验收完成度：约 40%-50%
```

## 六、修复与重新验收顺序

1. 撤销或更正 OpenSpec 完成声明，将变更恢复为 active/revise 状态。
2. 先让 `quality.sh` 全绿，并把真实输出作为归档凭证保存。
3. 修正 published 判定，终态必须同时满足独立测试绿色和 `verify_verdict=pass`。
4. 完成 production adapter：把 journal reducer、RetryPolicy、HookAdapter、sandbox 和 telemetry 接入 `run_daily.py`/`dev-agent.py`。
5. 停止修改 PRD，按 iteration 生成真实 retry ID，并从 journal artifacts 构造下一轮上下文。
6. 将 journal write/reduce 放到每个外部副作用之前，恢复时先 reconcile，再执行副作用。
7. 对 network allowlist 提供可验证的 egress enforcement；无法保证时不得标 higher assurance。
8. 补真实 SDK hook canary、真实 Node/Python container canary、OTLP outage 和四个副作用边界 crash drill。
9. 修复 runbook 中不存在的恢复命令，并实际演练一次。
10. shadow parity 对真实 dispatch 输出连续验证后，再开启 journal-driven cutover并保留 legacy fallback 一个发布周期。

## 七、最终判定

**判定：不通过（Not Ready，归档过早）。**

最新代码已经从“只有设计”进展到“多数能力有独立模块和 500 个通过的单元测试”，这是实质性进展。但核心运行路径仍是第一阶段 dispatch 加 shadow journal，第二阶段的恢复、安全和可观测能力尚未真正控制生产执行。修复上述 P0/P1 问题并提供真实 canary 与质量凭证前，不应宣称 `add-durable-loop-runtime` 已完成。
