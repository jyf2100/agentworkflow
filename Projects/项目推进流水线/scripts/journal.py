"""journal.py — append-only JSONL journal IO + 损坏检测（OpenSpec add-durable-loop-runtime task 2.2 + 2.3）。

journal 是第二阶段的「崩溃恢复真源」（design 决策#1）。每次 dispatch 的每个状态迁移、测试证据、验证反馈
都追加为一行 JSON；崩溃后重读 journal + ``loop_state.reduce`` 即可精确恢复到崩溃前的迭代状态——
无需依赖可能未落盘的运行时内存或被追加/覆写的 PRD。

    task 2.2（IO）—— ``append_event`` 原子追加：``O_APPEND``（POSIX 追加不撕裂已提交历史）+ ``flush``
        + ``os.fsync``（落盘，崩溃后 page cache 不丢已 append 的行）。``read_events`` 读 JSONL 还原 ``JournalEvent``。
    task 2.3（损坏检测）—— spec 硬断言：「tolerate a single incomplete trailing record but fail closed
        on malformed or missing records inside committed history」。即：
          * **末尾不完整容忍**：崩溃只可能截断最后一条 append（O_APPEND + fsync 保证更早的已落盘）→
            丢弃半行，继续归约前面已提交的事件；
          * **中部损坏 fail-closed**：已提交历史里夹坏行 = 磁盘错/写竞争污染 → ``JournalCorruptionError``，
            绝不静默跳过（否则状态机基于残缺事件归约出错误状态）。reducer 据此落 ``STATE_CORRUPT``。
    task 3.6（schema-invalid 收紧）—— 末行若是**完整 JSON 但 schema 非法**（缺必填字段/类型错，非崩溃截断的
        半行）→ 仍 fail-closed；只容忍**可证明不完整的截断尾写**，杜绝「完整但语义污染」被当截断静默丢弃。
        实现：``_scan`` 分离 ``JSONDecodeError``（截断，末行容忍）与 schema 构造失败（complete-but-invalid，始终 fail-closed）。

模块仅依赖 ``loop_state`` 数据模型 + 标准库（os/json/dataclasses/pathlib），不触 SDK——cron 隔离不变。
"""
from __future__ import annotations

import dataclasses
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from loop_state import JournalEvent


class JournalCorruptionError(Exception):
    """journal 已提交历史内出现损坏行（fail-closed）。

    spec：committed history 内的 malformed/missing 必须拒绝——绝不静默跳过坏行继续归约。
    ``line_number`` 1-based，供运维定位。
    """

    def __init__(self, line_number: int, raw_snippet: str = ""):
        self.line_number = line_number
        self.raw_snippet = raw_snippet
        super().__init__(
            f"journal 第 {line_number} 行损坏（committed history 内 malformed，fail-closed）"
        )


@dataclass(frozen=True)
class CorruptionReport:
    """journal 完整性扫描报告（validate_journal 返回，不 raise）。

    ``tail_truncated``：末尾不完整记录被容忍丢弃（正常崩溃恢复）；``corrupted_line_numbers``：中部损坏行
    （1-based）——非空即 ``is_fail_closed``，reducer 据此落 ``STATE_CORRUPT`` 而非基于残缺事件继续。
    """
    __test__: ClassVar[bool] = False
    events_read: int = 0
    tail_truncated: bool = False
    corrupted_line_numbers: tuple[int, ...] = ()

    @property
    def is_fail_closed(self) -> bool:
        """是否 fail-closed（有中部损坏行）——reducer 见 True 应落 STATE_CORRUPT。"""
        return bool(self.corrupted_line_numbers)


# JournalEvent 已知字段集合：读端只认这些键（防未来 schema 加键时 JournalEvent(**obj) TypeError；
# 也防恶意/损坏行塞入构造器不接受的键）。
_KNOWN_FIELDS: frozenset[str] = frozenset(f.name for f in dataclasses.fields(JournalEvent))


