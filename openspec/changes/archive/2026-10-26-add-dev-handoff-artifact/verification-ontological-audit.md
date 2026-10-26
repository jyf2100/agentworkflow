# 本体论审核（实现阶段，2026-10-26）

审核对象：add-dev-handoff-artifact 四件 artifacts（proposal / design / 2 delta specs / tasks）+ 已落地实现（`handoff.py` / `dev-agent.py` / `run_daily.py` / `recovery_context.py` / `graph_pa_nodes.py` / `coordinator.py` / `loop_state.py` / CONTEXT·RUNBOOK·CLAUDE 三文档）。
方法：同 add-hub-slice-2a——实体范畴边界、不变式与主 spec/实现一致性、**spec 声称行为的生产可达性**、术语残留。所有事实主张已 grep 源码/spec 坐实（行号为 2026-10-26 工作区）。

## 一、正面确认（咬合面成立）

1. **快照 vs artifact 双实体边界清晰**：in-flight 快照（`wt/state/runs/handoff-latest.json`，单文件最新覆盖语义——`handoff.py:34` 注释明示）与不可变 content-addressed artifact 是两个范畴；**收养（adopt）是从前者到后者的范畴转换**，journal 事件 `adopted_from_snapshot: true` 标记转换来源。snapshot 覆盖写不违反「later iteration MUST produce a new artifact」——那条约束钉的是 artifact 实体，实现用 content-addressed 天然满足（异内容异 hash）。
2. **never-gates 有结构性保证，不止测试锚**：`reconcile.recover_iteration` 内 `RP.decide(...)`（reconcile.py:293-296）**先于** `build_recovery_context`（L297）执行且 decide 入参不含 context——重试决策在结构上不可能读 handoff 内容；RecoveryContext 字段集无 verdict/gate/retry 名（test_recovery_handoff 机械锚钉死）。
3. **digest 同值域闭环**：`handoff.digest_of` ⇄ `artifact_store.compute_digest` 同 `sha256:<hex>` 格式；注入侧 fail-closed（`HandoffIntegrityError` → exit 10 归「输入类永久失败」属，help 文本同步）；恢复侧 digest 不过 = 拒收内容（evidence-integrity，两侧面分工在测试 docstring 声明）。
4. **baseline 零变的面域成立**：flag off → prompt 无契约段（`--expect-handoff` 缺省 False）、无注入参数、store/收养块整体短路（`_coord.flags.dev_handoff` gate）、graph 侧 `_handoff_upd` 空字典 no-op（state key 不动）；`iteration_handoff` 是 payload-only 事件，不进 shadow-parity dispatch 终态比对面。
5. **graph_pa 对称接线**：`_dev_build_cmd` 复用 `run_daily._dev_cmd`（kwargs 自动可达）+ DevLoopNode 镜像 store/收养序列；与 dispatch_one 同一调用面。
6. **断点①（verify revise round≥2）全链闭合**：store → `_parent_handoff_ref`（round loop 内活体，每轮覆盖）→ `--handoff-artifact`/`--handoff-digest` → prompt 独立标记块；verify-red 重投轮（含 session-aware retry 闭环的 continue）同 loop 内继承——SIGKILL 端到端冒烟（test_handoff_kill_smoke）锁死三段接缝。
7. **四 JSON 出口 `with_handoff` 无条件透传**与 delta scenario「Every JSON-emitting finish exit carries a handoff」逐字对应（null = 诚实缺失）；store/emit 失败 fail-open 不拖垮 dispatch（观测层语义，代码注释声明）。

## 二、问题清单

### B 级（范畴断裂：spec 声称的行为无生产路径，归档前须裁决）

**B1：session-aware-retry delta scenario「Handoff supplies decisions and next action」的恢复摘要并入面生产不可达**

spec 声称：journal 引用 digest-verified handoff → "the recovery summary's decisions and next-action sections are populated from the handoff"。实现事实（三链坐实）：
- `reconcile.recover_iteration`（reconcile.py:272）签名**无 `handoff_content` 参数**——生产唯一能喂该参的位置不存在；`build_recovery_context(handoff_content=...)` 全仓生产调用为零（仅单测）。
- recover_iteration 唯一生产调用点（run_daily.py:3209，verify-red retry 闭环）**不消费 `_rplan.context`**（只读 `decision.*` 与 `reconciliation.*`）——context 无消费是**本变更之前的既有缺口**（pre-change 亦然），delta 把「decisions/next-action populated」写成具体行为声明后缺口显性化。
- `context.handoff_path`/`handoff_digest`/`decisions`/`next_action` 无任何下游读取（grep 坐实）。

范畴定性：spec 说的「recovery summary」实体在生产不存在——目前兑现的恢复注入面只有 feedback-artifact（journal_driven_dispatch）与本变更新增的 handoff prompt 块（断点①实线）；「恢复摘要的 decisions/next-action 节」既无渲染面也无数据喂入。tasks 5.3 只钉了纯函数并入（与 spec 声称面有缝——spec 面宽于 tasks 面，这是缝隙根因）。
修法二选一：**(a)** 补接线：`recover_iteration` 增 `handoff_content`（从 events 里的 `iteration_handoff` ref 读 artifact + digest 校验；需补 artifact_root 接缝——签名现有 journal_path/run_id，artifact 相对 path 在 payload 内）+ run_daily retry 闭环把 `_rplan.context` 的 decisions/next_action 进 retry_decided journal payload（最小消费面）；**(b)** 收窄 scenario 措辞至已交付面（prompt 块注入）。**建议 (a)**——spec 意图明确、改动面小（~15 行 + 测试），且让 flag 依赖 journal_shadow 的理由（跨进程载体）真正闭环。

