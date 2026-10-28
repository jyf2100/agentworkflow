# pa-event-outlet

## Purpose

pa 侧语义事件出口能力：events.jsonl 跨仓接缝契约（hub 与 pa 的唯一语义事件接缝——单向文件接缝，hub 不解析 journal、pa 不直连 hub）+ 契约演进向后兼容（未知行跳过 / v 不匹配整文件跳过）+ vault 形态零行为变化 + report 双写（T2a 裁决：PA_REPORT_DIR）。

## Requirements

### Requirement: 事件出口契约（events.jsonl）
pa 容器内流水线 SHALL 在 critic / dispatch / verify 段产生语义事件时，向 run 的 state 卷出口目录追加写结构化事件文件 `events.jsonl`（JSONL 每行一事件）。事件行 SHALL 含契约字段：`v`（契约版本，本切片定 `1`）、`event`（枚举：`critic_verdict` / `pr_opened` / `verify_verdict` / `acceptance_suite`）、`seq`（run 内单调递增整数）、`stamp`（run 标识）、`project`，及各事件类型的事实字段（critic_verdict：`verdict` pass/drop/revise + 摘要句；pr_opened：`pr_url` + 目标仓）。

**verify_verdict 双产出点**（verify 有两个判定源，事件出口各自投射，互不替代）：
- **语义审轨**（pa-verify persona，既有 emit 点）：`verdict` pass/revise + `round` + 摘要句——产出点与值域零变化（非 invalid 判定走语义审，byte 级不变，不携带 reason 字段）。
- **invalid 分流点**（verify 独立验证判 invalid 时：分流 triage、不烧语义审会话、break 前）SHALL emit `verdict:"invalid"` 行，必附 `reason` 字段——值域六枚举，与 invalid 分流实现成因一一对齐：`unresolved`（declared 命令不可判读）/ `anchor_violation`（三锚 violation）/ `wallclock_timeout`（agent 轨会话墙钟超时或机械轨执行超时）/ `suite_load_failed`（验收套件 load 失败）/ `worktree_setup_failed`（验证 worktree 建立失败，agent 轨独有——机械轨同因分支 verdict 留 None 走既有跳过语义不进 invalid 分流）/ `exam_snapshot_mismatch`（考卷不变式破坏）。枚举 SHALL 定义于单一定义点（模块级常量，本 change 新建——代码现状无常量，成因编码在判定分支与人读 note 字符串中），产出点归一引用同源常量：verify_stream 判定分支（unresolved / anchor_violation）、`independent_verify` 内三分支（墙钟超时 / 套件 load 失败 / worktree 建立失败）、dispatch 考卷构造分支（exam_snapshot_mismatch）。
- `invalid` 是唯一新增值（增量加值不重命名——接缝契约演进纪律，旧消费方零协同升级安全）。

**acceptance_suite**（acceptance 工件状态出口）：`status` SHALL ∈ {stored, absent}（工件状态二值——套件要么入库要么没入库；`defective` 是对已入库套件的 triage **判定**而非工件状态，不进本枚举：缺陷可见性由 ①verify_verdict invalid 行的 reason（suite_load_failed 等）②换版后 stored 事件携带的 supersedes 链，两条既有事实承载）。stored 附 `digest` / `criteria_ids` / `supersedes`（首版 null；非 null 即发生过 triage 确认重编写，消费方读 supersedes 链即见换版）；absent 附 `reason`（值域与编排器落账 result 同源，七值：`record_absence` 同名五成因 no_criteria / no_harness / self_check_failed / no_files / store_failed ＋ persona 面两成因 refused / author_failed——实施取证：生产路径编排器直接构造 result dict、`store_suite` 不传 journal 句柄，缺席成因全集在编排器层）与 `detail`。挂点 SHALL 在编排器层 gate 出口统一面 `_author_acceptance_for_gate`（stage_critic 与 graph 聚合 node 共消费——双轨自动继承），事实字段与落账 result 同源零语义翻译，`acceptance_tests.py` 内部不动。

事件文件是 **hub 与 pa 的唯一语义事件接缝**——hub MUST NOT 解析 pa journal 内部格式（4a D2 边界不动），pa MUST NOT 经网络直连 hub 推事件（单向文件接缝，容器即弃后文件留卷）。

