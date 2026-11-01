"""daemon.py 测试——排度常驻循环（add-hub-slice-2a 3.1/3.2，spec: execution-hub「排度常驻服务」）。

fake clock（sleep 注入）+ fake lifecycle 驱动：循环派发 / 异常续跑与计数 /
优雅退出完成当前迭代不动容器 / 信号 handler 装配。
"""
from __future__ import annotations

import signal
import threading
import types

import daemon


class FakeLC:
    """fake lifecycle：记 tick/reconcile/startup 次数；raise_at 模拟单迭代异常。

    cfg.stale_check_ticks 默认天文数——既有测试零触发陈旧检测（4a 挂点测试单独设小值）。
    """

    def __init__(self):
        self.ticks = 0
        self.reconciles = 0
        self.startups = 0
        self.raise_at: set[int] = set()
        # 三低频槽默认天文数——既有测试零触发（各挂点测试单独设小值）
        self.cfg = types.SimpleNamespace(stale_check_ticks=10**9, probe_ticks=10**9, drift_ticks=10**9,
                                       digest_ticks=10**9, event_ticks=10**9)
        self.notifier = None

    def reconcile_startup(self):
        self.startups += 1

    def tick_dispatch(self):
        self.ticks += 1
        if self.ticks in self.raise_at:
            raise RuntimeError(f"docker CLI 瞬时失败（模拟）x{self.ticks}")

    def reconcile_running(self):
        self.reconciles += 1

    def sweep_confirmations(self):  # 2.4 确认环超时挂点替身（计数供断言）
        self.sweeps = getattr(self, "sweeps", 0) + 1

    def check_config_drift(self):  # add-config-drift-watchdog 2.2 挂点替身（计数供断言）
        self.drifts = getattr(self, "drifts", 0) + 1

    def emit_daily_digest(self):  # add-hub-daily-digest 1.3 挂点替身（计数供断言）
        self.digests = getattr(self, "digests", 0) + 1

    def poll_runner_events(self):  # add-event-sources 1.1 挂点替身（计数供断言）
        self.event_polls = getattr(self, "event_polls", 0) + 1


class StopAfter:
    """fake sleep：第 n 次间隔时置 stop_event（零真睡眠驱动循环收尾）。"""

    def __init__(self, stop_event: threading.Event, n: int):
        self.se, self.n, self.i = stop_event, n, 0

    def __call__(self, interval: float) -> None:
        self.i += 1
        if self.i >= self.n:
            self.se.set()


# ── 3.1 循环驱动 ─────────────────────────────────────────────

def test_loop_dispatches_until_stop():
    lc, se = FakeLC(), threading.Event()
    rc = daemon.run_forever(lc, 0.01, se, sleep=StopAfter(se, 3))
    assert rc == 0
    assert lc.startups == 1          # 启动先 reconcile_startup（幂等对账先于排度）
    assert lc.ticks == 3 and lc.reconciles == 3  # 每迭代 = tick + reconcile


def test_single_iteration_exception_continues():
    lc, se = FakeLC(), threading.Event()
    lc.raise_at = {1, 2}  # 前两迭代抛异常
    rc = daemon.run_forever(lc, 0.01, se, sleep=StopAfter(se, 4))
    assert lc.ticks == 4  # 异常不退出常驻循环，照常迭代
    assert rc == 0        # 迭代 4 干净 → 连续计数清零


def test_consecutive_exceptions_counted_and_alerted(capsys):
    lc, se = FakeLC(), threading.Event()
    lc.raise_at = {1, 2, 3}
    rc = daemon.run_forever(lc, 0.01, se, sleep=StopAfter(se, 3))
    assert rc == 3  # 返回连续异常计数（无干净迭代清零）
    err = capsys.readouterr().err
    assert "ALERT" in err and "x3" in err  # 达阈值告警可见（无静默失败）


# ── 3.2 优雅退出：完成当前迭代、不杀在跑容器 ─────────────────

def test_stop_mid_iteration_completes_current():
    class StopMidTick(FakeLC):
        def __init__(self, se):
            super().__init__()
            self.se = se

        def tick_dispatch(self):
            self.se.set()  # 信号在本迭代 tick 途中到达
            super().tick_dispatch()

    lc, se = StopMidTick(threading.Event()), None
    se = lc.se
    rc = daemon.run_forever(lc, 0.01, se, sleep=lambda interval: (_ for _ in ()).throw(AssertionError("不应再睡")))
    assert rc == 0
    assert lc.ticks == 1 and lc.reconciles == 1  # 当前迭代完整收尾（reconcile 也跑完）后退出
    assert lc.startups == 1


def test_install_signal_handlers_sets_stop_event():
    se = threading.Event()
    old_term, old_int = signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)
    try:
        daemon.install_signal_handlers(se)
        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler) and handler is not signal.SIG_DFL  # 裸进程形态：信号已接管
        handler(None, None)  # 直接调 handler（不起真信号打断 pytest）
        assert se.is_set()
    finally:
        signal.signal(signal.SIGTERM, old_term)
        signal.signal(signal.SIGINT, old_int)


