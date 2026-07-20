#!/usr/bin/env python3
"""
smtp_send.py — 项目推进流水线「报告自通知」SMTP 直发（cron 友好）

For future Claude：这是 pa-report 的「有活即发」投递工具，从既有
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
  （sina 需先在邮箱设置开启 SMTP 并取「授权码」，非登录密码）

退出码（供 cron/orchestrator 判断）：0=成功 | 2=凭据缺失（Keychain/pass 无条目） | 3=SMTP 连接/认证失败 | 4=参数错误
"""
from __future__ import annotations

import argparse
import os
import smtplib
import subprocess
import sys
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

# 外发通道预设。新通道在此加一项即可。
PROFILES: dict[str, dict] = {
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
DEFAULT_PROFILE = "sina"   # 默认外发通道：sina=dvs@vip.sina.com:465 SSL；newland=公司Exchange/DavMail 备选


def _env_bool(name: str, default: bool) -> bool:
    """读布尔型环境变量；未设返回 default。"""
    v = os.environ.get(name)
    if v is None:
        return default
    return v not in ("", "0", "false", "False", "no", "NO")


def read_password(service: str, account: str, pass_path: str) -> str:
    """跨平台读 SMTP 密码/授权码（永不接触明文输入）。

    优先级：PA_SMTP_PASSWORD_FILE 环境变量（两平台通用回退）> 平台默认后端。
      - macOS：Keychain（security find-generic-password -s service -a account -w）
      - Linux：pass 密码库（pass show <pass_path>）
    缺失则打印平台对应的修复指引并以码 2 退出。
    """
    env_file = os.environ.get("PA_SMTP_PASSWORD_FILE")
    if env_file:
        return Path(env_file).read_text(encoding="utf-8").strip()
    if sys.platform == "darwin":
        return _read_keychain(service, account)
    return _read_pass(pass_path)


def _read_keychain(service: str, account: str) -> str:
    """macOS：从 Keychain 读 SMTP 密码/授权码；缺失则打印修复指引并以码 2 退出。"""
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip()
        print(f"✗ Keychain 无 {service}/{account} 的密码条目（{stderr}）。", file=sys.stderr)
        print(
            "  请本人执行一次（提示时输入 SMTP 密码/授权码，脚本永不接触明文）：\n"
            f"    security add-generic-password -s {service} -a {account} -w",
            file=sys.stderr,
        )
        sys.exit(2)
    return result.stdout.strip()


def _read_pass(pass_path: str) -> str:
    """Linux：从 pass（GPG 密码库）读 SMTP 授权码；缺失则打印修复指引并以码 2 退出。"""
    try:
        result = subprocess.run(
            ["pass", "show", pass_path],
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"✗ pass 取 {pass_path} 失败（{exc}）。", file=sys.stderr)
        print(
            "  请本人执行一次（提示时输入 SMTP 授权码，脚本永不接触明文）：\n"
            f"    pass insert {pass_path}",
            file=sys.stderr,
        )
        sys.exit(2)
    return result.stdout.strip()


def build_message(
    sender,
    recipient,
    subject,
    body,
    attachments,
) -> MIMEMultipart:
    """构建邮件（纯函数：返回新对象，不改入参）。"""
    msg = MIMEMultipart()
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))
    for path in attachments:
        with open(path, "rb") as fh:
            part = MIMEApplication(fh.read(), Name=path.name)
        part["Content-Disposition"] = f'attachment; filename="{path.name}"'
        msg.attach(part)
    return msg


def send(
    sender: str,
    recipient: str,
    msg: MIMEMultipart,
    host: str,
    port: int,
    password: str,
    use_ssl: bool,
) -> None:
    """连 SMTP → 登录 → 发送（with 保证 quit）。

    use_ssl=True 走隐式 SSL（SMTPS，如 sina 465）；否则明文/STARTTLS——
    服务器广播 STARTTLS 才升密（DavMail 127.0.0.1:1025 不广播 → 明文，仅本机回环）。
    """
    if use_ssl:
        ctx = smtplib.SMTP_SSL(host, port, timeout=30)
    else:
        ctx = smtplib.SMTP(host, port, timeout=30)
    with ctx as server:
        if not use_ssl:
            server.ehlo()
            if server.has_extn("starttls"):
                server.starttls()
                server.ehlo()
        server.login(sender, password)
        server.sendmail(sender, [recipient], msg.as_string())


