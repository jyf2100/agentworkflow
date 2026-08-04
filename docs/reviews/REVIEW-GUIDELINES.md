# Scoped Code Review Guidelines

本指南用于所有代码评审、PR 复审、响应文档审核和修复批次验收。目标是严格发现真实缺陷，同时避免
移动验收终点。

## 1. 需求优先级

1. 用户本轮明确决定。
2. 已接受的 response / revise / ADR 决策。
3. OpenSpec tasks、capability specs 和验收标准。
4. 当前工单或上一轮明确 findings。
5. 工程增强建议。

第 5 类只能进入 `Follow-up`，不能覆盖前四类。

## 2. 冻结验收矩阵

评审实现前必须先写出：

| 分类 | 含义 | 评审动作 |
|---|---|---|
| Must pass | 本轮明确工单和已接受契约 | 失败可阻断 |
| Deferred | 已决定延后的能力 | 只检查诚实 red/open、无假绿 |
| Out of scope | 明确不属于本批 | 不检查、不阻断 |
| Follow-up | 可选加固 | 可建议，不得 Request Changes |

矩阵在当前评审轮次内保持不变。实现困难、发现更强方案或专家提出新模型，都不能自动扩大 `Must pass`。

## 3. 阻断准入条件

每条 blocker 必须完整回答：

```text
Contract: 对应哪条用户决定、task、spec、ADR 或已接受 response？
Code behavior: 当前代码具体做了什么？
Counterexample/evidence: 怎样稳定复现或确定性证明？
Current impact: 为什么会影响本轮验收，而非理论风险？
Minimal fix boundary: 最小行为修复是什么，不要求额外架构？
Severity: P0 / P1 / P2
```

没有契约引用、只存在理论可能、或修复要求明显超出当前任务时，降为 `Follow-up`。

## 4. Deferred 项

Deferred 不等于通过，也不等于本批必须实现。只检查：

- 状态是否明确标为 open/red；
- CLI、manifest、archive、PR 描述是否一致；
- 是否存在 helper、fixture、环境变量或兼容字段把它补成绿色；
- 是否会生成最终验收通过声明。

不得因为 deferred 项仍未实现而反复新增实现工单。

## 5. 增量复审

增量复审按顺序执行：

1. 逐项复验上一轮 findings。
2. 检查修复引入的直接回归。
3. 运行相关测试和统一质量命令。
4. 标记每项为 `closed / open / deferred / superseded`。
5. 新发现只有直接违反冻结契约时才能成为 blocker。

若新结论纠正了旧评审，必须明确写“本评审取代哪一条旧结论”，避免 PR 同时存在多套标准。

## 6. 专家委派

专家任务必须包含冻结矩阵和以下约束：

- 不得新增验收要求；
- 区分 blocker 与 follow-up；
- 每条 finding 必须引用矩阵中的契约；
- 只提交候选 findings，不直接外发。

主评审负责最终裁决。专家数量或措辞强度不构成严重级别依据。

## 7. 严重级别

- `P0`：当前契约内的直接假绿、破坏性数据/历史修改、关键安全边界绕过。
- `P1`：明确契约违反或实质回归，阻断当前验收。
- `P2`：范围、文档或维护问题，不阻断核心验收。
- `Follow-up`：更强但未约定的签名、read-back、事务、并发协议或防御性设计。

不要因为反例复杂、代码较长或修复成本高而提高严重级别。

## 8. 验证报告

测试结果必须分别报告：

```text
total:
passed:
failed:
CI status:
local-only environment failures:
focused counterexamples:
```

不能把测试总数写成 passed，也不能用本地依赖失败解释无关的产品语义。

## 9. 评审收尾

最终结论只回答：

1. 本轮工单是否关闭；
2. 是否存在直接违反既有契约的回归；
3. CI 和相关反例是否通过。

额外加固集中列入 `Follow-up`，不得混入 Request Changes。
