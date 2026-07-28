# Tasks — migrate-dev-agent-streaming-with-1106-patch

> 可执行步骤的完整代码与命令见 `docs/superpowers/plans/2026-07-28-dev-agent-streaming-migration.md`（plan Task 1–9 与本文件分组对应）。本文件为 openspec apply 阶段的进度跟踪。

## 1. 版本锁升级

- [x] 1.1 `pyproject.toml` 版本锁 `>=0.2.121,<0.2.123` → `>=0.2.128,<0.2.130` + 更新注释（0.2.128 的 `wait_for_result_and_end_input` 保活条件仍遗漏 `can_use_tool`，与 0.2.121 一字不差，故需本地 patch；#1106 合并后移除 patch 并放宽上界）
- [x] 1.2 `pip install -e ".[dev]"` 装 0.2.128；确认 `test_sdk_query_keeps_stdin_open_until_result` 仍 `xfail(strict)`（证实 spec §1.2「升级不解 #1105」）

## 2. sdk_compat_patch H3-patch 核心

- [x] 2.1 新建零依赖 `scripts/sdk_compat_patch.py`：`apply(query_cls=None)` 四阶段——(0) `inspect.getsource` fail-loud + marker 去重；(1) ast detection 四态分流（getsource 失败→raise / upstream-fixed 已含 `can_use_tool`→skip / 精确旧形态 `self.sdk_mcp_servers or self.hooks`→mutate / 其他形态→raise，fail-safe 方向）；(2) BoolOp 末位 append `self.can_use_tool`；(3) `compile` + `exec` 回原模块命名空间（零漂移、#1103 字节级保留）+ identity fixup + linecache marker；(4) identity 自检
- [x] 2.2 `apply` 返回 patched 引用供测试 identity 比对；`can_use_tool=None` 的 Query 实例语义等价未 patch（`or None` 短路）
- [x] 2.3 新建 `test_sdk_compat_patch.py`：defect-form 测试（apply 打 patch，`count("or self.can_use_tool")==1`，返回值 `is` 注入后方法）+ `_isolate_global_state` fixture（`monkeypatch` 隔离 `_APPLIED`/`_last_patched`，teardown 还原 session fixture 对真实 Query 的 patch，避免跨文件假红）

## 3. 守卫四态 + 注入 + prompt_stream 简化

- [x] 3.1 `test_sdk_compat_patch.py` 加守卫四态：`_FixedQuery`（upstream-fixed skip）/ getsource OSError→raise / `_NoAnchorQuery`（缺锚点→raise）/ `_RefactoredQuery`（抽变量等其他形态→raise）+ 幂等（二次 apply 同一引用，不叠加变异）+ identity 返回值
- [x] 3.2 `conftest.py` 加 session-scoped autouse fixture 调 `apply()`（严禁 try/except 包 apply；ImportError skip 防 lint-only CI 矩阵破）；确认 `test_sdk_query_keeps_stdin_open_until_result` 由 `xfailed` → `XPASS`（strict fail）
- [x] 3.3 `dev-agent.py` `main()` SDK try 块内、`options` 构造前调 `sdk_compat_patch.apply()`（严禁 try/except，RuntimeError 冒泡进 exit 11；不可吞，否则 patch 没打 + 诊断失真）
- [x] 3.4 `prompt_stream.py` 移除 `await asyncio.Event().wait()` 回归单 yield + 更新 docstring（conscious accept false-hedge loss；对冲交 canary + detection + 测试门）；改写 `test_prompt_stream_aclose_clean...` 为 `test_prompt_stream_single_yield_then_stopasynciteration`

## 4. 验证（xfail 翻转 + canary 发布门 + 全量）

- [x] 4.1 摘 `test_sdk_query_keeps_stdin_open_until_result` 的 `xfail(strict)` + 加结构断言 `count("or self.can_use_tool")==1` + identity `Query.X is sdk_compat_patch._last_patched`；确认普通 PASS（非 xfailed/XPASS）
- [x] 4.2 全量 `bash scripts/quality.sh` 绿（compileall + pytest + ruff E9+F），既有测试不破
- [x] 4.3 新增 `.github/workflows/canary-real-node-cli.yml`（`workflow_dispatch`，真实 dispatch cc-web-control 两 PRD，grep 断言无 `AbortError: Stream closed`）+ `RUNBOOK.md` 加 cutover 证据流程；branch protection 列为 required check

## 5. supersede C3 + 上游跟踪 + 规约同步

- [x] 5.1 `patch-sdk-can-use-tool-stdin-keepalive/proposal.md` 标 `Superseded by migrate-dev-agent-streaming-with-1106-patch`，未完成 tasks 标 `superseded`
- [ ] 5.2 在 #1105 留控制面复现评论（string-prompt + `can_use_tool` + 无 hooks/MCP → Stream closed；源码定位 `query.py wait_for_result_and_end_input`），订阅（**outward-facing，需用户确认后执行**）
- [ ] 5.3 评审通过后 `/opsx:sync` delta → main spec（MODIFIED `verified-dev-execution` Scenario 4 + 新增 Scenario 5 canary 发布门）
- [ ] 5.4 `/opsx:archive`
- [ ] 5.5 follow-up（独立 change）：#1106 合并后移除 `sdk_compat_patch.py` + 放宽版本锁上界 + 原生 streaming 迁移（ADR-0006 #7）
