## ADDED Requirements

### Requirement: 事件轮询接入（GitHub 单源 v1）

hub SHALL 按 profiles 的 `events:` 声明（未声明 = 零行为）周期轮询（`HUB_EVENT_TICKS`，≥1 整数非法值启动 fail fast）白名单仓的 GitHub 事件（issue/PR 评论、CI 状态），经**两级过滤**后将事件以机械拼接的增量语料文件（frontmatter：源仓/类型/作者/URL + 正文，零语义加工）落入项目语料目录，供 fetch/radar 既有链路消费。Fast Path（hub 侧）MUST 全机械：作者 ∈ 项目授权面、事件类型白名单、内容 sha 去重——零 LLM 调用；Slow Path 语义筛由既有 radar 承担（本能力不建语义判据）。轮询失败走三态分流（FOUND/NOT_FOUND/UNKNOWN——UNKNOWN 绝不当天断源），任何异常 MUST NOT 崩排度循环。事件 MUST NOT 短路派发链（必须经 radar→PRD→critic 全链——授权链不动）。

#### Scenario: 白名单事件落盘进链
- **WHEN** 已声明仓的授权作者提交一条 PR 评论且轮询周期到达
- **THEN** 该事件以机械拼接文件落入语料目录，次日 fetch/radar 链路按既有打分链消费

#### Scenario: 未授权作者被 Fast Path 拦下
- **WHEN** 非项目授权面作者的事件被轮询到
- **THEN** 不落盘不告警（机械筛零 LLM），轮询继续

#### Scenario: 轮询 UNKNOWN 不误伤
- **WHEN** gh api 超时/限流/5xx
- **THEN** 本轮跳过记日志（三态 UNKNOWN），既有语料与链路零影响，下轮再查

#### Scenario: 未声明零行为
- **WHEN** profiles 无 `events:` 声明
- **THEN** 轮询器零调用零落盘（byte-identical 基线）
