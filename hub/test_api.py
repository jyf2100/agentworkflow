"""api.py 单测——最小 API 面（add-hub-skeleton 任务 8.1，spec: execution-hub「最小 API 面」）。

仅三端点（POST /queue、GET /queue、GET /runs/{id}），无语义判定面（审 PRD/判价值
归 pa persona；hub 只做机械活）。cli.py 走本模块同一内部函数（8.2）。
"""
from datetime import datetime, timezone
from pathlib import Path

import pytest

import auth
import config
import db as hub_db
import queue_engine as qe
from fastapi.testclient import TestClient
from runner import FakeRunner

import api
import lifecycle as lc_mod
import mm_intake

_CLOCK = datetime(2026, 10, 21, 3, 0, 0, tzinfo=timezone.utc)


def _mk(tmp_path: Path, extra_env: dict | None = None, mm_client=None):
    env = {
        "HUB_USER_ID": "roc",
        # 长值：_credentials 派发前占位符判据（vault_store.placeholder_issues，2026-10-23）
        "HUB_USER_ANTHROPIC_BASE_URL": "https://litellm.example",
        "HUB_USER_ANTHROPIC_AUTH_TOKEN": "tok-abcdef0123456789abcdef",
        "HUB_USER_GH_TOKEN": "ghp_abcdefghij0123456789",
        "HUB_IMAGE_TAG_DEFAULT": "pa-executor:v0.1.3",
        "HUB_DATA_ROOT": str(tmp_path / "data"),
    }
    if extra_env:
        env.update(extra_env)
    tmp_path.mkdir(parents=True, exist_ok=True)  # mm 系列测试传未预建子目录（tmp_path/"mm" 等）
    d = hub_db.HubDB(tmp_path / "h.sqlite3")
    eng = qe.QueueEngine(d, max_containers=2, max_retries=2, image_tag_default="pa-executor:v0.1.3")
    cfg = config.load_config(env)
    lc = lc_mod.Lifecycle(d, eng, FakeRunner(images={"pa-executor:v0.1.3"}), cfg, clock=_CLOCK)
    # 启动必读面（镜像咬合）：容器内 pa 无条件读 sources.yaml + profiles
    profs = tmp_path / "data" / "proj-a" / "profiles"
    profs.mkdir(parents=True, exist_ok=True)
    (profs / "proj-a.yaml").write_text("name: proj-a\nadmission: true\n", encoding="utf-8")
    (tmp_path / "data" / "proj-a" / "sources.yaml").write_text("sources: []\n", encoding="utf-8")
    return TestClient(api.create_app(d, eng, lc, mm_client=mm_client)), d, eng, lc


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


# ── add-hub-slice-2a 4.1：lifespan 挂 daemon 线程（SIGTERM 路由链）──────

class _TickLC:
    """fake lifecycle：只供 daemon 循环驱动（不触端点，无需 cfg）。"""

    def __init__(self):
        self.ticks = 0
        self.startups = 0

    def reconcile_startup(self):
        self.startups += 1

    def tick_dispatch(self):
        self.ticks += 1
        return False

    def reconcile_running(self):
        pass


def test_lifespan_starts_and_stops_daemon(tmp_path):
    import time

    import api

    d = hub_db.HubDB(tmp_path / "h.sqlite3")
    eng = qe.QueueEngine(d, max_containers=1, max_retries=1, image_tag_default="t:v1")
    fake = _TickLC()
    app = api.create_app(d, eng, fake, tick_interval=0.01)
    with TestClient(app):
        deadline = time.time() + 2
        while fake.ticks == 0 and time.time() < deadline:
            time.sleep(0.01)
        assert fake.ticks >= 1 and fake.startups == 1  # startup 挂线程，启动先对账
        assert app.state.daemon_thread.is_alive()
    assert not app.state.daemon_thread.is_alive()  # shutdown 置 stop_event + join 后已退
    frozen = fake.ticks
    time.sleep(0.05)
    assert fake.ticks == frozen  # 优雅退出后循环不再跑（完成当前迭代即止）


def test_create_app_without_interval_passive(tmp_path):
    import api

    d = hub_db.HubDB(tmp_path / "h.sqlite3")
    eng = qe.QueueEngine(d, max_containers=1, max_retries=1, image_tag_default="t:v1")
    app = api.create_app(d, eng, _TickLC())  # 默认不挂线程（切片 1 被动形态不变）
    with TestClient(app):
        assert app.state.daemon_thread is None


# ── add-hub-slice-3 任务 2.3：POST /deliver 幂等交接端点（D1/D2/D6/D7）────
# file-first（先落盘再入队）+ db/文件双层幂等 + 写面模式钉死 + 可选 token 守卫。
# 落盘位 = <data_root>/<project>/prd/<filename>（F10 写面两类枚举之②）。

import hashlib


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _dbody(project: str = "proj-a", filename: str = "20261022_some-slug.md", content: str = "# PRD\n正文"):
    return {"project": project, "filename": filename, "sha256": _sha(content), "content": content}


def test_deliver_first_time_file_first_creates_file_and_queue(tmp_path):
    client, d, eng, lc = _mk(tmp_path)
    content = "# PRD\n正文"
    resp = client.post("/deliver", json=_dbody(content=content))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["duplicate"] is False and body["project"] == "proj-a"
    target = lc.cfg.data_root / "proj-a" / "prd" / "20261022_some-slug.md"
    assert target.read_text(encoding="utf-8") == content  # 落盘即投递内容
    assert body["prd_path"] == str(target)  # 队列项 PRD 路径 = 落盘位（spec scenario）
    item = d.get_item(body["id"])
    assert item["prd_path"] == str(target) and item["deliver_key"] == f"proj-a:20261022_some-slug.md:{_sha(content)}"
    assert item["enqueued_by"] == "vault-deliver"  # 机机通道来源可辨识


