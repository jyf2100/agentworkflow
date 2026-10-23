"""hub 面板渲染——服务端渲染纯 HTML（add-hub-slice-4b，spec: hub-panel）。

D1 形态：render 函数族 + f-string 拼 HTML，零新依赖（否决 Jinja2/前端构建链——
面板页数少、hub 最小依赖哲学不动）。XSS 防线机械唯一：一切动态值过 `_esc`
（html.escape）——渲染层无第二个转义入口。样式一段内联 <style>，无外部资源。

页面（均会话守卫，api.py 挂端点）：
- 提交页（GET /panel）：project 选择 + PRD 内容粘贴 + evidence 必填
- 队列页（GET /panel/queue）：队列项列表
- run 列表页（GET /panel/runs）/ run 详情页（GET /panel/runs/{id}）：终态事实
  + journal 尾部原样展示 + 报告渲染（机械展示零语义判定——spec「零语义渲染」）
"""
from __future__ import annotations

from html import escape
from typing import Any

_STYLE = """
body{font-family:system-ui,sans-serif;margin:0;background:#111;color:#e8e8e8}
main{max-width:880px;margin:2rem auto;padding:0 1rem}
a{color:#7ab8ff} h1{font-size:1.3rem} h2{font-size:1.05rem;margin-top:1.6rem}
table{border-collapse:collapse;width:100%;margin:.6rem 0}
td,th{border:1px solid #333;padding:.35rem .55rem;text-align:left;font-size:.88rem}
.ok{color:#6c6}.bad{color:#e66}.warn{color:#db6}
textarea,input,select{width:100%;box-sizing:border-box;background:#1c1c1c;color:#e8e8e8;
border:1px solid #444;border-radius:4px;padding:.45rem;font-family:inherit;font-size:.92rem}
textarea{min-height:12rem}
button{background:#2d5aa8;color:#fff;border:0;border-radius:4px;padding:.55rem 1.4rem;
font-size:.95rem;cursor:pointer;margin-top:.7rem}
.error{border-left:3px solid #e66;padding:.5rem .8rem;background:#2a1a1a;margin:.8rem 0}
pre{background:#1a1a1a;border:1px solid #333;padding:.7rem;overflow-x:auto;font-size:.8rem}
 nav a{margin-right:1rem}
"""


def _esc(v: Any) -> str:
    """唯一转义入口——一切动态值必经此处（XSS 防线机械唯一）。"""
    return escape(str(v if v is not None else ""))


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang=\"zh\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{_esc(title)} · pa-hub</title><style>{_STYLE}</style></head>"
        "<body><main>"
        "<nav><a href=\"/panel\">提交 PRD</a><a href=\"/panel/queue\">队列</a>"
        "<a href=\"/panel/runs\">runs</a><a href=\"/auth/logout\">登出</a></nav>"
        f"{body}</main></body></html>"
    )


def _status_class(status: str) -> str:
    return "ok" if status in ("done", "succeeded") else ("bad" if status in ("failed", "failed_to_start") else "warn")


def render_submit(projects: list[str], *, error: str | None = None, success: str | None = None, current_user: str = "") -> str:
    """提交页。error = 上次提交的拒绝原因（evidence 空等）回显；success = 入队回执。"""
    opts = "".join(f'<option value="{_esc(p)}">{_esc(p)}</option>' for p in projects) or "<option value=\"\">（无可用 project——先在服务器建必读面）</option>"
    err = f'<div class="error">{_esc(error)}</div>' if error else ""
    ok = f'<div class="ok">{_esc(success)}</div>' if success else ""
    body = (
        f"<h1>提交手写 PRD</h1><p>已登录：{_esc(current_user)}</p>{ok}{err}"
        "<form method=\"post\" action=\"/panel/prd\">"
        "<h2>project</h2>"
        f"<select name=\"project\" required>{opts}</select>"
        "<h2>PRD 内容（markdown，粘贴全文）</h2>"
        "<textarea name=\"prd_content\" required placeholder=\"# 目标…&#10;## 验收标准…\"></textarea>"
        "<h2>evidence（必填——链接或出处说明，空依据在闸前被拒）</h2>"
        "<input name=\"evidence\" required placeholder=\"https://… 或 出处说明\">"
        "<button type=\"submit\">提交入队</button></form>"
    )
    return _page("提交 PRD", body)


def render_queue(rows: list[dict]) -> str:
    trs = "".join(
        f"<tr><td>#{r['id']}</td><td>{_esc(r['project'])}</td>"
        f"<td class=\"{_status_class(r['status'])}\">{_esc(r['status'])}</td>"
        f"<td>{r['attempt']}</td><td>{_esc(r.get('initiator') or r.get('enqueued_by'))}</td>"
        f"<td>{_esc(r['last_error'] or '')}</td></tr>"
        for r in rows
    )
    body = (
        f"<h1>队列（{len(rows)} 项）</h1>"
        "<table><tr><th>id</th><th>project</th><th>status</th><th>attempt</th>"
        f"<th>发起人</th><th>last_error</th></tr>{trs}</table>"
    )
    return _page("队列", body)


def render_runs(runs: list[dict]) -> str:
    trs = "".join(
        f"<tr><td><a href=\"/panel/runs/{r['id']}\">#{r['id']}</a></td>"
        f"<td>{_esc(r['project'])}</td>"
        f"<td class=\"{_status_class(r['status'])}\">{_esc(r['status'])}</td>"
        f"<td>{'' if r['exit_code'] is None else r['exit_code']}</td>"
        f"<td>{_esc(r['started_at'])}</td><td>{_esc(r['ended_at'] or '')}</td></tr>"
        for r in runs
    )
    body = (
        f"<h1>runs（{len(runs)}）</h1>"
        "<table><tr><th>run</th><th>project</th><th>status</th><th>exit</th>"
        f"<th>started</th><th>ended</th></tr>{trs}</table>"
    )
    return _page("runs", body)


def render_run_detail(detail: dict, journal_tail: list[str], report_md: str | None) -> str:
    """run 详情：终态事实表 + journal 尾部原样 + 报告原文（均机械展示）。"""
    ec = "" if detail["exit_code"] is None else detail["exit_code"]
    rows = "".join(
        f"<tr><td>{_esc(k)}</td><td>{_esc(v if v is not None else '')}</td></tr>"
        for k, v in (
            ("run", f"#{detail['id']}"), ("project", detail["project"]),
            ("status", detail["status"]), ("exit_code", ec),
            ("run_dir", detail["run_dir"]), ("started_at", detail["started_at"]),
            ("ended_at", detail["ended_at"]), ("last_error", detail.get("last_error")),
        )
    )
    tail = "".join(f"{_esc(line)}\n" for line in journal_tail) or "（无 journal）"
    rep = f"<pre>{_esc(report_md)}</pre>" if report_md is not None else "<p>无报告</p>"
    body = (
        f"<h1>run #{detail['id']}</h1><table>{rows}</table>"
        f"<h2>journal 尾部（原样）</h2><pre>{tail}</pre>"
        f"<h2>报告</h2>{rep}"
    )
    return _page(f"run #{detail['id']}", body)


def render_503() -> str:
    """auth 未配置态面板整体 503（面板以会话为前提）。"""
    return _page("面板不可用", "<h1>面板不可用</h1><p>hub 未配置 Mattermost OAuth（auth 未配置态）——面板以会话为前提。REST 面与 cron 投递链不受影响。</p>")
