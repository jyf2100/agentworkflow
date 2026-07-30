#!/usr/bin/env bash
# canary_automerge.sh — single-flight-auto-merge §8.1 canary 门一键执行（RUNBOOK §8.3 / task 7.2）。
#
# For future Claude：这是控制面自动合 main 的 canary 运维脚本。canary 是**破坏性 outward**——会把真实
# commit 合进 cc-web-control main、留真实历史（merge/revert commit）。故：
#   - prep / verify 子命令**无害**（只读检查 + grep 判据），可自由跑；
#   - branch-protect / run-a / inject-red / check-c 子命令**改 main 或 GitHub**，必须显式 `--go` 才执行，
#     否则只打印将执行的命令（dry-run）。守 CLAUDE.md「outward/hard-to-reverse 须确认」+ memory
#     pa-test-no-dirty-data（隔离 state + 临时 log + unset PA_HEARTBEAT，不碰真实 cron.log/SMTP/日报）。
#
# 判据（RUNBOOK §8.3，全过才扩全量）：
#   (a) 正常闭环：绿 smoke → dev+verify 双绿 → merge + post-merge PASS → merged
#   (b) 故意红 auto-revert：main 合红 → post-merge FAIL → revert REVERTED → main 回绿
#   (c) 熔断：同 PRD 再投 → circuit breaker is_in_cooldown 命中 → triage(cooldown_revert_loop)
#   (d) 无 halt/CRITICAL（halt = 须人工，canary 失败信号）
#
# 用法：
#   bash scripts/canary_automerge.sh prep                 # 无害前置检查（默认）
#   bash scripts/canary_automerge.sh verify               # 无害判据 grep + journal 闭合检查
#   bash scripts/canary_automerge.sh run-a --go           # 判据 a：绿 smoke → merge + post-merge PASS（已由 #50 实质验证）
#   bash scripts/canary_automerge.sh inject-red --go      # 判据 b：注入红 merge → post-merge FAIL → revert REVERTED（待真跑）
#   bash scripts/canary_automerge.sh check-c --go         # 判据 c：注入 cooldown → 再投 → 熔断（待真跑）
#   bash scripts/canary_automerge.sh branch-protect save --go    # [可选保险] 备份 main 保护（实测 direct push 可过）
#   bash scripts/canary_automerge.sh branch-protect restore --go # [可选] 恢复
set -euo pipefail

# ── 路径推导（同 run_cron.sh：scripts/ → 项目推进流水线/ → Projects/ → vault 根）──────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VAULT="${PA_VAULT:-$(cd "$SCRIPT_DIR/../../.." && pwd)}"
PA="$VAULT/Projects/项目推进流水线"
DEV_AGENT_PY="$PA/scripts/dev-agent.py"
RUN_DAILY="$VAULT/Projects/项目推进流水线/scripts/run_daily.py"

# ── canary 目标（cc-web-control：owner/repo + 本地仓 + profile）──────────────────────────
OWNER_REPO="jyf2100/cc-web-control"
CC_WEB="${PA_CC_WEB:-/mnt/disk01/workspaces/worksummary/cc-web-control}"
GREEN_PRD="$PA/scripts/canary-green.prd.md"   # 判据 a 载体（trivial 绿 smoke）
RED_PRD="$PA/scripts/canary-red.prd.md"        # 判据 b 载体（故意红，双绿约束说明见文件内）
RED_SLUG="cc-web-control-canary-red"           # 判据 b/c 幂等键（circuit_breaker 的 _prd）

