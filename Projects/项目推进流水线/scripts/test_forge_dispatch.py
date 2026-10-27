"""test_forge_dispatch.py — add-forge-adapter 组 2 dispatch 链分流单测（tasks 2.5）。

覆盖：
- run_daily 四查询函数 fapi 分流三态消费（FOUND-空 按调用点映射；UNKNOWN 直通；REST 归一面 head 匹配）
- reconcile_pr REST 补开 PR 分流（FOUND→pr_open / UNKNOWN→blocked 保留分支 / ForgeConfigError→单项目 skip 终态）
- dispatch_one 准入 1.5：gitlab 缺 token → 单项目 skip（不炸、不 blocked 重试）；github 无声明 → 零 fapi 零变化
- askpass：脚本 0700 只引 env 键名 + 进程级注入 + 引用计数归零清理；github no-op
- dispatch_key：github 退化 owner_repo（锁键零变化）；gitlab 多级 host/full/path
- _dev_cmd：gitlab → 尾附 --forge/--forge-host；github/未声明 → 不附（基线 argv 一致）
- dev-agent _pr_automerge 分流：REST 面（fake adapter）/ github 面（gh argv 基线逐 token）+ _forge_rest fail-loud
"""
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from external_state import ExtState, found, not_found, unknown

import run_daily

PROJ = "forge_t"
GITLAB_TOK = "glpat-xxxxxxxxxxxxxxxxxxxx"


class _FakeApi:
    """最小 REST adapter 替身（按预设返 ExtResult / 抛 ForgeConfigError）。"""

    def __init__(self, results: dict):
        self.results, self.calls = results, []

    def _res(self, key):
        self.calls.append(key)
        r = self.results[key]
        if isinstance(r, Exception):
            raise r
        return r

    def branch_protection(self, base):
        return self._res("prot")

    def pr_list(self, state="open", head=None, limit=100):
        return self._res("pr_list")

    def pr_create(self, base, head, title, body):
        return self._res("pr_create")

    def pr_merge(self, number, title="", body=""):
        return self._res("pr_merge")


# ── 四查询函数 fapi 分流三态消费（tasks 2.3）─────────────────────────
def test_check_branch_protection_fapi_passthrough():
    f = _FakeApi({"prot": found(True, "已保护")})
    r = run_daily.check_branch_protection("o/r", "main", fapi=f)
    assert r.state is ExtState.FOUND and r.value is True and f.calls == ["prot"]
    f2 = _FakeApi({"prot": unknown("HTTP 503")})
    assert run_daily.check_branch_protection("o/r", "main", fapi=f2).is_unknown


def test_count_inflight_prs_fapi_found_empty_maps_to_zero():
    f = _FakeApi({"pr_list": found([{"number": 1}, {"number": 2}])})
    r = run_daily.count_inflight_prs("o/r", fapi=f)
    assert r.state is ExtState.FOUND and r.value == 2
    f2 = _FakeApi({"pr_list": found([])})   # FOUND-空（0 个开放 PR 亦 FOUND(0) 可决断）
    assert run_daily.count_inflight_prs("o/r", fapi=f2).value == 0
    f3 = _FakeApi({"pr_list": unknown("429")})
    assert run_daily.count_inflight_prs("o/r", fapi=f3).is_unknown   # UNKNOWN→准入阻断（不 fail-open）


def test_scan_pr_list_for_slug_fapi_normalized_head():
    prs = [{"number": 3, "state": "open", "head": "auto/20260718-fix-login"},
           {"number": 4, "state": "merged", "head": "auto/other"}]
    f = _FakeApi({"pr_list": found(prs)})
    err, hit = run_daily._scan_pr_list_for_slug("o/r", "20260718-fix-login", fapi=f)
    assert err is None and "PR #3" in hit          # 归一面 head 字段 ↔ gh headRefName
    f2 = _FakeApi({"pr_list": found(prs)})
    assert run_daily._scan_pr_list_for_slug("o/r", "nope", fapi=f2) == (None, None)
    f3 = _FakeApi({"pr_list": unknown("超时")})
    err3, _ = run_daily._scan_pr_list_for_slug("o/r", "x", fapi=f3)
    assert err3 and "超时" in err3


