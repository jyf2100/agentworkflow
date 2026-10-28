## MODIFIED Requirements

### Requirement: 面板只读面（队列 / run / 报告，零语义渲染）
hub SHALL 提供面板只读页：队列状态列表、run 列表（含终态与 exit code）、run 详情（终态事实 + journal 尾部行机械展示）。run 报告 SHALL 在报告落卷存在时只读渲染（T2a 裁决：pa 侧 report 双写 state 卷，见 spec: pa-event-outlet；roc 侧报告可见性 = 面板渲染 + bot 摘要）。run 详情 SHALL 在 events.jsonl（伴生事件文件）存在时机械展示其尾部若干行（原样文本——acceptance_suite / verify invalid 等语义事件从此面板可见，展示非判定）；SHALL 在 verify-report.json 落卷（pa 侧 verify-report 双写，见 spec: pa-event-outlet）存在时机械表格渲染其关键节：判定（verdict + invalid 时 reason）、命令清单（cmd / source / exit code 逐行）、锚检查（名称 + 通过/违反 rc）、环境奇偶节原样字段——缺失时显示「无 verify-report」占位（非错误，旧 run / flag off 形态无此文件）。面板渲染 MUST 限于机械展示 hub DB 自有行与 state 卷落盘文件——不解析 pa journal 内部结构做语义判定（journal/events 尾部行原样展示除外——展示非判定）、不聚合统计语义结论。只读面同样强制会话（与提交页同守卫口径）。队列状态列表 SHALL 最新优先（id 降序）并展示每行 `created_at`（入队时间）与 `updated_at`（最后状态变化时间）两列（原样展示，不做语义格式化）。

#### Scenario: run 列表与详情
- **WHEN** 已登录同事打开 run 列表 / 某 run 详情
- **THEN** 见队列项状态、run 终态、exit code、last_error、journal 尾部若干行（原样文本）

#### Scenario: 报告渲染
- **WHEN** 某 run 的报告文件已落卷（report 双写生效）
- **THEN** run 详情页渲染报告内容（markdown 原文展示）；报告缺失时显示「无报告」（非错误）

#### Scenario: verify-report 机械渲染
- **WHEN** 某 run 的 verify-report.json 已落卷（verify-report 双写生效）
- **THEN** run 详情页渲染判定（含 invalid reason）、逐命令 cmd/source/exit 表格、锚检查结果、环境奇偶字段（原样展示）；文件缺失时显示「无 verify-report」占位

#### Scenario: 语义事件尾部行可见
- **WHEN** 某 run 的 events.jsonl 含 `acceptance_suite`（absent——工件状态二值无 defective，缺陷可见性经 invalid reason + supersedes 链承载）或 `verify_verdict`（invalid + reason）事件行
- **THEN** run 详情页 events 尾部行区原样展示该行文本（无 hub 侧解读）

#### Scenario: 只读面不产生语义判定
- **WHEN** 任何只读页渲染
- **THEN** 页面内容为 DB 行与卷文件的事实展示，无 hub 侧生成的质量/价值/结论性判断

#### Scenario: 队列最新优先且含时间列
- **WHEN** 已登录同事打开队列页
- **THEN** 行序为 id 降序（最新提交/重跑的队列项在最上），每行含入队时间与最后状态变化时间两列（「MM-DD HH:MM」机械截短显示，title 悬停见 ISO 全文；操作列（重跑/放弃）位于 status 列旁，不因时间列加入被推出首屏可视区）
