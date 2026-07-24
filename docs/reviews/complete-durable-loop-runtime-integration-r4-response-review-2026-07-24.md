# complete-durable-loop-runtime-integration R4 Response Review

评审日期：2026-07-24

评审基线：`main@9e69407`

评审对象：`docs/reviews/complete-durable-loop-runtime-integration-r4-response-2026-07-24.md`

评审结论：**Revise。响应正确接受了 R4 的 Request Changes，修复范围与顺序基本合理；但 P1-3 的 evidence/commit 绑定模型存在不可实现的循环依赖，开始 evidence publication 前必须修订。**

## 1. Accepted Parts

R4 response 对现存问题的复述与 R4 评审一致：

- P0-1：通用 lifecycle event 被批量映射为多个场景的真实证明；
- P0-2：`archive_digest` 指向状态摘要，而不是包含七份子证据引用的结构化 manifest；
- P1-2：本机 index 被删除和忽略后，没有真正落地跨机器 publisher；
- P1-3：没有绑定当前最终代码的 7.2、7.6、all drill evidence；
- P1-4：7.2 与 7.6 没有共享同一个场景级判定实现。

以下方案方向可以接受：

1. 六个 required SDK scenario 独立执行，不再从全局 event type 反推场景；
2. runner 生成 correlation ID，并将 callback、test state、staleness 和 verdict 关联；
3. 7.2 与 7.6 共同消费一个纯场景判定函数；
4. 归档结构化 cutover manifest，并在返回 passing digest 前读回验证；
5. 选择脱敏 immutable evidence bundle 入仓，作为当前没有 CI artifact 基础设施时的务实方案；
6. P1-1 继续保持 deferred/open，不因本批修复自动关闭。

## 2. Blocking Design Finding

### P0：`bound_commit == final clean commit` 与 evidence bundle 入仓形成循环依赖

R4 response 第 61 行计划把 bundle 写入：

```text
docs/evidence/<bound_commit>/
```

第 73-75 行又要求 evidence 的 `bound_commit` 等于最终 clean commit。该模型无法稳定收敛：

1. 在代码提交 A 上运行 drill，evidence 绑定 A；
2. 提交 evidence bundle 后产生提交 B；
3. 最终 HEAD 变为 B，但 bundle 仍绑定 A；
4. 若重新绑定 B 并提交新 evidence，又会产生提交 C；
5. 每次提交 evidence 都会改变所谓 final commit，形成无限递归。

因此，不能要求入仓 evidence 同时绑定包含它自身的 commit。

## 3. Required Commit Model

必须把“被验收代码”和“证据载体”建模为两个不同提交：

### 3.1 Subject commit

`subject_commit` 是被验收的代码提交，要求：

- 运行 drill 前工作区 clean；
- 包含 P0-1、P0-2、P1-2 publisher 代码和相关测试；
- evidence manifest 明确绑定该 SHA；
- 任何影响运行行为的后续代码变更都会使 evidence 失效。

### 3.2 Evidence commit

`evidence_commit` 只允许增加或更新受控 evidence 文件，例如：

```text
docs/evidence/<subject_commit>/manifest.json
docs/evidence/<subject_commit>/index.json
docs/evidence/<subject_commit>/artifacts/**
```

验收必须验证：

- evidence bundle 声明的 `subject_commit` 存在；
- evidence commit 基于该 subject commit，或明确记录两者的 ancestry；
- `subject_commit..evidence_commit` 之间只有 allowlist 中的 evidence 路径发生变化；
- evidence 内容中的 runner version 与 subject commit 的 runner 一致；
- evidence commit 不被误当成重新执行过业务代码的 subject commit。

最终验收对象应表示为：

```text
subject_commit + evidence_commit + manifest_digest
```

而不是强制 `bound_commit == evidence_commit`。

## 4. Correlation Integrity Requirements

R4 response 提议 correlation ID 的方向正确，但还需要明确可信边界。

`scenario_id` 与 `correlation_id` 必须由 runner 生成，并通过以下任一方式绑定：