def test_deliver_file_first_enqueue_crash_leaves_file(tmp_path):
    """file-first 钉死（spec scenario「落盘先于入队」）：入队炸，落盘文件仍在——
    下轮重投经 db/文件双层幂等收敛，无「队列项存在但 PRD 永不补写」不可恢复态。"""
    client, d, eng, lc = _mk(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("enqueue crashed")

    eng.enqueue = boom  # monkeypatch 实例方法：模拟窗口⑥后段崩溃
    # 生产行为 = uvicorn 转 500；TestClient 默认把服务端异常直接冒泡，关掉它
    plain = TestClient(client.app, raise_server_exceptions=False)
    resp = plain.post("/deliver", json=_dbody())
    assert resp.status_code == 500
    assert (lc.cfg.data_root / "proj-a" / "prd" / "20261022_some-slug.md").exists()  # 落盘先于入队
    assert d.list_items() == []  # 无队列项（下一轮重投收敛）


def test_deliver_duplicate_returns_existing_and_repairs_file(tmp_path):
    client, d, eng, lc = _mk(tmp_path)
    first = client.post("/deliver", json=_dbody()).json()
    again = client.post("/deliver", json=_dbody())
    assert again.status_code == 200 and again.json()["duplicate"] is True  # 2xx 幂等回执
    assert again.json()["id"] == first["id"] and len(d.list_items()) == 1
    target = lc.cfg.data_root / "proj-a" / "prd" / "20261022_some-slug.md"
    target.unlink()  # 模拟文件丢失（窗口④/⑤残留）：重复投递须校验落盘完整
    r3 = client.post("/deliver", json=_dbody())
    assert r3.status_code == 200 and r3.json()["duplicate"] is True
    assert target.read_text(encoding="utf-8") == "# PRD\n正文"  # 缺失补写
    target.write_text("被篡改的内容", encoding="utf-8")  # 异容（磁盘损坏/人工）
    r4 = client.post("/deliver", json=_dbody())
    assert r4.status_code == 200 and target.read_text(encoding="utf-8") == "# PRD\n正文"  # 异容修正回声明内容


def test_deliver_preexisting_same_name_same_content_reuses(tmp_path):
    """窗口⑥收敛（spec scenario「同名同容复用」）：上次落盘成功但入队前崩，无 db 行——
    这次新键入队，磁盘同容文件幂等复用不重写。"""
    client, d, eng, lc = _mk(tmp_path)
    target = lc.cfg.data_root / "proj-a" / "prd" / "20261022_some-slug.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# PRD\n正文", encoding="utf-8")  # 崩溃残留（同容）
    resp = client.post("/deliver", json=_dbody())
    assert resp.status_code == 201 and resp.json()["duplicate"] is False
    assert target.read_text(encoding="utf-8") == "# PRD\n正文"  # 未重写（幂等复用）


def test_deliver_same_name_different_content_gets_sha_suffix(tmp_path):
    client, d, eng, lc = _mk(tmp_path)
    r1 = client.post("/deliver", json=_dbody())
    assert r1.status_code == 201
    body2 = _dbody(content="# PRD v2\n改了内容")  # 同文件名异容 = 新键 = 合法新 PRD
    r2 = client.post("/deliver", json=body2)
    assert r2.status_code == 201 and r2.json()["duplicate"] is False
    suffix8 = _sha("# PRD v2\n改了内容")[:8]
    expected = lc.cfg.data_root / "proj-a" / "prd" / f"20261022_some-slug-{suffix8}.md"
    assert expected.exists() and expected.read_text(encoding="utf-8") == "# PRD v2\n改了内容"
    assert len(d.list_items()) == 2 and d.get_item(r2.json()["id"])["prd_path"] == str(expected)


def test_deliver_sha_mismatch_400_no_side_effects(tmp_path):
    client, d, eng, lc = _mk(tmp_path)
    bad = _dbody(); bad["sha256"] = "0" * 64  # 声明值撒谎
    resp = client.post("/deliver", json=bad)
    assert resp.status_code == 400
    assert d.list_items() == [] and not (lc.cfg.data_root / "proj-a" / "prd").exists()  # 零副作用


def test_deliver_body_over_1mb_400(tmp_path):
    client, d, eng, lc = _mk(tmp_path)
    huge = _dbody(content="x" * (1024 * 1024 + 1))
    resp = client.post("/deliver", json=huge)
    assert resp.status_code == 400 and d.list_items() == []


def test_deliver_token_guard(tmp_path):
    """D7：HUB_DELIVER_TOKEN 配则 Bearer 必查（hmac 比较）；不配 = 内网无守卫。"""
    client, d, eng, lc = _mk(tmp_path, extra_env={"HUB_DELIVER_TOKEN": "sekrit"})
    b = _dbody()
    assert client.post("/deliver", json=b).status_code == 401  # 无 header
    assert client.post("/deliver", json=b, headers={"Authorization": "Bearer wrong"}).status_code == 401
    ok = client.post("/deliver", json=b, headers={"Authorization": "Bearer sekrit"})
    assert ok.status_code == 201
    # 不配（默认 _mk）：无守卫
    (tmp_path / "b").mkdir()
    client2, _, _, _ = _mk(tmp_path / "b")
    assert client2.post("/deliver", json=_dbody()).status_code == 201


def test_deliver_filename_and_project_pattern_enforced(tmp_path):
    client, d, eng, lc = _mk(tmp_path)
    for bad_name in ("../evil.md", "sub/dir/x.md", "20261022.md", "20261022_BAD.md", "20261022_x.md.exe", "x.md"):
        resp = client.post("/deliver", json=_dbody(filename=bad_name))
        assert resp.status_code == 400, bad_name
    resp = client.post("/deliver", json=_dbody(project="../evil"))
    assert resp.status_code == 400
    assert d.list_items() == [] and not (lc.cfg.data_root / "proj-a" / "prd").exists()  # 写面钉死：模式外零落盘


def test_deliver_touches_nothing_else_on_vault(tmp_path):
    client, d, eng, lc = _mk(tmp_path)
    before = {p for p in (lc.cfg.data_root).rglob("*")}
    client.post("/deliver", json=_dbody())
    after = {p for p in (lc.cfg.data_root).rglob("*")}
    added = after - before
    assert added == {lc.cfg.data_root / "proj-a" / "prd", lc.cfg.data_root / "proj-a" / "prd" / "20261022_some-slug.md"}
    assert d.list_all_runs() == []  # 不触发派发/run（纯交接入队）


# ── add-hub-slice-3 任务 2.5：清队列原语（回退序列 RUNBOOK §15 ③ 依赖）──

def test_delete_queue_clears_pending_with_audit_trail(tmp_path):
    """清 pending = 转终态 failed + last_error 记回退原因（行保留审计，非物理删除）。"""
    client, d, eng, lc = _mk(tmp_path)
    prd = tmp_path / "p.md"; prd.write_text("# x", encoding="utf-8")
    qid = eng.enqueue("proj-a", str(prd), "roc")
    resp = client.delete(f"/queue/{qid}")
    assert resp.status_code == 200
    item = d.get_item(qid)
    assert item["status"] == "failed" and "回退" in item["last_error"]  # 审计记录
    assert d.list_items(status="pending") == []


def test_delete_queue_rejects_running_and_terminal_and_missing(tmp_path):
    client, d, eng, lc = _mk(tmp_path)
    prd = tmp_path / "p.md"; prd.write_text("# x", encoding="utf-8")
    run_qid = eng.enqueue("proj-a", str(prd), "roc")
    lc.tick_dispatch()  # 起跑 running
    resp = client.delete(f"/queue/{run_qid}")
    assert resp.status_code == 409 and "drain" in resp.json()["detail"]  # 先 drain 提示
    assert d.get_item(run_qid)["status"] == "running"  # running 未被动
    done_qid = eng.enqueue("proj-b", str(prd), "roc")
    d.set_item_status(done_qid, "done")
    assert client.delete(f"/queue/{done_qid}").status_code == 409  # 终态不清（回退序列只清 pending）
    assert client.delete("/queue/999").status_code == 404


