"""test_forge_api.py — forge adapter 单测（OpenSpec forge-adapter；tasks 1.6）。

三平台 × 四操作 × 三态矩阵；monkeypatch ``_RestApi._http`` 注入状态码/响应体（无真网络）、
``_GithubApi._run`` 注入 gh 退出码/stdout。断言：
- 列表端点 404→UNKNOWN（看不见项目）、单对象 404→NOT_FOUND、空列表→FOUND
- 401/403→ForgeConfigError（permanent）；merge 405/409→permanent；406/429/5xx/坏 JSON→UNKNOWN
- state 归一（opened/locked/progress→open 保守算在途）
- GitLab merge：项目 merge_method≠merge → permanent；merge 后单 parent → permanent；双 parent → FOUND(sha)
- 错误消息不含 token 片段（sanitize 面覆盖）
- 工厂：非法 forge 值 fail-fast 不回落；gitlab 缺 host / 缺 token fail-fast；github 缺 owner_repo
- 身份键：host×全路径（github 省 host=旧形态；两 host 同路径不共键）
"""
import pytest

import forge_api
from external_state import ExtState
from forge_api import (ForgeConfigError, _GitcodeApi,
                       _GitlabApi, dispatch_identity, for_profile, remote_host_and_path,
                       validate_profile)

GITLAB_TOK = "glpat-xxxxxxxxxxxxxxxxxxxx"
GITCODE_TOK = "gdc_live_yyyyyyyyyyyyyyyy"


# ── 工具：把 (method, path) 路由表装进 _http fake ──
def install_http(monkeypatch, api, routes, default=(200, "{}")):
    """routes: {(method, path 前缀): (status, body)}；**最长前缀**命中（防 /projects/group 截胡
    /projects/group%2F…）。default 兜底。"""
    calls: list[dict] = []

    def fake_http(self, method, path, *, query=None, body=None):
        calls.append({"method": method, "path": path, "query": query, "body": body})
        for (m, prefix), resp in sorted(routes.items(), key=lambda kv: -len(kv[0][1])):
            if m == method and path.startswith(prefix):
                return resp
        return default

    monkeypatch.setattr(type(api), "_http", fake_http)
    return calls


@pytest.fixture
def gitlab(monkeypatch):
    monkeypatch.setenv("GITLAB_TOKEN", GITLAB_TOK)
    return _GitlabApi(host="gitlab.example.com", full_path="group/sub/proj",
                      token=GITLAB_TOK)   # token 显式注入（不测 env 读取本身）


@pytest.fixture
def gitcode(monkeypatch):
    monkeypatch.setenv("GITCODE_TOKEN", GITCODE_TOK)
    return _GitcodeApi(full_path="o/r", token=GITCODE_TOK)   # 路由前缀统一 /repos/o/r/…


# ── 工厂 / 装载校验 ─────────────────────────────────────────────────
def test_factory_default_github():
    api = for_profile({}, owner_repo="o/r")
    assert type(api).__name__ == "_GithubApi" and api.owner_repo == "o/r"


def test_factory_invalid_forge_fail_fast():
    with pytest.raises(ForgeConfigError, match="非法 forge 值"):
        for_profile({"forge": "gitee"}, full_path="a/b", owner_repo="o/r")


def test_factory_gitlab_requires_host(monkeypatch):
    monkeypatch.setenv("GITLAB_TOKEN", GITLAB_TOK)
    with pytest.raises(ForgeConfigError, match="gitlab_host"):
        for_profile({"forge": "gitlab"}, full_path="a/b")


def test_factory_gitlab_requires_token(monkeypatch):
    monkeypatch.delenv("GITLAB_TOKEN", raising=False)
    with pytest.raises(ForgeConfigError, match="GITLAB_TOKEN"):
        for_profile({"forge": "gitlab", "gitlab_host": "h"}, full_path="a/b")


def test_factory_github_requires_owner_repo():
    with pytest.raises(ForgeConfigError, match="owner_repo"):
        for_profile({"forge": "github"})


def test_validate_profile_noop_for_github_baseline():
    validate_profile({})                      # 未声明 → 零校验零变化
    validate_profile({"forge": "github"})


