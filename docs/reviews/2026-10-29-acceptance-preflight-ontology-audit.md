# add-acceptance-preflight-guard 本体论审核（实现后回审，2026-10-29）

对象：c2a7b8f（P0/P1 代码+测试）+ a7426fb（persona 措辞）两 commit 的实现面，对照 change 四工件声明的本体。
方法：先提取声明本体（概念实体/关系/不变式），再逐条核实现现实；发现按 severity 排序。
前置验证：V1-V5 现场实证（`python3 -c` 定向跑，见各 finding 引注）。

## 一、声明本体提取（从 spec delta + design）

| 概念实体 | 声明定义 | 声明位置 |
|---|---|---|
| 考卷（suite） | 外部编写、content-addressed、PRD 级常驻工件 | 主 spec Requirement 本体 |
| import 白名单 | 作者期无仓事实下的绝对保守集（D2 表） | design D2 |
| 作者期扣留（withheld） | store 前拦，absent + 违规 specifier detail 记账，fail-open | delta scenario 3 |
| 收集自检（collect check） | dispatch 期 worktree 内 pytest --collect-only 圈定注入文件 | delta scenario 4 |
| 消费期扣留 | store/supersedes 不动，仅本次 dispatch 不注入，幂等重试 | design D3 |
| 三态 | pass / fail（锚定注入文件）/ indeterminate（锚定外部，环境 UNKNOWN ≠ 套件坏） | design D3 |
| non_goals | verify 反馈第五要素：revise 轮不得触碰的面，强制字段 | design D4 |

不变式：①坏考卷第一发生点拦截（作者期或 dev 前，不等 verify/progress）；②环境 UNKNOWN 永不当套件有罪判定；③注入前双层自检全过才可导向 dev loop；④fail-open（扣考卷 ≠ 拦 dispatch）。

## 二、Findings（severity-ranked）

### F1 [MEDIUM·已修] config 豁免域未入声明
- **机制**：实现把 `playwright.config.ts` 从 import 白名单检查域排除（config 分支 `continue` 在白名单检查前，`run_daily.py:1044`）——行为正确（config 必然 `import 'path'` 等 Node 内置裸名，不豁免则 playwright 套件全灭），但 delta scenario 声明的白名单覆盖「authored suite」全集、config 属 authored files——**声明检查域 ⊋ 实现检查域**。
- **修复**：delta scenario 3 WHEN 句补豁免声明（已改）。

### F2 [MEDIUM·已修] 「injection」一词双层实体共用
- **机制**：delta scenario 4 声明「dispatch has injected … causes the orchestrator to remove the injected files」的时序图 = 注入持续存在、fail 才撤。实现是 **probe-injection**（临时写入仅为 collect，完即无条件清）+ **dev-agent authoritative injection**（唯一正式注入源，pass 时重新写）——两个概念实体共用「injection」一词，行为结果等价（fail 时 dev 无注入启动）但本体歧义：未来按 spec 字面实现「保留到 fail 才撤」会与 dev-agent 注入产生双写竞争。
- **修复**：scenario 4 WHEN 句显式声明 probe / authoritative 二分（已改）。

### F3 [MEDIUM·已修] 「真判活」能力边界声明不完整
- **机制**：`_collect_check` 用控制面 `sys.executable`——design D2 承诺「相对导入按文件系统解析到仓源码，能真实判活」对**注入文件自身直接 import 面**成立（相对导入 → worktree 内真解析）；但**仓源码自身的传递依赖**（仓模块顶层 import 的第三方）不在控制面 python 环境 → traceback 锚仓源码文件（非注入文件）→ indeterminate。判活能力比 D2 行文的暗示窄一层。方向安全（fail-open + verify 兜底），纯声明缺口。
- **修复**：design Risks 补「P1 判活能力的真实边界」条目（已改）。

### F4 [LOW·立案留观] 未知 harness 静默放行
- **机制**：`_import_whitelist_violation` 对非 pytest/jest/playwright 的 harness 值两分支都不进 → 返回 None 放行（V3 实证：`vitest` → None）。上游 `_author_acceptance_suite`（run_daily.py:1125）已拦截非法 harness（absent no_harness），现实不可达——但与「默认怀疑」的 fail-safe 精神相悖，缺防御纵深。既有 `_structural_self_check` 对未知 harness 同样只查形态（一致既有模式），单独收紧此函数收益低。
- **处置**：立案不改（改行为需连带核既有调用面；上游拦截已兜底）。若未来 harness 面扩集（如 vitest），此处是必须先改的点——design Open Questions jest/playwright 二层同款前提。

### F5 [LOW·已入声明] 非声明形态漏检
- **机制**：`await import('x')` dynamic import 不被提取（V2b 实证 `[]`）——D1 只声明三形态（import/from/require 行首语句形状），漏检方向 fail-open（P1 collect / verify post-hoc 兜底），可接受但 Risks 未列。
- **修复**：design Risks 补漏检条目（已改）。

### F6 [INFO·忽略] probe 清现场空目录残留
P1 `finally` unlink 只清文件不清 `mkdir` 建的父目录——空目录 git 不跟踪、dev-agent 再注入重用，零影响。忽略。

## 三、确认一致的关键链（正面清单）

- **harness 键数据源链**：`_author_acceptance_suite` → `store_suite(harness=)`（suite_ref 形态含 harness，acceptance_tests.py:65）→ 登记文件 → `_acceptance_ref_for` → P1 `_suite_snap["harness"]` 分发依据真实存在（非空串静默豁免）。
- **`__future__` 放行**（V1）：考卷写 `from __future__ import annotations` 不误扣（`__future__` ∈ sys.stdlib_module_names）。
- **TS `import type` 匹配**（V2）：正则字符类覆盖， specifier 正确提取。
- **双链 traceback 锚定**（V5）：`During handling…` 第二链为真炸点时 frames[-1] 取到注入文件 → fail（方向正确）；反向（考卷因、conftest 现场）锚 conftest → indeterminate（保守不扣）。
- **reducer 未知事件幂等**：acceptance_collect 不在 _EVENT_STATUS_MAP → status 不变无迁移错误（测试锁 test_collect_check.py::test_acceptance_collect_event_tolerated_by_reducer）。
- **消费期扣留不碰 store**：fail 仅置本地 `_suite_snap=None`，store/supersedes 零写；登记文件幂等面支撑下次 dispatch 重试 collect（D3 契约全对齐）。
- **fail-open 不变式**：P0 作者期扣留走既有 absent 面（不拦 dispatch）；P1 fail 扣留 dev 照常启动（无 acceptance_injection）；P1 接线异常 → indeterminate 不扣。三层失败全部 fail-open ✓。
- **措辞三面同步**：persona（pa-verify.md / pa-test-author.md）与编排器 prompt（verify_prompt / _test_author_prompt）五要素/白名单措辞一致，test_verify_loop.py 有 prompt 锁。

## 四、结论

行为层零缺陷（三态分发、fail-open、幂等、digest 链全部与声明一致）；发现集中在**规约工件的声明精度**（F1-F3 均为 spec/design 补丁级，已当场修复），F4 立案留观。change 可进入 archive 流程（delta 已同步本轮补丁）。
