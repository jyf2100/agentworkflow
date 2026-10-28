## Why

agent-verify-injection 落地后，控制面内部的 verify 三态语义（green/red/**invalid**，invalid→triage 升人工）与 acceptance suite 状态（stored/absent/defective）在 **pa→hub 唯一语义接缝**（events.jsonl + report 双写）处丢失：事件枚举只有 `critic_verdict/pr_opened/verify_verdict` 且 `verify_verdict` 按 pass/fail 二值设计，acceptance 状态零出口，verify-report.json 不在 report 双写清单。现阶段（verify agent flag off + roc 本机运维）够用；但 agent 轨转 on 与 hub 集中形态叠加时（面板/通知是唯一消费面，SSH 进 state 卷读 JSON 不可运维），两处断层同时爆发——两者尚未叠加，现在补契约最便宜。证据基础：`docs/reviews/2026-10-28-runtime-artifacts-ontology-audit.md`（F1/F2）。

## What Changes

- **verify_verdict 补 invalid 出口（双产出点）**：语义审轨（pass/revise）既有 emit 零变化；invalid 分流点（现状 break 前零出口）新 emit `verdict:"invalid"` + 结构化 `reason`——六枚举一一对齐实现成因（`unresolved` / `anchor_violation` / `wallclock_timeout` / `suite_load_failed` / `worktree_setup_failed` / `exam_snapshot_mismatch`；实施取证补齐 worktree 建立失败这一 agent 轨独立成因），消费方可区分「升人工 triage」与「revise 回环」并直达排障入口。
- **新增 `acceptance_suite` 事件**：acceptance 工件状态（`stored`/`absent` 二值）从编排器落账 result 投射到 events.jsonl 接缝——stored 附 digest/criteria_ids/supersedes（换版链非 null 即 triage 确认重编写的事实出口）、absent 附 reason/detail（与编排器落账同源值域，七值：record_absence 同名五成因 + persona 面 refused/author_failed——实施取证：生产路径 store_suite 不传 journal 句柄，缺席成因全集在编排器层）；`defective` 是 triage 判定非工件状态，不设第三状态（缺陷可见性走 invalid reason + supersedes 链两条既有事实）。
- **verify-report 纳入 report 双写**：`<stamp>.verify-report.json`（`pa.verify-report.v1`）随 report 双写机制拷入 `PA_REPORT_DIR`，hub 侧消费面（面板 run 详情）可读，无需 SSH state 卷。
- **hub 面板消费三态**：run 详情的 verify 结果展示从二值（pass/revise）扩含 invalid + invalid 原因 + acceptance suite 状态行 + verify-report 关键节（判定/命令清单/锚检查）渲染。
- **hub 通知 invalid 语义**：invalid 事件的通知分类为 triage（升人工），不按 red/revise 处理——「升人工」必须有可送达的出口，否则 triage 无消费者。
- **RUNBOOK invalid 排障 playbook**：补「invalid 第一步做什么」操作面（按五原因分流：unresolved 查命令匹配面、anchor_violation 查 worktree/注入件、wallclock_timeout 查环境奇偶、suite_load_failed 查 store 完整性、exam_snapshot_mismatch 查控制面双注入链路）。

## Capabilities

### New Capabilities

（无——全部为既有 capability 的 requirement 演进）

### Modified Capabilities

- `pa-event-outlet`: 事件契约扩容——`verify_verdict` 从 pass/fail 二值扩 green/red/invalid 三态 + invalid 原因字段；新增 `acceptance_suite` 事件类型（枚举扩容，向后兼容条款「未知行跳过」保证 hub 旧版不受损）；report 双写清单纳入 verify-report.json。
- `hub-panel`: run 详情消费面扩容——verify 三态渲染 + invalid 原因 + acceptance 状态 + verify-report 内容可达（从 PA_REPORT_DIR 读，不再依赖 state 卷）。
- `hub-notifications`: invalid 判定的通知语义——triage 分类（升人工），与 red/revise 通知分轨。

## Impact

- **pa（产出端）**：`Projects/项目推进流水线/scripts/run_daily.py` 与 `graph_pa_*` 的 events.jsonl 产出点（verify_verdict 事件加 verdict/reason 字段；acceptance 落账处同步 emit）；report 双写点扩 verify-report 拷贝（双轨 legacy/graph 对称）；invalid 原因枚举来源对齐 verify_stream/dispatch 的既有分流。
- **hub（消费端）**：`hub/` 面板 run 详情渲染 + 通知分类（`hub-panel` / `hub-notifications` 对应实现面）。
- **文档**：`RUNBOOK.md` invalid playbook 小节。
- **不动的**：控制面内部 invalid 分流逻辑、acceptance store 本体、verify_stream 三锚实现——本 change 只做「投射到接缝」，不改变内部语义（本体论纪律：断层在投射面，不在本体）。
- **时序**：契约扩展先行（hub 旧版跳过未知事件行不受损），hub 消费面随后——可分批落地。
