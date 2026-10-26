#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""forge_fetch.py — forge 源机械预取 CLI（add-forge-adapter 4.2）。

三平台（github REST / gitlab v4 / gitcode v5）× commits/pulls/issues 拉取 → 客户端窗口
过滤（口径钉死：commits=author date、pulls/issues=updated_at）→ forge_api 归一（state
封闭枚举 open|merged|closed；GitHub issues 剔混排 PR、GitLab 剔 confidential——4.3 在
adapter 层内联）→ 每仓一个归一 markdown 落 --out-dir；stdout 一行 JSON
{"items":[...],"failed":[...]}。

设计边界（design.md D 组 / tasks 4.2-4.4）：
- 零 LLM、零 persona：纯机械预取；语义 summarize 归 pa-fetch-forge-repo（Read-only）。
- 预取产物绝不落 source.root（radar discover_today_new 扫 root 下 YYYYMMDD_*.md，raw
  会重复消费）——编排器传 --out-dir=STATE_DIR/forge_prefetch/<stamp>/。
- token narrow-inject：编排器只把该源 forge 对应的平台 token env 注入本子进程（其余平台
  token 不透传）；token 绝不进 argv/stdout/stderr（错误消息只含 env 键名）。
- gitcode 串行节流：HTTP 调用间隔缺省 1s（--throttle-ms 可配）；UNKNOWN（429/5xx/网络）
  退避重试一次（--backoff-ms 可配）；ForgeConfigError（401/403 等 permanent）不重试。
- 窗口时间由编排器机械算好写进 --src-json（since/now ISO，测试可钉死确定性）；缺省
  now=UTC 当前、since=now-7d（与 github-repo 缺省窗口对齐）。
- 退出码：0=跑完（failed 可含部分仓）；1=配置类错误或全部仓失败（stderr 净化消息）。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 同目录扁平 import（scripts/ 非 pip 包，pytest pythonpath=scripts 同口径）
sys.path.insert(0, str(Path(__file__).resolve().parent))
import forge_api  # noqa: E402

VALID_WATCH = ("commits", "pulls", "issues")


def _parse_iso(s: str | None) -> datetime | None:
    """ISO8601 → aware datetime（Z→+00:00；naive 视作 UTC）。坏值 → None。"""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _in_window(ts: str | None, since: datetime) -> bool:
    """窗口过滤（口径钉死：调用方保证传对的字段）。无/坏时间戳 → 保留（fail-open，
    radar 出口去重兜底；静默丢条不可取）。"""
    dt = _parse_iso(ts)
    return dt is None or dt >= since


def _slug_repo(repo: str) -> str:
    """仓路径 → 文件名安全段：g/sub/proj → g__sub__proj。斜杠 → ``__``（双下划线防碰撞：
    字面仓名 a_b 与路径 a/b 不落同名文件互覆）；其余非 [A-Za-z0-9._-] → ``_``。"""
    s = repo.strip("/").replace("/", "__")
    out = []
    for ch in s:
        out.append(ch if ch.isalnum() or ch in "._-" else "_")
    return "".join(out) or "repo"


class _Throttle:
    """串行节流：第一次调用不等待，其后每次 wait() 睡 interval（gitcode 限速面）。"""

    def __init__(self, interval_s: float):
        self.interval_s = interval_s
        self._first = True

    def wait(self) -> None:
        if self.interval_s <= 0 or self._first:
            self._first = False
            return
        time.sleep(self.interval_s)


def _fetch_with_retry(fn, throttle: _Throttle, backoff_s: float):
    """拉一维：UNKNOWN（429/5xx/网络）退避重试一次；FOUND/其余态直接返回。"""
    throttle.wait()
    res = fn()
    if res.state is forge_api.ExtState.UNKNOWN:
        time.sleep(backoff_s)
        throttle.wait()
        res = fn()
    return res


def _fmt_dt(iso: str | None) -> str:
    dt = _parse_iso(iso)
    return dt.strftime("%Y-%m-%d") if dt else (iso or "?")


