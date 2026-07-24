# complete-durable-loop-runtime-integration Review R4

评审日期：2026-07-24

评审基线：`main@714c854`

评审范围：`d8970ff...714c854`

评审结论：**Request Changes。基础质量通过，但 P0-1、P0-2 和 P1-2 尚未形成真实、可追溯的验收闭环。**

## 1. Reviewed Commits

| Commit | Purpose |
|---|---|
| `337af75` | R3 P0-1：7.2 逐场景通过谓词 |
| `c2dd5cf` | R3 P0-2：子证据 fail-closed 与完整性门 |
| `2b6507c` | R3 P1-2：evidence index |
| `fe77127` | R3 P0-1：7.6 outcome 与 7.2 gate exact-match 补强 |
| `714c854` | 忽略运行态 index，并声明后续改走 CI artifact store |

## 2. Verification

在 `Projects/项目推进流水线` 执行统一质量命令：

```bash
PATH=/tmp/pa-review-venv312b/bin:$PATH \
PYTHON=/tmp/pa-review-venv312b/bin/python \
bash scripts/quality.sh
```

结果：

- compileall：通过；
- pytest：`722 passed in 4.88s`；
- Ruff：`All checks passed`；
- quality.sh：退出码 0；
- `git diff --check d8970ff...714c854`：通过。

本轮未运行需要真实模型调用的 7.2、7.6 和 `--drill all`。仓库也没有提供绑定 `714c854` 的最终运行证据。

## 3. Blocking Findings

### P0-1：逐场景 SDK callback 证明仍由通用事件类型推导

`runtime_evidence.py:473-475` 的 base query 只要求执行 `echo READY`，用于触发一次 Bash lifecycle。随后 `runtime_evidence.py:558-575` 按事件类型填充场景矩阵：

- 一个 `PostToolUse` 同时将 `test_red`、`stale_test`、`test_green` 标记为真实证明；
- 一个 `Stop` 同时将 `no_test`、`semantic_revise` 标记为真实证明；
- subagent 使用另一条通用 query 尝试触发 `SubagentStart`。

这些场景没有分别执行对应输入，也没有把 callback 与该场景的 test state、staleness、semantic verdict 关联。因此，新增的 predicate 虽然逐项检查六个布尔值，但六个布尔值本身仍可由少量通用事件批量产生。

**影响**：7.2 与 7.6 仍可能将未真实执行的场景标记为 proven，P0-1 的假绿根因没有消除。

**要求**：每个 required scenario 必须有独立运行标识、场景输入、callback journal 和 gate outcome。通过条件应校验场景关联证据，而不是只判断某种 lifecycle event 是否曾在整个 query 集合中出现。

### P0-2：归档的 cutover manifest 不包含子证据引用

`run_full_cutover_suite()` 已增加有效的内存完整性门：七个 outcome 名称齐全、digest 非空、artifact 可读且 digest 匹配才允许 `overall_passed=True`。这是本轮的实质改进。

但是 `cutover.py:1014-1016` 最终归档的内容仍只有：

```python
artifact_store.store(artifact_root, manifest.summary, ...)
```

`manifest.summary` 只包含各 outcome 的 PASS/FAIL 文本，不包含：

- `sub_evidence_refs`；
- 每个 outcome 的 `evidence_digests`；
- `evidence_integrity`；
- 可用于结构化读回的 manifest schema。

因此，`archive_digest` 指向的是状态摘要，不是声称引用七份子证据的 manifest。另一个环境即使读到该 artifact，也无法沿它遍历和验证子证据链。

**要求**：归档完整的结构化 manifest，至少包含 schema version、outcomes、evidence digests、完整性状态和必要运行元数据；归档后立即读回并验证其引用链，再返回 passing `archive_digest`。

## 4. Major Findings

### P1-2：跨机器 evidence publication 尚未实现

`runtime_evidence.py:1095-1186` 可以生成 index，但 index 内容仍包含本机 artifact root 和路径。`runtime_evidence.py:1251-1260` 在写入失败时仅打印警告，不影响成功退出。

随后 `714c854` 删除已提交的 index，并在 `Projects/项目推进流水线/.gitignore` 中忽略该文件，注释称“改走 CI artifact store”。但本次 diff 没有增加：

- CI workflow；
- artifact uploader；
- object-store publisher；
- 稳定远程地址；
- clean-room 下载和 digest 校验命令。

