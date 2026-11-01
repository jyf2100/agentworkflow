"""主动观察者——目标仓机械信号 → 影子提案（add-proactive-observer，spec: proactive-observer）。

本体（change design 本体论声明）：ShadowProposal = 未经请求的**提议记录**（观察面产物——非
Trigger 非 PRD 非告警）；主动工作与被动工作授权方向相反（先观察后**请求**授权）。信号 = 仓的
状态事实（CI 结果序列 / issue 速率），与 event-sources 的 Trigger 范畴互斥（actor 是不是人）。

纪律：判据全机械零 LLM；三态 UNKNOWN 绝不提案（提错误比不提案更糟——信任流失）；提案仅日报
聚合**永不即时推送**（「不需要时保持安静」）；mode: auto v1 降级 observe + warn（防静默漂移）；
挂点经 daemon 直调（不进 lifecycle——纠缠文件零改动）。幂等 = 文件名 hash(repo|signal|streak 锚)。
"""
from __future__ import annotations

import hashlib
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

# 判据缺省（profile proactive.rules 可覆盖；键名固定 fail loud）
DEFAULT_RULES = {"ci_red_streak": 2, "issue_surge_ratio": 3.0, "issue_surge_min": 3}
_RULE_KEYS = frozenset(DEFAULT_RULES)

_HTTP_TIMEOUT = 15
_RUNS_PAGE = 10   # T1 取一页 10 条——连败段锚定窗口（streak 增长锚不变）
_EVENTS_PAGE = 100


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sp8(repo: str, signal: str, anchor: str) -> str:
    return "sp-" + hashlib.sha256(f"{repo}|{signal}|{anchor}".encode()).hexdigest()[:8]


def _get(url: str, token: str | None):
    """GitHub API GET（Bearer；event_poller 同模式）。返回 (json | None, None) 或 (None, None)=UNKNOWN。"""
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8", "replace")), None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None, None


def load_proactive_declarations(data_root: Path) -> dict[str, dict]:
    """全项目 proactive 声明：{project: {mode, watch_repos, rules}}。

    profiles/<name>.yaml 顶层 proactive 块（pa load_profiles 只读 admission/name——零影响，
    实证 run_daily.py:262-269）。容错：无块/坏 yaml = 零声明；规则键名错 = fail loud（配置错
    装载期爆，不静默用缺省）。mode: auto → 降级 observe + warn（v1 未实装，防「配了以为在跑」）。
    """
    out: dict[str, dict] = {}
    if not data_root.is_dir():
        return out
    for proj_dir in sorted(data_root.iterdir()):
        pdir = proj_dir / "profiles"
        if not pdir.is_dir():
            continue
        for pf in sorted(pdir.glob("*.yaml")):
            try:
                prof = yaml.safe_load(pf.read_text(encoding="utf-8")) or {}
            except (yaml.YAMLError, OSError):
                continue
            decl = prof.get("proactive") if isinstance(prof, dict) else None
            if not isinstance(decl, dict):
                continue
            rules = {**DEFAULT_RULES, **{k: v for k, v in (decl.get("rules") or {}).items()}}
            bad = [k for k in rules if k not in _RULE_KEYS]
            if bad:
                print(f"[observer][ERROR] {proj_dir.name} proactive.rules 未知键 {bad}"
                      f"（合法：{sorted(_RULE_KEYS)}）——fail loud 不猜，修正后生效", file=sys.stderr)
                continue
            mode = str(decl.get("mode") or "observe")
            if mode != "observe":
                print(f"[observer][warn] {proj_dir.name} proactive.mode={mode!r} 未实装"
                      f"（后续 change：预算闸+置信门+全链 critic）——降级 observe", file=sys.stderr)
                mode = "observe"
            repos = [str(r).strip() for r in (decl.get("watch_repos") or []) if str(r).strip()]
            if repos:
                out[proj_dir.name] = {"mode": mode, "watch_repos": repos, "rules": rules}
    return out


def _check_ci_red(repo: str, token: str | None, streak_n: int, branch_cache: dict) -> tuple[str, str] | None:
    """T1 默认分支 CI 连红：返回 (信号锚, 证据) 或 None。

    锚 = 连败段最老 run id（streak 增长锚不变 → 同 streak 一条提案；恢复后再红新 streak 新提案）。
    无 workflows（404/空）/ 任何拉取失败 = 无信号（UNKNOWN 不提案）。
    """
    meta, _ = _get(f"https://api.github.com/repos/{repo}", token)
    if not isinstance(meta, dict) or not meta.get("default_branch"):
        return None
    branch = str(meta["default_branch"])
    branch_cache[repo] = branch
    data, _ = _get(f"https://api.github.com/repos/{repo}/actions/runs"
                   f"?branch={branch}&per_page={_RUNS_PAGE}", token)
    runs = data.get("workflow_runs") if isinstance(data, dict) else None
    if not isinstance(runs, list) or not runs:
        return None  # 无 CI / UNKNOWN——均无信号
    streak: list[dict] = []
    for r in runs:  # 端点默认新→旧
        if r.get("status") == "completed" and r.get("conclusion") == "failure":
            streak.append(r)
        else:
            break
    if len(streak) < streak_n:
        return None
    oldest = streak[-1]  # 连败段最老（锚——streak 2→10 不变）
    ev = " ".join(f"{r['id']}({r.get('conclusion')})" for r in streak[:3])
    return f"{oldest['id']}", f"branch={branch} 连败 {len(streak)} 次：{ev}{'…' if len(streak) > 3 else ''}"


