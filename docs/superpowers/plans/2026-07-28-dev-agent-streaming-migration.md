# dev-agent SDK 0.2.128 + #1106 H3-patch 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 升级 claude-agent-sdk 到 0.2.128 并本地应用上游 #1106 的 keep-alive 修复（H3-patch：ast 变异已安装版本原方法体），根治 #1105（dev-agent can_use_tool 通道 AbortError: Stream closed）。

**Architecture:** 新增零依赖模块 `sdk_compat_patch.py`，其 `apply()` 对 `Query.wait_for_result_and_end_input` 做最小 ast 变异（if.test BoolOp 末位加 `self.can_use_tool`）→ compile → exec 回原模块命名空间（零漂移、#1103 天然保留），带 fail-safe 四态 detection（精确旧形态→mutate，任何偏离→raise）。dev-agent `main()` 在构造 options 前显式调 `apply()`。prompt_stream 移除 Event.wait 回归单 yield。canary 落 CI required check 作发布门。

**Tech Stack:** Python ≥3.11、pytest、ruff（E9+F）、claude-agent-sdk 0.2.128、ast/inspect/linecache 标准库。

**Spec:** `docs/superpowers/specs/2026-07-28-dev-agent-streaming-migration-design.md`（v3，已提交 3bf953e）

**质量命令（单一真理源）：** `cd Projects/项目推进流水线 && bash scripts/quality.sh`

---

## File Structure

| 文件 | 责任 | 动作 |
|---|---|---|
| `Projects/项目推进流水线/pyproject.toml` | SDK 版本锁 | 修改 line 23-26 |
| `Projects/项目推进流水线/scripts/sdk_compat_patch.py` | H3-patch apply()（detection/mutation/inject/self-check） | 新建 |
| `Projects/项目推进流水线/scripts/test_sdk_compat_patch.py` | 守卫四态 + 幂等单测 | 新建 |
| `Projects/项目推进流水线/scripts/conftest.py` | autouse fixture 调 apply() | 修改（加 fixture） |
| `Projects/项目推进流水线/scripts/dev-agent.py` | main() 注入 apply() | 修改 line 440-441 |
| `Projects/项目推进流水线/scripts/prompt_stream.py` | 移除 Event.wait、单 yield | 修改 line 25-45 |
| `Projects/项目推进流水线/scripts/test_dev_agent_stream_lifespan.py` | 摘 xfail + 结构断言 + aclose 测试改写 | 修改 |
| `.github/workflows/canary-real-node-cli.yml` | canary 发布门 CI check | 新建 |

---

## Task 1: 升级 SDK 版本锁

**Files:**
- Modify: `Projects/项目推进流水线/pyproject.toml:22-26`

- [ ] **Step 1: 改版本锁 + 注释**

把 `pyproject.toml:22-26` 的依赖块改为：
```toml
    # dev-agent.py 控制面标准执行器（ADR-0006）：在目标仓 worktree 内驱动 SDK dev loop。
    # 0.2.128 + 本地 sdk_compat_patch.py（#1106 keep-alive 定向 patch）：0.2.128 的
    # wait_for_result_and_end_input 保活条件 `sdk_mcp_servers or hooks` 仍遗漏 can_use_tool
    # （与 0.2.121 一字不差），上游 PR #1106 OPEN 未合，故本地 ast 变异补丁。#1106 合并后
    # 移除 sdk_compat_patch.py 并放宽上界（独立 follow-up change）。
    "claude-agent-sdk>=0.2.128,<0.2.130",
```

- [ ] **Step 2: 装新版 SDK**

Run: `cd Projects/项目推进流水线 && pip install -e ".[dev]" 2>&1 | tail -5`
Expected: 安装 claude-agent-sdk 0.2.128（若已装 0.2.121 会升级）。

- [ ] **Step 3: 确认现有测试仍 RED（xfail baseline）**

Run: `cd Projects/项目推进流水线 && python -m pytest scripts/test_dev_agent_stream_lifespan.py -q`
Expected: `test_sdk_query_keeps_stdin_open_until_result` 仍 **xfailed**（0.2.128 未修 #1105，证实 spec §1.2「升级不解 #1105」）。

- [ ] **Step 4: 提交**

```bash
cd Projects/项目推进流水线
git add pyproject.toml
git commit -m "chore(pa): 升级 claude-agent-sdk 0.2.121→0.2.128（解版本锁；#1105 仍需 #1106 patch）"
```

