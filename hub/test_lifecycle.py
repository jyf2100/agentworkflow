"""lifecycle.py 单测——容器生命周期编排（add-hub-skeleton 任务 7.1-7.3，spec: execution-hub「容器生命周期」）。"""
import json
from datetime import datetime, timezone
from pathlib import Path

import config
import db as hub_db
import lifecycle
import queue_engine as qe
from runner import FakeRunner

_CLOCK = datetime(2026, 10, 21, 3, 0, 0, tzinfo=timezone.utc)


def _env(data_root: Path, **over) -> dict:
    env = {
        "HUB_USER_ID": "roc",
        "HUB_USER_ANTHROPIC_BASE_URL": "https://litellm.example",
        "HUB_USER_ANTHROPIC_AUTH_TOKEN": "tok-abc",
        "HUB_USER_GH_TOKEN": "ghp_xyz",
        "HUB_IMAGE_TAG_DEFAULT": "pa-executor:v0.1.3",
        "HUB_DATA_ROOT": str(data_root),
    }
    env.update(over)
    return {k: v for k, v in env.items() if v is not None}


def _mk(tmp_path: Path, images=None, max_retries: int = 2):
    d = hub_db.HubDB(tmp_path / "h.sqlite3")
    eng = qe.QueueEngine(d, max_containers=2, max_retries=max_retries, image_tag_default="pa-executor:v0.1.3")
    fake = FakeRunner(images=images if images is not None else {"pa-executor:v0.1.3"})
    cfg = config.load_config(_env(tmp_path / "data"))
    lc = lifecycle.Lifecycle(d, eng, fake, cfg, clock=_CLOCK)
    return lc, d, fake, tmp_path / "data"


def _prd(tmp_path: Path, name: str = "my-feature.md") -> Path:
    p = tmp_path / name
    p.write_text("# PRD\n", encoding="utf-8")
    return p


def test_dispatch_signs_meta_creates_run_starts_container(tmp_path):
    lc, d, fake, data = _mk(tmp_path)
    prd = _prd(tmp_path)
    qid = lc.engine.enqueue("proj-a", str(prd), "roc")
    assert lc.tick_dispatch() is True
    # F10：起容器前署名 meta，文件在 state 卷 runs/<proj>/<stamp>_<slug>/
    run_dir = data / "proj-a" / "state" / "runs" / "proj-a" / "20261021_my-feature"
    meta = run_dir / "20261021_my-feature.meta.json"
    payload = json.loads(meta.read_text(encoding="utf-8"))
    assert payload["initiator"] == "roc" and payload["queue_item_id"] == qid
    assert payload["image_tag"] == "pa-executor:v0.1.3"
    # run 索引 + 队列项 running + 容器载荷（A2：统一入口 /inject 只读卷 + 预分配 --stamp）
    runs = d.runs_for_item(qid)
    assert len(runs) == 1 and runs[0]["status"] == "running" and runs[0]["container_ref"]
    assert d.get_item(qid)["status"] == "running"
    spec = fake.containers[runs[0]["container_ref"]]["spec"]
    assert "--stamp" in spec.command and "20261021" in spec.command
    assert "/input/my-feature.md" in spec.command and "--from-stage" in spec.command
    assert spec.env_file is not None and spec.env_file.suffix == ".env"  # 与切片 2 同通道（D6）
    assert spec.state_root == data / "proj-a" / "state"


def test_dispatch_missing_image_fails_item_records_no_container(tmp_path):
    lc, d, fake, _ = _mk(tmp_path, images=set())
    prd = _prd(tmp_path)
    qid = lc.engine.enqueue("proj-a", str(prd), "roc")
    assert lc.tick_dispatch() is False  # 没派成
    item = d.get_item(qid)
    assert item["status"] == "failed" and "pa-executor:v0.1.3" in item["last_error"]  # F13：缺失 fail 报 tag
    assert not fake.containers  # 不产出容器
    run = d.runs_for_item(qid)[0]
    assert run["status"] == "failed_to_start" and run["container_ref"] is None  # A7 粒度


def test_reconcile_exit_zero_marks_done_and_run_succeeded(tmp_path):
    lc, d, fake, _ = _mk(tmp_path)
    qid = lc.engine.enqueue("proj-a", str(_prd(tmp_path)), "roc")
    lc.tick_dispatch()
    ref = d.runs_for_item(qid)[0]["container_ref"]
    fake.finish(ref, exit_code=0)
    lc.reconcile_running()
    assert d.get_item(qid)["status"] == "done"
    run = d.runs_for_item(qid)[0]
    assert run["status"] == "succeeded" and run["exit_code"] == 0


