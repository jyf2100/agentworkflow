"""hub API 面——三端点 + auth 端点群 + 可选排度常驻线程（add-hub-slice-2a 4.1 / 2b 2.x）。

仅机械面：入队（校验+固化 tag）/队列视图/run 详情 + auth（登录/会话/凭据录入，
spec: hub-oauth-session）。无语义判定面（审 PRD、判价值归 pa persona——hub 不做）。
共享内部函数（validate_and_enqueue / queue_view / run_detail）同时是 cli.py 的
数据源（8.2：同一函数，两种皮）。

SIGTERM 路由链（design D4）：容器形态 PID 1 是 uvicorn——lifespan shutdown 置
stop_event + join 线程，保证「完成当前迭代后退出」（daemon=True 线程随进程退
会被硬杀，故 shutdown 必须 join，不能只靠进程退出）。
"""
from __future__ import annotations

import hmac
import secrets
import threading
from contextlib import asynccontextmanager
from pathlib import Path

import auth
import daemon
import queue_engine
from lifecycle import PROJECT_RE
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

import db
import vault_store


class ApiInputError(ValueError):
    """入队输入非法——端点转 400，cli 转非零退出（fail fast 不留脏记录）。"""


class EnqueueBody(BaseModel):
    project: str
    prd_path: str
    image_tag: str | None = None
    enqueued_by: str | None = None  # 缺省由端点兜底 cfg.user_id（B4）——无第三身份字面量


class CredentialsBody(BaseModel):
    """模块级（不能进 create_app 闭包）：`from __future__ import annotations` 下
    FastAPI 按模块全局解析注解字符串，闭包内定义会退化成 query 参数（422 实证）。"""
    anthropic_base_url: str
    anthropic_auth_token: str
    gh_token: str
    model: str | None = None


def validate_and_enqueue(
    engine: queue_engine.QueueEngine,
    *,
    project: str,
    prd_path: str,
    image_tag: str | None = None,
    enqueued_by: str,
    initiator: str | None = None,
) -> dict:
    """入队校验 + 固化 tag——api 与 cli 共用（F13：缺省取引擎配置默认）。

    initiator（2b D4）：会话发起时的人读展示名（F10 meta initiator）；无会话为
    None（派发时回落 enqueued_by，即 2a 单用户行为）。
    """
    if not PROJECT_RE.fullmatch(project):
        raise ApiInputError(f"project 名非法（须匹配 {PROJECT_RE.pattern}）：{project!r}")
    if not Path(prd_path).is_file():
        raise ApiInputError(f"PRD 文件不存在：{prd_path}")
    item_id = engine.enqueue(project, prd_path, enqueued_by, image_tag=image_tag, initiator=initiator)
    return engine.get_item(item_id)


def queue_view(database: db.HubDB, status: str | None = None) -> list[dict]:
    if status is not None and status not in db.ITEM_STATUSES:
        raise ApiInputError(f"非法 status：{status!r}（合法：{'/'.join(db.ITEM_STATUSES)}）")
    return database.list_items(status)


def run_detail(database: db.HubDB, run_id: int) -> dict | None:
    try:
        run = database.get_run(run_id)
    except db.DBError:
        return None
    try:
        item = database.get_item(run["queue_item_id"])
    except db.DBError:
        item = None  # 孤儿归档行（meta 里的 queue_item_id 无对应记录）
    return {**run, "queue_item": item}


