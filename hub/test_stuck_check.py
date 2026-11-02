"""stuck_check.py 单测——卡死 run 带外墙钟监督（add-stuck-run-watchdog 任务 2.4/3.4，
spec: hub-stuck-run-watchdog）。

fake clock（now_fn 注入）+ tmp 监督面树 + FakeStatusRunner/FakeDB/Notifier 桩：判据各
分支、去抖三档、消解/离场闭环、kill 双窗与赛跑复测（mtime 两次读数注入）、失败闩、
单 run 异常不连坐。
"""
from __future__ import annotations

import json
import os
import types
from pathlib import Path

import stuck_check

NOW = 1_800_000_000.0  # 固定「现在」；监督面 mtime 用 os.utime 回拨构造静默龄


def _cfg(**over):
    base = dict(stuck_after_minutes=120, stuck_kill=False, stuck_kill_grace_minutes=30,
                stuck_kill_floor_minutes=None)
    base.update(over)
    return types.SimpleNamespace(**base)


class FakeDB:
    def __init__(self, runs, items=None):
        self._runs, self._items = runs, items or {}

    def runs_in_status(self, status):
        return [r for r in self._runs if r["status"] == status]

    def get_item(self, item_id):
        return self._items.get(item_id)


class FakeStatusRunner:
    """status 可编程（缺省 running）；stop 记调用可抛（stop_raise 注入）。"""

    def __init__(self, states=None, status_raise=None, stop_raise=None):
        self._states = states or {}
        self._status_raise = status_raise or {}
        self._stop_raise = stop_raise
        self.stops: list[tuple[str, str | None]] = []

    def status(self, container_ref, docker_host=None):
        if container_ref in self._status_raise:
            raise self._status_raise[container_ref]
        return types.SimpleNamespace(state=self._states.get(container_ref, "running"), exit_code=None)

    def stop(self, container_ref, docker_host=None):
        self.stops.append((container_ref, docker_host))
        if self._stop_raise is not None:
            raise self._stop_raise


class FakeNotifier:
    def __init__(self):
        self.msgs: list[str] = []

    def notify_admin(self, text):
        self.msgs.append(text)


def _run(rid=1, project="proj-a", item_id=10, run_dir=None, host=None):
    return {"id": rid, "project": project, "status": "running", "queue_item_id": item_id,
            "container_ref": f"c-{rid}", "run_dir": str(run_dir), "runner_docker_host": host}


def _face(tmp_path: Path, *, stdout_age_min=None, events_age_min=None, double_stamp=False,
          stem="my-prd", stamp="20261102_100000_my-prd"):
    """构造监督面树；age_min=None = 该面缺席。返回 run_dir。"""
    run_dir = tmp_path / "state" / "runs" / "proj-a" / stamp
    run_dir.mkdir(parents=True)
    if stdout_age_min is not None:
        sf = tmp_path / "state" / f"{stamp.split('_', 1)[0]}.stdout.log"
        sf.write_text("[run] tee\n", encoding="utf-8")
        os.utime(sf, (NOW - stdout_age_min * 60, NOW - stdout_age_min * 60))
    if events_age_min is not None:
        prefix = f"m_{stamp.split('_', 1)[0]}" if double_stamp else stamp.split("_", 1)[0]
        ef = run_dir.parent / f"{prefix}_{stem}.events.jsonl"
        ef.write_text(json.dumps({"v": 1, "event": "critic_verdict", "seq": 1}) + "\n", encoding="utf-8")
        os.utime(ef, (NOW - events_age_min * 60, NOW - events_age_min * 60))
    return run_dir


def _db(run_dir, rid=1, project="proj-a", host=None):
    return FakeDB([_run(rid, project, run_dir=run_dir, host=host)],
                  {10: {"prd_path": "/x/y/my-prd.md"}})


def _go(cfg, database, runner=None, notifier=None, seen=None, stop_raise=None):
    seen = seen if seen is not None else set()
    r = FakeStatusRunner(stop_raise=stop_raise) if runner is None else runner
    stuck_check.check_stuck_runs(cfg, database, r, notifier=notifier, seen=seen,
                                 now_fn=lambda: NOW)
    return r, seen


# ── supervision_mtime 判据 ─────────────────────────────────────────

