# 0001 — 控制面与目标面隔离：vault 不污染目标仓

> **2026-07-15 修订（见 ADR-0003）：** dev agent 产物已从 `.claude/agents/dev.md` 改为仓自带 SDK 脚本 `scripts/dev-agent.*`；本 ADR 的隔离**原则不变**（dev agent 仍归目标仓、随仓走），仅产物名变。

## 决定

流水线的**控制面**（vault 大脑 + vault 侧 agent：radar / prd / dispatch / report）与**目标面**（每个被开发的 workspace 项目仓，如 cc-web-control）**严格隔离**：

1. **流水线运行态状态绝不写进目标仓** —— PRD、run log、task 文件、`.project-auto/` 等只存在于控制面（vault 或中性 scratch 目录）。
2. **开发 agent 归属目标面** —— 每个白名单仓自带 `.claude/agents/dev.md` + 它自己的 CLAUDE.md（准入时由 roc 人工写进该仓）。dispatch 只递 PRD，执行 `cd <目标仓> && claude --agent dev -p "<PRD>"`。开发 agent 随仓走、仓自己决定怎么被开发；vault 不拥有它。
3. **PR diff 只含意图代码改动**，提交前 scrub 掉任何流水线元数据。
4. **vault 的 git 与目标仓的 git 永不交叉**（不做 submodule、不互相 `git add`）。

## 关键区分：什么是"污染"，什么不是

| 写进目标仓的东西 | 算污染吗 | 理由 |
|---|---|---|
| PRD / run log / `.project-auto/` 运行态 | **算** | 流水线副产物，污染目标仓历史与 review 噪音 |
| 目标仓自己的 `.claude/agents/dev.md` + CLAUDE.md | **不算** | 那是目标仓自己的开发结构，准入时人工写进、随仓走 |
| 开发 agent 实现的代码改动（PR） | **不算** | 这就是流水线存在的目的 |

## 背景

用户明确："**当前仓库不应该污染目标仓**。" 目标仓是独立项目（有自己的 git 历史、结构、协作者）；混入流水线运行态副产物会污染其历史、推高 PR review 噪音、且一旦 push 进 git 历史难以逆转。但"不污染"**不等于**"目标仓不能有自己的开发 agent / CLAUDE.md"——后者是目标仓自己的合法结构。

## 考虑过的替代

- **开发 agent 由 vault 拥有、伸手够目标仓**（dispatch 把 repo snapshot/PRD 经 headless 输入喂给一个 vault 侧 `pa-dev` persona）—— 拒绝：开发 agent 应在目标仓范围内、自带上下文，vault 不该拥有开发 agent。改由目标仓自带 dev agent。
- **目标仓内放流水线 task 文件** 方便传递 PRD —— 拒绝：污染运行态。PRD 改由 headless `-p` 输入传递。