---

## Task 2: sdk_compat_patch.py — H3-patch apply() 核心

**Files:**
- Create: `Projects/项目推进流水线/scripts/sdk_compat_patch.py`
- Test: `Projects/项目推进流水线/scripts/test_sdk_compat_patch.py`

- [ ] **Step 1: 写失败测试（defect-form：apply 打 patch）**

创建 `scripts/test_sdk_compat_patch.py`：
```python
"""sdk_compat_patch.apply() 守卫四态 + H3-patch 变异单测（spec §3.5.3）。

用 fake/stub Query 类（非 unittest.mock）测 detection/raise 分支——不与生产路径解耦。
"""
import inspect

import pytest

import sdk_compat_patch


class _DefectQuery:
    """模拟 SDK 缺陷形态：if 条件无 can_use_tool（精确旧形态）。"""
    sdk_mcp_servers = None
    hooks = None
    transport = None
    _first_result_event = None

    async def wait_for_result_and_end_input(self) -> None:
        if self.sdk_mcp_servers or self.hooks:
            await self._first_result_event.wait()
        await self.transport.end_input()


_DEFECT_ORIG_SRC = """\
async def wait_for_result_and_end_input(self) -> None:
    if self.sdk_mcp_servers or self.hooks:
        await self._first_result_event.wait()
    await self.transport.end_input()
"""


@pytest.fixture(autouse=True)
def _isolate_global_state(monkeypatch):
    """隔离 sdk_compat_patch 全局状态 + 还原 _DefectQuery 原版（跨文件耦合点）。

    _APPLIED/_last_patched 用 monkeypatch——teardown 自动还原到 conftest session fixture
    对真实 Query 的 patch 值，避免 test_dev_agent_stream_lifespan 的 ``Query.X is
    _last_patched`` 断言因 _last_patched 被本文件清空而假红。_DefectQuery.X 直接赋值
    原版（setup + teardown 都设），确保每个测试看到未 mutate 的缺陷形态。
    """
    ns: dict = {}
    exec(_DEFECT_ORIG_SRC, ns)
    _DefectQuery.wait_for_result_and_end_input = ns["wait_for_result_and_end_input"]
    monkeypatch.setattr(sdk_compat_patch, "_APPLIED", False)
    monkeypatch.setattr(sdk_compat_patch, "_last_patched", None)
    yield
    ns2: dict = {}
    exec(_DEFECT_ORIG_SRC, ns2)
    _DefectQuery.wait_for_result_and_end_input = ns2["wait_for_result_and_end_input"]


def test_apply_defect_form_patches_boolop():
    """state 4：精确旧形态 → mutate，patched 源码含 `or self.can_use_tool` 仅一次。"""
    patched = sdk_compat_patch.apply(query_cls=_DefectQuery)
    src = inspect.getsource(_DefectQuery.wait_for_result_and_end_input)
    assert patched is _DefectQuery.wait_for_result_and_end_input, "apply 返回值须 = 注入后的方法（identity）"
    assert src.count("or self.can_use_tool") == 1, f"keep-alive 条件未正确变异:\n{src}"
```

- [ ] **Step 2: 运行确认失败**

Run: `cd Projects/项目推进流水线 && python -m pytest scripts/test_sdk_compat_patch.py::test_apply_defect_form_patches_boolop -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'sdk_compat_patch'`。

- [ ] **Step 3: 实现 sdk_compat_patch.py（完整 H3-patch）**