def resolve_transport(args: argparse.Namespace) -> tuple[str, int, bool, str, str, str, str, str]:
    """flag > env > profile 合并出最终传输参数。

    返回 (host, port, use_ssl, sender, recipient, keychain_service, keychain_account, pass_path)。
    """
    p = PROFILES[args.profile]
    host = args.smtp_host or os.environ.get("PA_SMTP_HOST") or p["smtp_host"]

    if args.smtp_port is not None:
        port = args.smtp_port
    else:
        env_port = os.environ.get("PA_SMTP_PORT")
        port = int(env_port) if env_port else p["smtp_port"]

    if args.smtp_ssl is not None:
        use_ssl = args.smtp_ssl
    elif os.environ.get("PA_SMTP_SSL") is not None:
        use_ssl = _env_bool("PA_SMTP_SSL", p["ssl"])
    else:
        use_ssl = p["ssl"]

    sender = args.sender or p["sender"]
    recipient = args.to or p["sender"]
    kc_service = args.keychain_service or p["keychain_service"]
    kc_account = args.keychain_account or p["keychain_account"]
    pass_path = p["pass_path"]
    return host, port, use_ssl, sender, recipient, kc_service, kc_account, pass_path


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="项目推进流水线报告自通知 SMTP 直发（密码从 Keychain 读，多 profile，cron 友好）"
    )
    ap.add_argument(
        "--profile",
        choices=sorted(PROFILES),
        default=os.environ.get("PA_PROFILE", DEFAULT_PROFILE),
        help=f"外发通道预设（默认 {DEFAULT_PROFILE}；可用 PA_PROFILE 环境变量覆盖）："
             "sina=dvs@vip.sina.com(465/SSL)，newland=公司Exchange/DavMail(587/STARTTLS)",
    )
    ap.add_argument("--subject", help="邮件标题（--self-test 时可省略）")
    ap.add_argument("--body", help="邮件正文（与 --body-file 二选一）")
    ap.add_argument("--body-file", help="从文件读正文")
    ap.add_argument("--to", help="收件人（默认取 profile.sender）")
    ap.add_argument(
        "--from",
        dest="sender",
        help="发件人（默认取 profile.sender）",
    )
    ap.add_argument("--attach", action="append", default=[], help="附件路径（可多次）")
    ap.add_argument("--smtp-host", help="SMTP 主机（默认 profile；可用 PA_SMTP_HOST 覆盖）")
    ap.add_argument("--smtp-port", type=int, help="SMTP 端口（默认 profile；可用 PA_SMTP_PORT 覆盖）")
    ap.add_argument(
        "--smtp-ssl",
        dest="smtp_ssl",
        action="store_true",
        default=None,
        help="强制隐式 SSL/SMTPS（默认随 profile；可用 PA_SMTP_SSL=1 覆盖）",
    )
    ap.add_argument(
        "--smtp-no-ssl",
        dest="smtp_ssl",
        action="store_false",
        help="强制不用 SSL（明文/STARTTLS）",
    )
    ap.add_argument("--keychain-service", help="Keychain service（默认 profile）")
    ap.add_argument("--keychain-account", help="Keychain account（默认 profile）")
    ap.add_argument("--self-test", action="store_true", help="发一封自测邮件")
    return ap.parse_args()


def resolve_body_and_subject(
    args: argparse.Namespace,
    host: str,
    port: int,
    use_ssl: bool,
) -> tuple[str, str]:
    """返回 (body, subject)；参数不全则以码 4 退出。"""
    tls_desc = "SSL" if use_ssl else "STARTTLS/明文"
    if args.self_test:
        subject = "【自测】项目推进 SMTP 直发通路"
        body = (
            f"这是 smtp_send.py 的自测邮件（profile={args.profile}）。收到即说明 "
            f"{host}:{port}（{tls_desc}）+ Keychain 凭据链路通畅，可被 cron 非交互调用。"
        )
        return body, subject

    if not args.subject:
        print("✗ 非 --self-test 模式需提供 --subject", file=sys.stderr)
        sys.exit(4)
    if args.body_file:
        body = Path(args.body_file).read_text(encoding="utf-8")
    elif args.body is not None:
        body = args.body
    else:
        print("✗ 需提供 --body 或 --body-file", file=sys.stderr)
        sys.exit(4)
    return body, args.subject


def main() -> int:
    args = parse_args()
    host, port, use_ssl, sender, recipient, kc_service, kc_account, pass_path = resolve_transport(args)
    body, subject = resolve_body_and_subject(args, host, port, use_ssl)

    attachments = [Path(p) for p in args.attach]
    missing = [p for p in attachments if not p.exists()]
    if missing:
        print(f"✗ 附件不存在: {missing}", file=sys.stderr)
        return 4

    password = read_password(kc_service, kc_account, pass_path)
    msg = build_message(sender, recipient, subject, body, attachments)

    try:
        send(sender, recipient, msg, host, port, password, use_ssl)
    except smtplib.SMTPAuthenticationError as exc:
        print(f"✗ SMTP 认证失败（Keychain 密码/授权码可能失效，请重写）: {exc}", file=sys.stderr)
        return 3
    except (smtplib.SMTPException, OSError) as exc:
        print(
            f"✗ SMTP 连接/发送失败（run host 可能不可达 {host}:{port}）: {exc}",
            file=sys.stderr,
        )
        return 3

    print(f"✅ 已发送 → {recipient}：{subject}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
