# 运行时产出物完备性本体论审核（agent-verify-injection 落地后）

- 日期：2026-10-28
- 对象：pa 运行时产出物（工件 / 记录 / 事件 / 接缝四层）对「运营运维」消费面的完备性
- 背景：agent-verify-injection 已落地（a725364），verify agent 轨 flag `PA_VERIFY_AGENT` 默认 off、真跑零次（tasks 3.3 留发布时）；hub 集中形态已上线跑通（#72 / PR #59）
- 方法：domain-modeling 本体论纪律——先立产物范畴全景（谁写 → 谁读 → 生命周期），再按消费方分面判完备性，逐断层给出本体归因

## 产物本体全景（设计态）

| 层 | 产物 | 写者 → 读者 | 生命周期 |
|---|---|---|---|
| 工件 | acceptance suite（content-addressed：digest/criteria_ids/uncovered/supersedes） | pa-test-author → dev/verify 双注入 | supersedes 版本链，新版下次 dispatch 生效 |
| 工件 | 注入件（worktree 内机械拷入） | 编排器 → 三锚③ | rel path 白名单 + digest |
| 工件 | verify-report（`pa.verify-report.v1`：命令来源/逐命令 exit/三锚/env 奇偶） | verify_stream → dispatch record 持路径 | 落 state/runs/<stamp>.verify-report.json |
| 工件 | verify-stream 原始流（`<log>.verify-stream.jsonl`） | executor → 排障人 | 原始证据 |
| 工件 | 考卷快照 digest（iteration 不变式，1.9） | dispatch → verify 比对 | 同 iteration 同 digest |
| 记录 | dispatch record（22 键：+pr_number/pr_url/verify_report） | dispatch → report/reconcile | 每 PRD |
| 事件 | pa journal（acceptance stored/absent/defective、iteration_snapshot/handoff、invalid 分流、merge 群） | 各段 → recovery_cli/审计 | journal-driven 崩溃恢复 |
| 接缝 | events.jsonl + report 双写（`PA_REPORT_DIR`） | pa → hub（唯一语义接缝） | 单向文件，未知行跳过 |

## 判定

**内部闭环完备**：gate 判定 / 崩溃恢复 / supersedes / 考卷不变式 / reconcile 的写、读、失效、半途崩溃全有归属，本体自洽。**roc 本机运维够用**（RUNBOOK 10.3 有 flag 语义 + 两级证据路径）。

**两处本体断层（hub 集中形态运营不够用）**：

### F1 invalid 三态在接缝处退化成二值
控制面内部语义 green/red/**invalid**（unresolved / 锚 violation / 墙钟超时；invalid→triage 升人工不 revise），但 `pa-event-outlet` 契约 events.jsonl 事件枚举仅 `critic_verdict / pr_opened / verify_verdict` 三类，verify_verdict 按 pass/fail 二值设计。agent 轨转 on 后 hub 面板/通知看不到「为什么 invalid」，运维须 SSH 进 state 卷读 verify-report.json。**三态本体在接缝处未完整投射。**

### F2 acceptance suite 状态零出口
stored/absent/defective 事件只在 pa journal（内部），events.jsonl 无对应出口——hub 侧不知道「该 PRD 无验收套件保护就 dispatch 了」或「套件疑似缺陷被换版」。同族：verify-report.json 不在 report 双写（PA_REPORT_DIR 只覆盖 report 段产物，verify-report 是 dispatch 段落的），hub 面板 run 详情够不着。

### 次级缺口
- RUNBOOK 10.3 教「是什么」，缺「invalid 第一步做什么」排障 playbook。
- 「当前生效 digest」查询面缺失（需 jq journal；暂按 YAGNI 不立项）。

## 结论

- 现阶段（flag off + roc 本机）：够用。
- agent 轨 on + hub 集中形态叠加时：F1/F2 同时爆发。两者尚未叠加（真跑零次），现在补契约最便宜。

**行动**：立 change `fix-event-outlet-verify-parity`（events.jsonl 枚举扩 invalid 语义 + acceptance 状态事件出口 + verify-report hub 面板可达；RUNBOOK invalid playbook 顺带）。

## 会话存档

本审核的完整工作会话 transcript：`Knowledge/sessions/项目推进流水线/2026-10-28-1aba3b81-runtime-artifacts-ontology-audit.jsonl`（gitignore 内，含历史敏感片段不入仓；会话存档按项目挂专门子目录）。
