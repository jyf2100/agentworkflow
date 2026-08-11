# 编排层「机械层 vs 语义层」边界审查

> 审查日期：2026-08-11
> 性质：**read-only 架构审查**（未改任何代码）
> 范围：pa 编排器侧（`scripts/` 下调度/状态机/门代码）
> 结论：**编排层整体高度遵循边界原则；`independent_verify` install 藏输入是孤例漏抄，非系统性替判。审查另发现 3 类边界偏离（编排器产语义 verdict / prompt 内指引 / schema 硬编码），列为后续优化候选。**

---

## 0. 审查动因

诊断 `independent_verify` install 失败被吞（见 `independent-verify-install-failure-solutions.md`）时，确认其修法应守一条架构原则：

> **编排器（机械层）只如实记录并传递客观事实给 persona；语义结论（pass/revise/drop、原因、怎么修）全部归对抗 persona（语义层）；编排器不预判结论、不替判、不用硬门 override persona 的语义判断。**

install 路径违反了它（藏输入）。由此引出审查问题：**这是孤例，还是编排层有系统性替判倾向？** 本审查回答这个问题。

---

## 1. 总体结论

**孤例，非系统性。** 编排层对「机械 vs 语义」边界极为克制：

- 所有闭环（critic / verify / radar / dispatch / reconcile / circuit_breaker / retry_policy）都遵循「机械层记客观事实 → 喂 persona → 信 persona 的语义结论」。
- 所有 hard gate（VERIFY_MAX_ROUNDS / circuit_breaker / retry_policy / 外部三态）都是**资源管理或 fail-safe**，且 fallback 路径普遍**保留产物升人工**（`interrupted_pr` / `triaged` / `halted`+CRITICAL），而非静默替判。
- verify 闭环的 test 路径（`:2654-2668` 写 test_log 喂 pa-verify）是守原则的正面样本——install 路径（`:2650-2653`）是写的时候**漏抄隔壁孪生分支**。

---

## 2. 审查范围与方法

**框架**：对每个边界处理点判三态——

- **符合**：编排器只记/传客观事实，语义结论归 persona；
- **边界**：偏离理想但不硬违反（如 prompt 内指引、fail-closed 产 verdict）；
- **违反**：编排器硬替 persona 判结论 / 硬阻断 override / 预写语义反馈。

**审查文件**（`scripts/`）：`run_daily.py`（主编排器）、`circuit_breaker.py` / `retry_policy.py` / `external_state.py` / `reconcile.py`（durable runtime + 状态机）、`semantic_gate.py`（in-loop checkpoint）。**不审查** persona 自身（`.claude/agents/pa-*.md`）。

---

## 3. 逐闭环审查

### 3.1 critic 闭环 — 整体符合

| 处理点 | 评级 | 锚点 | 理由 |
|---|---|---|---|
| critic_prompt 构造 | 符合 | `run_daily.py:542-548` | 只塞机械事实（PRD path / source_path / profile），不预写 verdict 映射 |
| `_critic_one` wrapper | 符合 | `run_daily.py:968-975` | 纯机械壳：调 pa-prd-critic → log → setdefault round/revised |
| revise 回环 | 符合 | `run_daily.py:946-962` | verdict=revise 机械回喂 pa-prd round2；`revisions_needed` 来自 persona |
| pass/drop 分流 | 符合 | `run_daily.py:2896,3009-3010,3420-3421` | 纯按 verdict 字段机械分流，无 override |

**边界**（fail-closed 产 verdict，见 §4 层 2）：`:934-937` / `:940-943` / `:959-962`。

### 3.2 verify 闭环 — 整体符合，install 孤例违反

