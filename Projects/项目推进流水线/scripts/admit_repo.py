#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""admit_repo.py — 目标仓准入检查器（repo-admission spec；productize-pa-template §4，D6 落位 scripts/）。

同事把一个仓交给流水线接管前的准入体检。三项主检查三态回报（FOUND/NOT_FOUND/UNKNOWN，
沿用 fail-safe-dispatch 三态语义与 reconcile.GhPrResolver 的 subprocess gh 模式——
失败/超时/鉴权问题一律 UNKNOWN，绝不当作通过或不存在）：

  [1] branch protection：GET branches/<branch> 的 protected 字段
  [2] 测试入口 scripts.test：GET contents/package.json 解析 scripts.test
  [3] git commit 身份：git config user.name / user.email（repo 级或 global）

另附第 [4] 项上下文入口 CLAUDE.md 存在性（不参与准入判定；缺失输出建议文本，同事自建）。

退出码：0 全项通过（准入）| 1 有 NOT_FOUND（未准入，附修复指引）| 2 有 UNKNOWN（blocked，
fail-safe）或 --configure-protection 确认被拒 | 3 用法错误。

平台操作白名单（R2）：仅 --configure-protection 经 gh api PUT 配 branch protection（最小语义：
要求经 PR 合入、不要求审批/checks——挡直推默认分支），操作结果 FOUND/UNKNOWN，失败即 UNKNOWN。
控制面不写目标面（R3）：本模块零写目标仓文件的代码路径，缺失项一律输出文本由同事显式应用。

用法：
  python3 admit_repo.py <owner/repo> [--branch main] [--repo-dir <目标仓本地 checkout>]
  python3 admit_repo.py <owner/repo> --configure-protection [--yes]
"""
from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass

FOUND = "FOUND"
NOT_FOUND = "NOT_FOUND"
UNKNOWN = "UNKNOWN"

GH_TIMEOUT = 15          # gh api 超时（与 reconcile.GhPrResolver 一致）
GIT_TIMEOUT = 10

# 平台操作白名单（R2）唯一成员的 payload：要求 PR 合入（reviews 0 approvals 即挡直推），
# 不强制 checks/审批（团队后续可自调）；force push / 删除默认禁止。
PROTECTION_PAYLOAD = json.dumps({
    "required_status_checks": None,
    "enforce_admins": False,
    "required_pull_request_reviews": {"required_approving_review_count": 0},
    "restrictions": None,
    "allow_force_pushes": False,
    "allow_deletions": False,
})

_GUIDE_GH_AUTH = "修复指引：`gh auth status` 确认已登录且 token 未过期；确认 owner/repo 拼写与可见权限；网络代理可用后重跑。"

# 目标仓 CLAUDE.md 建议起点（R3：只输出文本，绝不写文件——由同事显式创建）
CLAUDE_MD_SUGGEST = """\
# CLAUDE.md（建议起点——admit 不写目标仓文件，以下由你自行创建/调整）

## 这个仓是什么
<一两句话：项目职责与边界>

## 怎么跑测试
npm test        # scripts.test 是流水线测试门的标准入口

