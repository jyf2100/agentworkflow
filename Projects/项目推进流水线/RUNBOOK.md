# Operator Runbook — 项目推进流水线 durable runtime

cron/运维在 durable runtime 异常时遵循本手册。spec（`runtime-cutover-evidence` → Scenario: Documented
recovery command）：**每个引用命令存在于仓库，产出 verifiable recovery 或 explicit manual-block**。

所有命令在 `scripts/` 目录运行（`cd 项目推进流水线/scripts`）。`<state_dir>` 默认 `.project-auto/state`。

---

## 1. Journal 损坏恢复

cron 崩溃或 journal 损坏告警时，先校验 journal 完整性并自动恢复或显式阻断：

```bash
python recovery_cli.py <state_dir>/runs/<proj>/<stamp>_<slug>.journal.jsonl [--prd <prd_path>]
```

- **exit 0 / `action=recovered`**：journal 可读（末尾截断容忍）→ 已重建终态（`terminal_status`）+
  可选 `recovery_context`（`--prd` 提供时）。可安全 resume。
- **exit 2 / `action=manual_block`**：journal **中部损坏**（fail-closed）→ **不自动修复**。
  按 `corrupted_line_numbers` 定位 → 备份损坏 journal → 运维重建或丢弃受污染 iteration。

> 中部损坏绝不静默跳过坏行归约——否则状态机基于残缺事件得错误状态（design 决策#1）。

## 2. Crash reconciliation（副作用 exactly-once）

崩溃在 commit/push/PR 后重启，重放前 reconcile 远端副作用（confirmed 跳过 / pending 重做 / unknown 阻断）：

```python
import cutover as CT
from reconcile import LocalGitResolver            # 生产：subprocess git 查 commit/branch/PR
ev = CT.run_crash_reconciliation_evidence(resolver=LocalGitResolver(...))
# ev.all_exactly_once == True ⇔ agent/test/commit/push/PR 五边界副作用状态全明确（无 unknown）
```

## 3. Shadow parity（journal-driven dispatch 切换前）

切换 `journal_driven_dispatch` 前验证 dispatch↔journal 终态分布一致（design 决策#2 cutover 前置）：

```python
ev = CT.run_shadow_parity_evidence(state_dir=<state_dir>, stamp_fn=<stamp_fn>)
# ev.parity.matched == True ⇔ mismatch 已解决基线，可安全切 journal_driven_dispatch
```

## 4. Quality evidence（rollout 就绪证据）

```bash
python quality_evidence.py
# exit 0 + readiness=True ⇔ compile + tests + ruff 全过，归档证据 digest（design 决策#6）
```

## 5. Learning memory（add-cross-prd-learning-memory）

### 5.1 开 canary（单项目）

1. profile yaml 加 ``learning_memory.enabled: True``（V1 项目级 allowlist 标记）。
2. 环境变量或 profile.loop 开 ``PA_LEARNING_SHADOW=1``（先单跑 shadow：candidate generation + catalog projection，不改 dev prompt）。
3. 观察 shadow 跑 N 次 dispatch 后，``.project-auto/state/lessons/catalog/<project>.json`` 有 active entries（cross-PRD 等价 recurrence ≥2 自动 promote）。
4. 准备开 injection：profile 显式标 ``learning_memory.parity_passed: True`` + ``quality_passed: True``（V1 evidence 流尚未自动化；canary 阶段人工签发）。设 ``PA_LEARNING_INJECTION=1`` → 四重 gate 全过后开仓注入 lesson block。

```yaml
# .project-auto/profiles/<project>.yaml 片段
learning_memory:
  enabled: true
  parity_passed: true
  quality_passed: true
loop:
  cross_prd_learning_shadow: true
  cross_prd_learning_injection: true
```

### 5.2 读 degraded records

reflection / injection / effectiveness 任一故障 → side-channel ``.project-auto/state/lessons/degraded/<project>.jsonl`` 一行一记录。运维查：

```python
import learning_memory_reflection as LMRefl
for rec in LMRefl.read_degraded_records(".project-auto/state", "<project>"):
    print(rec["timestamp"], rec["degraded_class"], rec["reason"])
# 常见 class：timeout / sdk_error / invalid_json / schema_reject / persist_failure /
#   evidence_history_mismatch / injection_not_gated / injection_parity_failed /
#   injection_quality_failed / injection_not_allowlisted / catalog_read_error / reflection_attach_error
```

> degraded 不阻断 dispatch（fail-open by construction）；只在 canary 项目需要 triage。非 allowlist 项目静默跳过（无 degraded 记录）。

### 5.3 重建 catalog（append-only 幂等）

catalog 是 rebuildable projection；删了可从 append-only facts 重建：

```python
import learning_memory_catalog as LMCat
result = LMCat.rebuild_catalog(".project-auto/state", "<project>")
# result.ok=True ⇔ replay 成功 + atomic write；ok=False ⇔ 中部损坏 fail-closed（旧 catalog 未被覆盖）
```

append-only 真源：``candidates/<project>.jsonl`` + ``events/<project>.jsonl`` + ``usage/<project>.jsonl``。``_replay`` 幂等（同输入 → 同 catalog）；中部 malformed record → fail-closed（绝不部分信任）；末尾截断容忍（崩溃只截最后一条 append）。

### 5.4 完全禁用（两级 rollback）

| 级别 | 操作 | 效果 |
|---|---|---|
| Level 1 | 删 profile.loop.cross_prd_learning_injection（或 PA_LEARNING_INJECTION=0） | candidate generation 继续；dev prompt baseline（无 lesson block）；selected_lesson_ids=() |
| Level 2 | 删 profile.loop.cross_prd_learning_shadow（或 PA_LEARNING_SHADOW=0） | reflection 完全不调（零 SDK 调用）；existing candidate facts inert + 可重建 |

两条 flag 都 off + profile 无 ``learning_memory.enabled`` → 整个子系统零副作用（不读 catalog、不调 SDK、不改 prompt）。

### 5.5 crash 后 reconcile

learning memory state 是 append-only（flock + O_APPEND + fsync），崩溃只可能丢「正在写的最后一条」。重启后：
- catalog 重建（``rebuild_catalog``）从 append-only facts 重新 projection——幂等，无 duplicate promotion（promotion 是确定性投影）。
- 已 append 的 candidate/event/usage record 100% 保留（fsync 已落盘）。
- 在 reflection 中途崩（candidate 已 append 但 catalog 未替换）→ 下次 ``rebuild_catalog`` 收尾。
- 在 usage append 中途崩（usage record 半行）→ ``read_usage_records`` 把末尾半行当 truncated 容忍。

---

## 6. Canary 发布门（#1105 根治 cutover）

`migrate-dev-agent-streaming-with-1106-patch` D5：堵掉 C3「deferred to natural dispatch」空头承诺。
单测层（FakeTransport）锁 channel-availability precondition，但证明不了真实 Node CLI + 真实 GitHub
凭证下 dev-agent 真能跑 `npm test`。canary 落 `.github/workflows/canary-real-node-cli.yml`，
branch protection 列为 required check（摘 xfail 的 PR 必带一次 green canary）。

### 6.1 自动段（无 secrets，CI/schedule 自动跑）

CI 真实 SDK 下 `sdk_compat_patch.apply()` detection 命中 + ast 变异成功 + `test_dev_agent_stream_lifespan`
三重锁（行为 + 结构 `count("or self.can_use_tool")==1` + identity）。

```bash
python -m pytest scripts/test_dev_agent_stream_lifespan.py -q   # 真实 SDK 行为绿（含结构 + identity 断言）
```

### 6.2 dispatch 段（需 secrets，运维手动启用）

canary 目标抽象：cc-web-control 两 PRD（`custom-mcp-server-url` / `hub-role-pair-view`）是当前实例。
迁移到其他等价目标仓须更新本节 + workflow。

启用步骤：

