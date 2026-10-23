"""hub CLI——面板前的人肉驱动面（add-hub-skeleton 8.2 + add-hub-slice-2a 1.3）。

数据面走 api.py 同一内部函数（validate_and_enqueue / queue_view）——CLI 与 API
是同一逻辑的两种皮，不另起炉灶。vault-import 是保险箱 bootstrap 单用户导入
（幂等；输出只含字段名，绝不回显明文值——spec: hub-credential-vault）。
check-notify 是通知通道自检（RUNBOOK §16 联调一键化：token 有效性 / 收件人
解析 / DM 两步 / 可选频道，逐步 ✓/✗，失败非零退出）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import api
import config
import db
import notify
import queue_engine
import vault_store


def main(argv: list[str] | None = None, *, runner=None, mm=None) -> int:
    """runner 接缝预留（后续 loop 命令挂容器轮询）；mm = check-notify 测试注入接缝。"""
    ap = argparse.ArgumentParser(prog="hub-cli", description="pa 执行 hub 人肉驱动面")
    ap.add_argument("--db", default=None, help="SQLite 路径（缺省 <HUB_DATA_ROOT>/hub.sqlite3）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    en = sub.add_parser("enqueue", help="入队一份 PRD")
    en.add_argument("--project", required=True)
    en.add_argument("--prd", required=True, help="PRD md 宿主机路径")
    en.add_argument("--tag", default=None, help="镜像 tag（缺省取 HUB_IMAGE_TAG_DEFAULT 固化）")
    en.add_argument("--by", default=None, help="入队人（缺省 HUB_USER_ID）")
    st = sub.add_parser("status", help="查队列状态")
    st.add_argument("--status", default=None, help="过滤 pending/running/done/failed")
    vi = sub.add_parser("vault-import", help="把 hub 进程 env 凭据导入保险箱（单用户 bootstrap）")
    vi.add_argument("--from-env", action="store_true", help="从 hub 进程 env 导入（当前唯一来源；交互录入归切片 2b）")
    cn = sub.add_parser("check-notify", help="通知通道自检（RUNBOOK §16 联调一键化）")
    cn.add_argument("--to", default=None, help="测试收件人（26-hex mm_user_id 或 username；缺省 HUB_USER_ID 走解析路径）")
    cn.add_argument("--channel", default=None, help="附加频道 id 连通性测试（对应 HUB_MM_PROJECT_CHANNELS 值）")
    args = ap.parse_args(argv)

    cfg = config.load_config()

    if args.cmd == "vault-import":
        return _vault_import(cfg, from_env=args.from_env)

    if args.cmd == "check-notify":
        return _check_notify(cfg, mm=mm, to=args.to, channel=args.channel)

    database = db.HubDB(Path(args.db) if args.db else cfg.data_root / "hub.sqlite3")
    engine = queue_engine.QueueEngine(
        database, cfg.max_containers, cfg.max_retries, cfg.image_tag_default
    )

    if args.cmd == "enqueue":
        try:
            item = api.validate_and_enqueue(
                engine, project=args.project, prd_path=args.prd,
                image_tag=args.tag, enqueued_by=args.by or cfg.user_id,
            )
        except api.ApiInputError as e:
            print(f"✗ {e}", file=sys.stderr)
            return 2
        print(f"✓ 已入队 #{item['id']} {item['project']} [{item['image_tag']}] → {item['status']}")
        return 0

    if args.cmd == "status":
        try:
            rows = api.queue_view(database, args.status)
        except api.ApiInputError as e:
            print(f"✗ {e}", file=sys.stderr)
            return 2
        for r in rows:
            err = f"  last_error={r['last_error']}" if r["last_error"] else ""
            print(f"#{r['id']:>4} {r['project']:<20} {r['status']:<8} attempt={r['attempt']}{err}")
        print(f"共 {len(rows)} 项")
        return 0

    return 1  # 不可达（subparsers required）


def _check_notify(cfg: config.HubConfig, *, mm, to: str | None, channel: str | None) -> int:
    """通知通道自检（三步 + 可选频道；RUNBOOK §16 联调 4 项的一键化）。

    逐步 ✓/✗，任一 ✗ 即停并返回 1（后续步骤依赖前步结果）。凭据绝不进输出。
    """
    missing = [name for name, v in (("HUB_MM_BOT_TOKEN", cfg.mm_bot_token),
                                    ("HUB_MM_BASE_URL", cfg.mm_base_url)) if not v]
    if missing:
        print(f"✗ 通知通道关闭（缺 {', '.join(missing)}）——hub 行为不受影响，配齐后重跑", file=sys.stderr)
        return 1
    client = mm or notify.HttpMMClient(cfg)
    try:
        me = client.me_id()
        print(f"✓ 1/3 bot token 有效（bot id {me}）")
    except Exception as e:  # noqa: BLE001——自检面：报类型不报响应体（防凭据泄漏进日志）
        print(f"✗ 1/3 bot token 无效或 MM base_url 错（{type(e).__name__}；token 不回显）", file=sys.stderr)
        return 1
    recipient = to or cfg.user_id
    import re as _re
    if _re.fullmatch(r"[0-9a-f]{26}", recipient):
        uid = recipient
        print("✓ 2/3 收件人为 mm_user_id 形态，直发（不走 username 解析）")
    else:
        try:
            uid = client.lookup_user_id(recipient)
        except Exception as e:  # noqa: BLE001
            print(f"✗ 2/3 username {recipient!r} 解析异常（{type(e).__name__}）", file=sys.stderr)
            return 1
        if uid is None:
            print(f"✗ 2/3 username {recipient!r} 解析失败（查无此用户或无权限）", file=sys.stderr)
            return 1
        print(f"✓ 2/3 username {recipient!r} → {uid}")
    try:
        client.post_dm(uid, "[pa-hub] 通知通道自检测试消息（check-notify 发出；收到 = DM 链路通）")
        print("✓ 3/3 DM 两步 API 通（测试私信已发，请在 Mattermost 查收）")
    except Exception as e:  # noqa: BLE001
        print(f"✗ 3/3 DM 推递失败（{type(e).__name__}）", file=sys.stderr)
        return 1
    if channel:
        try:
            client.post_channel(channel, "[pa-hub] 通知通道自检测试消息（check-notify --channel 发出；收到 = 频道通）")
            print(f"✓ 附加 频道 {channel} 广播通")
        except Exception as e:  # noqa: BLE001
            print(f"✗ 附加 频道 {channel} 广播失败（{type(e).__name__}；检查频道 id 与 bot 入频权限）", file=sys.stderr)
            return 1
    print("✓ 通知通道自检通过")
    return 0


def _vault_import(cfg: config.HubConfig, *, from_env: bool) -> int:
    """单用户 bootstrap：hub 进程 env 三值 + 可选路由 → 保险箱（幂等覆盖；不回显明文）。"""
    if not from_env:
        print("✗ vault-import 当前仅支持 --from-env（交互录入归切片 2b）", file=sys.stderr)
        return 2
    try:
        key = vault_store.load_key()
        vault = vault_store.CredentialVault(cfg.data_root, key)
        env = cfg.user_env()
        missing = [k for k in vault_store.VAULT_REQUIRED_KEYS if not env.get(k)]
        if missing:
            print(f"✗ 缺必需凭据（{', '.join(missing)}）——hub 进程 env 不全，不猜", file=sys.stderr)
            return 2
        vault.set_user(cfg.user_id, env)
    except vault_store.VaultError as e:
        print(f"✗ {e}", file=sys.stderr)
        return 2
    fields = ", ".join(sorted(env))
    print(f"✓ 已导入 user={cfg.user_id} 字段：{fields}（值不回显；重复导入幂等覆盖）")
    return 0


if __name__ == "__main__":  # 容器/宿主直跑入口（缺它则 `python3 cli.py …` 只 import 即静默退出）
    sys.exit(main())
