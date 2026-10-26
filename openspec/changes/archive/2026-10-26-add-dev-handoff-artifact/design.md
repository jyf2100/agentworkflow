# add-dev-handoff-artifact · design

## Context（事实基础，全部 grep 坐实）

dev 会话三类断点后的现有注入面：

| 断点 | 触发 | 新会话现有输入 | 缺口 |
|---|---|---|---|
| ① verify revise | pa-verify 判 revise → round 2 重调 | `--feedback-artifact`（verify 四要素，dev-agent.py L121/L568-572）+ immutable PRD | round 1 dev 自述 |
| ② stalled(12)/off_track(15) retry | retry_policy 决策衍生 iteration（L123 `--iteration-seq`） | `recovery_context.py`：PRD 目标/验收 + journal verifier_feedback + `suggested_next_step` 状态枚举机械映射 | dev 自述 |
| ③ hub 崩溃续跑 | 容器死 → 同 run 新容器、journal 卷上延续 | `recovery_cli` 恢复编排器；dev SDK 会话 context 全丢；中途崩时 dev_out 未吐 | 全部（含机械痕迹） |

现有通道正交性：`--lessons-artifact` 是跨 PRD 先验（cross-prd-learning-memory，≤5 lessons 排除叙事）；journal 事件是机械事实；feedback 是 verify 判据。均不携带「同 PRD 上一 iteration 的 dev 亲笔交接」。

参照系：`claude-quickstarts/autonomous-coding` 的 `claude-progress.txt`（裸文本 + git，覆盖全部断点）；pa 需 content-addressed + journal 引用版本（不可变、可校验、hub 卷上延续、符合 session-aware-retry 的 untrusted 叙事边界）。

## Goals / Non-goals

**Goals**：三类断点后新 dev 会话拿到上一 iteration 的结构化自述；崩溃中途也可恢复最后一次快照；baseline 零变更。

**Non-goals**：不动 immutable PRD；不把 handoff 用于任何 verdict/门/重试决策；不做跨 PRD 聚合（那是 cross-prd-learning-memory 范畴）；不改变 journal 单写与 exactly-once 语义。

## 决策

### D1 handoff 契约：结构化字段，不用自由叙事

```json
{
  "done": ["…"],            // 本 iteration 已完成并有测试/代码佐证的点
  "in_progress": ["…"],     // 未收口的半成品及其位置（文件/函数）
  "blockers": ["…"],        // 阻塞及外部依赖（缺凭据/环境/上游未决）
  "tried_and_failed": ["…"],// 已试无效的方案与原因（防 round 2 重蹈）
  "next": ["…"],            // 建议下一步（按优先级）
  "rationale": "…"          // 关键设计决策理由（≤500 字符）
}
```

- 每字段长度上限（列表项 ≤200 字符，`rationale` ≤500），总量 ≤4KB——防叙事倾泻污染 prompt。
- 结构化优于自由文本：可被 recovery summary 按字段映射（`next`→next action、`tried_and_failed`/`rationale`→decisions），注入时按字段渲染。

### D2 产出通道：prompt 契约 + assistant 消息内嵌标记块，harness 解析暂存

dev（模型）在 SDK loop 内，harness（dev-agent.py）只能从消息流取产出。**不引入第二个 query**（成本翻倍），而是 prompt 契约：

