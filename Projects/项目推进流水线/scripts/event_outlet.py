"""event_outlet.py — pa 语义事件出口（add-hub-slice-4b，spec: pa-event-outlet）。

pa 容器内 critic / dispatch / verify 段的语义事件 → run 伴生事件文件
``<state_dir>/runs/<project>/<stamp>_<slug>.events.jsonl``（与 journal 同目录
平铺同拼法——``<stamp>_<slug>`` 与 ``journal.py`` 的 ``{stamp}_{slug}.journal.jsonl``
逐字节同源，hub 按 run 找文件零寻址分叉）。

这是 **hub 与 pa 的唯一语义事件接缝**：hub 只读本文件机械转发通知，绝不解析
pa journal 内部格式（4a D2 边界不动）；pa 不经网络直连 hub 推事件（单向文件
接缝，容器即弃后文件留卷）。

事件行契约（v1）::

    {"v": 1, "event": "critic_verdict"|"pr_opened"|"verify_verdict",
     "seq": <run 内单调递增 int>, "stamp": ..., "project": ..., ...事实字段}

写纪律对齐 ``journal.py``：``O_APPEND`` + ``flush`` + ``os.fsync``（append-only
单写者下 seq = 现有行数即次序，崩溃只可能截断最后一条）。**fail-open**：出口
写失败 stderr 告警不阻断流水线主路径（事件是观察面，绝不为观察面炸执行面）。

vault 形态同写（spec「事件出口写点零行为变化」）：写面是既有 state 目录的追加，
零外部副作用，vault 侧无人消费该文件，cron 链行为不变。

纯 stdlib（json/os/sys/pathlib）——cron 安全（PATH 极简环境可跑）。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

EVENT_VERSION = 1  # 契约版本内嵌每行（spec：写方不省略；hub 对 v≠1 整文件跳过）


def events_path(state_dir: str | Path, project: str, stamp: str, slug: str) -> Path:
    """事件文件唯一拼法（与 journal 伴生同拼法——``{stamp}_{slug}`` 前缀逐字节一致）。"""
    return Path(state_dir) / "runs" / project / f"{stamp}_{slug}.events.jsonl"


def append_event(
    state_dir: str | Path, project: str, stamp: str, slug: str,
    event: str, **facts: object,
) -> None:
    """追加一条语义事件行（facts = 事件类型的事实字段，机械转发不加工）。

    fail-open：构造/写盘任一失败 → stderr 告警后返回，绝不 raise 穿透挂点
    （挂点在流水线主路径上，观察面故障不得炸执行面——对齐 notify 面 fail-open 哲学）。
    """
    try:
        target = events_path(state_dir, project, stamp, slug)
        target.parent.mkdir(parents=True, exist_ok=True)
        seq = _line_count(target)
        line = {
            "v": EVENT_VERSION, "event": event, "seq": seq,
            "stamp": stamp, "project": project, **facts,
        }
        with open(target, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
    except Exception as e:  # 观察面 fail-open：宽 except 有意（stderr 可见，不静默不穿透挂点）
        print(f"[event-outlet][warn] {event} 写失败（fail-open 不阻断流水线）: {type(e).__name__}",
              file=sys.stderr)


def _line_count(target: Path) -> int:
    """seq 来源：现有合法行数（append-only 单写者，行数即下一 seq——与读端 (stamp,seq)
    去重契约配套；末尾崩溃截断行数少 1 → 重写同 seq，hub 侧重启重发语义兜住）。"""
    if not target.exists():
        return 0
    with target.open(encoding="utf-8", errors="replace") as f:
        return sum(1 for ln in f if ln.strip())
