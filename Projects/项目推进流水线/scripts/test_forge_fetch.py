#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_forge_fetch.py — add-forge-adapter 4.6：forge-repo 采集链单测。

覆盖 tasks 4.6 六个断言面：
    - 源装载分支（非法 forge / 缺 host / gitcode+issues 拒 / watch / repos / window；github-repo 原样）；
    - 预取归一（三平台 state → 封闭枚举；GitHub merged PR 判别；commit 两形态）；
    - 窗口口径（commits=author date / pulls+issues=updated_at 边界；缺/坏时间戳 fail-open 保留）；
    - 混入/confidential 过滤（GitHub issues 剔 pull_request 键条目；GitLab 剔 confidential）；
    - 节流/退避（_Throttle 首次免睡；UNKNOWN 退避重试一次）；
    - radar 消费形态（stage_fetch 落盘 produced entry 与既有 kind 同构 + report failed 渲染）。

不触真实网络/LLM：_RestApi._http monkeypatch（单测无真网络注入口）、persona/预取子进程全桩。
凭据纪律：token 一律假值（glpat-xxxx 等），断言输出不含 token 片段。AAA 结构。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import forge_api  # noqa: E402
import forge_fetch  # noqa: E402
import run_daily  # noqa: E402

STAMP = "20260721"
SINCE = "2026-10-15T00:00:00+00:00"   # 窗口钉死（确定性断言；窗外条目 fixture 用 10-01/10-02）
NOW = "2026-10-20T00:00:00+00:00"

# ─── fixture 数据（三平台 API 原始形态；窗口内 10-15~10-20 / 窗外 10-01~10-02）───
GL_COMMITS = [   # GitLab 扁平形态：authored_date（author date 口径）
    {"id": "abcdef1234567890", "author_name": "roc", "authored_date": "2026-10-18T10:00:00Z",
     "message": "feat: 新特性\n\n正文行不进首行"},
    {"id": "0123456789abcdef", "author_name": "jyf", "authored_date": "2026-10-01T10:00:00Z",
     "message": "窗口外 commit"},
]
GL_MRS = [       # state 原生 merged/opened；locked→open 保守
    {"iid": 12, "title": "MR12", "state": "merged", "updated_at": "2026-10-18T00:00:00Z",
     "web_url": "https://gl/MR12", "author": {"username": "roc"}},
    {"iid": 11, "title": "MR11 窗外", "state": "opened", "updated_at": "2026-10-02T00:00:00Z",
     "web_url": "https://gl/MR11", "author": {"username": "jyf"}},
    {"iid": 10, "title": "MR10 locked", "state": "locked", "updated_at": "2026-10-18T00:00:00Z",
     "web_url": "https://gl/MR10", "author": {"username": "k"}},
]
GL_ISSUES = [
    {"iid": 5, "title": "公开 issue", "state": "opened", "updated_at": "2026-10-19T00:00:00Z",
     "web_url": "https://gl/i5", "author": {"username": "roc"}},
    {"iid": 6, "title": "机密 issue", "state": "opened", "updated_at": "2026-10-19T00:00:00Z",
     "confidential": True, "web_url": "https://gl/i6", "author": {"username": "roc"}},
]
GH_COMMITS = [   # GitHub/GitCode nested 形态：commit.author.date（author date 口径）
    {"sha": "fedcba0987654321", "commit": {"author": {"name": "roc", "date": "2026-10-17T08:00:00Z"},
                                           "message": "fix: 修复\nbody"}},
]
GH_PULLS = [     # GitHub REST：state 只有 open|closed，merged 靠 merged_at 判别
    {"number": 9, "title": "已合并", "state": "closed", "merged_at": "2026-10-18T00:00:00Z",
     "updated_at": "2026-10-18T00:00:00Z", "html_url": "https://gh/pull/9", "user": {"login": "roc"}},
    {"number": 8, "title": "纯关闭", "state": "closed", "updated_at": "2026-10-18T00:00:00Z",
     "html_url": "https://gh/pull/8", "user": {"login": "roc"}},
]
GH_ISSUES = [    # issues 端点混排 PR：带 pull_request 键条目须剔
    {"number": 7, "title": "纯 issue", "state": "open", "updated_at": "2026-10-19T00:00:00Z",
     "html_url": "https://gh/i7", "user": {"login": "roc"}},
    {"number": 8, "title": "混入的 PR", "state": "open", "updated_at": "2026-10-19T00:00:00Z",
     "html_url": "https://gh/pull/8", "user": {"login": "roc"}, "pull_request": {"url": "https://gh/pull/8"}},
]


