# Proposal: fix-hub-queue-recency

## Why

面板队列页两处可用性缺口（2026-10-27 用户使用反馈）：

1. **最新在最下面**：`db.list_items` 无 ORDER BY（按 rowid 升序 = 旧行在上）。队列积压时用户每次都要翻到页底找刚提交/刚重跑的项。
2. **无时间列**：队列行看不到入队时间与最后状态变化时间——一项 failed 后分不清「刚失败」还是「昨天就失败了」，重跑决策没依据。队列项自有 `created_at`（入队）与 `updated_at`（每次状态迁移刷新：pending→running→done/failed）两列，但渲染层没展示。

## What Changes

- `db.list_items` 补 `ORDER BY id DESC`（最新优先；REST GET /queue 与面板同源同序，消费点均为展示无升序依赖）。
- `panel.render_queue` 行加「入队 / 更新」两列（`created_at` / `updated_at` 原样 ISO 机械展示，不做语义格式化——与只读面零语义渲染口径一致）。

### 非 Goal

- 时间戳本地化/相对化格式化（fix-hub-panel-ux 非 Goal 已声明「时间戳格式化按需后续」，本 change 维持原样展示）
- 队列行 join runs 表取 run 级 started_at/ended_at（队列项可多 attempt 多 run，一对多不稳定；updated_at 已覆盖「最后状态变化」语义）
- 分页/过滤（队列规模尚小，YAGNI）

## Capabilities

### Modified Capabilities

- `hub-panel`：`面板只读面` requirement——队列列表补「最新优先排序 + 入队/更新时间列」呈现要求（仍限机械展示 DB 自有行）。
