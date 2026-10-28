"""db.py 单测——队列/_run 两表持久层（add-hub-skeleton 任务 2.1，spec: execution-hub「队列排度」+ D2/A3 重派语义）。"""
import sqlite3
from pathlib import Path

import db


def _open(tmp_path: Path) -> db.HubDB:
    return db.HubDB(tmp_path / "hub.sqlite3")


def test_enqueue_defaults_pending_attempt_zero_and_persists_across_connections(tmp_path):
    d = _open(tmp_path)
    qid = d.enqueue(project="proj-a", prd_path="/p/x.md", image_tag="pa-executor:v0.1.3", enqueued_by="roc")
    item = d.get_item(qid)
    assert item["status"] == "pending" and item["attempt"] == 0 and item["project"] == "proj-a"
    # 重开新连接（hub 重启不丢——spec scenario「hub 重启队列不丢」）
    d2 = db.HubDB(tmp_path / "hub.sqlite3")
    assert d2.get_item(qid)["prd_path"] == "/p/x.md"


def test_invalid_status_rejected(tmp_path):
    d = _open(tmp_path); qid = d.enqueue("a", "/p", "t", "u")
    try:
        d.set_item_status(qid, "weird"); raised = False
    except db.DBError:
        raised = True
    assert raised


def test_running_queries_per_project_and_count(tmp_path):
    d = _open(tmp_path)
    a1 = d.enqueue("a", "/p1", "t", "u"); a2 = d.enqueue("a", "/p2", "t", "u"); b1 = d.enqueue("b", "/p3", "t", "u")
    d.set_item_status(a1, "running")
    assert d.has_running("a") and not d.has_running("b") and d.count_running() == 1
    # A 已 running：A 的 pending 不派发，B 的最老 pending 可派（spec scenario「同项目第二项等待」的 DB 判据）
    assert d.oldest_pending_excluding_running_projects() == b1
    d.set_item_status(a1, "done")
    assert d.oldest_pending_excluding_running_projects() == a2  # A 释放后恢复 FIFO


def test_oldest_pending_fifo_order(tmp_path):
    d = _open(tmp_path)
    ids = [d.enqueue(f"p{i}", "/x", "t", "u") for i in range(3)]
    assert d.oldest_pending_excluding_running_projects() == ids[0]
    d.set_item_status(ids[0], "running")
    assert d.oldest_pending_excluding_running_projects() == ids[1]


def test_attempt_survives_requeue_to_pending(tmp_path):
    d = _open(tmp_path); qid = d.enqueue("a", "/p", "t", "u")
    d.set_item_status(qid, "running"); d.bump_attempt(qid); d.set_item_status(qid, "pending")
    item = d.get_item(qid)
    assert item["status"] == "pending" and item["attempt"] == 1  # A3：重派回 pending 保留计数


def test_run_lifecycle_and_item_link(tmp_path):
    d = _open(tmp_path); qid = d.enqueue("a", "/p", "t", "u")
    rid = d.create_run(queue_item_id=qid, project="a", run_dir="runs/a/20261021_x")
    d.set_run_running(rid, container_ref="c1")
    run = d.get_run(rid)
    assert run["status"] == "running" and run["container_ref"] == "c1"
    d.set_run_finished(rid, status="failed", exit_code=1)
    run = d.get_run(rid)
    assert run["status"] == "failed" and run["exit_code"] == 1 and run["ended_at"] is not None
    assert [r["id"] for r in d.runs_for_item(qid)] == [rid]


def test_list_items_by_status(tmp_path):
    d = _open(tmp_path)
    a = d.enqueue("a", "/p", "t", "u"); b = d.enqueue("b", "/p", "t", "u")
    d.set_item_status(b, "running")
    assert [i["id"] for i in d.list_items(status="pending")] == [a]
    assert len(d.list_items()) == 2
    assert [i["id"] for i in d.list_items()] == [b, a]  # 最新优先（fix-hub-queue-recency）


