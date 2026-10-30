"""hub 服务入口——uvicorn + daemon 线程装配（add-hub-slice-2a 4.2，design D4/D6）。

容器形态 PID 1：uvicorn 收 SIGTERM → FastAPI lifespan shutdown → stop_event +
join（优雅退出路由链）；compose stop_grace_period 30s 兜底慢迭代（见 api.py 头）。
裸进程直跑（调试）另装 daemon.install_signal_handlers。
"""
from __future__ import annotations

import os
import re

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
    # env 一次性 seed 迁移（add-mm-channel-intake D2）：表空才灌（幂等），此后
    # HUB_MM_PROJECT_CHANNELS env 键废止——绑定表是唯一真相。
    database.seed_project_channels(cfg.project_channels)
    # env 两键一次性 seed（add-hub-runner-pool D5，同上先例）：首启表空且 HUB_DOCKER_HOST
    # 有值才灌一行 runner（此后 runners 表是唯一真相，env 静默无效）；值不合形
    # seed 内部抛 DBError 不捕获 = 启动 fail loud（地址非凭据可入消息）。
    if cfg.docker_host:
        _seed_name = "seed-" + re.sub(r"[^0-9a-z]+", "-", cfg.docker_host.removeprefix("tcp://")).strip("-")
        database.seed_runner(_seed_name, cfg.docker_host,
                             str(cfg.runner_mount_root or cfg.data_root))
    engine = queue_engine.QueueEngine(database, cfg.max_containers, cfg.max_retries, cfg.image_tag_default)
    try:
        key = vault_store.load_key()
    except vault_store.VaultKeyMissing:
        key = None
    vault = vault_store.CredentialVault(cfg.data_root, key) if key is not None else None
    # MM 客户端单实例共享（add-mm-channel-intake）：Notifier 出站推递与 /mm/intake
    # 成员查询/频道 post 同一 bot 身份；bot token 或 base_url 缺 = None（出站通道
    # 关闭 + 入站成员判据恒 UNKNOWN fail-closed 拒绝——判据不可得绝不旁路）。
    mm_client = notify_mod.HttpMMClient(cfg) if (cfg.mm_bot_token and cfg.mm_base_url) else None
    # database = 归属路由判据（6.x）：Notifier 按绑定表路由 run 终态/语义事件/放弃告警
    notifier = notify_mod.build_notifier(cfg, mm=mm_client, database=database)
    lc = lifecycle_mod.Lifecycle(
        database, engine, runner_mod.DockerRunner(), cfg,
        vault=vault, notifier=notifier,
    )  # docker_host 是 per-call 参数（add-hub-runner-pool D2）：派发选机/对账快照
    # 逐次传入，运行时不再构造固化；无 enabled 远程行 = 恒 None 调用（本机 socket 零变化）
    interval = float(os.environ.get("HUB_TICK_INTERVAL") or 5)
    app = create_app(database, engine, lc, vault=vault, tick_interval=interval, mm_client=mm_client)
    return app, lc, cfg


def main() -> None:
    app, _, _ = build_app()
    # 信号路由不在此装：uvicorn 收 SIGTERM → lifespan shutdown → stop_event + join（api.py）；
    # app.state.stop_event 由 lifespan 赋值，此刻尚未存在。daemon.install_signal_handlers
    # 仅服务不经 uvicorn 的嵌入场景。
    uvicorn.run(app, host=os.environ.get("HUB_HOST") or "0.0.0.0", port=int(os.environ.get("HUB_PORT") or 8000))


if __name__ == "__main__":
    main()