1. 仓库 Settings → Secrets 配 `PA_GITHUB_TOKEN`（目标仓写权限）。
2. CI runner 装好 roc 代理 claude CLI（见 cron 链路 PATH 约定）。
3. workflow dispatch 段接入真实 dispatch（`run_daily.py --limit 1 --project cc-web-control`），tee 到 `canary.log`。
4. grep `canary.log` 无 `AbortError: Stream closed` ⇔ #1105 根治生效。

### 6.3 Cutover 证据（发布门前必过）

- (a) `canary-real-node-cli` workflow green（自动段至少必过；dispatch 段 green 优先）；
- (b) `python quality_evidence.py` readiness=True；
- (c) `bash scripts/quality.sh` 全绿（compileall + pytest + ruff E9+F）。

### 6.4 Rollback

若 cutover 后发现问题：revert 摘 xfail 的提交 + 移除 `sdk_compat_patch.apply()` 调用（patch 纯叠加，
移除即恢复原 SDK 行为，#1105 重现但 fail-safe 阻断仍在）。版本锁可独立回退（`>=0.2.128,<0.2.130` → 旧锁）。

## 7. Persona 输出契约违反（stage_contracts，change 2026-07-28）

`run_persona` 语法 parse 成功后对已注册 stage（critic / prd）跑 `stage_contracts.validate_stage`：
机械校验硬契约字段（critic 的 `verdict∈{pass,drop,revise}`+`prd_path`、prd 的 `prds[i].path`）。
`error` → 带诊断 `render_repair_hint` 重试一轮；`warning` → 记 log 不改行为；契约层自身故障 → fail-open 降级。
与语法层共享 `for attempt (1,2)` 预算（cap=2）。

### 7.1 识别（log 关键词）

```bash
# 范例：[critic:a] 语义契约违反: verdict(缺 verdict 字段...) → 带诊断重试（attempt 1/2）
grep -E '语义契约违反|契约 warning|fail-open 降级' <cron/run log>
```

### 7.2 Degraded 路径（不阻断流水线）

- 单次违反 + 重试成功 → 该 PRD 正常推进（log 一笔「带诊断重试」）。
- 连续违反（预算尽）→ payload 仍按现状返回（fail-open），下游宽容 `.get()` 处理；critic 段另有 Phase 0 止血（`run_daily.py:766-781`）：`verdict`/`path` 缺失降级 drop，不穿透 `except RuntimeError` 屏障。
- 契约层抛异常 → `validate_stage` 返 `[]`（fail-open），等同于该 stage 未注册契约。

### 7.3 反复违反的运营动作

某 persona 反复漏吐同一字段（log 高频同一 diagnosis）= persona 定义（`.claude/agents/pa-*.md`）的输出契约描述不够明确：

1. 查 `prd_gate_<stamp>.json` / `prd_manifest_<project>.json` 看实际漏吐字段。
2. 收紧对应 persona 定义的输出 schema（列 MUST 字段 + 受控值）。
3. **Non-goal**：stage_contracts 不自动改 persona 定义——谁改 `.claude/agents/pa-*.md` 是独立治理问题。
4. 无需人工干预 cron：dispatch 段不会因 critic 违反崩（Phase 0 止血已防）。

## 8. single-flight-auto-merge（控制面自动合 main + 回滚）

`single-flight-auto-merge` change（ADR-0008）：dispatch 段经 dev-agent 把 verify 双绿分支自动 `--no-ff` merge
进目标仓 main + ff-only push（取代兜底开 PR），merge 后 main 全量测试红则 auto-revert 单 commit。这是 ADR-0001
「不污染目标仓」的受控扩展——**自动 push 到 main**，故叠 8 护栏（执行位置/--no-ff+ff-only/三态 rebase/三态
post-merge+auto-revert/merge commit marker/exactly-once reconcile/flag gated/分支保护准入）。

### 8.1 渐进启用门（ADR-0008 护栏#7：shadow → drill → canary → 全量）

flag 默认关。渐进启用顺序，**逐级**（跳级=危险）：

1. **serial_shadow on**（`PA_SINGLE_FLIGHT_SERIAL_SHADOW=1`）：串行单飞消费（per-repo slot 准入）+ classify-only
   rebase 记 shadow 决策（`shadow_merge_decision` journal 事件），**不 merge/push**（main 不碰）。观察串行不崩 +
   shadow 决策 CLEAN 占比（canary 对照基线）。
2. **离线 drill**（task 7.1b，必做）：`python -m pytest scripts/test_dev_agent_merge.py -q`——真实 git tmp repo
   跑 merge→push→post-merge-test→revert 全链路（含故意红 auto-revert + CONFLICT/UNKNOWN）。补 shadow 测不到的核心链。
3. **canary**（task 7.2）：单项目（cc-web-control）开 `PA_SINGLE_FLIGHT_AUTO_MERGE=1`（须同时 serial_shadow=on，
   preflight 硬拦禁用组合），观察 N 次闭环（含一次故意红 auto-revert + 一次熔断触发）。
4. **全量**：canary 稳定后扩到所有项目。

**preflight 安全门**：`auto_merge=on, serial_shadow=off` → preflight blocked（`coordinator._FLAG_DEPENDENCIES`），
防并发同仓 merge chaos。`auto_merge` docstring 的「gated on serial_shadow+parity+canary」由此硬强制 serial_shadow 依赖。

### 8.2 Rollback（关 flag 回 baseline + 已合 commit 不自动撤回）

关 `single_flight_auto_merge`（及 `single_flight_serial_shadow`）→ dispatch 立即回 baseline：并发投递 + 兜底开
PR 待 review（旧行为，design 决策#8「flag off → baseline 不变」）。**已合进 main 的 commit 不自动撤回**（flag off
不触发 revert——ADR-0008 护栏#5：回滚后可 `git log --grep` 机械找出已合 commit，人工 revert）。

```bash
# 1. 关 flag（profile/env/cron 任一处；两级 rollback：关 auto_merge 回开 PR，再关 serial_shadow 回并发投递）
unset PA_SINGLE_FLIGHT_AUTO_MERGE PA_SINGLE_FLIGHT_SERIAL_SHADOW
# 或 profile: loop: { single_flight_auto_merge: false, single_flight_serial_shadow: false }

# 2. 找出本流水线自动合进 main 的 commit（稳定 marker footer，task 3.4）
git -C <目标仓> log origin/main --grep='Pipeline-Merge: ' --oneline          # 全部自动合入
git -C <目标仓> log origin/main --grep='Pipeline-Merge: <prd_id>' --oneline   # 某 PRD 的合入

# 3. 人工 revert 需回滚的合入（单 merge commit 粒度，-m 1 主线父级；禁 --force*）
git -C <目标仓> revert -m 1 --no-edit <merge_commit_sha>
git -C <目标仓> push origin main

# 4. 查「main 是否已过 post-merge 验证」+ merge/revert 闭环历史（定位坏合入的判决态）
#    main_status journal: state_dir/main_status/<owner_repo>.journal.jsonl（post-merge verdict + merge_commit）
#    merge_loop journal : state_dir/merge_loop/<owner_repo>.journal.jsonl  （merge_started/completed/revert 事件）
```

**halt/quarantine 态**：若回滚前有仓处于 `halted`（post-merge UNKNOWN / revert 非 REVERTED），其 slot 已是 `slot_halted`
终态，下轮 cron 自动 blocked（不再 admit 新 PRD）。回滚后该仓仍 blocked——须人工 triage 后清 slot（参考 §1 journal
恢复 + `recovery_cli.py`），确认 main 干净再恢复。CRITICAL 告警见 `state_dir/alerts/<owner_repo>.journal.jsonl`
（durable，不受 flag gating；ack 后才视为处理）。

### 8.3 Canary 执行（task 7.2：cc-web-control 单项目真实自动 merge）

⚠️ **破坏性、outward**：canary 会把真实 commit 合进 **cc-web-control `main`**（非 throwaway tmp repo；merge/revert
commit 留真实历史）。§8.1 step 1-2（shadow + 离线 drill）全过 + 运维显式 go 才执行。守 `pa-test-no-dirty-data`：手动跑
用**隔离 state + 临时 log + unset PA_HEARTBEAT**，不碰真实 cron.log/SMTP/日报。