- dev system prompt 增补：每次阶段性回复末尾携带 ```handoff``` 围栏块（内为 D1 JSON）；收尾回复必须携带最终版。
- **落点核实（实施期修正，2026-10-26）**：SDK 0.2.128 的 `ClaudeAgentOptions` 无 `append_system_prompt`（实测仅替换式 `system_prompt`——会丢 SDK 默认行为，不可用）→ 契约进 `build_prompt` 的 user prompt 面，经显式 CLI flag **`--expect-handoff`** 条件启用；控制面 `feature_flags.dev_handoff`（`PA_LOOP_DEV_HANDOFF`，默认 off）gate 传参。与 `cross_prd_learning_injection` 同构（控制面 flag → CLI 传参 → dev-agent 无参 = prompt byte-identical，delta baseline scenario 逐字成立）。若无条件启用契约段，则「无 `--handoff-artifact` 且无存储」的 baseline 调用 prompt 也会变化，违反 baseline scenario——故写侧启用必须独立成 flag（产出要求与注入物存在无关，round 1 无物可注入但已需产出）。harness 吸收/快照不 gate（模型自发产出 fence 也捕获，无害且更稳）。另注（本体论审核 C3，2026-10-26）：dev_out JSON 的 `handoff` 键在四个收尾出口**无条件**携带（null=诚实缺失）——baseline byte-identity 面为 prompts + recovery summaries，**不含 dev_out JSON 面**（write-side 吸收不 gate 的必然推论；dispatch 消费侧 flag off 短路，无行为变化）。
- harness 在 AssistantMessage 处理处（现 TextBlock 累积点）解析最新标记块，存 `state["handoff_latest"]`；解析失败静默跳过（下个检查点补）。
- 各收尾出口把 `state["handoff_latest"]` 写入 dev_out JSON 的 `handoff` 字段（有 JSON 出口的四个：publish / stalled 12 / off_track 15 / gate 14，L1095-1148；error exit 11 无 JSON 出口，L1115-1116 仅 stderr——该路径由 D3 快照收养覆盖）。

取舍：标记块可能被模型遗忘——靠 prompt 措辞 + 收尾出口对缺失记 `handoff: null`（诚实缺失优于伪造，对齐 delta「Absent handoff is not fabricated」）。

### D3 K-turn 快照：dev-agent 直接原子写 run 目录，编排器「收养」

断点③（进程被杀）下内存暂存无效。快照必须即时落盘：

- dev-agent 在每次解析到新 handoff 块时，写 `run_dir/handoff-latest.json`（tmp + rename 原子写；run_dir 即现有 run_log 所在目录，通道已存在）。
- 编排器 dispatch 正常收 dev_out 时：以 dev_out.handoff 落 content-addressed artifact + journal 事件（`iteration_handoff`，引用 digest/size/type/path；type=`handoff`，sensitivity=`internal`）。
- 编排器发现进程异常终止（dev_out 缺失，如容器非零退出对账后）时：若 `handoff-latest.json` 存在，**收养**（adopt）——落 artifact + 补 journal 事件（事件注明 `adopted_from_snapshot: true`），然后走既有恢复路径。

### D4 注入：第四个对称参数 + untrusted 边界

- `--handoff-artifact <path>`（与 `--feedback-artifact` L121 / `--lessons-artifact` L122 完全同模式）：dev-agent 读文件、digest 校验（校验值由编排器在传参旁路给出——同 feedback artifact 的既有传递方式）、渲染为 prompt 独立标记块（「上一轮 dev 交接（仅上下文，非判据）」）。
- `recovery_context.build_recovery_context` 增加 handoff 源：journal 引用存在且 digest 过 → `decisions`/`next_action` 取自述；不存在 → 现状派生（byte 级等价，回归测试钉死）。
- **fail-closed 与回退的区分**（对齐 delta）：落盘后被改（digest 不匹配）→ evidence-integrity 拒绝注入；从未产出 → 正常缺失回退。
- handoff 不进 verdict/gate/retry 决策（delta scenario「never gates verdicts」）。

### D5 范畴边界表（本体论澄清）

| 实体 | 范畴 | 生命周期 | 与 handoff 的区别 |
|---|---|---|---|
| handoff | 同 PRD 跨 iteration 的 dev 自述 | 每 iteration 产新，不可变 | — |
| feedback artifact | verify 判据（编排器→dev） | 每 verify round | 判据 vs 自述 |
| lessons | 跨 PRD 先验 | 聚合长期 | 跨 PRD vs 同 PRD |
| journal 事件 | 机械事实 | append-only | 机器记录 vs dev 亲笔 |
| run_log | 过程日志 | 每 run | 全量流水 vs 结构化交接 |

