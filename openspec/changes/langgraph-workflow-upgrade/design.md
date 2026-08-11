## Context

pa（项目推进流水线）是 Obsidian vault 控制面 R&D 编排器。7 阶段 `STAGES=["fetch","radar","prd","inject","critic","dispatch","report"]`，唯一入口 `run_daily.py`（~3400 行纯 Python 编排器，cron 经 `run_cron.sh` 每天 03:17 驱动）。

**每个 stage 的 runtime 都是 claude**：前 6 段经 `subprocess.run([claude_bin, "--agent", name, ...])` 调 persona（fetch/radar/prd/critic/verify/report），dispatch 段经 `dev-agent.py` + claude-agent-sdk `query()` dev loop（目标仓 worktree）。

**现状编排层**：`run_daily.py` 的 `main()` 用命令式 `if lo<=N<=hi` 顺序调度 `STAGES` + 命令式 critic/verify revise 回环 + `ThreadPoolExecutor` 跨项目并行 + `acquire_run_lock` run 级互斥。升人工路径（`interrupted_pr` / `off_track` / `halted` / `cooldown`）用 enum + 状态机硬编码。**durable runtime**（`journal.py` / `reconcile.py` / `recovery_cli.py` / `cutover.py` / `feature_flags.py` / `single_flight.py` / `circuit_breaker.py` / `merge_loop.py` / `learning_memory_*.py` / `retry_policy.py`）是纯 stdlib 自研层，经多轮 openspec 评审磨出。

**约束**：ADR-0001 控制面/目标面隔离；ADR-0006 dev-agent 唯一执行器（SDK 钉 `>=0.2.128,<0.2.130`）；机械/语义边界（编排器只传客观事实，语义结论归 persona）；三态 fail-safe（FOUND/NOT_FOUND/UNKNOWN）；测试门；exactly-once reconcile；cron 非 login shell（PATH 极简）；scripts/ 扁平非包；线性 git。

3 位专家（守门人/映射师/工程师）并行脑暴后，用户初选**方案 3 双写过渡**（Checkpointer graph 拓扑 projection + journal 事件流真源）。经 `/grilling` + `/domain-modeling` 多轮盘问后**收敛**：放弃 native interrupt（pa 是无头 cron，`interrupt()` 的 resume 值需调用者提供，无人在循环中）→ Checkpointer 的 resume 价值消失 → **降级为 journal 单写真源**（不用 Checkpointer）。LangGraph 只取图表达层（StateGraph + 条件边 + 子图），持久化与 durable 全保留 journal。本 design 落档收敛后的决策。

## Goals / Non-Goals

**Goals:**
- 用 LangGraph StateGraph 显式表达 7 阶段拓扑 + critic/verify/merge 子图 + 条件路由；流程一图可读。
- 4 类通用 node 抽象（Persona/DevLoop/Mechanical/Gateway）+ 规范 I/O 契约（NodeInput/NodeOutput/ArtifactHandle，TypedDict + 中心化验证）；7 阶段 = 配置实例，新加 stage 零新 node 代码。
- 回环表达：node 内回环（契约校验重试 / dev loop）走节点函数体，跨节点回环（critic/verify revise）走 graph 条件边。
- journal 单写真源（保留 append-only + fsync + 中部损坏 fail-closed 强契约）；不引入 Checkpointer。
- 标准化监控（obs schema 统一 + report 聚合 + 可查询），LangFuse 自托管留 follow-up。
- feature_flag 渐进 cutover + 一键秒回退。
- 重构契机修边界审查偏离（install 藏输入 + 3 处编排器产 drop→triaged）。

**Non-Goals:**
- 不改 claude runtime（persona subprocess / SDK dev loop 零改动）。
- **不引入 asyncio 事件循环**（编排器侧守同步；asyncio 只存在于 dev-agent.py 子进程内部，现状已如此）。
- 不引入 Checkpointer（journal 单写真源；放弃 native interrupt/resume）。
- 不替代 durable 特化容错层（journal/reconcile/cutover/single_flight/circuit_breaker 作纯函数库保留）。
- 不改 `dev-agent.py`（ADR-0006）。
- 不松绑 claude-agent-sdk 版本钉。
- 不改 per-agent model routing。
- 不引入 LangGraph Server / LangSmith 云部署（保持 cron + Python 脚本部署模型）。LangFuse 云同理 Non-Goal（自托管留 follow-up）。
- 不上 LangGraph Send API（dispatch 并行 node 内 ThreadPool 够用，见 E1）。
- 不重构与 LangGraph 正交的模块（learning_memory / retry_policy 内部实现）。

