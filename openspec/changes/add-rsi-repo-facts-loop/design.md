# Design: add-rsi-repo-facts-loop

## Context

cross-prd-learning-memory 已落地：终态后 curated evidence bundle → 结构化 candidate（enum schema）→ append-only 历史 + 可重建 catalog → **≥2 PRD byte-equal equivalence 晋升** → dev prompt 注入（≤5 条）+ 有效性反馈（置信/取代/退役）→ fail-open（不改任何判定谓词）。8 个 `learning_memory_*.py` 模块全离线可测。三次真机 run（#74/#75/#76）证实：教训只到 dev，上游语义链（prd/critic/test-author）无仓事实输入面，指标错配在源头无法拦截。

## Goals / Non-Goals

**Goals**：教训分轨——repo_facts 仓事实轨（一次即候选 + 人审生效 + 上游注入）落地；轨道 1 现状零触碰；fail-open 边界保持（repo_facts 不改测试/验证/发布谓词，只扩语义输入面）。
**Non-Goals**：轨道 1 策略、面板人审、facts 自动衰减、B 环本体。

## Decision: D1 教训类型学分轨（本体核心）

| | 轨道 1 失败模式 | 轨道 2 repo_facts |
|---|---|---|
| 本体 | 偶发行为模式（「dev 常忘跑 lint」类） | 结构性事实（「仓无 DOM 测试库」「jsdom 不解析 var()」） |
| 置信来源 | 复现确证（≥2 PRD equivalence） | 单次可验证事实 + 人审拍板 |
| 生效速度 | 慢（等复现） | 快（人审即生效） |
| 注入面 | dev prompt（既有） | **上游语义链**（prd/critic/test-author/spec-author） |
| 风险面 | 低（dev 提示性） | 高（影响 critic 判据）→ 人审强制 |

错配诊断（#76 根因）：仓事实被塞进失败模式轨 → 等 2 次复现 → 上游永远不知道。分轨后各自走各自的确证与生效路径。

## Decision: D2 轨道 2 schema：repo_fact_candidate

复用 candidate 提取管道（curated evidence bundle 同源），新 schema 类：`{"kind":"repo_fact", "project":..., "fact_class": enum("test_infra"|"dep_reality"|"harness_limit"|"convention"), "fact": "≤200 字可执行陈述", "evidence": [run 链引用], "affects": enum("prd"|"critic"|"test_author"|"dev"), "proposed_repo_facts_entry": {"key":..., "value":...}}`。fact 必须**可机械复核**（evidence 指向 log 行/文件存在性——如「仓 test_*.cjs 均纯函数风格、无 DOM 依赖」引 package.json devDeps + 3 个测试文件）；不可复核的转轨道 1 走复现。机械面：fact_key 由 (project, fact_class, fact 规范化) 派生，防重复提案。

## Decision: D3 人审环（MM confirm 形态复用）

hub 收 `repo_fact_proposed` 事件（新事件类型，event_outlet 增类）→ 频道 post @发起人（或 panel admin，v1 = HUB_PANEL_ADMINS——仓事实是项目级知识，admin 拍板而非单 run 发起人）→ `/pa fact ok #<id>` / `/pa fact no #<id>` / `/pa fact merge #<id> <改写>`。确认后写 profile `repo_facts` 节（YAML，带 `confirmed_by/confirmed_at/evidence_run` 溯源），条数上限 12/项目（FIFO 满则提示先清）。确认幂等/超时 72h 同 B 环语义。

## Decision: D4 上游注入点（四个，机械拼接零 LLM）

- **prd 生成**：MM wrap 路径 `_wrap_mm_prd` 与 radar-PRD prompt 尾部追加「## 仓事实（验收指标须贴此现实）」节（repo_facts 全量机械拼入）。
- **critic**：critic prompt 附 repo_facts 节；判据增机械理由——PRD 验收指标与 repo_facts 直接冲突（如 facts 说无 DOM 测试库而 PRD 要 jsdom 断言）→ drop（理由引 fact key）或 needs_metrics（B 环落地后）。
- **test-author / spec-author**：prompt 附同节（测试形态选择依据）。
- **dev**：轨道 1 注入保持；repo_facts 不再注入 dev（它自己读仓，事实对它冗余）。

## Decision: D5 fail-open 边界

repo_facts 注入是**语义输入面扩展**，非谓词变更：critic 对冲突的 drop/needs_metrics 是 critic 既有判定权的行使（判据文本扩展），不触碰测试门/verify/发布/重试谓词。人审前零生效（候选只进 MM 事件流，不进任何 prompt）——坏教训无法自动污染链路。

## Risks / Trade-offs

- **错误仓事实进 profile**（人审看走眼）→ evidence 链可追溯 + profile 版本化可回滚 + 条数上限；且 critic 冲突判定引 fact key，审计面完整。
- **fact 陈旧**（仓后来加了 jsdom）→ v1 人工清（D3 管理面）；自动失效检测 v2。
- **admin 不在 MM**（面板时代项目）→ v1 显式限制：repo_facts 轨仅 MM 绑定项目生效，未绑定项目候选暂存（表内 requested 滞留至过期，RUNBOOK 注记）。

## Migration Plan

纯增量：新事件类 / 新 profile 节 / 四注入点机械拼接 / MM 子命令。无 flag（未确认事实零注入，最坏 = 现状）。

## Open Questions

（待评审团裁决——architect 收敛性 / python-reviewer 钩子现实 / planner 分解）
