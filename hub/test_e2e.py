"""E2E 干跑——FakeRunner 全链旅程（add-hub-skeleton 任务 9.1，零 docker、零 LLM 成本）。

入队 → 排度 → meta 署名 → 容器 → 模拟容器内 pa 写卷 → 终态 → DB 记录断言；
含一次崩溃-重启-对账旅程（7.3 语义的端到端坐实）。
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import config
import db as hub_db
import lifecycle as lc_mod
import queue_engine as qe
from runner import FakeRunner

_CLOCK = datetime(2026, 10, 21, 3, 0, 0, tzinfo=timezone.utc)


def _mk(tmp_path: Path, images=None):
    d = hub_db.HubDB(tmp_path / "h.sqlite3")
    eng = qe.QueueEngine(d, max_containers=2, max_retries=2, image_tag_default="pa-executor:v0.1.3")
    fake = FakeRunner(images=images if images is not None else {"pa-executor:v0.1.3"})
    cfg = config.load_config({
        "HUB_USER_ID": "roc",
        # 长值：_credentials 派发前占位符判据（vault_store.placeholder_issues，2026-10-23）
        "HUB_USER_ANTHROPIC_BASE_URL": "https://litellm.example",
        "HUB_USER_ANTHROPIC_AUTH_TOKEN": "tok-abcdef0123456789abcdef",
        "HUB_USER_GH_TOKEN": "ghp_abcdefghij0123456789",
        "HUB_IMAGE_TAG_DEFAULT": "pa-executor:v0.1.3",
        "HUB_DATA_ROOT": str(tmp_path / "data"),
    })
    lc = lc_mod.Lifecycle(d, eng, fake, cfg, clock=_CLOCK)
    # per-project 启动必读面（镜像咬合）：容器内 pa 无条件读 sources.yaml + profiles
    for proj in ("proj-a", "proj-b"):
        profs = tmp_path / "data" / proj / "profiles"
        profs.mkdir(parents=True, exist_ok=True)
        (profs / f"{proj}.yaml").write_text(f"name: {proj}\nadmission: true\n", encoding="utf-8")
        (tmp_path / "data" / proj / "sources.yaml").write_text("sources: []\n", encoding="utf-8")
    return lc, d, fake, cfg, tmp_path / "data"


def _simulate_container_writes(run_dir: Path, stamp: str, slug: str) -> None:
    """扮演容器内 pa：往挂载的 state 卷写 manifest + journal（机械活，非语义）。
    journal 是 runs/<proj>/ 下平铺文件（run_daily 事实布局），与 run 目录同名同层。"""
    state_root = run_dir.parent.parent.parent
    (state_root / f"prd_manifest_{stamp}.json").write_text(json.dumps({"slug": slug}), encoding="utf-8")
    (run_dir.parent / f"{run_dir.name}.journal.jsonl").write_text('{"event": "run_started"}\n', encoding="utf-8")


def test_full_journey_enqueue_to_done_with_crash_restart(tmp_path):
    lc, d, fake, cfg, data = _mk(tmp_path)

    # ── 旅程 1：正常全链（入队 → tick 派发 → 容器写卷 → rc=0 → done）──
    prd = tmp_path / "feat-x.md"; prd.write_text("# feat x\n", encoding="utf-8")
    qid = lc.engine.enqueue("proj-a", str(prd), "roc")
    assert lc.tick_dispatch() is True
    run1 = d.runs_for_item(qid)[0]
    run_dir = Path(run1["run_dir"])
    _simulate_container_writes(run_dir, "20261021", "feat-x")
    fake.finish(run1["container_ref"], exit_code=0)
    lc.reconcile_running()
    assert d.get_item(qid)["status"] == "done"
    assert d.runs_for_item(qid)[0]["status"] == "succeeded"
    assert (run_dir / "20261021_feat-x.meta.json").exists()       # F10 署名在卷
    assert (run_dir.parent / "20261021_feat-x.journal.jsonl").exists()  # 容器写的平铺 journal 在卷
    assert (data / "proj-a" / "state" / "prd_manifest_20261021.json").exists()

    # ── 旅程 2：崩溃-重启-对账 + 失败重派后成功 ──
    prd2 = tmp_path / "feat-y.md"; prd2.write_text("# feat y\n", encoding="utf-8")
    qid2 = lc.engine.enqueue("proj-b", str(prd2), "roc")
    assert lc.tick_dispatch() is True
    ref2 = d.runs_for_item(qid2)[0]["container_ref"]
    # hub「崩溃」：丢 lifecycle；新 lifecycle 同 DB 同 FakeRunner（容器进程树分离，还活着）
    eng2 = qe.QueueEngine(d, max_containers=2, max_retries=2, image_tag_default="pa-executor:v0.1.3")
    lc2 = lc_mod.Lifecycle(d, eng2, fake, cfg, clock=_CLOCK)
    lc2.reconcile_startup()
    assert d.runs_for_item(qid2)[0]["status"] == "running"  # 活容器续跟踪
    assert d.get_item(qid2)["status"] == "running"
    fake.finish(ref2, exit_code=1)  # 重启后容器以失败告终
    lc2.reconcile_running()
    item2 = d.get_item(qid2)
    assert item2["status"] == "pending" and item2["attempt"] == 1  # A3：回 pending 保计数
    assert d.runs_for_item(qid2)[0]["status"] == "failed"
    assert lc2.tick_dispatch() is True  # 重派（FIFO 优先）
    runs2 = d.runs_for_item(qid2)
    assert len(runs2) == 2 and runs2[0]["run_dir"] == runs2[1]["run_dir"]  # 同 run 复用卷 state
    run_dir2 = Path(runs2[1]["run_dir"])
    _simulate_container_writes(run_dir2, "20261021", "feat-y")
    fake.finish(runs2[1]["container_ref"], exit_code=0)
    lc2.reconcile_running()
    assert d.get_item(qid2)["status"] == "done"
    final_runs = d.runs_for_item(qid2)  # 重新查询（runs2 是 reconcile 前快照）
    assert final_runs[1]["status"] == "succeeded"

    # ── 收尾断言：DB 与卷一致，队列清空 ──
    assert {i["status"] for i in d.list_items()} == {"done"}
    assert all(p.exists() for p in [
        run_dir2 / "20261021_feat-y.meta.json",
        run_dir2.parent / "20261021_feat-y.journal.jsonl",
        data / "proj-b" / "state" / "prd_manifest_20261021.json",
    ])


def test_full_journey_missing_image_fails_without_any_container(tmp_path):
    lc, d, fake, _, _ = _mk(tmp_path, images=set())
    prd = tmp_path / "z.md"; prd.write_text("# z\n", encoding="utf-8")
    qid = lc.engine.enqueue("proj-a", str(prd), "roc")
    assert lc.tick_dispatch() is False
    item = d.get_item(qid)
    assert item["status"] == "failed" and "pa-executor:v0.1.3" in item["last_error"]
    assert d.runs_for_item(qid)[0]["status"] == "failed_to_start"
    assert not fake.containers  # 全链零容器产出（F13 fail 不回退）