def _fake_http(routes: dict[str, tuple[int, object]]):
    """routes: path 子串 → (status, payload)。payload dict/list 自动 JSON 化。"""
    def fake(self, method, path, *, query=None, body=None):
        for key, (status, payload) in routes.items():
            if key in path:
                return status, json.dumps(payload, ensure_ascii=False)
        return 404, json.dumps({"message": "no route"})
    return fake


def _write_src_json(tmp_path: Path, **over) -> Path:
    body = {"forge": "gitlab", "gitlab_host": "gitlab.example.com", "repos": ["g/sub/proj"],
            "watch": ["commits", "pulls", "issues"], "since": SINCE, "now": NOW}
    body.update(over)
    f = tmp_path / "src.json"
    f.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    return f


def _write_sources(tmp_path: Path, body: str, monkeypatch) -> None:
    f = tmp_path / "sources.yaml"
    f.write_text(body, encoding="utf-8")
    monkeypatch.setattr(run_daily, "SOURCES_FILE", f)
    monkeypatch.setattr(run_daily, "PROJECT_DIR", tmp_path)


# ═══ ① 源装载分支（tasks 4.1 面）═══

@pytest.mark.parametrize("params_yaml,match", [
    ("{forge: gitee, repos: [a/b]}", "非法 forge 值"),
    ("{forge: gitlab, repos: [a/b]}", "缺 gitlab_host"),
    ("{forge: gitcode, repos: [a/b], watch: [commits, issues]}", "暂不支持"),
    ("{forge: gitlab, gitlab_host: h, repos: [a/b], watch: [commits, blogs]}", "watch 非法值"),
    ("{forge: gitlab, gitlab_host: h, repos: []}", "repos 为空"),
    ("{forge: gitlab, gitlab_host: h, repos: [a/b], window: 7w}", "window 形态非法"),
])
def test_load_sources_forge_repo_rejects_bad_config(tmp_path, monkeypatch, params_yaml, match):
    # Arrange：forge-repo 源带各类非法配置（cron 无人值守——装载期爆，不进 fetch 半路炸）
    _write_sources(tmp_path, f"sources:\n  - {{name: s1, root: r1, kind: forge-repo,"
                             f" params: {params_yaml}}}\n", monkeypatch)
    # Act + Assert：fail-fast
    with pytest.raises(SystemExit, match=match):
        run_daily.load_sources()


def test_load_sources_forge_repo_valid_and_github_repo_untouched(tmp_path, monkeypatch):
    # Arrange：合法 forge-repo（gitlab 全参含 issues 维）+ 既有 github-repo kind（无 forge 概念）
    _write_sources(tmp_path, """sources:
  - {name: s1, root: r1, kind: forge-repo,
     params: {forge: gitlab, gitlab_host: gl.example.com, repos: [g/s/p, g2/s2/p2],
              watch: [commits, pulls, issues], window: 12h}}
  - {name: s2, root: r2, kind: github-repo, params: {repos: [o/r]}}
""", monkeypatch)
    # Act
    out = run_daily.load_sources()
    # Assert：两源装载通过；github-repo kind 原样（零变更，不进 forge 校验）
    assert [s["name"] for s in out] == ["s1", "s2"]
    assert out[1]["kind"] == "github-repo"


# ═══ ② 归一封闭枚举（state → open|merged|closed；GitHub merged 判别；commit 两形态）═══

@pytest.mark.parametrize("raw,expected", [
    ({"state": "opened", "iid": 1, "title": "t"}, "open"),
    ({"state": "open", "number": 2, "title": "t"}, "open"),
    ({"state": "locked", "iid": 3, "title": "t"}, "open"),       # gitlab locked → open 保守（在途）
    ({"state": "merged", "iid": 4, "title": "t"}, "merged"),
    ({"state": "closed", "number": 5, "title": "t"}, "closed"),
    # 审核修复 M3：复用 _STATE_MAP 单表——rejected 归 closed（已驳回不在途）、progress 保守 open，
    # 此前 fetch 面内联表缺这两键（rejected 误标 open）
    ({"state": "rejected", "number": 7, "title": "t"}, "closed"),
    ({"state": "progress", "iid": 8, "title": "t"}, "open"),
    ({"state": "weird-future-state", "iid": 6, "title": "t"}, "open"),   # 未知 → open 保守
])
def test_norm_issue_like_maps_states_to_closed_enum(raw, expected):
    assert forge_api._norm_issue_like(raw, pr=False)["state"] == expected


