## Why

8/5 cc-web-control 重复 PR 投递（#55 #56 同 slug `20260805_agent-screenshot-verify` 两 open PR 堵满在途上限 9 天）的根因诊断收敛为两条**正交**缺陷，都落在 verify 回环：

### A（结构）PR 生命周期跟 stamp 不跟需求

dev-agent.py 分支名 `branch = f"{branch_prefix}/{s}-{slug}"`（L795-796），`s = stamp()` 含时分。verify revise 回环每轮重调 dev-agent 是**独立进程**，各自 `stamp()` → 不同时分 → **不同分支名**。配合 dev-agent 主流程 L966-976 `gh pr create` **无条件、不查已有 PR**，每轮新分支都开一个 PR。run_daily round≥2 `--base` = 上轮分支（增量重投；`cur_base=branch` 在 L2472）放大：round2 在 round1 产出上建**新**分支（stamp 变）→ 独立 head → GitHub 允许第二个 open PR。

**违反「PR 跟需求、需求（PRD/slug）不变 PR 不变」**：生命周期三层（worktree → branch → PR）全跟 stamp，不跟需求。verify revise 本是对同一需求的迭代审查，被实现成「每轮新分支新 PR」。

### B（职责）pa-verify 退化成测试红绿裁判

pa-verify.md persona 判据（L25-26, L45）= **测试绿 → pass / 红 → revise（一律）**；verify_prompt（run_daily L640-646）把 `test_rc` 机械转 test_state 喂入。对照 pa-verify 应做的**三方独立审核**（与项目自审核并行、不替代——见 design「ADR-0002 边界澄清」）：

| 三方审核维度 | 当前做 |
|---|---|
| 功能对齐（diff 真实现 PRD/Issue 需求） | ⚠️ 仅防跑题 sanity |
| 开发遗漏（PRD 验收点逐项覆盖、无漏） | ❌ |
| 质量达标（代码质量 / 安全 / 性能 / 兼容硬伤，profile 裁剪） | ❌ |
| 独立测试证实（test_rc 交叉证伪假绿） | ✅ 唯一在做，却被当成**唯一判据** |

更糟：independent_verify 的**基建失败**（`npm ci` 失败，`install_rc ≠ 0`，cc-web-control lock 不一致）导致 test 步骤根本未跑（`test_rc=null`）。**r2 R2-C-F2 修正**：verify_prompt 的 test_state 渲染（run_daily L640-646）本就三分支正确（绿/红/未跑都渲染），gap 在喂给 pa-verify 的 **verdict 规则指令段**（L667-670 只有 绿→pass / 红→revise、**未跑无 verdict 规则**）+ persona verdict 规则（pa-verify.md L25-26 同样只有两条）→ 未跑时 persona 无规则，默认落入 revise → 让 dev「修」一个 dev 修不了的环境问题 → 无意义回环。用 pa 三态哲学：`npm ci` 装不上 = **UNKNOWN**（不知代码能否跑），本该 fail-safe 升人工，却被当 NOT_FOUND（代码有缺陷让 dev 修），违反 fail-safe-dispatch 不变式。

### A 与 B 的关系

B 是 A 的**触发放大器**：8/5 这场回环本不该发生（npm ci 失败是基建 UNKNOWN，非代码缺陷）。但即使 B 修了、pa-verify 做真语义审查，**合法的**语义 revise（代码真有安全洞 / 逻辑错 / 不兼容）仍会触发回环——所以 A 仍要修，让合法 revise 走同分支累积、不开新 PR。两条正交，都要修。

## What Changes

**A — PR 跟需求（结构）**
- **A1 dev-agent branch 去 stamp**：`auto/{slug}`（或带 PRD 稳定 id），不含时分。verify revise 各轮复用同一分支。
- **A2 dev-agent 开 PR 幂等**：L974 `gh pr create` 前先 `_lookup_pr(branch)`（run_daily 已有 helper），已有 open PR 则只 `git push`（同 head 新 commits → GitHub 自动并入已 open PR），无才 create。
- **A3 worktree 改名跟 slug**：worktree 名 `{slug}`（非 `{stamp}-{slug}`）。**r2 R2-A-7 修正**：dev worktree 本就跨轮复用（run_daily L2152 循环前建一次、循环内不重建），A3 实际效果是**改名**——让 L2153 `if wt.exists(): remove` 跨 cron 确定性清理旧 worktree、并与分支名一致，非「启用跨轮复用」。
- **A4 增量重投改同分支累积**：run_daily L1258 round≥2 base = 上轮分支 → base 不变（项目默认分支）、worktree 持久继承上轮改动。**取舍**：失去「分支级隔离」，退化为 commit 级回滚（见 design D1）。