创建 `scripts/sdk_compat_patch.py`：
```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sdk_compat_patch.py — 本地应用上游 #1106 keep-alive 修复（ADR-0006 #7，根治 #1105）。

why
---
SDK ``Query.wait_for_result_and_end_input`` 的 stdin 保活条件
``if self.sdk_mcp_servers or self.hooks:`` 遗漏 ``can_use_tool``。生产（无 MCP/hooks）
下 stream_input 耗尽即 ``end_input()`` 关 stdin → 后续 can_use_tool 权限响应写不回 →
``AbortError: Stream closed`` → dev-agent 无法跑测试 → verify 闸 test_failed。

上游 PR #1106（OPEN 未合）修此缺陷。本模块对**已安装**版本原方法体做最小 ast 变异
（if.test BoolOp 末位加 ``self.can_use_tool``）→ compile → exec 回原模块命名空间：零漂移、
#1103 逻辑字节级保留、模块级名（logger/_first_result_event 等）零漂移。#1106 合并后
摘除本模块（独立 follow-up change）。

零依赖模块（同 slug_utils/evidence/bash_allowlist）→ 顶层无 claude_agent_sdk import，
Query 在 apply() 内延迟 import，保持 cron 路径隔离。
"""
from __future__ import annotations

import ast
import inspect
import linecache
import textwrap
from typing import Any

_APPLIED: bool = False
_last_patched: Any = None

_MARKER = "# sdk_compat_patch: APPLIED_MARKER"
_FAKE_FILE = "<sdk_compat_patch:wait_for_result_and_end_input>"


def _find_keepalive_if(func: ast.AST) -> ast.If | None:
    """在方法体里找 if.test 为 BoolOp 的 If 节点（keep-alive 条件）。"""
    for node in ast.walk(func):
        if isinstance(node, ast.If) and isinstance(node.test, ast.BoolOp):
            return node
    return None


def apply(query_cls: Any = None) -> Any:
    """对 ``Query.wait_for_result_and_end_input`` 应用 #1106 keep-alive patch（H3-patch）。

    对已安装版本原方法体 ast 变异 if.test BoolOp 末位加 ``self.can_use_tool``，compile + exec
    回原模块命名空间（零漂移、#1103 天然保留）。返回 patched 函数引用（供测试 identity 比对）。

    失败方向 fail-safe：getsource 失败 / 结构不明 / 缺锚点 → raise RuntimeError（fail-loud，
    dev-agent exit 11 → dispatch 报告标红 → 人介入），绝不盲打。

    ``can_use_tool=None`` 的 Query 实例语义等价未 patch（``or None`` 短路为假，保活退化为原条件）。
    """
    global _APPLIED, _last_patched
    if _APPLIED:
        return _last_patched  # already-applied (self)

    if query_cls is None:
        from claude_agent_sdk._internal.query import Query  # 延迟 import，保持 cron 路径隔离
        query_cls = Query
    target = query_cls
    orig = target.wait_for_result_and_end_input

    # 阶段0: getsource fail-loud (C1)
    try:
        src = inspect.getsource(orig)
    except (OSError, TypeError) as e:
        raise RuntimeError(
            f"cannot inspect SDK source (pyc-only/zip/C-ext?): {e}; "
            f"refuse to monkey-patch blind. Pin SDK to a source-distributed "
            f"version or remove this patch once upstream #1106 merges."
        ) from e
    src = textwrap.dedent(src)

    # marker (F-4): 已被本模块 patch 过（reload 重置 _APPLIED 后靠 marker 识别 self-applied）
    if _MARKER in src:
        _APPLIED = True
        _last_patched = orig
        return orig

    # 阶段1: detection (F-2 fail-safe 方向)
    tree = ast.parse(src)  # getsource+dedent 后为合法顶层 async def
    func = next(
        (n for n in tree.body if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))), None)
    if func is None:
        raise RuntimeError("cannot find function def in SDK source; refuse to patch blind")
    target_if = _find_keepalive_if(func)
    if target_if is None or not isinstance(target_if.test, ast.BoolOp):
        raise RuntimeError("cannot locate keep-alive If/BoolOp; refuse to patch blind")

    members = [ast.unparse(v) for v in target_if.test.values]
    member_text = " ".join(members)
    for anchor in ("self.sdk_mcp_servers", "self.hooks"):  # 锚点校验 (H3)
        if anchor not in member_text:
            raise RuntimeError(f"missing anchor {anchor!r} in keep-alive BoolOp; refuse blind")

    if any("self.can_use_tool" in m for m in members):
        # 新形态：上游已修（#1106 merged）→ upstream-fixed skip
        _APPLIED = True
        _last_patched = orig
        return orig
    if not (len(members) == 2
            and members[0].strip() == "self.sdk_mcp_servers"
            and members[1].strip() == "self.hooks"):
        # 任何其他形态（抽 helper/_skip_keepalive/getattr/间接变量）→ refuse (F-2 fail-safe)
        raise RuntimeError(
            f"keep-alive BoolOp not precise old form {members!r}; "
            f"SDK may have refactored — refuse to patch blind")

    # 阶段2: mutation — BoolOp 末位 append self.can_use_tool（最小变异）
    target_if.test.values.append(
        ast.Attribute(value=ast.Name(id="self", ctx=ast.Load()),
                      attr="can_use_tool", ctx=ast.Load()))
    ast.fix_missing_locations(tree)
    mutated_src = _MARKER + "\n" + ast.unparse(tree)

    # 阶段3: inject — compile + exec 回原模块命名空间（零漂移，规避 H2 的 NameError 陷阱）
    code = compile(tree, _FAKE_FILE, "exec")
    module = inspect.getmodule(orig)
    if module is None:
        raise RuntimeError("cannot resolve SDK module for exec namespace; refuse blind")
    ns = vars(module)
    exec(code, ns)
    patched = ns["wait_for_result_and_end_input"]

    # identity fixup (H5)：traceback 定位 + 二次 getsource 可读
    patched.__name__ = orig.__name__
    patched.__qualname__ = getattr(orig, "__qualname__", orig.__name__)
    patched.__module__ = getattr(orig, "__module__", "")

    # linecache 注册 (F-4 marker)：让 post-patch getsource 返回变异源码（不报 OSError）
    lines = mutated_src.splitlines(keepends=True)
    linecache.cache[_FAKE_FILE] = (len(mutated_src), None, lines, mutated_src)

    # 赋值注入
    target.wait_for_result_and_end_input = patched  # type: ignore[assignment]
    _APPLIED = True
    _last_patched = patched

    # 阶段4: identity 自检 (F-9)
    assert target.wait_for_result_and_end_input is patched, "patch assignment did not take effect"
    return patched
```

