## Why

当前 dispatch 段对同一目标仓**并发**投递多个 PRD（`max-concurrent=4`），各自独立 worktree→verify→开 PR，靠 `max_prs_in_flight`（默认 2）限并发扇出。但这道闸**对错了靶子**：它限的是「分支总数」，而真正决定"能否合进 main"的是「改动是否落在同一文件」。

实证（2026-07-29 cc-web-control）：同日 #47+#48 都改 `hub/server.cjs`（会撞）被同时放行，而和谁都 不重叠的 #49（config 体系）反被「在途 2≥2」挡在门外排队——系统性放行了危险的、挡住了安全的，方向都偏了。代价落到唯一 review/merger 身上：面对多个挂在 main 上的 OPEN PR（状态不可读），每次手动 merge 都要处理跨分支 rebase 冲突，且今日没撞只是因为 #47 自己 test 失败被关、侥幸没进 main。

## What Changes

- **投递模式**：同目标仓 dispatch 从**并发** → **串行单飞**（一次一个 PRD 走完 dev→verify→merge 才下一个；跨目标仓仍并行）。
- **新增自动 merge 阶段**：dev+verify 绿 → rebase 到 main → 自动 merge + push main（替换当前「兜底开 PR 待人工 review」）。
- **auto-revert 兜底**：merge 后对 main 跑全量测试，红即 `git revert` 该 commit + 告警，队列继续下一个。
- **triage 池**：dev 超时 / verify 2 次仍红 / rebase 冲突 → PRD 出队进 triage 池，**不阻塞队列**，单独成报告。
- `max_prs_in_flight` 退化（同项目内恒 1；保留作 bug 安全阀防消费器意外并发）。
- **BREAKING**：绿 PR 不再开「待 review」PR，而是直接合进 main。main 从此由流水线自动演进；人工角色从「逐 PR merger」变为「只 triage 异常」。

## Capabilities

### New Capabilities

- `single-flight-auto-merge`: 同目标仓 PRD **串行单飞消费**（dev→verify→merge 原子闭环）+ 自动 merge main + merge 后 auto-revert 兜底 + 异常 PRD triage 池。

### Modified Capabilities

- `fail-safe-dispatch`: dispatch 投递从并发改串行单飞；inflight 准入语义重构——从「分支总数 ≤ N」改为「同项目串行、跨项目并行」；新增 merge 阶段复用三态 fail-safe（clean→自动合 / conflict+unknown→转人工 triage，**绝不强合**，沿用既有 UNKNOWN=阻断 不变式）。
- `verified-dev-execution`: **反向约束**——标准执行器 SHALL NOT merge into main（merge 是破坏性副作用，归 `single-flight-auto-merge` 闭环负责；闭环自带 post-merge main 全量验证 + auto-revert 兜底）。executor 的「绿测试才发布」门仍只管 commit/push/PR，**不**延伸到 merge——避免把 merge 验证塞进 executor 造成越层（v2 修订点）。

## Impact

- **代码**：`Projects/项目推进流水线/scripts/run_daily.py` dispatch 段（消费器并发→串行）+ 新增 merge / auto-revert 阶段；`DISPATCH_LOCKS`（per-owner_repo）从「防 TOCTOU」扩展为「整个 dev→merge 串行」。
- **既有 spec**：`fail-safe-dispatch`（delta）、`verified-dev-execution`（delta）。
- **运维**：report 简讯语义从「开 PR #X 待 review」→「已合 main（commit abc）」/「需 triage」；cron 产出的 main 自动演进，须配套回滚预案（auto-revert + 单 commit `git revert` 粒度）。
- **状态 schema**：`dispatch_*.json` 需扩展（`merge_commit` / `reverted` / `triage_reason` 字段）。