| 处理点 | 评级 | 锚点 | 理由 |
|---|---|---|---|
| `_pa_verify_round` wrapper | 符合 | `run_daily.py:1407-1424` | derive anchors/bundles → 喂 prompt → 信 verdict |
| verify_prompt 喂客观事实 | 符合 | `run_daily.py:632-671` | 喂 PRD/branch/base/diff/test_log/test_rc/anchors/bundles |
| `verify_verdict` 诚实记录 | 符合 | `run_daily.py:2310` | `vinfo.get("verdict")` 直取，无改写 |
| verdict=pass → reconcile+publish | 符合 | `run_daily.py:2323-2462` | 信 verdict；reconcile 是机械 fail-safe（UNKNOWN→blocked） |
| verdict=revise → 反馈+增量重投 | 符合 | `run_daily.py:2463-2509` | 机械路由 persona revise |
| 判红用满 → `interrupted_pr` | 符合 | `run_daily.py:2510-2512` | **不**标 drop，defer 人工 review（不替判死） |
| `VERIFY_MAX_ROUNDS=2` | 符合 | `run_daily.py:118` | 资源上限；触顶 `interrupted_pr`（保留），非 drop |
| green evidence 持久化失败 → blocked | 符合 | `run_daily.py:2281-2295` | 完整性门（拒「无据 green」），非 verdict override |
| post-merge FAIL → auto-revert | 符合 | `run_daily.py:2399-2418` | verify 判 candidate；post-merge 判 integration；main 客观红→revert 是机械响应；UNKNOWN→halt 不 auto-revert |

**违反**：`:2650-2653` install 藏输入（见 §4 层 1）。
**边界**：`:667-670` prompt 预写 verdict 映射（§4 层 3）；`:2484` verifier_signal 硬编码（§4 附带）。

### 3.3 radar/dispatch 闭环 — 全部符合

| 处理点 | 评级 | 锚点 | 理由 |
|---|---|---|---|
| radar_prompt 喂事实 | 符合 | `run_daily.py:485-503` | 喂 today_new + match_surface + dedup_items；score 由 persona 自打 |
| stage_radar 候选去重 | 符合 | `run_daily.py:840-858` | 按 `done_sources` 机械去重（省 LLM）；**不**按 relevance 后置过滤 |
| dispatch 准入门 | 符合 | `run_daily.py:2099-2137` | profile/branch protection 三态/idempotency/inflight 全机械；UNKNOWN→blocked |
| reconcile_pr 三态对账 | 符合 | `run_daily.py:2536-2585` + `reconcile.py` | 远端真源三态查；UNKNOWN→保留不补开/删 |
| off_track → triage | 符合 | `run_daily.py:2249-2259` | pa-progress persona 判 on/off_track；编排器仅机械路由 |

**边界**：`:503` radar prompt `relevance<0.5` 阈值（§4 层 3）。

### 3.4 硬门/threshold — 全机械止损或 fail-safe

| 处理点 | 评级 | 锚点 | 理由 |
|---|---|---|---|
| `VERIFY_MAX_ROUNDS` / `MAX_TURNS` / `TIMEOUT` | 符合 | `run_daily.py:109-118` | 资源上限 |
| `circuit_breaker.py` | 符合 | 全文 | 触发于客观 post-merge red + auto-revert REVERTED；cooldown 内禁再 auto-merge → `triaged`；fail-open |
| `retry_policy.py` | 符合 | `:144-200` | 5 决策纯机械信号驱动（budget/session/exception/fingerprint） |
| `external_state.py` / `reconcile.py` | 符合 | 全文 | 三态（FOUND/NOT_FOUND/UNKNOWN）；UNKNOWN→fail-safe BLOCK |
| `blocked_test_gate` | 符合 | `run_daily.py:2216-2231` | dev-agent exit 14（自身机械测试门）→ 记状态不验证/不开 PR |
| `blocked_evidence` | 符合 | `run_daily.py:2281-2295` | green evidence 持久化失败 → fail-closed |

---

## 4. 偏离点分层（按性质）

### 层 1 — 藏输入【违反·孤例】

- **`run_daily.py:2650-2653`**：npm ci 失败 `return`，install stdout 只落脏 `log_file`，不写独立 `install_log`；`verify_prompt :640-646` 不读 `install_rc`/`install_log` → pa-verify 看到「未跑」而非「install 红」。
- **性质**：剥夺 persona 判断依据（不产 verdict，只是不给真相）。
- **修法**：照抄正下方 test 路径（`:2654-2668`）—— 写 `install_log` + verify_prompt 读 `install_rc`/`install_log` + test_state 加 install 失败分支。详见 `independent-verify-install-failure-solutions.md` 方案 1。

