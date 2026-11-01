"""事件轮询接入——白名单仓 GitHub 事件 → 机械筛 → 语料增量文件（add-event-sources，spec: event-intake）。

设计（change design.md D2/D3）：事件即语料增量——hub 侧轮询器把过筛事件写成机械拼接的 md 文件
落 `data_root/<project>/events/`，项目 sources.yaml 声明 directory 源（root: .project-auto/events/）
即可被 fetch「今日新内容」date-marker 机制机械发现、radar 语义筛消费——**编排零改动、事件不短路
派发**（必须过 radar→PRD→critic 全链）。

Fast Path 全机械（零 LLM）：事件类型白名单 + 作者 ∈ events.authors 声明（项目侧配置的 GitHub
login 授权面——跨信道身份映射被显式拒绝，映射由配置人完成）。去重 = 文件名含 event id（存在即
跳过——幂等、重启安全、零 db 面）。gh 失败三态：UNKNOWN 本轮跳过记日志绝不当天断源。单 repo
异常不连坐；整体由 daemon 槽兜底（观察面纪律）。
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import yaml

# 事件类型白名单缺省（GitHub Events API 原生 type 值；声明可收窄）
DEFAULT_EVENT_TYPES = ("IssueCommentEvent", "IssuesEvent", "PullRequestEvent")

# 正文截断（机械摘要面——radar/critic 按需读原文经 url）
_BODY_LIMIT = 2000
_GH_TIMEOUT = 30


def load_events_declarations(data_root: Path) -> dict[str, list[dict]]:
    """全项目 events 声明装载：{project: [{repo, types, authors}, ...]}。

    声明位置 = 各项目 sources.yaml 顶层 ``events:`` 键（pa 侧 load_sources 只读
    ``data["sources"]``——顶层 events 键对 pa 零影响）。无键/无文件/坏形态 = 该项目
    零声明零行为（容错不崩——轮询是观察面）；authors 空 = 拒绝全部（零误报取向，
    一次性 warn 提示配置面）。
    """
    out: dict[str, list[dict]] = {}
    if not data_root.is_dir():
        return out
    for proj_dir in sorted(data_root.iterdir()):
        sf = proj_dir / "sources.yaml"
        if not sf.is_file():
            continue
        try:
            data = yaml.safe_load(sf.read_text(encoding="utf-8")) or {}
        except (yaml.YAMLError, OSError):
            continue
        decls = data.get("events") if isinstance(data, dict) else None
        if not isinstance(decls, list):
            continue
        clean: list[dict] = []
        for d in decls:
            if not isinstance(d, dict) or not d.get("repo"):
                continue
            repo = str(d["repo"]).strip()
            types = tuple(str(t) for t in (d.get("types") or DEFAULT_EVENT_TYPES))
            authors = tuple(str(a).strip() for a in (d.get("authors") or []) if str(a).strip())
            if not authors:
                print(f"[events][warn] {proj_dir.name} 声明 {repo} authors 为空——拒绝全部事件"
                      f"（零误报取向；配置 GitHub login 授权面后放行）", file=sys.stderr)
            clean.append({"repo": repo, "types": types, "authors": authors})
        if clean:
            out[proj_dir.name] = clean
    return out


def gh_events_client(token: str | None = None):
    """GitHub Events API 客户端闭包：GET api.github.com/repos/{repo}/events（Bearer token，
    hub thin 镜像无 gh CLI—— urllib 直连；公共端点无 token 也可用但限流 60/h）。

    返回 (events 列表 | None, None)——网络/限流/5xx/解析错全归 (None, None) = UNKNOWN
    （跳轮不误伤，下轮再查；绝不当天断源）。
    """
    req_headers = {"Accept": "application/vnd.github+json"}
    if token:
        req_headers["Authorization"] = f"Bearer {token}"

    def _call(repo: str) -> tuple[list[dict] | None, str | None]:
        url = f"https://api.github.com/repos/{repo}/events?per_page=30"
        req = urllib.request.Request(url, headers=req_headers)
        try:
            with urllib.request.urlopen(req, timeout=_GH_TIMEOUT) as resp:
                payload = json.loads(resp.read().decode("utf-8", "replace"))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            return None, None
        if not isinstance(payload, list):
            return None, None
        return [_norm_event(e) for e in payload if isinstance(e, dict)], None

    return _call


def _norm_event(e: dict) -> dict:
    """原始 API 事件 → 消费面投影（与旧 gh --jq 投影逐字段等价）：actor 拍平 login、
    payload 取 title/body/url/action 四机械字段（缺则 None）。"""
    p = e.get("payload") or {}
    return {
        "id": e.get("id"), "type": e.get("type"),
        "actor": (e.get("actor") or {}).get("login"),
        "created_at": e.get("created_at"),
        "payload": {
            "title": p.get("issue", {}).get("title") if isinstance(p.get("issue"), dict)
                else (p.get("pull_request", {}) or {}).get("title") if isinstance(p.get("pull_request"), dict) else None,
            "body": (p.get("comment", {}) or {}).get("body") if isinstance(p.get("comment"), dict)
                else (p.get("issue", {}) or {}).get("body") if isinstance(p.get("issue"), dict)
                else (p.get("pull_request", {}) or {}).get("body") if isinstance(p.get("pull_request"), dict) else None,
            "url": (p.get("comment", {}) or {}).get("html_url") if isinstance(p.get("comment"), dict)
                else (p.get("issue", {}) or {}).get("html_url") if isinstance(p.get("issue"), dict)
                else (p.get("pull_request", {}) or {}).get("html_url") if isinstance(p.get("pull_request"), dict) else None,
            "action": p.get("action"),
        },
    }


def _event_body(ev: dict) -> str:
    """事件正文机械拼接（零语义）：标题 + 动作 + 正文截断 + 原文链接。"""
    p = ev.get("payload") or {}
    title = str(p.get("title") or "").strip()
    body = str(p.get("body") or "").strip()[:_BODY_LIMIT]
    url = str(p.get("url") or "").strip()
    action = str(p.get("action") or "").strip()
    lines = [f"# [{ev.get('type')}] {title}" if title else f"# {ev.get('type')}"]
    if action:
        lines.append(f"动作：{action}")
    if body:
        lines.extend(["", body])
    if url:
        lines.extend(["", f"原文：{url}"])
    return "\n".join(lines)


def poll_events(cfg, client=None) -> int:
    """单轮轮询（daemon 每 event_ticks 调一次）：全声明项目逐 repo 拉取 → 过筛 → 落文件。

    返回本轮落盘事件数（观察面计数）。文件名 `{project}/events/{YYYYMMDD}_{event_id}.md`
    ——存在即跳过（幂等去重，重启安全）。单 repo 异常 warn 不连坐。
    """
    if client is None:
        client = gh_events_client(cfg.gh_token)
    written = 0
    for project, decls in load_events_declarations(cfg.data_root).items():
        for d in decls:
            try:
                events, _err = client(d["repo"])
            except Exception as e:  # noqa: BLE001——单 repo 不连坐
                print(f"[events][warn] {project}/{d['repo']} 拉取异常（跳过本轮）：{type(e).__name__}", file=sys.stderr)
                continue
            if events is None:
                print(f"[events] {project}/{d['repo']} 拉取 UNKNOWN（网络/限流），下轮再查", file=sys.stderr)
                continue
            out_dir = cfg.data_root / project / "events"
            for ev in events:
                if not isinstance(ev, dict):
                    continue
                if ev.get("type") not in d["types"]:
                    continue  # 类型白名单（机械）
                if not d["authors"] or ev.get("actor") not in d["authors"]:
                    continue  # 作者授权面（声明 GitHub login 集；空=拒绝全部）
                eid = str(ev.get("id") or "").strip()
                if not eid:
                    continue
                date = str(ev.get("created_at") or "")[:10].replace("-", "") or "undated"
                path = out_dir / f"{date}_{eid}.md"
                if path.exists():
                    continue  # 幂等去重
                out_dir.mkdir(parents=True, exist_ok=True)
                fm = (f"---\nproject: \"{project}\"\nsource: events\nrepo: \"{d['repo']}\"\n"
                      f"event_type: \"{ev.get('type')}\"\nactor: \"{ev.get('actor')}\"\n"
                      f"event_id: \"{eid}\"\ncreated_at: \"{ev.get('created_at')}\"\nurl: \"{(ev.get('payload') or {}).get('url') or ''}\"\n---\n\n")
                path.write_text(fm + _event_body(ev), encoding="utf-8")
                written += 1
    if written:
        print(f"[events] 本轮落盘 {written} 个事件文件（语料增量，待 fetch/radar 链路消费）", file=sys.stderr)
    return written