def _check_issue_surge(repo: str, token: str | None, ratio: float, minimum: int) -> tuple[str, str] | None:
    """T2 issue 激增：24h opened 数 > 7d 日均×ratio 且 ≥ minimum。锚 = 窗口日（同日一条）。"""
    data, _ = _get(f"https://api.github.com/repos/{repo}/events?per_page={_EVENTS_PAGE}", token)
    if not isinstance(data, list):
        return None
    now = _now()
    c24 = c7d = 0
    titles: list[str] = []
    for e in data:
        if not isinstance(e, dict) or e.get("type") != "IssuesEvent":
            continue
        p = e.get("payload") or {}
        if p.get("action") != "opened":
            continue
        created = e.get("created_at") or ""
        try:
            ts = datetime.fromisoformat(created.replace("Z", "+00:00"))
        except ValueError:
            continue
        if now - ts <= timedelta(hours=24):
            c24 += 1
            t = (p.get("issue") or {}).get("title")
            if t:
                titles.append(str(t)[:40])
        if now - ts <= timedelta(days=7):
            c7d += 1
    baseline = c7d / 7.0
    if c24 > baseline * ratio and c24 >= minimum:
        return now.strftime("%Y%m%d"), (f"24h opened {c24} > 7d 日均 {baseline:.1f}×{ratio}"
                                       f"（绝对 {c24}≥{minimum}）：{'; '.join(titles[:3])}")
    return None


def poll_proactive_signals(cfg, *, client=None) -> int:
    """单轮观察（daemon 每 observer_ticks 调一次）：逐项目逐仓判 T1/T2 → ShadowProposal 落盘。

    返回本轮新提案数。逐仓吞异常不连坐；UNKNOWN 零提案；同锚文件已存在即跳过（幂等）。
    """
    token = cfg.gh_token
    branch_cache: dict = {}
    written = 0
    for project, decl in load_proactive_declarations(cfg.data_root).items():
        for repo in decl["watch_repos"]:
            try:
                hits: list[tuple[str, str, str]] = []  # (signal, anchor, evidence)
                r = decl["rules"]
                t1 = _check_ci_red(repo, token, int(r["ci_red_streak"]), branch_cache)
                if t1:
                    hits.append(("ci_red_streak", t1[0], t1[1]))
                t2 = _check_issue_surge(repo, token, float(r["issue_surge_ratio"]), int(r["issue_surge_min"]))
                if t2:
                    hits.append(("issue_surge", t2[0], t2[1]))
                out_dir = cfg.data_root / project / "shadow_proposals"
                for signal, anchor, evidence in hits:
                    sp = _sp8(repo, signal, anchor)
                    path = out_dir / f"{_now().strftime('%Y%m%d')}_{sp}.md"
                    if path.exists():
                        continue
                    out_dir.mkdir(parents=True, exist_ok=True)
                    proposed = ("重跑失败的 workflow 并修复默认分支 CI" if signal == "ci_red_streak"
                                else "聚类阅读激增 issues，识别共性根因并拟修复 PRD")
                    path.write_text(
                        f"---\nproject: \"{project}\"\nsource: shadow-proposal\nid: \"{sp}\"\n"
                        f"repo: \"{repo}\"\nsignal: \"{signal}\"\nsignal_window: \"{anchor}\"\n"
                        f"evidence: \"{evidence[:200]}\"\nproposed_action: \"{proposed}\"\n"
                        f"policy_version: \"observe-v1\"\nrepo_fact_candidate: true\n"
                        f"created_at: \"{_now().isoformat(timespec='seconds')}\"\n---\n\n"
                        f"## 证据（机械拼接）\n\n{evidence}\n\n"
                        f"## 拟议动作（模板，未经语义评估）\n\n{proposed}\n\n"
                        f"采纳方式：`/pa run <需求> ` evidence 填 `shadow-proposal:{sp}`。\n",
                        encoding="utf-8")
                    written += 1
            except Exception as e:  # noqa: BLE001——单仓不连坐
                print(f"[observer][warn] {project}/{repo} 观察异常（跳过本轮）：{type(e).__name__}: {e}",
                      file=sys.stderr)
    if written:
        print(f"[observer] 本轮落盘 {written} 条影子提案（observe——仅日报聚合，不进执行链）",
              file=sys.stderr)
    return written
