# Tasks — agent-verify-injection

## 1. 验收测试工件链（flag 无关，design 阶段 1）

- [ ] 1.1 `scripts/acceptance_tests.py`：`store_suite(prd_id, files) -> {digest, path}`（content-addressed，复用 artifact_store 模式）+ journal 事件 `acceptance_tests`{digest, path, criteria_ids}；load + digest 校验入口；重编写路径存新 digest 时 journal 记 supersedes 版本链（测试学二审 T2）
- [ ] 1.2 测试（先红）：store → digest 跨调用稳定 + journal 事件字段齐；无 criteria / 无 harness → 显式缺席记录，不产空测试套；**覆盖闭合断言**——criteria_ids ⊇ 全部验收点 id，缺者显式 uncovered 记录不静默（T1）；自检失败套件被扣留（缺席 + 原因记录）
- [ ] 1.3 persona `pa-test-author`（`.claude/agents/`）：输入 = PRD 验收点（稳定 id）+ profile harness 声明；输出 = harness 内可执行测试文件 + 每测试标注 criterion id（traceability 机械可查）；无 harness 项目明确拒绝而非猜测；**编写契约**——全覆盖（每 criterion ≥1 test，未覆盖显式声明）+ 确定性纪律（禁 sleep/墙钟/网络依赖，随机钉种子——T4）
- [ ] 1.4 编排接线：critic pass 后调 pa-test-author → 产物入 store 前收集级自检（harness 支持者，pytest `--collect-only` 类；破损扣留记缺席，T2）→ journal 落账（`run_daily.py` critic 段出口 + `graph_pa_critic` 对称；观测面 fail-open——编写失败不拦 dispatch，记缺席）
- [ ] 1.5 profile schema 扩展：`harness` / `verify_test_cmd` / `env` 块（v1 工具链级）三可选字段；harness 声明面 v1 收窄到行锚解析器支持集（jest/pytest），其余显式降级 unresolved（T5）；`CONTEXT.md` profile 字段表同步
- [ ] 1.6 dev 侧注入面：`dev-agent.py --acceptance-tests <path> --acceptance-digest <sha256>`；注入测试 = recognized project test（红 → `test_failed` 拦发布）；注入件编辑计为 candidate change（进 TestEvidence 新鲜度门）
- [ ] 1.7 测试（先红）：dev 侧注入参数解析 + digest 不符 fail-closed（exit 非 0 带结构化原因）；注入件被编辑 → 证据新鲜度视为被破坏
- [ ] 1.8 `harden-verify-test-scope` 归并收尾：proposal 头加归并说明（指回本题 D7），目录移 `archive/2026-10-26-harden-verify-test-scope/`（0/9 未施的**吸收归并**非完工归档——归档动作经用户确认，本体论审核 C3）

## 2. verify agent 执行器 + 机械锚（design 阶段 2）

- [ ] 2.0 headless stream-json 冒烟（本体论审核 B3 前置）：`claude -p --output-format stream-json` 经 roc 代理跑最小 Bash 会话，证实事件流含 tool_use/tool_result 且 Bash result 带 exit code + stdout 结构；产出 fixture 锚定 2.2 解析器（仓内零先例，同 github MCP 冒烟惯例）
- [ ] 2.1 `scripts/verify_agent.py`：`VerifyExecutor` Protocol + `ClaudeHeadlessExecutor`（worktree 内 headless `claude -p --output-format stream-json --allowedTools "Bash,Read"`，编排器捕获原始流，会话墙钟上限沿用 VERIFY_*_TIMEOUT 量级）
- [ ] 2.2 三锚 + 证据落盘 + 完整测试报告：① stream 解析器——declared cmd shell 归一化前缀匹配 → 对应 tool_result **exit code + stdout**（stdout 落 `test_log`，行锚解析输入不断供，B1），匹配不到 → `unresolved`；② `git diff`/`--cached` 为空 + 非 ignored untracked ⊆ 注入集；③ 注入件逐文件 sha256 复核（`artifact_store.compute_digest`）；④ 原始 stream（或锚证据子集+digest）持久化 `state/runs/`（B2）；⑤ **完整测试报告**（两轨统一输出契约，机械组装零 LLM）：run 身份 + 命令来源与清单 + 逐命令 exit code/输出指针 + 锚检查或 rc + 判定与证据指针 + 会话元 + **环境奇偶节**（declared env spec vs transcript 观测实际组装，Bash 命令机械提取，v1 记录不阻断——T3），落 `state/runs/<stamp>.verify-report.json`，dispatch record 持路径（用户追加 2026-10-26）
- [ ] 2.3 测试（先红）：三锚各自绿样本 + 违反样本（改 tracked 文件 / 偷改注入件 / 命令不匹配 unresolved），断言 invalid 语义、record 详情与证据落盘存在；**报告完备性断言**——绿/红/invalid 三态各产完整 verify-report.json（身份/命令/exit code/锚或 rc/判定/证据指针/**环境奇偶节**字段齐 + record 持路径），机械轨同构一份；**超时映射断言**——会话墙钟超时判 invalid 非红（T4）
- [ ] 2.4 `independent_verify` flag 分支：`PA_VERIFY_AGENT`（`feature_flags.py`，默认 off）on → agent 轨（env spec + 验收测试机械拷入（编排器自己拷，非 agent）+ 全量门命令优先序 profile `verify_test_cmd` > dev hint > 跳过+note）；off → 现机械轨 byte-identical
- [ ] 2.5 测试（先红）：优先序三场景（profile 声明胜出 / 无声明用 hint / 皆无跳过+note 记来源）+ flag off 回归锚（现双轨行为不变）
- [ ] 2.6 graph 对称 + record：`graph_pa_verify` / `graph_pa_nodes` 直调面同改；dispatch record `note` 记命令来源（`profile` / `dev-reported` / `none`）+ 锚检查详情；agent 轨 `test_log` 沿既有面喂 pa-verify 与行锚解析；install 失败（无匹配 tool_result）→ invalid/triage 不进 revise 回环；会话墙钟超时同映射 invalid（非红，T4）；triage 理由 `acceptance_suite_defective`（收集级失败签名 + 确认）→ 触发重编写（新 digest 版本链）→ 重跑 verify，不烧 revise 预算（T2）

## 3. 门禁、canary 与文档

- [ ] 3.1 全量 `bash scripts/quality.sh` 绿 + `openspec validate agent-verify-injection` 过
- [ ] 3.2 `RUNBOOK.md` verify 小节补 agent 轨（flag / 三锚 / invalid-triage 语义 / 回滚 = flag off）；`CONTEXT.md` 术语表补 acceptance suite / 注入件 / 三锚；`CLAUDE.md` 三处（persona 表加 pa-test-author 行、「9 个 headless persona」计数更 10、per-agent-model-routing 节 persona 计数同步——本体论审核 C1）
- [ ] 3.3 canary：同 PRD 双跑对账（机械轨 vs agent 轨绿红分布 + 分歧样本清单），默认 `--dispatch-skip-dev` 零 outward 零 LLM 成本侧通道，agent 轨侧真跑一次 verify 会话作 release gate