def test_nonzero_requeues_same_run_dir_new_container(tmp_path):
    # 7.1：重派上限内续跑 = 同 run 新容器复用卷 state（runs 表 1:N 行同 run_dir）
    lc, d, fake, _ = _mk(tmp_path, max_retries=2)
    qid = lc.engine.enqueue("proj-a", str(_prd(tmp_path)), "roc")
    lc.tick_dispatch()
    r1 = d.runs_for_item(qid)[0]
    fake.finish(r1["container_ref"], exit_code=1)
    lc.reconcile_running()
    item = d.get_item(qid)
    assert item["status"] == "pending" and item["attempt"] == 1  # 回 pending 保留计数，不占 running 名额
    assert d.runs_for_item(qid)[0]["status"] == "failed"
    assert lc.tick_dispatch() is True  # FIFO 重派优先（同项最老）
    runs = d.runs_for_item(qid)
    assert len(runs) == 2 and runs[0]["run_dir"] == runs[1]["run_dir"]  # 同 run 复用卷 state
    fake.finish(runs[1]["container_ref"], exit_code=0)
    lc.reconcile_running()
    assert d.get_item(qid)["status"] == "done"


def test_attempt_limit_separates_item_failed_from_run_failed(tmp_path):
    # 两生命周期分离：run failed 是容器事实（每行都记），item failed 是放弃决定
    lc, d, fake, _ = _mk(tmp_path, max_retries=1)
    qid = lc.engine.enqueue("proj-a", str(_prd(tmp_path)), "roc")
    lc.tick_dispatch()
    ref = d.runs_for_item(qid)[0]["container_ref"]
    fake.finish(ref, exit_code=1)
    lc.reconcile_running()
    item = d.get_item(qid)
    assert item["status"] == "failed" and item["attempt"] == 1  # 达上限即放弃
    assert d.runs_for_item(qid)[0]["status"] == "failed" and d.runs_for_item(qid)[0]["exit_code"] == 1


def test_orphan_meta_marked_failed_to_start_and_kept(tmp_path):
    # 7.2：有 meta 无 journal 的 run 目录 → failed_to_start 索引，meta 保留不清理（F10）
    lc, d, _, data = _mk(tmp_path)
    orphan_dir = data / "proj-a" / "state" / "runs" / "proj-a" / "20260101_old-thing"
    orphan_dir.mkdir(parents=True)
    meta = orphan_dir / "20260101_old-thing.meta.json"
    meta.write_text(json.dumps({"initiator": "roc", "queue_item_id": 999, "image_tag": "t:v0", "written_at": "x"}), encoding="utf-8")
    lc.reconcile_startup()
    archived = [r for r in d.list_all_runs() if r["run_dir"].endswith("20260101_old-thing")]
    assert len(archived) == 1 and archived[0]["status"] == "failed_to_start"
    assert meta.exists()  # 保留不清理
    # 幂等：再跑一次不重复索引
    lc.reconcile_startup()
    assert len([r for r in d.list_all_runs() if r["run_dir"].endswith("20260101_old-thing")]) == 1


def test_crash_restart_live_container_retracked_until_exit(tmp_path):
    # 7.3：hub 崩溃不杀容器（进程树分离）——新 lifecycle 同 DB 同 runner 续跟踪
    lc1, d, fake, _ = _mk(tmp_path)
    qid = lc1.engine.enqueue("proj-a", str(_prd(tmp_path)), "roc")
    lc1.tick_dispatch()
    ref = d.runs_for_item(qid)[0]["container_ref"]
    # 模拟 hub 崩溃重启：全新 lifecycle（同 DB 同 FakeRunner——容器还活着）
    eng2 = qe.QueueEngine(d, max_containers=2, max_retries=2, image_tag_default="pa-executor:v0.1.3")
    cfg2 = config.load_config(_env(tmp_path / "data"))
    lc2 = lifecycle.Lifecycle(d, eng2, fake, cfg2, clock=_CLOCK)
    lc2.reconcile_startup()
    assert d.runs_for_item(qid)[0]["status"] == "running"  # 在跑：续跟踪不误杀
    assert d.get_item(qid)["status"] == "running"
    fake.finish(ref, exit_code=0)
    lc2.reconcile_running()
    assert d.get_item(qid)["status"] == "done"


def test_crash_restart_gone_container_archived_by_volume_evidence(tmp_path):
    # 已消失：按卷归档终态——有 journal → run failed；无 journal → failed_to_start；均走失败状态机
    lc1, d, fake, data = _mk(tmp_path)
    qid = lc1.engine.enqueue("proj-a", str(_prd(tmp_path)), "roc")
    lc1.tick_dispatch()
    ref = d.runs_for_item(qid)[0]["container_ref"]
    fake.stop(ref)  # 容器消失（如 daemon 清理）
    run_dir = Path(d.runs_for_item(qid)[0]["run_dir"])
    (run_dir / "20261021_my-feature.journal.jsonl").write_text("{}\n", encoding="utf-8")  # 卷上证据：跑过
    eng2 = qe.QueueEngine(d, max_containers=2, max_retries=2, image_tag_default="pa-executor:v0.1.3")
    lc2 = lifecycle.Lifecycle(d, eng2, fake, config.load_config(_env(tmp_path / "data")), clock=_CLOCK)
    lc2.reconcile_startup()
    run = d.runs_for_item(qid)[0]
    assert run["status"] == "failed"  # 有 journal：跑过，终态不明按失败归档（fail-safe）
    assert d.get_item(qid)["status"] == "pending"  # 走重派状态机


