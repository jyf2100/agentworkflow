#!/usr/bin/env bash
# canary_graph_cutover.sh — graph orchestrator cutover shadow-parity canary（RUNBOOK §8.3 / task 5.5）。
#
# For future Claude：这是 langgraph-workflow-upgrade Phase 3 graph 路径 cutover 的 canary 脚本。与
# canary_automerge.sh（验 merge/revert 闭环、outward 改 main）**不同**——本脚本验 **shadow parity**：
# 同 PRD 输入下 graph_pa.py（graph 路径）与 run_daily.py（legacy 路径）两条编排产出的 dispatch 终态 +
# 逐 stage 产物 byte-identical（R7 防 Counter 假绿）。
#
# 默认 --dispatch-skip-dev（零 outward + 零 LLM 成本）：inject 段手写 PRD 直产 manifest（不经 fetch/radar/
# prd persona），critic --skip-critic 跳，dispatch 仅过准入不跑 dev loop（不开 PR、不改目标仓 main）。
# 故 prep/shadow-run/parity/verify **全无害**，可自由重复跑（≥3 cron 坐实 matched=True 后移硬 gate）。
# 守 CLAUDE.md「outward 须确认」+ memory pa-test-no-dirty-data（隔离双 state_dir + 临时 log + unset
# PA_HEARTBEAT，不碰真实 cron.log/SMTP/日报）。
#
# 判据（RUNBOOK §8.3，全过才移硬 gate + 扩全量，批 4 task 5.6 软 open item → 硬 gate ops step）：
#   (a) parity.matched=True（dispatch 终态 Counter 分布一致）〔硬 gate〕
#   (c) 无 load_failed（双源 dispatch_{stamp}.json 都成功 load，非双失败假绿）〔硬 gate〕
#   (b) per_stage byte-identical：**诊断维度**（非硬 gate）——双 state_dir canary 下 prd_path 路径前缀 +
#       verify_round 初始值（legacy None vs graph _build_dispatch_shell=1）必然 mismatch（canary 首跑坐实，
#       非真实编排回归）；mismatch 字段须人工确认无语义漂移，规范化比对（剥路径前缀 + 初始值归一）留 follow-up。
#   (d) ≥3 cron 周期重复全过（坐实非偶然）
#
# 用法：
#   bash scripts/canary_graph_cutover.sh prep                   # 无害前置检查（默认）
#   bash scripts/canary_graph_cutover.sh shadow-run             # 双跑（legacy + graph）隔离 state + skip-dev
#   bash scripts/canary_graph_cutover.sh parity                 # 对照双 dispatch_{stamp}.json（matched 判据）
#   bash scripts/canary_graph_cutover.sh verify                 # 汇总 matched 判据 + ≥3 提示
#   STAMP=20260813 bash scripts/canary_graph_cutover.sh shadow-run   # 指定 stamp（默认今天）
set -euo pipefail

# ── 路径推导（同 canary_automerge.sh：scripts/ → 项目推进流水线/ → Projects/ → vault 根）──────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VAULT="${PA_VAULT:-$(cd "$SCRIPT_DIR/../../.." && pwd)}"
PA="$VAULT/Projects/项目推进流水线"
RUN_DAILY="$PA/scripts/run_daily.py"
GRAPH_PA="$PA/scripts/graph_pa.py"
GREEN_PRD="$PA/scripts/canary-green.prd.md"   # 复用 auto-merge canary 的绿 smoke 载体（cc-web-control）
PROJECT="cc-web-control"

