#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""transform.py — vault → template 去个人化机械转换（template-release D2/D5，task 2.1）。

设计约束（`openspec/changes/productize-pa-template/specs/template-release/spec.md`）：
- 转换 MUST 全部机械（确定性 pattern 替换，无 LLM/无交互）、**幂等**（对已转换文本再跑 = 零改动）、
  输出**可 diff**（release 落盘后与源逐文件可比对，审计友好）。
- 幂等语义三分支：`old` 在文本中 → 执行替换（transformed）；`old` 不在但 `new` 在 → 已应用
  （already，幂等命中）；两者都不在 → **MismatchError fail-loud**（上游改了锚文本，规则须人工
  更新——绝不静默跳过，见 CLAUDE.md「无静默失败」）。
- vault 是开发真源、保留个人配置（newland/sina 通道、roc 路径）；template 投影只含 generic 形态。
  本模块是 vault→template 的**唯一**转换通道（D1：投影无其他内容来源）。

个人痕迹侦查结论（2026-10-20，task 2.1 首查 + task 2.2 冒烟修正）：个人痕迹共 5 个文件——
smtp_send.py（docstring + PROFILES 两大块）、install_cron.sh（Keychain/pass 提示 2 行）、
run_cron.sh（SMTP 通道注释块）、run_daily.py（收件人硬编码 + 凭据提示 2 处）、CONTEXT.md
（roc 路径 3 行）。`.claude/agents/`、`openspec/specs/`、`docs/adr/` 零真实命中（grep 'roc'
命中皆为 process/proceed 英文内嵌）。首查曾误判「scripts/ 除 smtp_send.py 外零痕迹」，
task 2.2 冒烟的 fail-loud 失配暴露漏了 3 个文件——机制有效性的实证；规则集之外文件 =
passthrough 直拷，release_template.sh 的 grep 门禁兜底捕捉规则遗漏。

CLI（release_template.sh 调用）：
  python3 transform.py --vault <vault_root> --out <dst_root> --file <relpath> [--file ...]
  退出码：0 = 全部成功（transformed/already/passthrough）｜2 = 规则失配（MUST 阻断发布）
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

# ════════════════════════════════════════════════════════════════════════════
# 规则表（vault 相对路径 → (old 锚文本, new 替换文本)）
# ════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class TransformRule:
    relpath: str
    old: str
    new: str


_SMTP_DOCSTRING_OLD = r'''For future Claude：这是 pa-report 的「有活即发」投递工具，从既有
`Projects/部门工作跟踪/邮件跟踪/小组周报/20260331/发送邮件.py` 通用化而来。
关键差异（为何能 cron 无人值守）：密码**不交互、不进仓**——从系统凭据库读
（macOS=Keychain，Linux=pass；用户一次性写入），脚本本身永不接触明文密码。
另支持 PA_SMTP_PASSWORD_FILE 环境变量（两平台通用回退：指向含授权码的文件）。

支持多外发通道（profile）：
  newland（默认）—— 公司 Exchange：smtp.newland.com.cn:587 STARTTLS；
                   本机被代理 fake-ip 拦时走 DavMail 本地中继 127.0.0.1:1025（不广播
                   STARTTLS → send() 自动跳过、明文 AUTH LOGIN，仅本机回环）。
  sina            —— dvs@vip.sina.com：smtp.vip.sina.com:465 隐式 SSL（SMTPS）。

用法：
  # newland（默认，本机走 DavMail 中继）
  PA_SMTP_HOST=127.0.0.1 PA_SMTP_PORT=1025 python3 smtp_send.py --self-test

  # sina 通道一键切（隐式 SSL，无需 env）
  python3 smtp_send.py --profile sina --self-test

  # 发报告（newland）
  python3 smtp_send.py --subject "项目推进 20260715｜1 待 review / 0 failing" \\
               --body-file 项目推进/项目推进报告_20260715.md \\
               --to juyf@newland.com.cn --attach 项目推进/项目推进报告_20260715.md

优先级：命令行 flag > 环境变量(PA_SMTP_HOST/PA_SMTP_PORT/PA_SMTP_SSL) > profile > 内置默认。
新通道只需在 PROFILES 加一项，无需改 send()/parse_args() 主体。

一次性写凭据（**用户本人执行**，提示时输入 SMTP 密码/授权码；脚本永远不碰明文）：
  macOS Keychain:
    newland:  security add-generic-password -s newland-smtp -a juyf@newland.com.cn -w
    sina:     security add-generic-password -s sina-smtp    -a dvs@vip.sina.com -w
  Linux pass（先 pass init <GPG key ID> 初始化密码库）:
    pass insert smtp/newland
    pass insert smtp/sina
  （sina 需先在邮箱设置开启 SMTP 并取「授权码」，非登录密码）'''

