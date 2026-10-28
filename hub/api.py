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
import json
import secrets
import sqlite3
import sys
import threading
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import auth
import daemon
import mm_intake
import panel
import prd_store
import projects
import queue_engine
from lifecycle import PROJECT_RE
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
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


class DeliverBody(BaseModel):
    """slice-3 D1：PRD 正文内嵌投递（vault→hub 机机通道）。sha256 是声明值，
    hub 落盘前校验 sha256(content)==声明值（防传输损坏 + 防幂等键撒谎）。"""
    project: str
    filename: str
    sha256: str
    content: str


_DELIVER_MAX_BYTES = 1024 * 1024  # body 正文上限 1MB（超限 400 确定性拒绝）
_DELIVER_ENQUEUED_BY = "vault-deliver"  # 机机通道来源字面量（队列面可辨识，非人身份）
_PANEL_EVIDENCE_MAX = 2048  # 面板 evidence 上限（frontmatter 内单行，2026-10-23 修复新增）


def _wrap_panel_prd(project: str, prd_content: str, evidence: str) -> str:
    """面板 PRD frontmatter 包装（2026-10-23 首跑 #4 实证修复）：裸 markdown 会被容器内
    pa stage_inject 的 frontmatter.project 校验确定性拒绝（run_daily.py inject 段）。
    hub 在落盘点机械补 frontmatter——纯字符串拼接零语义判定：

    - project：已过 PROJECT_RE 白名单的值，双引号 scalar；
    - source_path：空串（手写注入无源是 pa 预期态，inject 仅 warn；**不伪造路径**——
      source_path 在 pa 侧被 critic「Read 信息源」与 dispatch --source 当路径消费）；
    - evidence：面板收的依据（此前「收了又扔」），落盘可审计、正文批评可读；单行化 +
      双引号转义，超 _PANEL_EVIDENCE_MAX 抛 ValueError（端点转 422）。
    包装后全文即落盘与 sha256 的对象——retry 复用同文件天然幂等。"""
    ev = " ".join(evidence.split())  # 压成单行（换行/制表 → 空格，杜绝 frontmatter 逃逸）
    if len(ev.encode("utf-8")) > _PANEL_EVIDENCE_MAX:
        raise ValueError(f"evidence 超 {_PANEL_EVIDENCE_MAX} 字节上限（压缩单行后）")
    esc = ev.replace("\\", "\\\\").replace('"', '\\"')
    front = f"project: \"{project}\"\nsource_path: ''\nevidence: \"{esc}\"\n"
    return f"---\n{front}---\n\n{prd_content.lstrip()}"