- [ ] **Step 4: 运行确认通过**

Run: `cd Projects/项目推进流水线 && python -m pytest scripts/test_sdk_compat_patch.py::test_apply_defect_form_patches_boolop -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
cd Projects/项目推进流水线
git add scripts/sdk_compat_patch.py scripts/test_sdk_compat_patch.py
git commit -m "feat(pa): sdk_compat_patch.apply() H3-patch——ast 变异 keep-alive 条件根治 #1105"
```

---

## Task 3: 守卫四态 + 幂等单测

**Files:**
- Modify: `Projects/项目推进流水线/scripts/test_sdk_compat_patch.py`

- [ ] **Step 1: 加 fake Query 变体 + 四态测试**

在 `test_sdk_compat_patch.py` 顶部 `_DefectQuery` 后追加：
```python
class _FixedQuery:
    """新形态：上游已修（if 含 can_use_tool）→ apply skip。"""
    async def wait_for_result_and_end_input(self) -> None:
        if self.sdk_mcp_servers or self.hooks or self.can_use_tool:
            await self._first_result_event.wait()
        await self.transport.end_input()


class _NoAnchorQuery:
    """缺锚点（无 sdk_mcp_servers）→ apply raise。"""
    async def wait_for_result_and_end_input(self) -> None:
        if self.something_else or self.hooks:
            await self._first_result_event.wait()
        await self.transport.end_input()


class _RefactoredQuery:
    """其他形态（can_use_tool 抽到变量）→ apply raise（F-2 fail-safe）。"""
    async def wait_for_result_and_end_input(self) -> None:
        keep = self.can_use_tool
        if self.sdk_mcp_servers or self.hooks or keep:
            await self._first_result_event.wait()
        await self.transport.end_input()


def test_apply_upstream_fixed_skips():
    """state 1：if 已含 can_use_tool → skip，方法不变。"""
    before = _FixedQuery.wait_for_result_and_end_input
    returned = sdk_compat_patch.apply(query_cls=_FixedQuery)
    assert returned is before, "upstream-fixed 应返回原方法（不 mutate）"
    assert _FixedQuery.wait_for_result_and_end_input is before


def test_apply_getsource_oserror_raises(monkeypatch):
    """state 2：getsource 抛 OSError（pyc-only）→ apply raise RuntimeError（C1）。"""

    def _boom(_obj):
        raise OSError("could not get source")

    monkeypatch.setattr(sdk_compat_patch.inspect, "getsource", _boom)
    with pytest.raises(RuntimeError, match="cannot inspect SDK source"):
        sdk_compat_patch.apply(query_cls=_DefectQuery)


def test_apply_missing_anchor_raises():
    """state 3：缺锚点 → apply raise（fail-loud）。"""
    with pytest.raises(RuntimeError, match="missing anchor"):
        sdk_compat_patch.apply(query_cls=_NoAnchorQuery)


def test_apply_refactored_form_raises():
    """F-2：can_use_tool 抽到变量（非精确旧形态、非新形态）→ raise（fail-safe）。"""
    with pytest.raises(RuntimeError, match="not precise old form"):
        sdk_compat_patch.apply(query_cls=_RefactoredQuery)


def test_apply_idempotent():
    """F-6/F-4：二次调用返回同一 patched 引用，不重复 mutate。"""
    first = sdk_compat_patch.apply(query_cls=_DefectQuery)
    second = sdk_compat_patch.apply(query_cls=_DefectQuery)
    assert first is second
    src = inspect.getsource(_DefectQuery.wait_for_result_and_end_input)
    assert src.count("or self.can_use_tool") == 1, "二次调用不应叠加变异"


def test_apply_returns_patched_for_identity_assert():
    """apply 返回 patched 引用 → 测试体可 `assert Query.X is apply()`（anti-mock，适配 H3-patch）。"""
    patched = sdk_compat_patch.apply(query_cls=_DefectQuery)
    assert patched is _DefectQuery.wait_for_result_and_end_input
```