# ── PATH 补齐（同 run_cron.sh：dev-agent.py import claude_agent_sdk 在 miniconda；此处 run_daily/graph_pa 亦需）──
export NVM_DIR="$HOME/.nvm"; [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
export PATH="$HOME/.local/bin:$HOME/miniconda3/bin:$PATH"
PYTHON="$(command -v python3 || true)"

# ── canary 隔离双 state_dir + 临时 log（守 pa-test-no-dirty-data：不碰真实 .project-auto/state）──
# 落 .project-auto/ 下独立子目录（已 gitignore，CLAUDE.md）：在 VAULT_ROOT 内（inject prd_path.relative_to
# 可过）又隔离真实 state + 不入仓。双 state_dir 供 shadow parity 双源对照。
mkdir -p "$VAULT/.project-auto"
# 固定路径（非随机 mktemp）：shadow-run/parity/verify 分子命令须共享同双 state_dir；shadow-run 开头
# rm -rf 清旧确保 parity 对照本次双跑（非残留）。多 canary 并跑用 PA_CANARY_STATE_LEGACY/GRAPH env 隔离。
STATE_LEGACY="${PA_CANARY_STATE_LEGACY:-$VAULT/.project-auto/canary-graph-legacy}"
STATE_GRAPH="${PA_CANARY_STATE_GRAPH:-$VAULT/.project-auto/canary-graph}"
CANARY_LOG="${PA_CANARY_LOG:-/tmp/canary-graph-cutover.log}"   # 固定（非 $$）：shadow-run/parity/verify 分子命令共享
STAMP="${STAMP:-$(date +%Y%m%d)}"

GO=0
for arg in "$@"; do [ "$arg" = "--go" ] && GO=1; done

log() { echo "  $*"; }
die() { echo "  ✗ $*" >&2; exit 1; }

# ══════════════════════════════════════════════════════════════════════════════════════
# prep：无害前置检查（flag 注册 + preflight orchestrator⇒shadow + graph_pa.py + graph 套件绿 + profile）
# ══════════════════════════════════════════════════════════════════════════════════════
prep() {
  echo "═══ graph cutover canary prep（无害前置检查）═══  stamp=$STAMP  legacy=$STATE_LEGACY  graph=$STATE_GRAPH  log=$CANARY_LOG"
  local rc=0

  log "▸ graph_pa.py 主图入口存在 …"
  [ -f "$GRAPH_PA" ] && log "  ✅ $GRAPH_PA" || { log "  ✗ graph_pa.py 缺（批 1 未完成？）"; rc=1; }

  log "▸ flag 注册（PA_GRAPH_SHADOW / PA_GRAPH_ORCHESTRATOR env 映射）…"
  grep -q 'PA_GRAPH_SHADOW' "$PA/scripts/feature_flags.py" && \
    grep -q 'PA_GRAPH_ORCHESTRATOR' "$PA/scripts/feature_flags.py" && \
    log "  ✅ 双 flag env 注册就位" || { log "  ✗ flag env 缺（批 2 task 5.1？）"; rc=1; }

  log "▸ preflight 依赖（orchestrator⇒shadow，批 2 task 5.4）…"
  grep -q 'pa_graph_orchestrator.*pa_graph_shadow' "$PA/scripts/coordinator.py" && \
    log "  ✅ orchestrator⇒shadow 依赖注册" || { log "  ✗ preflight 依赖缺（批 2 task 5.4？）"; rc=1; }

  log "▸ run_cron.sh 分流（PA_GRAPH_ORCHESTRATOR=1 → graph_pa）…"
  grep -q 'PA_GRAPH_ORCHESTRATOR' "$PA/scripts/run_cron.sh" && \
    log "  ✅ run_cron 分流就位" || { log "  ✗ run_cron 分流缺（批 2 task 5.2？）"; rc=1; }

  log "▸ graph 套件绿（主图 e2e + 拓扑 + 映射，task 5.8）…"
  ( cd "$PA" && python -m pytest scripts/test_graph_main_e2e.py scripts/test_graph_topology.py \
      scripts/test_subgraph_to_record_mapping.py scripts/test_graph_dispatch_e2e.py -q >/tmp/canary-graph-prep.log 2>&1 ) && \
    log "  ✅ graph 套件绿（$(tr -cd '.' < /tmp/canary-graph-prep.log | wc -c) 测点）" || { log "  ✗ graph 套件红（查 /tmp/canary-graph-prep.log）"; rc=1; }

  log "▸ cutover shadow parity 函数（批 3 task 5.3 + 批 4 task 5.6 接入）…"
  grep -q 'def run_graph_shadow_parity_evidence' "$PA/scripts/cutover.py" && \
    grep -q 'def run_graph_shadow_parity_drill_per_stage' "$PA/scripts/cutover.py" && \
    log "  ✅ parity evidence + per_stage 函数就位" || { log "  ✗ parity 函数缺"; rc=1; }

  log "▸ cc-web-control profile + 绿 smoke PRD 载体 …"
  [ -f "$VAULT/.project-auto/profiles/$PROJECT.yaml" ] && log "  ✅ profile 存在" || { log "  ✗ profile 缺"; rc=1; }
  [ -f "$GREEN_PRD" ] && log "  ✅ 绿 smoke PRD（$GREEN_PRD）" || { log "  ✗ GREEN_PRD 缺（canary-green.prd.md）"; rc=1; }

  echo "═══ prep 完成（rc=$rc）═══  canary 全无害（--dispatch-skip-dev），可自由跑 shadow-run/parity/verify"
  return $rc
}

# ══════════════════════════════════════════════════════════════════════════════════════
# shadow-run：双跑（legacy run_daily + graph graph_pa）同 PRD，隔离双 state_dir，--dispatch-skip-dev
# 零 outward + 零 LLM 成本：inject 手写 PRD → critic skip → dispatch 过准入不跑 dev loop → 双 dispatch_{stamp}.json
# ══════════════════════════════════════════════════════════════════════════════════════
shadow_run() {
  echo "═══ shadow-run：双跑 stamp=$STAMP ═══  PRD=$GREEN_PRD  (默认 --dispatch-skip-dev 零 outward)"
  [ -f "$GRAPH_PA" ] || die "graph_pa.py 缺（先 prep）"
  [ -f "$GREEN_PRD" ] || die "绿 smoke PRD 缺：$GREEN_PRD"
  unset PA_HEARTBEAT || true
  rm -rf "$STATE_LEGACY" "$STATE_GRAPH"          # 清旧 state，确保 parity 对照本次双跑（非残留）
  mkdir -p "$STATE_LEGACY" "$STATE_GRAPH"
  cd "$VAULT"

  log "▸ legacy 路径（run_daily.py，PA_GRAPH_* off）→ $STATE_LEGACY …"
  unset PA_GRAPH_SHADOW PA_GRAPH_ORCHESTRATOR
  python3 "$RUN_DAILY" --from-stage inject --to-stage dispatch \
    --inject-prd "$GREEN_PRD" --project "$PROJECT" --state-dir "$STATE_LEGACY" \
    --no-notify --skip-critic --dispatch-skip-dev 2>&1 | tee "$CANARY_LOG"
  [ -f "$STATE_LEGACY/dispatch_$STAMP.json" ] && log "  ✅ legacy 产 dispatch_$STAMP.json" || \
    die "legacy 未产 dispatch_$STAMP.json（查 $CANARY_LOG）"

  log "▸ graph 路径（graph_pa.py，PA_GRAPH_SHADOW=1 PA_GRAPH_ORCHESTRATOR=1）→ $STATE_GRAPH …"
  export PA_GRAPH_SHADOW=1 PA_GRAPH_ORCHESTRATOR=1
  python3 "$GRAPH_PA" --from-stage inject --to-stage dispatch \
    --inject-prd "$GREEN_PRD" --project "$PROJECT" --state-dir "$STATE_GRAPH" \
    --no-notify --skip-critic --dispatch-skip-dev 2>&1 | tee -a "$CANARY_LOG"
  [ -f "$STATE_GRAPH/dispatch_$STAMP.json" ] && log "  ✅ graph 产 dispatch_$STAMP.json" || \
    die "graph 未产 dispatch_$STAMP.json（查 $CANARY_LOG）"
  unset PA_GRAPH_SHADOW PA_GRAPH_ORCHESTRATOR

  echo "═══ shadow-run 完成 ═══  跑 \`$0 parity\` 对照双源（stamp=$STAMP）"
}

# ══════════════════════════════════════════════════════════════════════════════════════
# parity：对照双 state_dir 的 dispatch_{stamp}.json（终态 Counter + 逐 stage byte-identical）
# ══════════════════════════════════════════════════════════════════════════════════════
parity() {
  echo "═══ parity 对照 ═══  stamp=$STAMP  legacy=$STATE_LEGACY  graph=$STATE_GRAPH"
  [ -f "$STATE_LEGACY/dispatch_$STAMP.json" ] || die "legacy dispatch_$STAMP.json 缺（先 shadow-run）"
  [ -f "$STATE_GRAPH/dispatch_$STAMP.json" ] || die "graph dispatch_$STAMP.json 缺（先 shadow-run）"
  PYTHONPATH="$PA/scripts" python3 - <<PY 2>&1 | tee -a "$CANARY_LOG"
import sys; sys.path.insert(0, "$PA/scripts")
import cutover
ev = cutover.run_graph_shadow_parity_evidence(daily_state_dir="$STATE_LEGACY",
                                              graph_state_dir="$STATE_GRAPH", stamp="$STAMP")
ps = cutover.run_graph_shadow_parity_drill_per_stage(daily_state_dir="$STATE_LEGACY",
                                                     graph_state_dir="$STATE_GRAPH", stamp="$STAMP")
pty = type(ev.parity).__name__
print(f"  parity: type={pty} matched={ev.parity.matched} n_daily={ev.n_daily} n_graph={ev.n_graph}")
if hasattr(ev.parity, "mismatches") and ev.parity.mismatches:
    for m in ev.parity.mismatches: print(f"    ⚠️ {m}")
print(f"  per_stage: matched={ps.matched} stages={ps.stages_checked} mismatches={ps.mismatches}")
print(f"  ── 判据 (a)parity.matched={ev.parity.matched} (b)per_stage.matched={ps.matched} "
      f"(c)load_ok={pty != 'LoadFailureReport'}")
PY
  echo "═══ parity 完成 ═══  跑 \`$0 verify\` 汇总判据"
}

# ══════════════════════════════════════════════════════════════════════════════════════
# verify：汇总 matched 判据（RUNBOOK §8.3 通过判据 + ≥3 cron 提示）
# ══════════════════════════════════════════════════════════════════════════════════════
verify() {
  echo "═══ graph cutover canary verify ═══  log=$CANARY_LOG"
  [ -f "$CANARY_LOG" ] || { log "⚠️ 无 canary log（先 shadow-run + parity）"; return 0; }
  local a=0 c=0
  grep -qE 'parity:.*matched=True' "$CANARY_LOG" && a=1
  grep -qE 'load_ok=True' "$CANARY_LOG" && c=1
  # per_stage byte-identical 作**诊断维度**（非硬 gate）：双 state_dir canary 下 prd_path 路径前缀 +
  # verify_round 初始值（legacy None vs graph 1）必然 mismatch（canary 首跑坐实，非真实编排回归）。
  # 硬 gate = 终态 (a) parity.matched（dispatch 终态 Counter 分布）+ (c) load_ok（双源真跑非假绿）。
  log "判据 (a) parity.matched（终态 Counter） : $([ $a = 1 ] && echo '✅ 通过' || echo '⬜ 未过')〔硬 gate〕"
  log "判据 (c) load_ok（非双失败假绿）         : $([ $c = 1 ] && echo '✅ 通过' || echo '⬜ 未过')〔硬 gate〕"
  log "诊断 (b) per_stage byte-identical        : ℹ️ $(grep -oE 'per_stage:.*' "$CANARY_LOG" | tail -1)"
  log "    （双 state_dir 路径前缀 + verify_round 初始值预期 mismatch，非编排回归；mismatch 字段须人工确认无语义漂移）"
  log "判据 (d) ≥3 cron 周期重复                : ⬜ 须运维手动 ≥3 次（不同 stamp 重复 shadow-run+parity+verify）"
  if [ $a = 1 ] && [ $c = 1 ]; then
    log "✅ 单次 parity 硬 gate 全过（终态 matched + load_ok）——累计 ≥3 cron 全过后，可移硬 gate（cutover.py _ALLOWED_OPEN_ITEMS 原子删三元组 + 测试守护）"
  else
    log "⚠️ parity 硬 gate 未全过——查 log + 双 state_dir 的 dispatch_$STAMP.json diff，定位 graph vs legacy 真实漂移"
  fi
  echo "═══ verify 完成 ═══"
}

# ── 分发 ──────────────────────────────────────────────────────────────────────────────
sub="${1:-prep}"; shift || true
case "$sub" in
  prep) prep "$@" ;;
  shadow-run) shadow_run "$@" ;;
  parity) parity "$@" ;;
  verify) verify "$@" ;;
  *) echo "用法：$0 {prep|shadow-run|parity|verify} [--go]  [STAMP=YYYYMMDD]" ; exit 2 ;;
esac