def _render_repo_md(forge: str, repo: str, since: datetime, now: datetime,
                    commits: list, pulls: list, issues: list, watch: list[str],
                    truncated: list[str] | None = None) -> str:
    """归一 markdown（预取产物，persona 只读不改）。"""
    lines = [f"# {forge}: {repo}",
             f"窗口：{_fmt_dt(since.isoformat())} → {_fmt_dt(now.isoformat())}"
             "（commits 按 author date、PR/issue 按 updated_at）"]
    if truncated:   # 审核修复 M8：单页截断声明（拉满 per_page 上限，窗口内更早条目可能未取）
        lines.append("⚠ 截断：" + "；".join(truncated))
    lines.append("")
    if "commits" in watch:
        lines.append(f"## Commits（{len(commits)} 条）")
        for c in commits:
            lines.append(f"- `{c.get('sha') or '?'}` {_fmt_dt(c.get('date'))}"
                         f" {c.get('author') or '?'}：{c.get('message') or ''}")
        lines.append("")
    if "pulls" in watch:
        lines.append(f"## Pull/Merge Requests（{len(pulls)} 条）")
        for p in pulls:
            lines.append(f"- !{p.get('number') or '?'} [{p.get('state')}] {p.get('title')}"
                         f" — {p.get('author') or '?'}，updated {_fmt_dt(p.get('updated_at'))}"
                         f" — {p.get('url') or ''}")
        lines.append("")
    if "issues" in watch:
        lines.append(f"## Issues（{len(issues)} 条）")
        for i in issues:
            lines.append(f"- #{i.get('number') or '?'} [{i.get('state')}] {i.get('title')}"
                         f" — {i.get('author') or '?'}，updated {_fmt_dt(i.get('updated_at'))}"
                         f" — {i.get('url') or ''}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="forge-fetch", description="forge 源机械预取（零 LLM）")
    ap.add_argument("--src-json", required=True, help="源配置 JSON 路径（编排器写，含 forge/repos/watch/since/now）")
    ap.add_argument("--out-dir", required=True, help="预取产物目录（STATE_DIR/forge_prefetch/<stamp>/，绝不落 source.root）")
    ap.add_argument("--throttle-ms", type=int, default=1000, help="gitcode 串行节流间隔（缺省 1000；0=关）")
    ap.add_argument("--backoff-ms", type=int, default=2000, help="UNKNOWN 重试退避（缺省 2000；0=关）")
    args = ap.parse_args(argv)

    try:
        src = json.loads(Path(args.src_json).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"✗ src-json 不可读：{type(e).__name__}（路径/JSON 形态）", file=sys.stderr)
        return 1
    forge = str(src.get("forge") or "").strip().lower()
    if forge not in forge_api.VALID_FORGES:
        print(f"✗ 非法 forge 值：{src.get('forge')!r}（合法：{'/'.join(forge_api.VALID_FORGES)}）", file=sys.stderr)
        return 1
    if forge == forge_api.FORGE_GITLAB and not src.get("gitlab_host"):
        print("✗ gitlab 缺 gitlab_host（不猜，源配置显式声明）", file=sys.stderr)
        return 1
    repos = [str(r) for r in (src.get("repos") or []) if str(r).strip()]
    if not repos:
        print("✗ repos 为空（forge-repo 源无监控仓无意义）", file=sys.stderr)
        return 1
    watch = [str(w) for w in (src.get("watch") or ["commits", "pulls"])]
    bad_watch = [w for w in watch if w not in VALID_WATCH]
    if bad_watch:
        print(f"✗ watch 非法值：{bad_watch}（合法：{'/'.join(VALID_WATCH)}）", file=sys.stderr)
        return 1
    if "issues" in watch and forge == forge_api.FORGE_GITCODE:
        print("✗ gitcode 暂不支持 issues（API 字段快照 fixture 未锚定——tasks 5.2 人工 curl 验证后放开）",
              file=sys.stderr)
        return 1
    now = _parse_iso(src.get("now")) or datetime.now(timezone.utc)
    since = _parse_iso(src.get("since")) or now - timedelta(days=7)
    limit = int(src.get("limit") or 30)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    throttle = _Throttle(args.throttle_ms / 1000.0 if forge == forge_api.FORGE_GITCODE else 0.0)
    backoff_s = args.backoff_ms / 1000.0
    items: list[dict] = []
    failed: list[dict] = []
    for repo in repos:
        try:
            api = forge_api.for_fetch(forge, repo, host=src.get("gitlab_host"))
        except forge_api.ForgeConfigError as e:   # 缺 token/缺 host 等 permanent（消息只含 env 键名）
            failed.append({"repo": repo, "reason": str(e)[:160]})
            continue
        try:
            dims, errs, trunc = _collect_dims(api, watch, limit, since, throttle, backoff_s)
        except Exception as e:  # 预取面：按仓失败隔离（BroadCatch 有意），消息截断防泄漏
            failed.append({"repo": repo, "reason": f"{type(e).__name__}: {str(e)[:120]}"})
            continue
        if errs:
            failed.append({"repo": repo, "reason": "; ".join(errs)[:160]})
            continue
        commits, pulls, issues = dims.get("commits", []), dims.get("pulls", []), dims.get("issues", [])
        md = _render_repo_md(forge, repo, since, now, commits, pulls, issues, watch, trunc)
        # 审核修复 L2：文件名带 forge 前缀——两平台同路径仓（gitlab+gitcode 各有 g/x）不互覆
        path = out_dir / f"{forge}__{_slug_repo(repo)}.md"
        path.write_text(md, encoding="utf-8")
        item = {"repo": repo, "forge": forge, "file": path.name,
                "commits": len(commits), "pulls": len(pulls), "issues": len(issues)}
        if trunc:   # 审核修复 M8：截断声明透传 items（编排器/digest 消费面可见）
            item["truncated"] = trunc
        items.append(item)

    print(json.dumps({"items": items, "failed": failed}, ensure_ascii=False))
    if repos and not items:
        reason = failed[0]["reason"] if failed else "（未知）"
        print(f"✗ forge 预取全部仓失败（{forge}，{len(failed)}/{len(repos)} 仓）：{reason}", file=sys.stderr)
        return 1
    return 0


def _collect_dims(api, watch: list[str], limit: int, since: datetime,
                  throttle: _Throttle, backoff_s: float) -> tuple[dict[str, list], list[str], list[str]]:
    """按 watch 拉三维 → 窗口过滤（口径钉死：commits=author date / pulls+issues=updated_at）。

    返回 (dims, errs, truncated)；errs 非空 = 该仓失败（UNKNOWN 重试后仍不可用——fetch_* 拉满窗口
    是源的意义，缺维即失败不产半成品 md）；truncated = 拉满单页上限的维度清单（审核修复 M8：
    单页采集无翻页，返回条数达 per_page 上限时窗口内可能还有更早条目未取——声明不静默）。"""
    dims: dict[str, list] = {}
    truncated: list[str] = []
    page_cap = min(int(limit), 100)
    for dim in ("commits", "pulls", "issues"):
        if dim not in watch:
            continue
        fn = getattr(api, f"fetch_{dim}")
        res = _fetch_with_retry(lambda f=fn: f(limit), throttle, backoff_s)
        if res.state is not forge_api.ExtState.FOUND:
            # 审核修复 L9：源级失败登记用描述性文案，不借三态术语（三态只在 adapter 契约面）
            return {}, [res.reason or "原因缺失（该维拉取失败且无原因文本）"], []
        if len(res.value or []) >= page_cap:
            truncated.append(f"{dim}（单页上限 {page_cap} 条已拉满，窗口内更早条目可能未取）")
        ts_key = "date" if dim == "commits" else "updated_at"
        dims[dim] = [x for x in (res.value or []) if _in_window(x.get(ts_key), since)]
    return dims, [], truncated


if __name__ == "__main__":
    sys.exit(main())