_SMTP_DOCSTRING_NEW = r'''For future Claude：这是 pa-report 的「有活即发」投递工具。
关键差异（为何能 cron 无人值守）：密码**不交互、不进仓**——从系统凭据库读
（macOS=Keychain，Linux=pass；用户一次性写入），脚本本身永不接触明文密码。
另支持 PA_SMTP_PASSWORD_FILE 环境变量（两平台通用回退：指向含授权码的文件）。

外发通道：内置仅一个 example 演示通道（smtp.example.com），**无任何真实通道**。
真实部署三选一：① 设 PA_SMTP_HOST/PA_SMTP_PORT/PA_SMTP_SENDER 环境变量；
② 在 PROFILES 加自己的通道（见下方规则）；③ 用 --profile 指定自加通道。

用法：
  # env 驱动（推荐，零改动）
  PA_SMTP_HOST=smtp.example.com PA_SMTP_PORT=587 python3 smtp_send.py --self-test

  # 发报告
  python3 smtp_send.py --subject "项目推进 报告主题｜1 待 review / 0 failing" \\
               --body-file 项目推进/项目推进报告_YYYYMMDD.md \\
               --to you@example.com --attach 项目推进/项目推进报告_YYYYMMDD.md

优先级：命令行 flag > 环境变量(PA_SMTP_HOST/PA_SMTP_PORT/PA_SMTP_SSL) > profile > 内置默认。
新通道只需在 PROFILES 加一项，无需改 send()/parse_args() 主体。

一次性写凭据（**用户本人执行**，提示时输入 SMTP 密码/授权码；脚本永远不碰明文）：
  macOS Keychain:
    security add-generic-password -s example-smtp -a you@example.com -w
  Linux pass（先 pass init <GPG key ID> 初始化密码库）:
    pass insert smtp/example'''

_SMTP_PROFILES_OLD = r'''PROFILES: dict[str, dict] = {
    "newland": {
        "smtp_host": "smtp.newland.com.cn",
        "smtp_port": 587,
        "sender": "juyf@newland.com.cn",
        "keychain_service": "newland-smtp",
        "keychain_account": "juyf@newland.com.cn",
        "pass_path": "smtp/newland",
        "ssl": False,
    },
    "sina": {
        "smtp_host": "smtp.vip.sina.com",
        "smtp_port": 465,
        "sender": "dvs@vip.sina.com",
        "keychain_service": "sina-smtp",
        "keychain_account": "dvs@vip.sina.com",
        "pass_path": "smtp/sina",
        "ssl": True,
    },
}
DEFAULT_PROFILE = "sina"   # 默认外发通道：sina=dvs@vip.sina.com:465 SSL；newland=公司Exchange/DavMail 备选'''

_SMTP_PROFILES_NEW = r'''PROFILES: dict[str, dict] = {
    "example": {
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "sender": "you@example.com",
        "keychain_service": "example-smtp",
        "keychain_account": "you@example.com",
        "pass_path": "smtp/example",
        "ssl": False,
    },
}
DEFAULT_PROFILE = "example"   # 演示通道：真实部署走 PA_SMTP_* env 或在此加自己的通道（见模块 docstring）'''

# ── install_cron.sh：SMTP 凭据一次性写入提示（macOS / Linux 两分支）──────────