**前置**：① cc-web-control profile 就绪 + 今日有针对它的 PRD（`run_daily.py --limit 1 --project cc-web-control` dry-run
   有候选）；② 目标仓 main 有分支保护但允许 ff-only push（ADR-0008 护栏#8：绑 CD/无分支保护 → 禁 auto-merge 退 triage）；
  ③ `PA_GITHUB_TOKEN` 有 cc-web-control 写权限。

**执行**（隔离 state，临时 log）：

```bash
export PA_SINGLE_FLIGHT_SERIAL_SHADOW=1 PA_SINGLE_FLIGHT_AUTO_MERGE=1   # 双 flag on（preflight 要求 serial_shadow on）
unset PA_HEARTBEAT                                                       # 手动跑防误触 heartbeat 告警
CANARY_STATE=$(mktemp -d)                                                # 隔离 state（不碰真实 .project-auto/state）
python3 Projects/项目推进流水线/scripts/run_daily.py \
    --project cc-web-control --state-dir "$CANARY_STATE" --no-notify \
    2>&1 | tee /tmp/canary-automerge-$(date +%s).log
# 确定性投递（不依赖今日 radar 命中）：用 --from-stage inject --inject-prd <手写PRD.md> 替上面，直达 dispatch
```

**观察 N 次闭环**（跨多 cron 周期；含一次故意红 auto-revert + 一次熔断触发）：

```bash
LOG=/tmp/canary-automerge-*.log
grep -E '🎉.*已合 main|✅.*post-merge main 全量测试绿.*merged' $LOG    # (a) 正常闭环：merge + post-merge PASS
grep -E '🔴.*post-merge main 红.*auto-revert|↩️.*revert 成功' $LOG     # (b) 故意红 → auto-revert REVERTED → main 回绿
grep -E '🧊.*熔断命中.*cooldown_revert_loop' $LOG                      # (c) 同 PRD 再投 → circuit breaker 触发 → triage
grep -E '🛑.*(halt|CRITICAL)' $LOG                                      # halt/quarantine（UNKNOWN/revert 失败）→ 须人工
git -C <cc-web-control> log origin/main --grep='Pipeline-Merge: '      # 确认 marker 落地（§8.2 回滚锚点）
```

**通过判据**（全过才扩全量）：(a)(b)(c) 各至少出现一次且无 (d)；merge_loop/main_status/alerts journal 事件自洽
（merge_started→completed / revert_started→completed 闭合无 open intent）；目标仓 main 最终绿（无残留红 commit）。

### 8.3.1 canary 一键脚本（`scripts/canary_automerge.sh`，推荐）

上面 §8.3 的手动流程已封装成 `canary_automerge.sh`（隔离 state + 临时 log + unset PA_HEARTBEAT，
守 `pa-test-no-dirty-data`）。**outward 子命令必须显式 `--go`**（否则 dry-run 只打印命令）：

```bash
cd Projects/项目推进流水线
bash scripts/canary_automerge.sh prep                       # 无害前置检查（默认；rc=0 = 前置就绪）
bash scripts/canary_automerge.sh branch-protect save --go    # ① 备份 main 保护 + 临时移除（outward）
bash scripts/canary_automerge.sh run-a --go                  # ② 判据 a：绿 smoke → merge + post-merge PASS
bash scripts/canary_automerge.sh inject-red --go             # ③ 判据 b：注入红 merge → post-merge FAIL → revert REVERTED
bash scripts/canary_automerge.sh check-c --go                # ④ 判据 c：注入 cooldown → 再投 → 熔断
bash scripts/canary_automerge.sh verify                      # 无害判据 grep + journal 闭合检查
bash scripts/canary_automerge.sh branch-protect restore --go # ⑤ 恢复 main 保护（canary 后必做）
```

**前置就绪（prep 核对，2026-07-30）**：§8.1 step 1-2 绿（shadow task 7.1a + 离线 drill 12 测）+ flag env +
CLI 参数（`--project`/`--state-dir`/`--no-notify`/`--inject-prd` 均真实存在）+ cc-web-control profile/本地仓/
remote + gh 认证（jyf2100）+ main 双检出 bug 已修（detached checkout，commit 59e780a）。

**分支保护（实测不拦，§8.3.1 ① 为可选保险）**：cc-web-control main 虽有 `required_pull_request_reviews` +
`enforce_admins=true`，但 **direct push 实测可过**——origin/main 已有自动合入 marker（`7b35bb6 pa-merge:
20260729_effort-cache-lockdown`，#50 经 dev loop 绿→merge→post-merge pass 真实合入，2026-07-30）。GitHub
PR-review 保护不阻 owner/admin 的 direct push。故 `branch-protect save/restore` 是**可选保险**（万一某仓配置
不同真拦了），非 cc-web-control canary 的必需前置。**判据 a 已实质验证**：#50 effort-cache-lockdown 闭环 PASS
（merge `7b35bb6` 带 `Pipeline-Merge` marker + post-merge pass + main 绿），只是用真实 PRD 而非 smoke 载体。

**故意红（判据 b，§8.3.1 ③ inject-red）**：dispatch 前置是「dev+verify 双绿 → 才 merge」，故 PRD 经 dev loop
**无法自然产生 post-merge 红**（红测试会被 verify 先拦，到不了 post-merge）。判据 b 因此**绕 dev loop**：
`inject-red` 在独立 worktree（detached at main，不碰主工作区）建红 merge commit（`--no-ff` + `Pipeline-Merge`
marker，复刻 dispatch 形态，可被 `revert -m 1`）push 到 main → 触发 `dev-agent --phase post-merge-test`（FAIL）
→ `--phase revert`（REVERTED）→ main 回绿。对齐离线 drill `test_post_merge_fail_then_revert_restores_green`
的 fixture 模式。`canary-red.prd.md` 文档化了此双绿约束边界。

**熔断（判据 c，§8.3.1 ④ check-c）**：同判据 b 根因，「经 dispatch 的 post-merge FAIL→record_revert」难自然触发。
`check-c` 直接 `circuit_breaker.record_revert` 注入 cooldown 记录 → 再投同 slug PRD → 验 dispatch merge 前
`is_in_cooldown` 命中 → triage(cooldown_revert_loop)。完整 record→cooldown 闭环由 `test_circuit_breaker.py`
11 测覆盖；此处验熔断门在 dispatch 真实路径生效。

**工作区（无需清）**：cc-web-control 主工作区当前有 effort 功能删除重构（16 文件，并行真实工作，**勿 stash/
discard**）。canary 全程用 dispatch 独立 worktree（`.worktrees/` 或 `inject-red` 的 mktemp worktree），不碰主工作区。
唯一注意：main 在主工作目录检出 + 脏，但 merge/revert phase 的 detached/ref 级操作（59e780a/0437031）已避开双检出。

## 9. LangGraph 编排层 cutover（graph_pa 渐进迁移，`langgraph-workflow-upgrade`）
`graph_pa.py`（LangGraph 主图）与 `run_daily.py`（legacy）并行存在，`PA_GRAPH_SHADOW`/`PA_GRAPH_ORCHESTRATOR`
两 flag 物理隔离（flag off = run_daily 完整保留，graph_pa 不被 import）。`run_cron.sh` 分流：`PA_GRAPH_ORCHESTRATOR=1`
→ `graph_pa.py`，else `run_daily.py`。**秒回退 = `unset PA_GRAPH_ORCHESTRATOR`**（下个 cron 回 legacy）。

### 9.1 graph 崩溃恢复（journal-driven，非 graph state 重建）
graph 路径**不用 Checkpointer**（D2），持久化走 journal 单写：commit_node 接 `append_event`+`fsync`。崩溃恢复
**复用 §1 的 `recovery_cli.py` over journal**（`graph_pa_recovery.py` 重建 `_REQUIRED_SHELL` 续跑前置，但终态以 journal 为准）：
```bash
# 同 §1：exit 0 = 可 --from-stage 续跑 / exit 2 = manual_block（中部损坏 fail-closed，不静默跳过）
python recovery_cli.py <state_dir>/runs/<proj>/<stamp>_<slug>.journal.jsonl
# 续跑（graph 路径）：复用已落盘 state 产物
PA_GRAPH_ORCHESTRATOR=1 PA_GRAPH_SHADOW=1 python graph_pa.py --from-stage <stage> ...
```

