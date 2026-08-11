# Tasks — langgraph-workflow-upgrade

> 实现步骤。每条对应 spec 的 requirement 与 design 的决策（D#）/风险（R#）/迁移 Phase。代码层 big bang、发布层渐进（Phase 0→4）。回退：任一 Phase 失败 → `unset PA_GRAPH_ORCHESTRATOR` 秒回 legacy。

## 1. Phase 0：依赖共存验证（前置 spike，2 周）

- [x] 1.1 `pyproject.toml` 锁版本加 `langgraph>=1.2.10,<2`（避撤回的 1.2.3/1.1.7；CVE 阈值：sqlite<3.0.1=CVE-2026-28277、checkpoint<4.0.0=CVE-2026-27794——pa 不引 checkpoint-sqlite，sqlite 仅间接依赖时关注；**不需要** langgraph-checkpoint-sqlite，依赖减负；+ 确认 pydantic 间接依赖版本，pa 不主动依赖，OQ2 已决 TypedDict）；`pip install -e ".[dev]"` 装到 miniconda3（D8）
- [x] 1.2 `scripts/test_graph_cron_path.py`：在极简 PATH（`/usr/bin:/bin` + run_cron.sh 补的 miniconda3）下 `import langgraph; import claude_agent_sdk` 断言通过（R1 / spec「cron 极简 PATH 可 import」）
- [x] 1.3 共存 spike：1-node graph 调 `pa-radar`，在 cron 极简 PATH 跑通 + SDK 0.2.128 + `sdk_compat_patch` 仍命中 + 隔离 state smoke（R1/R2）
- [x] 1.4 radar 产物 byte-identical 断言（graph 1-node vs `run_daily.stage_radar`）+ `quality.sh` 全绿

## 2. Phase 1：graph 骨架 + I/O 契约 + 通用 node 抽象（2 周）

- [x] 2.1 `scripts/graph_pa_contracts.py`：`NodeInput`（6 类字段）+ `NodeOutput` envelope（7 字段）+ `ArtifactHandle`（{kind, store, rel_path, digest, must_exist}）用 **TypedDict + 中心化验证函数**（`validate_node_output`，守 pa 纯 stdlib 风格，不主动依赖 pydantic，D4 / spec「统一 node I/O 契约」+「ArtifactHandle 路径契约」，OQ2 已决 TypedDict）；解 OQ3（digest 长期强制、tmp 可选）
- [x] 2.2 `scripts/graph_pa_state.py`：`TypedDict` graph state（只持可序列化：str/ArtifactHandle as dict/轮次计数器 int，绝对路径 node 内解析不入 state，R8）+ 轮次计数器字段（`prd_round`/`verify_round`，跨节点回环判上限用，D3）
- [x] 2.3 `scripts/graph_pa_nodes.py`：4 类通用 node 工厂骨架（`PersonaNode`/`DevLoopNode`/`MechanicalNode`/`GatewayNode`，D3 / spec「四类通用 node 抽象」）；`verdict` 只在 `PersonaNode` 工厂暴露写入
- [x] 2.4 `scripts/check_boundary.py` + `scripts/test_check_boundary.py`：lint 扫「非 persona node 产 verdict」+「裸 `path: str`」（D3/R6 / spec「verdict 仅 PersonaNode 可写」）
- [x] 2.5 `node_radar` 迁移：`MechanicalNode` 壳 import 复用 `run_daily.run_persona`（零重写逻辑主体），radar 配置实例化（spec「新加 stage 不写新 node 代码」）
- [x] 2.6 `scripts/test_graph_radar.py`：graph radar 产物 == `run_daily.stage_radar` 产物 byte-identical（Phase 1 判据）

## 3. Phase 2：7 stage 全迁 + journal 单写持久化（4 周，核心期）

- [ ] 3.1 fetch node（3 个 persona 配置实例：github-repo/wechat-url/deepresearch）
- [ ] 3.2 inject + prd node（prd 配 `PersonaNode`，critic revise 回环先占位）
- [ ] 3.3 critic 子图（`PersonaNode` + revise 回环 round2，复用 `_critic_one`）
- [ ] 3.4 verify 子图（`PersonaNode` revise 闭环 + 跨节点条件边回环 + `VERIFY_MAX_ROUNDS` 用满 → interrupted_pr **enum 终态**（非 interrupt，D5 撤 / spec「升人工路径保持机械硬门」））；接 ArtifactHandle 传 `install_log`（见 4.1）
- [ ] 3.5 dispatch 子图（`DevLoopNode` + SDK dev loop + worktree + session 续接；迁 single_flight/circuit_breaker/merge_loop/reconcile 协同，留 2 周，R4 / spec「特化容错作纯函数库」）
- [ ] 3.6 report node（`MechanicalNode` 机械聚合所有 node 的 `obs` cost/turns/duration_ms/model/token_usage → 标准化可查询 metrics 文件，决策 M 路径 A / spec「统一 node I/O 契约」）
- [ ] 3.7 journal 单写持久化：node 内 `journal.append_event`(fsync) 先 → return state（**不用 Checkpointer**，D2 单写真源 / R3 / spec「journal 单写真源」）
- [ ] 3.8 崩溃恢复接 `recovery_cli → recover_iteration(journal)` 判 external_known + 重建 initial state → `graph.invoke(state, thread_id)` 续跑（节点幂等，靠 reconcile + idempotency_key，D3 三不变式 / spec「崩溃恢复走 journal 重建 initial state」）
- [ ] 3.9 `scripts/test_graph_topology.py`（7 stage 拓扑 + critic/verify/merge 子图边 + 回环条件边）+ `scripts/test_graph_state_serialize.py`（state 序列化可移植 + 轮次计数器，R8；无 checkpoint-resume，D2 单写）
- [ ] 3.10 每 stage 接 `cutover.run_shadow_parity_drill`（不只比终态 Counter，加逐 stage byte-identical，R7）；Phase 2 判据：`ShadowParityReport.matched=True` + 7 stage byte-identical + `quality.sh` 全绿

