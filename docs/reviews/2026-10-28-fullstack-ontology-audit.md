# 全盘本体论审核（fullstack）——声明本体 × 代码现实 × 运行现实三层对照

日期：2026-10-28 ｜ 方法：5 域并行独立审核（subagent，只读），每域做「spec 声明本体 → 代码实现 → 真机实证」三层对照，按五类错位归类（范畴错位 / 身份断裂 / 不变式违例 / 幽灵实体 / 死实体）。本文件为汇总索引，逐域全文见文末出处说明（五份 subagent 报告原文存于本会话转录；前一份单域审核见 [2026-10-28-runtime-artifacts-ontology-audit.md](2026-10-28-runtime-artifacts-ontology-audit.md)）。

## 全景统计

| 域 | findings | 最重级 | 一句话诊断 |
|---|---|---|---|
| ① hub 观测面（lifecycle/notify/panel/events） | 13 | 3 HIGH | hub 终态 ≠ pa 业务终态，这条分层从未被写下 |
| ② persona 声明面（10 agents × 调用链） | 9 | 1 HIGH | 计数/身份/值域三处漂移，progress 判定理由不可审计 |
| ③ 编排器内核（journal/recovery/reconcile/state 词表） | 10 | 4 high | 骨架单点质量高，断裂集中在状态词表消费侧 |
| ④ 跨 spec 身份链（标识符互洽 + 术语表） | 15 | 2 HIGH | 身份链主干最健康；裂缝集中在 2026-10 hub 面三条 |
| ⑤ 验收工件链（acceptance/verify/e2e） | 15 | 2 高 + 1 中高 | 机械判定核最健康；e2e 新域与既有不变式相撞 |
| 合计 | **62** | **12 条最高级** | — |

运行现实锚（当日真机）：cc-web-control #77/#78/#79/#80/#81（m8-m11），MM 通知「run #89 → succeeded, exit 0」× dispatch record fail 并存；#79 dev 50-turn off_track 止损。

## 跨域互证主题（按修复优先排序）

### T1｜终态语义范畴错位（① × ③ × 当日实证）——已立案方向
- hub `lifecycle.py:369` 的 `succeeded = rc == 0` 是**容器宇宙**的终态；dispatch record 的 `status=fail` 是**业务宇宙**的终态；两者零映射、全 12 个 spec 无一处声明分层（③的 F7 独立发现同一断层）。
- 当日实证：MM 收「succeeded, exit 0」而业务失败——不是 bug，是声明真空的必然呈现。
- 修复方向已在计划中：`run_dir/pipeline_outcome.json` 终态摘要契约（run_daily 机械写、hub 读它报业务终态、缺失回退 exit code）——归入待立的观测面 change（#133 合一）。

### T2｜e2e 新域 × 既有不变式相撞（⑤ F1/F2）——**对 run 92 有直接预测力**
- **F2 playwright.config 双身份矛盾**：dev 侧（#79 教训 + commit 2c553db）宣布 config 是「环境对接面可修正」，verify 侧同一文件是「考卷本体」——编排器把 store 原件机械覆盖进 verify worktree（`run_daily.py:3978-3981`），覆盖 dev 分支上已修正的 config → verify 永远重跑 test-author 的错误端口猜测 → dev revise 修不了（再改再被覆盖）→ 死循环，唯一出口人工 triage。
- **F1 playwright 副产物 × 锚②**：`test-results/`（失败/flaky 截图）是 verify worktree 内非注入 untracked 文件 → 锚②「工作区净」判脏 → `derive_verdict` 中 invalid 优先于 red → e2e「真红」与「flaky 绿」机械不可达。
- **预测**：run 92（m11，PA_VERIFY_AGENT=1）若 dev 过闸走到 verify agent 轨，大概率红/绿皆不可判（invalid）。这本身就是 3.3 release gate 要坐实的——坐实即审核 F1/F2 的真机确认。修法方向：config 升格为「可换版对接工件」（supersedes 链的正当用武之地）或 `outputDir`/`test-results` 进锚②显式白名单。

