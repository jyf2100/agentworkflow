## Why

第一阶段加固解决“能否安全发布”的底线问题，但流水线的 verify/retry 仍依赖分散的 JSON、日志、被追加修改的 PRD 和 Git 分支来恢复上下文，SDK session、compaction、hook 证据及重试决策没有统一契约。要把当前可运行原型升级为可恢复、可审计的生产级 Loop Agent，需要让每次 iteration 成为有持久身份、证据和明确恢复策略的工程事实。

## What Changes

- 建立 append-only iteration journal，统一记录 PRD、轮次、SDK session、base/head、diff、测试证据、裁决、反馈、成本、耗时和终止原因。
- 将施工反馈从原始 PRD 中分离；PRD 保持不可变需求真源，下一轮上下文由 journal 归约生成。
- 增加 session-aware retry policy：根据失败分类选择 resume、fork 或 new session，并保证重试不重放已完成的副作用。
- 接入 Claude Agent SDK 生命周期 hooks：在工具调用前做确定性策略检查，在工具调用后采集结构化证据，在 Stop 时执行快速完成门，在 PreCompact 时归档恢复摘要。
- 为 SDK 执行引入可配置的文件系统、进程、凭据和网络隔离；权限回调不再被视为唯一安全边界。
- 增加 OpenTelemetry iteration/agent/tool/verify spans 和运行指标，使一次 PRD 的跨轮执行可以端到端追踪。
- 定义崩溃恢复、journal 损坏、session 不可恢复、compaction 信息丢失和 sandbox 启动失败时的降级行为。

## Capabilities

### New Capabilities

- `durable-loop-state`: 定义 append-only iteration journal、证据引用、状态机、兼容读取与崩溃恢复。
- `session-aware-retry`: 定义 SDK session ID 持久化以及 resume、fork、new-session 的确定性选择和副作用防重放。
- `loop-lifecycle-controls`: 定义 PreToolUse、PostToolUse、Stop、PreCompact 和 subagent hooks 的证据与控制契约。
- `isolated-observable-execution`: 定义 sandbox 安全边界、凭据/网络隔离、OpenTelemetry tracing、指标与敏感信息处理。

### Modified Capabilities

无。`harden-project-pipeline` 的第一阶段 capability 尚未归档到 `openspec/specs/`；本变更将其视为实施前置条件，不复制或修改其需求。

## Impact

- 核心执行：`Projects/项目推进流水线/scripts/dev-agent.py` 的 SDK options、消息处理、hooks 和最终结果契约。
- 编排：`run_daily.py` 的 dispatch retry loop、状态持久化、恢复入口、报告聚合和清理策略。
- 新模块：journal/state machine、retry policy、hook evidence、sandbox adapter、telemetry adapter。
- 状态：`.project-auto/state/runs/` 增加稳定 schema 的 iteration records、evidence artifacts 和 trace/session 引用。
- 配置：profile 或全局配置增加 sandbox、session storage、telemetry 和 retry policy 开关。
- 基础设施：可选容器/沙箱运行环境、OpenTelemetry collector/backend、session store。
- 文档：新增 Loop Agent runtime ADR、恢复运行手册、安全模型和可观测性字段说明。
- 前置依赖：必须先完成 `harden-project-pipeline` 的测试绿发布门、外部查询失败关闭和可复现 CI 基线。
