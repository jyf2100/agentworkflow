# harden-pa-verify-determinism — R1 Review

Date: 2026-08-04
Range: spec-level（proposal/design/specs/tasks 四 artifact 全 done，零代码实现；文件 untracked 未提交）
Verdict before response: **Request Changes**

## Frozen Acceptance Matrix

| Class | Boundary |
|---|---|
| Must pass | ① spec delta 与 `verified-dev-execution` 既有 4 requirement 协调不冲突；② 每 scenario 确定性可测；③ design 决策有契约/证据引用；④ tasks 覆盖所有 scenario 且不声明未实现能力；⑤ 与 ADR-0001/0002/0005 一致 |
| Deferred | 实现代码与真实测试证据（本轮只评 spec/docs）；anchor 映射对非 npm/pytest 框架的覆盖；bundle 阈值实证调参值；reflection 最终触发条件；persona 回归黄金套件 |
| Out of scope | open-code-review CLI 本体正确性；目标仓业务代码；pa-radar/pa-prd-critic persona；persona 回归黄金集（独立 meta follow-up） |
| Follow-up | persona 回归评估套件；delegation mode 形态；非标准 test_cmd mapper 扩展；README provenance 加固 |

## 评审执行

按 REVIEW-GUIDELINES §6「专家委派」spawn 3 个独立对抗视角（主评审 = change 作者，有确认偏差，故委派独立视角提候选 findings，主评审裁决）：

- **视角 A**：spec 契约一致性 + scenario 可测性
- **视角 B**：design 决策合理性 + ADR 合规
- **视角 C**：tasks 完整性 + 假绿风险

主评审对三视角候选 findings 去重、合并、定级如下。

## P1 Findings（阻断，response 必须闭环）

### R1-P1-1 — Requirement B 与 green-pass 契约冲突 + coverage scenario 三重不可测

**合并自**：视角 A P1-A + P1-B、视角 B F5、视角 C F2（同根因）。

- **Contract**：delta spec Requirement「Bundle-scoped review coverage for large diffs」触发条件 `When the dev diff exceeds a configured threshold`（**未限定测试颜色/路径**）；scenario「Acceptance-criterion coverage is explicit」（THEN 要求 per-criterion accounting）；既有 persona 契约 `.claude/agents/pa-verify.md`「测试绿（test_rc=0）：verdict=pass。**快速确认** diff 与 PRD 验收标准大致对应」；proposal「What Changes」声称「No philosophy changes」。
- **Artifact behavior**：Requirement B 按 `diff exceeds threshold` 触发 bundling + per-criterion accounting，不限红/绿；但 persona green-path 契约是 quick-pass（不穷尽审查）；design D4 又限定 reflection revise-only。
- **Counterexample/evidence**：构造大 diff（>10 文件）+ 全量测试绿（test_rc=0）→ Requirement B 要 per-bundle per-criterion accounting，persona green-pass 契约要 quick-pass，**同一输入两契约规定不同审查深度**。叠加 coverage scenario 三重不可测：① 输出无落点（`feedback_section` 标注 revise-only，green-pass 时为空，"reports criterion not covered" 无处落）；② persona 认知合规测试明确 deferred（design Non-Goals：golden suite 不在本 change）；③ tasks 5.1–5.5 无一覆盖 coverage-reporting。proposal「No philosophy changes」名不副实——Requirement B 实际改了 green-path 审查深度。
- **Current impact**：直接违反冻结矩阵 Must-pass ②（scenario 可测）+ ①（与既有 requirement 协调）+ ④（tasks 覆盖 scenario）。实现端三条路全违契：（a）只 revise 切 bundle 违字面；（b）green 也切但 persona quick-pass 则 scenario THEN 落空；（c）改 green 做 per-criterion = 未声明的 philosophy change。
- **Minimal fix boundary**：把 bundling 的 per-criterion accounting + coverage-reporting **限定为 revise-path only**（与 D4 reflection revise-only 同构）；green-path 即使大 diff 也只 per-bundle quick sanity、不穷尽 accounting；scenario「coverage is explicit」THEN 从 persona 认知改为 orchestrator 结构契约（"the orchestrator feeds each bundle with its mapped acceptance criteria to pa-verify with isolated per-bundle context"），persona 是否真 cover 降为 design guidance / deferred（待 golden suite）；同步把 proposal「No philosophy changes」改为「green-path 审查形态不变（仍 quick-pass），确定性补强只作用于 revise 反馈」。
- **Severity**：P1。