### T3｜恢复叙事空心化（⑤ F3/F4 × ③ F4 × 当日实证）
- **supersedes 版本链零生产者**：四处 spec 声明换版链，唯一 `store_suite` 调用不传 supersedes，无重编写工具——整条「oracle 缺陷→重编写→不烧 revise 预算→同版本重跑」恢复叙事无机械载体。**当日实证：m11 events.jsonl `supersedes: null`**（审核发出后 5 分钟新 run 再次坐实恒 null）。
- **journal 事件 `acceptance_tests` 幽灵 × `acceptance_bound` 无声明**：spec 声明前者，emit 面完整但生产调用 `sj=None` 永不发射；实际落账的是无任何 spec 声明的后者——两份 active spec 对同一事实互相矛盾。
- **merge/revert reconcile 死实体**（③ F4）：声明中的恢复路径无生产者。
- 同族根因：声明了「应该有的恢复能力」，代码只落了「检测出问题」的前半段，「从问题恢复」的后半段是空头支票。

### T4｜状态词表漂移（③ F1-F3 × ④ × ②）
- journal 迭代身份 seq0/seqN 分裂 → 归约器在真实 journal 上到不了终态，shadow parity 门只对测试 fixture 成立（③ F1，最重）。
- 四张终态词表互相漂移 8 项；同一「merged」记录新读者判成功、legacy 读者判 STATE_CORRUPT（③ F2/F3）。
- 语义事件去重键 spec 写 `(stamp, seq)` 而代码用 `(run_id, seq)`，前者跨项目同日互撞（④ M3）。
- 事件枚举被两个 active change 越界（`metrics_proposed`/`repo_fact_proposed` 不在 pa-event-outlet 白名单）、critic 第四态 `needs_metrics` 无 delta（④ M4）。
- persona 计数 10/9/8 三处漂移 + `pa-verify-agent` 幽灵半身份（② F1）。

### T5｜写面越界与寻址断裂（④ F1/F2）
- **MM 入站文件名越出 execution-hub 钉死的写面正则**（`MM_NAME_RE` vs spec MUST `^\d{8}(m\d*)?_…$`），add-mm-channel-intake 落码但未 delta execution-hub → 主 spec MUST 已被违。修法：归档前补 delta（change 还 active，来得及）。
- **面板源 run 的 events/verify-report 工件寻址链物理不通**（pa 侧 pinyin-slug 规范化后 stem 对 hub 不可知），缺口只在代码注释承认，无 spec 承载 carve-out。

## 各域摘要

### ① hub 观测面（13：3 HIGH / 6 MED / 4 LOW）
HIGH：T1 终态范畴错位；run:container 基数 1:N 被声明写死 1:1（重跑=新 run，spec 文本已失真）；容器 stdout 零保留条款（`--rm` 后 log 即失——#133 的 spec 级根源）。MED 集中在 events.jsonl 尾部展示语义、notify 文本截断、panel 只读面边界。核心结论：**hub 对「跑完」和「干成」的区分从未被写下**。

### ② persona 声明面（9：1 HIGH / 4 MED / 4 LOW）
HIGH：pa-progress 判定理由不落任何工件（on_track/off_track 只有结论无证据，worktree 随容器销毁即失——#133 又一实证）。MED：pa-verify-agent 幽灵半身份（persona 面声明了它、`--agent` 名录查无此名——它实为 verify agent 轨的模型角色非 headless persona）；qwen 模型值违反 CLAUDE.md 声明值域；fetch-forge-repo 的 token 隔离叙事与实际工具面有出入。LOW：计数漂移、prompt 冗余。

### ③ 编排器内核（10：4 high / 4 med / 2 low）
high：T4 全部四条（归约到不了终态 / 词表漂移 / merged 双读者两范畴 / merge-revert reconcile 死实体）。骨架层（三态查询、确定性 ID、append-only journal、single-flight）单点实现质量高且测试密。med：exit 0 与 record fail 并存无声明（T1 互证）、终态出口漏 emit、`STATE_CORRUPT` 八项词表误报面。