def test_lookup_pr_fapi_three_states():
    f = _FakeApi({"pr_list": found([{"number": 9, "url": "u9", "state": "open"}])})
    r = run_daily._lookup_pr("o/r", "auto/x", fapi=f)
    assert r.state is ExtState.FOUND and r.value["number"] == 9
    f2 = _FakeApi({"pr_list": found([])})
    assert run_daily._lookup_pr("o/r", "auto/x", fapi=f2).state is ExtState.NOT_FOUND
    from forge_api import ForgeConfigError
    f3 = _FakeApi({"pr_list": ForgeConfigError("认证/权限失败 HTTP 401")})
    with pytest.raises(ForgeConfigError):   # 审核修复 M1：permanent 上抛不降级 UNKNOWN，调用点落单项目 skip 终态
        run_daily._lookup_pr("o/r", "auto/x", fapi=f3)


def test_github_gh_paths_untouched_without_fapi(monkeypatch):
    """fapi=None → gh 内联路径行为不变（D1 行为等价）：subprocess argv 与基线一致。"""
    argv_seen: list[list[str]] = []

    class _P:
        returncode, stdout, stderr = 0, "[]", ""

    def fake_run(cmd, **kw):
        argv_seen.append(cmd); return _P()
    monkeypatch.setattr(run_daily.subprocess, "run", fake_run)
    assert run_daily.count_inflight_prs("o/r").value == 0
    assert argv_seen[-1] == ["gh", "pr", "list", "-R", "o/r", "--state", "open",
                             "--limit", "50", "--json", "number"]
    run_daily._lookup_pr("o/r", "b")
    assert argv_seen[-1] == ["gh", "pr", "list", "-R", "o/r", "--head", "b",
                             "--state", "all", "--limit", "5", "--json", "number,url,state"]


# ── reconcile_pr REST 分流（tasks 2.3：补开 PR 触点）─────────────────
def _recon_rec(branch="auto/x"):
    return {"branch": branch, "prd_path": "p.md", "run_log": None}


def test_reconcile_fapi_creates_pr(monkeypatch):
    monkeypatch.setattr(run_daily, "_has_commits", lambda *a, **k: found(True))
    f = _FakeApi({"pr_list": found([]), "pr_create": found({"url": "http://pr/7", "number": 7})})
    rec = _recon_rec()
    run_daily.reconcile_pr("/repo", "o/r", rec, "main", "s", interrupted=False, fapi=f)
    assert rec["status"] == "pr_open" and rec["pr_url"] == "http://pr/7" and f.calls == ["pr_list", "pr_create"]


def test_reconcile_fapi_unknown_keeps_branch(monkeypatch):
    monkeypatch.setattr(run_daily, "_has_commits", lambda *a, **k: found(True))
    f = _FakeApi({"pr_list": found([]), "pr_create": unknown("HTTP 503")})
    rec = _recon_rec()
    run_daily.reconcile_pr("/repo", "o/r", rec, "main", "s", interrupted=True, fapi=f)
    assert rec["status"] == "blocked_external_state" and rec["blocked_check"] == "pr_create"


def test_reconcile_fapi_config_error_single_project_skip(monkeypatch):
    from forge_api import ForgeConfigError
    monkeypatch.setattr(run_daily, "_has_commits", lambda *a, **k: found(True))
    f = _FakeApi({"pr_list": found([]), "pr_create": ForgeConfigError("认证/权限失败 HTTP 403")})
    rec = _recon_rec()
    run_daily.reconcile_pr("/repo", "o/r", rec, "main", "s", interrupted=True, fapi=f)
    assert rec["status"] == "skip" and "forge 配置类失败" in rec["skip_reason"]   # 单项目终态（非 blocked 重试）