范畴映射注记（本体论审核 C2，2026-10-26）：「恢复摘要」有**两个同名载体的实体**——`recovery_context.RecoveryContext` 是生产实体（reconcile 驱动构建、消费 handoff 的 decisions/next_action）；`loop_state.RecoverySnapshot`（durable-loop-state task 4.5/5.4 落点）是 spec 级投影，**生产构造点为零（未实例化）**。本变更只增富前者；后续 change 若实例化 Snapshot，须与本处 RecoveryContext 的 handoff 并入语义对齐（journal 引用 + digest 双验 + never-gates）。

### D6 残余风险与缓解

- **prompt 注入面**：handoff 是 dev 产出的自由文本进下轮 prompt。digest 只保完整性不保善意。缓解：仅增补不判定（D4）、字段化 + 长度上限（D1）、渲染时固定标记块框架。已知残余，接受（与 feedback/lessons 同级风险面，且 dev 产出本就经 pa-progress/pa-verify 对抗审）。
- **`graph_pa`* 同步**：dispatch/recovery 包装 node 若 cutover 已合，同改；tasks 显式含对齐项（现状 flag-gated 并行，改 wrapper 直调 stage_X，同改面小）。
- **与 verify-loop-discipline 交互**：其 A4 同分支累积让 round 2 更需要 handoff；其 delta（verified-dev-execution/verified-publication-integrity）与本变更 delta（durable-loop-state/session-aware-retry）无 capability 交集，正交。

## Open Questions

1. **K 取值**：暂定与 pa-progress 抽查同值（K=10）共用常量，避免双 K 配置面。实施时若 handoff 块产出频度干扰节奏，再分离。
   *结案（组2 实施）*：handoff 解析挂 AssistantMessage 处理处（每 turn 自然检查，无独立计时器），与 K=10 抽查节奏零耦合——双 K 顾虑消除，无需分离常量。
2. **`handoff-latest.json` 的目标面落点**：写 run_dir（控制面侧 state）符合 ADR-0001；但 hub 容器形态下 run_dir 在卷上、路径推导需对齐 hub 的卷挂载约定——实施任务里含一次 hub 形态路径核对。
   *结案（组6·6.2 静态核对，2026-10-26 冷 checkout 实证依据 = hub/runner.py 挂载面 + hub/deploy/pa-executor.Dockerfile）*：
   - 快照落点恒为 `wt/state/runs/handoff-latest.json`（dev-agent 以 REPO_ROOT=cwd 推导，`--state-dir` 只改 session_store）——ADR-0001 合规（dev-agent 只写执行环境内）。
   - **roc 真身机 cron 形态（主部署）**：worktree 在宿主盘（`Path(repo)/.worktrees/<devslug>` 确定性路径），kill/超时后盘上幸存 → 收养路径完整闭环（6.1 端到端冒烟即此形态）。
   - **hub 容器形态（当前切片）**：executor 镜像 `WORKDIR /workspace` 是容器可写层（hub「绝不挂不碰」，design D6 预留目标面落点）；hub 显式写面卷仅 `/opt/pa/.project-auto/state`。容器死 → worktree 及未 store 的快照随容器丢 = **诚实缺失**（delta「Absent handoff is not fabricated」），不破坏正确性；journal 事件 + content-addressed artifact 在 state 卷上，已 store 的 handoff 全程幸存、跨容器可续。
   - hub 形态要闭环「dev loop 中途崩的 in-flight 快照」需目标面挂持久卷（D6 部署决策）或 dev-agent 快照落点参数化（如 run_dir 跟 `--state-dir` 走 state 卷）——独立 follow-up change 范畴，本 change 不扩面。
