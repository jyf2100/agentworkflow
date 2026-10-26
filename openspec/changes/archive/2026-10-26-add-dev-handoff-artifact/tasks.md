# add-dev-handoff-artifact · tasks

## 1. handoff 契约纯函数（新无依赖模块 `scripts/handoff.py`，同 `slug_utils`/`evidence` 模式）

- [x] 1.1 写测试（先红）：`parse_handoff_block(text)` 从 assistant 回复提取 ```handoff``` 围栏 JSON；坏 JSON / 超长字段（>200/500 上限）截断或拒收；多块取最新
- [x] 1.2 写测试（先红）：`validate_handoff(dict)` 按 D1 契约校验字段与长度上限（总量 ≤4KB）；缺失字段补空值（不伪造内容）
- [x] 1.3 实现 `handoff.py`（纯 stdlib，零 SDK 导入，单测可零依赖锁定红绿分支）；`bash scripts/quality.sh` 绿

## 2. dev-agent 写侧

- [x] 2.1 写测试（先红）：AssistantMessage 文本流经 `parse_handoff_block` 后 `state["handoff_latest"]` 持最新块；无块保持 None
- [x] 2.2 写测试（先红）：解析到新 handoff 时原子写 `run_dir/handoff-latest.json`（tmp+rename；无 run_dir 则跳过不炸）
- [x] 2.3 写测试（先红）：四个有 JSON 出口的收尾（publish / stalled 12 / off_track 15 / gate 14）的 dev_out JSON 均含 `handoff` 字段（值可为 null——诚实缺失）；error exit 11 无 JSON 出口（L1115-1116 现状），由 2.2 快照 + 4.2 收养覆盖
- [x] 2.4 实现：`--expect-handoff` flag（控制面 `dev_handoff` feature flag gate，默认 off；实施期修正——SDK 0.2.128 无 `append_system_prompt`，契约进 build_prompt 须 flag 条件化保 baseline byte-identical）经 build_prompt 增补 handoff 块契约（阶段性回复末尾携带，收尾必带最终版）；harness 暂存 + 原子写 + dev_out 字段
- [x] 2.5 K-turn 节奏对齐：handoff 解析挂在 AssistantMessage 处理处（自然每 turn 检查，无需独立计时器）；确认与 pa-progress K=10 抽查共存无干扰

## 3. dev-agent 读侧（注入）

- [x] 3.1 写测试（先红）：`--handoff-artifact` 参数解析（与 `--feedback-artifact` L121 同模式）+ 文件存在且 digest 匹配 → prompt 含独立标记块「上一轮 dev 交接（仅上下文，非判据）」
- [x] 3.2 写测试（先红）：digest 不匹配 → 拒绝注入 + 结构化错误（evidence-integrity 路径），dev-agent 不静默降级
- [x] 3.3 写测试（先红）：baseline（不传参）prompt 与现状 byte 级等价（回归锚）
- [x] 3.4 实现注入路径与 prompt block 渲染（位置：feedback_block 与 lessons_block 之间，三块各自独立标记）

## 4. 编排器（run_daily.py）

- [x] 4.1 写测试（先红）：dispatch 收 dev_out 含 handoff → content-addressed artifact 落盘 + journal 事件 `iteration_handoff`（引用 digest/size/type=handoff/path；复用既有 artifact 通道）
- [x] 4.2 写测试（先红）：dev_out 缺失（进程异常终）且 `handoff-latest.json` 存在 → 收养路径（adopt：落 artifact + 补事件 `adopted_from_snapshot: true`）；两者皆无 → 无事件（正常缺失）
- [x] 4.3 写测试（先红）：verify revise round≥2 与 retry 衍生 iteration 重调 dev-agent 时传 `--handoff-artifact`（同 `--feedback-artifact` 透传点）；`dev_handoff` flag on 时每轮传 `--expect-handoff`（写侧启用，2.4）
- [x] 4.4 实现 4.1-4.3；`reconcile` 对 adopted 事件幂等（crash 后重放不双写）
- [x] 4.5 `graph_pa`* 对应包装 node 同步（若 cutover flag 已合入主线则同改直调面；flag 仍 off 则在 graph_pa_dispatch/recovery node 补对称逻辑并纳入 shadow parity 面）

## 5. recovery_context 并入

- [x] 5.1 写测试（先红）：journal 引用存在且 digest 过 → `decisions`/`next_action` 取 handoff 自述（`tried_and_failed`+`rationale` → decisions；`next` → next action）；同时断言 recovery summary 输出结构中无任何 verdict/gate/retry 决策字段读入 handoff 内容（delta「never gates verdicts」的机械锚）
- [x] 5.2 写测试（先红）：无 handoff → build_recovery_context 输出与现状 byte 级等价（回归锚，对齐 delta「No handoff falls back」）
- [x] 5.3 实现 handoff 源并入 `build_recovery_context`（纯函数保持：入参加 artifact 读取，不引 SDK）

## 6. 集成冒烟与文档

- [x] 6.1 模拟中途 kill：跑 dev loop 至首个 handoff 快照后 SIGKILL → 编排器收养 → 恢复的 iteration prompt 含 handoff 块（断点③端到端）
- [x] 6.2 hub 形态路径核对：容器内 run_dir 挂载路径下 `handoff-latest.json` 可写可收养（对齐 design Open Question 2，实测后回写 design 结案）
- [x] 6.3 `CONTEXT.md` 术语表补 handoff（与 feedback/lessons/journal 的范畴区分表）；`RUNBOOK.md` 补收养路径运维说明；`CLAUDE.md` persona 表后补一句 handoff 工件存在性
- [x] 6.4 全量 `bash scripts/quality.sh` + `bash hub/quality.sh` 绿；`openspec validate add-dev-handoff-artifact` 过
