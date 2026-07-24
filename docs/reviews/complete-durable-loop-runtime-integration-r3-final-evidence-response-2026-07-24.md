# complete-durable-loop-runtime-integration R3 Final Evidence Response

日期：2026-07-24

关联文件：

- `docs/reviews/complete-durable-loop-runtime-integration-review-r3-2026-07-24.md`
- `docs/reviews/complete-durable-loop-runtime-integration-r3-response-2026-07-24.md`

当前评审基线：`main@cfa87ca`

响应结论：**Request Changes 保持不变。代码修复声明已接近闭环，但最终提交上的验收证据尚未生成，P1-2 的跨机器可复核性也尚未证明。**

## 1. Reported Fix Chain

实施方报告以下本地、未推送提交：

| Commit | Reported change | Review state |
|---|---|---|
| `bd92770` | P0-1：7.2 逐场景通过谓词 | 代码修复待复验 |
| `c698a9c` | P0-2：子证据 fail-closed 与完整性门 | 代码修复待复验 |
| `7a69a36` | P1-2：evidence index | 证据可移植性待证明 |
| `b25ba1c` | P0-1：7.6 outcome 与 7.2 gate exact-match 补强 | 代码修复待最终运行验证 |

截至本回复提交前，当前本地及远程 `main` 均为 `cfa87ca`，上述四个 commit object 不存在于当前仓库。因此，本回复只裁定重新申请评审所需的证据条件，不认定这些修复已经进入当前评审基线。

## 2. Why the Minimum Gate Is Not Yet Met

### 2.1 Final code has not run the final acceptance suite

`b25ba1c` 修改了 7.2 与 7.6 的通过语义。实施方明确说明没有在该提交上重新运行真实 `--drill all`。

谓词变化会直接改变验收结果，因此单元测试绿色只能证明局部逻辑，不能证明最终 cutover suite 能在新规则下通过。必须至少在最终提交上重新运行：

```bash
runtime_evidence.py --drill 7.2
runtime_evidence.py --drill 7.6
runtime_evidence.py --drill all
```

三个命令均须记录退出码、manifest 和子证据引用。任何 required scenario blocked、gate 不匹配、子证据缺失或 digest 校验失败都必须使对应命令非零退出。

### 2.2 Existing index is bound to an earlier commit

实施方说明当前 `runtime-evidence-index.json` 仍是 `c698a9c` 的运行快照。该 index 不能验收 `b25ba1c`，原因包括：

- 被验收 commit 不同；
- 7.2/7.6 的 pass predicate 已改变；
- 旧 manifest 没有经过最终谓词执行；
- 旧 evidence 不能证明最终代码的 clean state。

最终 index 必须由 `b25ba1c` 或其后仅含证据更新的提交重新生成，并准确记录 code commit 与 evidence commit 的关系。

### 2.3 Local digest is not cross-machine evidence

如果 index 引用的 artifact 仍只存在于 gitignored `.project-auto/`，另一台机器拉取仓库后无法读取原始内容。重新执行 drill 生成新 artifact 只能证明可重复运行，不能独立复核原始 passing manifest。

P1-2 只有在以下任一条件满足后才能关闭：

1. 脱敏后的原始 manifest 与七份子 evidence 作为 immutable bundle 纳入版本控制；
2. 原始 artifacts 上传到长期可访问、不可变的 CI artifact 或 object store，index 包含稳定位置和 digest；
3. 仓库配置了可由干净环境访问的受控 evidence store，并提供实际读回验证。

index 自身的写入、发布、读取或 digest 校验失败必须导致验收非零退出。

## 3. Required Clean-Room Verification

重新申请关闭前，应从一个不复用原 `.project-auto/` 内容的干净工作区执行以下检查：

1. checkout 最终待验收 commit；
2. 运行 `quality.sh`，保存测试计数、Ruff 结果和退出码；
3. 运行 7.2、7.6 与 all drill；
4. 根据新 index 读取原始顶层 manifest；
5. 读取七份子 evidence，并逐一重算 digest；
6. 校验 manifest 中 outcome 名称完整、每项业务通过且 evidence refs 完整；
7. 校验 index 绑定最终 commit，且没有凭据、绝对临时路径或不可访问位置；
8. 保存验证命令及结果摘要。

这里的 clean-room verification 是读取并校验本次原始 evidence，而不是重新运行后用新 evidence 替代原 evidence。

## 4. Current Status

| Item | Current decision | Closure condition |
|---|---|---|
| P0-1 | Code fix reported; verification pending | 最终 commit 上 7.2/7.6/all 全绿，两个反例测试通过 |
| P0-2 | Code fix reported; verification pending | 最终 manifest 上归档失败与不完整引用均 fail-closed |
| P1-2 | Open | 原始 artifacts 可从干净环境按 index 读取并校验 |
| P1-1 | Deferred | 下一阶段执行真实统一质量入口 |
| P1-3 | Deferred | 下一阶段改用 immutable PRD input |
| P1-4 | Deferred | 下一阶段提供生产 rollout 证据或更正任务文字 |

## 5. Final Decision

**继续 Request Changes。不得将当前状态表述为“R3 最低前提已满足”。**

下一步不扩大到 P1-1、P1-3、P1-4。先在最终 P0 修复提交上完成真实端到端验收，生成与最终代码绑定的新 evidence index，并证明原始 artifacts 能被另一干净环境读取。

上述证据通过独立复核后，才可将 P0-1、P0-2 与 P1-2 标记为 closed，并推送该修复批次申请下一轮评审。
