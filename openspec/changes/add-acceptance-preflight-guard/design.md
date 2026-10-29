## Context

`_structural_self_check`（run_daily.py）是验收套件的第一层注入前自检：文件形态（jest `*.test.*` / pytest `test_*.py` / playwright `e2e_*.spec.ts`）、playwright config 检查、`page.waitForTimeout` 禁令、criterion 注解闭合——但**不看 import 面**。pa-test-author 的 prompt 纪律「只 import 仓内已有依赖面，不引新 devDependency」只是文字，违反无检查。spec 的收集级自检 scenario 悬空（`run_daily.py:972` 注释自认「留注入面在 worktree 内跑(第二层)」，从未实现）。

run #76 的失败链：PRD 验收写 jsdom → test-author 写出 `require('jsdom')` 考卷 → 注入 → dev 跑 60 turns off_track。本 change 在链上补两个首次发生点拦截 + verify 反馈围栏。

时序（改后）：

```
test-author 返回 suite
      │
      ▼
┌─ 第一层（作者期，控制面，纯静态，零环境依赖）──────────┐
│ 结构自检（既有）+ import 白名单（P0 新增）             │
│ 违规 → 不入库：record_absence(self_check_failed,      │
│         detail=import_violation)，fail-open 继续      │
└──────────────┬──────────────────────────────────────┘
               ▼ store（content-addressed）+ journal
dispatch：worktree 建好 → 注入考卷
               │
               ▼
┌─ 第二层（dispatch 期，worktree 内，一次子进程）────────┐
│ pytest：python -m pytest <注入文件> --collect-only     │
│  ├─ exit 0                         → pass，dev 启动   │
│  ├─ 失败锚定注入文件（ImportError 等）→ 撤注入文件，   │
│  │   journal acceptance_collect(fail) → dev 照常启动   │
│  └─ 失败锚定外部（conftest/环境/超时）→ indeterminate  │
│      不扣套件（verify 侧 post-hoc 签名既有兜底）        │
│ jest/playwright v1 豁免（裸 worktree 无 node 工具链）  │
└──────────────┬──────────────────────────────────────┘
               ▼ dev-agent 启动（acceptance_injection 或无）
```

## Goals / Non-Goals

**Goals:**

- #76 家族（考卷 import 仓内不存在的第三方包）在**首次发生点**被机械拦截：作者期（P0）或 dev 启动前（P1），不再等 verify 或 progress checkpoint。
- spec 的收集级自检 scenario 从悬空变为真实（或明确豁免），消除 spec↔实现漂移。
- pa-verify 反馈带「不动什么」围栏，revise 轮保护上轮已绿的面。

**Non-Goals:**

- 不做 harness 环境准备（不为 jest/playwright worktree 装 node_modules）——那是编排器依赖安装能力的前置缺口，v1 明确豁免并挂 follow-up。
- 不改 PRD 文本层的事实面（B 环 add-rsi-repo-facts-loop 的领地）；不引入仓依赖清单驱动的动态白名单（待 B 环 repo_facts 落地后衔接，见 Open Questions）。
- 不动 store/supersedes/digest 机制；不动 verify 的三态 verdict 与 post-hoc 签名识别（它们继续作为最后兜底）。
- 不给 non_goals 加编排器解析（feedback_section 仍是编排器原样拼接的字符串，JSON 契约零变化）。

## Decisions

### D1：P0 白名单的提取机制——pytest 用 AST，jest/playwright 用受约束正则

- **pytest**：`ast.parse` 提取 `Import` / `ImportFrom` 节点。备选正则被拒：注释与字符串里的 import 必然误报；AST 是 stdlib 自带，零新依赖、零误报。语法错（parse 失败）本身即 self_check_failed，顺带覆盖。
- **jest / playwright**：控制面无 TS/JS 解析器依赖（不引），用行首锚定的语句形状正则提取 `import … from 'X'` / `import 'X'` / `require('X')` 的 specifier。误报风险与缓解见 Risks。

### D2：白名单规则——「作者期无仓事实」下的绝对保守集

| harness | 放行 | 拒绝（→ import_violation 扣留） |
|---|---|---|
| pytest | `sys.stdlib_module_names` 内的模块名；相对导入（`ImportFrom.level > 0`） | 其余一切裸名（第三方 **和** 仓绝对包名，如 `import myrepo`） |
| jest | 相对/绝对路径（`./` `../` `/` 开头）；`jest`、`@jest/globals` | 其余裸包名 |
| playwright | `@playwright/test`；相对/绝对路径 | 其余裸包名 |

关键取舍：作者期（store 前）**没有仓的事实**——无法机械区分「仓里真有 requests」和「jsdom 式幻觉」，唯一安全判据是绝对保守集。仓绝对包名同拒看似激进，但与两层分工自洽：

