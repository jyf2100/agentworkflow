# Design — patch-sdk-can-use-tool-stdin-keepalive

> 方案 A（archive `2026-07-27-fix-dev-agent-stream-aclose-race`）的后续。方案 A 改**输入侧**（`prompt_stream.py` yield 后 `await asyncio.Event().wait()` 保持 pending），单测层 GREEN；但 2026-07-27 真实 dispatch（cc-web-control `custom-mcp-server-url` / `hub-role-pair-view`）3 轮全 `test_failed`，缺陷在真实 SDK 0.2.121 下复现。本 change 改 **SDK 方法侧**——在上游缺陷的源头打补丁。

## 1. 方案 A 为何不够（现状复核）

方案 A 的修复机制：`prompt_stream` 单 yield 后 `await asyncio.Event().wait()`，使 SDK 的 `stream_input()` 永不耗尽、永不调 `wait_for_result_and_end_input()`、从而不早关 stdin。这在**主路径**正确，单测（真实 `prompt_stream` + 真实 `Query` + FakeTransport）GREEN。

但真实环境仍 RED，且方案 A 在 R2 review 后把 spec Scenario 4 收紧成「真实 Node-command canary **deferred to natural dispatch verification**」——把「单测层无法证明真实修复」合理化为「留给自然 dispatch」。而自然 dispatch（2026-07-27）一直 RED，这是个**永不兑现的承诺**。根因：方案 A 的输入侧 workaround 覆盖不到 SDK 内部 `wait_for_result_and_end_input` 的实际 keep-alive 条件——只要该条件仍遗漏 `can_use_tool`，SDK 在任何次要关闭路径上仍会早关 stdin。

## 2. 根因再确认（SDK 方法侧，2026-07-27 源码核验）

钉死版本 `claude-agent-sdk==0.2.121`，`_internal/query.py:819`：

```python
async def wait_for_result_and_end_input(self):
    if self.sdk_mcp_servers or self.hooks:        # ← 保活白名单遗漏 can_use_tool
        await self._first_result_event.wait()
    await self.transport.end_input()              # ← 默认关 hooks → 立即关 stdin
    await self._first_result_event.wait()
```

`can_use_tool` 的 permission response 经 `_send_control_request`（query.py:384-435）写回**同一条 stdin**。默认 lifecycle-hooks 关闭时，`stream_input` 耗尽（或任何触发 `wait_for_result_and_end_input` 的路径）即 `end_input()` 关 stdin → 后续 `can_use_tool` response 写不回 → `AbortError: Stream closed`。上游 Issue **#1105 OPEN 未修**（0 评论，2026-07-11 后未动）；0.2.127 的 #1103 修的是另一场景（后台任务），不解 #1105。

## 3. 为什么是 monkey-patch 而非升级/迁移

- **裸升级 0.2.123+**：`0.2.123` 的 `wait_for_result_and_end_input` 条件与 `0.2.121` 相同（方案 A design §5 已核验），且 0.2.123 起 `can_use_tool` 要求 streaming 模式，与本执行器 string-prompt `query()` 冲突（ADR-0006）。升级 = 牵动 SDK 版本锁 + 迁移 streaming，大工程，非止血首选。
- **原生 streaming 迁移**：是已知正确 follow-up，但影响面大（ADR-0006 #7），不适合作为「让今日 dispatch 能跑」的快修。
- **monkey-patch SDK 方法侧**：直接在 `wait_for_result_and_end_input` 的 keep-alive 条件加 `or self.can_use_tool`，最小语义改动、不动版本锁、不弱化权限闸，是当前可控的止血 + 根治。

## 4. C3 方案（patch + 版本守卫）

**patch 点**：`claude_agent_sdk._internal.query.Query.wait_for_result_and_end_input`。

**策略（窄 patch，最小语义偏离）**：替换该方法为一个等价实现，仅把 keep-alive 条件从 `self.sdk_mcp_servers or self.hooks` 改为 `self.sdk_mcp_servers or self.hooks or self.can_use_tool`，其余逻辑（`_first_result_event.wait()` / `transport.end_input()`）原样保留。dev-agent 场景下 `can_use_tool` 总注册，语义等价于「保活到 result」。

**注入时序**：在 `dev-agent.py` import SDK 之后、首次 `query()` 之前，调用 `sdk_compat_patch.apply()`。新零依赖模块 `scripts/sdk_compat_patch.py` 承载，便于单测在 module load 时触发。

**fail-loud 版本守卫**（`apply()` 内）：
1. `inspect.getsource(Query.wait_for_result_and_end_input)` 取源码；
2. 若源码已含 `can_use_tool`（上游修了 #1105）→ 跳过 patch，log「upstream-fixed」；
3. 若源码结构不匹配已知缺陷形态（缺 `sdk_mcp_servers` / `self.hooks` / `end_input` 任一锚点）→ `raise RuntimeError`（拒绝盲打，防 SDK 重构后 patch 打错方法）；
4. 仅当结构匹配且仍遗漏 `can_use_tool` → 应用 patch，log「patched」。

## 5. 验收（xfail strict 翻转 = 主信号）

现状：`test_dev_agent_stream_lifespan.py::test_sdk_query_keeps_stdin_open_until_result` 是 `@pytest.mark.xfail(strict=True)`，直接调真实 `Query.wait_for_result_and_end_input()`，锁上游缺陷 RED（end_input 早关）。

patch 在 module load 生效后：
- 该测试由 RED → **PASS**（SDK 方法现在保活）；
- `xfail(strict=True)` 下 PASS = **XPASS → strict fail**（pytest 强制失败）；
- 验收动作 = **移除该测试的 `xfail` 标记**，使其成为普通 pass。

这正是 xfail-strict-regression-lock 纪律的收口：理论缺陷被实际修复后，strict 强制摘 xfail，防缺陷悄悄变绿。**移除 xfail 标记 + 测试 pass = patch 生效的可复核单测证据**，堵掉方案 A「deferred to natural dispatch」的空头承诺。

**真实 canary**：重跑 cc-web-control `custom-mcp-server-url` / `hub-role-pair-view` dispatch，dev-agent 在 worktree 跑 `npm test` 返回真实 exit status（无 `AbortError: Stream closed`）。

**全量**：`bash scripts/quality.sh` 绿（compileall + pytest + ruff），既有 1243 passed 不破。

## 6. 风险与对冲

- **monkey-patch 第三方内部方法**：SDK 升级/重构可能冲掉 patch → fail-loud 版本守卫（结构不匹配即 raise）+ xfail 翻转测试（patch 失效即测试回 RED）双重保险。
- **不弱化权限闸**：patch 只改 keep-alive 条件，`can_use_tool` → `decide_bash` 审批链不动，admit/deny 语义不变。
- **与方案 A 共存**：`prompt_stream.py` 的 pending workaround 保留为输入侧冗余，与 SDK 方法侧 patch 无冲突，两者叠加更稳。
- **上游依赖**：patch 是临时止血，真正解法是 streaming 迁移（ADR-0006 follow-up）+ 上游修 #1105。在 #1105 留复现评论并订阅，修了即提版本锁 + 移 patch。
