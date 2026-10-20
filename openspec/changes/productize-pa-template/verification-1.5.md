# 验证笔记 1.5：术语现状标注 × 代码实际行为交叉验证

> 日期：2026-10-20 ｜ 对应 tasks 1.1/1.5。CONTEXT.md 现状标注的逐条 file:line 证据。路径前缀 `Projects/项目推进流水线/scripts/`。

## 1. 「编排器三边界仍产 drop」（drop 词条现状标注）

| 边界 | run_daily.py（legacy stage_critic） | graph_pa_aggregate.py（聚合层镜像） |
|---|---|---|
| ① prd 缺 path → drop | `run_daily.py:1107-1110`（`[critic] ⚠ prd 缺 path … → 降级 drop`） | `graph_pa_aggregate.py:141-144`（注释自标「镜像 stage_critic L934-937」） |
| ② 漏吐 verdict → drop | `run_daily.py:1111-1115`（`critic 漏吐 verdict → 降级 drop`） | `graph_pa_aggregate.py:155-163` |
| ③ revise 异常 → drop | `run_daily.py:1132-1135`（`revise 失败，按 drop`；**例外**：L1132 熔断终态 `raise` 穿透——pa-hardening P0-1，按 drop 吞掉=熔断失效） | 子图 `_revise_fn`（`graph_pa_aggregate.py:104-105` 头注自认全镜像） |

聚合层头注自证：`graph_pa_aggregate.py:14-15`「三边界（缺 path→drop / 漏吐 verdict→drop / revise 异常→drop）在聚合层 + 子图 `_revise_fn` 全镜像 stage_critic」。

## 2. 「task 4.2 未完成，目标态未达」

- `openspec/changes/langgraph-workflow-upgrade/tasks.md` §4：`- [ ] 4.2 3 处编排器产 drop 改 triaged（层 2）`——checkbox 未勾。
- 目标态（编排器从不产 drop、残缺→triaged）与 §1 现状矛盾 → CONTEXT.md 标注「目标态 vs 现状」二分是诚实的。

## 3. 「循环用尽路径 triaged 语义已生效」（triaged 词条现状标注）

- `run_daily.py:2792-2793`：verify 判红用满 / pa-verify 异常 / 无产出 → 对账降级 `interrupted_pr`（**不 drop**，半成品留 review）——机械层不替判死已在此路径生效。

## 4. 「大脑」词条范畴迁移 × 拓扑现状

- 多实例范畴：依据 `docs/superpowers/specs/2026-10-20-productize-pa-design.md` D3（singleton→multi-instance）。
- 删除「无 remote」过时声明依据：vault 现有 GitHub remote（`github.com/jyf2100/agentworkflow`，git 状态 `main...origin/main`）。
- 「pa-dev 永不改动它」→「dev 永不改动它」：`pa-dev` 已弃用（CONTEXT.md 开发 agent 词条 _Avoid_ 自证）。

## 5. 结论

CONTEXT.md 三处现状标注与代码逐条对上，无失实。遗留（非本任务范围）：目标态落地属 langgraph task 4.2；部署拓扑过时路径（`/Users/roc/workspace/`）由 task 1.3 的部署拓扑节处理。