### 9.2 Shadow parity canary（`canary_graph_cutover.sh`，双源 byte-identical 验证）
双源（legacy `run_daily` vs `graph_pa`）同 PRD 输入下 dispatch 终态 byte-identical 验证。默认 `--dispatch-skip-dev`
（零 outward + 零 LLM 成本，守 `pa-test-no-dirty-data`：隔离双 state_dir + 临时 log + unset PA_HEARTBEAT）：
```bash
bash scripts/canary_graph_cutover.sh prep          # 无害前置（flag 注册 + graph 套件绿 + profile）
bash scripts/canary_graph_cutover.sh shadow-run    # 双跑（legacy + graph）隔离 state + skip-dev
bash scripts/canary_graph_cutover.sh parity        # 对照双 dispatch_{stamp}.json（matched 判据）
bash scripts/canary_graph_cutover.sh verify        # 汇总判据 + ≥3 cron 提示
STAMP=20260813 bash scripts/canary_graph_cutover.sh shadow-run   # 指定 stamp（多 cron 周期）
```
判据（全过才移硬 gate）：(a) `parity.matched=True`〔**硬 gate**，终态 Counter 分布一致〕+ (c) `load_ok=True`
〔**硬 gate**，双源 dispatch_{stamp}.json 都成功 load，非双失败假绿〕；诊断维度 (b) per_stage byte-identical
（双 state_dir 路径前缀 + verify_round 初始值预期 mismatch，须人工确认无语义漂移）+ (d) ≥3 cron 周期。

### 9.3 Cutover 发布门（`run_full_cutover_suite`，9 维度）
graph 路径移硬 gate 前必过 cutover suite（9 维度 overall_passed）：含 `graph_shadow_parity` 条件性硬 gate——
真 `ShadowParityReport` matched=False（双源都产 dispatch.json 但终态分布漂移 = 真实编排回归）**硬阻断** overall；
`LoadFailureReport`（baseline flag off 无真双源）**软 open**（不阻断 baseline，进 open_items 诚实报告）。
```bash
# 9 维度全绿 → overall_passed=True + 归档 immutable manifest digest（design 决策#6）
PYTHONPATH=scripts python -c "import cutover as C; print(C.run_full_cutover_suite(drills=C.CutoverDrillBundle(...), artifact_root='...').overall_passed)"
```

### 9.4 Rollback（秒回退 = unset flag）
两级回退（同 §8.2 哲学）：关 `PA_GRAPH_ORCHESTRATOR` 回 legacy 编排；再关 `PA_GRAPH_SHADOW` 回无 shadow。
```bash
unset PA_GRAPH_ORCHESTRATOR PA_GRAPH_SHADOW     # run_cron.sh 下个 cron 秒回 run_daily.py（graph_pa 不被 import）
# state 产物（dispatch_{stamp}.json / journal）双路径同 schema，回退无需迁移
```

## 10. verify 闭环单 PR 语义（verify-loop-discipline，change 2026-08）

branch/worktree 去 stamp（跟 slug）后的两条一次性运维：

### 10.1 旧 stamp worktree / 分支清理（一次性）
A1/A3 上线前产生的旧命名残留（`<stamp>-<slug>` worktree、`auto/<stamp>-<slug>` 分支/PR）不会被新
remove-if-exists 自清（路径不同）。一次性清理（各目标仓）：
```bash
git -C <repo> worktree list | grep -E '\.worktrees/[0-9]{8}-[0-9]{4}-'   # 旧 stamp 命名 worktree
git -C <repo> worktree remove --force <path>
git -C <repo> branch --list 'auto/*' | grep -E 'auto/[0-9]{8}-[0-9]{4}-'  # 对应旧分支（PR 处理见 §10.2 注）
```
遗留 open PR 按需求处置（同 slug 新 PR 已开则关旧的；见 change E1 惯例）。

### 10.2 `_ensure_feature_branch` show-ref 本地盲区恢复
分支存在性查 `git show-ref`（**本地**）——若本地分支被误删但 origin 仍有（或换 clone 后本地缺失），
checkout 会 `-b` 新建导致与远端分叉。恢复路径：
```bash
git -C <repo> fetch origin <branch> && git -C <repo> branch <branch> origin/<branch>   # 重建本地引用
# 或让 dev-agent checkout fallback：git checkout -b <branch> origin/<branch>（follow-up，暂手动）
```
判据：dev log 出现 `already exists` 反复失败 / PR diff 突然缩水（新分支只含本轮 commit）→ 查
`git rev-parse <branch>` vs `origin/<branch>` 是否同 SHA。

## 11. 部署拓扑与路径真相（2026-10 产品化前置 F3）

**双 checkout 拓扑**——vault 有两个 checkout，职责不同：

| | 运行时真身机（roc mac） | 本服务器（Linux 开发/评审 checkout） |
|---|---|---|
| 路径 | `/Users/roc/…`（vault + 目标仓 workspace 双检出） | `/mnt/disk01/workspaces/worksummary/agentworkflow` |
| cron | ✅ 03:17 `run_cron.sh`（`install_cron.sh` 装） | ❌ 无 |
| `.project-auto/`（profiles、sources.yaml、state） | ✅ 存在（gitignore，绝不入仓，故**只在此机**） | ❌ 不存在（冷 checkout） |
| 目标仓 worktree | ✅ `<repo>/.worktrees/`（dispatch 建） | ❌ 无目标仓 |
| 职责 | 流水线每日运行 | 编辑 pa 源码、评审、git 操作 |

**路径真相**：CONTEXT.md / SPEC.md / 旧报告中的 `/Users/roc/*` 路径指运行时真身机，在本服务器上无效（对应物是 `/mnt/disk01/…`）；两 checkout 经 `github.com/jyf2100/agentworkflow` remote 同步。**在冷 checkout 上跑 `run_daily.py` 会因 `.project-auto` 缺失而无法全链运行**（VAULT_ROOT 推导正常但 profiles/sources 不在）——这是预期行为，不是 bug。恢复/迁移运行时 = 从真身机搬 `.project-auto/`（含 profiles、sources.yaml、去重清单；state 可选）到目标机 vault 根。

## 12. 执行 hub 骨架（add-hub-skeleton 切片 1，2026-10）

**部署形态**：vault 根 `hub/` 是服务器集中式部署的执行调度层（阶段 2 切片 1 骨架）——与控制面（vault 大脑）、目标面（被控项目仓）三分。hub 只做机械活：队列排度（SQLite，per-project 串行 + 峰值上限 + 重派状态机）、F10 卷上署名 meta、容器生命周期记账、最小 API/CLI。语义活（审 PRD/判价值）仍归 pa persona。**hub 不 import pa**（两部署物零耦合）。

**env 清单**（单用户直注 bootstrap，切片 2 换凭据保险箱）：必需六项 `HUB_USER_ID` / `HUB_USER_ANTHROPIC_BASE_URL` / `HUB_USER_ANTHROPIC_AUTH_TOKEN` / `HUB_USER_GH_TOKEN` / `HUB_IMAGE_TAG_DEFAULT` / `HUB_DATA_ROOT`（缺失列出全部缺项拒绝启动）；可选 `HUB_USER_MODEL`（模型路由）、`HUB_MAX_CONTAINERS`（默认 3）、`HUB_MAX_RETRIES`（默认 2）。凭据只经 0600 env-file 注入容器（即弃即删），绝不进错误消息/日志/DB/镜像层。

**数据卷布局**：`<HUB_DATA_ROOT>/<project>/state/` 是该项目 state 卷（挂给容器内 pa 的 `/workspace/vault/.project-auto/state`）；run 目录 = `state/runs/<proj>/<stamp>_<slug>/`；hub 在卷上的**写面钉死为仅 meta 署名文件**（`<stamp>_<slug>.meta.json`，文件名正则硬校验）——journal 与 state 其他内容归容器内 pa 写。

