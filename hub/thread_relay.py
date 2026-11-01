"""订阅直通——绑定频道在飞 run 窗口内的授权人话 → human_notes 排队面（add-thread-subscription，spec: hub-mm-intake「订阅直通」）。

三条红线：不新增派发授权（Gate = 成员三态既有判据，与 /pa 入站同门）；不中断/不改写
在飞 run（v1 只做排队面——落盘 + 下一 iteration 经 --human-notes 注入，仅上下文非判据）；
授权面外作者忽略。读取通道 fail-open——任何异常不崩排度循环。

关联判据（D1）：绑定频道 + 在飞 run 时间窗（post.create_at > run.started_at）+ 非 bot
作者 → 归该频道最老在飞 run（single-flight 串行下同频道天然单 run）。水位（D3） =
db intake_watermarks 键 thread_relay:{channel_id}，存上次见到的最大 create_at(ms)——
重启安全（水位持久），(message_id 在文件名) 双保险幂等。
"""
from __future__ import annotations

import sys
from pathlib import Path

# mm_intake 延迟导入（函数内）：模块级会成环 lifecycle→thread_relay→mm_intake→prd_store→lifecycle
#（thread_relay 被 lifecycle 模块级导入；prd_store 从 lifecycle 取 PROJECT_RE）


def _post_ms(post: dict) -> int:
    try:
        return int(post.get("create_at") or 0)
    except (TypeError, ValueError):
        return 0


def relay_channel_notes(cfg, database, mm, notifier=None, *, member_cache=None) -> int:
    """单轮直通捕获（daemon 每 relay_ticks 调一次）：全部绑定频道 → 各自最老在飞 run。

    返回本轮采纳笔记数。逐绑定 fail-open（单频道异常不连坐）；无在飞 run 的频道只推
    水位不采集（窗口判据天然过滤）。回执：每频道每轮至多一条汇总（水位只进不退 = 汇总
    天然去抖，绝不逐条刷屏）。
    """
    import mm_intake  # 延迟导入破环（见模块头注）
    if mm is None:
        return 0  # bot 通道未配置 = 直通关闭（fail-closed 与 /pa 入站同语义）
    try:
        bindings = database.list_channel_bindings()
        running = database.runs_in_status("running")
    except Exception as e:  # noqa: BLE001——观察面 fail-open
        print(f"[relay][warn] 绑定/在飞读取失败（fail-open）: {type(e).__name__}", file=sys.stderr)
        return 0
    cache = member_cache if member_cache is not None else mm_intake.MemberCache()
    total = 0
    for b in bindings:
        channel_id, project = b["channel_id"], b["project"]
        try:
            runs = sorted((r for r in running if r["project"] == project), key=lambda r: r["id"])
            run = runs[0] if runs else None
            wm_key = f"thread_relay:{channel_id}"
            since = int(database.get_watermark(wm_key) or 0)
            posts = mm.read_channel_posts(channel_id, since)
            if posts is None:
                print(f"[relay] {project} 频道读取 UNKNOWN（网络/权限），下轮再查", file=sys.stderr)
                continue
            bot_id = mm.bot_user_id()
            notes_dir = Path(run["run_dir"]) / "human_notes" if run else None
            taken, newest = 0, since
            for p in posts:
                ms = _post_ms(p)
                newest = max(newest, ms)
                if not p.get("id") or not p.get("message", "").strip():
                    continue
                if bot_id and str(p.get("user_id")) == bot_id:
                    continue  # bot 自己（事件转发/回执）绝不采
                if run is None or ms <= _iso_ms(run.get("started_at")):
                    continue  # 无在飞 run / 窗口外（run 起点之前的话）
                if mm_intake.check_membership(mm, channel_id, str(p.get("user_id")), cache) != mm_intake.MEMBER:
                    continue  # 授权面外忽略（与 /pa 入站同门，不回执）
                path = notes_dir / f"{ms}_{p['id']}.md"
                if path.exists():
                    continue  # (message_id 文件名) 幂等
                notes_dir.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    f"---\nuser_id: \"{p.get('user_id')}\"\nmessage_id: \"{p['id']}\"\n"
                    f"created_at_ms: {ms}\nchannel: \"{channel_id}\"\nrun_id: {run['id']}\n---\n\n"
                    f"{str(p.get('message')).strip()}\n", encoding="utf-8")
                taken += 1
            if newest > since:
                database.set_watermark(wm_key, str(newest))
            if taken and notifier is not None:
                notifier._post_bound_channel(
                    channel_id,
                    f"[订阅直通] 已记入 run #{run['id']}（{project}）下一轮上下文：{taken} 条"
                    f"（仅参考非判据，不影响验收）", f"relay-run{run['id']}")
                print(f"[relay] {project} 采纳 {taken} 条人话 → {notes_dir}", file=sys.stderr)
            total += taken
        except Exception as e:  # noqa: BLE001——单频道 fail-open 不连坐
            print(f"[relay][warn] {project} 频道直通异常（fail-open）: {type(e).__name__}: {e}", file=sys.stderr)
    return total


def _iso_ms(iso: str | None) -> int:
    """run.started_at（ISO）→ ms epoch（解析失败 = 0 = 窗口全开，宁采勿漏取向由调用方窗口语义兜底）。"""
    if not iso:
        return 0
    from datetime import datetime
    try:
        return int(datetime.fromisoformat(iso).timestamp() * 1000)
    except ValueError:
        return 0
