# Proposal: add-acceptance-spec-confirmation

## Why

critic 对「来源交代齐但验收不可执行」的模糊需求一律 **drop**（真机 run #74：一句「赛博朋克风格」被毙）。后果是规格撰写的活全推给需求方：人必须先把主观描述手工落成可断言指标（#75 的人工具体化）才能过闸——用户在 MM 频道「随口发需求」的产品体验被这个分界线打断。

用户已确认方向（2026-10-28 会话）：**agent 拟指标 → 人拍板**——模糊需求不再毙，agent 补规格，发起人只在 MM 频道点头。

## What Changes

- critic 判定面：对「有据 ✓ + 验收不可执行」的 PRD 从 `drop` 改判新枚举 `needs_metrics`（drop 保留给「无据/不可救」）。
- pa 侧新 persona **pa-spec-author**：对 needs_metrics PRD 拟「可断言验收指标清单」（结构化 JSON：逐条指标 + 依据 PRD 原文哪句），写事件 `metrics_proposed` 后 run 正常退出（不 dispatch）。
- hub 侧新表 `confirmation_requests`（零侵入 queue_items 状态 CHECK）：收 `metrics_proposed` 事件 → 指标清单格式化进频道 `@发起人` + 建行（requested）。
- MM 入站新子命令：`/pa confirm #<id>`（拍板：指标并入 PRD 新 sha 重新入队全链跑） / `/pa reject #<id>`（放弃） / `/pa metrics #<id> <补充>`（附加上下文重拟）。确认权 = 发起人本人（user_id 判据）。
- 超时：requested 行 72h 过期（频道回执过期）。
- **来源分叉**：needs_metrics 判据仅对 MM 绑定源生效——critic prompt 按来源分（编排器知 item 来源）；panel/radar 源模糊 PRD 保持既有 revise/drop 旧径（机械降级，summary 注「需 MM 渠道确认环」），不产出无人可确认的滞留行。

## Out of Scope

- 面板路径确认环（v1 仅 MM 绑定渠道；非 MM 源见上「来源分叉」——模糊仍走旧径，无机制空转）。
- pa-spec-author 读目标仓代码（hub 容器内无目标仓；v1 从 PRD × profile 拟，v2 再议 clone 后读码）。
- E2E 级指标（依赖 add-e2e-acceptance-harness 落地后 test-author 面扩，正交组合）。
- MM interactive 按钮（bot 仅纯文本 post 是现状能力面，slash 子命令零新权限）。