def test_norm_issue_like_infers_merged_from_merged_at_for_github_prs():
    # GitHub REST /pulls 的 state 只有 open|closed——merged PR 靠 merged_at（或 merged:true）判别
    closed_merged = forge_api._norm_issue_like(
        {"state": "closed", "merged_at": "2026-10-18T00:00:00Z", "number": 9}, pr=True)
    closed_pure = forge_api._norm_issue_like({"state": "closed", "number": 8}, pr=True)
    bool_merged = forge_api._norm_issue_like({"state": "closed", "merged": True, "number": 7}, pr=True)
    # merged_at 判别只对 PR 生效；issue 面 closed 就是 closed
    issue_closed = forge_api._norm_issue_like(
        {"state": "closed", "merged_at": "2026-10-18T00:00:00Z", "number": 6}, pr=False)
    assert closed_merged["state"] == "merged"
    assert closed_pure["state"] == "closed"
    assert bool_merged["state"] == "merged"
    assert issue_closed["state"] == "closed"


def test_norm_commit_gitlab_flat_form():
    c = forge_api._norm_commit(GL_COMMITS[0], sha_key="id", nested=False)
    assert c == {"sha": "abcdef12", "date": "2026-10-18T10:00:00Z",
                 "author": "roc", "message": "feat: 新特性"}   # sha[:8] + message 首行


def test_norm_commit_nested_form():
    c = forge_api._norm_commit(GH_COMMITS[0], sha_key="sha", nested=True)
    assert c == {"sha": "fedcba09", "date": "2026-10-17T08:00:00Z",
                 "author": "roc", "message": "fix: 修复"}


# ═══ ③ 窗口口径（边界 + fail-open）═══

def test_in_window_boundary_and_fail_open():
    since = datetime(2026, 10, 15, tzinfo=timezone.utc)
    assert forge_fetch._in_window("2026-10-15T00:00:00Z", since) is True    # == since 边界保留
    assert forge_fetch._in_window("2026-10-14T23:59:59Z", since) is False   # 早一秒剔除
    assert forge_fetch._in_window(None, since) is True                      # 缺时间戳 → 保留（fail-open）
    assert forge_fetch._in_window("not-a-date", since) is True              # 坏时间戳 → 保留


# ═══ ④ 混入 / confidential 过滤（adapter 层内联，tasks 4.3）═══

def test_github_fetch_issues_strips_pr_mixins(monkeypatch):
    monkeypatch.setattr(forge_api._RestApi, "_http", _fake_http({"/issues": (200, GH_ISSUES)}))
    api = forge_api._GithubRestApi("o/r", token="ghp-test")
    res = api.fetch_issues(30)
    assert res.state is forge_api.ExtState.FOUND
    assert [i["number"] for i in res.value] == [7]        # 带 pull_request 键的 #8 被剔


def test_gitlab_fetch_issues_strips_confidential(monkeypatch):
    monkeypatch.setattr(forge_api._RestApi, "_http", _fake_http({"/issues": (200, GL_ISSUES)}))
    api = forge_api._GitlabApi("gl.example.com", "g/s/p", token="glpat-xxxx")
    res = api.fetch_issues(30)
    assert res.state is forge_api.ExtState.FOUND
    assert [i["number"] for i in res.value] == [5]        # confidential=true 的 #6 被剔


def test_github_fetch_pulls_marks_merged(monkeypatch):
    monkeypatch.setattr(forge_api._RestApi, "_http", _fake_http({"/pulls": (200, GH_PULLS)}))
    api = forge_api._GithubRestApi("o/r", token="ghp-test")
    res = api.fetch_pulls(30)
    states = {p["number"]: p["state"] for p in res.value}
    assert states == {9: "merged", 8: "closed"}


def test_for_fetch_missing_github_token_raises_permanent(monkeypatch):
    monkeypatch.delenv("GH_TOKEN", raising=False)
    with pytest.raises(forge_api.ForgeConfigError, match="GH_TOKEN"):
        forge_api.for_fetch("github", "o/r")


# ═══ ⑤ 节流 / 退避 ═══

