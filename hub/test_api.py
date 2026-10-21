"""api.py 单测——最小 API 面（add-hub-skeleton 任务 8.1，spec: execution-hub「最小 API 面」）。

仅三端点（POST /queue、GET /queue、GET /runs/{id}），无语义判定面（审 PRD/判价值
归 pa persona；hub 只做机械活）。cli.py 走本模块同一内部函数（8.2）。
"""
from datetime import datetime, timezone
from pathlib import Path

import config
import db as hub_db
import queue_engine as qe
from fastapi.testclient import TestClient
from runner import FakeRunner

import api
import lifecycle as lc_mod

_CLOCK = datetime(2026, 10, 21, 3, 0, 0, tzinfo=timezone.utc)


def _mk(tmp_path: Path):
    d = hub_db.HubDB(tmp_path / "h.sqlite3")
    eng = qe.QueueEngine(d, max_containers=2, max_retries=2, image_tag_default="pa-executor:v0.1.3")
    cfg = config.load_config({
        "HUB_USER_ID": "roc",
        "HUB_USER_ANTHROPIC_BASE_URL": "https://x",
        "HUB_USER_ANTHROPIC_AUTH_TOKEN": "tok",
        "HUB_USER_GH_TOKEN": "ghp",
        "HUB_IMAGE_TAG_DEFAULT": "pa-executor:v0.1.3",
        "HUB_DATA_ROOT": str(tmp_path / "data"),
    })
    lc = lc_mod.Lifecycle(d, eng, FakeRunner(images={"pa-executor:v0.1.3"}), cfg, clock=_CLOCK)
    return TestClient(api.create_app(d, eng, lc)), d, eng, lc


def test_post_queue_enqueues_and_pins_default_tag(tmp_path):
    client, d, eng, _ = _mk(tmp_path)
    prd = tmp_path / "p.md"; prd.write_text("# x", encoding="utf-8")
    resp = client.post("/queue", json={"project": "proj-a", "prd_path": str(prd), "enqueued_by": "roc"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "pending" and body["image_tag"] == "pa-executor:v0.1.3"  # F13 固化默认
    assert d.get_item(body["id"])["project"] == "proj-a"


def test_post_queue_rejects_bad_project_and_missing_prd(tmp_path):
    client, d, eng, _ = _mk(tmp_path)
    prd = tmp_path / "p.md"; prd.write_text("# x", encoding="utf-8")
    r1 = client.post("/queue", json={"project": "../evil", "prd_path": str(prd), "enqueued_by": "roc"})
    assert r1.status_code == 400 and "detail" in r1.json()
    r2 = client.post("/queue", json={"project": "proj-a", "prd_path": str(tmp_path / "nope.md"), "enqueued_by": "roc"})
    assert r2.status_code == 400
    assert d.list_items() == []  # 拒绝即不入队（fail fast，不留脏记录）


def test_get_queue_lists_and_filters_by_status(tmp_path):
    client, d, eng, lc = _mk(tmp_path)
    prd = tmp_path / "p.md"; prd.write_text("# x", encoding="utf-8")
    eng.enqueue("proj-a", str(prd), "roc"); eng.enqueue("proj-b", str(prd), "roc")
    lc.tick_dispatch()  # proj-a 起跑
    all_items = client.get("/queue").json()
    assert len(all_items) == 2 and {i["status"] for i in all_items} == {"running", "pending"}
    pending = client.get("/queue", params={"status": "pending"}).json()
    assert len(pending) == 1 and pending[0]["project"] == "proj-b"
    bad = client.get("/queue", params={"status": "weird"})
    assert bad.status_code == 400


def test_post_queue_defaults_enqueued_by_to_config_user(tmp_path):
    # B4：缺省 enqueued_by = cfg.user_id（F10 单用户形态唯一 initiator 身份），无第三身份字面量
    client, d, eng, lc = _mk(tmp_path)
    prd = tmp_path / "p.md"; prd.write_text("# x", encoding="utf-8")
    resp = client.post("/queue", json={"project": "proj-a", "prd_path": str(prd)})
    assert resp.status_code == 201
    assert resp.json()["enqueued_by"] == lc.cfg.user_id  # "roc"，非 "hub-api"
    assert d.get_item(resp.json()["id"])["enqueued_by"] == lc.cfg.user_id


def test_get_run_detail_shape_and_404(tmp_path):
    client, d, eng, lc = _mk(tmp_path)
    assert client.get("/runs/999").status_code == 404
    prd = tmp_path / "p.md"; prd.write_text("# x", encoding="utf-8")
    qid = eng.enqueue("proj-a", str(prd), "roc")
    lc.tick_dispatch()
    rid = d.runs_for_item(qid)[0]["id"]
    detail = client.get(f"/runs/{rid}").json()
    assert detail["id"] == rid and detail["status"] == "running"
    assert detail["queue_item"]["id"] == qid  # 附队列项概要
    assert "run_dir" in detail and "container_ref" in detail
