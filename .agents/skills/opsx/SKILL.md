---
name: opsx
description: (openspec + 本体论开发工作流，pa/pa-hub 仓库专用。) Use when the user says "opsx", "按流程做", "openspec 流程", or asks to implement a change/feature/bugfix in this repo. Runs the full 7-step pipeline: draft change package (proposal/design/spec delta/tasks) → adversarial review → cold review → fix → deterministic gates (openspec validate + quality.sh) → live verification → archive with evidence. Not for quick questions or pure research.
---

# OPSX：openspec + 本体论开发工作流

按 CLAUDE.md「开发工作流程」节的 7 步强制流程执行。`$ARGUMENTS` 是 change 名或需求描述。

## 步骤（严格按序）

1. **起草**：在 `openspec/changes/<change名>/` 创建 proposal.md（Why/What Changes/Non-Goals）+ design.md（本体论声明 + D 编号决策 + 状态机 + schema）+ spec delta（ADDED/MODIFIED + Scenario WHEN/THEN）+ tasks.md + `.openspec.yaml`（`schema: spec-driven`）。
2. **对抗评审**：派独立评审员（不参与起草），按 `docs/reviews/REVIEW-GUIDELINES.md` §3 格式出 findings（Contract / Code behavior / Counterexample / Impact / Minimal fix / Severity）。重点查本体错位、范畴混用、与既有 spec 冲突。
3. **冷评审**：派另一名未参与实施的评审员核对 finding 是否被**结构性**解决（纸面认领不算）+ 红线（在途文件禁改 / 值不入面 / fail-open / 三态精神）。
4. **实施修复**：按评审意见逐条修，scoped pytest 到绿。
5. **确定性门**：`openspec validate <id> --strict --no-interactive` + `bash hub/quality.sh`（或 pa `quality.sh`）。红了回炉一次。
6. **真机验证**：条件允许时入队真实 run 走全链；不允许时在 tasks.md 标注「模块就绪待真机」——不虚报完成。
7. **勾任务 + commit**：每项行内补证据，不空勾。在途文件禁改（切片法分离，`git add -u` 禁手）。

## 完成后

`openspec archive <change名>` 归档，spec delta 同步到主 specs（`openspec/specs/<capability>/spec.md`）。