## Decisions

### D1. claude runtime 零改动 + 编排器侧守同步，LangGraph 只换编排表达层
**Rationale**：LangGraph 不是 claude 替代品。每个 node 里仍是 claude（subprocess persona / SDK query）。LangGraph 替换的是「编排 claude 的自研 Python」（`run_daily.py` 命令式状态机）。

**编排器侧守同步**：node 函数 sync，node 内 `subprocess.run` 调 claude；`graph.invoke()`（sync）不引入 asyncio 事件循环。dev loop 的 asyncio 仍封在 `dev-agent.py` 子进程内部（现状已如此，编排器侧只见 `subprocess.run` 同步等）。与 pa 现状（纯同步 ThreadPool + subprocess + 文件锁，无 asyncio loop）一致，迁移面最小。

**Alternatives**：
- 全栈 LangGraph（node 里跑 LangChain Tool / claude 经 ToolNode）——**否决**：违反复认知、ToolNode 吞三态、吞 SDK 版本钉、吞 wall-clock。
- async node + Send API——**否决**：pa 不 streaming / 不 batch / 不实时消费 stream，async 能力用不上（YAGNI）；且强制编排器进 asyncio 增迁移面与 SDK loop 互踩风险（R2）。

### D2. journal 单写真源（grilling 后从方案 3 收敛）
**Rationale**：初选方案 3（Checkpointer projection + journal 真源双写）。grilling 盘问 native interrupt 时发现：pa 是无头 cron，LangGraph `interrupt()` 的 resume 值**必须来自调用者**（无头模式无人在循环中提供），interrupt 在 pa 行不通 → 放弃 interrupt（见 D5 撤）→ Checkpointer 的 resume 价值消失。

剩余权衡：Checkpointer 的 graph 拓扑快照 vs journal 的事件流。
- journal 的 append-only + fsync + 中部损坏 fail-closed（`JournalCorruptionError`）是 pa 6 轮评审磨出的强契约；Checkpointer 的 SQLite WAL 损坏语义不等价（WAL 损坏是整个 DB，非单行），且**不可逆**。
- journal 透明（人可读 JSONL）、跨进程安全（无 SQLite 锁）、exactly-once reconcile 真源已就位。
- Checkpointer 的图拓扑快照对 pa 增量价值低——graph 拓扑可从 journal 事件流重建 initial state 后 `graph.invoke` 重放（节点幂等，见 D3 三不变式）。

**结论**：不用 Checkpointer，journal 单写真源。LangGraph 只取图表达（StateGraph + 条件边 + 子图），持久化全归 journal。

**崩溃恢复**：走 `recovery_cli → recover_iteration(journal)` 判 external_known + RetryPolicy.decide → 重建 graph initial state → `graph.invoke(state, thread_id)` 续跑。graph 节点幂等（靠 reconcile + idempotency_key，见 D3/E1）。

**写纪律**：node 内先 `journal.append_event`(fsync) 再 return state。单源无双源漂移风险。

### D3. 4 类通用 node 抽象，verdict 类型层守护 + 回环表达
**4 类**：
- `PersonaNode`（控制面语义：subprocess 调 persona + 两层 JSON 解析 + 契约校验 + 1 次 repair-hint 重试）—— **唯一可产语义 verdict**（pass/revise/drop）
- `DevLoopNode`（目标面语义：SDK dev loop + 解析 exit 14 测试门/15 跑偏/12 刹车 + worktree + session 续接）
- `MechanicalNode`（零 LLM 机械活：文件发现/去重/落盘/聚合/SMTP/install-test）
- `GatewayNode`（fail-safe 门：三态查询/测试门/reconcile/single_flight/circuit_breaker，UNKNOWN→blocked）

