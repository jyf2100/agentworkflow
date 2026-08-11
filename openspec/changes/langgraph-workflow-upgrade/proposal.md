## Why

pa 的 7 阶段编排现在是 `run_daily.py` 命令式状态机（~3400 行）：`if lo<=N<=hi` 顺序调度 + 命令式 critic/verify 回环 + enum 硬编码升人工路径（`interrupted_pr` / `off_track` / `halted` / `cooldown` 散落各处）。两个痛点（grilling 后收敛——原 3 痛点中的「升人工路径缺原生语义」「持久化职责未分离」经 `/grilling` 盘问不再成立：native interrupt 在无头 cron 行不通、Checkpointer 不要，见 design D2/D5）：

- **流程不显式**：7 阶段顺序 / revise 回环 / verify 闭环 / dispatch 并行 / learning 5 段埋在命令式 if-for 里，新人难一图懂全貌；新加 stage 要改调度主干。
- **边界靠人守**：verdict 机械/语义边界 + install 藏输入类 bug 靠人记忆与 review，无类型层守护。

LangGraph 的 StateGraph / conditional edges / 子图对症（interrupt / Checkpointer / Send 经 grilling 否决——见 design D1/D2/E1）。**关键收敛点**：claude runtime（persona 子进程 / SDK dev loop）零改动，durable 特化容错层（journal / reconcile / cutover / single_flight / circuit_breaker / learning_memory / retry_policy）作 graph 边界调用的**纯函数库保留**——重构面收敛在「编排表达层」，不动 pa 护城河。

动机：① graph 重塑流程表达；② 类型层守边界（verdict 只在 PersonaNode + ArtifactHandle 路径契约）；③ 技术栈收敛（删 ~1500 行命令式状态机）。

## What Changes

- **新增 LangGraph StateGraph 编排层**（`scripts/graph_pa*.py`）：7 阶段 = 通用 node 配置实例，流程拓扑显式可读；新加 stage = 配置一个通用 node 实例 + 加 graph 边，零新 node 代码。
- **通用 node 抽象（4 类）+ 规范 I/O 契约**：`PersonaNode` / `DevLoopNode` / `MechanicalNode` / `GatewayNode`；统一 `NodeInput`（标识/上下文/配置/上游产物/恢复/可观测性 6 类字段）+ `NodeOutput` envelope（status/artifacts/verdict/side_effects/obs/error/idempotency_key 7 字段）。**verdict 只在 PersonaNode 类型层暴露写入**——机械/语义边界从「靠人守」升级为「靠类型守」。
- **ArtifactHandle 路径契约**：裸 `path: str` 升级为 `store`(vault/worktree/tmp) + `rel_path` + `digest` + `must_exist`；跨面隔离（ADR-0001）类型层守（worktree 写入只由 DevLoopNode 暴露）；**结构性根治 install 藏输入 bug**（`install_log` 成规范 artifact，`must_exist=True` 强制传递，pa-verify 经 NodeInput 拿到自行判断）。
- **journal 单写真源（grilling 后从方案 3 收敛）**：放弃 native interrupt（无头 cron 无人在循环中提供 resume 值）→ Checkpointer 的 resume 价值消失 → **不用 Checkpointer**。journal 独承载事件流 + reconcile 真源（append-only + fsync + 中部损坏 fail-closed 不变）。崩溃恢复走 `recovery_cli → recover_iteration(journal)` 重建 initial state → `graph.invoke` 续跑（节点幂等，靠 reconcile + idempotency_key）。LangGraph 只取图表达（StateGraph + 条件边 + 子图）。
- **升人工路径保留 enum**（grilling 否决 native interrupt）：`interrupted_pr` / `off_track→triage` / `blocked_external` / `halted` / `cooldown` 保持现状 enum + 状态机；graph 条件边机械路由这些终态（不替判）。跨 cron（跨 thread）的 cooldown/halt 仍走 circuit_breaker / critical_alert 独立 journal。
- **feature_flag 渐进 cutover**：`pa_graph_shadow` / `pa_graph_orchestrator`（镜像 `single_flight_*` 双 flag 模式）+ 复用 `cutover.py` 三重 gate（flag + parity + allowlist）+ 一键秒回退（`unset PA_GRAPH_ORCHESTRATOR` → 下个 cron 周期回 legacy）。
- **重构契机一并修债**：边界审查（`docs/orchestrator-boundary-audit-2026-08-11.md`）4 处偏离——install 藏输入（层 1）+ 3 处编排器产 `drop` 改 `triaged`（层 2）——在 graph 化时一并修。
- **BREAKING（内部）**：`run_daily.py` 的 `main()` / 命令式 `STAGES` 调度在 Phase 4 下旧（留 1 release cycle 双路径共存，feature_flag 秒回退兜底）；`scripts/` 扁平结构新增 `graph_pa*.py`（**非包**，守 CLAUDE.md「不要改成包结构」）。

