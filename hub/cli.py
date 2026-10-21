"""hub CLI——面板前的人肉驱动面（add-hub-skeleton 任务 8.2：入队/查状态两命令）。

数据面走 api.py 同一内部函数（validate_and_enqueue / queue_view）——CLI 与 API
是同一逻辑的两种皮，不另起炉灶。排度循环（tick 服务）不在本命令面——切片 1
以 lifecycle.tick_dispatch 手动驱动为主。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import api
import config
import db
import queue_engine


def main(argv: list[str] | None = None, *, runner=None) -> int:
    """runner 接缝预留（后续 loop 命令挂容器轮询）；enqueue/status 不触容器。"""
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
    args = ap.parse_args(argv)

    cfg = config.load_config()
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