# ── PATH 补齐（同 run_cron.sh：dev-agent.py import claude_agent_sdk 在 miniconda）─────────
export NVM_DIR="$HOME/.nvm"; [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
export PATH="$HOME/.local/bin:$HOME/miniconda3/bin:$PATH"
PYTHON="$(command -v python3 || true)"

# ── canary 隔离 state + 临时 log（守 pa-test-no-dirty-data：不碰真实 .project-auto/state）──
CANARY_STATE="${PA_CANARY_STATE:-$(mktemp -d -t canary-automerge-state-XXXXXX)}"
CANARY_LOG="${PA_CANARY_LOG:-/tmp/canary-automerge-$$.log}"
BRANCH_PROTECT_BACKUP="${PA_BP_BACKUP:-/tmp/canary-automerge-bp-$$.json}"

GO=0
for arg in "$@"; do [ "$arg" = "--go" ] && GO=1; done

log() { echo "  $*"; }
die() { echo "  ✗ $*" >&2; exit 1; }
require_go() {
  [ "$GO" = "1" ] || die "outward 子命令需显式 --go（当前 dry-run，未执行）。确认授权后加 --go 重跑。"
}

# ══════════════════════════════════════════════════════════════════════════════════════
# prep：无害前置检查（RUNBOOK §8.1 step 1-2 + canary 前置 ①②③）
# ══════════════════════════════════════════════════════════════════════════════════════
prep() {
  echo "═══ canary prep（无害前置检查）═══  state=$CANARY_STATE  log=$CANARY_LOG"
  local rc=0

  # step 2 离线 drill（canary 硬前置，本地真实 git tmp repo 全链路）
  log "▸ §8.1 step 2：离线 merge drill（test_dev_agent_merge.py）…"
  ( cd "$PA" && python -m pytest scripts/test_dev_agent_merge.py -q >/tmp/canary-prep-drill.log 2>&1 ) && \
    log "  ✅ 离线 drill 绿（$(tr -cd '.' < /tmp/canary-prep-drill.log | wc -c) 测点）" || { log "  ✗ 离线 drill 红"; rc=1; }

  # flag env 机制
  log "▸ flag env 机制（PA_SINGLE_FLIGHT_SERIAL_SHADOW / _AUTO_MERGE）…"
  grep -q 'PA_SINGLE_FLIGHT_SERIAL_SHADOW' "$PA/scripts/feature_flags.py" && \
    grep -q 'PA_SINGLE_FLIGHT_AUTO_MERGE' "$PA/scripts/feature_flags.py" && \
    log "  ✅ 双 flag env 注册就位" || { log "  ✗ flag env 缺"; rc=1; }

  # run_daily CLI 参数（RUNBOOK §8.3 命令依赖）
  log "▸ run_daily CLI 参数（--project/--state-dir/--no-notify/--inject-prd）…"
  grep -q -- '"--project"' "$RUN_DAILY" && grep -q -- '"--state-dir"' "$RUN_DAILY" && \
    grep -q -- '"--no-notify"' "$RUN_DAILY" && grep -q -- '"--inject-prd"' "$RUN_DAILY" && \
    log "  ✅ canary 命令参数就位（非文档漂移）" || { log "  ✗ CLI 参数缺"; rc=1; }

  # cc-web-control profile + 本地仓 + remote
  log "▸ cc-web-control profile + 本地仓 …"
  [ -f "$VAULT/.project-auto/profiles/cc-web-control.yaml" ] && log "  ✅ profile 存在" || { log "  ✗ profile 缺"; rc=1; }
  [ -d "$CC_WEB/.git" ] && log "  ✅ 本地仓 $CC_WEB" || { log "  ✗ 本地仓不存在"; rc=1; }
  git -C "$CC_WEB" remote get-url origin 2>/dev/null | grep -q "github.com" && log "  ✅ remote $(git -C "$CC_WEB" remote get-url origin)" || { log "  ✗ remote 缺"; rc=1; }

  # gh 认证（dev-agent push 走 git origin，credential helper 注入 gh token；不依赖 PA_GITHUB_TOKEN）
  log "▸ gh 认证（push 凭证）…"
  gh auth status 2>&1 | grep -q "Logged in" && log "  ✅ gh 已认证（$(gh auth status 2>&1 | grep -o 'as [^ ]*' | head -1)）" || { log "  ⚠️ gh 未认证（push 会失败）"; rc=1; }

  # 分支保护 + direct push 实测（判据 a 前置——以 origin/main 真实 marker 为权威，非理论推断）
  log "▸ cc-web-control main direct push 可行性（判据 a 前置）…"
  local bp marker
  bp=$(gh api "repos/$OWNER_REPO/branches/main/protection" 2>&1) || true
  marker=$(git -C "$CC_WEB" log origin/main --grep='Pipeline-Merge:' --oneline 2>/dev/null | head -1)
  if [ -n "$marker" ]; then
    log "  ✅ origin/main 已有自动合入 marker（${marker%% *}）→ direct push 实测可过，auto-merge 不被分支保护拦"
    log "     （main 有 PR-review 保护但 owner direct push 不被拒；branch-protect 子命令留作可选保险，非必需）"
  elif echo "$bp" | grep -q "required_pull_request_reviews"; then
    log "  ⚠️ main 有 required_pull_request_reviews 且 origin/main 暂无自动合入 marker"
    log "     → 若 direct push 被拒（push_failed→UNKNOWN→halt），用 \`branch-protect save/restore --go\` 临时移除"
  else
    log "  ✅ main 无 PR-review 保护，direct push 可过"
  fi

  # 工作区说明（主工作区的 effort 重构是并行工作，canary 用 dispatch 独立 worktree 不受影响）
  log "▸ cc-web-control 主工作区状态 …"
  local dirty
  dirty=$(git -C "$CC_WEB" status --porcelain 2>/dev/null | wc -l)
  if [ "$dirty" -gt 0 ]; then
    log "  ℹ️ 主工作区有 $dirty 处改动（疑 effort 功能删除重构）——canary 用 dispatch 独立 worktree，不受影响；勿清（是并行真实工作）"
  else
    log "  ✅ 主工作区干净"
  fi

  echo "═══ prep 完成（rc=$rc）═══  canary 须运维显式 go 才跑 outward 子命令"
  return $rc
}

# ══════════════════════════════════════════════════════════════════════════════════════
# branch-protect：备份/恢复 cc-web-control main 分支保护（outward，--go）
# 直接 push main 会被 required_pull_request_reviews 拒；canary 前临时移除、后恢复。
# ══════════════════════════════════════════════════════════════════════════════════════
branch-protect() {
  local op="${1:-}"
  case "$op" in
    save)
      echo "▸ 备份 cc-web-control main 分支保护 → $BRANCH_PROTECT_BACKUP"
      if [ "$GO" = "1" ]; then
        gh api "repos/$OWNER_REPO/branches/main/protection" > "$BRANCH_PROTECT_BACKUP" 2>/dev/null && \
          log "  ✅ 已备份（$(wc -c < "$BRANCH_PROTECT_BACKUP") bytes）" || die "备份失败（main 可能本就无保护）"
        echo "▸ 临时移除 main 保护（让 auto-merge direct push 可过）"
        gh api -X DELETE "repos/$OWNER_REPO/branches/main/protection" 2>/dev/null && \
          log "  ✅ main 保护已移除（canary 后务必 restore）" || die "移除保护失败"
        log "  ⚠️ 恢复命令：$0 branch-protect restore --go"
      else
        log "  [dry-run] gh api .../protection > $BRANCH_PROTECT_BACKUP; gh api -X DELETE .../protection"
      fi ;;
    restore)
      echo "▸ 从 $BRANCH_PROTECT_BACKUP 恢复 cc-web-control main 分支保护"
      [ -f "$BRANCH_PROTECT_BACKUP" ] || die "备份不存在（先 save）"
      if [ "$GO" = "1" ]; then
        gh api -X PUT "repos/$OWNER_REPO/branches/main/protection" \
          -H "Accept: application/vnd.github+json" --input "$BRANCH_PROTECT_BACKUP" && \
          log "  ✅ main 保护已恢复" || die "恢复失败（手动检查 $BRANCH_PROTECT_BACKUP）"
      else
        log "  [dry-run] gh api -X PUT .../protection --input $BRANCH_PROTECT_BACKUP"
      fi ;;
    *) die "用法：$0 branch-protect save|restore [--go]" ;;
  esac
}

