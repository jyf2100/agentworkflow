# Tasks — fix-event-outlet-verify-parity

## 1. pa 产出端：事件契约扩容（legacy/graph 双轨对称）

- [x] 1.1 invalid reason 枚举新建 + 产出点归一：模块级常量六值（`unresolved` / `anchor_violation` / `wallclock_timeout` / `suite_load_failed` / `worktree_setup_failed` / `exam_snapshot_mismatch`——代码现状无常量，成因编码在判定分支与 note 字符串；worktree 值为实施取证补齐的第六成因）；`verify_stream` 判定扩 `derive_invalid_reason` + verify-report 加结构化 `invalid_reason` 字段（unresolved/anchor_violation 在判定分支内分流），编排层分支归一赋值（agent 轨 catch VerifySessionTimeout → wallclock_timeout、load_suite 失败 → suite_load_failed、建 worktree 失败 → worktree_setup_failed、机械轨超时 → wallclock_timeout、dispatch 考卷分支 → exam_snapshot_mismatch）——先红测试钉死六值与分流成因一一对应（防漂移，design 风险 3）
- [x] 1.2 verify_verdict 双产出点落地：**invalid 分流点新 emit**（`run_daily.py:3388` break 前，verdict="invalid" + reason）；**语义审轨 L3441 emit 零变化**（pass/revise、无 reason 字段，byte 级回归锚）；invalid 分流点在 dispatch 共享函数内 → graph 轨自动继承，测试确认双轨同点
- [x] 1.3 acceptance_suite 事件投射（status 二值 {stored, absent}）：编排器 gate 出口统一面 `_author_acceptance_for_gate` 加 `EO.append_event`——stored 附 digest/criteria_ids/supersedes（首版 null）、absent 附 reason/detail（与编排器落账 result 同源零翻译，七值含 refused/author_failed）；stage_critic 与 graph 聚合 node 共消费 → 双轨自动继承。**defective 不设第三状态**（triage 判定非工件状态，确认是人工面无自动挂点——缺陷可见性走 invalid reason + 换版 stored 事件 supersedes 链，spec D2/design D2）
- [x] 1.4 测试（先红）：invalid **六成因**各产事件行（含考卷不变式破坏、套件 load 失败、worktree 建立失败三条易漏路径，reason 值 ∈ 枚举）；语义审轨事件行 byte 级不变（非 invalid 不携带 reason）；acceptance stored（含 supersedes=null 首版与换版非 null 两形态）/ absent（七 reason 值域对齐编排器落账 result）各产事件行
- [x] 1.5 verify-report 双写：`independent_verify` 内 build_verify_report 落盘后，`PA_REPORT_DIR` 设置则 `shutil.copy2` 同名拷入（fail-open try/except stderr）；vault 缺省零变化
- [x] 1.6 测试（先红）：双写生效（env 设置 → 两处文件存在）+ 缺省零变化 + 拷贝失败 fail-open（注入 OSError → 流水线照常 + stderr 告警）

## 2. hub 消费端：判读 + 面板

- [x] 2.1 `hub/events.py` `KNOWN_EVENTS` 加 `acceptance_suite`（未知行跳过逻辑零改动）
- [x] 2.2 `hub/lifecycle.py` 判读扩容：`_event_broadcast`——acceptance absent 进频道、stored 仅私信（verify invalid 经既有 `!= "pass"` 已覆盖，加测试钉死防回归）；`_event_text`——invalid 行拼 reason、acceptance 行拼 status + reason/digest/supersedes（机械拼接不变式不动）
- [x] 2.3 hub 测试（先红）：invalid 转发含 reason 且进频道；absent 进频道；stored 仅私信（supersedes 非 null 时文本附换版链）；旧版行为回归（pass 仅私信 / revise 进频道不变）
- [x] 2.4 `hub/panel.py` run 详情扩容（`render_run_detail` 现有签名扩参）：events.jsonl 尾部行原样展示区（与 journal 尾部行同模式）+ verify-report.json 机械表格渲染（判定含 reason / 逐命令 cmd·source·exit / 锚检查 rc / 环境奇偶原样字段；缺失占位「无 verify-report」非错误）
- [x] 2.5 面板测试（先红）：verify-report 落卷时渲染关键节、缺失时占位；events 尾部行含 acceptance_suite/invalid 原样可见；零语义渲染铁律回归（无 hub 侧结论性文本）

## 3. 门禁与文档

- [x] 3.1 `RUNBOOK.md` verify 小节补 invalid 排障 playbook：reason 六分流（unresolved→查命令匹配面 / anchor_violation→查锚 rc / wallclock_timeout→查环境奇偶节与 `VERIFY_*_TIMEOUT` / suite_load_failed→查 acceptance store 完整性 / worktree_setup_failed→查 git worktree/磁盘/并发残留 / exam_snapshot_mismatch→查控制面双注入链路非目标仓），每条链到 verify-report 对应节 + 面板 run 详情入口
- [x] 3.2 全量质量门：`cd Projects/项目推进流水线 && bash scripts/quality.sh` 绿 + `cd hub && bash quality.sh` 绿 + `openspec validate fix-event-outlet-verify-parity` 过
- [x] 3.3 双仓时序验证：pa 先行旧 hub 兼容（acceptance_suite 行被旧 `KNOWN_EVENTS` 跳过不炸——用 hub 改动前代码面跑一次带新事件行的 read_events 断言跳过告警）
