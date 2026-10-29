"""test_confirmation.py — add-acceptance-spec-confirmation 2.5：确认环全链单测。

三面（FakeMMClient + tmp data_root 全离线，quality.sh 零 LLM）：
- db 状态机：四态全转移（requested→confirmed/rejected/expired）/ 非法转移拒 /
  UNIQUE 幂等 / 超时扫描 / 祖先链计数与继承；
- lifecycle 事件消费：KNOWN_EVENTS 死链防回归（注册前丢注册后不丢）/ 建行字段
  映射（initiator=enqueued_by、channel_id=绑定、prd_text=快照、origin=祖先链根）/
  无 initiator 不建行 / 事件重复到达不重建 / 终态 flush / 超时回执 / 文案形态；
- mm 三子命令：发起人判据 / 非 requested 幂等回执 / confirm 并节新 sha 入队 /
  未绑定保持 requested / reject 置态 / metrics 补充重入队 / 祖先链上界。
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config
import db as hub_db
import events
import lifecycle as lc_mod
import mm_intake
import notify
import queue_engine as qe
from runner import FakeRunner

_U = "a1b2c3d4e5f6a1b2c3d4e5f6a1"   # 26-hex 发起人 user_id
_C = "c1b2c3d4e5f6a1b2c3d4e5f6a1"   # 26 字符频道 id
_PRD_SHA = "f" * 64

_METRICS = [
    {"id": "m1", "metric": "body 背景色为 #0a0a12", "from": "暗底", "coverage": "proxy"},
    {"id": "m2", "metric": "标题文字色 #00f0ff", "from": "霓虹高亮", "coverage": "full"},
    {"id": "infra", "metric": "本仓未声明 playwright：需先补 profile 声明", "from": "", "coverage": "full"},
]

_MM_PRD = (
    "---\n"
    'project: "proj-a"\n'
    "source_path: ''\n"
    "source: mm-channel\n"
    f'user_id: "{_U}"\n'
    'username: "rocju"\n'
    f'channel_id: "{_C}"\n'
    'evidence: "频道原文"\n'
    "---\n\n给首页增加赛博朋克风格样式（暗底、霓虹高亮）。\n"
)


def _cfg(tmp_path: Path):
    return config.load_config({
        "HUB_USER_ID": "roc",
        "HUB_USER_ANTHROPIC_BASE_URL": "https://litellm.example",
        "HUB_USER_ANTHROPIC_AUTH_TOKEN": "tok-abcdef0123456789abcdef",
        "HUB_USER_GH_TOKEN": "ghp_abcdefghij0123456789",
        "HUB_IMAGE_TAG_DEFAULT": "pa-executor:v0.1.3",
        "HUB_DATA_ROOT": str(tmp_path / "data"),
    })


def _mk(tmp_path: Path, bind: bool = True):
    d = hub_db.HubDB(tmp_path / "h.sqlite3")
    eng = qe.QueueEngine(d, max_containers=2, max_retries=2, image_tag_default="pa-executor:v0.1.3")
    cfg = _cfg(tmp_path)
    data = tmp_path / "data"
    profs = data / "proj-a" / "profiles"
    profs.mkdir(parents=True, exist_ok=True)
    (profs / "proj-a.yaml").write_text("name: proj-a\nadmission: true\n", encoding="utf-8")
    (data / "proj-a" / "sources.yaml").write_text("sources: []\n", encoding="utf-8")
    if bind:
        d.upsert_bind("proj-a", _C, "roc")
    return d, eng, cfg, data


def _mk_lc(tmp_path: Path, bind: bool = True):
    """lifecycle 形态（事件消费面用）：真 DB/engine + FakeRunner + FakeMMClient notifier。"""
    d, eng, cfg, data = _mk(tmp_path, bind=bind)
    mm = notify.FakeMMClient(user_ids={"rocju": _U})
    lc = lc_mod.Lifecycle(d, eng, FakeRunner(images={"pa-executor:v0.1.3"}),
                          cfg, notifier=notify.Notifier(cfg, mm=mm, database=d))
    return lc, d, eng, cfg, data, mm


def _mk_item(eng, data: Path, enqueued_by: str | None = _U) -> int:
    prd = data / "proj-a" / "state" / "prd" / "mm-abcdef12.md"
    prd.parent.mkdir(parents=True, exist_ok=True)
    prd.write_text(_MM_PRD, encoding="utf-8")
    return eng.enqueue("proj-a", str(prd), enqueued_by if enqueued_by is not None else "")


def _run_row(d, qid: int) -> dict:
    return d.runs_for_item(qid)[0]


def _write_events(run: dict, qid: int, rows: list[dict]) -> None:
    """模拟容器内 pa 写伴生事件文件（locate_pa_artifact 精确拼路径：run 目录平级）。"""
    rd = Path(run["run_dir"])
    stamp = rd.name.split("_", 1)[0]
    stem = f"mm-{'abcdef12'}"
    (rd.parent / f"{stamp}_{stem}.events.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def _mp_event(qid: int, seq: int = 0, prd_sha: str = _PRD_SHA) -> dict:
    return {"v": 1, "event": "metrics_proposed", "seq": seq, "stamp": "20261029",
            "project": "proj-a", "metrics": _METRICS, "prd_sha": prd_sha,
            "initiator": _U}


# ── A. db 状态机（2.1/2.5）────────────────────────────────────────

def test_confirm_crud_unique_idempotent(tmp_path):
    d, *_ = _mk(tmp_path)
    rid = d.insert_confirmation(item_id=1, project="proj-a", channel_id=_C,
                                initiator=_U, prd_text=_MM_PRD,
                                metrics_json=json.dumps(_METRICS), origin_sha=_PRD_SHA)
    assert rid == 1
    assert d.insert_confirmation(item_id=1, project="proj-a", channel_id=_C,
                                 initiator=_U, prd_text="x", metrics_json="[]",
                                 origin_sha="o") is None          # UNIQUE 撞键 → None 幂等
    row = d.get_confirmation(rid)
    assert row["status"] == "requested" and row["initiator"] == _U
    assert d.get_confirmation_by_item(1)["id"] == rid
    assert d.get_confirmation(999) is None


def test_confirm_state_machine_all_transitions(tmp_path):
    d, *_ = _mk(tmp_path)
    for final in ("confirmed", "rejected", "expired"):
        rid = d.insert_confirmation(item_id={"confirmed": 1, "rejected": 2, "expired": 3}[final],
                                    project="proj-a", channel_id=_C, initiator=_U,
                                    prd_text="p", metrics_json="[]", origin_sha="o")
        d.decide_confirmation(rid, final)
        assert d.get_confirmation(rid)["status"] == final
        try:
            d.decide_confirmation(rid, "confirmed" if final != "confirmed" else "rejected")
            raise AssertionError("终态再转移应拒")
        except ValueError:
            pass
    rid_bad = d.insert_confirmation(item_id=4, project="proj-a", channel_id=_C,
                                    initiator=_U, prd_text="p", metrics_json="[]", origin_sha="o")
    try:
        d.decide_confirmation(rid_bad, "requested")                # 非法目标态
        raise AssertionError("非法目标态应拒")
    except ValueError:
        pass


def test_confirm_expire_sweep(tmp_path):
    d, *_ = _mk(tmp_path)
    rid_old = d.insert_confirmation(item_id=1, project="proj-a", channel_id=_C, initiator=_U,
                                    prd_text="p", metrics_json="[]", origin_sha="o")
    rid_new = d.insert_confirmation(item_id=2, project="proj-a", channel_id=_C, initiator=_U,
                                    prd_text="p", metrics_json="[]", origin_sha="o")
    d.decide_confirmation(rid_new, "confirmed")
    old_time = (datetime.now(timezone.utc) - timedelta(hours=73)).isoformat(timespec="seconds")
    d._conn.execute("UPDATE confirmation_requests SET created_at=? WHERE id=?", (old_time, rid_old))
    swept = d.expire_sweep_confirmations()
    assert [r["id"] for r in swept] == [rid_old]                    # 仅超窗 requested 被扫
    assert d.get_confirmation(rid_old)["status"] == "expired"
    assert d.get_confirmation(rid_new)["status"] == "confirmed"    # 终态行不动


def test_confirm_count_origin_and_latest_inherit(tmp_path):
    d, *_ = _mk(tmp_path)
    for i in range(3):                                             # 同链 3 confirmed
        rid = d.insert_confirmation(item_id=10 + i, project="proj-a", channel_id=_C,
                                    initiator=_U, prd_text="p", metrics_json="[]",
                                    origin_sha="sha-root")
        d.decide_confirmation(rid, "confirmed")
    rid4 = d.insert_confirmation(item_id=99, project="proj-a", channel_id=_C,
                                 initiator=_U, prd_text="p", metrics_json="[]", origin_sha="sha-root")
    d.decide_confirmation(rid4, "rejected")
    assert d.count_confirm_origin("sha-root") == 3                 # 只数 confirmed
    assert d.latest_confirmation_origin("proj-a", _U) == "sha-root"  # 继承判据
    assert d.latest_confirmation_origin("proj-b", _U) is None


def test_confirm_list_pending(tmp_path):
    d, *_ = _mk(tmp_path)
    assert d.list_confirmations_pending() == []
    d.insert_confirmation(item_id=1, project="proj-a", channel_id=_C, initiator=_U,
                          prd_text="p", metrics_json="[]", origin_sha="o")
    assert len(d.list_confirmations_pending()) == 1


# ── B. lifecycle 事件消费（2.2/2.4）────────────────────────────────

def test_known_events_regression_unregistered_dropped(tmp_path):
    """白名单未注册即死链（spec scenario）：注册前丢、注册后不丢——锁死防回归。"""
    f = tmp_path / "e.jsonl"
    f.write_text(json.dumps(_mp_event(1)) + "\n", encoding="utf-8")
    orig = events.KNOWN_EVENTS
    try:
        events.KNOWN_EVENTS = frozenset(orig - {"metrics_proposed"})   # 注册前形态
        assert events.read_events(f) == []                          # 整行丢弃
    finally:
        events.KNOWN_EVENTS = orig
    assert len(events.read_events(f)) == 1                          # 注册后不丢


def test_forward_creates_row_and_posts(tmp_path):
    lc, d, eng, cfg, data, mm = _mk_lc(tmp_path)
    qid = _mk_item(eng, data)
    assert lc.tick_dispatch() is True
    run = _run_row(d, qid)
    _write_events(run, qid, [_mp_event(qid)])
    lc._forward_events(run)

    row = d.get_confirmation_by_item(qid)
    assert row is not None and row["status"] == "requested"
    assert row["initiator"] == _U                                   # initiator=enqueued_by
    assert row["channel_id"] == _C                                  # 项目绑定
    assert row["prd_text"] == _MM_PRD                               # PRD 全文快照
    assert row["origin_sha"] == _PRD_SHA                            # 首次 = 事件 prd_sha
    assert json.loads(row["metrics_json"]) == _METRICS
    assert len(mm.channels) == 1 and mm.dms == []                   # 绑定 → 频道 post
    text = mm.channels[0][1]
    assert f"#{row['id']}" in text and "m1" not in text             # 行 id 可回执；清单不带 id 前缀
    assert "body 背景色为 #0a0a12" in text and "〔代理指标〕" in text  # proxy 逐条标注
    assert "本仓未声明" in text                                      # infra 尾注条目也进频道
    assert "/pa confirm" in text and "/pa reject" in text and "/pa metrics" in text

    # 事件重复到达（重启语义：_event_seen 清空）→ 行不重建（同 rid），文本幂等重发
    lc2 = lc_mod.Lifecycle(d, eng, FakeRunner(images={"pa-executor:v0.1.3"}), cfg,
                           notifier=notify.Notifier(cfg, mm=mm, database=d))
    lc2._forward_events(run)
    assert d.get_confirmation_by_item(qid)["id"] == row["id"]       # 不重建
    assert len(mm.channels) == 2                                    # 重发一条（接受语义）


def test_forward_no_initiator_no_row_no_post(tmp_path):
    lc, d, eng, cfg, data, mm = _mk_lc(tmp_path)
    qid = _mk_item(eng, data, enqueued_by=None)                     # 面板形态无 enqueued_by
    assert lc.tick_dispatch() is True
    run = _run_row(d, qid)
    _write_events(run, qid, [_mp_event(qid)])
    lc._forward_events(run)
    assert d.get_confirmation_by_item(qid) is None                  # 不建行（回归信号）
    assert mm.channels == []                                        # 无行可确认——不 post


def test_finish_run_flushes_metrics_event(tmp_path):
    """终态 flush（2.4）：running 末轮后写入的 metrics_proposed 不丢（run 早退死链面）。"""
    lc, d, eng, cfg, data, mm = _mk_lc(tmp_path)
    qid = _mk_item(eng, data)
    assert lc.tick_dispatch() is True
    run = _run_row(d, qid)
    _write_events(run, qid, [_mp_event(qid)])                       # running 轮询后新写
    lc._runner.finish(run["container_ref"], exit_code=0)            # 容器退出
    lc.reconcile_running()                                          # exited 分支：flush 先于归档
    assert d.get_confirmation_by_item(qid) is not None              # 事件没丢——行建了
    assert d.runs_for_item(qid)[0]["status"] == "succeeded"


def test_origin_inherited_across_reenqueue_cycle(tmp_path):
    """二环继承：confirm 重入队后新 run 的事件带新 prd_sha，新行 origin 保持链根。"""
    lc, d, eng, cfg, data, mm = _mk_lc(tmp_path)
    qid1 = _mk_item(eng, data)
    assert lc.tick_dispatch() is True
    run1 = _run_row(d, qid1)
    _write_events(run1, qid1, [_mp_event(qid1, seq=0, prd_sha="root" + "0" * 60)])
    lc._forward_events(run1)
    row1 = d.get_confirmation_by_item(qid1)
    d.decide_confirmation(row1["id"], "confirmed")                  # 第一环拍板
    lc._runner.finish(run1["container_ref"], exit_code=0)           # 第一 run 终态（project 串行位释放）
    lc.reconcile_running()
    qid2 = _mk_item(eng, data)                                      # confirm 并节新 sha 入队（模拟）
    assert lc.tick_dispatch() is True
    run2 = _run_row(d, qid2)
    _write_events(run2, qid2, [_mp_event(qid2, seq=0, prd_sha="child" + "0" * 59)])
    lc._forward_events(run2)
    row2 = d.get_confirmation_by_item(qid2)
    assert row2["origin_sha"] == row1["origin_sha"]                 # 继承链根，不随新 sha 换


def test_sweep_confirmations_notifies_channel(tmp_path):
    lc, d, eng, cfg, data, mm = _mk_lc(tmp_path)
    rid = d.insert_confirmation(item_id=1, project="proj-a", channel_id=_C, initiator=_U,
                                prd_text="p", metrics_json="[]", origin_sha="o")
    old_time = (datetime.now(timezone.utc) - timedelta(hours=80)).isoformat(timespec="seconds")
    d._conn.execute("UPDATE confirmation_requests SET created_at=? WHERE id=?", (old_time, rid))
    lc.sweep_confirmations()
    assert d.get_confirmation(rid)["status"] == "expired"
    assert len(mm.channels) == 1 and "已过期" in mm.channels[0][1]


def test_event_text_truncates_over_eight(tmp_path):
    run = {"id": 7, "run_dir": "/x/20261029_slug", "project": "proj-a"}
    row = _mp_event(1)
    row["metrics"] = [{"id": f"m{i}", "metric": f"指标{i}", "from": "f", "coverage": "full"}
                      for i in range(10)]
    text = lc_mod._event_text(run, row, rid=42)
    assert "指标7" in text and "指标8" not in text                   # 前 8 条截断
    assert "共 10 条" in text and "#42" in text


# ── C. mm 三子命令（2.3）───────────────────────────────────────────

def _cmd(d, eng, cfg, text, user_id=_U):
    return mm_intake.handle_command(
        database=d, engine=eng, cfg=cfg, mm=notify.FakeMMClient(),
        form={"text": text, "channel_id": _C, "user_id": user_id, "user_name": "rocju"},
        now_utc=datetime(2026, 10, 29, 12, 0, 0, tzinfo=timezone.utc))


def _mk_row(d, item_id=1, status="requested", origin="sha-root"):
    rid = d.insert_confirmation(item_id=item_id, project="proj-a", channel_id=_C,
                                initiator=_U, prd_text=_MM_PRD,
                                metrics_json=json.dumps(_METRICS), origin_sha=origin)
    if status != "requested":
        d.decide_confirmation(rid, status)
    return rid


def test_confirm_happy_path_merges_and_enqueues(tmp_path):
    d, eng, cfg, data = _mk(tmp_path)
    rid = _mk_row(d)
    out = _cmd(d, eng, cfg, f"confirm #{rid}")
    assert "已拍板入队" in out["ephemeral"] and out["channel_post"] is not None
    assert d.get_confirmation(rid)["status"] == "confirmed"
    # 新 PRD 落盘：指标节并入 + 原 frontmatter 保留（下游 critic 判全指标 MM 源）
    files = sorted((data / "proj-a" / "prd").glob("mm-*.md"))
    assert len(files) == 1
    merged = files[0].read_text(encoding="utf-8")
    assert "## 验收指标（发起人确认）" in merged
    assert "m1: body 背景色为 #0a0a12〔代理指标〕" in merged          # id 前缀 + proxy 标注
    assert "m2: 标题文字色 #00f0ff" in merged
    assert "本仓未声明" not in merged                                 # infra 尾注不入 PRD
    assert "source: mm-channel" in merged and f'user_id: "{_U}"' in merged


def test_confirm_not_initiator_rejected(tmp_path):
    d, eng, cfg, data = _mk(tmp_path)
    rid = _mk_row(d)
    out = _cmd(d, eng, cfg, f"confirm #{rid}", user_id="b" * 26)
    assert "仅发起人" in out["ephemeral"] and out["channel_post"] is None
    assert d.get_confirmation(rid)["status"] == "requested"


def test_confirm_terminal_states_idempotent_receipt(tmp_path):
    d, eng, cfg, data = _mk(tmp_path)
    for st, word in (("confirmed", "已确认"), ("rejected", "已放弃"), ("expired", "已过期")):
        rid = _mk_row(d, item_id={"confirmed": 1, "rejected": 2, "expired": 3}[st], status=st)
        out = _cmd(d, eng, cfg, f"confirm #{rid}")
        assert word in out["ephemeral"] and "幂等" in out["ephemeral"]


def test_confirm_missing_row_and_bad_usage(tmp_path):
    d, eng, cfg, data = _mk(tmp_path)
    assert "不存在" in _cmd(d, eng, cfg, "confirm #999")["ephemeral"]
    assert "缺行 id" in _cmd(d, eng, cfg, "confirm")["ephemeral"]


def test_confirm_unbound_keeps_requested(tmp_path):
    d, eng, cfg, data = _mk(tmp_path, bind=False)
    rid = _mk_row(d)
    out = _cmd(d, eng, cfg, f"confirm #{rid}")
    assert "未绑定" in out["ephemeral"]
    assert d.get_confirmation(rid)["status"] == "requested"          # spec：保持 requested
    assert not list((data / "proj-a" / "prd").glob("mm-*.md")) if (data / "proj-a" / "prd").exists() else True


def test_confirm_origin_cap_blocks_reenqueue(tmp_path):
    d, eng, cfg, data = _mk(tmp_path)
    for i in range(3):                                              # 同链 3 次 confirmed 达上界
        _mk_row(d, item_id=10 + i, status="confirmed")
    rid = _mk_row(d, item_id=99)                                    # 第 4 行 requested
    out = _cmd(d, eng, cfg, f"confirm #{rid}")
    assert "请人工改写" in out["ephemeral"]
    assert d.get_confirmation(rid)["status"] == "requested"          # 不入队不动行
    assert not (data / "proj-a" / "state" / "prd").exists() or \
        not list((data / "proj-a" / "prd").glob("mm-*.md"))


def test_reject_sets_state_and_channel_post(tmp_path):
    d, eng, cfg, data = _mk(tmp_path)
    rid = _mk_row(d)
    out = _cmd(d, eng, cfg, f"reject #{rid}")
    assert "已放弃" in out["ephemeral"] and out["channel_post"] is not None
    assert d.get_confirmation(rid)["status"] == "rejected"


def test_metrics_supplement_reenqueues(tmp_path):
    d, eng, cfg, data = _mk(tmp_path)
    rid = _mk_row(d)
    out = _cmd(d, eng, cfg, f"metrics #{rid} 要支持一键切回默认主题")
    assert "重新入队" in out["ephemeral"] and out["channel_post"] is not None
    assert d.get_confirmation(rid)["status"] == "confirmed"          # 计入祖先链（D5）
    files = sorted((data / "proj-a" / "prd").glob("mm-*.md"))
    merged = files[0].read_text(encoding="utf-8")
    assert "## 发起人补充" in merged and "一键切回默认主题" in merged
    assert "验收指标（发起人确认）" not in merged                     # 补充重拟不并入旧清单


def test_metrics_requires_supplement_text(tmp_path):
    d, eng, cfg, data = _mk(tmp_path)
    rid = _mk_row(d)
    assert "补充" in _cmd(d, eng, cfg, f"metrics #{rid}")["ephemeral"]  # 缺补充 → 用法回执
