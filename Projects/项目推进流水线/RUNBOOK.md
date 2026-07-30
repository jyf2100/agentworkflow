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