def test_sqlite_multithread_writes_no_deadlock_no_loss(tmp_path):
    """add-hub-slice-2a 3.3：API enqueue 线程 × daemon tick 线程并发共连接——
    WAL + busy_timeout + 短事务下无死锁无丢写（spec: execution-hub「排度常驻服务」）。"""
    import threading

    import queue_engine as qe

    d = db.HubDB(tmp_path / "h.sqlite3")
    eng = qe.QueueEngine(d, max_containers=2, max_retries=1, image_tag_default="t:v1")
    errors: list[Exception] = []

    def writer():  # API 线程：连续 enqueue 40
        for i in range(40):
            try:
                eng.enqueue("proj-a", f"/prd/{i}.md", "roc")
            except Exception as e:  # noqa: BLE001——并发错误面全收（断言 not errors）
                errors.append(e)

    def reader_marker():  # daemon 线程：读 + 状态写交错（set 同值 = 纯写压力，不污染 writer 语义）
        for _ in range(40):
            try:
                rows = d.list_items(None)
                if rows:
                    d.set_item_status(rows[0]["id"], "pending")
            except Exception as e:  # noqa: BLE001
                errors.append(e)

    t1, t2 = threading.Thread(target=writer), threading.Thread(target=reader_marker)
    t1.start(); t2.start(); t1.join(timeout=30); t2.join(timeout=30)
    assert not errors
    assert len(d.list_items(None)) == 40  # 无丢写


# ── add-hub-slice-3 任务 2.1：deliver_key 幂等列（D2）──────────────────
# 幂等键 "{project}:{filename}:{sha256}" 生命周期 = 队列项生命周期，同表 UNIQUE 索引；
# 2a/2b 既有库经 _migrate ALTER 补列不丢队列；并发撞键 db 层兜底（IntegrityError），
# handler（2.4）捕获后查既有项返重复响应——文件层幂等互补（spec: prd-delivery）。

# 2b 既有库 schema：有 initiator 无 deliver_key
_LEGACY_SCHEMA_2B = """
CREATE TABLE queue_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project     TEXT NOT NULL,
    prd_path    TEXT NOT NULL,
    image_tag   TEXT NOT NULL,
    status      TEXT NOT NULL,
    attempt     INTEGER NOT NULL DEFAULT 0,
    enqueued_by TEXT NOT NULL,
    initiator   TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    last_error  TEXT
);
"""
_LEGACY_SCHEMA_2A = _LEGACY_SCHEMA_2B.replace("    initiator   TEXT,\n", "")  # 2a 库：连 initiator 都没有

_KEY = "proj-a:20261022_some-slug.md:%s" % ("ab" * 32)


def _legacy_db_with_row(db_file: Path, schema: str) -> None:
    conn = sqlite3.connect(db_file)
    conn.executescript(schema)
    conn.execute(
        "INSERT INTO queue_items (project, prd_path, image_tag, status, attempt, enqueued_by, created_at, updated_at)"
        " VALUES ('proj-a','/p/x.md','pa-executor:v1','pending',0,'roc','2026-10-01T00:00:00+00:00','2026-10-01T00:00:00+00:00')"
    )
    conn.commit(); conn.close()


def _cols(db_file: Path) -> set[str]:
    conn = sqlite3.connect(db_file)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(queue_items)")}
    conn.close()
    return cols


def test_deliver_key_column_and_unique_index_on_fresh_db(tmp_path):
    db_file = tmp_path / "hub.sqlite3"
    db.HubDB(db_file)
    cols = _cols(db_file)
    assert "deliver_key" in cols
    conn = sqlite3.connect(db_file)
    idx = {r[1] for r in conn.execute("PRAGMA index_list(queue_items)") if r[2]}  # r[2]=unique
    conn.close()
    assert any("deliver_key" in name for name in idx), "deliver_key 须有 UNIQUE 索引（并发撞键 db 层兜底）"


def test_migrate_2b_legacy_db_adds_deliver_key_keeps_rows(tmp_path):
    db_file = tmp_path / "hub.sqlite3"
    _legacy_db_with_row(db_file, _LEGACY_SCHEMA_2B)
    d = db.HubDB(db_file)  # 打开即迁移
    assert "deliver_key" in _cols(db_file)
    items = d.list_items()
    assert len(items) == 1 and items[0]["project"] == "proj-a" and items[0]["deliver_key"] is None


