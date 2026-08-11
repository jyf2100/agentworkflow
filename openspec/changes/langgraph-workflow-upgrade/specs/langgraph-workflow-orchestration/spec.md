# langgraph-workflow-orchestration

> Capability：LangGraph 编排层——用 StateGraph 显式表达 pa 7 阶段拓扑（fetch→radar→prd→inject→critic→dispatch→report，含 critic/verify/merge 子图），node 内 claude runtime（subprocess persona / SDK dev loop）零改动，编排器侧守同步（不引入 asyncio）。提供 4 类通用 node 抽象（Persona/DevLoop/Mechanical/Gateway）+ 统一 I/O 契约（NodeInput/NodeOutput/ArtifactHandle，TypedDict + 中心化验证），7 阶段是配置实例。回环表达：node 内回环（契约重试/dev loop）走节点函数体，跨节点回环（critic/verify revise）走条件边。journal 单写真源（不用 Checkpointer）。升人工路径保留 enum + 状态机（不用 native interrupt）。标准化监控（obs schema + report 聚合 + 可查询，LangFuse 自托管留 follow-up）。feature_flag 渐进 cutover + 秒回退。现有 durable 特化容错（journal/reconcile/cutover/single_flight/circuit_breaker 等）作纯函数库保留，需求不变。本 capability 是新增编排表达层，不污染既有 11 个 capability。

## ADDED Requirements

### Requirement: StateGraph 表达 7 阶段拓扑且 claude runtime 不变

编排层 SHALL 用 LangGraph StateGraph 的节点+边+条件路由显式表达 pa 7 阶段，含 critic revise 回环、verify revise 闭环、merge（rebase→merge→post-merge→revert）子图。每个 node 内的 claude 调用（subprocess persona / SDK dev loop）MUST 与现状 byte-identical——LangGraph 只替换「编排 claude 的自研 Python」（run_daily.py 命令式状态机），不替换 claude runtime。**编排器侧守同步**：node 函数 sync，不引入 asyncio 事件循环（asyncio 只存于 dev-agent.py 子进程内部，现状已如此）；dispatch 跨项目并行走 dispatch_node 内 ThreadPoolExecutor（node 内，graph 不见），不上 LangGraph Send API。

#### Scenario: 流程经 graph 边从 fetch 推进到 report

- **WHEN** cron 启动一个 thread_id=run_<stamp> 的 graph invoke
- **THEN** fetch_node → radar_node → prd_node → critic 子图 → dispatch 子图 → report_node 经 graph 边推进
- **AND** 每个 node 内仍经 subprocess 调 claude persona 或经 SDK 调 dev loop（claude runtime 零改动）

#### Scenario: claude-agent-sdk 版本钉不松绑

- **WHEN** LangGraph 引入后 dev_node 触发 dispatch
- **THEN** dev loop 仍经 dev-agent.py + claude-agent-sdk（>=0.2.128,<0.2.130）+ sdk_compat_patch
- **AND** LangGraph 不直接 import SDK（只经 subprocess 调用），耦合面隔离

#### Scenario: 编排器侧守同步，dispatch 并行 node 内 ThreadPool

- **WHEN** dispatch 跨项目（每项目一 dev-agent 子进程）并行
- **THEN** 并行走 dispatch_node 内部 ThreadPoolExecutor（node 内，graph 只看汇总 NodeOutput）
- **AND** 不上 LangGraph Send API（Send 要 async node，违守同步）
- **AND** graph.invoke 为 sync，编排器无 asyncio 事件循环

#### Scenario: 回环表达——node 内 vs 跨节点

- **WHEN** PersonaNode 输出契约不合规
- **THEN** node 内补 repair-hint 重调 1 次（节点函数体循环，graph 只见一次执行 + journal 记 repair_retry 事件）
- **WHEN** critic 判 revise 且 prd_round < MAX
- **THEN** graph 条件边 router 读 state 的 prd_round 路由回 prd_node（跨节点回环）
- **AND** 跨节点回环节点幂等（靠 reconcile + idempotency_key），state 带轮次计数器（prd_round/verify_round）判上限

### Requirement: 四类通用 node 抽象，配置驱动实例化

