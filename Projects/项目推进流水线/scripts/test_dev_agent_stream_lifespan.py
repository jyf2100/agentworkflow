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

import pytest
from claude_agent_sdk import PermissionResultAllow
from claude_agent_sdk._internal.query import Query
from claude_agent_sdk._internal.transport import Transport

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


@pytest.mark.xfail(
    strict=True,
    reason=(
        "锁 SDK 0.2.121 上游缺陷：_internal/query.py:819-827 wait_for_result_and_end_input "
        "保活条件 `sdk_mcp_servers or hooks` 遗漏 can_use_tool。dev-agent 层修复"
        "（prompt_stream 保持 pending）不改 SDK，本测试仍 RED。SDK 升级修复后 XPASS → "
        "strict fail → 提醒移除本标记并复盘 prompt_stream workaround 是否可简化。"
    ),
)
def test_sdk_query_keeps_stdin_open_until_result() -> None:
    """直接锁 SDK query.py:819-827 缺陷：can_use_tool 存在时 result 前 end_input 不应被调。

    当前 RED（SDK 缺陷，dev-agent 修复不改动它）→ xfail 标记。
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


def test_prompt_stream_aclose_clean_does_not_raise_already_running() -> None:
    """§1.2 aclose 分类：修复后 prompt_stream 在 aclose 时正常完成，不产生
    'aclose(): asynchronous generator is already running'。

    该异常是旧实现（单 yield 正常耗尽）下并发清理的独立症状，非首因——首因（end_input
    早关）由 ``test_sdk_query_keeps_stdin_open_until_result`` 独立锁定（直接调
    wait_for_result_and_end_input，不涉及 generator aclose），二者解耦。
    """

    async def run() -> None:
        gen = prompt_stream("x")
        await gen.__anext__()  # generator 现在挂在 await Event.wait()
        # aclose 应在挂起点正常注入 GeneratorExit 并清理，不抛 'already running'
        await gen.aclose()

    asyncio.run(run())