### ④ 跨 spec 身份链（15：2 HIGH / 5 MED / 7 LOW / 1 INFO）
身份链总表结论：stamp/digest/criterion id/branch/project name 主干双向互指、机械编号单源生成——**规约面最强部分**。digest 问答：acceptance 与 handoff 确为同一 `sha256:<hex>` 机制，PRD 投递族另用裸 hex + `-<sha8>` 后缀（同一原语两个格式域，无总述无事故）。CONTEXT.md 三处术语滞后/越界（harness 双值、频道仅失败类、verify-drop 半句）。CLAUDE.md 计数/版本声明**今日全部核实准确**，但 add-acceptance-spec-confirmation 落地日将同时击穿 persona 计数（10→11）与 critic 三态表述——无同步挂靠清单。

### ⑤ 验收工件链（15：2 高 / 1 中高 / 5 中 / 7 低）
实体清单确认表：20 个实体中 15 个 ✓ 声明-实现-测试三面对齐（三锚/verdict 三态/invalid 六成因/digest fail-closed/考卷快照/graph 对称），**全链最健康**。✗ 两个（supersedes、journal 事件）即 T3；半一个（defective oracle 重编写）。MED：report 阶段消费方未读三态（invalid 计入红面，vault 日报退化回二值）；两轨统一 verify-report 未落地机械轨（tasks 勾选失真）；verify-report run 身份空壳（project/iteration_id 传空串）；双注入 SHALL 无 flag 条款（默认 off 时恒半边）。#79 事实核对：行为与声明一致（单侧 port 校验是声明的已知缺口非违例）；dev 修正通道 scenario 即 #79 事后修复，但该修复本身引发 F2。

## 结构性观察

1. **错位分布不均**：单点机械件（digest、stamp、journal append、三态查询、verdict 判定）质量高且有测试钉死；断裂集中在**消费侧**（谁读这些状态、按什么词表读、读到后映射到哪个宇宙）和**接缝处**（e2e × 既有不变式、hub × pa 终态、面板 × pa slug）。
2. **未归档 change 是漂移窗口**：三个已实施 change（agent-verify-injection / add-e2e / verify-loop-discipline）的主 spec sync 滞后，本体只活在 delta；④的 HIGH#1 又新增一处代码先行越界。收敛归档（#134）本身就是在关闭最大漂移源。
3. **恢复路径系统性空心**：检测能力（triage 候选、invalid 判定、off_track 止损）已落地，恢复能力（supersedes 重编写、merge revert reconcile、redirect 后续）普遍是声明空头支票——三者同族，值得立一个「恢复叙事落地或撤声明」的专项裁决。

## 建议行动排序

1. **run 92 终态判定即 T2 真机检验**（进行中）——若如预测 invalid/死循环，T2 从「审核推断」升级「真机实证」，修复（config 换版工件 / 副产物白名单）进 agent-verify-injection 归档前的最后一修。
2. **T1 观测面 change**（#133 合一，用户已裁先收尾后立案）：pipeline_outcome.json 契约 + 容器 stdout 落盘 + 通知业务终态；可吸收同族（events 枚举越界、PA_* 透传声明化、build 排除程序化）。
3. **T5-1 零成本修**：add-mm-channel-intake 归档前补 execution-hub delta（change 还 active，一次 Edit 的事）。
4. **T3/T4 归档前文本修正**：supersedes/journal 事件要么补最小生产者要么撤声明；词表漂移在 langgraph cutover 前收敛（否则 parity 门持续只对 fixture 成立）。
5. 低优先：CONTEXT.md 三处术语、CLAUDE.md will-break 清单挂靠、digest 双格式总述。

——五份原始报告全文存本会话转录（taskId: hub 观测面 / persona / 编排器 / ad532d77c4ec6bf88 身份链 / acc70291dc9c19bf9 验收链）；本汇总为唯一入仓件。