# ── 读面守卫（add-hub-slice-4a 1.2，spec: execution-hub delta「读面会话守卫」）──

_MM_ENV = {  # auth 已配置态（mm_configured=True）——守卫生效前提
    "HUB_MM_BASE_URL": "https://mm.example.com",
    "HUB_MM_CLIENT_ID": "cid", "HUB_MM_CLIENT_SECRET": "sec",
    "HUB_SESSION_SECRET": "S" * 43,
}


def test_read_endpoints_guarded_when_auth_configured(tmp_path):
    client, d, eng, lc = _mk(tmp_path, extra_env=_MM_ENV)
    # 无会话：三端点 401（GET 两读面 + DELETE 回退原语）
    assert client.get("/queue").status_code == 401
    assert client.get("/runs/1").status_code == 401
    assert client.delete("/queue/1").status_code == 401
    # 有效会话（sign_token 直造，不走 OAuth 流）：守卫放行走到路由层
    tok = auth.sign_token(
        {"mm_user_id": "abc123def456abc123def456ab", "username": "zhang3"},
        lc.cfg.session_secret, ttl_s=60,
    )
    client.cookies.set(auth.SESSION_COOKIE, tok)
    assert client.get("/queue").status_code == 200
    assert client.get("/runs/999").status_code == 404  # 守卫放行后正常 404（非 401）
    assert client.delete("/queue/999").status_code == 404


def test_read_endpoints_unguarded_when_auth_unconfigured(tmp_path):
    client, *_ = _mk(tmp_path)  # 无 MM env = 2a 回退态：守卫零变化
    assert client.get("/queue").status_code == 200
    assert client.get("/runs/999").status_code == 404
    assert client.delete("/queue/999").status_code == 404


# ── 面板 PRD frontmatter 包装（2026-10-23 首跑 #4 实证修复：裸 md 被容器内 pa
#    inject 的 frontmatter.project 校验确定性拒绝 exit 1；hub 落盘即包装）──

import api as api_mod


def test_wrap_panel_prd_shape_and_escape():
    w = api_mod._wrap_panel_prd("proj-a", "# 标题\n正文", "https://x/y")
    assert w.startswith('---\nproject: "proj-a"\nsource_path: \'\'\nevidence: "https://x/y"\n---\n\n# 标题\n')
    # evidence 转义：引号/反斜杠；换行压成单行（frontmatter 逃逸面归零）
    w2 = api_mod._wrap_panel_prd("p", "# t", '含"引\\号"\n第二行')
    assert 'evidence: "含\\"引\\\\号\\" 第二行"' in w2
    # project 白名单值原样（调用方已过 PROJECT_RE）
    assert 'project: "proj-a"' in w


def test_wrap_panel_prd_evidence_overlong_rejects():
    with pytest.raises(ValueError, match="上限"):
        api_mod._wrap_panel_prd("p", "# t", "字" * 1025)  # 压缩后仍 3072B > 2048


def test_panel_prd_persists_wrapped_with_evidence(tmp_path):
    """落盘即包装（spec 契约修订）：文件含 frontmatter（project/source_path 空/evidence），
    sha 按包装后全文；队列项 prd_path 指向它（retry 复用同文件幂等）。"""
    client, d, eng, lc = _mk(tmp_path, extra_env=_MM_ENV)
    _login(client, lc)
    resp = client.post("/panel/prd", data={
        "project": "proj-a", "prd_content": "# 优化UI\n添加古风样式", "evidence": "用户手写，面板直提",
    })
    assert resp.status_code == 201, resp.text
    item = d.list_items()[0]
    saved = Path(item["prd_path"]).read_text(encoding="utf-8")
    assert saved.startswith('---\nproject: "proj-a"\nsource_path: \'\'\nevidence: "用户手写，面板直提"\n---\n')
    assert "# 优化UI" in saved
    assert item["enqueued_by"] == "abc123def456abc123def456ab"


def test_panel_prd_evidence_overlong_422_no_queue(tmp_path):
    client, d, eng, lc = _mk(tmp_path, extra_env=_MM_ENV)
    _login(client, lc)
    resp = client.post("/panel/prd", data={"project": "proj-a", "prd_content": "# t", "evidence": "字" * 1025})
    assert resp.status_code == 422 and "上限" in resp.text
    assert d.list_items() == []


# ── add-hub-panel-console：面板项目建档 + run 操作（D1-D4，spec: hub-panel delta）──


def _login(client, lc, username="zhang3", mm_user_id="abc123def456abc123def456ab"):
    """直造会话 cookie（不走 OAuth 流）——与读面守卫测试同模式。"""
    tok = auth.sign_token({"mm_user_id": mm_user_id, "username": username}, lc.cfg.session_secret, ttl_s=60)
    client.cookies.set(auth.SESSION_COOKIE, tok)
    return {"mm_user_id": mm_user_id, "username": username}


def test_panel_project_create_happy_and_visible_in_submit(tmp_path):
    """建档 happy（spec scenario）：两文件落盘 + 提交页下拉当次可见。"""
    client, d, eng, lc = _mk(tmp_path, extra_env=_MM_ENV)
    _login(client, lc)
    resp = client.post("/panel/projects", data={
        "name": "demo", "repo": "owner/repo", "goal": "把 demo 推进到 v1", "surface": "demo 相关信号",
    })
    assert resp.status_code == 201, resp.text
    prof = (lc.cfg.data_root / "demo" / "profiles" / "demo.yaml").read_text(encoding="utf-8")
    assert "name: demo\n" in prof and "admission: true\n" in prof
    assert 'repo: "https://github.com/owner/repo"' in prof  # repo 规范化
    assert (lc.cfg.data_root / "demo" / "sources.yaml").read_text(encoding="utf-8") == "sources: []\n"
    submit = client.get("/panel")
    assert submit.status_code == 200 and ">demo<" in submit.text  # 下拉命中


def test_panel_project_create_422_no_side_effects(tmp_path):
    """非法输入 422 回显不落盘（spec scenario）：重名/非法名/空字段/换行/坏 repo。"""
    client, d, eng, lc = _mk(tmp_path, extra_env=_MM_ENV)
    _login(client, lc)
    ok = {"repo": "o/r", "goal": "g", "surface": "s"}
    r = client.post("/panel/projects", data={"name": "proj-a", **ok})  # 重名（fixture 已有）
    assert r.status_code == 422 and "已存在" in r.text
    assert client.post("/panel/projects", data={"name": "../evil", **ok}).status_code == 422
    assert client.post("/panel/projects", data={"name": "demo", "repo": "o/r", "goal": "", "surface": "s"}).status_code == 422
    r = client.post("/panel/projects", data={"name": "demo", "repo": "o/r", "goal": "a\nb: evil", "surface": "s"})
    assert r.status_code == 422  # 换行注入拒绝
    assert client.post("/panel/projects", data={"name": "demo", "repo": "not a repo", "goal": "g", "surface": "s"}).status_code == 422
    assert not (lc.cfg.data_root / "demo").exists()  # 零残留（含半写清理路径未触发即拒）


