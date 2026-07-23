# complete-durable-loop-runtime-integration Review R2

评审日期：2026-07-23
评审基线：`main@4438748`
评审范围：`a3af709..4438748` 对上一轮 Request Changes 的修复
评审结论：**Request Changes。仍不允许 archive。**

## 1. Verification

实际执行统一质量命令：

```bash
PATH=/tmp/pa-review-venv312b/bin:$PATH \
PYTHON=/tmp/pa-review-venv312b/bin/python \
bash scripts/quality.sh
```

结果：

- compileall：通过；
- pytest：`705 passed in 4.56s`；
- Ruff：`All checks passed`；
- quality.sh：退出码 0。

本轮代码的单元测试和静态质量基线为绿色。以下问题集中在安全边界、生产编排和验收证据真实性。

## 2. Blocking Findings

### P0-1：runtime evidence 会删除已有 main 分支保护

`runtime_evidence.py:258-262` 使用 GitHub API PUT 修改 `main` branch protection；finally 在 `runtime_evidence.py:290-292` 直接 DELETE protection。

代码没有：

- 读取原 protection 配置；
- 保存完整配置；
- 区分原本受保护还是本次临时创建；
- 恢复原 required checks、review、restriction 和 admin enforcement。

运行 `--drill 7.1`、`7.5`、`7.6` 或 `all` 可能永久删除仓库已有的保护规则。

**要求**：证据命令必须只读，或者完整保存并逐字段恢复原配置；恢复失败必须非零退出并显式阻断。

### P0-2：Docker canary 把宿主凭据传入容器

`runtime_evidence.py:138-148` 使用：

```bash
docker run -e GH_TOKEN -e GITHUB_TOKEN -e ANTHROPIC_API_KEY ...
```

Docker 的 `-e NAME` 会复制宿主同名环境变量，不是清除变量。该实现与“long-lived credentials host-side”要求相反。

同时，检查列表包含 `AWS_ACCESS_KEY_ID`，但 Docker 参数没有处理该变量。

**要求**：使用显式最小环境、空值覆盖或受控 env-file，并在真实容器内证明所有禁止变量不存在；canary 本身不得接触真实长期凭据。

### P0-3：session-aware retry 没有由 run_daily 驱动

`dev-agent.py` 已增加 `--iteration-seq`、`--resume-session`、`--fork-session`，并透传 SDK options。这只能证明执行器可以接受 retry 参数。

生产编排仍缺少：

- `run_daily.py` 调用 RetryPolicy；
- 根据决策生成上述命令行参数；
- retry 前调用 `recover_iteration()`；
- resume/fork/new-session 对应的 journal 决策事件；
- retry 预算消耗和终止处理。

全仓搜索显示这些参数只由 `dev-agent.py` 解析和消费，`run_daily.py` 没有传入。

此外，`dev-agent.py` 使用目标仓 `REPO_ROOT/state/sessions`，而控制面 coordinator 使用控制面 `STATE_DIR/sessions`，双方不是同一个 SessionStore。

**影响**：tasks 2.1、3.3、4.4 仍未形成生产闭环。

### P0-4：runtime evidence 失败时仍返回成功

`runtime_evidence.py:785-819` 运行 drill 并归档输出，但最终无条件 `return 0`。

以下情况都不会让 CLI 失败：

- Docker 不可用或 canary 红；
- SDK query 报错或 hooks 未覆盖；
- parity/cutover 失败；
- session lifecycle `overall_proven=false`；
- suite `overall_passed=false`。

代码还会归档失败结果，却用 `ARCHIVED evidence` 文案呈现。CI 和运维无法用退出码判断 rollout 是否通过。

**要求**：定义每个 drill 的 pass predicate；任一请求 drill 不通过时返回非零，失败 evidence 必须明确标记 `failed`，不能作为 passing manifest。

### P0-5：7.6 仍是混合聚合器，不是完整真实 suite

`real_cutover_suite()` 有效调用了部分真实 helper，但仍人工构造多个维度：

- SDK 维度调用旧的 `CT.run_sdk_hook_canary()`，不是 `real_sdk_canary()`；
- quality 使用 `{"passed": 3, "failed": 0}`，没有运行 quality evidence 命令；
- crash evidence 使用 `results=()` 并把布尔结果包装为对象；
- sandbox evidence被压成一个手工 `SandboxDrillResult`；
- manifest 只保存 summary，不保存输入 evidence digest 的引用关系。

**要求**：suite runner 必须直接执行真实子 drill，验证每个子 evidence，并归档包含子 artifact refs/digests 的 manifest。