_CRON_MAC_HINT_OLD = '    echo "   security add-generic-password -s sina-smtp -a dvs@vip.sina.com -w   # 默认 sina 通道"'
_CRON_MAC_HINT_NEW = '    echo "   security add-generic-password -s example-smtp -a you@example.com -w   # example 通道"'

_CRON_PASS_HINT_OLD = '    echo "   pass insert smtp/sina         # 默认 sina 通道"'
_CRON_PASS_HINT_NEW = '    echo "   pass insert smtp/example        # example 通道"'

# ── run_cron.sh：SMTP 通道选择注释块 ─────────────────────────────────────────

_RUN_CRON_SMTP_COMMENT_OLD = '''# SMTP 默认走 sina 通道（smtp_send.py 默认 profile=sina：dvs@vip.sina.com:465 SSL 直连）。
# 欲改走公司 Exchange/DavMail：export PA_PROFILE=newland
#   （本机代理 fake-ip 拦 smtp.newland.com.cn 时，再加 PA_SMTP_HOST=127.0.0.1 PA_SMTP_PORT=1025
#    走 DavMail 中继，并需 DavMail 常驻）
# sina 凭据缺（macOS Keychain / Linux pass 未写授权码）则邮件失败 rc=2、但报告仍落盘（_smtp_notify 退化不阻塞）。'''

_RUN_CRON_SMTP_COMMENT_NEW = '''# SMTP 默认走 example 演示通道（smtp_send.py 内置仅 example；真实通道部署时经 PA_SMTP_* env
# 或在 PROFILES 自行添加——见 smtp_send.py docstring）。
# 凭据缺（macOS Keychain / Linux pass 未写授权码）则邮件失败 rc=2、但报告仍落盘（_smtp_notify 退化不阻塞）。'''

# ── run_daily.py：报告收件人硬编码 + 凭据缺失提示（_smtp_notify 内）─────────

_RUN_DAILY_RECIPIENT_OLD = '''    # 收件人：观察期发自己（dvs），稳定后改回 juyf@newland.com.cn（居燕峰）——2026-07-17 上线初用户决策。
    # 如需更灵活可挪到 profile/env（PA_REPORT_TO），当前按用户选择保持简单硬编码 + 醒目注释。
    report_to = "dvs@vip.sina.com"   # 观察期；稳定后改 "juyf@newland.com.cn"'''

_RUN_DAILY_RECIPIENT_NEW = '''    # 收件人：实例维护者邮箱（部署时改成自己的地址；template 不内置任何真实收件人）。
    # 如需更灵活可挪到 profile/env（PA_REPORT_TO），当前按用户选择保持简单硬编码 + 醒目注释。
    report_to = "you@example.com"   # 部署时改成真实收件人'''

_RUN_DAILY_CRED_HINT_OLD = '''        # 凭据缺失（macOS Keychain / Linux pass）；smtp_send stderr 已打平台对应的修复指引，此处补一句 sina 默认。
        cred_hint = ("security add-generic-password -s sina-smtp -a dvs@vip.sina.com -w"
                     if sys.platform == "darwin" else "pass insert smtp/sina")'''

_RUN_DAILY_CRED_HINT_NEW = '''        # 凭据缺失（macOS Keychain / Linux pass）；smtp_send stderr 已打平台对应的修复指引，此处补一句 example 默认。
        cred_hint = ("security add-generic-password -s example-smtp -a you@example.com -w"
                     if sys.platform == "darwin" else "pass insert smtp/example")'''

# ── smtp_send.py 残余：send() docstring 环境注记 + --profile help 通道列表 ───

_SMTP_SEND_DOC_OLD = '''    use_ssl=True 走隐式 SSL（SMTPS，如 sina 465）；否则明文/STARTTLS——
    服务器广播 STARTTLS 才升密（DavMail 127.0.0.1:1025 不广播 → 明文，仅本机回环）。'''

_SMTP_SEND_DOC_NEW = '''    use_ssl=True 走隐式 SSL（SMTPS，常见于 465 端口）；否则明文/STARTTLS——
    服务器广播 STARTTLS 才升密（不广播则明文——仅建议本机回环场景）。'''

