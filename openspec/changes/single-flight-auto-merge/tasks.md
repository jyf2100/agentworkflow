## 1. 准备：feature flag + 状态 schema + ADR

- [x] 1.1 在 `feature_flags.py` 注册 single-flight **双 flag**（`single_flight_serial_shadow` + `single_flight_auto_merge`，默认关）——遵循 pa shadow→driven pattern（单 bool flag 表达不了「串行+不merge」vs「串行+真merge」）；TDD RED→GREEN（23 测全过）+ ruff 干净
- [x] 1.2 扩展 `dispatch_*.json` schema：新增 `merge_commit` / `reverted` / `triage_reason` / `post_merge_verdict` 字段（向后兼容，旧字段保留）；TDD RED(KeyError)→GREEN(24 测过)+ruff 干净；rec 最早构造点即含新字段（preflight 阻断短路可断言）
- [x] 1.3 新 ADR `0008-control-plane-auto-merges-target-main.md`：8 护栏覆盖执行位置(D6/守 ADR-0001)、分支保护/CD 准入、auto-revert 充分性(D3)、exactly-once、flag gated——「pa 凭什么自动改 main」复核锚点

## 2. 串行单飞消费器（D1 / D5 / D9）

- [x] 2.1 改 dispatch 投递为同 `owner_repo` 串行消费（一次一个 PRD 走完 dev→verify→merge 闭环才下一个），跨 `owner_repo` 并行；复用并扩展现有 `DISPATCH_LOCKS`（已是整段闭环串行，本项是**显式化 + 跨进程化**，非全新实现）；新增 `_dispatch_serial_by_repo`（按 owner_repo 分组、同组顺序 _run_one、跨组 ThreadPoolExecutor 并行）+ stage_dispatch flag gated 路由；TDD RED→GREEN(27 测)+ruff
- [x] 2.2 改准入门为 per-repo single-flight slot 检查：slot 状态 = journal 在途闭环状态 + 跨进程 flock（**非** GitHub OPEN PR `count_inflight_prs`、非进程内 `threading.Lock`）；slot `UNKNOWN` → `blocked_external_state`，不当代空闲
- [x] 2.3 slot crash 恢复：journal 重放 + lease TTL 重建为 known（free 或 in-flight-with-lease），**不**默认 free
- [x] 2.4 `max_prs_in_flight` 退化为同项目恒 1（保留作 bug 安全阀）；`count_inflight_prs` 语义独立为「OPEN PR 上限」门（≠ slot），去留见 design Open Questions；dispatch_one 准入门 4 serial_shadow on→`_max_inflight=1`、off→baseline(prof 默认 2)；TDD RED(planned≠skip)→GREEN(36 测)+ruff

## 3. 自动 merge 阶段（D2 / D6 / D7）

- [ ] 3.1 dev+verify 双绿 → fetch main HEAD + rebase 分支到当前 main，产出三态 `CLEAN` / `CONFLICT` / `UNKNOWN`（CLEAN 须**正向证据**：fetch 成功 + exit0 + 干净工作树 + 无冲突标记；缺证 = UNKNOWN）
- [ ] 3.2 `CLEAN` → 经 dev-agent SDK loop 在 worktree 内 `--no-ff` merge + push（ff-only，禁 `--force*`），记录 `merge_commit`（替换现「兜底开 PR 待 review」）
- [ ] 3.3 `CONFLICT` / `UNKNOWN` → 转 triage 池，**不强合**（对齐 fail-safe UNKNOWN 不变式）
- [ ] 3.4 给 merge commit 打稳定 marker（footer `Pipeline-Merge: <prd_id>`）供回滚后机械找出（`git log --grep`）

## 4. post-merge 验证 + auto-revert 兜底（D3 / D8 / D11）

