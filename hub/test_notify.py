"""notify.py 单测——bot 通道 / run 终态通知 / fail-open / 频道广播（add-hub-slice-4a，spec: hub-notifications）。

D7 测试接缝：FakeMMClient 按 (recipient, text) 记调用清单——测试只锁此契约，
不锁 MM API 调用序列。全离线（httpx 不触网）。
"""
import httpx
from pathlib import Path

import api
import config
import notify
from db import HubDB

_MM_ID = "a1b2c3d4e5f6a1b2c3d4e5f6a1"  # 26-hex（值形态分流判据）


def _cfg(tmp_path: Path, **over) -> config.HubConfig:
    base = dict(
        user_id="roc", anthropic_base_url="https://litellm.example",
        anthropic_auth_token="tok", gh_token="ghp", user_model=None,
        image_tag_default="pa:v1", data_root=tmp_path / "data",
        max_containers=1, max_retries=0,
        mm_base_url="https://mm.example.com", mm_bot_token="bot-tok",
    )
    base.update(over)
    return config.HubConfig(**base)


def _notify(**kw):
    out = dict(run_id=7, project="proj-a", run_dir_name="20261021_my-feature", status="succeeded")
    out.update(kw)  # 允许覆盖 status/exit_code 等
    return out


def _db_bound(tmp_path: Path, *pairs: tuple[str, str]) -> HubDB:
    """带绑定表的 HubDB（6.x 归属路由判据）：pairs = (project, channel_id)。"""
    d = HubDB(tmp_path / "notify-h.sqlite3")
    for project, chan in pairs:
        d.upsert_bind(project, chan, "test")
    return d


def test_build_notifier_off_without_bot_token_or_base_url(tmp_path):
    # D8 关闭态构造：缺 bot token 或缺 base_url → None（通道关闭零行为，两级回退之一级）
    assert notify.build_notifier(_cfg(tmp_path, mm_bot_token=None)) is None
    assert notify.build_notifier(_cfg(tmp_path, mm_base_url=None)) is None
    assert notify.build_notifier(_cfg(tmp_path)) is not None  # 齐备才造


def test_dm_to_mm_user_id_direct_no_lookup(tmp_path):
    # 值形态第一代：26-hex mm_user_id 直发，不经 username 解析
    mm = notify.FakeMMClient()
    n = notify.Notifier(_cfg(tmp_path), mm=mm)
    n.notify_run(**_notify(enqueued_by=_MM_ID))
    assert mm.dms == [(_MM_ID, mm.dms[0][1])] and mm.lookups == []


def test_real_machine_nonhex_id_form_is_direct_not_username(tmp_path):
    # 真机值域锁（2026-10-28）：实例 user id 含 g-z 非纯 hex（bi79e3rknjncfj9mfaz9gebzmo）。
    # _MM_ID_RE 钉 [0-9a-f] 时这类 id 被误判当 username 走 lookup 解析 → 丢通知。
    real = "bi79e3rknjncfj9mfaz9gebzmo"  # 26 位小写字母数字（真机实证形态）
    mm = notify.FakeMMClient()
    n = notify.Notifier(_cfg(tmp_path), mm=mm)
    n.notify_run(**_notify(enqueued_by=real))  # 未绑定 → 私信路径
    assert mm.dms == [(real, mm.dms[0][1])] and mm.lookups == []  # 直发不解析
    n2 = notify.Notifier(_cfg(tmp_path), mm=mm, database=_db_bound(tmp_path, ("proj-a", "chan-1")))
    n2.notify_run(**_notify(enqueued_by=real))  # 绑定 → 频道提及直拼
    assert mm.channels[-1][1].startswith(f"<@{real}> ") and not mm.lookups


