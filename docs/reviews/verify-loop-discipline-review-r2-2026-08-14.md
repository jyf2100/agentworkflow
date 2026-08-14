# verify-loop-discipline — R2 评审记录

- **Change**: `openspec/changes/verify-loop-discipline/`
- **轮次**: R2（R1 基础上复核，3 专家视角并行）
- **日期**: 2026-08-14
- **评审者**: R2-A 结构状态机 / R2-B spec+职责纪律 / R2-C 契约+根因（3 个专家 agent）
- **基线**: R1 处置后的 proposal + design + 2 spec deltas + tasks（2026-08-14 R1 落档后）

## 执行摘要

R2 比 R1 更严：聚焦 R1 留下的 P2 措辞收口 + spec 纪律 + 状态机耦合。3 视角共 **12 findings**（6 P1 / 6 P2），**无 P0**。

**关键好消息**：R2-C 确认 ADR-0002 边界澄清**站得住**——论证②「验证 ≠ 干预」有 ADR 原文支持（独立验证闸 = 验交付物，非 scope 规定），无需正式 ADR 修订即可进实现。建议（非阻塞）开 ADR-0002-amendment follow-up 持久化语义。

**两段通过状态**：

- **A 段（结构状态机）r2 通过**：A1-A5 + A1a 物理可达、无新崩溃路径。R2-A-5（A4 改 cur_base 后耦合 verify_prompt/diff）补 A4a 后闭合。
- **B 段（spec+职责）r2 经批 2 重写后通过**：4 个 P1 根源是 scope requirement 混 persona 推理（违基准 spec L91）。批 2 重写——scope requirement 只断言编排器侧可测不变式，verdict 判定逻辑移 `pa-verify.md`。

**R2-B 核心收敛**：基准 spec L91「the structured anchor field is the machine-asserted contract; **persona prose compliance is design guidance, not a scenario THEN**」是纪律源头。base spec 自己已解 R2-B-3（Line-anchored 管 test-traced 锚 / 新 requirement 管 non-test 锚，正交）+ R2-B-4（Bundle-scoped L110/117-125 已立「per-criterion coverage 只 revise path / green path quick-confirm」= OQ1 答案）。

## Findings

### P1（阻塞，批 1/2 已处置）

| ID | 视角 | 陈述 | 处置 | 状态 |
|---|---|---|---|---|
| R2-A-5 | 结构 | A4 改 `cur_base`（L2472 上轮分支→项目默认分支）后，耦合点 verify_prompt L660 文本 + L637 docstring + L2304 `_dump_branch_diff` 未同步 → round2 收自相矛盾指令 | 加 task A4a（三耦合点同步改） | ✅ 批 1 |
| R2-B-1 | spec | scope requirement 正文+scenario 混 persona 推理（三维度综合、绿但缺陷→revise），违基准 L91 | scope requirement 重写：只断言编排器侧可测不变式，persona 推理移正文+标 design guidance | ✅ 批 2 |
| R2-B-2 | spec | scenario 锚「PRD acceptance-criterion id」但 id 机制未定义 | SHALL 2 + scenario 3：slug-derived 稳定 id，编排器分配注入 | ✅ 批 2 |
| R2-B-3 | spec | base Line-anchored Purpose（location 锚失败测试）与新 requirement（锚 PRD id/diff）字面矛盾 | 正文末段 + scenario 5：两类锚点正交，Line-anchored 管 test-traced，本 requirement 管 non-test，不改 base | ✅ 批 2 |
| R2-B-4 | spec | completeness 穷举（每验收点覆盖）vs base green-path quick sanity 冲突 | SHALL 5 + scenario 6：对齐 Bundle-scoped，green path 无逐项核算，revise path 才逐项（OQ1 锁定） | ✅ 批 2 |
| R2-C-F2 | 根因 | proposal §B「verify_prompt 无未跑分支」事实错（L640-646 三分支正确）；gap 在 verdict 规则段 L667-670 + persona verdict 规则 | proposal §B 事实修正 + B3 改判方向（删 verdict 规则段机械映射，非加判据） | ✅ 批 1+2 |

### P2（必改，批 1 已处置）

