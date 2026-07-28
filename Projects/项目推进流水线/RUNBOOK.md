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