## Capabilities

### New Capabilities

- `langgraph-workflow-orchestration`: LangGraph 编排层——StateGraph 7 阶段拓扑（含 critic/verify/merge 子图）+ 4 类通用 node 抽象 + 规范 I/O 契约（NodeInput/NodeOutput/ArtifactHandle，TypedDict + 中心化验证）+ 回环表达（node 内 / 跨节点条件边）+ journal 单写真源（不用 Checkpointer）+ 升人工路径保留 enum + 标准化监控（obs schema + report 聚合，LangFuse 留 follow-up）+ feature_flag 渐进 cutover（三重 gate + 秒回退）+ 边界不变式类型层守护（verdict 只在 PersonaNode）+ 编排器侧守同步（不引入 asyncio）。

### Modified Capabilities

- 无既有 spec delta。现有 11 个 capability（`durable-loop-state` / `fail-safe-dispatch` / `verified-dev-execution` / `runtime-cutover-evidence` / `session-aware-retry` / `loop-lifecycle-controls` / `cross-prd-learning-memory` / `durable-runtime-integration` / `isolated-observable-execution` / `reproducible-pipeline-validation` / `verified-publication-integrity`）需求级 behavior **不变**——journal / reconcile / cutover / single_flight / circuit_breaker / learning_memory / retry_policy 作 graph 边界调用的纯函数库原样保留；LangGraph 是新增的编排表达层，与既有能力层解耦，不污染既有规约。

## Impact

- **新依赖**：`langgraph>=1.2.10,<2`（避撤回的 1.2.3/1.1.7；**不需要** `langgraph-checkpoint-sqlite`——不用 Checkpointer，依赖减负；+ pydantic 间接，pa 不主动依赖；Phase 0 spike 确认与 cron 极简 PATH / claude-agent-sdk `>=0.2.128,<0.2.130` 共存）。
- **代码（新增）**：`scripts/graph_pa.py`（拓扑）/ `graph_pa_nodes.py`（4 类通用 node 工厂）/ `graph_pa_state.py`（TypedDict state）/ `graph_pa_contracts.py`（NodeInput/NodeOutput/ArtifactHandle schema）/ `check_boundary.py`（机械/语义边界 lint，扫「非 persona node 产 verdict」）。
- **代码（下旧，Phase 4）**：`run_daily.py` 的 `main()` / `acquire_run_lock` / `_run_one` / 命令式 `_run_pipeline`；**保留** `stage_*` 纯函数 + `run_persona`（被 graph node import 复用，零重写）。
- **不变（核心资产）**：`dev-agent.py`（ADR-0006 唯一执行器）/ claude runtime（persona subprocess + SDK dev loop）/ journal / reconcile / cutover / feature_flags / single_flight / circuit_breaker / learning_memory / retry_policy（纯函数库）/ claude-agent-sdk 版本钉 / per-agent model routing。
- **cron**：`run_cron.sh` 加 flag 分流（`PA_GRAPH_ORCHESTRATOR=1` → `graph_pa.py`，else → `run_daily.py`）。
- **测试**：~65 个测纯函数模块**零改动复用**；`test_baseline_regressions.py` / `test_run_persona_contract.py` 改 import 路径；~4 个测 dispatch wiring 的重写为测 graph 拓扑；新增 graph 拓扑 / state-serialize / parity / cron-path / sdk-compat / concurrent-write 测试。
- **风险**：① cron PATH 找不到 LangGraph 依赖（致命，流水线全线挂）；② claude-agent-sdk 0.2.128 与 LangGraph 共存（编排器侧守同步不引入 asyncio，耦合面小）；③ 跨节点回环节点非幂等导致重入副作用（靠 reconcile + idempotency_key）；④ dispatch 子图漏迁 single_flight/circuit_breaker/merge_loop 协同；⑤ LangGraph 版本绑定（breaking change 频繁）。均经 Phase 0 共存 spike + 5 Phase 渐进 cutover + feature_flag 秒回退 + cutover.py 7 维 evidence suite 缓解。