默认 artifact root 又位于临时 workdir。一次成功运行后，唯一 index 仍可能只是未跟踪的本机文件，其他机器无法读取原始 evidence。

**要求**：从脱敏 bundle 入仓、不可变 CI artifact 或受控 evidence store 中选择并落实一种方案。index 写入、发布、读取或校验失败必须导致验收命令非零退出。

### P1-3：没有最终提交上的真实验收证据

现有旧 index 对应 `c698a9c`，且已从当前树删除。`fe77127` 改变了 7.2/7.6 通过语义，`714c854` 又改变 evidence index 的交付方式。

当前仓库没有提供 `714c854` 上以下命令的退出码和 evidence refs：

```bash
runtime_evidence.py --drill 7.2
runtime_evidence.py --drill 7.6
runtime_evidence.py --drill all
```

旧 commit 的 manifest 不能验收当前代码。

### P1-4：7.2 与 7.6 仍未共享一个判定实现

本轮将 expected gates 和 required scenarios 收敛为共享常量，降低了清单漂移风险。但：

- 7.2 在 `runtime_evidence.py:1060-1083` 内联计算 `cb_proven/sdk_cb_ok/gate_ok`；
- 7.6 在 `cutover.py:828-845` 分别调用 gate helper并计算 callback 集合。

两个入口仍可能在字段缺失、兼容字段或错误信息处理上发生语义漂移。R3 response 中的共享纯函数建议尚未落实。

**要求**：将场景级判定和诊断结果建模为单一纯函数/结果对象，由 7.2 predicate 与 7.6 outcome 共同消费。

## 5. Scope Finding

根 `.gitignore` 新增 `.claude/projects`、todos、history、shell snapshots 以及全局 `*.bak`、`*.tmp`、`*.log`，与本次 R3 验收修复没有直接关系。

这些规则可能影响仓库内未来需要跟踪的 fixture、日志样例或配置。建议拆为独立提交并单独说明影响范围，不应混入 P0/P1 验收闭环。

## 6. Confirmed Improvements

以下改动可以确认有效：

- 7.2 adapter gate 已从 truthy 检查改为 expected value 精确匹配；
- 7.6 不再只依赖 `real_query_proven`，还要求 required scenario 集合；
- 子证据归档异常不再静默返回空引用；
- cutover runner 会将 drill 执行、提取或归档异常记为 red；
- 内存完整性门会检查七个 outcome、非空 digest、可读性和 digest 匹配；
- 新增的回归测试进入统一质量套件，质量基线由 712 增长到 722 tests 且 Ruff 保持绿色。

这些改进缩小了假绿空间，但没有解除第 3、4 节的阻断项。

## 7. Acceptance Status

| Item | Decision | Reason |
|---|---|---|
| P0-1 | Fail | 场景布尔值仍由通用事件类型批量推导 |
| P0-2 | Fail | 实际归档 manifest 不含子 evidence refs |
| P1-2 | Fail | 无跨机器 publisher，index 失败仍 fail-open |
| Final quality | Pass | 722 tests + Ruff 全绿 |
| Final real drills | Missing | 没有绑定 `714c854` 的 7.2/7.6/all evidence |
| P1-1/P1-3/P1-4 from R3 | Deferred/Open | 本轮没有处理，不得自动关闭 |

## 8. Required Next Evidence

下一轮申请关闭前应提供：

1. 每个 SDK scenario 独立执行并携带 correlation ID 的真实 callback/gate evidence；
2. 归档后可读回的结构化 cutover manifest，包含七个子 evidence refs；
3. index/evidence publication 的 fail-closed 行为及反例测试；
4. 从干净环境下载或读取原始 artifacts 并重算 digest 的记录；
5. 最终 commit 上 `quality.sh`、7.2、7.6、all 的命令、退出码和 refs；
6. 证明 passing evidence 绑定 clean final commit，而不是旧快照。

## 9. Final Verdict

**Request Changes。R3 尚未关闭。**

当前实现解决了 predicate 表层强度和内存证据完整性，但仍未证明各 SDK 场景被真实执行，实际归档 manifest 也没有携带子证据链。P1-2 在删除本机 index 后没有落地替代 publisher，反而使跨机器复核入口消失。

在 P0-1 的场景真实性、P0-2 的结构化归档以及 P1-2 的持久 publication 完成前，不得声明 durable loop runtime 已最终验收通过。
