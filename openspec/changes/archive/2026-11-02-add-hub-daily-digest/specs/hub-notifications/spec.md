## ADDED Requirements

### Requirement: 运维日报（一日一条聚合）

hub SHALL 每日（`HUB_DIGEST_TICKS`，默认 17280 tick ≈ 5s tick 下一日，≥1 整数非法值启动 fail fast）产出一条运维日报并经管理面告警通道（`notify_admin` MM 私信）投递配置单用户，同时 stderr 恒留痕；未配 bot 通道 = 降级 stderr（可见的丢）。日报 SHALL 聚合三面且仅含现状事实（计数/名称/时长/红绿标记——凭据值与报文全文 MUST NOT 入面）：① 队列（pending 数、最老 pending 等待时长、running 数、近 24h succeeded/failed 终态计数）；② runner 池（各行探针红绿与坏项名、忙闲 load/capacity、上次镜像分发结果标记）；③ 配置漂移（未消解漂移项计数；看门狗未运行时标注无数据）。日报槽异常 MUST NOT 崩排度循环（观察面纪律），MUST NOT 建立任何新持久化写面。

#### Scenario: 每日一条聚合投递
- **WHEN** daemon 累计 `HUB_DIGEST_TICKS` 个 tick
- **THEN** 配置单用户收到一条含队列/池/漂移三节的日报私信，stderr 同文留痕；中间 tick 零产出

#### Scenario: 未配通道降级
- **WHEN** 未配 MM bot 通道
- **THEN** 日报全文降级 stderr（可见的丢而非静默吞），周期与内容不变

#### Scenario: 周期键非法启动拒起
- **WHEN** `HUB_DIGEST_TICKS` 为 0、负数或非整数
- **THEN** load_config 抛 ConfigError 拒绝启动（与既有周期键同口径）

#### Scenario: 日报异常不崩循环
- **WHEN** 单轮聚合或投递抛异常（db 故障等）
- **THEN** 记 warn 后排度循环照常，派发与对账不受影响
