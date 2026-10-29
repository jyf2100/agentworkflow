## 1. P0 静态 import 白名单（第一层扩展）

- [x] 1.1 run_daily.py 新增 `_extract_imports_py(text)`（AST 提取 Import/ImportFrom）与 `_extract_imports_js(text)`（行首锚定正则提取 import/from/require specifier），返回 (specifier, kind) 列表；语法 parse 失败归第一层既有失败面
- [x] 1.2 `_structural_self_check` 接入白名单判定：pytest ∈ `sys.stdlib_module_names` 或相对导入（level>0）；jest 相对/绝对路径或 {jest, @jest/globals}；playwright ⊆ {@playwright/test} ∪ 相对/绝对路径；违规返回带首个违规 specifier 的失败原因
- [x] 1.3 缺席记录带 detail：`record_absence(self_check_failed)` 路径上把违规 import 名并入 journal 事件的 reason detail（确认 acceptance_tests.py / run_daily.py 中 reason 的落点后选最小改动面）
- [x] 1.4 单测 `scripts/test_structural_self_check.py`（或既有文件追加）：正例（stdlib/相对/jest 面/@playwright/test）、反例（jsdom 伪装 require('jsdom')、import requests、require('lodash')、import '@vue/test-utils'）、字符串内 require 不误判

## 2. P1 第二层收集级自检（dispatch 期，pytest-only）

- [x] 2.1 run_daily.py dispatch 段在套件注入后、dev 启动前插入 `_collect_check(worktree, injected_files)`：`[python, -m, pytest, <files…>, --collect-only, -q]`，cwd=worktree，timeout=120s
- [x] 2.2 三态判定：exit 0 → pass；exit≠0 且 traceback 路径锚定注入文件 → fail（撤除注入文件 + journal `acceptance_collect{verdict:fail}`）；锚定外部（conftest/pytest 缺失/超时）→ indeterminate（journal 记录，不扣）
- [x] 2.3 失败路径撤除注入文件并让 dev 无 acceptance_injection 启动（fail-open）；jest/playwright harness 直接跳过（记 skipped）
- [x] 2.4 单测：mock subprocess 的三态各一例（pass / 锚定注入文件 fail / conftest 缺失 indeterminate），及撤除后 dev 命令行不含 acceptance 注入参数
- [x] 2.5 journal 消费面核查：确认 `acceptance_collect` 新事件类型不破坏 recovery_cli / reconcile / report 的未知事件容忍（读 journal.py 事件分发表，必要时补容忍测试）

## 3. pa-verify 反馈第五要素 non_goals

- [x] 3.1 `.claude/agents/pa-verify.md` feedback_section 模板四 → 五要素：新增「⑤ 不动什么（non_goals）」——本轮 revise 中 dev 不得触碰的面（上轮已绿验收点对应实现、公共 API 形状、已注入验收测试文件），无则显式写「无」（强制字段）
- [x] 3.2 核查编排器侧 feedback_section 消费点（run_daily.py 拼接「⚠️ 审核反馈」节处）确认零解析零改动；JSON 契约不变

## 4. test-author prompt 对齐

- [x] 4.1 `.claude/agents/pa-test-author.md` 纪律措辞收紧为白名单事实（pytest stdlib+相对；jest 相对+jest 面；playwright @playwright/test+相对），声明「编排器机械强制，违规即扣」；「收集级自检(T2)」承诺措辞对齐两层现实（第一层注入前静态、第二层 dev 前收集）

## 5. 质量门与收尾

- [ ] 5.1 `cd Projects/项目推进流水线 && bash scripts/quality.sh` 全绿（compileall + pytest + ruff）
- [ ] 5.2 对照 delta 自查：openspec/changes/add-acceptance-preflight-guard/specs/ 的两个新 scenario（import 白名单扣留、dispatch 期收集三态扣留）各有对应测试路径
- [ ] 5.3 git 线性提交（P0/P1/1-2 commit + persona 措辞 1 commit），commit message 引 change 名