def test_throttle_first_call_free_then_waits(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(forge_fetch.time, "sleep", lambda s: sleeps.append(s))
    t = forge_fetch._Throttle(1.5)
    t.wait(); t.wait(); t.wait()      # 紧凑写法有意（首次免睡 + 其后每次睡 interval）
    assert sleeps == [1.5, 1.5]


def test_throttle_zero_interval_never_sleeps(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(forge_fetch.time, "sleep", lambda s: sleeps.append(s))
    t = forge_fetch._Throttle(0.0)
    t.wait(); t.wait()
    assert sleeps == []


def test_fetch_with_retry_retries_once_on_unknown(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(forge_fetch.time, "sleep", lambda s: sleeps.append(s))
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        return forge_api.unknown("429") if calls["n"] == 1 else forge_api.found([{"x": 1}])

    res = forge_fetch._fetch_with_retry(flaky, forge_fetch._Throttle(0), 0.05)
    assert res.state is forge_api.ExtState.FOUND
    assert calls["n"] == 2                    # UNKNOWN → 退避重试恰好一次
    assert 0.05 in sleeps                     # 退避发生了（throttle interval=0 不掺和）


def test_fetch_with_retry_no_retry_on_found(monkeypatch):
    monkeypatch.setattr(forge_fetch.time, "sleep", lambda s: pytest.fail("FOUND 不应有任何 sleep"))
    calls = {"n": 0}

    def ok():
        calls["n"] += 1
        return forge_api.found([])

    res = forge_fetch._fetch_with_retry(ok, forge_fetch._Throttle(0), 0.05)
    assert res.state is forge_api.ExtState.FOUND
    assert calls["n"] == 1


# ═══ ⑥ forge_fetch.main 端到端（fake _http；零 LLM / 零真网络）═══

def test_main_gitlab_happy_path(tmp_path, monkeypatch, capsys):
    # Arrange：gitlab 三维，窗口钉死（10-01 commit / 10-02 MR 窗外）
    monkeypatch.setattr(forge_api._RestApi, "_http", _fake_http({
        "repository/commits": (200, GL_COMMITS), "merge_requests": (200, GL_MRS), "/issues": (200, GL_ISSUES)}))
    monkeypatch.setenv("GITLAB_TOKEN", "glpat-xxxx")
    out_dir = tmp_path / "pf"
    # Act
    rc = forge_fetch.main(["--src-json", str(_write_src_json(tmp_path)), "--out-dir", str(out_dir)])
    # Assert：md 落盘（斜杠→__ 防碰撞）+ 内容含窗口/归一结果
    md = out_dir / "gitlab__g__sub__proj.md"
    assert rc == 0 and md.is_file()
    txt = md.read_text(encoding="utf-8")
    assert "## Commits（1 条）" in txt            # 窗外 commit 被滤（author date 口径）
    assert "`abcdef12`" in txt
    assert "!12 [merged] MR12" in txt             # gitlab 原生 merged 直映射
    assert "!10 [open] MR10 locked" in txt        # locked → open 保守
    assert "MR11" not in txt                      # 窗外 MR 被滤（updated_at 口径）
    assert "机密 issue" not in txt and "公开 issue" in txt   # confidential 剔除
    # stdout 一行 JSON：items 计数（预取形态，编排器消费）
    data = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert data == {"items": [{"repo": "g/sub/proj", "forge": "gitlab", "file": "gitlab__g__sub__proj.md",
                               "commits": 1, "pulls": 2, "issues": 1}],
                    "failed": []}


def test_main_gitcode_applies_throttle(tmp_path, monkeypatch, capsys):
    # Arrange：gitcode 两维（watch 缺省 commits+pulls）+ throttle 50ms
    monkeypatch.setattr(forge_api._RestApi, "_http", _fake_http({
        "/commits": (200, GH_COMMITS), "/pulls": (200, [])}))
    monkeypatch.setenv("GITCODE_TOKEN", "gdc_live_yyyy")
    sleeps: list[float] = []
    monkeypatch.setattr(forge_fetch.time, "sleep", lambda s: sleeps.append(s))
    srcj = _write_src_json(tmp_path, forge="gitcode", gitlab_host=None, watch=["commits", "pulls"])
    # Act
    rc = forge_fetch.main(["--src-json", str(srcj), "--out-dir", str(tmp_path / "pf"),
                           "--throttle-ms", "50", "--backoff-ms", "0"])
    # Assert：rc=0 且第 2 次 HTTP 调用前有 0.05s 节流（gitcode 限速面）
    assert rc == 0
    assert 0.05 in sleeps


def test_main_all_repos_failed_returns_1(tmp_path, monkeypatch, capsys):
    # Arrange：404（列表端点 → UNKNOWN）重试后仍 UNKNOWN → 缺维即失败 → 全仓 failed
    monkeypatch.setattr(forge_api._RestApi, "_http", _fake_http({}))
    monkeypatch.setenv("GITLAB_TOKEN", "glpat-xxxx")
    # Act
    rc = forge_fetch.main(["--src-json", str(_write_src_json(tmp_path)),
                           "--out-dir", str(tmp_path / "pf"), "--backoff-ms", "0"])
    # Assert
    assert rc == 1
    captured = capsys.readouterr()
    assert "全部仓失败" in captured.err
    data = json.loads(captured.out.strip().splitlines()[-1])
    assert data["items"] == [] and data["failed"][0]["repo"] == "g/sub/proj"


def test_main_partial_repo_failure_rc0_exposes_failed_array(tmp_path, monkeypatch, capsys):
    # 审核修复 H1 契约锚：两仓一 404（列表端点 UNKNOWN 重试后仍不可用）一成功 → rc=0
    # （不整源丢）+ stdout JSON failed 数组透传（编排器 _forge_prefetch 消费它登记「预取部分仓失败」）
    monkeypatch.setattr(forge_api._RestApi, "_http", _fake_http({
        "proj/repository/commits": (200, GL_COMMITS), "proj/merge_requests": (200, GL_MRS),
        "proj/issues": (200, GL_ISSUES)}))   # 仓因子串锚路由：g/ghost 不含 proj/ 前缀 → 404 no route
    monkeypatch.setenv("GITLAB_TOKEN", "glpat-xxxx")
    srcj = _write_src_json(tmp_path, repos=["g/sub/proj", "g/ghost"])
    # Act
    rc = forge_fetch.main(["--src-json", str(srcj), "--out-dir", str(tmp_path / "pf"),
                           "--backoff-ms", "0"])
    # Assert：成功仓照常产 md + 失败仓进 failed 数组 + rc=0（部分失败不是源级失败）
    assert rc == 0
    assert (tmp_path / "pf" / "gitlab__g__sub__proj.md").is_file()
    data = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert [i["repo"] for i in data["items"]] == ["g/sub/proj"]
    assert [f["repo"] for f in data["failed"]] == ["g/ghost"]
    assert data["failed"][0]["reason"]   # 非空原因文本（L9：描述性文案非 "UNKNOWN" 字面）


def test_main_page_cap_truncation_declared(tmp_path, monkeypatch, capsys):
    # 审核修复 M8 契约锚：返回条数拉满单页上限（limit）→ md 头 + items 元数据双声明截断（不静默丢更早条目）
    monkeypatch.setattr(forge_api._RestApi, "_http", _fake_http({
        "proj/repository/commits": (200, GL_COMMITS)}))   # 恰 2 条（1 窗内 1 窗外）
    monkeypatch.setenv("GITLAB_TOKEN", "glpat-xxxx")
    srcj = _write_src_json(tmp_path, watch=["commits"], limit=2)   # cap=2 → 拉满
    rc = forge_fetch.main(["--src-json", str(srcj), "--out-dir", str(tmp_path / "tc"), "--backoff-ms", "0"])
    assert rc == 0
    md = (tmp_path / "tc" / "gitlab__g__sub__proj.md").read_text(encoding="utf-8")
    assert "⚠ 截断" in md and "commits（单页上限 2 条已拉满" in md
    data = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert data["items"][0]["truncated"] and "commits" in data["items"][0]["truncated"][0]
    # 对照：limit 高于返回条数（未拉满）→ 无截断声明
    srcj2 = _write_src_json(tmp_path, watch=["commits"], limit=5)
    rc2 = forge_fetch.main(["--src-json", str(srcj2), "--out-dir", str(tmp_path / "tc2"), "--backoff-ms", "0"])
    assert rc2 == 0
    md2 = (tmp_path / "tc2" / "gitlab__g__sub__proj.md").read_text(encoding="utf-8")
    assert "⚠ 截断" not in md2
    data2 = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert "truncated" not in data2["items"][0]


@pytest.mark.parametrize("over", [
    {"forge": "gitee"},
    {"forge": "gitcode", "gitlab_host": None, "watch": ["commits", "issues"]},
    {"repos": []},
])
def test_main_config_errors_return_1(tmp_path, monkeypatch, over, capsys):
    # Arrange/Act：配置类错误（非法 forge / gitcode+issues / 空 repos）不触网络
    monkeypatch.setattr(forge_api._RestApi, "_http",
                        lambda *a, **k: pytest.fail("配置错误不应触网络"))
    rc = forge_fetch.main(["--src-json", str(_write_src_json(tmp_path, **over)),
                           "--out-dir", str(tmp_path / "pf")])
    # Assert
    assert rc == 1
    assert "✗" in capsys.readouterr().err


def test_main_missing_token_clean_failure(tmp_path, monkeypatch, capsys):
    # Arrange：缺 GITLAB_TOKEN（for_fetch 构造期 ForgeConfigError，消息只含 env 键名）
    monkeypatch.delenv("GITLAB_TOKEN", raising=False)
    # Act
    rc = forge_fetch.main(["--src-json", str(_write_src_json(tmp_path)),
                           "--out-dir", str(tmp_path / "pf")])
    # Assert：失败登记干净——键名可见，无任何 token 值可泄漏
    captured = capsys.readouterr()
    assert rc == 1
    assert "GITLAB_TOKEN" in captured.out + captured.err
    data = json.loads(captured.out.strip().splitlines()[-1])
    assert data["items"] == [] and "GITLAB_TOKEN" in data["failed"][0]["reason"]


def test_main_wrong_token_never_leaks_value(tmp_path, monkeypatch, capsys):
    # Arrange：401 → ForgeConfigError（消息含 token_env_key，不含 token 值）
    monkeypatch.setattr(forge_api._RestApi, "_http", _fake_http({"repository/commits": (401, {})}))
    monkeypatch.setenv("GITLAB_TOKEN", "glpat-secret-doom-value")
    # Act
    rc = forge_fetch.main(["--src-json", str(_write_src_json(tmp_path)),
                           "--out-dir", str(tmp_path / "pf"), "--backoff-ms", "0"])
    # Assert：输出无 token 片段（stdout JSON + stderr 全查）
    captured = capsys.readouterr()
    assert rc == 1
    assert "glpat-secret-doom-value" not in captured.out + captured.err
    assert "GITLAB_TOKEN" in captured.out + captured.err


# ═══ ⑦ stage_fetch forge-repo 集成（persona/预取全桩；radar 消费形态同构）═══

SRC = {"name": "内部 forge", "kind": "forge-repo", "root": "Knowledge/Forge",
       "params": {"forge": "gitlab", "gitlab_host": "gl.example.com",
                  "repos": ["g/sub/proj"], "watch": ["commits", "pulls", "issues"]}}
PF_ITEMS = [{"repo": "g/sub/proj", "forge": "gitlab", "file": "gitlab__g__sub__proj.md",
             "commits": 1, "pulls": 2, "issues": 1}]
PAYLOAD = {"items": [{"repo": "g/sub/proj", "title": "g-sub-proj window digest",
                      "markdown": "# g/sub/proj digest", "commits_count": 1,
                      "pulls_count": 2, "issues_count": 1}]}
META = {"cost": 0.42, "turns": 3}


def _setup_stage(tmp_path, monkeypatch):
    monkeypatch.setattr(run_daily, "VAULT_ROOT", tmp_path)
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path / "state")
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)   # stage_fetch 末尾落 fetch json


def test_stage_fetch_forge_repo_happy_path(tmp_path, monkeypatch):
    # Arrange：预取 1 仓非空 → persona summarize 1 item → 落盘 source.root
    _setup_stage(tmp_path, monkeypatch)
    monkeypatch.setenv("GH_TOKEN", "ghp-env-token")
    monkeypatch.setenv("GITLAB_TOKEN", "glpat-env-token")
    monkeypatch.setenv("GITCODE_TOKEN", "gdc-env-token")
    calls: list[dict] = []
    monkeypatch.setattr(run_daily, "_forge_prefetch", lambda src, stamp: (PF_ITEMS, [], None))
    monkeypatch.setattr(run_daily, "run_persona_with_backoff",
                        lambda name, prompt, stage, label, **k:
                        calls.append({"name": name, "prompt": prompt, **k}) or (PAYLOAD, META))
    # Act
    out = run_daily.stage_fetch(SimpleNamespace(dry_run=False), [SRC], STAMP)
    # Assert ① produced entry 与既有 kind 同构（radar 消费面零分支）
    assert len(out["produced"]) == 1 and out["failed_sources"] == []
    entry = out["produced"][0]
    assert entry["source"] == "内部 forge" and entry["title"] == "g-sub-proj window digest"
    assert entry["cost"] == 0.42 and entry["turns"] == 3
    # Assert ② md 落 source.root（radar discover_today_new 扫此处；预取产物绝不落这）
    f = tmp_path / entry["path"]
    assert entry["path"].startswith("Knowledge/Forge/") and f.is_file()
    assert f.read_text(encoding="utf-8") == "# g/sub/proj digest"
    # Assert ③ persona 调用形态：Read-only 工具面 + 窄 env（三平台 token 全剥）+ prompt 带预取清单
    assert calls[0]["name"] == "pa-fetch-forge-repo"
    assert calls[0]["allowed_tools"] == ["Read"]
    env = calls[0]["env"]
    assert all(k not in env for k in ("GH_TOKEN", "GITLAB_TOKEN", "GITCODE_TOKEN"))
    assert "gitlab__g__sub__proj.md" in calls[0]["prompt"] and "commits 1 / pulls 2 / issues 1" in calls[0]["prompt"]


def test_stage_fetch_forge_repo_prefetch_fail_registers_source(tmp_path, monkeypatch):
    # Arrange：预取 fail_reason（如缺 token）→ 源级失败登记，persona 不烧
    _setup_stage(tmp_path, monkeypatch)
    monkeypatch.setattr(run_daily, "_forge_prefetch",
                        lambda src, stamp: ([], [], "缺 GITLAB_TOKEN（预取未跑；本机 env 或 hub vault 注入）"))
    calls: list = []
    monkeypatch.setattr(run_daily, "run_persona_with_backoff",
                        lambda *a, **k: calls.append(1) or (PAYLOAD, META))
    # Act
    out = run_daily.stage_fetch(SimpleNamespace(dry_run=False), [SRC], STAMP)
    # Assert
    assert out["produced"] == [] and calls == []
    assert out["failed_sources"] == [{"source": "内部 forge", "reason": "缺 GITLAB_TOKEN（预取未跑；本机 env 或 hub vault 注入）"}]


def test_stage_fetch_forge_repo_zero_prefetch_is_silent(tmp_path, monkeypatch):
    # Arrange：预取零仓（窗口内无活动）→ 正常静默：不登记失败、不调 persona
    _setup_stage(tmp_path, monkeypatch)
    monkeypatch.setattr(run_daily, "_forge_prefetch", lambda src, stamp: ([], [], None))
    calls: list = []
    monkeypatch.setattr(run_daily, "run_persona_with_backoff",
                        lambda *a, **k: calls.append(1) or (PAYLOAD, META))
    # Act
    out = run_daily.stage_fetch(SimpleNamespace(dry_run=False), [SRC], STAMP)
    # Assert
    assert out == {"produced": [], "failed_sources": [], "stamp": STAMP} and calls == []


def test_stage_fetch_forge_repo_summarize_empty_registers_source(tmp_path, monkeypatch):
    # Arrange：预取非空但 summarize 零产出 → 语义层失败也登记（不静默吞）
    _setup_stage(tmp_path, monkeypatch)
    monkeypatch.setattr(run_daily, "_forge_prefetch", lambda src, stamp: (PF_ITEMS, [], None))
    monkeypatch.setattr(run_daily, "run_persona_with_backoff", lambda *a, **k: ({"items": []}, META))
    # Act
    out = run_daily.stage_fetch(SimpleNamespace(dry_run=False), [SRC], STAMP)
    # Assert
    assert out["produced"] == []
    assert out["failed_sources"][0]["source"] == "内部 forge"
    assert "summarize 零产出" in out["failed_sources"][0]["reason"]


def test_stage_fetch_forge_repo_partial_repo_failure_registers_and_continues(tmp_path, monkeypatch):
    # 审核修复 H1：部分仓失败 → failed_sources 登记，但成功仓继续 summarize（不整源陪葬）
    _setup_stage(tmp_path, monkeypatch)
    failures = [{"repo": "g/other", "reason": "原因缺失（该维拉取失败且无原因文本）"}]
    monkeypatch.setattr(run_daily, "_forge_prefetch", lambda src, stamp: (PF_ITEMS, failures, None))
    calls: list = []
    monkeypatch.setattr(run_daily, "run_persona_with_backoff",
                        lambda *a, **k: calls.append(1) or (PAYLOAD, META))
    # Act
    out = run_daily.stage_fetch(SimpleNamespace(dry_run=False), [SRC], STAMP)
    # Assert：失败登记 + persona 照跑 + 成功仓产出保留（三者并存）
    assert calls == [1] and len(out["produced"]) == 1
    assert len(out["failed_sources"]) == 1
    row = out["failed_sources"][0]
    assert row["source"] == "内部 forge"
    assert "预取部分仓失败（1 仓）" in row["reason"] and "g/other" in row["reason"]


def test_stage_fetch_forge_repo_partial_failure_zero_success_skips_persona(tmp_path, monkeypatch):
    # 部分失败的全失败形态：零成功仓 → 失败登记一次即可（含 >3 仓折叠），persona 不烧
    _setup_stage(tmp_path, monkeypatch)
    failures = [{"repo": f"g/r{i}", "reason": f"404 r{i}"} for i in range(4)]
    monkeypatch.setattr(run_daily, "_forge_prefetch", lambda src, stamp: ([], failures, None))
    calls: list = []
    monkeypatch.setattr(run_daily, "run_persona_with_backoff",
                        lambda *a, **k: calls.append(1) or (PAYLOAD, META))
    # Act
    out = run_daily.stage_fetch(SimpleNamespace(dry_run=False), [SRC], STAMP)
    # Assert：无 persona 调用、无产出；reason 折叠「等 4 仓」且截断 ≤160
    assert calls == [] and out["produced"] == []
    reason = out["failed_sources"][0]["reason"]
    assert "预取部分仓失败（4 仓）" in reason and "等 4 仓" in reason and "g/r0" in reason
    assert len(reason) <= 160


def test_stage_fetch_generic_source_exception_registers_failed(tmp_path, monkeypatch):
    # Arrange：既有 kind 的 per-source exception 也进 failed_sources（4.5 泛化，不限 forge）
    _setup_stage(tmp_path, monkeypatch)
    monkeypatch.setattr(run_daily, "run_persona_with_backoff",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("persona 崩")))
    src = {"name": "deep", "root": "r1", "kind": "agent-deepresearch"}
    # Act
    out = run_daily.stage_fetch(SimpleNamespace(dry_run=False), [src], STAMP)
    # Assert：源失败登记（type: msg），不影响 out_json 形态
    assert out["produced"] == []
    assert out["failed_sources"] == [{"source": "deep", "reason": "RuntimeError: persona 崩"}]


