"""db.py 单测——队列/_run 两表持久层（add-hub-skeleton 任务 2.1，spec: execution-hub「队列排度」+ D2/A3 重派语义）。"""
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
