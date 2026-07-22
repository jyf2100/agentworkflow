# add-durable-loop-runtime 实现评审

评审日期：2026-07-22
评审范围：当前 `main` 分支（`3324927`）
设计依据：`openspec/changes/add-durable-loop-runtime/`
评审结论：**不满足第二阶段设计要求；当前主要完成第一阶段 `harden-project-pipeline`。**

## 一、结论摘要

当前代码已经具备第一阶段的安全发布基础：三态外部状态、fail-safe 准入、控制面统一执行器、测试发布门、独立 worktree 验证、`pa-verify` 裁决和 GitHub 对账。

但 `add-durable-loop-runtime` 要求的生产级恢复与审计能力尚未落地。当前仍依赖整体覆写的 dispatch JSON、分散日志、固定轮次 retry 和被追加修改的 PRD，尚未形成 append-only journal、显式状态机、session-aware retry、生命周期 hooks、sandbox 或 OpenTelemetry 因果链。

## 二、阻断性发现

### P0-1：缺少 durable journal

设计要求每个 run/PRD 使用 append-only、版本化 JSONL journal，并通过 reducer 重建状态（见 `design.md:31-37`）。

当前实现仍在内存中更新 `rec`，结束时整体写入 `dispatch_<stamp>.json`：

- `Projects/项目推进流水线/scripts/run_daily.py:1114`：初始化 dispatch 记录；
- `Projects/项目推进流水线/scripts/run_daily.py:1508`：整体写入 dispatch JSON。

`dev-agent.py` 的 JSONL 是运行日志，不具备 schema version、event ID、iteration ID、状态迁移校验、幂等键或 reducer，因此不能作为恢复真源。

**影响**：进程在副作用边界崩溃后，无法可靠判断 commit、push 或 PR 是否已经发生，也无法安全恢复。

### P0-2：没有 session-aware retry

设计要求根据结构化失败分类选择 `resume`、`fork`、`new_session`、`block` 或 `stop`（见 `design.md:45-56`）。

当前 retry 只是固定轮次：

- `Projects/项目推进流水线/scripts/run_daily.py:1181`：`range(1, VERIFY_MAX_ROUNDS + 1)`；
- `Projects/项目推进流水线/scripts/run_daily.py:1245-1250`：判红后追加反馈、切换 base、再次调用 dev-agent。

没有 session 持久化、失败指纹、compaction 计数、进展信号或 resume/fork/new-session 策略。

**影响**：无法区分瞬时 provider 故障、上下文污染、重复失败和外部状态未知，重试可能重复副作用或固化错误上下文。

### P0-3：PRD 仍被追加修改

设计要求原始 PRD 保持不可变，verify feedback 作为 journal artifact 保存（见 `design.md:84-86`）。

当前仍调用：

- `Projects/项目推进流水线/scripts/run_daily.py:1248`：`_append_verify_feedback(prd_abs, ...)`。

**影响**：需求、执行反馈和历史状态混在同一文件，无法从不可变输入和事件证据重建某一 iteration 的上下文。

## 三、重要缺口

### P1-1：生命周期 hooks 尚未实现

设计要求记录 `PreToolUse`、`PostToolUse`、`Stop`、`PreCompact`、`SubagentStart` 和 `SubagentStop`（见 `tasks.md:27-34`）。

当前只有第一阶段的 `can_use_tool` 权限回调（`dev-agent.py:385-386`），没有 hook adapter、tool-use correlation ID、PostToolUse 配对、PreCompact snapshot、Stop continuation 或子代理证据契约。

`can_use_tool` 不能替代完整 lifecycle hooks。

### P1-2：没有 sandbox 适配器

设计要求 local-worktree 和 container 两个 assurance tier、非 root、worktree-only mount、临时 home、资源限制、网络 allowlist，以及 sandbox 启动失败时的 `sandbox_blocked`（见 `design.md:72-76`）。

当前 SDK 直接以目标 worktree 作为 `cwd`（`dev-agent.py:382-386`），没有 `ExecutionSandbox` 接口、container adapter 或 sandbox failure 状态。`acceptEdits + can_use_tool` 不是 OS/容器级安全边界。

### P1-3：没有 OpenTelemetry 因果链

设计要求关联 `run → iteration → SDK session → tool → test → verify → reconcile → publish`，并支持跨进程 trace context/span links（见 `design.md:78-82`）。

当前仅有成本、turn 数、run log 路径和 verify round 等零散字段，没有 root trace ID、iteration ID、session 关联、OTLP export 或 telemetry outage event。

### P1-4：没有 artifact store 和证据完整性校验

设计要求使用 SHA-256 内容寻址 artifacts、metadata allowlist、secret redaction 和 digest verification（见 `design.md:68-70`）。

当前 diff、测试输出和日志通过普通路径写入，例如 `run_daily.py:1382`，没有内容摘要、元数据校验或敏感信息扫描契约。

## 四、已满足的第一阶段能力

当前代码已覆盖以下能力，但这些不能作为第二阶段完成证明：

- branch protection、idempotency、in-flight count 的三态 fail-safe 准入；
- 控制面统一 `dev-agent.py` 执行器；
- 新鲜绿色 TestEvidence 发布门（`dev-agent.py:430-440`）；
- 独立 worktree 测试重放（`run_daily.py:1332-1384`）；
- `pa-verify` 语义裁决（`run_daily.py:1090-1098`）；
- 有界 verify round（`run_daily.py:1181-1253`）；
- GitHub branch/commit/PR reconciliation（`run_daily.py:1258-1330`）。

## 五、OpenSpec 任务状态判断

`openspec/changes/add-durable-loop-runtime/tasks.md` 的 1 至 8 组任务当前均为未勾选状态，包含：

- journal、state reducer 和 artifact store；
- shadow journaling 与 crash-injection；
- lifecycle hooks；
- session-aware retry；
- sandbox 与网络/凭据隔离；
- OpenTelemetry 与指标；
- canary、恢复演练和 journal-driven cutover。

因此当前状态应标记为：

```text
Phase 1 harden-project-pipeline：已实现并归档
Phase 2 add-durable-loop-runtime：已设计，尚未实施
```

## 六、建议验收顺序

1. 先实现 `durable-loop-state`：journal schema、append/reduce、状态迁移、崩溃恢复和 artifact store。
2. 以 shadow mode 并行写 journal，验证 journal terminal state 与旧 dispatch JSON 一致。
3. 实现 `session-aware-retry`，先支持 `block/stop/new_session`，再引入 `resume/fork`。
4. 在 mock SDK contract tests 通过后启用 lifecycle hooks，并保留外层独立验证。
5. 实现 container sandbox 的 dry-run、网络阻断和凭据隔离测试。
6. 接入 metadata-only telemetry，验证 backend 不可用时本地执行仍可继续且产生 degradation event。
7. 完成 crash drills、单项目 canary 和一周期 legacy fallback 后，才切换 journal-driven dispatch。

## 七、最终判定

**判定：不通过（Not Ready）。**

当前版本不能声明满足 `add-durable-loop-runtime`。它是一个完成第一阶段安全发布加固、并为第二阶段保留设计文件的版本。后续实现应优先补齐 durable journal、状态 reducer 和 session-aware retry，这三项是恢复正确性的基础；不建议先做 LangGraph 或其他工作流框架迁移。
