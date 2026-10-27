## ADDED Requirements

### Requirement: 执行链段范围（inject→report）
hub 派发命令 SHALL 显式传 `--from-stage inject --to-stage report`：执行链 SHALL 跑满 pa 后半段 inject→critic→dispatch→report（而非止于 `--to-stage` 默认值 dispatch）——report 段经 `PA_REPORT_DIR`（派发时注入容器 env）双写产物至 state 卷 `state/reports/`，供面板 run 详情只读渲染（见 hub-panel 报告面与 pa-event-outlet report 双写）。report 段内 SMTP 通知在容器内 MUST 走既有 fail-open 语义（失败退化为告警、不阻塞终态）——容器形态通知面是 hub-notifications，MUST NOT 为此透传 SMTP env。`run_daily --to-stage` 默认值（dispatch）MUST 保持零变化（mac 本机 cron 形态不受影响）。

#### Scenario: 执行容器跑满后半段产 report
- **WHEN** hub 派发一个队列项、执行容器正常跑完
- **THEN** 容器命令含 `--from-stage inject --to-stage report`；dispatch 终态持久化后 report 段执行，state 卷 `state/reports/` 出现该 stamp 的报告产物，面板 run 详情可渲染报告

#### Scenario: SMTP 容器内 fail-open 不阻塞
- **WHEN** report 段 SMTP 通知在容器内因无 SMTP env 而失败
- **THEN** 退化为告警日志，run 终态不受影响（不为 SMTP 向容器透传 env）

#### Scenario: 历史无 report 产物 → 面板显「无报告」
- **WHEN** 面板查看本 change 之前的历史 run（state/reports/ 无其产物）
- **THEN** 面板按 hub-panel 既有语义显示「无报告」（非错误），不回填历史产物
