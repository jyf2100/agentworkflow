# verify-loop-discipline — R3 评审记录（实现后回归）

- **Change**: `openspec/changes/verify-loop-discipline/`
- **轮次**: R3（**A 段实现后**回归评审——不同于 R1/R2 的设计前置评审）
- **日期**: 2026-08-14
- **评审者**: R3-A 结构状态机 / R3-B 契约回归 / R3-C spec 对齐（3 个专家 agent）
- **基线**: A 段 A1/A1a/A2/A3/A4/A4a + C1/C2 全绿（quality 1892）后的实现代码

## 执行摘要

R3 是**实现后**评审：验证 A 段（结构：PR 跟需求不跟 stamp）实现是否真正闭合 #55/#56 双根因中的 A 根因，
并找出实现引入的新缺陷。3 视角共 **10 findings**（1 P0 / 2 P1 / 7 P2），**全部已当场处置**。

**R3-C 结论**：A 段完成、可进 B 段（pa-verify 三维度语义审查）；E1（关 #56 保 #55）前提成立——
单 PR 语义闭环，旧 stamp 分支会被 already_dispatched 前置闸拦截，不会开第三个 PR。

## Findings 与处置

### P0（当场修复）

| ID | 视角 | 陈述 | 处置 | 状态 |
|---|---|---|---|---|
| R3-A F1 = R3-B F1 | 结构+契约（同一缺陷） | **graph_pa 路径未同步**：`graph_pa_verify._redo_fn` 仍 `_cur_base=branch`（旧增量重投）、`graph_pa_nodes._dev_build_cmd` 未传 `round_n`（round marker 断）、`_worktree_op` 仍 stamp-slug 路径 → flag on 时 graph 路径退化为旧多 PR 行为 | 三处同步改（删 `_cur_base` / 传 `round_n=state.get("verify_round",1)` / `dev_slugify(slug)`）；test_graph_verify + test_graph_worktree 旧语义断言 intended 翻转；graph 六件套 69 测全绿 | ✅ |

### P1（当场修复）

| ID | 视角 | 陈述 | 处置 | 状态 |
|---|---|---|---|---|
| R3-A F2 | 结构 | **no-progress 检测失灵**：round≥2 dev 零新 commit（publish no_changes exit 0）时 has_commits(main,branch) 仍 True（round1 commits 在）→ 白跑一轮 independent_verify + pa-verify（同一全量 diff 大概率重复 revise）→ 耗尽 rounds 退化 interrupted_pr | dispatch_one branch 检查后加门：round≥2 且 `no_changes` → `status=no_progress` + reconcile + break（TDD：`test_dispatch_round2_no_changes_terminates_no_progress`，iv_calls==1） | ✅ |
| R3-A F3 | 结构 | **persona 指令矛盾**：`.claude/agents/pa-verify.md` L16「round≥2 base 可能是上次 dev 分支」L19「增量重做过」与新 prompt（同分支累积）自相矛盾；docs/verify-commit-loop-design.md L37-38 同 | pa-verify.md 两行最小同步（B1 重写前的过渡补丁）；design 文档加演进注记（历史文档不改写原决策） | ✅ |

### P2（当场处置）

| ID | 视角 | 陈述 | 处置 | 状态 |
|---|---|---|---|---|
| R3-B F2 | 契约 | publish error 路径无 test_passed 透传（on_success/on_no_changes 有，on_error 不对称） | `run_publish_phase` 加 `on_error` 参数（三分支对称）；main 传 `test_passed=last_test=="green"` | ✅ |
| R3-A F5 | 结构 | `--phase checkout/publish` branch/base 无参校验 → 缺省会真建名为 "None" 的分支 | 两 phase 入口 fail-fast exit 10 + 测试（`test_phase_missing_branch_fails_fast`） | ✅ |
| R3-C F2 | spec | tasks.md B 段行号漂移（B2 引 L2648-2653 实为 L2643+；B2a 引 L2658 实为 L2686；B3 引 L640-670 实为 L632+） | tasks.md 重锚（no_progress 门插入后再 +12 行，均实测） | ✅ |
| R3-B F4 | 契约 | A3 去 stamp 后同 slug 并发 dispatch 撞同一 worktree 路径（原 stamp天然隔离） | 非缺陷：并发靠 slot 互斥（DISPATCH_LOCKS + single-flight），但注记落 tasks.md A3 行 + RUNBOOK §10.1 | ✅ 注记 |
| R3-A F7 | 结构 | make_branch/worktree「跨 cron 确定性」注释过宽（PRD 文件名含日期，跨 cron 新 PRD → 新 slug） | 两处注释收窄：收敛只覆盖「同一 PRD 文件的 verify 各轮 + 崩溃恢复重入」；跨 cron 防重靠 already_dispatched | ✅ |
| R3-A F4/F6 | 结构 | 旧 stamp worktree 不会被新 remove-if-exists 自清；show-ref 本地盲区（本地分支误删 origin 在 → checkout -b 与远端分叉）无恢复路径 | RUNBOOK 新增 §10（10.1 一次性清理 + 10.2 show-ref 盲区恢复与判据） | ✅ |
| R3-C F1/F3 + R3-B F5 | spec+契约 | 注释残留（graph_pa_verify 模块 docstring「增量重投」/`_cur_base` 字段注、run_daily 段头、dev-agent `_pr_automerge L184-190` 行号引用）+ mock 分支名不同（auto/r1 vs auto/r2，与 A4 同分支语义不符） | 注释批量清理；mock 改同 branch（test_verify_loop 4 处 + branch 1 处，41 测回归绿） | ✅ |

## 处置后回归

- `test_verify_loop.py` + `test_verify_loop_branch.py`：41 测全绿
- graph 六件套（verify/devloop/dev_post/dispatch_e2e/dispatch/worktree）：69 测全绿
- 最终 `bash scripts/quality.sh`：见 change tasks C7（R3 处置后重跑）

## 对 B 段的输入（R3-C）

- B2 锚：`independent_verify` def L2643 起；B2a：Python `install_rc=0` 硬编码 L2686；B3：verify_prompt def L632、verdict 机械映射段 ~L671-673。
- `already_dispatched` 精确匹配改造挂 A5/follow-up，勿在 B 段顺手改（R3-C 建议）。
