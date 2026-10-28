# Design: add-rsi-repo-facts-loop

## Context

cross-prd-learning-memory 已落地：终态后 curated evidence bundle → 结构化 candidate（enum schema）→ append-only 历史 + 可重建 catalog → **≥2 PRD byte-equal equivalence 晋升** → dev prompt 注入（≤5 条）+ 有效性反馈（置信/取代/退役）→ fail-open（不改任何判定谓词）。8 个 `learning_memory_*.py` 模块全离线可测。三次真机 run（#74/#75/#76）证实：教训只到 dev，上游语义链（prd/critic/test-author）无仓事实输入面，指标错配在源头无法拦截。

## Goals / Non-Goals

**Goals**：教训分轨——repo_facts 仓事实轨（一次即候选 + 人审生效 + 上游注入）落地；轨道 1 现状零触碰；软出口 + 拦截回流保证环可收敛（错误事实有自然退出信号）。
**Non-Goals**：轨道 1 策略、面板人审、facts 自动衰减、B 环本体。

## Decision: D1 教训类型学分轨 + 作用域判据（本体核心）

| | 轨道 1 失败模式 | 轨道 2 repo_facts |
|---|---|---|
| 本体 | 行为模式（偶发、跨仓可通用，含外部库/工具行为） | **项目作用域内**的结构性事实（「仓无 DOM 测试库」「测试纯函数风格」） |
| 置信来源 | 复现确证（≥2 PRD equivalence） | 单次可验证事实 + 人审拍板 |
| 生效速度 | 慢（等复现） | 快（人审即生效） |
| 注入面 | dev prompt（既有） | **上游语义链**（radar-PRD/critic/test-author） |
| 风险面 | 低（dev 提示性） | 高（影响 critic 判据）→ 人审强制 + 软出口 + 拦截回流 |

**作用域判据**（评审 A1）：事实断言的主语必须是本仓的清单/文件/风格（evidence 可指向 package.json devDeps、具体测试文件、仓内配置）；**外部库/工具的通用行为**（如「jsdom 不解析 CSS var()」——是 jsdom 的行为不是本仓的事实）MUST NOT 产出 repo_fact_candidate，转轨道 1 走复现。#76 的正确仓事实是「仓测试基建无 DOM 测试库」（evidence：package.json devDeps 无 jsdom/happy-dom + test_*.cjs 纯函数风格）——仅此一条即可拦住「jsdom 断言」类指标，无需引用任何库行为。

错配诊断（#76 根因）：仓事实被塞进失败模式轨 → 等 2 次复现 → 上游永远不知道。分轨后各自走各自的确证与生效路径。

## Decision: D2 轨道 2 schema：repo_fact_candidate

复用 candidate 提取管道（curated evidence bundle 同源），新平行 dataclass + 独立 parse（`learning_memory_schema.py` 现状单类无 kind 判别字段，纯增量）：`{"kind":"repo_fact", "project":..., "fact_class": enum("test_infra"|"dep_reality"|"convention"), "fact": "≤200 字可执行陈述", "evidence": [run 链引用], "affects": enum(...), "proposed_entry": {...}}`。fact 必须**可机械复核**（evidence 指向仓内 log 行/文件存在性/依赖清单）；不可复核或作用域在外部工具的 → 轨道 1。机械面：fact_key 由 (project, fact_class, fact 规范化) 派生，规范化风格对齐既有 `derive_equivalence_key` 的 canonical 形式；merge 改写后 fact_key **重派生**（防改写绕过去重）。`affects` v1 = 纯元数据，注入不按其过滤（注入全量机械拼入，保持零 LLM 纯机械性）。

## Decision: D3 人审环 + facts sidecar 写面

- hub 收 `repo_fact_proposed` 事件 → 频道 post @admin（HUB_PANEL_ADMINS——仓事实是项目级知识，admin 拍板）→ `/pa fact ok|no|merge|del #<id>`（del 亦用于陈旧清理）。
- **facts sidecar 文件**（评审 R4）：确认后写 `profiles/<name>.facts.yaml` **独立文件**，不动 profile 主文件——hub 侧唯一既有 profile 写面是建档一次性字符串模板（projects.py），运行期改写主文件是开无先例新写面且 `yaml.dump` 回写会重排键序/丢注释、毁「落盘键集逐字节一致」回归基线。sidecar 写面：读-改-走 tmp 文件 + `os.replace` 原子换名，追加式块写入；pa 读面按需合并。条数上限 12/项目（FIFO 满则回执提示先清）。条目带 `confirmed_by/confirmed_at/evidence_run` 溯源（该三字段 schema 契约在 tasks 1.2 定稿，hub 写侧引用同一契约）。
- **拦截审计回流**（评审 B1/B2）：fact 条目带 `blocked_count/last_blocked_run`；同一 fact_key 连续拦截达 2 次 → 频道自动 post @admin 复审（附计数与最近拦截 run 链）；check-intake 展示。这是错误事实的自然退出信号——否则事实拦掉的 PRD 永不跑，永无反驳证据（环自密封）。
- 状态机与 B 环共享：fact_requests 表结构 requested/confirmed/rejected/expired + 72h 过期 + 幂等与 confirmation_requests 同构；B 环先落地则复用其 `request_decide` helper 与 expire_sweep（合并为一 sweep 扫两表），避免双表双状态机 DRY 漂移。