| ID | 视角 | 陈述 | 处置 | 状态 |
|---|---|---|---|---|
| R2-A-3 | 结构 | A2 自实现查重应引同文件 `_pr_automerge` 先例（勿写松散版） | tasks A2 加 `_pr_automerge`（L184-190）+ `gh()`（L160-168）先例引用 | ✅ 批 1 |
| R2-A-7 | 结构 | A3「worktree 跨轮复用」理由失真（worktree 本就跨轮复用 L2152） | proposal+tasks A3 改「改名跟 slug」（跨 cron 确定性清理 + 与分支名一致），非新增复用 | ✅ 批 1 |
| R2-C-F3 | 根因 | tasks B3「10 维度」残留 | tasks B3 → 三维度（+ R2-C-F2 判方向修正） | ✅ 批 1 |
| R2-C-F4 | 根因 | proposal 行号偏移（L794-795→L795-796, L974→L966-976, L1258→L2472） | proposal Why §A + Impact 行号全扫改 | ✅ 批 1 |
| R2-C-F5 | 根因 | design D2「9 维度」+ tasks OQ1「10 维度」残留 | design D2 option b + tasks OQ1 → 三维度 | ✅ 批 1 |
| R2-B-5 | spec | scenario 2 THEN 尾部混 persona（"verdict produced by persona over composite"） | 并入批 2 scope requirement 重写（整块替换） | ✅ 批 2 |

### FU / 建议（不阻塞）

| ID | 视角 | 陈述 | 处置 |
|---|---|---|---|
| R2-A-6 | 结构 | verified-publication-integrity SHALL 含 worktree 无专属 scenario | 加「Worktree identifier follows the requirement slug」scenario（断言 id=slug 非 timestamp，不断言复用——复用是既有行为，见 R2-A-7） |
| R2-C-建议 | 契约 | ADR-0002 澄清目前只在 design 节，非正式 ADR 修订 | follow-up：开 ADR-0002-amendment 持久化「独立验证闸 = 验交付物」语义（独立 change，不阻塞本期） |

## 处置分批

- **批 1（机械修正，task #94）**：9 处文档修订跨 proposal/design/tasks + verified-publication-integrity worktree scenario。无争议的行号 / 事实 / 残留 / 耦合修正。所有 R2 行号证据经源码核实（run_daily L640-671 verify_prompt 三分支、L660 prompt 文本、L2304 `_dump_branch_diff`、L2472 `cur_base=branch`；dev-agent L160-168 `gh()`、L184-190 `_pr_automerge`、L795-796 branch）。
- **批 2（spec 纪律收敛，task #95）**：verified-dev-execution scope requirement 整块重写（6 scenario 全编排器侧可测）+ design 加 spec/persona 分工说明 + tasks B1/B3 实现方向对齐（B3 删机械映射非加判据；B1 承载移出的判定逻辑 + 消费 PRD 稳定 id）。

## spec 纪律收敛要点（批 2 核心）

R2-B 的 4 P1 根源是 scope requirement 混 persona 推理。收敛后 scope requirement 只断言编排器侧可测不变式（每条 THEN 都是源码 grep / context 构造 / PRD 产物 / prompt 生成 / 结构化字段 / base 对齐可断言）：

1. context 含三锚源（test_rc+log / PRD 验收清单带 id / dev diff），无单源独占
2. verify_prompt / dispatch 无 `test_rc==0→verdict=pass` 硬编码（grep 可测）
3. PRD 验收点带 slug-derived 稳定 id，注入 context
4. 维度按 profile 裁剪（前端含 UX，纯后端不含）
5. 两类锚点正交于 Line-anchored（test-traced 锚失败测试 / non-test 锚 PRD id 或 diff）
6. green path 无逐项核算，对齐 Bundle-scoped（OQ1 锁定绿轻红重）

**移出 spec、落 `pa-verify.md`（task B1）的 persona 判定逻辑**：三维度综合、绿但任一维度阻塞缺陷→revise、三维度全过→pass、默认怀疑但不苛刻基线、feedback 锚点类型选择、绿轻红重审查深度。

## 结论

R2 无 P0。6 P1 + 6 P2 全处置（批 1 机械 + 批 2 spec 纪律）。A 段状态机 r2 通过；B 段经批 2 重写后通过。ADR-0002 澄清站得住（R2-C 确认），无需正式 ADR 修订。

**设计已锁定，可进实现**（task A 结构 / B 职责）。spec 严格守基准 L91 纪律（编排器侧可测不变式 vs persona design guidance），与 base Line-anchored / Bundle-scoped 正交对齐。

## 下一步

- **实现**（task A 结构 / B 职责）—— spec 已锁定，TDD（task C 先红后绿）
- **E1** #55/#56 运维处理（outward，须用户确认后执行）
- **R3**（可选）：实现完成后回归评审，或直接 canary（task D）作经验证
- **follow-up**：ADR-0002-amendment（独立 change，持久化「独立验证闸 = 验交付物」语义）
