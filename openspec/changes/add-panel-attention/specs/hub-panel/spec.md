## ADDED Requirements

### Requirement: 注意力视图（只显示需要人的地方）

面板 SHALL 提供 `/panel/attention` 聚合页：四类人等事项——阻断待 triage（runs 近
`HUB_ATTENTION_WINDOW_H`（缺省 72h）终态 blocked_test_gate / verify_invalid / triage 类，
含 gate/skip 摘要）、确认环待决（confirmation_requests requested 含超时倒计时）、影子提案
待采纳（现存 shadow_proposals 含命中率注记）、漂移未消解（drift 计数）——每项链回源实体，
聚合页 MUST 零自有状态零新真相源（纯投影）。空类整节隐藏，全空显示「无事需要你」；导航栏
SHALL 带待办总数徽标。读面任何有效会话；行内行动 MUST 复用既有 admin 门端点或呈现命令
复制框（采纳 = 命令框，人复制执行——v1 无一键）。页面 MUST 标注实时视图与日报（推面）
的分工。

#### Scenario: 四类齐备一屏列齐
- **WHEN** 存在阻断 run、requested 确认、未采纳提案、未消解漂移各一
- **THEN** 页面四节各一行（各带源摘要与行动入口），徽标显示 4

#### Scenario: 空态安静
- **WHEN** 四类全空
- **THEN** 页面仅显示「无事需要你」，徽标 0——不渲染空节框架

#### Scenario: 投影无主从
- **WHEN** 处置掉一项（如确认环 confirm）
- **THEN** 徽标减一且源实体状态变化（页与日报各自独立读源，互不缓存）
