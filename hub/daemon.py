"""排度常驻循环——tick_dispatch + reconcile_running 周期驱动（add-hub-slice-2a 3.1/3.2，spec: execution-hub「排度常驻服务」）。

run_forever(lc, interval, stop_event)：启动先 reconcile_startup（幂等对账先于排度），
随后按间隔循环 tick + reconcile。单迭代异常全捕获记日志续跑（无静默失败）+
连续异常计数告警（干净迭代清零）。优雅退出 = stop_event 置位后**完成当前迭代**再退，
MUST NOT 杀在跑容器（docker -d 进程树分离，重启后 reconcile_startup 状态机接管）。

信号路由（design D4）：容器形态 PID 1 是 uvicorn——SIGTERM 由 FastAPI lifespan
shutdown 置 stop_event + join（见 api.py）；install_signal_handlers 仅服务裸进程
形态（serve.py 直跑 / 手动调试）。
"""
from __future__ import annotations

import signal
import sys
import threading
import time

ALERT_AFTER = 3  # 连续异常告警阈值：≥3 次连续失败升 ALERT 级日志（不中断循环）


def run_forever(
    lc,
    interval: float,
    stop_event: threading.Event,
    *,
    sleep=time.sleep,
) -> int:
    """常驻排度循环；返回退出时连续异常计数（0 = 干净退出）。

    sleep 可注入（fake clock 测试）。启动对账失败不阻循环（下轮 reconcile_running
    再对）；迭代内不检查 stop_event——当前迭代必然完整收尾（spec「优雅退出不动容器」）。
    """
    try:
        lc.reconcile_startup()
    except Exception as e:  # noqa: BLE001——对账失败也不该杀死常驻进程
        print(f"[daemon][warn] 启动对账异常（继续进循环，后续迭代再对）: {e}", file=sys.stderr)
    consecutive = 0
    while not stop_event.is_set():
        try:
            lc.tick_dispatch()
            lc.reconcile_running()
            consecutive = 0
        except Exception as e:  # noqa: BLE001——docker CLI 瞬时失败等：记日志续跑
            consecutive += 1
            level = "[daemon][ALERT]" if consecutive >= ALERT_AFTER else "[daemon][warn]"
            print(f"{level} 迭代异常（连续 x{consecutive}）: {e}", file=sys.stderr)
        if not stop_event.is_set():
            sleep(interval)
    print(f"[daemon] 优雅退出：当前迭代已收尾，连续异常 {consecutive}（在跑容器不动，重启经对账接管）")
    return consecutive


def install_signal_handlers(stop_event: threading.Event) -> None:
    """裸进程形态：SIGTERM/SIGINT → stop_event（只能主线程调用；uvicorn 形态不装）。"""
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop_event.set())
