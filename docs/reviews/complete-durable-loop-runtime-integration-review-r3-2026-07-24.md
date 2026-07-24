# complete-durable-loop-runtime-integration Review R3

评审日期：2026-07-24
评审基线：`main@a9ffaed`（与 `origin/main` 一致）
评审范围：R2 修复提交 `2d2236a..a9ffaed`，以及归档后的 OpenSpec 验收状态
评审结论：**Request Changes。代码质量基线通过，但当前证据不足以支持最终验收和归档。**

## 1. Verification

实际执行统一质量命令：

```bash
PATH=/tmp/pa-review-venv312b/bin:$PATH \
PYTHON=/tmp/pa-review-venv312b/bin/python \
bash scripts/quality.sh
```

结果：

- pytest：`712 passed in 4.47s`；
- Ruff：`All checks passed`；
- quality.sh：退出码 0；
- 本地 `main` 与 `origin/main` 均指向 `a9ffaed0fc9eb326ed49d1f55ee7b0ef8ac00732`。

因此，本轮问题不在基础测试是否绿色，而在 cutover drill 的通过谓词、证据完整性和设计语义是否真实闭环。

## 2. Blocking Findings

### P0-1：7.2 SDK lifecycle canary 仍可在场景未覆盖时假绿

`runtime_evidence.py:548-576` 将八个场景映射到 lifecycle event，但多个不同业务场景共享同一种通用事件：

- `no_test`、`semantic_revise` 只要求出现 `Stop`；
- `test_red`、`stale_test`、`test_green` 只要求出现 `PostToolUse`；
- `compaction`、`hook_failure` 只要求出现 `PreCompact`。

这只能证明 SDK 触发过某类 callback，不能证明每个业务场景执行了对应的 gate 行为。代码也会诚实记录 `blocked_scenarios`，但 `runtime_evidence.py:1038-1040` 的 drill 通过条件仅为：

```python
bool(res.get("lifecycle_callback_proven"))
```

而 `lifecycle_callback_proven` 在任意一种 callback 出现时即为真。即使 `PreCompact`、subagent 或其他必需场景仍 blocked，7.2 和 `--drill all` 仍可能通过。

**要求**：通过谓词必须逐场景校验；每个要求真实触发的场景均应有独立的输入、callback、gate outcome 和证据引用。无法稳定触发的场景必须使验收 blocked，而不是整体通过。

### P0-2：passing manifest 允许缺失子证据

`cutover.py:879-897` 的 `_archive_sub_evidence()` 捕获所有异常并返回空 tuple。随后 `run_full_cutover_suite()` 只按 outcome 的业务布尔值计算 `overall_passed`，没有要求每个 outcome 都存在 evidence digest。

因此，artifact store 写入失败时仍可能出现：

- `overall_passed=True`；
- `archive_digest` 非空；
- 一个或多个 outcome 的 `evidence_digests=()`；
- `sub_evidence_refs` 不完整。

这与“passing manifest 必须引用全部子 evidence”的设计要求冲突。

**要求**：任一子证据归档失败必须 fail closed；完整性检查应要求七个 outcome 均至少有一个可解析、可读取且 digest 匹配的证据引用，之后才允许归档 passing manifest。

## 3. Major Findings

### P1-1：7.6 quality gate 没有执行仓库统一质量入口

`runtime_evidence.py:832-835` 将若干 drill 布尔值转换为：

```python
test_counts={"passed": _p, "failed": len(_qdims) - _p}
```

该计数不是 pytest/quality evidence 的执行结果。7.6 runner 没有调用 `scripts/quality.sh`、`quality_evidence.py` 或等价的真实仓库质量入口。

本评审独立执行 `quality.sh` 得到 712 tests 全绿，说明代码质量当前确实通过；但这不能证明 `--drill all` 自身具备设计要求的完整质量门。

**要求**：suite runner 应执行统一质量入口，归档命令、退出码、测试计数和输出 digest，并将失败级联为 suite red。

### P1-2：归档提交没有携带可独立复核的执行证据

仓库未跟踪本次 `--drill all` 的 manifest、日志或 digest index；`.runtime-evidence/` 被忽略。OpenSpec archive 只包含 proposal/design/spec/tasks 等规范文件。

当前报告中的 digest 若没有可访问的 artifact store 地址或受版本控制的索引，其他评审者重新拉取 `main` 后无法解析 digest、读取内容并验证其与 `a9ffaed` 的绑定关系。

