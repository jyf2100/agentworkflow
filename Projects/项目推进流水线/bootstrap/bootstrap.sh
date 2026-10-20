#!/usr/bin/env bash
# bootstrap.sh — template 实例化唯一入口（template-bootstrap spec；productize-pa-template §3）。
#
# 同事 clone template 后跑本脚本完成实例化；升级 = `git pull` + 重跑（D1 本地改动检测护住分叉）。
# 步骤序（前置失败即停，MUST NOT 静默继续）：
#   [1] 前置冒烟：Python ≥3.11 ＋ claude CLI（--version ＋ 端点连通一轮真对话）
#   [2] 依赖安装：pip install -e ".[dev]"（编排器核心；webapp 面板另装 ".[webapp]"）
#   [3] 实例数据初始化：建 config/ 与仓根 .project-auto/profiles/，样例仅缺失时落（绝不覆盖实例数据）
#   [4] 环境冲突检测（D4）：用户级 ~/.claude/agents/ 同名 persona → 列出 + 默认拒绝；settings 交互点 → 警示
#   [5] 本地改动检测（D1 机制化）：scripts/、.claude/agents/、openspec/ vs git 基线 → 漂移警告 + diff 摘要 + 回流指引（不覆盖）
#   [6] 可选项缺省关闭：cron 交互可选（跳过即不写 crontab）；SMTP 通知默认关（不配任何 env，报告落盘即止）
#
# 用法：bash bootstrap.sh [--non-interactive]
#   --non-interactive：跳过一切交互（cron=不装；persona 冲突=直接退出 2 而非询问）
# 退出码：0 成功 | 2 前置失败/冲突默认拒绝 | 3 用法错误
set -euo pipefail

NON_INTERACTIVE=0
[[ "${1:-}" == "--non-interactive" ]] && NON_INTERACTIVE=1
[[ $# -eq 0 || $NON_INTERACTIVE -eq 1 ]] || { echo "用法：bootstrap.sh [--non-interactive]" >&2; exit 3; }

# ── 布局探测：template 根（release 投影把本脚本复到根）或 vault 开发布局（<pa>/bootstrap/）──
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -d "$SELF_DIR/Projects/项目推进流水线" ]]; then
  TOP="$SELF_DIR"                                    # template 根布局
elif [[ -f "$SELF_DIR/../pyproject.toml" ]]; then
  TOP="$(cd "$SELF_DIR/../../.." && pwd)"            # vault 布局：<vault>/<pa>/bootstrap → vault 根（三层上）
else
  echo "✗ 无法定位控制面根（bootstrap 须位于 template 根或 <pa>/bootstrap/）" >&2; exit 2
fi
PA_DIR="$TOP/Projects/项目推进流水线"
SAMPLES_DIR="$PA_DIR/bootstrap/samples"
PA_REL="${PA_DIR#"$TOP"/}"
die() { echo "✗ [bootstrap] $*" >&2; exit 2; }

# ── [1] 前置冒烟 ─────────────────────────────────────────────────────────────
echo "▶ [1/6] 前置冒烟"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
  || die "Python ≥3.11 必需（当前：$(python3 --version 2>&1)）。装好后重跑。"
command -v claude >/dev/null 2>&1 \
  || die "claude CLI 不可用——需要同事自备已登录的 claude CLI（详见 template CLAUDE.md 模型层说明）。"
claude --version >/dev/null 2>&1 || die "claude --version 失败——CLI 存在但不可执行，检查安装。"
echo "ok" | claude -p "只回复 ok" --max-turns 1 >/dev/null 2>&1 \
  || die "claude 端点连通冒烟失败——CLI 在但模型端点不可达（登录/代理配置见 template CLAUDE.md）。"
echo "  ✓ Python ≥3.11 ＋ claude CLI 冒烟通过"

# ── [2] 依赖安装 ─────────────────────────────────────────────────────────────
echo "▶ [2/6] 依赖安装（pip install -e .[dev]）"
( cd "$PA_DIR" && python3 -m pip install -q -e ".[dev]" ) \
  || die "pip install -e \".[dev]\" 失败——检查网络/代理后重跑；webapp 面板另需 \".[webapp]\"。"
echo "  ✓ 依赖就绪"

