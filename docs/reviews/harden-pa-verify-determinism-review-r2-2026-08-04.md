# harden-pa-verify-determinism — R2 Review (Incremental)

Date: 2026-08-04
Range: R1 response 修订后（proposal/design/specs/tasks + R1 Resolution 节 + persona green-path 段）
Verdict: **Approve — 准入 apply / commit**

## Frozen Acceptance Matrix

与 R1 一致，本轮不变（§矩阵在评审轮次内保持不变）：Must pass = spec delta 协调 / scenario 可测 / design 决策有契约引用 / tasks 覆盖 scenario / 与 ADR-0001/0002/0005 一致；Deferred = 实现代码/真实测试/阈值调参/reflection 触发/persona 黄金套件。

## 评审执行

R2 = 增量复审（REVIEW-GUIDELINES §5）。2 独立对抗视角（§6 专家委派，主评审裁决）：

- **R2-A**：方向 A 四处一致性 + 8 个 scenario THEN 全结构断言 + spec 自洽 + response 新回归
- **R2-B**：新 flag 三处对齐（含核对 `feature_flags.py` 现状验"new"属实）+ R1 全部 17 条逐项 closed 复验（防假装闭环）+ D5/D6/D2 论证质量 + 契约回归

## R1 Findings 增量复验（逐项）

| R1 ID | R2 裁决 | 证据 |
|---|---|---|
| **P1-1** 方向 A 拆 green/revise | **closed** | spec Requirement B（4 scenario：both-paths/revise-accounting/green-quick-sanity/small）+ design D3 + tasks 3.3/3.4/3.5 + persona green-path 段 + proposal「Green-path review shape unchanged」——**五处贯彻一致**（R2-A 核） |
| **P1-2** ADR-0005 论证 | **closed** | design D6 诚实论证：ADR-0005 文本管 report、pa-verify 跑 dispatch，`verify-commit-loop-design.md` §4 援引是借援；真锚 = 机械-语义切分原则。R2-B 核 ADR-0005 原文确认管 report 段，D6 措辞精准。tasks 6.3 回写 SPEC §4.5（3→4） |
| **P1-3** new flag 三处 | **closed** | proposal Impact / tasks 4.0 / design Migration 三处一致（"new flag in LoopFlags pattern + FLAGS_ENV_MAP", precedent `cross_prd_learning_*`）。R2-B 核 `feature_flags.py` 现有 8 flag 无 verify 域 → "new"属实，无"existing switch"残留 |
| **P2-1** THEN 全结构断言 | **closed**（B3 R2 顺手修后 8/8） | 8 scenario THEN 落 orchestrator 结构断言；persona prose（cites/does-not-fabricate）显式降 design guidance。R2-A 发现 B3 混层（见下 F-R2-1），已顺手修 |
| **P2-2** spec 自洽 | **closed** | specs 顶 Scope note：pa-verify 输出契约仍 persona md 拥有，本 delta 锚 location + bundle 触发；base Purpose 拓宽留 sync 阶段（显式 defer，非漏） |
| **P2-3** shadow-parity → flag-gated | **closed**（R2 顺手加 N≥3） | design Migration 论证 shadow-parity 对输入形状变更不适用（shadow-off 平凡通过 / shadow-on 反卡死 cutover），换 flag-gated + manual verdict audit；tasks 4.2 manifest 字段齐 + 拒斥自报布尔。R2 顺手加 N≥3（见 F-R2-2） |
| **P2-4** D5 重写 | **closed** | 撤回"conflict with test gate"伪因果（显式标 withdrawn as non-sequitur）；换 comment 降噪 vs 结构化施工反馈论证。R2-B 确认真消解 |
| **P2-5** red fixture | **closed** | tasks 5.1 拆 (a) red-producing fixture（`assert.ok(false)`/`assert False`）+ (b) 解析器单测；标注现有 fixture green-only |
| **P2-6** round≥2 base-side | **closed** | spec A4 scenario（`base-side regression at <file:line>` 区分 `unresolved`）+ design D2 细化二态 + tasks 1.2/5.4 |
| **P2-7** task 2.1 措辞 | **closed** | "Embed alongside the existing markdown location text"，backward-compatible |
| **FU-1** README SHA | **closed** | design References 双 commit SHA（open-code-review `0f3c920`、skill-up `2953782`）+ URL + 摘录 |
| **FU-2** runner 边界 | **closed** | spec 显式 first-supported = jest + pytest，其余归 D2 fail-open |
| **FU-3** OQ-Q2 | **closed** | tasks 2.2「resolves OQ-Q2 to revise-always」 |
| **FU-4** mapper wiring | **closed** | tasks 1.1「wire dispatcher to select mapper by test_cmd type」 |
| **FU-5** 多失败测试 | **closed** | tasks 1.1「anchor all, configurable cap」 |
| **FU-6** fail-safe 引用 | **closed** | design D2 引 fail-safe-dispatch UNKNOWN 同源 |
| **FU-7** bundle rogue 方向 | **deferred** | 留实现层（per-bundle 判断），与冻结矩阵 Deferred 一致，非假装闭环 |

