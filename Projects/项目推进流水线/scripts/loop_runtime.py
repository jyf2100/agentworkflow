"""loop_runtime.py — shadow journal 写入运行时（OpenSpec add-durable-loop-runtime task 3.2）。

把 journal 接入 dispatch 流程的「写入器」：dispatch 每个决策点旁路调 ``ShadowJournal.emit`` 写一条
``JournalEvent``。**shadow mode**（design 决策#8）三条硬契约：

    1. ``enabled=False``（``journal_shadow`` flag 默认关）→ ``emit`` 完全 no-op，dispatch 行为 **零变化**
       （flag 关即第一阶段 baseline，可随时回滚）；
    2. ``enabled=True`` → 旁路写 journal，**不改 dispatch 决策**——emit 只观测、不返回影响流程的值
       （调用方不得用 emit 的返回值驱动控制流）；
    3. **emit 内部吞所有异常**——journal 是观测层，写失败（盘满/权限/path 非法）绝不得让 dispatch 崩
       （spec「shadow 不改决策」含「不因自身故障改控制流」——否则观测反成单点故障源）。

``stamp`` 由调用方注入（``Callable[[], str]``）——本模块不触系统时间，便于测试确定性 + 复用调用方已有的
时间函数。``ShadowJournal`` 是有状态写入器（维护 event 自增序号保 event_id 唯一），故非 frozen。

模块依赖 ``journal``/``loop_state``/标准库，不触 SDK——cron 隔离不变。
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from journal import append_event
from loop_state import JOURNAL_SCHEMA_VERSION, JournalEvent


class ShadowJournal:
    """shadow journal 写入器（task 3.2）。

    三条硬契约见模块 docstring：flag 关 no-op；flag 开旁路写不改决策；emit 异常吞掉。

    用法（dispatch_one 关键点）::

        sj = ShadowJournal(path, run_id, stamp_fn, enabled=flags.journal_shadow)
        sj.emit("running", iteration_id=iter_id, prd_id=prd_id, payload={"base": base})
        # ... dispatch 决策照常，emit 仅旁路记录 ...
    """

    def __init__(self, path: str | Path | None, run_id: str,
                 stamp: Callable[[], str], enabled: bool = False):
        """Args:
            path: journal 文件路径；None → emit no-op（历史 run 无 journal 目录兼容）。
            run_id: per-run 稳定 ID（``ids.run_id``），写入每条 event 的 ``run_id`` 字段。
            stamp: 时间戳函数（调用方注入，返回 ISO 字符串；本模块不触系统时间）。
            enabled: ``journal_shadow`` flag——False 时 emit 完全 no-op。
        """
        self.path = path
        self.run_id = run_id
        self._stamp = stamp
        self.enabled = enabled
        self._seq = 0   # event 自增序号（event_id 唯一性，reducer dedup 依据）

    def emit(self, event_type: str, iteration_id: str, prd_id: str,
             payload: dict | None = None) -> str | None:
        """旁路写一条 ``JournalEvent``。

        契约：
            * ``enabled=False`` 或 ``path=None`` → no-op，返回 None；
            * 否则构造 ``JournalEvent``（event_id = ``<run_id>-<seq>`` 唯一）并 ``append_event``；
            * **任何异常吞掉**，返回 None——shadow 绝不影响 dispatch 控制流。

        Returns:
            写入的 event_id；no-op/失败时 None。**调用方不得用此返回值驱动控制流**（否则违背 shadow 语义）。
        """
        if not self.enabled or self.path is None:
            return None

        self._seq += 1
        event_id = f"{self.run_id}-{self._seq}"
        event = JournalEvent(
            schema_version=JOURNAL_SCHEMA_VERSION,
            event_id=event_id,
            timestamp=self._stamp(),
            iteration_id=iteration_id,
            run_id=self.run_id,
            prd_id=prd_id,
            event_type=event_type,
            payload=payload or {},
        )
        try:
            append_event(self.path, event)
        except Exception:
            # shadow 契约#3：观测层自身故障不得拖垮被观测的 dispatch——静默吞掉。
            # 不 log（避免 log 又抛）；上层若有独立 log 通道可另行探查 journal 完整性。
            return None
        return event_id