## 4. 边界债修复（重构契机，D3/D4，可并 Phase 2）

- [ ] 4.1 install 藏输入根治（层 1）：`independent_verify` 的 `install_log` 写为 `ArtifactHandle(store=tmp, must_exist=True, digest)`，verify node 的 `NodeInput.upstream_artifacts` 强制含，pa-verify 经 handle 自行 Read 判断（spec「install_log 成规范 artifact」+「根治藏输入」）
- [ ] 4.2 3 处编排器产 `drop` 改 `triaged`（层 2）：prd 缺 path / critic 漏吐 verdict / revise 异常 → graph 编排器产 `triaged`（升人工，不替判死）（spec「编排器残缺输入改 triaged」）
- [ ] 4.3 扩 pa-verify schema 加 `verifier_signal`（附带 #3，去 `:2484` 硬编码，解锁 FORK 重试分支，可选）
- [ ] 4.4 prompt 预写规则（层 3）评估移入 persona contract（低优，可选）

## 5. Phase 3：feature_flag + cutover（2 周）

- [ ] 5.1 `feature_flags.py` 加 `pa_graph_shadow` + `pa_graph_orchestrator`（镜像 `single_flight_serial_shadow`/`single_flight_auto_merge`，D7 / spec「feature_flag 渐进 cutover」）
- [ ] 5.2 `run_cron.sh` 加 `PA_GRAPH_ORCHESTRATOR` 分流（=1 → `graph_pa.py`，else → `run_daily.py`）
- [ ] 5.3 `cutover.py` 加 `run_graph_state_shadow_parity`（双源漂移 drill，R3）+ 三重 gate（flag + parity + allowlist，镜像 `resolve_dispatch_source`）
- [ ] 5.4 preflight 强制 `pa_graph_orchestrator=on` 必须先 `pa_graph_shadow` N 次 parity（镜像 single_flight 依赖，spec「driven flag 依赖 shadow parity」）
- [ ] 5.5 canary cc-web-control 单项目开 flag 跑 ≥3 cron（隔离 state + 临时 log + `unset PA_HEARTBEAT`，守 pa-test-no-dirty-data）
- [ ] 5.6 `cutover.run_full_cutover_suite` 7 维 evidence 作发布门（Phase 3 判据：`overall_passed=True` + dispatch 段 durable 不变式守住）

## 6. Phase 4：下旧（2 周）

- [ ] 6.1 flag 默认 True 留 1 release cycle（≥4 周 cron）观察（R10）
- [ ] 6.2 删 `run_daily.py` 的 `main()` / 命令式 `STAGES` 调度（**留** `stage_*` 纯函数 + `run_persona` 被 graph node import 复用，spec「claude runtime 不变」）
- [ ] 6.3 `run_cron.sh` 改 `exec graph_pa.py`（legacy 路径删）
- [ ] 6.4 历史 journal.jsonl 保留只读（审计）

## 7. 文档 + 收尾

- [ ] 7.1 `CLAUDE.md` 高层架构小节补 LangGraph 编排层（graph_pa*.py + feature_flag 分流 + journal 单写持久化 + 秒回退）
- [ ] 7.2 `RUNBOOK.md` 加 graph 恢复（`recovery_cli` over journal）+ shadow parity drill + 秒回退步骤
- [ ] 7.3 新增测试套全绿（test_graph_cron_path / test_graph_radar / test_graph_topology / test_graph_state_serialize / test_check_boundary / test_artifact_handle / test_shadow_parity）
- [ ] 7.4 `bash scripts/quality.sh` 最终全绿（compileall + pytest + ruff）+ OQ1-OQ4 决策落档（OQ1/OQ2 已决、OQ3 待 Phase 1、OQ4 撤）

## 8. follow-up（重构落地后，不在本期）

- [ ] 8.1 LangFuse 自托管标准化监控 sink（决策 M 路径 C）：软接入（fire-and-forget，服务挂了只 log 不 crash 流水线），obs 数据流不变只改 sink（report-file → LangFuse span）；前置 = 路径 A 的 obs schema 已稳定（见 3.6）