**运维口径**：
- 质量命令：`bash hub/quality.sh`（compile+pytest+ruff 三段，与 pa quality.sh **并列**——pa scripts/ 零改动）。
- 入队/查状态：`python -m hub.cli enqueue --project X --prd /path/to.md`（或 `hub/` 下 `python cli.py …`）；API 三端点 `POST /queue`、`GET /queue`、`GET /runs/{id}`。
- 镜像缺失（F13）：队列项转 failed 并报 tag 与 build 提示——**先手动 build 该 tag 再重新入队，绝不回退 latest**。
- hub 崩溃恢复：重启即 `reconcile_startup` 对账——容器在跑续跟踪（hub 崩溃不杀容器，docker -d 进程树分离）；已消失按卷上 journal 证据归档（有 journal → run failed 走重派；无 journal → failed_to_start）；卷上孤儿 meta（有 meta 无 journal 且 DB 无记录）补索引 failed_to_start，meta 保留不清理。
- 重派语义（A3）：容器失败回 pending 保留 attempt 计数（FIFO 保序天然优先），`HUB_MAX_RETRIES` 达上限才转 failed；run 的 failed（容器事实）与队列项的 failed（放弃决定）是两个生命周期。
- daemon 故障退避（本体论审核 B2）：起容器基础设施异常（如 docker daemon down）→ run 记账 failed_to_start、队列项留 pending；连续失败进冷却（`dispatch_backoff` 默认 3——每 3 tick 重试一次，不逐 tick 记账膨胀 runs 表），daemon 恢复后自动续派；meta 首写保真（written_at 不因重试刷新，各次尝试的时间线在 runs 索引各行 started_at）。镜像缺失（F13）是确定性失败不重试——终态 failed，修环境重新入队。

## 13. hub compose 部署（add-hub-slice-2a 切片 2a，2026-10）

**形态**：pa-hub 以 docker compose 常驻（`hub/deploy/docker-compose.yml`，唯一 service；执行容器是 hub 经 `/var/run/docker.sock` 起的**兄弟容器**，不在 compose 里——D1）。hub 镜像 = thin 独立 Dockerfile（`hub/deploy/Dockerfile`，python:3.11-slim + fastapi/uvicorn/cryptography/pypinyin + docker CLI，与执行镜像零共享层）。排度常驻 = FastAPI 同进程 daemon 线程（`serve.py` 入口，`HUB_TICK_INTERVAL` 默认 5s）。

**部署三步（顺序不可倒——①时 data_root 尚属宿主用户可写，②之后宿主写不进）**：
1. **vault-import**：宿主 checkout `hub/` 下 `python3 cli.py vault-import --from-env`（env 具备六项必需 + `HUB_VAULT_MASTER_KEY`；master key 生成 `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" > hub/deploy/vault.key && chmod 600 hub/deploy/vault.key`）。确认输出只含字段名不含值；重复执行幂等覆盖。凭据源文件 `hub/deploy/hub.env`（0600）同放 deploy/ 下——**vault.key 与 hub.env 均已 gitignore + dockerignore 钉死，绝不入仓/入镜像层**。
2. **chown**：`sudo chown -R 1001:1001 /data/pa`（连 vault 一起递归；hub 容器以 `1001:1001` 跑 = 与执行镜像 `pa(uid=1001)` 对齐，**拒 777**）。per-project 启动必读面（`<proj>/profiles/<proj>.yaml` + `<proj>/sources.yaml`）缺则派发 fail fast（补齐重新入队）。
3. **compose up**：`docker compose -f hub/deploy/docker-compose.yml up -d --build`。执行镜像先手动 build 显式 tag（F13，队列项入队时固化 tag；镜像缺失首派发 fail 暴露，绝不回退 latest）。

**凭据口径（D3）**：派发时 vault 优先（`<data_root>/vault/credentials.json` 单 Fernet token，0600/0700）→ 无该 user 记录降级 hub 进程 env 直注（warn 日志）→ 双缺明确报错（列缺失项）。master key 配了但坏 = 启动即崩，不静默吞成 env-only。

**运维口径**：
- 优雅退出：`docker compose stop`（SIGTERM → uvicorn lifespan shutdown → daemon 线程有界等待当前迭代收尾（join 上限 10s），超界随进程退出、悬挂 run 由重启对账归档兜底；`stop_grace_period: 30s` 外层兜底）——在跑执行容器不动，重启后 `reconcile_startup` 幂等对账接管。
- 崩溃自愈：进程内崩溃（exit/OOM/容器内 `kill -9 1`）由 `restart: unless-stopped` 拉起，先 `reconcile_startup` 幂等对账再进排度（2026-10-21 实证：runs 行数不变、无重复派发）。注意 **`docker kill`/`docker stop` 是显式操作，restart 策略不覆盖**——显式停后须手动 `docker start`。
- socket 权限：hub 以 uid=1001 跑而宿主 `/var/run/docker.sock` 是 `root:docker 0660`——compose `group_add: "${HUB_DOCKER_GID:-999}"` 补宿主 docker 组 gid（本机实测 999；换机核 `stat -c %g /var/run/docker.sock`，可在 hub.env 覆盖）。症状缺失时派发报 connect permission denied。
- data 卷是**同路径 bind**（`/data/pa:/data/pa`）：hub 容器内生成的 `-v` 源路径被宿主 daemon 按字面解析，内外必须同路径——改挂载点须同改 `HUB_DATA_ROOT`。属主不对齐（漏 chown）的症状：每个 tick 日志刷 `[daemon][ALERT]` PermissionError、队列项永留 pending 无终态——核 `ls -ln /data/pa` 属主是否 1001，补跑三步②。

**MM OAuth 身份（2b，可选面）**：MM 做 IdP（不新建用户系统），hub 发自包含 HMAC 签名会话 cookie（重启/MM 宕机会话不丢；`HUB_SESSION_SECRET` 与 vault master key 分离）。启用前置：MM 管理员在 **系统控制台 → 集成 → OAuth 2.0 应用** 注册 confidential client，**redirect_uri 填 `http://<hub-host>:<port>/auth/callback`**（与部署可达地址严格一致）；`hub.env` 补四项（见 `hub/deploy/hub.env.example`）：`HUB_MM_BASE_URL` / `HUB_MM_CLIENT_ID` / `HUB_MM_CLIENT_SECRET` / `HUB_SESSION_SECRET`（`openssl rand -base64 32`；可选 `HUB_SESSION_TTL_S` 默认 7d）。**四项全不配 = 2a 单用户回退态**（auth 群 503、`POST /queue` 走 `HUB_USER_ID` 兜底，零行为变化）。

- 登录流：`GET /auth/authorize` →（MM 授权页）→ `GET /auth/callback` 发会话 → `GET /auth/me` 确认。登录后本人 `PUT /auth/credentials` 录凭据入保险箱（key = mm_user_id；响应只含字段名不回显值）。会话入队时 `enqueued_by=mm_user_id`（vault 解密 key）、`initiator=username`（meta 人读署名）；该用户未录凭据 = 派发降级 hub env + warn。
- auth 排障：**503** = MM 四项未配全（或 `PUT /auth/credentials` 时 `HUB_VAULT_MASTER_KEY` 未配）；**400 state** = 授权往返超 10 min 或非同浏览器发起（state 签名短期 cookie 校验）；**401** = 无有效会话（未登录/已 `GET /auth/logout`/cookie 过期，重登即可）；**502** = MM token 端点/`users/me` 拒绝或宕机（查 MM 侧 client_secret 与可用性）。

**MM OAuth 真机实录（11.10.1，2026-10-23 定稿；编程式注册/自测时必读）**：