编排层 SHALL 提供四类通用 node 抽象：`PersonaNode`（控制面语义，subprocess 调 persona + 两层 JSON 解析 + 契约校验 + 1 次 repair-hint 重试）、`DevLoopNode`（目标面语义，SDK dev loop + 解析 exit 14/15/12 + worktree + session 续接）、`MechanicalNode`（零 LLM 机械活）、`GatewayNode`（fail-safe 门，UNKNOWN→blocked）。pa 7 阶段 MUST 是这四类的配置实例，而非各自手写。

#### Scenario: 新加 stage 不写新 node 代码

- **WHEN** 要新增一个编排阶段
- **THEN** 通过配置一个通用 node 实例（注入 agent_name/op/contracts/timeout）+ 加 graph 边完成
- **AND** 不新建独立的 node 实现文件

### Requirement: verdict 仅 PersonaNode 可写（类型层守边界）

语义判决（verdict: pass/revise/drop + reason + feedback）SHALL 只由 `PersonaNode` 产出（其背后是 claude 对抗 persona）。`DevLoopNode`/`MechanicalNode`/`GatewayNode` 的工厂 MUST NOT 在类型层暴露 verdict 写入接口。`check_boundary.py` lint SHALL 扫「非 persona node 产 verdict」并失败。编排器（graph node/edge）MUST 只读 verdict 做路由，不判定语义、不改写 verdict。

#### Scenario: 非 persona node 产 verdict 被 lint 拒

- **WHEN** MechanicalNode/GatewayNode/DevLoopNode 的实现写入 verdict 字段
- **THEN** check_boundary.py lint 报错（非 persona node 产语义判决）
- **AND** quality.sh 非 zero exit

#### Scenario: 编排器残缺输入改 triaged（修边界审查层 2）

- **WHEN** persona 输出残缺/异常（prd 缺 path / critic 漏吐 verdict / revise 异常）
- **THEN** graph 编排器产 `triaged`（升人工）
- **AND** MUST NOT 产语义 `drop`（守「编排器不替判死」）

### Requirement: 统一 node I/O 契约（NodeInput / NodeOutput envelope）

所有 node SHALL 接同一 `NodeInput`（6 类字段：标识 / 上下文路径 / 配置 / 上游产物 / 恢复上下文 / 可观测性）并返回同一 `NodeOutput` envelope（7 字段：status / artifacts / verdict / side_effects / obs / error / idempotency_key）。NodeInput / NodeOutput / ArtifactHandle SHALL 用 **TypedDict + 中心化验证函数**（`graph_pa_contracts.py` 的 `validate_node_output(d) → NodeOutput | raise`），守 pa 纯 stdlib 风格，MUST NOT 主动依赖 pydantic（即便 LangGraph 间接拉入）。每个 node MUST 吐 `obs`（cost/turns/duration_ms/model/token_usage）供 report node 统一聚合为**标准化可查询 metrics**（obs 是标准化监控的数据源，见决策 M）。

#### Scenario: 所有 node 吐统一 obs 元数据

- **WHEN** 任一 node 完成
- **THEN** NodeOutput.obs 含 cost/turns/duration_ms/model/token_usage
- **AND** report_node 机械聚合所有 node 的 obs（无需各 stage 单独埋点）

### Requirement: ArtifactHandle 路径契约

node I/O 涉及的文件路径 SHALL 用规范 `ArtifactHandle`（{kind, store, rel_path, digest, must_exist}），MUST NOT 用裸 `path: str`。`store` 三态：`vault`（控制面，相对 vault_root）/ `worktree`（目标面，相对 worktree_root）/ `tmp`（stamp 作用域）。state MUST 只存 `rel_path + store`（绝对路径 node 内解析，不入 state，可移植）。`store=worktree` 的写入 MUST 只由 `DevLoopNode` 暴露（ADR-0001 类型层守）。

#### Scenario: install_log 成规范 artifact，根治藏输入

- **WHEN** independent_verify 跑 npm ci
- **THEN** install 输出写为 ArtifactHandle（store=tmp, must_exist=True, digest=内容 sha）
- **AND** verify node 的 NodeInput.upstream_artifacts 强制含 install_log handle
- **AND** pa-verify 经 handle 自行 Read digest 对应文件并自行判断（语义归 persona，编排器不替判）