**要求**：至少提交一个不含敏感信息的 evidence index，记录 commit、runner 版本、执行时间、manifest digest、全部子 digest、存储位置和验证命令；或者提供 CI 中长期可访问、不可变的 artifact 链接。

### P1-3：session-aware recovery 未始终使用 immutable PRD input

`run_daily.py` 在 dispatch 开始时已经捕获 `prd_content`，并以该内容生成 content-addressed identity。但 revise 后调用 `recover_iteration()` 时，`run_daily.py:1561-1564` 重新执行：

```python
prd_content=prd_abs.read_text(encoding="utf-8")
```

在非 journal-driven 兼容路径中，`_append_verify_feedback()` 可能已修改 PRD 文件。此时 recovery 接收的是追加反馈后的内容，而不是 new-run 时捕获的 immutable input。session-aware retry 与 journal-driven dispatch 是不同 flag，代码没有在此处强制二者同时启用。

**要求**：recovery 始终使用 dispatch 时捕获并校验过的 `prd_content`；反馈只通过 journal artifact/recovery context 输入。若兼容模式必须读取已修改 PRD，应明确限定其不能进入 durable/session-aware 设计路径。

### P1-4：7.5 更接近真实 gate 验证，但仍不是生产 rollout 证据

R2 已改为在 evidence workdir 写入并解析真实 project profile，并通过真实 flag resolver 与 legacy fallback 路径验证三重 gate。这修复了“完全手工构造 allowlist”的问题。

但该 profile 仍是临时证据目录中的 canary profile，没有证明生产配置中的一个 allowlisted project 实际由 `run_daily.py` 以 journal-driven 模式运行一个发布周期，也没有提交该周期可复核的 terminal/fallback evidence。

**要求**：若 task 7.5 的验收文本要求真实单项目灰度，应提供生产 profile、实际 dispatch run ID、journal terminal state 与回退记录。若只要求离线 gate drill，应修改任务文字，避免把模拟灰度表述为已完成生产 rollout。

## 4. Confirmed R2 Fixes

以下 R2 问题已得到实质修复，不应继续作为当前缺陷重复计算：

- branch protection drill 会识别保护是否由本次临时添加，只清理自身添加的保护，上一轮指出的不可逆副作用风险已收敛；
- Docker canary 不再通过 `-e GH_TOKEN` 一类参数复制宿主长期凭据；
- runtime evidence 会在所请求 drill 失败时返回非零退出码；
- `run_daily.py` 已接入 RetryPolicy、`recover_iteration()`、retry budget，并向 dev-agent 传递 iteration/resume/fork 参数；
- 控制面与 dev-agent 已使用同一 state directory / SessionStore 路径；
- push reconciliation 使用远端 `git ls-remote` 作为事实来源；
- crash drill 已使用真实子进程、kill point 与 restart/reconcile；
- full cutover suite 已改为调用 bundle 中的子 drill，并尝试归档子证据引用；
- project profile rollout evidence 已从纯手工 allowlist 前进到真实 profile resolver 路径。

## 5. Acceptance Recommendation

| Area | 判定 | 说明 |
|---|---|---|
| Repository quality | Pass | 712 tests + Ruff 全绿 |
| Production retry wiring | Conditional Pass | 主链路已接通，但 immutable PRD 输入仍需修正 |
| Credential / branch safety | Pass | R2 的两项安全阻断已修复 |
| Crash/restart durability | Pass | 已有真实子进程 crash/restart 证据路径 |
| SDK lifecycle matrix | Fail | 场景级真实性不足且通过谓词假绿 |
| Cutover quality gate | Fail | 未执行真实统一质量入口 |
| Evidence manifest integrity | Fail | 子证据缺失不阻断 passing manifest |
| Auditable archive | Fail | 拉取仓库后无法独立解析本次运行证据 |
| Real project rollout | Needs clarification | 当前证明 gate/profile drill，不足以证明生产灰度周期 |

## 6. Final Verdict

**Request Changes。`a9ffaed` 不满足 complete-durable-loop-runtime-integration 的最终归档验收要求。**

本轮实现相较 R2 有明显、可验证的进展，尤其是安全边界、生产 retry 接线、远端事实对账和真实 crash/restart。剩余问题主要集中在验收系统本身：7.2 的通过条件弱于任务语义，7.6 没有执行真实质量门，manifest 对证据缺失不 fail closed，且归档后无法从仓库独立复核证据链。

在 P0-1、P0-2 修复并补齐可复核 evidence index 之前，建议撤销或更正 OpenSpec 的“全部完成/已归档通过”声明。