def test_reconcile_lookup_401_permanent_skip(monkeypatch):
    """审核修复 M1：_lookup_pr 面 401/403 上抛 → reconcile 落单项目 skip 终态（不降级 UNKNOWN 进保留分支臂）。"""
    from forge_api import ForgeConfigError
    monkeypatch.setattr(run_daily, "_has_commits", lambda *a, **k: found(True))
    f = _FakeApi({"pr_list": ForgeConfigError("认证/权限失败 HTTP 401")})
    rec = _recon_rec()
    run_daily.reconcile_pr("/repo", "o/r", rec, "main", "s", interrupted=True, fapi=f)
    assert rec["status"] == "skip" and "forge 配置类失败" in rec["skip_reason"]   # 单项目终态（非 blocked 重试）


def test_reconcile_fapi_existing_pr_state_case_insensitive(monkeypatch):
    """gh 大写 OPEN 与 REST 小写 open 同归 pr_open（消费面大小写不敏感比对）。"""
    for st in ("OPEN", "open"):
        f = _FakeApi({"pr_list": found([{"number": 5, "url": "u5", "state": st}])})
        rec = _recon_rec()
        run_daily.reconcile_pr("/repo", "o/r", rec, "main", "s", fapi=f)
        assert rec["status"] == "pr_open" and rec["pr_url"] == "u5"


# ── dispatch_one 准入 1.5：forge 配置 fail-fast（tasks 2.1）──────────
def _entry():
    return {"project": PROJ, "prd_path": f"state/prd/{PROJ}/20260718_t.md", "source_path": ""}


def _prof(repo, **kw):
    base = {"name": PROJ, "repo": str(repo), "admission": True, "dev_agent_ready": True,
            "type": "code", "default_branch": "main", "conda_env": "", "goal": "g",
            "match_surface": {"one_liner": "x"}, "max_prs_in_flight": 2}
    base.update(kw); return base


def _args():
    return SimpleNamespace(force=False, dispatch_skip_dev=False, dispatch_limit=None, max_concurrent=1)


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(run_daily, "VAULT_ROOT", tmp_path)
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path / "state")


def _mk_repo(tmp_path):
    """gitlab 测试仓：真 .git + remote（for_profile 从 remote 提取全路径身份）。"""
    import subprocess as sp
    sp.run(["git", "init", "-q", str(tmp_path)], check=True)
    sp.run(["git", "-C", str(tmp_path), "remote", "add", "origin",
            "https://gitlab.example.com/g/sub/proj.git"], check=True)
    return tmp_path


def test_dispatch_missing_forge_token_single_project_skip(tmp_path, monkeypatch):
    """gitlab 声明 + 缺 GITLAB_TOKEN → 准入早期 skip 单项目终态（不烧 dev loop、不 blocked 重试）。"""
    _isolate(monkeypatch, tmp_path)
    monkeypatch.delenv("GITLAB_TOKEN", raising=False)
    _mk_repo(tmp_path)
    (tmp_path / _entry()["prd_path"]).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / _entry()["prd_path"]).write_text("# PRD", encoding="utf-8")
    monkeypatch.setattr(run_daily, "repo_owner_repo", lambda *a, **k: "o/r")
    rec = run_daily.dispatch_one(_entry(), _prof(tmp_path, forge="gitlab", gitlab_host="gitlab.example.com"),
                                 "20260718", _args())
    assert rec["status"] == "skip" and "GITLAB_TOKEN" in rec["skip_reason"]


def test_dispatch_invalid_forge_value_skip(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.delenv("GITLAB_TOKEN", raising=False)
    (tmp_path / _entry()["prd_path"]).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / _entry()["prd_path"]).write_text("# PRD", encoding="utf-8")
    monkeypatch.setattr(run_daily, "repo_owner_repo", lambda *a, **k: "o/r")
    rec = run_daily.dispatch_one(_entry(), _prof(tmp_path, forge="gitee"), "20260718", _args())
    assert rec["status"] == "skip" and "非法 forge" in rec["skip_reason"]   # 不静默回落 github