# ── fix-hub-form-parity ④：面板建项目 forge 三形态（spec: hub-panel「建项目表单」）──


def test_panel_project_create_gitlab_via_form(tmp_path):
    """gitlab 经界面自助建档（spec scenario）：表单 forge+gitlab_host → 落盘三键 + 提交页可见。"""
    client, d, eng, lc = _mk(tmp_path, extra_env=_MM_ENV)
    _login(client, lc)
    r = client.post("/panel/projects", data={
        "name": "lab-a", "forge": "gitlab", "gitlab_host": "http://172.32.153.183:8180",
        "repo": "my-group/sub-team/demo-repo", "goal": "推进 lab 项目", "surface": "lab 信号",
    })
    assert r.status_code == 201, r.text
    prof = (lc.cfg.data_root / "lab-a" / "profiles" / "lab-a.yaml").read_text(encoding="utf-8")
    assert 'forge: gitlab\n' in prof
    assert 'gitlab_host: "http://172.32.153.183:8180"\n' in prof
    assert 'repo: "my-group/sub-team/demo-repo"\n' in prof  # 全路径原样（不走 GitHub 归一）
    submit = client.get("/panel")
    assert ">lab-a<" in submit.text  # 提交页下拉命中


def test_panel_project_create_gitlab_missing_host_422(tmp_path):
    """gitlab 缺 host 被 422 拒（spec scenario）：回显错误、零落盘。"""
    client, d, eng, lc = _mk(tmp_path, extra_env=_MM_ENV)
    _login(client, lc)
    r = client.post("/panel/projects", data={
        "name": "lab-a", "forge": "gitlab", "repo": "grp/repo", "goal": "g", "surface": "s",
    })
    assert r.status_code == 422 and "gitlab_host 必填" in r.text
    assert not (lc.cfg.data_root / "lab-a").exists()


def test_panel_project_create_gitcode_and_github_default(tmp_path):
    """gitcode 两级（spec scenario）+ github 缺省回归（表单不带 forge 键 = 既有行为）。"""
    client, d, eng, lc = _mk(tmp_path, extra_env=_MM_ENV)
    _login(client, lc)
    r = client.post("/panel/projects", data={
        "name": "code-a", "forge": "gitcode", "repo": "owner/repo", "goal": "g", "surface": "s",
    })
    assert r.status_code == 201, r.text
    prof = (lc.cfg.data_root / "code-a" / "profiles" / "code-a.yaml").read_text(encoding="utf-8")
    assert 'forge: gitcode\n' in prof and "gitlab_host" not in prof
    assert 'repo: "owner/repo"\n' in prof
    # github 缺省：不带 forge 键（老表单/脚本直 POST）——落盘与既有逐字节一致
    r2 = client.post("/panel/projects", data={
        "name": "demo", "repo": "owner/repo", "goal": "g", "surface": "s",
    })
    assert r2.status_code == 201, r2.text
    prof2 = (lc.cfg.data_root / "demo" / "profiles" / "demo.yaml").read_text(encoding="utf-8")
    assert "forge" not in prof2 and 'repo: "https://github.com/owner/repo"' in prof2


def test_panel_ops_admin_guard(tmp_path):
    """D4：未登录 401 / 白名单外 403 / 白名单内放行 / 空名单=任何有效会话放行。"""
    client, d, eng, lc = _mk(tmp_path, extra_env={**_MM_ENV, "HUB_PANEL_ADMINS": "li4"})
    prd = tmp_path / "p.md"; prd.write_text("# x", encoding="utf-8")
    qid = eng.enqueue("proj-a", str(prd), "roc")
    eng.fail_item(qid, "x")  # 供 retry
    qid2 = eng.enqueue("proj-a", str(prd), "roc")  # 供 abandon
    form = {"name": "demo", "repo": "o/r", "goal": "g", "surface": "s"}
    # 未登录：三操作 401（GET 表单页则 302 到 authorize——浏览器友好；不跟随重定向，
    # 否则 TestClient 会跟到 MM 假外域 mm.example.com 上）
    assert client.post("/panel/projects", data=form).status_code == 401
    assert client.post(f"/panel/queue/{qid}/retry").status_code == 401
    assert client.post(f"/panel/queue/{qid2}/abandon").status_code == 401
    assert client.get("/panel/projects/new", follow_redirects=False).status_code == 302
    # 白名单外：403
    _login(client, lc, username="zhang3")
    assert client.post("/panel/projects", data=form).status_code == 403
    assert client.post(f"/panel/queue/{qid}/retry").status_code == 403
    assert client.post(f"/panel/queue/{qid2}/abandon").status_code == 403
    # 白名单内：放行（建档 201 / 重跑 201 / 放弃 200）
    _login(client, lc, username="li4", mm_user_id="bbb123def456abc123def456ab")
    assert client.post("/panel/projects", data=form).status_code == 201
    assert client.post(f"/panel/queue/{qid}/retry").status_code == 201
    assert client.post(f"/panel/queue/{qid2}/abandon").status_code == 200
    # 空名单（默认 _MM_ENV 不配 HUB_PANEL_ADMINS）= 单用户回退态：任何有效会话放行
    (tmp_path / "b2").mkdir()
    client2, _, _, lc2 = _mk(tmp_path / "b2", extra_env=_MM_ENV)
    _login(client2, lc2)
    assert client2.post("/panel/projects", data=form).status_code == 201


def test_panel_retry_failed_creates_new_item(tmp_path):
    """重跑 happy（spec scenario）：新项同 project/prd、发起人=操作者；旧行留审计。"""
    client, d, eng, lc = _mk(tmp_path, extra_env=_MM_ENV)
    _login(client, lc)  # enqueued_by 断言改走原项后 ident 不再用（F841）
    prd = tmp_path / "p.md"; prd.write_text("# x", encoding="utf-8")
    qid = eng.enqueue("proj-a", str(prd), "roc", initiator="roc")
    eng.fail_item(qid, "跑炸了")
    resp = client.post(f"/panel/queue/{qid}/retry")
    assert resp.status_code == 201, resp.text
    new = [i for i in d.list_items() if i["id"] != qid][0]
    assert new["status"] == "pending" and new["project"] == "proj-a"
    assert new["prd_path"] == str(prd)
    # 凭据跟入队人走：admin 代点重跑不换凭据源（2026-10-27 #52 凭据缺失回归锚）
    assert new["enqueued_by"] == "roc" and new["initiator"] == "zhang3"
    assert d.get_item(qid)["status"] == "failed" and d.get_item(qid)["last_error"] == "跑炸了"  # 旧行原样
    assert f"#{new['id']}" in resp.text  # 回执含新项号