def test_mtime_takes_max_of_present_faces(tmp_path):
    rd = _face(tmp_path, stdout_age_min=10, events_age_min=50)
    assert stuck_check.supervision_mtime(rd, "my-prd") == NOW - 10 * 60  # max = 最近写面
    rd2 = _face(tmp_path / "b", stdout_age_min=50, events_age_min=10)
    assert stuck_check.supervision_mtime(rd2, "my-prd") == NOW - 10 * 60
    rd3 = _face(tmp_path / "c", stdout_age_min=30)
    assert stuck_check.supervision_mtime(rd3, "my-prd") == NOW - 30 * 60  # 单 stdout 面
    rd4 = _face(tmp_path / "d", events_age_min=30)
    assert stuck_check.supervision_mtime(rd4, "my-prd") == NOW - 30 * 60  # 单 events 面


def test_mtime_both_missing_returns_none(tmp_path):
    assert stuck_check.supervision_mtime(_face(tmp_path), "my-prd") is None


def test_mtime_double_stamp_glob_fallback_hits(tmp_path):
    # pa inject 双前缀形态（run #73/#74 实证）：精确拼未命中 → *_{stem} glob 兜底命中
    rd = _face(tmp_path, events_age_min=20, double_stamp=True)
    assert stuck_check.supervision_mtime(rd, "my-prd") == NOW - 20 * 60  # stdout 缺席、events 兜底命中


# ── 告警档（默认唯一动作）─────────────────────────────────────────

def test_stale_triggers_alert_once_debounced(tmp_path):
    rd = _face(tmp_path, stdout_age_min=150)
    n = FakeNotifier()
    seen: set = set()
    _go(_cfg(), _db(rd), notifier=n, seen=seen)
    assert len(n.msgs) == 1 and "疑似卡死 run #1" in n.msgs[0]
    _go(_cfg(), _db(rd), notifier=n, seen=seen)  # 第二轮
    assert len(n.msgs) == 1  # 去抖：不重复


def test_below_threshold_zero_behavior(tmp_path):
    rd = _face(tmp_path, stdout_age_min=100)  # 单 dev 会话中段：100 < 120
    n = FakeNotifier()
    r, _ = _go(_cfg(), _db(rd), notifier=n)
    assert n.msgs == [] and r.stops == []


def test_both_faces_missing_not_judged(tmp_path):
    n = FakeNotifier()
    r, _ = _go(_cfg(), _db(_face(tmp_path)), notifier=n)  # 双缺
    assert n.msgs == []


def test_recovery_sends_resolution_receipt_and_clears(tmp_path):
    rd = _face(tmp_path, stdout_age_min=150)
    seen: set = set()
    n = FakeNotifier()
    _go(_cfg(), _db(rd), notifier=n, seen=seen)
    assert (1, stuck_check.TIER_ALERTED) in seen
    os.utime(rd.parent.parent.parent / "20261102.stdout.log",
             (NOW - 5 * 60, NOW - 5 * 60))  # 恢复写进（stdout tee 面推进）
    _go(_cfg(), _db(rd), notifier=n, seen=seen)
    assert (1, stuck_check.TIER_ALERTED) not in seen
    assert any("消解此前卡死告警" in m for m in n.msgs)


def test_departure_sends_terminal_receipt_and_clears_all_tiers(tmp_path):
    seen = {(1, stuck_check.TIER_ALERTED), (2, stuck_check.TIER_KILL_FAILED)}
    db = FakeDB([], {})  # 全部 run 已自然离场
    n = FakeNotifier()
    _go(_cfg(), db, notifier=n, seen=seen)
    assert seen == set()  # 全档清键
    assert any("已自然到达终态" in m for m in n.msgs)  # 仅 alerted 档有闭环回执
    assert len(n.msgs) == 1  # kill_failed 档静默清（终态通知归 reconcile 既有面）


def test_container_exited_zero_behavior(tmp_path):
    rd = _face(tmp_path, stdout_age_min=150)
    runner = FakeStatusRunner(states={"c-1": "exited"})
    n = FakeNotifier()
    r, _ = _go(_cfg(), _db(rd), runner, notifier=n)
    assert n.msgs == [] and r.stops == []  # 交 reconcile，本面零行为


def test_per_run_exception_no_crosstalk(tmp_path):
    rd1 = _face(tmp_path / "a", stdout_age_min=150)
    rd2 = _face(tmp_path / "b", stdout_age_min=150)
    db = FakeDB([_run(1, run_dir=rd1), _run(2, run_dir=rd2)], {10: {"prd_path": "/x/y/my-prd.md"}})
    runner = FakeStatusRunner(status_raise={"c-1": RuntimeError("daemon 不可达")})
    n = FakeNotifier()
    _go(_cfg(), db, runner, notifier=n)
    assert any("run #2" in m for m in n.msgs)  # run 2 照常判定（不连坐）