- [ ] **Step 2: 运行确认通过**

Run: `cd Projects/项目推进流水线 && python -m pytest scripts/test_sdk_compat_patch.py -q`
Expected: 全部 PASS（6 测试）。

- [ ] **Step 3: 提交**

```bash
cd Projects/项目推进流水线
git add scripts/test_sdk_compat_patch.py
git commit -m "test(pa): sdk_compat_patch 守卫四态 + 幂等 + identity 单测"
```

---

## Task 4: conftest.py autouse fixture

**Files:**
- Modify: `Projects/项目推进流水线/scripts/conftest.py`

- [ ] **Step 1: 加 session-scoped autouse fixture 调 apply()**

在 `conftest.py` 末尾（`stub_externals` fixture 后）追加：
```python
@pytest.fixture(autouse=True, scope="session")
def _apply_sdk_compat_patch():
    """session 级 apply() 一次：让所有 channel-availability 测试跑在 patched SDK 上（C2）。

    严禁 try/except 包 apply()（spec §3.3）——RuntimeError 必须冒泡（patch 没打 = 测试 RED，
    非「dev 没跑测试」）。SDK 缺失（ImportError）跳过，避免 lint-only CI 矩阵破（N6）。
    """
    try:
        import sdk_compat_patch
        sdk_compat_patch.apply()
    except ImportError:
        pytest.skip("claude_agent_sdk not installed; sdk_compat_patch unavailable")
```

- [ ] **Step 2: 运行确认 apply 在真实 Query 上生效**

Run: `cd Projects/项目推进流水线 && python -m pytest scripts/test_dev_agent_stream_lifespan.py -q`
Expected: `test_sdk_query_keeps_stdin_open_until_result` 从 **xfailed** 变 **XPASS**（strict 下 fail）—— 证实 patch 在真实 Query 上生效。其余测试 PASS。

- [ ] **Step 3: 提交**

```bash
cd Projects/项目推进流水线
git add scripts/conftest.py
git commit -m "test(pa): conftest session autouse fixture 调 sdk_compat_patch.apply()"
```

---

## Task 5: dev-agent.py 注入 apply()

**Files:**
- Modify: `Projects/项目推进流水线/scripts/dev-agent.py:440-441`

- [ ] **Step 1: 在 SDK try 块内、options 构造前调 apply()**

在 `dev-agent.py` 顶部 import 区（与 `prompt_stream` import 同区）加：
```python
import sdk_compat_patch  # 零依赖，顶层不触 SDK；ADR-0006 #7 根治 #1105
```

把 `main()` 中 `try:` 后第一行（line 440 `try:` 之后、line 441 `options = ClaudeAgentOptions(` 之前）插入：
```python
    try:
        # ADR-0006 #7：应用 #1106 keep-alive patch（根治 can_use_tool 通道 AbortError）。
        # 严禁 try/except 包 apply()（H4/F-4）——RuntimeError 必须冒泡进下方 except（exit 11），
        # 不可被吞（吞后 patch 没打 + 诊断失真，run_daily 误判「dev 没跑测试」非「patch 没打」）。
        sdk_compat_patch.apply()
        options = ClaudeAgentOptions(
```
（即 `sdk_compat_patch.apply()` 紧跟 `try:`，在 `options = ClaudeAgentOptions(` 前。）