## Decision: D4 上游注入点（pa 侧三处 + B 后一处，机械拼接零 LLM）

- **radar-PRD prompt**（`run_daily.py` prd prompt 构建）：尾部追加「## 仓事实（验收指标须贴此现实）」节。
- **critic prompt**：附 repo_facts 节；判据增软出口理由——PRD 验收指标与 repo_facts 直接冲突 → **revise 打回**（反馈附冲突 fact key 与仓事实原文；B 环落地后为 needs_metrics），**SHALL NOT 终局 drop**。PRD 打回重写后会再来，环不密封；人从 revise 反馈可见冲突，可改写指标或 `/pa fact no` 推翻事实。
- **test-author prompt**：附同节（纯 append，不碰 harness 声明段——A 环先行定型的面）。
- **spec-author prompt**：B 环落地后扩展（blocked-by: add-acceptance-spec-confirmation；v1 不对不存在 persona 建 SHALL 面）。
- **不注入**：MM wrap（`hub/mm_intake.py wrap_mm_prd`）与 panel wrap（`hub/api.py _wrap_panel_prd`）保持纯机械零注入——wrap 是 deliver_key sha 的输入，注入 = hub 对人写 PRD 正文的语义增写，污染 provenance 审计面，且与 critic prompt 注入双源冗余、事实冻结在入队时刻；**dev** 不注入（它自己读仓，事实冗余）。

## Decision: D5 fail-open 边界（软出口版）

repo_facts 注入是**语义输入面扩展**，非谓词变更：冲突的出口是 revise/needs_metrics（critic 既有非终局态），不是终局 drop——与 cross-prd-learning-memory「MUST NOT change terminal predicates」对齐（终局 drop/测试门/verify/发布/重试谓词零触碰）。人审前零生效（候选只进 MM 事件流，不进任何 prompt；facts sidecar 不存在/条目未确认时，注入点输出 byte-identical 现状——有单测锁死）。错误事实的纠错力来自三个通道：revise 反馈人可见、拦截计数回流 @admin、人工 del。

## Decision: D6 事件链完整性（hub 侧三落点 + 终态补读）

评审 R2/R3：事件白名单**在 hub 侧**——`hub/events.py KNOWN_EVENTS`（现状 frozenset 四类），`read_events` 对未注册行**静默丢弃**；pa 侧 `event_outlet.py` 无类枚举（事件是自由字符串，append_event 直写即可，无需改 pa 侧）。因此 tasks 2.2 必须三处落点：

1. `hub/events.py` KNOWN_EVENTS 增 `repo_fact_proposed`；
2. `hub/lifecycle.py _event_text` 增该事件分支（@admin + `/pa fact ok|no|merge #id` 用法行）；
3. `_forward_events` 增 db 建行路径（事件幂等）。

**终态补读**：`reconcile_running` 现状见容器 exited → 直接 `_finish_run`，无最后一次事件 flush；`repo_fact_proposed` 是终态 reflection 钩子产物，写完容器随即退出——不补读则事件永不被转发（RSI 环静默死链）。决策：`_finish_run` 前对 run_dir 做最后一次 `read_events` flush（自带最小实现；若 fix-event-outlet-verify-parity 先落地且已建终态 flush 机制则复用）。

## Decision: D7 实施顺序与波次

**A（e2e harness）→ B（spec-confirmation）→ RSI**。理由：RSI 1.5c 与 A 同文件（A 先改 harness 段、RSI 纯 append）；critic 判据段基线（B 四态文本）、mm_intake 子命令 dispatch、请求状态机 helper、expire_sweep 合并点均在 B 手里定契约。RSI 内部两波：

- **第一波（B 前可全做）**：1.1/1.2/1.3/1.4 + 1.5a/1.5b(revie 软出口)/1.5c + hub 2.1-2.5 + 3.1/3.2/3.3a/3.4
- **第二波（B 落地后）**：1.5d（spec-author 附节）+ 1.5b 判据升级 needs_metrics + 3.3b

## Risks / Trade-offs

- **错误仓事实进 sidecar**（人审看走眼）→ 溯源链 + 软出口（PRD 打回不死）+ 拦截计数回流复审 + 人工 del。
- **fact 陈旧**（仓后来加了 jsdom）→ blocked_count/last_blocked_run 审计可见 + `/pa fact del` 清理 + RUNBOOK SOP；自动失效检测 v2。
- **admin 不在 MM** → v1 显式限制：仓事实轨仅 MM 绑定项目生效；未绑定项目候选在表内 requested 滞留至过期，check-intake 可见（RUNBOOK 注记）。
- **sidecar 并发**（hub 写 × pa 容器读）→ 单文件 tmp+os.replace 原子换名；pa 读面容错（解析失败 = 空事实集，fail-open）。

## Migration Plan

纯增量：新事件类（hub 侧注册）/ facts sidecar 新文件 / pa 三 prompt 机械拼接 / MM 子命令。无 flag（未确认事实零注入，最坏 = 现状）。

## Open Questions

（三专家评审 2026-10-28 已收敛——本版为评审修订版；残余 open：拦截回流阈值 2 次是否合适，真机 3.3a 坐实后回调。）
