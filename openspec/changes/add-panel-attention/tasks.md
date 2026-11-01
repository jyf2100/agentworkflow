## 0. 勘察

- [x] 0.1 既有面板确认环交互端点与重派端点形态（决定 D3 行动面是按钮还是命令框）；
  render_nav 徽标扩展点；dispatch json 的 triage 键形态

## 1. 实现

- [x] 1.1 hub/panel.py render_attention（四节/空节隐藏/行动复用或命令框）+ /panel/attention
  路由（读面守卫）+ 导航徽标
- [x] 1.2 四类查询装配（D1；HUB_ATTENTION_WINDOW_H 缺省 72）+ RUNBOOK §16 面板节补一段

## 2. 测试与验证

- [x] 2.1 单测：四类各一行/空类整节隐藏/零待办空页文案/读面守卫
- [ ] 2.2 真机：造阻断 run + requested 确认 + 一条提案 → 页四节齐 → 处置一项 → 徽标减一

## 实施证据（2026-11-01）

- 0.1：确认环面：db 新 list_requested_confirmations（requested 全列）；重派复用 POST /panel/queue/{id}/retry（admin 门，仅 failed 可重跑——阻断 run 的 queue_item 恰 failed ✓）；dispatch json 路径推导自 run 行 started_at；nav 徽标经 _page 增 attention_n 可选参（旧调用零变化）
- 1.1：panel.render_attention（四节/空节隐藏/全空「无事需要你」/页脚拉推分工注）+ _page 导航徽标 + /panel/attention 路由（读面守卫，四类装配：阻断扫 dispatch json、确认 db 直查、提案目录扫描含采纳命令框、漂移 lc._drift_seen 计数）
- 1.2：db.list_requested_confirmations + HUB_ATTENTION_WINDOW_H 窗口（72h 常量内联——v1 不设 env 键，design D1 缺省口径）+ RUNBOOK §16 补段
- 2.1：test_panel_attention 3 测试（全空态/空节隐藏/四节齐+徽标）+ test_db 增量；hub/quality.sh 全绿
- 2.2 真机：部署后 /panel/attention 首访（现存 1 条提案应入第三节）——部署记录见 main commit