**7 阶段是这 4 类的配置实例**（新加 stage = 配置实例 + 加 graph 边，零新 node 代码）。verdict 字段只在 `PersonaNode` 工厂暴露写入；其他 3 类**类型上不暴露 verdict 写入**——机械/语义边界从「靠人守」升级为「靠类型守」。`check_boundary.py` lint 兜底（扫「非 persona node 产 verdict」）。

**回环表达（grilling Q1）**：
- **判定标准**：回环的「判官」是谁，回环就长在哪。判官 = 同一 node 的输出契约校验（机械、确定性）→ **node 内回环**（节点函数体普通 Python 循环，graph 只看最终 NodeOutput）；判官 = 不同语义角色（critic 判 prd / verify 判 dev）→ **跨节点回环**（graph 条件边）。
- **node 内回环**：PersonaNode repair-hint 重试（1 次）/ DevLoopNode 整个 dev loop（含 in-loop checkpoint / session retry / off_track break+重发，dev-agent.py 进程黑盒，graph 不拆——守 D1 + ADR-0006）。
- **跨节点回环**：critic revise（prd↔critic）/ verify revise（dispatch↔verify）= graph 条件边 + state 轮次计数器判上限。
- **边界 case**：node 内循环**用尽**吐终态信号（dev exit 15 → triaged / verify 用满 → interrupted_pr）→ **边路由终态**。判定在 node 内（语义，靠 persona），路由在边（机械）——不混。

**三不变式**：
1. **跨节点回环节点幂等**——恢复时 graph 从 initial state 重放，条件边走相同分支，副作用节点（dispatch commit/push/PR）重入；靠 `reconcile` + `idempotency_key`（纯函数库）保 exactly-once。
2. **state 带轮次计数器**（`prd_round` / `verify_round`）——router 读它判上限；state 只持可序列化（R8），router 不碰复杂对象。
3. **node 内重试也 `append_event`**——PersonaNode 第 2 次 repair-hint 记 journal（守 journal 真源；一个 node 可吐多条事件）。

**Alternatives**：7 个 stage 各写一个 node —— **否决**：紧耦合、重复、新加 stage 要写新代码、verdict 边界靠人守。

### D4. ArtifactHandle 路径契约（TypedDict + 中心化验证）
裸 `path: str` 升级为 `{kind, store, rel_path, digest, must_exist}`：
- `store` 三态：`vault`（控制面，相对 vault_root）/ `worktree`（目标面，相对 worktree_root）/ `tmp`（stamp 作用域，隔离 state_dir 下）
- **可移植**：state 只存 `rel_path + store`，绝对路径 node 内即时解析（不入 state），cross-machine bundle 友好（呼应 `cutover.py` `_BUNDLE_VERIFY_TEMPLATE`）
- **完整性**：`digest`(sha) 喂 `ArtifactEvidenceResolver` 对账；graph state 重建后可校验未篡改
- **fail-closed**：`must_exist=True` 的上游 artifact node 入口校验；不存在 → `status=blocked, error.code=missing_artifact`（不静默跳过）
- **跨面隔离（ADR-0001）**：`store=worktree` 只由 `DevLoopNode` 经 dev-agent.py 暴露；其他 3 类类型上不写 worktree
- **根治 install 藏输入**：`install_log` 成规范 ArtifactHandle（`store=tmp, must_exist=True`），independent_verify 写、verify node 的 NodeInput 强制含、pa-verify 经 handle 自行 Read 判断（守「语义归 persona」）

**实现类型（grilling Q3）**：NodeInput / NodeOutput / ArtifactHandle 用 **TypedDict + 中心化验证函数**（`graph_pa_contracts.py` 的 `validate_node_output(d) → NodeOutput | raise`），守 pa 纯 stdlib 风格（与现状 `stage_contracts.py` 手写 dict 校验一致），不主动依赖 pydantic（即便 LangGraph 间接拉入）。

**Alternatives**：裸 `path: str` —— **否决**：不可移植、无校验、install 藏输入类 bug 靠人记。Pydantic —— **否决**：pa 纯 stdlib 风格，中心化验证函数够用且风格统一。