def test_dm_username_resolved_and_cached(tmp_path):
    # 值形态第二代：username 经 users/username/{name} 解析后发；解析成功进内存缓存（一次 lookup 多次复用）
    mm = notify.FakeMMClient(user_ids={"roc": _MM_ID})
    n = notify.Notifier(_cfg(tmp_path), mm=mm)
    n.notify_run(**_notify(enqueued_by="roc"))
    n.notify_run(**_notify(enqueued_by="roc"))
    assert mm.lookups == ["roc"]  # 第二次命中缓存不再查
    assert len(mm.dms) == 2 and all(r == _MM_ID for r, _ in mm.dms)


def test_dm_unresolvable_username_visible_drop(tmp_path, capsys):
    # 解析失败：可见的丢（stderr warn 留痕）非静默吞；异常不外抛
    mm = notify.FakeMMClient(fail_lookup={"ghost"})
    n = notify.Notifier(_cfg(tmp_path), mm=mm)
    n.notify_run(**_notify(enqueued_by="ghost"))
    assert mm.dms == [] and "ghost" in capsys.readouterr().err


def test_vault_deliver_literal_and_none_fall_back_to_cfg_user(tmp_path):
    # 值形态第三代：机机字面量 / None → 回落 cfg.user_id 走 username 路径；
    # 两代回落同目标 → 第二次命中解析缓存（lookup 只一次）
    mm = notify.FakeMMClient(user_ids={"roc": _MM_ID})
    n = notify.Notifier(_cfg(tmp_path), mm=mm)
    n.notify_run(**_notify(enqueued_by=notify.VAULT_DELIVER_LITERAL))
    n.notify_run(**_notify(enqueued_by=None))
    assert mm.lookups == ["roc"] and len(mm.dms) == 2
    assert all(r == _MM_ID for r, _ in mm.dms)


def test_vault_deliver_literal_matches_api_constant():
    # 锁等防漂移：notify 侧字面量与 api._DELIVER_ENQUEUED_BY 必须同值（两处定义的代价）
    assert notify.VAULT_DELIVER_LITERAL == api._DELIVER_ENQUEUED_BY


def test_notify_crash_fail_open_no_raise(tmp_path, capsys):
    # fail-open：MM 推递崩溃 → notify_run 不抛（stderr warn），调用方流水线零扰动
    mm = notify.FakeMMClient(crash_on_send=RuntimeError("mm down"))
    n = notify.Notifier(_cfg(tmp_path), mm=mm)
    n.notify_run(**_notify(enqueued_by=_MM_ID))  # 不抛即过
    assert "私信推递异常" in capsys.readouterr().err


# ── 6.x 归属路由（add-mm-channel-intake：绑定表真相，env 映射废止）────────


def test_bound_channel_all_statuses_with_mention_no_dm(tmp_path):
    """绑定 → 全终态进频道（成败皆进——频道=项目工作区）+ <@提及>；不重复私信。"""
    mm = notify.FakeMMClient(user_ids={"roc": _MM_ID})
    n = notify.Notifier(_cfg(tmp_path), mm=mm, database=_db_bound(tmp_path, ("proj-a", "chan-1")))
    n.notify_run(**_notify(enqueued_by="roc"))  # succeeded 也进（旧「仅失败」口径废止）
    n.notify_run(**_notify(status="failed", exit_code=1, enqueued_by=_MM_ID))
    assert len(mm.channels) == 2 and mm.dms == []  # 频道全覆盖，零私信
    assert mm.channels[0][0] == "chan-1" and mm.channels[1][0] == "chan-1"
    assert mm.channels[0][1].startswith(f"<@{_MM_ID}> ")  # username 经解析提及（roc → mm id）
    assert "run #7" in mm.channels[0][1] and "succeeded" in mm.channels[0][1]
    assert mm.channels[1][1].startswith(f"<@{_MM_ID}> ")  # 26-hex 直拼提及


def test_unbound_and_env_mapping_dead_still_dm(tmp_path):
    """未绑定 → DM 三代分流不变；env project_channels 读点已死（配了也只认绑定表）。"""
    mm = notify.FakeMMClient(user_ids={"roc": _MM_ID})
    n = notify.Notifier(_cfg(tmp_path, project_channels={"proj-a": "chan-1"}), mm=mm,
                        database=_db_bound(tmp_path))  # env 配 chan-1 但绑定表空
    n.notify_run(**_notify(status="failed", enqueued_by="roc"))
    assert mm.channels == [] and len(mm.dms) == 1  # env 映射无效：私信照旧


