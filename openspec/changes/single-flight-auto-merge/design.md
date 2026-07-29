## Context

pa 的 dispatch 段当前对同一目标仓**并发**投递多个 PRD（`max-concurrent=4`），各自独立 worktree→verify→「兜底开 PR 待人工 review」，靠 `max_prs_in_flight`（默认 2）限并发扇出。

经 grilling 拷问 + 5 专家审核，确认两件事：(1) 该闸**对错了靶子**——限「分支总数」而非「改动是否落在同一文件」（2026-07-29 实证：#47+#48 都改 `hub/server.cjs` 会撞被同时放行，不重叠的 #49 反被挡）；(2) v1 设计在「自动 merge 改 main」这一**新破坏性副作用**上深度不足——auto-revert 失败链路、merge exactly-once/crash 恢复、执行位置（ADR-0001）、post-merge 测试同源漏判等均未闭合。本 v2 补齐这些。

既有约束（不可破坏）：`fail-safe-dispatch` 三态不变式（UNKNOWN=阻断）；`verified-dev-execution` 测试门（绿才发布）；durable runtime（journal-driven，exactly-once reconcile）；ADR-0001（控制面/目标面分离）；ADR-0006（控制面标准执行器，经 SDK loop 在目标仓 worktree 内干活）。

## Goals / Non-Goals

**Goals:**
- 消灭同项目跨分支 rebase 冲突（串行单飞）。
- main 自动演进、状态可读（绿即自动合）。
- 异常 PRD 不阻塞队列（triage 池）。
- **merge/revert 作为新破坏性副作用，有 exactly-once、三态 fail-safe、crash 恢复、失败 halt 闭环。**
- 复用既有 fail-safe 三态 + 测试门 + journal-driven 恢复，不破坏现有不变式。

**Non-Goals:**
- 不做准入层改动重叠预判（PRD 无结构化改动面，判定只在出口层）。
- 不做「冲突组动态串行」（Q4-B，重架构，留 follow-up；本期全串行）。
- 不引入人工 approve（用户选 A 全自动）。
- 不把 merge 验证塞进 `verified-dev-execution`（executor 职责，越层——见 D6）。

## Decisions

### D1. 串行单飞 vs 并发（选串行）
同目标仓一次只一个 PRD 走完 dev→verify→merge 闭环；跨目标仓并行。消灭「merge 时 main 被并发动过」这一冲突前提。代价（同项目吞吐塌方）由跨项目并行 + wall-clock 上限（D10）+ 异常出队（D4）缓解。

### D2. 自动 merge 触发条件
dev + verify 双绿，且 rebase 到当前 main 为 `CLEAN` → 自动 merge + push main，替换「兜底开 PR」。`CONFLICT`/`UNKNOWN` → 转 triage，**绝不强合**。

### D3. auto-revert 兜底（v2 强化：三态 + 失败 halt）
merge → push main → 对 main 跑全量测试 → **三态**（`PASS`/`FAIL`/`UNKNOWN`）：`PASS` 保留；`FAIL` → revert；`UNKNOWN` → **不自动 revert 也 keep**，quarantine + 告警等人工。revert 本身亦三态（`REVERTED`/`CONFLICT`/`UNKNOWN`）：非 `REVERTED` = **halt + quarantine 整仓队列**（不 continue），CRITICAL 告警——杜绝「坏代码留 main + 队列续跑叠加」。
- 理由：v1 把唯一恢复压在不可失败的 `git revert`，且要求 continue——审核判为 CRITICAL。

### D4. triage 池（非阻塞出队）
dev 超时 / verify 2 次仍红 / rebase `CONFLICT`/`UNKNOWN` / post-merge `FAIL`-reverted / post-merge `UNKNOWN` → 出队进 triage 池，不阻塞队列，单独成报告。

### D5. max_prs_in_flight 退化
同项目内恒 1；保留作 bug 安全阀。不再是「分支总数」语义。

### D6. merge 执行位置（守 ADR-0001）+ 验证归属
merge / revert / post-merge test **经 dev-agent SDK loop 在目标仓 worktree 内执行**（控制面只发 string prompt 命令，不直接持 git 写句柄）——与现有 dev/verify 同范式，**不违反 ADR-0001**（文件级变更由目标面内 loop 产出，控制面只发指令）。相应地，**merge 验证归 `single-flight-auto-merge` 新 spec**，`verified-dev-execution` 仅加反向约束「executor SHALL NOT merge into main」，不把 merge 塞进 executor 的 publication 门（审核一致 F4）。

### D7. 合并策略（--no-ff + ff-only push）
merge 用 `--no-ff` 产生**单一 merge commit**（revert 目标明确）；push **fast-forward only，严禁 `--force*`**（防覆盖人工/并发提交）。revert 目标 = 本次自动合入产生的唯一 commit（journal 记其 sha）。

### D8. post-merge 测试须与 verify 有差异 + 三态
post-merge 跑的是**集成后的 main 全量 suite**（基线 = main，含本次合入），与 verify（candidate branch、可能是 dev 子集）**覆盖面与基线都不同**——这是 R4 存在的理由，须显式注释，否则 implementer 当冗余偷工。更关键：若 post-merge 与 verify **同源同套**，verify 假绿→post-merge 假绿→auto-revert 永不触发（安全网失效）；post-merge 须更严或独立 env。结果三态（PASS/FAIL/UNKNOWN），UNKNOWN 不自动 revert（D3）。

