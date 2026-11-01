## ADDED Requirements

### Requirement: 订阅直通（在飞 run 频道人话排队注入）

绑定频道除 `/pa` slash 入站外，hub SHALL 支持**订阅直通**：在飞 run 生命周期窗口内，授权面（成员三态，与 `/pa` 同门）作者在绑定频道的普通人话经 (channel, run) 关联（时间窗 + 最老在飞 run）落 run 伴生 `human_notes/`（署名 + message_id 幂等去重），并在该 run 的下一 iteration 经既有 dev handoff 注入面带入（仅上下文非判据）。采纳 SHALL 经既有事件面向频道回执（去抖一次）。直通 MUST NOT 新增派发授权（派发权仍在确认环）、MUST NOT 中断或改写在飞 run、MUST NOT 采纳授权面外作者的话；读取通道 MUST fail-open——任何读取异常不影响派发与事件转发。

#### Scenario: 在飞 run 的人话排队注入
- **WHEN** 绑定频道有在飞 run 且授权作者在 bot 事件消息后发言
- **THEN** 该话落 human_notes（署名+幂等），下一 iteration 注入面带入，频道收到一次回执

#### Scenario: 授权面外作者被忽略
- **WHEN** 非成员三态授权面作者在窗口内发言
- **THEN** 不落盘不回执（与 /pa 入站同门），读取继续

#### Scenario: 读取异常不伤主链
- **WHEN** MM 读 API 超时/权限异常
- **THEN** 本轮跳过记日志（fail-open），派发/事件转发/对账零影响