# ══════════════════════════════════════════════════════════════════════════════════════
# run-a：判据 a 正常闭环（outward，--go）
# 双 flag on + 隔离 state + --inject-prd 绿 smoke → dispatch dev loop → merge + post-merge PASS
# ══════════════════════════════════════════════════════════════════════════════════════
run-a() {
  echo "═══ 判据 a：正常 auto-merge 闭环 ═══  PRD=$GREEN_PRD"
  require_go
  [ -f "$GREEN_PRD" ] || die "绿 smoke PRD 缺：$GREEN_PRD"
  export PA_SINGLE_FLIGHT_SERIAL_SHADOW=1 PA_SINGLE_FLIGHT_AUTO_MERGE=1
  unset PA_HEARTBEAT || true
  cd "$VAULT"
  python3 "$RUN_DAILY" --from-stage inject --to-stage dispatch \
    --inject-prd "$GREEN_PRD" --project cc-web-control --state-dir "$CANARY_STATE" --no-notify \
    2>&1 | tee "$CANARY_LOG"
  log "  判据 a 完成——跑 \`$0 verify\` 检查 (a) merge+post-merge PASS"
}

# ══════════════════════════════════════════════════════════════════════════════════════
# inject-red：判据 b 故意红 auto-revert（outward，--go）
# 双绿约束下 PRD 经 dev loop 无法自然 post-merge 红（verify 先拦），故绕 dev loop：
# 独立 worktree 建红 merge commit（--no-ff + Pipeline-Merge marker，复刻 dispatch 形态）push 到 main
# → 触发 dev-agent --phase post-merge-test（FAIL）→ --phase revert（REVERTED）→ main 回绿。
# 对齐离线 drill test_post_merge_fail_then_revert_restores_green 的 fixture 模式。
# ══════════════════════════════════════════════════════════════════════════════════════
inject-red() {
  echo "═══ 判据 b：故意红 → post-merge FAIL → auto-revert ═══  slug=$RED_SLUG"
  require_go
  [ -d "$CC_WEB/.git" ] || die "cc-web-control 本地仓缺"
  command -v npm >/dev/null || die "npm 缺（post-merge test_cmd=npm test 需要）"

  local WT; WT="$(mktemp -d -t canary-red-wt-XXXXXX)"
  # 独立 worktree（detached at origin/main）——不碰主工作区的 effort 重构（memory pa-concurrent-claude-session-git）
  git -C "$CC_WEB" fetch origin main
  git -C "$CC_WEB" worktree add --detach "$WT" origin/main
  trap 'git -C "$CC_WEB" worktree remove --force "$WT" 2>/dev/null || true' EXIT

  # feature 分支 + 红测试 + commit
  git -C "$WT" checkout -q -b "redfeat-$RED_SLUG"
  cat > "$WT/test/canary-red.test.cjs" <<'REDTEST'
const test = require('node:test');
const assert = require('node:assert/strict');
// canary 故意红（判据 b）：此断言预期失败，验证 post-merge auto-revert 闭环。勿修复。
test('canary 故意红——验证 post-merge auto-revert（判据 b）', () => {
  assert.strictEqual(1, 2, 'canary 故意红：预期失败，触发 auto-revert');
});
REDTEST
  git -C "$WT" add test/canary-red.test.cjs
  git -C "$WT" commit -q -m "canary red: 故意红测试（判据 b）"

  # detached at main + --no-ff merge（双 parent merge commit，可被 revert -m 1；带 Pipeline-Merge marker）
  git -C "$WT" checkout -q --detach origin/main
  git -C "$WT" merge --no-ff "redfeat-$RED_SLUG" -m "canary red merge（判据 b）" -m "Pipeline-Merge: $RED_SLUG"
  local MC; MC="$(git -C "$WT" rev-parse HEAD)"
  log "  红 merge commit: $MC（双 parent + marker，复刻 dispatch 形态）"
  git -C "$WT" push origin "HEAD:main"   # main 现在红

  # post-merge-test（dev-agent 在 worktree cwd 跑 npm test → 红 → FAIL）
  log "  ▸ 触发 post-merge-test（预期 FAIL）…"
  ( cd "$WT" && python3 "$DEV_AGENT_PY" --phase post-merge-test \
      --test-cmd "npm test" --main main --prd-id "$RED_SLUG" --state-dir "$CANARY_STATE" ) | tee -a "$CANARY_LOG"

  # revert（merge_commit=MC → git revert -m 1 → push → main 回绿）
  log "  ▸ 触发 revert（预期 REVERTED，main 回绿）…"
  ( cd "$WT" && python3 "$DEV_AGENT_PY" --phase revert \
      --merge-commit "$MC" --main main --prd-id "$RED_SLUG" --state-dir "$CANARY_STATE" ) | tee -a "$CANARY_LOG"

  log "  判据 b 完成——跑 \`$0 verify\` 检查 (b) revert REVERTED + main 回绿"
}