- [ ] **Step 2: 确认 apply 不破坏现有 dispatch 单测**

Run: `cd Projects/项目推进流水线 && python -m pytest scripts/test_dispatch_skip_projects.py scripts/test_dev_agent_stream_lifespan.py -q`
Expected: 全 PASS（dispatch 单测用 stub_externals，apply 在真实 Query 上 no-op 于 stub 路径）。

- [ ] **Step 3: 提交**

```bash
cd Projects/项目推进流水线
git add scripts/dev-agent.py
git commit -m "feat(pa): dev-agent main() 显式调 sdk_compat_patch.apply()（ADR-0006 #7）"
```

---

## Task 6: prompt_stream.py 移除 Event.wait

**Files:**
- Modify: `Projects/项目推进流水线/scripts/prompt_stream.py:25-45`
- Modify: `Projects/项目推进流水线/scripts/test_dev_agent_stream_lifespan.py:143-158`

- [ ] **Step 1: 改 prompt_stream 为单 yield + 更新 docstring**

把 `prompt_stream.py:25-45` 的函数替换为：
```python
async def prompt_stream(prompt: str) -> AsyncIterator[dict[str, Any]]:
    """yield 首条 user 消息后正常结束（SDK streaming 模式）。

    产出 dict 结构对齐 SDK 字符串路径（``_internal/client.py:214-219``）。
    用法：``query(prompt=prompt_stream(p), options=options)``。

    v3（2026-07-28）：移除 ``await asyncio.Event().wait()``（方案 A 输入侧 workaround）。
    accept loss of false hedge（RCA §4.2 实证 Event.wait 在真实 SDK 下本就不工作）；
    保活现由 ``sdk_compat_patch.apply()`` 在 SDK 方法侧根治（``or self.can_use_tool``）。
    对冲 = canary（CI-required release gate）+ H3-patch detection fail-safe + dev-agent
    测试门 fail-closed 主路径（``test_not_run``→exit 14→``blocked_by_gate``）。
    """
    yield {
        "type": "user",
        "session_id": "",
        "message": {"role": "user", "content": prompt},
        "parent_tool_use_id": None,
    }
```

同时删除文件顶部 `import asyncio`（line 21，若仅 Event.wait 使用）。确认无其他 asyncio 引用后删除；若有其他引用则保留。

- [ ] **Step 2: 改写 aclose 测试为「单 yield 后 StopAsyncIteration」**

把 `test_dev_agent_stream_lifespan.py:143-158` 的 `test_prompt_stream_aclose_clean_does_not_raise_already_running` 替换为：
```python
def test_prompt_stream_single_yield_then_stopasynciteration():
    """v3 回归锁：移除 Event.wait 后 prompt_stream 单 yield 即 StopAsyncIteration（无挂起点）。

    取代旧 ``test_prompt_stream_aclose_clean...``（其验证 Event.wait 挂起点上 aclose，移除后语义失效）。
    """
    import inspect

    from prompt_stream import prompt_stream

    async def run() -> None:
        gen = prompt_stream("x")
        msg = await gen.__anext__()
        assert msg["type"] == "user"
        with pytest.raises(StopAsyncIteration):
            await gen.__anext__()

    asyncio.run(run())
```

- [ ] **Step 3: 运行确认通过**

Run: `cd Projects/项目推进流水线 && python -m pytest scripts/test_dev_agent_stream_lifespan.py::test_prompt_stream_single_yield_then_stopasynciteration -q`
Expected: PASS。

- [ ] **Step 4: 提交**

```bash
cd Projects/项目推进流水线
git add scripts/prompt_stream.py scripts/test_dev_agent_stream_lifespan.py
git commit -m "refactor(pa): prompt_stream 移除 Event.wait 回归单 yield（v3 conscious，patch 根治保活）"
```

---

## Task 7: 摘 xfail + 结构断言 + anti-mock

**Files:**
- Modify: `Projects/项目推进流水线/scripts/test_dev_agent_stream_lifespan.py:68-76`

- [ ] **Step 1: 摘 xfail 标记 + 加结构断言 + identity anti-mock**

