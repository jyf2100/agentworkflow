#!/usr/bin/env bash
# install_cron.sh — 把项目推进流水线接入 crontab（SPEC §6：每天 03:17 自动跑全流程
# → 出报告 + SMTP 简讯；SMTP 直发无 Foxmail GUI / 3am 登录依赖）。
#
# ⚠️ 系统级改动：由用户**本人在终端**执行（L15 模式：Claude 只备料）。
# 幂等：重复跑自动去重，只保留一条指向 run_cron.sh 的 cron 行。
#
# 用法：  bash Projects/项目推进流水线/scripts/install_cron.sh
# 卸载：  crontab -l | grep -v run_cron.sh | crontab -
set -euo pipefail

# VAULT：PA_VAULT 环境变量覆盖 > 从脚本自身位置上溯推导（scripts/ → 项目推进流水线/ → Projects/ → vault 根）。
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VAULT="${PA_VAULT:-$(cd "$SCRIPT_DIR/../../.." && pwd)}"
WRAPPER="$VAULT/Projects/项目推进流水线/scripts/run_cron.sh"
LOG="$VAULT/.project-auto/state/cron.log"
LINE="17 3 * * * $WRAPPER >> $LOG 2>&1"   # 03:17 避开整点（SPEC §6）

# 去重后追加（保留用户其余 crontab 条目）
(crontab -l 2>/dev/null | grep -vF "$WRAPPER" || true; echo "$LINE") | crontab -

echo "✅ 已接入 crontab："
echo "   $LINE"
echo "   日志：$LOG"
echo

case "$(uname -s)" in
  Darwin)
    echo "⚠️ macOS 须授予执行 cron 的进程「完全磁盘访问权限」"
    echo "   （系统设置 → 隐私与安全性 → 完全磁盘访问权限 → 添加 /usr/sbin/cron），"
    echo "   否则 cron 读写 vault / 目标仓会被静默拒绝。"
    echo
    echo "⚠️ SMTP 简讯需先一次性写入 Keychain（本人执行，提示时输 SMTP 授权码）："
    echo "   security add-generic-password -s sina-smtp -a dvs@vip.sina.com -w   # 默认 sina 通道"
    ;;
  *)
    echo "⚠️ SMTP 简讯需先一次性写入 pass（本人执行，提示时输 SMTP 授权码）："
    echo "   pass init '<GPG key ID>'     # 仅首次初始化密码库"
    echo "   pass insert smtp/sina         # 默认 sina 通道"
    echo
    echo "⚠️ cron 非交互态读 pass 需 gpg-agent 已缓存 GPG 密码，否则 rc=2（报告仍落盘）。"
    echo "   建议配 ~/.gnupg/gpg-agent.conf 的 default-cache-ttl 为较长值。"
    ;;
esac
echo
echo "   自测：python3 $VAULT/Projects/项目推进流水线/scripts/smtp_send.py --self-test"
