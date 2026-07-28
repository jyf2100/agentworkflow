# 设计：升级 SDK 0.2.128 + 应用 #1106 定向 patch 根治 #1105

> **日期**：2026-07-28
> **范围**：`Projects/项目推进流水线/scripts/dev-agent.py`（控制面标准执行器，ADR-0006）+ SDK 版本锁 + 配套测试
> **前置**：根因分析 `项目推进/根因分析_dev-agent-SDK通道缺陷_20260727.md`、archive change `2026-07-27-fix-dev-agent-stream-aclose-race`（方案 A）、未实现 change `patch-sdk-can-use-tool-stdin-keepalive`（C3）
> **上游**：[anthropics/claude-agent-sdk-python#1105](https://github.com/anthropics/claude-agent-sdk-python/issues/1105)（OPEN）、[#1106](https://github.com/anthropics/claude-agent-sdk-python/pull/1106)「fix: keep stdin open for can_use_tool permission responses」（OPEN，未合并）

---

## 0. TL;DR

dispatch 阶段 dev-agent 在目标仓 worktree 跑 dev loop，需 `can_use_tool` 审批的 Bash 测试命令（`npm test`/`node --test`）一律 `AbortError: Stream closed` → dev 盲写无法自测 → verify 闸 `test_failed` → fail-safe 阻断开 PR。根因是 SDK `Query.wait_for_result_and_end_input` 的 stdin 保活条件漏了 `can_use_tool`。

本设计：**升级 SDK 0.2.121 → 0.2.128**（解版本锁 + 拿其他修复）+ **本地应用上游 #1106 的对症修复**（保活条件加 `or self.can_use_tool`），带 fail-loud 版本守卫、可移除。这是用户选定的根治路径，取代 C3 monkey-patch change（C3 基于 0.2.121、无升级）。

---

## 1. 关键事实核查（2026-07-28 源码级，self-contained）

### 1.1 #1105 真因（已确认）
SDK `_internal/query.py` `wait_for_result_and_end_input()`：
```python
if self.sdk_mcp_servers or self.hooks:   # ← 保活白名单遗漏 can_use_tool
    await self._first_result_event.wait()
await self.transport.end_input()          # ← 生产（无 MCP/hooks）立即关 stdin
```
`can_use_tool` 的 permission response 经 `_send_control_request`（`transport.write`）写回**同一条 stdin**。生产默认关 lifecycle hooks → `stream_input` 耗尽即触发 `wait_for_result_and_end_input` → `end_input()` 关 stdin → 后续审批写不回 → `AbortError: Stream closed`。首轮 `node -v`（通道未关）能过，后续 `npm test`（通道已关）不过——与实测时序吻合。

### 1.2 升级不解 #1105（修正根因分析的乐观假设）
根因分析 §8.1.2 称「升级 ≥0.2.123 后 can_use_tool 原生支持」。**源码核查证伪**：
- 下载 0.2.128 wheel，`query.py:925` 保活条件仍 `if self.sdk_mcp_servers or self.hooks:` —— **与 0.2.121 一字不差**。
- 0.2.128 的该方法体已被 #1103（后台任务）改过（docstring 提 background tasks/#1088），但**条件仍未加 can_use_tool**。
- 修 #1105 的对症 PR **#1106 OPEN、未合并、无 milestone、0 评论**。#1105 本身 OPEN、2026-07-11 后未动。
→ 结论：**所有已发布版本（0.2.122–0.2.128）都治不了 #1105**。

### 1.3 #1106 的精确修复（patch 内容来源）
#1106 改 `query.py`（核心一行）：
```diff
-        if self.sdk_mcp_servers or self.hooks:
+        if self.sdk_mcp_servers or self.hooks or self.can_use_tool:
             logger.debug(
                 "Waiting for first result before closing stdin "
                 f"(sdk_mcp_servers={len(self.sdk_mcp_servers)}, "
-                f"has_hooks={bool(self.hooks)})")
+                f"has_hooks={bool(self.hooks)}, "
+                f"has_can_use_tool={bool(self.can_use_tool)})")
```
外加 docstring 更新 + 107 行回归测试 `test_streaming_prompt_with_can_use_tool_waits_for_result`（mock transport 验证 can_use_tool 保活 stdin）。

### 1.4 dev-agent 已是 streaming 形态（非"从零迁移"）
dev-agent 走 `client.py:224` 的 `stream_input(prompt)` 路径（AsyncIterable，经 `prompt_stream`），不是 string 路径。SDK 内部「Always use streaming mode internally」（client.py:172）。**「迁移到 streaming」不是一个能自动解 #1105 的开关**——它实质是「升级 + 正确保活 control channel」。

### 1.5 升级 breaking 核查（0.2.121 → 0.2.128，无 breaking）
- `__init__` 导出：`query`/`ClaudeAgentOptions`/`AssistantMessage`/`UserMessage`/`ResultMessage`/`TextBlock`/`ToolUseBlock`/`ToolResultBlock`/`PermissionResultAllow`/`PermissionResultDeny`/`HookMatcher` 全在。
- `ClaudeAgentOptions`：`can_use_tool`/`setting_sources`/`hooks`/`fork_session`/`env`/`session_store`/`session_store_flush`/`resume`/`tools`/`permission_mode`/`max_turns`/`max_budget_usd`/`cwd` 全 FOUND。
- `ResultMessage`：`total_cost_usd`/`num_turns`/`session_id`/`is_error`/`result`/`errors` 全在（新增 `structured_output`/`model_usage`/`permission_denials`/`deferred_tool_use`/`terminal_reason` 向后兼容）。
- `client.py` 对 `can_use_tool` 的处理逻辑与 0.2.121 一致。
→ pa 两个 SDK 消费点（`dev-agent.py`、`learning_memory_reflection.py`）**无 breaking**。

### 1.6 迁移范围
- **`dev-agent.py`**：唯一 `can_use_tool` 消费点，受 #1105 影响 —— 本 change 主改。
- **`learning_memory_reflection.py` / `runtime_evidence`**：string-prompt、**无 can_use_tool**、不受 #1105 影响。升级后跑一次确认仍工作，无代码改。

---

## 2. 方案

升级 SDK 到 0.2.128 + 本地应用 #1106 修复。patch 是上游已写好的对症修复（语义可信，非自创），带 fail-loud 版本守卫；#1106 合并后摘除 patch、提版本锁上界（独立 follow-up change）。

**为何不是别的路径**（决策记录）：
- **纯 streaming 迁移**（改调用方式）：治不了 #1105（§1.2、§1.4）。
- **零-patch 深查 Python 保活**：方案 A（prompt_stream Event.wait）真实环境失效机制未明，真因可能在 Node CLI 侧，Python 可能触不到，风险高。
- **绕开 #1105（权限下放目标面 hooks）**：零 patch 但改权限模型，偏离 ADR-0006 #7（控制面单一权限源头），且需保证每仓有 hook。
- **C3 monkey-patch（基于 0.2.121）**：用户选定路径1 取代它——C3 无升级、拿不到 #1103 等其他修复，且仍卡在版本锁。

---

## 3. 详细设计

### 3.1 版本锁升级
- `Projects/项目推进流水线/pyproject.toml:26`：`"claude-agent-sdk>=0.2.121,<0.2.123"` → `"claude-agent-sdk>=0.2.128,<0.2.130"`。
  - 上界 `<0.2.130`：允许 0.2.128/0.2.129；若 0.2.129 改了方法体结构，patch 守卫 fail-loud（§3.2），不会静默失效。#1106 合并后 follow-up 放宽上界。
- `pyproject.toml:23-25` 注释 + `CLAUDE.md:79`：把过时叙述（「0.2.123 起 can_use_tool 要求 streaming / string-prompt 冲突」）改成「0.2.128 + 本地 #1106 patch（sdk_compat_patch.py，#1106 合并后移除）」。

### 3.2 #1106 定向 patch（新零依赖模块 `scripts/sdk_compat_patch.py`）
零依赖模块（同 `slug_utils`/`evidence`/`bash_allowlist` 既定模式，单测可零 SDK 导入）。

`apply()` 在 `dev-agent.py` **import SDK 后、首次 `query()` 前**调用（模块级 import 时触发，便于单测）：

1. `inspect.getsource(Query.wait_for_result_and_end_input)` 取真实源码；
2. **已含 `can_use_tool`** → skip + log「upstream-fixed (#1106 merged)」（上游修了）；
3. **缺锚点**（`sdk_mcp_servers` / `self.hooks` / `end_input` 任一）→ `raise RuntimeError`（fail-loud，拒绝盲打，防 SDK 重构后 patch 打错方法）；
4. **匹配缺陷形态**（有锚点、无 `can_use_tool`）→ 替换为等价实现：保活条件加 `or self.can_use_tool` + debug log `has_can_use_tool`，其余逻辑（`_first_result_event.wait()` / `transport.end_input()`）原样保留。**只复刻 #1106 的条件行 + log**（0.2.128 方法体已被 #1103 改过，docstring 不照搬 #1106 diff）。

注入方式：`Query.wait_for_result_and_end_input = <patched>`（monkey-patch 第三方内部方法——风险见 §5，靠守卫 + xfail 测试双重对冲）。

### 3.3 dev-agent.py 改动（最小）
- 模块顶部 `import sdk_compat_patch`（零依赖，不触 SDK）。
- `apply()` **延迟到 `main()` 内、构造 `ClaudeAgentOptions`/调 `query()` 前**调用（与现有「顶部 import 不触 SDK 连带加载、cron 路径隔离」原则一致；`sdk_compat_patch` 内部延迟 import `claude_agent_sdk._internal.query.Query`）。
- `can_use_tool`/`decide_bash` 权限闸不动，admit/deny 语义不变。

### 3.4 prompt_stream.py 清理（回归最简）
patch 根治后，`wait_for_result_and_end_input` 因 `can_use_tool` 正确保活 stdin。`prompt_stream` 的 `await asyncio.Event().wait()`（永不返回，方案 A 输入侧 workaround）不再必需。

改为最简形态（yield 首条 user 消息后正常结束）：
```python
async def prompt_stream(prompt: str) -> AsyncIterator[dict[str, Any]]:
    yield {"type": "user", "session_id": "",
           "message": {"role": "user", "content": prompt},
           "parent_tool_use_id": None}
```
stream 正常耗尽 → `stream_input` 调 `wait_for_result_and_end_input` → 因 patch 的 `or self.can_use_tool` 保活到 result → 不早关 stdin。更新模块 docstring（删方案 A Event.wait 叙述，改述「依赖 sdk_compat_patch 的 #1106 修复保活」）。`test_prompt_stream.py` 的 dict 结构守卫保留。

### 3.5 测试与验收
1. **xfail strict 翻转（主验收信号）**：`test_dev_agent_stream_lifespan.py::test_sdk_query_keeps_stdin_open_until_result` 现为 `xfail(strict)` 锁缺陷 RED。patch 生效 → PASS → strict 下 XPASS fail → **摘 `xfail` 标记**成普通 pass。这是 xfail-strict-regression-lock 纪律的收口（理论缺陷被实际修复后，strict 强制摘 xfail，防悄悄变绿）。
2. **复刻 #1106 回归测试**：移植 #1106 的 `test_streaming_prompt_with_can_use_tool_waits_for_result`（mock transport + can_use_tool + 断言「end_input 后写 control response 会失败、保活后能写、permission_calls==["Write"]」）进 pa，作为 channel-availability 的确定性集成测试。对应 `verified-dev-execution` spec 的 Scenario「Bidirectional permission channel remains available」。
3. **守卫三态单测**（`test_sdk_compat_patch.py`，用 fake/stub Query 类，不依赖真实 SDK 内部）：
   - 源码已含 `can_use_tool` → `apply()` skip；
   - 源码缺锚点 → `apply()` raise；
   - 源码匹配缺陷形态 → `apply()` 打 patch（断言替换后方法 keep-alive 条件含 `can_use_tool`）。
4. **真实 canary**：重跑 cc-web-control `custom-mcp-server-url` / `hub-role-pair-view` dispatch，dev-agent 在 worktree 跑 `npm test` 返回真实 exit status（无 `AbortError: Stream closed`），编排器 verify 闸观测到 dev 能自测。
5. **全量**：`cd Projects/项目推进流水线 && bash scripts/quality.sh` 绿（compileall + pytest + ruff E9+F），既有 1243 passed 不破。

### 3.6 cutover 证据
SDK 升级是运行时依赖变更。shadow 证据 = 真实 canary（§3.5.4）+ 全量 quality（§3.5.5）；`runtime_evidence.py`/`quality_evidence.py` 产 readiness=True 才算过。learning_memory_reflection/runtime_evidence 升级后跑一次确认无回归。

### 3.7 上游跟踪 + follow-up
- 在 #1105/#1106 留控制面复现评论（string-prompt + can_use_tool + 无 hooks/MCP → Stream closed；源码定位 query.py），订阅。
- **#1106 合并后的 follow-up（独立 change）**：提版本锁上界 + 移 `sdk_compat_patch` + 摘守卫测试。

### 3.8 openspec
- 本设计走 brainstorming→writing-plans 流程产出实现计划。
- `verified-dev-execution` spec 的 Requirement「Dev-agent test command executability across the SDK dev loop」**文本无需改**（它约束可观测行为，不约束实现 —— spec.md:57 明示）。
- Scenario 4「Regression locks the executability fix」措辞随实现微调（xfail 摘除、canary 不再 deferred），实现完成后 `/opsx:sync` delta 进 main spec。
- 废弃 C3 change 目录 `openspec/changes/patch-sdk-can-use-tool-stdin-keepalive/`（本 change 取代它）。

---

## 4. 验收标准（汇总）

- [ ] `test_sdk_query_keeps_stdin_open_until_result` 摘 xfail 后普通 pass（patch 生效的可复核单测证据）。
- [ ] 复刻的 #1106 回归测试 pass（channel-availability 确定性集成证据）。
- [ ] `test_sdk_compat_patch.py` 守卫三态全 pass。
- [ ] 真实 canary：cc-web-control 2 份 PRD dispatch 的 dev-agent 跑 `npm test` 返回真实 exit status，无 Stream closed。
- [ ] `bash scripts/quality.sh` 绿，1243 passed 不破。
- [ ] learning_memory_reflection/runtime_evidence 升级后无回归。
- [ ] pyproject.toml / CLAUDE.md 版本锁 + 叙述更新。
- [ ] #1105/#1106 已留复现评论 + 订阅。

---

## 5. 风险与对冲

| 风险 | 对冲 |
|---|---|
| monkey-patch 第三方内部方法，SDK 升级/重构可能冲掉 | fail-loud 版本守卫（结构不匹配即 raise）+ xfail 翻转测试（patch 失效即回 RED）双重保险 |
| 0.2.129 改方法体结构 | 守卫 raise（fail-loud），版本锁上界 `<0.2.130` 限定已验证范围 |
| 不弱化权限闸 | patch 只改 keep-alive 条件，`can_use_tool`→`decide_bash` admit/deny 链不动 |
| prompt_stream 清理后若 patch 失效无输入侧兜底 | 守卫 fail-loud 在启动期拦截，dev-agent 不会静默进入坏状态；xfail 测试锁 channel-availability |
| 升级引入未知回归 | breaking 核查（§1.5）已静态确认无 breaking + canary + 全量 quality 实测 |
| cron 迁移期空跑烧 token | 用户决策：不降噪，连续推进尽快落地 |

---

## 6. 范围外 / follow-up

- **#1106 合并后**：提版本锁上界、移 `sdk_compat_patch.py`、摘守卫测试（独立 change）。
- **dev loop hung 机制（方案 B 开 lifecycle_hooks）**：本 change 不解（开 hooks 非 baseline），留作 SDK 行为待查项。
- **max_turns/max_budget_usd 被 SDK 绕过**（ADR-0006 #6 follow-up）：本 change 不碰。

---

## 7. 决策记录

| 决策点 | 选择 | 理由 |
|---|---|---|
| 战略定位 | 直接 streaming 迁移，跳过 C3 止血 | 用户判定大工程值得根治，不愿引入 monkey-patch 中间补丁 |
| 根治路径 | 升级 0.2.128 + #1106 定向 patch | 源码核查证明纯 streaming 治不了 #1105（#1106 未合）；升级拿其他修复 + 复刻上游对症 patch 最务实 |
| prompt_stream | 清理回归最简（去 Event.wait） | patch 是主修复；去掉永不返回的 hack，代码更易懂；xfail + 守卫兜底 |
| 迁移期 cron | 不降噪，尽快落地 | 连续推进周期短，空跑成本可控，无需记得恢复跳过 |