_SMTP_PROFILE_HELP_OLD = '''             "sina=dvs@vip.sina.com(465/SSL)，newland=公司Exchange/DavMail(587/STARTTLS)",'''

_SMTP_PROFILE_HELP_NEW = '''             "example=smtp.example.com(587/STARTTLS)",'''

# ── RUNBOOK.md：roc 代理提法 + §11 双 checkout 实例特定拓扑（task 6.3 交付 template 版前过渡）──

_RUNBOOK_PROXY_OLD = '2. CI runner 装好 roc 代理 claude CLI（见 cron 链路 PATH 约定）。'
_RUNBOOK_PROXY_NEW = '2. CI runner 装好接 LLM 代理的 claude CLI（见 cron 链路 PATH 约定）。'

_RUNBOOK_TOPOLOGY_OLD = '''**双 checkout 拓扑**——vault 有两个 checkout，职责不同：

| | 运行时真身机（roc mac） | 本服务器（Linux 开发/评审 checkout） |
|---|---|---|
| 路径 | `/Users/roc/…`（vault + 目标仓 workspace 双检出） | `/mnt/disk01/workspaces/worksummary/agentworkflow` |
| cron | ✅ 03:17 `run_cron.sh`（`install_cron.sh` 装） | ❌ 无 |
| `.project-auto/`（profiles、sources.yaml、state） | ✅ 存在（gitignore，绝不入仓，故**只在此机**） | ❌ 不存在（冷 checkout） |
| 目标仓 worktree | ✅ `<repo>/.worktrees/`（dispatch 建） | ❌ 无目标仓 |
| 职责 | 流水线每日运行 | 编辑 pa 源码、评审、git 操作 |

**路径真相**：CONTEXT.md / SPEC.md / 旧报告中的 `/Users/roc/*` 路径指运行时真身机，在本服务器上无效（对应物是 `/mnt/disk01/…`）；两 checkout 经 `github.com/jyf2100/agentworkflow` remote 同步。**在冷 checkout 上跑 `run_daily.py` 会因 `.project-auto` 缺失而无法全链运行**（VAULT_ROOT 推导正常但 profiles/sources 不在）——这是预期行为，不是 bug。恢复/迁移运行时 = 从真身机搬 `.project-auto/`（含 profiles、sources.yaml、去重清单；state 可选）到目标机 vault 根。'''

_RUNBOOK_TOPOLOGY_NEW = '''**单实例单机拓扑**——每个 template 实例 = 一台运行机上的一个控制面仓 checkout：cron（可选，`install_cron.sh` 装）+ `.project-auto/`（profiles、sources.yaml、state；gitignore 绝不入仓）+ 目标仓 worktree（dispatch 建）同机共存。

**路径真相**：`.project-auto/` 不入 git，clone 出的 checkout 是冷的——流水线因 profiles/sources 缺失无法全链运行是预期行为，不是 bug；实例初始化见 bootstrap。实例迁移 = 整体搬控制面仓 checkout + `.project-auto/` 到新机。'''

# task 6.3：RUNBOOK 投影插入使用者 §0（升级/恢复入口）。锚为「劈开式」——old 连续串在 new 中被打断
# （`---` 后接 `## 0.` 而非 `## 1.`），保证第二遍转换 old 不再命中 → already（幂等）。
_RUNBOOK_USAGE_OLD = '''---

## 1. Journal'''

_RUNBOOK_USAGE_NEW = '''---

## 0. template 实例：升级与常见入口

- **升级** = `git pull` → 重跑 `bash bootstrap.sh`（根目录或 `Projects/项目推进流水线/bootstrap/` 皆可）。
  bootstrap 的本地改动检测会对 `scripts/`、`.claude/agents/`、`openspec/` 与 template 基线 diff：
  漂移 → 警告 + 回流指引（不覆盖不阻断）；`config/`、`.project-auto/` 是实例数据，不检测不覆盖。
- **常见恢复入口**：journal 损坏 → §1（`recovery_cli.py`，exit 2 = manual_block 是显式阻断待人工，
  不是失败）；crash 后副作用对账 → §2；跑级中断 → `run_daily.py --from-stage <stage>` 断点续跑
  （复用已有 state 产物）；run 进行中无法启动新 run（`.run.lock` 互斥，确信无活进程才 `--break-lock`）。
- 部署拓扑与路径真相见本文 §11（template 投影后为单实例单机）。

## 1. Journal'''

