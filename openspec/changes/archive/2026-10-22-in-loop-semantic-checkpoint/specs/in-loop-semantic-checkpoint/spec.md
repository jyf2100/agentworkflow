# in-loop-semantic-checkpoint

> Capability：dev SDK 内循环的**方向抽查 + 两阶段纠偏**。作为 stalled（卡死刹车）之外的第二干预源，
> 补三道机械机制（bash_allowlist / N_STALL / evaluate_gate）对"方向"瞎眼的结构性盲区——
> dev 可一直动、一直让测试变绿却没解决 PRD 真验收标准（**勤奋跑偏**）。评判器独立于实现 Agent
> （Loop Engineering 核心原则），把该原则从 cycle 边界（pa-verify，事后完成判定）下沉到内循环（事中早止损）。

## ADDED Requirements

### Requirement: Periodic in-loop direction checkpoint

dev SDK 内循环 SHALL 每 K turn（K=10）触发一次方向抽查：把 PRD 验收标准全文 + 当前 git diff 摘要喂给独立评判 persona，判 `on_track` / `off_track`。抽查点 SHALL 在 turn 计数可靠的边界（append_run_line 后、stall 刹车前）。turn 非 K 整数倍时 MUST 跳过抽查，不引入额外开销。

- 抽查频率 K=10 是早止损优先的密集策略；max_turns=150 → 最多约 15 次。配套 max_turns(15) + timeout(120s) + diff 截断(10KB) 控时长。
- 抽查是 dev loop 串行内的同步阻塞（dev loop 本就串行）。

#### Scenario: K 整数倍 turn 触发方向抽查

WHEN dev 内循环 turn 计数到达 10（且 > 0）
THEN executor 调一次独立评判 persona（stage=progress），输入 PRD 全文 + 当前 diff 摘要
AND 评判返回前 dev loop 暂停（不推进下一条 SDK message）。

#### Scenario: 非 K 边界 turn 跳过抽查

WHEN dev 内循环 turn 计数为 7（非 10 整数倍）
THEN executor 不调评判 persona，直接推进下一条 SDK message
AND 不产生 judge run_log 条目。

### Requirement: Independent judge persona contract (pa-progress)

方向评判 SHALL 由独立于 dev 实现的 persona（`pa-progress`）承担，判据是"当前 diff 是否在解决 PRD 验收标准"，MUST NOT 依赖测试绿/红、MUST NOT 要求测试产物。输出 payload MUST 含受控 `verdict ∈ {on_track, off_track}`，并附 `covered`（已覆盖的验收点）/ `off_topic`（跑偏项）/ `redirect_hint`（可执行纠偏指引）/ `summary`。judger SHALL NOT 修改任何项目文件（只 Read + 判 + 写 JSON）。

- rubric 由 judger 自抽：PRD 全文整段喂入，judger 自行定位验收标准节（零接线，不动 pa-prd / build_prompt / parse_args）。

#### Scenario: 评判返回受控方向判定

WHEN pa-progress 读到 PRD 全文 + diff 摘要
THEN 其输出 JSON 含 `verdict ∈ {on_track, off_track}`
AND `validate_stage("progress", payload)` 对合法 verdict 返回 []（无 error Issue）。

#### Scenario: 评判越界 verdict 被契约拦下并重试

WHEN pa-progress 返回 `verdict="maybe"`（非受控值）
THEN `validate_stage("progress", payload)` 返回 `Issue(field="verdict", severity="error", ...)`
AND persona_call 按契约重试预算带诊断重试一轮。

### Requirement: Two-stage off-track correction

off_track 处置 SHALL 是两阶段纠偏：首次 off_track → break 当前 SDK query、`main()` 用 `resume=<session_id>` + redirect 提示续做（给且仅给 1 次纠偏机会）；连续第二次 off_track → 止损退出（exit 15）。on_track SHALL 重置 strike 计数（off_track_count 归零），允许 dev 在纠正后继续。