### D5（撤）. native interrupt
**grilling 否决**。pa 是无头 cron，LangGraph `interrupt()` 的 resume 值必须来自调用者（无头模式无人在循环中提供），interrupt 在 pa 行不通。升人工路径（`interrupted_pr` / `off_track→triage` / `blocked_external` / `halted` / `cooldown`）**保留 enum + 状态机硬编码**（现状不变）。跨 cron（跨 thread）的 cooldown/halt 仍走 `circuit_breaker` / `critical_alert` 独立 journal。

测试门 / 三态 fail-safe 仍是机械硬门（enum 终态阻断，不替 persona 判，见 D6）。

### D6. 特化容错作纯函数库保留，不重写；升人工路径 + 测试门保持机械硬门
`journal` / `reconcile` / `cutover` / `single_flight` / `circuit_breaker` / `merge_loop` / `critical_alert` / `retry_policy` / `learning_memory` / `feature_flags` —— 作为 graph node 边界调用的纯函数库原样保留。LangGraph 原生不提供这些 pa 特化容错。~65 个测纯函数模块**零改动复用**（回归安全网主体）。

**升人工路径 + 测试门保持机械硬门**（D5 撤后 enum 保留）：dev loop `exit 14`（测试发布门 GATE_FAILED/NOT_RUN/STALE）→ `blocked_test_gate` 终态（机械阻断），不用 interrupt 等人确认；graph 条件边机械路由这些 enum 终态，不替判。

**Alternatives**：用 LangGraph 原生（Checkpointer/Store/内置 retry）替代 —— **否决**：语义不等价（reconcile exactly-once / 三态 / cutover shadow parity 是 pa 特化）。

### D7. feature_flag 渐进 cutover + 秒回退
`pa_graph_shadow` / `pa_graph_orchestrator`（镜像 `single_flight_serial_shadow` / `single_flight_auto_merge` 双 flag 模式：shadow→driven）。复用 `cutover.py` 三重 gate（flag + parity + allowlist，镜像 `resolve_dispatch_source`）+ `run_full_cutover_suite` 7 维 evidence 作发布门。`run_cron.sh` 加 flag 分流（`PA_GRAPH_ORCHESTRATOR=1` → `graph_pa.py`，else → `run_daily.py`）。`unset PA_GRAPH_ORCHESTRATOR` → 下个 cron 秒回 legacy。graph 代码物理隔离（Phase 4 前不删 `run_daily.py` 任何东西），flag off = legacy 完整保留。preflight 强制 `pa_graph_orchestrator=on` 必须先 `pa_graph_shadow` N 次 parity（镜像 single_flight 依赖）。

### D8. 扁平结构（非包）+ cron PATH 应对 + CVE 版本锁定
graph 代码放 `scripts/` 扁平（`graph_pa.py` 拓扑 / `graph_pa_nodes.py` node 工厂 / `graph_pa_state.py` TypedDict state / `graph_pa_contracts.py` I/O schema / `check_boundary.py`），**非子目录包**——守 CLAUDE.md「不要改成包结构」。LangGraph 依赖装 miniconda3（`run_cron.sh` 已补 PATH），Phase 0 spike 验证 cron 极简 PATH 下 import。

**版本锁定 + CVE**：`langgraph>=1.2.10,<2`（避撤回的 1.2.3/1.1.7）。**不需要** `langgraph-checkpoint-sqlite`（D2 不用 Checkpointer，依赖减负）。CVE 阈值（实证）：sqlite<3.0.1=CVE-2026-28277、checkpoint<4.0.0=CVE-2026-27794——pa 不引 checkpoint-sqlite，sqlite 仅作 LangGraph/SDK 间接依赖时关注。pydantic 间接依赖版本 Phase 0 确认（OQ2 已决 TypedDict，pa 不主动依赖 pydantic）。

### E1. 并发映射：dispatch 并行 node 内 ThreadPool，不上 Send
**Rationale**：pa 的并行只 dispatch 一处（跨项目，每项目一 dev-agent 子进程）。现状 = `ThreadPoolExecutor`（`run_daily.py:2946` 基准 / `:2826` serial_shadow），同步。

映射到 graph：dispatch 跨项目并行 = **dispatch_node 内部 ThreadPoolExecutor**（node 内，graph 只看 dispatch 汇总 NodeOutput）。不上 LangGraph Send API（Send 要 async node，违 D1 守同步）。

