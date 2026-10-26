"""forge_api.py — 三平台 Git 托管 API 适配层（OpenSpec forge-adapter / add-forge-adapter D1-D4）。

github / gitlab（自建 API v4）/ gitcode（api.gitcode.com/api/v5，Gitee 血统）统一四操作契约：

    pr_list(state, head, limit)   在途/全量 PR 列表（归一字段面 number/url/state/head/base）
    pr_create(base, head, title, body)
    pr_merge(number, title, body) 双 parent merge（GitLab 前置校验 merge method + merge 后验证）
    branch_protection(branch)     分支保护实查（准入用）

三态 fail-safe 语义（OpenSpec fail-safe-dispatch；判据按端点分型，D2）：

    列表端点（pr_list / branch_protection 查询）  404 → UNKNOWN（看不见项目——GitLab 对无权限故意
        返 404，绝不等于「无 PR」，否则 fail-open）；200（含空列表）→ FOUND。
    单对象端点（pr_merge 目标 PR）                 404 → NOT_FOUND（对象确定不存在）。
    401/403 → ForgeConfigError（permanent 配置类确定性失败）——dispatch 消费面映射为单项目终态
        （skip + 高声暴露），不进瞬态重试臂、不炸整段；pr_merge 的 405/409 亦 permanent。
    网络/超时/5xx/429/坏 JSON → UNKNOWN（净化原因，绝不带 token 片段）。

FOUND-空 → 调用点按既有语义消费（``_lookup_pr`` 空 = 无 PR；``count_inflight_prs`` 空 = 0 在途）；
映射不在 adapter 内做（adapter 不感知调用语境）。

平台差异归一（D3/spec）：
    state 封闭枚举 ``{open, merged, closed}``（opened/progress→open；gitlab ``locked`` 保守归 open
    算在途——防 single-flight 低报超额投递；rejected→closed）。
    GitLab v4 **无请求级 merge_method 参数**（merge method 是项目级设置，2026-10-23 审核纠偏）：
    pr_merge 前置校验项目设置（≠ "merge" 即 permanent 报错）+ merge 后验证双 parent（项目配 squash
    会静默产单 parent，正是 revert 对账最怕的形态）。

仓库身份（D4）：GitLab 路径可多级（组/子组/仓），``repo_full_path`` 提取 remote URL 尾部全路径
（两级形态与旧 ``repo_owner_repo`` 逐字节一致）；GitLab API 侧 URL-encode 全路径。dispatch 身份键
= host × 全路径二元组（两个 host 同路径是两个仓，不共享锁；github.com host 恒定，身份键序列化
省略 host 即旧 owner/repo 形态——github 现产键零变化）。

github 分支（D1）：不做全 REST 化。按调用面提供两个命名入口（共享命令构造）：
    dev-agent 面（cwd 推断 + 抛异常契约）：``dev_pr_list_open / dev_pr_create / dev_pr_merge`` 返回
        gh argv（不含 "gh" 前缀），dev-agent 经自身 ``gh()`` 执行——argv 与既有基线逐 token 一致。
    run_daily 面（``-R`` + 三态契约）：``branch_protection / pr_lookup / pr_list_open / pr_scan_all``
        内部 subprocess gh，三态判据 = 旧 run_daily 四函数原判据（退出码 + stderr 文案），argv 逐
        token 一致（行为等价：装载代码因分流而变，github 无 forge 声明时行为不变）。

凭据（D5）：REST token 经 env（GITLAB_TOKEN / GITCODE_TOKEN）由本模块直接读取，绝不进 argv /
URL / 错误消息（``sanitize`` + 错误体截断）。git 远程操作凭据（GIT_ASKPASS）不归本模块——见
run_daily askpass 接线（tasks 2.4）。

纯 stdlib（urllib）零新依赖（镜像零变更），同目录扁平 import（external_state 三态基座复用，
不重复造）。单测经 monkeypatch ``_RestApi._http`` 注入状态码/响应体（无真网络）。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request

from external_state import ExtResult, ExtState, found, not_found, unknown  # 三态基座（纯 stdlib；found/not_found/unknown 内部已 sanitize）

# ── 平台常量 ─────────────────────────────────────────────────────────
FORGE_GITHUB = "github"
FORGE_GITLAB = "gitlab"
FORGE_GITCODE = "gitcode"
VALID_FORGES = (FORGE_GITHUB, FORGE_GITLAB, FORGE_GITCODE)

#: forge → 平台 token env 键（「一键一平台」，命名不漂移；hub vault / 本机 env 同键名）
FORGE_TOKEN_ENV = {FORGE_GITHUB: "GH_TOKEN", FORGE_GITLAB: "GITLAB_TOKEN", FORGE_GITCODE: "GITCODE_TOKEN"}

#: gitcode 缺省 host（国内 SaaS 单实例；gitlab 必须显式 host——自建实例不猜）
GITCODE_API = "https://api.gitcode.com/api/v5"

#: REST 超时（秒）——对齐 run_daily gh 子进程 timeout=20 量级
_REST_TIMEOUT = 20

#: PR state 封闭枚举（归一目标；spec forge-adapter「平台差异归一」）
STATE_OPEN, STATE_MERGED, STATE_CLOSED = "open", "merged", "closed"

#: 平台原生 state → 封闭枚举（locked/progress 保守归 open：在途形态，防 single-flight 低报超额投递）
_STATE_MAP = {
    "open": STATE_OPEN, "opened": STATE_OPEN, "progress": STATE_OPEN, "locked": STATE_OPEN,
    "merged": STATE_MERGED,
    "closed": STATE_CLOSED, "rejected": STATE_CLOSED,
}


class ForgeConfigError(Exception):
    """配置类确定性失败（permanent）——不进瞬态重试臂。

    触发面：非法 forge 值 / gitlab 缺 host / 缺平台 token / REST 401/403（token 无效或权限不足）/
    pr_merge 405/409（merge 前置条件不满足）/ GitLab 项目 merge method ≠ merge commit / merge 后
    单 parent（squash 形态暴露）。dispatch 消费面映射为**单项目终态**（skip + 高声暴露），
    不炸整段、不影响同批其他项目。消息已 sanitize（不含 token 片段）。
    """


# ── 仓库身份（D4：host × 全路径）────────────────────────────────────
def remote_host_and_path(url: str) -> tuple[str | None, str | None]:
    """从 git remote URL 提取 (host, 仓库全路径)。多级路径（GitLab 组/子组/仓）全保留。

    ``git@host:group/sub/repo.git`` / ``https://host/group/sub/repo.git`` → ("host", "group/sub/repo")。
    提不出 → (None, None)。"""
    url = (url or "").strip()
    m = re.match(r"^(?:https?://|git@)([^/:]+)[/:](.+?)(?:\.git)?/?$", url)
    if not m:
        return None, None
    return m.group(1), m.group(2)


def repo_full_path(repo: str) -> str | None:
    """本地仓路径 → remote origin 仓库全路径（多级）。失败返 None。

    两级形态（owner/repo）与旧 ``run_daily.repo_owner_repo`` 正则结果**逐字节一致**（尾部全路径
    正则在两级输入上退化为同一捕获），github 命令面（-R owner/repo）零变化。"""
    return remote_origin(repo)[1]


def remote_origin(repo: str) -> tuple[str | None, str | None]:
    """本地仓路径 → (host, 仓库全路径)（git remote get-url origin 一次提取）。失败 (None, None)。

    dispatch 身份键（D4）构造用：github.com host 恒定 → dispatch_identity 退化两级裸形态；
    其余 host → host/full/path。与 repo_full_path 同源（不重复跑 git）。"""
    try:
        r = subprocess.run(["git", "-C", repo, "remote", "get-url", "origin"],
                           capture_output=True, text=True, timeout=15)
        return remote_host_and_path(r.stdout.strip())
    except Exception:
        return None, None


def dispatch_identity(host: str | None, full_path: str) -> str:
    """dispatch 身份键（single-flight 锁 / already_dispatched / 分支查重）：host × 全路径二元组。

    两个 host 同路径是两个仓，不共享锁。github.com host 恒定 → 序列化省略 host 前缀
    （= 旧 owner/repo 形态，github 现产锁键零变化）；其余 host 显式 ``host/full/path``。"""
    if host in (None, "github.com"):
        return full_path
    return f"{host}/{full_path}"


# ── REST 基座（gitlab/gitcode 共用；github 走 gh wrapper 不经此类）────
class _RestApi:
    """REST 平台公共基座：urllib 请求 + 三态判据按端点分型（D2）。

    子类给 ``api_root`` / ``headers`` / ``api_repo``（平台仓库路径段）。单测 monkeypatch
    ``_http`` 注入 (status, body_text)——无真网络。"""

    def __init__(self, forge: str, token_env_key: str, token: str):
        self.forge = forge
        self.token_env_key = token_env_key
        self.token = token

    # 子类钩子
    api_root: str = ""
    api_repo: str = ""   # URL 里的仓库路径段（gitlab=urlencoded 全路径；gitcode=owner/repo）

    def headers(self, extra: dict | None = None) -> dict:
        raise NotImplementedError

    def _http(self, method: str, path: str, *, query: dict | None = None,
              body: dict | None = None) -> tuple[int, str]:
        """真网络请求（单测 monkeypatch 点）。返回 (status, body_text)。网络层异常抛 URLError 等。"""
        url = f"{self.api_root}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        for k, v in self.headers().items():
            req.add_header(k, v)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=_REST_TIMEOUT) as resp:
                return resp.status, resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", errors="replace")

    # ── 三态判据（D2 端点分型）──
    def _call(self, method: str, path: str, *, query=None, body=None,
              list_endpoint: bool, on_409: str = "raise") -> ExtResult:
        """统一请求 → 三态。``list_endpoint=True``：404→UNKNOWN（看不见项目）；
        False（单对象）：404→NOT_FOUND。401/403 → ForgeConfigError；405 恒 permanent；
        409 分面（审核修复 M6）：``on_409="raise"``（缺省，merge 面——不可 merge 冲突重试必败）/
        ``"unknown"``（pr_create 面——同 head PR 已存在，返回 UNKNOWN 供调用点幂等 recheck，对齐
        gh 面 A5）。429/5xx/网络/坏 JSON → UNKNOWN。"""
        try:
            status, text = self._http(method, path, query=query, body=body)
        except ForgeConfigError:
            raise
        except Exception as e:   # URLError / timeout / ConnectionError 等
            return unknown(f"{self.forge} 网络异常: {type(e).__name__}")
        if status in (401, 403):
            raise ForgeConfigError(
                f"{self.forge} 认证/权限失败（HTTP {status}，{self.token_env_key} 无效或权限不足）")
        if status == 404:
            if list_endpoint:
                return unknown(f"{self.forge} 404 看不见项目（无权限/路径错；≠ 无 PR，fail-safe）")
            return not_found(f"{self.forge} 对象不存在（404）")
        if status in (405, 409):
            # merge 前置条件不满足（405 方法不允许 / 409 冲突——如已 merge/不可 merge）：
            # permanent（重试必败），pr_merge 面消费；列表查询面不会收到（GET 无此形态）。
            # 例外（审核修复 M6）：pr_create 面 409 = 同 head PR 已存在——幂等 recheck 信号非失败
            if status == 409 and on_409 == "unknown":
                return unknown(f"{self.forge} PR 已存在（409 同 head 撞名，幂等 recheck 面）")
            raise ForgeConfigError(f"{self.forge} HTTP {status}（merge 前置条件不满足或冲突）")
        if status == 429 or status >= 500:
            return unknown(f"{self.forge} HTTP {status}（限速/服务端，可重试面）")
        if status >= 400:
            # 错误体一律不进 reason：平台错误体形态各异（不可信）且是主要泄漏面（token 片段可能
            # 出现在任何平台的错误回显里，白名单式脱敏防不住未知前缀）——只报状态码，诊断价值
            # 够用（状态码已区分限速/服务端/客户端错），安全面收最紧
            return unknown(f"{self.forge} HTTP {status}（客户端错误）")
        try:
            return found(json.loads(text) if text.strip() else {})
        except json.JSONDecodeError as e:
            return unknown(f"{self.forge} 坏 JSON: {e}")

    # ── 归一（平台差异只在 adapter 边界做一层）──
    @staticmethod
    def _norm_state(raw) -> str:
        s = _STATE_MAP.get(str(raw or "").lower())
        return s if s else STATE_OPEN   # 未知原生态保守归 open（算在途，fail-safe 方向）

    def _norm_pr(self, pr: dict, *, number_key: str, url_key: str,
                 head_key: str, base_key: str) -> dict:
        return {"number": pr.get(number_key), "url": pr.get(url_key) or "",
                "state": self._norm_state(pr.get("state")),
                "head": pr.get(head_key) or "", "base": pr.get(base_key) or ""}


# ── GitLab（自建，API v4，PAT PRIVATE-TOKEN）─────────────────────────
class _GitlabApi(_RestApi):
    def __init__(self, host: str, full_path: str, token: str | None = None):
        token = token if token is not None else os.environ.get(FORGE_TOKEN_ENV[FORGE_GITLAB], "")
        if not token:
            raise ForgeConfigError("gitlab 缺 GITLAB_TOKEN（声明 forge: gitlab 则必需；hub vault 注入或本机 env）")
        super().__init__(FORGE_GITLAB, FORGE_TOKEN_ENV[FORGE_GITLAB], token)
        if not host:
            raise ForgeConfigError("gitlab 缺 gitlab_host（自建实例不猜，profile 显式声明）")
        self.host = host.rstrip("/")
        self.api_root = f"https://{self.host}/api/v4"
        self.project = urllib.parse.quote(full_path, safe="")   # v4 官方形态：URL-encode 全路径
        self.api_repo = self.project

    def headers(self, extra=None):
        return {"PRIVATE-TOKEN": self.token}

    # 四操作（外部 state 枚举 {open,merged,closed,all} → v4 {opened,merged,closed,all}）
    def pr_list(self, state: str = STATE_OPEN, head: str | None = None,
                limit: int = 50) -> ExtResult:
        q: dict = {"state": {"open": "opened", "merged": "merged",
                             "closed": "closed", "all": "all"}[state], "per_page": min(int(limit), 100)}
        if head:
            q["source_branch"] = head
        res = self._call("GET", f"/projects/{self.project}/merge_requests",
                         query=q, list_endpoint=True)
        if res.state is not ExtState.FOUND:
            return res
        prs = [self._norm_pr(p, number_key="iid", url_key="web_url",
                             head_key="source_branch", base_key="target_branch")
               for p in (res.value or []) if isinstance(p, dict)]
        return found(prs, f"gitlab MR {len(prs)} 条")

    def pr_create(self, base: str, head: str, title: str, body: str) -> ExtResult:
        res = self._call("POST", f"/projects/{self.project}/merge_requests",
                         body={"source_branch": head, "target_branch": base,
                               "title": title, "description": body},
                         list_endpoint=True,   # 项目级端点：404=看不见项目 → UNKNOWN
                         on_409="unknown")     # 审核修复 M6：409=同 head 已存在 → recheck 复用
        if res.state is ExtState.UNKNOWN and "409" in (res.reason or ""):
            prs = self.pr_list(state=STATE_OPEN, head=head)   # 幂等 recheck（对齐 gh 面 A5）
            if prs.state is ExtState.FOUND and prs.value:
                return found(prs.value[0], "gitlab MR 已存在（409 recheck 复用）")
            return unknown("gitlab 409 后 recheck 未命中（并发窗口，重跑收敛）")
        if res.state is not ExtState.FOUND:
            return res
        p = res.value or {}
        return found(self._norm_pr(p, number_key="iid", url_key="web_url",
                                   head_key="source_branch", base_key="target_branch"),
                     "gitlab MR 已建")

    def _project_setting(self) -> dict:
        """前置校验用：项目设置（merge method 等）。独立于三态面——配置错直接 permanent。"""
        return self._call("GET", f"/projects/{self.project}",
                          query={"simple": "true"}, list_endpoint=True)

    def pr_merge(self, number, title: str = "", body: str = "") -> ExtResult:
        # ① 前置：项目 merge method 必须 "merge"（v4 无请求级参数——2026-10-23 审核纠偏）
        proj = self._project_setting()
        if proj.state is not ExtState.FOUND:
            return unknown(f"gitlab 项目设置查询不可用: {proj.reason[:80]}")
        method = str((proj.value or {}).get("merge_method") or "merge")
        if method != "merge":
            raise ForgeConfigError(
                f"gitlab 项目 merge method = {method}（需 merge commit；squash/rebase 形态破坏双 parent "
                f"revert 对账，拒投——改项目设置或换仓）")
        # ② merge（单对象端点：404=PR 不存在 → NOT_FOUND；405/409 permanent 在 _call）
        res = self._call("PUT", f"/projects/{self.project}/merge_requests/{number}/merge",
                         body={"merge_commit_message": f"{title}\n\n{body}" if body else title},
                         list_endpoint=False)
        if res.state is not ExtState.FOUND:
            return res
        # ③ 双 parent 验证（squash 形态暴露：merge 成功但单 parent → permanent，不静默）
        return self._verify_two_parents(number)

    def _verify_two_parents(self, number) -> ExtResult:
        mr = self._call("GET", f"/projects/{self.project}/merge_requests/{number}",
                        list_endpoint=False)
        if mr.state is not ExtState.FOUND:
            return unknown(f"gitlab merge 后 MR 回查不可用: {mr.reason[:80]}")
        sha = (mr.value or {}).get("merge_commit_sha") or ""
        if not sha:
            return found(None, "gitlab merge 完成（无 merge_commit_sha 回报，跳过 parent 验证）")
        c = self._call("GET", f"/projects/{self.project}/repository/commits/{sha}",
                       list_endpoint=False)
        if c.state is not ExtState.FOUND:
            return unknown(f"gitlab merge commit 回查不可用: {c.reason[:80]}")
        parents = (c.value or {}).get("parent_ids") or []
        if len(parents) != 2:
            raise ForgeConfigError(
                f"gitlab merge commit 单 parent（{len(parents)} 个，疑似 squash 形态）——双 parent "
                f"revert 对账不可用，拒投")
        return found(sha, "gitlab MR 已 merge（双 parent 已验证）")

    def branch_protection(self, branch: str) -> ExtResult:
        # 列表端点（无单对象保护查询——单对象 404 与「未保护」不可区分，故走列表自查）
        res = self._call("GET", f"/projects/{self.project}/protected_branches",
                         query={"query": branch}, list_endpoint=True)
        if res.state is not ExtState.FOUND:
            return res
        for b in (res.value or []):
            if isinstance(b, dict) and b.get("name") == branch:
                return found(True, f"gitlab 分支 {branch} 已保护")
        return not_found(f"gitlab 分支 {branch} 未保护（拒投）")

    # ── fetch 采集面（add-forge-adapter 4.2；归一列表，客户端窗口过滤交给 forge_fetch）──
    def fetch_commits(self, limit: int = 30) -> ExtResult:
        res = self._call("GET", f"/projects/{self.project}/repository/commits",
                         query={"per_page": min(int(limit), 100)}, list_endpoint=True)
        if res.state is not ExtState.FOUND:
            return res
        return found([_norm_commit(c, sha_key="id", nested=False) for c in (res.value or [])],
                     f"gitlab commits {len(res.value or [])} 条")

    def fetch_pulls(self, limit: int = 30) -> ExtResult:
        res = self._call("GET", f"/projects/{self.project}/merge_requests",
                         query={"state": "all", "order_by": "updated_at", "sort": "desc",
                                "per_page": min(int(limit), 100)},
                         list_endpoint=True)
        if res.state is not ExtState.FOUND:
            return res
        return found([_norm_issue_like(p, pr=True) for p in (res.value or [])],
                     f"gitlab MR {len(res.value or [])} 条")

    def fetch_issues(self, limit: int = 30) -> ExtResult:
        res = self._call("GET", f"/projects/{self.project}/issues",
                         query={"scope": "all", "state": "all", "sort": "desc",
                                "per_page": min(int(limit), 100)},
                         list_endpoint=True)
        if res.state is not ExtState.FOUND:
            return res
        # 4.3：confidential=true 不进采集面（内联剔除）
        issues = [_norm_issue_like(i, pr=False)
                  for i in (res.value or []) if not (isinstance(i, dict) and i.get("confidential"))]
        return found(issues, f"gitlab issues {len(issues)} 条")


# ── GitCode（api.gitcode.com/api/v5，Gitee 血统，Bearer）──────────────
class _GitcodeApi(_RestApi):
    def __init__(self, full_path: str, token: str | None = None):
        token = token if token is not None else os.environ.get(FORGE_TOKEN_ENV[FORGE_GITCODE], "")
        if not token:
            raise ForgeConfigError("gitcode 缺 GITCODE_TOKEN（声明 forge: gitcode 则必需）")
        super().__init__(FORGE_GITCODE, FORGE_TOKEN_ENV[FORGE_GITCODE], token)
        self.api_root = GITCODE_API
        self.api_repo = full_path   # 两级 owner/repo（gitcode 无多级路径形态）

    def headers(self, extra=None):
        return {"Authorization": f"Bearer {self.token}"}

    def pr_list(self, state: str = STATE_OPEN, head: str | None = None,
                limit: int = 50) -> ExtResult:
        q: dict = {"state": {"open": "open", "merged": "merged",
                             "closed": "closed", "all": "all"}[state],
                   "per_page": min(int(limit), 100), "dir": "desc"}
        if head:
            q["head"] = head
        res = self._call("GET", f"/repos/{self.api_repo}/pulls", query=q, list_endpoint=True)
        if res.state is not ExtState.FOUND:
            return res
        prs = [self._norm_pr(p, number_key="number", url_key="html_url",
                             head_key="head", base_key="base")
               for p in (res.value or []) if isinstance(p, dict)]
        return found(prs, f"gitcode PR {len(prs)} 条")

    def pr_create(self, base: str, head: str, title: str, body: str) -> ExtResult:
        res = self._call("POST", f"/repos/{self.api_repo}/pulls",
                         body={"title": title, "head": head, "base": base, "body": body},
                         list_endpoint=True, on_409="unknown")   # 审核修复 M6：409 → recheck 复用
        if res.state is ExtState.UNKNOWN and "409" in (res.reason or ""):
            prs = self.pr_list(state=STATE_OPEN, head=head)   # 幂等 recheck（对齐 gh 面 A5）
            if prs.state is ExtState.FOUND and prs.value:
                return found(prs.value[0], "gitcode PR 已存在（409 recheck 复用）")
            return unknown("gitcode 409 后 recheck 未命中（并发窗口，重跑收敛）")
        if res.state is not ExtState.FOUND:
            return res
        p = res.value or {}
        return found(self._norm_pr(p, number_key="number", url_key="html_url",
                                   head_key="head", base_key="base"), "gitcode PR 已建")

    def pr_merge(self, number, title: str = "", body: str = "") -> ExtResult:
        # 请求级 merge_method=merge（Gitee 血统支持请求级参数；与 GitLab 项目级互补）
        res = self._call("PUT", f"/repos/{self.api_repo}/pulls/{number}/merge",
                         query={"merge_method": "merge",
                                "merge_message": (f"{title}\n\n{body}" if body else title)},
                         list_endpoint=False)
        if res.state is not ExtState.FOUND:
            return res
        # 双 parent 验证（能拿到 merge_commit_sha 就验；字段形态 fixture 锚定前拿不到则降级跳过）
        p = self._call("GET", f"/repos/{self.api_repo}/pulls/{number}", list_endpoint=False)
        if p.state is not ExtState.FOUND:
            return found(None, "gitcode PR 已 merge（回查不可用，跳过 parent 验证）")
        sha = (p.value or {}).get("merge_commit_sha") or ""
        if not sha:
            return found(None, "gitcode PR 已 merge（无 merge_commit_sha 回报，跳过 parent 验证）")
        c = self._call("GET", f"/repos/{self.api_repo}/commits/{sha}", list_endpoint=False)
        if c.state is not ExtState.FOUND:
            return found(None, "gitcode PR 已 merge（commit 回查不可用，跳过 parent 验证）")
        parents = (c.value or {}).get("parents") or []
        if len(parents) != 2:
            raise ForgeConfigError(
                f"gitcode merge commit 单 parent（{len(parents)} 个，疑似 squash 形态）——双 parent "
                f"revert 对账不可用，拒投")
        return found(sha, "gitcode PR 已 merge（双 parent 已验证）")

    def branch_protection(self, branch: str) -> ExtResult:
        # Gitee 血统：branch 详情自带 protected 布尔（无独立保护端点）。200 即决断；404 无法区分
        # 「分支不存在」与「仓不可见（无权限/路径错）」——审核修复 M5：按 spec 列表分型口径收
        # UNKNOWN（fail-safe 阻断——此前误当 NOT_FOUND「未保护→放行」可能烧 dev loop 后才在 push 暴露）
        res = self._call("GET", f"/repos/{self.api_repo}/branches/{urllib.parse.quote(branch)}",
                         list_endpoint=True)   # 404 → UNKNOWN（spec: forge-adapter 列表分型）
        if res.state is not ExtState.FOUND:
            return res
        protected = bool((res.value or {}).get("protected"))
        if protected:
            return found(True, f"gitcode 分支 {branch} 已保护")
        return not_found(f"gitcode 分支 {branch} 未保护（拒投）")

    # ── fetch 采集面（add-forge-adapter 4.2；归一列表，客户端窗口过滤交给 forge_fetch）──
    def fetch_commits(self, limit: int = 30) -> ExtResult:
        res = self._call("GET", f"/repos/{self.api_repo}/commits",
                         query={"per_page": min(int(limit), 100)}, list_endpoint=True)
        if res.state is not ExtState.FOUND:
            return res
        return found([_norm_commit(c, sha_key="sha", nested=True) for c in (res.value or [])],
                     f"gitcode commits {len(res.value or [])} 条")

    def fetch_pulls(self, limit: int = 30) -> ExtResult:
        # 审核修复 M9：补 sort=updated——默认创建序时 per_page 截断会挤掉「旧创建但近期活跃」
        # 的 PR（窗口口径是 updated_at，排序必须同口径；dir 参数名待 5.2 真数据 curl 锁定）
        res = self._call("GET", f"/repos/{self.api_repo}/pulls",
                         query={"state": "all", "per_page": min(int(limit), 100),
                                "sort": "updated", "dir": "desc"},
                         list_endpoint=True)
        if res.state is not ExtState.FOUND:
            return res
        return found([_norm_issue_like(p, pr=True) for p in (res.value or [])],
                     f"gitcode pulls {len(res.value or [])} 条")


# ── fetch 采集归一（add-forge-adapter 4.2/4.3；三平台同构封闭枚举）────
def _norm_commit(c: dict, *, sha_key: str, nested: bool) -> dict:
    """commit 归一：{sha, date(author ISO), author, message 首行}。

    ``nested=True``：GitHub/GitCode 形态（``c.commit.author.date`` / ``c.commit.message``）；
    False：GitLab v4 扁平形态（``c.author_name`` / ``c.authored_date``——author date 口径钉死）。"""
    a = (c.get("commit") or {}).get("author") or {} if nested else {
        "name": c.get("author_name"), "date": c.get("authored_date")}
    msg = ((c.get("commit") or {}).get("message") if nested else c.get("message")) or ""
    return {"sha": (c.get(sha_key) or "")[:8], "date": a.get("date") or "",
            "author": a.get("name") or "", "message": msg.splitlines()[0] if msg else ""}


def _norm_issue_like(x: dict, *, pr: bool) -> dict:
    """pull/issue 归一：{number, title, state(封闭枚举 open|merged|closed), updated_at, url, author}。

    原生态映射复用 ``_STATE_MAP`` 单表（dispatch 面同口径——locked/progress 保守 open、
    rejected 归 closed「已驳回不在途」；审核 M3：fetch/dispatch 两张表曾分叉致 gitcode rejected
    在 digest 误标 open）。未知原生态保守归 ``open``。"""
    state = _STATE_MAP.get(str(x.get("state") or "").lower(), STATE_OPEN)
    # GitHub REST /pulls 的 state 只有 open|closed——merged PR 靠 merged_at（或显式 merged 布尔）判别
    if pr and (x.get("merged_at") or x.get("merged") is True):
        state = STATE_MERGED
    u = x.get("user") or x.get("author") or {}
    return {"number": x.get("number") if "number" in x else x.get("iid"),
            "title": x.get("title") or "", "state": state,
            "updated_at": x.get("updated_at") or "", "url": x.get("web_url") or x.get("html_url") or "",
            "author": u.get("login") or u.get("username") or u.get("name") or "",
            "is_pr": pr}


class _GithubRestApi(_RestApi):
    """GitHub REST（api.github.com，Bearer）——**fetch 采集专用**（add-forge-adapter 4.2）。

    dispatch github 面保持 gh CLI 零变化（D1 两调用面不混）；fetch 走 REST 与 gitlab/gitcode
    统一（顺带获得 issues 能力）。缺 GH_TOKEN 硬错（匿名 60/h 限速形态不可依赖）。"""

    def __init__(self, owner_repo: str, token: str | None = None):
        token = token if token is not None else os.environ.get(FORGE_TOKEN_ENV[FORGE_GITHUB], "")
        if not token:
            raise ForgeConfigError("github fetch 缺 GH_TOKEN（REST 匿名限速形态不可依赖）")
        super().__init__(FORGE_GITHUB, FORGE_TOKEN_ENV[FORGE_GITHUB], token)
        self.api_root = "https://api.github.com"
        self.owner_repo = owner_repo.strip("/")
        self.api_repo = self.owner_repo

    def headers(self, extra=None):
        return {"Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json"}

    def fetch_commits(self, limit: int = 30) -> ExtResult:
        res = self._call("GET", f"/repos/{self.owner_repo}/commits",
                         query={"per_page": min(int(limit), 100)}, list_endpoint=True)
        if res.state is not ExtState.FOUND:
            return res
        return found([_norm_commit(c, sha_key="sha", nested=True) for c in (res.value or [])],
                     f"github commits {len(res.value or [])} 条")

    def fetch_pulls(self, limit: int = 30) -> ExtResult:
        res = self._call("GET", f"/repos/{self.owner_repo}/pulls",
                         query={"state": "all", "sort": "updated", "per_page": min(int(limit), 100)},
                         list_endpoint=True)
        if res.state is not ExtState.FOUND:
            return res
        return found([_norm_issue_like(p, pr=True) for p in (res.value or [])],
                     f"github pulls {len(res.value or [])} 条")

    def fetch_issues(self, limit: int = 30) -> ExtResult:
        res = self._call("GET", f"/repos/{self.owner_repo}/issues",
                         query={"state": "all", "sort": "updated", "per_page": min(int(limit), 100)},
                         list_endpoint=True)
        if res.state is not ExtState.FOUND:
            return res
        # 4.3：REST issues 端点混排 PR（带 pull_request 键）——剔除，只留真 issue
        issues = [_norm_issue_like(i, pr=False)
                  for i in (res.value or []) if not (isinstance(i, dict) and i.get("pull_request"))]
        return found(issues, f"github issues {len(issues)} 条")


def for_fetch(forge: str, repo: str, *, host: str | None = None) -> "_RestApi":
    """fetch 采集面的 adapter 工厂（add-forge-adapter 4.2）：三平台统一 commits/pulls 面。

    github → REST（``_GithubRestApi``，dispatch 的 gh CLI 面不混）；gitlab → v4（host 必需）；
    gitcode → v5。forge 非法 raise ForgeConfigError（不静默回落）。"""
    f = (forge or FORGE_GITHUB).strip().lower()
    if f == FORGE_GITHUB:
        return _GithubRestApi(repo)
    if f == FORGE_GITLAB:
        return _GitlabApi(host or "", repo)
    if f == FORGE_GITCODE:
        return _GitcodeApi(repo)
    raise ForgeConfigError(f"非法 forge 值：{forge!r}（合法：{'/'.join(VALID_FORGES)}）")


# ── github（gh CLI thin wrapper；两调用面，不做假统一——D1）──────────
class _GithubApi:
    """github 分支：委托既有 gh 调用序列（argv 与基线逐 token 一致，行为等价）。

    run_daily 面（``-R`` + 三态）：``branch_protection`` / ``pr_lookup`` / ``pr_list_open`` /
    ``pr_scan_all``——判据 = 旧 run_daily 四函数原判据（退出码 + stderr 文案），缺 gh/超时/非零
    均 UNKNOWN（gh 形态凭证缺失也 UNKNOWN——REST 401/403 的 permanent carve-out 不适用 gh）。
    dev-agent 面（cwd + 抛异常）：``dev_*`` 返回 gh argv（不含前缀），dev-agent 经自身 ``gh()``
    执行（cwd=REPO_ROOT 推断 owner/repo，无 -R）。"""

    def __init__(self, owner_repo: str):
        self.owner_repo = owner_repo

    # ── run_daily 面（三态）──
    def _run(self, argv: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(["gh", *argv], capture_output=True, text=True, timeout=20)

    def branch_protection(self, branch: str) -> ExtResult:
        """准入分支保护实查（旧 check_branch_protection 判据原样：200=保护 / 404=未保护 / 其余 UNKNOWN）。"""
        try:
            r = self._run(["api", f"repos/{self.owner_repo}/branches/{branch}/protection", "--silent",
                           "-H", "Accept: application/vnd.github+json"])
            if r.returncode == 0:
                return found(True, "已保护")
            if "404" in (r.stderr or ""):
                return not_found("未保护（404），拒投")
            return unknown(f"查询失败 rc={r.returncode}: {(r.stderr or '').strip()[:120]}")
        except subprocess.TimeoutExpired:
            return unknown("查询超时")
        except FileNotFoundError:
            return unknown("缺 gh 命令（未安装/不在 PATH）")
        except Exception as e:
            return unknown(f"查询异常: {e}")

    def pr_lookup(self, branch: str) -> ExtResult:
        """reconcile 对账：该分支的 PR（旧 _lookup_pr 判据原样）。FOUND-空 由调用点映射（空=无 PR）。"""
        try:
            r = self._run(["pr", "list", "-R", self.owner_repo, "--head", branch, "--state", "all",
                           "--limit", "5", "--json", "number,url,state"])
            if r.returncode != 0:
                return unknown(f"gh pr list rc={r.returncode}: {(r.stderr or '').strip()[:120]}")
            prs = json.loads(r.stdout or "[]")
            if prs:
                return found(prs[0])
            return not_found(f"无 {branch} 的 PR")
        except json.JSONDecodeError as e:
            return unknown(f"坏 PR JSON: {e}")
        except subprocess.TimeoutExpired:
            return unknown("PR 查询超时")
        except FileNotFoundError:
            return unknown("缺 gh 命令")
        except Exception as e:
            return unknown(f"PR 查询异常: {e}")

    def pr_list_open(self, limit: int = 50) -> ExtResult:
        """在途开放 PR 数（旧 count_inflight_prs 判据原样；FOUND(count)，0 亦 FOUND）。"""
        try:
            r = self._run(["pr", "list", "-R", self.owner_repo, "--state", "open",
                           "--limit", str(limit), "--json", "number"])
            if r.returncode != 0:
                return unknown(f"gh pr list rc={r.returncode}: {(r.stderr or '').strip()[:120]}")
            return found(len(json.loads(r.stdout or "[]")))
        except json.JSONDecodeError as e:
            return unknown(f"坏 JSON: {e}")
        except subprocess.TimeoutExpired:
            return unknown("查询超时")
        except FileNotFoundError:
            return unknown("缺 gh 命令（未安装/不在 PATH）")
        except Exception as e:
            return unknown(f"查询异常: {e}")

    def pr_scan_all(self, limit: int = 100, fields: str = "number,headRefName,state") -> ExtResult:
        """幂等查重：全态 PR 列表（旧 _scan_pr_list_for_slug 判据原样；FOUND(list)）。"""
        try:
            r = self._run(["pr", "list", "-R", self.owner_repo, "--state", "all",
                           "--limit", str(limit), "--json", fields])
            if r.returncode != 0:
                return unknown(f"gh pr list rc={r.returncode}: {(r.stderr or '').strip()[:120]}")
            return found(json.loads(r.stdout or "[]"))
        except json.JSONDecodeError as e:
            return unknown(f"坏 PR JSON: {e}")
        except subprocess.TimeoutExpired:
            return unknown("PR 查询超时")
        except FileNotFoundError:
            return unknown("缺 gh 命令")
        except Exception as e:
            return unknown(f"PR 查询异常: {e}")

    # ── dev-agent 面（argv 构造；与 dev-agent.py 既有 gh 序列逐 token 一致）──
    @staticmethod
    def dev_pr_list_open(head: str) -> list[str]:
        return ["pr", "list", "--head", head, "--state", "open", "--json", "number"]

    @staticmethod
    def dev_pr_list_open_url(head: str) -> list[str]:
        return ["pr", "list", "--head", head, "--state", "open", "--json", "number,url"]

    @staticmethod
    def dev_pr_create(base: str, head: str, title: str, body: str) -> list[str]:
        return ["pr", "create", "--base", base, "--head", head, "--title", title, "--body", body]

    @staticmethod
    def dev_pr_merge(number: str, title: str, body: str) -> list[str]:
        return ["pr", "merge", number, "--merge", "--subject", title, "--body", body]


# ── 工厂（唯一构造入口）─────────────────────────────────────────────
def for_profile(prof: dict | None, *, full_path: str | None = None,
                owner_repo: str | None = None) -> _RestApi | _GithubApi:
    """profile → 平台适配器。forge 缺省 github（现产零变化）；非法值 fail-fast 不回落 github。

    gitlab：profile ``gitlab_host`` 必需（缺 → ForgeConfigError）；仓库身份 = ``full_path``
    （多级全路径，URL-encode 进 API）。gitcode：单实例无 host；两级路径。github：``owner_repo``
    （-R 命令面身份）。token 各平台经 env（缺 → ForgeConfigError，准入早期 fail-fast 同款判据）。"""
    prof = prof or {}
    forge = prof.get("forge") or FORGE_GITHUB
    if forge not in VALID_FORGES:
        raise ForgeConfigError(f"非法 forge 值：{forge!r}（合法：{'/'.join(VALID_FORGES)}；不回落 github）")
    if forge == FORGE_GITHUB:
        if not owner_repo:
            raise ForgeConfigError("github 分支缺 owner_repo（-R 命令面身份）")
        return _GithubApi(owner_repo)
    if not full_path:
        raise ForgeConfigError(f"{forge} 缺仓库全路径（repo_full_path 提取失败？）")
    if forge == FORGE_GITLAB:
        return _GitlabApi(host=str(prof.get("gitlab_host") or ""), full_path=full_path)
    return _GitcodeApi(full_path=full_path)


def validate_profile(prof: dict | None) -> None:
    """装载期校验（准入早期 fail-fast，tasks 2.1）：非法 forge 值 / gitlab 缺 host / 声明 forge
    而缺对应平台 token env——不烧 dev loop 才在首个远程调用处炸。github/未声明零校验（零行为变化）。"""
    prof = prof or {}
    forge = prof.get("forge")
    if not forge or forge == FORGE_GITHUB:
        return
    if forge not in VALID_FORGES:
        raise ForgeConfigError(f"非法 forge 值：{forge!r}（合法：{'/'.join(VALID_FORGES)}）")
    if forge == FORGE_GITLAB and not prof.get("gitlab_host"):
        raise ForgeConfigError("forge: gitlab 缺 gitlab_host（自建实例不猜，profile 显式声明）")
    env_key = FORGE_TOKEN_ENV[forge]
    if not os.environ.get(env_key, ""):
        raise ForgeConfigError(f"forge: {forge} 缺 {env_key}（hub vault 注入或本机 cron 环境；"
                               f"与缺 host 同级，准入期报错不烧 dev loop）")
