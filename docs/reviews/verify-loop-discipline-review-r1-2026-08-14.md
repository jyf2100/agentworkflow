# verify-loop-discipline — R1 评审记录

- **Change**: `openspec/changes/verify-loop-discipline/`
- **轮次**: R1（首评，3 专家视角并行）
- **日期**: 2026-08-14
- **评审者**: R1-A 结构 / R1-B 职责+spec / R1-C 根因+契约（3 个专家 agent）
- **基线**: proposal + design + 2 spec deltas + tasks（2026-08-13 落档）

## 冻结验收矩阵

| 维度 | 判据 | 状态 |
|---|---|---|
| 根因诊断证据链 | A（stamp→branch/PR）+ B（pa-verify 退化为红绿裁判 + 基建失败误路由）行号锚定、可复核 | ✅ 通过（R1-C F4 行号微修） |
| A 结构完整性 | A1-A4 是否物理可达（无崩溃路径） | ⚠️ P1-1：缺 checkout 适配，round2 崩 → 加 A1a |
| B 职责合法性 | B1 范围是否在 ADR-0002 授权内 | ⚠️ F1：原 10 维度越界 → **用户裁决收敛三维度三方并行** |
| spec 可测性 | scenario THEN 是否编排器侧可测 | ⚠️ F-B1：scenario 2/3 混 persona prose → 重写 |
| 基建三分覆盖 | B2 是否覆盖所有项目类型 | ⚠️ F-B2：Python 仓无 install 闸 → 加 B2a |
| 推迟/范围外 | — | FU-1 worktree scenario / F-B4 base Purpose 协调（r2） |

## 执行摘要

3 个专家视角并行评审，共 **15 条 findings**：3 HIGH、5 MEDIUM、7 LOW/FOLLOWUP。**无 P0**。

最关键的两条方向性 HIGH：

1. **R1-C Finding 1（ADR-0002 冲突）**：B1 原设计的 10 维度语义审查与 ADR-0002 项目自治字面冲突（ADR 把「代码质量 / review 流程」划归项目自治）。**用户裁决**：pa-verify = 三方独立审核（功能对齐 / 开发遗漏 / 质量达标），与项目自审核**并行不替代**；不违 ADR-0002（独立验证闸 = 验交付物，非 scope 规定——审已产出 PR，不替项目决定怎么开发）。design 新增「ADR-0002 边界澄清」节消解字面张力，B1 从 10 维度收敛三维度。
2. **R1-A P1-1（checkout 崩溃）**：A1 去 stamp 后，revise round2 在同 worktree `git checkout -b auto/{slug}` 必崩于「branch already exists」→ exit 13，本 change 核心目标（单 PR、同分支累积）物理不可达。加 task A1a（分支存在则 checkout，否则 -b）。

第三条 HIGH（R1-B F-B1）：spec scenario 2/3 的 THEN 混 persona 内心推理，不可从输出 JSON 测试，违 harden-pa-verify F-R2-1 纪律 → spec 重写为编排器侧结构断言（context 构造 + 输出字段）。

所有 HIGH 已处置。中等 findings（F-B2 Python 闸 / F-B3 锚点 / Finding 2 根因措辞 / P2-1 single_flight / P2-2 stamp 兄弟 / Finding 5 平面越界 / 行号偏移）已落 tasks/proposal/design 修正。

## Findings

### HIGH

| ID | 视角 | 陈述 | 处置 | 状态 |
|---|---|---|---|---|
| R1-C-F1 | 根因+契约 | B1 原 10 维度语义审查与 ADR-0002 项目自治冲突（代码质量/review 划归项目） | **用户裁决**：收敛三维度（功能对齐/遗漏/质量）三方独立审核，与项目自审并行不替代；design 加「ADR-0002 边界澄清」节 | ✅ 消解 |
| R1-A-P1-1 | 结构 | A1 去 stamp 后 round2 `git checkout -b` 崩于 branch exists → exit 13，核心目标不可达 | 加 task A1a（分支存在 checkout / 否则 -b，dev-agent.py L799-803） | ✅ 已落 tasks |
| R1-B-F-B1 | 职责+spec | scope scenario 2/3 THEN 混 persona prose，不可测（违 F-R2-1） | spec 重写：scenario 2 = context 含三锚源；scenario 3 = profile 裁剪维度集 | ✅ 已重写 |

### MEDIUM