- API 注册 `POST /api/v4/oauth/apps` 字段名是 **`callback_urls`**（蛇形数组）。字段名错（如 `callbacks`）时 MM 反序列化得空数组，与「URL 非法」报**同一错误码** `model.oauth.is_valid.callback.app_error`——报「URL 必须以 http:// 开头」先核字段名再怀疑 URL。
- MM **忽略提交的 client_secret**，自生成 26 位（NewId 形态）——**只在注册响应 `client_secret` 字段出现一次**，`GET /api/v4/oauth/apps/{id}` 已脱敏不返回。注册脚本必须当场从响应捕获写盘（写前校验 id 为 26-hex）；丢了只能删 app 重注册。
- token 端点是 **`POST {MM}/oauth/access_token`**（网页路由、form-encoded：grant_type/client_id/client_secret/code/redirect_uri）——`/api/v4/oauth/token` 与 `/api/v4/oauth/access_token` 均 404。hub `auth.py exchange_code` 已按此实现。
- 编程式走授权流（自测用）：MM 网页会话 cookie 是 **`MMUSERID=<uid>; MMAUTHTOKEN=<API login Token>`**（MM 11 名，非老文档的 `MMTOKEN`）；app 设 `is_trusted=true` 时登录态 GET `/oauth/authorize` 直接 302 回 callback 带 code（无 Allow 页）。

**F2 复审结论（2b D6，2026-10-22 两笔均维持现状）**：
- **socket 挂载等效 root**：维持（内网单机形态成本最优）；**公网/多租户暴露前必须加 socket proxy（如 docker-socket-proxy 白名单化 API 面）**，此项为暴露硬门槛，不是可选优化。
- **master key 经 compose env 注入、无轮换工具**：维持（YAGNI——单 key 单机，泄漏概率低且可整箱重录）；后手：真需轮换时做 re-encrypt 工具（新 key 重加密全 vault 记录 + 原子切换），届时同步补 vault-store spec。

- 回退：`docker compose down` 后切片 1 手工形态完整可用（env 兜底保证代码向后兼容；`vault-import` 不必撤销，data 卷属主 chown 回滚无害）。auth 面回退 = hub.env 去掉 MM 四项重启（auth 群 503，队列/派发回到 2a 单用户行为）。

## 14. 段级熔断 PA_BREAKER（pa-hardening P0-1，OBSERVE 灰度）

**机制**（`scripts/stage_breaker.py`，与 §8 的 `circuit_breaker.py` 键空间/journal/调用点均不重叠——那边熔「合入后回退」循环，这边熔「段级 persona 连续失败 + 日成本」）：双熔丝——熔丝1 `PA_BREAKER_N`（默认 3，同段连续失败）+ 熔丝2 `PA_BREAKER_COST_CAP`（美元，当日累计）。状态文件 `<state_dir>/breaker_<stamp>.json`（次日自然重置）；跨日观测面 `<state_dir>/breaker.journal.jsonl`（事件 `stage_breaker_tripped` / `stage_persona_failed`）。真熔 = journal 落事件 + 抛 `CircuitBreakerTripped` → run_daily 写 checkpoint `halted` + exit 3。fail-open：状态损坏不熔（护栏需正向证据）。

**当前状态**：`run_cron.sh` 挂 `PA_BREAKER=1 + PA_BREAKER_OBSERVE=1 + PA_BREAKER_COST_CAP=1.0`（2026-09-15 批准，观察期 1-2 周校准 cap；cap = 观测值 $0.30 的 3 倍）。OBSERVE = fuse 命中只 log + 落 journal 不真熔。**2026-10-22 盘点：超期未收口，数据在 roc 真身机 `.project-auto/`（gitignore 不入仓），须 roc 侧提取后裁决。**

**收口数据提取（roc 真身机 vault 根）**：
```bash
# ① 熔丝命中（observe 降级）事件数与样本——真熔后 halt 频率预估
#    注：journal 惰性创建（零失败即无文件，grep 兜底输出 0）；非 observe 模式下 _trip 的 journal
#    事件先于状态文件写、fail-open 时状态写失败不熔——计数轻微高估，方向可接受
grep -c '"stage_breaker_tripped"' .project-auto/state/breaker.journal.jsonl 2>/dev/null || echo 0
grep '"stage_breaker_tripped"' .project-auto/state/breaker.journal.jsonl 2>/dev/null | tail -3
# ② 成本分布（熔丝2 cap 校准数据源）——per-stamp 与按天聚合双口径
#    注：同日手动/恢复重跑 stamp bump（m2/...）各自成 breaker 文件，「当日累计」被碎片化，cap 校准以按天聚合为准
python3 - <<'PY'
import json,glob,collections,math
xs=[json.load(open(p)).get('cost_usd',0) for p in glob.glob('.project-auto/state/breaker_*.json')]
if not xs: raise SystemExit('无 breaker 状态文件（疑似 env 注入链失效，见判定规则第三条）')
p95=lambda v:sorted(v)[max(0,math.ceil(.95*len(v))-1)]  # 夹逼取保守高位，小样本不低估
by_day=collections.defaultdict(float)
for p in glob.glob('.project-auto/state/breaker_*.json'):
    by_day[p.split('breaker_')[1][:8]]+=json.load(open(p)).get('cost_usd',0)
days=sorted(by_day.values())
print('per-stamp n=%d p50=%.2f p95=%.2f max=%.2f'%(len(xs),sorted(xs)[len(xs)//2],p95(xs),max(xs)))
print('按天聚合 n=%d p95=%.2f max=%.2f（cap 校准以此为准）'%(len(days),p95(days),max(days)))
if len(days)<20: print('⚠ 天样本不足 20，p95 仅供方向参考')
PY
```

**判定规则 → 收口三选项**（去/留改 flag 均改 `run_cron.sh` 三行）：
- observe 事件 0 次 且 ② p95 ≪ $1.0 → **选项 A（去 OBSERVE 真熔）**：删 `PA_BREAKER_OBSERVE` 行，`PA_BREAKER_COST_CAP` 收紧至 p95×2 左右。
- observe 事件频繁 → 真熔会日级 halt：先调 cap/N 再观察一轮（**选项 B**），或判定成本风险可接受走 **选项 C（关 BREAKER 回 baseline）**。
- journal 连 `stage_persona_failed` 都为 0 → 核 env 注入链是否生效（cron 非 login shell）；确为 5 周零失败 → 熔丝1 无数据，按默认 N=3 真熔即可（保守值）。

- 回退：注释 `run_cron.sh` 三行，下个 cron 即回 baseline（flag off = 全 no-op 零行为变化）。

## 15. 投递协议运维（add-hub-slice-3，PRD state ownership 交接）

**机制**（F11）：mac cron `--to-stage prd` 只跑前半段（fetch→radar→prd 产出即停），`scripts/deliver_prd.py` 随后串行投递至服务器 hub `POST /deliver`（幂等键 `(project, 文件名, sha256)`，file-first 落盘 `<卷根>/<project>/prd/`）；容器内经既有 inject 桥消费（`--from-stage inject --inject-prd`），critic 起不进 mac——执行链权威物理唯一。vault 视角 PRD 生命周期 `produced → delivered` 止步（critic 结果在服务器 state，vault 不知道也不需要知道）。

