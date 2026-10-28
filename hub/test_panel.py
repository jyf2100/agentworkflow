"""panel 面板单测（add-hub-slice-4b 任务 1.5，spec: hub-panel）。

覆盖：提交闭环（落盘+入队+发起人身份）、evidence 机械必填 422、GET 未登录 302 /
POST 未登录 401、auth 未配置 503、只读页事实渲染、XSS 转义、project 清单必读面
扫描、run 详情 journal 尾部 + 报告渲染（T2a）。全离线（FakeRunner + 无 MM 出站）。
"""
from datetime import datetime, timezone
from pathlib import Path

import auth
import config
import db as hub_db
import panel
import queue_engine as qe
from fastapi.testclient import TestClient
from runner import FakeRunner

import api
import lifecycle as lc_mod

_CLOCK = datetime(2026, 10, 21, 3, 0, 0, tzinfo=timezone.utc)
_AUTH_ENV = {  # 四项齐 = auth 已配置态（面板以会话为前提）
    "HUB_MM_BASE_URL": "https://mm.example.com",
    "HUB_MM_CLIENT_ID": "cid",
    "HUB_MM_CLIENT_SECRET": "csec",
    "HUB_SESSION_SECRET": "s" * 32,
}


def _mk(tmp_path: Path, extra_env: dict | None = None):
    env = {
        "HUB_USER_ID": "roc",
        # 长值：_credentials 派发前占位符判据（vault_store.placeholder_issues，2026-10-23）
        "HUB_USER_ANTHROPIC_BASE_URL": "https://litellm.example",
        "HUB_USER_ANTHROPIC_AUTH_TOKEN": "tok-abcdef0123456789abcdef",
        "HUB_USER_GH_TOKEN": "ghp_abcdefghij0123456789",
        "HUB_IMAGE_TAG_DEFAULT": "pa-executor:v0.1.3",
        "HUB_DATA_ROOT": str(tmp_path / "data"),
    }
    env.update(extra_env or {})
    d = hub_db.HubDB(tmp_path / "h.sqlite3")
    eng = qe.QueueEngine(d, max_containers=2, max_retries=2, image_tag_default="pa-executor:v0.1.3")
    cfg = config.load_config(env)
    lc = lc_mod.Lifecycle(d, eng, FakeRunner(images={"pa-executor:v0.1.3"}), cfg, clock=_CLOCK)
    _mk_project(tmp_path / "data", "proj-a")  # 启动必读面齐备的 project
    return TestClient(api.create_app(d, eng, lc)), d, eng, lc


def _mk_project(root: Path, name: str, *, complete: bool = True):
    """造 project 必读面：complete=True = profiles 目录 + sources.yaml（派发同判据）。"""
    (root / name / "profiles").mkdir(parents=True, exist_ok=True)
    if complete:
        (root / name / "sources.yaml").write_text("sources: []\n", encoding="utf-8")


def _login(client: TestClient, lc, mm_user_id="abc123defghijklmnopqrstuv", username="alice"):
    tok = auth.sign_token({"mm_user_id": mm_user_id, "username": username}, lc.cfg.session_secret, ttl_s=60)
    client.cookies.set(auth.SESSION_COOKIE, tok)
    return mm_user_id


# ── 提交闭环（spec scenario「会话提交闭环」）──────────────────────────


def test_panel_submit_full_loop(tmp_path):
    client, d, eng, lc = _mk(tmp_path, _AUTH_ENV)
    uid = _login(client, lc)
    resp = client.post("/panel/prd", data={
        "project": "proj-a", "prd_content": "# 手写 PRD\n## 验收\n- [ ] x",
        "evidence": "https://example.com/issue/1",
    })
    assert resp.status_code == 201 and "已入队" in resp.text and "队列项 #1" in resp.text
    item = d.list_items()[0]
    assert item["enqueued_by"] == uid  # 会话身份（直发路径），非 cfg 兜底
    assert item["initiator"] == "alice" and item["status"] == "pending"
    prd_files = list((tmp_path / "data" / "proj-a" / "prd").glob("*_hand-*.md"))
    assert len(prd_files) == 1 and "手写 PRD" in prd_files[0].read_text(encoding="utf-8")