def test_panel_retry_rejects_non_failed_and_missing_prd(tmp_path):
    """重跑 409/404 形态（spec scenario「重跑非 failed 项 409」）：pending/running 防双跑、
    done 防重复投递、PRD 文件不在 404、项不存在 404。"""
    client, d, eng, lc = _mk(tmp_path, extra_env=_MM_ENV)
    _login(client, lc)
    prd = tmp_path / "p.md"; prd.write_text("# x", encoding="utf-8")
    qid = eng.enqueue("proj-a", str(prd), "roc")
    assert client.post(f"/panel/queue/{qid}/retry").status_code == 409  # pending
    assert len(d.list_items()) == 1  # 拒绝不入队
    lc.tick_dispatch()  # 起跑 running
    assert client.post(f"/panel/queue/{qid}/retry").status_code == 409
    d.set_item_status(qid, "done")
    assert client.post(f"/panel/queue/{qid}/retry").status_code == 409
    eng.fail_item(qid, "x")
    prd.unlink()  # failed 但原 PRD 文件没了
    assert client.post(f"/panel/queue/{qid}/retry").status_code == 404
    assert client.post("/panel/queue/999/retry").status_code == 404


def test_panel_abandon_pending_same_as_rest_delete(tmp_path):
    """放弃与 REST DELETE 同源（spec scenario）：同语义同实现，非 pending 同 409 文案。"""
    client, d, eng, lc = _mk(tmp_path, extra_env=_MM_ENV)
    _login(client, lc)
    prd = tmp_path / "p.md"; prd.write_text("# x", encoding="utf-8")
    qid = eng.enqueue("proj-a", str(prd), "roc")
    resp = client.post(f"/panel/queue/{qid}/abandon")
    assert resp.status_code == 200
    item = d.get_item(qid)
    assert item["status"] == "failed" and "面板人工放弃" in item["last_error"]  # 记因 + 行留审计
    assert d.list_items(status="pending") == []
    r_ab = client.post(f"/panel/queue/{qid}/abandon")  # 已 failed 再放弃
    r_del = client.delete(f"/queue/{qid}")  # 同一实现同文案
    assert r_ab.status_code == r_del.status_code == 409
    assert r_ab.json()["detail"] == r_del.json()["detail"]
    assert client.post("/panel/queue/999/abandon").status_code == 404


# ── fix-hub-panel-ux：422 回显回填（spec: hub-panel「UX 底线」——拒绝不丢输入）──


def test_panel_prd_422_repaints_submitted_values(tmp_path):
    """提交 PRD 422（evidence 空白）：页面回填已选 project（selected）+ 已贴 PRD 正文。"""
    client, d, eng, lc = _mk(tmp_path, extra_env=_MM_ENV)
    _login(client, lc)
    resp = client.post("/panel/prd", data={
        "project": "proj-a", "prd_content": "# 手写目标\n## 验收\n- [ ] 回填可见", "evidence": "  ",
    })
    assert resp.status_code == 422 and "evidence 必填" in resp.text
    assert "# 手写目标" in resp.text and "回填可见" in resp.text  # textarea 正文回填
    assert '<option value="proj-a" selected>' in resp.text  # select selected 回填


def test_panel_project_create_422_repaints_submitted_values(tmp_path):
    """建项目 422（重名触发）：六字段回填 + forge=gitlab selected + gitlab_host 值保留。"""
    client, d, eng, lc = _mk(tmp_path, extra_env=_MM_ENV)
    _login(client, lc)
    resp = client.post("/panel/projects", data={  # name=proj-a 与 fixture 重名 → 422；其余字段合法
        "name": "proj-a", "repo": "g/sub/r", "goal": "推进 lab-x", "surface": "lab 信号",
        "forge": "gitlab", "gitlab_host": "http://172.32.0.1:8180",
    })
    assert resp.status_code == 422 and "已存在" in resp.text
    assert 'value="g/sub/r"' in resp.text and 'value="推进 lab-x"' in resp.text
    assert 'value="lab 信号"' in resp.text and 'value="http://172.32.0.1:8180"' in resp.text
    assert '<option value="gitlab" selected>' in resp.text


def test_panel_credentials_422_token_not_repainted(tmp_path):
    """凭据 422：非密码框回填（base_url/model）；token 框不回填（密码语义例外锚）。"""
    from cryptography.fernet import Fernet

    import vault_store as vs
    _, d, eng, lc = _mk(tmp_path, extra_env=_MM_ENV)
    vault = vs.CredentialVault(tmp_path / "vault", Fernet.generate_key())
    client = TestClient(api.create_app(d, eng, lc, vault=vault))
    _login(client, lc)
    resp = client.post("/panel/credentials", data={  # 平台 token 全空 → 422（auth 两值已给全）
        "anthropic_base_url": "http://172.32.0.1:9001",
        "anthropic_auth_token": "sk-secret-tok-0123456789abcd",
        "model": "qwen3.8-27b-fast",
    })
    assert resp.status_code == 422 and "缺平台 token" in resp.text
    assert 'value="http://172.32.0.1:9001"' in resp.text  # base_url 回填
    assert 'value="qwen3.8-27b-fast"' in resp.text  # model 回填
    assert "sk-secret-tok-0123456789abcd" not in resp.text  # token 框绝不回填（密码例外）


# ── fix-hub-queue-recency：队列最新优先（spec: hub-panel「面板只读面」）──


def test_queue_listing_recency_order(tmp_path):
    """GET /queue 同源降序：最新入队在前（与面板同 db.list_items，无分支透传）。"""
    client, d, eng, lc = _mk(tmp_path, extra_env=_MM_ENV)
    _login(client, lc)  # REST 读面同会话守卫（与面板同口径）
    p = tmp_path / "a.md"; p.write_text("# x", encoding="utf-8")
    q1 = eng.enqueue("proj-a", str(p), "t", "u")
    q2 = eng.enqueue("proj-a", str(p), "t", "u")
    resp = client.get("/queue")
    assert resp.status_code == 200
    assert [i["id"] for i in resp.json()] == [q2, q1]  # 最新优先



# ── add-mm-channel-intake：POST /mm/intake 入站端点全路径（2.2/4.5/5.2）──
# 全离线：FakeMMClient 可编程成员面；slash token 走 env；绑定直插 db（bind 面
# 另测）。form 字段对齐 MM slash command POST（x-www-form-urlencoded）。

_SLASH_TOK = "slash-secret-token"
_U1 = "a1b2c3d4e5f6a1b2c3d4e5f6a1"  # 发起人 user_id（26-hex 直发形态）
_BOT = "f" * 26                     # FakeMMClient.me_id（bot 探活判据）
_C1 = "c1b2c3d4e5f6a1b2c3d4e5f6a1"  # 频道 id（26 字符形态）
_C2 = "d1b2c3d4e5f6a1b2c3d4e5f6a1"


