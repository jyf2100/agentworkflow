# fix-event-outlet-verify-parity — adversarial 审核 + 全面测试（r2）

日期：2026-10-28｜对象：commit 4bd1846 的完整实施（14/14 tasks）｜方式：三 agent 并行
（hub 消费面 / pa 产出面 / 契约一致性）+ 主线逐条甄别修复 + 双质量门复跑。

## 结论

16 findings：2 高 4 中 10 低——全部处置（15 修 + 1 项越界说明）；跨界端到端
（pa 真 emit → hub 真判读，四形态：invalid/pass/stored/absent）PASS；双门复跑绿
（pa 2422 passed / hub 17+23 passed / openspec validate 过）。

## 高严重度（已修）

1. **graph 轨 invalid 分流零 emit + 考卷 vj 缺 invalid_reason**（graph_pa_nodes._dev_post_op）：
   graph dispatch 段是子图独立实现（非包装直调 dispatch_one），invalid 分流不经过
   run_daily 的 emit 点 → graph 轨 cutover 后 hub 对其 invalid 全盲。修复：invalid 分流
   `_reconcile_terminal()` 后自持 emit（facts 形态与 legacy 逐字段一致）+ 考卷分支 vj 补
   `invalid_reason=REASON_EXAM_SNAPSHOT_MISMATCH`。
2. **测试假保证**：旧 `test_graph_track_has_no_independent_emit_point` 只 inspect 组装层
   `graph_pa_dispatch.py`（本就零业务逻辑）——真分流在 `graph_pa_nodes._dev_post_op`。
   改写为 `test_graph_track_dispatch_emit_parity`：保留组装层零 emit 断言 + `_dev_post_op`
   源五要素真锁（EO.append_event / "verify_verdict" / verdict="invalid" /
   reason=vj.get("invalid_reason") / REASON_EXAM_SNAPSHOT_MISMATCH）。

## 中严重度（已修）

3. **panel exit_code 裸拼未过 _esc**（XSS 链：verify-report 在 state 卷、目标仓敌意代码可写）
   → 改 `_esc(r.get('exit_code'))`。
4. **report→out invalid_reason 传播行零测试**（unresolved/anchor_violation 两成因未贯通）
   → 补 `test_agent_track_report_reason_propagation`（_agent_track_verify 全链直调，参数化
   两成因构造）。
5. **supersedes 换版场景生产不可达**（store_suite 生产调用不传 supersedes 恒 null；scenario
   超诺）→ spec scenario 补 NOTE：字段契约钉死 + 生产写方现状 + 属 triage 重编写接线后形态
   （不动 store_suite——写方接线超本 change 范围）。
6. **graph 考卷 vj 缺 invalid_reason**（同 #1，独立计数）。

## 低严重度（已修）

7. `_verify_report_html` 内层类型零防御 → 整体 try/except 降级原样转义 + isinstance 防御 +
   violations str 化（坏内层结构降级展示非 500）。
8. `test_panel._dispatched_run` PRD 名无下划线锁不住寻址错位 → fixture 改
   `20261020_hand-a1b2c3d4.md`（下划线锁死 `_slugify` 错位回归）。
9. 六成因 → 事件行投射仅 2 字面值有集成 → dispatch 集成参数化 `SIX_REASONS` 全值。
10. `_author_acceptance_for_gate` except 分支吞异常后无事件行（fail-open 吞异常不得吞事件）
    → 补 absent/author_failed emit + 测试。
11. no_files / store_failed producer 层零测试 → test_acceptance_wiring 补两形态。
12. hub verify pass 非广播无显式断言 → test_events 补 `mm.channels == []`。
13. hub-panel spec「absent/defective」defective 残留 → 修措辞（工件状态二值，缺陷可见性经
    invalid reason + supersedes 链承载）。
14. verify-report 落卷场景未限定 agent 轨 → spec scenario 限定（机械轨超时不产 report）。
15. RUNBOOK wallclock 行未区分轨道 → 注明 agent 轨有 verify-report、机械轨超时即结论走
    hub run 日志；proposal「五原因」/design「五分流」计数残留 → 六。

## 越界项（不修，显式记录）

**graph 轨 critic_verdict / pr_opened / 语义审 verify_verdict（pass/revise）事件缺席**：
graph 的 critic/dispatch 段是聚合/子图独立实现，从 add-hub-slice-4b 起即不经过 run_daily
内这些 emit 点（4b 只做了 legacy 轨）。本 change 声明面仅 invalid 分流（新加分流点双轨
emit 是本 change 承诺）+ acceptance_suite（经 `_author_acceptance_for_gate` 共消费双轨真
继承）。语义事件 graph 侧补齐属 4b 遗留债的后续 change；graph cutover 门
（run_full_cutover_suite）若纳入事件面维度会暴露此缺口。

## 验证证据

- pa 门：`bash scripts/quality.sh` → 2422 passed, 1 skipped, 5 xfailed，ruff 绿。
- hub 门：`bash quality.sh` → test_events 17 + test_panel 23 全绿，ruff 绿。
- `openspec validate fix-event-outlet-verify-parity` → valid。
- 跨界端到端（审核期间）：pa 真 `EO.append_event` 四形态 → hub 真 read_events + 判读
  （invalid 进频道含 reason / pass 私信无 reason / stored 私信含 digest / absent 进频道
  含 reason+detail）PASS；stamp 同源经 hub 容器 cmd `--stamp` 注入坐实。
