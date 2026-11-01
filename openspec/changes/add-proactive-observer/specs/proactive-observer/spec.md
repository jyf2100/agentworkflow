## ADDED Requirements

### Requirement: 影子提案（observe 模式主动观察）

hub SHALL 按项目 profiles `proactive:` 声明（缺省无声明 = 零行为 byte-identical）周期观察
（`HUB_OBSERVER_TICKS`，≥1 整数非法值启动 fail fast）白名单仓两类机械信号——T1 默认分支
CI 连续失败（≥ rules.ci_red_streak，缺省 2）、T2 issue opened 速率超 7d 基线 × rules.
issue_surge_ratio（缺省 3.0）且绝对数 ≥ rules.issue_surge_min（缺省 3）——判据 MUST 全机械
零 LLM。命中 SHALL 产出 ShadowProposal 文件（frontmatter：project/repo/signal/evidence/
proposed_action/policy_version/id；id = sp+hash(repo|signal|streak 锚)），幂等去重由文件名
承载；同一 streak 至多一条提案。提案 MUST NOT 进入任何执行链（不 dispatch 不 PRD）、MUST NOT
即时 MM 推送（仅日报聚合节 + 计数）；拉取失败走三态 UNKNOWN 跳轮（绝不基于 UNKNOWN 提案）。
`mode: auto` 声明 v1 MUST 降级 observe 并 warn（面板/日报标注预期与实际差异——防静默漂移）。
采纳追踪 SHALL 机械：提案 id 出现在后续 PRD frontmatter `source: shadow-proposal:<id>` 即命中，
digest 报周窗口命中率。

#### Scenario: CI 连红产出影子提案
- **WHEN** 声明仓默认分支最近 N 次 workflow runs 全败且达到 streak 阈值
- **THEN** 产出一条 ShadowProposal（证据含 run 链接链，拟议动作为机械模板），同 streak 不重复产出

#### Scenario: 信号拉取 UNKNOWN 不提案
- **WHEN** gh api 限流/超时/5xx
- **THEN** 本轮跳过该仓（日志可见），零提案零推送，下轮再查

#### Scenario: 无声明零行为
- **WHEN** profiles 无 proactive 块
- **THEN** 观察者零调用零落盘（baseline 不变）

#### Scenario: auto 声明降级且可见
- **WHEN** profile 声明 mode: auto（v1 未实装）
- **THEN** 观察按 observe 运行 + stderr warn + 日报标注「policy 预期 auto / 实际 observe」

#### Scenario: 采纳命中率机械可核
- **WHEN** 一条提案 id 出现在后续任一 PRD frontmatter 的 source 字段
- **THEN** digest 影子节将其计入采纳（周窗口命中率 = 采纳/提案）
