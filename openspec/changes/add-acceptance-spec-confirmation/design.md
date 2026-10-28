# Design: add-acceptance-spec-confirmation

## Context

critic 枚举现 pass/drop/revise（revise = 1 次修订回环，`run_daily.py:1538`）。hub 队列 `queue_items.status CHECK ('pending','running','done','failed')`。MM 入站 `/pa` 子命令 run/bind/unbind（`mm_intake.py:194-203`）。事件转发已修（`locate_pa_artifact`，2026-10-28 上线）——`metrics_proposed` 事件可依赖同管道进 hub。

## Goals / Non-Goals

**Goals**：模糊需求「不毙、补规格、人拍板」全环闭合于 MM 渠道；critic 判定面语义不劣化（无据仍 drop）；幂等与超时明确。
**Non-Goals**：interactive 按钮、面板路径、spec-author 读码（见 proposal Out of Scope）。

## Decision: D1 critic 第四态的机械判据

critic persona prompt 判据段增：`有据（来源齐）但验收不可执行（无具体可测指标）` → verdict `needs_metrics`（不再 drop）。**drop 收窄定义**：无据 / 与项目 match_surface 无关 / 需求本体自相矛盾。三态→四态向后兼容：编排器对 `needs_metrics` 的处置 = 调 spec-author（不进 revise 回环、不 dispatch）。

## Decision: D2 新 persona pa-spec-author（不复用 test-author）

test-author 输入是「**过闸 PRD 的验收点**」（确定性纪律落文件）；spec-author 输入是「**未过闸的模糊需求**」（提议清单待人确认）——职责、输入、输出物、纪律全不同，复用会污染 test-author 的确定性契约。输出一行 JSON：`{"metrics":[{"id":"m1","metric":"<可断言指标>","from":"<PRD 原文依据>"}...],"refused":null}`。工具面 Read-only（读 PRD + profile），模型路由随 persona 体系（`PA_PERSONA_MODEL_PA_SPEC_AUTHOR`）。

## Decision: D3 拟指标在 pa 侧、确认态在 hub 侧

spec-author 由 **pa 编排器**在 critic needs_metrics 后同 run 内调用（事件 `metrics_proposed` 落 events.jsonl），run 正常退出。**hub** 收事件（`_forward_events` 同管道）→ 频道 post 清单 + `confirmation_requests` 建行。理由：LLM 面 pa 侧已全备（persona 链），hub 保持零 LLM 不变量；hub 只做状态机与回执。

## Decision: D4 confirmation_requests 表（不动 queue_items CHECK）

新表：`id INTEGER PK / item_id INTEGER（原 queue item）/ project / channel_id / initiator / prd_text TEXT（原 PRD 全文快照）/ metrics_json TEXT / status CHECK ('requested','confirmed','rejected','expired') / created_at / decided_at`。 ALTER 既有表 + 重建 CHECK 的迁移风险 > 旁表；queue_items 状态机零触碰（复用既有入队路径重新投递）。

## Decision: D5 confirm 语义 = 指标并入 + 新 PRD 全新 run

`/pa confirm #id`（发起人 user_id 判据）→ hub 把 metrics 清单 append 进 PRD 快照（新节「## 验收指标（发起人确认 <日期>）」）→ wrap 落盘新 `mm-<sha8>.md`（sha 必变）→ **全新 deliver_key** → 走既有入队路径（绑定/幂等/回执全复用）。好处：确认后的 run 对下游（critic/dispatch）零特判——它就是一份验收点齐全的普通 PRD。`reject` → 行 rejected + 频道回执。`metrics` 子命令 → 附加上下文并入 PRD 快照 → 重新调 spec-author？**否**——v1 重新入队原需求（快照含补充）走 critic→needs_metrics→spec-author 全链（自然重拟，零新代码路径）。

## Decision: D6 超时与幂等

- requested 行 72h 过期：hub 周期扫（复用 reconcile tick）置 expired + 频道回执。
- confirm/reject 重放幂等（行状态机：非 requested 态重复操作回执「已处理」）；发起人判据 user_id 严格相等（非成员查询路径，无三态问题）。

## Risks / Trade-offs

- **critic 判定面漂移**（needs_metrics 与 drop 边界主观）→ 真机回归：#74 原文重放应判 needs_metrics 而非 drop，#75 原文应直接 pass。
- **spec-author 拟的指标不合发起人意** → 天然由 confirm 环吸收（reject/metrics 重拟），无下游污染面。
- **频道消息变长**（指标清单）→ 格式化限每条一行 + 总条数封顶 8（超出截断提示走面板看全文——面板只读面已有 events tail）。

## Migration Plan

纯增量：新表 / 新子命令 / 新 persona / critic 判据段更新。无 flag（critic 新判定只影响本会 drop 的路径——最坏回退 = 指标仍拟但人不确认，72h 过期，行为不劣于现状 drop）。

## Open Questions

无。
