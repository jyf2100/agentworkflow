# Changelog

本项目推进流水线（pa-pipeline）版本记录。格式参照 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵从 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

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
