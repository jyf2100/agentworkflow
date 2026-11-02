## Decisions

### D1 第四低频槽 + 默认开
digest 是纯只读观察（零写面、单条/日、收件人=配置单用户），默认开与探针/陈旧检测同档（它们也是默认开）；`HUB_DIGEST_TICKS` ≥1 同型校验，配超大值即事实关闭。daemon tick 从 1 计数——部署后满 N tick 首报（每日缺省 ≈ 24h 后第一条），此后每 N tick 一条。

### D2 聚合面三节（全部既有数据，零新采集）
- 队列：`queue_snapshot()` 单 SQL（COUNT + MIN(created_at) 差分钟）+ `runs_in_status("running")` 计数 + 24h 终态（list_all_runs 过滤 ended_at ≥ now-24h，succeeded/failed 分计）。
- 池：`list_runners()`（已带 load 列）——每行 name + 探针（probe_ok：🟢/🔴/未探 + probe_json 坏项名）+ load/capacity + image_sync_json 上次结果 ✅/❌。
- 漂移：`drift_seen` 中 drift 类键计数 = 未消解漂移项（lifecycle 持有的去抖集合直读，零新状态）；None（看门狗未跑）标注「无数据」。

### D3 值不入面
文本仅含计数/名称/时长/红绿标记；probe_json 坏项只取名（daemon/image/mount），不含报文全文（报文含镜像 tag/地址——低敏但保持最小面）。

## Risks / Trade-offs

- [tick 计数与整日漂移（重启重置）] → 重启即重发一条（可接受，同探针语义）；需要墙钟对齐（每日固定时刻）属后续增强。
- [runs 24h 过滤依赖 ended_at ISO 串比较] → 同 SQLite 既有时间戳口径（_now() ISO），字符串比较成立。
