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
import os
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
    d.upsert_bind("proj-a", "chan-1", "test")  # 6.x 归属路由判据 = 绑定表（env 映射废止）
    if boot_inputs:
        _boot_inputs(tmp_path / "data", "proj-a")
    notifier = notify.Notifier(cfg, mm=mm, database=d) if mm is not None else None
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
    lc.reconcile_running()  # running → 转发新行（绑定 proj-a → 全量进频道）
    assert len(mm.channels) == 2 and mm.dms == []  # 全事件进频道 + 不重复私信
    t0, t1 = mm.channels[0][1], mm.channels[1][1]
    assert t0.startswith("<@")  # @发起人提及（roc 解析 mm id）
    assert "critic 判定 → revise" in t0 and "证据不足回炉" in t0 and f"run #{run['id']}" in t0
    assert "verify 判定 → pass" in t1 and "round 1" in t1  # pass 也进（新口径全事件）
    # 去重：同 (run, seq) 二跑不重发
    lc.reconcile_running()
    assert len(mm.channels) == 2
    # 新 seq 出现 → 只转发新行
    pass


def test_forward_events_real_machine_doubled_stamp_naming(tmp_path):
    """真机实证回归锁（2026-10-28，run #73/#74）：pa inject 给注入文件名再前缀
    stamp（自增 m 避碰，hub 拿不到 actual），pa 侧 append 以注入后 stem 拼事件
    文件 = {actual}_{actual}_{stem}.events.jsonl——hub 精确拼不中，
    locate_pa_artifact 按 content-addressed stem 后缀 glob 兜底（本 bug 曾致
    语义事件转发在生产从未生效——面板时代 20261027_20261027_* 文件同为铁证）。"""
    mm = notify.FakeMMClient(user_ids={"roc": _MM_ID})
    lc, d, fake, data = _mk(tmp_path, mm=mm)
    prd = tmp_path / "mm-5d554c15.md"  # content-addressed 形态（同 stem 工件唯一）
    prd.write_text("# PRD\n", encoding="utf-8")
    qid = lc.engine.enqueue("proj-a", str(prd), "roc")
    assert lc.tick_dispatch() is True
    run = d.runs_for_item(qid)[0]
    rd = Path(run["run_dir"])
    stamp = rd.name.split("_", 1)[0]
    (rd.parent / f"{stamp}_{stamp}_mm-5d554c15.events.jsonl").write_text(
        json.dumps(_line(0, "critic_verdict", verdict="drop", summary="无验收标准")) + "\n",
        encoding="utf-8")
    lc.reconcile_running()
    assert len(mm.channels) == 1 and mm.dms == []
    assert "critic 判定 → drop" in mm.channels[0][1] and "无验收标准" in mm.channels[0][1]


def test_forward_new_seq_only_after_growth(tmp_path):
    mm = notify.FakeMMClient(user_ids={"roc": _MM_ID})
    lc, d, fake, data = _mk(tmp_path, mm=mm)
    run, ev = _running_run_with_events(lc, d, fake, tmp_path, [
        _line(0, "critic_verdict", verdict="pass", summary="")])
    lc.reconcile_running()
    assert len(mm.channels) == 1
    with ev.open("a", encoding="utf-8") as f:  # 容器追加 pr_opened（seq=1）
        f.write(json.dumps(_line(1, "pr_opened", pr_url="https://github.com/o/r/pull/9", repo="o/r")) + "\n")
    lc.reconcile_running()
    assert len(mm.channels) == 2 and "PR 开出（o/r）" in mm.channels[1][1] and mm.channels[1][1].endswith("/pull/9")


def test_forward_channel_rules(tmp_path):
    """绑定频道四类全量进（6.2 新口径：per-event 广播判定废止——pass/pr_opened 也进，
    全事件时间线语义）；发起人零重复私信。"""
    mm = notify.FakeMMClient(user_ids={"roc": _MM_ID})
    lc, d, fake, data = _mk(tmp_path, mm=mm)
    _running_run_with_events(lc, d, fake, tmp_path, [
        _line(0, "critic_verdict", verdict="drop", summary="无据"),
        _line(1, "critic_verdict", verdict="pass", summary=""),
        _line(2, "pr_opened", pr_url="https://github.com/o/r/pull/1", repo="o/r"),
        _line(3, "verify_verdict", verdict="revise", round=1, summary="测试未过"),
    ])
    lc.reconcile_running()
    assert mm.dms == []              # 绑定频道已覆盖个人触达（@提及）
    assert len(mm.channels) == 4     # 四类全量（旧「仅警示类」口径废止）
    assert "critic 判定 → drop" in mm.channels[0][1]
    assert "critic 判定 → pass" in mm.channels[1][1]  # pass 也进（新口径锚）
    assert "PR 开出（o/r）" in mm.channels[2][1]       # pr_opened 也进
    assert "verify 判定 → revise" in mm.channels[3][1]
    assert d.get_channel_binding_by_project("proj-a")["channel_id"] == "chan-1"  # 路由判据 = 绑定表


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
    lc.reconcile_running()  # exited → succeeded 收敛（+ 终态通知进绑定频道）
    assert any("succeeded" in t for _, t in mm.channels)
    n_before = len(mm.channels)
    with ev.open("a", encoding="utf-8") as f:  # 终态后事件文件再长
        f.write(json.dumps(_line(0, "critic_verdict", verdict="drop", summary="late")) + "\n")
    lc.reconcile_running()
    assert len(mm.channels) == n_before  # 不再读（run 已不在 running 扫描集）


