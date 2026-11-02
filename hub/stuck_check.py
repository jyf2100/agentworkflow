"""卡死 run 带外墙钟监督——hub 可见卷上证据面停写判据 + 动作阶梯（add-stuck-run-watchdog，
spec: hub-stuck-run-watchdog）。

本体（design 本体论声明）：stuck = 容器事实（running）与卷上证据事实（停写）的背离。产物只有
三类：告警 / kill / 回执。**绝不产终态**——kill 只制造确定性 exited 事实，终态归 reconcile
既有对账单写者（不调 _finish_run / on_exit）。

监督面（实施期核实的确定性可寻址面，design「监督面构成」表）：
- 平铺 journal `<run_dir.name>.journal.jsonl`（run_dir 平铺兄弟，_journal_exists 同款拼法，
  iteration/stage 级写面）
- 平铺 events `<stamp>_<prd_stem>.events.jsonl`（events.locate_pa_artifact 既有寻址——
  stem 来自 queue_item.prd_path 非 run_dir slug，stamp 翻倍 glob 兜底同源）
- 不入面：worktree 侧遥测（run_log/handoff-latest，dev-agent STATE_RUNS_DIR = worktree）与
  pa 侧摘要命名的 artifacts/reports 面（ids.run_id = 内容摘要 hub 不可重建）——不可寻址的
  面误用必串扰。

阈值语义（design D2）：hub 可见合法静默 = 单次 dev SDK 会话全长（小时量级）——AFTER 是
可见性非精确判定；kill 的防误杀主力是 FLOOR 标定前置（config 层 kill=1 无 FLOOR 即拒起），
mtime 复测只承担赛跑安全（对本观测面无鉴别力）。

纪律：判据全机械零 LLM；fail-open（单 run 异常不连坐，整体异常由 daemon 槽兜）；
值不入面（告警/回执文本零凭据零 env 值）；去抖进程内三档键 `(run_id, 档)`，重启重告警可接受
（stale_check 同款）；kill 失败进闩——进程生命周期不再重试 stop（默认槽距 3min 下否则构成
重试轰炸）；grace 按「mtime 龄 ≥ max(AFTER+GRACE, FLOOR)」一次度量（design D5 等价关系，
无时间戳键）；run 离开 running 集合 = 自然终态，终态回执一条并清键（告警的闭环）。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Callable

import events

# 去抖档位（进程内 set 键的第二元；同档一次，消解/离场清键）
TIER_ALERTED = "alerted"
TIER_KILL_DONE = "kill_done"
TIER_KILL_FAILED = "kill_failed"


def supervision_mtime(run_dir: Path, prd_stem: str | None) -> float | None:
    """监督面 max(mtime)；双缺返回 None（不判 stuck——failed_to_start 归 reconcile）。

    journal 按 run_dir.name 直接拼（_journal_exists 同款）；events 经 locate_pa_artifact
    （stem 来自 prd_path，孤儿 queue_item → None = 本面跳过，journal 面仍可用）。
    """
    mtimes: list[float] = []
    journal = run_dir.parent / f"{run_dir.name}.journal.jsonl"
    try:
        if journal.is_file():
            mtimes.append(journal.stat().st_mtime)
    except OSError:
        pass  # 单面 stat 异常 = 该面本轮缺席，不抬异常（fail-open）
    if prd_stem:
        try:
            ev_file = events.locate_pa_artifact(run_dir, prd_stem, ".events.jsonl")
            if ev_file is not None:
                mtimes.append(ev_file.stat().st_mtime)
        except OSError:
            pass
    return max(mtimes) if mtimes else None


def check_stuck_runs(
    cfg,
    database,
    runner,
    notifier=None,
    seen: set | None = None,
    *,
    now_fn: Callable[[], float] = time.time,
    stop_fn: Callable[[str, str | None], None] | None = None,
) -> None:
    """单轮监督（daemon 每 N tick 经 lifecycle 薄挂点调一次）。

    cfg.stuck_ticks 已由 daemon modulo 保证节奏，本函数只做事实判定与动作。
    seen = 去抖集合（调用方持有，进程生命周期）；stop_fn 注入接缝（缺省
    runner.stop per-run 快照路由——首个生产调用点）；异常由调用方兜（daemon
    续跑），单 run 异常不连坐（本函数内逐 run try/except）。
    """
    if seen is None:
        seen = set()
    running = database.runs_in_status("running")
    running_ids = {r["id"] for r in running}
    # 自然离场闭环：键里的 run 已不在 running 集合 → 终态回执（仅 alerted 档需要——
    # kill_done 的终态通知归 reconcile 既有终态通知面）+ 清键（含 kill_failed 闩）
    for key in [k for k in seen if k[0] not in running_ids]:
        seen.discard(key)
        if key[1] == TIER_ALERTED and notifier is not None:
            notifier.notify_admin(f"[pa-hub] run #{key[0]} 已自然到达终态（此前曾告警卡死）——监督闭环")
    stop = stop_fn if stop_fn is not None else (
        lambda ref, host: runner.stop(ref, docker_host=host)
    )
    for run in running:
        try:
            _supervise_one(cfg, database, runner, notifier, seen, now_fn, stop, run)
        except Exception as e:  # noqa: BLE001——单 run 异常不连坐（event_poller 同型）
            print(f"[stuck][warn] run #{run['id']} 监督异常（不连坐）: {type(e).__name__}: {e}",
                  file=sys.stderr)


def _supervise_one(cfg, database, runner, notifier, seen, now_fn, stop, run) -> None:
    """单 run 判定与动作（调用方已隔离异常）。双重事实：DB running + 容器 running。"""
    rid = run["id"]
    st = runner.status(run["container_ref"], docker_host=run.get("runner_docker_host"))
    if st.state != "running":
        return  # exited/gone 归 reconcile，本面零行为
    item = database.get_item(run["queue_item_id"])
    prd_stem = Path(item["prd_path"]).stem if item is not None else None
    run_dir = Path(run["run_dir"])
    mtime = supervision_mtime(run_dir, prd_stem)
    if mtime is None:
        return  # 监督面双缺 → 不判（failed_to_start 范畴）
    now = now_fn()
    age_min = (now - mtime) / 60.0
    project = run["project"]
    if age_min < cfg.stuck_after_minutes:
        if (rid, TIER_ALERTED) in seen:
            # 恢复消解：mtime 前进（长会话自然推进）——回执一次清键（闩同清，design D4）
            seen.discard((rid, TIER_ALERTED))
            seen.discard((rid, TIER_KILL_FAILED))
            if notifier is not None:
                notifier.notify_admin(f"[pa-hub] run #{rid}（{project}）监督面恢复写进——消解此前卡死告警")
        return
    if (rid, TIER_ALERTED) not in seen:
        seen.add((rid, TIER_ALERTED))
        text = (
            f"[pa-hub] 疑似卡死 run #{rid}（{project}）：监督面停写 {age_min:.0f} 分钟"
            f"（容器仍 running）。该 run 占住 project 串行名额与峰值名额；"
            f"人工处置见 RUNBOOK「卡死 run 看门狗」节"
        )
        print(f"[stuck][warn] {text}", file=sys.stderr)
        if notifier is not None:
            notifier.notify_admin(text)  # 内部 fail-open 不抛
    if not cfg.stuck_kill:
        return  # 告警是唯一动作（kill 关档）
    if (rid, TIER_KILL_DONE) in seen or (rid, TIER_KILL_FAILED) in seen:
        return  # 已 kill（等容器退出交 reconcile）或失败闩（进程生命周期不重试）
    kill_floor = cfg.stuck_kill_floor_minutes
    threshold_min = max(cfg.stuck_after_minutes + cfg.stuck_kill_grace_minutes, kill_floor or 0)
    if age_min < threshold_min:
        return
    # 赛跑安全复测：恰在本窗内恢复则放弃 kill（对本观测面无鉴别力——防误杀主力是
    # FLOOR 标定门，复测只挡「刚好恢复」的窗口期碰撞，design D2）
    fresh = supervision_mtime(run_dir, prd_stem)
    if fresh is not None and (now_fn() - fresh) / 60.0 < cfg.stuck_after_minutes:
        seen.discard((rid, TIER_ALERTED))
        if notifier is not None:
            notifier.notify_admin(f"[pa-hub] run #{rid}（{project}）复测发现恢复——放弃 kill，发消解回执")
        return
    try:
        stop(run["container_ref"], run.get("runner_docker_host"))
    except Exception as e:  # noqa: BLE001——stop 失败进闩不重试（design D4）
        seen.add((rid, TIER_KILL_FAILED))
        text = f"[pa-hub] run #{rid}（{project}）watchdog stop 失败：{type(e).__name__}——已进闩本轮进程不再重试，请人工处置"
        print(f"[stuck][ERROR] {text}", file=sys.stderr)
        if notifier is not None:
            notifier.notify_admin(text)
        return
    seen.add((rid, TIER_KILL_DONE))
    text = f"[pa-hub] watchdog 已 stop 卡死 run #{rid}（{project}）——容器 exited 事实落卷，终态归 reconcile 对账（可能触发重派）"
    print(f"[stuck][warn] {text}", file=sys.stderr)
    if notifier is not None:
        notifier.notify_admin(text)