# ══════════════════════════════════════════════════════════════════════════════════════
# check-c：判据 c 熔断（outward，--go）
# 双绿约束下"经 dispatch 的 post-merge FAIL→record_revert"难自然触发（同判据 b 根因）。故这里验证
# 熔断门本身：手动经 circuit_breaker.record_revert 注入 cooldown 记录 → 再投同 slug PRD →
# dispatch merge 前 is_in_cooldown 命中 → triage(cooldown_revert_loop)。完整 record→cooldown 闭环
# 由 test_circuit_breaker.py 11 测覆盖；此处验 is_in_cooldown 门在 dispatch 真实路径生效。
# ══════════════════════════════════════════════════════════════════════════════════════
check-c() {
  echo "═══ 判据 c：circuit breaker 熔断门 ═══  slug=$RED_SLUG"
  require_go
  log "  ▸ 手动注入 cooldown 记录（record_revert）…"
  python3 - <<PY 2>&1 | tee -a "$CANARY_LOG"
import sys; sys.path.insert(0, "$PA/scripts")
import circuit_breaker as CB
CB.record_revert("$CANARY_STATE", "$OWNER_REPO", "$RED_SLUG")
print("  ✅ cooldown 记录已注入（slug=$RED_SLUG）")
PY
  log "  ▸ 再投同 slug PRD（merge 前 is_in_cooldown 应命中 → triage）…"
  export PA_SINGLE_FLIGHT_SERIAL_SHADOW=1 PA_SINGLE_FLIGHT_AUTO_MERGE=1
  unset PA_HEARTBEAT || true
  cd "$VAULT"
  python3 "$RUN_DAILY" --from-stage inject --to-stage dispatch \
    --inject-prd "$RED_PRD" --project cc-web-control --state-dir "$CANARY_STATE" --no-notify \
    2>&1 | tee -a "$CANARY_LOG"
  log "  判据 c 完成——跑 \`$0 verify\` 检查 (c) 熔断命中 cooldown_revert_loop"
}