### D9. single-flight slot 数据源（跨进程）+ 既有门去留
slot 状态 = **本地 journal 在途闭环状态 + 跨进程 flock**（非 GitHub OPEN PR，非进程内 `threading.Lock`）。理由：cron 每次起新进程，`threading.Lock` 跨 run 不可见（审核一致 F5）；`count_inflight_prs`（查 GitHub OPEN PR）与新「slot」语义错位。`count_inflight_prs` 保留为**独立「OPEN PR 上限」门**（≠ slot），`max_prs_in_flight` 退化为 slot 恒 1（D5）。crash 后 slot 经 journal 重放 + lease TTL 重建为 known（free 或 in-flight-with-lease）。

### D10. wall-clock 预算
各阶段显式 timeout：rebase（~120s）/ merge+push（~60s）/ post-merge test（profile 化，如 ~1800s）/ revert+push（~60s）。单 PRD 总预算封顶（防串行下整仓无限阻塞）。超时 → 出队 triage。

### D11. revert 循环熔断
同 PRD（按幂等键）被 post-merge revert 后，**cooldown 窗口内（如 7 天 / N 轮）禁再 auto-merge**，强制进 triage 等人工——杜绝「verify 绿、合 main 红」的 PRD 在信号衰减前夜夜复发无限循环（审核可测 F2）。

### D12. merge exactly-once reconcile
merge/revert 是新破坏性副作用，须纳入 exactly-once：新增幂等 kind `merge`/`revert`（扩 `reconcile.ALLOWED_KINDS`）+ resolver（三态：`merge` 事件查 **merge_commit** 是否 main 祖先；`revert` 事件查 **revert_commit**（`git revert` 产出的新 commit，revert 时记录 sha）是否 main 祖先。⚠️ git revert 不删原 merge commit，原 commit revert 后仍为 main 祖先，故 revert 状态不可由 merge_commit presence/absence 推断，必须查 revert_commit ancestry）+ 新 crash 边界 `merge_push`/`revert_push`（扩 `CRASH_BOUNDARIES`）+ **写顺序规约**：先 journal append 意图（`merging`）→ push → journal append 确认（`merged`），恢复器据两端事件 + resolver 决断（FOUND→跳过重 merge；UNKNOWN→阻断）。

## Risks / Trade-offs

- **[吞吐塌方]** 同项目串行 → 多 PRD 日 wall-clock 变长 → 缓解：跨项目并行 + D10 上限 + 异常出队。
- **[verify 漏判 → 烂代码进 main]** → 缓解：D3 auto-revert 三态 + D8 post-merge 与 verify 差异 + D7 单 commit revert 粒度。
- **[main 分支保护 / CD 耦合]**（审核架构 F2）目标仓 main 可能受分支保护 / 是 CD 触发器 → auto-merge 直接放大成未审代码上线 → 缓解：canary 只在安全仓白名单；spec 加不变式「main 有 CD 绑定/无保护分支策略 → 禁 auto-merge，退 triage」；显式 push 凭据来源。
- **[main 瞬态红新契约]**（审核安全 F8）先 push 再测 → main 在 [push, 验证完成] 窗口必然可能红 → 须对外暴露「main may be transiently red」契约 + 最大红窗上界（联 D10）+ 可查询的「main 是否已过 post-merge 验证」状态。
- **[dispatch god-stage]**（审核架构 F4）闭环含 8 子阶段 → 须画子阶段 state machine，每个子阶段独立 journal checkpoint（不只「闭环」整体），否则 crash 恢复只能整段重跑/放弃。
- **[告警非 durable]**（审核安全 F5）告警与 revert 耦合 → 告警须 durable（journal 化 + retry，复用 `retry_policy.py`），告警未确认 + revert 未确认 → 队列 halt。
- **[队头阻塞]** → D10 wall-clock + 红 2 次出队。
- **[BREAKING]** → 配套新 ADR（见 Migration）+ triage 报告 + auto-revert 闭环。

## Migration Plan

1. **新 ADR**（审核架构 F3）：`Control-plane auto-merges to target main`，覆盖 D6（执行位置）、分支保护/CD、D3（auto-revert 安全网充分性）——任何「pa 凭什么自动改 main」的复核锚点。
2. **feature flag**（`feature_flags.py`）：`single_flight_auto_merge`，shadow 子模式（走串行消费但只 log、不真 merge/push），对齐 shadow parity（`cutover.py`）。
3. **离线 merge drill**（审核执行 H3，canary 前）：用 fixture 真实 git tmp repo（注入故意红 commit），跑 merge→push→post-merge-test→revert 全链路，断言 main 回干净 + revert commit 干净——补 shadow 测不到的核心链路。
4. **canary**：安全仓白名单内单项目（cc-web-control）开真实自动 merge，观察 N 次闭环（含一次故意红 auto-revert）。
5. **回滚**：flag 关 → 回并发 + 开 PR 待 review 旧行为；**已合 main 的 commit 不自动撤回**（须人工 `git revert`），故给 merge commit 打稳定 marker（footer `Pipeline-Merge: <prd_id>`）供回滚后机械找出（`git log --grep`）。

## Open Questions

- wall-clock 各阶段具体值（D10 给的是草案）。
- `count_inflight_prs` 去留：保留为独立 OPEN-PR 上限门，还是废弃（D9 倾向保留但语义独立）。
- post-merge 与 verify 的差异具体形式（更严覆盖？独立 env？还是接受 main 集成 suite 本身就够不同）。
- auto-revert 后该 PRD 是否入队尾重试：本期进 triage + cooldown（D11），不自动重试。
