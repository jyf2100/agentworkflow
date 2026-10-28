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
textarea:focus,input:focus,select:focus{border-color:#7ab8ff;outline:1px solid #7ab8ff}
button{background:#2d5aa8;color:#fff;border:0;border-radius:4px;padding:.55rem 1.4rem;
font-size:.95rem;cursor:pointer;margin-top:.7rem}
button:hover{background:#3a6dc0}
button:focus-visible{outline:2px solid #7ab8ff;outline-offset:2px}
.error{border-left:3px solid #e66;padding:.5rem .8rem;background:#2a1a1a;margin:.8rem 0}
pre{background:#1a1a1a;border:1px solid #333;padding:.7rem;overflow-x:auto;font-size:.8rem}
pre.maxh{max-height:24rem;overflow:auto}
nav a{margin-right:1rem}
label{display:block;margin-top:1.1rem;font-weight:600;font-size:.92rem}
.hint{margin:.15rem 0 .3rem;color:#9a9a9a;font-size:.8rem}
.sym{font-weight:700;margin-right:.25rem}
td.err{max-width:16rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tablewrap{overflow-x:auto}
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
        "<a href=\"/panel/runs\">runs</a><a href=\"/panel/projects/new\">建项目</a>"
        "<a href=\"/panel/credentials\">凭据</a>"
        "<a href=\"/auth/logout\">登出</a></nav>"
        f"{body}</main></body></html>"
    )


def _status_class(status: str) -> str:
    return "ok" if status in ("done", "succeeded") else ("bad" if status in ("failed", "failed_to_start") else "warn")


def _status_sym(status: str) -> str:
    """状态符号通道（fix-hub-panel-ux D4）：与 _status_class 同范畴——✓/✗/… 与色并行，色觉障碍可辨。"""
    if status in ("done", "succeeded"):
        return "✓"
    if status in ("failed", "failed_to_start"):
        return "✗"
    return "…"


def _status_cell(status: str) -> str:
    return f'<td class="{_status_class(status)}"><span class="sym">{_status_sym(status)}</span>{_esc(status)}</td>'


def render_submit(projects: list[str], *, error: str | None = None, success: str | None = None,
                  current_user: str = "", values: dict[str, str] | None = None) -> str:
    """提交页。error = 上次提交的拒绝原因（evidence 空等）回显；success = 入队回执。
    values = 已提交值（fix-hub-panel-ux：422 回显不丢输入，服务端回填经 _esc）。"""
    v = values or {}
    opts = "".join(
        f'<option value="{_esc(p)}"{" selected" if p == v.get("project") else ""}>{_esc(p)}</option>'
        for p in projects
    ) or '<option value="">（无可用 project）</option>'
    empty_hint = ('<p class="hint">还没有项目？先去<a href="/panel/projects/new">建项目</a>——'
                  "github / gitlab / gitcode 三形态均可在界面自助建档</p>") if not projects else ""
    err = f'<div class="error">{_esc(error)}</div>' if error else ""
    ok = f'<div class="ok">{_esc(success)}</div>' if success else ""
    body = (
        f"<h1>提交手写 PRD</h1><p>已登录：{_esc(current_user)}</p>{ok}{err}"
        "<form method=\"post\" action=\"/panel/prd\">"
        '<label for="f-project">project</label>'
        f'<p class="hint">已建档项目的下拉（来自 data_root 必读面扫描）</p>{empty_hint}'
        f'<select name="project" id="f-project" required>{opts}</select>'
        '<label for="f-prd">PRD 内容</label>'
        '<p class="hint">markdown，粘贴全文</p>'
        f'<textarea name="prd_content" id="f-prd" required placeholder="# 目标…&#10;## 验收标准…">{_esc(v.get("prd_content", ""))}</textarea>'
        '<label for="f-evidence">evidence</label>'
        '<p class="hint">必填——链接或出处说明，空依据在闸前被拒</p>'
        f'<input name="evidence" id="f-evidence" required value="{_esc(v.get("evidence", ""))}" placeholder="https://… 或 出处说明">'
        '<button type="submit">提交入队</button></form>'
    )
    return _page("提交 PRD", body)


# 建项目成功后的下一步引导（fix-hub-panel-ux 收尾：HTML 在渲染层构造，api 只传 forge 形态 key——
# 动态值不进 success 文本，XSS 防线形态不变）
_NEXT_HINTS = {
    "gitlab": '下一步：去<a href="/panel/credentials">凭据页</a>配 GITLAB_TOKEN（PAT，api scope）——缺了派发前会被拒',
    "gitcode": '下一步：去<a href="/panel/credentials">凭据页</a>配 GITCODE_TOKEN——缺了派发前会被拒',
    "": '可直接去<a href="/panel">提交 PRD</a>（github 项目另需凭据页配 GH_TOKEN）',
}


def render_project_new(*, error: str | None = None, success: str | None = None,
                       current_user: str = "", values: dict[str, str] | None = None,
                       next_forge: str = "") -> str:
    """建项目表单页（add-hub-panel-console D1；fix-hub-form-parity 补 forge 三形态；
    fix-hub-panel-ux：label/hint 结构 + values 回填 + forge→gitlab_host 显隐联动 + 成功引导）。"""
    v = values or {}
    err = f'<div class="error">{_esc(error)}</div>' if error else ""
    if success:
        ok = f'<div class="ok">{_esc(success)}——{_NEXT_HINTS.get(next_forge, _NEXT_HINTS[""])}</div>'
    else:
        ok = ""
    forge_sel = v.get("forge", "")
    fsel = lambda val: " selected" if forge_sel == val else ""  # 紧凑三态 selected
    body = (
        f"<h1>建项目</h1><p>已登录：{_esc(current_user)}</p>{ok}{err}"
        "<form method=\"post\" action=\"/panel/projects\">"
        '<label for="f-name">项目名</label>'
        '<p class="hint">小写字母/数字/-/_（PRD frontmatter 的 project 值须等于它）</p>'
        f'<input name="name" id="f-name" required value="{_esc(v.get("name", ""))}" placeholder="demo">'
        '<label for="f-forge">目标仓形态（forge）</label>'
        '<p class="hint">决定 repo 填法与派发机制：github=本地 clone（缺省）；gitlab/gitcode=打 forge API，仓不须本地 clone</p>'
        f'<select name="forge" id="f-forge">'
        f'<option value=""{fsel("")}>github（缺省——repo 填 owner/repo 或 GitHub URL）</option>'
        f'<option value="gitlab"{fsel("gitlab")}>gitlab（自建实例——须填实例地址，repo 填全路径）</option>'
        f'<option value="gitcode"{fsel("gitcode")}>gitcode（SaaS——repo 填 owner/repo 两级）</option>'
        "</select>"
        '<div id="f-gitlab-host-box">'
        '<label for="f-gitlab-host">gitlab 实例地址</label>'
        '<p class="hint">仅 forge=gitlab 必填——带 scheme，可带端口；gitcode 无此键（固定 SaaS）</p>'
        f'<input name="gitlab_host" id="f-gitlab-host" value="{_esc(v.get("gitlab_host", ""))}" placeholder="http://172.32.153.183:8180">'
        "</div>"
        '<label for="f-repo">目标仓</label>'
        '<p class="hint">流水线在其中开分支干活——github：owner/repo 或 GitHub URL；gitlab：组/子组/仓 全路径（可多级）；gitcode：owner/repo 两级</p>'
        f'<input name="repo" id="f-repo" required value="{_esc(v.get("repo", ""))}" placeholder="owner/repo 或 组/仓">'
        '<label for="f-goal">goal</label>'
        '<p class="hint">一句话：这个项目要推进成什么</p>'
        f'<input name="goal" id="f-goal" required value="{_esc(v.get("goal", ""))}" placeholder="…">'
        '<label for="f-surface">match_surface</label>'
        '<p class="hint">一句话：什么信号归这个项目</p>'
        f'<input name="surface" id="f-surface" required value="{_esc(v.get("surface", ""))}" placeholder="…">'
        "<button type=\"submit\">建档</button></form>"
        # forge→gitlab_host 显隐联动（D2：内联原生脚本仅此一处、无外部资源；禁 JS 时框常显，hint 说明 + 服务端校验兜底）
        "<script>\n"
        "(function(){var s=document.getElementById('f-forge'),b=document.getElementById('f-gitlab-host-box');\n"
        "function sync(){b.style.display=s.value==='gitlab'?'':'none'}\n"
        "s.addEventListener('change',sync);sync();})();\n"
        "</script>"
    )
    return _page("建项目", body)


def render_credentials(*, configured_fields: list[str] | None = None,
                       warning: str | None = None,
                       error: str | None = None, success: str | None = None,
                       current_user: str = "", values: dict[str, str] | None = None) -> str:
    """凭据录入页（2026-10-23：PUT /auth/credentials 早已有，UI 补齐——值不过对话/不经手）。

    configured_fields = 当前 vault 记录的字段名列表（None = 未配置）——只显示键名，
    绝不回显值（与 PUT 响应同口径）。token 双框 type=password（浏览器不回显明文）。
    model 可选（ANTHROPIC_MODEL 模型路由，如 qwen3.8-27b-fast）。
    warning = 有记录但缺必需键时的降级提示（A-LOW 2026-10-23：「已配置」不等于可派发）。"""
    if configured_fields is None:
        status = '<p class="warn">当前：未配置（派发将明确报缺凭据）</p>'
    else:
        status = (f'<p class="ok">当前：已配置（字段：{_esc("、".join(configured_fields))}）'
                  "——重新提交覆盖（token 框留空 = 保留既有值）</p>")
    warn = f'<p class="warn">{_esc(warning)}</p>' if warning else ""
    err = f'<div class="error">{_esc(error)}</div>' if error else ""
    ok = f'<div class="ok">{_esc(success)}</div>' if success else ""
    v = values or {}
    body = (
        f"<h1>凭据</h1><p>已登录：{_esc(current_user)}</p>{status}{warn}{ok}{err}"
        "<form method=\"post\" action=\"/panel/credentials\">"
        '<label for="f-base-url">ANTHROPIC_BASE_URL</label>'
        '<p class="hint">LiteLLM 代理地址，含 http:// 前缀</p>'
        f'<input name="anthropic_base_url" id="f-base-url" required value="{_esc(v.get("anthropic_base_url", ""))}" placeholder="http://172.32.0.1:9001">'
        '<label for="f-auth-token">ANTHROPIC_AUTH_TOKEN</label>'
        '<p class="hint">代理鉴权 token</p>'
        '<input name="anthropic_auth_token" id="f-auth-token" type="password" required placeholder="sk-…">'
        "<h2>平台 token（按项目 forge 按需）</h2>"
        '<p class="hint">三框至少其一非空；留空 = 保留既有值（密码语义，拒绝后不回填）</p>'
        '<label for="f-gh">GH_TOKEN（GitHub 项目）</label>'
        '<input name="gh_token" id="f-gh" type="password" placeholder="ghp_…（github 项目必填）">'
        '<label for="f-gitlab">GITLAB_TOKEN（forge: gitlab 项目）</label>'
        '<input name="gitlab_token" id="f-gitlab" type="password" placeholder="glpat-…（gitlab 项目必填）">'
        '<label for="f-gitcode">GITCODE_TOKEN（forge: gitcode 项目）</label>'
        '<input name="gitcode_token" id="f-gitcode" type="password" placeholder="（gitcode 项目必填）">'
        '<label for="f-model">模型</label>'
        '<p class="hint">可选（ANTHROPIC_MODEL 路由），默认走代理侧默认模型</p>'
        f'<input name="model" id="f-model" value="{_esc(v.get("model", ""))}" placeholder="如 qwen3.8-27b-fast（留空 = 保留既有）">'
        "<button type=\"submit\">保存进保险箱</button></form>"
    )
    return _page("凭据", body)


def _queue_action(row: dict) -> str:
    """行内操作（add-hub-panel-console D2/D3）：failed→重跑 / pending→放弃；其余空。"""
    if row["status"] == "failed":
        return (f"<form method=\"post\" action=\"/panel/queue/{row['id']}/retry\">"
                "<button type=\"submit\">重跑</button></form>")
    if row["status"] == "pending":
        return (f"<form method=\"post\" action=\"/panel/queue/{row['id']}/abandon\">"
                "<button type=\"submit\">放弃</button></form>")
    return ""


def _short_time(iso: str) -> str:
    """ISO → 「MM-DD HH:MM」机械截短（不改时区不改语义；title 悬停看全文）。"""
    s = iso or ""
    return s[5:16].replace("T", " ") if len(s) >= 16 else s


def render_queue(rows: list[dict], *, success: str | None = None) -> str:
    trs = "".join(
        f"<tr><td>#{r['id']}</td><td>{_esc(r['project'])}</td>"
        f"{_status_cell(r['status'])}"
        f"<td>{_queue_action(r)}</td>"
        f"<td>{r['attempt']}</td><td>{_esc(r.get('initiator') or r.get('enqueued_by'))}</td>"
        # fix-hub-queue-recency：入队/最后状态变化两列（截短显示，title 全文）；
        # 操作列紧邻 status——时间列加入不得把重跑/放弃按钮挤出首屏可视区
        f"<td title=\"{_esc(r.get('created_at') or '')}\">{_esc(_short_time(r.get('created_at') or ''))}</td>"
        f"<td title=\"{_esc(r.get('updated_at') or '')}\">{_esc(_short_time(r.get('updated_at') or ''))}</td>"
        f"<td class=\"err\" title=\"{_esc(r['last_error'] or '')}\">{_esc(r['last_error'] or '')}</td></tr>"
        for r in rows
    )
    ok = f'<div class="ok">{_esc(success)}</div>' if success else ""
    body = (
        f"<h1>队列（{len(rows)} 项）</h1>{ok}"
        '<div class="tablewrap"><table><tr><th>id</th><th>project</th><th>status</th><th>操作</th><th>attempt</th>'
        f"<th>发起人</th><th>入队</th><th>更新</th><th>last_error</th></tr>{trs}</table></div>"
    )
    return _page("队列", body)


def render_runs(runs: list[dict]) -> str:
    trs = "".join(
        f"<tr><td><a href=\"/panel/runs/{r['id']}\">#{r['id']}</a></td>"
        f"<td>{_esc(r['project'])}</td>"
        f"{_status_cell(r['status'])}"
        f"<td>{'' if r['exit_code'] is None else r['exit_code']}</td>"
        f"<td>{_esc(r['started_at'])}</td><td>{_esc(r['ended_at'] or '')}</td></tr>"
        for r in runs
    )
    body = (
        f"<h1>runs（{len(runs)}）</h1>"
        '<div class="tablewrap"><table><tr><th>run</th><th>project</th><th>status</th><th>exit</th>'
        f"<th>started</th><th>ended</th></tr>{trs}</table></div>"
    )
    return _page("runs", body)


def _verify_report_html(vr: dict) -> str:
    """verify-report 机械表格渲染（fix-event-outlet-verify-parity 2.4）——字段原样进表，
    零语义：判词（verdict/reason/invalid_reason）只来自文件本身，hub 不产结论。

    内层结构防御：该文件在 state 卷上、目标仓代码可写（dev 循环对目标仓零信任），
    坏内层形态（evidence 非 dict / violations 非 str 元素等）不打废整个 run 详情页——
    降级为原样转义全文展示（可见非静默），且一切插值必经 _esc（XSS 防线唯一入口）。"""
    try:
        run, cmds, anc = vr.get("run") or {}, vr.get("commands") or {}, vr.get("anchors") or {}
        ws, dg = anc.get("workspace_clean") or {}, anc.get("injected_digests") or {}
        envp = vr.get("env_parity") or {}
        evd = vr.get("evidence")
        evd = evd if isinstance(evd, dict) else {}
        facts = "".join(
            f"<tr><td>{_esc(k)}</td><td>{_esc(v)}</td></tr>"
            for k, v in (
                ("verdict", vr.get("verdict")),
                ("invalid_reason", vr.get("invalid_reason")),
                ("reason", vr.get("reason")),
                ("commands.source", cmds.get("source")),
                ("gate_cmd", cmds.get("gate_cmd")),
                ("harness", run.get("harness")),
                ("stream_path", evd.get("stream_path")),
            ))
        cmd_rows = "".join(
            f"<tr><td>{_esc(r.get('declared'))}</td>"
            f"<td>{_esc(r.get('matched_command') or ('unresolved' if r.get('unresolved') else ''))}</td>"
            f"<td>{_esc(r.get('exit_code'))}</td></tr>"
            for r in cmds.get("results") or [])
        anchor_rows = "".join(
            f"<tr><td>{_esc(k)}</td><td>{_esc(v)}</td></tr>"
            for k, v in (
                ("workspace_clean.ok", ws.get("ok")),
                ("workspace_clean.detail", ws.get("detail")),
                ("injected_digests.ok", dg.get("ok")),
                ("injected_digests.violations",
                 "；".join(str(x) for x in (dg.get("violations") or []))),
                ("env_parity.declared", envp.get("declared")),
                ("env_parity.observed_exports", envp.get("observed_exports")),
                ("env_parity.note", envp.get("note")),
            ))
        return (
            f'<div class="tablewrap"><table><tr><th>判定</th><th>值</th></tr>{facts}</table></div>'
            '<div class="tablewrap"><table><tr><th>declared cmd</th><th>matched</th><th>exit</th></tr>'
            f"{cmd_rows}</table></div>"
            f'<div class="tablewrap"><table><tr><th>锚/环境</th><th>值</th></tr>{anchor_rows}</table></div>'
        )
    except Exception:  # 坏内层结构：降级原样转义全文（可见非静默），不打废整页
        return f'<pre class="maxh">{_esc(vr)}</pre>'


def render_run_detail(detail: dict, journal_tail: list[str], report_md: str | None,
                      events_tail: list[str] | None = None,
                      verify_report: dict | None = None) -> str:
    """run 详情：终态事实表 + journal/events 尾部原样 + verify-report 机械表格 + 报告原文。"""
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
    ev_tail = "".join(f"{_esc(line)}\n" for line in events_tail or []) or "（无 events）"
    vr_html = _verify_report_html(verify_report) if verify_report is not None else "<p>无 verify-report</p>"
    rep = f'<pre class="maxh">{_esc(report_md)}</pre>' if report_md is not None else "<p>无报告</p>"
    body = (
        f"<h1>run #{detail['id']}</h1><div class=\"tablewrap\"><table>{rows}</table></div>"
        f"<h2>journal 尾部（原样）</h2><pre class=\"maxh\">{tail}</pre>"
        f"<h2>events 尾部（原样）</h2><pre class=\"maxh\">{ev_tail}</pre>"
        f"<h2>verify-report</h2>{vr_html}"
        f"<h2>报告</h2>{rep}"
    )
    return _page(f"run #{detail['id']}", body)


def render_503() -> str:
    """auth 未配置态面板整体 503（面板以会话为前提）。"""
    return _page("面板不可用", "<h1>面板不可用</h1><p>hub 未配置 Mattermost OAuth（auth 未配置态）——面板以会话为前提。REST 面与 cron 投递链不受影响。</p>")
