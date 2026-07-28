"""确定性 SDK 集成 RED：dev-agent SDK 流式 prompt 的 permission 控制通道生命周期缺陷。

对应 OpenSpec change `fix-dev-agent-stream-aclose-race` tasks §1.1。

根因（claude-agent-sdk==0.2.121 源码逐条核验）：
- `Query.wait_for_result_and_end_input()`（_internal/query.py:809-827）的 stdin 保活条件是
  `if self.sdk_mcp_servers or self.hooks:` —— **遗漏 `can_use_tool`**。
- 故当无 SDK MCP servers 且无 lifecycle hooks 时，它不等首个 result，立即
  `await self.transport.end_input()`（query.py:827），关闭 stdin。
- 但 `Client._process_query_inner`（_internal/client.py:101-105）明文要求
  `can_use_tool callback requires streaming mode`（双向 stdin）。can_use_tool 的
  permission response 经 `transport.write`（query.py:528）发回 CLI；stdin 早关后该 write
  失败 → `AbortError: Stream closed`。

dev-agent.py 用单 yield async generator `prompt_stream` 喂 `query()`，prompt 耗尽即触发上述
早关 → dev loop 后续轮次的 `npm test` 等需审批命令在权限层即中止，进程从未启动。
"""

import asyncio
import inspect

from claude_agent_sdk import PermissionResultAllow
from claude_agent_sdk._internal.query import Query
from claude_agent_sdk._internal.transport import Transport

import sdk_compat_patch  # H3-patch（conftest session fixture 已 apply；此处仅结构/identity 断言用）
from prompt_stream import prompt_stream  # 真实 dev-agent prompt 源


class _FakeTransport(Transport):
    """记录 end_input / write 时序的最小 Transport 实现。

    `write` 在 `end_input` 之后抛 RuntimeError，模拟 subprocess stdin 关闭后再写失败
    （即 dev 日志中的 `AbortError: Stream closed`）。
    """

    def __init__(self) -> None:
        self.writes: list[str] = []
        self.end_input_called: bool = False
        self._input_ended: bool = False

    async def connect(self) -> None:  # noqa: D401
        return None

    async def write(self, data: str) -> None:
        if self._input_ended:
            raise RuntimeError("Stream closed")
        self.writes.append(data)

    async def read_messages(self):  # type: ignore[override]
        if False:  # pragma: no cover
            yield {}  # noqa: E701

    async def close(self) -> None:  # noqa: D401
        return None

    def is_ready(self) -> bool:
        return True

    async def end_input(self) -> None:
        self.end_input_called = True
        self._input_ended = True


async def _admit(_tool_name, _tool_input, _context):  # noqa: D401
    return PermissionResultAllow()


def test_sdk_query_keeps_stdin_open_until_result() -> None:
    """H3-patch 生效后：can_use_tool 存在时 result 前 end_input 不应被调（#1105 已根治）。

    sdk_compat_patch.apply() 经 conftest session fixture 在真实 Query 上 ast 变异
    wait_for_result_and_end_input 保活条件末位加 can_use_tool；本测试三重锁定：
    (1) 行为——result 前 end_input 不调；(2) 结构——源码 ``count("or self.can_use_tool")==1``；
    (3) identity——``Query.X is sdk_compat_patch._last_patched``（anti-mock，证实 patch 真打上）。
    """
    # 结构 + identity 断言（D6 三方交叉确认）：patched 源码含 ``or self.can_use_tool`` 仅一次，
    # 且 = conftest session fixture 打的 patched（防 XPASS 来自 mock 污染/偶然 pass）。
    src = inspect.getsource(Query.wait_for_result_and_end_input)
    assert src.count("or self.can_use_tool") == 1, f"keep-alive 条件未被 H3-patch 变异:\n{src}"
    assert Query.wait_for_result_and_end_input is sdk_compat_patch._last_patched, (
        "Query.wait_for_result_and_end_input 应 = session patched（H3-patch 未生效？）"
    )

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
            "通道在首个 result 前即关闭（#1105 回归——sdk_compat_patch 未生效？）"
        )
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    asyncio.run(run())


def test_prompt_stream_keeps_stdin_open_until_result() -> None:
    """主回归锁：真实 prompt_stream 喂 stream_input 时，result 前不应 end_input。

    模拟 dev-agent.py:467 `query(prompt=prompt_stream(prompt), options=options)` +
    client.py:224 `query.spawn_task(query.stream_input(prompt))` 真实路径。
    当前 prompt_stream 单 yield 耗尽 → 触发 wait_for_result_and_end_input → 立即 end_input
    （RED）。修复后（prompt_stream 保持 pending 到 result/cancel）→ stream_input 阻塞在
    async for，不调 end_input（GREEN）。方案 A/B/C 均以此为同一反例。
    """

    async def run() -> None:
        fake = _FakeTransport()
        query = Query(
            transport=fake,
            is_streaming_mode=True,
            can_use_tool=_admit,
            hooks=None,
            sdk_mcp_servers=None,
        )
        task = asyncio.create_task(query.stream_input(prompt_stream("run npm test")))
        await asyncio.sleep(0.1)
        assert fake.writes, "prompt user message 应已写入 transport"
        assert not fake.end_input_called, (
            "prompt_stream 耗尽后 end_input 在 result 前被调：dev loop 后续 tool turn 的"
            "审批命令将无法写回 permission response"
        )
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    asyncio.run(run())


def test_prompt_stream_single_yield_then_stopasynciteration() -> None:
    """prompt_stream 单 yield 后正常耗尽（回归最小 AsyncIterable 契约）。

    历史 ``await asyncio.Event().wait()`` 的「输入侧冗余对冲」已 conscious 移除（虚假对冲：
    早关根因在 SDK 方法侧，输入 pending 救不了）；真实根治交给 sdk_compat_patch ast 变异。
    本测试锁定移除后 prompt_stream 仅 yield 一条 user 消息即结束。
    """

    async def run() -> None:
        gen = prompt_stream("x")
        msgs = []
        async for msg in gen:
            msgs.append(msg)
        assert len(msgs) == 1, "prompt_stream 应单 yield 后耗尽（Event.wait 已移除）"
        assert msgs[0]["type"] == "user"
        assert msgs[0]["message"]["content"] == "x"

    asyncio.run(run())
