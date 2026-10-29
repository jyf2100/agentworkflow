"""hub 数据层——SQLite 持久（队列不随 hub 重启丢，spec: execution-hub「队列排度」）。

add-hub-skeleton D2（含本体论审核 A3 修正）：queue_items 的 status 枚举
pending/running/done/failed + attempt 计数——run 的 failed 是容器事实，队列项的
failed 是放弃决定，两个生命周期分离；重派 = 回 pending 保留 attempt。
runs 表是索引不是真相（F10：卷上 meta 可重建）。sqlite3 标准库，无 ORM。
"""
from __future__ import annotations

import functools
import re
import sqlite3
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

ITEM_STATUSES = ("pending", "running", "done", "failed")
RUN_STATUSES = ("running", "succeeded", "failed", "failed_to_start")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS queue_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project     TEXT NOT NULL,
    prd_path    TEXT NOT NULL,
    image_tag   TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('pending','running','done','failed')),
    attempt     INTEGER NOT NULL DEFAULT 0,
    enqueued_by TEXT NOT NULL,
    initiator   TEXT,
    deliver_key TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    last_error  TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    queue_item_id  INTEGER NOT NULL REFERENCES queue_items(id),
    project        TEXT NOT NULL,
    run_dir        TEXT NOT NULL,
    container_ref  TEXT,
    status         TEXT NOT NULL CHECK (status IN ('running','succeeded','failed','failed_to_start')),
    started_at     TEXT NOT NULL,
    ended_at       TEXT,
    exit_code      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_queue_items_status ON queue_items(status);