# ── kill 通道（flag 默认关 + FLOOR 前置 + 双窗 + 失败闩）──────────

def test_kill_off_alert_only_no_stop(tmp_path):
    rd = _face(tmp_path, stdout_age_min=300)
    r, _ = _go(_cfg(), _db(rd))  # stuck_kill 缺省 False
    assert r.stops == []


def test_kill_below_max_threshold_no_stop(tmp_path):
    rd = _face(tmp_path, stdout_age_min=140)  # 140 < max(120+30, 90)=150
    r, _ = _go(_cfg(stuck_kill=True, stuck_kill_floor_minutes=90), _db(rd))
    assert r.stops == []


def test_kill_floor_dominates_grace(tmp_path):
    rd = _face(tmp_path, stdout_age_min=140)  # 140 < max(150, 200)=200
    r, _ = _go(_cfg(stuck_kill=True, stuck_kill_floor_minutes=200), _db(rd))
    assert r.stops == []  # FLOOR 主导：标定上限未到
    rd2 = _face(tmp_path / "x", stdout_age_min=210)
    r2, _ = _go(_cfg(stuck_kill=True, stuck_kill_floor_minutes=200), _db(rd2))
    assert len(r2.stops) == 1  # 超 FLOOR → kill


def test_kill_double_window_met_stops_once_with_receipt(tmp_path):
    rd = _face(tmp_path, stdout_age_min=160)  # 160 ≥ max(150, 0)
    seen: set = set()
    n = FakeNotifier()
    r, seen = _go(_cfg(stuck_kill=True), _db(rd), notifier=n, seen=seen)
    assert len(r.stops) == 1 and r.stops[0] == ("c-1", None)
    assert (1, stuck_check.TIER_KILL_DONE) in seen
    assert any("watchdog 已 stop 卡死 run #1" in m and "终态归 reconcile 对账" in m
               for m in n.msgs)
    _go(_cfg(stuck_kill=True), _db(rd), notifier=n, seen=seen)  # 容器仍 running 的窗口
    assert len(r.stops) == 1  # kill_done 去抖：不重复 stop


def test_kill_routes_per_run_docker_host_snapshot(tmp_path):
    rd = _face(tmp_path, stdout_age_min=160)
    r, _ = _go(_cfg(stuck_kill=True), _db(rd, host="tcp://10.0.0.9:2375"))
    assert r.stops == [("c-1", "tcp://10.0.0.9:2375")]  # per-run 快照路由（runner-pool D2）


def test_kill_recheck_recovered_aborts_with_receipt(tmp_path, monkeypatch):
    rd = _face(tmp_path, stdout_age_min=160)
    reads = {"n": 0}
    real = stuck_check.supervision_mtime

    def flaky(run_dir, prd_stem):
        reads["n"] += 1
        if reads["n"] == 1:
            return NOW - 160 * 60  # 判定读数：超双窗
        return NOW - 5 * 60        # 复测读数：恰在本窗恢复（赛跑碰撞）

    monkeypatch.setattr(stuck_check, "supervision_mtime", flaky)
    seen: set = set()
    n = FakeNotifier()
    r, seen = _go(_cfg(stuck_kill=True), _db(rd), notifier=n, seen=seen)
    assert r.stops == []  # 复测发现恢复 → 放弃 kill
    assert (1, stuck_check.TIER_ALERTED) not in seen
    assert any("复测发现恢复" in m and "放弃 kill" in m for m in n.msgs)
    monkeypatch.setattr(stuck_check, "supervision_mtime", real)


def test_stop_failure_latches_no_retry_bombardment(tmp_path):
    rd = _face(tmp_path, stdout_age_min=160)
    seen: set = set()
    n = FakeNotifier()
    r, seen = _go(_cfg(stuck_kill=True), _db(rd), notifier=n, seen=seen,
                  stop_raise=RuntimeError("daemon unreachable"))
    assert len(r.stops) == 1  # 首次尝试
    assert (1, stuck_check.TIER_KILL_FAILED) in seen
    assert any("stop 失败" in m and "不再重试" in m for m in n.msgs)
    for _ in range(3):  # 后续三个槽（≈9 分钟）：闩生效
        _go(_cfg(stuck_kill=True), _db(rd), notifier=n, seen=seen)
    assert len(r.stops) == 1  # 闩：零重试
    assert len([m for m in n.msgs if "stop 失败" in m]) == 1  # 升级告警也恰一条（不轰炸）
