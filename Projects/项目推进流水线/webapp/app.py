#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""app.py — PRD 注入面板（prd-injection-console spec；productize-pa-template §5）。

面板只做三件事，零编排逻辑（注入与 CLI 手动注入共享同一实现——subprocess 起
``run_daily.py --from-stage inject``，绝不在面板进程内重实现编排）：

  [1] POST /api/inject  只收 markdown 文件（拒绝命令/非文件载荷/非 .md）→ 落盘 state/inbox/
      → subprocess 触发 inject；锁占用（复用编排器 ``.run.lock`` 语义）→ 409 显式「进行中」，
      不排队不并发
  [2] GET /api/status   只读解析既有 ``dispatch_*.json`` 渲染当日终态 + PR 列表 + 运行中状态；
      零 verdict 产生或改写（机械/语义切分不变式）
  [3] GET /             一页三区静态页（webapp/static/index.html，零构建链零 node）

fail-open：子进程 start_new_session 脱离面板进程组——面板崩溃/被杀，进行中的 run 不受影响，
状态数据完整落盘 state 目录。localhost 安全边界：默认 bind 127.0.0.1 无鉴权（单人本机场景）；
``--host`` 显式覆盖远程绑定是用户自己的决定，默认值永不远程。

面板不 import 编排器模块（run_daily 顶层有环境检查副作用）——锁判定/常量本地复刻并注释对齐。
依赖：pip install -e ".[webapp]"（FastAPI/uvicorn/python-multipart；编排器核心零新依赖）。

用法：python3 webapp/app.py [--port 8321] [--host 127.0.0.1]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse

# ── 路径推导（与 run_daily.py 同层次：webapp/ 在 <pa> 直下，三层上到 vault 根）──
WEBAPP_DIR = Path(__file__).resolve().parent
PA_HOME = WEBAPP_DIR.parent                                   # .../项目推进流水线
VAULT_ROOT = PA_HOME.parent.parent                            # vault 根（Projects 的上两层）
RUN_DAILY = PA_HOME / "scripts" / "run_daily.py"

DEFAULT_HOST = "127.0.0.1"      # spec：默认必须 localhost，不提供远程绑定默认值
DEFAULT_PORT = 8321

# 与 run_daily.MAX_RUN_WALL 对齐的锁龄阈值（≈90min：DEV_LOOP_TIMEOUT+1800）。锁文件语义
# （{"pid", "started"}，pid 活 AND 未超龄 = 占用；失活/超龄 = 陈旧可接管）复刻自
# run_daily.acquire_run_lock——本模块不 import 编排器（顶层有 yaml 环境检查副作用）。
MAX_RUN_WALL = 3600 + 1800

# 上传落盘名白名单（防路径注入：只留字母数字点横下划线汉数字）
_SAFE_NAME = re.compile(r"[^\w.\-一-鿿]")
MAX_UPLOAD_BYTES = 2 * 1024 * 1024   # 2MB：一份 PRD markdown 的宽裕上限

# 面板内触发互斥（webapp↔webapp 双触发）；跨进程（cron↔webapp）由锁文件兜：
# run_daily 自身 acquire 失败即拒启（exit 2），任何路径都不会并发第二个 run。
_current_run: subprocess.Popen | None = None


def run_lock_held(state_dir: Path) -> dict | None:
    """编排器 ``.run.lock`` 占用判定（复刻 run_daily.acquire_run_lock 的陈旧语义）。

    返回占用信息 dict（pid/started/age）或 None（未占用/陈旧可接管）。
    读失败 → 视占用（fail-safe：宁可误报进行中，绝不并发启动）。"""
    lock = state_dir / ".run.lock"
    try:
        info = json.loads(lock.read_text(encoding="utf-8"))
        pid = int(info.get("pid", 0))
        started = info.get("started", "")
    except FileNotFoundError:
        return None
    except Exception:
        return {"pid": None, "started": "?", "note": "锁文件不可解析（fail-safe 视占用；确信无 run 可删锁文件）"}
    if pid:
        try:
            os.kill(pid, 0)                 # 探活：ProcessLookupError = pid 失活
        except OSError:
            return None                     # 陈旧（PID 失活）→ 不视占用（run_daily 会接管）
    age = 0.0
    if started:
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(started)).total_seconds()
        except ValueError:
            age = MAX_RUN_WALL + 1          # 解析不了 → 当超龄（对齐 run_daily：stale_age=True）
    if age > MAX_RUN_WALL:
        return None                         # 陈旧（锁龄超限）→ run_daily 会接管
    return {"pid": pid or None, "started": started, "age_seconds": round(age)}


def _now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _safe_upload_name(name: str) -> str:
    base = Path(name or "prd.md").name                     # 只取 basename，剥任何路径成分
    return _SAFE_NAME.sub("_", base) or "prd.md"


