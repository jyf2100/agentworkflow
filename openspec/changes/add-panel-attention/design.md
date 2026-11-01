## 本体论声明

**注意力项（attention item）= 需要人决策/行动的聚合视图行**——派生物，无自有状态、零新
真相源（每行链回其源实体：run 行 / confirmation 行 / 提案文件 / drift seen）。范畴与四源
一一对应，绝不引入第五种「待办」实体（防 TODO 蔓延——待办的真相永远在源系统，聚合页只
投影）。判据「需要人」= 源系统的状态机停在人等态（blocked/requested/未采纳/未消解）——
状态机不动，页永远对。

## Decisions

### D1 四类查询（全既有面，零新查询形态）
| 类 | 源查询 | 人等态 |
|---|---|---|
| 阻断 triage | runs 表近 72h 终态 + dispatch json 的 triage/block 键 | blocked_*/verify_invalid |
| 确认环 | confirmation_requests status='requested' | requested |
| 影子提案 | data_root/<proj>/shadow_proposals ls（frontmatter 抽 id/signal/created） | 未采纳（无对应 PRD source） |
| 漂移 | lifecycle._drift_seen 的 drift 类键计数 + 最近心跳行 | 未消解 > 0 |
dispatch json 读放 hub 进程（state 卷本机可达）；72h 窗口常量可配（HUB_ATTENTION_WINDOW_H
缺省 72）。

### D2 渲染与导航
panel.py 新 render_attention(items)——单表四节（每节空则整节隐藏——空页 = 一行「无事需要
你」）；导航栏「待办(N)」徽标（render_nav 既有函数扩展，N=总数）。样式全复用面板既有 CSS 面。

### D3 行动面（复用与克制）
- 重派：复用 POST /queue 的 re-enqueue 形态（admin 门既有）——若确认无既有重派端点则 v1
  只显示命令复制框（与「采纳」同保守），端点补齐列 follow-up（任务 0.1 勘察定）。
- 确认：复用 /pa confirm 的面板形态端点（confirmation 既有面板交互面）。
- 采纳：**命令复制框**「/pa run …evidence: shadow-proposal:sp-xxx」——采纳是人的决定，
  v1 不做一键（提案→PRD 的语义组装人把关）。

### D4 与 digest 的关系（一拉一推，同源不同面）
日报（推）= 时点聚合快照；attention 页（拉）= 实时状态投影。两者读同样的源，绝不互相引用
（都是派生物，无主从）。页顶注明「实时视图，日报为每日快照」。

## Rollout 与回滚

纯呈现层新页——零行为变更零迁移；回滚 = 删路由。空类隐藏语义保证新装对无人值守项目
零噪音。

## Open Questions（不阻塞起案）

- 漂移项源在 hub 进程内存（_drift_seen）——重启后未消解项清零至再发现，页数瞬时归零属
  已知语义（漂移去抖同款），页脚注说明。