**B — pa-verify 做语义审查（职责）**
- **B1 pa-verify persona 重写为三方独立审核**：定位 = 发布前独立三方审核（与项目自审核并行、不替代——见 design「ADR-0002 边界澄清」）。审三维度：① **功能对齐**（diff 真正实现 PRD/Issue 需求）② **开发遗漏**（PRD 验收点逐项覆盖、无漏）③ **质量达标**（代码质量 / 安全 / 性能 / 兼容等硬伤，profile 驱动裁剪）。test_rc 降为「独立测试证实」的**输入之一**（交叉证伪假绿），不单独决定 verdict。任一维度有阻塞性缺陷 → revise；三维度全过 → pass。
- **B2 基建 vs 测试 vs 语义三分**：independent_verify 的 `install_rc` 失败（npm ci / install）= UNKNOWN → fail-safe 升人工（`triaged` / `blocked_infra`），**不进 revise 回环**；只有 `test_rc` 失败（代码层）才喂 pa-verify 判 revise。
- **B3 pa-verify persona 输出契约**：verdict 由三维度综合定；阻塞性缺陷的 feedback_section 沿用现有四要素（定位 / 原因 / 怎么改 / 收尾门）。**锚点**：功能对齐 / 开发遗漏锚 PRD 验收清单（每个验收点 → diff 对应位置）；质量硬伤锚 diff 行——不依赖失败测试，故「绿测试 + 阻塞性缺陷」的 revise 路径仍有机械锚点（解 harden-pa-verify Line-anchored 与绿路径的张力）。

## Capabilities

### Modified Capabilities

- `verified-dev-execution`：新增 **"pa-verify three-party review scope"** requirement（三方独立审核三维度：功能对齐 / 开发遗漏 / 质量达标，test_rc 降为证实输入）+ **"Infrastructure-failure triage"** requirement（install / build 失败 = UNKNOWN 升人工，不进 revise）。现有 "Line-anchored verify feedback" / "Bundle-scoped review coverage" **不变**——它们是 revise 路径的机械辅助，与本变更的审核维度正交。
- `verified-publication-integrity`：新增 **"Pull-request lifecycle follows the requirement"** requirement（PR 跟 slug；verify revise 同分支累积不开新 PR；dev-agent 开 PR 幂等——已有 open PR 则只 push）。现有 "Dual publication gate" / "Evidence integrity" 不变。

## Impact

- **代码（改）**：`dev-agent.py`（branch L795-796 去 stamp + 开 PR L966-976 幂等）、`run_daily.py`（independent_verify 基建三分 L2648-2658 + verify revise 同分支累积 L2472 cur_base / L2172 round loop + reconcile_pr 适配）、`.claude/agents/pa-verify.md`（persona 重写：frontmatter description + 正文判据）、`graph_pa`* 对应 node（若 langgraph cutover 已合，同改）。
- **不变（核心资产）**：dev loop 自治测试 + 发布门 TestEvidence（dev loop 仍自治跑测试判绿，绿才算完事——测试是 dev loop 的活）、fail-safe 三态哲学、journal / reconcile / cutover / single_flight / circuit_breaker 纯函数库、claude runtime。
- **取舍（见 design）**：A4 同分支累积失去分支级隔离（round2 改坏污染 round1 PR commits）→ commit 级回滚；B1 pa-verify 三维度审核增加 LLM 判断面（成本 / 延迟 ↑），但消除无意义 revise 回环（净省）。
- **测试（新增）**：① 同 slug verify revise 复用同分支（round2 不新建 branch，断言 branch string 相等）② dev-agent 开 PR 幂等（mock 已有 PR → 只 push 不 create）③ independent_verify install 失败 → `triaged` / `blocked_infra` 不进 revise ④ pa-verify 测试绿但语义缺陷 → revise ⑤ 回归：合法 test 红 → revise（不回归现有红线锚点）。
- **运维**：#55 #56 堵着的处理（关 #56 round2 重复，保留 #55，释放在途上限）——列入 tasks，需用户确认后执行（outward）。
- **风险**：① A3 worktree 跨轮复用的并发 / 清理（**r1 P2-1 修正**：同 slug 跨 cron 去重由 radar/prd + circuit_breaker 防，非 single_flight——single_flight 是 per-owner_repo 并发槽；同 cron 内 round1→round2 串行无竞态）② B1 pa-verify 三维度 verdict 一致性（需 canary 校准 verdict 分布，防语义判红过严 → 回环依旧）③ A2 幂等 push 同分支的 base 一致性（worktree 持久则 base 稳定，无 rebase 问题）。
