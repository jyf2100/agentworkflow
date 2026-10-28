## Context

agent-verify-injection 落地后控制面内部 verify 三态（green/red/invalid）与 acceptance suite 状态（stored/absent/defective）已在 pa journal 全量落账，但 **pa→hub 唯一语义接缝**（`events.jsonl` + report 双写）按二值时代设计（`docs/reviews/2026-10-28-runtime-artifacts-ontology-audit.md` F1/F2）。接缝两端现状（已核实）：

- **pa 产出端**：`event_outlet.append_event(state_dir, project, stamp, slug, event, **facts)` 通用 API——事件类型与事实字段全透传，**扩契约零模块改动**（只加挂点）。legacy 轨 emit 点：`run_daily.py` L1547（critic）/ L3441（verify_verdict，语义审轨）/ L4364（pr_opened）；graph 轨包装直调共享段函数（graph_pa_*.py 零独立 emit），同一批落点。verify_verdict 现值域 = **pass / revise**。
- **verify 双判定源**（本体论审核 F1 核实）：事件 verify_verdict 投射的是 **pa-verify 语义审**（pass/revise）；invalid 分流（`run_daily.py:3388-3403`）在语义审**之前** break——invalid 判定根本到不了 L3441 emit 点。invalid 实际成因**六类**（实施取证补齐第六类）：unresolved / 锚 violation（verify_stream 判定分支）、墙钟超时（agent 轨 catch VerifySessionTimeout + 机械轨执行超时）、套件 load 失败、**验证 worktree 建立失败**（agent 轨独有——机械轨同因分支 verdict 留 None 走既有跳过语义，不进 invalid 分流）、考卷不变式破坏（dispatch_one 构造分支）——**无常量**，成因编码在判定分支与人读 note 字符串。
- **acceptance 落账面**（本体论审核 F3 核实 + 实施取证修正）：工件状态只有 **stored / absent** 两值；「defective」是 triage **判定**非工件状态——自动面只有 candidate 预检（`suspected_suite_defective` journal 标志 + triage_reason 候选），确认与重编写是人工面；换版后的间接体现 = 新 stored 事件带 supersedes 链。生产路径权威事实源是编排器 `_author_acceptance_suite` 的 result dict（登记文件落盘同内容）——**`store_suite` 调用不传 journal 句柄，库层 stored/absent journal 事件未接线**；absent reason 全集（七值）在编排器层：no_criteria / no_harness / no_files / self_check_failed / store_failed（与 record_absence 同名同义）+ refused / author_failed（persona 拒写/熔断，编排器层独有）。
- **hub 消费端**：`hub/events.py` `KNOWN_EVENTS` 白名单 + 未知行跳过；`hub/lifecycle.py` `_event_broadcast` 判红 = `verdict != "pass"`（机械枚举比对）、`_event_text` 事实字段机械拼接；`hub/panel.py` run 详情 = DB 行 + journal 尾部行 + 报告渲染（零语义铁律）。
- **verify-report**：`verify_stream.build_verify_report` 落 `state/runs/<stamp>.verify-report.json`（`independent_verify` 共享函数内 L3915，legacy/graph 双轨同走），dispatch record 持路径；**不在 report 双写清单**（PA_REPORT_DIR 只覆盖 report 段产物）。

## Goals / Non-Goals

**Goals:**
- 控制面内部三态/状态本体**无损投射**到接缝：invalid 附结构化原因、acceptance_suite 状态出口、verify-report hub 可达。
- 向后兼容：hub 旧版对新增事件类型/字段零炸（未知行跳过既有条款兜底）。
- hub 消费面最小闭环：面板 run 详情三态可视 + 通知 invalid/acceptance 语义分轨。

**Non-Goals:**
- 不动控制面内部 invalid 分流逻辑、acceptance store 本体、三锚实现（断层在投射面非本体）。
- 不做「当前生效 digest」查询面（YAGNI，jq journal 够用——审核已裁决）。
- 不改 events.jsonl 契约版本（v 保持 1——增量加值不改值，不到双仓协同升级版本的程度）。
- 不做 hub 侧语义聚合/统计（零语义渲染铁律不动）。

## Decisions

### D1 verify_verdict 双产出点：invalid 于分流点新 emit，语义审轨零变化
事件 verify_verdict 有**两个判定源**，本 change 只补缺口不改既有面：
- **语义审轨（既有，不动）**：非 invalid 判定继续走 pa-verify 语义审，emit 值域仍 pass/revise、无 reason 字段——byte 级不变（回归锚钉死）。
- **invalid 分流点（新增 emit）**：verify 独立验证判 invalid 处（`run_daily.py:3388` break 前）emit `verdict:"invalid"` + `reason`。reason 六枚举一一对齐实现成因：`unresolved` / `anchor_violation` / `wallclock_timeout` / `suite_load_failed` / `worktree_setup_failed` / `exam_snapshot_mismatch`——**新建**模块级常量单一定义点（代码现状无常量），产出点归一引用：verify_stream 判定（unresolved/anchor_violation，经 verify-report `invalid_reason` 字段带出）、`independent_verify` 三分支（墙钟超时 / 套件 load 失败 / worktree 建立失败，agent 轨直赋 + 机械轨超时分支）、dispatch 考卷构造分支（exam_snapshot_mismatch）。
**为什么不在 L3441 统一映射**（初审 D1 的错误模型）：invalid 分流 break 在语义审之前，L3441 拿到的永远是 pass/revise——「green→pass/red→revise 映射」的数据流不存在；且 invalid 恰恰绕过该点。**向后兼容不变**：`invalid` 是唯一新增值（加值不改值），旧 hub `!= "pass"` 判读恰好把 invalid 归警示类，`_event_text` 不认识 reason 只拼 verdict（文本少原因但不错误）——零协同升级也安全。
*备选（否决）*：`invalid: true` 布尔旁字段——混合编码难判读且机械拼接面要特判；把三态映射塞进 L3441——不可达路径（见上）。