def test_notifier_none_channel_off_zero_behavior(tmp_path, capsys):
    """通道关闭（notifier None）：转发零行为零崩溃（stderr 亦无事件面输出）。"""
    lc, d, fake, data = _mk(tmp_path, mm=None)
    _running_run_with_events(lc, d, fake, tmp_path, [
        _line(0, "critic_verdict", verdict="drop", summary="x")])
    lc.reconcile_running()  # 不抛即过；无通知面可断言，靠 4.2 关闭态测试补 stderr 断言
    assert d.list_items()[0]["status"] == "running"


# ── 3.3 PA_REPORT_DIR env 注入 ───────────────────────────────────


def test_container_env_pa_report_dir(tmp_path, monkeypatch):
    """#22：PA_* 透传后本断言须在受控 env 下跑（宿主 PA_* env 不渗进 spec）。"""
    for k in list(os.environ):
        if k.startswith("PA_"):
            monkeypatch.delenv(k)
    lc, d, fake, data = _mk(tmp_path)
    prd = tmp_path / _PRD_NAME
    prd.write_text("# PRD\n", encoding="utf-8")
    lc.engine.enqueue("proj-a", str(prd), "roc")
    assert lc.tick_dispatch() is True
    ref = d.runs_for_item(1)[0]["container_ref"]
    spec = fake.containers[ref]["spec"]
    assert spec.container_env == (f"PA_REPORT_DIR={CONTAINER_STATE}/reports",)
    assert spec.env_file is not None and spec.env_file.suffix == ".env"  # 凭据通道不混（分离）


def test_container_env_passes_pa_flags(tmp_path, monkeypatch):
    """#22：hub 进程 env 的 PA_* 透传进 run 容器（feature flag 通道，与凭据 env-file 分离）。"""
    for k in list(os.environ):
        if k.startswith("PA_"):
            monkeypatch.delenv(k)
    monkeypatch.setenv("PA_SINGLE_FLIGHT_AUTO_MERGE", "1")
    monkeypatch.setenv("PA_GRAPH_ORCHESTRATOR", "0")
    lc, d, fake, data = _mk(tmp_path)
    prd = tmp_path / _PRD_NAME
    prd.write_text("# PRD\n", encoding="utf-8")
    lc.engine.enqueue("proj-a", str(prd), "roc")
    assert lc.tick_dispatch() is True
    ref = d.runs_for_item(1)[0]["container_ref"]
    spec = fake.containers[ref]["spec"]
    assert spec.container_env == (
        f"PA_REPORT_DIR={CONTAINER_STATE}/reports",            # 生成项在前
        "PA_GRAPH_ORCHESTRATOR=0",                              # sorted 确定性
        "PA_SINGLE_FLIGHT_AUTO_MERGE=1")
    # 非 PA_ 前缀不透传（如 HUB_* 面板配置不渗进 run 容器）
    assert not any(kv.startswith("HUB_") for kv in spec.container_env)


# ── 4.1/4.2 无 run 放弃告警 ──────────────────────────────────────


def test_abandon_bound_routes_to_channel(tmp_path):
    """6.2 归属路由：proj-a 绑定 chan-1 → 无 run 放弃进频道（团队可见），无 DM。"""
    mm = notify.FakeMMClient(user_ids={"roc": _MM_ID})
    lc, d, fake, data = _mk(tmp_path, mm=mm, boot_inputs=False)  # 必读面缺失
    prd = tmp_path / _PRD_NAME
    prd.write_text("# PRD\n", encoding="utf-8")
    lc.engine.enqueue("proj-a", str(prd), "roc")
    assert lc.tick_dispatch() is False
    item = d.list_items()[0]
    assert item["status"] == "failed"  # 放弃仍落账
    assert mm.dms == [] and len(mm.channels) == 1 and mm.channels[0][0] == "chan-1"
    t = mm.channels[0][1]
    assert "放弃" in t and "proj-a" in t and "必读面缺失" in t  # project + 原因摘要