#### Scenario: must_exist artifact 缺失 → fail-closed

- **WHEN** node 入口校验 must_exist=True 的上游 artifact 且文件不存在
- **THEN** node 返回 status=blocked, error.code=missing_artifact
- **AND** MUST NOT 静默跳过

#### Scenario: 跨面隔离类型层守

- **WHEN** PersonaNode/MechanicalNode/GatewayNode 尝试写 store=worktree artifact
- **THEN** 类型层/工厂不暴露该写入接口
- **AND** 只有 DevLoopNode 经 dev-agent.py 在目标面写

### Requirement: journal 单写真源（不用 Checkpointer）

编排层 SHALL 用 journal 单写持久化：journal（append-only 事件流 + exactly-once reconcile 真源，中部损坏 fail-closed 不变）独承载持久化。**MUST NOT 用 LangGraph Checkpointer**（grilling 收敛：native interrupt 在无头 cron 行不通 → Checkpointer 的 resume 价值消失；journal 的 append-only + fsync + fail-closed 强契约优于 SQLite WAL，且透明、跨进程安全）。node 内完成副作用操作 MUST 先 `journal.append_event`(fsync) 再 return state。崩溃恢复 MUST 走 `recovery_cli → recover_iteration(journal)` 判 external_known + RetryPolicy.decide → 重建 graph initial state → `graph.invoke(state, thread_id)` 续跑（节点幂等，靠 reconcile + idempotency_key）。graph 拓扑从 journal 事件流重建，MUST NOT 依赖 Checkpointer 快照。

#### Scenario: node 副作用先 journal fsync

- **WHEN** node 完成一个有副作用的操作（commit/push/PR/test 终态）
- **THEN** 先调 journal.append_event 并 fsync
- **AND** 再 return state
- **AND** 单源无双源漂移风险

#### Scenario: 崩溃恢复走 journal 重建 initial state

- **WHEN** cron 在某 node 中途崩溃后重启
- **THEN** 走 recovery_cli → recover_iteration(journal) 判 external_known + RetryPolicy.decide
- **AND** 重建 graph initial state → graph.invoke(state, thread_id) 续跑
- **AND** 跨节点回环节点幂等（reconcile + idempotency_key 保 exactly-once）

#### Scenario: 跨 thread cooldown 走独立 journal

- **WHEN** circuit_breaker cooldown 跨 cron（跨 thread_id）判定
- **THEN** 仍走 circuit_breaker.py + 独立 cooldown journal
- **AND** 不依赖任何 per-thread 持久化（journal 是全局 append-only）

### Requirement: 升人工路径 + 测试门保持机械硬门（不用 native interrupt）

升人工路径（`interrupted_pr`（verify 判红用满）/ `off_track → triage` / `blocked_external`（三态 UNKNOWN）/ `halted` / `cooldown`）SHALL 保持现状 enum + 状态机硬编码，MUST NOT 用 LangGraph native `interrupt`（grilling 否决：pa 无头 cron 无人在循环中提供 interrupt resume 值）。graph 条件边 SHALL 机械路由这些 enum 终态（不替判）。测试门（commit/push/PR 前新绿证据）与三态 fail-safe MUST 保持机械硬门。

#### Scenario: verify revise 用满 → interrupted_pr 终态（enum，非 interrupt）

- **WHEN** pa-verify 判 revise 且 VERIFY_MAX_ROUNDS 用满
- **THEN** graph 落 interrupted_pr 终态（enum + 状态机，升人工不替判死）
- **AND** MUST NOT 触发 LangGraph interrupt

#### Scenario: 测试门保持机械硬门

- **WHEN** dev loop exit 14（测试发布门 GATE_FAILED/NOT_RUN/STALE）
- **THEN** graph 落 blocked_test_gate 终态（机械阻断）
- **AND** MUST NOT 用 interrupt 等人确认

### Requirement: 特化容错作纯函数库保留

