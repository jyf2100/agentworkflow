"""订阅直通——绑定频道在飞 run 窗口内的授权人话 → human_notes 排队面（add-thread-subscription，spec: hub-mm-intake「订阅直通」；
harden-human-notes-lineage：落点谱系化）。

三条红线：不新增派发授权（Gate = 成员三态既有判据，与 /pa 入站同门）；不中断/不改写
在飞 run（v1 只做排队面——落盘 + 下一 iteration 经 --human-notes 注入，仅上下文非判据）；
授权面外作者忽略。读取通道 fail-open——任何异常不崩排度循环。

关联判据（D1）：绑定频道 + 在飞 run 时间窗（post.create_at > run.started_at）+ 非 bot
作者 → 归该频道最老在飞 run（single-flight 串行下同频道天然单 run）。水位（D3） =
db intake_watermarks 键 thread_relay:{channel_id}，存上次见到的最大 create_at(ms)——
重启安全（水位持久），(message_id 在文件名) 双保险幂等。

归属本体（harden-human-notes-lineage D1/D2，审核 P1-2）：notes 归 **queue_item 谱系**
而非 run 实例——落 `<data_root>/<project>/state/human_notes/qi-<queue_item_id>/`（cfg
data_root 推导，与 lifecycle state_root 同拼法）。谱系键 = run 行 queue_item_id（重派
attempt+1 新 run 新树不改键）→ 旧 iteration 窗口的人话在重派后 pa 合并读面同树可见，
不因换树成死信；hub 写 pa 读同树（state 挂载既有面，零新挂载）。
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


def _lineage_notes_dir(cfg, project: str, run: dict) -> Path:
    """谱系落点（harden-human-notes-lineage D2）：<data_root>/<project>/state/human_notes/qi-<id>/。

    键 = run 行 queue_item_id（NOT NULL 列，idx_runs_item）——重派不改键；与 lifecycle 的
    state_root 拼法同源（cfg.data_root / project / "state"），pa 容器内即 STATE_DIR/human_notes/
    qi-<id>（state 挂载同树）。cfg 而非 run_dir.parent.parent 推导：部署路径归属 config，
    不耦合目录层级布局。"""
    return Path(cfg.data_root) / project / "state" / "human_notes" / f"qi-{run['queue_item_id']}"


def relay_channel_notes(cfg, database, mm, notifier=None, *, member_cache=None) -> list[dict]:
    """单轮直通捕获（daemon 每 relay_ticks 调一次）：全部绑定频道 → 各自最老在飞 run。

    返回本轮采纳笔记数。逐绑定 fail-open（单频道异常不连坐）；无在飞 run 的频道只推
    水位不采集（窗口判据天然过滤）。回执：每频道每轮至多一条汇总（水位只进不退 = 汇总
    天然去抖，绝不逐条刷屏）。
    """
    import mm_intake  # 延迟导入破环（见模块头注）
    router_candidates: list[dict] = []  # add-router-persona：直通拒帖集（授权外/窗外）给解释层
    if mm is None:
        return router_candidates  # bot 通道未配置 = 直通关闭（fail-closed 与 /pa 入站同语义）
    try:
        bindings = database.list_channel_bindings()
        running = database.runs_in_status("running")
    except Exception as e:  # noqa: BLE001——观察面 fail-open
        print(f"[relay][warn] 绑定/在飞读取失败（fail-open）: {type(e).__name__}", file=sys.stderr)
        return 0
    cache = member_cache if member_cache is not None else mm_intake.MemberCache()
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
            notes_dir = _lineage_notes_dir(cfg, project, run) if run else None
            taken, newest = 0, since
            for p in posts:
                ms = _post_ms(p)
                newest = max(newest, ms)
                if not p.get("id") or not p.get("message", "").strip():
                    continue
                if bot_id and str(p.get("user_id")) == bot_id:
                    continue  # bot 自己（事件转发/回执）绝不采
                if run is None or ms <= _iso_ms(run.get("started_at")):
                    router_candidates.append({"channel_id": channel_id,
                                              "user_id": str(p.get("user_id")),
                                              "message": str(p.get("message"))})
                    continue  # 窗外——不直通但可问答（add-router-persona D6 互斥分流）
                if mm_intake.check_membership(mm, channel_id, str(p.get("user_id")), cache) != mm_intake.MEMBER:
                    router_candidates.append({"channel_id": channel_id,
                                              "user_id": str(p.get("user_id")),
                                              "message": str(p.get("message"))})
                    continue  # 授权面外不直通（与 /pa 入站同门）但可问答
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
            pass  # taken 计数已移入回执文本
        except Exception as e:  # noqa: BLE001——单频道 fail-open 不连坐
            print(f"[relay][warn] {project} 频道直通异常（fail-open）: {type(e).__name__}: {e}", file=sys.stderr)
    return router_candidates


def _iso_ms(iso: str | None) -> int:
    """run.started_at（ISO）→ ms epoch（解析失败 = 0 = 窗口全开，宁采勿漏取向由调用方窗口语义兜底）。"""
    if not iso:
        return 0
    from datetime import datetime
    try:
        return int(datetime.fromisoformat(iso).timestamp() * 1000)
    except ValueError:
        return 0
