## Decisions

### D1 关联判据（channel, 时间窗）
绑定频道 + 在飞 run 窗口（run started_at 之后）+ bot 事件消息之后的最早人话归最老在飞 run——不引入回复引用格式要求（用户零学习成本）；歧义（多 run 并行同频道）按项目单飞（single-flight 串行）现状天然单 run。

### D2 落点=排队面（dev handoff 既有注入面）
在飞注入需打断 SDK 会话（高风险）；排队面复用 add-dev-handoff-artifact 的 `--handoff-artifact`（仅上下文非判据的既有语义）在 retry/revise 的下一 iteration 带入——零新注入机制。落盘 `human_notes/<seq>_<stamp>.md`（署名 + 原文 + 关联 run_id，meta_sign 同款署名惯例）。

### D3 读取通道与幂等
HttpMMClient 增读频道消息方法（MM REST v4 channels/{id}/posts since=上次水位——水位存 hub db 新列或 runners 同款小表，勘察定）；(message_id) 幂等去重（event_seen 同型）。fail-open 全承（观察面纪律——读取挂了不影响派发）。

### D4 前置勘察（tasks 0.x）
MM bot 读权限面（现有 bot token 能否读频道历史）、human_notes 与 handoff 工件的装配接缝、events.py 事件枚举扩展口径（hub↔pa 两侧同步注册——pa-event-outlet 教训）。

## 依赖
建议在 add-event-sources 之后实施（同一批 MM/事件面勘察可复用）；不阻塞起草。
