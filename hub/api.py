"""hub 最小 API 面——FastAPI 三端点（add-hub-skeleton 任务 8.1，spec: execution-hub「最小 API 面」）。

仅机械面：入队（校验+固化 tag）/队列视图/run 详情。无语义判定面（审 PRD、判
价值归 pa persona——hub 不做）。共享内部函数（validate_and_enqueue /
queue_view / run_detail）同时是 cli.py 的数据源（8.2：同一函数，两种皮）。
"""
from __future__ import annotations

import queue_engine
from lifecycle import PROJECT_RE
from fastapi import FastAPI, HTTPException
from pathlib import Path
from pydantic import BaseModel

import db


class ApiInputError(ValueError):
    """入队输入非法——端点转 400，cli 转非零退出（fail fast 不留脏记录）。"""


class EnqueueBody(BaseModel):
    project: str
    prd_path: str
    image_tag: str | None = None
    enqueued_by: str | None = None  # 缺省由端点兜底 cfg.user_id（B4）——无第三身份字面量


def validate_and_enqueue(
    engine: queue_engine.QueueEngine,
    *,
    project: str,
    prd_path: str,
    image_tag: str | None = None,
    enqueued_by: str,
) -> dict:
    """入队校验 + 固化 tag——api 与 cli 共用（F13：缺省取引擎配置默认）。"""
    if not PROJECT_RE.fullmatch(project):
        raise ApiInputError(f"project 名非法（须匹配 {PROJECT_RE.pattern}）：{project!r}")
    if not Path(prd_path).is_file():
        raise ApiInputError(f"PRD 文件不存在：{prd_path}")
    item_id = engine.enqueue(project, prd_path, enqueued_by, image_tag=image_tag)
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


def create_app(database: db.HubDB, engine: queue_engine.QueueEngine, lc) -> FastAPI:
    app = FastAPI(title="pa-hub", version="0.1.0")

    @app.post("/queue", status_code=201)
    def enqueue(body: EnqueueBody) -> dict:
        try:
            return validate_and_enqueue(
                engine, project=body.project, prd_path=body.prd_path,
                image_tag=body.image_tag,
                enqueued_by=body.enqueued_by or lc.cfg.user_id,  # B4：单用户形态唯一 initiator
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