class CredentialsBody(BaseModel):
    """模块级（不能进 create_app 闭包）：`from __future__ import annotations` 下
    FastAPI 按模块全局解析注解字符串，闭包内定义会退化成 query 参数（422 实证）。

    add-forge-adapter 3.3：平台 token 三键全可选（留空 = 保留既有键值，gitlab/gitcode-only
    用户不被迫造假 GH_TOKEN）；必填校验在 endpoint 内按 merge 后记录判。"""
    anthropic_base_url: str
    anthropic_auth_token: str
    gh_token: str = ""
    gitlab_token: str = ""
    gitcode_token: str = ""
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
    mm_client=None,
) -> FastAPI:
    """tick_interval=None = 被动 API（切片 1 形态零变化）；非 None = startup 挂排度常驻线程。

    vault（2b）：凭据录入端点的写入通道（None = 未配 master key，录入端点 503）。
    auth 配置从 lc.cfg 读（可选面：未配置 = auth 群 503，其余端点零影响）。
    mm_client（add-mm-channel-intake）：MM 客户端（bot token + base_url 齐时
    serve 传入，与 Notifier 共享实例）；None = 成员判据恒 UNKNOWN（入站 fail-closed
    拒绝——判据不可得绝不旁路）+ bind 探活不可用回执。
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

    def _require_session(request: Request) -> None:
        """读面守卫（add-hub-slice-4a D1，F2 收紧）：auth 已配置态强制有效会话；
        未配置回退态跳过检查（2a 单用户形态零变化——守卫强度跟随 auth 配置面）。"""
        if auth.mm_configured(lc.cfg) and _session_identity(request) is None:
            raise HTTPException(status_code=401, detail="读面须有效会话（auth 已配置态）——先 /auth/authorize 登录")

    def _require_admin(request: Request) -> dict:
        """改行为操作守卫（add-hub-panel-console D4）：有效会话 + HUB_PANEL_ADMINS
        白名单（空 = 任何有效会话放行，单用户回退态）。返回会话身份。"""
        _require_configured()
        ident = _session_identity(request)
        if ident is None:
            raise HTTPException(status_code=401, detail="面板操作须有效会话——先 /auth/authorize 登录")
        if lc.cfg.panel_admins and ident["username"] not in lc.cfg.panel_admins:
            raise HTTPException(status_code=403, detail=f"面板操作白名单外（HUB_PANEL_ADMINS 未含 {ident['username']}）")
        return ident

    def _abandon_queue_item(item_id: int, reason: str) -> dict:
        """清队列原语（slice-3 2.5；add-hub-panel-console D3 两皮一芯）：仅 pending 可清。
        清除 = 转终态 failed + last_error 记因（行保留审计，非物理删除）。"""
        try:
            item = engine.get_item(item_id)
        except db.DBError:
            raise HTTPException(status_code=404, detail=f"queue_item 不存在：{item_id}") from None
        if item["status"] != "pending":
            hint = "——先 drain（等 running 终态或停容器；「清 pending」不覆盖 running）" if item["status"] == "running" else ""
            raise HTTPException(status_code=409, detail=f"仅 pending 可清（当前 {item['status']}）{hint}")
        engine.fail_item(item_id, reason)
        return engine.get_item(item_id)

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
        resp = RedirectResponse("/panel", status_code=302)  # 浏览器来处回浏览器去处：授权完直接落面板（/auth/me 仍是 API 验证端点）
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
        # add-forge-adapter 3.3：留空 = 保留既有键值（非 replace 抹除）——读旧记录 merge。
        # 这是 get_user_env 的第三个读取点（派发 / vault-import / 本人自录 merge）：
        # 按 user 读自己、只写回，不构成「列出全部明文」入口。
        try:
            old = vault.get_user_env(ident["mm_user_id"]) or {}
        except vault_store.VaultError as e:
            raise HTTPException(status_code=502, detail=f"保险箱读取失败（留空保留需读旧记录）：{e}") from None
        env = dict(old)  # 旧记录打底：model + 平台 token 留空即原样保留
        for k, v in {
            "ANTHROPIC_BASE_URL": body.anthropic_base_url,
            "ANTHROPIC_AUTH_TOKEN": body.anthropic_auth_token,
            "GH_TOKEN": body.gh_token,
            "GITLAB_TOKEN": body.gitlab_token,
            "GITCODE_TOKEN": body.gitcode_token,
        }.items():
            if v:
                env[k] = v
        missing = [k for k in ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN") if not env.get(k)]
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"缺必需字段：{', '.join(missing)}（留空时按既有记录补全亦缺——原子不落部分记录）")
        if not any(env.get(k) for k in vault_store.FORGE_TOKEN_KEYS.values()):
            raise HTTPException(
                status_code=400,
                detail="缺平台 token：GH_TOKEN / GITLAB_TOKEN / GITCODE_TOKEN 至少录其一（dispatch 开 PR 必需）")
        bad = vault_store.placeholder_issues(env)
        if bad:
            # 录入口拦截（2026-10-23 首跑 #3）：占位符进保险箱跨重启持久，比进 env 更糟
            raise HTTPException(status_code=400, detail=f"凭据形态非法（疑似占位符）：{'；'.join(bad)}")
        if body.model:
            env["ANTHROPIC_MODEL"] = body.model
        try:
            vault.set_user(ident["mm_user_id"], env)
        except vault_store.VaultError as e:
            raise HTTPException(status_code=502, detail=f"保险箱写入失败：{e}") from None
        # 响应只含确认与字段名，绝不回显明文值（spec「会话用户凭据录入」）
        return {"ok": True, "user": ident["mm_user_id"], "fields": sorted(env)}

    # ── 面板端点群（add-hub-slice-4b，spec: hub-panel）─────────────────
    # 守卫口径：auth 未配置 = 整群 503（面板以会话为前提，对齐 auth 群）；
    # GET 未登录 302 到 authorize（浏览器友好）；POST 未登录 401（机器可判）。

    def _panel_projects() -> list[str]:
        """project 清单 = data_root 下有启动必读面的子目录（与派发 fail fast 同判据，
        lifecycle.py 启动必读面检查）——hub 不维护独立 project 注册表。判据本体在
        mm_intake.readable_projects（与 /pa bind 必读面校验两皮一芯）。"""
        return mm_intake.readable_projects(lc.cfg.data_root)

    def _panel_get(request: Request) -> HTMLResponse | RedirectResponse | None:
        """GET 面守卫：未配置 503（HTML）/ 未登录 302；已登录返回 None 由端点续渲染。"""
        if not auth.mm_configured(lc.cfg):
            return HTMLResponse(panel.render_503(), status_code=503)
        if _session_identity(request) is None:
            return RedirectResponse("/auth/authorize", status_code=302)
        return None

    @app.get("/panel")
    def panel_home(request: Request):
        guard = _panel_get(request)
        if guard is not None:
            return guard
        ident = _session_identity(request)
        return HTMLResponse(panel.render_submit(_panel_projects(), current_user=ident["username"]))

    @app.post("/panel/prd")
    async def panel_prd(request: Request):
        """提交闭环（spec scenario「会话提交闭环」）：会话身份 → evidence 机械必填
        （空 422 回显）→ 包装 frontmatter（_wrap_panel_prd——裸 md 必被容器内 pa inject
        拒，evidence 落 frontmatter.evidence 键可审计）→ 落盘（复用 store_prd，文件名
        stamp 前缀 + uuid hex8 唯一，sha = 包装后全文）→ 入队（enqueued_by = mm_user_id
        直发路径）。"""
        if not auth.mm_configured(lc.cfg):
            return HTMLResponse(panel.render_503(), status_code=503)
        ident = _session_identity(request)
        if ident is None:
            raise HTTPException(status_code=401, detail="面板提交须有效会话——先 /auth/authorize 登录")
        form = await request.form()  # urlencoded 由 Starlette 原生解析（零新依赖）
        project = str(form.get("project") or "")
        prd_content = str(form.get("prd_content") or "")
        evidence = str(form.get("evidence") or "")
        user = ident["username"]

        def _reject(msg: str) -> HTMLResponse:
            # fix-hub-panel-ux：422 回显回填已提交值（不丢输入，经 _esc）
            return HTMLResponse(
                panel.render_submit(_panel_projects(), error=msg, current_user=user,
                                    values={"project": project, "prd_content": prd_content, "evidence": evidence}),
                status_code=422,
            )

        if not evidence.strip():
            return _reject("evidence 必填（链接或出处说明）——空依据在闸前被拒")
        if not prd_content.strip():
            return _reject("PRD 内容不能为空")
        if not PROJECT_RE.fullmatch(project):
            return _reject(f"project 名非法（须匹配 {PROJECT_RE.pattern}）：{project!r}")
        if project not in _panel_projects():
            return _reject(f"project {project!r} 不在可提交清单（data_root 必读面扫描未命中）")
        if len(prd_content.encode("utf-8")) > _DELIVER_MAX_BYTES:
            return _reject("PRD 内容超 1MB 上限——拒绝")
        try:
            wrapped = _wrap_panel_prd(project, prd_content, evidence)  # 裸 md 必被 inject 拒——落盘即包装
        except ValueError as e:
            return _reject(str(e))

        filename = f"{datetime.now():%Y%m%d}_hand-{uuid4().hex[:8]}.md"
        try:
            target = prd_store.store_prd(
                lc.cfg.data_root, project, filename, wrapped,
                declared_sha=prd_store.sha256_text(wrapped), overwrite=False,
            )
        except prd_store.PrdStoreError as e:
            return _reject(f"落盘失败：{e}")
        item_id = engine.enqueue(project, str(target), ident["mm_user_id"], initiator=user)
        return HTMLResponse(
            panel.render_submit(
                _panel_projects(),
                success=f"已入队：队列项 #{item_id}（{project}）——状态见「队列」页",
                current_user=user,
            ),
            status_code=201,
        )

    @app.get("/panel/projects/new")
    def panel_project_new(request: Request):
        """建项目表单页（add-hub-panel-console D1）：GET 面守卫同其余面板页。"""
        guard = _panel_get(request)
        if guard is not None:
            return guard
        ident = _session_identity(request)
        return HTMLResponse(panel.render_project_new(current_user=ident["username"]))

    @app.post("/panel/projects")
    async def panel_project_create(request: Request):
        """建档（D1）：admin 守卫（D4）→ 机械落盘两文件（projects.create_project，
        零 YAML 库手写）→ 422 错误回显 / 201 成功回执。项目档案 = 运行时配置
        （真相源 data_root），面板写它不构成控制面越界（proposal 边界定性）。"""
        if not auth.mm_configured(lc.cfg):
            return HTMLResponse(panel.render_503(), status_code=503)
        ident = _require_admin(request)
        form = await request.form()
        name = str(form.get("name") or "").strip()
        repo = str(form.get("repo") or "").strip()
        goal = str(form.get("goal") or "").strip()
        surface = str(form.get("surface") or "").strip()
        forge = str(form.get("forge") or "").strip()
        gitlab_host = str(form.get("gitlab_host") or "").strip()

        def _reject(msg: str) -> HTMLResponse:
            # fix-hub-panel-ux：422 回显回填已提交值（不丢输入，经 _esc）
            return HTMLResponse(
                panel.render_project_new(error=msg, current_user=ident["username"],
                                         values={"name": name, "repo": repo, "goal": goal, "surface": surface,
                                                 "forge": forge, "gitlab_host": gitlab_host}),
                status_code=422,
            )

        if not name or not repo or not goal or not surface:
            return _reject("四字段（name/repo/goal/match_surface）均必填")
        try:
            projects.create_project(lc.cfg.data_root, name, repo, goal, surface,
                                    forge=forge, gitlab_host=gitlab_host)
        except projects.ProjectSetupError as e:
            return _reject(str(e))
        return HTMLResponse(
            panel.render_project_new(
                success=f"已建档：{name}", next_forge=forge, current_user=ident["username"]
            ),
            status_code=201,
        )

    @app.post("/panel/queue/{item_id}/retry")
    def panel_queue_retry(item_id: int, request: Request):
        """重跑（D2）：仅 failed 可重跑（防双跑/防重复投递）——以原 project + 原 PRD
        新建队列项，旧行留审计。enqueued_by 沿用原项（凭据跟入队人走——admin 代点
        重跑不换凭据源，2026-10-27 #52 凭据缺失修复），initiator 记当前操作者。
        PRD 文件不在则 404。"""
        ident = _require_admin(request)
        try:
            item = engine.get_item(item_id)
        except db.DBError:
            raise HTTPException(status_code=404, detail=f"queue_item 不存在：{item_id}") from None
        if item["status"] != "failed":
            raise HTTPException(
                status_code=409,
                detail=f"仅 failed 可重跑（当前 {item['status']}）——pending/running 防双跑，done 防重复投递",
            )
        if not Path(item["prd_path"]).is_file():
            raise HTTPException(status_code=404, detail=f"原 PRD 文件已不存在，无法重跑：{item['prd_path']}")
        new_id = engine.enqueue(item["project"], item["prd_path"], item["enqueued_by"], initiator=ident["username"])
        return HTMLResponse(
            panel.render_queue(database.list_items(), success=f"已重跑：新队列项 #{new_id}（原 #{item_id} 留审计）"),
            status_code=201,
        )

    @app.post("/panel/queue/{item_id}/abandon")
    def panel_queue_abandon(item_id: int, request: Request):
        """放弃（D3）：复用清队列原语 _abandon_queue_item——与 REST DELETE /queue/{id}
        同一语义同一实现（两皮一芯），仅 pending 可清、转 failed + last_error 记因。"""
        _require_admin(request)
        _abandon_queue_item(item_id, "面板人工放弃（add-hub-panel-console）")
        return HTMLResponse(
            panel.render_queue(database.list_items(), success=f"已放弃：#{item_id} → failed（行留审计）"),
        )

    @app.get("/panel/credentials")
    def panel_credentials(request: Request):
        """凭据录入页（2026-10-23）：PUT /auth/credentials 的面板 UI 补齐——会话人自录，
        值不过对话。状态只显键名不显值（与 PUT 响应同口径）。"""
        guard = _panel_get(request)
        if guard is not None:
            return guard
        ident = _session_identity(request)
        if vault is None:
            return HTMLResponse(panel.render_credentials(
                error="保险箱未配置（HUB_VAULT_MASTER_KEY）——无法录入",
                current_user=ident["username"]), status_code=503)
        try:
            rec = vault.get_user_env(ident["mm_user_id"])
        except vault_store.VaultError as e:
            rec = None
            _vault_err = str(e)
        else:
            _vault_err = None
        if _vault_err:
            return HTMLResponse(panel.render_credentials(
                error=f"保险箱读取失败：{_vault_err}", current_user=ident["username"]), status_code=502)
        # A-LOW 2026-10-23：有记录但缺必需键时降级提示——「已配置」≠可派发。
        # add-forge-adapter 3.3 口径：ANTHROPIC 两值必需；平台 token 按项目 forge 而定
        # （页面无项目上下文，只提示「三平台全缺」——任何项目都派不动）。
        _warn = None
        if rec:
            _missing = [k for k in ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN") if not rec.get(k)]
            if not _missing and not any(rec.get(k) for k in vault_store.FORGE_TOKEN_KEYS.values()):
                _missing = ["平台 token（GH/GITLAB/GITCODE 至少其一）"]
            if _missing:
                _warn = f"已配置但缺：{'、'.join(_missing)}——派发将按项目 forge 声明报缺凭据（补录即生效，留空字段保留）"
        return HTMLResponse(panel.render_credentials(
            configured_fields=(sorted(rec) if rec else None),
            warning=_warn,
            current_user=ident["username"]))

    @app.post("/panel/credentials")
    async def panel_credentials_save(request: Request):
        """凭据录入提交：与 PUT /auth/credentials 同校验口径（missing + 占位符拦截），
        差异只在错误呈现（面板回显表单 vs JSON 400）。成功响应只含字段名。"""
        if not auth.mm_configured(lc.cfg):
            return HTMLResponse(panel.render_503(), status_code=503)
        ident = _session_identity(request)
        if ident is None:
            raise HTTPException(status_code=401, detail="凭据录入须有效会话——先 /auth/authorize 登录")
        if vault is None:
            return HTMLResponse(panel.render_credentials(
                error="保险箱未配置（HUB_VAULT_MASTER_KEY）——无法录入",
                current_user=ident["username"]), status_code=503)
        form = await request.form()

        def _reject(msg: str, status_code: int = 422) -> HTMLResponse:
            # fix-hub-panel-ux：422 回显回填非密码框（token/password 框不回填——密码语义）
            return HTMLResponse(panel.render_credentials(
                error=msg, current_user=ident["username"],
                values={"anthropic_base_url": str(form.get("anthropic_base_url") or "").strip(),
                        "model": str(form.get("model") or "").strip()}), status_code=status_code)

        # add-forge-adapter 3.3：与 PUT /auth/credentials 同 merge 口径——token 框留空保留既有
        try:
            old = vault.get_user_env(ident["mm_user_id"]) or {}
        except vault_store.VaultError as e:
            return _reject(f"保险箱读取失败（留空保留需读旧记录）：{e}", status_code=502)
        env = dict(old)
        for k, v in {
            "ANTHROPIC_BASE_URL": str(form.get("anthropic_base_url") or "").strip(),
            "ANTHROPIC_AUTH_TOKEN": str(form.get("anthropic_auth_token") or "").strip(),
            "GH_TOKEN": str(form.get("gh_token") or "").strip(),
            "GITLAB_TOKEN": str(form.get("gitlab_token") or "").strip(),
            "GITCODE_TOKEN": str(form.get("gitcode_token") or "").strip(),
        }.items():
            if v:
                env[k] = v
        model = str(form.get("model") or "").strip()

        missing = [k for k in ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN") if not env.get(k)]
        if missing:
            return _reject(f"缺必需字段：{'、'.join(missing)}（留空时按既有记录补全亦缺——原子不落部分记录）")
        if not any(env.get(k) for k in vault_store.FORGE_TOKEN_KEYS.values()):
            return _reject("缺平台 token：GH_TOKEN / GITLAB_TOKEN / GITCODE_TOKEN 至少录其一（dispatch 开 PR 必需）")
        bad = vault_store.placeholder_issues(env)
        if bad:
            return _reject(f"凭据形态非法（疑似占位符）：{'；'.join(bad)}")
        if model:
            env["ANTHROPIC_MODEL"] = model
        try:
            vault.set_user(ident["mm_user_id"], env)
        except vault_store.VaultError as e:
            return _reject(f"保险箱写入失败：{e}", status_code=502)
        return HTMLResponse(panel.render_credentials(
            configured_fields=sorted(env),
            success=f"已保存 {len(env)} 个字段进保险箱（不回显值；留空字段保留既有）——派发即生效",
            current_user=ident["username"]))

    @app.get("/panel/queue")
    def panel_queue(request: Request):
        guard = _panel_get(request)
        if guard is not None:
            return guard
        return HTMLResponse(panel.render_queue(database.list_items()))

    @app.get("/panel/runs")
    def panel_runs(request: Request):
        guard = _panel_get(request)
        if guard is not None:
            return guard
        return HTMLResponse(panel.render_runs(database.list_all_runs()))

    @app.get("/panel/runs/{run_id}")
    def panel_run_detail(request: Request, run_id: int):
        guard = _panel_get(request)
        if guard is not None:
            return guard
        detail = run_detail(database, run_id)
        if detail is None:
            raise HTTPException(status_code=404, detail=f"run 不存在：{run_id}")
        qi = detail.pop("queue_item") or {}
        flat = {**detail, "last_error": qi.get("last_error")}  # last_error 在 queue_item 上不在 runs 表
        prd_path = qi.get("prd_path")
        return HTMLResponse(panel.render_run_detail(
            flat, _run_journal_tail(detail["run_dir"]), _run_report_md(detail),
            events_tail=_run_events_tail(detail["run_dir"], prd_path),
            verify_report=_run_verify_report(detail, prd_path)))

    def _run_journal_tail(run_dir: str, n: int = 30) -> list[str]:
        """journal 平铺伴生（lifecycle._journal_exists 同拼法）尾部 n 行原样读。"""
        jf = Path(run_dir).parent / f"{Path(run_dir).name}.journal.jsonl"
        if not jf.is_file():
            return []
        with jf.open(encoding="utf-8", errors="replace") as f:
            return [line.rstrip("\n") for line in deque(f, maxlen=n)]

    def _run_report_md(run: dict) -> str | None:
        """报告落卷读取（T2a）：host 侧 = data_root/<project>/state/reports/，文件名
        对齐 run stamp（run_dir 名前缀）。缺失 = None → 面板显「无报告」（非错误）。"""
        reports = lc.cfg.data_root / run["project"] / "state" / "reports"
        if not reports.is_dir():
            return None
        stamp = Path(run["run_dir"]).name.split("_", 1)[0]
        hits = sorted(reports.glob(f"项目推进报告_{stamp}.md")) or sorted(reports.glob(f"项目推进报告_{stamp}*.md"))
        if not hits:
            return None
        return hits[-1].read_text(encoding="utf-8", errors="replace")

    def _run_events_tail(run_dir: str, prd_path: str | None, n: int = 30) -> list[str]:
        """events.jsonl 尾部行原样读（fix-event-outlet-verify-parity 2.4）——与
        lifecycle 事件转发同拼法 `{stamp}_{prd stem}.events.jsonl`（prd stem 原样，
        hub run 目录横线化 slug 与之错位，寻址不得依赖目录名）。"""
        if not prd_path:
            return []
        stem = Path(prd_path).stem
        ev = Path(run_dir).parent / f"{Path(run_dir).name.split('_', 1)[0]}_{stem}.events.jsonl"
        if not ev.is_file():
            return []
        with ev.open(encoding="utf-8", errors="replace") as f:
            return [line.rstrip("\n") for line in deque(f, maxlen=n)]

    def _run_verify_report(run: dict, prd_path: str | None) -> dict | None:
        """verify-report 落卷读取（2.4）：pa 落点 state/runs/<proj>/<stamp>_<prd stem>
        .verify-report.json（与 events.jsonl 同拼法同 stem）。缺失/坏 JSON = None →
        面板显「无 verify-report」（非错误）。"""
        if not prd_path:
            return None
        stem = Path(prd_path).stem
        rd = Path(run["run_dir"])
        vr = rd.parent / f"{rd.name.split('_', 1)[0]}_{stem}.verify-report.json"
        if not vr.is_file():
            return None
        try:
            data = json.loads(vr.read_text(encoding="utf-8", errors="replace"))
        except (json.JSONDecodeError, OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    # ── MM 入站端点（add-mm-channel-intake D1/D6/D7，spec: hub-mm-intake）─────
    # 薄壳：token 恒时校验（未配 503 对齐 auth 群未配置回退态 / 不匹配 401）→
    # form 解析 → mm_intake.handle_command（全部语义面）→ ephemeral JSON 响应 +
    # 后置频道 post（fail-open——MM 对 slash 响应秒级超时，频道 post 拉长处理链
    # 会放大超时→重试面）。

    _member_cache = mm_intake.MemberCache()

    @app.post("/mm/intake")
    async def mm_intake_endpoint(request: Request) -> JSONResponse:
        tok = lc.cfg.mm_slash_token  # D6：不配 = 入站关闭零行为
        if tok is None:
            raise HTTPException(status_code=503, detail="入站未配置（HUB_MM_SLASH_TOKEN）——/pa slash command 关闭")
        form = await request.form()  # x-www-form-urlencoded 由 Starlette 原生解析
        req_token = str(form.get("token") or "")
        if not hmac.compare_digest(req_token, tok):
            raise HTTPException(status_code=401, detail="slash token 校验失败")
        result = mm_intake.handle_command(
            database=database, engine=engine, cfg=lc.cfg, mm=mm_client,
            form={k: str(v) for k, v in form.items()},
            member_cache=_member_cache,
        )
        channel_post = result.get("channel_post")
        if channel_post is not None:
            channel_id = str(form.get("channel_id") or "")
            try:
                mm_client.post_channel(channel_id, channel_post)  # 后置于响应构造前但 fail-open
            except Exception as e:  # noqa: BLE001——时间线 post 绝不拖垮 ephemeral 回执
                print(f"[mm-intake][warn] 频道时间线 post 异常（fail-open）: {type(e).__name__}", file=sys.stderr)
        return JSONResponse({"response_type": "ephemeral", "text": result["ephemeral"]})

    # ── 投递端点（slice-3 D1/D2/D6/D7，spec: prd-delivery「hub 幂等交接端点」）──

    def _deliver_receipt(item: dict, *, duplicate: bool) -> dict:
        return {
            "id": item["id"], "project": item["project"], "prd_path": item["prd_path"],
            "status": item["status"], "deliver_key": item["deliver_key"], "duplicate": duplicate,
        }

    def _store_or_repair(project: str, filename: str, content: str, sha: str, *, overwrite: bool) -> Path:
        try:
            return prd_store.store_prd(
                lc.cfg.data_root, project, filename, content, declared_sha=sha, overwrite=overwrite,
            )
        except prd_store.PrdStoreError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None

    @app.post("/deliver")
    def deliver(body: DeliverBody, request: Request) -> JSONResponse:
        """vault→hub PRD 交接（state ownership 交接协议）。file-first：先落盘（含 sha
        校验）再入队；db/文件双层幂等（重复投递回既有项 + 校验落盘完整并补写）；
        并发撞键 IntegrityError db 层兜底后回重复响应。响应 2xx 即 delivered 回执。"""
        tok = lc.cfg.deliver_token  # D7：不配 = 内网无守卫；配置则 Bearer 必查
        if tok is not None:
            scheme, _, value = request.headers.get("authorization", "").partition(" ")
            if scheme.lower() != "bearer" or not hmac.compare_digest(value, tok):
                raise HTTPException(status_code=401, detail="投递 token 缺失或错误")
        if len(body.content.encode("utf-8")) > _DELIVER_MAX_BYTES:
            raise HTTPException(status_code=400, detail="content 超 1MB 上限——拒绝")

        key = f"{body.project}:{body.filename}:{body.sha256}"
        existing = database.get_item_by_deliver_key(key)
        if existing is not None:
            # 幂等回执 + 落盘完整校验（缺失/异容补写回原位——防 db-first 崩溃窗口不可恢复态）
            target = prd_store.prd_dir(lc.cfg.data_root, body.project) / body.filename
            if not target.exists() or prd_store.sha256_text(target.read_text(encoding="utf-8")) != body.sha256:
                _store_or_repair(body.project, body.filename, body.content, body.sha256, overwrite=True)
            return JSONResponse(_deliver_receipt(existing, duplicate=True), status_code=200)

        target = _store_or_repair(body.project, body.filename, body.content, body.sha256, overwrite=False)
        try:
            item_id = engine.enqueue(
                body.project, str(target), _DELIVER_ENQUEUED_BY, deliver_key=key,
            )
        except sqlite3.IntegrityError:
            # 并发撞键（两请求同键竞态，先者已入队）——db 层兜底后回重复响应
            winner = database.get_item_by_deliver_key(key)
            if winner is None:
                raise HTTPException(status_code=500, detail="撞键后查无既有项——状态不一致") from None
            return JSONResponse(_deliver_receipt(winner, duplicate=True), status_code=200)
        return JSONResponse(_deliver_receipt(engine.get_item(item_id), duplicate=False), status_code=201)

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
    def list_queue(request: Request, status: str | None = None) -> list[dict]:
        _require_session(request)
        try:
            return queue_view(database, status)
        except ApiInputError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None

    @app.delete("/queue/{item_id}")
    def delete_queue_item(item_id: int, request: Request) -> dict:
        """清队列原语（slice-3 2.5，回退序列 RUNBOOK §15 ③）：仅 pending 可清。
        清除 = 转终态 failed + last_error 记回退原因（行保留审计，非物理删除）；
        running 拒绝并提示先 drain（「清 pending」不覆盖 running——双跑撞目标仓
        branch 命名空间）；done/failed 终态不清（回退序列只清 pending）。
        add-hub-panel-console D3：实现收敛 _abandon_queue_item（面板放弃同源）。"""
        _require_session(request)
        return _abandon_queue_item(item_id, "回退序列人工清除（RUNBOOK §15 ③——服务器投递回退）")

    @app.get("/runs/{run_id}")
    def get_run(run_id: int, request: Request) -> dict:
        _require_session(request)
        detail = run_detail(database, run_id)
        if detail is None:
            raise HTTPException(status_code=404, detail=f"run 不存在：{run_id}")
        return detail

    return app