def _mk_mm(tmp_path, mm, extra_env=None, **mk_kw):
    env = {"HUB_MM_SLASH_TOKEN": _SLASH_TOK}
    if extra_env:
        env.update(extra_env)
    return _mk(tmp_path / "mm", extra_env=env, mm_client=mm, **mk_kw)


def _slash(client, *, token=_SLASH_TOK, channel_id=_C1, user_id=_U1, user_name="zhang3", text=""):
    return client.post("/mm/intake", data={
        "token": token, "channel_id": channel_id, "user_id": user_id,
        "user_name": user_name, "command": "/pa", "text": text,
    })


def _mm_bound(members=None, **kw):
    import notify
    return notify.FakeMMClient(channel_members={_C1: members if members is not None else [_U1, _BOT]}, **kw)


def test_mm_intake_token_gate_two_states(tmp_path):
    """2.2 契约：未配 slash token = 503（入站关闭零行为）；配了不匹配 = 401；匹配放行。"""
    client0, _, _, _ = _mk(tmp_path / "off")  # 未配
    assert client0.post("/mm/intake", data={"token": "x"}).status_code == 503
    mm = _mm_bound()
    client, _, _, _ = _mk_mm(tmp_path, mm)
    assert _slash(client, token="wrong").status_code == 401  # 恒时校验的形态（非时序断言）
    assert _slash(client, text="随便").status_code == 200  # 对 token → 分流（用法清单也是 200）


def test_mm_intake_unknown_subcommand_usage(tmp_path):
    client, _, _, _ = _mk_mm(tmp_path, _mm_bound())
    resp = _slash(client, text="跳舞")
    assert resp.status_code == 200 and resp.json()["response_type"] == "ephemeral"
    assert "/pa run" in resp.json()["text"] and "/pa bind" in resp.json()["text"]


def test_mm_intake_unbound_channel_educates_bind(tmp_path):
    client, d, _, _ = _mk_mm(tmp_path, _mm_bound())
    resp = _slash(client, text="run 修 bug 出处: https://x/1")
    assert resp.status_code == 200
    assert "未绑定" in resp.json()["text"] and "/pa bind" in resp.json()["text"]
    assert d.list_items() == []  # 拒绝即不入队


def test_mm_intake_non_member_denied_no_channel_post(tmp_path):
    mm = _mm_bound(members=["b" * 26, _BOT])  # 发起人不在集
    client, d, _, _ = _mk_mm(tmp_path, mm)
    d.upsert_bind("proj-a", _C1, "rocju")
    resp = _slash(client, text="run 修 bug 出处: https://x/1")
    assert "不是本频道成员" in resp.json()["text"]
    assert d.list_items() == [] and mm.channels == []  # 拒绝不广播


def test_mm_intake_unknown_fail_closed(tmp_path):
    """UNKNOWN（网络错/403/无 bot）fail-closed 拒——绝不旁路、绝不回执「非成员」。"""
    d_side = tmp_path / "d1"; mm1 = _mm_bound(crash_on_members=RuntimeError("net"))
    client1, d1, _, _ = _mk_mm(d_side, mm1); d1.upsert_bind("proj-a", _C1, "rocju")
    t1 = _slash(client1, text="run 修 bug 出处: https://x/1").json()["text"]
    assert "校验暂不可用" in t1 and "非成员" not in t1
    d_side2 = tmp_path / "d2"; mm2 = _mm_bound(members_forbidden={_C1})
    client2, d2, _, _ = _mk_mm(d_side2, mm2); d2.upsert_bind("proj-a", _C1, "rocju")
    assert "校验暂不可用" in _slash(client2, text="run x 出处: y").json()["text"]
    d_side3 = tmp_path / "d3"  # mm_client=None：判据不可得恒 UNKNOWN
    client3, d3, _, _ = _mk_mm(d_side3, None); d3.upsert_bind("proj-a", _C1, "rocju")
    assert "校验暂不可用" in _slash(client3, text="run x 出处: y").json()["text"]
    assert d1.list_items() == [] and d2.list_items() == [] and d3.list_items() == []


def test_mm_intake_channel_missing_check_binding_receipt(tmp_path):
    mm = _mm_bound(members_not_found={_C1})
    client, d, _, _ = _mk_mm(tmp_path, mm)
    d.upsert_bind("proj-a", _C1, "rocju")
    resp = _slash(client, text="run 修 bug 出处: https://x/1")
    assert "频道不存在" in resp.json()["text"] and "检查绑定" in resp.json()["text"]
    assert d.list_items() == []


def test_mm_intake_evidence_gate_fullwidth_colon_accepted(tmp_path):
    mm = _mm_bound()
    client, d, _, _ = _mk_mm(tmp_path, mm)
    d.upsert_bind("proj-a", _C1, "rocju")
    # 全角冒号（中文输入法高频）必须通过
    r = _slash(client, text="run 修登录超时 出处：https://issues/1")
    assert "已入队" in r.json()["text"]
    # 缺标记 / 标记在但值空
    (tmp_path / "mm" / "e2").mkdir(parents=True)
    client2, d2, _, _ = _mk_mm(tmp_path / "e2", _mm_bound()); d2.upsert_bind("proj-a", _C1, "rocju")
    assert "缺出处" in _slash(client2, text="run 修登录超时").json()["text"]
    r2 = _slash(client2, text="run 修登录超时 出处：")
    assert "缺出处" in r2.json()["text"] and d2.list_items() == []