def test_channel_crash_fail_open_no_dm_fallback(tmp_path, capsys):
    """绑定频道 post 崩 → fail-open warn 留痕，不回退私信（双通道同事件是噪音源）。"""
    mm = notify.FakeMMClient(user_ids={"roc": _MM_ID}, crash_on_channel=RuntimeError("chan down"))
    n = notify.Notifier(_cfg(tmp_path), mm=mm, database=_db_bound(tmp_path, ("proj-a", "chan-1")))
    n.notify_run(**_notify(status="failed", enqueued_by="roc"))
    assert mm.dms == []  # 绝不补发 DM
    err = capsys.readouterr().err
    assert "频道推递异常" in err and "不回退私信" in err  # warn 留痕可见


def test_mention_resolution_failure_degrades_plain_text(tmp_path, capsys):
    """提及目标解析失败 → 降级无提及纯文本，消息照发（提及是增强非送达前提）。"""
    mm = notify.FakeMMClient(fail_lookup={"ghost"})
    n = notify.Notifier(_cfg(tmp_path), mm=mm, database=_db_bound(tmp_path, ("proj-a", "chan-1")))
    n.notify_run(**_notify(enqueued_by="ghost"))
    assert len(mm.channels) == 1 and not mm.channels[0][1].startswith("<@")
    assert "run #7" in mm.channels[0][1] and mm.dms == []


def test_database_none_means_dm_fallback(tmp_path):
    """无 database（旧装配形态/部分单测）= 无归属判据 → 恒私信回落，零频道路由。"""
    mm = notify.FakeMMClient(user_ids={"roc": _MM_ID})
    n = notify.Notifier(_cfg(tmp_path, project_channels={"proj-a": "chan-1"}), mm=mm, database=None)
    n.notify_run(**_notify(status="failed", enqueued_by="roc"))
    assert mm.channels == [] and len(mm.dms) == 1


def test_notify_event_bound_all_events_channel_no_dm(tmp_path):
    """语义事件：绑定 → 四类全量进频道（per-event 判定废止——pass/stored 也进）。"""
    mm = notify.FakeMMClient(user_ids={"roc": _MM_ID})
    n = notify.Notifier(_cfg(tmp_path), mm=mm, database=_db_bound(tmp_path, ("proj-a", "chan-1")))
    n.notify_event(text="[pa-hub] critic 判定 → pass", enqueued_by="roc", project="proj-a")
    n.notify_event(text="[pa-hub] PR 开出", enqueued_by="roc", project="proj-a")
    assert len(mm.channels) == 2 and mm.dms == []
    assert mm.channels[0][1] == f"<@{_MM_ID}> [pa-hub] critic 判定 → pass"
    # 未绑定 project 的事件 → 私信
    n.notify_event(text="[pa-hub] x", enqueued_by="roc", project="proj-b")
    assert len(mm.dms) == 1 and len(mm.channels) == 2


def test_notify_abandon_routes_by_binding(tmp_path):
    """无 run 放弃告警归属路由：绑定 → 频道纯文本（无提及）；未绑定 → admin DM。"""
    mm = notify.FakeMMClient(user_ids={"roc": _MM_ID})
    n = notify.Notifier(_cfg(tmp_path), mm=mm, database=_db_bound(tmp_path, ("proj-a", "chan-1")))
    n.notify_abandon("proj-a", "[pa-hub] 队列项 #9（proj-a）放弃")
    assert mm.channels == [("chan-1", "[pa-hub] 队列项 #9（proj-a）放弃")] and mm.dms == []
    n.notify_abandon("proj-b", "[pa-hub] 队列项 #10（proj-b）放弃")
    assert len(mm.dms) == 1 and "proj-b" in mm.dms[0][1]  # 未绑定 → cfg.user_id DM


