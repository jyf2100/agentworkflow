# 0008 — 控制面自动合 main：pa 首次直接演进目标仓 main

> **关联：** 扩展 [ADR-0001](0001-vault-target-isolation.md)（控制面/目标面隔离）的「不污染」边界；执行经 [ADR-0006](0006-devagent-control-plane-standard-executor.md) 标准执行器。配套变更：openspec `single-flight-auto-merge`。

## 决定

流水线的 **dispatch 段**（经 ADR-0006 标准执行器 `dev-agent.py`，在目标仓 worktree 内）**可自动**把 verify 双绿的分支经 **PR auto-merge**（`gh pr create` + `gh pr merge --merge`）合进目标仓 `main`，**取代**此前「兜底开 PR 待人工 review」。PR auto-merge 仍是自动合 main（不经人工 review），但走 PR 而非直接 push main——直接 push main 在有 PR-review 分支保护的仓被 GH006 拒（详见护栏 #2/#8 + canary 实证）。merge 后对 main 跑全量测试，红则 **auto-revert** 单个 merge commit。

这是 ADR-0001「不污染目标仓」原则的**受控扩展**：ADR-0001 表格第3行已认定「开发 agent 实现的代码改动（PR）不算污染」——auto-merge 把该改动从「PR 待 review」推进为「自动 merge 进 main」（经 auto-merged PR，非直推），性质仍是目标仓意图代码改动，但因**自动合 main** 而非留 PR 待 review，须叠加以下护栏（缺一不可）。

## 护栏（约束这条决定不被滥用）

1. **执行位置（守 ADR-0001 运行时隔离）**：merge/revert/post-merge test **经 dev-agent SDK loop 在目标仓 worktree 内执行**，控制面只发 string prompt 命令、**不直接持 git 写句柄**。源码在 vault、运行时贴目标仓——与 ADR-0006 同范式。
2. **PR auto-merge（`--merge`，双 parent）**：merge 经 `gh pr create` + `gh pr merge --merge` 产生**单一双 parent merge commit**（revert 目标明确；`--merge` 非 squash/rebase，守粒度）。**直接 push main 已弃用**——PR-review 分支保护（`required_pull_request_reviews` + `enforce_admins=true`）下 owner/admin 直推也被 GH006 拒（2026-07-30 canary 实证）。rebased feature branch 是 pa 临时分支，可 `--force-with-lease` 推送；**`main` 严禁 `--force*`**（防覆盖人工/并发提交）。
3. **merge 前三态 rebase**：rebase 到当前 main 须显式 `CLEAN`（正向证据：fetch 成功 + exit0 + 干净树 + 无冲突标记；缺证 = UNKNOWN）；`CONFLICT`/`UNKNOWN` → 转 triage，**绝不强合**（沿用 fail-safe UNKNOWN=阻断 不变式）。
4. **merge 后 main 全量测试 + 三态 auto-revert**：`PASS` → 保留；`FAIL` → `git revert` 单 merge commit；`UNKNOWN` 测试结果 / 非 `REVERTED` revert 结果 → **halt + quarantine 整仓队列 + CRITICAL 告警，不 continue**（杜绝坏代码留 main + 队列续跑叠加）。
5. **merge commit marker**：footer `Pipeline-Merge: <prd_id>`，回滚后可 `git log --grep` 机械找出已合 commit（flag 关闭**不**自动撤回已合 main）。
6. **exactly-once reconcile**：merge/revert 是跨 cron 破坏性副作用，纳入 durable reconcile——幂等 kind `merge`/`revert` + ancestry resolver（**merge 查 merge_commit 祖先、revert 查 revert_commit 祖先**——`git revert` 不删原 commit，故 revert 不可用 merge_commit presence 判定）+ crash 边界 `merge_push`/`revert_push` + 写顺序 intent→push→confirm。
7. **feature flag gated**：`single_flight_auto_merge`（默认关）+ shadow 子模式 `single_flight_serial_shadow`；shadow → 离线 drill → canary → 全量渐进。
8. **分支保护准入（PR path 兼容）**：目标仓 main 有 PR-review 分支保护时，auto-merge **走 PR**（`gh pr merge --merge` 可过：0 review 已满足 + `enforce_admins` 只拦直推不拦 PR 合并）；但若有必过 CI（`required_status_checks`）→ PR merge 被 check 阻塞 → **禁 auto-merge，退 triage**。CD 触发器放大风险同理 → 退 triage 待人工。

## 背景

用户核心关切：**「最担心 PR 没法合 main」**。既有的 `max_prs_in_flight`（默认 2）**对错了靶子**——限「分支总数」而非「改动是否落在同一文件」（2026-07-29 实证：#47+#48 都改 `hub/server.cjs` 会撞却被同时放行，不重叠的 #49 反被挡）。代价落到唯一 review/merger：面对多个挂在 main 上的 OPEN PR，每次手动 merge 都处理跨分支 rebase 冲突。串行单飞消灭冲突前提；自动 merge 闭环让 main 自动演进、人工角色从「逐 PR merger」变为「只 triage 异常」。

## 考虑过的替代

- **人工 review PR（保持现状）** —— 拒绝：用户选全自动；且并发 PR 的跨分支 rebase 冲突压垮唯一 merger，系统性放行危险 / 挡住安全。
- **控制面直接持 git 写句柄 merge** —— 拒绝：违反 ADR-0001 运行时隔离（控制面进程直写目标仓）。改经 dev-agent loop（同 ADR-0006）。
- **merge 失败后 continue（乐观恢复）** —— 拒绝：留坏代码进 main、队列续跑叠加污染。改 halt + quarantine + CRITICAL 告警。
- **force-push 修 main** —— 拒绝：覆盖人工/并发提交、不可逆。改 ff-only push + 单 commit revert 粒度。
