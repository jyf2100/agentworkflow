# fix-event-outlet-verify-parity 本体论审核（立项后、实施前）

- 日期：2026-10-28
- 对象：change 四工件（proposal/design/specs/tasks）声明本体 vs 代码现实
- 方法：domain-modeling 纪律——四工件每条事实断言逐一对代码取证（invalid 分流全路径 `run_daily.py:3355-3441`、acceptance_tests 全部落账点、record_absence 五调用点、verify_stream 判定分支、hub panel/lifecycle/events、graph_pa 零独立 emit）
- 结论：**动机与方向成立（F1/F2 断层真实），但初审工件有 3 处声明-现实断层 + 1 处枚举失真，当日已全部修复入工件**

## 发现与修复

### F1（critical）invalid 事件挂点错位 → 修为双产出点
初审称「L3441 emit 点扩三态映射 green→pass/red→revise」——现实：invalid 分流（`run_daily.py:3388-3403`）在语义审**之前** break，L3441 emit 的值来自 pa-verify 语义审（pass/revise 是其本性），invalid 永远到不了该点；「三态映射」数据流不存在。归因：把 verify 的**两个判定源**（独立验证机械三态 / 语义审二值）误当一个源的两个编码。修复：invalid 于分流点新 emit（+reason）；语义审轨 byte 级回归锚；design D1 / spec / tasks 1.2 全改双产出点模型。

### F2（critical）reason 枚举三类漏二 + 「引用既有常量」失真
代码无此枚举（成因编码在判定分支与 note 字符串，需**新建**非引用）；实际成因**五类**（初审漏 suite_load_failed、exam_snapshot_mismatch——漏则那两条路径违反「invalid 必附 reason」SHALL）。产出点分散三处（verify_stream 判定 / independent_verify 墙钟+load / dispatch 考卷分支）。修复：五值枚举 + 单一定义点常量 + 三产出点归一（design D1、风险 3、tasks 1.1）。

### F3（critical）defective 是范畴混淆（判定混进状态枚举）
journal 事件面只有 stored/absent；「defective」是 triage **判定**（对已入库套件），自动面只有 candidate 预检，确认与重编写是人工面——初审的 defective 事件挂在不存在的人工动作上。修复：status 收敛 {stored, absent}；缺陷可见性走两条既有事实（invalid reason 套件类成因 + 换版 stored 事件 supersedes 链）——spec 换版 scenario、hub 通知、tasks 1.3 同步。

### F4（medium）absent reason 枚举三错两漏
初审 author_failed/collect_failed 不存在；实际值域 = record_absence 五调用点（no_criteria/no_harness/self_check_failed/no_files/store_failed）。修复：spec/tasks 对齐实际值域。

## 正面确认（免复查）
graph 轨零独立 emit（挂点入共享函数即双轨继承）；`render_run_detail` 结构在（D4 挂点可行）；verify-report 落盘在 `independent_verify` 共享函数（D3 一处改双轨成立）；旧 hub `!= "pass"` 判读把 invalid 归警示类的兼容论证经代码复核成立。

## 修复后状态
四工件当日修复完毕，`openspec validate` 过。本记录与 `docs/reviews/2026-10-28-runtime-artifacts-ontology-audit.md`（立项动机）构成该 change 的完整评审链。