def test_message_contains_identifiers_not_routing_initiator(tmp_path):
    # initiator 进文本（人读）不参与路由；消息含 project / run 标识 / 终态 / 失败原因
    mm = notify.FakeMMClient()
    n = notify.Notifier(_cfg(tmp_path), mm=mm)
    n.notify_run(**_notify(status="failed", exit_code=2, enqueued_by=_MM_ID,
                           initiator="zhang3", last_error="起容器异常：docker down"))
    text = mm.dms[0][1]
    assert "proj-a" in text and "#7" in text and "20261021_my-feature" in text
    assert "failed" in text and "exit 2" in text and "zhang3" in text and "docker down" in text


# ── add-mm-channel-intake 3.1：channel_member_ids 契约（Http 生产面 + Fake 面对齐）──

def _http_mm(tmp_path: Path, handler) -> notify.HttpMMClient:
    """MockTransport 注入的生产客户端——测试只锁三态契约不锁 API 序列（D7）。"""
    return notify.HttpMMClient(_cfg(tmp_path), client=httpx.Client(transport=httpx.MockTransport(handler)))


def _page(ids: list[str], page: int, per_page: int = 200) -> list[dict]:
    return [{"user_id": u} for u in ids[page * per_page:(page + 1) * per_page]]


def test_http_channel_member_ids_paginates_to_completion(tmp_path):
    ids = [f"{i:026x}" for i in range(450)]  # 450 成员 → per_page=200 需 3 页
    seen_pages: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import urllib.parse
        q = urllib.parse.parse_qs(request.url.query.decode())
        seen_pages.append(int(q["page"][0]))
        assert q["per_page"][0] == "200"
        return httpx.Response(200, json=_page(ids, seen_pages[-1]))

    out = _http_mm(tmp_path, handler).channel_member_ids("c" * 26)
    assert out == ids  # 翻页取全
    assert seen_pages == [0, 1, 2]  # 末页短页（50<200）即停——不多拉空页


def test_http_channel_member_ids_404_raises_not_found(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)
    try:
        _http_mm(tmp_path, handler).channel_member_ids("c" * 26); raised = False
    except notify.MMChannelNotFound:
        raised = True
    assert raised


def test_http_channel_member_ids_403_and_5xx_are_unknown_none(tmp_path):
    codes = iter([403, 500])
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(next(codes))
    mm = _http_mm(tmp_path, handler)
    assert mm.channel_member_ids("c" * 26) is None  # 403 = 判据不可用（非授权结论）
    assert mm.channel_member_ids("c" * 26) is None  # 5xx 同