# F1（本体论 review 2026-10-20）：上游身份标识 jyf2100 去个人化（门禁 pattern 同步增项）。
# F5（同轮复审）：机器路径 /mnt/disk01 去个人化（与门禁既有 /Users/roc 同类——机器身份标识）。
# vault 源不动（真源保留维护者便利默认）；投影逐条占位化——实例侧误跑 drill/canary 时
# gh 查 OWNER/REPO 不存在 → UNKNOWN → fail-safe 阻断，绝不误碰上游真仓；机器路径同理
# （同事机器无该路径 → canary 自检 fail / ponytail 插件加载失败，自然 fail-safe）。
# apply_rule 是 count=1 单点替换，多处出现须逐锚一条（幂等：全清后 old 不在、new 在 → already）。
_UPSTREAM_ID_PAIRS = (
    ("Projects/项目推进流水线/scripts/runtime_evidence.py",
     'def real_crash_restart_drill(workdir: Path, gh_repo: str = "jyf2100/agentworkflow") -> dict:',
     'def real_crash_restart_drill(workdir: Path, gh_repo: str = "OWNER/REPO") -> dict:'),
    ("Projects/项目推进流水线/scripts/runtime_evidence.py",
     'def real_dispatch_skip_dev(workdir: Path, gh_repo: str = "jyf2100/agentworkflow") -> dict:',
     'def real_dispatch_skip_dev(workdir: Path, gh_repo: str = "OWNER/REPO") -> dict:'),
    ("Projects/项目推进流水线/scripts/runtime_evidence.py",
     'def real_allowlist_rollout(workdir: Path, gh_repo: str = "jyf2100/agentworkflow") -> dict:',
     'def real_allowlist_rollout(workdir: Path, gh_repo: str = "OWNER/REPO") -> dict:'),
    ("Projects/项目推进流水线/scripts/runtime_evidence.py",
     'def real_cutover_suite(workdir: Path, gh_repo: str = "jyf2100/agentworkflow",',
     'def real_cutover_suite(workdir: Path, gh_repo: str = "OWNER/REPO",'),
    ("Projects/项目推进流水线/scripts/runtime_evidence.py",
     'def real_session_lifecycle(workdir: Path, gh_repo: str = "jyf2100/agentworkflow") -> dict:',
     'def real_session_lifecycle(workdir: Path, gh_repo: str = "OWNER/REPO") -> dict:'),
    ("Projects/项目推进流水线/scripts/runtime_evidence.py",
     '# 本地仓库路径（repo_owner_repo 跑 git -C <path> remote get-url → jyf2100/agentworkflow）',
     '# 本地仓库路径（repo_owner_repo 跑 git -C <path> remote get-url → 解析实例仓 owner/repo）'),
    ("Projects/项目推进流水线/scripts/canary_automerge.sh",
     'OWNER_REPO="jyf2100/cc-web-control"',
     'OWNER_REPO="<owner>/<repo>"   # 维护者侧 canary 目标仓；实例侧使用前显式设置'),
    ("Projects/项目推进流水线/scripts/run_daily.py",
     '"""从 git remote origin 提取 owner/repo（如 jyf2100/cc-web-control）。失败返 None。"""',
     '"""从 git remote origin 提取 owner/repo。失败返 None。"""'),
    ("Projects/项目推进流水线/scripts/test_single_flight.py",
     'OWNER = "jyf2100/cc-web-control"',
     'OWNER = "example/repo"'),
    ("Projects/项目推进流水线/scripts/test_single_flight.py",
     'assert SF._safe_name("jyf2100/cc-web-control") == "jyf2100__cc-web-control"',
     'assert SF._safe_name("example/repo") == "example__repo"'),
    ("Projects/项目推进流水线/RUNBOOK.md",
     'remote + gh 认证（jyf2100）+ main 双检出 bug 已修（detached checkout，commit 59e780a）。',
     'remote + gh 认证 + main 双检出 bug 已修（detached checkout，commit 59e780a）。'),
    # ── F5：机器路径 3 处（canary 目标仓默认值 + PA_PONYTAIL_DIR 默认值 + 断言镜像）──
    ("Projects/项目推进流水线/scripts/canary_automerge.sh",
     'CC_WEB="${PA_CC_WEB:-/mnt/disk01/workspaces/worksummary/cc-web-control}"',
     'CC_WEB="${PA_CC_WEB:?须设置 PA_CC_WEB=canary 目标仓本地路径}"'),
    ("Projects/项目推进流水线/scripts/run_daily.py",
     '"/mnt/disk01/workspaces/worksummary/pa-ponytail/ponytail")',
     '"~/pa-ponytail/ponytail")'),
    ("Projects/项目推进流水线/scripts/test_run_persona_contract.py",
     'assert cmd[cmd.index("--plugin-dir") + 1] == "/mnt/disk01/workspaces/worksummary/pa-ponytail/ponytail"',
     'assert cmd[cmd.index("--plugin-dir") + 1] == "~/pa-ponytail/ponytail"'),
)