def append_event(path: str | Path, event: JournalEvent) -> None:
    """原子追加一条 event 到 JSONL journal。

    实现：``open(.., "a")`` 走 ``O_APPEND``（POSIX 保证追加不与并发/历史撕裂）+ ``flush`` 推到 OS +
    ``os.fsync`` 落盘。崩溃只可能丢「正在写的最后一条」，已 fsync 的更早记录必可恢复（design 决策#1 前提）。
    父目录不存在则创建（首次 dispatch）。每行 = 一个完整合法 JSON + ``\\n``。
    """
    line = json.dumps(dataclasses.asdict(event), ensure_ascii=False)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "a", encoding="utf-8") as f:
        f.write(line + "\n")
        f.flush()
        os.fsync(f.fileno())


def _scan(path: str | Path) -> tuple[list[JournalEvent], CorruptionReport]:
    """扫描 journal：逐行解析，返回 ``(events, report)``，**不 raise**。

    损坏策略（spec）：
        * **JSON 词法解析失败**（半行/截断，崩溃只可能截断最后一条 append）且该行是「最后一条非空行」
          → 末尾截断，``tail_truncated=True``，丢弃该行；
        * **JSON 词法解析失败**且非末尾 → 中部损坏，记入 ``corrupted_line_numbers``（1-based）；
        * **complete JSON 但 schema 非法**（缺必填字段/类型错，task 3.6）→ 始终 fail-closed，
          记入 ``corrupted_line_numbers``（不论是否末行——完整但语义不合规是写污染，非崩溃截断）；
        * 解析 + 校验成功 → 追加到 events。
    """
    target = Path(path)
    if not target.exists():
        return [], CorruptionReport()

    lines = target.read_text(encoding="utf-8").splitlines()
    non_empty_idx = [i for i, ln in enumerate(lines) if ln.strip()]
    last_nonempty = non_empty_idx[-1] if non_empty_idx else -1

    events: list[JournalEvent] = []
    corrupted: list[int] = []
    tail_truncated = False

    for i, line in enumerate(lines):
        if not line.strip():
            continue
        # 第一步：JSON 词法解析。失败 = 半行/截断（provably incomplete trailing write，
        # 崩溃只可能截断最后一条 append）。仅容忍末行的截断；中部截断仍 fail-closed。
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            # 末尾不完整（崩溃截断最后一条 append）→ 容忍；中部损坏 → fail-closed 记录
            if i == last_nonempty:
                tail_truncated = True
            else:
                corrupted.append(i + 1)   # 1-based
            continue
        # 第二步：schema 校验。complete JSON 但缺必填字段/类型错（task 3.6）→ 始终 fail-closed，
        # 不论是否末行——「complete-but-schema-invalid」不是崩溃截断，是写污染/磁盘错，绝不静默丢弃。
        try:
            filtered = {k: v for k, v in obj.items() if k in _KNOWN_FIELDS}
            events.append(JournalEvent(**filtered))
        except Exception:
            corrupted.append(i + 1)   # 1-based

    report = CorruptionReport(
        events_read=len(events),
        tail_truncated=tail_truncated,
        corrupted_line_numbers=tuple(corrupted),
    )
    return events, report


def read_events(path: str | Path) -> list[JournalEvent]:
    """读 journal 还原 ``JournalEvent`` 列表。

    末尾不完整容忍（丢弃半行）；**中部损坏 raise ``JournalCorruptionError``**（fail-closed）。
    文件不存在 → 空列表（首次 dispatch 无 journal 是正常态）。
    """
    events, report = _scan(path)
    if report.corrupted_line_numbers:
        raise JournalCorruptionError(report.corrupted_line_numbers[0])
    return events


def validate_journal(path: str | Path) -> CorruptionReport:
    """校验 journal 完整性，返回报告（**不 raise**）。

    调用方/reducer 据 ``report.is_fail_closed`` 决定是否落 ``STATE_CORRUPT``——把「检测」与「处置」解耦，
    validate 可用于运维探查而不强制中断。
    """
    _, report = _scan(path)
    return report
