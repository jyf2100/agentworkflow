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

## 3. 根因分层（待 tasks §1 复现确认）

`dev-agent.py:467` 调 `query(prompt=prompt_stream(prompt), options=options)`；`prompt_stream` 是单 yield async generator（`prompt_stream.py`）。dev 日志 267 行的 `RuntimeError: aclose(): asynchronous generator is already running` 证明存在并发关闭症状，但不能单独证明它先关闭了权限控制通道。

对项目钉死的 `claude-agent-sdk==0.2.121` 做源码核验后，当前更强的根因候选位于 SDK input/control 生命周期：

1. `Client._process_query_inner()` 对 `AsyncIterable` 启动后台 `Query.stream_input(prompt)`；
2. 单 yield prompt 正常耗尽后，`stream_input()` 调 `wait_for_result_and_end_input()`；
3. 该函数仅在 `sdk_mcp_servers or hooks` 时等待首个 result，未把 `can_use_tool` 算作需要双向 stdin 的控制协议；
4. 默认 lifecycle hooks 关闭时，它立即 `transport.end_input()`；之后的 `can_use_tool` response 无通道可写，表现为 `AbortError: Stream closed`。

`0.2.123` 的相关条件与 `0.2.121` 相同，因此裸升级不能修复当前问题。tasks §1 必须分别验证：正常耗尽后的过早 `end_input()`、并发 `aclose()` 是否为独立第二问题、以及后续 permission request/response 的实际时序。

「node --version 能跑、npm test 不能」的当前解释候选是：版本探针可能被 SDK 自动放行而不需要双向 permission response；`npm test` 等命令需要 `can_use_tool` 时，有限 prompt 已耗尽并触发 `end_input()`。也可能叠加后续轮次的 `aclose()` 竞态，必须由下述分层复现判定。

## 4. 复现计划（tasks §1）

1. **确定性 SDK 集成复现（主 RED）**：固定 `claude-agent-sdk==0.2.121`，用 fake transport 驱动真实 `Query.stream_input` / control request 路径；单条 prompt 后发起后续 `can_use_tool` request，断言当前实现先 `end_input()`，permission response 无法写回。
2. **`aclose()` 分离复现**：独立复现 concurrent `aclose()`，判断它是第一因、第二问题还是退出清理噪声；不得用“人为并发关闭 generator 必然报错”的 mock 代替 SDK 因果证据。
3. **真实 dev-loop canary**：多轮 tool 后运行一个需审批的 Node 测试命令，证明进程实际启动并返回真实 exit status。
4. 据复现确认根因层并把证据落回本节，再选择修复。

## 5. 修复方案（候选，复现后定）

- **A（有限 prompt workaround）**：使用明确生命周期的 AsyncIterable；首条 user message 后保持 pending，直到 result/cancel，而不是立即 `StopAsyncIteration`。只把 async generator 换成同样有限的 iterator class 无效，因为正常耗尽仍触发 SDK `end_input()`。
- **B（SDK/client integration fix）**：选择或局部修补能在 `can_use_tool` 存在时保持 stdin 到 result 的 SDK client 路径。若需 vendored patch，必须钉版本并用确定性集成反例锁住，且不得绕过权限闸。
- **C（原生 streaming 迁移）**：只有在目标 SDK 版本或 `ClaudeSDKClient` 路径经同一反例证明确实保持双向控制通道时才可选。`0.2.123` 的 `query()` 路径源码与 `0.2.121` 同样遗漏 `can_use_tool`，裸升级不成立。

复现后按最小影响面选择；不预先推荐 A/B/C。任何方案都必须先通过相同的 permission request/response RED→GREEN，再跑真实 dev-loop canary。

## 6. 验证

- 复现测试 RED → 修复后 GREEN。
- 端到端：重跑一份 dev loop，确认 dev 能在 worktree 跑 `npm test`（不再 AbortError）。
- 全量 `bash scripts/quality.sh` 绿（compileall + pytest + ruff）。
- 回归：`verified-dev-execution` / `durable-runtime-*` 既有测试不破。

## 7. 风险

- async generator aclose 语义属 SDK 内部行为，复现可能需特定 SDK 版本时序；单元复现可能要 mock 较深。
- 方案 C（原生 streaming 迁移）牵动 ADR-0006 #7 与 SDK 版本锁，影响面大，非首选。
- 修复不得弱化 `can_use_tool` 权限闸（仍要经 `decide_bash`），只修复 input/control channel 生命周期。