- [ ] 4.1 自动 merge 后经 dev-agent loop 对**集成后 main** 跑全量测试（基线 = main，与 verify 覆盖面/基线均不同；显式注释防被当冗余偷工）
- [ ] 4.2 测试三态：`PASS`→保留+放行；`FAIL`→revert；`UNKNOWN`→keep+quarantine+halt+CRITICAL 告警（**不**自动 revert）
- [ ] 4.3 revert 三态：`REVERTED`→放行+进 triage(reason=`post_merge_red_reverted`)；`CONFLICT`/`UNKNOWN`→halt 整仓队列 + CRITICAL 告警（**不** continue，**不**强改 main）
- [ ] 4.4 revert 循环熔断：同幂等键 PRD 被 post-merge revert 后 cooldown 窗口内禁再 auto-merge，强制进 triage
- [ ] 4.5 告警 durable 化（journal 事件 + retry，复用 `retry_policy.py`）；告警未确认 + revert 未确认 → 队列 halt
- [ ] 4.6 「main may be transiently red」契约 + 最大红窗上界（联 D10）+ 可查询的「main 是否已过 post-merge 验证」状态

## 5. triage 池 + report 语义（D4）

- [ ] 5.1 各 triage 出口进池不阻塞（除非 halt）；ejection reason 取自固定枚举（`timeout`/`verify_exhausted`/`rebase_conflict`/`rebase_unknown`/`push_failed`/`post_merge_red_reverted`/`post_merge_unknown`）
- [ ] 5.2 report 简讯语义从「开 PR #X 待 review」→「已合 main（commit abc）」/「需 triage（原因）」；triage 单独成段，与已合/在途/halted 区分
- [ ] 5.3 `stage_report` 状态桶扩展（merged / reverted / triaged / halted），不只「在途/完成」

## 6. durable + spec 对齐（D12，拆细）

- [ ] 6.1a 状态机扩展：新 `MERGED` 非终态（现有 `published` 是终态 `_TRANSITIONS[PUBLISHED]=frozenset()` 会阻塞 revert；`MERGED` 须允许 → post-merge-verified / reverted 转移）
- [ ] 6.1b reconcile：扩 `ALLOWED_KINDS` 加 `merge`/`revert`；resolver 三态——`merge` 查 `merge_commit` 是否 main 祖先；**`revert` 查 `revert_commit`（git revert 新产 commit，revert 时须记录 sha）是否 main 祖先**（⚠️ v2.1 修正：git revert 不删原 merge commit，故不能用 merge_commit presence/absence 判 revert 是否已发生）；FOUND→跳过重 apply，UNKNOWN→阻断
- [ ] 6.1c crash 边界：扩 `CRASH_BOUNDARIES` 加 `merge_push`/`revert_push`（子阶段独立 checkpoint，不只「闭环」整体；防 god-stage 整段重跑/放弃）
- [ ] 6.1d 写顺序规约：journal intent(`merging`/`reverting`) → push → journal confirm(`merged`/`reverted`)；恢复器据两端事件 + resolver 决断
- [ ] 6.2 为 `single-flight-auto-merge`（全 scenario）/ `fail-safe-dispatch`(delta) / `verified-dev-execution`(delta) 的每个 scenario 写对应单测/集成测

## 7. 验证 + 灰度上线

- [ ] 7.1a shadow 模式：串行消费器全跑但只 log、不真 merge/push，对齐 shadow parity（`cutover.py` 产出证据）
- [ ] 7.1b **离线 merge drill**（canary 前必做）：fixture 真实 git tmp repo（注入故意红 commit），跑 merge→push→post-merge-test→revert 全链路，断言 main 回干净 + revert commit 干净 + marker 可 grep——补 shadow 测不到的核心链路
- [ ] 7.2 canary：cc-web-control 单项目开真实自动 merge，观察 N 次闭环（含一次故意红的 auto-revert + 一次熔断触发）
- [ ] 7.3 回滚验证：`single_flight_auto_merge` 关 → 回到并发 + 开 PR 待 review 旧行为（已合 main 的 commit **不**自动撤回，靠 marker `git log --grep` 找出人工 revert）
