# dev 阶段 verify+commit 闭环 — 设计纪要

> 状态：**设计共识已定**（grilling 2026-07-18），待实施。
> 动机：今早 cron 暴露 dev-agent 两种失败模式，用户提议加"验证+提交"独立 agent 形成闭环。经 grilling + 重投实证，设计已收敛。

## 1. 背景与现状

现状（dispatch 段，`scripts/run_daily.py`）：
- **dev-agent**（目标仓内 `scripts/dev-agent.mjs` / `dev-agent.py`）：写代码、自报 `test_cmd`、自开 PR。
- **`independent_verify()`**：全新 worktree 重跑 `npm test` / 重放 dev 上报的 `test_cmd`；红→标 failing，PR 留着不关。
- **`reconcile_pr()`**：有 PR 录入 / 有 commit 无 PR 补 `interrupted_pr` / 无 commit 删孤儿。
- **merge 永不**（`branch-only`，ADR-0002 不可谈刹车）。

两个痛点（2026-07-18 cron 实测）：
- **cc-web-control**：dev stall（写不过全量测试），无 commit、分支被删、没 PR、verify 根本没机会跑。
- **baostock**（PR #1）：dev 产 4 个 commit 但**没开 PR、没报 test_cmd** → `interrupted_pr`、verify 因 `test_cmd=null` 跳过。

## 2. 实证教训（决定设计走向，关键）

**重投实验**（2026-07-18，往 PRD 末尾手工追加"已知坑"节后重投 cc-web-control）：

| | 首投 | 重投（带已知坑 PRD 节） |
|---|---|---|
| 轮次 | 84 | **202** |
| 结果 | stalled | **stalled** |
| commit | 无 | 无 |
| cost | $7（回传） | **None（未回传）** |

- 反馈写得再细，glm-5.2 dev-agent 仍 stall、无 commit。
- **结论：根因是 glm-5.2 在大全栈任务（协议+持久化+查询+UI+测试）上的能力上限，非反馈质量。** verify+commit 闭环救不了"任务大到模型写不过"——判红重做 N 次，仍是 N 次 stall。
- **budget 回传 bug 实锤**：`dev-agent.mjs` 未回传 cost → `_run_one` 解析 `script_json.get("cost")=None` → 预算刹车失效，202 轮仅靠 `maxTurns` 兜底。

## 3. 已锁定设计决策

1. **`pa-verify` persona**（控制面新增）：裁判 + 书记员 + 法警定位，语义审核 dev 产出。
   - 判绿 → **兜底开 PR 收尾**（治 baostock 式 interrupted_pr）。
   - 判红 → **反馈写进 PRD** → 打回增量重做。
2. **增量重做**：第 1 次 `--base master`；判红第 2 次 `--base <上次dev分支>`。纯控制面换参（`_run_one` L772/774 已传 `--base`），**目标仓零改动、不越界 ADR-0002**。
3. **2 次机会**：判红时**保留 dev 分支做下次 base**（现状 stalled/orphan 才删，需改为"判红待重做不删"）；2 次用满降级 `interrupted_pr`（**不 drop**，dev 半成品可能有价值）。
4. **反馈形态**：醒目独立节追加进原 PRD 末尾（`## ⚠️ 审核反馈（verify 第N轮·非需求变更，未重过 critic 闸）`），四要素：①红的定位（文件/测试/断言行）②红的原因 ③该怎么改（可执行）④收尾门（全量 `npm test` 绿才算过）。**不重新过 critic 闸**（反馈是施工指引、非需求变更）。
5. **PRD 拆分前置**（实证后新增）：`pa-prd` 控制单 PR 规模，大需求拆成 N 个**小到 glm-5.2 能过**的 PRD。**verify 闭环只对匹配模型能力的小任务有效**——大任务不拆，谁来 verify 都救不了。
6. **merge 仍 `branch-only`**（不变）。

## 4. 与 ADR 的关系