def test_panel_submit_empty_evidence_rejected_422(tmp_path):
    client, d, eng, lc = _mk(tmp_path, _AUTH_ENV)
    _login(client, lc)
    for ev in ("", "   "):  # 空与纯空白（strip 判据）
        resp = client.post("/panel/prd", data={
            "project": "proj-a", "prd_content": "# x", "evidence": ev,
        })
        assert resp.status_code == 422 and "evidence 必填" in resp.text
    assert d.list_items() == []  # 拒绝即不入队、不落盘
    assert not (tmp_path / "data" / "proj-a" / "prd").exists()


def test_panel_submit_rejects_unknown_project(tmp_path):
    client, d, eng, lc = _mk(tmp_path, _AUTH_ENV)
    _login(client, lc)
    _mk_project(tmp_path / "data", "proj-b", complete=False)  # 缺 sources.yaml = 非候选
    for proj in ("proj-b", "no-such", "<script>x</script>"):
        resp = client.post("/panel/prd", data={
            "project": proj, "prd_content": "# x", "evidence": "https://e",
        })
        assert resp.status_code == 422
    assert d.list_items() == []


# ── 守卫（spec scenario「未登录不可提交」）────────────────────────────


def test_panel_get_redirects_to_authorize_when_not_logged_in(tmp_path):
    client, *_ = _mk(tmp_path, _AUTH_ENV)
    for path in ("/panel", "/panel/queue", "/panel/runs", "/panel/runs/1"):
        resp = client.get(path, follow_redirects=False)
        assert resp.status_code == 302 and resp.headers["location"] == "/auth/authorize"


def test_panel_post_401_when_not_logged_in(tmp_path):
    client, d, *_ = _mk(tmp_path, _AUTH_ENV)
    resp = client.post("/panel/prd", data={"project": "proj-a", "prd_content": "# x", "evidence": "https://e"})
    assert resp.status_code == 401
    assert d.list_items() == []


def test_panel_503_when_auth_unconfigured(tmp_path):
    """auth 未配置态面板整群 503（面板以会话为前提；REST 面不受影响的对照见 test_api）。"""
    client, d, *_ = _mk(tmp_path)  # 无 _AUTH_ENV = 2a 回退态
    assert client.get("/panel").status_code == 503
    assert client.get("/panel/queue").status_code == 503
    resp = client.post("/panel/prd", data={"project": "proj-a", "prd_content": "# x", "evidence": "https://e"})
    assert resp.status_code == 503 and d.list_items() == []


# ── project 清单扫描（spec scenario「project 清单来自必读面扫描」）────


def test_panel_project_list_scans_prerequisites(tmp_path):
    client, d, eng, lc = _mk(tmp_path, _AUTH_ENV)
    _login(client, lc)
    _mk_project(tmp_path / "data", "proj-c")           # 齐备 → 候选
    _mk_project(tmp_path / "data", "proj-d", complete=False)  # 缺 sources.yaml → 非候选
    (tmp_path / "data" / "loose-files").mkdir(parents=True)   # 无必读面杂目录 → 非候选
    html = client.get("/panel").text
    assert 'value="proj-a"' in html and 'value="proj-c"' in html
    assert 'value="proj-d"' not in html and "loose-files" not in html


# ── 只读面（spec「面板只读面——零语义渲染」）─────────────────────────


def test_panel_read_pages_render_facts(tmp_path):
    client, d, eng, lc = _mk(tmp_path, _AUTH_ENV)
    _login(client, lc)
    prd = tmp_path / "p.md"; prd.write_text("# x", encoding="utf-8")
    qid = eng.enqueue("proj-a", str(prd), "roc")
    lc.tick_dispatch()
    rid = d.runs_for_item(qid)[0]["id"]

    q_html = client.get("/panel/queue").text
    assert "proj-a" in q_html and "running" in q_html
    runs_html = client.get("/panel/runs").text
    assert f"/panel/runs/{rid}" in runs_html and "proj-a" in runs_html
    assert client.get("/panel/runs/999").status_code == 404