def test_dispatch_401_permanent_single_project_skip(tmp_path, monkeypatch):
    """准入 2 REST 401/403 → ForgeConfigError → 单项目 skip 终态（同批其他项目不受影响）。"""
    from forge_api import ForgeConfigError
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("GITLAB_TOKEN", GITLAB_TOK)
    _mk_repo(tmp_path)
    (tmp_path / _entry()["prd_path"]).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / _entry()["prd_path"]).write_text("# PRD", encoding="utf-8")
    monkeypatch.setattr(run_daily, "repo_owner_repo", lambda *a, **k: "o/r")
    monkeypatch.setattr(run_daily, "check_branch_protection",
                        lambda *a, **k: (_ for _ in ()).throw(ForgeConfigError("认证/权限失败 HTTP 401")))
    rec = run_daily.dispatch_one(_entry(), _prof(tmp_path, forge="gitlab", gitlab_host="h"), "20260718", _args())
    assert rec["status"] == "skip" and "forge 配置类失败" in rec["skip_reason"]


def test_dispatch_admission34_401_permanent_skip(tmp_path, monkeypatch):
    """审核修复 M1：准入 3（幂等查）/ 准入 4（在途数）面 401/403 上抛 → 单项目 skip 终态（不降级 UNKNOWN 进 blocked 重试臂）。"""
    from forge_api import ForgeConfigError
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("GITLAB_TOKEN", GITLAB_TOK)
    _mk_repo(tmp_path)
    (tmp_path / _entry()["prd_path"]).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / _entry()["prd_path"]).write_text("# PRD", encoding="utf-8")
    monkeypatch.setattr(run_daily, "repo_owner_repo", lambda *a, **k: "o/r")
    monkeypatch.setattr(run_daily, "check_branch_protection", lambda *a, **k: found(True, "已保护"))
    monkeypatch.setattr(run_daily, "already_dispatched",
                        lambda *a, **k: (_ for _ in ()).throw(ForgeConfigError("认证/权限失败 HTTP 401")))
    rec = run_daily.dispatch_one(_entry(), _prof(tmp_path, forge="gitlab", gitlab_host="h"), "20260718", _args())
    assert rec["status"] == "skip" and "forge 配置类失败" in rec["skip_reason"]   # 准入 3：skip 终态（非 blocked）
    monkeypatch.setattr(run_daily, "already_dispatched", lambda *a, **k: not_found("无既有投递"))
    monkeypatch.setattr(run_daily, "count_inflight_prs",
                        lambda *a, **k: (_ for _ in ()).throw(ForgeConfigError("认证/权限失败 HTTP 403")))
    rec2 = run_daily.dispatch_one(_entry(), _prof(tmp_path, forge="gitlab", gitlab_host="h"), "20260718", _args())
    assert rec2["status"] == "skip" and "forge 配置类失败" in rec2["skip_reason"]  # 准入 4：skip 终态（非 blocked）


def test_dispatch_github_no_forge_decl_no_fapi(tmp_path, monkeypatch):
    """未声明 forge → fapi=None → 走 gh 内联路径（D1 行为等价；validate 零校验零拦截）。"""
    _isolate(monkeypatch, tmp_path)
    (tmp_path / _entry()["prd_path"]).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / _entry()["prd_path"]).write_text("# PRD", encoding="utf-8")
    monkeypatch.setattr(run_daily, "repo_owner_repo", lambda *a, **k: "o/r")
    seen: dict = {}

    def _cap(owner_repo, base, fapi=None):
        seen["fapi"] = fapi; return not_found("未保护（404），拒投")
    monkeypatch.setattr(run_daily, "check_branch_protection", _cap)
    rec = run_daily.dispatch_one(_entry(), _prof(tmp_path), "20260718", _args())
    assert seen["fapi"] is None                     # github 基线：无 adapter 介入
    assert rec["status"] == "skip" and "未保护" in rec["skip_reason"]


