# Proposal: add-hub-daily-digest

## Why

P0 观察面三件套（漂移看门狗 / runner 探针+镜像分发 / 队列状态）各自只有「出事才告警」或 stderr 心跳——没有一条「今天整体怎么样」的聚合线。dsh-superagent 面板原则「只显示需要人的地方」的日报版：一日一条 MM 私信，扫一眼即知队列/池/漂移三面健康，异常项才需要点开（演进路线 P0-④）。

## What Changes

1. **hub/daily_digest.py**：`build_digest(cfg, db, drift_seen)` 纯聚合（队列 pending/running + 最老等待时长 + 24h 终态计数；runner 池各行探针红绿+忙闲+上次分发结果；漂移未消解项数）→ 一段文本。零新写面、值不入面（计数/名称/时长 only）。
2. **daemon 第四低频槽** `HUB_DIGEST_TICKS`（默认 17280 ≈ 5s tick 下每日一轮）：经 `notify_admin` MM 私信 + stderr 恒留痕；未配 bot = 可见的丢（降级语义同陈旧告警）。fail-open 同三前例。
3. **db 增一个读 helper** `queue_snapshot()`（pending 数 + 最老 pending 等待分钟 + running 数，单 SQL）。

## Non-Goals

- 不做成本汇总（runs 表无成本列；成本在 dispatch json，列扩展另案）。
- 不建面板/持久化（纯即时聚合文本）。
- 不重复既有告警语义（漂移/分发失败仍走各自即时告警+去抖；日报只汇总现状不即时告警）。