RULES: tuple[TransformRule, ...] = (
    TransformRule(
        relpath="Projects/项目推进流水线/scripts/smtp_send.py",
        old=_SMTP_DOCSTRING_OLD,
        new=_SMTP_DOCSTRING_NEW,
    ),
    TransformRule(
        relpath="Projects/项目推进流水线/scripts/smtp_send.py",
        old=_SMTP_PROFILES_OLD,
        new=_SMTP_PROFILES_NEW,
    ),
    TransformRule(
        relpath="Projects/项目推进流水线/CONTEXT.md",
        old="派发到**白名单项目**（roc 自有的 workspace 仓）",
        new="派发到**白名单项目**（白名单准入的项目仓）",
    ),
    TransformRule(
        relpath="Projects/项目推进流水线/CONTEXT.md",
        old="roc 自有的代码仓库（github.com/jyf2100/ 或 okwinds/ 下），位于 /Users/roc/workspace/，有 GitHub remote，经白名单准入。",
        new="白名单准入的代码仓库（有 GitHub remote，本地路径由实例 profiles 配置——见 RUNBOOK 部署拓扑节）。",
    ),
    TransformRule(
        relpath="Projects/项目推进流水线/CONTEXT.md",
        old="只有 roc 明确要求时才重新投递。",
        new="只有实例维护者明确要求时才重新投递。",
    ),
    TransformRule(
        relpath="Projects/项目推进流水线/scripts/install_cron.sh",
        old=_CRON_MAC_HINT_OLD,
        new=_CRON_MAC_HINT_NEW,
    ),
    TransformRule(
        relpath="Projects/项目推进流水线/scripts/install_cron.sh",
        old=_CRON_PASS_HINT_OLD,
        new=_CRON_PASS_HINT_NEW,
    ),
    TransformRule(
        relpath="Projects/项目推进流水线/scripts/run_cron.sh",
        old=_RUN_CRON_SMTP_COMMENT_OLD,
        new=_RUN_CRON_SMTP_COMMENT_NEW,
    ),
    TransformRule(
        relpath="Projects/项目推进流水线/scripts/run_daily.py",
        old=_RUN_DAILY_RECIPIENT_OLD,
        new=_RUN_DAILY_RECIPIENT_NEW,
    ),
    TransformRule(
        relpath="Projects/项目推进流水线/scripts/run_daily.py",
        old=_RUN_DAILY_CRED_HINT_OLD,
        new=_RUN_DAILY_CRED_HINT_NEW,
    ),
    TransformRule(
        relpath="Projects/项目推进流水线/scripts/smtp_send.py",
        old=_SMTP_SEND_DOC_OLD,
        new=_SMTP_SEND_DOC_NEW,
    ),
    TransformRule(
        relpath="Projects/项目推进流水线/scripts/smtp_send.py",
        old=_SMTP_PROFILE_HELP_OLD,
        new=_SMTP_PROFILE_HELP_NEW,
    ),
    TransformRule(
        relpath="Projects/项目推进流水线/RUNBOOK.md",
        old=_RUNBOOK_PROXY_OLD,
        new=_RUNBOOK_PROXY_NEW,
    ),
    TransformRule(
        relpath="Projects/项目推进流水线/RUNBOOK.md",
        old=_RUNBOOK_TOPOLOGY_OLD,
        new=_RUNBOOK_TOPOLOGY_NEW,
    ),
    TransformRule(
        relpath="Projects/项目推进流水线/RUNBOOK.md",
        old=_RUNBOOK_USAGE_OLD,
        new=_RUNBOOK_USAGE_NEW,
    ),
    *[TransformRule(relpath=r, old=o, new=n) for r, o, n in _UPSTREAM_ID_PAIRS],
)


