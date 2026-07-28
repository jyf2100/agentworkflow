# Design — migrate-dev-agent-streaming-with-1106-patch

> 设计依据 `docs/superpowers/specs/2026-07-28-dev-agent-streaming-migration-design.md`（v3，commit 3bf953e）。可执行步骤见 `docs/superpowers/plans/2026-07-28-dev-agent-streaming-migration.md`。本 design 聚焦「为什么这么选」，不逐行重复实现。

## Context

控制面标准执行器（`dev-agent.py`，ADR-0006）经 `query(prompt=prompt_stream(prompt), options)` 驱动每个目标仓的 dev loop，`can_use_tool` 作 Bash 权限闸。上游 #1105 使该通道在生产配置（无 hooks/MCP）下崩溃：`Query.wait_for_result_and_end_input` 的 stdin 保活条件 `if self.sdk_mcp_servers or self.hooks:` 遗漏 `can_use_tool`；finite prompt 耗尽即 `end_input()` 关 stdin → 后续 `can_use_tool` 权限响应写不回 → `AbortError: Stream closed` → dev-agent 跑不了 `npm test` → verify 闸 `test_failed`。

前置 change `patch-sdk-can-use-tool-stdin-keepalive`（C3，0.2.121 monkey-patch 思路）已核验根因，但本轮源码核验**推翻其两个隐含假设**：

1. **「升级即修」不成立**：0.2.128 的 `wait_for_result_and_end_input` 保活条件与 0.2.121 一字不差（0.2.128 仅 #1103 改了方法体内后台任务逻辑，条件未动）。真正修复在 PR #1106（OPEN 未合）。
2. **「canary deferred to natural dispatch」是空头承诺**：C3/Plan A 把 real Node-command 验证推迟给自然 dispatch，而自然 dispatch（2026-07-27 cc-web-control 两 PRD）3 轮全 RED，承诺永不兑现。

故需：(a) 升级 0.2.128 解锁版本锁 + 为 #1106 合入后零成本切换铺路；(b) 本地忠实复刻 #1106 的最小 patch，但用比 C3 monkey-patch 更安全的方式（ast 变异原方法体，零漂移）；(c) 把 canary 落 CI required check，堵掉空头承诺。

## Goals / Non-Goals

**Goals:**
- 根治 #1105：dev-agent 在生产配置下 dev loop 任一 tool turn 都能跑目标仓原生测试命令并拿到真实 exit status。
- patch 机制零漂移：对**已安装**版本原方法体做最小 ast 变异（仅 if.test BoolOp 末位加 `self.can_use_tool`），#1103 等无关逻辑字节级保留，模块级名（`logger`/`_first_result_event` 等）天然可见。
- patch 失败方向 fail-safe：SDK 升级/重构使结构偏离已知缺陷形态时，**raise 而非盲打**（绝不 patch 错方法）。
- canary 落 CI required check 发布门，正式承担「real executability verification」契约。
- xfail-strict-regression-lock 收口：理论缺陷被实际修复后 strict 强制摘 xfail，防悄悄变绿。

**Non-Goals:**
- 原生 streaming 迁移（ADR-0006 #7 全量）——本 change 仍是 string-prompt `query()`，仅 patch 其 keep-alive 缺陷。streaming 迁移保留为独立 follow-up。
- #1106 合并后的清理（移除 `sdk_compat_patch.py`、放宽版本锁上界）——独立 follow-up change。
- 弱化或绕过 `can_use_tool`→`decide_bash` 权限闸——patch 只改 keep-alive 条件，审批链不动。

## Decisions

### D1: 升级 0.2.128（明知不解 #1105）

**选择**：版本锁 `>=0.2.121,<0.2.123` → `>=0.2.128,<0.2.130`。
**为何**：源码核验证实 0.2.128 不解 #1105，但升级带来 (1) 0.2.124–0.2.128 的其它改进；(2) 为 #1106 合入后零成本切换铺路（届时只需删 patch + 放宽上界）；(3) 解锁 C3 钉死 0.2.121 的版本锁。升级本身风险低（保活条件未变，行为等价）。
**替代**：留在 0.2.121 + patch——可行但欠下版本债，且 0.2.121 越来越旧。拒绝。

### D2: H3-patch（ast 变异原方法体）而非 C3 monkey-patch（替换整个方法）

**选择**：`apply()` 用 `inspect.getsource` 取原方法体 → `ast.parse` → 在 if.test BoolOp 末位 append `self.can_use_tool` → `compile` + `exec` 回原模块命名空间。
**为何**：C3 monkey-patch 替换整个方法体，需手工复刻原逻辑（`_first_result_event.wait()`/`transport.end_input()`/logger 行），与 SDK 漂移风险高；且 0.2.128 的方法体含 #1103 后台任务逻辑，手工复刻易漏。H3-patch 只动 if.test 一个 BoolOp 节点，原方法体其余字节级保留，#1103 天然在——**最小变异 = 最小漂移**。exec 回原模块命名空间（`vars(inspect.getmodule(orig))`）让模块级名（`logger`/`anyio`/`_inflight_requests`）天然可见，规避 H2「内联 0.2.128 方法体」方案的 NameError 陷阱。
**替代**：
- H2（内联 0.2.128 完整方法体）：执行命名空间陷阱（`_inflight_requests`/`logger`/`anyio` 在 patch 模块命名空间不可见）+ 依赖 wheel=安装版本。拒绝。
- C3 monkey-patch：漂移风险 + 需复刻 #1103。拒绝。

### D3: detection 四态 fail-safe（精确旧形态→mutate，偏离→raise）

