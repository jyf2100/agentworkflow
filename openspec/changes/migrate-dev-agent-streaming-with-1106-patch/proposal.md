# Proposal — migrate-dev-agent-streaming-with-1106-patch

> Supersedes `patch-sdk-can-use-tool-stdin-keepalive`（C3，基于 0.2.121 monkey-patch 思路）。C3 的根因核验与 R2 评审历史保留供追溯，但 C3 未完成 tasks 标 `superseded`。本 change 在 C3 根因核验基础上**推翻两个乐观假设**：(1) 升级到 0.2.128 并不解 #1105——0.2.128 的 `wait_for_result_and_end_input` 保活条件与 0.2.121 一字不差；(2) C3 把 real Node-command canary「deferred to natural dispatch verification」是个空头承诺，自然 dispatch 自身一直 RED。本 change 把 patch 机制升级为 ast 变异（零漂移、fail-safe detection）+ canary 落 CI required check 发布门，根治 #1105。设计依据 `docs/superpowers/specs/2026-07-28-dev-agent-streaming-migration-design.md`（v3，commit 3bf953e）。

## Why

控制面标准执行器（`dev-agent.py`，ADR-0006）对每个目标仓经 `query(prompt=prompt_stream(prompt), options)` 驱动 dev loop，`can_use_tool` 作 Bash 权限闸。#1105（claude-agent-sdk-python）使该通道在生产配置下崩溃：`wait_for_result_and_end_input` 的 stdin 保活条件 `if self.sdk_mcp_servers or self.hooks:` 遗漏 `can_use_tool`；无 hooks/MCP（生产默认）时，finite prompt 耗尽即 `end_input()` 关 stdin → 后续 `can_use_tool` 权限响应（经 `_send_control_request` 写回同一 stdin）写不回 → `AbortError: Stream closed` → dev-agent 跑不了 `npm test` → verify 闸 `test_failed` → fail-safe 阻断 PR。

**源码核验推翻「升级即修」假设**：0.2.128 的保活条件与 0.2.121 完全相同；真正修复在 PR #1106（OPEN 未合）。故 C3「钉死 0.2.121 + monkey-patch」与「升级即修」两端都不成立。本 change：升级 0.2.128（拿其它改进 + 为 #1106 合入后零成本切换铺路）+ 本地忠实复刻 #1106 的最小 patch（ast 变异已安装版本原方法体，零漂移、#1103 逻辑字节级保留）+ fail-safe detection（精确旧形态→mutate，任何偏离→raise）+ canary 落 CI required check（堵掉「deferred to natural dispatch」空头承诺）。

## What Changes

- **升级 SDK 版本锁** `>=0.2.121,<0.2.123` → `>=0.2.128,<0.2.130`。证实升级不解 #1105（保活条件未变），但解锁版本锁并为 #1106 合入后零成本切换铺路。
- **新增零依赖 `scripts/sdk_compat_patch.py`**：`apply()` 对 `Query.wait_for_result_and_end_input` 做 H3-patch——ast 变异 if.test BoolOp 末位加 `self.can_use_tool` → compile → exec 回原模块命名空间（零漂移、模块级名 `logger`/`_first_result_event` 等天然保留、#1103 后台任务逻辑字节级保留）。带 fail-safe 四态 detection：getsource 失败→raise；upstream-fixed（已含 can_use_tool）→skip；精确旧形态（`self.sdk_mcp_servers or self.hooks`）→mutate；任何其他形态（缺锚点/抽 helper/间接变量）→raise（绝不盲打）。
- **dev-agent `main()` 注入**：SDK try 块内、options 构造前显式调 `sdk_compat_patch.apply()`；严禁 try/except 包 apply()（RuntimeError 必须冒泡进 exit 11，不可被吞）。
- **移除 `prompt_stream.py` 的 `Event.wait`**：C3 保留为输入侧冗余，v3 conscious 移除回归单 yield。accept loss of false hedge（RCA 实证 Event.wait 在真实 SDK 下本就不工作）；对冲交给 canary 发布门 + detection fail-safe + dev-agent 测试门 fail-closed 主路径。
- **canary 落 CI required check 发布门**：新增 `.github/workflows/canary-real-node-cli.yml`（workflow_dispatch，真实 dispatch cc-web-control 两 PRD，验证无 `AbortError: Stream closed`）；摘 xfail 的 PR 必须带一次 green canary run。
- **xfail 翻转 + 结构断言 + identity anti-mock**：`test_sdk_query_keeps_stdin_open_until_result` 摘 `xfail(strict)`；加 `count("or self.can_use_tool")==1` 结构断言（三方交叉确认 CRITICAL：`count("self.can_use_tool")` 会因 logger 装饰行误计为 2）+ `Query.X is sdk_compat_patch._last_patched` identity anti-mock。
- **supersede C3**：`patch-sdk-can-use-tool-stdin-keepalive/proposal.md` 标 superseded 指向本 change。

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `verified-dev-execution`: 收紧「Dev-agent test command executability across the SDK dev loop」requirement 的回归锁 Scenario——patch 机制从 C3 的泛「version-guarded compatibility patch」升级为对已安装版本原方法体的 ast 变异 + fail-safe 四态 detection（精确旧形态 mutate / 偏离即 raise）；并新增 canary 发布门 Scenario，要求摘 xfail 的变更必须带一次 green real-dispatch canary（CI required check），正式堵掉「deferred to natural dispatch verification」的空头承诺。requirement 的可观察契约（通道可用性 + 权限真实性）文本不变。

## Impact

- **代码**：`Projects/项目推进流水线/scripts/` 新增 `sdk_compat_patch.py` + `test_sdk_compat_patch.py`；修改 `dev-agent.py`（apply 注入）、`prompt_stream.py`（移除 Event.wait）、`conftest.py`（session autouse fixture）、`test_dev_agent_stream_lifespan.py`（摘 xfail + 结构断言 + aclose 测试改写）、`pyproject.toml`（版本锁）。
- **CI**：新增 `.github/workflows/canary-real-node-cli.yml`（manual dispatch，branch protection required check）；`RUNBOOK.md` 加 cutover 证据流程。
- **依赖**：`claude-agent-sdk` 0.2.121→0.2.128（minor 升级，0.2.128 仍遗漏 can_use_tool，故需本地 patch）。
- **supersede**：`patch-sdk-can-use-tool-stdin-keepalive`（C3）标 superseded；其根因核验/R2 评审历史保留。
- **风险**：ast 变异第三方内部方法在 SDK 升级/重构时可能失效 → 三重保险：(1) fail-safe detection（结构偏离即 raise，不盲打）；(2) xfail 翻转测试（patch 失效即回 RED）；(3) canary 发布门（CI required，真实 dispatch 验证）。不弱化权限闸（patch 只改 keep-alive 条件，`can_use_tool`→`decide_bash` 审批链不动）。
- 无控制面/目标面边界变化；无 immutable PRD 或 target-worktree state 变化。