graph 价值在显式表达 7 阶段拓扑 + critic/verify 子图回环（条件边）；dispatch 内部多项目并行是 node 内细节（同「dev loop 内部循环是 node 内」，见 D3），graph 不拆。

**代价**：放弃 LangGraph 原生并行（Send）。但 ThreadPoolExecutor 够用——Send 的增量价值（graph 可见 fan-out 拓扑）不值 async 复杂度（YAGNI）。

### M. 标准化监控（A 先行 + C 留 follow-up）
**A（现在做，零新依赖）**：obs 标准化 schema（NodeOutput.obs：cost/turns/duration_ms/model/token_usage）+ report node 机械聚合所有 node obs + 产出可查询 metrics 文件（结构化 JSON/JSONL，供 grep/脚本查询）。取代现状散落（journal/state/报告靠 grep）。

**C（follow-up，重构落地后增量）**：LangFuse 自托管（开源、Postgres 后端，不云、数据不出 vault 所在机、不绑 LangGraph）。**软接入**（fire-and-forget：LangFuse 服务挂了上报失败只 log 不 crash 流水线，守「无静默失败」但不让它成 SPOF）。obs 数据流不变，只改 sink（report-file → LangFuse span）。

**为什么不现在做 C**：A 的 obs schema 是 A/C 共同前置（无论 sink 是 report-file 还是 LangFuse，obs 标准化都得做）；守 YAGNI（不为假设的 UI 需求提前背服务栈）。A 先行，C 留可观测需求明确后增量。

## Risks / Trade-offs

- **[R1 cron PATH 找不到 LangGraph 依赖，静默失败（致命）]** → Phase 0 共存 spike 硬门 + `test_graph_cron_path.py` 在 `/usr/bin:/bin` 下 import + 装到 miniconda3（`run_cron.sh:29` 已补路径，历史教训 2026-07-26 连挂）
- **[R2 claude-agent-sdk 0.2.128 与 LangGraph asyncio loop 互踩]** → 编排器侧守同步（D1）不引入 asyncio，dev loop asyncio 封 dev-agent.py 子进程，耦合面小；Phase 0 spike 跑 dev loop + graph 端到端 + `sdk_compat_patch` 仍命中
- **[R3 跨节点回环节点非幂等导致重入副作用]** → reconcile + idempotency_key（纯函数库）保 exactly-once；每 stage shadow parity byte-identical（原 R3 双源漂移随 D2 单写消解，此处改为幂等焦点）
- **[R4 dispatch 子图漏迁 single_flight/circuit_breaker/merge_loop 协同]** → dispatch node 单独留 2 周（Phase 2 第 3-4 周）+ 离线 drill 先跑 + canary 单项目
- **[R5 LangGraph 版本 breaking change]** → 锁版本 `>=1.2.10,<2`（D8）+ 守门人 lint + cutover suite 全套跑过才升
- **[R6 node 被诱导写语义判定（边界滑落）]** → verdict 类型层只在 PersonaNode + `check_boundary.py` lint
- **[R7 shadow parity 假绿（终态分布表面相等但内部漂移）]** → 不只比终态 Counter，加逐 stage byte-identical + 7 维 evidence suite 发布门
- **[R8 graph state 序列化失败（不可序列化对象）]** → state 只持可序列化（str/ArtifactHandle as dict/轮次计数器 int）+ `test_graph_state_serialize`
- **[R9 graph 大文件超 800 行]** → 拆多文件（`graph_pa.py` 拓扑 + `graph_pa_nodes_*.py` 按 node 类型拆）
- **[R10 Phase 4 下旧后才发现 regression]** → 留 1 release cycle 双路径共存；`run_daily.main()` 删除前先 deprecated 跑 4 周；线性历史 `git revert` 能力保留
- **[R11 放弃 native interrupt，升人工路径仍靠 enum 维护]** → enum + 状态机是现状，零新增风险；graph 条件边机械路由 enum 终态（不替判）

**Trade-offs**：
- 失去 Python 命令式灵活（`--from-stage`/`--force`/`--dispatch-skip-dev` 快路径 hack 要 map 到 config flag + 条件边）
- 放弃 LangGraph native interrupt/resume（升人工路径仍 enum，无 graph 原生 HITL）—— pa 无头 cron 本就无 HITL 场景，代价可接受
- 放弃 LangGraph 原生并行 Send（dispatch 并行 node 内 ThreadPool）
- 多一层 LangGraph 版本绑定（pa 现只锁 SDK 一个上游）

