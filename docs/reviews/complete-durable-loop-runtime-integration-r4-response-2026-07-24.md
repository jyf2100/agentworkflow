# complete-durable-loop-runtime-integration R4 Review Response

日期：2026-07-24

关联评审：`docs/reviews/complete-durable-loop-runtime-integration-review-r4-2026-07-24.md`

评审代码基线：`main@714c854`（review 文档于 `74c1573` 入仓；当前 `main@74c1573`）

响应结论：**接受 R4 的 Request Changes。承认 P0-1 / P0-2 / P1-2 阻断与 P1-3 / P1-4 open，规划一个定向修复批次；不声明 durable loop runtime 已最终验收通过。**

## 1. Evidence Status

确认 R4 评审结论：基础质量已过（`722 passed` + Ruff 全绿 + `git diff --check` 通过），但验收证据不真实、不可追溯、不可跨机器复核。承认 R4 定位的现状：

| 发现 | 现状定位 |
|---|---|
| P0-1 | `runtime_evidence.py:473-475` base query 仅 `echo READY` 触发一次 Bash lifecycle；`:558-575` 按事件类型回填 → 一个 `PostToolUse` 同时标 `test_red`/`test_stale`/`test_green`，一个 `Stop` 同时标 `no_test`/`semantic_revise` |
| P0-2 | `cutover.py:1014-1016` 归档 `manifest.summary`（仅 PASS/FAIL 文本），缺 `sub_evidence_refs` / `evidence_digests` / schema |
| P1-2 | `runtime_evidence.py:1095-1186` 生成的 index 含本机 artifact root / 路径；`:1251-1260` 写失败仅打印警告（fail-open）；`714c854` 删 index + `.gitignore` 忽略，无替代 publisher |
| P1-3 | 无绑定 `714c854` 的 7.2 / 7.6 / all drill 退出码与 evidence refs |
| P1-4 | 7.2（`:1060-1083` 内联判定）与 7.6（`cutover.py:828-845` gate helper）两入口未共享判定实现 |

R3-response §2.2「共同判断收敛为纯函数」的建议在 R3 修复批次中未落实，R4 P1-4 据此重新提出，本批次一并闭环。

## 2. P0-1 逐场景真实执行 + correlation ID（计划）

**现状确认**：六个 required scenario 的布尔值由少量通用事件批量产生，predicate 逐项检查六个布尔值但布尔值本身可由跨场景复用的事件推导，假绿根因未消除。

**修复方案**：
- 把六个 scenario（`test_red` / `test_stale` / `test_green` / `no_test` / `semantic_revise` / `subagent`）从「单 base query 触发通用 lifecycle + 按事件类型回填」改为 **6 个独立 query**，每 query 注入该 scenario 的专属输入（对应 fixture / verdict / subagent 触发）。
- 每 query 生成唯一 `correlation_id`，callback journal 落 `(scenario_id, correlation_id, event_type, payload)`，并把该 scenario 的 test_state / staleness / semantic_verdict 关联到同一 `correlation_id`。
- predicate 改为**逐 scenario 校验场景关联证据**：存在匹配该 `correlation_id` 的 callback，且关联的 test_state / staleness / verdict 与 scenario 一致——替换「某 lifecycle event 曾在 query 集合中出现」的语义。

**反例验收**：
- 单个通用事件（如一次 `PostToolUse`）不得证明任一 scenario 为真实；
- 仅当某 scenario 的独立输入已执行、且其 `correlation_id` 关联的 callback + test_state + staleness + verdict 齐全时，该 scenario 才 proven；
- 六个 scenario 未全部 proven 时，7.2 predicate 与 7.6 SDK outcome 均为 red。

## 3. P1-4 共享判定纯函数（计划，与 P0-1 同批）

**修复方案**：提取纯函数 `evaluate_scenario(scenario_id, callback_evidence, test_state, staleness, verdict) -> ScenarioJudgement(proven, diagnostic)`（纯 stdlib、无副作用）。7.2 CLI predicate（`runtime_evidence.py`）与 7.6 outcome extractor（`cutover.py`）均 import 并消费同一函数，消除字段缺失 / 兼容字段 / 错误信息的两入口漂移。为两个消费点分别保留回归测试。

此即 R3-response §2.2 与 R4 P1-4 共同要求的收敛点。

## 4. P0-2 结构化 manifest + 读回（计划）

**现状确认**：`archive_digest` 指向状态摘要，另一环境无法沿它遍历验证七份子证据。

