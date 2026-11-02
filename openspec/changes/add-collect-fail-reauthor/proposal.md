## Why

add-acceptance-preflight-guard（2026-10-29 归档，已实现）落地了双层自检，collect fail（套件锚定）时扣留沿迭代传播——`_suite_snap = None` 是 iteration 级单一快照源，dev 与 verify 双消费面（`run_daily.py:3864`）都拿不到考卷。但本体论审核（F1b）发现这恰好**切断了既有修复路径的触发条件**：post-hoc `ERROR collecting` 签名识别要求 verify 真的跑考卷（`run_daily.py:3902` 产 `acceptance_suite_defective_candidate`），传播后 verify 无考卷 → 签名永不触发；而 collect-fail 本身不触发任何修复动作。结果是：确定性坏考卷（同 digest 同仓状态必复现）**每个 iteration 静默扣留一次，oracle 永不修复，该 PRD 永远无考卷**，直到人工读 journal triage。fail-open 保住了流水线不炸，但 oracle 闭环缺自动出口。

套件锚定的 collect 失败本身就是 triage 所需的全部证据——机械锚定（traceback 定位注入文件）、确定性可重放，比 post-hoc 签名更强（后者只产 candidate 待人工确认）。应自动触发既有 supersedes 重写路径（现为 triage 人工动作，`run_daily.py:4351` docstring）。

## What Changes

- **collect-fail 自动触发 superseding re-author**：dispatch 段 collect fail（套件锚定）分支在扣留后调用 pa-test-author 重写（`store_suite(supersedes=旧digest)`），重写 prompt 携带失败证据包（失败文件 + collect 错误签名 + 「上一版因 collect 失败被扣」上下文）；新 digest 经既有 `latest_suite` supersedes 消费面**下轮 dispatch 生效**（本 iteration 已扣留，不变式自洽）。不耗 dev 预算。
- **限次 1 + 熔断**：journal 推数该 PRD 已用的 collect-fail 重写次数；第二次套件锚定失败不再重写，记 journal `acceptance_reauthor_exhausted` + report 记 known issue，PRD 以无考卷模式跑完（不阻塞、不循环，人工 triage 面保留为兜底）。
- **Spec delta**：dispatch-time scenario 的 THEN 补 re-author 分支与熔断；「Defective oracle is re-authored」scenario 补自动确认通道（collect-fail 机械证据 ≈ 人工确认的等价物，自动路径限次熔断）。
- **Rationale 落档**（审核 F2/F3/F5，进 design）：失败家族分解（#76 家族词面可判=第一层全 harness 覆盖；仓结构家族=第二层，层不对称的正当性）；白名单统一概念化（「无需仓事实即可解析的 import 面」——三种异质辩护基础的共同本质）；indeterminate 归因哲学（草稿内在可运行性=对象属性 vs 验证者环境=关系属性，三态教义实例化）。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `acceptance-test-injection`: "Dispatch-time collection check" scenario 的 THEN 增加——套件锚定失败自动触发 superseding re-author（限 1 次、带证据、不耗 dev 预算、下轮生效），二次失败记 exhausted + known issue；"Defective oracle is re-authored" scenario 增加机械证据自动确认通道（与人工 triage 确认并行，语义同源）。

## Impact

- **代码**：`Projects/项目推进流水线/scripts/run_daily.py`（collect fail 分支扩：重写调用 + journal 限次推数 + exhausted 事件；`_test_author_prompt` 扩 evidence 参数）；report 段 known issue 落点核实。
- **接线共享**：改动在 per-PRD dispatch 函数内，graph_pa 聚合 node 直调同函数 → langgraph 轨自动继承，无需镜像（tasks 含验证项）。
- **spec**：`openspec/specs/acceptance-test-injection/spec.md` 上述两 scenario delta。
- **测试**：fail→重写触发、二次 fail→熔断、下轮 `latest_suite` 指新 digest、exhausted 后 report known issue。
- **成本**：每次失败多一次 authoring LLM 调用（秒级）；限次封顶 2 次。
- **记名 follow-up（不进本 change）**：独立 feedback 工件（PRD 内对象层/元层混居的根治，审核 F4）；jest/playwright 第二层所需的 harness 环境准备能力（F6，归档 change open question 继承）。