def test_mm_intake_too_long_rejected(tmp_path):
    mm = _mm_bound()
    client, d, _, _ = _mk_mm(tmp_path, mm)
    d.upsert_bind("proj-a", _C1, "rocju")
    huge = "字" * (mm_intake.MAX_TEXT_BYTES // 3 + 1)  # >1MB utf-8
    resp = _slash(client, text=f"run {huge} 出处: x")
    assert "超长" in resp.json()["text"] and "面板" in resp.json()["text"]
    assert d.list_items() == []  # 拒绝不截断不入队


def test_mm_intake_success_dual_receipt_and_direct_route(tmp_path):
    """成功全链：enqueued_by=user_id 直发、确定性文件名、deliver_key 形态、双回执。"""
    import prd_store
    mm = _mm_bound()
    client, d, _, _ = _mk_mm(tmp_path, mm)
    d.upsert_bind("proj-a", _C1, "rocju")
    resp = _slash(client, text="run 修登录超时 出处: https://issues/1")
    body = resp.json()
    assert body["response_type"] == "ephemeral" and "已入队 #" in body["text"]
    items = d.list_items()
    assert len(items) == 1
    it = items[0]
    assert it["enqueued_by"] == _U1  # 26-hex 直发路由（入站身份即 user_id）
    assert it["project"] == "proj-a" and it["status"] == "pending"
    assert it["deliver_key"].startswith(f"mm:proj-a:{_U1}:") and it["deliver_key"].endswith(tuple("0123456789"))
    # 确定性文件名 mm-<sha8>.md；wrap frontmatter 可审计
    files = sorted((tmp_path / "mm" / "data" / "proj-a" / "prd").glob("mm-*.md"))
    assert len(files) == 1 and len(files[0].stem) == len("mm-") + 8
    content = files[0].read_text(encoding="utf-8")
    assert "source: mm-channel" in content and 'evidence: "https://issues/1"' in content
    assert prd_store.sha256_text(content)  # 落盘完整（sha 同源）
    # 双回执：频道时间线 1 条含 @发起人 + #id；无 DM
    assert len(mm.channels) == 1 and f"<@{_U1}>" in mm.channels[0][1] and "#" in mm.channels[0][1]
    assert mm.dms == []


def test_mm_intake_same_content_retry_idempotent(tmp_path):
    """同 (user, text) 重发（MM webhook 重试）——同小时窗撞 deliver_key 幂等回执 #id。"""
    mm = _mm_bound()
    client, d, _, _ = _mk_mm(tmp_path, mm)
    d.upsert_bind("proj-a", _C1, "rocju")
    r1 = _slash(client, text="run 修登录超时 出处: https://issues/1")
    r2 = _slash(client, text="run 修登录超时 出处: https://issues/1")
    assert "已入队" in r1.json()["text"]
    assert "已入队" in r2.json()["text"] and "幂等" in r2.json()["text"]
    assert len(d.list_items()) == 1  # 不重复入队
    assert len(mm.channels) == 1  # 重试不再进频道时间线


def test_mm_intake_channel_post_failure_fail_open(tmp_path):
    """频道时间线 post 崩（bot 被踢等）——ephemeral 回执照回、入队不受影响（fail-open）。"""
    mm = _mm_bound(crash_on_channel=RuntimeError("chan down"))
    client, d, _, _ = _mk_mm(tmp_path, mm)
    d.upsert_bind("proj-a", _C1, "rocju")
    resp = _slash(client, text="run 修登录超时 出处: https://issues/1")
    assert resp.status_code == 200 and "已入队" in resp.json()["text"]
    assert len(d.list_items()) == 1


# ── bind/unbind 管理命令（5.2）────────────────────────────────────

def test_bind_requires_admin_whitelist(tmp_path):
    mm = _mm_bound()
    client, d, _, _ = _mk_mm(tmp_path, mm, extra_env={"HUB_PANEL_ADMINS": "rocju"})
    resp = _slash(client, user_name="zhang3", text="bind proj-a")
    assert "管理员" in resp.json()["text"]
    assert d.get_channel_binding_by_channel(_C1) is None  # 拒绝不落库


def test_bind_empty_admins_fail_closed_on_slash_channel(tmp_path):
    """空白名单语义分立（F2 裁决）：面板空 = OAuth 会话回退放行；slash 空 = 全拒。"""
    mm = _mm_bound()  # 不配 HUB_PANEL_ADMINS
    client, d, _, _ = _mk_mm(tmp_path, mm)
    resp = _slash(client, user_name="rocju", text="bind proj-a")
    assert "未开放" in resp.json()["text"] and "HUB_PANEL_ADMINS" in resp.json()["text"]
    assert d.get_channel_binding_by_channel(_C1) is None


def test_bind_project_not_readable_rejected(tmp_path):
    mm = _mm_bound()
    client, d, _, _ = _mk_mm(tmp_path, mm, extra_env={"HUB_PANEL_ADMINS": "rocju"})
    resp = _slash(client, user_name="rocju", text="bind proj-nope")
    assert "不在可绑定清单" in resp.json()["text"] and "proj-a" in resp.json()["text"]  # 回执带可用清单


def test_bind_success_and_rebind_shows_replacement(tmp_path):
    mm = _mm_bound()
    mm.channel_members[_C2] = [_U1, _BOT]
    client, d, _, _ = _mk_mm(tmp_path, mm, extra_env={"HUB_PANEL_ADMINS": "rocju"})
    r1 = _slash(client, user_name="rocju", text="bind proj-a")
    assert "已绑定：proj-a" in r1.json()["text"] and "by rocju" in r1.json()["text"]
    assert mm.channels == []  # bind 管理动作不进频道时间线
    r2 = _slash(client, user_name="rocju", channel_id=_C2, text="bind proj-a")  # project 换频道
    assert "替换旧绑定" in r2.json()["text"] and _C1 in r2.json()["text"]
    assert d.get_channel_binding_by_channel(_C1) is None  # 旧绑定必清
    assert d.get_channel_binding_by_project("proj-a")["channel_id"] == _C2


def test_bind_bot_not_in_channel_rejected(tmp_path):
    mm = _mm_bound(members=[_U1])  # bot 不在成员集
    client, d, _, _ = _mk_mm(tmp_path, mm, extra_env={"HUB_PANEL_ADMINS": "rocju"})
    resp = _slash(client, user_name="rocju", text="bind proj-a")
    assert "bot 不在本频道" in resp.json()["text"] and "拉进" in resp.json()["text"]
    assert d.get_channel_binding_by_channel(_C1) is None


def test_bind_requires_mm_client(tmp_path):
    client, d, _, _ = _mk_mm(tmp_path, None, extra_env={"HUB_PANEL_ADMINS": "rocju"})
    resp = _slash(client, user_name="rocju", text="bind proj-a")
    assert "bot 未配置" in resp.json()["text"]
    assert d.get_channel_binding_by_channel(_C1) is None


def test_unbind_then_intake_denied_and_education(tmp_path):
    mm = _mm_bound()
    client, d, _, _ = _mk_mm(tmp_path, mm, extra_env={"HUB_PANEL_ADMINS": "rocju"})
    d.upsert_bind("proj-a", _C1, "rocju")
    r1 = _slash(client, user_name="rocju", text="unbind")
    assert "已解绑：proj-a" in r1.json()["text"]
    r2 = _slash(client, user_name="rocju", text="unbind")  # 再解绑：教育回执
    assert "未绑定" in r2.json()["text"]
    r3 = _slash(client, text="run 修 bug 出处: https://x/1")  # 入站回落未绑定拒绝
    assert "未绑定" in r3.json()["text"] and d.list_items() == []


# ── 服务器池端点（add-hub-runner-pool 5.2）────────────────────────────
# 权限分层：观察面（GET /runners、GET /panel/runners）任何有效会话；改行为面
# （注册/编辑/禁启用/删除/探针）挂 HUB_PANEL_ADMINS 门（非白名单 403、无会话 401）。

_POOL_ENV = {**_MM_ENV, "HUB_PANEL_ADMINS": "rocju"}  # admins 白名单单用户


def _mk_pool(tmp_path):
    return _mk(tmp_path, extra_env=_POOL_ENV)


_BODY = {"name": "gpu-a", "docker_host": "tcp://10.0.0.1:2375", "mount_root": "/mnt/nfs/pa", "capacity": 2}


def test_runner_observation_any_valid_session(tmp_path):
    # 观察面放行：普通登录用户（非白名单）也能看池列表红绿灯 + 面板池页
    client, d, eng, lc = _mk(tmp_path, extra_env=_MM_ENV)
    _login(client, lc)  # zhang3（无 admins 配置 = 空，本用例仅证读面守卫）
    r = client.get("/runners")
    assert r.status_code == 200 and r.json() == []
    assert client.get("/panel/runners").status_code == 200


def test_runner_register_and_validation_passthrough(tmp_path):
    client, d, eng, lc = _mk_pool(tmp_path)
    _login(client, lc, username="rocju")
    r = client.post("/runners", json=_BODY)
    assert r.status_code == 201
    v = r.json()
    assert v["name"] == "gpu-a" and v["running"] == 0 and v["capacity"] == 2
    assert v["enabled"] is True and v["probe_ok"] is None  # 入池未探——不参选
    # 校验透传 db 层：三种坏形态各 400 带原因（name 大写 / host 无 tcp:// / capacity 0）
    for bad in [{"name": "GPU-A"}, {"docker_host": "http://10.0.0.1:2375"}, {"capacity": 0}]:
        resp = client.post("/runners", json={**_BODY, **bad})
        assert resp.status_code == 400 and resp.json()["detail"], bad
    assert d.list_runners() != [] and len(d.list_runners()) == 1  # 坏形态零落行


def test_runner_mutation_admin_gate(tmp_path):
    # 改行为面白名单门：非白名单登录 403、无会话 401（观察面同身份放行）
    client, d, eng, lc = _mk_pool(tmp_path)
    d.register_runner("gpu-a", "tcp://10.0.0.1:2375", "/mnt/nfs/pa", 1)
    _login(client, lc)  # zhang3 不在白名单
    assert client.get("/runners").status_code == 200  # 观察面同身份放行
    assert client.post("/runners", json=_BODY).status_code == 403
    assert client.post("/runners/1", json=_BODY).status_code == 403
    for path in ("/runners/1/enable", "/runners/1/disable", "/runners/1/probe"):
        assert client.post(path).status_code == 403, path
    assert client.delete("/runners/1").status_code == 403
    client.cookies.delete(auth.SESSION_COOKIE)  # 无会话：401（登录前置）
    assert client.post("/runners/1/enable").status_code == 401


def test_runner_disable_soft_offline_with_inflight_run(tmp_path):
    # 禁用软下线放行：有在飞 run（快照在 runs 行）亦放行——不拒绝不断账
    client, d, eng, lc = _mk_pool(tmp_path)
    _login(client, lc, username="rocju")
    rid = d.register_runner("gpu-a", "tcp://10.0.0.1:2375", "/mnt/nfs/pa", 1)
    prd = tmp_path / "p.md"; prd.write_text("# PRD\n", encoding="utf-8")
    iid = eng.enqueue("proj-a", str(prd), "zhang3")
    d.create_run(iid, "proj-a", str(tmp_path / "run-x"),
                 runner_name="gpu-a", runner_docker_host="tcp://10.0.0.1:2375")
    r = client.post(f"/runners/{rid}/disable")
    assert r.status_code == 200 and r.json()["enabled"] is False
    # 在飞 run 快照不受禁用影响（对账仍路由原机）
    detail = api_mod.run_detail(d, 1)
    assert detail["runner_name"] == "gpu-a" and detail["runner_docker_host"] == "tcp://10.0.0.1:2375"
    assert client.post(f"/runners/{rid}/enable").json()["enabled"] is True


def test_runner_probe_and_delete(tmp_path, monkeypatch):
    client, d, eng, lc = _mk_pool(tmp_path)
    _login(client, lc, username="rocju")
    rid = d.register_runner("gpu-a", "tcp://10.0.0.1:2375", "/mnt/nfs/pa", 1)

    def fake_probe(db_, r_, cfg_):  # 三项绿写缓存；缺行 KeyError → 端点 404
        if db_.get_runner(r_) is None:
            raise KeyError(r_)
        detail = {"daemon": "ok", "image": "ok", "mount": "ok"}
        db_.set_runner_probe(r_, True, detail)
        return detail

    import health_probe as hp
    monkeypatch.setattr(hp, "probe_and_store", fake_probe)
    r = client.post(f"/runners/{rid}/probe")
    assert r.status_code == 200 and r.json()["probe_ok"] is True
    assert client.post("/runners/999/probe").status_code == 404
    assert client.delete(f"/runners/{rid}").json() == {"deleted": rid}
    assert client.delete(f"/runners/{rid}").status_code == 404  # 再删缺行
    assert d.list_runners() == []


def test_panel_runner_form_flow(tmp_path):
    # 面板皮全流程：form 注册 → 422 回显回填 → ?edit 回填 → edit 提交（清探针缓存）
    # → 禁用/启用薄壳
    client, d, eng, lc = _mk_pool(tmp_path)
    _login(client, lc, username="rocju")
    rv = client.post("/panel/runners", data=_BODY)
    assert rv.status_code == 200 and "gpu-a" in rv.text
    assert d.list_runners()[0]["capacity"] == 2
    # 坏表单 422：错误可见 + 非密码字段回填（不丢输入）
    rv = client.post("/panel/runners", data={**_BODY, "name": "gpu-b", "docker_host": "http://x"})
    assert rv.status_code == 422 and "tcp://" in rv.text and 'value="gpu-b"' in rv.text
    # 编辑模式同页回填（action 指向 edit 端点）
    rid = d.list_runners()[0]["id"]
    rv = client.get(f"/panel/runners?edit={rid}")
    assert f"/panel/runners/{rid}/edit" in rv.text and 'value="gpu-a"' in rv.text
    # 探针转绿后再编辑：缓存被清（回未探不参选）
    d.set_runner_probe(rid, True, {"daemon": "ok", "image": "ok", "mount": "ok"})
    rv = client.post(f"/panel/runners/{rid}/edit",
                     data={**_BODY, "docker_host": "tcp://10.0.0.9:2375", "capacity": "3"})
    assert rv.status_code == 200
    row = d.get_runner(rid)
    assert row["docker_host"] == "tcp://10.0.0.9:2375" and row["capacity"] == 3
    assert row["probe_ok"] is None and row["probed_at"] is None
    # 面板禁用/启用薄壳
    assert client.post(f"/panel/runners/{rid}/disable").status_code == 200
    assert d.get_runner(rid)["enabled"] == 0
    assert client.post(f"/panel/runners/{rid}/enable").status_code == 200
    assert d.get_runner(rid)["enabled"] == 1
    # 行内红绿灯渲染（探针缓存清后 = 未探）
    assert "⚪ 未探" in client.get("/panel/runners").text
