# Design — fix-dev-agent-stream-aclose-race

## 1. 确凿症状（2026-07-27 dispatch）

两份过闸 PRD（custom-mcp-server-url / hub-role-pair-view）均 `test_failed`。证据来源：
- dev-agent 日志：`state/runs/cc-web-control/20260727_*.log`
- dev 的独立子代理验证（结论与主 agent 一致）

报告：任何执行 Node 代码的命令（`npm test` / `node --test` / `node -e` / `node <file>` / `node --check`）在权限审批层返回 `Tool permission request failed: AbortError: Stream closed`，**进程启动前中止**；仅 `node --version` / `echo` / `git` 只读自动放行。`dangerouslyDisableSandbox` / 后台运行 / 子代理代办全同样中止。dev 盲写无法自测 → 编排器 verify 闸跑 `npm test` 得 exit=1 → `test_failed`。

## 2. 排除项

- **不是 `scope-bash.cjs`（目标面 hook）**：dev 日志明确「scope-bash hook 已通过，阻断来自核心权限层」。scope-bash 拒绝时 exit 2 + stderr 反馈，非 AbortError。PR #44 放宽 scope-bash 是独立正确的环境修复，但非此根因。
- **不是 `decide_bash`（控制面 `_can_use_tool` 权限闸）返回 deny**：deny 会带 message 给 dev，非 AbortError。AbortError 是 SDK stream 关闭后连 permission request 都发不出。
- **不是编排器 verify 闸**：编排器能在独立 worktree 跑 `npm test`（exit=1），证明测试命令本身可执行、worktree 环境正常。阻断只在 dev-agent 的 SDK dev loop 内。

## 3. 根因推断（待 tasks §1 复现确认）

`dev-agent.py:467` 调 `query(prompt=prompt_stream(prompt), options=options)`；`prompt_stream` 是单 yield async generator（`prompt_stream.py`）。SDK 0.2.x 在多轮 tool 调用中消费此 AsyncIterable，于某点 `aclose` 它。dev 日志 267 行 `RuntimeError: aclose(): asynchronous generator is already running` 表明 `aclose` 与 in-flight 迭代竞态 → stream 中途关闭 → 后续 tool（含 `can_use_tool` permission request）全 `AbortError: Stream closed`。

「node --version 能跑、npm test 不能」的可能解释：竞态在 dev loop 某轮次后触发（首个需 `can_use_tool` 审批的 Bash 之后）；竞态前的短命令已跑，之后的全挂。或：版本探针被 SDK 自动放行不经 `can_use_tool`，而 `npm test` 经 `can_use_tool` 时 stream 已关。复现验证之。

## 4. 复现计划（tasks §1）

1. **端到端最小复现**：构造最小 dev loop（`prompt_stream` + 多轮 Bash tool，含一个需审批的 `npm test`），跑 `dev-agent.py`，捕获 AbortError + aclose RuntimeError。
2. **单元复现**（更轻）：mock SDK 消费 `prompt_stream` + 中途 aclose，断言竞态。
3. 据复现确认根因层（`prompt_stream` aclose vs SDK 内部 vs 其他），落证据。

## 5. 修复方案（候选，复现后定）

- **A（推荐，最小直击根因）**：`prompt_stream` 改稳健 AsyncIterable —— 用 aiterator class（`__anext__`/`__aiter__` + 显式状态）替代 async generator，规避 generator 的 aclose 竞态语义。零新依赖。
- **B**：在 `process_dev_loop` 包一层守护，阻止 SDK 中途 aclose prompt stream（若 SDK 暴露 `disable_auto_close` 或等价）。
- **C（已知 follow-up，CLAUDE.md）**：升级 SDK ≥0.2.123 走原生 streaming prompt，去掉 `prompt_stream` workaround。但须重验 0.2.123 起 `can_use_tool` 与本执行器 string-prompt `query()` 的兼容性（ADR-0006 #7 当初锁版本的根因）。

复现后若 A 可行则首选；否则退 B/C。

## 6. 验证

- 复现测试 RED → 修复后 GREEN。
- 端到端：重跑一份 dev loop，确认 dev 能在 worktree 跑 `npm test`（不再 AbortError）。
- 全量 `bash scripts/quality.sh` 绿（compileall + pytest + ruff）。
- 回归：`verified-dev-execution` / `durable-runtime-*` 既有测试不破。

## 7. 风险

- async generator aclose 语义属 SDK 内部行为，复现可能需特定 SDK 版本时序；单元复现可能要 mock 较深。
- 方案 C（原生 streaming 迁移）牵动 ADR-0006 #7 与 SDK 版本锁，影响面大，非首选。
- 修复不得弱化 `can_use_tool` 权限闸（仍要经 `decide_bash`），只是让 stream 不中途关闭。
