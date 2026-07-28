# Tasks — patch-sdk-can-use-tool-stdin-keepalive

> **⚠ Superseded by `migrate-dev-agent-streaming-with-1106-patch` (2026-07-28)。**
> 本 change 未实施即被推翻（C3 monkey-patch 思路被 H3-patch ast 变异取代）。下列全部 tasks 标
> `superseded`——不再实施；等价工作由新 change 承担（H3-patch 取代 2.1–2.3，xfail 翻转 + canary
> 取代 3.1–3.4，上游跟踪/规约同步见新 change Task 5）。版本锁也从 C3「钉死 0.2.121」改为「升级 0.2.128」。

## 1. 现状确认

- [ ] 1.1 复核 main spec `verified-dev-execution` Scenario 4 现状 = 方案 A R2 收紧后的「real Node canary deferred to natural dispatch」文本，确认本 change 的 MODIFIED delta 精确替换它
- [ ] 1.2 复核 `test_dev_agent_stream_lifespan.py::test_sdk_query_keeps_stdin_open_until_result` 当前为 `xfail(strict=True)` 且 RED（锁 query.py:819 缺陷）；记录其调用真实 `Query.wait_for_result_and_end_input()` 的事实
- [ ] 1.3 复核上游 #1105 仍 OPEN 未修（`gh issue view 1105`），0.2.127 #1103 不解本场景

## 2. 实现 patch

- [ ] 2.1 新建零依赖 `scripts/sdk_compat_patch.py`：`apply()` 用 `inspect.getsource` 取 `Query.wait_for_result_and_end_input` 源码 → 已含 `can_use_tool` 则 skip（log upstream-fixed）→ 缺 `sdk_mcp_servers`/`self.hooks`/`end_input` 锚点则 `raise RuntimeError`（fail-loud）→ 否则替换为 keep-alive 条件加 `or self.can_use_tool` 的等价实现
- [ ] 2.2 在 `dev-agent.py` import SDK 之后、首次 `query()` 之前调 `sdk_compat_patch.apply()`；不影响 `can_use_tool`/`decide_bash` 权限闸
- [ ] 2.3 patch 仅作用于 SDK 版本 `>=0.2.121,<0.2.123`（当前锁）；不动 SDK 版本锁、不动 `prompt_stream.py`（方案 A workaround 保留为冗余）

## 3. 验证

- [ ] 3.1 **xfail 翻转（主验收）**：patch 生效后 `test_sdk_query_keeps_stdin_open_until_result` 由 RED → PASS → strict XPASS fail → 移除其 `xfail` 标记使其普通 pass；此「摘 xfail + pass」即 patch 生效的可复核单测证据
- [ ] 3.2 全量 `bash scripts/quality.sh` 绿（compileall + pytest + ruff E9+F）；既有 1243 passed 不破，含 `test_prompt_stream.py` dict 结构守卫
- [ ] 3.3 版本守卫单测：构造「源码已含 can_use_tool」→ apply() skip；构造「结构不匹配」→ apply() raise；构造「缺陷形态」→ apply() 打 patch（用 fake/stub Query 类，不依赖真实 SDK 内部）
- [ ] 3.4 真实 dev-loop canary：重跑 cc-web-control `custom-mcp-server-url` / `hub-role-pair-view` dispatch，dev-agent 在 worktree 跑 `npm test` 返回真实 exit status（无 `AbortError: Stream closed`）；编排器 verify 闸观测到 dev 能自测

## 4. 上游跟踪 + 规约同步

- [ ] 4.1 在 #1105 留复现评论（string-prompt + `can_use_tool` + 无 hooks/MCP → Stream closed；源码定位 query.py:819），订阅
- [ ] 4.2 评审通过后 `/opsx:sync` delta → main spec（MODIFIED Scenario 4）
- [ ] 4.3 `/opsx:archive`
- [ ] 4.4 follow-up（独立 change）：#1105 修后提版本锁 + 移 patch + streaming 迁移（ADR-0006）