class TransformMismatchError(RuntimeError):
    """规则锚文本在 vault 源与规则表中均不存在——上游已改，规则须人工更新（fail-loud）。"""


# ════════════════════════════════════════════════════════════════════════════
# 纯函数核心（测试接缝：apply_rule / transform_text / transform_tree）
# ════════════════════════════════════════════════════════════════════════════


def apply_rule(text: str, rule: TransformRule) -> tuple[str, str]:
    """对 text 应用单条规则，返回 (新文本, 状态)。

    状态：transformed（本次替换）| already（幂等命中：new 已在、old 已无）。
    两者皆无 → TransformMismatchError（不静默跳过）。
    """
    if rule.old in text:
        return text.replace(rule.old, rule.new, 1), "transformed"
    if rule.new in text:
        return text, "already"
    raise TransformMismatchError(
        f"规则失配（{rule.relpath}）：锚文本与替换文本均不在源中——vault 源已变，须人工更新 RULES。\n"
        f"  锚开头 80 字符：{rule.old[:80]!r}"
    )


def transform_text(relpath: str, text: str) -> tuple[str, dict[str, str]]:
    """对单文件文本应用其全部规则（按 RULES 声明序）。返回 (新文本, {锚开头: 状态})。"""
    statuses: dict[str, str] = {}
    for rule in RULES:
        if rule.relpath == relpath:
            text, status = apply_rule(text, rule)
            statuses[rule.old[:60]] = status
    return text, statuses


def transform_tree(vault_root: Path, out_root: Path, relpaths: list[str]) -> dict[str, dict[str, str]]:
    """把 vault 相对路径集写到 out_root（有规则→转换；无规则→passthrough 直拷）。

    返回 {relpath: {锚: 状态}}（passthrough 文件值为 {}）。任一规则失配即抛
    TransformMismatchError（调用方 release_template.sh 以非零退出阻断发布）。
    """
    report: dict[str, dict[str, str]] = {}
    for rel in relpaths:
        src = vault_root / rel
        if not src.is_file():
            raise FileNotFoundError(f"manifest 路径不存在：{src}")
        text = src.read_text(encoding="utf-8")
        new_text, statuses = transform_text(rel, text)
        dst = out_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(new_text, encoding="utf-8")
        report[rel] = statuses
    return report


# ════════════════════════════════════════════════════════════════════════════
# CLI（release_template.sh 调用）
# ════════════════════════════════════════════════════════════════════════════


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="vault → template 去个人化机械转换（幂等、可 diff）")
    parser.add_argument("--vault", required=True, type=Path, help="vault 根（开发真源）")
    parser.add_argument("--out", required=True, type=Path, help="输出根（template 工作副本）")
    parser.add_argument("--file", action="append", required=True, dest="files",
                        help="vault 相对路径（可多次；MANIFEST 逐行展开）")
    args = parser.parse_args(argv)
    try:
        report = transform_tree(args.vault.resolve(), args.out.resolve(), args.files)
    except TransformMismatchError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2
    for rel, statuses in report.items():
        summary = ", ".join(f"{s}" for s in statuses.values()) or "passthrough"
        print(f"  {rel}: {summary}")
    print(f"✓ transform 完成（{len(report)} 文件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
