## Context

项目当前使用 Claude Agent SDK 作为目标仓开发内循环，`run_daily.py` 在外层执行独立测试、`pa-verify` 裁决、最多两轮增量重做和 GitHub 对账。第一阶段 `harden-project-pipeline` 将补齐测试绿发布硬门、外部查询失败关闭、统一执行器和可复现 CI。

第二阶段面对的是持续运行问题：每轮状态散落在 dispatch JSON、run log、测试输出、Git 分支和被追加修改的 PRD；SDK session ID 没有成为恢复契约；hooks 没有系统采集证据；权限回调缺少硬隔离兜底；一次 PRD 跨 dev、verify、retry 的因果链无法统一追踪。

设计必须保持控制面/目标面隔离、GitHub 为远端发布真源、永不自动 merge、第一阶段的绿色测试能力令牌和 fail-safe dispatch 不被绕过。它还必须支持 macOS 开发机与 Linux cron，并允许 sandbox、session store 和 telemetry 后端按部署环境渐进启用。

## Goals / Non-Goals

**Goals:**

- 每个 PRD iteration 都有稳定身份、不可变输入摘要、证据引用和明确终态。
- 进程崩溃后能够从最后一个 durable boundary 恢复，而不重复 commit、push、PR 或其他副作用。
- 根据失败原因确定性选择 resume、fork 或 new session。
- 用 SDK hooks 在内循环中执行快速门禁和证据采集，同时保留外层 clean-worktree 独立验证。
- 用 sandbox/OS 边界限制文件、进程、凭据和网络，而不是仅依赖 prompt 或命令字符串匹配。
- 通过 OpenTelemetry 和指标端到端观察 PRD、iteration、SDK session、tool、test、verify 和 delivery。

**Non-Goals:**

- 不替换 Claude Agent SDK 或重写其内部 model/tool loop。
- 不在本阶段实现通用多租户 SaaS、全局任务队列或自动 merge。
- 不改变 radar、PRD、critic 的语义。
- 不把完整工具输出、密钥或源文件内容写入 telemetry。
- 不保证所有失败都可原地 resume；安全的新 session 是合法降级。
- 不重复第一阶段的 TestEvidence、外部查询三态和 CI 基线实现。

## Decisions

### 1. Append-only journal 是本地恢复真源

为每个 `run_id/prd_id` 建立 append-only JSONL journal。每条 event 带 `schema_version`、event ID、timestamp、iteration ID 和 payload；iteration 通过事件归约得到状态，不原地覆写记录。大体积 diff/test/transcript 作为内容寻址 artifact 单独存储，journal 只记录路径、hash、size 和敏感级别。

选择 journal 而不是继续扩展单个 dispatch JSON，是因为 append-only 能保留因果历史并降低崩溃时半写覆盖风险。每行通过临时文件/flush/fsync 或单次原子 append 落盘；读取端遇到尾部不完整行可忽略并报警，中部损坏则阻塞自动恢复。

GitHub 仍是 PR/远端分支真源；SDK session store 是 conversation 真源；journal 负责把这些外部身份与本次 iteration 的意图和证据关联起来。

### 2. 显式 iteration 状态机

状态至少包括：`planned`、`running`、`agent_finished`、`test_blocked`、`verifying`、`revise`、`external_blocked`、`publish_ready`、`published`、`aborted`、`failed`。状态迁移函数必须校验前态，并使用 idempotency key 防止重复处理同一 event。

只有 `published` 是成功交付终态；SDK `ResultMessage(success)` 只能产生 `agent_finished`，不能直接产生 `publish_ready` 或 `published`。

### 3. RetryPolicy 只消费结构化失败分类

策略输入包括 SDK result subtype、测试证据、failure fingerprint、compaction 次数、session 可恢复性、diff 是否有进展和轮次预算。默认决策：

- 临时 provider/transport 中断且 session 可用：`resume`；
- verifier 给出局部反馈、历史仍可信：`resume`；
- 需要比较另一方案或保留原历史：`fork`；
- 重复相同失败、上下文污染、session 缺失/损坏：`new_session`；
- 外部真源未知：`block`，不消耗 retry；
- 预算/轮次用尽：`stop`。

每次决策写入 journal，包含 policy version 和 reason。副作用是否已发生必须先通过 GitHub/Git/TestEvidence 对账，绝不依赖模型记忆判断。

### 4. Hook 负责快速控制与证据，外层 verifier 负责独立事实

- `PreToolUse`：执行路径、命令、网络和敏感资源策略；
- `PostToolUse`：采集 tool result 摘要、exit code、文件变化和测试证据；
- `Stop`：检查第一阶段定义的新鲜绿色 TestEvidence，未满足则阻止正常结束并回传原因；
- `PreCompact`：写入 recovery snapshot，保留目标、验收标准、文件/commit/diff、测试、失败和下一步；
- `SubagentStart/Stop`：记录父子因果、工具面、effort 和结果 artifact。