#### Scenario: critic 裁决产出事件行
- **WHEN** 容器内 critic 段对某 PRD 产出 verdict（pass/drop/revise）
- **THEN** `events.jsonl` 追加一行 `{v:1, event:"critic_verdict", seq:N, ...}`，事实字段含 verdict 与摘要

#### Scenario: PR 开出产出事件行
- **WHEN** dispatch 段在某目标仓开出 PR
- **THEN** `events.jsonl` 追加一行 `pr_opened` 事件，含 pr_url

#### Scenario: verify invalid 于分流点产出事件行（含原因）
- **WHEN** verify 独立验证判 invalid（六成因任一：declared 不可判读 / 锚 violation / 墙钟超时 / 套件 load 失败 / 验证 worktree 建立失败 / 考卷不变式破坏）
- **THEN** 分流点 emit `verify_verdict` 行，`verdict:"invalid"` 且 `reason` 为六枚举对应值；语义审不发生（不烧 pa-verify 会话）

#### Scenario: 语义审轨事件行零变化（回归锚）
- **WHEN** verify 独立验证非 invalid（green/red，继续走语义审）
- **THEN** 语义审 emit 的 `verify_verdict` 行值域仍为 pass/revise，不携带 reason 字段，与改动前 byte 级一致

#### Scenario: acceptance 落账产出事件行
- **WHEN** critic 段 pa-test-author 产物入 store 落账（stored）或显式缺席（absent，含 reason/detail）
- **THEN** `events.jsonl` 追加一行 `acceptance_suite` 事件，status 与 digest/criteria_ids/supersedes 或 reason/detail 事实字段与编排器落账 result 同源

#### Scenario: 套件换版经 supersedes 链可见
- **WHEN** triage 确认套件缺陷后重编写，新套件入 store（journal 落新 stored 事件）
- **THEN** `events.jsonl` 追加新 `acceptance_suite` stored 行，`supersedes` 指向被换版本 digest（非 null）——消费方无需独立「defective」事件即见换版事实
- **NOTE**（r2 审核修正）：换版**字段契约**钉于此；当前生产写方 `store_suite` 生产调用不传 supersedes、恒 null——supersedes 非 null 属 triage 重编写流程接线后的形态（投射面已就绪，非超诺「生产任一 run 可见」）

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
pa report 段 SHALL 支持经 env `PA_REPORT_DIR` 指定额外落盘目录（服务器容器形态 hub 挂 state 卷出口路径）：设置时报告文件**双写**（原 vault 落点不变 + `PA_REPORT_DIR` 落点追加）；未设置时行为与现状完全一致（缺省零变化）。**verify-report 双写**：dispatch 段落 `verify-report.json`（`pa.verify-report.v1`，`independent_verify` 共享落盘点）SHALL 在 `PA_REPORT_DIR` 设置时同名拷入该目录——挂点在 verify-report 落盘即时处（非 report 段统一拷：invalid→triage 路径不走完整 report 段，而 invalid 的报告恰是最需人看的证据）；拷贝失败 SHALL fail-open（stderr 告警不阻断 dispatch）。hub 面板读卷渲染见 spec: hub-panel。

#### Scenario: 双写生效
- **WHEN** 容器内 env 含 `PA_REPORT_DIR=/opt/pa/.project-auto/state/reports`
- **THEN** 报告 md 在原 vault 落点与该目录各存一份

#### Scenario: 缺省零变化
- **WHEN** 未设 `PA_REPORT_DIR`（vault 本机形态）
- **THEN** 报告只落原落点，与现状一致

#### Scenario: verify-report 落卷可达（agent 轨）
- **WHEN** 某 run verify 完成（绿/红/invalid 任一态）且 `PA_REPORT_DIR` 已设置——**agent 轨**（`PA_VERIFY_AGENT=1`）；机械轨执行超时（wallclock_timeout）不产 verify-report——超时本身即结论，排障走 hub run 日志 / dispatch record
- **THEN** `<stamp>.verify-report.json` 同名出现在 `PA_REPORT_DIR`，hub 无需 SSH state 卷即可读

#### Scenario: verify-report 拷贝失败不阻断
- **WHEN** verify-report 双写拷贝抛 OSError（卷满等）
- **THEN** stderr 告警后流水线照常，state 原落点文件不受影响