def test_validate_profile_missing_token(monkeypatch):
    monkeypatch.delenv("GITLAB_TOKEN", raising=False)
    with pytest.raises(ForgeConfigError, match="GITLAB_TOKEN"):
        validate_profile({"forge": "gitlab", "gitlab_host": "h"})


def test_validate_profile_ok(monkeypatch):
    monkeypatch.setenv("GITLAB_TOKEN", GITLAB_TOK)
    validate_profile({"forge": "gitlab", "gitlab_host": "h"})   # 不抛即过


# ── 身份键 / 路径（D4）──────────────────────────────────────────────
def test_remote_host_and_path_multilevel():
    assert remote_host_and_path("git@gitlab.example.com:group/sub/proj.git") == \
        ("gitlab.example.com", "group/sub/proj")
    assert remote_host_and_path("https://gitlab.example.com/group/sub/proj.git") == \
        ("gitlab.example.com", "group/sub/proj")
    assert remote_host_and_path("https://github.com/octocat/example-repo.git") == \
        ("github.com", "octocat/example-repo")
    assert remote_host_and_path("not a url") == (None, None)


def test_dispatch_identity_github_omits_host():
    # github.com host 恒定 → 键 = 旧 owner/repo 形态（现产锁键零变化）
    assert dispatch_identity("github.com", "o/r") == "o/r"
    assert dispatch_identity(None, "o/r") == "o/r"
    # 两 host 同路径是两个仓，不共键
    assert dispatch_identity("gitlab.a.com", "g/p") == "gitlab.a.com/g/p"
    assert dispatch_identity("gitlab.b.com", "g/p") == "gitlab.b.com/g/p"
    assert dispatch_identity("gitlab.a.com", "g/p") != dispatch_identity("gitlab.b.com", "g/p")


def test_gitlab_project_path_urlencoded(gitlab):
    assert gitlab.project == "group%2Fsub%2Fproj"    # v4 官方形态：URL-encode 全路径


# ── 三态矩阵：列表端点（pr_list / branch_protection 查询）───────────
def test_gitlab_pr_list_404_unknown(gitlab, monkeypatch):
    install_http(monkeypatch, gitlab, {(  "GET", "/projects/"): (404, '{"message":"404 Project Not Found"}')})
    r = gitlab.pr_list()
    assert r.state is ExtState.UNKNOWN and "看不见项目" in r.reason


def test_gitlab_pr_list_200_empty_found(gitlab, monkeypatch):
    install_http(monkeypatch, gitlab, {}, default=(200, "[]"))
    r = gitlab.pr_list()
    assert r.state is ExtState.FOUND and r.value == []   # FOUND-空：调用点映射（adapter 不感知语境）


def test_gitlab_pr_list_normalizes_state(gitlab, monkeypatch):
    body = ('[{"iid":1,"web_url":"u1","state":"locked","source_branch":"h1","target_branch":"main"},'
            '{"iid":2,"web_url":"u2","state":"merged","source_branch":"h2","target_branch":"main"}]')
    install_http(monkeypatch, gitlab, {}, default=(200, body))
    r = gitlab.pr_list(state="all")
    assert r.value[0]["state"] == "open"     # locked 保守归 open（算在途，防 single-flight 低报）
    assert r.value[1]["state"] == "merged"
    assert r.value[0]["number"] == 1 and r.value[0]["head"] == "h1" and r.value[0]["base"] == "main"


def test_gitlab_branch_protection_found_and_notfound(gitlab, monkeypatch):
    install_http(monkeypatch, gitlab, {}, default=(200, '[{"name":"main"}]'))
    assert gitlab.branch_protection("main").value is True
    install_http(monkeypatch, gitlab, {}, default=(200, "[]"))
    r = gitlab.branch_protection("main")
    assert r.state is ExtState.NOT_FOUND     # 列表成功且无该分支 = 明确未保护（拒投）


def test_gitlab_branch_protection_404_unknown(gitlab, monkeypatch):
    install_http(monkeypatch, gitlab, {("GET", "/projects/"): (404, "{}")})
    assert gitlab.branch_protection("main").state is ExtState.UNKNOWN


