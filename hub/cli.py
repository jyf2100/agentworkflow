"""hub CLI——面板前的人肉驱动面（add-hub-skeleton 8.2 + add-hub-slice-2a 1.3）。

数据面走 api.py 同一内部函数（validate_and_enqueue / queue_view）——CLI 与 API
是同一逻辑的两种皮，不另起炉灶。vault-import 是保险箱 bootstrap 单用户导入
（幂等；输出只含字段名，绝不回显明文值——spec: hub-credential-vault）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import api
import config
import db
import queue_engine
import vault_store


def main(argv: list[str] | None = None, *, runner=None) -> int:
    """runner 接缝预留（后续 loop 命令挂容器轮询）；enqueue/status/vault-import 不触容器。"""
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
    args = ap.parse_args(argv)

    cfg = config.load_config()

    if args.cmd == "vault-import":
        return _vault_import(cfg, from_env=args.from_env)

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