| ID | 视角 | 陈述 | 处置 | 状态 |
|---|---|---|---|---|
| R1-B-F-B2 | 职责 | Python 仓 independent_verify install_rc 硬编码 0（L2658），B2 基建三分对 Python 失效 | 加 task B2a（Python install 步骤） | ✅ 已落 tasks |
| R1-B-F-B3 | 职责 | 绿测试+语义红 revise 无机械锚点（Line-anchored 要失败测试锚，绿路径没有） | B3 锚点改 PRD 验收清单/diff 行，不依赖失败测试；已落 spec/proposal | ✅ 已落 |
| R1-A-P2-1 | 结构 | proposal 风险①「同 slug 跨 cron 由 single_flight 防」失真（single_flight 是 per-owner_repo 并发槽） | 修 proposal 风险①：去重是 radar/prd+circuit_breaker | ✅ 已修 |
| R1-A-P2-2 | 结构 | stamp 兄弟消费点（run_log L809 / coordinator L817）未声明保留，实现者可能误删 | A1 注释加「stamp 仍用于 run_log/coordinator，仅 branch 去 stamp」 | ⏳ r2 补注释 |
| R1-C-F2 | 根因 | 根因 B「install 失败被当红」不准（实为 test_rc=None「未跑」）+ fix-locus 漏 verify_prompt L640-646 | proposal Why §B 措辞精修 + B3 task 含 verify_prompt 解耦 | ⏳ r2 核实措辞 |
| R1-B-F-B4 | spec | base Purpose carve-out（只锚 location+bundle）与新 verdict-composition requirement 矛盾 | r2 协调 base Purpose requirement 措辞 | ⏳ r2 |

### LOW / FOLLOWUP

| ID | 视角 | 陈述 | 处置 | 状态 |
|---|---|---|---|---|
| R1-B-F-B5 | spec | Bundle-scoped green-path 指引被间接废弃风险 | r2 确认 Bundle-scoped requirement 与新 scope 不冲突 | ⏳ r2 |
| R1-B-F-B6 | 职责 | 根因 B「被当成红」措辞（实为「未跑」） | 并入 F2 精修 | ⏳ r2 |
| R1-C-F3 | 根因 | #55/#56 同 slug 不同 head 是推断，非 gh 实证 | E1 运维处理时 gh 实证 | ⏳ E1 |
| R1-C-F4 | 根因 | 行号偏移（L1258 注释→L2472；L794-795→L795-796） | tasks 已修正行号 | ✅ 已修 |
| R1-C-F5 | 契约 | A2 dev-agent import run_daily._lookup_pr 违 ADR-0001（平面越界） | A2 改 dev-agent 内自实现 `gh pr list --head` 查重 | ✅ 已落 tasks |
| R1-A-FU-1 | 结构 | worktree SHALL 声明无专属 scenario | r2 补 worktree scenario 或归 structure requirement | ⏳ r2 |

## 用户设计裁决（Finding 1 消解）

**裁决原文**：「审核代码与功能是否对齐，开发是否遗漏、质量是否达标（自审核和三方审核不冲突）」。

**落地**：
- pa-verify = 发布前**三方独立审核**，审三维度：① 功能对齐（diff 真实现 PRD/Issue 需求）② 开发遗漏（PRD 验收点逐项覆盖、无漏）③ 质量达标（代码质量/安全/性能/兼容硬伤，profile 裁剪）。
- 与项目自审核**并行不替代**（项目自审 = 开发期内部 review；控制面三方审 = 发布前独立把关）。
- 不违 ADR-0002：独立验证闸从「验绿」扩为「验交付物」，仍是「验证」非「scope 规定」（审已产出 PR，不替项目决定怎么开发）。
- 不另开大 ADR；design「ADR-0002 边界澄清」节消解字面张力，r2/r3 若认为需正式 ADR 修订再开 follow-up。

## 验证（文档修订清单）

| 文档 | 修订 | 对应 findings |
|---|---|---|
| proposal.md | Why §B 表格 10 维度→三维度；B1/B3/Capabilities/Impact/Risk 三维度措辞；风险① single_flight 修正 | F1, P2-1, F2 |
| design.md | D2/D3「10 维度」→三维度；新增「ADR-0002 边界澄清」节 | F1 |
| verified-dev-execution/spec.md | scope requirement 重写三维度；scenario 2/3 改可测结构断言 | F1, F-B1, F-B3 |
| tasks.md | 加 A1a（checkout）/ B2a（Python install 闸）；A2 改自实现查重；A1/A4 行号修正；B1/B4 三维度 | P1-1, F-B2, F5, F4 |

## 结论

R1 无 P0。3 条 HIGH 全处置（用户裁决消解 ADR 冲突 + A1a 解 checkout 崩溃 + spec 重写解可测性）。中等/低等 findings 已落修订或标记 r2 核实。

**可进 R2**：验证 (a) ADR-0002 边界澄清说服力 (b) spec scenario 可测性 (c) A1a checkout 逻辑完整性 (d) P2-2 / F2 / F-B4 / F-B5 措辞协调。

## 下一步

- **R2**：聚焦 HIGH 复核 + MEDIUM 措辞收口
- **实现**（task A/B）待 R2 锁定设计后启动
- **E1** #55/#56 运维处理（outward，须用户确认后执行）