def test_gitcode_pr_list_and_protection(gitcode, monkeypatch):
    install_http(monkeypatch, gitcode, {("GET", "/repos/o/r/branches/"): (200, '{"protected":true}')},
                 default=(200, '[{"number":7,"html_url":"u","state":"progress","head":"h","base":"main"}]'))
    r = gitcode.pr_list()
    assert r.state is ExtState.FOUND and r.value[0]["state"] == "open"   # progress→open 保守
    assert gitcode.branch_protection("main").value is True
    install_http(monkeypatch, gitcode, {("GET", "/repos/o/r/branches/"): (404, "{}")})
    # 审核修复 M5：404 无法区分「分支不存在」与「仓不可见」→ 按列表分型收 UNKNOWN（fail-safe 阻断）
    assert gitcode.branch_protection("main").state is ExtState.UNKNOWN


# ── 三态矩阵：401/403 permanent（ForgeConfigError）──────────────────
@pytest.mark.parametrize("status", [401, 403])
def test_rest_401_403_permanent(gitlab, gitcode, monkeypatch, status):
    install_http(monkeypatch, gitlab, {("GET", "/projects/"): (status, '{"message":"denied"}')})
    with pytest.raises(ForgeConfigError, match="认证/权限失败"):
        gitlab.pr_list()
    install_http(monkeypatch, gitcode, {("GET", "/repos/"): (status, '{"message":"denied"}')})
    with pytest.raises(ForgeConfigError):
        gitcode.pr_list()


# ── 三态矩阵：UNKNOWN 面（网络/429/5xx/406/坏 JSON）─────────────────
def test_gitlab_transient_unknown(gitlab, monkeypatch):
    def boom(self, method, path, *, query=None, body=None):
        raise OSError("connection refused")
    monkeypatch.setattr(_GitlabApi, "_http", boom)
    assert gitlab.pr_list().state is ExtState.UNKNOWN
    install_http(monkeypatch, gitlab, {("GET", "/projects/"): (429, "{}")})
    assert gitlab.pr_list().state is ExtState.UNKNOWN
    install_http(monkeypatch, gitlab, {("GET", "/projects/"): (503, "{}")})
    assert gitlab.pr_list().state is ExtState.UNKNOWN
    install_http(monkeypatch, gitlab, {("GET", "/projects/"): (200, "not-json")})
    assert gitlab.pr_list().state is ExtState.UNKNOWN


def test_gitlab_merge_406_unknown(gitlab, monkeypatch):
    install_http(monkeypatch, gitlab, {
        ("GET", "/projects/group"): (200, '{"merge_method":"merge"}'),
        ("PUT", "/projects/"): (406, '{"message":"not mergeable"}')})
    assert gitlab.pr_merge(1, "t", "b").state is ExtState.UNKNOWN


# ── pr_merge：单对象 404 → NOT_FOUND；405/409 → permanent ───────────
def test_gitlab_merge_404_not_found(gitlab, monkeypatch):
    install_http(monkeypatch, gitlab, {
        ("GET", "/projects/group"): (200, '{"merge_method":"merge"}'),
        ("PUT", "/projects/"): (404, "{}")})
    assert gitlab.pr_merge(99, "t", "b").state is ExtState.NOT_FOUND


@pytest.mark.parametrize("status", [405, 409])
def test_gitlab_merge_405_409_permanent(gitlab, monkeypatch, status):
    install_http(monkeypatch, gitlab, {
        ("GET", "/projects/group"): (200, '{"merge_method":"merge"}'),
        ("PUT", "/projects/"): (status, "{}")})
    with pytest.raises(ForgeConfigError):
        gitlab.pr_merge(1, "t", "b")


# ── pr_create 409：幂等 recheck 复用（审核修复 M6，对齐 gh 面 A5）─────
def test_gitlab_pr_create_409_recheck_reuses_existing(gitlab, monkeypatch):
    install_http(monkeypatch, gitlab, {
        ("POST", "/projects/"): (409, '{"message":"already exists"}'),
        ("GET", "/projects/"): (200, '[{"iid":5,"web_url":"u","state":"opened",'
                                      '"source_branch":"h","target_branch":"main"}]')})
    r = gitlab.pr_create("main", "h", "t", "b")
    assert r.state is ExtState.FOUND and r.value["number"] == 5
    assert "recheck 复用" in r.reason


