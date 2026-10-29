# verification-test-expert-review — agent-verify-injection（提案态二审，2026-10-26）

二审视角：测试学专家，以本体论为指导——对每个测试域实体（oracle、覆盖账、flake、环境奇偶、
判定三态、超时）按其类别应有的测试学属性逐一拷问。一审（本体论）报告：
`verification-ontological-audit.md`（类别/一致性/可构造性）；本报告问的是**这套验证设计作为测试
体系是否成立**。对象仍是提案态（0/19 实施），修复=工件级编辑。

## 一、正面确认（测试学属性健全面）

1. **green/red/invalid 三态是测试学正统**：inconclusive ≠ failure（JUnit ERROR vs FAILURE 同构）。
   invalid 类别没有被误归红——锚违反/基建失败不消耗 revise 预算走 triage，预算语义干净。
2. **双层测试分层正确**：specification tests（验收套件，oracle 在实现之外编写——破解「自己考
   自己」）+ regression suite（项目全量门，防回归）职责正交，无重复无空档。
3. **oracle 跨轮稳定**：critic 一次修订回环收口后才编写（判据来自终版 PRD）；verify revise 轮
   不换判据——同 PRD 同 oracle，跨轮判定可比，红→绿的差分有意义。
4. **anti-gaming 结构成立**：三锚机械取证（判定不可伪造）；可见测试 + 隐藏裁判（pa-verify 语义
   三维度）组合边界了 teaching-to-the-test 的收益面（D3 取舍的测试学依据）。
5. **任务面 TDD 纪律**：先红测试、2.0 冒烟 fixture 锚定解析器（一审 B3）、报告完备性断言
   三态×两轨——验证器自身也在被测。

## 二、Findings

### T1（B）反向覆盖缺口——orphan criterion 随绿门放行

traceability 只锁了 **test→criterion** 单向（每测试标 criterion id）；「**每个 criterion ≥1
test**」缺失。orphan criterion（无测试覆盖的验收点）= 未验证的需求随绿门放行，覆盖账不闭合——
这是验收测试体系最经典的失效模式（测了容易测的，漏了该测的）。**修复**：delta spec
`acceptance-test-injection` 加场景「Every criterion is covered」（机械断言 criteria_ids ⊇
全部验收点稳定 id，缺者显式记 uncovered 不静默）；tasks 1.3（persona 契约）+ 1.2（断言面）。

### T2（B）破损 oracle 无出口——born-broken suite 永判红

验收套件在实现存在**之前**编写（critic 出口、dispatch 前，D3 的 TDD 语义），故**首次执行就在
门上**。若 persona 产出语法/夹具破损的测试：dev 禁改（digest 锚③）、revise 禁改（判据不可变）
→ oracle 自身的缺陷被永久误分类为「代码红」，烧完 2 轮 redo 预算 → triage 且**无救济通道**。
oracle 缺陷与被测物缺陷是两个类别，现设计只有后者的通道。**修复**：①编写时自检——harness
支持收集级校验的（pytest `--collect-only` 类）先跑一遍防低级破损（不依赖实现存在）；②triage
理由 `acceptance_suite_defective` → 触发重编写（新 digest = 工件新版本，journal 记版本链）→
重跑 verify；spec 加场景（oracle defect ≠ code red，不烧 revise 预算）。

### T3（B）实际组装环境无对账——green 的环境语义悬空

agent 自适应装环境 ⇒ 绿只在「agent 实际组装出的那个环境」成立，未必是声明环境（装错 node 版本
测试碰巧也绿）。声明 env spec 是真源、实际组装可从 transcript 的 Bash 命令**机械提取**——奇偶
比对天然符合锚模式，却缺位。**修复**：verify-report 必含「declared env vs transcript-observed
env」奇偶节（机械提取组装事实）；v1 记录不阻断（奇偶分歧升门留 v2）。

### T4（C）flake 政策缺失 + 超时映射未注明

①pa-test-author 编写契约无确定性纪律（禁 sleep/墙钟/网络依赖/随机种子不钉）——LLM authored
测试是 flake 高发源；②agent 会话墙钟超时的判定映射未写明：应映射 **invalid**（对齐现机械轨
`_run_capture` 超时抛 RuntimeError 的非红语义，run_daily.py:1806），不是红。**修复**：1.3
persona 契约加确定性纪律；D4 加「会话超时 → invalid/triage」一句。

### T5（C）harness 声明面与行锚解析器不对齐 + post-merge 面未声明

①profile harness 任意声明，但行锚解析首支持面是 jest/pytest——go test/cargo 的红反馈降级
`unresolved`；v1 应把可声明 harness 收窄到解析器支持集（其余显式接受降级）；②single-flight
auto-merge 的 post-merge verify 面（`_post_merge_test_cmd`）是否走 agent 轨未声明——v1 不动
需明说，避免同能力双面的本体歧义。**修复**：design Non-Goals 补两句。

## 三、裁决记录

用户裁决（2026-10-26，AskUserQuestion）：**全部修复**（T1-T3 B 级 + T4-T5 C 级全部落进工件）。

已落：
- **T1** → `acceptance-test-injection` ADDED requirement 补覆盖闭合文义（criteria_ids ⊇ 全部验收点 id）+ 场景「Every criterion is covered or explicitly uncovered」；tasks 1.2 断言面 / 1.3 全覆盖契约 / design D2
- **T2** → 同 requirement 补场景「Suite is self-checked at authoring time」（收集级自检，破损扣留 fail-open）+「Defective oracle is re-authored, not blamed on code」（triage `acceptance_suite_defective` → 新 digest supersedes 版本链 → 重跑 verify，不烧 revise 预算）；tasks 1.1 版本链 / 1.4 自检 / 2.6 通道 / design D2 + Risks
- **T3** → `verified-dev-execution` 报告场景扩环境奇偶节（declared env spec vs transcript 观测实际组装，机械提取，v1 记录不阻断）；design D5④ + tasks 2.2⑤ / 2.3
- **T4** → `verified-dev-execution` 补场景「Session wall-clock timeout is invalid, not red」+ requirement 文义；design D4 超时映射（对齐 `_run_capture` RuntimeError 语义）；tasks 1.3 确定性纪律 / 2.3 超时断言 / 2.6
- **T5** → design Non-Goals 补 harness 声明面收窄（jest/pytest，其余显式降级 unresolved）+ post-merge 面不走 agent 轨（纯机械保持）；tasks 1.5 收窄声明

修复后 `openspec validate agent-verify-injection` 复验通过。
