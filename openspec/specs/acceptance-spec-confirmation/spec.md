# acceptance-spec-confirmation

## Purpose

模糊需求的人工确认环：critic 对「来源交代齐但验收不可执行」的 MM 绑定源 PRD 判第四态 `needs_metrics`（不再一律 drop），编排器调 pa-spec-author 拟可断言验收指标清单并以 `metrics_proposed` 事件回流 hub。hub 将清单格式化 post 进绑定频道 `@发起人` 并建 `confirmation_requests` 行，发起人经 `/pa confirm`（指标并入 PRD 快照全新落盘入队）/ `/pa reject` / `/pa metrics` 子命令拍板（重入队 3 次上界），requested 行 72h 未决过期。

## Requirements

### Requirement: critic needs_metrics 第四判定

critic 对「来源交代齐（有据）但验收不可执行（无可断言指标）」的 **MM 绑定源** PRD SHALL 判 `needs_metrics` 而非 `drop`；`drop` SHALL 收窄保留给无据 / 与项目无关 / 自相矛盾。needs_metrics 输出 SHALL 含子因 `reason: "no_metrics"|"fact_conflict"` 与可选 `fact_keys: []`（仓事实冲突子因由 RSI 第二波启用，hub 拦截计数按结构化字段累计，无字段不计数）。编排器收到 `needs_metrics` SHALL 调 pa-spec-author 拟指标且本 run 不 dispatch。**非 MM 绑定源（panel/radar）SHALL 保持三态判据**（模糊仍走既有 revise/drop，不产出无人可确认的 needs_metrics）。

#### Scenario: 模糊需求不再毙

- **WHEN** MM 绑定源 PRD 仅含「增加赛博朋克风格样式（暗底、霓虹高亮）」类主观描述且出处交代齐（真机 #74 重放形态）
- **THEN** critic 判 `needs_metrics`（reason=no_metrics），pa 调 spec-author 拟指标清单，事件流出现 `metrics_proposed`，run 不进 dispatch

#### Scenario: 无据仍 drop

- **WHEN** PRD 无出处交代
- **THEN** critic 判 `drop`（既有行为零变化）

#### Scenario: 非 MM 源仍走旧径

- **WHEN** radar 信号 PRD 或 panel 源 PRD 验收不可执行
- **THEN** critic 按既有三态判（revise/drop），不判 needs_metrics、不产 metrics_proposed 事件

### Requirement: 指标清单回流频道待确认

hub SHALL 经事件转发管道收 `metrics_proposed`（事件须已注册进 hub 事件白名单——未注册整行丢弃即死链），将指标清单格式化 post 进绑定频道 `@发起人`（每条一行、总条数 ≤8 超出截断提示；`coverage: proxy` 条目与 `requires:<capability>` 尾注逐项标注），并建 `confirmation_requests` 行（requested；initiator=enqueued_by、channel_id=项目绑定、prd_text=PRD 全文快照）。hub 收到无 initiator 的 `metrics_proposed` SHALL 不建行并记 warn（回归信号）。

#### Scenario: 清单进频道

- **WHEN** `metrics_proposed` 事件到达 hub
- **THEN** 频道出现 @发起人 + 逐条指标（proxy/requires 标注）+ 用法行（confirm/reject/metrics 三子命令说明）

#### Scenario: 白名单未注册即死链（防回归）

- **WHEN** `metrics_proposed` 未在 hub 事件白名单注册而事件行到达
- **THEN** 该行被丢弃、无建行无频道 post——此形态 SHALL 有单测锁死为「注册前丢、注册后不丢」

### Requirement: confirm 拍板重入队

`/pa confirm #<id>` 仅发起人本人（user_id 相等判据）可执行；执行后 hub SHALL 将指标清单并入 PRD 快照（「验收指标（发起人确认）」节）经既有入站路径全新落盘入队（新 sha / 新 deliver_key），下游零特判。非 requested 态重复 confirm SHALL 幂等回执「已处理」。非发起人 SHALL 拒绝。confirm 时项目绑定已不存在 SHALL 保持 requested 并回执「项目未绑定」。同一原始需求（sha 祖先链）confirm/metrics 重入队达 3 次 SHALL 回执「请人工改写需求」不再自动重拟。人确认 SHALL NOT 豁免仓事实软拦截（冲突处置归 RSI 软出口）。

#### Scenario: 拍板后全新 run

- **WHEN** 发起人发 `/pa confirm #77` 且仓事实无冲突
- **THEN** 新 `mm-<sha8>.md` 含指标节入队，回执新 #id；该 run 对 critic/dispatch 是普通全指标 PRD（#75 形态），应直接 pass（若与已确认仓事实冲突则 RSI 软出口接手，不因确认豁免）

#### Scenario: 重入队达上界

- **WHEN** 同一原始需求的 confirm/metrics 重入队计数已达 3 次仍未收敛
- **THEN** 后续 confirm/metrics 回执「请人工改写需求后重新提交」，不再自动重拟

#### Scenario: 确认时项目已解绑

- **WHEN** requested 行存续期内项目 `/pa unbind` 后发起人 `/pa confirm`
- **THEN** 行保持 requested + 回执「项目未绑定」，无入队发生

### Requirement: reject 与 metrics 补充

`/pa reject #<id>`（发起人）置 rejected + 频道回执；`/pa metrics #<id> <补充>` 将补充并入 PRD 快照后重新入队走 critic→spec-author 链自然重拟（受上述重入队上界约束）。两者对非发起人拒绝、对非 requested 态幂等回执。

#### Scenario: 补充上下文重拟

- **WHEN** 发起人发 `/pa metrics #77 要支持一键切回默认主题`
- **THEN** 补充并入后重新入队，新 run 产出的指标清单含新上下文

### Requirement: 确认超时

requested 行 72h 未决 SHALL 置 expired 并频道回执；expired/rejected 行不可再 confirm。

#### Scenario: 过期

- **WHEN** requested 行龄 >72h 且 reconcile tick 扫过
- **THEN** 行置 expired，频道 @发起人 过期回执，后续 confirm 回执「已过期」