def test_panel_run_detail_journal_tail_and_report(tmp_path):
    client, d, eng, lc = _mk(tmp_path, _AUTH_ENV)
    _login(client, lc)
    prd = tmp_path / "p.md"; prd.write_text("# x", encoding="utf-8")
    qid = eng.enqueue("proj-a", str(prd), "roc")
    lc.tick_dispatch()
    run = d.runs_for_item(qid)[0]
    rid, run_dir = run["id"], Path(run["run_dir"])

    # journal 平铺伴生（lifecycle 同拼法）+ 报告落卷（T2a：state/reports/ 按 stamp 对齐）
    jf = run_dir.parent / f"{run_dir.name}.journal.jsonl"
    jf.write_text('{"event":"stage_done","stage":"fetch"}\n{"event":"run_finished"}\n', encoding="utf-8")
    stamp = run_dir.name.split("_", 1)[0]
    reports = tmp_path / "data" / "proj-a" / "state" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / f"项目推进报告_{stamp}.md").write_text("# 今日推进总结\n内容……", encoding="utf-8")

    html = client.get(f"/panel/runs/{rid}").text
    assert "stage_done" in html and "run_finished" in html  # journal 尾部原样
    assert "今日推进总结" in html  # 报告原文渲染


def test_panel_run_detail_no_report_is_not_error(tmp_path):
    client, d, eng, lc = _mk(tmp_path, _AUTH_ENV)
    _login(client, lc)
    prd = tmp_path / "p.md"; prd.write_text("# x", encoding="utf-8")
    qid = eng.enqueue("proj-a", str(prd), "roc")
    lc.tick_dispatch()
    rid = d.runs_for_item(qid)[0]["id"]
    html = client.get(f"/panel/runs/{rid}").text
    assert "无报告" in html and "无 journal" in html  # 缺失显式降级（非错误态）


# ── XSS（render 层 _esc 唯一转义入口）───────────────────────────────


def test_panel_escapes_dynamic_values():
    html = panel.render_submit(["<b>p</b>"], error="<script>alert(1)</script>", current_user="<i>u</i>")
    for raw in ("<script>", "<b>p</b>", "<i>u</i>"):
        assert raw not in html
    assert "&lt;script&gt;" in html and "&lt;b&gt;p&lt;/b&gt;" in html


def test_panel_submit_error_echo_escaped(tmp_path):
    """端到端：非法 project 名（含标签）422 回显须转义——可注入面只有 error 回显。"""
    client, d, eng, lc = _mk(tmp_path, _AUTH_ENV)
    _login(client, lc)
    resp = client.post("/panel/prd", data={
        "project": "<script>alert(1)</script>", "prd_content": "# x", "evidence": "https://e",
    })
    assert resp.status_code == 422
    assert "<script>" not in resp.text and "&lt;script&gt;" in resp.text


# ── fix-hub-panel-ux：表单结构（label/hint/联动）+ 状态符号 + CSS 底线 ──


def test_form_labels_paired_with_input_ids():
    """label for-id 配对（UX 底线）：三表单每个 for= 都指向同 id 控件。"""
    import re

    for html in (panel.render_submit(["p"]), panel.render_project_new(), panel.render_credentials()):
        for_ = re.findall(r'<label for="([^"]+)"', html)
        assert for_, "零 label = 结构缺失"
        for f in for_:
            assert f'id="{f}"' in html


def test_project_form_forge_toggle_and_hints():
    """建项目表单：hint 存在 + forge→gitlab_host 联动（包裹 id + change 监听 + 初始 sync）。"""
    html = panel.render_project_new()
    assert 'class="hint"' in html
    assert 'id="f-forge"' in html and 'id="f-gitlab-host-box"' in html
    assert "addEventListener('change'" in html and "sync()" in html


def test_submit_empty_project_list_links_to_new_project():
    """空 project 清单：提示链接到建项目页（收尾引导——不再引导 ssh 建必读面）。"""
    html = panel.render_submit([])
    assert 'href="/panel/projects/new"' in html
    assert "先在服务器" not in html  # 旧 ssh 引导文案已移除（必读面字样仍见于扫描说明 hint，属合法）


def test_project_new_success_hint_by_forge():
    """成功消息按 forge 形态引导下一步（收尾引导）：gitlab/gitcode→凭据页、缺省→提交页。"""
    lab = panel.render_project_new(success="已建档：x", next_forge="gitlab")
    assert 'href="/panel/credentials"' in lab and "GITLAB_TOKEN" in lab
    assert "GITCODE_TOKEN" in panel.render_project_new(success="已建档：x", next_forge="gitcode")
    assert 'href="/panel"' in panel.render_project_new(success="已建档：x", next_forge="")


