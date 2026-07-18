# 0005 — 报告（report）是编排器机械 stage，不立 pa-report persona

## 决定

报告段落地为 `scripts/run_daily.py:stage_report` ——**纯控制面机械聚合**：读 4 份 state JSON（`candidates_`/`prd_manifest_`/`prd_gate_`/`dispatch_<stamp>.json`）→ 按 §8 七节模板渲染 `项目推进/项目推进报告_<stamp>.md` + 日报指针 + 有活则 SMTP 直发。**不建 `pa-report` persona**，不调 `claude -p`。

## 背景

SPEC §0 / §9 把「报告」列为流水线第 5 个 persona（`pa-report`）。但报告的本质是**把已落盘的 state 结构化数据搬进固定 markdown 模板**——零语义、零判断、零外部信息抽取：

- 数据源全是编排器自己产的 JSON（无新信息要"读/抽/提炼"）。
- 模板固定（§8 七节），无自由文本生成。
- 判定（待 review 绿 / failing / drop / 有活即发）全是确定性的字段比较。

这与 Phase-3 dispatch 的处境完全同构：SPEC §0 也把 dispatch 列为 persona，落地时因"全机械无语义"改为编排器 stage（[[ADR-0003]] 链路下，dispatch 的对账/验证是确定性逻辑，不立 persona）。report 沿用同一先例。

## 考虑过的替代

- **立 `pa-report` persona（按 SPEC §0 原文）**：拒绝。persona = 调 `claude -p --agent` = 花钱、有延迟、非确定性。把确定性模板渲染交给 LLM 是负 ROI：既增加 cron 每日成本与不稳定性，又得不到任何"人写不出"的产物（模板是写死的）。唯一可能的价值是 LLM 生成「洞察/建议」段落，但 v1 报告不需要（人 review PR 时自带判断），需要时再增量加一个 persona 段落即可，不必把整个报告段 persona 化。
- **报告里加 LLM 洞察段（混合）**：推迟。v1 全机械够用；日后真要洞察，单独加 `pa-insight` persona 产洞察段、`stage_report` 把它拼进报告即可（增量、不推翻本决定）。

## 后果

- report 段**零成本、确定性、cron 友好**——每日 03:17 跑全流程出报告不发钱（仅 radar/prd/critic 三段花 claude，dispatch 触发 dev-agent 花目标仓 SDK）。
- SPEC §0 的「5 persona」表述与实现有偏差：**实为 3 headless persona（radar/prd/prd-critic）+ 2 编排器 stage（dispatch/report）**。SPEC §9 Phase 5 已回写澄清。
- 若日后 slug 算法 / state schema / 报告模板变更，只改 `stage_report` 一处（无 persona prompt 要同步）。
- SMTP 直发仍是 report 段职责（`_smtp_notify` 调 `smtp_send.py`），但**真发邮件是外发动作**：自测 / cron 首跑前由用户终端写 Keychain + 跑 `--self-test`（不擅自发）。

## 关联

- SPEC §8（报告格式）/ §9 Phase 5（已回写）/ §10（SMTP 直发已结案）。
- 先例：Phase-3 dispatch 同样从 persona 改为 stage（[[ADR-0003]]、[[ADR-0004]]）。
- 代码：`scripts/run_daily.py:stage_report` / `_append_daily_pointer` / `_smtp_notify` / `scripts/smtp_send.py` / `scripts/run_cron.sh` / `scripts/install_cron.sh`。