### R1-P1-2 — design 完全未承接 ADR-0005 合规论证

**来自**：视角 B F1。

- **Contract**：冻结矩阵 Must-pass ⑤（与 ADR-0005 一致）；`verify-commit-loop-design.md` §4 已援引 ADR-0005「日后真要洞察、单独加 pa-insight persona、增量不推翻本决定」口子作为 pa-verify persona 化依据。
- **Artifact behavior**：`design.md` 通篇**零次**提到 ADR-0005 / pa-insight / report-mechanical-not-persona。pa-verify 为什么是 persona 而非机械 stage 的前置论证被整个吞掉。
- **Counterexample/evidence**：`verify-commit-loop-design.md` §4 的论证本身就是"借援"——ADR-0005 文本管的是 **report 段**，而 pa-verify 跑在 **dispatch 子循环**（`run_daily.py` `_pa_verify_round`，SPEC §4.4「pa-dispatch」内）。叠加 `SPEC.md` §4.5 仍写「控制面的 **3 个** 语义 persona」（radar/prd/prd-critic），pa-verify 已是第 4 个，计数陈旧。
- **Current impact**：Must-pass ⑤ 无书面论证；后续实施者/readback 拿不到"为什么这不违 ADR-0005"的依据。
- **Minimal fix boundary**：design 加 ≤1 段——诚实说明 ADR-0005 文本管 report 段，pa-verify 跑 dispatch，故 ADR-0005 不直接管；pa-verify 的 ADR 锚是**机械-语义切分原则**（语义活才立 persona，全流水线通用），属 ADR-0005 自留口子的同类增量而非文本管辖；同步回写 SPEC §4.5 persona 计数（3 → 4，列 pa-verify）。
- **Severity**：P1（契约引用缺失；非假绿/破坏性故不升 P0）。

### R1-P1-3 — 迁移计划"behind an existing feature_flags switch"与事实不符

**来自**：视角 B F2。

- **Contract**：冻结矩阵 Must-pass ③（design 决策有契约/证据引用）；`scripts/feature_flags.py` `LoopFlags` schema + `FLAGS_ENV_MAP` 是稳定对外契约（注释明示"改这些 = 改对外契约"）。
- **Artifact behavior**：design.md「Ship behind an **existing** `feature_flags` switch」+ tasks 4.1「Wire the change behind an **existing** `feature_flags` switch」；proposal Impact **未列** `feature_flags.py`。
- **Counterexample/evidence**：`LoopFlags` 现有 8 个 flag（`journal_shadow / journal_driven_dispatch / session_aware_retry / lifecycle_hooks / container_sandbox / telemetry_export / cross_prd_learning_shadow / cross_prd_learning_injection`），**无一**覆盖 verify-anchor / verify-bundle 域。ship 本变更必须**新增** 1–2 个 flag（precedent：`cross_prd_learning_*` 新增时还伴随 env-var 名设计 + 域切割决策）+ 改 `FLAGS_ENV_MAP` 契约。
- **Current impact**：readback 误以为零机制改动；实际是新增 flag + 改契约文件 + `feature_flags.py` 应进 Impact。tasks 4.1–4.3 继承错误前提。
- **Minimal fix boundary**：design/proposal 改"an existing feature_flags switch" → "a **new** flag in the existing `LoopFlags` pattern (precedent: `cross_prd_learning_*`)"；proposal Impact 增列 `scripts/feature_flags.py` + `FLAGS_ENV_MAP`；tasks 4.x 加一步"add flag + env-var name"。
- **Severity**：P1（事实性错误 + 契约文件漏进 Impact）。

## P2 Findings（建议本轮一起闭环）

### R1-P2-1 — scenario THEN 混层（机械可测 + 语义不可测绑定）