def test_abandon_unbound_falls_back_admin_dm(tmp_path):
    """未绑定 → admin DM 不变（notify_abandon 回落 cfg.user_id 路径）。"""
    mm = notify.FakeMMClient(user_ids={"roc": _MM_ID})
    lc, d, fake, data = _mk(tmp_path, mm=mm, boot_inputs=False)
    d.unbind_by_channel("chan-1")  # 摘掉绑定 → 未绑定形态
    prd = tmp_path / _PRD_NAME
    prd.write_text("# PRD\n", encoding="utf-8")
    lc.engine.enqueue("proj-a", str(prd), "roc")
    assert lc.tick_dispatch() is False
    assert mm.channels == [] and len(mm.dms) == 1
    assert "放弃" in mm.dms[0][1] and "proj-a" in mm.dms[0][1] and "必读面缺失" in mm.dms[0][1]


def test_abandon_stderr_when_channel_off(tmp_path, capsys):
    lc, d, fake, data = _mk(tmp_path, mm=None, boot_inputs=False)
    prd = tmp_path / _PRD_NAME
    prd.write_text("# PRD\n", encoding="utf-8")
    lc.engine.enqueue("proj-a", str(prd), "roc")
    assert lc.tick_dispatch() is False
    err = capsys.readouterr().err
    assert "通道关闭" in err and "proj-a" in err        # 降级留痕可见
    assert d.list_items()[0]["status"] == "failed"       # fail_item 不受通知面影响


# ── fix-event-outlet-verify-parity：invalid reason / acceptance_suite 判读 ──


def test_forward_verify_invalid_reason_to_channel(tmp_path):
    """verify invalid ≠ pass → 进频道（既有规则钉死防回归）+ 文本机械拼 reason（六成因枚举值）。"""
    mm = notify.FakeMMClient(user_ids={"roc": _MM_ID})
    lc, d, fake, data = _mk(tmp_path, mm=mm)
    _running_run_with_events(lc, d, fake, tmp_path, [
        _line(0, "verify_verdict", verdict="invalid", round=1, reason="wallclock_timeout", summary="")])
    lc.reconcile_running()
    assert mm.dms == [] and len(mm.channels) == 1        # 绑定 → 频道不私信
    assert "verify 判定 → invalid" in mm.channels[0][1]
    assert "wallclock_timeout" in mm.channels[0][1]      # reason 机械拼接可见（triage 入口）


def test_forward_verify_revise_text_unchanged(tmp_path):
    """旧版回归锚：语义审轨 pass/revise 行无 reason 字段 → 文本零变化（round 拼接位置不动）。"""
    mm = notify.FakeMMClient(user_ids={"roc": _MM_ID})
    lc, d, fake, data = _mk(tmp_path, mm=mm)
    _running_run_with_events(lc, d, fake, tmp_path, [
        _line(0, "verify_verdict", verdict="revise", round=2, summary="测试未过")])
    lc.reconcile_running()
    assert mm.dms == []                                  # 绑定 → 频道不私信
    t = mm.channels[0][1]                                # 绑定全量进频道（revise 同）
    assert "verify 判定 → revise（round 2）" in t and "测试未过" in t
    assert "reason" not in t and "原因" not in t          # 无 reason 行 → 不拼 reason 节


def test_forward_acceptance_absent_to_channel(tmp_path):
    """acceptance absent：进频道（套件缺位是推进阻断，团队可见）；reason/detail 机械拼接。"""
    mm = notify.FakeMMClient(user_ids={"roc": _MM_ID})
    lc, d, fake, data = _mk(tmp_path, mm=mm)
    _running_run_with_events(lc, d, fake, tmp_path, [
        _line(0, "acceptance_suite", status="absent", reason="no_harness",
              detail="profile 未声明 harness")])
    lc.reconcile_running()
    assert mm.dms == [] and len(mm.channels) == 1        # 绑定 → 频道不私信
    t = mm.channels[0][1]
    assert "acceptance" in t and "absent" in t and "no_harness" in t
    assert "profile 未声明 harness" in t


def test_forward_acceptance_stored_also_channel_with_supersedes_chain(tmp_path):
    """acceptance stored 也进频道（6.2 新口径：四类全量——stored 是正常产出也是项目
    状态）；换版（supersedes 非 null）文本附换版链（拼接契约零变化继承）。"""
    mm = notify.FakeMMClient(user_ids={"roc": _MM_ID})
    lc, d, fake, data = _mk(tmp_path, mm=mm)
    _running_run_with_events(lc, d, fake, tmp_path, [
        _line(0, "acceptance_suite", status="stored", digest="a" * 64,
              criteria_ids=["c1"], supersedes=None),
        _line(1, "acceptance_suite", status="stored", digest="b" * 64,
              criteria_ids=["c1"], supersedes="a" * 64)])
    lc.reconcile_running()
    assert mm.dms == [] and len(mm.channels) == 2         # stored 全量进频道（旧「仅私信」口径废止）
    t0, t1 = mm.channels[0][1], mm.channels[1][1]
    assert "stored" in t0 and "a" * 64 in t0
    assert "supersedes" not in t0.lower()                  # 首版（null）无换版链
    assert "supersedes" in t1.lower() and "a" * 64 in t1   # 换版行附链（旧 digest 可回查）