def test_gitlab_pr_create_409_recheck_miss_stays_unknown(gitlab, monkeypatch):
    install_http(monkeypatch, gitlab, {
        ("POST", "/projects/"): (409, "{}"),
        ("GET", "/projects/"): (200, "[]")})
    assert gitlab.pr_create("main", "h", "t", "b").state is ExtState.UNKNOWN


def test_gitcode_pr_create_409_recheck_reuses_existing(gitcode, monkeypatch):
    install_http(monkeypatch, gitcode, {
        ("POST", "/repos/"): (409, "{}"),
        ("GET", "/repos/"): (200, '[{"number":7,"html_url":"u","state":"open",'
                                    '"head":"h","base":"main"}]')})
    r = gitcode.pr_create("main", "h", "t", "b")
    assert r.state is ExtState.FOUND and r.value["number"] == 7
    assert "recheck 复用" in r.reason


# ── GitLab merge：method 前置校验 + 双 parent 验证 ──────────────────
def test_gitlab_merge_method_not_merge_permanent(gitlab, monkeypatch):
    calls = install_http(monkeypatch, gitlab, {}, default=(200, '{"merge_method":"squash"}'))
    with pytest.raises(ForgeConfigError, match="squash"):
        gitlab.pr_merge(1, "t", "b")
    assert all(c["method"] == "GET" for c in calls)   # 前置拦截：未发 merge 请求


def test_gitlab_merge_single_parent_permanent(gitlab, monkeypatch):
    install_http(monkeypatch, gitlab, {
        ("GET", "/projects/group"): (200, '{"merge_method":"merge"}'),
        ("PUT", "/projects/"): (200, "{}"),
        ("GET", "/projects/group%2Fsub%2Fproj/merge_requests/1"): (200, '{"merge_commit_sha":"abc"}'),
        ("GET", "/projects/group%2Fsub%2Fproj/repository/commits/"): (200, '{"parent_ids":["p1"]}')})
    with pytest.raises(ForgeConfigError, match="单 parent"):
        gitlab.pr_merge(1, "t", "b")


def test_gitlab_merge_two_parents_found_sha(gitlab, monkeypatch):
    install_http(monkeypatch, gitlab, {
        ("GET", "/projects/group"): (200, '{"merge_method":"merge"}'),
        ("PUT", "/projects/"): (200, "{}"),
        ("GET", "/projects/group%2Fsub%2Fproj/merge_requests/1"): (200, '{"merge_commit_sha":"abc"}'),
        ("GET", "/projects/group%2Fsub%2Fproj/repository/commits/"): (200, '{"parent_ids":["p1","p2"]}')})
    r = gitlab.pr_merge(1, "title", "body")
    assert r.state is ExtState.FOUND and r.value == "abc"


def test_gitcode_merge_two_parents(gitcode, monkeypatch):
    install_http(monkeypatch, gitcode, {
        ("PUT", "/repos/o/r/pulls/3/merge"): (200, "{}"),
        ("GET", "/repos/o/r/pulls/3"): (200, '{"merge_commit_sha":"sha9"}'),
        ("GET", "/repos/o/r/commits/sha9"): (200, '{"parents":["a","b"]}')})
    r = gitcode.pr_merge(3, "t", "b")
    assert r.state is ExtState.FOUND and r.value == "sha9"


def test_gitcode_merge_no_sha_degrades_gracefully(gitcode, monkeypatch):
    install_http(monkeypatch, gitcode, {
        ("PUT", "/repos/"): (200, "{}"),
        ("GET", "/repos/o/r/pulls/3"): (200, "{}")})   # 文档粒度粗：无 merge_commit_sha 回报
    r = gitcode.pr_merge(3, "t", "b")
    assert r.state is ExtState.FOUND and "跳过 parent 验证" in r.reason


def test_gitcode_pr_create(gitcode, monkeypatch):
    calls = install_http(monkeypatch, gitcode, {}, default=(201, '{"number":5,"html_url":"u","state":"open"}'))
    r = gitcode.pr_create("main", "h", "t", "b")
    assert r.value["number"] == 5 and calls[0]["method"] == "POST"