### D2 acceptance_suite 事件：状态二值 + 缺陷可见性走既有事实链
新增事件类型 `acceptance_suite`：`status` ∈ **{stored, absent}**（工件状态二值）。stored 附 `digest`/`criteria_ids`/`supersedes`（首版 null）；absent 附 `reason`（与编排器落账 result 同源，七值：record_absence 同名五成因 + refused / author_failed persona 面）+ `detail`。
**defective 不设第三状态**（初审的范畴混淆）：defective 是对已入库套件的 triage **判定**而非工件状态——journal 事件面从无 defective 状态，确认是人工面（无自动挂点可 emit）。缺陷可见性由两条**既有事实**承载：①verify_verdict invalid 行的 reason（`suite_load_failed` 等套件类成因）②triage 确认重编写后新 stored 事件的 `supersedes` 链（非 null 即换版，消费方读链即见）。
挂点在**编排器层 gate 出口统一面** `_author_acceptance_for_gate`（stage_critic 与 graph 聚合 node 共消费——双轨自动继承），`acceptance_tests.py` 内部不动——与既有挂点模式一致（事件出口写点属编排器，工件链模块不感知 run 级 stamp/slug 上下文）。投射来源：`_author_acceptance_suite` 的 result dict（生产路径权威事实源，含全部所需字段）——同事实字段换出口，零语义翻译。

### D3 verify-report 双写：dispatch 段落即时拷，不进 report 段
`independent_verify` 内 `build_verify_report` 落盘后（L3915-3918 落点），若 `PA_REPORT_DIR` 设置则 `shutil.copy2` 同名拷入（fail-open try/except stderr）。**为什么不在 report 段统一拷**：invalid→triage 路径可能不走完整 report 段，而 invalid 的 verify-report 恰恰是最需要人看的证据；且该函数 legacy/graph 双轨共享，**一处改双轨生效**。vault 形态（未设 env）行为零变化，对齐既有 report 双写缺省语义。

### D4 hub 面板：verify-report 机械渲染 + events.jsonl 尾部行原样展示
run 详情两处扩展（零语义铁律内）：① 若 run state 卷存在双写落卷的 verify-report.json → 机械表格渲染（判定/reason/命令清单 cmd·source·exit/锚检查名·rc/环境奇偶节原样字段）——与报告渲染同「落卷存在才展示、缺失显示占位」模式；② journal 尾部行展示旁加 events.jsonl 尾部行原样文本（acceptance_suite/invalid 事件从此面板可见）。不做任何 hub 侧结论性判断。

### D5 hub 通知：invalid 归警示类 + acceptance absent 进频道
`KNOWN_EVENTS` 加 `acceptance_suite`；`_event_broadcast` 扩：acceptance_suite 的 `absent` 进频道（无验收保护 dispatch 是团队该看见的警示），`stored` 仅私信（正常落账非警示；换版事实经 supersedes 字段机械附带）；`_event_text` 扩：verify invalid 行拼 `reason`，acceptance_suite 行拼 `status` + reason/digest/supersedes。机械拼接不变式不动（hub 零语义生成）。

### D6 RUNBOOK invalid playbook：按 reason 五分流
「invalid 第一步做什么」：`unresolved` → 查 verify-report 命令清单与 declared cmd 匹配面（末端段匹配语义）；`anchor_violation` → 查锚检查 rc（worktree 净/注入件 digest 哪个 violated）；`wallclock_timeout` → 查环境奇偶节（容器冷缓存/命令挂起），必要时调 `VERIFY_*_TIMEOUT`；`suite_load_failed` → 查 acceptance store 完整性（digest 校验失败=篡改/半写）；`exam_snapshot_mismatch` → 控制面接线异常（dev 上报 digest ≠ iteration 快照），查双注入链路不查目标仓。每条链到 verify-report 对应节。

## Risks / Trade-offs

- [旧 hub 遇 `acceptance_suite` 行会逐行 stderr 告警刷屏] → 噪音可接受（告警本就该提示升级）；hub 消费面与 pa 产出端同 change 落地，窗口期极短。
- [verify-report 双写放大 state 卷写量] → 单文件几 KB 级 JSON，每 run 至多（revise 重做次数）份；卷容量非已知约束。
- [invalid reason 枚举与内部分流实现漂移] → 枚举是本 change **新建**的模块级常量单一定义点（代码现状无常量——成因编码在判定分支与 note 字符串），产出点归一引用同源常量，tasks 加单测钉死六值与分流成因一一对应。
- [invalid 分流点 emit 遗漏某成因分支] → 六成因各产事件行的场景测试全覆盖（含考卷不变式破坏、套件 load 失败、worktree 建立失败三条易漏路径）；语义审轨 byte 级回归锚防误改 L3441。

## Migration Plan

1. pa 产出端先行（事件扩展 + verify-report 双写）——旧 hub 跳过未知行，零协同风险。
2. hub 消费端跟进（KNOWN_EVENTS / 判读 / 面板）——同 change 内连续落地。
3. 回滚：pa 侧挂点 fail-open 且事件文件无人强依赖，revert 单 commit 即回；hub 侧独立 revert 不影响 pa。

## Open Questions

（无——四点代码现状核实后无待决项；D1-D6 均已闭环。）
