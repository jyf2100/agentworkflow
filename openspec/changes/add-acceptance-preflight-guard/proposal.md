## Why

run #76 家族事故（PRD 验收点写 `jsdom 断言 computed style`，目标仓根本没有任何 DOM 测试库）在 dev 侧烧掉 60 turns off_track 后才被 progress checkpoint 拦下。B 环（add-rsi-repo-facts-loop）从 PRD 文本层补了事实面，但验收套件这条**独立通路**仍无机械防线：pa-test-author 只有 prompt 级纪律「只 import 仓内已有依赖面」——模型可以违反且无人检查；第一层结构自检（`_structural_self_check`）只查文件形态/注解闭合，不查 import 面；而 acceptance-test-injection spec 里承诺的收集级自检（"the pytest `--collect-only` class ... runs before injection"）**从未实现**（代码中仅存 2 处 docstring 注释，属悬空 scenario）。

veriloop 深研确认这是同类系统的通用教训：考卷自己先要能跑，"a check that cannot run is a defect of the draft"。本 change 把防线从「事后 verify 报告里认 ERROR collecting 签名」（最贵时机）前移到「注入前纯静态」（零成本）与「dev 启动前 worktree 收集」（一次子进程），并顺手补上 pa-verify 反馈闭环缺失的「不动什么」围栏（veriloop repair_plan 六要素中的 non_goals——pa 现有四要素反馈只说做什么，不说哪些不许动，revise 轮 dev 容易把上轮已绿的验收面顺手重构坏）。

## What Changes

- **P0 静态 import 白名单（第一层扩展，零环境依赖）**：`_structural_self_check` 新增 import 面检查——pytest 套件只许标准库（`sys.stdlib_module_names` 机械判）+ 相对导入；jest 套件只许相对路径（`./`、`../`）+ jest 全局面（不许任何裸包名 import）；playwright 套件 import 面 ⊆ `{@playwright/test}`。违规 → 套件扣留，缺席事件 reason 细分 `import_violation`（fail-open 同现有面，不拦 dispatch）。jsdom 类第一发生点即被拦截。
- **P1 第二层收集级自检落地（补悬空 scenario 的实现，v1 仅 pytest）**：dispatch 段 worktree 建好、套件注入后、dev 启动前，编排器在 worktree 内跑 `python -m pytest <注入文件> --collect-only`（收集范围圈定注入考卷，不全仓）。收集失败且失败签名锚定注入文件 → 撤除注入文件 + 记 dispatch 期扣留（`collect_failed`），dev 照常启动（fail-open 回退 PRD 验收面）；失败锚定外部（conftest/环境缺失/超时）→ `collect_indeterminate`，不扣套件（verify 侧既有 post-hoc 签名兜底）。jest / playwright v1 豁免第二层：jest 文件发现不解析模块；两者在裸 worktree（无 node_modules）中收集命令必然环境性失败——harness 环境准备是前置缺口（open question，待编排器具备依赖安装能力后扩展）。
- **Spec 悬空措辞对齐**：acceptance-test-injection 的 "Suite is self-checked at authoring time" scenario 从「作者期单点收集验证」改为「两层」语义——第一层（注入前，控制面纯静态：结构 + import 白名单）+ 第二层（worktree 内收集级，dev 启动前）。消除 spec 承诺与实现的漂移。
- **pa-verify 反馈第五要素 non_goals**：feedback_section 由四要素（定位/原因/怎么改/收尾门）扩为五要素，新增「**不动什么**」——列出本轮 revise 中 dev 不得触碰的面（典型：上轮已绿的验收点对应实现、公共 API 形状、已注入验收测试文件）。仅改 `.claude/agents/pa-verify.md`（verified-dev-execution spec 明确 feedback 契约归 persona 文件所有，无 spec delta）。

## Capabilities

### New Capabilities

（无——四块改动全部挂靠既有能力或 persona 契约）

### Modified Capabilities

- `acceptance-test-injection`: "Suite is self-checked at authoring time" scenario 的要求从悬空的单点收集验证改为两层防线——第一层注入前静态自检（既有结构检查 + 新增 import 白名单），第二层 worktree 收集级验证（dev 启动前，harness 支持时）；失败扣留语义统一到既有缺席面，jest 豁免第二层。

## Impact

- **代码**：`Projects/项目推进流水线/scripts/run_daily.py`（`_structural_self_check` 扩展 import 白名单；dispatch 段新增收集级验证步骤与撤除逻辑；`record_absence` reason 枚举扩 `import_violation` / `collect_failed`）；`scripts/acceptance_tests.py`（若 reason 常量收敛于此则同步）。
- **persona**：`.claude/agents/pa-verify.md`（feedback_section 四 → 五要素）；`.claude/agents/pa-test-author.md`（纪律措辞与新白名单对齐——prompt 里的「收集级自检(T2)」承诺现在真实存在）。
- **spec**：`openspec/specs/acceptance-test-injection/spec.md`（上述 scenario delta；archive 时 sync）。
- **测试**：`scripts/test_*` 新增 import 白名单正反例（jsdom 伪装用例必须被拦）、collect 失败扣留路径的单测。
- **与 B 环的关系**：互补不重叠——B 环（add-rsi-repo-facts-loop）治 PRD 文本层（事后、语义）；P0/P1 治验收套件层（首次发生点、机械）。B 环 D7 声明 A→B→RSI 顺序，本 change 独立于该序列可先行（纯编排器侧，无 MM 交互面）。
- **成本**：P0 零成本（纯 Python 静态）；P1 每 PRD 一次收集子进程（秒级）；non_goals 零成本（prompt 措辞）。