**选择**：detection 按四态分流——(1) `getsource` 失败（pyc-only/zip）→ raise；(2) BoolOp 已含 `can_use_tool`（upstream-fixed）→ skip；(3) 精确旧形态 `self.sdk_mcp_servers or self.hooks` → mutate；(4) 任何其他形态（缺锚点/抽 helper/间接变量）→ raise。
**为何**：方向是 fail-safe 而非 fail-unsafe。C3 的「白名单命中→跳过；否则补丁」是 fail-unsafe——SDK 重构成未知形态时仍盲打，可能 patch 错方法。v3「精确旧形态才 mutate，任何偏离即 raise」保证 SDK 重构时 dev-agent exit 11 → dispatch 报告标红 → 人介入，而非静默 patch 错误。`can_use_tool=None` 的 Query 实例语义等价未 patch（`or None` 短路为假），无副作用。
**替代**：C3 fail-unsafe 白名单。拒绝。

### D4: 移除 prompt_stream 的 Event.wait（conscious，accept false-hedge loss）

**选择**：移除 `await asyncio.Event().wait()`，回归单 yield。
**为何**：RCA 实证 Event.wait 在真实 SDK 下本就不工作（#1105 在 SDK 方法侧，输入侧 pending 救不了次要关闭路径）。C3 保留它为「输入侧冗余对冲」，但这是虚假对冲——给假绿提供温床。v3 conscious 移除，accept false-hedge loss，对冲显式交给三重真实机制：(1) canary 发布门（CI required）；(2) H3-patch detection fail-safe；(3) dev-agent 测试门 fail-closed 主路径（`test_not_run`→exit 14→`blocked_by_gate`）。docstring 写明取舍。
**替代**：C3 保留 Event.wait 为冗余。拒绝（虚假对冲）。

### D5: canary 落 CI required check 发布门

**选择**：新增 `.github/workflows/canary-real-node-cli.yml`（`workflow_dispatch`，真实 dispatch cc-web-control 两 PRD，grep 断言无 `AbortError: Stream closed`）；branch protection 列为 required check；摘 xfail 的 PR 必须带一次 green canary。
**为何**：堵掉 C3「deferred to natural dispatch」空头承诺。单测层（FakeTransport）能锁 channel-availability precondition，但证明不了真实 Node CLI + 真实 GitHub 凭证下 dev-agent 真能跑 `npm test`——这部分契约必须由真实 dispatch canary 承担。canary 目标抽象：cc-web-control 两 PRD 是当前实例，迁移到其他等价目标仓须更新 RUNBOOK。
**替代**：继续 deferred to natural dispatch。拒绝（永不兑现）。

### D6: 结构断言 `count("or self.can_use_tool")==1`（三方交叉确认 CRITICAL）

**选择**：xfail 翻转测试加结构断言 `src.count("or self.can_use_tool") == 1` + identity `Query.X is sdk_compat_patch._last_patched`。
**为何**：`count("self.can_use_tool")==1` 是错的——#1106/H3-patch 使 `self.can_use_tool` 在 2 处出现（if 条件 + logger f-string `bool(self.can_use_tool)`）。`count("or self.can_use_tool")==1` 锚定最小变异的唯一标记，对「仅变异 if 条件、有意省略 logger 装饰行」和「全量 #1106」两种情况都鲁棒。identity anti-mock 防 XPASS 来自 mock 污染/偶然 pass。此修正由 python-reviewer、silent-failure-hunter、architect 三方独立确认（CRITICAL）。

## Risks / Trade-offs

- **ast 变异第三方内部方法**：SDK 升级/重构可能冲掉 patch → 三重保险：D3 fail-safe detection（偏离即 raise）+ xfail 翻转测试（失效即回 RED）+ D5 canary 发布门（真实 dispatch 验证）。
- **accept Event.wait false-hedge loss**（D4）：移除输入侧对冲，理论上失去一道防线 → 但该防线本就虚假（真实 SDK 下不工作）；真实对冲已由三重机制承担。trade-off 可接受，docstring 显式记录。
- **getsource 在非源码发行版失败**（pyc-only/zip/C-ext）：D3 state 1 直接 raise，dev-agent exit 11 → 人介入钉源码发行版。fail-loud 优于盲打。
- **canary 目标耦合 cc-web-control**：当前 canary 硬编码 cc-web-control 两 PRD → RUNBOOK 标明「迁移到其他目标仓须更新」，非通用发布门。已知限制，记录在案。
- **不弱化权限闸**：patch 只改 keep-alive 条件，`can_use_tool`→`decide_bash` admit/deny 语义不变。已确认无副作用。

## Migration Plan

9 任务 TDD 链（详见 plan）：(1) 升级版本锁；(2) sdk_compat_patch.py H3-patch 核心 + defect-form 测试；(3) 守卫四态 + 幂等单测；(4) conftest session autouse fixture；(5) dev-agent apply 注入；(6) prompt_stream 移除 Event.wait + aclose 测试改写；(7) 摘 xfail + 结构断言 + anti-mock；(8) canary 发布门 CI + cutover runbook；(9) supersede C3 + 上游 #1105 复现评论。

**Cutover 证据**（发布门前必过）：(a) canary-real-node-cli workflow green；(b) `python quality_evidence.py` readiness=True；(c) `bash scripts/quality.sh` 全绿（compileall + pytest + ruff E9+F），既有测试不破。

**Rollback**：若 cutover 后发现问题，回退路径 = revert 摘 xfail 的提交 + 移除 sdk_compat_patch 调用（patch 是纯叠加，移除即恢复原 SDK 行为，#1105 重现但 fail-safe 阻断仍在）。版本锁可独立回退。

## Open Questions

无。所有关键决策（升级/H3-patch/detection 方向/Event.wait/canary/count 断言）均经 v3 三专家第二轮审查 + 用户「可以了」批准，已收敛。