**来自**：视角 A P2-C（泛化）。scenario「Red test maps to a diff hunk」「Anchor cannot be resolved」THEN 同时含机械段（orchestrator derives / records gap，可测）与语义段（"pa-verify's feedback location **cites** those anchors rather than model-recalled"、"explicitly flags... instead of fabricating"，persona prose 合规，不可机器验证）。
**Fix**：所有 scenario THEN 落在可机器验证的结构断言（如"the structured anchor sub-field in feedback_section carries the orchestrator-provided anchors or the unresolved flag"）；persona prose 合规降 design guidance。

### R1-P2-2 — spec 内部不自洽（Purpose 漂移 + 术语未定义）

**合并自**：视角 C F3 + 视角 A P2-D。delta 引用 `feedback_section`/`verdict=revise`/`round`/`pa-verify`，但 base `verified-dev-execution` spec 未定义这些（只在 persona md）；Purpose 仍写「Specify the control-plane standard development executor」，未提 semantic verifier。
**Fix**：Purpose 拓宽一行覆盖 executor + semantic verifier 两半（"...and the semantic verify feedback contract anchored on mechanical line anchors and bundle-scoped coverage"）；或在 delta ADDED 段顶加 Scope note 指明 pa-verify 输出契约仍由 persona md 拥有、本 delta 只锚 location 元素与 bundling 触发。

### R1-P2-3 — shadow-parity/cutover 概念错配 + 4.2 假绿措辞

**合并自**：视角 B F3 + 视角 C F1。`cutover.resolve_dispatch_source` 的 parity 语义是"决策等价"（`status` 不变）；本变更改 pa-verify **输入形状**——shadow 不喂=verdict 不变→parity 平凡通过、cutover gate 无意义；shadow 喂=verdict 会变（变更目的）→parity 要求"不变"反而卡死 cutover。叠加 tasks 4.2「using the X pattern」未约束输入源/样本量/指标/manifest，重演 durable-loop P0 假绿模式（`run_shadow_parity_evidence` 手工 event flow 教训）。
**Fix**：design 把"shadow parity + cutover"换成"flag-gated rollout + 人工抽检 verdict 质量（N 条 dispatch record 比对 revise 反馈可执行性）"，或显式声明本变更不做 shadow parity、只做 flag + rollback；tasks 4.2 钉死输入源/最少记录数/parity manifest 字段（含 digest、verdict drift、anchor-mismatch 计数）+ 显式拒斥自报布尔。

### R1-P2-4 — D5 哲学分叉论证自相矛盾

**来自**：视角 B F4。design D5「lowering pa-verify's willingness to pass would **conflict with** test-evidence gate」不成立——测试门是 executor 机械闸、pa-verify 是其后的语义闸，串行两道、不共享决策，降 Recall 不会让测试门"冲突"。
**Fix**：D5 why 段重写——open-code-review 降 Recall 服务 **comment 降噪**（人面对 comment noise）；pa-verify revise 是**结构化施工反馈**、非 comment 流，故降噪权衡不迁移；测试门兜底使高 Recall 可承受。保留高 Recall 决策不变。

### R1-P2-5 — task 5.1 前提错（现有 fixture 是 green-only）

**来自**：视角 B F6。`conftest.py` 的 `node_target_repo`（`assert.ok(true)`）、`python_target_repo`（`assert True`）恒绿，产不出 red 输出；fixture 设计意图是 cwd 隔离/publication gating，非产生真实红 stack。task 5.1「use the reproducible-pipeline-validation fixtures」测 red 解析无法兑现。
**Fix**：tasks 5.1 拆两步——(a) 加 red-producing fixture（`assert.ok(false)` / `assert False` 各一条）或新建独立 red fixture；(b) 解析器单测。或 design Risk 段承认"npm test/pytest 支持仅对 canned 样本验证、真实 runner 变体走 D2 fail-open"。

### R1-P2-6 — round≥2「红断言行落在 base、不在增量 diff」边界缺失

**来自**：视角 B F7。round 2 diff = `previous_dev_branch..new_dev_branch`（增量）；round 2 红测试很可能断言在 round 1 已存在、round 2 未触的行（改 A 连带断 B）→ `failing file:line` 落 base、不在增量 diff → 无 hunk → D2 `unresolved`，丢失"增量改动在 B 处引发回归"这一最高诊断价值信号。
**Fix**：spec 加 scenario「round ≥ 2 红断言行落在 base（不在增量 diff）→ anchor 标 `base-side regression at <file:line>` 并显式区分于 `unresolved`（unsupported framework）」；或 design D2 把 fail-open 二态细化为 unresolved（解析失败）vs base-side（解析成功但不在增量）。