# ── askpass（tasks 2.4 / D5）─────────────────────────────────────────
def test_askpass_setup_teardown_refcount(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    prof = {"forge": "gitlab"}
    env = run_daily._askpass_env_setup(prof, PROJ)
    script = Path(env["GIT_ASKPASS"])
    assert script.exists() and oct(script.stat().st_mode & 0o777) == "0o700"
    body = script.read_text(encoding="utf-8")
    assert "GITLAB_TOKEN" in body and GITLAB_TOK not in body        # 只引 env 键名，本体不含 token
    assert os_environ("GIT_ASKPASS") == str(script)                  # 进程级注入（ls-remote/push 全覆盖）
    assert os_environ("GIT_TERMINAL_PROMPT") == "0"
    run_daily._askpass_teardown(prof)                                # refcnt 1→0：清 env + 删脚本
    assert os_environ("GIT_ASKPASS") is None and not script.exists()
    assert os_environ("GIT_TERMINAL_PROMPT") is None


def test_askpass_github_noop_local(tmp_path, monkeypatch):
    """本机 github 面 no-op 不变（mac 真身 ssh/keychain 凭据通道，加 askpass 反而截胡）。"""
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(run_daily, "_in_container", lambda: False)
    assert run_daily._askpass_env_setup({"forge": "github"}, PROJ) == {}
    assert run_daily._askpass_env_setup({}, PROJ) == {}
    run_daily._askpass_teardown({"forge": "github"})                 # no-op 不误删全局
    assert os_environ("GIT_TERMINAL_PROMPT") is None


def test_askpass_github_container_writes_script(tmp_path, monkeypatch):
    """m10 缺口②：github 面容器形态 push 无凭据（askpass no-op → git 交互挂起 30s 超时，
    m10 止损清场 push --delete 实证）。容器内 GH_TOKEN 由 hub vault --env-file 注入
    （m10 容器 Config.Env 实证存在），github 面对齐 forge 面同通道；脚本按 host 分流已含
    github→GH_TOKEN 分支（H2）。teardown 对称（容器 github refcnt 不泄漏）。"""
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(run_daily, "_in_container", lambda: True)
    env = run_daily._askpass_env_setup({"forge": "github"}, PROJ)
    assert env and os_environ("GIT_ASKPASS") == env["GIT_ASKPASS"]
    script = Path(env["GIT_ASKPASS"])
    assert script.exists() and str(script).startswith("/tmp/")       # 容器=真 /tmp（挂载卷外）
    body = script.read_text(encoding="utf-8")
    assert "GH_TOKEN" in body and GITLAB_TOK not in body             # 只引 env 键名
    run_daily._askpass_teardown({"forge": "github"})                 # 容器 github：对称清理
    assert not script.exists() and os_environ("GIT_ASKPASS") is None


def test_askpass_default_face_container_writes_script(tmp_path, monkeypatch):
    """m13 缺口⑤：未声明 forge（= 历史默认 github 面）容器形态同样写 askpass。真跑常态：
    github 项目 profile 不写 forge 字段（gitlab/gitcode 才显式声明），旧判定 ``not forge →
    return {}`` 使默认面容器**永远**拿不到凭据——m13 全链走到 merge push 才暴露
    ``could not read Username for 'https://github.com'``（GH_TOKEN 在容器 env 但 git 够不着）。
    本机默认面仍 no-op（见 test_askpass_github_noop_local 的 {} 断言，语义不变）。"""
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(run_daily, "_in_container", lambda: True)
    env = run_daily._askpass_env_setup({}, PROJ)                    # 未声明 forge
    assert env and os_environ("GIT_ASKPASS") == env["GIT_ASKPASS"]
    script = Path(env["GIT_ASKPASS"])
    assert script.exists() and str(script).startswith("/tmp/")
    assert "GH_TOKEN" in script.read_text(encoding="utf-8")         # 脚本按 host 分流含 github 分支
    run_daily._askpass_teardown({})                                 # 未声明面：对称清理不泄漏
    assert not script.exists() and os_environ("GIT_ASKPASS") is None


def test_askpass_script_selects_token_by_host(tmp_path, monkeypatch):
    """审核修复 H2：多 token 并存时按提示串 host 选键（原恒定级联 GITLAB→GITCODE→GH 错绑——
    gitcode 项目 + GITLAB_TOKEN 并存时拿错凭据；hub 整记录注入使容器内并存成常态）。真跑脚本。"""
    import subprocess as sp
    _isolate(monkeypatch, tmp_path)
    env = run_daily._askpass_env_setup({"forge": "gitcode"}, PROJ)
    script = Path(env["GIT_ASKPASS"])
    fake = {"GITLAB_TOKEN": "glpat-wrong", "GITCODE_TOKEN": "gdc-right",
            "GH_TOKEN": "ghp-ghface", "PATH": "/usr/bin:/bin"}

    def ask(prompt: str) -> str:
        return sp.run(["sh", str(script), prompt], capture_output=True, text=True,
                      env=fake, stdin=sp.DEVNULL).stdout

    assert ask("Password for 'https://oauth2@api.gitcode.com':") == "gdc-right"
    assert ask("Password for 'https://oauth2@gitcode.com/o/r.git':") == "gdc-right"
    assert ask("Password for 'https://oauth2@github.com/o/r.git':") == "ghp-ghface"
    assert ask("Password for 'https://roc@gl.internal.corp/g/sub/p.git':") == "glpat-wrong"
    assert ask("Username for 'https://gitcode.com':") == "oauth2\n"    # Username 恒 oauth2（PAT 面）
    run_daily._askpass_teardown({"forge": "gitcode"})                  # refcnt 归零（不污染下游测试）


def os_environ(k):
    import os
    return os.environ.get(k)


def test_askpass_concurrent_refcount(tmp_path, monkeypatch):
    """并发 dispatch：先退者不拆台（refcnt 未归零不清 env/脚本）。"""
    _isolate(monkeypatch, tmp_path)
    prof = {"forge": "gitlab"}
    run_daily._askpass_env_setup(prof, PROJ)
    run_daily._askpass_env_setup(prof, PROJ)                         # +1 → 2
    script = Path(os_environ("GIT_ASKPASS"))
    run_daily._askpass_teardown(prof)                                # 2→1：仍活
    assert script.exists() and os_environ("GIT_ASKPASS") == str(script)
    run_daily._askpass_teardown(prof)                                # 1→0：清
    assert not script.exists() and os_environ("GIT_ASKPASS") is None


# ── dispatch_key（D4：host × 全路径身份键）────────────────────────────
# 组5.1 hub 全链 #10 实证修订：dispatch 链 repo 输入是 profile repo 字符串非本地仓路径——
# 旧 remote_origin 提取路径从未生效（恒 fallback owner_repo）。身份来源 = profile 声明面。
def test_dispatch_key_github_degrades_to_owner_repo():
    assert run_daily.dispatch_key("o/r", "o/r") == "o/r"             # 锁/slot/分组键零变化
    assert run_daily.dispatch_key("o/r", "o/r", {"forge": "github"}) == "o/r"
    assert run_daily.dispatch_key("o/r", "o/r", None) == "o/r"       # 未声明 forge → 旧键形态


def test_dispatch_key_gitlab_multilevel():
    prof = {"forge": "gitlab", "gitlab_host": "gitlab.a.com"}
    assert run_daily.dispatch_key("g/sub/proj", "sub/proj", prof) == "gitlab.a.com/g/sub/proj"   # 旧尾两级撞键已修
    # 声明形态（scheme 前缀 / host:port）恒归一同一裸 host——身份与声明形态解耦（bare_host 同源）
    assert run_daily.dispatch_key("g/sub/proj", "sub/proj",
                                  {"forge": "gitlab", "gitlab_host": "https://gitlab.a.com"}) \
        == "gitlab.a.com/g/sub/proj"
    assert run_daily.dispatch_key("g/sub/proj", "sub/proj",
                                  {"forge": "gitlab", "gitlab_host": "http://gitlab.a.com:8180"}) \
        == "gitlab.a.com:8180/g/sub/proj"


def test_dispatch_key_gitcode_two_segment():
    prof = {"forge": "gitcode"}
    assert run_daily.dispatch_key("o/r", "o/r", prof) == "gitcode.com/o/r"   # 单实例 host 恒定


# ── _dev_cmd forge flag 透传（tasks 2.2）─────────────────────────────
def test_dev_cmd_gitlab_appends_forge_flags(monkeypatch):
    monkeypatch.delenv("PA_DEV_MODEL", raising=False)
    cmd = run_daily._dev_cmd(_prof("/repo", forge="gitlab", gitlab_host="gitlab.example.com"),
                             "prd.md", "main", "src.md")
    assert cmd[-2:] == ["--forge", "gitlab"] or cmd[-4:-2] == ["--forge", "gitlab"]
    assert "--forge-host" in cmd and "gitlab.example.com" in cmd


def test_dev_cmd_github_baseline_no_forge_flags(monkeypatch):
    monkeypatch.delenv("PA_DEV_MODEL", raising=False)
    for prof in (_prof("/repo"), _prof("/repo", forge="github")):
        cmd = run_daily._dev_cmd(prof, "prd.md", "main", "src.md")
        assert "--forge" not in cmd                                 # github/未声明 → 基线 argv 一致


# ── dev-agent PR 面分流（tasks 2.2；连字符文件名 → importlib 按路径加载）──
@pytest.fixture(scope="module")
def dev_mod():
    p = Path(__file__).parent / "dev-agent.py"
    spec = importlib.util.spec_from_file_location("dev_agent_under_test", p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _reset_dev_forge(mod):
    """还原 github 基线（conf 空 + adapter 缓存清）——_forge_dev_api 对 None 缓存惰性重构造。"""
    mod._FORGE_CONF.clear()
    mod._FORGE_API_CACHE = None


def test_dev_agent_github_face_gh_argv_baseline(dev_mod, monkeypatch):
    """_FORGE_CONF 空（github 基线）→ gh 序列 argv 逐 token 与既有实现一致（装载代码零变化）。

    fix(pa) pr_url：``pr list`` 带 ``--json number,url``（PR 身份随返回值带出）。
    """
    argv: list[list[str]] = []
    monkeypatch.setattr(dev_mod, "gh", lambda a: (argv.append(a), "[]" if "list" in a else "http://pr/9")[1])
    monkeypatch.setattr(dev_mod, "git", lambda a: "sha-tip")
    dev_mod._pr_automerge("auto/x", "main", "T", "B")
    assert argv[0] == ["pr", "list", "--head", "auto/x", "--state", "open", "--json", "number,url"]
    assert argv[-1] == ["pr", "merge", "9", "--merge", "--subject", "T", "--body", "B"]


def test_dev_agent_rest_face_uses_adapter(dev_mod, monkeypatch):
    """forge=gitlab → REST adapter 幂等模板（list→create→merge；UNKNOWN → RuntimeError fail-loud）。"""
    _reset_dev_forge(dev_mod)
    dev_mod._FORGE_CONF.update(forge="gitlab", forge_host="h")
    calls: list[str] = []

    class _Api:
        def pr_list(self, state="open", head=None, limit=100):
            calls.append("list"); return found([], "")
        def pr_create(self, base, head, title, body):
            calls.append(("create", base, head, title)); return found({"number": 12, "url": "u"}, "")
        def pr_merge(self, number, title="", body=""):
            calls.append(("merge", number, title)); return found("msha", "")
    monkeypatch.setattr(dev_mod, "for_profile", lambda *a, **k: _Api())
    monkeypatch.setattr(dev_mod, "repo_full_path", lambda r: "g/p")
    monkeypatch.setattr(dev_mod, "git", lambda a: "tip")
    sha, pr_num, pr_url = dev_mod._pr_automerge("auto/x", "main", "T", "B")
    assert sha == "tip" and pr_num == "12" and pr_url == "u"   # fix(pa)：三元组带出 PR 身份
    assert calls[0] == "list" and calls[1] == ("create", "main", "auto/x", "T")
    assert calls[2] == ("merge", "12", "T")
    # UNKNOWN → RuntimeError（exit 13 契约，不静默）
    class _ApiBad:
        def pr_list(self, **kw):
            return unknown("HTTP 503")
    monkeypatch.setattr(dev_mod, "for_profile", lambda *a, **k: _ApiBad())
    dev_mod._FORGE_API_CACHE = None                                  # 清缓存让新 for_profile 生效
    with pytest.raises(RuntimeError, match="503"):
        dev_mod._pr_automerge("auto/x", "main", "T", "B")
    _reset_dev_forge(dev_mod)                                        # 还原基线


def test_dev_agent_rest_config_error_to_runtime_error(dev_mod, monkeypatch):
    from forge_api import ForgeConfigError
    _reset_dev_forge(dev_mod)
    dev_mod._FORGE_CONF.update(forge="gitlab", forge_host="h")

    def boom(*a, **k):
        raise ForgeConfigError("gitlab_host 缺失")
    monkeypatch.setattr(dev_mod, "for_profile", boom)
    with pytest.raises(RuntimeError, match="forge 配置错"):
        dev_mod._forge_dev_api()
    _reset_dev_forge(dev_mod)


def test_dev_agent_publish_rest_face(dev_mod, monkeypatch, tmp_path):
    """run_publish_phase REST 分流：查重命中 → 复用 url 不再 create；不命中 → create。"""
    _reset_dev_forge(dev_mod)
    dev_mod._FORGE_CONF.update(forge="gitcode")

    def _git(a):
        if a[:2] == ["diff", "--cached"]:    # 非空暂存 diff → 走过 commit/push 到 PR 分流
            return " src/x | 2 +-\n 1 file changed"
        return ""
    monkeypatch.setattr(dev_mod, "git", _git)

    class _Api:
        def __init__(self):
            self.created = False
        def pr_list(self, state="open", head=None, limit=100):
            return found([] if self.created else [{"number": 3, "url": "http://pr/3"}], "")
        def pr_create(self, base, head, title, body):
            self.created = True; return found({"number": 8, "url": "http://pr/8"}, "")
    api = _Api()
    monkeypatch.setattr(dev_mod, "for_profile", lambda *a, **k: api)
    monkeypatch.setattr(dev_mod, "repo_full_path", lambda r: "o/r")
    args = {"branch": "auto/x", "base": "main", "prd": str(tmp_path / "p.md")}
    rc = dev_mod.run_publish_phase(args)                             # 命中 → 复用（不 create）
    assert rc == 0 and api.created is False
    api2 = _Api(); api2.created = True                               # 强制不命中 → create
    monkeypatch.setattr(dev_mod, "for_profile", lambda *a, **k: api2)
    dev_mod._FORGE_API_CACHE = None
    rc2 = dev_mod.run_publish_phase(args)
    assert rc2 == 0
    _reset_dev_forge(dev_mod)


def test_dev_agent_publish_forge_config_error_exit14(dev_mod, monkeypatch, tmp_path, capsys):
    """审核修复 M2：REST 面 401/403（ForgeConfigError）分桶 exit 14（permanent，hub 队列不重派），不与 13 混。"""
    from forge_api import ForgeConfigError
    _reset_dev_forge(dev_mod)
    dev_mod._FORGE_CONF.update(forge="gitcode")

    def _git(a):
        if a[:2] == ["diff", "--cached"]:
            return " src/x | 2 +-\n 1 file changed"
        return ""
    monkeypatch.setattr(dev_mod, "git", _git)

    class _Api:
        def pr_list(self, state="open", head=None, limit=100):
            raise ForgeConfigError("认证/权限失败 HTTP 401")
    monkeypatch.setattr(dev_mod, "for_profile", lambda *a, **k: _Api())
    monkeypatch.setattr(dev_mod, "repo_full_path", lambda r: "o/r")
    rc = dev_mod.run_publish_phase({"branch": "auto/x", "base": "main", "prd": str(tmp_path / "p.md")})
    assert rc == 14                                        # 永久失败分桶（非 13——13 会被 hub 无差别重派）
    assert "永久失败" in capsys.readouterr().out           # JSON error 文案带永久标记（控制面可见）
    _reset_dev_forge(dev_mod)