# ── add-hub-slice-4a 3.2：镜像陈旧检测低频挂点（每 N tick 一次）────────

def test_stale_check_runs_every_n_ticks(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(daemon.stale_check, "check_stale",
                        lambda cfg, seen, notifier=None, client=None: calls.append({"seen": seen}))
    lc, se = FakeLC(), threading.Event()
    lc.cfg = types.SimpleNamespace(stale_check_ticks=2)  # 每 2 tick 一次
    rc = daemon.run_forever(lc, 0.01, se, sleep=StopAfter(se, 5))
    assert rc == 0 and lc.ticks == 5
    assert len(calls) == 2  # tick 2 / tick 4 触发；tick 1/3/5 不触发
    assert calls[0]["seen"] is calls[1]["seen"]  # 去抖集合进程内同一引用（跨周期记忆）


def test_stale_check_exception_does_not_kill_loop(monkeypatch):
    def boom(cfg, seen, notifier=None, client=None):
        raise RuntimeError("gh 挂了")

    monkeypatch.setattr(daemon.stale_check, "check_stale", boom)
    lc, se = FakeLC(), threading.Event()
    lc.cfg = types.SimpleNamespace(stale_check_ticks=1)  # 每 tick 都查 → 必炸
    rc = daemon.run_forever(lc, 0.01, se, sleep=StopAfter(se, 3))
    assert rc == 0 and lc.ticks == 3  # 检测异常只记日志，循环续跑（观察面不崩执行面）


# ── add-config-drift-watchdog 2.2：第三低频槽（每 drift_ticks 个 tick 一次）──

def test_drift_check_runs_every_n_ticks():
    lc, se = FakeLC(), threading.Event()
    lc.cfg = types.SimpleNamespace(stale_check_ticks=10**9, probe_ticks=10**9, drift_ticks=2)
    rc = daemon.run_forever(lc, 0.01, se, sleep=StopAfter(se, 5))
    assert rc == 0 and lc.ticks == 5
    assert lc.drifts == 2  # tick 2 / tick 4 触发；tick 1/3/5 不触发


def test_drift_check_exception_does_not_kill_loop(capsys):
    class BoomDrift(FakeLC):
        def check_config_drift(self):
            raise RuntimeError("intent IO 挂了")

    lc, se = BoomDrift(), threading.Event()
    lc.cfg = types.SimpleNamespace(stale_check_ticks=10**9, probe_ticks=10**9, drift_ticks=1)
    rc = daemon.run_forever(lc, 0.01, se, sleep=StopAfter(se, 3))
    assert rc == 0 and lc.ticks == 3  # 看门狗异常只记日志，循环续跑（观察面不崩执行面）
    assert "[drift][warn]" in capsys.readouterr().err


def test_digest_runs_every_n_ticks():
    lc, se = FakeLC(), threading.Event()
    lc.cfg = types.SimpleNamespace(stale_check_ticks=10**9, probe_ticks=10**9, drift_ticks=10**9,
                                   digest_ticks=2)
    rc = daemon.run_forever(lc, 0.01, se, sleep=StopAfter(se, 5))
    assert rc == 0 and lc.digests == 2  # tick 2 / 4 触发


def test_event_poll_runs_every_n_ticks_and_survives_errors(capsys):
    class BoomPoll(FakeLC):
        def poll_runner_events(self):  # 计数后抛——同时断言「触发了」与「不崩循环」
            self.event_polls = getattr(self, "event_polls", 0) + 1
            raise RuntimeError("gh 挂了")

    lc, se = BoomPoll(), threading.Event()
    lc.cfg = types.SimpleNamespace(stale_check_ticks=10**9, probe_ticks=10**9, drift_ticks=10**9,
                                   digest_ticks=10**9, event_ticks=2)
    rc = daemon.run_forever(lc, 0.01, se, sleep=StopAfter(se, 5))
    assert rc == 0 and lc.ticks == 5 and lc.event_polls == 2  # tick 2/4 触发，异常不崩循环
    assert "[events][warn]" in capsys.readouterr().err


def test_digest_exception_does_not_kill_loop(capsys):
    class BoomDigest(FakeLC):
        def emit_daily_digest(self):
            raise RuntimeError("db 挂了")

    lc, se = BoomDigest(), threading.Event()
    lc.cfg = types.SimpleNamespace(stale_check_ticks=10**9, probe_ticks=10**9, drift_ticks=10**9,
                                   digest_ticks=1, event_ticks=10**9)
    rc = daemon.run_forever(lc, 0.01, se, sleep=StopAfter(se, 3))
    assert rc == 0 and lc.ticks == 3
    assert "[digest][warn]" in capsys.readouterr().err
