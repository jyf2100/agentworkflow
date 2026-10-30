# Changelog

本项目推进流水线（pa-pipeline）版本记录。格式参照 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵从 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [1.1.0] - 2026-10-30

从单机 cron 编排器到「**hub 服务器集中式执行 + MM 渠道入口 + 验收面闭环**」。自 1.0.0 起 178+ commits，OpenSpec change 全程驱动（提案 → 多轮评审 → 落地 → 归档 delta sync）。

### hub：服务器集中式执行（新代码区 `hub/`）
- **执行 hub**（spec: `execution-hub`）：队列 + run 记录 + 容器生命周期；pa-executor 镜像容器化跑 pa 全链，per-project 串行派发（`oldest_pending_excluding_running_projects`）。
- **MM OAuth 身份面 + 面板控制台**（登录 / 凭据 / 队列 / runs / projects）+ 投递 API（PRD state ownership 交接协议，幂等 deliver_key）。
- **通知面**：MM bot 出站归属路由 + 语义事件出口 + 无 run 放弃告警 + 镜像陈旧检测；通知以 `pipeline_outcome` 业务终态 lead，容器退出码降附录。
- **executor 内置工程技能包**（20 skill 快照：9 可调用 / 11 惰性随行）。

### MM 渠道入口
- Mattermost 频道 `/pa run <一句话需求>` 直通流水线（单 run 容器 inject→report）；频道绑定推断 project。
- 成员三态授权（FOUND 授权 / NOT_MEMBER 拒 / UNKNOWN fail-closed 拒不回执）；bind/unbind 仅 admin。

### 验收面闭环
- **agent-verify-injection**：验收测试控制面工件链（外部编写 → content-addressed store → dev/verify 双注入，观测面 fail-open）；verify 执行 agent 化三机械锚 + invalid 三态。
- **e2e-acceptance-harness**：playwright 进 harness 声明面。
- **acceptance-preflight-guard**：作者期 import 白名单 + dispatch 期收集级自检。
- **确认环**：critic 第四态 needs_metrics × pa-spec-author 拟指标 → `metrics_proposed` 事件回流 → `/pa confirm|reject|metrics` + 72h 超时 + 重入队上界。

### dispatch 侧演进
- **single-flight-auto-merge**：verify 绿 + rebase CLEAN → 自动合 main（post-merge 全量测试 + 红 auto-revert + revert 循环熔断 + slot journal 跨进程 flock）。
- **dev-handoff 工件**：dev 断点结构化自述（围栏快照 / 崩溃收养 / 下轮注入；flag 默认 off）。
- **forge 适配器**：gitlab/gitcode 双平台（dispatch PR 面 + fetch 采集 + 凭据面）。

### persona 7 → 11
新增 pa-fetch-forge-repo（多 forge 预取语义半段）、pa-fetch-deepresearch、pa-spec-author、pa-test-author、pa-progress（dev 内循环方向抽查）；pa-prd-critic 升四态。

### 渐进迁移（flag-gated，baseline 零变更）
- **LangGraph 编排层**：`graph_pa.py` 7 node StateGraph 与 run_daily 并行存在；shadow parity canary + cutover 9 维发布门；flag off 零耦合秒回退。
- **per-agent model routing**：env > config 文件 > roc 默认。

### 真机修复链（hub 镜像 v0.1.28–v0.1.34，生产排障）
- persona wall-clock 壳 300→900s（qwen3.8 后端分钟级响应，300s 结构性不够）。
- revise 回环假通防御（重写 sha 机械校验 fail-closed drop + critic r2 注入 round1 反馈复审）+ pa-prd headless Write 预批准（`--allowedTools Read,Write`）。
- dev loop 预算 10→25（`PA_DEV_MAX_BUDGET` 透传链）+ SDK effort 显式下发（网关后端只认 xhigh/medium/low）。
- `pipeline_outcome` merged 终态归 green（auto-merge 绿不再误报「异常」）。
- spec-author 降级 drop 通知补可操作出路（重新 /pa run 提交）。

## [1.0.0] - 2026-07-29

首个正式版本。pa 编排器达到「生产可用」：7 段流水线在 cron 真实环境端到端跑通，dispatch 真开 PR，durable runtime 经崩溃恢复验证。

### 里程碑验证
- **2026-07-29 cron env 全量真跑**：`env -i` 极简 PATH 下全流程跑通，dispatch 真开 [cc-web-control#46](https://github.com/jyf2100/cc-web-control/pull/46)，#1105 三层缺陷（import / streaming 通道 / 证据采集）全 PASS。

### 核心
- **单一编排器 7 段流水线**（`run_daily.py`：fetch → radar → prd → inject → critic → dispatch → report）；cron 经 `run_cron.sh` 包装（每日 03:17）。
- **控制面 / 目标面严格分离**（ADR-0001）：本 vault = 控制面（编排 + 状态 + persona），各被控项目仓 = 目标面（dev-agent 干活）。
- **7 个 headless persona**（`.claude/agents/pa-*.md`）：fetch / radar / prd / prd-critic / verify，经 `claude --agent` 链式调用。
- **控制面标准执行器**（ADR-0006，`dev-agent.py`）：驱动所有目标仓的唯一执行器，在目标仓 worktree 内经 `claude-agent-sdk` 跑 dev loop。
- **fail-safe 分发**：三态远程查询（FOUND / NOT_FOUND / **UNKNOWN**），UNKNOWN = fail-safe 信号绝不当成功。
- **验证开发执行**：commit / push / PR 前须有新的绿色测试证据；无新绿则阻断。
- **durable runtime**（journal-driven 调度）：`journal.py` 事件流 + `recovery_cli.py`（崩溃恢复，中部损坏 fail-closed）+ `reconcile.py`（exactly-once 副本对账）+ `cutover.py` / `feature_flags.py` / `hook_adapter.py` / `container_sandbox.py` / `retry_policy.py`（会话感知重试）。
- **stage 输出契约层**（`stage_contracts.py`）：横切 persona 输出契约校验 + 诊断重试；critic 段止血消灭整晚 abort。
- **机械活 vs 语义活切分**：确定性零 LLM 的 Python（文件发现 / 去重 / journal / reconcile）vs headless persona（抽信号 / 翻译 PRD / 对抗审）。

### #1105 dispatch 三层修复
- **层1 streaming 通道**：SDK `wait_for_result_and_end_input` 保活条件漏 `can_use_tool` → `AbortError: Stream closed`。`sdk_compat_patch.py` ast 变异根治（`5665b27`）。
- **层2 测试证据采集**：`is_clean_test_cmd` 拒带 `|>&;` 的命令 → `test_not_run`。dev-agent prompt 加测试命令纯度规约（`1c44587`）。
- **层0 import**：cron 极简 PATH 解析到 `/usr/bin/python3` 缺 `claude_agent_sdk` → `ModuleNotFoundError`。`run_cron.sh` 补 miniconda PATH（`0eee85d`）。
- **DISPATCH_SKIP_PROJECTS 安全阀**：env 驱动临时跳过指定项目 dispatch（`648a9fc`），env 空集 = no-op。

### 质量与基础设施
- **单一质量命令** `bash scripts/quality.sh`（compileall + pytest + ruff，任一失败非零退出），CI 与本地共用。
- ruff 仅 `E9` + `F`（实缺陷），line-length=120，py311；pytest 全绿。
- canary CI（`.github/workflows/canary-real-node-cli.yml`）：真实 SDK + sdk_compat_patch + 三重锁回归网。
- OpenSpec 规约即设计：capability spec + 多轮评审（`docs/reviews/`）驱动变更。