# ═══ ⑧ report 渲染（4.5：failed_sources 可见；无失败零新增行）═══

def _setup_report(tmp_path, monkeypatch, fetch_failed):
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(run_daily, "REPORT_DIR", tmp_path / "report")
    monkeypatch.setattr(run_daily, "DAILY_DIR", tmp_path / "daily")
    st = tmp_path / "state"
    st.mkdir(parents=True, exist_ok=True)
    (st / f"candidates_{STAMP}.json").write_text(json.dumps(
        {"candidates": [], "today_new_count": 1, "stats": {"signals_extracted": 1}}), encoding="utf-8")
    (st / f"prd_gate_{STAMP}.json").write_text(json.dumps([]), encoding="utf-8")
    (st / f"dispatch_{STAMP}.json").write_text(json.dumps([]), encoding="utf-8")
    (st / f"fetch_{STAMP}.json").write_text(json.dumps(
        {"produced": [], "failed_sources": fetch_failed, "stamp": STAMP}), encoding="utf-8")
    monkeypatch.setattr(run_daily.subprocess, "run",
                        lambda cmd, *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""))


def test_report_renders_fetch_failed_sources(tmp_path, monkeypatch):
    # Arrange：1 源预取失败
    _setup_report(tmp_path, monkeypatch, [{"source": "内部 forge", "reason": "缺 GITLAB_TOKEN（注入）"}])
    # Act
    rp = run_daily.stage_report(SimpleNamespace(dry_run=True, no_notify=True), {}, STAMP)
    txt = rp.read_text(encoding="utf-8")
    # Assert：失败源 + 原因片段进报告（运维 triage 可见）
    assert "采集源失败" in txt and "内部 forge" in txt and "缺 GITLAB_TOKEN" in txt


def test_report_no_failed_sources_no_new_line(tmp_path, monkeypatch):
    # Arrange：无失败（baseline 报告形态）
    _setup_report(tmp_path, monkeypatch, [])
    # Act
    rp = run_daily.stage_report(SimpleNamespace(dry_run=True, no_notify=True), {}, STAMP)
    txt = rp.read_text(encoding="utf-8")
    # Assert：零漂移——条件渲染，无失败不加行
    assert "采集源失败" not in txt
