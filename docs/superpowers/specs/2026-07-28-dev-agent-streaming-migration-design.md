# 设计：升级 SDK 0.2.128 + 应用 #1106 定向 patch 根治 #1105

> **日期**：2026-07-28
> **范围**：`Projects/项目推进流水线/scripts/dev-agent.py`（控制面标准执行器，ADR-0006）+ SDK 版本锁 + 配套测试
> **前置**：根因分析 `项目推进/根因分析_dev-agent-SDK通道缺陷_20260727.md`、archive change `2026-07-27-fix-dev-agent-stream-aclose-race`（方案 A）、未实现 change `patch-sdk-can-use-tool-stdin-keepalive`（C3）
> **上游**：[anthropics/claude-agent-sdk-python#1105](https://github.com/anthropics/claude-agent-sdk-python/issues/1105)（OPEN）、[#1106](https://github.com/anthropics/claude-agent-sdk-python/pull/1106)「fix: keep stdin open for can_use_tool permission responses」（OPEN，未合并）
> **修订**：v2（2026-07-28）—— 经 4 方专家并行审核（SDK 核验 / 架构 / Python 实现 / 静默失效），固化 CRITICAL/HIGH 加固与 Event.wait conscious 移除决策。修订日志见文末。

---

## 0. TL;DR

dispatch 阶段 dev-agent 在目标仓 worktree 跑 dev loop，需 `can_use_tool` 审批的 Bash 测试命令（`npm test`/`node --test`）一律 `AbortError: Stream closed` → dev 盲写无法自测 → verify 闸 `test_failed` → fail-safe 阻断开 PR。根因是 SDK `Query.wait_for_result_and_end_input` 的 stdin 保活条件漏了 `can_use_tool`。

本设计：**升级 SDK 0.2.121 → 0.2.128**（解版本锁 + 拿其他修复）+ **本地应用上游 #1106 的对症修复**（保活条件加 `or self.can_use_tool`），带 fail-loud 四态版本守卫、可移除。这是用户选定的根治路径，取代 C3 monkey-patch change（C3 基于 0.2.121、无升级）。

---

## 1. 关键事实核查（2026-07-28 源码级，self-contained）

### 1.1 #1105 真因（已确认）
SDK `_internal/query.py` `wait_for_result_and_end_input()`：
```python
if self.sdk_mcp_servers or self.hooks:   # ← 保活白名单遗漏 can_use_tool
    await self._first_result_event.wait()
await self.transport.end_input()          # ← 生产（无 MCP/hooks）立即关 stdin
```
`can_use_tool` 的 permission response 经 `_handle_control_request`（query.py:483，处理 CLI 入站 `can_use_tool` 请求；`transport.write`）写回**同一条 stdin**。> 注：非 `_send_control_request`（query.py:501，那是 SDK 出站请求如 `initialize`）；两者共用同一 transport 同一 stdin，故结论一致，仅函数名精度修正（SDK 核验 LOW）。生产默认关 lifecycle hooks → `stream_input` 耗尽即触发 `wait_for_result_and_end_input` → `end_input()` 关 stdin → 后续审批写不回 → `AbortError: Stream closed`。首轮 `node -v`（通道未关）能过，后续 `npm test`（通道已关）不过——与实测时序吻合。

### 1.2 升级不解 #1105（修正根因分析的乐观假设）
根因分析 §8.1.2 称「升级 ≥0.2.123 后 can_use_tool 原生支持」。**源码核查证伪**：
- 下载 0.2.128 wheel，`query.py:925` 保活条件仍 `if self.sdk_mcp_servers or self.hooks:` —— **与 0.2.121 一字不差**（SDK 核验逐字比对 CONFIRMED）。
- 0.2.128 的该方法体已被 #1103（后台任务）改过（docstring 提 background tasks/#1088），但**条件仍未加 can_use_tool**。
- 修 #1105 的对症 PR **#1106 OPEN、未合并、无 milestone、0 评论**。#1105 本身 OPEN、2026-07-11 后未动（SDK 核验 CONFIRMED）。
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
外加 docstring 更新 + 107 行回归测试 `test_streaming_prompt_with_can_use_tool_waits_for_result`（mock transport 验证 can_use_tool 保活 stdin）。SDK 核验确认 diff 与本设计复刻 patch **一字不差**。

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

### 1.7 `Query.__init__` 的 `self.can_use_tool` 字段名核验（v2 新增，静默 F-8）
patched body 用 `self.can_use_tool`（Query 实例属性）。须在实现期核验 0.2.128 `Query.__init__` 确把 callback 存到 `self.can_use_tool`（而非 `self._can_use_tool`/`self._permission_callback` 等私有别名）。#1106 diff 本身用 `self.can_use_tool`（上游维护者基于当前源码写），是强证据；但实现期 apply() 须加运行时 `hasattr(query_instance, "can_use_tool")` 探针兜底——若字段名被改，patched 条件 `or self.can_use_tool` 会 `AttributeError`（fail-loud）或恒 `None`（`or None` 短路为假 = silent RED，靠 dev-agent 测试门 exit 14 兜底）。

---

## 2. 方案

升级 SDK 到 0.2.128 + 本地应用 #1106 修复。patch 是上游已写好的对症修复（语义可信，非自创），带 fail-loud 四态版本守卫；#1106 合并后摘除 patch、提版本锁上界（独立 follow-up change）。

**为何不是别的路径**（决策记录）：
- **纯 streaming 迁移**（改调用方式）：治不了 #1105（§1.2、§1.4）。
- **零-patch 深查 Python 保活**：方案 A（prompt_stream Event.wait）真实环境失效机制未明，真因可能在 Node CLI 侧，Python 可能触不到，风险高。
- **绕开 #1105（权限下放目标面 hooks）**：零 patch 但改权限模型，偏离 ADR-0006 #7（控制面单一权限源头），且需保证每仓有 hook。
- **C3 monkey-patch（基于 0.2.121）**：用户选定路径取代它——C3 无升级、拿不到 #1103 等其他修复，且仍卡在版本锁。
- **vendor 本地 SDK fork**：本质等价于 monkey-patch（同样偏离上游），但增加分发/安装/drift 追加成本；对 1 行 keep-alive 条件修复属过度方案（架构评审）。
- **等 #1106 合入再升级**：#1106 OPEN/0 评论/2026-07-11 后未动，无限期阻塞生产，不可接受（架构评审）。

---

## 3. 详细设计

### 3.1 版本锁升级
- `Projects/项目推进流水线/pyproject.toml:26`：`"claude-agent-sdk>=0.2.121,<0.2.123"` → `"claude-agent-sdk>=0.2.128,<0.2.130"`。
  - 上界 `<0.2.130`：允许 0.2.128/0.2.129；若 0.2.129 改了方法体结构，patch 守卫 fail-loud（§3.2），不会静默失效。#1106 合并后 follow-up 放宽上界。
- `pyproject.toml:23-25` 注释 + `CLAUDE.md:79`：把过时叙述（「0.2.123 起 can_use_tool 要求 streaming / string-prompt 冲突」）改成「0.2.128 + 本地 #1106 patch（sdk_compat_patch.py，#1106 合并后移除）」。

### 3.2 #1106 定向 patch（新零依赖模块 `scripts/sdk_compat_patch.py`）
零依赖模块（同 `slug_utils`/`evidence`/`bash_allowlist` 既定模式，单测可零 SDK 导入）。**顶层禁任何 `claude_agent_sdk` import**（M5）；`Query` 在 `apply()` 内部延迟 import，保持「顶部 import 不触 SDK 连带加载、cron 路径隔离」原则。

**`apply()` 机制：H3-patch（AST 变异已安装版本原方法体）+ fail-safe detection**（v3 重构；H2 inline → H3-patch，python v2 裁决 + 三方复核）。

> **为何 H3-patch 而非 H2 inline**：H2（从 0.2.128 wheel 提取整方法体 inline 到 `sdk_compat_patch` 顶层）有 exec 命名空间陷阱——方法体引用 `_inflight_requests`/`_first_result_event`/`logger`/`anyio`/`#1103 helper` 等 query.py 模块级名，但函数定义在 `sdk_compat_patch` 命名空间，exec 时这些名不可见 → `NameError`；且依赖 wheel=安装版本一致。**H3-patch 对已安装版本原方法体做最小 AST 变异（仅 `if.test` BoolOp 末位加 `self.can_use_tool`）→ compile → exec 回原模块命名空间**：#1103 字节级天然保留（原方法体一部分）、模块级名零漂移、不依赖 wheel 文本。复杂度换正确性，非过度（架构 YAGNI 审计认可）。**AST 变异失败（结构不明）→ raise，不退回 inline 兜底**（inline 漂移风险未消，兜底引入新风险）。

**阶段 0 — 幂等 + getsource fail-loud**：

1. **`_APPLIED` + 源码持久 marker 防 reload**（F-6/F-4）：模块级 `_APPLIED`，二次调用看 `True` → log「already-applied (self)」返回。但 `importlib.reload(sdk_compat_patch)` 会重置 `_APPLIED`——故 post-patch 变异源码注册 `linecache.cache` 时打首行 marker `# sdk_compat_patch: APPLIED_MARKER`，apply() 先 getsource 查 marker：有 marker=self-applied（log already-applied，**不依赖进程内 `_APPLIED`**）；无 marker 才进 detection。reload 后仍靠 marker 正确识别 self-applied，不把首次打的 patch 误报为 upstream-fixed。
2. `inspect.getsource(Query.wait_for_result_and_end_input)` 取真实源码；**`getsource` 抛 `OSError`/`TypeError`（pyc-only / zipapp / C-ext 分发）显式归入 fail-loud**（C1）：
   ```python
   try:
       src = inspect.getsource(Query.wait_for_result_and_end_input)
   except (OSError, TypeError) as e:
       raise RuntimeError(
           f"cannot inspect SDK source (pyc-only/zip/C-ext?): {e}; "
           f"refuse to monkey-patch blind. Pin SDK to a source-distributed "
           f"version or remove this patch once upstream #1106 merges."
       ) from e
   ```
3. **AST 精确形态匹配**（F-2 fail-safe 方向）：`ast.parse(src)` 找目标 `If` 节点，按**精确形态**三分——
   - **新形态**（`if.test` BoolOp 已含 `self.can_use_tool`）→ upstream-fixed，skip + log（#1106 merged）；
   - **精确旧形态**（`if.test` == BoolOp(`self.sdk_mcp_servers`, `self.hooks`)，即 `if self.sdk_mcp_servers or self.hooks:`）→ 进入 step 5；
   - **任何其他形态**（can_use_tool 抽 helper / `_skip_keepalive` 短路 / `getattr` 包裹 / `keep=` 间接变量 / 结构不明）或缺锚点 → **`raise RuntimeError`**（refuse blind；锚点集 `sdk_mcp_servers`/`self.hooks`/`end_input`/`_first_result_event`，H3）。
   > **方向修正**（F-2）：v2 曾是「白名单 ast 命中→skip；否则 patch」，方向 **fail-unsafe**——上游重构使 ast miss → patch 覆盖本已正确的方法。v3 翻成「精确旧形态→mutate；任何偏离→raise」，失败方向变 **fail-safe**。`SyntaxError`（getsource 片段畸形，如动态/装饰器方法）→ 降级 substring 兜底 + WARN（仍 raise，F-7）。
4. 缺锚点已并入 step 3 的「任何其他形态 / 缺锚点 → raise」分支（锚点集 `sdk_mcp_servers`/`self.hooks`/`end_input`/`_first_result_event`，H3）。
5. 精确旧形态命中 → 进入阶段 2 mutation + 阶段 3 inject（见 patched body / 注入方式段）。

**阶段 4 — self-check + 运行时探针**：
6. **post-apply identity 自检**（F-9 升级）：`assert Query.wait_for_result_and_end_input is patched`——identity 优于 substring，捕获「`Query.X = patched` 未生效 / 方法搬到 mixin 致 MRO 仍解析旧方法」的 silent no-op；substring `"can_use_tool" in getsource(...)` 仅辅助。
7. **运行时属性探针**（F-8）——**在守卫单测里执行，不在 `apply()` 本身**（N2 单一职责）：fake transport 构造 Query 实例，`hasattr` 断言 `can_use_tool`/`_first_result_event`/`transport` 存在；缺则 raise（防字段改名 silent RED）。`apply()` 不构造 Query 实例，仅做源码静态检查 + 变异 + 注入。

**阶段 2 — mutation + 阶段 3 — inject（H3-patch）**：
- **mutation**：对命中 `If.test` 的 BoolOp 末位 append `or self.can_use_tool`（`ast.fix_missing_locations` + 校验变异后 `if.test` 含 `self.can_use_tool`）；同步在 logger.debug f-string 加 `has_can_use_tool`（#1106 对称，减 diff 漂移）。`ast.unparse(tree)` 得变异源码。
- **inject**：`code = compile(tree, '<sdk_compat_patch>', 'exec')`；`ns = vars(inspect.getmodule(orig))`（**在 Query 模块命名空间 exec**——`_inflight_requests`/`logger`/`anyio`/`#1103 helper` 全部解析，零漂移，规避 H2 的 NameError 陷阱）；`exec(code, ns)`；`patched = ns['wait_for_result_and_end_input']`。
- **identity fixup**（H5）：`patched.__name__`/`__qualname__`/`__module__` 伪装回原名（traceback 定位 + 二次 getsource 可读；idempotency 由阶段 0 marker 保证，非靠 patched 函数模块顶层定义）。
- **linecache 注册 + APPLIED_MARKER**（F-4）：把变异源码（首行 `# sdk_compat_patch: APPLIED_MARKER`）注册进 `linecache.cache`——让 post-patch `inspect.getsource` 返回变异源码（不报 OSError）+ 阶段 0 reload 防护可查 marker。
- **赋值注入**：`Query.wait_for_result_and_end_input = patched  # type: ignore[assignment]`（monkey-patch 第三方内部方法——风险见 §5，靠 detection fail-safe + 测试触发协议 + canary 三重对冲）；`_APPLIED = True`。

> **`apply(query_cls=None)` 可测 API**（python v2 #3 + silent F-5）：`apply()` 接受可选 `query_cls`（默认延迟 import 真实 `Query`），守卫单测传 fake/stub Query 类测 detection/raise 分支——避免 conftest autouse 设 `_APPLIED=True` 后对真实 Query 调 `apply()` 短路假过（python v2 #3 的 conftest 污染）。守卫单测须保存/还原原方法 + 重置 `_APPLIED`（隔离）。`can_use_tool=None` 的 Query 实例语义等价未 patch（M6：`or None` 短路为假），docstring 明示，守卫单测加一态。

### 3.3 dev-agent.py 改动（最小）
- 模块顶部 `import sdk_compat_patch`（零依赖，**不触 SDK**——`sdk_compat_patch` 顶层无 SDK import）。
- `apply()` **在 `main()` 的 SDK try 块内、构造 `ClaudeAgentOptions` / 调 `query()` 前**显式调用。**严禁**用 `try/except` 包 `apply()`（H4/F-4）——apply() 的 `RuntimeError` 必须如实冒泡进 main 的 SDK 异常路径（exit 11），不可被宽 except 吞掉（吞后 patch 没打 + 诊断失真，run_daily 误判为「dev 没跑测试」而非「patch 没打」）。
- `can_use_tool`/`decide_bash` 权限闸不动，admit/deny 语义不变。
- **patch 对 `can_use_tool=None` 的 Query 实例语义等价于未 patch**（M6）：`or None` 短路为假，保活条件退化为原 `sdk_mcp_servers or hooks`。故 `learning_memory_reflection`（无 can_use_tool）即便同进程受 patch 也 no-op；apply() docstring 须明示此性质，守卫单测加一态验证。

### 3.4 prompt_stream.py 清理（Event.wait conscious 移除，v2 决策变更）
> **决策变更**：v1 曾列「清理回归最简」。v2 经审核重新决策为 **conscious 移除**（用户拍板）——采纳架构专家实证论证：RCA §4.2 已证明方案 A（`Event.wait`）在真实 SDK 0.2.121 下 3 轮 dispatch 全 RED，**它作为 patch 失效兜底本就不工作，保留是虚假安全感**。

patch 根治后，`wait_for_result_and_end_input` 因 `can_use_tool` 正确保活 stdin。`prompt_stream` 的 `await asyncio.Event().wait()`（永不返回，方案 A 输入侧 workaround）移除。

改为最简形态（yield 首条 user 消息后正常结束）：
```python
async def prompt_stream(prompt: str) -> AsyncIterator[dict[str, Any]]:
    yield {"type": "user", "session_id": "",
           "message": {"role": "user", "content": prompt},
           "parent_tool_use_id": None}
```
stream 正常耗尽 → `stream_input` 调 `wait_for_result_and_end_input` → 因 patch 的 `or self.can_use_tool` 保活到 result → 不早关 stdin。更新模块 docstring：明述「**accept loss of false hedge**（RCA §4.2 实证 Event.wait 在真实 SDK 下本就不工作，保留是虚假安全感）；对冲 = canary（**CI-required** release gate，F-3 已落强制，§3.5.4）+ H3-patch detection fail-safe（§3.2）+ dev-agent 测试门 fail-closed 主路径（`test_not_run`→exit 14→`blocked_by_gate`）」——**silent 回挑战（v3 吸收）**：Event.wait 移除的前提是「canary + detection 三重对冲」成立，F-3 把 canary 落 CI 强制后此前提名副其实。删方案 A Event.wait 叙述。

**测试改写**（F-10）：现有 `test_prompt_stream_aclose_clean_does_not_raise_already_running`（test_dev_agent_stream_lifespan.py:143-158）验证的是 Event.wait 挂起点上 aclose 不抛——移除 Event.wait 后该测试语义失效（不再有挂起点）。改为「prompt_stream 单 yield 后 `StopAsyncIteration`」回归锁，名字 + docstring 同步改。`test_prompt_stream.py` 的 dict 结构守卫保留。

### 3.5 测试与验收（v2 重写：测试触发协议 + canary=release gate）

**测试触发协议**（C2/F-1/F-2，防假绿的核心）：所有 channel-availability 测试（`test_sdk_query_keeps_stdin_open_until_result`、复刻的 #1106 测试、守卫测试）**必须**经 `sdk_compat_patch.apply()` 真实触发 patch；`scripts/conftest.py` 加 autouse fixture 调一次 `apply()`。**严禁** mock `claude_agent_sdk._internal.query.Query`（mock 让测试与生产路径解耦 = 假绿）。加 anti-mock 断言：`apply()` 返回 patched 引用，测试体 `assert Query.wait_for_result_and_end_input is <apply返回值>`（H3-patch 下 patched 是 exec 在 query 模块命名空间产出，**非** `sdk_compat_patch` 模块顶层属性，故 v2 的 `is sdk_compat_patch.wait_for_result_and_end_input_patched` 断言必然失败——python #5/silent F-6；改用 apply() 返回值 `is` 比对）。identity 断言必须写在**每个** channel-availability 测试体里（非 fixture），保证 conftest 未触发时单测本身 fail（silent F-5）。

**两道独立门**（M1，重排 v1 把 xfail 列首项的误导）：
- **合并门（merge gate）**= xfail 翻转 + 守卫四态单测 + 结构断言（确定性，CI 跑）。
- **发布门（release gate）**= 真实 canary（§3.5.4）。**canary 不过不准 cutover**。mock 层过 ≠ 真实 Node CLI 通道通（Plan A 当初就是 mock 绿、真实 RED 的教训，C3 design §1 引述）——canary 必须在 cutover 前跑过，不接受「先合再 dispatch 自然验证」的空头承诺。

1. **xfail strict 翻转 + 结构断言**（H6）：`test_dev_agent_stream_lifespan.py::test_sdk_query_keeps_stdin_open_until_result` 现为 `xfail(strict)` 锁缺陷 RED。patch 生效 → PASS → strict 下 XPASS fail → **摘 `xfail` 标记**成普通 pass。**补结构断言**（防 XPASS 来自 mock 污染/偶然 pass）：`assert inspect.getsource(Query.wait_for_result_and_end_input).count("or self.can_use_tool") == 1`。**v3 修正**（三方独立确认 CRITICAL）：#1106 diff 使 `self.can_use_tool` 出现 **2 处**——if 条件 + logger f-string `bool(self.can_use_tool)`——故 `count("self.can_use_tool")==1` 永远 RED，会让正确 patch 在合并门假阻断、诱使 implementer 妥协删守卫；锚定 `or self.can_use_tool` 仅一次。这是 xfail-strict-regression-lock 纪律的收口。**硬顺序**：canary（§3.5.4）必先绿，才允许摘 xfail——不是并列项。
2. **复刻 #1106 回归测试**：移植 #1106 的 `test_streaming_prompt_with_can_use_tool_waits_for_result`（真实 patch + mock transport + can_use_tool + 断言「end_input 后写 control response 会失败、保活后能写、permission_calls==["Write"]」）进 pa，作为 channel-availability 的确定性集成测试。对应 `verified-dev-execution` spec 的 Scenario「Bidirectional permission channel remains available」。
3. **守卫四态单测**（`test_sdk_compat_patch.py`，用 fake/stub Query 类，不依赖真实 SDK 内部）：
   - 源码已含 `can_use_tool`（在 if 条件）→ `apply()` skip + log upstream-fixed；
   - `getsource` 抛 `OSError`（造无 `__source__` 的 stub）→ `apply()` raise（C1）；
   - 源码缺锚点 → `apply()` raise；
   - 源码匹配缺陷形态 → `apply()` 打 patch（断言替换后方法源码含 `or self.can_use_tool`，且保留 #1103 标志物如 `_inflight_requests`/`background`，缺失即 fail——H3-patch 对原方法体 ast 变异，#1103 天然保留）。
   - 加 `_APPLIED` 幂等性测试（二次调用 log already-applied，F-6）+ `can_use_tool=None` no-op 测试（M6）。
4. **真实 canary（发布门）**：重跑 cc-web-control `custom-mcp-server-url` / `hub-role-pair-view` dispatch，dev-agent 在 worktree 跑 `npm test` 返回真实 exit status（无 `AbortError: Stream closed`），编排器 verify 闸观测到 dev 能自测。**CI 强制**（F-3）：canary 落为 named CI check（如 `canary-real-node-cli`），列入 branch protection required status check——摘 xfail 的 PR 必须带 canary job run ID；或运行时门（dispatch 前 `sdk_compat_patch.apply()` 后要求 `PA_CANARY_EVIDENCE=<run_id>`，缺失即 exit 14）。**「canary 必先绿才摘 xfail」从文档承诺落为 CI/代码强制**（v2 仅文档承诺 = silent F-3，复刻 Plan A「mock 绿真实 RED」失败模式）。**canary 目标抽象**（N5）：cc-web-control 两 PRD 仅「当前具体实例」；迁移到其他等价目标仓（任何需 can_use_tool Bash 审批的 dispatch）须在 release gate 文档更新。
5. **全量**：`cd Projects/项目推进流水线 && bash scripts/quality.sh` 绿（compileall + pytest + ruff E9+F），既有 1243 passed 不破。

### 3.6 cutover 证据
SDK 升级是运行时依赖变更。shadow 证据 = 真实 canary（§3.5.4，release gate）+ 全量 quality（§3.5.5）；`runtime_evidence.py`/`quality_evidence.py` 产 readiness=True 才算过。learning_memory_reflection/runtime_evidence 升级后跑一次确认无回归。

### 3.7 上游跟踪 + follow-up
- 在 #1105/#1106 留控制面复现评论（string-prompt + can_use_tool + 无 hooks/MCP → Stream closed；源码定位 query.py:819/925），订阅。
- **#1106 合并后的 follow-up（独立 change）**：提版本锁上界 + 移 `sdk_compat_patch` + 摘守卫测试。

### 3.8 openspec（v2：C3 archive + supersession）
- 本设计走 brainstorming→writing-plans 流程产出实现计划，落为新 openspec change（建议 id `migrate-dev-agent-sdk-0.2.128-with-1106-patch`）。
- `verified-dev-execution` spec 的 Requirement「Dev-agent test command executability across the SDK dev loop」**文本无需改**（它约束可观测行为，不约束实现 —— spec.md:57 明示）。
- Scenario 4「Regression locks the executability fix」措辞随实现微调——**delta 写死 before/after**（避免实现期再争论）：
  - before：「real Node-command canary remain deferred to natural dispatch verification」
  - after：「canary is a CI-required release gate run before cutover (named `canary-real-node-cli` check); xfail marker removed only after canary green」
  实现完成后 `/opsx:sync` delta 进 main spec。
- **C3 change（`patch-sdk-can-use-tool-stdin-keepalive`）走 `/opsx:archive`，非 delete**（M2）：保留其根因核验/R2 评审历史，仅在 proposal 顶部加 `> Superseded by migrate-dev-agent-sdk-0.2.128-with-1106-patch (升级 0.2.128 + 本地 #1106 patch 取代本 monkey-patch 方案，2026-07-28)`；C3 未完成 tasks 标 `superseded`。参照既有 archive 案例 `2026-07-27-fix-dev-agent-stream-aclose-race`。

---

## 4. 验收标准（v2 重排：release gate 与 merge gate 分列）

**合并门（merge gate，CI 必过）**：
- [ ] `test_sdk_query_keeps_stdin_open_until_result` 摘 xfail 后普通 pass **且**结构断言（源码含 `or self.can_use_tool` 仅一次）pass。
- [ ] 复刻的 #1106 回归测试 pass（channel-availability 确定性集成证据）。
- [ ] `test_sdk_compat_patch.py` 守卫四态全 pass（含 getsource OSError 态、_APPLIED 幂等、can_use_tool=None no-op）。
- [ ] `scripts/conftest.py` autouse fixture 调 `apply()` + anti-mock 断言就位。
- [ ] `bash scripts/quality.sh` 绿，1243 passed 不破。
- [ ] learning_memory_reflection/runtime_evidence 升级后无回归。
- [ ] pyproject.toml / CLAUDE.md 版本锁 + 叙述更新。

**发布门（release gate，cutover 前必过，不可 deferred）**：
- [ ] 真实 canary：cc-web-control 2 份 PRD dispatch 的 dev-agent 跑 `npm test` 返回真实 exit status，无 Stream closed。
- [ ] **canary 为 CI required status check**（`canary-real-node-cli`），PR 携带 canary job run ID（F-3）——不接受「先合再 dispatch 自然验证」的空头承诺。
- [ ] `runtime_evidence.py`/`quality_evidence.py` readiness=True。

**流程项**：
- [ ] #1105/#1106 已留复现评论 + 订阅。
- [ ] C3 `/opsx:archive` + supersession 注指向新 change id。

---

## 5. 风险与对冲（v2 扩充）

| 风险 | 对冲 |
|---|---|
| monkey-patch 第三方内部方法，SDK 升级/重构可能冲掉 | 四态守卫（getsource OSError/缺锚点/结构不匹配均 raise）+ 测试触发协议（conftest 真触发 apply）+ canary 三重 |
| patched body 回退 #1103 / inline 漂移（H2→H3-patch） | H3-patch 对**已安装**版本原方法体 ast 变异（非 inline），#1103 字节级天然保留、模块级名零漂移；守卫测试断言 #1103 标志物（`_inflight_requests`/`background`）保留 |
| 0.2.129 改方法体结构 | 守卫 raise（fail-loud），版本锁上界 `<0.2.130` 限定已验证范围 |
| substring 锚点误判（结构匹配但语义已变，H3） | state「已含 can_use_tool」升级为 ast 解析（确认在 if 条件 BoolOp 中）；运行时 hasattr 探针 |
| apply() 被上层 try 吞（F-4） | §3.3 明文严禁 try/except 包 apply()；raise 进 main SDK 异常路径 exit 11 |
| apply() 二次调用误报 upstream-fixed（F-6） | `_APPLIED` 模块标志位区分 self-applied vs upstream-fixed |
| xfail 测试结构性无法探测 CI 未生效（F-1，CRITICAL） | 测试触发协议（conftest autouse + 禁 mock + anti-mock 断言）+ canary 必先绿才摘 xfail |
| mock 层过 ≠ 真实通道通 | canary = release gate，cutover 前必跑；不接受 deferred |
| `self.can_use_tool` 字段名被改（F-8） | §1.7 核验 + 运行时 hasattr 探针；恒 None 路径靠 dev-agent 测试门兜底 |
| 方法搬到 mixin致 silent no-op（F-9） | apply 后自检 `assert "can_use_tool" in getsource(...)` |
| **Event.wait 移除 = defense-in-depth 丧失（conscious）** | accept loss of **false** hedge（RCA §4.2 实证 Event.wait 真实不工作）；对冲 = canary（**CI-required**，F-3 已落强制）+ H3-patch detection fail-safe（§3.2）+ fail-closed 主路径。silent 回挑战（v3 吸收）：canary 落 CI 强制后，Event.wait 移除的三重对冲前提名副其实 |
| 不弱化权限闸 | patch 只改 keep-alive 条件，`can_use_tool`→`decide_bash` admit/deny 链不动；patch 上线后既有 decide_bash 边界被真实使用，建议同步复审 `bash_allowlist`（静默权限核验） |
| 升级引入未知回归 | breaking 核查（§1.5）已静态确认无 breaking + canary + 全量 quality 实测 |
| dry-run JSON 报 ok:True 不带 evidence（F-7，既有 bug 本设计放大） | 不在本 change 范围强求修，记 §6 follow-up |
| 守卫 raise 的 fail-closed 报告可见性 | apply() raise → dev-agent exit 11 → dispatch 报告标红，人介入；不会静默吞 |
| cron 迁移期空跑烧 token | 用户决策：不降噪，连续推进尽快落地 |

---

## 6. 范围外 / follow-up

- **#1106 合并后**：提版本锁上界、移 `sdk_compat_patch.py`、摘守卫测试（独立 change）。
- **dry-run JSON 区分 ok-with-evidence vs ok-without-evidence**（F-7）：本设计放大该既有风险，记独立 follow-up。
- **`bash_allowlist` 边界复审**：patch 上线后，之前因 stdin 关闭而 abort 的 Bash 命令现在能真正执行，decide_bash 既有黑白名单边界被真实使用，建议同步审一遍（静默权限核验）。
- **dev loop hung 机制（方案 B 开 lifecycle_hooks）**：本 change 不解（开 hooks 非 baseline），留作 SDK 行为待查项。
- **max_turns/max_budget_usd 被 SDK 绕过**（ADR-0006 #6 follow-up）：本 change 不碰。

---

## 7. 决策记录（v2 更新）

| 决策点 | 选择 | 理由 |
|---|---|---|
| 战略定位 | 直接 streaming 迁移，跳过 C3 止血 | 用户判定大工程值得根治，不愿引入 monkey-patch 中间补丁 |
| 根治路径 | 升级 0.2.128 + #1106 定向 patch | 源码核查证明纯 streaming 治不了 #1105（#1106 未合）；升级拿其他修复 + 复刻上游对症 patch 最务实 |
| prompt_stream | **conscious 移除 Event.wait，accept loss of defense-in-depth**（v2 变更） | 4 方审核重新决策：RCA §4.2 实证 Plan A（Event.wait）在真实 SDK 下 3 轮 dispatch 全 RED，作 patch 失效兜底本就不工作、是虚假安全感；对冲 = canary（release gate）+ 四态守卫 + fail-closed 主路径。v1「清理回归最简」的乐观叙述被推翻 |
| 守卫形态 | H3-patch（ast 变异原方法体）+ detection fail-safe（精确旧形态→mutate，偏离→raise，F-2）+ identity 自检 + 运行时探针 + APPLIED_MARKER 防 reload | 防 patch 静默失效/假绿/漂移的 design-level 加固（v2 三方迭代审核 CRITICAL/HIGH） |
| 测试门 | 合并门（xfail+守卫+结构断言 `count("or self.can_use_tool")==1`）与发布门（canary **CI-required**）分列 | mock 层过 ≠ 真实通道通（Plan A 教训）；canary 不可 deferred 且落 CI 强制（silent F-3） |
| C3 收尾 | `/opsx:archive` + supersession，非 delete | 保留根因核验/R2 评审历史，供本设计追溯（架构审核） |
| 迁移期 cron | 不降噪，尽快落地 | 连续推进周期短，空跑成本可控，无需记得恢复跳过 |

---

## 修订日志

### v3（2026-07-28）— v2 迭代审核修订（architect / python-reviewer / silent-failure-hunter 三方）

三方对 v2 做迭代对抗审核（全 APPROVE-WITH-CONCERNS），核心修订：
- **CRITICAL（三方交叉确认）**：H6 结构断言 `count("self.can_use_tool")==1` 永远 RED（#1106 diff 使 `self.can_use_tool` 出现 **2 处**：if 条件 + logger f-string `bool(self.can_use_tool)`），照落首个 PR 必撞红、诱使妥协删守卫 → 改 `count("or self.can_use_tool")==1`（§3.5 验收项 1 + §4 合并门）。
- **patch 机制 H2 inline → H3-patch**（python 裁决，三方复核，§3.2）：H2 有 exec 命名空间陷阱（`_inflight_requests`/`logger` 等 query.py 模块名在 `sdk_compat_patch` 命名空间不可见 → `NameError`）+ 依赖 wheel=安装版本。H3-patch 对**已安装**版本原方法体做最小 ast 变异（if.test BoolOp 末位加 `self.can_use_tool`）→ compile → exec 回原模块命名空间：#1103 字节级天然保留、零漂移。ast 变异失败 → raise，不退回 inline 兜底。
- **ast detection 方向 fail-unsafe → fail-safe**（silent F-2，§3.2 step 3）：v2「白名单命中→skip，否则 patch」会被上游重构 ast miss → patch 覆盖正确方法；v3「精确旧形态→mutate，任何偏离→raise」。
- **canary 落 CI required status check**（silent F-3，§3.5.4 + §4 + §3.8）：v2「canary 必先绿才摘 xfail」仅文档承诺，复刻 Plan A 失败模式；v3 落 `canary-real-node-cli` named check + PR 携 run ID / 运行时 `PA_CANARY_EVIDENCE` 门。吸收 silent 对 Event.wait 移除的回挑战（canary 强制后三重对冲名副其实）。
- **F-4 reload 防护**（§3.2 step 1）：`_APPLIED` 被 `importlib.reload` 重置 → patched 源码加 `APPLIED_MARKER` 持久注释 + 注册 linecache，apply() 查 marker 识别 self-applied，不误报 upstream-fixed。
- **apply(query_cls=) 可测 API + N2 探针位置 + F-9 identity 自检**（§3.2）：守卫单测传 fake Query 类 + 保存/还原原方法（避 conftest `_APPLIED` 污染假过）；hasattr 探针在测试不在 apply()（单一职责）；post-apply identity 自检优于 substring。
- **架构 N2-N6**（§3.2/§3.5/§5）：四态/七步术语映射、fake vs mock 术语澄清（N3「禁 mock」特指 `unittest.mock.patch` 替换 Query 类，mock transport/fake-stub 类不在此列）、测试数去硬编码（N4）、canary 目标抽象（N5）、conftest autouse SDK 硬依赖加 ImportError skip 守护（N6）——记实现期注意。
- 战略层不变：升级 0.2.128 + #1106 对症 patch 路径、Event.wait conscious 移除、C3 archive——三方认可，仅 patch 机制与守卫工程细节迭代加固。

### v2（2026-07-28）— 4 方专家审核修订
经 SDK 核验 / 架构 / Python 实现 / 静默失效 4 位专家并行审核（APPROVE-WITH-CONCERNS），固化：
- **CRITICAL**：C1 四态守卫覆盖 getsource OSError（§3.2）；C2 测试触发协议防假绿（§3.5）。
- **HIGH**：H1 §3.2/§3.3 apply 时序矛盾解除（main 显式）；H2 patched body inline 完整 0.2.128 方法体（防回退 #1103）；H3 ast 解析 + hasattr 探针；H4 严禁 try 包 apply；H5 模块顶层 patched 函数 + `_APPLIED`；H6 xfail 补结构断言。
- **MEDIUM**：M1 canary=release gate；M2 C3 archive+supersession；M3 §1.7 字段名核验；M4 dry-run follow-up；M5 零 SDK 顶层 import；M6 can_use_tool=None no-op 声明。
- **决策变更**：Event.wait 由「清理回归最简」改为「conscious 移除，accept loss of defense-in-depth」（用户拍板，采纳架构实证论证）。
- **LOW**：§1.1 函数名精度修正；patched 方法 `__name__`/`__qualname__` 伪装；§5 风险表扩充；F-10 测试改写；权限闸边界复审。
- 战略层不变：3 条核心论断源码级 CONFIRMED，升级+对症 patch 路径三方认可，fail-safe 主路径未被弱化。
