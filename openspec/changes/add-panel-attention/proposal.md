# Proposal: add-panel-attention

## Why

面板现有页（队列/runs/池/凭据）都是**全量视图**——人要自己找「哪些需要我」。dsh 面板原则
「只显示需要人的地方」（演进路线 P2-⑩）是注意力预算的 UI 兑现：一个聚合页把**四类等人事项**
一屏列齐，每项带直达行动。数据源 100% 既有，本 change 是纯呈现层。

## What Changes

1. **`/panel/attention` 新页**：四类聚合——
   - **阻断待 triage**：dispatch 终态 blocked_test_gate / verify_invalid / triage 的 run
     （近 72h，含 gate_reason/skip_reason 摘要 + 重派按钮）
   - **确认环待决**：confirmation_requests requested 态（含 72h 倒计时）
   - **影子提案待采纳**：shadow_proposals 目录现存提案（含命中率注记 + 「按此提案 /pa run」
     命令复制框）
   - **漂移未消解**：本进程 drift_seen 暴露面（lifecycle 只读读数——经 digest 同源）
2. **顶部导航徽标**：待办总数（0 时页面显示「无事需要你」——dsh「不需要时保持安静」的 UI 形态）。
3. **读面守卫**：任何有效会话（观察面）；行内行动按钮复用既有 admin 门端点（面板改行为面
   既有纪律零新增）。

## Non-Goals

- 不新增行动端点（重派/确认/采纳全部复用既有——「采纳」= 命令复制框不是按钮，v1 保守）。
- 不做推送（页是拉面；MM 推送属漂移/提案各自的通道纪律）。
- 不做跨项目全局页（按项目过滤器，缺省本会话可见全部）。
