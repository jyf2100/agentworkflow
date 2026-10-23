"""hub 语义事件读取——events.jsonl 解析防御（add-hub-slice-4b 任务 3.1，spec: pa-event-outlet hub 读面）。

hub 只读 pa 写的伴生事件文件机械转发，绝不解析 pa journal 内部格式（4a D2
边界不动）。契约版本（v=1）不认识 = **整文件跳过**——转发半懂的数据比不转发
危险（事件文本会被人当事实消费）；未知 event 枚举只跳该行（写方新增事件类型
不应废掉其余已知事件的转发）；坏 JSON 行跳过（append-only 尾部截断的既有点）。

零持久写面：读文件 → 吐行列表，去重与转发归 lifecycle（消费侧状态在进程内存）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

EVENT_VERSION = 1  # 与 pa event_outlet.EVENT_VERSION 同值——两文件一处契约
KNOWN_EVENTS = frozenset({"critic_verdict", "pr_opened", "verify_verdict"})


def read_events(path: Path) -> list[dict]:
    """读事件文件返回可转发行（已是合法 dict、v=1、event ∈ KNOWN_EVENTS）。

    文件不存在 = []（事件还没发生）；任一行 v≠1 → 整文件 [] + stderr 告警
    （版本混乱说明写方形态未知，宁可不转发）；坏 JSON 行 / 未知 event 行跳过。
    """
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        print(f"[events][warn] 读事件文件失败（跳过本轮）: {type(e).__name__}", file=sys.stderr)
        return []
    rows: list[dict] = []
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            row = json.loads(ln)
        except json.JSONDecodeError:
            print(f"[events][warn] 坏 JSON 行跳过：{path.name}", file=sys.stderr)
            continue
        if not isinstance(row, dict) or row.get("v") != EVENT_VERSION:
            print(f"[events][warn] 契约版本未知（v≠{EVENT_VERSION}）——整文件跳过：{path.name}", file=sys.stderr)
            return []
        ev = row.get("event")
        if ev not in KNOWN_EVENTS:
            print(f"[events][warn] 未知 event 枚举行跳过：{ev!r}", file=sys.stderr)
            continue
        rows.append(row)
    return rows