**部署顺序（钉死——顺序错会产生双执行窗口）**：
1. hub 侧先上：拉 main 重启 hub 容器（端点纯新增，不配 token = 内网无守卫，现有调用零影响）；
2. pa-executor 镜像重建（critic prompt 改动硬依赖）：build context = pa template 投影 checkout（服务器 `/mnt/disk01/workspaces/worksummary/pa-instance`），**build 前置检查：投影根须有 `.dockerignore`**（排除 `.project-auto/`、`.venv/`、`*.egg-info/` 等——本体论审核 C-F1 2026-10-23：.gitignore 只防入仓不防入镜像，全量 Dockerfile 的 `COPY . /opt/pa` 曾会把 428M 的 `.venv/` 与流水线本地状态打进镜像层）。Dockerfile 已归档 `hub/deploy/pa-executor.Dockerfile`（2026-10-23 从 v0.1.3 docker history 反提取重建——原始 build context 丢失的部署资产缺口已补；注意 FROM 是 node:22-slim 而 v0.1.3 实际基为 node 22 bookworm 完整版，含 yarn 等额外工具链，全量重建与 v0.1.3 行为面非严格等价）。**小改动走增量补丁版**（`hub/deploy/pa-executor.patch.Dockerfile`：`FROM` 上一 tag + `COPY` 变更文件，不重跑 apt/npm/pip 网络层，行为等价性风险最低；v0.1.4 即此法）；变更文件先打进投影 checkout 再 `docker build -f hub/deploy/pa-executor.patch.Dockerfile -t pa-executor:<新tag> /mnt/disk01/workspaces/worksummary/pa-instance` → 冒烟（例：`docker run --rm --entrypoint grep pa-executor:<新tag> -n "def exit_diag" "/opt/pa/Projects/项目推进流水线/scripts/persona_call.py"` + `docker run --rm --entrypoint python3 pa-executor:<新tag> /opt/pa/Projects/项目推进流水线/scripts/run_daily.py --help`）→ 服务器 `hub.env` **与 `hub.env.example` 同步**升 `HUB_IMAGE_TAG_DEFAULT`（example 是模板真理源，漂移会让照抄者静默回退 tag）→ `docker compose up -d` 重启 hub → `docker exec deploy-pa-hub-1 printenv HUB_IMAGE_TAG_DEFAULT` 确认。不重建则容器内 critic 仍是旧 prompt（静默不生效）；
3. mac 侧**同一 cron 变更**一次切换：`run_cron.sh` 里 `--to-stage report` → `--to-stage prd` + 追加 `deliver_prd.py` 调用（物理分家与投递同时生效）。

**executor 工程技能面**（add-executor-eng-skills，v0.1.8 起，spec: execution-hub）：镜像内置 mattpocock-skills 1.3.1 engineering 面 20 个工程 skill（9 个模型可自主调用；11 个 `disable-model-invocation` 惰性随行），快照在 `hub/deploy/executor-skills/`（含 `PROVENANCE.md` 溯源；**投影 manifest 已纳管该路径**——投影仓原本不含 hub/，不纳管则后续 tag 的 build context 缺 COPY 源），build 时拷至容器 `/home/pa/.claude/skills/` 供 dev loop 会话发现（persona 工具面由 agent 定义 `tools:` 收窄，不受影响）。升级路径：按 PROVENANCE 内命令刷新快照 → patch 通道 build 新 tag → `hub.env` 与 example 同步升（同上第 2 步尾部流程）。

**token**：`hub.env` 配 `HUB_DELIVER_TOKEN=<random>`（注释态默认无守卫，内网可接受；公网暴露前是硬门槛），mac 侧投递器 env `PA_DELIVER_TOKEN` 传同值、`PA_HUB_URL=http://<hub-host>:<port>`。

**ledger / 死信排查**（`<state_dir>/deliver_ledger.jsonl` append-only，半行损坏自动跳过视为未投递）：
```bash
# 死信分布（error_class 一眼分流）
grep '"failed"' .project-auto/state/deliver_ledger.jsonl | python3 -c "import sys,json,collections; print(collections.Counter(json.loads(l)['error_class'] for l in sys.stdin))"
```
- `config_4xx`（401/403/404/405）：部署/凭据错（token 失配、URL 错、hub 未升级）——投递器已高声告警非零退出（exit 2），修 hub 侧后 `--retry` 重投；
- `data_4xx`（400/413）：单份坏数据（sha 不符/超 1MB/文件名非法）——exit 1，修 PRD 产出后 `--retry`；
- `unknown_exhausted`：连续 5 次 UNKNOWN（网络/hub 5xx）转死信——排查 hub 后 `--retry`。
- 退出码：0=无死信；1=有死信；2=config_4xx；3=hub 地址缺失。

**完整回退序列（五步，顺序不可乱——「服务器与本机执行互不干扰」不可依赖，in-flight 双跑仍撞目标仓 branch 命名空间）**：
1. **停投递**：`run_cron.sh` 注释掉 `deliver_prd.py` 调用行（先停增量）；
2. **drain running**：等服务器 running 队列项到终态，或停容器（`GET /queue` 看 running；「清 pending」不覆盖 running）；
3. **清服务器 pending**：逐项 `DELETE /queue/<id>`（2.5 原语——仅 pending 可清，清除 = 转 failed 留审计记录）；
4. **回退 cron**：`--to-stage prd` → `--to-stage report`（mac 重新全链本机跑）；
5. **处理当日已投 PRD**：ledger 已 delivered 但服务器未执行的 PRD，于 mac 逐份 `run_daily.py --from-stage inject --inject-prd <落盘副本>` 补跑，**或明确放弃并记录**——不补跑则这些 PRD 两侧都不执行 = 静默丢失。
   （投递 PRD 的 mac 本机副本即 `state/prd/<project>/` 原产出文件，投递不移动不删改它。）

## 16. 通知与陈旧检测（add-hub-slice-4a 切片 4a，2026-10）

hub 观察面三件套：读面会话守卫 + MM 通知 bot + 镜像陈旧检测（spec: `openspec/specs/`（归档后）hub-notifications / execution-hub delta）。全离线可测，真机联调项见下。

**bot 创建（前置）**：MM 管理员在 系统控制台 → 集成 → Bot 帐号 新建 bot（独立于 OAuth 2.0 应用的第二个凭据实体——登录归 OAuth app，推通知归 bot token）。记下 bot token；每个要收通知的用户需把 bot 加为 DM 联系人（否则 DM 推递失败——fail-open 只留 stderr warn，不重试）。**PAT 替代（2026-10-27 真机实证可用）**：也可走 系统控制台 → 集成 → 个人访问令牌——推递功能等价，仅凭据身份不同（PAT 绑定创建用户本人而非独立 bot，F14「身份与通道两范畴」语义弱化为二者同身份）。

**env 填值（hub.env）**：
- `HUB_MM_BOT_TOKEN`：bot token（与 `HUB_MM_BASE_URL` 齐才生效）。**⚠️ PAT 形态坑（2026-10-27 真机实证）**：本 MM 实例的个人访问令牌生效形态是**只填「访问令牌」单段**——令牌 ID 拼接成 `id.secret`（标准 bot token 形态）反而 401 `api.context.session_expired`（首配踩过此坑，check-notify 卡 1/3 即查此处）；改值后须 `docker compose up -d` 重建再自检；
- `HUB_MM_PROJECT_CHANNELS=proj:chan-id,...`：可选失败广播频道（仅 failed/failed_to_start；chan-id 是频道 id 非名字）；
- `HUB_IMAGE_TEMPLATE_REPO=owner/repo`：陈旧检测比对源（不配 = 关闭）；`HUB_STALE_CHECK_TICKS` 默认 720（≈5s tick 每小时一查）。

**两级回退**：只 unset `HUB_MM_BOT_TOKEN` = 通知静默（读面守卫不变——bot 通道与 auth 独立）；连 MM 四项（BASE_URL/CLIENT_ID/CLIENT_SECRET/SESSION_SECRET）也 unset = 整体回 2a 回退态（读面守卫同退、auth 群 503）。读面守卫强度跟随 auth 配置面，无独立开关。

**MM 真机联调项**（离线测试覆盖不了的面）：
1. bot DM 两步 API（`/channels/direct` 建 channel → `/posts` 发帖）对本 MM 版本的行为；
2. `users/username/{name}` 解析真实 username（cfg.user_id 形态如 "roc"）；
3. OAuth 会话用户（enqueued_by=26-hex mm_user_id）直发路径；
4. 频道 id 有效性（坏 id → post 失败 fail-open warn，无广播）。

**一键自检（上四项的命令化）**：hub.env 配好后 `python3 hub/cli.py check-notify`（在 hub 进程 env 下跑，容器内 `python3 cli.py check-notify` 同）——①bot token 有效性 ②username 解析 ③DM 测试私信 三步 ✓/✗，任一 ✗ 非零退出即停；`--to <26-hex>` 验直发路径（联调项 3）、`--channel <chan-id>` 附加频道连通（联调项 4）。token 绝不回显。