def test_migrate_2a_legacy_db_chains_both_alters_keeps_rows(tmp_path):
    db_file = tmp_path / "hub.sqlite3"
    _legacy_db_with_row(db_file, _LEGACY_SCHEMA_2A)
    d = db.HubDB(db_file)
    cols = _cols(db_file)
    assert "initiator" in cols and "deliver_key" in cols  # 两步链式迁移都到终点
    assert len(d.list_items()) == 1


def test_duplicate_deliver_key_rejected_at_db_layer(tmp_path):
    d = _open(tmp_path)
    d.enqueue("proj-a", "/p/x.md", "t", "roc", deliver_key=_KEY)
    try:
        d.enqueue("proj-a", "/p/x.md", "t", "roc", deliver_key=_KEY); raised = False
    except sqlite3.IntegrityError:
        raised = True
    assert raised  # 撞键 db 层兜底；handler 捕获后查既有项返重复响应（2.4）


def test_get_item_by_deliver_key_roundtrip(tmp_path):
    d = _open(tmp_path)
    qid = d.enqueue("proj-a", "/p/x.md", "t", "roc", deliver_key=_KEY)
    item = d.get_item_by_deliver_key(_KEY)
    assert item is not None and item["id"] == qid and item["prd_path"] == "/p/x.md"
    assert d.get_item_by_deliver_key("no:such:key") is None


def test_enqueue_without_deliver_key_allows_multiple_nulls(tmp_path):
    d = _open(tmp_path)
    d.enqueue("a", "/p1", "t", "u"); d.enqueue("b", "/p2", "t", "u")  # 既有 CLI/API 入队路径零破坏
    assert len(d.list_items()) == 2  # UNIQUE 索引对 NULL 不约束——非投递入队无幂等键语义


# ── add-mm-channel-intake 1.x：project_channels 绑定表（D2 频道↔项目 1:1 唯一真相）──

_C1 = "c" * 26
_C2 = "d" * 26


def test_bind_crud_roundtrip(tmp_path):
    d = _open(tmp_path)
    assert d.get_channel_binding_by_channel(_C1) is None and d.get_channel_binding_by_project("proj-a") is None
    d.upsert_bind("proj-a", _C1, "roc")
    row = d.get_channel_binding_by_channel(_C1)
    assert row is not None and row["project"] == "proj-a" and row["bound_by"] == "roc"
    assert d.get_channel_binding_by_project("proj-a")["channel_id"] == _C1
    # 重开连接持久（绑定是 db 实体非内存态）
    d2 = db.HubDB(tmp_path / "hub.sqlite3")
    assert d2.get_channel_binding_by_channel(_C1)["project"] == "proj-a"


def test_bind_unique_constraints_both_directions(tmp_path):
    d = _open(tmp_path)
    d.upsert_bind("proj-a", _C1, "roc"); d.upsert_bind("proj-b", _C2, "roc")
    conn = sqlite3.connect(tmp_path / "hub.sqlite3")
    # 同 project 二频道 / 同频道二 project 双向撞 UNIQUE（1:1 两方向都唯一）
    try:
        conn.execute("INSERT INTO project_channels VALUES ('proj-a',?, 'x','t')", (_C2,)); raised = False
    except sqlite3.IntegrityError:
        raised = True
    assert raised
    try:
        conn.execute("INSERT INTO project_channels VALUES ('proj-c',?, 'x','t')", (_C1,)); raised = False
    except sqlite3.IntegrityError:
        raised = True
    assert raised
    conn.close()


def test_rebind_project_switches_channel_clears_old(tmp_path):
    d = _open(tmp_path)
    d.upsert_bind("proj-a", _C1, "roc")
    replaced = d.upsert_bind("proj-a", _C2, "roc")  # project 换频道
    assert [r["channel_id"] for r in replaced] == [_C1]
    assert d.get_channel_binding_by_channel(_C1) is None  # 旧绑定必清
    assert d.get_channel_binding_by_project("proj-a")["channel_id"] == _C2
    assert d.get_channel_binding_by_channel(_C2)["project"] == "proj-a"


