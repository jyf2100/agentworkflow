# Operator Recovery Runbook — Durable Loop Runtime

> OpenSpec `add-durable-loop-runtime` task 8.7。覆盖 5 类运行时故障的运维恢复流程：
> state corruption / missing session / sandbox failure / telemetry outage /
> externally blocked reconciliation。每类给出「症状 → 诊断 → 恢复动作 → 验证」。

## 前置：故障定位通用入口

所有恢复都从 journal 与 artifact store 这两个不可变真源出发：

```bash
# 1. 读 iteration journal，reduce 出最后合法状态
python3 -c "import sys;sys.path.insert(0,'Projects/项目推进流水线/scripts'); \
import journal as J, loop_state as L; \
evs=J.read_events('state/journal/<run_id>.jsonl'); \
print(L.reduce(evs, L.initial_state('<run_id>','<prd_id>','<iter_id>','')).status)"

# 2. 校验 journal 完整性（中部损坏 fail-closed）
python3 -c "import sys;sys.path.insert(0,'Projects/项目推进流水线/scripts'); \
import journal as J; print(J.validate_journal('state/journal/<run_id>.jsonl'))"
```

恢复铁律（design 决策#1/#3）：
- **绝不盲目重放副作用**（commit/push/PR）——先 reconcile 判定是否已发生（exactly-once）。
- **fail-closed 优先**：任何不确定（UNKNOWN / 损坏 / 缺 session）→ 阻断并升级运维，不自动猜测。
- **shadow→driven 渐进**：`journal_driven_dispatch` flag 关时 dispatch 走 legacy JSON，恢复可随时回退。

---

## 1. State Corruption（journal 中部损坏）

**症状**：`validate_journal` 报 `middle corruption`，或 reducer 抛 `JournalCorruptionError`，
或归约态 `STATE_CORRUPT`。

**诊断**：journal 是 append-only JSONL。尾部不完整可截断重放（自动恢复）；**中部损坏不可自动恢复**
（reducer 拒绝跨损坏行归约，避免基于错误状态决策）。

**恢复动作**：
1. 备份损坏 journal：`cp state/journal/<run_id>.jsonl state/journal/<run_id>.jsonl.corrupt-<date>`。
2. 从 artifact store 找最近 recovery snapshot（`PreCompact` 事件 payload.snapshot 指针）。
3. 用 snapshot + 截断到损坏点之前的 events 重建：
   ```bash
   # 截断损坏行（保留损坏点之前），从 snapshot 恢复后让 reconcile 重决策
   python3 scripts/recover_from_snapshot.py --run <run_id> --snapshot <digest>
   ```
4. 若无 snapshot 且中部损坏 → 该 iteration 标 `STATE_CORRUPT`，**放弃自动恢复**，
   人工核对 PRD + 已发生副作用（git log / gh pr list），手动决定续做或废弃。

**验证**：`validate_journal` 通过；reduce 终态非 `STATE_CORRUPT`；reconcile 副作用无 UNKNOWN。

---

## 2. Missing Session（SDK session 缺失/损坏）

**症状**：`recover_iteration` 决策 `NEW_SESSION`，日志 `missing session metadata` 或
`session not resumable`。

**诊断**：SDK session ID 持久化失败 / session token 过期 / compaction 后上下文污染。
RetryPolicy 已自动选 `NEW_SESSION`（全新 session，丢弃污染上下文）——这是**预期恢复**，非故障。

**恢复动作**：
1. 确认 session store 真的缺该 iteration：`session_store.load(iter_id)` 返回 None。
2. 让 RetryPolicy 的 `NEW_SESSION` 决策执行（全新 SDK session + recovery context 注入）。
   recovery context 从**不可变 PRD + journal artifacts** 派生（`build_recovery_context`），
   不依赖丢失的 session。
3. 若重复 `NEW_SESSION` 仍失败 → 检查 `repeated_failure_rate`；重复相同 fingerprint + 停滞
   → 仍 `NEW_SESSION` 但消耗 SDK retry 预算；预算耗尽 → `STOP`（人工介入）。

**验证**：新 session 跑通且产 fresh green TestEvidence；journal 因果链（span link relation=`new_session`）
指回 parent run，不丢历史。

---

## 3. Sandbox Failure（容器启动/策略失败）

**症状**：`open_sandbox` 返 `SandboxBlocked`（tier=CONTAINER，未降级 local）。
reason 含 `unavailable`（运行时缺失）或 `policy_violation`（网络/资源违例）。