# ══════════════════════════════════════════════════════════════════════════════════════
# verify：无害判据 grep + journal 闭合检查（RUNBOOK §8.3 通过判据）
# ══════════════════════════════════════════════════════════════════════════════════════
verify() {
  echo "═══ canary 判据 verify ═══  log=$CANARY_LOG"
  [ -f "$CANARY_LOG" ] || { log "⚠️ 无 canary log（先跑 run-a/inject-red/check-c --go）"; return 0; }
  local a=0 b=0 c=0 d=0
  grep -qE '🎉.*已合 main|✅.*post-merge main 全量测试绿.*merged' "$CANARY_LOG" && a=1
  grep -qE '🔴.*post-merge main 红.*auto-revert|↩️.*revert 成功' "$CANARY_LOG" && b=1
  grep -qE '🧊.*熔断命中.*cooldown_revert_loop' "$CANARY_LOG" && c=1
  grep -qE '🛑.*(halt|CRITICAL)' "$CANARY_LOG" && d=1
  log "判据 (a) 正常闭环 merge+PASS  : $([ $a = 1 ] && echo '✅ 出现' || echo '⬜ 未出现')"
  log "判据 (b) 故意红 auto-revert   : $([ $b = 1 ] && echo '✅ 出现' || echo '⬜ 未出现')"
  log "判据 (c) 熔断 cooldown_revert : $([ $c = 1 ] && echo '✅ 出现' || echo '⬜ 未出现')"
  log "判据 (d) halt/CRITICAL（须无）: $([ $d = 1 ] && echo '🔴 出现（canary 失败信号）' || echo '✅ 无')"
  log "▸ marker 落地（§8.2 回滚锚点）:"
  git -C "$CC_WEB" log origin/main --grep='Pipeline-Merge: ' --oneline 2>/dev/null | head -5 | sed 's/^/    /' || true
  log "▸ merge_loop journal 闭合（无 open intent）:"
  local ml="$CANARY_STATE/merge_loop/${OWNER_REPO//\//_}.journal.jsonl"
  [ -f "$ml" ] && tail -3 "$ml" | sed 's/^/    /' || log "    （无 merge_loop journal）"
  echo "═══ verify 完成 ═══"
  [ $a = 1 ] && [ $b = 1 ] && [ $c = 1 ] && [ $d = 0 ] && log "✅ 全判据通过——可考虑扩全量（§8.1 step 4）" || \
    log "⚠️ 判据未全过——查 log + journal，必要时 branch-protect restore --go + 人工 triage"
}

# ── 分发 ──────────────────────────────────────────────────────────────────────────────
sub="${1:-prep}"; shift || true
case "$sub" in
  prep) prep "$@" ;;
  branch-protect) branch-protect "$@" ;;
  run-a) run-a "$@" ;;
  inject-red) inject-red "$@" ;;
  check-c) check-c "$@" ;;
  verify) verify "$@" ;;
  *) echo "用法：$0 {prep|branch-protect|run-a|inject-red|check-c|verify} [--go]"; exit 2 ;;
esac