def test_crash_restart_gone_without_journal_is_failed_to_start(tmp_path):
    lc1, d, fake, _ = _mk(tmp_path)
    qid = lc1.engine.enqueue("proj-a", str(_prd(tmp_path)), "roc")
    lc1.tick_dispatch()
    fake.stop(d.runs_for_item(qid)[0]["container_ref"])
    lc1.reconcile_running()
    run = d.runs_for_item(qid)[0]
    assert run["status"] == "failed_to_start"  # 无 journal：容器从未跑成（A7 粒度）
    assert d.get_item(qid)["status"] == "pending"


def test_daemon_failure_records_once_per_retry_with_backoff(tmp_path):
    # B2 退避：起容器 Exception（daemon 故障）→ run 记账 failed_to_start、item 留 pending
    # （区别 F13 终态）；连续失败进冷却——冷却 tick 不重复记账，冷却结束再试
    d = hub_db.HubDB(tmp_path / "h.sqlite3")
    eng = qe.QueueEngine(d, max_containers=2, max_retries=2, image_tag_default="pa-executor:v0.1.3")
    fake = FakeRunner(images={"pa-executor:v0.1.3"}, crash_on_start=RuntimeError("docker daemon down"))
    cfg = config.load_config(_env(tmp_path / "data"))
    lc = lifecycle.Lifecycle(d, eng, fake, cfg, clock=_CLOCK, dispatch_backoff=2)
    qid = lc.engine.enqueue("proj-a", str(_prd(tmp_path)), "roc")

    assert lc.tick_dispatch() is False  # 第 1 次尝试：记账 1 行，不放弃
    assert len(d.runs_for_item(qid)) == 1
    assert d.runs_for_item(qid)[0]["status"] == "failed_to_start"
    assert d.get_item(qid)["status"] == "pending"
    assert lc.tick_dispatch() is False and lc.tick_dispatch() is False  # 冷却 2 tick：不记账
    assert len(d.runs_for_item(qid)) == 1
    assert lc.tick_dispatch() is False  # 冷却毕：重试（第 2 行记账）
    assert len(d.runs_for_item(qid)) == 2

    fake.crash_on_start = None  # daemon 恢复：tick4 失败再入冷却，耗尽后派发成功
    assert lc.tick_dispatch() is False and lc.tick_dispatch() is False
    assert lc.tick_dispatch() is True
    assert len(d.runs_for_item(qid)) == 3 and d.runs_for_item(qid)[2]["status"] == "running"


def test_retry_dispatch_preserves_first_meta_written_at(tmp_path):
    # B2 首写保真（lifecycle 面）：daemon 故障重试再成功，meta 仍是首派署名时刻
    d = hub_db.HubDB(tmp_path / "h.sqlite3")
    eng = qe.QueueEngine(d, max_containers=2, max_retries=2, image_tag_default="pa-executor:v0.1.3")
    fake = FakeRunner(images={"pa-executor:v0.1.3"}, crash_on_start=RuntimeError("down"))
    cfg = config.load_config(_env(tmp_path / "data"))
    t1 = datetime(2026, 10, 21, 3, 0, 0, tzinfo=timezone.utc)
    lc1 = lifecycle.Lifecycle(d, eng, fake, cfg, clock=t1)
    lc1.engine.enqueue("proj-a", str(_prd(tmp_path)), "roc")
    assert lc1.tick_dispatch() is False  # 失败 tick：meta 已署名（written_at = t1）

    fake.crash_on_start = None
    t2 = datetime(2026, 10, 21, 9, 30, 0, tzinfo=timezone.utc)
    lc2 = lifecycle.Lifecycle(d, eng, fake, cfg, clock=t2)  # 新实例（hub 重启形态），重派同 item
    assert lc2.tick_dispatch() is True
    meta = (tmp_path / "data" / "proj-a" / "state" / "runs" / "proj-a"
            / "20261021_my-feature" / "20261021_my-feature.meta.json")
    assert json.loads(meta.read_text(encoding="utf-8"))["written_at"] == "2026-10-21T03:00:00+00:00"


def test_project_path_injection_rejected_no_write(tmp_path):
    lc, d, fake, data = _mk(tmp_path)
    prd = _prd(tmp_path)
    qid = lc.engine.enqueue("../evil", str(prd), "roc")
    assert lc.tick_dispatch() is False
    assert d.get_item(qid)["status"] == "failed"  # 防御性 fail（入队面 API 层另有校验）
    assert not (tmp_path / "data" / "..").joinpath("evil").exists()  # 无越界写
    assert not fake.containers
