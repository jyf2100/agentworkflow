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
KNOWN_EVENTS = frozenset({"critic_verdict", "pr_opened", "verify_verdict", "acceptance_suite",
                            "metrics_proposed"})  # add-acceptance-spec-confirmation 2.2：未注册整行丢弃即死链（spec scenario）


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


def locate_pa_artifact(run_dir: Path, stem: str, suffix: str) -> Path | None:
    """pa 落卷工件寻址（events.jsonl / verify-report.json 共用，三消费点唯一拼法）。

    先按契约精确拼 `{stamp}_{stem}{suffix}`（stamp = run 目录名前缀，hub 写面纪
    律）；真机实证（2026-10-28，run #73/#74）：pa inject 会给注入文件名再前缀
    stamp（自增 m/m2 避碰，hub 拿不到 actual），pa 侧 append 以注入后 stem 拼
    `{actual}_{actual}_{stem}{suffix}`——精确未命中时按 `*_{stem}{suffix}` 后缀
    glob 兜底。mm 路径 stem content-addressed（同内容 store_prd 拒二次落盘 →
    同 stem 工件唯一）故后缀可靠；面板路径 pinyin slug 对 hub 不可知，glob 亦
    不可达（pa 侧契约补 hub_stem 回写另行立项——本兜底先救 mm 路径）。
    返回 None = 无此工件（调用方按缺失处理）。"""
    rd = Path(run_dir)
    exact = rd.parent / f"{rd.name.split('_', 1)[0]}_{stem}{suffix}"
    if exact.is_file():
        return exact
    cands = sorted(rd.parent.glob(f"*_{stem}{suffix}"))
    return cands[-1] if cands else None