把 `test_dev_agent_stream_lifespan.py:68-76` 的 `@pytest.mark.xfail(...)` 装饰器整块删除，并在 `test_sdk_query_keeps_stdin_open_until_result` 函数体开头加结构断言。函数改为：
```python
def test_sdk_query_keeps_stdin_open_until_result() -> None:
    """直接锁 SDK query.py 缺陷修复：can_use_tool 存在时 result 前 end_input 不应被调。

    v3：sdk_compat_patch.apply() 已在 conftest session fixture 打上 → 本测试 GREEN。
    结构断言防 XPASS 来自 mock 污染/偶然 pass（H6）。
    """
    import inspect

    import sdk_compat_patch

    src = inspect.getsource(Query.wait_for_result_and_end_input)
    # 结构断言（三方交叉确认 CRITICAL）：count("self.can_use_tool") 会是 1（H3-patch 仅变异
    # if 条件，有意省略 logger 装饰行）；锚定 `or self.can_use_tool` 仅一次，鲁棒于两种情况。
    assert src.count("or self.can_use_tool") == 1, f"keep-alive patch 未生效:\n{src}"
    # identity anti-mock（适配 H3-patch：patched 是 exec 产出，用 apply 返回值 is 比对）
    assert Query.wait_for_result_and_end_input is sdk_compat_patch._last_patched, (
        "Query 方法非 patched 引用——conftest apply() 未触发或被 mock 绕过（假绿）")

    async def run() -> None:
        fake = _FakeTransport()
        query = Query(
            transport=fake,
            is_streaming_mode=True,
            can_use_tool=_admit,
            hooks=None,
            sdk_mcp_servers=None,
        )
        assert not query._first_result_event.is_set()
        task = asyncio.create_task(query.wait_for_result_and_end_input())
        await asyncio.sleep(0.1)
        assert not fake.end_input_called, (
            "end_input 在 result 到达前被调：can_use_tool 的 permission response 双向"
            "通道在首个 result 前即关闭"
        )
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    asyncio.run(run())
```

- [ ] **Step 2: 运行确认 GREEN（不再是 xfail）**

Run: `cd Projects/项目推进流水线 && python -m pytest scripts/test_dev_agent_stream_lifespan.py -q`
Expected: 全 PASS，`test_sdk_query_keeps_stdin_open_until_result` 普通 PASS（非 xfailed/XPASS）。

- [ ] **Step 3: 提交**

```bash
cd Projects/项目推进流水线
git add scripts/test_dev_agent_stream_lifespan.py
git commit -m "test(pa): 摘 xfail + 结构断言 count(\"or self.can_use_tool\")==1 + identity anti-mock"
```

---

## Task 8: canary 发布门 + cutover

**Files:**
- Create: `.github/workflows/canary-real-node-cli.yml`

- [ ] **Step 1: 加 canary CI workflow（manual dispatch，required check 锚点）**

创建 `.github/workflows/canary-real-node-cli.yml`：
```yaml
# canary-real-node-cli：发布门（spec §3.5.4）。真实 dispatch 验证 can_use_tool 通道通。
# workflow_dispatch 手动触发（需真实 GitHub 凭证 + 目标仓 + Node CLI，不能在普通 PR CI 跑）。
# 摘 xfail 的 PR 必须带一次 green canary run——branch protection 列本 check 为 required。
name: canary-real-node-cli
on:
  workflow_dispatch:
    inputs:
      prd_slugs:
        description: "cc-web-control PRD slug（逗号分隔，默认 custom-mcp-server-url,hub-role-pair-view）"
        default: "custom-mcp-server-url,hub-role-pair-view"
jobs:
  canary:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: cd Projects/项目推进流水线 && pip install -e ".[dev]"
      - name: 真实 dispatch canary
        env:
          GH_TOKEN: ${{ secrets.PA_DISPATCH_TOKEN }}
        run: |
          python3 Projects/项目推进流水线/scripts/run_daily.py --limit 2 \
            --from-stage dispatch 2>&1 | tee canary.log
          # 验证：dev-agent 跑 npm test 无 AbortError: Stream closed，verify 闸判绿
          grep -q "AbortError: Stream closed" canary.log && exit 1 || exit 0
```

- [ ] **Step 2: 记录 cutover 证据流程**