def test_repaint_values_escaped():
    """回填值经 _esc：XSS 防线唯一性不因回填新增入口（载荷精确断言，避开合法联动 script 块）。"""
    html = panel.render_project_new(values={"name": "<script>alert(1)</script>"})
    assert "<script>alert(1)" not in html
    assert "&lt;script&gt;" in html


def test_status_symbols_in_queue_and_runs():
    """状态符号双通道（D4）：✓/✗/… 与色 class 并行；last_error 截断 + title 悬停全文。"""
    rows = [
        {"id": 1, "project": "p", "status": "done", "attempt": 1, "initiator": "a", "last_error": None,
         "created_at": "2026-10-27T04:14:08+00:00", "updated_at": "2026-10-27T04:20:31+00:00"},
        {"id": 2, "project": "p", "status": "failed", "attempt": 2, "initiator": "a", "last_error": "boom",
         "created_at": "2026-10-27T03:14:08+00:00", "updated_at": "2026-10-27T03:40:00+00:00"},
        {"id": 3, "project": "p", "status": "running", "attempt": 1, "initiator": "a", "last_error": None,
         "created_at": "2026-10-27T05:00:00+00:00", "updated_at": "2026-10-27T05:01:00+00:00"},
    ]
    html = panel.render_queue(rows)
    assert '<span class="sym">✓</span>done' in html
    assert '<span class="sym">✗</span>failed' in html
    assert '<span class="sym">…</span>running' in html
    assert 'class="err" title="boom"' in html  # D5：截断列 + title 悬停全文
    # fix-hub-queue-recency：入队/更新时间列（截短显示 + title 全文）+ 操作列紧邻 status
    assert "<th>入队</th><th>更新</th>" in html
    assert '<th>status</th><th>操作</th>' in html  # 按钮不被时间列挤出可视区
    assert 'title="2026-10-27T04:14:08+00:00"' in html  # ISO 全文进 title
    assert ">10-27 04:14<" in html and ">10-27 04:20<" in html  # 显示为截短形
    runs = [{"id": 1, "project": "p", "status": "succeeded", "exit_code": 0,
             "started_at": "t", "ended_at": "t"}]
    assert '<span class="sym">✓</span>succeeded' in panel.render_runs(runs)


def test_css_baseline_present():
    """CSS 底线静态锚：focus 可见态 / hover / 长内容截断与滚动 / 表格容器均入 _STYLE。"""
    for pat in (":focus", "button:hover", "text-overflow:ellipsis", "pre.maxh", ".tablewrap"):
        assert pat in panel._STYLE


# ── fix-event-outlet-verify-parity：events 尾部 + verify-report run 详情扩容 ──


def _dispatched_run(tmp_path, client, d, eng, lc):
    """派发一个 run → 返回 (rid, runs_dir, stamp, prd_stem)——事件/verify-report 寻址三要素。

    PRD stem 含下划线：hub run 目录名经 _slugify 下划线→横线（错位），pa 侧文件名
    stem 原样——用它锁死面板寻址从 prd_path stem 拼（若实现回归成从 run 目录名拼，
    本 fixture 下事件/verify-report 全找不到 → 下方断言必红）。"""
    prd = tmp_path / "20261020_hand-a1b2c3d4.md"
    prd.write_text("# x", encoding="utf-8")
    qid = eng.enqueue("proj-a", str(prd), "roc")
    lc.tick_dispatch()
    run = d.runs_for_item(qid)[0]
    run_dir = Path(run["run_dir"])
    return run["id"], run_dir.parent, run_dir.name.split("_", 1)[0], Path(prd).stem