journal / reconcile / cutover / single_flight / circuit_breaker / merge_loop / critical_alert / retry_policy / learning_memory / feature_flags SHALL 作为 graph node 边界调用的纯函数库原样保留。LangGraph 原生件（Checkpointer/Store/内置 retry）MUST NOT 替代这些 pa 特化容错。现有 ~65 个测纯函数模块 MUST 零改动复用。

#### Scenario: reconcile exactly-once 仍经 reconcile.py

- **WHEN** publish_node 收尾需对账远端副作用
- **THEN** 经 reconcile.reconcile_side_effects（KeyResolver 三态查 GitHub）+ idempotency_key
- **AND** MUST NOT 用 Checkpointer snapshot 对账

### Requirement: feature_flag 渐进 cutover + 秒回退

编排层 SHALL 提供 `pa_graph_shadow`（旁路双写不改决策）+ `pa_graph_orchestrator`（graph 驱动）双 flag（镜像 single_flight_serial_shadow/single_flight_auto_merge 模式）。cutover SHALL 复用 cutover.py 三重 gate（flag + parity + allowlist）+ run_full_cutover_suite 7 维 evidence 作发布门。run_cron.sh SHALL 按 PA_GRAPH_ORCHESTRATOR 分流。`unset PA_GRAPH_ORCHESTRATOR` MUST 在下个 cron 周期秒回 legacy。graph 代码物理隔离（Phase 4 前不删 run_daily.py），flag off = legacy 完整保留。

#### Scenario: unset flag 秒回 legacy

- **WHEN** cutover 后发现问题且 unset PA_GRAPH_ORCHESTRATOR
- **THEN** 下个 cron 周期 run_cron.sh 分流回 run_daily.py
- **AND** graph 代码物理隔离，legacy 路径完整保留

#### Scenario: driven flag 依赖 shadow parity 证据

- **WHEN** pa_graph_orchestrator=on 但 pa_graph_shadow 未先证明 N 次 parity
- **THEN** preflight blocked（镜像 single_flight shadow→auto_merge 强制依赖）

### Requirement: 扁平模块结构 + cron PATH 隔离

graph 代码 SHALL 放 scripts/ 扁平（graph_pa.py / graph_pa_nodes.py / graph_pa_state.py / graph_pa_contracts.py / check_boundary.py），MUST NOT 改成子目录包（守 CLAUDE.md「不要改成包结构」）。LangGraph 依赖 SHALL 装到 miniconda3（run_cron.sh 已补 PATH），MUST 经 Phase 0 spike 验证 cron 极简 PATH 下可 import。LangGraph SHALL 锁版本 `langgraph>=1.2.10,<2`（pyproject.toml，避撤回的 1.2.3/1.1.7）；CVE 阈值实证：sqlite<3.0.1=CVE-2026-28277、checkpoint<4.0.0=CVE-2026-27794（pa 不引 checkpoint-sqlite，sqlite 仅间接依赖时关注）。**不需要** `langgraph-checkpoint-sqlite`（不用 Checkpointer，依赖减负）。

#### Scenario: graph 代码扁平 import

- **WHEN** graph_pa.py 引用 node/state/contracts
- **THEN** 经 scripts/ 同目录扁平 import（sys.path.insert 惯例）
- **AND** 不用 from pa.graph_pa_nodes import（非包结构）

#### Scenario: cron 极简 PATH 下 LangGraph 可 import

- **WHEN** cron（非 login shell，PATH=/usr/bin:/bin + run_cron.sh 补的 miniconda3/node/claude）跑 graph
- **THEN** import langgraph 成功（依赖装在 miniconda3）
- **AND** test_graph_cron_path.py 在极简 PATH 下 import 断言通过

### Requirement: 重构契机修边界债

graph 化 SHALL 一并修边界审查（docs/orchestrator-boundary-audit-2026-08-11.md）的 4 处偏离：层 1 install 藏输入（→ ArtifactHandle 规范传递）+ 层 2 三处编排器产 drop（→ triaged 升人工）。

#### Scenario: install 藏输入在 graph 化中根治

- **WHEN** graph verify 子图的 independent_verify 跑 install
- **THEN** install 输出经 ArtifactHandle 强制传 pa-verify（见 ArtifactHandle 路径契约 requirement）
- **AND** pa-verify 不再被蒙（修边界审查层 1）