在 `RUNBOOK.md`（`Projects/项目推进流水线/`）追加「SDK 0.2.128 + #1106 patch cutover」节：
```markdown
## SDK 0.2.128 + #1106 patch cutover

发布门（cutover 前必过）：
1. 手动触发 `canary-real-node-cli` workflow（cc-web-control 两 PRD）→ green。
2. `cd Projects/项目推进流水线 && python quality_evidence.py` → readiness=True。
3. learning_memory_reflection / runtime_evidence 升级后跑一次确认无回归。

canary 目标抽象：cc-web-control 两 PRD 仅当前实例；迁移到其他等价目标仓（任何需
can_use_tool Bash 审批的 dispatch）须在本节更新。
```

- [ ] **Step 3: 全量质量门**

Run: `cd Projects/项目推进流水线 && bash scripts/quality.sh`
Expected: compileall + pytest + ruff 全绿（既有测试不破；新增 sdk_compat_patch + 守卫测试 pass）。

- [ ] **Step 4: ruff 复核（仅 E9+F）**

Run: `cd Projects/项目推进流水线 && ruff check scripts`
Expected: 无 E9/F 错误（test_* 紧凑写法不受影响）。

- [ ] **Step 5: 提交**

```bash
git add .github/workflows/canary-real-node-cli.yml Projects/项目推进流水线/RUNBOOK.md
git commit -m "ci(pa): canary-real-node-cli 发布门 workflow + cutover runbook（spec §3.5.4/§3.6）"
```

---

## Task 9: openspec C3 archive + 上游跟踪

**Files:**
- Modify: `openspec/changes/patch-sdk-can-use-tool-stdin-keepalive/proposal.md`

- [ ] **Step 1: C3 proposal 加 supersession 注**

在 `openspec/changes/patch-sdk-can-use-tool-stdin-keepalive/proposal.md` 顶部加：
```markdown
> **Superseded by `migrate-dev-agent-streaming-with-1106-patch`**（升级 0.2.128 + 本地 #1106 H3-patch ast 变异取代本 0.2.121 monkey-patch 方案，2026-07-28）。本 change 的根因核验/R2 评审历史保留供追溯，未完成 tasks 标 `superseded`。
```

- [ ] **Step 2: 在 #1105/#1106 留控制面复现评论**

Run（需用户确认后执行，因是 outward-facing）:
```bash
gh issue comment 1105 --repo anthropics/claude-agent-sdk-python --body "控制面复现：string-prompt query() + can_use_tool（Bash 权限闸）+ 无 hooks/MCP → can_use_tool 权限响应写回时 AbortError: Stream closed。源码定位 _internal/query.py wait_for_result_and_end_input 保活条件 `if self.sdk_mcp_servers or self.hooks:` 遗漏 can_use_tool。期待 #1106 合并。"
```

- [ ] **Step 3: 提交**

```bash
git add openspec/changes/patch-sdk-can-use-tool-stdin-keepalive/proposal.md
git commit -m "docs(openspec): C3 patch-sdk-can-use-tool-stdin-keepalive 标 superseded 指向新 change"
```

---

## Self-Review

**1. Spec coverage:**
- §3.1 版本锁 → Task 1 ✓
- §3.2 H3-patch apply()（detection/mutation/inject/self-check/F-4 marker/apply API）→ Task 2 + 3 ✓
- §3.3 dev-agent apply() 注入 + 严禁 try 包 → Task 5 ✓
- §3.4 Event.wait 移除 + 测试改写 → Task 6 ✓
- §3.5 测试触发协议（conftest autouse）→ Task 4 ✓；xfail 翻转 + 结构断言 + anti-mock → Task 7 ✓；守卫四态 → Task 3 ✓；canary 发布门 → Task 8 ✓
- §3.6 cutover 证据 → Task 8 Step 2 ✓
- §3.7 上游跟踪 → Task 9 ✓
- §3.8 C3 archive → Task 9 ✓

**2. Placeholder scan:** 无 TBD/TODO；每步含完整代码或确切命令。Task 9 Step 2 标注「需用户确认后执行」（outward-facing gh comment）——非 placeholder，是流程门。

**3. Type consistency:** `apply(query_cls=None)` 返回 patched 引用；测试用 `sdk_compat_patch._last_patched` 做 identity（apply 内部维护，与返回值一致）；`_DefectQuery`/`_FixedQuery` 等类名贯穿 Task 2-3 一致；结构断言统一 `count("or self.can_use_tool") == 1`。

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-28-dev-agent-streaming-migration.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
