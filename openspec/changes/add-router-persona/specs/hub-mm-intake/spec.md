## ADDED Requirements

### Requirement: 自然语言问答（Router persona 解释层）

hub SHALL 支持绑定频道内非 slash 自然语言消息的语义问答：经全机械预筛（run 引用 `#N` /
提案引用 `sp-*` / 问句关键词集，`HUB_ROUTER_PATTERN` 可配）命中且预算内（`HUB_ROUTER_RATE`
缺省 6/频道/小时，超限静默）时，以只读 persona（pa-router，tools 仅 Read）headless 调用
（wall-clock ≤60s）生成回答并回帖频道。persona prompt SHALL 注入机械拼装的事实块（被引用
run 摘要 / 活跃影子提案 / 今日计数 + 可 Read 的详情文件路径），凭据值 MUST NOT 入块。Router
MUST 零行动权（不 enqueue/不 abandon/不改状态/不发确认——行动建议仅命令文本）；回答 MUST
只引事实且无事实明说。与订阅直通的分流 MUST 互斥（在飞 run 窗口内的授权人话走直通，其余
才进 router 候选）；bot 自帖 MUST NOT 触发。flag `PA_ROUTER_PERSONA` 默认 off（off = 非
slash 消息零处理 byte-identical）；调用失败/超时 MUST 静默跳过（stderr 留痕，不阻塞主链）。

#### Scenario: 问 run 阻断原因得事实回答
- **WHEN** 绑定频道成员发「#110 为什么阻断」且 flag 开
- **THEN** 预筛命中 → 回帖含 dispatch 记录的 gate/skip 事实（引用详情文件），≤200 词，无行动执行

#### Scenario: 闲聊零调用
- **WHEN** 预筛三形态全不命中（无 run/提案引用、无疑问词）
- **THEN** 零 persona 调用零回帖（预算不消耗）

#### Scenario: 与直通互斥
- **WHEN** 同一帖既在在飞 run 窗口内又含问句
- **THEN** 该帖走订阅直通（人话进 dev 上下文），router 不消费同帖

#### Scenario: 失败静默
- **WHEN** persona 调用超时/异常
- **THEN** 无回帖、stderr 留痕、排度与事件转发零影响
