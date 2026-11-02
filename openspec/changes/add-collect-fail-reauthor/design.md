## Context

归档的 add-acceptance-preflight-guard 已落地双层自检。dispatch 段（`run_daily.py:3676`）：pytest 套件 probe 注入 worktree → `_collect_check` 三态 → fail（套件锚定）→ `_suite_snap = None`。快照是 iteration 级单一消费源（agent-verify-injection 1.9）：dev 注入（L3746/3756）与 verify 注入（L3864 `suite_ref=_suite_snap`）全用它——**扣留已天然沿迭代传播**（审核 F1a 无需补）。

缺口（审核 F1b）：

```
collect fail（套件锚定，确定性：同 digest 同仓状态必复现）
  ├─ 已实现：扣留沿迭代传播（dev + verify 双无考卷）
  ├─ 已实现：journal acceptance_collect(fail) + 考卷不残留
  └─ 缺口：无修复动作
       ├─ post-hoc 签名路径已被传播切断（verify 不跑考卷 → L3902
       │   acceptance_suite_defective_candidate 永不产生）
       └─ 既有重写路径 = triage 人工动作（L4351 docstring）
       ⇒ 坏考卷每 iteration 白扣一次，PRD 永远无考卷，直至人工读 journal
```

## Goals / Non-Goals

**Goals:**

- collect-fail（套件锚定）自动触发一次带证据的 superseding re-author，确定性坏考卷在 1 个 iteration 内被替换，下轮生效。
- 熔断封顶：同 PRD 第二次套件锚定失败 → 记 exhausted + known issue，流水线继续（无循环、无阻塞）。
- 归档 change 的三条审核 rationale（家族分解 / 白名单概念化 / 归因哲学）落档防失传。

**Non-Goals:**

- 不动三态判定、白名单规则、probe 注入机制（归档 change 领地，工作正常）。
- 不做独立 feedback 工件（F4 follow-up）、harness 环境准备（F6 open question 继承）。
- 不给 indeterminate 配重写（环境 UNKNOWN ≠ 套件坏；环境恢复后下轮 collect 幂等重试即可）。
- 不改 supersedes 消费面 / iteration 快照不变式（重写只对下轮生效，本 iteration 已扣留 = None，语义自洽）。

## Decisions

### D1：证据前移——collect-fail ≈ acceptance_suite_defective 的机械自动确认

两条证据通道的强度对比：

| 通道 | 证据形态 | 确认面 |
|---|---|---|
| post-hoc 签名（L3902） | verify 跑考卷炸出 `ERROR collecting` | 只产 **candidate**，triage 人工确认 |
| collect-fail（本 change） | traceback 机械锚定注入文件，确定性可重放 | **自动确认**（限次），等价人工 triage 已确认 |

机械锚定证据强于 LLM 会话产物 → 自动路径合法。人工 triage 面保留为兜底（熔断后、非 collect 可见的缺陷形态仍走人工）。重写产物走既有 `store_suite(supersedes=...)` 版本链，journal 记 `acceptance_reauthor`（携带旧/新 digest 与证据摘要）。

### D2：重写接线——复用 author 轨，扩一个 evidence 参数

`_test_author_prompt`（L952）与 `_author_acceptance`（L1154）既有轨道复用；prompt 追加 evidence 块：失败文件名、collect 错误签名（截断）、「上一版因 dispatch 期 collect 失败被扣留，请规避同类结构性错误」。重写仍过第一层静态自检 + 覆盖闭合（既有 `store_suite` 面前人人平等，重写不豁免任何闸）。

### D3：限次 1 + 熔断——journal 推数，防确定性死循环

重写版再 fail 是大概率事件（同因）且确定性 → 不限次就是 author×∞ 循环。限次实现：dispatch 段从 journal 数该 PRD 的 `acceptance_reauthor` 事件 ≥1 时跳过重写；记 `acceptance_reauthor_exhausted`（journal）+ report 段 known issue 落点（PRD 以无考卷模式跑完，report 显式可见「该 PRD 验收面退化为 PRD 文本判读」）。最坏成本封顶：2 次 authoring 调用 + 2 次 collect。

### D4：双轨共享——接线点在 per-PRD 函数内，graph 轨零镜像

改动全部落在 run_daily.py 的 per-PRD dispatch 函数；langgraph 轨（graph_pa 聚合 node）直调该函数（已验证：graph_pa_*.py 无 collect 镜像代码，归档 change 实现即单点）→ 自动继承。tasks 含验证项（grep 双轨无第二接线点 + quality.sh）。

### Rationale 落档（审核 F2 / F3 / F5，归档 change design 的补强）

**F2 失败家族分解（层不对称的正当性）**：本防线家族的主诉求——幻觉第三方依赖（`require('jsdom')`）——是**词面可判**的，第一层即拦且三 harness 全覆盖（jsdom 本就是 jest 场景）；层不对称只影响**仓结构错配家族**（fixture 缺失/conftest 交互），该家族需实例-环境关系，只有第二层能判，而第二层 v1 仅 pytest 可跑。故主诉求家族全 harness 闭环；「jest 也要第二层」的优先级由仓结构家族实际发生率决定，不由对称性审美决定。

**F3 白名单统一概念化**：三 harness 白名单的辩护基础异质（stdlib=语言事实；jest 面=运行时注入事实；`@playwright/test`=约定），共同本质是**「无需仓事实即可解析的 import 面」**。这同时解释 moduleNameMapper 别名（`@/utils`，由仓配置赋予意义）为何落入仓事实依赖拒绝族——与仓绝对包名同族，词面无真假，须待仓事实或 worktree 实例裁决。新增 harness 按此本质切白名单。

**F5 indeterminate 归因哲学**：「跑不起来的考卷是草稿的缺陷」针对草稿的**内在可运行性**（对象属性）；indeterminate 是**验证者环境的外在不可用**（关系属性）。故障归因需要锚定证据——三态教义（UNKNOWN ≠ 失败）在考卷域的实例化。

## Risks / Trade-offs

- [重写版同因再 fail] → D3 熔断封顶（最坏 2 authoring + 2 collect），exhausted 后 known issue 可见。
- [journal 线性推数的成本] → journal 事件量小（PRD 级过滤），且仅 collect-fail 分支才数；不做索引。
- [重写触发在 dispatch 关键路径上，author 慢会拖 dispatch] → authoring 是无外副作用的 LLM 调用（秒级），且只在 fail 分支；超时/异常 → 按未重写处理（记 journal，下轮 collect 再撞再试），fail-open。
- [自动确认与人工 triage 双通道混淆审计] → `acceptance_reauthor` 事件显式带 `confirmed_by: "collect_probe"`；人工 triage 路径措辞不变，审计可区分。

## Migration Plan

纯增量：新 journal 事件类型（消费者未知事件已容忍）；fail 分支新增动作；`_test_author_prompt` 加可选参数（默认 None = 现有调用零变化）。回滚 = revert 单 commit；回滚后回到「扣留但无重写」现状（不比现在差）。