**汇总：16 closed + 1 deferred。无 open / partial / 假装闭环。**

## 用户指定三重点验证

1. **方向 A 四处一致** — ✅ 通过。R2-A 核 spec Requirement B / design D3 / tasks 3.3-3.5 / persona green-path 段，**且 proposal「What Changes」第五处也对齐**（"act on the revise path only"）。无遗漏/矛盾。
2. **新 flag 三处对齐** — ✅ 通过。R2-B 核 proposal Impact / tasks 4.0 / design Migration 三处一致，**且对照 `feature_flags.py` 现状验"new"属性属实**（现有 8 flag：journal_shadow / journal_driven_dispatch / session_aware_retry / lifecycle_hooks / container_sandbox / telemetry_export / cross_prd_learning_shadow / cross_prd_learning_injection —— 无一覆盖 verify 域）。
3. **scenario THEN 全落结构断言** — ✅ 通过（B3 顺手修后）。R2-A 发现 B3 是唯一未遵循 A1/A2/B2 统一降级模式的 scenario，已本轮顺手修。

## R2 新发现（均 Follow-up，已顺手闭环）

- **F-R2-1**（R2-A，= R2-B obs-B）：B3「Green-path quick sanity」THEN 前半段是 persona prose（"does quick sanity / returns pass"），未显式标 design guidance。**已修**：B3 重写为纯 orchestrator 结构动作（"feeds bundles without per-criterion mapping / no criteria_coverage field on green path"）+ persona 措辞移括号标 design guidance。
- **F-R2-2**（R2-A，= R2-B obs-A）：tasks 4.2「N real dispatch records」无下限，与"self-attested boolean 不算 evidence"有张力（N=1 蒙混风险）。**已修**：加「N≥3, tuned empirically」（与 bundle threshold 同措辞）。

两条均按 §5「新发现只有直接违反冻结契约才升 blocker」判 Follow-up，但因成本极低（各一行）本轮顺手闭环，不留尾巴。

## Validation After R2 Response

```text
OpenSpec strict validation: passed（`openspec validate` → valid；R2 顺手修后重验）
Artifacts: 4/4 complete
Implementation tests: not run; this range changes specification documents only
CI status: not available（change 未提交，untracked）
契约回归扫描：base spec 4 requirement 未被改写（纯 ADDED）；ADR-0001/0002/0005 合规；test gate 未碰；scenario × tasks 覆盖 8/8 无漏洞
```

## Verdict

**Approve。** R1 的 3 P1 + 7 P2 全 closed、6/7 FU 顺手闭环（FU-7 合理 deferred）；R2 两视角无阻断、无假装闭环、无契约回归；方向 A 与新 flag 的用户三重点全部通过。R2 顺手闭环 F-R2-1/F-R2-2 后，change **可准入 apply / commit**。

后续 apply 阶段的 implementation evidence（真实 red fixture、N≥3 audit、新 flag wiring）仍须按 tasks 产出——本 R2 只 closes spec-level 验收，不代替实现证据。