**陈旧检测语义**：差异（remote ≠ `HUB_IMAGE_TAG_DEFAULT`）→ bot 私信 + stderr 告警一次/组合（去抖，重启重告）；repo 404 → stderr 高声（配置错，不修则检测整体失效）；网络错/5xx → 静默下轮再查。**hub 绝不自动拉取或重建镜像**——升级是人工决定（重建流程见 §15 部署顺序第 2 步）。

**读面守卫（顺带，F2 收紧）**：auth 已配置态下 `GET /queue`、`GET /runs/{id}`、`DELETE /queue/{item_id}` 须有效会话（401 否则）；未配置回退态三端点无守卫（2a 零变化）。CLI（`cli.py`）走函数直调不经 HTTP——守卫不影响 CLI 双皮。

## 17. 面板与语义事件（add-hub-slice-4b 切片 4b，2026-10）

手写 PRD 入口（面板 UI）+ 语义事件通知（critic / PR / verify）+ 无 run 放弃告警（spec: `openspec/changes/add-hub-slice-4b/`，归档后入 hub-panel / pa-event-outlet delta）。**前置依赖 4a**：面板登录走 OAuth 会话（MM 四项 + SESSION_SECRET），事件/放弃通知走 bot token——两条通道都不配时 4b 全静默零行为（503 / 零转发）。

**面板使用入口**：`/panel`（提交页）、`/panel/queue`（队列）、`/panel/runs`（run 列表）、`/panel/runs/{id}`（run 详情：journal 尾部 30 行 + 报告原文）。提交闭环：登录 → 选 project（清单 = data_root 下有 `profiles/` 目录 + `sources.yaml` 的项目，与派发 fail fast 同判据）→ 粘贴 PRD markdown → **evidence 必填**（链接或出处说明，strip 后空则 422 拒绝——无据 PRD 在闸前被拒）→ 提交即落盘 `data_root/<project>/prd/<stamp>_hand-<8hex>.md` + 入队（enqueued_by = 会话 mm_user_id，initiator = 显示名）。内网外不可达（与 hub 同暴露面）；auth 未配置整群 503。

**events.jsonl 契约一页**（pa → hub 唯一语义事件接缝；hub 绝不解析 pa journal 内部格式）：
- 路径：`<state>/runs/<project>/<stamp>_<PRD stem>.events.jsonl`（与 journal 平铺伴生同拼法；**stem 原样含下划线**——hub 侧从 queue_item.prd_path 拼寻址，不用 run 目录名，因 hub `_slugify` 把下划线转横线会使目录名与文件名错位）；
- 行：`{"v":1,"event":"critic_verdict"|"pr_opened"|"verify_verdict","seq":<run 内单调 int>,"stamp":...,"project":...,...事实字段}`；critic 带 verdict+summary，pr_opened 带 pr_url+repo，verify 带 verdict+round+summary；
- 写纪律：O_APPEND + fsync 对齐 journal.py；写失败 fail-open（stderr warn 不阻断流水线）；
- hub 读面：reconcile_running 对 running run 每轮读 → `(run_id, seq)` 进程内存去重 → 新行经 4a bot 通道转发（critic drop / verify 红进频道 + 私信，其余仅私信）；`v≠1` **整文件跳过**（不转发半懂的形态）、未知 event 行跳过、坏 JSON 行跳过；run 终态后不读；孤儿 run（item 查无）跳过。

**PA_REPORT_DIR（report 双写）**：hub 派发时给容器注入 `PA_REPORT_DIR=<容器内 state>/reports`（docker `-e` 直传，与凭据 env-file 分离）——pa report 段在该目录**追加**双写一份 `项目推进报告_<stamp>.md`（原落点不变，缺省零变化），落容器内 state 卷即 host `<data_root>/<project>/state/reports/` 可见，面板 run 详情页按 run stamp 对齐读。不注入（如本地 vault 直接跑 run_daily）= 行为与 4b 前完全一致。

**容器形态：执行链段范围与模型路由（fix-hub-form-parity，2026-10，spec: execution-hub「执行链段范围」）**：
- **段范围**：派发 cmd 已补 `--to-stage report`（此前止于 dispatch 默认——report 段从不跑、`state/reports/` 恒空、面板恒「无报告」）。现在执行链 inject→critic→dispatch→report 跑满，report 双写产物真实落卷。**容器内 report 段 SMTP 必走 fail-open 告警**（SMTP env 不透传容器，只透 `PA_*`）——run 尾恒一条 SMTP 告警日志属预期，勿为此配 SMTP env（hub 形态通知面是 MM bot，见 §16）。
- **模型路由 env-only**：容器形态**仅 env 点**可配（`PA_PERSONA_MODEL_<AGENT>` / `PA_DEV_MODEL`）——executor 镜像不含 `config/`，文件主点 `config/model-routing.json` 是 vault 本机形态专属（有意选择：路由随 hub.env 差异化、改后即刻生效，烤进镜像反而钉死在发布时点）。hub.env 示例：`PA_PERSONA_MODEL_PA_PROGRESS=haiku`、`PA_DEV_MODEL=sonnet`；改后 `docker compose up -d` 重建 hub 进程即生效（`PA_*` 经派发透传进 run 容器），**无需重发镜像**。容器内优先级 env > roc（无文件点可降级）。

**无 run 放弃告警**：create_run 前的五类放弃（project 名非法 / PRD 缺失 / 启动必读面缺失 / 凭据缺失 / meta 署名失败）收敛经 `_abandon` → bot 私信管理员（含队列项 id + project + 原因摘要）；通道关闭 = stderr 降级留痕（fail_item 不受影响）。与 run 终态通知（4a notify_run）互补：无 run 的放弃也有可见性。

## 18. dev 交接 Handoff（add-dev-handoff-artifact，2026-10）

dev 亲笔交接工件：断点后新 dev 会话拿到上一 iteration 的结构化自述（写侧 → content-addressed 归档 → 下轮注入）。**默认 off**（flag `PA_LOOP_DEV_HANDOFF`，env > profile.loop > False；依赖 `journal_shadow`——preflight 静态阻断 dev_handoff on + journal_shadow off 组合）。off = dev-agent prompt/JSON/文件面 byte-identical baseline，关 flag 即回退。

**链路与产物**（flag on 时）：
- 写侧：dev-agent 每 turn 解析 assistant 回复的 ```handoff``` 围栏 JSON → `state["handoff_latest"]` + 原子快照 `wt/state/runs/handoff-latest.json`（tmp+rename；`--expect-handoff` 开契约）；
- 归档：dispatch 在 `_run_dev_agent` 后、blocked_by_gate 短路前，落 content-addressed artifact（kind=handoff）+ journal 事件 `iteration_handoff`（payload 带 digest/size/path/round/adopted_from_snapshot）；
- 收养（adopt）：dev_out 缺失（kill/超时/崩，`script_json=None`）但快照存在 → 从快照归档，事件标 `adopted_from_snapshot: true`；两者皆无 → 无事件（正常缺失，非错误）；
- 注入：round≥2 经 `--handoff-artifact`+`--handoff-digest` 成对透传（digest 不匹配 → dev-agent exit 10 `handoff_integrity`，fail-closed 不静默降级）；recovery summary 的 decisions/next_action 优先取自述（`build_recovery_context` 入参 `handoff_content`，调用方读文件）。

**运维要点**：
- 观测层 fail-open：store/emit 失败只 log `⚠ iteration_handoff store 失败`，不拖垮 dispatch；handoff 永不进 verdict/gate/retry 决策（RetryPolicy 输入不含它）；
- 排查：journal grep `iteration_handoff` 看每轮归档（含 adopted 标记）；artifact 在 `state/artifacts/<run>/handoff/`；dev 侧中途快照在 worktree `state/runs/handoff-latest.json`（roc cron 形态盘上幸存可收养；hub 容器形态 worktree 是容器可写层，容器死后 in-flight 快照随容器丢 = 诚实缺失回退，已归档的 journal/artifact 在 state 卷幸存——见 design.md Open Question 2 结案）；
- 回退：`PA_LOOP_DEV_HANDOFF=false` 一键关回 baseline（env 压过 profile）。