# ── 错误消息不含 token（sanitize 面）────────────────────────────────
def test_error_messages_never_leak_token(gitlab, gitcode, monkeypatch):
    leak_body = f'{{"message":"bad token {GITLAB_TOK}"}}'
    install_http(monkeypatch, gitlab, {("GET", "/projects/"): (422, leak_body)})
    r = gitlab.pr_list()
    assert GITLAB_TOK not in r.reason and "glpat" not in r.reason   # 错误体不进 reason（根上不泄漏）
    install_http(monkeypatch, gitlab, {("GET", "/projects/"): (401, leak_body)})
    with pytest.raises(ForgeConfigError) as ei:
        gitlab.pr_list()
    assert GITLAB_TOK not in str(ei.value)
    install_http(monkeypatch, gitcode, {("GET", "/repos/"): (500, f'{{"e":"{GITCODE_TOK}"}}')})
    assert GITCODE_TOK not in gitcode.pr_list().reason


# ── github wrapper：argv 逐 token 与基线一致（行为等价锚）────────────
def test_github_dev_face_argv_token_identical():
    # dev-agent 面（dev-agent.py _pr_automerge / run_publish_phase 既有序列逐 token）
    assert _GithubApi_argv(forge_api._GithubApi.dev_pr_list_open("auto/x")) == \
        ["pr", "list", "--head", "auto/x", "--state", "open", "--json", "number"]
    assert forge_api._GithubApi.dev_pr_create("main", "auto/x", "T", "B") == \
        ["pr", "create", "--base", "main", "--head", "auto/x", "--title", "T", "--body", "B"]
    assert forge_api._GithubApi.dev_pr_merge("7", "T", "B") == \
        ["pr", "merge", "7", "--merge", "--subject", "T", "--body", "B"]


def _GithubApi_argv(x):
    return x


def test_github_run_daily_face_argv(monkeypatch):
    recorded: list[list[str]] = []

    def fake_run(argv, **kw):
        recorded.append(argv)
        class R:  # 最小 CompletedProcess 替身
            returncode, stdout, stderr = 0, "[]", ""
        return R()

    monkeypatch.setattr(forge_api.subprocess, "run", fake_run)
    api = for_profile({"forge": "github"}, owner_repo="o/r")
    api.branch_protection("main")
    assert recorded[-1] == ["gh", "api", "repos/o/r/branches/main/protection", "--silent",
                            "-H", "Accept: application/vnd.github+json"]
    api.pr_lookup("auto/x")
    assert recorded[-1] == ["gh", "pr", "list", "-R", "o/r", "--head", "auto/x",
                            "--state", "all", "--limit", "5", "--json", "number,url,state"]
    api.pr_list_open(limit=50)
    assert recorded[-1] == ["gh", "pr", "list", "-R", "o/r", "--state", "open",
                            "--limit", "50", "--json", "number"]
    api.pr_scan_all()
    assert recorded[-1] == ["gh", "pr", "list", "-R", "o/r", "--state", "all",
                            "--limit", "100", "--json", "number,headRefName,state"]


def test_github_run_daily_face_three_states(monkeypatch):
    api = for_profile({}, owner_repo="o/r")

    def mk(rc=0, out="[]", err=""):
        class R:
            returncode, stdout, stderr = rc, out, err
        return R()

    monkeypatch.setattr(forge_api.subprocess, "run", lambda a, **k: mk(0, "[]"))
    assert api.pr_lookup("b").state is ExtState.NOT_FOUND          # 查询成功无 PR
    monkeypatch.setattr(forge_api.subprocess, "run", lambda a, **k: mk(1, "", "boom"))
    assert api.pr_lookup("b").state is ExtState.UNKNOWN            # 非零 → UNKNOWN（gh 判据）
    monkeypatch.setattr(forge_api.subprocess, "run", lambda a, **k: mk(0, "bad"))
    assert api.pr_lookup("b").state is ExtState.UNKNOWN            # 坏 JSON → UNKNOWN
    # 分支保护：404 in stderr = 明确未保护（NOT_FOUND）；非零无 404 = UNKNOWN
    monkeypatch.setattr(forge_api.subprocess, "run", lambda a, **k: mk(1, "", "HTTP 404"))
    assert api.branch_protection("main").state is ExtState.NOT_FOUND
    monkeypatch.setattr(forge_api.subprocess, "run", lambda a, **k: mk(1, "", "HTTP 500"))
    assert api.branch_protection("main").state is ExtState.UNKNOWN