def test_rebind_channel_takes_over_project(tmp_path):
    d = _open(tmp_path)
    d.upsert_bind("proj-a", _C1, "roc"); d.upsert_bind("proj-b", _C2, "roc")
    replaced = d.upsert_bind("proj-a", _C2, "roc")  # 频道顶绑：proj-a←C2 顶掉 proj-b←C2
    assert {(r["project"], r["channel_id"]) for r in replaced} == {("proj-a", _C1), ("proj-b", _C2)}
    assert d.get_channel_binding_by_project("proj-b") is None  # C2 原 project 绑定被顶清
    assert d.get_channel_binding_by_channel(_C1) is None  # proj-a 原频道绑定被清
    assert d.get_channel_binding_by_channel(_C2)["project"] == "proj-a"  # 唯一幸存：新绑定


def test_rebind_same_pair_returns_own_old_row(tmp_path):
    d = _open(tmp_path)
    d.upsert_bind("proj-a", _C1, "roc")
    replaced = d.upsert_bind("proj-a", _C1, "jyf")  # 重复 bind 同对 = 换绑覆盖自身（bound_by 更新）
    assert len(replaced) == 1 and replaced[0]["bound_by"] == "roc"
    assert d.get_channel_binding_by_channel(_C1)["bound_by"] == "jyf"


def test_unbind_by_channel(tmp_path):
    d = _open(tmp_path)
    assert d.unbind_by_channel(_C1) is None  # 未绑定 → None（教育回执判据）
    d.upsert_bind("proj-a", _C1, "roc")
    row = d.unbind_by_channel(_C1)
    assert row is not None and row["project"] == "proj-a"
    assert d.get_channel_binding_by_project("proj-a") is None


def test_seed_empty_table_seeds_with_migration_audit(capsys, tmp_path):
    d = _open(tmp_path)
    n = d.seed_project_channels({"proj-a": _C1, "proj-b": _C2})
    assert n == 2
    row = d.get_channel_binding_by_channel(_C1)
    assert row["bound_by"] == "seed-migration" and row["bound_at"]  # 审计列：seed 来源 + 时刻
    assert "warn" not in capsys.readouterr().err


def test_seed_skips_when_table_nonempty(tmp_path):
    d = _open(tmp_path)
    d.upsert_bind("proj-a", _C1, "roc")  # 表非空（哪怕只有一行手工绑定）
    assert d.seed_project_channels({"proj-b": _C2}) == 0  # 跳过——env 再改也不重灌
    assert d.get_channel_binding_by_project("proj-b") is None
    assert d.get_channel_binding_by_channel(_C1)["bound_by"] == "roc"  # 既有绑定不被覆盖


def test_seed_empty_pairs_and_rerun_idempotent(tmp_path):
    d = _open(tmp_path)
    assert d.seed_project_channels({}) == 0  # env 缺失/空 → 零行为零行
    assert d.get_channel_binding_by_channel(_C1) is None
    assert d.seed_project_channels({"proj-a": _C1}) == 1
    assert d.seed_project_channels({"proj-a": _C1, "proj-b": _C2}) == 0  # 二次启动：表非空跳过


def test_seed_rejects_malformed_channel_id(capsys, tmp_path):
    d = _open(tmp_path)
    n = d.seed_project_channels({
        "proj-a": "not-a-channel-url",       # 频道名/URL 误填形态
        "proj-b": "abc123",                  # 短 id
        "proj-c": _C1,
    })
    assert n == 1  # 只有合法形态入库
    assert d.get_channel_binding_by_project("proj-a") is None
    assert d.get_channel_binding_by_project("proj-c")["channel_id"] == _C1
    err = capsys.readouterr().err
    assert "proj-a" in err and "proj-b" in err  # 拒入 stderr 可见（无静默失败）