CREATE INDEX IF NOT EXISTS idx_runs_item ON runs(queue_item_id);
CREATE TABLE IF NOT EXISTS project_channels (
    project     TEXT NOT NULL UNIQUE,
    channel_id  TEXT NOT NULL UNIQUE,
    bound_by    TEXT NOT NULL,
    bound_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS confirmation_requests (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id      INTEGER NOT NULL,
    project      TEXT NOT NULL,
    channel_id   TEXT NOT NULL,
    initiator    TEXT NOT NULL,
    prd_text     TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    origin_sha   TEXT NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('requested','confirmed','rejected','expired')),
    created_at   TEXT NOT NULL,
    decided_at   TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_confirm_item ON confirmation_requests(item_id);
CREATE INDEX IF NOT EXISTS idx_confirm_status ON confirmation_requests(status);
CREATE INDEX IF NOT EXISTS idx_confirm_origin ON confirmation_requests(origin_sha);
"""  # deliver_key UNIQUE 索引不在此——旧表存在时 executescript 先于 _migrate 跑，索引引用新列会炸；统一在 _migrate 建
# project_channels 是全新表（add-mm-channel-intake D2）：旧库 executescript 直接建出含 UNIQUE
# 的完整表，无补列问题——UNIQUE 内联声明安全，不走 _migrate。
# confirmation_requests 同为全新表（add-acceptance-spec-confirmation D4：确认环旁表，零侵入
# queue_items CHECK）；item_id UNIQUE = 事件重复到达幂等（同 queue item 只建一行）；
# origin_sha = 原始需求 sha 祖先链根（confirm/metrics 重入队计数判据，D5 上界 3 次）。


class DBError(Exception):
    """非法状态/约束违规——不静默吞。"""


# MM 频道 id 公开事实：26 字符小写字母数字（与 user_id 同形态族；落库与 seed 迁移的
# 机械判据，防频道名/URL 误填——add-mm-channel-intake D2）
MM_CHANNEL_ID_RE = re.compile(r"[0-9a-z]{26}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _locked(method):
    """同连接跨线程串行化（add-hub-slice-2a 3.3：API enqueue 线程 × daemon tick 线程）。

    check_same_thread=False 只解除 Python 层限制，连接对象本身的 execute/commit
    交错仍是竞态（实测：并发下 OperationalError 'cannot commit - no transaction
    is active'）；一把进程内锁把「语句+fetch+commit」收成原子段。WAL + busy_timeout
    兜的是库级锁（跨进程/跨连接），与本锁正交。db 方法平铺无互相调用——Lock 不可
    重入无风险。
    """
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapper


class HubDB:
    """多线程形态（add-hub-slice-2a 3.3：API enqueue 线程 + daemon tick 线程共连接）：
    进程内 @_locked 串行化 + WAL + busy_timeout=5s；写路径全短事务（单语句 + 立即
    commit）。tick 序列本身仍单循环串行（daemon 单线程顺序迭代），pick → start →
    mark_running 的排度顺序无并发窗口。
    """

    def __init__(self, path: Path | str):
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._migrate()

    def _migrate(self) -> None:
        """既有库补列（2b initiator：2a 库升级不丢队列——CREATE IF NOT EXISTS 不加列）。"""
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(queue_items)")}
        if "initiator" not in cols:
            self._conn.execute("ALTER TABLE queue_items ADD COLUMN initiator TEXT")
            self._conn.commit()
        if "deliver_key" not in cols:  # slice-3 D2：幂等键列（投递交接去重）
            self._conn.execute("ALTER TABLE queue_items ADD COLUMN deliver_key TEXT")
            self._conn.commit()
        # UNIQUE 索引统一在此建（新库/旧库同路径）：旧库补列后全 NULL 建立无冲突
        self._conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_queue_items_deliver_key ON queue_items(deliver_key)"
        )
        self._conn.commit()

    @_locked
    def close(self) -> None:
        self._conn.close()

    # ── queue_items ────────────────────────────────────────────────

    @_locked
    def enqueue(
        self, project: str, prd_path: str, image_tag: str, enqueued_by: str,
        initiator: str | None = None, deliver_key: str | None = None,
    ) -> int:
        """deliver_key（slice-3 D2）="{project}:{filename}:{sha256}" 投递幂等键；
        None = 非投递入队（既有 CLI/API 路径，无幂等语义——UNIQUE 对 NULL 不约束）。"""
        ts = _now()
        cur = self._conn.execute(
            "INSERT INTO queue_items (project, prd_path, image_tag, status, attempt, enqueued_by, initiator, deliver_key, created_at, updated_at)"
            " VALUES (?,?,?,?,0,?,?,?,?,?)",
            (project, prd_path, image_tag, "pending", enqueued_by, initiator, deliver_key, ts, ts),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    @_locked
    def get_item_by_deliver_key(self, deliver_key: str) -> dict | None:
        """撞键回查（并发投递 IntegrityError 后取既有项返重复响应——handler 2.4 用）。"""
        row = self._conn.execute(
            "SELECT * FROM queue_items WHERE deliver_key=?", (deliver_key,)
        ).fetchone()
        return dict(row) if row is not None else None

    @_locked
    def get_item(self, item_id: int) -> dict:
        row = self._conn.execute("SELECT * FROM queue_items WHERE id=?", (item_id,)).fetchone()
        if row is None:
            raise DBError(f"queue_item 不存在：{item_id}")
        return dict(row)

    @_locked
    def list_items(self, status: str | None = None) -> list[dict]:
        if status is not None and status not in ITEM_STATUSES:
            raise DBError(f"非法 queue_item status：{status!r}")
        sql = "SELECT * FROM queue_items"
        args: tuple = ()
        if status is not None:
            sql += " WHERE status=?"
            args = (status,)
        return [dict(r) for r in self._conn.execute(sql + " ORDER BY id DESC", args)]  # 最新优先（fix-hub-queue-recency）

    @_locked
    def set_item_status(self, item_id: int, status: str) -> None:
        if status not in ITEM_STATUSES:
            raise DBError(f"非法 queue_item status：{status!r}")
        self._conn.execute(
            "UPDATE queue_items SET status=?, updated_at=? WHERE id=?", (status, _now(), item_id)
        )
        self._conn.commit()

    @_locked
    def bump_attempt(self, item_id: int) -> None:
        self._conn.execute(
            "UPDATE queue_items SET attempt=attempt+1, updated_at=? WHERE id=?", (_now(), item_id)
        )
        self._conn.commit()

    @_locked
    def set_item_error(self, item_id: int, error: str) -> None:
        """记录最近一次失败原因（F13/重派可查；凭据绝不入此字段）。"""
        self._conn.execute(
            "UPDATE queue_items SET last_error=?, updated_at=? WHERE id=?", (error, _now(), item_id)
        )
        self._conn.commit()

    @_locked
    def fail_item(self, item_id: int, error: str) -> None:
        """终态 failed + 原因（放弃决定——与 run 的 failed 是两个生命周期，A3）。"""
        self._conn.execute(
            "UPDATE queue_items SET status='failed', last_error=?, updated_at=? WHERE id=?",
            (error, _now(), item_id),
        )
        self._conn.commit()

    # ── 派发查询（per-project 串行权威判据，spec「队列排度」）────────

    @_locked
    def has_running(self, project: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM queue_items WHERE project=? AND status='running' LIMIT 1", (project,)
        ).fetchone()
        return row is not None

    @_locked
    def count_running(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM queue_items WHERE status='running'").fetchone()[0])

    @_locked
    def oldest_pending_excluding_running_projects(self) -> int | None:
        """最老 pending 且其 project 无 running 项（FIFO——重派项因入队更早天然优先，A3）。"""
        row = self._conn.execute(
            "SELECT id FROM queue_items WHERE status='pending'"
            " AND project NOT IN (SELECT DISTINCT project FROM queue_items WHERE status='running')"
            " ORDER BY id LIMIT 1"
        ).fetchone()
        return int(row[0]) if row is not None else None

    # ── runs（索引非真相，F10）──────────────────────────────────────

    @_locked
    def create_run(self, queue_item_id: int, project: str, run_dir: str) -> int:
        cur = self._conn.execute(
            "INSERT INTO runs (queue_item_id, project, run_dir, status, started_at) VALUES (?,?,?,'running',?)",
            (queue_item_id, project, run_dir, _now()),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    @_locked
    def get_run(self, run_id: int) -> dict:
        row = self._conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise DBError(f"run 不存在：{run_id}")
        return dict(row)

    @_locked
    def runs_for_item(self, queue_item_id: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM runs WHERE queue_item_id=? ORDER BY id", (queue_item_id,)
        )
        return [dict(r) for r in rows]

    @_locked
    def runs_in_status(self, status: str) -> list[dict]:
        if status not in RUN_STATUSES:
            raise DBError(f"非法 run status：{status!r}")
        rows = self._conn.execute("SELECT * FROM runs WHERE status=? ORDER BY id", (status,))
        return [dict(r) for r in rows]

    @_locked
    def list_all_runs(self) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM runs ORDER BY id")
        return [dict(r) for r in rows]

    @_locked
    def set_run_running(self, run_id: int, container_ref: str) -> None:
        self._conn.execute(
            "UPDATE runs SET status='running', container_ref=?, ended_at=NULL, exit_code=NULL WHERE id=?",
            (container_ref, run_id),
        )
        self._conn.commit()

    @_locked
    def set_run_finished(self, run_id: int, status: str, exit_code: int | None = None) -> None:
        if status not in ("succeeded", "failed", "failed_to_start"):
            raise DBError(f"非法 run 终态：{status!r}")
        self._conn.execute(
            "UPDATE runs SET status=?, ended_at=?, exit_code=? WHERE id=?",
            (status, _now(), exit_code, run_id),
        )
        self._conn.commit()

    # ── project_channels（add-mm-channel-intake D2：频道↔项目绑定唯一真相）──

    @_locked
    def get_channel_binding_by_channel(self, channel_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM project_channels WHERE channel_id=?", (channel_id,)
        ).fetchone()
        return dict(row) if row is not None else None

    @_locked
    def get_channel_binding_by_project(self, project: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM project_channels WHERE project=?", (project,)
        ).fetchone()
        return dict(row) if row is not None else None

    def list_channel_bindings(self) -> list[dict]:
        """全表（自检面消费：cli check-intake 逐绑定验 bot 可达）。"""
        rows = self._conn.execute(
            "SELECT * FROM project_channels ORDER BY project"
        ).fetchall()
        return [dict(r) for r in rows]

    @_locked
    def upsert_bind(self, project: str, channel_id: str, bound_by: str) -> list[dict]:
        """双向换绑对称覆盖（1:1 双 UNIQUE）：清 project 旧绑定与 channel_id 既有绑定
        （各 0/1 条）后插新行——DELETE+INSERT 同事务单 commit，原子（旧绑定必清，
        不留双绑/暗绑）。返回被替换旧绑定列表（调用方回执明示，0-2 条）。"""
        ts = _now()
        old = self._conn.execute(
            "SELECT * FROM project_channels WHERE project=? OR channel_id=?", (project, channel_id)
        ).fetchall()
        replaced = [dict(r) for r in old]
        self._conn.execute("DELETE FROM project_channels WHERE project=? OR channel_id=?", (project, channel_id))
        self._conn.execute(
            "INSERT INTO project_channels (project, channel_id, bound_by, bound_at) VALUES (?,?,?,?)",
            (project, channel_id, bound_by, ts),
        )
        self._conn.commit()
        return replaced

    @_locked
    def unbind_by_channel(self, channel_id: str) -> dict | None:
        """按频道解绑；返回被删行（None = 本频道未绑定——调用方教育回执）。"""
        row = self._conn.execute(
            "SELECT * FROM project_channels WHERE channel_id=?", (channel_id,)
        ).fetchone()
        if row is None:
            return None
        self._conn.execute("DELETE FROM project_channels WHERE channel_id=?", (channel_id,))
        self._conn.commit()
        return dict(row)

    @_locked
    def seed_project_channels(self, pairs: dict[str, str]) -> int:
        """HUB_MM_PROJECT_CHANNELS env 一次性 seed 迁移（D2）：表空才整批 seed
        （bound_by=seed-migration、bound_at=seed 时刻）；表非空跳过（幂等——env 再改
        也不重灌，绑定表是唯一真相）。channel_id 形态校验不过的条目拒入并 stderr
        可见（无静默失败）。返回实际入库行数。"""
        if self._conn.execute("SELECT 1 FROM project_channels LIMIT 1").fetchone():
            return 0
        ts = _now()
        count = 0
        for project, channel_id in pairs.items():
            if not MM_CHANNEL_ID_RE.fullmatch(channel_id):
                print(f"[db][warn] seed 拒入（channel_id 非 26 字符 id 形态）：{project}", file=sys.stderr)
                continue
            self._conn.execute(
                "INSERT INTO project_channels (project, channel_id, bound_by, bound_at) VALUES (?,?,?,?)",
                (project, channel_id, "seed-migration", ts),
            )
            count += 1
        self._conn.commit()
        return count

    # ── confirmation_requests（add-acceptance-spec-confirmation D4）──────────

    @_locked
    def insert_confirmation(
        self, item_id: int, project: str, channel_id: str, initiator: str,
        prd_text: str, metrics_json: str, origin_sha: str,
    ) -> int | None:
        """建 requested 行。item_id UNIQUE = 事件重复到达幂等：撞键返回 None
        （调用面按「行已存在跳过」处理，不重复建行/重复 post）。"""
        try:
            cur = self._conn.execute(
                "INSERT INTO confirmation_requests"
                " (item_id, project, channel_id, initiator, prd_text, metrics_json, origin_sha, status, created_at)"
                " VALUES (?,?,?,?,?,?,?,'requested',?)",
                (item_id, project, channel_id, initiator, prd_text, metrics_json, origin_sha, _now()),
            )
            self._conn.commit()
            return int(cur.lastrowid)
        except sqlite3.IntegrityError:
            return None

    def get_confirmation(self, rid: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM confirmation_requests WHERE id=?", (rid,)
        ).fetchone()
        return dict(row) if row is not None else None

    def get_confirmation_by_item(self, item_id: int) -> dict | None:
        """幂等查重 + 确认态回查（2.3 子命令经 #id 定位，此面供事件面）。"""
        row = self._conn.execute(
            "SELECT * FROM confirmation_requests WHERE item_id=?", (item_id,)
        ).fetchone()
        return dict(row) if row is not None else None

    @_locked
    def decide_confirmation(self, rid: int, new_status: str) -> dict | None:
        """状态机转移：仅 requested → confirmed/rejected/expired 合法（D6）。
        非法转移（含终态重放）raise ValueError——调用面 catch 后转幂等回执「已处理」。
        行不存在返 None。"""
        if new_status not in ("confirmed", "rejected", "expired"):
            raise ValueError(f"非法目标态：{new_status}")
        row = self.get_confirmation(rid)
        if row is None:
            return None
        if row["status"] != "requested":
            raise ValueError(f"非 requested 态不可转移（当前 {row['status']}）")
        self._conn.execute(
            "UPDATE confirmation_requests SET status=?, decided_at=? WHERE id=?",
            (new_status, _now(), rid),
        )
        self._conn.commit()
        return self.get_confirmation(rid)

    @_locked
    def expire_sweep_confirmations(self, max_age_hours: int = 72) -> list[dict]:
        """requested 行龄超窗 → expired（D6 超时面）。返回被过期行（调用面频道回执）。
        ISO UTC 字符串比较（_now 同格式，时区恒 UTC）。"""
        cutoff = (datetime.now(timezone.utc) -
                  timedelta(hours=max_age_hours)).isoformat(timespec="seconds")
        rows = self._conn.execute(
            "SELECT * FROM confirmation_requests WHERE status='requested' AND created_at<?",
            (cutoff,),
        ).fetchall()
        swept = []
        for row in rows:
            self._conn.execute(
                "UPDATE confirmation_requests SET status='expired', decided_at=? WHERE id=?",
                (_now(), row["id"]),
            )
            swept.append(dict(row))
        self._conn.commit()
        return swept

    def count_confirm_origin(self, origin_sha: str) -> int:
        """同祖先链已 confirmed 次数（D5 重入队上界判据：达 3 → 回执人工改写）。"""
        row = self._conn.execute(
            "SELECT COUNT(*) FROM confirmation_requests WHERE origin_sha=? AND status='confirmed'",
            (origin_sha,),
        ).fetchone()
        return int(row[0])

    def list_confirmations_pending(self) -> list[dict]:
        """requested 态行（check-intake 确认面：待决行数 + 最老行龄，2.4）。"""
        rows = self._conn.execute(
            "SELECT * FROM confirmation_requests WHERE status='requested' ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]

    def latest_confirmation_origin(self, project: str, initiator: str) -> str | None:
        """同 project+发起人最近一行的 origin_sha（建行时祖先链根继承判据，2.2）。

        confirm/metrics 重入队后新 run 的 metrics_proposed 带新 prd_sha，但祖先链根
        须保持最初 sha——有前行则继承，查无（首次到达）则调用方以本事件 prd_sha 为根。
        """
        row = self._conn.execute(
            "SELECT origin_sha FROM confirmation_requests WHERE project=? AND initiator=? "
            "ORDER BY id DESC LIMIT 1",
            (project, initiator),
        ).fetchone()
        return row["origin_sha"] if row else None
