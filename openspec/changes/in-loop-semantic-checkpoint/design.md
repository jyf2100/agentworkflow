## Context

dev-agent 内循环三道机械机制对"方向"瞎眼 → **勤奋跑偏**失效模式无内循环干预。本 change 补一个**独立的轻量方向评判**到内循环，作为 stalled 之外的第二干预源。

**SDK spike 决定性发现**（claude_agent_sdk 0.2.128 = 实装版本；streaming 已由 `prompt_stream.py` 满足、`can_use_tool` 可用——CLAUDE.md / pyproject.toml 已同步此版本）：

- `query()` docstring 明文 "No interrupts: Cannot interrupt or send follow-up messages"。CLI 在一个 user prompt 下跑**一整个长 turn**（dev 内部所有 tool-step 都在一个 turn 内），只在 ResultMessage 边界才读下一条 user message。
- 因此**中途进程内注入**（往 stdin buffer push 反馈）会滞留到 agent 自然结束 → 等于没注入。

## Goals

- 内循环每 K turn 抽一次方向，独立评判器判 on_track / off_track。
- 两阶段纠偏：首次 off_track 给 1 次纠偏机会（redirect 续做），二次仍 off_track 止损（exit 15）。
- fail-open：评判基础设施故障绝不误杀 dev（额外护栏无正向证据放行）。
- 守 SDK-隔离 invariant（dev-agent 调评判子进程不连带加载 SDK）。

## Non-Goals

- 真·进程内实时注入（Path A，ClaudeSDKClient + interrupt）—— 未来增强，需独立 spike，本期不做。
- 替换 cycle 边界 `pa-verify`——本机制是早止损，pa-verify 仍是完成判定（绿且无回归），两者并存。
- 判 PRD 价值 / 修改 PRD / 改测试——评判器只判方向、只裁判。
- 把 run_daily.run_persona 迁到 persona_call——本期 dev-agent 独用，run_daily 稳定后再改薄 shim。

## Decisions

### D1：Path B（break + 新 session 重发）——接受新 session 现实（review 三方核验修正）

- **Path C 证伪**：spike 确认 `query()` 不支持中途注入，stdin buffer 滞留到 turn 结束。
- **Path A 高风险**：`ClaudeSDKClient + interrupt()` 真实时注入需独立验证。本期不做。
- **Path B（采用）**：off_track → break 当前 query → `main()` 用 redirect prompt **开新 session** 重发一次 `query()`。
- ⚠ **原设计声称「resume 续做 = 语义等价进程内注入」经 review 三方（python-reviewer / architect / silent-failure-hunter）核验为伪**：
  break 触发在 `process_dev_loop` 的 AssistantMessage 分支，而 `session_id` 仅在流末 ResultMessage 才出现
  → break 后 `last_session_id` 总是 None → `options.resume=None` → redirect leg 实为**全新 query**，拿不到先前 session 的对话上下文。
  D1 据此诚实化：redirect leg **自包含 PRD + hint + 工作树自恢复提示**（让新 session dev 先审视 `git diff` 既有改动再续做）。
  失去的：先前 session 的对话 transcript（dev 不记得自己刚做了什么，只看得到工作树物理改动）。保留的：工作树改动 + off_track 信号到达 + 2 次 off_track 止损。
  机制仍有价值：补三道机械机制（bash_allowlist/N_STALL/evaluate_gate）对「方向」瞎眼的盲区——off_track 早发现 + 1 次纠偏重试 + 止损，三者原本全失效。
  Path A（真·resume 续做 / 真·进程内注入，保留对话上下文）= 未来增强，需独立 spike（见 Open Question #1/#4）。

### D2：两阶段纠偏（首次 redirect 续做 / 二次 exit 15）

off_track 不立即止损（单次跑偏可能是误判或正在铺垫），给 1 次纠偏机会（redirect hint 注入新 session 续做，见 D1）；仍 off_track 才止损，避免无限消耗。on_track 重置 strike 计数。