#### Scenario: 首次 off_track 注入 redirect 续做

WHEN 评判返回 off_track 且 state.off_track_count 当前为 0
THEN executor 设 state.redirect_pending=<redirect_hint> 并 break 当前 query
AND main() 用 resume=<last_session_id> + redirect 提示重发一次 query()（同 session transcript）
AND off_track_count 变为 1（仍有一次机会）。

#### Scenario: 连续第二次 off_track 止损退出

WHEN 评判返回 off_track 且 state.off_track_count 当前为 1（已给过 1 次纠偏）
THEN executor 设 state.off_track_exhausted=True 并 break
AND main() 发出 exit 15（不再 resume）。

#### Scenario: on_track 重置 strike 计数

WHEN 评判返回 on_track（无论 off_track_count 当前值）
THEN executor 把 state.off_track_count 归零
AND dev loop 继续推进（不 break）。

### Requirement: Fail-open evaluation

方向评判基础设施 SHALL fail-open：评判子进程失败 / 超时 / 成本熔断 / session 不可恢复 → MUST NOT 干预 dev（不 break、不 exit），dev loop 照常推进。本机制是额外护栏而非破坏性操作，误伤 dev 的代价高于漏判跑偏；护栏无正向证据时放行，与破坏性操作的 fail-safe（无正向证据阻断）方向相反。

#### Scenario: 评判子进程失败不干预 dev

WHEN K-turn 触发评判但 run_direction_probe 抛异常（subproc 超时 / 非零退出 / 非 JSON）
THEN executor 把该次评判当 None 处理，off_track_count 不变、redirect_pending 不变
AND dev loop 继续推进（不 break、不 exit）。

#### Scenario: 成本字段未知被容忍

WHEN 评判返回的 meta["cost"] 为 None（SDK 已知上报缺陷）
THEN executor 累加 0 到 judge_cost_acc
AND 成本熔断不触发（fail-open 可接受）。

### Requirement: Cost breaker

executor SHALL 维护 judge_cost_acc（累加每次评判的 meta["cost"]）；当 judge_cost_acc ≥ JUDGE_BUDGET_CAP（0.40 USD）时，后续 checkpoint MUST 跳过评判（fail-open 不阻断 dev）。熔断状态不重置，持续到 dev loop 结束。

#### Scenario: 累计成本达上限跳过下一次评判

WHEN 某 turn 是 K 整数倍但 state.judge_cost_acc 已 ≥ JUDGE_BUDGET_CAP
THEN executor 不调评判 persona，dev loop 继续推进
AND 落 run_log 记一条 skip（原因=cost_breaker）。

### Requirement: Truthful off-track result contract

executor SHALL 在 off_track_exhausted 时发出 exit 15 + JSON 结果含 `"off_track": true`，并 MUST NOT 与 stalled(12) / 发布门(14) / git-PR(13) 混用。编排器消费 exit-15 后 SHALL 把该 run 归入 triage 池（triage_reason=`semantic_off_track`，pre-merge 出口，不阻塞同项目队列），区别于 verify_exhausted / timeout。main() 末段判定顺序 SHALL 为：off_track_exhausted(15) → redirect_pending(resume) → stalled(12) → gate(14) → commit，互斥。

#### Scenario: off_track 耗尽发出 exit-15 + off_track:true

WHEN state.off_track_exhausted 为 True
THEN main() 向 stdout 输出 `{"ok": false, "off_track": true, "exit_code": 15, ...}` 并以 exit 15 退出
AND MUST NOT 触发 commit / push / PR（exit 15 优先于 stalled/gate/commit）。

#### Scenario: 编排器把 off_track 归入 triage 池

WHEN dispatch 读到 dev-agent stdout 含 `off_track: true`（exit 15）
THEN 编排器设 rec["off_track"]=True 且 triage_reason="semantic_off_track"
AND 该 PRD 进 triage 池单独成报告，不阻塞同项目队列的下一条 PRD。