def _latest_dispatch(state_dir: Path) -> Path | None:
    """最新 dispatch_<stamp>.json（stamp 字典序 = 时间序；无则 None）。"""
    files = sorted(state_dir.glob("dispatch_*.json"), reverse=True)
    return files[0] if files else None


def _parse_dispatch(path: Path) -> list[dict]:
    """只读解析 dispatch 记录 → 渲染行（字段白名单：只取展示所需，绝不回写）。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [{"project": "?", "slug": path.name, "status": "unreadable",
                 "pr_url": None, "note": f"解析失败：{exc}"}]
    recs = data if isinstance(data, list) else data.get("records") or data.get("items") or [data]
    rows = []
    for r in recs if isinstance(recs, list) else []:
        rows.append({
            "project": r.get("project"), "slug": r.get("slug"), "status": r.get("status"),
            "pr_url": r.get("pr_url"), "branch": r.get("branch"),
            "verify_verdict": r.get("verify_verdict"), "skip_reason": r.get("skip_reason"),
        })
    return rows


def create_app(*, state_dir: Path | None = None, pipeline_cmd: list[str] | None = None) -> FastAPI:
    """应用工厂。``state_dir`` / ``pipeline_cmd`` 为测试注入点（缺省=真实推导）。"""
    sdir = Path(state_dir) if state_dir else VAULT_ROOT / ".project-auto" / "state"
    cmd: list[str] = pipeline_cmd if pipeline_cmd else [sys.executable, str(RUN_DAILY)]
    inbox = sdir / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    app = FastAPI(title="pa 注入面板", docs_url=None, redoc_url=None)

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(WEBAPP_DIR / "static" / "index.html")

    @app.post("/api/inject")
    async def inject(file: UploadFile = File(...)) -> JSONResponse:
        global _current_run
        # 只收 markdown 文件：文件名后缀白名单（载荷内容是同事的手写 PRD，非命令非参数）
        name = _safe_upload_name(file.filename or "")
        if not name.lower().endswith((".md", ".markdown")):
            raise HTTPException(400, "只接受 markdown 文件（.md/.markdown）")
        # 面板内互斥：本面板已起的 run 仍活 → 显式进行中（不排队不并发第二个编排器进程）
        if _current_run is not None and _current_run.poll() is None:
            return JSONResponse({"status": "running", "note": "本面板已触发一次注入，run 进行中"},
                                status_code=409)
        # 跨进程互斥：编排器锁占用（cron 正跑/陈旧未清）→ 显式进行中，不 spawn
        held = run_lock_held(sdir)
        if held is not None:
            return JSONResponse({"status": "running", **held,
                                 "note": "编排器 run 进行中（run 锁占用），本次不受理"}, status_code=409)
        body = await file.read()
        if len(body) > MAX_UPLOAD_BYTES:
            raise HTTPException(400, f"文件超上限 {MAX_UPLOAD_BYTES} 字节")
        if not body.strip():
            raise HTTPException(400, "空文件——需要一份手写 PRD markdown（YAML frontmatter + 正文）")
        stamp = _now_stamp()
        dest = inbox / f"{stamp}_{name}"
        dest.write_text(body.decode("utf-8", errors="strict"), encoding="utf-8")
        # 与 CLI 手动注入共享同一实现：subprocess 起 run_daily --from-stage inject（cwd=vault 根）
        log_path = sdir / "runs" / f"webapp_{stamp}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        run_cmd = [*cmd, "--from-stage", "inject", "--inject-prd", str(dest)]
        with open(log_path, "wb") as lf:
            proc = subprocess.Popen(run_cmd, cwd=str(VAULT_ROOT), stdout=lf, stderr=lf,
                                    stdin=subprocess.DEVNULL, start_new_session=True)
        _current_run = proc
        return {"status": "accepted", "stamp": stamp, "prd": str(dest), "log": str(log_path),
                "pid": proc.pid}

    @app.get("/api/status")
    def status() -> dict:
        held = run_lock_held(sdir)
        disp = _latest_dispatch(sdir)
        rows = _parse_dispatch(disp) if disp else []
        today = datetime.now().strftime("%Y%m%d")
        return {
            "running": held is not None,
            "lock": held,
            # 「当日」优先：最新文件的 stamp 前缀（YYYYMMDD）== 今天；否则取最新并标 stale
            "dispatch_file": disp.name if disp else None,
            "is_today": bool(disp and disp.stem.split("_")[-1].startswith(today)),
            "records": rows,
            "prs": [r["pr_url"] for r in rows if r.get("pr_url")],
            "counts": {s: sum(1 for r in rows if r.get("status") == s)
                       for s in sorted({r.get("status") for r in rows if r.get("status")})},
        }

    return app


app = create_app()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="pa 注入面板（默认 127.0.0.1，localhost 单人场景）")
    parser.add_argument("--host", default=DEFAULT_HOST,
                        help=f"监听地址（默认 {DEFAULT_HOST}——远程绑定是显式决定，默认永不远程）")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