### R1-P2-7 — task 2.1 措辞与 design Risk 段矛盾

**来自**：视角 B F8。tasks 2.1「Change `feedback_section` element ① **to** a structured sub-field」读作**替换**；design Risk 段「anchors are **embedded** as a structured sub-field; the markdown section dev reads stays backward-compatible」是**并置**。`dev-agent.py` 不解析 feedback_section（grep 证实），只把 PRD 当 markdown 读——照 task 字面替换会破坏可读性 + 违 design 声明。
**Fix**：tasks 2.1 改「**Embed** anchors as a structured sub-field **alongside** the existing markdown location text (markdown stays as dev reads it today; structured anchors ride within the same section)」。

## Follow-up（不阻断）

- **R1-FU-1** README provenance 仅自证，无 URL/commit/摘录（视角 B F9）→ archive 前在 design 附录补 README URL + commit SHA。
- **R1-FU-2** "Node `npm test`" 是 launcher 不是 framework，下游 jest/mocha/node:test/vitest 红格式各异（视角 B F10）→ spec scenario 显式列 first-supported runners（jest + pytest），其余归 D2 fail-open。
- **R1-FU-3** task 2.2 静默关闭 design OQ-Q2，未标注引用（视角 C F4）→ 2.2 加注「(resolves OQ-Q2 to revise-always)」。
- **R1-FU-4** dispatcher 按 test_cmd 类型选 mapper 的 wiring 无显式 task（视角 C F5）→ 1.1 末加一句 wiring 说明。
- **R1-FU-5** 多失败测试的锚点策略未规定（单数 `a failing test`，视角 A FU-E）→ 实现层定（所有失败都锚 / 取首 N）。
- **R1-FU-6** D2 fail-open 无显式契约引用（与 fail-safe-dispatch UNKNOWN 同源，视角 A FU-F）→ design 补一句指向 fail-safe-dispatch。
- **R1-FU-7** bundle 内「diff 文件无对应验收标准」（rogue/多做）方向未覆盖（视角 A FU-G）→ per-bundle 判断，Follow-up。

## Validation

```text
OpenSpec strict validation: passed（`openspec validate harden-pa-verify-determinism` → valid；4/4 artifacts complete）
Implementation tests: not run; this range changes specification documents only
CI status: not available（change 未提交，untracked）
Focused evidence: 3 独立对抗评审视角（A/B/C）+ 上游 README 核对（alibaba/open-code-review main，Apache-2.0，2026-08-04）
证伪留痕：测试门边界守住（4 个既有 scenario 未被改写）；ADR-0001/0002/0006 合规；threshold 边界（exceeds=>，at or below=≤）清晰可测；round≥2 recompute 三处对齐；proposal/design current-state claim 与 run_daily.py:483-506/1170-1228 一致
```

## Resolution (R1 response · 2026-08-04)

P1-1 修复方向经用户拍板 = **A（accounting 限 revise + bundling 两路都切，green 只 quick sanity）**。逐 finding 闭环：

**P1（全闭环）**
- **R1-P1-1**：specs Requirement B 拆——bundling 触发仍 `diff exceeds threshold`（green/revise 都切），per-criterion accounting + `criteria_coverage` 字段限 revise-path；coverage scenario THEN 改为 orchestrator 结构契约（喂 bundle + mapped criteria）；新增「Green-path quick sanity per bundle」scenario；proposal「No philosophy changes」改「Green-path review shape unchanged」；persona green-path 段补「逐 bundle quick sanity，不穷尽 accounting」；tasks 3.x 拆为 3.3（两路 bundling）/3.4（revise accounting）/3.5（green quick sanity）。
- **R1-P1-2**：design 新增 **D6**（pa-verify 的 ADR 锚 = 机械-语义切分原则，非 ADR-0005 report 段文本；诚实说明 `verify-commit-loop-design.md` §4 的 ADR-0005 援引是借援）；tasks 6.3 回写 SPEC §4.5 persona 计数（3→4）。
- **R1-P1-3**：design Migration 改「new flag in existing `LoopFlags` pattern (precedent `cross_prd_learning_*`)」；proposal Impact 增列 `scripts/feature_flags.py` + `FLAGS_ENV_MAP`；tasks 新增 **4.0** add-flag。

