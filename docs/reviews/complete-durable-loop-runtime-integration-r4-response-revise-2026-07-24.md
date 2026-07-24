# complete-durable-loop-runtime-integration R4 Response (Revise)

日期：2026-07-24

关联文档：
- 初版响应：`docs/reviews/complete-durable-loop-runtime-integration-r4-response-2026-07-24.md`
- 评审反馈：`docs/reviews/complete-durable-loop-runtime-integration-r4-response-review-2026-07-24.md`（结论 Revise）

当前基线：`main@600e6d2`

响应结论：**依据 r4-response-review 的 Revise 结论修订。核心修订 P1-3 的 commit 绑定模型为 `subject_commit + evidence_commit + manifest_digest`（消除循环依赖），并按评审 §4-6 加强 P0-1（runner-owned correlation）、P1-2（allowlist bundle + fail-closed）、P0-2（manifest 归档读回）。本版仅提交设计复审，不直接开始实现。P1-1 继续保持 open，不声明 durable loop runtime 已最终验收通过。**

## 1. 评审采纳

接受 r4-response-review 的全部裁决：

| 项 | 评审裁决 | 本版处理 |
|---|---|---|
| P0-1 plan | Conditional Accept | 加强为 runner-owned correlation（§3） |
| P1-4 plan | Accept | 保持，共享纯函数（§6） |
| P0-2 plan | Conditional Accept | 加强为归档后读回 7 步（§5） |
| P1-2 plan | Conditional Accept | 加强为 allowlist + secret scan + fail-closed（§4） |
| P1-3 plan | **Revise** | 改为 subject/evidence commit 分离（§2） |
| P1-1 | Open/Deferred | 保持 open（§8） |

## 2. P1-3 核心修订：subject_commit + evidence_commit + manifest_digest

**撤回**初版 §5/§6 的 `docs/evidence/<bound_commit>/` + `bound_commit == 最终 clean commit` 模型——评审 §2 已证明其无法收敛（每次提交 evidence 都改变所谓 final commit，无限递归）。

**新模型**——把「被验收代码」与「证据载体」建模为两个不同提交：

### 2.1 subject_commit（被验收代码）
- 运行 drill 前工作区 clean；
- 包含 P0-1、P0-2、P1-2 publisher 代码与相关测试；
- evidence manifest 明确绑定该 SHA；
- 任何影响运行行为的后续代码变更都会使 evidence 失效。

### 2.2 evidence_commit（证据载体）
只允许增加或更新受控 evidence 文件：

```text
docs/evidence/<subject_commit>/manifest.json
docs/evidence/<subject_commit>/index.json
docs/evidence/<subject_commit>/artifacts/**
```

### 2.3 验收对象与校验
最终验收对象表示为：

```text
subject_commit + evidence_commit + manifest_digest
```

**而非** `bound_commit == evidence_commit`。验收必须验证：

1. evidence bundle 声明的 `subject_commit` 存在；
2. evidence_commit 基于该 subject commit，或明确记录两者的 ancestry；
3. `subject_commit..evidence_commit` 之间只有 allowlist 中的 evidence 路径发生变化；
4. evidence 内容中的 runner version 与 subject commit 的 runner 一致；
5. evidence_commit 不被误当成重新执行过业务代码的 subject commit。

## 3. P0-1 加强：runner-owned correlation

correlation ID 方向不变，补齐可信边界（评审 §4）：

- `scenario_id` 与 `correlation_id` **由 runner 生成**，通过以下任一方式绑定：
  - 每个 scenario 使用独立 hook journal；或
  - 每次 query 注册由 closure 捕获 `{scenario_id, correlation_id}` 的 hook callback。
- **不得依赖模型在 prompt、tool arguments 或文本输出中回传 correlation ID**——模型输出属于不可信输入，不能作为 callback 归属的唯一依据。

每场景 proven 条件至少包含：

1. 独立 query 已启动并结束；
2. runner 分配的 correlation ID 唯一；
3. callback journal 中存在同一 ID 的目标 lifecycle event；
4. test state、staleness 或 semantic verdict 与场景预期精确匹配；
5. callback 时间窗属于该 query；
6. 其他 query 的 event 不能补足本场景缺失证据。

## 4. P1-2 加强：allowlist bundle + fail-closed

「移除绝对路径、用户名、临时 token」不足以构成可信脱敏（评审 §5）。bundle publication 必须 fail-closed：

- 使用 **allowlist schema**，只发布明确允许的字段，拒绝未知字段（不原样透传）；
- 对环境变量、prompt、tool input/output、错误文本执行 **secret scan**；
- 禁止 GitHub、模型、SMTP、云服务凭据及其派生值；
- 脱敏、扫描、写入、读回或 digest 校验任一失败时返回非零；
- 提交前从干净工作区执行 bundle verifier。

bundle 中保存**最小充分证据**，不直接收录完整 prompt、模型输出或任意 stderr。最终 index 仍绑定初版 §5 所列七项（subject_commit、runner version 与时间、clean/dirty 语义、manifest digest、七子 evidence digest、持久位置、跨机器验证命令）。

## 5. P0-2 加强：结构化 manifest 归档读回（7 步）

manifest 至少包含（评审 §6）：`schema_version`、`subject_commit`、runner version 与执行时间、七个唯一 outcome、每个 outcome 的判定/诊断/evidence digests、全局 `sub_evidence_refs`、`evidence_integrity`、manifest 自身 digest 算法。

归档流程必须是：

1. 写入七份子 evidence；
2. 逐份读回并重算 digest；
3. 构造结构化 manifest；
4. 归档 manifest；
5. 读回 manifest；
6. 从读回内容重新遍历七份子证据；
7. 全部成功后才返回 passing `archive_digest`。

只验证写入前的内存对象仍不满足要求。

## 6. P1-4：共享纯函数（保持，Accept）

提取纯函数 `evaluate_scenario(scenario_id, callback_evidence, test_state, staleness, verdict) -> ScenarioJudgement(proven, diagnostic)`（纯 stdlib、无副作用）。7.2 CLI predicate 与 7.6 outcome extractor 共同消费，为两个消费点分别保留回归测试。此为 r3-response §2.2 与 r4-response-review 共同要求的收敛点，方向已 Accept。

## 7. 修订交付顺序（对齐 r4-response-review §8）

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

## 8. Deferred

P1-1（7.6 执行真实统一质量入口）保持 open/deferred，不因本批修复自动关闭。即使本批全部通过，仍不得声明 durable loop runtime 已最终验收完成。

## 9. 下一步

**本 revise 版提交设计复审。** 在复审认可 `subject_commit + evidence_commit` 模型与上述加强前，不直接开始实现（不写 runtime_evidence.py / cutover.py 的代码改动）。
