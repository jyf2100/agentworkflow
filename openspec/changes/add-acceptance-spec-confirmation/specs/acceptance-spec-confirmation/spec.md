## ADDED Requirements

### Requirement: critic needs_metrics 第四判定

critic 对「来源交代齐（有据）但验收不可执行（无可断言指标）」的 PRD SHALL 判 `needs_metrics` 而非 `drop`；`drop` SHALL 收窄保留给无据 / 与项目无关 / 自相矛盾。编排器收到 `needs_metrics` SHALL 调 pa-spec-author 拟指标且本 run 不 dispatch。

#### Scenario: 模糊需求不再毙

- **WHEN** PRD 仅含「增加赛博朋克风格样式（暗底、霓虹高亮）」类主观描述且出处交代齐（真机 #74 重放形态）
- **THEN** critic 判 `needs_metrics`，pa 调 spec-author 拟指标清单，事件流出现 `metrics_proposed`，run 不进 dispatch

#### Scenario: 无据仍 drop

- **WHEN** PRD 无出处交代
- **THEN** critic 判 `drop`（既有行为零变化）

### Requirement: 指标清单回流频道待确认

hub SHALL 经事件转发管道收 `metrics_proposed`，将指标清单格式化 post 进绑定频道 `@发起人`（每条一行、总条数 ≤8 超出截断提示），并建 `confirmation_requests` 行（requested）。

#### Scenario: 清单进频道

- **WHEN** `metrics_proposed` 事件到达 hub
- **THEN** 频道出现 @发起人 + 逐条指标 + 用法行（confirm/reject/metrics 三子命令说明）

### Requirement: confirm 拍板重入队

`/pa confirm #<id>` 仅发起人本人（user_id 相等判据）可执行；执行后 hub SHALL 将指标清单并入 PRD 快照（「验收指标（发起人确认）」节）经既有入站路径全新落盘入队（新 sha / 新 deliver_key），下游零特判。非 requested 态重复 confirm SHALL 幂等回执「已处理」。非发起人 SHALL 拒绝。

#### Scenario: 拍板后全新 run

- **WHEN** 发起人发 `/pa confirm #77`
- **THEN** 新 `mm-<sha8>.md` 含指标节入队，回执新 #id；该 run 对 critic/dispatch 是普通全指标 PRD（#75 形态），应直接 pass

### Requirement: reject 与 metrics 补充

`/pa reject #<id>`（发起人）置 rejected + 频道回执；`/pa metrics #<id> <补充>` 将补充并入 PRD 快照后重新入队走 critic→spec-author 链自然重拟。两者对非发起人拒绝、对非 requested 态幂等回执。

#### Scenario: 补充上下文重拟

- **WHEN** 发起人发 `/pa metrics #77 要支持一键切回默认主题`
- **THEN** 补充并入后重新入队，新 run 产出的指标清单含新上下文

### Requirement: 确认超时

requested 行 72h 未决 SHALL 置 expired 并频道回执；expired/rejected 行不可再 confirm。

#### Scenario: 过期

- **WHEN** requested 行龄 >72h 且 reconcile tick 扫过
- **THEN** 行置 expired，频道 @发起人 过期回执，后续 confirm 回执「已过期」
