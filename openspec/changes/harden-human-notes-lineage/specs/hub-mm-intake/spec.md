## MODIFIED Requirements

### Requirement: 订阅直通（在飞 run 频道人话排队注入）

绑定频道除 `/pa` slash 入站外，hub SHALL 支持**订阅直通**：在飞 run 生命周期窗口内，授权面（成员三态，与 `/pa` 同门）作者在绑定频道的普通人话经 (channel, run) 关联（时间窗 + 最老在飞 run）落 **queue_item 谱系目录** `<state_root>/human_notes/qi-<queue_item_id>/`（署名 + message_id 幂等去重；谱系键 = 该 run 行的 queue_item_id，经既有 state 挂载 hub 写 pa 读同树），并在该 run 的下一 iteration 经既有 dev handoff 注入面带入（仅上下文非判据）。谱系归属 SHALL 跨 run 实例连续：队列重派（attempt+1）新 run 新树不改谱系键——attempt 1 窗口内已落盘的人话在重派后的 iteration 组装中 MUST 仍可见，不因换树成死信；同一 queue item 再次入队（新 queue item）则开新谱系目录，两代需求上下文不串味。采纳 SHALL 经既有事件面向频道回执（去抖一次）。直通 MUST NOT 新增派发授权（派发权仍在确认环）、MUST NOT 中断或改写在飞 run、MUST NOT 采纳授权面外作者的话；读取通道 MUST fail-open——任何读取异常不影响派发与事件转发；pa 侧谱系目录不存在或 queue item 回链缺失 → 无注入（`--human-notes` 零参数 baseline），MUST NOT 拦派发。

#### Scenario: 在飞 run 的人话排队注入

- **WHEN** 绑定频道有在飞 run 且授权作者在 bot 事件消息后发言
- **THEN** 该话落该 run 所属 queue item 的谱系目录 `human_notes/qi-<id>/`（署名+幂等），下一 iteration 注入面带入，频道收到一次回执

#### Scenario: 队列重派后谱系连续

- **WHEN** 某 queue item 的 run 失败重派（attempt+1，新 stamp 新 run 树），且 attempt 1 窗口内已有授权人话落 `qi-<id>/`
- **THEN** 重派后的 dispatch 组装读同一 `qi-<id>/` 谱系目录合并注入——attempt 1 的旧人话与 attempt 2 窗口内的新人话同树可见，无一成死信

#### Scenario: 授权面外作者被忽略

- **WHEN** 非成员三态授权面作者在窗口内发言
- **THEN** 不落盘不回执（与 /pa 入站同门），读取继续

#### Scenario: 读取异常不伤主链

- **WHEN** MM 读 API 超时/权限异常
- **THEN** 本轮跳过记日志（fail-open），派发/事件转发/对账零影响