# ── [3] 实例数据初始化（只建缺失，绝不覆盖）────────────────────────────────
echo "▶ [3/6] 实例数据初始化"
# 实例数据根 = 仓根 .project-auto/（对齐 run_daily：PA_HOME=VAULT_ROOT/.project-auto——profiles、
# sources.yaml、state 全在此；config/ 在 pa 目录内，仅收纳样例，无代码消费路径约束）
INSTANCE_DIR="$TOP/.project-auto"
mkdir -p "$PA_DIR/config" "$INSTANCE_DIR/profiles"
install_sample() {  # install_sample <样例> <目标>：目标已存在 → 保留实例数据
  local src="$1" dst="$2"
  if [[ ! -e "$dst" ]]; then cp "$src" "$dst"; echo "  + 已建 $(realpath --relative-to="$TOP" "$dst")（样例）"; fi
}
[[ -f "$SAMPLES_DIR/sources.yaml" ]] && install_sample "$SAMPLES_DIR/sources.yaml" "$INSTANCE_DIR/sources.yaml"
# profile 样例装到 config/（不进 profiles glob——load_profiles 读 *.yaml，样例带 admission:true
# 若落 profiles/ 会被当真仓接管投递到占位 repo；复制改好后才移入 profiles/）
[[ -f "$SAMPLES_DIR/profile.example.yaml" ]] && install_sample "$SAMPLES_DIR/profile.example.yaml" "$PA_DIR/config/profile.example.yaml"
[[ -f "$SAMPLES_DIR/notify.example.env" ]] && install_sample "$SAMPLES_DIR/notify.example.env" "$PA_DIR/config/notify.example.env"
echo "  ✓ 实例数据就绪（config/、.project-auto/ 为合法本地改动区，升级不检测不覆盖）"

# ── [4] 环境冲突检测（D4）────────────────────────────────────────────────────
echo "▶ [4/6] 环境冲突检测"
USER_AGENTS="$HOME/.claude/agents"
CONFLICTS=()
if [[ -d "$TOP/.claude/agents" && -d "$USER_AGENTS" ]]; then
  for f in "$TOP/.claude/agents"/*.md; do
    [[ -e "$USER_AGENTS/$(basename "$f")" ]] && CONFLICTS+=("$(basename "$f")")
  done
fi
if [[ ${#CONFLICTS[@]} -gt 0 ]]; then
  echo "  ⚠ 用户级 ~/.claude/agents/ 存在与 template 同名 persona：" >&2
  printf '    %s\n' "${CONFLICTS[@]}" >&2
  if [[ $NON_INTERACTIVE -eq 1 ]]; then
    die "persona 冲突且非交互模式——默认拒绝继续。请改名/移走用户级同名文件后重跑（不静默合并）。"
  fi
  read -r -p "  以上用户级同名 persona 与 template 的交互可能互相干扰，仍继续？[y/N] " ans
  [[ "$ans" == "y" || "$ans" == "Y" ]] || die "已拒绝（默认）——处理冲突文件后重跑 bootstrap。"
fi
if [[ -f "$HOME/.claude/settings.json" ]] && grep -qE '"(hooks|env)"' "$HOME/.claude/settings.json" 2>/dev/null; then
  echo "  ⚠ ~/.claude/settings.json 含 hooks/env 配置（交互点）——与 persona 链路可能相互影响，请自行确认。"
fi
echo "  ✓ 冲突检测完成"

# ── [5] 本地改动检测（D1 机制化，升级护栏）──────────────────────────────────
echo "▶ [5/6] 本地改动检测（受检：$PA_REL/scripts、.claude/agents、openspec）"
if git -C "$TOP" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  DRIFT="$(git -C "$TOP" status --porcelain -- "$PA_REL/scripts" ".claude/agents" "openspec" 2>/dev/null || true)"
  if [[ -n "$DRIFT" ]]; then
    echo "  ⚠ 检出本地漂移（受检路径与 template 基线不一致）：" >&2
    printf '    %s\n' "$DRIFT" >&2
    git -C "$TOP" diff --stat -- "$PA_REL/scripts" ".claude/agents" "openspec" 2>/dev/null >&2 || true
    echo "  → 这些路径是 template 投影（非实例数据）：bug/改进请回流上游（vault 修复 → 下次 release），" >&2
    echo "    本脚本不会覆盖它们；继续使用前请自行确认漂移含义。" >&2
  else
    echo "  ✓ 受检路径与基线一致"
  fi
else
  echo "  （非 git 目录——跳过基线 diff，建议以 git clone 方式使用 template）"
fi

# ── [6] 可选项（缺省关闭）────────────────────────────────────────────────────
echo "▶ [6/6] 可选项（cron / SMTP 通知，均缺省关）"
INSTALL_CRON=0
if [[ $NON_INTERACTIVE -eq 0 ]]; then
  read -r -p "  安装每日 03:17 cron 自动运行？[y/N] " ans
  [[ "$ans" == "y" || "$ans" == "Y" ]] && INSTALL_CRON=1
fi
if [[ $INSTALL_CRON -eq 1 ]]; then
  bash "$PA_DIR/scripts/install_cron.sh" || die "cron 安装失败——可稍后手动重试 scripts/install_cron.sh。"
else
  echo "  ✓ 未装 cron（跳过即不写任何 crontab；手动触发：python3 $PA_REL/scripts/run_daily.py）"
fi
echo "  ✓ SMTP 通知默认关（不配 env 即关，报告落盘即止；启用样例见 config/notify.example.env）"

echo
echo "✓ bootstrap 完成。下一步："
echo "  1) 准入目标仓：python3 $PA_REL/scripts/admit_repo.py <owner/repo>"
echo "  2) 手动注入 PRD：python3 $PA_REL/scripts/run_daily.py --from-stage inject"
echo "  3) （可选）webapp 面板：pip install -e \".[webapp]\" 后见 webapp/ 说明"