**修复方案**：
- 定义 cutover manifest schema：`schema_version`、`run_meta`、`outcomes[]`（每项含 `passed`、`evidence_digests[]`、`sub_evidence_refs[]`）、`evidence_integrity`。
- `cutover.py:1014-1016` 归档**完整结构化 manifest**（替换 `manifest.summary`）。
- 归档后立即 `read-back` 并校验所有 `sub_evidence_refs` 引用存在且 digest 匹配；任一校验失败不返回 passing `archive_digest`（red）。

**反例验收**：归档可读回；`sub_evidence_refs` 缺失、digest 不匹配、outcome 不齐、artifact 不可读均 fail-closed，不返回 passing digest。

## 5. P1-2 evidence publication（计划）

**现状确认**：`714c854` 删除本机 index 后跨机器复核入口消失；index 写入失败 fail-open。

**修复方案**：采纳 R3-response §3 与 R4 三选一中的**方案①——脱敏 immutable evidence bundle 入仓**：
- drill 产出脱敏 bundle（移除本机绝对路径、用户名、临时 workdir token，保留 digest + 相对引用 + 必要 artifact 内容），落 `docs/evidence/<bound_commit>/`，由 index 引用。
- `publish_evidence()` 对 write / publish / read-back / digest-verify 任一失败 raise（替换 `runtime_evidence.py:1251-1260` warn-and-continue），验收命令非零退出。
- 最终 index 至少绑定 R3-response §3 列出的七项：被验收 commit SHA、runner 版本与执行时间、clean/dirty 状态及准确语义、顶层 manifest digest、七个子 evidence digest、持久存储位置、可在另一台机器执行的验证命令。

**方案②（CI artifact / object store）**列为 follow-up：本仓 cron 本地驱动、暂无 CI publisher 基础设施；本批次先以 bundle 入仓满足 cross-machine 可读可重算，CI store 作为后续升级路径。

**反例验收**：
- write / publish / read-back / digest-verify 四类失败均导致验收命令非零退出；
- 另一台机器仅凭仓库即可读取 bundle、重算 digest、确认引用链，无需访问产生机器的本地路径。

## 6. P1-3 final commit drill 绑定（计划）

**修复方案**：drill 记录 `bound_commit = git rev-parse HEAD`，写入 manifest `run_meta.bound_commit`；acceptance 校验 `bound_commit == 最终 clean commit`，来自旧 commit 的 evidence 不被接受为本批次验收。在最终 clean commit 上产出 `runtime_evidence.py --drill 7.2` / `--drill 7.6` / `--drill all` 的命令、退出码与 evidence refs。

**反例验收**：`bound_commit` 与最终 clean commit 不一致时，验收拒绝。

## 7. Deferred Items

不纳入本修复批次，单独跟踪、不因 P0 修复自动视为完成：

- **P1-1**：7.6 执行真实统一质量入口（from R3，R4 仍 deferred/open）。
- **Scope Finding**：根 `.gitignore` 的 `.claude` / `*.bak` / `*.tmp` / `*.log` 规则已在 `714c854` 独立提交，不混入本批次。

## 8. Resubmission Gate

重新申请关闭前提供（对应 R4 §8 *Required Next Evidence* 六项）：

1. 每个 SDK scenario 独立执行并携带 correlation ID 的真实 callback / gate evidence（P0-1）；
2. 归档后可读回的结构化 cutover manifest，包含七个子 evidence refs（P0-2）；
3. index / evidence publication 的 fail-closed 行为及反例测试（P1-2）；
4. 从干净环境下载或读取原始 artifacts 并重算 digest 的记录（P1-2 cross-machine）；
5. 最终 clean commit 上 `quality.sh`、`--drill 7.2`、`--drill 7.6`、`--drill all` 的命令、退出码与 evidence refs（P1-3）；
6. 证明 passing evidence 绑定 clean final commit 而非旧快照（P1-3）。

## 9. Final Decision

**接受 Request Changes。** 规划定向修复批次，按依赖分步提交（线性历史）：P0-1 + P1-4（共享纯函数 + 逐场景执行，同批）→ P0-2（结构化 manifest + 读回）→ P1-2（bundle 入仓 publication + fail-closed）→ P1-3（final commit drill 绑定 + 产出绑定证据）。

在 P0-1 场景真实性、P0-2 结构化归档、P1-2 持久 publication 完成前，不推送「R3/R4 已全部关闭」或「durable loop runtime 已最终验收通过」的声明。
