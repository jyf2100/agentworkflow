# pa-event-outlet（add-hub-slice-4b 新 capability，跨仓接缝契约）

## ADDED Requirements

### Requirement: 事件出口契约（events.jsonl）
pa 容器内流水线 SHALL 在 critic / dispatch / verify 段产生语义事件时，向 run 的 state 卷出口目录追加写结构化事件文件 `events.jsonl`（JSONL 每行一事件）。事件行 SHALL 含契约字段：`v`（契约版本，本切片定 `1`）、`event`（枚举：`critic_verdict` / `pr_opened` / `verify_verdict`）、`seq`（run 内单调递增整数）、`stamp`（run 标识）、`project`，及各事件类型的事实字段（critic_verdict：`verdict` pass/drop/revise + 摘要句；pr_opened：`pr_url` + 目标仓；verify_verdict：`verdict` + 摘要句）。事件文件是 **hub 与 pa 的唯一语义事件接缝**——hub MUST NOT 解析 pa journal 内部格式（4a D2 边界不动），pa MUST NOT 经网络直连 hub 推事件（单向文件接缝，容器即弃后文件留卷）。

#### Scenario: critic 裁决产出事件行
- **WHEN** 容器内 critic 段对某 PRD 产出 verdict（pass/drop/revise）
- **THEN** `events.jsonl` 追加一行 `{v:1, event:"critic_verdict", seq:N, ...}`，事实字段含 verdict 与摘要

#### Scenario: PR 开出产出事件行
- **WHEN** dispatch 段在某目标仓开出 PR
- **THEN** `events.jsonl` 追加一行 `pr_opened` 事件，含 pr_url

### Requirement: 契约演进向后兼容
读方（hub）SHALL 对未知 `event` 枚举值或未知字段跳过不炸（转发已知类型、忽略未知行并记日志）；`v` 不匹配时 SHALL 整文件跳过并记日志告警（契约版本升级须双仓协同，hub 不猜格式）。写方（pa）SHALL 内嵌版本字段不省略。

#### Scenario: 未知事件类型跳过
- **WHEN** events.jsonl 含 hub 不认识的 event 值
- **THEN** 该行跳过（stderr 记日志），前后行照常转发，处理不中断

### Requirement: 事件出口写点零行为变化（vault 形态）
pa 侧事件出口写 SHALL 在 vault 形态（roc 本机 cron 链）同样落 `.project-auto/state` 出口目录（无害统一写，不设开关——写面是既有 state 目录的追加，零外部副作用）；vault 侧无人消费该文件，不影响 cron 链任何行为。pa 侧改动 MUST 限于事件出口写函数与挂点，不动各段输出契约本体。

#### Scenario: vault cron 链无扰动
- **WHEN** roc 本机 cron 跑完整流水线（无 hub 参与）
- **THEN** state 目录多出 events.jsonl 无人读，其余产物与行为与改动前 byte 级一致

### Requirement: report 双写（T2a 裁决落地）
pa report 段 SHALL 支持经 env `PA_REPORT_DIR` 指定额外落盘目录（服务器容器形态 hub 挂 state 卷出口路径）：设置时报告文件**双写**（原 vault 落点不变 + `PA_REPORT_DIR` 落点追加）；未设置时行为与现状完全一致（缺省零变化）。hub 面板读卷渲染报告见 spec: hub-panel。

#### Scenario: 双写生效
- **WHEN** 容器内 env 含 `PA_REPORT_DIR=/opt/pa/.project-auto/state/reports`
- **THEN** 报告 md 在原 vault 落点与该目录各存一份

#### Scenario: 缺省零变化
- **WHEN** 未设 `PA_REPORT_DIR`（vault 本机形态）
- **THEN** 报告只落原落点，与现状一致