- **裸第三方名**：作者期拒（无事实），worktree 期也无法验证（控制面环境无目标仓依赖）→ 拒是对的。
- **相对导入**：作者期放行（形态合法），worktree 期**真验证**——P1 在 worktree 里 collect 时，相对导入按文件系统路径解析到仓源码（就在 worktree），不依赖 site-packages，能真实判活。
- 即：**P0 管「无事实时保守」，P1 管「有事实（仓源码）时验证」**，两层正好互补闭环。

考卷断言能力被收紧为黑盒面（子进程跑 CLI / 检查文件产物 / stdlib 断言）而非「import 被测函数」的单测面——这与验收考卷的定位（外部驱动的验收，不是单测）相容，属有意保守。B 环 repo_facts 落地后可把仓依赖清单并入白名单放开（follow-up）。

### D3：P1 失败三态——锚定归属判定，不惩罚环境

`acceptance_collect` journal 事件带 verdict `pass | fail | indeterminate`：

- **pass**：collect exit 0。
- **fail**（扣套件）：exit ≠ 0 且失败签名（ImportError / ModuleNotFoundError / collection error 的 traceback 文件路径）**锚定注入的考卷文件** → 撤除注入文件、dev 照常启动（fail-open 回退 PRD 验收面）。
- **indeterminate**（不扣）：exit ≠ 0 但失败锚定外部——worktree conftest 缺失、pytest 本身未装、命令超时（120s）。环境 UNKNOWN ≠ 套件坏（三态远程查询的同一精神）；此路径由 verify 侧既有 post-hoc `ERROR collecting` 签名识别兜底。

扣留是**消费期**语义：store 里的套件不动、supersedes 链不动，仅本次 dispatch 不注入；环境修复后下次 dispatch 幂等重试（再 collect 一次）。

备选（被拒）：收集失败回写 store 缺席——混淆了作者期缺席与消费期扣留，且会把可恢复的环境问题固化成套件版本链噪声。

### D4：non_goals 形态——persona prompt 五要素，编排器零改动

pa-verify.md feedback_section 模板：① 定位 ② 原因 ③ 怎么改 ④ 收尾门 → 新增 **⑤ 不动什么（non_goals）**：列出本轮 revise 中 dev 不得触碰的面（典型：上轮已绿验收点对应实现、公共 API 形状、已注入验收测试文件）；无则显式写「无」（强制字段防空缺）。`feedback_section` 仍是字符串，编排器原样拼入 PRD「⚠️ 审核反馈」节——round 2 时上轮 non_goals 已在 PRD 上下文，围栏自然延续。verified-dev-execution spec line 5 已声明 feedback 契约归 persona 文件 → 无 spec delta。

### D5：test-author prompt 措辞对齐

「只 import 仓内已有依赖面，不引新 devDependency」→ 收紧为白名单事实：「只 import 标准库/相对路径（pytest）、相对路径/jest 面（jest）、@playwright/test+相对路径（playwright）——编排器机械强制，违规即扣」；「编排器会在注入前跑收集级自检(T2)」→ 对齐两层现实（第一层注入前静态、第二层 dev 前收集）。

## Risks / Trade-offs

- [jest/playwright 正则把字符串里的 `require('x')` 误判为 import] → 正则行首锚定语句形状（缩进后的 `import`/`const x = require`），字符串字面量内不匹配；即便漏进，扣留代价是 dev 回退 PRD 验收面（fail-open），非死锁。
- [P0 拒绝仓绝对包名，短期压缩考卷写法] → v1 有意保守（黑盒验收面仍完整）；B 环 repo_facts 提供仓依赖清单后放开（衔接点：test-author prompt 注入 facts + 白名单读 facts）。
- [P1 对 conftest 重的仓，收集范围失控/超时] → collect 目标显式圈定注入文件路径（不全仓）；超时归 indeterminate 不扣套件。
- [控制面环境缺 pytest] → pa 自身 dev 依赖已含 pytest（quality.sh 依赖它），真身机 cron 装了 `.[dev]`；缺则 indeterminate，不误扣。
- [non_goals 写成空话（dev 无视）] → 与 ①—④ 同一反馈节拼接、同轮被 dev 读取；无机械强制，属 prompt 层纪律（与 veriloop repair_plan 同层级，其亦为 schema 约束而非运行时强制）；verify 下轮若发现围栏被破，定位元素天然点出（回滚即收尾门）。

## Migration Plan

纯增量，无 flag、无数据迁移：P0 并入既有第一层调用点（失败面复用 `record_absence`）；P1 是 dispatch 段新步骤 + 新 journal 事件类型（journal 消费者向后兼容：未知事件类型已容忍）。回滚 = revert 单 commit。non_goals 为 prompt 措辞，随 persona 文件生效/回滚。

## Open Questions

- jest/playwright 第二层的前提：编排器需具备「worktree 内 harness 环境准备」（如 `npm ci` / `pip install -e .`）——成本、时长与失败三态如何定，待真机需求出现后另立 change。
- B 环 repo_facts 落地后，P0 白名单是否从「绝对保守集」升级为「仓事实白名单」（facts 依赖清单 ∪ stdlib）——衔接点已留（D2/D5），随 B 环实现定。