def create_app(
    database: db.HubDB,
    engine: queue_engine.QueueEngine,
    lc,
    *,
    tick_interval: float | None = None,
    vault: vault_store.CredentialVault | None = None,
) -> FastAPI:
    """tick_interval=None = 被动 API（切片 1 形态零变化）；非 None = startup 挂排度常驻线程。

    vault（2b）：凭据录入端点的写入通道（None = 未配 master key，录入端点 503）。
    auth 配置从 lc.cfg 读（可选面：未配置 = auth 群 503，其余端点零影响）。
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        stop = threading.Event()
        thread = None
        if tick_interval is not None:
            thread = threading.Thread(
                target=daemon.run_forever, args=(lc, tick_interval, stop),
                name="hub-daemon", daemon=True,
            )
            thread.start()
        app.state.daemon_thread = thread
        app.state.stop_event = stop
        try:
            yield
        finally:
            stop.set()  # uvicorn lifespan shutdown（SIGTERM 终点）→ 优雅退出信号
            if thread is not None and thread.is_alive():
                thread.join(timeout=10)  # 等当前迭代收尾；超时由 compose stop_grace_period 兜底

    app = FastAPI(title="pa-hub", version="0.2.0", lifespan=lifespan)

    # ── auth 端点群（2b，spec: hub-oauth-session；未配置 MM = 整群 503）────
    # cfg 在各端点内按需取 lc.cfg——不在主体取（lifespan 测试替身无 cfg，只测线程面）

    def _session_identity(request: Request) -> dict | None:
        if not auth.mm_configured(lc.cfg):
            return None
        return auth.verify_session(request.cookies.get(auth.SESSION_COOKIE, ""), lc.cfg.session_secret)

    def _require_configured() -> None:
        if not auth.mm_configured(lc.cfg):
            raise HTTPException(
                status_code=503,
                detail="auth 未配置（需 HUB_MM_BASE_URL / HUB_MM_CLIENT_ID / HUB_MM_CLIENT_SECRET / HUB_SESSION_SECRET）",
            )

    @app.get("/auth/authorize")
    def auth_authorize(request: Request):
        _require_configured()
        state = auth.sign_token({"n": secrets.token_hex(8)}, lc.cfg.session_secret, ttl_s=auth.STATE_TTL_S)
        redirect_uri = str(request.base_url).rstrip("/") + "/auth/callback"
        resp = RedirectResponse(auth.build_authorize_url(lc.cfg, redirect_uri, state), status_code=302)
        resp.set_cookie(auth.STATE_COOKIE, state, max_age=auth.STATE_TTL_S, httponly=True, samesite="lax")
        return resp

    @app.get("/auth/callback")
    def auth_callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None):
        _require_configured()
        if error:
            raise HTTPException(status_code=400, detail=f"用户拒绝授权：{error}")
        cookie_state = request.cookies.get(auth.STATE_COOKIE, "")
        if not code or not state or not cookie_state:
            raise HTTPException(status_code=400, detail="缺 code/state——拒绝（CSRF 防御）")
        if not hmac.compare_digest(state, cookie_state):
            raise HTTPException(status_code=400, detail="state 与发起时不匹配——拒绝（CSRF 防御）")
        if auth.verify_token(cookie_state, lc.cfg.session_secret) is None:
            raise HTTPException(status_code=400, detail="state 过期或非法——拒绝")
        redirect_uri = str(request.base_url).rstrip("/") + "/auth/callback"
        try:
            mm_user_id, username = auth.exchange_code(lc.cfg, code, redirect_uri)
        except auth.AuthError as e:
            raise HTTPException(status_code=502, detail=str(e)) from None
        session = auth.sign_token(
            {"mm_user_id": mm_user_id, "username": username}, lc.cfg.session_secret, ttl_s=lc.cfg.session_ttl_s,
        )
        resp = RedirectResponse("/auth/me", status_code=302)  # 纯 API 层：落 me 即会话生效证明
        resp.set_cookie(
            auth.SESSION_COOKIE, session, max_age=lc.cfg.session_ttl_s,
            httponly=True, samesite="lax",
        )
        resp.delete_cookie(auth.STATE_COOKIE)
        return resp

    @app.get("/auth/me")
    def auth_me(request: Request) -> dict:
        _require_configured()
        ident = _session_identity(request)
        if ident is None:
            raise HTTPException(status_code=401, detail="无有效会话")
        return ident

    @app.get("/auth/logout")
    def auth_logout() -> dict:
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(auth.SESSION_COOKIE)
        resp.delete_cookie(auth.STATE_COOKIE)
        return resp

    @app.put("/auth/credentials")
    def auth_credentials(body: CredentialsBody, request: Request) -> dict:
        _require_configured()
        ident = _session_identity(request)
        if ident is None:
            raise HTTPException(status_code=401, detail="无有效会话")
        if vault is None:
            raise HTTPException(status_code=503, detail="保险箱未配置（HUB_VAULT_MASTER_KEY）——无法录入")
        env = {
            "ANTHROPIC_BASE_URL": body.anthropic_base_url,
            "ANTHROPIC_AUTH_TOKEN": body.anthropic_auth_token,
            "GH_TOKEN": body.gh_token,
        }
        missing = [k for k, v in env.items() if not v]
        if missing:
            raise HTTPException(status_code=400, detail=f"缺必需字段：{', '.join(missing)}（原子不落部分记录）")
        if body.model:
            env["ANTHROPIC_MODEL"] = body.model
        try:
            vault.set_user(ident["mm_user_id"], env)
        except vault_store.VaultError as e:
            raise HTTPException(status_code=502, detail=f"保险箱写入失败：{e}") from None
        # 响应只含确认与字段名，绝不回显明文值（spec「会话用户凭据录入」）
        return {"ok": True, "user": ident["mm_user_id"], "fields": sorted(env)}

    # ── 既有三端点（POST /queue 会话优先，2b D5）────────────────────

    @app.post("/queue", status_code=201)
    def enqueue(body: EnqueueBody, request: Request) -> dict:
        ident = _session_identity(request)  # 会话可选（D7）：有则真实身份，无则 2a 兜底
        try:
            return validate_and_enqueue(
                engine, project=body.project, prd_path=body.prd_path,
                image_tag=body.image_tag,
                enqueued_by=(ident["mm_user_id"] if ident else body.enqueued_by) or lc.cfg.user_id,
                initiator=ident["username"] if ident else None,
            )
        except ApiInputError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None

    @app.get("/queue")
    def list_queue(status: str | None = None) -> list[dict]:
        try:
            return queue_view(database, status)
        except ApiInputError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None

    @app.get("/runs/{run_id}")
    def get_run(run_id: int) -> dict:
        detail = run_detail(database, run_id)
        if detail is None:
            raise HTTPException(status_code=404, detail=f"run 不存在：{run_id}")
        return detail

    return app
