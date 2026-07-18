#!/usr/bin/env bash
# run_cron.sh — 项目推进流水线 cron 入口包装（SPEC §6）。
#
# For future Claude：cron 非 login shell，PATH 极简（/usr/bin:/bin），找不到
# nvm 的 node/claude/python3 之外的依赖。本脚本先 source nvm 补齐 PATH + 设
# PA_CLAUDE_BIN，再 exec run_daily.py --to-stage report（全流程 + 出报告 +
# SMTP 简讯）。cron 行由 install_cron.sh 写入。
#
# 手测：bash run_cron.sh
set -euo pipefail

# VAULT：PA_VAULT 环境变量覆盖 > 从脚本自身位置上溯推导（scripts/ → 项目推进流水线/ → Projects/ → vault 根）。
# 不再硬编码绝对路径，macOS/Linux 通用。
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VAULT="${PA_VAULT:-$(cd "$SCRIPT_DIR/../../.." && pwd)}"
cd "$VAULT"   # run_daily.py 的 .project-auto 相对 cwd，必须从 vault 根跑

# 补 nvm（node + claude CLI）
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"

CLAUDE_BIN="$(command -v claude || true)"
[ -n "$CLAUDE_BIN" ] && export PA_CLAUDE_BIN="$CLAUDE_BIN"

# 心跳模式：report 段全绿也发一封状态邮件（无头服务器上「邮件断了 = 流水线挂了」）。
# 手动跑 run_daily.py 不设此 env，行为不变（全绿不发）。
export PA_HEARTBEAT=1

# SMTP 默认走 sina 通道（smtp_send.py 默认 profile=sina：dvs@vip.sina.com:465 SSL 直连）。
# 欲改走公司 Exchange/DavMail：export PA_PROFILE=newland
#   （本机代理 fake-ip 拦 smtp.newland.com.cn 时，再加 PA_SMTP_HOST=127.0.0.1 PA_SMTP_PORT=1025
#    走 DavMail 中继，并需 DavMail 常驻）
# sina 凭据缺（macOS Keychain / Linux pass 未写授权码）则邮件失败 rc=2、但报告仍落盘（_smtp_notify 退化不阻塞）。

exec python3 "$VAULT/Projects/项目推进流水线/scripts/run_daily.py" --to-stage report
