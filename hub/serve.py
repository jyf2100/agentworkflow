"""hub 服务入口——uvicorn + daemon 线程装配（add-hub-slice-2a 4.2，design D4/D6）。

容器形态 PID 1：uvicorn 收 SIGTERM → FastAPI lifespan shutdown → stop_event +
join（优雅退出路由链）；compose stop_grace_period 30s 兜底慢迭代（见 api.py 头）。
裸进程直跑（调试）另装 daemon.install_signal_handlers。
"""
from __future__ import annotations

import os

import uvicorn

import config
import db
import lifecycle as lifecycle_mod
import notify as notify_mod
import queue_engine
import runner as runner_mod
import vault_store
from api import create_app


def build_app(cfg: config.HubConfig | None = None):
    """装配（app, lc, cfg）——测试与 main 共用；凭据来源 vault 优先 env 兜底（D3）。

    未配 master key（VaultKeyMissing）= env-only 合法形态静默落位；配了但坏
    （VaultError 其他）= 明确崩启动，绝不静默吞成 env-only。
    通知通道（4a D8）：bot token + MM base_url 齐 → 构造 Notifier 注入 Lifecycle
    （daemon 陈旧检测同引用复用）；缺任一 = None 注入（通道关闭零行为）。
    """
    cfg = cfg or config.load_config()
    database = db.HubDB(cfg.data_root / "hub.sqlite3")
    engine = queue_engine.QueueEngine(database, cfg.max_containers, cfg.max_retries, cfg.image_tag_default)
    try:
        key = vault_store.load_key()
    except vault_store.VaultKeyMissing:
        key = None
    vault = vault_store.CredentialVault(cfg.data_root, key) if key is not None else None
    notifier = notify_mod.build_notifier(cfg)
    lc = lifecycle_mod.Lifecycle(database, engine, runner_mod.DockerRunner(), cfg, vault=vault, notifier=notifier)
    interval = float(os.environ.get("HUB_TICK_INTERVAL") or 5)
    app = create_app(database, engine, lc, vault=vault, tick_interval=interval)
    return app, lc, cfg


def main() -> None:
    app, _, _ = build_app()
    # 信号路由不在此装：uvicorn 收 SIGTERM → lifespan shutdown → stop_event + join（api.py）；
    # app.state.stop_event 由 lifespan 赋值，此刻尚未存在。daemon.install_signal_handlers
    # 仅服务不经 uvicorn 的嵌入场景。
    uvicorn.run(app, host=os.environ.get("HUB_HOST") or "0.0.0.0", port=int(os.environ.get("HUB_PORT") or 8000))


if __name__ == "__main__":
    main()
