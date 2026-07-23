# complete-durable-loop-runtime-integration Tasks Review

评审日期：2026-07-23
评审基线：`main@aef3f69`
评审对象：`openspec/changes/complete-durable-loop-runtime-integration/tasks.md`
评审结论：**Request Changes。任务清单不应保持 34/34 完成，当前可确认约 25/34。**

## 1. Verification

实际执行仓库统一质量命令：

```bash
PATH=/tmp/pa-review-venv312b/bin:$PATH \
PYTHON=/tmp/pa-review-venv312b/bin/python \
bash scripts/quality.sh
```

结果：

- compileall：通过；
- pytest：`694 passed in 4.98s`；
- Ruff：`All checks passed`；
- quality.sh：退出码 0。

因此 task 1.1 可以保持完成。`quality_evidence.py` 也确实提供了执行真实 subprocess、记录解释器、命令、测试计数、Ruff 结果和 artifact digest 的能力，task 1.2 的“命令实现”部分成立。

## 2. Blocking Findings

### P0-1：session-aware retry 没有接入生产执行路径

受影响任务：2.1、3.3、4.4。

任务要求 coordinator 统一持有 retry/reconciliation，为 revise、resume、fork、new-session 分配 iteration，并在每次 retry 前对账副作用。

实际情况：

- `Coordinator` 持有 flags、IDs、journal、artifact、trace 和 telemetry，但没有 RetryPolicy、SessionStore 或 reconciliation driver；
- `run_daily.py`、`dev-agent.py` 和 `coordinator.py` 没有调用 `recover_iteration()`；
- `dev-agent.py` 没有持久化 SDK `session_id`，也没有设置 SDK `resume` 或 `fork_session`；
- verify revise 仍然由固定外层轮次重跑。

结论：模块 helper 存在，但生产 session-aware retry 尚未完成。

### P0-2：task 7.6 没有真正运行完整 cutover suite

`run_cutover_suite()` 接收七个调用方传入的布尔值，再执行 `all(flags)`。它不负责运行 quality、sandbox、SDK canary、recovery、telemetry 或 crash drills。

提交 `aef3f69` 增加的是聚合器及全 `True` 单元测试；仓库没有保存一次真实完整 suite 的输入证据、输出记录和可验证 artifact manifest。

结论：7.6 应恢复为未完成。

### P0-3：task 7.2 的“真实 SDK hook canary”没有调用 SDK

`run_sdk_hook_canary()` 直接调用 `HookAdapter` 方法模拟生命周期场景。`cutover.py` 文件头也明确说明真实 SDK runtime 验证不在 harness 内。

虽然 `hook_bridge.py` 已按 pinned SDK 字段完成映射，`dev-agent.py` 也注册了 hooks，但没有一次真实 SDK `query()` 触发 hooks 的 canary 证据。

结论：2.3 的 wiring 可以保留完成，7.2 的真实 canary 不能勾选。

### P0-4：task 7.5 没有对真实 allowlisted project 完成 rollout

仓库有 flag、parity、allowlist 三重 gate 和对应单元测试，但没有：

- 实际项目 allowlist 配置；
- 某个项目开启 `journal_driven_dispatch` 的 profile；
- 该项目 parity 通过的真实证据；
- 一个发布周期的 legacy fallback 运行记录。

结论：gate 实现完成不等于 rollout 已执行，7.5 应恢复为未完成。

## 3. Major Findings

### P1-1：task 5.5 不是真实 container canary

当前 canary：

- container 路径使用 `FakeContainerRunner`；
- Node/Python fixture 仅执行 `print/console.log('CANARY_OK')`；
- allowed-network 场景没有真实网络访问；
- resource-limit 场景只确认 spec 字段被 reflected，没有验证 cgroup enforcement；
- credential 场景只验证内存字典清洗，没有从真实容器内观察环境。

这可以证明 canary helper 的逻辑，但不能证明 Docker/Podman 隔离边界。5.5 应恢复为未完成。

### P1-2：task 7.1 的 real no-write dispatch 是手工 event flow

`run_shadow_parity_evidence()` 使用 `NO_WRITE_DRY_RUN_FLOW` 手工写 journal 并 reduce，没有调用 `run_daily.py --dispatch-skip-dev` 或真实 `dispatch_one()`。

因此 fixture parity 已实现，但无法覆盖 admission、coordinator、真实 journal emit 和 dispatch record 的集成漂移。7.1 的“real no-write dispatch”部分未完成。

### P1-3：task 7.3 没有证明进程崩溃后的 exactly-once

当前 crash drill 通过 helper 和注入 resolver 模拟五个边界，没有实际：

- kill 控制面进程；
- 从 journal 重启；
- 对真实 branch/commit/push/PR 做恢复对账；
- 验证同一副作用没有被重复执行。

另外，`LocalGitResolver.check("push")` 使用本地 `show-ref` 判断 push，而不是查询远端 `git ls-remote`。本地存在 branch 不能证明远端 push 已发生。

结论：7.3 应恢复为未完成，且远端 push resolver 必须修正后再做真实 drill。

## 4. Task Status Recommendation

建议恢复为未完成：

| Task | 建议 | 原因 |
|---|---|---|
| 2.1 | Uncheck | coordinator 未持有 retry/reconciliation |
| 3.3 | Uncheck | resume/fork/new-session 未进入生产 iteration 路径 |
| 4.4 | Uncheck | retry 前真实副作用对账未接入，push resolver 也非远端真源 |
| 5.5 | Uncheck | 只有 fake container 和本地 subprocess canary |
| 7.1 | Uncheck | no-write dry-run 是手工 event flow，不是真实 dispatch |
| 7.2 | Uncheck | canary 未调用真实 SDK query |
| 7.3 | Uncheck | 未做真实 crash/restart/remote reconciliation |
| 7.5 | Uncheck | 没有真实 allowlisted project rollout |
| 7.6 | Uncheck | suite 仅聚合调用方布尔值，没有运行和归档完整证据 |

其余任务可以暂时保留勾选，但归档前仍应通过 capability specs 的场景级验收，而不能只依赖 commit message 或单元测试数量。

## 5. Required Acceptance Evidence

重新勾选上述任务前，至少需要：

1. 一次真实 SDK session，证明 six lifecycle hooks 被 SDK 实际触发并写入 journal。
2. 一次真实 Docker/Podman Node + Python canary，包含真实 allowed/denied egress、容器内凭据检查和资源限制观察。
3. 一次真实 `dispatch-skip-dev` shadow parity，而不是手工 event flow。
4. 一个真实项目 allowlist 配置及 journal-driven 运行记录。
5. 五个边界的真实 crash/restart drill，push 使用远端真源查询。
6. 一次完整 suite runner，由 runner 自己执行各子项并归档带 digest 的 manifest，而不是接收外部布尔值。
7. session metadata、resume/fork/new-session 与 reconciliation-before-retry 的生产路径证据。

## 6. Final Verdict

**Request Changes。暂不允许 archive。**

本轮实现的工程量和单元测试覆盖是真实的，统一质量命令也已全绿；问题集中在“把 helper、fake adapter 和单元测试视为真实 rollout/canary 完成”。OpenSpec tasks 同时包含 implementation 与 execution evidence，两者必须都满足才能勾选。