- **不违 ADR-0002**（`docs/adr/0002-pipeline-project-contract.md`，项目自治）：控制面只裁判 + 反馈进 PRD，**不改项目代码/测试**；拆分是 PRD 层面的规模控制，非 boundaries 强加。
- **增量扩展 ADR-0005**（`docs/adr/0005-report-mechanical-not-persona.md`，dispatch/report 机械不立 persona）：dispatch 多了语义 verify 循环；但 ADR-0005 自留口子——"日后真要洞察，单独加 `pa-insight` persona...增量、不推翻本决定"。本设计正属此类增量，不推翻 ADR-0005。

## 5. 实施清单（带代码定位）

1. **止血·修 budget 回传**（赶下次 3:17 cron）：
   - `dev-agent.mjs`（目标仓）吐 cost → `run_daily.py` `_run_one`（L755+）解析 `script_json` 的 cost → 预算刹车生效。
   - 现状：`dispatch_20260718.json` `dev_cost=None, dev_turns=202`。
2. **建 `pa-verify` persona**：
   - 工具：`Read`（读 dev worktree 的 `git diff` + 红测试输出）；可能加 `Grep`/`Glob`。
   - 契约：一行 JSON `{verdict: pass|revise, round: N, feedback_section: "..."}`。
   - 参考：`run_persona`（L216，已 patch tolerant JSON 提取）调用模式；`stage_critic` revise loop（L392-408）作为 **dev revise loop 的同构模板**。
3. **改 `stage_dispatch`**（L990）：✅ **已实施**（2026-07-18，dispatch_one 内 verify 闭环循环 + 辅助函数；单测 `scripts/test_verify_loop.py`）
   - 接 `pa-verify`：dev 完成 → pa-verify 审核。
   - 判红：保留分支（不删）+ 反馈追加进 PRD + 第 2 次 `--base=<上次分支>` 重投。
   - 判绿：兜底开 PR（把 `reconcile_pr` 的"补开 interrupted_pr"升级为正常收尾）。
   - 用满：降级 `interrupted_pr`。
   - 落地选型（§6-① 已定）：**dispatch 内子循环**（同构 stage_critic revise loop）。reconcile 顺位后移到「裁定后收尾」——不预先为中间红的分支补开 PR；`reconcile_pr` 加 `interrupted` 参（False=verify 绿开正常 PR / True=红或异常开 ⏸ 中断 PR）；`independent_verify` 落干净 `.testout` + `_dump_branch_diff` 落 diff，喂 pa-verify Read。
4. **`pa-prd` 加单 PR 规模上限 + 拆分**：
   - 判据待定（验收标准条数？跨层——协议/UI/测试？涉及文件数？）。
   - 超限 → 拆成 N 个独立 PRD，各自走 inject→critic→dispatch→verify。

## 6. 仍待定（实施时定，不影响主干）

- ~~`pa-verify` 放独立 stage（`dispatch→verify→report`）还是 dispatch 内子循环？~~ → **已定：dispatch 内子循环**（item 3 已实施）。
- 拆分判据的具体阈值？
- `pa-verify` 工具集边界（`Read` 够不够）？
- 反馈节追加是否需要 `dev-agent.mjs` 侧配合（识别"这是反馈节、在 base 上接着改而非从零"）？

## 附：关键位置速查

- **run_daily.py**：`stage_dispatch` L990 / `independent_verify` / `reconcile_pr` / `_run_one` L755+ / `run_persona` L216（已 patch）/ `stage_critic` revise loop L392-408 / dev cmd `--base` L772,774
- **ADR**：`docs/adr/0002`（项目自治）/ `0003`（目标 dev-agent SDK）/ `0005`（report 机械不 persona）
- **state**：`.project-auto/state/dispatch_<stamp>.json` / `state/prd/<project>/` / `state/runs/<project>/`
- **实证数据**：`dispatch_20260718.json`（cc-web-control stalled, 202 轮, dev_cost=None）；`dispatch_20260717m.json`（baostock interrupted_pr, 4 commit, test_cmd=null）
