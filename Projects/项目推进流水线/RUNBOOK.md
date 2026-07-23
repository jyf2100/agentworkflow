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
