## Why

dev-agent 的 SDK 内循环已有三道机械安全机制：`bash_allowlist`（命令闸）、`N_STALL=100`（卡死刹车）、`evaluate_gate`（发布门）。它们对「方向」都瞎眼：

- `N_STALL` 判"还在不在动" → dev 一直改代码就不刹车；
- `evaluate_gate` 判"测试绿不绿" → 测试绿就放行。

于是有一种失效模式三者都拦不住：**dev 勤奋地跑偏**——一直在动、一直让测试变绿，却根本没在解决 PRD 的真验收标准。dev 只要"把容易绿的测试做绿"就能同时骗过 N_STALL 和 evaluate_gate，无需作弊。这个缺口目前只靠 cycle 边界的 `pa-verify` 兜（事后语义审 diff），但它是**事后**的——dev 可能已烧几十 turn 在错方向上才被 cycle 边界发现。早一个数量级止损的语义探测在内循环是缺位的。

## What Changes

- **内循环新增方向抽查（in-loop checkpoint）**：dev loop 内每 K=10 turn，把"PRD 验收标准全文 + 当前 git diff 摘要"喂给一个**独立**的轻量评判 persona（`pa-progress`），判方向 `on_track` / `off_track`。评判器 ≠ 实现 Agent，对齐 Loop Engineering 核心原则，并把该原则从 cycle 边界下沉到内循环。
- **两阶段纠偏状态机**：首次 `off_track` → break 当前 SDK query → `main()` 用 `resume=<session_id>` + redirect 提示续做（给 1 次纠偏机会）；仍 `off_track`（第 2 次）→ 止损退出（exit 15）。
- **新 exit 15 = semantic off_track**：dev loop 的退出码扩位，编排器消费后归入 triage 池（不阻塞队列，类比 timeout），区别于 stalled(12) / 发布门(14)。
- **fail-open 评判**：评判子进程失败 / 成本熔断 / session 不可恢复 → 不干预 dev（额外护栏无正向证据放行，绝不因护栏故障误杀 dev）。
- **共享零依赖 `persona_call.py`**：从 `run_daily.run_persona` 抽两层 JSON 解析 + 重试 + 契约校验成纯 stdlib 模块，供 dev-agent（目标面执行器）在不连带加载 SDK 的前提下调用评判子进程（守 `test_dev_agent_source.py` 反 invariant）。

## Capabilities

### New Capabilities

- `in-loop-semantic-checkpoint`: dev SDK 内循环的方向抽查——周期性独立评判（K=10 turn）+ 两阶段纠偏状态机（首 off_track 注入 redirect 续做 / 二 off_track exit-15 止损）+ fail-open 评判 + 成本熔断 + exit-15 结果契约。

### Modified Capabilities

- 无既有 spec delta。`verified-dev-execution`（纯机械测试门，无语义评判概念）保持不变；语义方向探测独立成 `in-loop-semantic-checkpoint`，不污染机械门规约。executor 结果 JSON 实际新增 `off_track` 字段 + exit 15，但其语义归属本新 capability 描述。

## Impact

- **代码**：
  - `scripts/dev-agent.py`：`create_loop_state` 加 9 个 checkpoint 字段；`process_dev_loop` 加 `prd_text` 参 + checkpoint 分支；`main()` 改 resume 重发 while 循环 + exit-15 emit；新增 `git_diff_for_judge` / `build_progress_prompt` / `build_redirect_prompt` / `run_direction_probe`；docstring 加 `15=semantic off_track`。
  - `scripts/persona_call.py`（新建）：零依赖共享模块（stdlib only + stage_contracts）。
  - `scripts/stage_contracts.py`：注册 `CONTRACTS["progress"]`（verdict ∈ {on_track, off_track}）。
  - `.claude/agents/pa-progress.md`（新建）：方向评判 persona。
  - `scripts/run_daily.py`：`TRIAGE_REASONS` 加 `"semantic_off_track"`；dispatch 消费 dev-agent stdout `off_track:true` → 归类 triage（pre-merge 出口，不阻塞队列）。
- **既有 spec**：无 delta（新 capability 独立）。
- **运维**：cron 产出的 dev run 多一种 triage 出口 `semantic_off_track`，report 简讯归类（类比 timeout / verify_exhausted）；run_log 多 `judge` 字段（verdict / covered / cost / round）供上线后调 K。
- **成本**：常态 15×~20s≈300s（DEV_LOOP_TIMEOUT 的 8%）+ 熔断 $0.40 上界；fail-open 保证护栏故障不误杀。
