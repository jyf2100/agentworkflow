# verification-ontological-audit — agent-verify-injection（提案态，2026-10-26）

方法论沿用 `archive/2026-10-22-add-hub-slice-2a` 四维检查。对象是**提案态** change（0/17 实施），
「生产可达性」维度换为「**从既有代码基的可构造性**」：delta spec 声称的行为能否用仓内既有实体
（模块/机制/词汇）构造出来，不需发明未验证的新地基。

## 一、正面确认

1. **实体类别干净**：acceptance suite = 控制面 durable 工件（content-addressed，镜像 handoff /
   artifact_store 既有类别）；verify worktree 注入副本 = digest 锚定的目标面临时投影（编排器拷入，
   类别同既有 worktree 创建，不违 ADR-0001）；三态 verify 结果（green/red/invalid）正确扩展既有
   pass/test_rc 词汇，invalid→triage 与 dispatch 既有 `triaged` 状态衔接，未发明孤立状态。
2. **「需求注入」零新实体**：PRD 验收点稳定 id 既有且机械可抽（`run_daily.py:809`，`{slug}:ac{n}`，
   verify-loop-discipline B3 落地）——D2 直接复用真源。
3. **MODIFIED requirement 证据化语义与 TestEvidence 本体一致**：注入测试「红拦发布」是
   evidence-based（执行过 + exit code≠0 → `test_failed`）而非 presence-based，与
   `evaluate_gate`（evidence.py:51）判定序完全同构；识别通道 `is_clean_test_cmd`
   （dev-agent.py:419）既有，dev 侧零新机制。
4. **digest 门单源模式沿 B1 教训**：编排器持有注入 digest，消费点（dev 启动 / verify 跑后）只对账
   不二次判——无双源漂移面。
5. **渐进迁移惯例符合**：flag 默认 off + graph 对称 + off 回归锚 + canary，与 PA_GRAPH_*/PA_LOOP_* 同构。

## 二、Findings

### B1（跨 spec 契约断链）agent 轨未指定 test stdout → test_log 通道

主 spec `verified-dev-execution`「Line-anchored verify feedback」（本题未改）要求从 independent
verify 输出机械解析 failing test 的 file:line——解析对象是 `test_log`（干净测试 stdout，
`run_daily.py:3446`）。本题锚①只提取 declared cmd 的 **exit code**；agent 轨如何产出可解析的
runner 输出（test_log）在 specs/design/tasks 三处均未指定。后果：agent 轨一切红路径系统性降级为
`unresolved` 锚（spec-legal 但属基线契约在新轨的整体性退化——revise 反馈失锚）。
**修复**：D5 锚①扩展为「exit code + 该 tool_result 的 stdout 落 test_log（保留 runner 原始输出）」；
tasks 2.2/2.6 同步。

### B2（证据实体类别违例）判定证据源（transcript）持久化未指定

机械轨的绿证据是持久化的（test_log 落 state/runs + dispatch record）；agent 轨的判定证据 =
编排器捕获的原始 stream——**进程内存态**。invalid triage / 事后审计 / crash 复核时无可回看对象，
违反本仓证据实体必须 durable 可复核的类别原则（journal/test_log/runtime_evidence 同类）。
**修复**：D5 加第四件落盘——原始 stream（或锚证据子集 + digest）落 state/runs 与 test_log 同目录。

### B3（未验证地基）headless `--output-format stream-json` 仓内零先例

D4 整个锚①建立在 headless `claude -p --output-format stream-json` 产出含 exit code 的
tool_use/tool_result 事件流上。全仓 grep 零先例（persona 走 `json` 信封；SDK 流在 prompt_stream
另路）。仓内有 headless 能力差异前车（github MCP 冒烟证不可用改走 gh CLI）。**修复**：tasks 组 2
前置冒烟任务（headless -p + stream-json 经 roc 代理，验证事件含 Bash tool_result 的 exit code /
stdout 结构），锚①解析器以冒烟产物 fixture 锚定。

### C1（文档面遗漏）CLAUDE.md 不在 tasks 3.2

新 persona pa-test-author 使 persona 数 9→10；CLAUDE.md「9 个 headless persona」计数、persona 表、
per-agent-model routing 节「persona（8 个）」（本已陈旧）三处都需更——tasks 3.2 只列了
RUNBOOK/CONTEXT。**修复**：3.2 补 CLAUDE.md 三处。

### C2（flag 交互未注明）shadow parity / canary 须 flag-off

PA_VERIFY_AGENT=on 会把 LLM 非确定性注入 dispatch 终态分布——graph cutover 的 shadow parity
canary（matched 判定）若带此 flag 跑会莫名 matched=False。D6 未注明。**修复**：D6 加一句
「shadow parity / canary 一律 PA_VERIFY_AGENT=off 跑」（与 --dispatch-skip-dev 零 LLM 原则同源）。

### C3（归档门禁惯例）task 1.8 归档 0/9 未施 change 须用户确认

opsx:archive 对未完成任务有确认门；harden-verify-test-scope 是 0/9 吸收归并，task 1.8 应注明
「归档动作经用户确认（吸收归并非完工归档）」。**修复**：task 1.8 补一句。

## 三、裁决记录

用户裁决（2026-10-26，AskUserQuestion）：**全部修复**（B1-B3 修进工件 + C1-C3 随手带上）。

已落：
- B1 → design D5 锚①扩为「exit code + stdout 落 test_log」+ tasks 2.2/2.6 同步
- B2 → design D5 新增第 ④ 项证据落盘（原始 stream 持久化 state/runs）+ tasks 2.2/2.3 覆盖
- B3 → design D4 注明零先例 + tasks 新增 2.0 前置冒烟（fixture 锚定解析器）
- C1 → tasks 3.2 补 CLAUDE.md 三处
- C2 → design D6 补「shadow parity / canary 一律 flag-off」
- C3 → tasks 1.8 注明吸收归并须经用户确认

修复后 `openspec validate agent-verify-injection` 复验通过。

**审核后用户追加（2026-10-26）**：「测试完成要有完整的测试报告」——落为 verify 阶段**两轨统一的输出契约**：
delta spec ADDED requirement 加场景「Complete structured test report is produced on every run」（绿/红/invalid
三态必产 + dispatch record 持路径）；design D5 第 ④ 项扩为证据落盘 + 完整报告（字段清单，机械组装零
LLM，pa-verify / triage / report 共消费）；tasks 2.2 第 ⑤ 项 + 2.3 报告完备性断言（三态 × 两轨）。