### 层 2 — 编排器产语义 verdict【边界·fail-closed，性质最接近替判】

- **`run_daily.py:934-937`**：prd 缺 path → 编排器产 `verdict: "drop"`（注释「Phase 0 止血，不 TypeError 穿透 abort」）。
- **`run_daily.py:940-943`**：critic 漏吐 verdict → 编排器产 `entry["verdict"] = "drop"`（同上止血）。
- **`run_daily.py:959-962`**：revise 异常 → 编排器产 `verdict: "drop"`。
- **性质**：这三处是 persona 输出**残缺**时编排器**自己产语义 `drop`**（下游 = PRD 不进 dispatch = 语义排除）。动机合理（fail-closed 防穿透 abort），但**行为上比 install 更接近「替判」**——install 是藏输入不产 verdict，这是编排器真在产语义结论。
- **建议**：改 `triaged`/`blocked`（升人工），守「编排器不替判死，defer to human」。属优化，非阻断。

### 层 3 — prompt 内预写指引【边界·最轻，persona 可 off-script】

- **`run_daily.py:667-670`**：verify_prompt 预写「测试绿→pass / 测试红→revise」映射。带 escape hatch（「无重大跑题即可」可 off-script 判 revise-on-green）。
- **`run_daily.py:503`**：radar_prompt 预写 `relevance<0.5 丢弃`，由 persona 自己应用（编排器不后置过滤）。
- **性质**：「prompt 教 persona 怎么判」，非「编排器代码 override」。persona 仍可偏离。
- **建议（低优）**：理想下这些规则归各 persona 自己的 contract（`.claude/agents/pa-*.md`），编排器 prompt 只喂事实。但**不算硬替判**，当前可接受。

### 附带 — schema 缺口【边界·硬编码语义分类】

- **`run_daily.py:2484`**：`verifier_signal=RP.VerifierSignal.LOCAL_FEEDBACK` 硬编码（session_aware_retry flag 门内）。
- **性质**：RetryPolicy 的 `SUGGEST_ALTERNATIVE→FORK` 分支被这硬编码结构性堵死。根因：pa-verify 输出 schema 当前不产 `verifier_signal` 分类，编排器保守默认 LOCAL_FEEDBACK。
- **建议**：扩 pa-verify 输出加 `verifier_signal` 字段，编排器透传（去硬编码）。

---

## 5. 后续优化候选（按优先级）

| # | 项 | 层 | 锚点 | 优先级 |
|---|---|---|---|---|
| 1 | install 藏输入（补 install_log + verify_prompt 读） | 层 1 | `:2650-2653` | 高（已诊断，见 solutions 方案 1） |
| 2 | critic fail-closed 产 drop 改 triaged（升人工） | 层 2 | `:934-937 / :940-943 / :959-962` | 中（编排器产语义 verdict，守原则值得改） |
| 3 | 扩 pa-verify schema 加 verifier_signal（去 `:2484` 硬编码） | 附带 | `:2484` | 中（解锁 FORK 重试分支） |
| 4 | prompt 预写规则移入 persona contract | 层 3 | `:667-670 / :503` | 低（不算硬替判） |

候选 2/3/4 均为独立小 change，不影响当前架构合规性判定。

---

## 6. 相关文档

- 单点 bug 诊断 + 三方案：`independent-verify-install-failure-solutions.md`
- 原则出处：`CONTEXT.md`（机械活/语义活切分）、`CLAUDE.md`（机械活 vs 语义活）、`SPEC.md` §4.4（独立验证闸）
- 控制面/目标面隔离：ADR-0001；dev-agent 通用执行器：ADR-0006

---

> 审查为 read-only，未改任何代码。层 1/2/3/附带 关键锚点经 2026-08-11 实证复核。
