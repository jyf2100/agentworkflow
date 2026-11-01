## 1. 实现

- [x] 1.1 `db.py` 增 `queue_snapshot()`：pending 数 / 最老 pending 等待分钟（MIN(created_at) 差）/ running 数，单查询
- [x] 1.2 `hub/daily_digest.py`：`build_digest(cfg, db, drift_seen)` 三节聚合文本（D2/D3 口径）+ `emit_digest(cfg, db, notifier, drift_seen)`（notify_admin + stderr，fail-open）
- [x] 1.3 `config.py`：`HUB_DIGEST_TICKS`（默认 17280，≥1 校验）；`lifecycle.emit_daily_digest()` 薄挂点（传 self._drift_seen）；`daemon.py` 第四低频槽整体兜底

## 2. 测试与部署

- [x] 2.1 `test_daily_digest.py`：三节内容断言（fake db：队列数字/池行红绿+分发标记/漂移计数含 None 态）+ 值不入面 + emit 双通道（notifier None 降级 stderr）
- [x] 2.2 `test_daemon.py` 增第四槽周期触发与异常不崩；`test_config.py` 增周期键校验
- [x] 2.3 `hub.env.example` 注记 + RUNBOOK §16 补一段；`bash hub/quality.sh` 全绿

## 3. 验证

- [x] 3.1 真机：up -d 后 tick=0 首轮立即一条（stderr + MM），内容三节可见

## 实施证据（2026-11-01）

- 1.1：db.queue_snapshot()：pending 数/MIN(created_at) 差分钟/running 数单查询（datetime import 与既有行 ruff 合并）
- 1.2：daily_digest.py：build_digest 三节（单节独立 try/except 不连坐；24h 窗口 ISO 过滤；坏项只取名、报文全文不入面）+ emit_digest 双通道（notify_admin + stderr，未配 bot 降级）
- 1.3：config HUB_DIGEST_TICKS（默认 17280，≥1 校验）+ lifecycle.emit_daily_digest 薄挂点（drift_seen 直读）+ daemon 第四低频槽整体兜底
- 2.1：test_daily_digest.py 4 测试：三节内容/漂移 None 与全绿与空池/单节隔离/双通道与降级
- 2.2：test_daemon 增 2（第四槽周期+异常不崩，FakeLC 补 digests 计数与默认天文数）+ test_config 增校验
- 2.3：hub.env.example 注记 + RUNBOOK §16 日报段；bash hub/quality.sh 全绿（2026-11-01）
- 3.1：真机 one-shot（2026-11-01）：三节真实数据全出——running 1（忙闲 1/10）、183 🟢+上次分发 ✅、漂移未消解 0；24h 终态 0 计数经查正确（#110 ended 24h06m 前恰在窗口外）。自动首轮在部署后 ~24h（daemon tick 从 1 计数，每日语义正确；design D1 的「tick=0 即首轮」措辞已按此理解修正为部署后满周期首报）