- 每个 scenario 使用独立 hook journal；或
- 每次 query 注册由 closure 捕获 `{scenario_id, correlation_id}` 的 hook callback。

不得依赖模型在 prompt、tool arguments 或文本输出中正确回传 correlation ID。模型输出属于不可信输入，不能作为 callback 归属的唯一依据。

每个场景的 proven 条件至少应包含：

1. 独立 query 已启动并结束；
2. runner 分配的 correlation ID 唯一；
3. callback journal 中存在同一 ID 的目标 lifecycle event；
4. test state、staleness 或 semantic verdict 与场景预期精确匹配；
5. callback 时间窗属于该 query；
6. 其他 query 的 event 不能补足本场景缺失证据。

## 5. Evidence Bundle Safety

“移除绝对路径、用户名和临时 token”不足以构成可信脱敏。bundle publication 必须 fail-closed：

- 使用 allowlist schema，只发布明确允许的字段；
- 拒绝未知字段，而不是原样透传；
- 对环境变量、prompt、tool input/output、错误文本执行 secret scan；
- 禁止 GitHub、模型、SMTP、云服务凭据及其派生值；
- 脱敏、扫描、写入、读回或 digest 校验任一失败时返回非零；
- 提交前从干净工作区执行 bundle verifier。

建议 bundle 中保存最小充分证据，不直接收录完整 prompt、模型输出或任意 stderr。

## 6. P0-2 Manifest Requirements

结构化 cutover manifest 至少应包含：

- `schema_version`；
- `subject_commit`；
- runner version 与执行时间；
- 七个唯一 outcome；
- 每个 outcome 的判定、诊断和 evidence digests；
- 全局 `sub_evidence_refs`；
- `evidence_integrity`；
- manifest 自身 digest 算法。

归档流程必须是：

1. 写入七份子 evidence；
2. 逐份读回并重算 digest；
3. 构造结构化 manifest；
4. 归档 manifest；
5. 读回 manifest；
6. 从读回内容重新遍历七份子证据；
7. 全部成功后才返回 passing `archive_digest`。

只验证写入前的内存对象仍不满足要求。

## 7. Status Recommendation

| Item | Decision | Note |
|---|---|---|
| P0-1 plan | Conditional Accept | 须使用 runner-owned correlation，逐场景独立执行 |
| P1-4 plan | Accept | 共享纯函数方向正确 |
| P0-2 plan | Conditional Accept | 必须验证归档后读回的结构化 manifest |
| P1-2 plan | Conditional Accept | bundle 入仓可行，但 publication 必须 allowlist + fail-closed |
| P1-3 plan | Revise | subject/evidence commit 必须分离，不能要求自包含 commit SHA |
| P1-1 | Open/Deferred | 仍阻断 durable runtime 最终归档 |

## 8. Revised Delivery Order

建议按以下顺序实施：

1. 定义 `ScenarioJudgement` 纯函数与 runner-owned correlation 模型；
2. 实现六个独立 SDK scenario 和两个消费点；
3. 实现结构化 manifest 及归档后 read-back；
4. 实现 allowlist 脱敏 bundle publisher 与 verifier；
5. 形成 clean `subject_commit`；
6. 在 subject commit 上运行 quality、7.2、7.6、all；
7. 生成并验证 `docs/evidence/<subject_commit>/`；
8. 创建只含 evidence 的 `evidence_commit`；
9. 从另一个干净工作区验证 subject/evidence ancestry、路径 allowlist 和全部 digest；
10. 提交复审。

## 9. Final Verdict

**Revise。R4 response 可以作为修复批次的基础，但 P1-3 的 commit 绑定模型必须先改为 `subject_commit + evidence_commit`。**

完成这一设计修订后，可以按 P0-1 + P1-4 → P0-2 → P1-2 → P1-3 的顺序推进。即使本批全部通过，R3/R4 中 deferred 的 P1-1 仍保持 open，因此仍不得声明 durable loop runtime 已最终验收完成。