**B2：durable-loop-state delta scenario「K-turn checkpoint snapshots survive a hard kill」范畴混层，且 WHEN 举例与 design OQ2 结案直接矛盾**

scenario 声称："the most recent K-turn handoff snapshot … **is recoverable from the journal**, and **the resumed iteration can reference it**"，WHEN 举例 "e.g. container death under execution-hub resumption"。三个不符：
- **范畴混层**：in-flight 快照是文件系统实体（不进 journal）；进 journal 的是 store/收养后的 artifact 引用事件。「recoverable from the journal」把两个实体捏成一个。
- **举例恰是反例**：hub 容器死 = 快照随容器亡 = 诚实缺失（design OQ2 结案 2026-10-26 明确）；该 scenario 的 WHEN 举的正是快照不可幸存的形态。
- **「resumed iteration can reference it」无接线**：跨进程 resumed dispatch 的 `_parent_handoff_ref` 初始化 None，无 journal 回读播种；roc 形态 worktree 幸存但 resumed run 对同 PRD 的 worktree 是 `remove --force` 重建（run_daily.py:2825-2833），快照在收养前即毁。
修法：scenario 拆成两个真声明——(i) orchestrator 存活、dev 进程被杀 → 快照收养（已实现 + 冒烟）；(ii) journal 事件与 content-addressed artifact 在 state 卷幸存、跨容器可查（真）；hub 容器形态 in-flight 快照 = 诚实缺失（对齐 OQ2）。「resumed iteration can reference it」删除或降级为「已 store 的 handoff 跨容器幸存可被 journal 引用」（注入播种归 B1a 一并裁决）。

### C 级（术语残留 / 范畴映射缺失，随手带上）

**C1："K-turn checkpoint" 术语残留**——delta requirement 正文与 proposal 均写 "at every K-turn checkpoint / 与 pa-progress 抽查同节奏（K=10）"，实现是**每 AssistantMessage 吸收**（design OQ1 结案：无独立计时器，与 K=10 零耦合）。archive sync 会把已废弃机制名灌进主 spec。修法：delta 正文措辞改 "at every assistant-turn checkpoint"（一处 requirement + scenario 标题）。

**C2：RecoveryContext 与 RecoverySnapshot 同名字段双实体，范畴映射未记载**——`loop_state.RecoverySnapshot`（L242，durable-loop-state task 4.5/5.4 落点）本来就有 `decisions: tuple` + `next_action: str`，但**生产构造点为零**（grep 仅定义处，挂在 `LoopState.recovery_snapshot` 无人赋值）；本变更把同名字段加在生产实体 `recovery_context.RecoveryContext` 上。两个「恢复上下文」实体并存（一个 dormant 一个生产），spec 不点名实体故不违约，但范畴债加深。修法：CONTEXT.md 术语表 handoff 条目或 design D5 表补一行映射（RecoveryContext=生产恢复摘要实体；RecoverySnapshot=durable-loop-state 的 spec 投影，未实例化），防止后续 change 修错实体。

**C3：baseline byte-identity 面域未声明 dev_out JSON 豁免**——`with_handoff` 无条件执行 → baseline（flag off）dev_out JSON 也新增 `"handoff": null` 键。delta scenario「Baseline behavior is unchanged」只钉 prompts + recovery summaries（实现成立），dev_out JSON 面**有意豁免**（write-side 不 gate 是 D2 决策：自发围栏也捕获）但边界未写明。修法：design D2 修正 bullet 补半句「dev_out JSON 无条件携带 handoff 键（null=诚实缺失）——baseline byte-identity 面不含 dev_out JSON」，防未来审核误判。

## 三、裁决结果（用户 2026-10-26 批准：B1 补接线 + B2/C1-C3 全部随手带上）

五项已全部修入（TDD：`test_reconcile_handoff.py` 4 测先红后绿）：

- **B1 补接线**：`reconcile.recover_iteration` 增 `artifact_root` 接缝 + `_load_handoff_content`（只读文件，digest 门单源 build_recovery_context 防双源漂移）；run_daily verify-red retry 闭环传 `artifact_root=STATE_DIR/artifacts/<run>` 并把 `_rplan.context` 的 `decisions`/`next_action` 落 `retry_decided` journal payload（最小消费面）；graph_pa_recovery 对称传参。session-aware-retry scenario「Handoff supplies decisions and next action」经生产 driver 可达。
- **B2 scenario 重写**：durable-loop-state delta「K-turn checkpoint snapshots survive a hard kill」→「Stored handoffs survive orchestrator death」（journal 事件 + artifact 在 state 卷幸存、digest 可验；未收养快照在即弃容器形态诚实缺失）；「resumed iteration can reference it」无接线声明删除；收养路径由既有 scenario 2 覆盖。
- **C1 术语清理**：requirement 正文 "at every K-turn checkpoint" → "at every assistant-turn checkpoint（无独立计时器）"；scenario 2 "K-turn handoff snapshot" → "handoff snapshot"；proposal 6 处同步（保留 1 处 OQ1 解耦表述）。
- **C2 范畴映射**：design D5 表下补映射注记（RecoveryContext=生产恢复摘要实体；RecoverySnapshot=durable-loop-state spec 投影，生产构造点为零）。
- **C3 豁免声明**：design D2 修正 bullet 补「dev_out JSON 无条件携带 handoff 键——baseline byte-identity 面为 prompts + recovery summaries，不含 dev_out JSON」。

门禁复跑：pa `quality.sh` 绿（compile + pytest 2250 passed + ruff）、`hub/quality.sh` 绿、`openspec validate add-dev-handoff-artifact` valid。
