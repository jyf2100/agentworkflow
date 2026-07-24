# complete-durable-loop-runtime-integration R3 Review Response

日期：2026-07-24

关联评审：`docs/reviews/complete-durable-loop-runtime-integration-review-r3-2026-07-24.md`

当前评审基线：`main@a9ddc5f`

响应结论：**Request Changes 保持不变。允许继续一个严格限界的修复批次，但尚不接受“R3 三项全部关闭”的声明。**

## 1. Evidence Status

实施方报告以下本地提交：

| Item | Reported commit | Reported result |
|---|---|---|
| P0-1 | `bd92770` | 7.2 逐场景 SDK callback 与 adapter gate 覆盖 |
| P0-2 | `c698a9c` | passing manifest 子证据 fail-closed 与完整性门 |
| P1-2 | `7a69a36` | evidence index |

截至本回复提交前，当前工作区的本地及远程 `main` 均为 `a9ddc5f`，上述三个 commit object 不存在于当前仓库。因此，本回复依据实施方摘要与三份独立复核结论作出范围裁决，不把上述修复记录为已在当前基线复验通过。

三方复核形成的高置信共识是：P0-2 的 fail-closed 和证据完整性门设计有效；同时，silent-failure-hunter 发现 P0-1 尚未闭环到所有消费点。

## 2. Required P0-1 Closure

必须补一个小范围的 P0-1 闭环提交，至少满足以下两项。

### 2.1 7.2 adapter gate 必须精确匹配

7.2 predicate 不能只检查 `adapter_gate_outcome` 是否为非空 truthy value。八个场景必须逐项与 `_EXPECTED_LIFECYCLE_GATES` 的预期值精确匹配。

反例验收：八个场景均返回非空但错误的 `"WRONG"` 时，7.2 必须失败并给出具体不匹配场景。

### 2.2 7.6 必须复用同一场景级语义

`_sdk_canary_outcome` 不得继续仅使用 `lifecycle_callback_proven` 或 `real_query_proven` 判断通过。7.6 必须与 7.2 使用同一份场景级判定逻辑，避免两个验收入口发生语义漂移。

反例验收：存在任意真实 callback，但六个 required SDK callback 场景未全部证明时，7.6 SDK outcome 必须为 red，且不得生成 passing cutover manifest。

建议把共同判断收敛为一个纯函数，由 7.2 CLI predicate 和 7.6 outcome extractor 共同调用，并为两个消费点分别保留回归测试。

## 3. P1-2 Remains Open

“提交 evidence index”不等于“原始证据可跨机器独立复核”。若 index 只包含 digest 和本机 gitignored `.project-auto/` 路径，则其他评审者拉取仓库后无法读取 digest 对应内容。

重新运行 drill 会生成一套新证据，不能证明原归档 evidence 的内容与完整性。因此，P1-2 在满足以下任一方案前保持 open：

1. 提交经过脱敏的 immutable evidence bundle，并由 index 引用；
2. 将 evidence 上传至长期可访问、不可变的 CI artifact/object store，并在 index 中记录稳定地址、内容长度与 digest；
3. 使用仓库已有的受控 evidence store，确保新环境凭 index 和仓库配置即可读取原始 artifact。

此外，evidence index 是通过声明的必要组成部分。index 写入、发布或校验失败必须使验收命令非零退出，不能静默降级为成功。

最终 index 至少应绑定：

- 被验收 commit SHA；
- runner 版本与执行时间；
- clean/dirty 状态及其准确语义；
- 顶层 manifest digest；
- 七个子 evidence digest；
- 持久存储位置；
- 可在另一台机器执行的验证命令。

## 4. Deferred Items

以下项目不纳入当前小修复批次：

- P1-1：7.6 执行真实统一质量入口；
- P1-3：recovery 始终使用 dispatch 时捕获的 immutable PRD；
- P1-4：生产 project rollout 证据或任务文字更正；
- `_git_safe` 降级语义、私有函数依赖、文件换行、函数长度等打磨项。

这些项目继续保留在下一阶段，不得因当前 P0 修复而自动视为完成。

## 5. Resubmission Gate

重新申请 R3 关闭前必须提供：

1. 包含上述 P0-1 两个反例测试的修复提交；
2. P0-2 归档失败、缺 digest、不可读 digest、digest 不匹配和 outcome 不齐的 fail-closed 测试；
3. 可跨机器读取原始 artifact 的 evidence index 与存储证明；
4. `quality.sh` 完整结果；
5. `runtime_evidence.py --drill 7.2`、`--drill 7.6` 和 `--drill all` 的退出码与 evidence refs；
6. 当前修复 commit 上重新生成的 clean evidence，不得复用旧 commit 的 passing manifest。

## 6. Final Decision

**继续 Request Changes。**

允许实施方补充一个 P0-1 闭环提交，并同时完成真正可跨机器复核的 P1-2 evidence publication。P0-2 可在相关提交进入评审分支后按三方共识重点复验，不要求扩大实现范围。

在 P0-1 所有消费点语义一致、P1-2 原始证据可由另一环境读取之前，不得推送“R3 已全部关闭”或“durable loop runtime 已最终验收通过”的声明。
