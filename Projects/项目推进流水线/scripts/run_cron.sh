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

# claude CLI 装在 ~/.local/bin（非 nvm node bin）；cron 极简 PATH 不含它 → 须显式补
# （2026-07-26 修：0722-0726 连挂于「✗ 找不到 claude CLI」）
export PATH="$HOME/.local/bin:$PATH"

# miniconda python：dev-agent.py 直接 import claude_agent_sdk（SDK 模式，非 CLI 子进程），
# 该包只装在 miniconda site-packages；cron 极简 PATH 解析到 /usr/bin/python3 → ModuleNotFoundError。
# （2026-07-29 修：0729 cron dev r1 崩于 dev-agent.py:46 import；persona 走 claude CLI 子进程不受影响）
export PATH="$HOME/miniconda3/bin:$PATH"

CLAUDE_BIN="$(command -v claude || true)"
[ -n "$CLAUDE_BIN" ] && export PA_CLAUDE_BIN="$CLAUDE_BIN"

# 心跳模式：report 段全绿也发一封状态邮件（无头服务器上「邮件断了 = 流水线挂了」）。
# 手动跑 run_daily.py 不设此 env，行为不变（全绿不发）。
export PA_HEARTBEAT=1

# 临时降噪（#1105，memory pa-target-plane-dev-exec-lock）：claude CLI 子进程 stdio
# can_use_tool 权限协议 bug，致 cc-web-control 等 node 项目 dispatch 反复 test_failed。
# 此 env 命中项目跳过 dispatch 段（过闸 PRD 落 status=skip，report 段可见，非静默丢弃）。
# ⚠️ 临时：上游 SDK/CLI 修 #1105 或 streaming 迁移后删此行即恢复全量 dispatch。
# export DISPATCH_SKIP_PROJECTS=cc-web-control

# SMTP 默认走 sina 通道（smtp_send.py 默认 profile=sina：dvs@vip.sina.com:465 SSL 直连）。
# 欲改走公司 Exchange/DavMail：export PA_PROFILE=newland
#   （本机代理 fake-ip 拦 smtp.newland.com.cn 时，再加 PA_SMTP_HOST=127.0.0.1 PA_SMTP_PORT=1025
#    走 DavMail 中继，并需 DavMail 常驻）
# sina 凭据缺（macOS Keychain / Linux pass 未写授权码）则邮件失败 rc=2、但报告仍落盘（_smtp_notify 退化不阻塞）。

# langgraph-workflow-upgrade task 5.2：编排器渐进 cutover 分流点（D7 flag 物理隔离）。
# PA_GRAPH_ORCHESTRATOR=1 → cron 走 graph 主图 graph_pa.py（须经 shadow parity + canary 门控；
#   coordinator.preflight 强制 orchestrator⇒shadow 依赖，orchestrator on 但 shadow off → blocked）。
# unset / =0 → 仍走 run_daily.py（baseline 不变）。秒回退：unset PA_GRAPH_ORCHESTRATOR 下个 cron
# 立即回 legacy run_daily（flag off = run_daily 完整保留，graph_pa.py 不被 run_daily import）。
if [ "${PA_GRAPH_ORCHESTRATOR:-}" = "1" ]; then
    exec python3 "$VAULT/Projects/项目推进流水线/scripts/graph_pa.py" --to-stage report
else
    exec python3 "$VAULT/Projects/项目推进流水线/scripts/run_daily.py" --to-stage report
fi
