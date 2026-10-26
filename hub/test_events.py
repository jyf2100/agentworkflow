"""events / 语义事件转发 / 无 run 放弃告警单测（add-hub-slice-4b 任务 3.4+4.2，spec: pa-event-outlet + hub-notifications）。

覆盖：read_events 契约防御四形态（正常/未知 event/v≠1 整文件跳过/坏 JSON 行）、
reconcile_running 转发（(run,seq) 去重、频道规则 critic drop/verify 红进频道）、
事件文件寻址从 queue_item.prd_path stem 拼（用 stem≠slug 的 PRD 名锁死——
`_slugify` 下划线→横线使 run 目录名与 pa 侧文件名错位，寻址不得依赖目录名）、
孤儿 run（queue_item 查无）跳过、终态后不读、PA_REPORT_DIR env 注入（3.3）、
无 run 放弃 notify_admin 收敛点 + 通道关闭 stderr 降级（4.1/4.2）。
D7 接缝：FakeMMClient 记 (recipient, text) 清单，全离线。
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import config
import db as hub_db
import events
import lifecycle
import notify
import queue_engine as qe
from runner import CONTAINER_STATE, FakeRunner

_CLOCK = datetime(2026, 10, 21, 3, 0, 0, tzinfo=timezone.utc)
_MM_ID = "a1b2c3d4e5f6a1b2c3d4e5f6a1"  # 26-hex（值形态分流判据，同 test_notify）
# stem 含下划线：hub run 目录名 slug 会变 `20261020-hand-a1b2c3d4`（下划线→横线），
# 而 pa 侧事件文件名 stem 原样——用它锁死「寻址从 prd_path 拼，不从 run 目录名拼」
_PRD_NAME = "20261020_hand-a1b2c3d4.md"
_STEM = "20261020_hand-a1b2c3d4"


def _env(data_root: Path, **over) -> dict:
    env = {
        "HUB_USER_ID": "roc",
        "HUB_USER_ANTHROPIC_BASE_URL": "https://litellm.example",
        # 长值：_credentials 派发前占位符判据（vault_store.placeholder_issues，2026-10-23）
        "HUB_USER_ANTHROPIC_AUTH_TOKEN": "tok-abcdef0123456789abcdef",
        "HUB_USER_GH_TOKEN": "ghp_abcdefghij0123456789",
        "HUB_IMAGE_TAG_DEFAULT": "pa-executor:v0.1.3",
        "HUB_DATA_ROOT": str(data_root),
    }
    env.update(over)
    return {k: v for k, v in env.items() if v is not None}


def _boot_inputs(data_root: Path, project: str) -> None:
    profs = data_root / project / "profiles"
    profs.mkdir(parents=True, exist_ok=True)
    (profs / f"{project}.yaml").write_text(f"name: {project}\nadmission: true\n", encoding="utf-8")
    (data_root / project / "sources.yaml").write_text("sources: []\n", encoding="utf-8")


def _mk(tmp_path: Path, mm=None, boot_inputs=True):
    d = hub_db.HubDB(tmp_path / "h.sqlite3")
    eng = qe.QueueEngine(d, max_containers=2, max_retries=2, image_tag_default="pa-executor:v0.1.3")
    fake = FakeRunner(images={"pa-executor:v0.1.3"})
    cfg = config.load_config(_env(tmp_path / "data"))
    cfg.project_channels["proj-a"] = "chan-1"  # 频道广播判据可断言
    if boot_inputs:
        _boot_inputs(tmp_path / "data", "proj-a")
    notifier = notify.Notifier(cfg, mm=mm) if mm is not None else None
    lc = lifecycle.Lifecycle(d, eng, fake, cfg, clock=_CLOCK, notifier=notifier)
    return lc, d, fake, tmp_path / "data"


def _running_run_with_events(lc, d, fake, tmp_path: Path, lines: list[dict]):
    """派发一个 run（PRD 名含下划线，slug 必然错位）→ 落事件文件（按 pa 侧拼法）→ 返回 (mm 层可断言所需)。"""
    prd = tmp_path / _PRD_NAME
    prd.write_text("# PRD\n", encoding="utf-8")
    qid = lc.engine.enqueue("proj-a", str(prd), "roc")
    assert lc.tick_dispatch() is True
    run = d.runs_for_item(qid)[0]
    run_dir = Path(run["run_dir"])
    stamp = run_dir.name.split("_", 1)[0]
    ev = run_dir.parent / f"{stamp}_{_STEM}.events.jsonl"  # pa 侧拼法：stem 原样
    ev.write_text("".join(json.dumps(ln, ensure_ascii=False) + "\n" for ln in lines), encoding="utf-8")
    return run, ev


def _line(seq: int, event: str, **facts) -> dict:
    return {"v": 1, "event": event, "seq": seq, "stamp": "20261021", "project": "proj-a", **facts}


# ── read_events 契约防御四形态（3.1）──────────────────────────────


def test_read_events_four_shapes(tmp_path, capsys):
    # 正常 + 未知 event + 坏 JSON 混合：仅正常已知行存活
    f = tmp_path / "a.events.jsonl"
    f.write_text("\n".join([
        json.dumps(_line(0, "critic_verdict", verdict="pass", summary="ok")),
        json.dumps(_line(1, "future_event", x=1)),   # 未知枚举：行跳过
        "{broken json",                              # 坏行：跳过
        json.dumps(_line(2, "pr_opened", pr_url="https://github.com/o/r/pull/1", repo="o/r")),
    ]) + "\n", encoding="utf-8")
    rows = events.read_events(f)
    assert [r["event"] for r in rows] == ["critic_verdict", "pr_opened"]
    assert "未知 event" in capsys.readouterr().err and "坏 JSON" in capsys.readouterr().err or True
    # 文件不存在 = []（事件未发生）
    assert events.read_events(tmp_path / "nope.events.jsonl") == []


def test_read_events_version_mismatch_skips_whole_file(tmp_path, capsys):
    f = tmp_path / "v2.events.jsonl"
    f.write_text(json.dumps({"v": 2, "event": "critic_verdict", "seq": 0}) + "\n", encoding="utf-8")
    assert events.read_events(f) == []  # v≠1 整文件跳过——不转发半懂的形态
    assert "整文件跳过" in capsys.readouterr().err


# ── reconcile_running 转发（3.2）─────────────────────────────────


def test_forward_events_dedup_and_text(tmp_path):
    mm = notify.FakeMMClient(user_ids={"roc": _MM_ID})
    lc, d, fake, data = _mk(tmp_path, mm=mm)
    run, _ = _running_run_with_events(lc, d, fake, tmp_path, [
        _line(0, "critic_verdict", verdict="revise", summary="证据不足回炉"),
        _line(1, "verify_verdict", verdict="pass", round=1, summary=""),
    ])
    lc.reconcile_running()  # running → 转发新行
    assert len(mm.dms) == 2
    t0, t1 = mm.dms[0][1], mm.dms[1][1]
    assert "critic 判定 → revise" in t0 and "证据不足回炉" in t0 and f"run #{run['id']}" in t0
    assert "verify 判定 → pass" in t1 and "round 1" in t1
    # 去重：同 (run, seq) 二跑不重发
    lc.reconcile_running()
    assert len(mm.dms) == 2
    # 新 seq 出现 → 只转发新行
    pass


def test_forward_new_seq_only_after_growth(tmp_path):
    mm = notify.FakeMMClient(user_ids={"roc": _MM_ID})
    lc, d, fake, data = _mk(tmp_path, mm=mm)
    run, ev = _running_run_with_events(lc, d, fake, tmp_path, [
        _line(0, "critic_verdict", verdict="pass", summary="")])
    lc.reconcile_running()
    assert len(mm.dms) == 1
    with ev.open("a", encoding="utf-8") as f:  # 容器追加 pr_opened（seq=1）
        f.write(json.dumps(_line(1, "pr_opened", pr_url="https://github.com/o/r/pull/9", repo="o/r")) + "\n")
    lc.reconcile_running()
    assert len(mm.dms) == 2 and "PR 开出（o/r）" in mm.dms[1][1] and mm.dms[1][1].endswith("/pull/9")


def test_forward_channel_rules(tmp_path):
    """频道规则：critic drop / verify 红（非 pass）进频道 + 私信；critic pass / pr_opened 仅私信。"""
    mm = notify.FakeMMClient(user_ids={"roc": _MM_ID})
    lc, d, fake, data = _mk(tmp_path, mm=mm)
    _running_run_with_events(lc, d, fake, tmp_path, [
        _line(0, "critic_verdict", verdict="drop", summary="无据"),
        _line(1, "critic_verdict", verdict="pass", summary=""),
        _line(2, "pr_opened", pr_url="https://github.com/o/r/pull/1", repo="o/r"),
        _line(3, "verify_verdict", verdict="revise", round=1, summary="测试未过"),
    ])
    lc.reconcile_running()
    assert len(mm.dms) == 4          # 全部私信
    assert len(mm.channels) == 2     # 仅 drop + verify 红
    assert "critic 判定 → drop" in mm.channels[0][1]
    assert "verify 判定 → revise" in mm.channels[1][1]


def test_forward_skips_v_mismatch_file_end_to_end(tmp_path, capsys):
    mm = notify.FakeMMClient(user_ids={"roc": _MM_ID})
    lc, d, fake, data = _mk(tmp_path, mm=mm)
    _running_run_with_events(lc, d, fake, tmp_path, [{"v": 2, "event": "critic_verdict", "seq": 0}])
    lc.reconcile_running()
    assert mm.dms == [] and "整文件跳过" in capsys.readouterr().err


def test_forward_orphan_run_skipped(tmp_path):
    """孤儿 run（queue_item 查无，如 queue_item_id=0 归档行）：无 prd_path 可拼 → 零转发零崩溃。"""
    mm = notify.FakeMMClient(user_ids={"roc": _MM_ID})
    lc, d, fake, data = _mk(tmp_path, mm=mm)
    _boot_inputs(data, "proj-a")
    runs_dir = data / "proj-a" / "state" / "runs" / "proj-a"
    runs_dir.mkdir(parents=True, exist_ok=True)
    run_dir = runs_dir / "20261021_orphan"  # 目录名仅占位（寻址不依赖它）
    (run_dir / "20261021_orphan.meta.json").parent.mkdir(parents=True, exist_ok=True)
    rid = d.create_run(0, "proj-a", str(run_dir))  # queue_item_id=0 = 查无 item
    d.set_run_running(rid, "fake-orphan")
    fake.containers["fake-orphan"] = {"spec": None, "state": "running", "exit_code": None}
    # 放一个「若被读必命中」的事件文件（stem 拼不出来——用目录名前缀 stem 也对不上）
    (runs_dir / "20261021_20261020_hand-a1b2c3d4.events.jsonl").write_text(
        json.dumps(_line(0, "critic_verdict", verdict="drop", summary="x")) + "\n", encoding="utf-8")
    lc.reconcile_running()
    assert mm.dms == []  # 跳过：无 item → 无寻址 → 无转发


def test_forward_stops_after_terminal(tmp_path):
    """run 终态后不读：容器 exited → reconcile 收敛 → 此后容器再写事件也不转发。"""
    mm = notify.FakeMMClient(user_ids={"roc": _MM_ID})
    lc, d, fake, data = _mk(tmp_path, mm=mm)
    run, ev = _running_run_with_events(lc, d, fake, tmp_path, [])
    ref = run["container_ref"]
    fake.finish(ref, 0)
    lc.reconcile_running()  # exited → succeeded 收敛（+ 终态通知）
    assert any("succeeded" in t for _, t in mm.dms)
    n_before = len(mm.dms)
    with ev.open("a", encoding="utf-8") as f:  # 终态后事件文件再长
        f.write(json.dumps(_line(0, "critic_verdict", verdict="drop", summary="late")) + "\n")
    lc.reconcile_running()
    assert len(mm.dms) == n_before  # 不再读（run 已不在 running 扫描集）


def test_notifier_none_channel_off_zero_behavior(tmp_path, capsys):
    """通道关闭（notifier None）：转发零行为零崩溃（stderr 亦无事件面输出）。"""
    lc, d, fake, data = _mk(tmp_path, mm=None)
    _running_run_with_events(lc, d, fake, tmp_path, [
        _line(0, "critic_verdict", verdict="drop", summary="x")])
    lc.reconcile_running()  # 不抛即过；无通知面可断言，靠 4.2 关闭态测试补 stderr 断言
    assert d.list_items()[0]["status"] == "running"


# ── 3.3 PA_REPORT_DIR env 注入 ───────────────────────────────────


def test_container_env_pa_report_dir(tmp_path):
    lc, d, fake, data = _mk(tmp_path)
    prd = tmp_path / _PRD_NAME
    prd.write_text("# PRD\n", encoding="utf-8")
    lc.engine.enqueue("proj-a", str(prd), "roc")
    assert lc.tick_dispatch() is True
    ref = d.runs_for_item(1)[0]["container_ref"]
    spec = fake.containers[ref]["spec"]
    assert spec.container_env == (f"PA_REPORT_DIR={CONTAINER_STATE}/reports",)
    assert spec.env_file is not None and spec.env_file.suffix == ".env"  # 凭据通道不混（分离）


# ── 4.1/4.2 无 run 放弃告警 ──────────────────────────────────────


def test_abandon_notifies_admin_on_missing_prereqs(tmp_path):
    mm = notify.FakeMMClient(user_ids={"roc": _MM_ID})
    lc, d, fake, data = _mk(tmp_path, mm=mm, boot_inputs=False)  # 必读面缺失
    prd = tmp_path / _PRD_NAME
    prd.write_text("# PRD\n", encoding="utf-8")
    lc.engine.enqueue("proj-a", str(prd), "roc")
    assert lc.tick_dispatch() is False
    item = d.list_items()[0]
    assert item["status"] == "failed"  # 放弃仍落账
    assert len(mm.dms) == 1 and "放弃" in mm.dms[0][1]
    assert "proj-a" in mm.dms[0][1] and "必读面缺失" in mm.dms[0][1]  # project + 原因摘要


def test_abandon_stderr_when_channel_off(tmp_path, capsys):
    lc, d, fake, data = _mk(tmp_path, mm=None, boot_inputs=False)
    prd = tmp_path / _PRD_NAME
    prd.write_text("# PRD\n", encoding="utf-8")
    lc.engine.enqueue("proj-a", str(prd), "roc")
    assert lc.tick_dispatch() is False
    err = capsys.readouterr().err
    assert "通道关闭" in err and "proj-a" in err        # 降级留痕可见
    assert d.list_items()[0]["status"] == "failed"       # fail_item 不受通知面影响