## 3. Major Findings

### P1-1：7.2 只证明了一个简单 SDK query 路径

`real_sdk_canary()` 确实调用真实 `claude_agent_sdk.query()`，这是有效改进。但 prompt 只执行一次 Bash 后停止。

真实 SDK 没有覆盖任务要求的：

- stale-test；
- semantic-revise；
- compaction；
- subagent；
- hook-failure。

当前通过条件只要求至少一个 lifecycle callback 被触发，不要求七类场景全部满足预期。

### P1-2：crash drill 没有真实 crash/restart

`real_crash_restart_drill()` 创建真实 Git/bare remote 并使用 `ls-remote` 对账，这是有效修复。但所谓 crash 只是写到 `publish_ready` 后，在同一进程继续调用 `recover_iteration()`。

没有实际子进程、kill point、重新启动控制面或重新载入 journal/session store，因此没有验证 restart entrypoint 和 durable boundary。

### P1-3：7.5 仍未启用真实项目 rollout

`real_allowlist_rollout()` 在函数内部构造 `allowlist=[project_id]` 并调用 gate helper，没有：

- 写入或读取实际 project profile；
- 让生产 `run_daily.py` 在 journal-driven 模式执行；
- 保存一个发布周期的 legacy fallback 记录。

它还依赖具有破坏性 branch-protection 操作的 7.1 drill。

### P1-4：session lifecycle evidence 使用伪造 SDK result 和源码字符串检查

`real_session_lifecycle()` 使用手工 dict 模拟 SDK ResultMessage，然后用字符串搜索证明 `dev-agent.py` 含相关参数。它没有真实运行一次 resume/fork/new-session，也没有证明控制面 RetryPolicy 的决策实际传到了 SDK。

因此 `overall_proven=true` 高估了证据强度。

## 4. Effective Improvements

以下改动可以确认有效：

- `LocalGitResolver` 对 push 改用远端 `git ls-remote`；
- coordinator 增加 retry budget 和 SessionStore 所有权字段；
- `dev-agent.py` 支持 SDK resume/fork 参数并持久化 ResultMessage session ID；
- `real_sdk_canary()` 已能发起基础真实 SDK query；
- Docker canary 已从 FakeContainerRunner 向真实 `docker run` 迈进；
- 全量质量命令从 694 增长到 705 个通过测试，Ruff 保持全绿。

这些是实质进展，但不足以解除上述阻断项。

## 5. Task Status Recommendation

建议继续保持未完成：

| Task | 判定 | 原因 |
|---|---|---|
| 2.1 | Uncheck | coordinator 持有对象，但没有驱动 production retry/reconcile |
| 3.3 | Uncheck | run_daily 未选择和传递 resume/fork/new-session |
| 4.4 | Uncheck | reconciliation helper 未接到每次生产 retry 路径 |
| 5.5 | Uncheck | canary 凭据参数不安全，尚未形成可信通过证据 |
| 7.1 | Uncheck | drill 会破坏 branch protection，不能执行验收 |
| 7.2 | Uncheck | 真实 SDK 只覆盖单一 Bash/Stop 路径 |
| 7.3 | Uncheck | 没有真实进程 crash/restart |
| 7.5 | Uncheck | 没有真实项目 profile rollout |
| 7.6 | Uncheck | suite 使用人工映射和伪造 test counts，CLI 失败仍返回 0 |

## 6. Required Next Review Evidence

下一轮申请验收前应提供：

1. 非破坏性的真实 dispatch parity，禁止修改现有 branch protection，或证明完整保存/恢复。
2. 最小容器环境策略及容器内禁止凭据清单的真实检查结果。
3. `run_daily → RetryPolicy → recover_iteration → dev-agent args → SDK` 的端到端测试。
4. 七类 SDK hook 场景逐项真实触发证据。
5. 使用子进程 kill/restart 的五边界 crash drill。
6. 实际 project profile allowlist 和 journal-driven rollout 记录。
7. suite runner 直接调用真实 quality evidence、SDK、Docker、crash、recovery 与 cutover drill。
8. 失败 suite 非零退出，passing manifest 引用所有子 evidence digest。

## 7. Final Verdict

**Request Changes。不得 archive。**

本轮解决了一部分上一轮指出的纯代码缺陷，但新增 evidence 工具包含两个严重安全问题，并通过命名、人工映射和无条件成功退出把“可运行 helper”描述为“真实验收完成”。在安全问题修复和端到端证据补齐前，tasks 不应恢复为 34/34。