def test_http_channel_member_ids_network_error_is_none(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")
    assert _http_mm(tmp_path, handler).channel_member_ids("c" * 26) is None


def test_fake_channel_member_ids_programmable_faces():
    # Fake 与 Http 同判：FOUND 列表 / forbidden→None / not_found→异常 / crash→异常
    mm = notify.FakeMMClient(channel_members={"c" * 26: [_MM_ID]})
    assert mm.channel_member_ids("c" * 26) == [_MM_ID]
    assert mm.channel_member_ids("z" * 26) == []  # 未知频道 = 空集（200 空页语义）
    mm2 = notify.FakeMMClient(members_forbidden={"c" * 26})
    assert mm2.channel_member_ids("c" * 26) is None
    mm3 = notify.FakeMMClient(members_not_found={"c" * 26})
    try:
        mm3.channel_member_ids("c" * 26); raised = False
    except notify.MMChannelNotFound:
        raised = True
    assert raised


# ── add-pipeline-outcome-observability 2.1：通知消费 outcome（lead 业务摘要）──
# #85 标本驱动：verify verdict=red、PR #60 留 review，MM 只见「succeeded, exit 0」。
def _outcome_red_pr60():
    return {"schema": "pa.pipeline_outcome.v1",
            "summary": {"green": 0, "red": 1, "invalid": 0, "blocked": 0, "abnormal": 0},
            "prs": [{"project": "cc-web-control", "status": "pr_open", "pr_number": 60,
                     "pr_url": "https://github.com/o/r/pull/60", "verify_verdict": "red",
                     "reason_headline": "playwright e2e 3 条 declared 全匹配但 exit 1"}],
            "container": {"exit_code": 0}}


def test_load_pipeline_outcome(tmp_path):
    sr = tmp_path / "state"; sr.mkdir()
    (sr / "pipeline_outcome_20261029m1.json").write_text('{"summary": {"red": 1}}', encoding="utf-8")
    assert notify.load_pipeline_outcome(sr, "20261029m1_cc-web-control") == {"summary": {"red": 1}}
    assert notify.load_pipeline_outcome(sr, "20261028m2_old") is None        # 缺文件
    (sr / "pipeline_outcome_bad.json").write_text("{broken", encoding="utf-8")
    assert notify.load_pipeline_outcome(sr, "bad_x") is None                 # 坏 json
    (sr / "pipeline_outcome_arr.json").write_text("[1,2]", encoding="utf-8")
    assert notify.load_pipeline_outcome(sr, "arr_x") is None                 # 非 dict


def test_message_with_outcome_lead_business_summary(tmp_path):
    n = notify.Notifier(_cfg(tmp_path), mm=notify.FakeMMClient())
    msg = n._message(run_id=85, project="cc-web-control", run_dir_name="20261029m1_x",
                     status="succeeded", exit_code=0, initiator=None, last_error=None,
                     outcome=_outcome_red_pr60())
    assert "业务终态：🟢0 🔴1 · invalid 0 · blocked 0 · 异常 0" in msg           # lead 计数
    assert "🔴 PR #60：playwright e2e" in msg                                  # PR 行 + 首因
    assert "（run → succeeded，容器 exit 0）" in msg                            # 退出码降附录
    assert "→ succeeded，exit 0" not in msg.split("\n")[0]                     # 旧 lead 形态不再


def test_message_without_outcome_fallback_unchanged(tmp_path):
    n = notify.Notifier(_cfg(tmp_path), mm=notify.FakeMMClient())
    msg = n._message(run_id=7, project="p", run_dir_name="d", status="succeeded",
                     exit_code=0, initiator=None, last_error=None, outcome=None)
    assert msg == "[pa-hub] p run #7（d）→ succeeded，exit 0"                  # 回退回归锁


def test_notify_run_outcome_reaches_channel_and_dm(tmp_path):
    o = _outcome_red_pr60()
    mm = notify.FakeMMClient()
    notify.Notifier(_cfg(tmp_path), mm=mm, database=_db_bound(tmp_path, ("cc-web-control", "c" * 26))
                    ).notify_run(run_id=85, project="cc-web-control", run_dir_name="20261029m1_x",
                                 status="succeeded", exit_code=0, outcome=o)
    assert "业务终态" in mm.channels[0][1]                                     # 绑定频道
    mm2 = notify.FakeMMClient()
    notify.Notifier(_cfg(tmp_path), mm=mm2).notify_run(
        run_id=85, project="cc-web-control", run_dir_name="20261029m1_x",
        status="succeeded", exit_code=0, enqueued_by=_MM_ID, outcome=o)
    assert "🔴 PR #60" in mm2.dms[0][1]                                        # 私信同文


def test_pr_line_variants(tmp_path):
    line = notify.Notifier._pr_line({"pr_number": 61, "verify_verdict": "green",
                                     "reason_headline": "全绿开 PR", "status": "pr_open"})
    assert line == "🟢 PR #61：全绿开 PR"
    no_pr = notify.Notifier._pr_line({"status": "blocked_external_state",
                                      "verify_verdict": None, "reason_headline": "远端分支已在"})
    assert no_pr == "⚪ blocked_external_state：远端分支已在"                    # 无 PR 号 → status
    no_head = notify.Notifier._pr_line({"pr_number": 9, "verify_verdict": "invalid"})
    assert no_head == "⚪ PR #9"                                               # 无 headline 不拖冒号