**P2（全闭环）**
- **R1-P2-1**：所有 scenario THEN 落结构断言（"structured anchor sub-field carries anchors / unresolved flag"）；persona prose 合规（"cites rather than recalls"）显式降为 design guidance，非 scenario THEN。
- **R1-P2-2**：specs 顶加 Scope note（pa-verify 输出契约仍由 persona md 拥有，本 delta 只锚 location 元素 + bundle 触发；base Purpose 拓宽留 sync 阶段）。
- **R1-P2-3**：design Migration 把 shadow-parity + cutover 换「flag-gated rollout + 人工 verdict 审计」（论证 parity 语义对输入形状变更不适用：shadow-off 平凡通过 / shadow-on 反卡死 cutover）；tasks 4.2 钉死输入源 + 最少记录数 + parity manifest 字段（digest/verdict drift/anchor-mismatch）+ 显式拒斥自报布尔。
- **R1-P2-4**：design **D5 重写**——撤回"conflict with test gate"伪因果，改为「open-code-review 降 Recall 服务 comment 降噪；pa-verify revise 是结构化施工反馈、非 comment 流，故降噪权衡不迁移」。
- **R1-P2-5**：tasks 5.1 拆 (a) 加 red-producing fixture（`assert.ok(false)`/`assert False`）+ (b) 解析器单测。
- **R1-P2-6**：specs 新增 scenario「Round ≥ 2 red assertion lands in the base」→ 标 `base-side regression at <file:line>` 区分 `unresolved`；design D2 细化二态；tasks 1.2 + 5.4 覆盖。
- **R1-P2-7**：tasks 2.1 改「**Embed** anchors **alongside** the existing markdown location text」。

**Follow-up（6 顺手闭环 / 1 留实现层）**
- FU-1 闭环：design References 加 README URL + 双 commit SHA（open-code-review `0f3c920`、skill-up `2953782`，均 2026-08-03）。
- FU-2 闭环：specs 显式列 first-supported runners（jest + pytest），其余归 D2 fail-open。
- FU-3 闭环：tasks 2.2 加注「resolves OQ-Q2 to revise-always」。
- FU-4 闭环：tasks 1.1 加「wire dispatcher to select mapper by test_cmd type」。
- FU-5 闭环：tasks 1.1 注多失败测试策略（全部锚定，可配 cap）。
- FU-6 闭环：design D2 引用 fail-safe-dispatch `UNKNOWN` 同源姿态。
- **FU-7 留 Follow-up**：bundle 内「diff 文件无对应 criterion」（rogue/多做）方向——per-bundle 判断，留实现层。

## Validation After Response

```text
OpenSpec strict validation: passed（`openspec validate` → valid；response 后重验）
Artifacts: 4/4 complete（proposal/design/specs/tasks 均已修订）
Implementation tests: not run; this range changes specification documents only
CI status: not available（change 未提交，untracked）
Focused evidence: 方向 A 贯彻四处一致（spec Requirement B / design D3 / tasks 3.3-3.5 / persona green-path 段）
```

## Verdict (after response)

**所有 P1 + P2 闭环；6/7 Follow-up 顺手闭环（FU-7 留实现层）。**

Response 未引入新架构——全部是 spec/docs 层面修订（scenario 拆分 + 结构化字段 + 论证补全 + flag 契约列名 + 措辞对齐）。`openspec validate` 通过，4/4 artifacts complete。

**可准入 R2 复审或 apply。** 若走 R2，重点复验：
1. P1-1 方向 A 是否真贯彻四处一致（spec Requirement B 的 green/revise scenario + design D3 + tasks 3.3-3.5 + persona green-path 段）——无遗漏；
2. P1-3 新 flag 是否在 proposal Impact + tasks 4.0 + design Migration 三处对齐；
3. scenario THEN 是否全部落在可机器验证的结构断言上（P2-1 无回归）。
