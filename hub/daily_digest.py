"""运维日报——队列/池/漂移三面一日一条聚合（add-hub-daily-digest，spec: hub-notifications「运维日报」）。

纯只读观察：零新写面、值不入面（计数/名称/时长/红绿标记——报文全文与凭据片段不进
文本）；异常 fail-open 绝不崩排度循环。数据全部既有（queue_snapshot / list_all_runs /
list_runners / drift_seen 去抖集合直读），零新采集。
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def build_digest(cfg, database, drift_seen: set | None = None) -> str:
    """聚合三节文本。db 异常由调用方兜（emit_digest fail-open）；本函数内部分节独立
    try/except——单节挂不连坐他节（标注该节无数据）。"""
    now = datetime.now(timezone.utc)
    lines: list[str] = [f"[pa-hub 日报] {now.isoformat(timespec='seconds')}"]

    # ① 队列
    try:
        snap = database.queue_snapshot()
        runs24 = {"succeeded": 0, "failed": 0}
        for r in database.list_all_runs():
            ended = _parse_iso(r.get("ended_at"))
            if ended and ended >= now - timedelta(hours=24) and r.get("status") in runs24:
                runs24[r["status"]] += 1
        oldest = f"（最老等待 {snap['pending_oldest_min']}min）" if snap["pending"] else ""
        lines.append(f"队列：pending {snap['pending']}{oldest}｜running {snap['running']}"
                     f"｜24h 终态：succeeded {runs24['succeeded']} / failed {runs24['failed']}")
    except Exception as e:  # noqa: BLE001——单节 fail-open
        lines.append(f"队列：（读取失败 {type(e).__name__}）")

    # ② runner 池
    try:
        rows = database.list_runners()
        if not rows:
            lines.append("池：（空池——本机形态）")
        for r in rows:
            probe = "未探" if r.get("probe_ok") is None else ("🟢" if r["probe_ok"] else "🔴")
            bad = ""
            if r.get("probe_ok") == 0 and r.get("probe_json"):
                try:
                    import json as _json
                    d = _json.loads(r["probe_json"])
                    bad = "（坏项：" + "/".join(k for k, v in d.items() if v != "ok") + "）"
                except (ValueError, TypeError):
                    pass
            sync = ""
            if r.get("image_sync_json"):
                try:
                    import json as _json
                    s = _json.loads(r["image_sync_json"])
                    sync = "｜上次分发 " + ("✅" if s.get("ok") else "❌")
                except (ValueError, TypeError):
                    pass
            lines.append(f"池：{r['name']} {probe}{bad}｜忙闲 {r['load']}/{r['capacity']}{sync}"
                         f"｜{'启用' if r['enabled'] else '禁用'}")
    except Exception as e:  # noqa: BLE001
        lines.append(f"池：（读取失败 {type(e).__name__}）")

    # ③ 配置漂移（drift_seen 去抖集合直读：drift 类键 = 未消解漂移项）
    if drift_seen is None:
        lines.append("漂移：（看门狗未运行——HUB_DRIFT_TICKS 槽未触发过）")
    else:
        n = sum(1 for s in drift_seen if isinstance(s, tuple) and s and s[0] == "drift")
        lines.append(f"漂移：未消解 {n} 项" + ("（四规则全绿）" if n == 0 else "（详见 [drift] 日志与私信）"))
    return "\n".join(lines)


def emit_digest(cfg, database, notifier=None, drift_seen: set | None = None) -> None:
    """单轮日报（daemon 每 digest_ticks 个 tick 调一次）：MM 私信 + stderr 恒留痕。"""
    text = build_digest(cfg, database, drift_seen)
    print(text, file=sys.stderr)
    if notifier is not None:
        notifier.notify_admin(text)  # 内部 fail-open；未配通道 = 可见的丢（stderr 已留痕）