**诊断**：design 决策#6/6.5——sandbox 失败 **fail-closed，不自动降级 local tier**
（local 是 lower assurance，静默降级 = 安全语义偷换）。

**恢复动作**：
1. **不**降级。先修 blocker：
   - `unavailable` → 检查 docker/podman 可用（`docker info`）、镜像存在、cgroup 权限。
   - `policy_violation`（network）→ 审 `requested_hosts`，确认是否真需该 host；
     需要则加进 `sources.yaml` 的 `network_allowlist`，不需要则改代码去掉该外联。
   - `policy_violation`（resource）→ 调高 `cpu_limit`/`memory_limit`/`process_limit`。
2. blocker 修复后重跑 `open_sandbox`（同一 spec）。
3. 若短期无法修容器 → 显式切 `container_sandbox` flag 关，**记录降级决定**到运维日志
   （显式人工降级 ≠ 静默自动降级），接受 lower assurance 跑该 iteration。

**验证**：`open_sandbox` 返 `SandboxHandle`（非 Blocked）；fixture 在修复后的 tier 跑通；
网络违例 host 要么在 allowlist、要么被 block。

---

## 4. Telemetry Outage（OTLP backend 不可用）

**症状**：`TelemetrySink.flush` 返 `ExportResult(degraded=True)`；journal 出现
`observability degradation` event；report 的 `observability_degraded=True`。

**诊断**：design L82——telemetry 是观测层，**outage 不拖垮 dispatch**。本地执行继续，
仅记一次可见 degradation event。**这是降级运行，不是故障**。

**恢复动作**：
1. dispatch 无需中断（已自动继续）。
2. 诊断 backend：检查 OTLP collector endpoint 可达（`curl <endpoint>`）、
   collector 进程、Jaeger/后端存储容量。
3. backend 恢复后，sink 保留的未导出 spans 会在下次 flush 重试（失败不清空）。
4. 若长时间 outage → degradation event 会持续累积；监控 `observability_degraded` 指标。

**验证**：backend 恢复后 `flush` 返 `degraded=False`；新 spans 成功导出；
trace ID 在 Jaeger 可查；**绝无敏感数据泄漏**（属性经字段 allowlist + secret scrub）。

---

## 5. Externally Blocked Reconciliation（外部真源 UNKNOWN）

**症状**：`reconcile_side_effects` 报告含 `unknown` 项；RetryPolicy 决策 `BLOCK`
（reason: `external source of truth unknown; reconcile before retry`）；归约态 `EXTERNAL_BLOCKED`。

**诊断**：design risk#90——KeyResolver（git/gh）查不到副作用状态（gh 无 token / git 远端不通 /
PR API 失败）。fail-safe：**绝不盲目补做 commit/push/PR**（可能重复）。

**恢复动作**：
1. 查每个 unknown 副作用的真实状态：
   - commit → `git cat-file -e <sha>`
   - push → `git ls-remote origin <branch>`（远端真源，非本地 ref）
   - pr → `gh pr list --head <branch> --state all`
2. 状态明确后（confirmed=已发生跳过 / absent=未发生执行），重新 `reconcile_side_effects`
   → `external_known=True` → RetryPolicy 解除 BLOCK。
3. 若 gh token 缺失 → 配 `GH_TOKEN` env（host-side，不进 sandbox）后重 reconcile。
4. 若副作用状态永久不可查（仓库删/权限失）→ 人工决定，标 `ABORTED` 或 `FAILED`。

**验证**：`ReconciliationReport.unknown` 为空；`external_known=True`；retry 执行后副作用
exactly-once（无重复 commit/PR）。

---

## 升级与回退

| 场景 | 自动恢复 | 需运维介入 |
|---|---|---|
| 尾部不完整 journal | ✅ 截断重放 | — |
| Missing session | ✅ NEW_SESSION | 预算耗尽 → STOP |
| Telemetry outage | ✅ 降级继续 | 长时间 → 修 backend |
| 中部 journal 损坏 | ❌ fail-closed | ✅ snapshot 恢复或人工 |
| Sandbox failure | ❌ 不降级 | ✅ 修 blocker |
| External UNKNOWN | ❌ BLOCK | ✅ 查真源或配 token |

**全局回退**：任何不可控故障 → 关 `journal_driven_dispatch` flag，dispatch 回 legacy JSON
路径（保留一个 release cycle），隔离问题 iteration 后再逐项恢复。
