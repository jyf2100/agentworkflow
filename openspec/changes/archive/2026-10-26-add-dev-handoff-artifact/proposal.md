# add-dev-handoff-artifact

## Why

dev 会话在三种断点后重启，新会话拿不到上一个 dev 的亲笔交接（做到哪 / 为什么这么做 / 试过什么没用 / 下一步建议），只能从 diff 与机械日志反推：

1. **verify revise round 2**：dev 拿到 `--feedback-artifact`（pa-verify 四要素判据，`dev-agent.py` L121/L568-572）与 immutable PRD，但 round 1 dev 的实现思路与未竟意图沉没在 diff 里。`verify-loop-discipline` A4 落地同分支累积后，round 2 在 round 1 半成品上继续，该缺口放大。
2. **stalled(exit 12) / off_track(exit 15) retry**：`recovery_context.py` 从 immutable PRD（目标/验收）+ journal artifacts（verifier_feedback）派生恢复上下文，`suggested_next_step` 按状态枚举机械映射——没有 dev 自述。
3. **hub 崩溃续跑**：execution-hub「容器生命周期（1:N 即弃 + 续跑）」下 journal 在卷上延续、`recovery_cli` 恢复编排器状态，但 dev SDK 会话 context 全丢；dispatch 中途崩溃时连 dev_out JSON 都未吐出，机械痕迹也缺。

现有注入通道均与本缺口正交：`--feedback-artifact` 是 verify 判据（编排器→dev）、`--lessons-artifact` 是跨 PRD 先验（cross-prd-learning-memory 范畴）、journal 事件是机械事实。三者都不携带「同一 PRD 上一 iteration 的 dev 自述」。

`session-aware-retry` spec 的 recovery summary 已列出 `decisions` and `next action` 字段，但 journal 中无 dev 自述事件可派生它们——本变更让这两个字段从「机械推断」升级为「journal 可引用的 dev 亲笔证据」。参照系：Anthropic 官方 `claude-quickstarts/autonomous-coding`（与《Effective harnesses for long-running agents》同日发布的参考实现）用 `claude-progress.txt` 一件裸工件覆盖全部三类断点；pa 的对应物须走 content-addressed artifact + journal 引用（不可变、可校验、hub 卷上延续）。

## What Changes

- **handoff 产出（写侧）**：dev loop 有 JSON 出口的四个收尾出口（正常 publish / stalled exit 12 / off_track exit 15 / 发布门拦截 exit 14）在 dev_out JSON 新增 `handoff` 字段——dev 亲笔的结构化自述（完成 / 进行中 / 阻塞 / 已试无效方案 / 下一步 / 关键决策理由）。error exit 11 现状无 JSON 出口（`dev-agent.py` L1115-1116 仅 stderr），其 handoff 经 turn 快照的收养路径覆盖（与断点 ③ 同机制）。
- **每 turn 暂存**：dev loop 在每个 assistant turn 吸收最新 handoff 快照（实施期 OQ1 结案：与 pa-progress K=10 抽查零耦合，无独立计时器）——覆盖断点 ③（进程被杀时至少留最后一次快照）。
- **content-addressed 存储**：编排器把 handoff 按 `durable-loop-state`「Content-addressed evidence artifacts」既有机制落盘（hash/size/type/path 引用进 journal 事件），不追加回 PRD（immutable PRD 不变式不动）。
- **注入（读侧）**：`--handoff-artifact` 作为第四个对称注入参数（与 `--feedback-artifact` / `--lessons-artifact` 同模式）加入 dev-agent；`recovery_context.build_recovery_context` 把 handoff artifact 并入恢复摘要证据源。
- **安全边界**：handoff 是 untrusted free-form 叙事——只有经 journal 引用且 digest 校验通过的 artifact 才可进 prompt，严格对齐 `session-aware-retry` 现有 scenario「excludes untrusted free-form historical narration that is not referenced by the journal」；handoff 只作 prompt 增补，不进任何 verdict/决策判定。

## Capabilities

### New Capabilities

（无——handoff 工件的存储机制复用 `durable-loop-state` 既有 content-addressed artifact 范畴，恢复注入复用 `session-aware-retry` 既有 recovery context 范畴，不新建 capability 以避免范畴重叠。）

### Modified Capabilities

- `durable-loop-state`：新增 **"Dev handoff artifact"** requirement——dev loop 各收尾出口与每 turn 检查点产出 handoff、经 content-addressed artifact 存储、journal 事件引用其 digest；handoff 不可变（新 iteration 产出新 artifact，不覆盖）。
- `session-aware-retry`：修改 **"Recovery context is evidence-derived"** requirement——恢复摘要的证据源在 immutable PRD + journal evidence 之上显式纳入 dev handoff artifact；`decisions` 与 `next action` 字段优先取 dev 自述，无 handoff 时维持现状（机械派生）；untrusted 边界条款延续并具体化到 handoff。

## Impact

- **代码（改）**：`dev-agent.py`（收尾各出口 handoff 产出 + dev_out 字段 + 每 turn 暂存 + `--handoff-artifact` 参数与 prompt block）、`run_daily.py`（dispatch 侧 artifact 落盘 + journal 引用 + retry/revise 链路传参）、`recovery_context.py`（handoff 源并入）。`graph_pa`* 对应 node 若 cutover 已合，同改。
- **不变（核心资产）**：immutable PRD source、ADR-0001 控制面/目标面隔离（handoff 经控制面 artifact 传递，同 feedback/lessons 模式）、TestEvidence 发布门、fail-safe 三态、journal 单写与 exactly-once。
- **取舍**：① dev 出口为 SIGKILL 级崩溃时只有最后一次 turn 暂存（尽力而为，不承诺完备）；② handoff 增加 dev loop 收尾与每 turn 的一次产出动作（token 成本轻微上升）；③ untrusted 叙事进 prompt 有注入面——靠「journal 引用 + digest 校验 + 仅增补不判定」三重约束收敛。
- **测试（新增）**：① 各出口 dev_out 含 handoff 且落盘为 content-addressed artifact；② digest 不匹配 → 拒绝注入（evidence-integrity 错误路径）；③ `--handoff-artifact` 注入渲染进 prompt block；④ `build_recovery_context` 并入 handoff 后 decisions/next_action 取自述；⑤ turn 暂存：模拟中途 kill，最后一次快照可恢复；⑥ 回归：无 handoff（baseline 不传参）时 recovery context 与现状 byte 级等价。
- **风险**：① dev 把 handoff 写成叙事倾泻污染 prompt——字段结构化 + 长度上限约束；② 与 `verify-loop-discipline`（22/24 未 archive）交互面：A4 同分支累积后 round 2 更需要 handoff，两者正交不冲突，但实施顺序上 handoff 的注入点（round≥2 传参）依赖其 iteration 语义，建议在其 archive 后落地或在 tasks 中显式对齐 `--iteration-seq` 既有语义（本提案取后者：不依赖，`--iteration-seq` L123 已在）。