## Migration Plan

**代码层 big bang，发布层渐进**（复用 pa 既有的 shadow → drill → canary → 全量范式，RUNBOOK §8 已为 single-flight-auto-merge 验证过）：

**Phase 0｜依赖共存验证（2 周，前置 spike）**
miniconda3 装 `langgraph>=1.2.10,<2`（**不需要** checkpoint-sqlite）；1-node graph 调 `pa-radar` 在 cron 极简 PATH 跑通；SDK 0.2.128 + `sdk_compat_patch` 共存；隔离 state smoke；确认 pydantic 间接依赖版本。
**判据**：cron PATH `import langgraph; import claude_agent_sdk` 不报错 + radar 产物 byte-identical + `quality.sh` 全绿。

**Phase 1｜graph 骨架 + I/O 契约 + 单 stage 端到端（2 周）**
`scripts/graph_pa*.py` 建骨架（TypedDict state + TypedDict I/O 契约 + 中心化验证 + 4 类 node 工厂 + `check_boundary.py`）；radar 迁到 `node_radar`；node 直接 import 复用 `run_daily` 的 `run_persona` 等（graph 是壳，逻辑主体是已 ETT 的纯函数）；`test_graph_radar.py` byte-identical 断言。
**判据**：graph radar 产物 == `run_daily.stage_radar` 产物（byte-identical）+ 边界原则在 node 内守。

**Phase 2｜7 stage 全迁 + shadow 并行（4 周，核心期）**
每周 1-2 stage（fetch→inject→prd→critic→dispatch→report，复杂度递增）；dispatch 留 2 周（single_flight/circuit_breaker/merge_loop 协同 + node 内 ThreadPool 并行）；critic/verify 跨节点回环（条件边 + 轮次计数器）；report node 加 obs 标准化聚合 + 可查询 metrics；每 stage shadow 测试；接入 `cutover.run_shadow_parity_drill`。
**判据**：`ShadowParityReport.matched=True` + `quality.sh` 全绿 + 7 stage byte-identical + critic/verify/dispatch 三闭环边界原则在 node 内守。

**Phase 3｜cutover（单项目 canary → 全量，2 周）**
加 feature_flag `pa_graph_orchestrator`（默认 False）；canary 单项目开 flag 跑 N cron（隔离 state + 临时 log + unset `PA_HEARTBEAT`，守 pa-test-no-dirty-data）；通过则全量；跑 `cutover.run_full_cutover_suite` 7 维作发布门。
**判据**：shadow parity N 次 matched + 真实 cron ≥3 周期无回归 + cutover suite `overall_passed=True` + dispatch 段所有 durable 不变式仍守住。

**Phase 4｜下旧（2 周）**
flag 默认 True 留 1 release cycle 观察；删 `run_daily.py` 的 `main()`/命令式调度（**留** `stage_*` + `run_persona` 被 node import）；`run_cron.sh` 改 `exec graph_pa.py`；历史 journal.jsonl 保留（审计只读）。
**判据**：1 release cycle（≥4 周 cron）稳定后下旧。

**回退**：任一 Phase 失败 → `unset PA_GRAPH_ORCHESTRATOR` 秒回 legacy（graph 代码物理隔离，legacy 完整保留到 Phase 4）。

## Open Questions

- **OQ1（已决）**：`GatewayNode` 独立还是并入 `MechanicalNode`？grilling Q2 决——**独立**，突出 fail-safe 语义（产 `blocked` 终态影响路由）。
- **OQ2（已决）**：I/O 契约 `TypedDict` 还是 `Pydantic`？grilling Q3 决——**TypedDict + 中心化验证**，守 pa 纯 stdlib 风格（见 D4）。
- **OQ3**：`digest` 全强制（更严但多算 sha）还是长期强制 / tmp 可选？**倾向长期产物强制、tmp 可选**（install_log/test_log 量大，可选省成本）。Phase 1 定。
- **OQ4（撤）**：Checkpointer 用 SqliteSaver 还是 PostgresSaver？——撤（D2 不用 Checkpointer）。