### D3：rubric 由 judge 自抽（prd_text 整段喂入）

不接线控制面 / 不动 pa-prd / build_prompt / parse_args——把 PRD 全文整段喂给 pa-progress，让它自己定位验收标准节。零接线、低耦合。

### D4：密集 K=10

每 10 turn 抽一次，max_turns=150 → 最多约 15 次。早止损优先于成本。配套紧 max_turns(15) + timeout(120s) + diff 截断(10KB) + 成本熔断($0.40) 控时长。若实测均值 >40s/次 → K 调回 20（Open Question #2）。

### D5：fail-open 评判（绝不因护栏故障误杀 dev）

评判子进程 raise / 成本熔断 / session 不可恢复 → 不干预 dev，继续 loop。额外护栏（本机制）无正向证据时放行，与既有破坏性操作的 fail-safe（无正向证据阻断）方向相反——因为本机制不是破坏性操作，误伤 dev 的代价高于漏判跑偏。

### D6：成本熔断（JUDGE_BUDGET_CAP=0.40 USD）—— ⚠ 当前实际失效（防御性死代码）

state 累加 `meta["cost"]`，超 cap 跳过 checkpoint。**但 SDK cost 上报不可靠**：`meta.cost` / `total_cost_usd` 常返回 None
（dev-agent.py canary 实证，ADR-0006 #6 已知问题）→ `judge_cost_acc` 实际累加 0，本熔断**永不触发**（当前是防御性死代码，待 cost 上报修复后自动激活）。
fail-open 可接受（熔断意在保护，失效只是失去这层保护，绝不伤 dev）；真成本管控依赖 SDK cost 上报修复（ADR-0006 #6 follow-up）。

### D7：exit 15 = semantic off_track（pre-merge triage 出口）

退出码 13 已占（git/PR），15 空闲。off_track 归 triage 池（不阻塞队列，类比 timeout），区别于 stalled(12) / 发布门(14)。

## Open Questions

1. **Path A（ClaudeSDKClient + interrupt 真实时注入 / 真·resume 续做）** — 未来增强，需独立 spike。本期 Path B（break + 新 session 重发，见 D1）只达成「off_track 信号到达 dev + 2 次止损」，**不**达成「同 session 对话上下文续做」——后者需 Path A。
2. **K 是否调回 20** — 取决于 pa-progress 实测单次时长；触发条件均值 >40s/次。上线后观察 run_log `judge.duration` 1-2 周。
3. **roc 快模型** — pa-progress 是独立 `claude --agent` 子进程，model 由 CLI 默认（roc→glm-5.2）定。要用 haiku 量级需 roc 支持 fast alias 或 frontmatter `model:`。先默认 + 紧 max_turns 控时长。
4. **redirect leg 的 session 续接** — 已确认 resume 不可行（D1：break 拿不到流末 session_id）。redirect leg 开新 session，靠工作树 diff 自恢复，丢失先前对话上下文。未来 Path A（真·注入）可恢复同 session 续做。
5. **run_daily 是否同步迁移到 persona_call** — 推荐先 dev-agent 独用，run_daily.run_persona 稳定后改薄 shim（向后兼容）。本期不强制。

## Rejected Alternatives

- **Path C（queue-backed prompt_stream 中途注入）**：SDK 源码证伪，spike 否决。
- **复用 pa-verify 做内循环评判**：pa-verify 判据硬绑"测试绿"（test_rc=0），且输入需测试产物 + diff 路径，与内循环"判方向不看绿"语义不符。必须新建 pa-progress。
- **单阶段立即止损（首次 off_track 即 exit）**：单次跑偏可能是误判或铺垫，过激；两阶段给纠偏机会更稳。
- **稀疏 K（20/30）**：早止损优先，K=10 在控时长前提下更早发现跑偏。
- **把语义评判塞进 verified-dev-execution spec**：污染纯机械测试门规约；独立成新 capability。
