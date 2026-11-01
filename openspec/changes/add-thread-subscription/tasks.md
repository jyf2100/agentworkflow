## 0. 勘察（实装前置）

- [x] 0.1 MM bot token 读频道历史权限面实证；水位存储形态（db 列 vs 小表）
- [x] 0.2 human_notes → --handoff-artifact 装配接缝（dev handoff 机制的 orchestration 侧消费点）
- [x] 0.3 hub/events.py 事件枚举扩展口径（回执事件两侧同步注册）

## 1. 实现

- [x] 1.1 HttpMMClient 读频道消息（since 水位）+ (channel, run) 关联 + Gate 授权筛
- [x] 1.2 human_notes 落盘（署名/幂等去重）+ 下一 iteration 装配注入
- [x] 1.3 采纳回执事件（频道，去抖）+ daemon 低频槽 + RUNBOOK 节

## 2. 测试与验证

- [x] 2.1 单测：关联判据/Gate/幂等/水位（FakeMMClient 既有先例）
- [ ] 2.2 真机：在飞 run 频道回一句 → human_notes 落盘 → 下一 iteration 注入可见 → 回执一条

## 勘察证据（2026-11-01）

- 0.1：MM bot token 实测 GET channels/{id}/posts = 200 可读（2026-11-01 绑定频道 2 帖探通）——读取通道现成零新权限；水位形态建议 db 小表（runners 同款先例，勘察定案在实装时）
- 0.2：装配接缝定位：run_daily.py:2620 --handoff-artifact 组装透传（镜像 --feedback-artifact 模式）——human_notes 在此点并入注入通道（或同 flag 并列注入），零新机制
- 0.3：hub/events.py 事件枚举封闭（:13-14），pa 侧 event_outlet 写、hub 读转发——回执事件需两侧同步注册（pa-event-outlet 教训已有；实装时按 add 步骤双侧登记）

## 实施证据（2026-11-01）

- 1.1：notify 双客户端 read_channel_posts（MM v4 since 语义，httpx 同形态）+ bot_user_id（排除自帖）；(channel, run) 关联 = 绑定频道 + 在飞 run started_at 窗口 + 最老在飞 run（D1）；Gate = mm_intake.check_membership 成员三态（与 /pa 同门，MemberCache 复用）
- 1.2：hub/thread_relay.py：human_notes 署名落盘（frontmatter user_id/message_id/run_id + 原文；文件名 <ms>_<message_id> 幂等）+ pa 侧装配：run_daily._merge_human_notes（项目 runs 树 glob 最新 run 目录合并 artifact）→ _dev_cmd --human-notes → dev-agent prompt 独立标记块（仅上下文非判据，feedback 后 handoff 前）
- 1.3：水位 db intake_watermarks（thread_relay:{channel}，持久重启安全）+ 频道回执一条汇总/轮（notifier._post_bound_channel fail-open）+ daemon 第六低频槽 HUB_RELAY_TICKS（缺省 24≈2min）+ RUNBOOK §25
- 2.1：test_thread_relay.py 3 测试（窗口/Gate/bot 排除/落盘署名/幂等/水位/回执汇总/UNKNOWN/无 run 只推水位/单频道隔离）+ test_daemon 第六槽 + test_config 键；hub/quality.sh 全绿；pa quality.sh 全绿（--human-notes 注入面）
- 0.3 修订：回执走 hub 直发频道面（notifier），不经 pa 事件枚举——零枚举扩展（原「两侧同步注册」按此简化）
- 2.2 真机：部署 + one-shot 直通（水位建立/零崩溃）已做；**人话 showcase 需授权成员在频道真实发言**——列观测项（下次真实 run 期间在绑定频道说一句话即完成闭环）