Stop hook 是低延迟内门，不能替代 `independent_verify()` 的新 worktree 第二意见。Hook 异常默认 fail closed，但 journaling/telemetry 自身失败不得静默伪装为验证绿。

### 5. Artifact 与敏感数据分层

Artifacts 使用 SHA-256 内容地址和元数据索引。默认保存 sanitized tool summary、测试 stdout/stderr、diff 和 compaction snapshot；完整 transcript 受配置与保留期控制。Authorization、cookie、环境变量值、Keychain/pass 内容和已识别 secret 必须在落盘与 export 前脱敏。

### 6. Sandbox adapter 渐进启用

定义 `ExecutionSandbox` 接口，至少支持 local-worktree 和 container 两个实现。Local 模式保留现状但标记较低 assurance；container 模式提供只挂载目标 worktree、只读 PRD/source、非 root 用户、资源限制、临时 home、显式网络 allowlist 和最小凭据注入。

Sandbox 启动或策略加载失败时不得自动回退到更宽权限 local 模式；应进入 `sandbox_blocked`。Git push/PR 可继续由控制面宿主执行，避免把长期 GitHub 凭据放进 agent sandbox。

### 7. OpenTelemetry 表达跨轮因果

每个 PRD run 创建 root span；iteration、SDK session、tool、test、verify、reconcile 和 publish 为子 span。resume/fork 或跨进程 continuation 使用 trace context 和 span links 表达因果。属性只记录 ID、状态、耗时、token/cost、文件计数、hash 和错误分类，不记录 prompt、源码和 secret。

同时输出低基数指标：成功率、blocked/failed 数、iteration 数、测试通过率、重复失败率、恢复成功率、成本和 wall-clock。Telemetry 后端不可用时本地执行继续，但记录一次可见的 observability degradation event。

### 8. Backward-compatible staged migration

第一阶段完成后先以 shadow mode 写 journal/trace，但恢复决策仍走旧逻辑；对照一致后启用状态机作为 dispatch 控制。历史 dispatch JSON 保持可读，报告优先读 journal、缺失时回退旧 state。旧 PRD 中已追加的 verify feedback 不迁移删除，新 iteration 不再追加。

## Risks / Trade-offs

- [Journal 与 GitHub 状态暂时不一致] → 每个恢复入口先做 fail-safe reconciliation，并把外部检查结果作为新 event 追加，不改历史。
- [Session resume 固化错误上下文] → RetryPolicy 使用重复失败、compaction 和进展信号切换 fork/new session。
- [Stop hook 形成无限内循环] → hook continuation 受独立次数和 wall-clock 限制，外层仍有硬 kill。
- [Sandbox 增加安装时间和平台差异] → 使用预构建基础镜像、缓存依赖，并保留显式 local assurance tier 供开发 smoke。
- [Telemetry 泄露源码或凭据] → 默认 metadata-only、字段 allowlist、export 前 secret scanner、短保留期。
- [Append-only state 增长] → run 完成后压缩索引但保留原 journal，按保留策略归档 artifacts。
- [Hook/SDK 版本差异] → pin SDK 版本并建立 contract tests；不支持的 hook 必须在启动 preflight 失败，而非运行中静默忽略。

## Migration Plan

1. 完成并验证 `harden-project-pipeline`；冻结第一阶段状态/TestEvidence 契约。
2. 新增 journal schema、state reducer、artifact store 和历史 JSON compatibility fixtures。
3. 以 shadow mode 在现有 dispatch 流程旁写 journal，比较报告和终态一致性。
4. 接入 SDK hook adapter 与 mock transport contract tests，再对一个目标仓 canary。
5. 实现 RetryPolicy 和 session metadata；先启用 crash resume，再启用 fork/new-session 分流。
6. 引入 local/container sandbox adapter，container 先用于 dry-run 和独立验证，再用于真实 dev。
7. 接入 OpenTelemetry，验证敏感字段扫描和 backend outage 降级。
8. 将 dispatch 控制切到 journal reducer；保留旧 state 读取一个发布周期。

回滚时关闭 journal-driven、hooks、sandbox 和 telemetry feature flags，恢复旧 dispatch 控制；append-only 数据保留供审计，不执行逆向删除。任何已发生的远端副作用仍以 GitHub 对账处理。

## Open Questions

- 第一版 session store 使用 SDK filesystem + journal 引用，还是立即接 Redis/S3 adapter？默认建议先 filesystem，跨主机需求明确后再接外部 store。
- container sandbox 是否允许 agent 访问包注册表；建议按项目 profile 声明域名 allowlist，而非全网开放。
- Stop hook 最多允许几次 continuation 才交给外层 retry？建议独立小上限 2–3 次，但需 canary 数据校准。
- Journal/artifact 默认保留期和磁盘配额是多少？需要结合每日 PRD 数量与 transcript 策略测算。
- OpenTelemetry backend 选用现有 collector/Jaeger 还是仅输出 OTLP，由部署环境决定；规范只要求 OTLP-compatible export。
