"""sdk_compat_patch.apply() 守卫四态 + H3-patch 变异单测（spec §3.5.3，D3 fail-safe 方向）。

用 fake/stub Query 类（非 unittest.mock）测 detection/raise 分支——不与生产路径解耦，
每个分支对应一种 SDK 未来形态，结构断言 ``count("or self.can_use_tool")==1`` 锁最小变异标记。
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


class _FixedQuery:
    """新形态：上游已修（if 含 can_use_tool）→ apply skip。"""
    sdk_mcp_servers = None
    hooks = None
    can_use_tool = None
    transport = None
    _first_result_event = None

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
    """其他形态（can_use_tool 抽到变量，非精确旧形态、非新形态）→ apply raise（F-2 fail-safe）。"""
    async def wait_for_result_and_end_input(self) -> None:
        keep = self.can_use_tool
        if self.sdk_mcp_servers or self.hooks or keep:
            await self._first_result_event.wait()
        await self.transport.end_input()


# 捕获 _DefectQuery 原版方法引用（test 文件源码，可 inspect.getsource）——fixture 用它还原。
# **不可用 exec 重建**：exec 创建的函数 co_filename=<string>，getsource 失败，会误触发 apply 的
# state-2 fail-loud raise（这恰好印证 detection 的 fail-safe 方向是对的；production 真实 SDK 文件不受影响）。
_DEFECT_ORIG = _DefectQuery.wait_for_result_and_end_input


@pytest.fixture(autouse=True)
def _isolate_global_state(monkeypatch):
    """隔离 sdk_compat_patch 全局状态 + 还原 _DefectQuery 原版（跨文件耦合点）。

    ``_APPLIED``/``_last_patched`` 用 monkeypatch——teardown 自动还原到 conftest session fixture
    对真实 Query 的 patch 值，避免 ``test_dev_agent_stream_lifespan`` 的
    ``Query.X is _last_patched`` 断言因 ``_last_patched`` 被本文件清空而假红。
    ``_DefectQuery.X`` setup + teardown 都还原为 ``_DEFECT_ORIG``（可 getsource 的原版），
    确保每个测试看到未 mutate 的缺陷形态。
    """
    monkeypatch.setattr(sdk_compat_patch, "_APPLIED", False)
    monkeypatch.setattr(sdk_compat_patch, "_last_patched", None)
    _DefectQuery.wait_for_result_and_end_input = _DEFECT_ORIG
    yield
    _DefectQuery.wait_for_result_and_end_input = _DEFECT_ORIG


def test_apply_defect_form_patches_boolop():
    """state 4：精确旧形态 → mutate，patched 源码含 ``or self.can_use_tool`` 仅一次（D6）。"""
    patched = sdk_compat_patch.apply(query_cls=_DefectQuery)
    src = inspect.getsource(_DefectQuery.wait_for_result_and_end_input)
    assert patched is _DefectQuery.wait_for_result_and_end_input, "apply 返回值须 = 注入后的方法（identity）"
    assert src.count("or self.can_use_tool") == 1, f"keep-alive 条件未正确变异:\n{src}"


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
    """F-2：can_use_tool 抽到变量（非精确旧形态、非新形态）→ raise（fail-safe 方向）。"""
    with pytest.raises(RuntimeError, match="not precise old form"):
        sdk_compat_patch.apply(query_cls=_RefactoredQuery)


def test_apply_idempotent():
    """F-6/F-4：二次调用返回同一 patched 引用，不重复变异（``count`` 仍 1）。"""
    first = sdk_compat_patch.apply(query_cls=_DefectQuery)
    second = sdk_compat_patch.apply(query_cls=_DefectQuery)
    assert first is second
    src = inspect.getsource(_DefectQuery.wait_for_result_and_end_input)
    assert src.count("or self.can_use_tool") == 1, "二次调用不应叠加变异"


def test_apply_returns_patched_for_identity_assert():
    """apply 返回 patched 引用 → 测试体可 ``assert Query.X is apply()``（anti-mock，适配 H3-patch）。"""
    patched = sdk_compat_patch.apply(query_cls=_DefectQuery)
    assert patched is _DefectQuery.wait_for_result_and_end_input