## 流水线约定（交给 pa 接管前请确认）
- commit / push / PR 前必须有新的绿色测试证据（无新绿则阻断）
- dev 产出经独立验证闸审核后才开 PR
"""


@dataclass(frozen=True)
class CheckResult:
    """单项准入检查结果。guidance 在 NOT_FOUND/UNKNOWN 时非空（修复指引是 spec 义务）。"""
    name: str
    state: str
    detail: str = ""
    guidance: str = ""

    @property
    def label(self) -> str:
        return {"branch_protection": "branch protection",
                "test_entry": "测试入口 scripts.test",
                "commit_identity": "git commit 身份",
                "context_entry": "上下文入口 CLAUDE.md"}.get(self.name, self.name)


def _gh(args: list[str], runner: Callable, *, input_text: str | None = None) -> subprocess.CompletedProcess:
    """gh api 统一执行口（三态解析交调用方；异常上抛由各 check 转 UNKNOWN）。"""
    return runner(["gh", "api", *args], capture_output=True, text=True,
                  timeout=GH_TIMEOUT, input=input_text)


def _is_http_404(r: subprocess.CompletedProcess) -> bool:
    # gh 把 HTTP 错误写进 stderr，格式稳定（`gh: Not Found (HTTP 404)`）
    return "HTTP 404" in (r.stderr or "")


def _err_tail(r: subprocess.CompletedProcess) -> str:
    lines = (r.stderr or "").strip().splitlines()
    return lines[-1] if lines else f"rc={r.returncode}"


# ─── 三项主检查 + 一项附检（全部三态）───────────────────────────────────────

def check_branch_protection(owner_repo: str, branch: str, runner: Callable) -> CheckResult:
    """[1] 默认分支保护状态。404 = 仓/分支不可见（名字错或私有无权限——GitHub 防枚举同 404）
    → UNKNOWN 阻断（不可判，不猜）；查询成功看 protected 布尔。"""
    try:
        r = _gh([f"repos/{owner_repo}/branches/{branch}"], runner)
    except Exception as exc:   # 超时/进程失败 → UNKNOWN（fail-safe）
        return CheckResult("branch_protection", UNKNOWN, f"查询失败：{exc}", _GUIDE_GH_AUTH)
    if r.returncode == 0:
        try:
            protected = json.loads(r.stdout or "").get("protected") is True
        except (json.JSONDecodeError, AttributeError):
            return CheckResult("branch_protection", UNKNOWN, "响应不是合法 JSON", _GUIDE_GH_AUTH)
        if protected:
            return CheckResult("branch_protection", FOUND, f"branch {branch} 已启用 protection")
        return CheckResult("branch_protection", NOT_FOUND, f"branch {branch} 未启用 protection",
                           "运行 admit_repo.py --configure-protection（交互确认，需 admin 权限），"
                           "或在仓 Settings → Branches 手动开启。")
    if _is_http_404(r):
        return CheckResult("branch_protection", UNKNOWN,
                           f"仓或分支不可见（404）：{owner_repo}@{branch}", _GUIDE_GH_AUTH)
    return CheckResult("branch_protection", UNKNOWN, f"gh api 失败：{_err_tail(r)}", _GUIDE_GH_AUTH)


def check_test_entry(owner_repo: str, runner: Callable) -> CheckResult:
    """[2] package.json 的 scripts.test（测试门标准入口）。404 = 仓没有该文件 → NOT_FOUND
    （明确事实，指引可行动）；有 package.json 但缺 scripts.test 同为 NOT_FOUND。"""
    try:
        r = _gh([f"repos/{owner_repo}/contents/package.json"], runner)
    except Exception as exc:
        return CheckResult("test_entry", UNKNOWN, f"查询失败：{exc}", _GUIDE_GH_AUTH)
    if r.returncode == 0:
        try:
            data = json.loads(r.stdout or "")
            pkg = json.loads(base64.b64decode(data.get("content", "")))
            has_test = bool(pkg.get("scripts", {}).get("test"))
        except Exception:
            return CheckResult("test_entry", UNKNOWN, "package.json 解析失败（非法 base64/JSON）", _GUIDE_GH_AUTH)
        if has_test:
            return CheckResult("test_entry", FOUND, f"scripts.test = {pkg['scripts']['test']!r}")
        return CheckResult("test_entry", NOT_FOUND, "package.json 无 scripts.test",
                           '在 package.json 补："scripts": {"test": "<你的测试命令>"}——'
                           "流水线测试门经此入口验证「新的绿色测试证据」。")
    if _is_http_404(r):
        return CheckResult("test_entry", NOT_FOUND, "目标仓无 package.json（无测试入口）",
                           "创建含 scripts.test 的 package.json（npm init 后补 test 脚本）；"
                           "非 npm 仓请与 template 维护者确认测试入口适配。")
    return CheckResult("test_entry", UNKNOWN, f"gh api 失败：{_err_tail(r)}", _GUIDE_GH_AUTH)


def check_commit_identity(repo_dir: str | None, runner: Callable) -> CheckResult:
    """[3] commit 身份（dev-agent 在目标仓 worktree 内 commit 的署名来源；repo 级缺省回退
    global）。任一键未配置/空值 → NOT_FOUND；git 自身失败 → UNKNOWN。"""
    missing: list[str] = []
    for key in ("user.name", "user.email"):
        try:
            r = runner(["git", "config", key], capture_output=True, text=True,
                       timeout=GIT_TIMEOUT, cwd=repo_dir)
        except Exception as exc:
            return CheckResult("commit_identity", UNKNOWN, f"git config {key} 查询失败：{exc}",
                               "确认 git 可用（`git --version`）后重跑。")
        if r.returncode == 1:
            missing.append(key)          # 未定义（rc 1 是 git config 的明确「未配置」）
        elif r.returncode != 0:
            return CheckResult("commit_identity", UNKNOWN,
                               f"git config {key} rc={r.returncode}", "确认 git 可用后重跑。")
        elif not (r.stdout or "").strip():
            missing.append(key)          # 配了空值同样无法署名
    if missing:
        return CheckResult("commit_identity", NOT_FOUND, f"未配置：{'、'.join(missing)}",
                           'git config --global user.name "你的名字" && '
                           'git config --global user.email "你的邮箱"（或在该仓本地 `git config user.name ...`）')
    return CheckResult("commit_identity", FOUND, "user.name / user.email 已配置")


def check_context_entry(owner_repo: str, runner: Callable) -> CheckResult:
    """[4] 目标仓 CLAUDE.md 存在性（附检，不参与准入判定）：缺失 → 输出建议文本由同事自建。"""
    try:
        r = _gh([f"repos/{owner_repo}/contents/CLAUDE.md"], runner)
    except Exception as exc:
        return CheckResult("context_entry", UNKNOWN, f"查询失败：{exc}", _GUIDE_GH_AUTH)
    if r.returncode == 0:
        return CheckResult("context_entry", FOUND, "CLAUDE.md 存在")
    if _is_http_404(r):
        return CheckResult("context_entry", NOT_FOUND, "目标仓无 CLAUDE.md",
                           "（建议起点见下方输出——由你自行创建，admit 不写目标仓文件）")
    return CheckResult("context_entry", UNKNOWN, f"gh api 失败：{_err_tail(r)}", _GUIDE_GH_AUTH)


# ─── 平台操作白名单（R2 唯一成员）───────────────────────────────────────────

def configure_protection(owner_repo: str, branch: str, runner: Callable) -> CheckResult:
    """经 gh api PUT 配 branch protection。成功 → FOUND；任何失败（无 admin 权限/404/网络）
    → UNKNOWN 阻断（spec：失败即 UNKNOWN，不静默不重试）。"""
    try:
        r = _gh([f"repos/{owner_repo}/branches/{branch}/protection", "--method", "PUT",
                 "--input", "-"], runner, input_text=PROTECTION_PAYLOAD)
    except Exception as exc:
        return CheckResult("branch_protection", UNKNOWN, f"配置请求失败：{exc}", _GUIDE_GH_AUTH)
    if r.returncode == 0:
        return CheckResult("branch_protection", FOUND,
                           f"protection 已配置（要求经 PR 合入 {branch}，不要求审批/checks）")
    return CheckResult("branch_protection", UNKNOWN, f"gh api PUT 失败：{_err_tail(r)}",
                       "需要目标仓 admin 权限（403=权限不足）；`gh auth status` 确认登录；"
                       "确认 branch 名存在（404）。")


# ─── 报告与 CLI ──────────────────────────────────────────────────────────────

def _print_result(idx: int, total: int, res: CheckResult) -> None:
    mark = {"FOUND": "✓", "NOT_FOUND": "✗", "UNKNOWN": "⚠"}[res.state]
    print(f"  [{idx}/{total}] {res.label}：{res.state} {mark}")
    if res.detail:
        print(f"        {res.detail}")
    if res.guidance and res.state != FOUND:
        for line in res.guidance.splitlines() or [res.guidance]:
            print(f"        → {line}")


def _confirm_or_die(owner_repo: str, branch: str, assume_yes: bool) -> bool:
    """平台写操作前的显式确认（默认拒绝；非交互终端须 --yes）。"""
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        print("✗ 非交互终端且未给 --yes——平台写操作默认拒绝。", file=sys.stderr)
        return False
    ans = input(f"将为 {owner_repo}@{branch} 配置 branch protection（要求经 PR 合入）。确认？[y/N] ")
    return ans.strip().lower() in ("y", "yes")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="目标仓准入检查器（三态；UNKNOWN=blocked）")
    parser.add_argument("owner_repo", help="目标仓，形如 owner/repo")
    parser.add_argument("--branch", default="main", help="受检默认分支（默认 main）")
    parser.add_argument("--repo-dir", default=None,
                        help="目标仓本地 checkout 路径（git 身份检查位置；缺省=当前目录，含 global 回退）")
    parser.add_argument("--configure-protection", action="store_true",
                        help="经 gh api 配 branch protection（平台操作白名单，需确认）")
    parser.add_argument("--yes", action="store_true", help="跳过 --configure-protection 的交互确认")
    args = parser.parse_args(argv)

    if "/" not in args.owner_repo or len(args.owner_repo.split("/")) != 2:
        print("✗ owner_repo 形如 owner/repo（恰好一个斜杠）", file=sys.stderr)
        return 3
    runner: Callable = subprocess.run

    if args.configure_protection:
        print(f"▶ admit_repo：配置 branch protection（白名单操作）{args.owner_repo}@{args.branch}")
        current = check_branch_protection(args.owner_repo, args.branch, runner)
        if current.state == FOUND:
            print("  ✓ protection 已启用，无需配置")
            return 0
        if not _confirm_or_die(args.owner_repo, args.branch, args.yes):
            print("✗ 已拒绝（默认）——未执行任何平台写操作。", file=sys.stderr)
            return 2
        res = configure_protection(args.owner_repo, args.branch, runner)
        _print_result(1, 1, res)
        return 0 if res.state == FOUND else 2

    print(f"▶ admit_repo：目标仓准入检查 {args.owner_repo}（branch={args.branch}）")
    main_checks = [
        check_branch_protection(args.owner_repo, args.branch, runner),
        check_test_entry(args.owner_repo, runner),
        check_commit_identity(args.repo_dir, runner),
    ]
    ctx = check_context_entry(args.owner_repo, runner)
    for i, res in enumerate(main_checks + [ctx], 1):
        _print_result(i, 4, res)
    if ctx.state == NOT_FOUND:
        print("\n  建议的目标仓 CLAUDE.md 起点（复制后自行调整；admit 不会替你写入）：")
        for line in CLAUDE_MD_SUGGEST.splitlines():
            print(f"    | {line}")

    unknown = [r for r in main_checks if r.state == UNKNOWN]
    missing = [r for r in main_checks if r.state == NOT_FOUND]
    if unknown:
        print(f"\n✗ blocked：{len(unknown)} 项 UNKNOWN（状态不可知，fail-safe 阻断）——"
              "按各项「→ 修复指引」处理后重跑；确认前请勿注入该仓的 PRD。")
        return 2
    if missing:
        print(f"\n✗ 未准入：{len(missing)} 项缺失——按各项「→ 指引」补齐后重跑本检查。")
        return 1
    print("\n✓ 准入通过。下一步：手动注入 PRD——"
          "python3 Projects/项目推进流水线/scripts/run_daily.py --from-stage inject")
    return 0


if __name__ == "__main__":
    sys.exit(main())
