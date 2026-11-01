## 0. 勘察（实装前置）

- [x] 0.1 fetch 段 inbox/discovery/去重机制现状与事件文件落点对齐（机械伴生形态可行性）
- [x] 0.2 gh api 事件端点（issues/comments/statuses）限流与三态分流口径；成员三态授权面读取接缝

## 1. 实现

- [x] 1.1 `hub/event_poller.py`：profiles `events:` 声明解析（未声明零行为）+ daemon 低频槽 + gh api 轮询三态
- [x] 1.2 Fast Path 机械筛（作者授权面/类型白名单/sha 去重）+ 事件文件落盘（frontmatter 机械拼接）
- [x] 1.3 `HUB_EVENT_TICKS` 配置 + hub.env.example + RUNBOOK 节

## 2. 测试与验证

- [x] 2.1 单测：声明解析/三态/筛判据/去重/落盘形态（fake gh client）
- [x] 2.2 真机（2026-11-01，one-shot 轮询 + 容器侧 fetch→radar 冒烟）：cc-web-control 声明 repo=jyf2100/agentworkflow author=jyf2100 → 真发 issue+评论（IssueCommentEvent/IssuesEvent 各一，PushEvent 被类型白名单正确滤除）→ one-shot 落 2 个事件文件（frontmatter/正文机械拼接全对、文件名幂等）→ v0.1.36 容器挂 events ro 跑 fetch→radar：`[radar] source=events-inbox kind=directory｜今日新=2`（candidates=0 = 语义筛对测试事件正确打出局）。事件→PRD 的 LLM 半段由后续真实事件自然验证

## 勘察证据（2026-11-01）

- 0.1：fetch_dedup_list（run_daily.py:389）按 profile 机械去重清单存在；事件文件落 profile 语料 root（如 Knowledge/github-watch/）即被 fetch「今日新内容」date-marker 机制机械发现——语料增量形态可行，实装对齐文件命名/日期约定即可
- 0.2：成员三态接缝现成：mm_intake.membership_verdict（无缓存）+ 带缓存版 + notify.channel_members 翻页全集（notify.py:138）——Fast Path 作者筛直接复用零新判据；gh api 事件端点（issues/comments/statuses）沿用 stale_check gh_client 三态模式（限流→UNKNOWN 跳轮）

## 实施证据（2026-11-01）

- 1.1：hub/event_poller.py：load_events_declarations（sources.yaml 顶层 events 键容错装载，D5）+ gh_events_client（gh api --paginate --jq 投影，stale_check 同模式）+ poll_events（daemon 第五低频槽 HUB_EVENT_TICKS 默认 720）
- 1.2：Fast Path 全机械（类型白名单缺省三类型 + authors 授权面 D6 + 文件名 event_id 幂等去重 D7）+ 事件文件机械拼接（frontmatter + 正文截 2000）
- 1.3：config HUB_EVENT_TICKS（≥1 校验）+ RunSpec events_root 自动挂载（目录存在才挂，ro）+ hub.env.example + RUNBOOK §24（启用三步）
- 2.1：test_event_poller.py 4 测试（声明容错/筛+幂等/authors 空拒全+warn/UNKNOWN+单repo隔离）+ test_daemon 第五槽（周期+异常不崩）+ test_config 键校验；hub/quality.sh 全绿
