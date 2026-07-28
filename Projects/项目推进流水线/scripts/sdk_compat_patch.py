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

reload 安全：reload 后 ``_APPLIED`` 重置为 False，apply 重新检测——Query.X 已是 patched
（源码含 can_use_tool）→ upstream-fixed 分支 skip，不重复变异。故无需 source marker。

implementation note（偏离 spec v3 F-4）：spec 设计了 ``_MARKER`` 源码前缀防重载，但实测
marker 前缀会使 ``inspect.getsource(patched)`` 行号错位（co_firstlineno 指向 marker 行）；
而 detection 的 upstream-fixed 分支已覆盖 reload 场景，marker 冗余，故移除。
"""
from __future__ import annotations

import ast
import inspect
import linecache
import textwrap
from typing import Any

_APPLIED: bool = False
_last_patched: Any = None

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
        # 新形态：上游已修（#1106 merged）或本模块已 patch → upstream-fixed skip
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
    mutated_src = ast.unparse(tree)

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

    # linecache 注册：让 post-patch getsource 返回变异源码（不报 OSError）
    lines = mutated_src.splitlines(keepends=True)
    linecache.cache[_FAKE_FILE] = (len(mutated_src), None, lines, mutated_src)

    # 赋值注入
    target.wait_for_result_and_end_input = patched  # type: ignore[assignment]
    _APPLIED = True
    _last_patched = patched

    # 阶段4: identity 自检 (F-9)
    assert target.wait_for_result_and_end_input is patched, "patch assignment did not take effect"
    return patched