def test_panel_run_detail_events_tail_and_verify_report(tmp_path):
    """events 尾部行原样（acceptance_suite / invalid reason 可见）+ verify-report 关键节渲染。"""
    import json as _json
    client, d, eng, lc = _mk(tmp_path, _AUTH_ENV)
    _login(client, lc)
    rid, runs_dir, stamp, stem = _dispatched_run(tmp_path, client, d, eng, lc)

    (runs_dir / f"{stamp}_{stem}.events.jsonl").write_text(
        '{"v":1,"event":"verify_verdict","verdict":"invalid","reason":"wallclock_timeout"}\n'
        '{"v":1,"event":"acceptance_suite","status":"absent","reason":"no_harness"}\n',
        encoding="utf-8")
    vr = {
        "schema_version": "pa.verify-report.v1",
        "run": {"run_id": stamp, "prd_id": stem, "harness": "pytest"},
        "commands": {"source": "profile", "gate_cmd": None,
                     "results": [{"declared": "npm test", "unresolved": False,
                                  "matched_command": "cd wt && npm test", "exit_code": 1}]},
        "anchors": {"workspace_clean": {"ok": True, "detail": "ok"},
                    "injected_digests": {"ok": False, "violations": ["digest-mismatch:t.py"]}},
        "verdict": "invalid", "reason": "unresolved（declared 未被执行/变体不可判读）",
        "invalid_reason": "unresolved",
        "evidence": {"stream_path": "/x/y.verify-stream.jsonl"},
        "env_parity": {"declared": "", "observed_exports": {"FOO": ["1"]}, "note": "v1 记录不阻断"},
    }
    (runs_dir / f"{stamp}_{stem}.verify-report.json").write_text(
        _json.dumps(vr, ensure_ascii=False), encoding="utf-8")

    html = client.get(f"/panel/runs/{rid}").text
    assert "events 尾部" in html                                   # 新展示区标题
    assert "acceptance_suite" in html and "no_harness" in html      # 事件行原样可见
    assert "wallclock_timeout" in html                              # invalid reason 原样可见
    # verify-report 机械表格：判定节（verdict + invalid_reason + 人读 reason）+ 逐命令 + 锚
    assert "verify-report" in html
    assert "unresolved（declared 未被执行/变体不可判读）" in html    # 人读 reason 原样
    assert "invalid_reason" in html and "npm test" in html
    assert "cd wt &amp;&amp; npm test" in html                      # 命令经 _esc
    assert "digest-mismatch:t.py" in html                           # 锚 violation 原样
    assert "v1 记录不阻断" in html                                  # 环境奇偶 note 原样


def test_panel_run_detail_missing_events_and_verify_report_placeholder(tmp_path):
    """缺失显式占位非错误态（与「无 journal」「无报告」同模式）。"""
    client, d, eng, lc = _mk(tmp_path, _AUTH_ENV)
    _login(client, lc)
    rid, *_ = _dispatched_run(tmp_path, client, d, eng, lc)
    html = client.get(f"/panel/runs/{rid}").text
    assert "无 verify-report" in html and "（无 events）" in html


def test_panel_verify_report_render_zero_semantics(tmp_path):
    """零语义渲染铁律：verify-report 节只透传字段，无 hub 侧结论性文本（判词只来自文件）。"""
    html = panel.render_run_detail(
        {"id": 1, "project": "p", "status": "succeeded", "exit_code": 0,
         "run_dir": "/x", "started_at": "t", "ended_at": "t"},
        [], None, events_tail=[], verify_report={"verdict": "green", "reason": "全净"})
    assert "verify-report" in html and "全净" in html                 # 文件字段原样
    for hub_verdict_word in ("hub 判定", "结论：", "不合格", "建议"):  # hub 不产结论
        assert hub_verdict_word not in html


def test_panel_verify_report_malformed_inner_shapes_degrade_not_500(tmp_path):
    """坏内层结构降级非 500：evidence 非 dict / violations 非 str / exit_code 可注字符串——
    全经 _esc 原样展示，run 详情页不炸（该文件在 state 卷上、目标仓代码可写）。"""
    detail = {"id": 1, "project": "p", "status": "succeeded", "exit_code": 0,
              "run_dir": "/x", "started_at": "t", "ended_at": "t"}
    html = panel.render_run_detail(
        detail, [], None, events_tail=[],
        verify_report={"verdict": "red", "evidence": "not-a-dict",          # evidence 非 dict
                       "anchors": {"injected_digests":
                                   {"violations": [123, None]}},            # 非 str 元素
                       "commands": {"results": [{"declared": "x",
                                                 "exit_code": "<img src=x onerror=alert(1)>"}]}})
    assert "<img src=x" not in html and "&lt;img src=x" in html      # 注入面全经 _esc
    html2 = panel.render_run_detail(detail, [], None, events_tail=[], verify_report="junk-shape")
    assert "verify-report" in html2                                  # 顶层怪形也不打废整页（降级原样）
