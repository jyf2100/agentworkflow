# add-rsi-repo-facts-loop — tasks

> 顺序：A（add-e2e-acceptance-harness）→ B（add-acceptance-spec-confirmation）→ 本 change。
> 波次：第一波 = 1.x + 2.x + 3.1/3.2/3.3a/3.4（B 前可全做）；第二波 = 1.5d + 1.5b 判据升级 + 3.3b（B 落地后）。

## 1. pa 侧：仓事实候选提取与上游注入（轨道 2）

- [ ] 1.1 `learning_memory_schema.py`：新平行 dataclass `repo_fact_candidate` + 独立 parse 函数（纯增量，不动 LessonCandidate）；fact_key 派生对齐 `derive_equivalence_key` 的 canonical 规范化；作用域判据规则落地（外部库/工具行为 → 轨道 1 转轨）
- [ ] 1.2 facts sidecar 读面与 schema 契约定稿：`profiles/<name>.facts.yaml` 解析（条目含 fact/fact_class/confirmed_by/confirmed_at/evidence_run；上限 12 溢出 warn；文件缺失/解析失败 = 空事实集 fail-open）；**`load_profiles` 的 `*.yaml` glob 显式排除 `*.facts.yaml`（`lifecycle._profile_forges` 同规则）+ 单测锁死**——sidecar 顶层是 list，撞 `prof.get()` 直接 AttributeError 炸 inject 段——**本 task 定稿的文件契约是 hub 2.3 写侧的引用基准**
- [ ] 1.3 `learning_memory_reflection.py`：curated evidence bundle 增仓事实提取视角（dev 独白 log / off_track 判定 / verify-report 证据段）；提取 prompt 分轨指令（事实 vs 模式 + 项目作用域判据）（依赖 1.1）
- [ ] 1.4 `run_daily.py` 终态钩子：仓事实候选产出后 `append_event(..., "repo_fact_proposed", ...)`（event_outlet 事件为自由字符串**无需改动**——白名单在 hub 侧，见 2.2；`repo_fact_proposed` 与既有事件同走 locate_pa_artifact 管道）（依赖 1.1+1.3）
- [ ] 1.5a radar-PRD prompt（`run_daily.py` prd prompt 构建）尾追加「## 仓事实」节（repo_facts 全量机械拼入；依赖 1.2）
- [ ] 1.5b critic prompt 附 repo_facts 节 + 软出口判据文本：冲突 → revise 打回（反馈附 fact key 与仓事实原文；**额度条款**：消耗既有 revise 1 次额度，额度耗尽 round-2 仍冲突 → 终局 drop 但 summary 引 fact key + 冲突原文 = 终局非无声；**人确认不豁免**；**test_infra/dep_reality 冲突且超出已声明 harness 能力 → 反馈文案指向 profile 声明动作**（补 harness/e2e_dev_cmd 声明或改写指标），不自动改 profile）；B 环落地后在 B 四态文本基线上 diff 升级 needs_metrics（reason=fact_conflict + **critic 事件 payload 增结构化 `fact_keys` 字段**——B 契约同名字段，hub 拦截计数按此累计，第二波）（依赖 1.2）
- [ ] 1.5c test-author prompt（`run_daily.py _test_author_prompt`）尾附同节（纯 append，不碰 A 环定型的 harness 声明段）（依赖 1.2）
- [ ] 1.5d spec-author prompt 附节（**blocked-by: add-acceptance-spec-confirmation**，第二波；v1 不对不存在 persona 建注入面）
- [ ] （显式注记：MM wrap `hub/mm_intake.py wrap_mm_prd` 与 panel wrap `hub/api.py _wrap_panel_prd` 保持纯机械零注入，v1 不改）

## 2. hub 侧：人审环与事件链

- [ ] 2.1 `hub/db.py`：`fact_requests` 表（id/project/fact_key/payload_json/status CHECK(requested,confirmed,rejected,expired)/created_at/decided_at）+ 拦截审计字段（blocked_count/last_blocked_run）+ CRUD + expire_sweep 72h（B 环先落地则复用其 `request_decide` helper，sweep 合并扫两表）
- [ ] 2.2 事件消费**三处落点**（缺一整行被静默丢弃）：`hub/events.py` KNOWN_EVENTS 增 `repo_fact_proposed`；`hub/lifecycle.py _event_text` 增该事件分支（@admin + `/pa fact ok|no|merge #id` 用法行）；`_forward_events` 增 fact_requests 建行路径（事件幂等）
- [ ] 2.3 `hub/mm_intake.py`：`fact ok|no|merge|del` 子命令（**admin 判据复用 B 环先落地的 `_require_bind_admin` helper**；ok = 原子写 facts sidecar（tmp + os.replace，追加式块，schema 按 1.2 契约）+ 置 confirmed + **拦截审计字段（blocked_count/last_blocked_run）随确认迁入 sidecar 条目**（表行 confirmed 是状态机终态不可再累计）+ 频道回执；满 12 条回执提示先清；merge 改写后 fact_key 重派生；del = 删 sidecar 条目；**幂等判据按 fact_key 先行**——ok 重放时 sidecar 已有同 key 条目直接回执「已生效」不再写（防半程崩溃重放双条目，尤其 merge 改写后 key 重派生场景）；fact_requests 建行亦按 fact_key 幂等去重；过期语义同 B 环）
- [ ] 2.4 `hub/lifecycle.py` 终态补读：`_finish_run` 前对 run_dir 最后一次 `read_events` flush（`repo_fact_proposed` 是终态钩子产物，写完容器即退；若 fix-event-outlet-verify-parity 先落地且已建终态 flush 机制则复用）
- [ ] 2.5 拦截回流：按 critic 事件**结构化 `fact_keys` 字段**累计 blocked_count/last_blocked_run（非文本解析；无字段不计数）；「连续」= 同 fact_key 拦截序列无 fact 决策介入（任何 `fact ok/no/merge/del` 转移清零）；同 run 多轮 revise 计 1 次（按 run 去重）；连拦 2 次 → 频道 @admin 复审 post，**回执分叉**（fact 可疑 → 复审 evidence 链；fact 可信 → 复审需求与基建（含 v2 harness 立项建议））
- [ ] 2.6 `hub/cli.py` check-intake 增仓事实面（待决 fact 数 / sidecar 条数 / 各 fact blocked_count 与 last_blocked_run）

## 3. 测试与真机

- [ ] 3.1 pa 单测：分轨提取（#76 fixture → repo_fact_candidate「仓无 DOM 测试库」；「jsdom 不解析 var()」外部行为 → 轨道 1 零变化）/ fact_key 派生去重 / 作用域判据转轨 / **load_profiles glob 排除后 sidecar 在场不炸 inject 段** / 注入点机械拼接（**facts 空时三 prompt byte-identical 现状——人审前零注入不变式锁死**）/ critic 软出口判据文本（含额度条款终局形态 + 回执指向声明面文案）/ **已排队项身份连续**（fact ok 落 sidecar 后已排队项 deliver_key 不变、不触发重投；pending 项下一 run critic prompt 新鲜读 sidecar）/ 终态分布回归 fixture（repo_facts 存在与否不改变测试门/verify/发布谓词行为）
- [ ] 3.2 hub 单测：fact_requests 状态机全转移 / 事件幂等建行（含 confirm 后事件重复到达不重建行）/ KNOWN_EVENTS 增类后 read_events 不丢行 / ok 原子写 sidecar 溯源完整 + 解析失败容错 / 非 admin 拒 / 72h 过期 / 终态 flush 补读 / 满 12 回执 / merge 后 fact_key 重派生 / 拦截计数回流触发——FakeMMClient + tmp data_root
- [ ] 3.3a 真机坐实（第一波，独立可验收）：重放 #76 形态需求 → 候选进频道 → `/pa fact ok` → sidecar 落条 → 再发同指标冲突需求 → critic 以冲突理由 **revise 打回**（合法终态，不 drop）→ 连拦 2 次自动 @admin 复审——RSI 闭环（失败 → 学习 → 上游软拦截 → 反馈回流）一镜到底
- [ ] 3.3b 真机坐实（第二波，B 环后）：同场景 critic 判 needs_metrics 分支行为
- [ ] 3.4 RUNBOOK 新节：仓事实生命周期（候选→人审→注入→拦截审计→人工清理）+ `/pa fact` 用法 + 未绑定 MM 项目 requested 滞留说明 + 陈旧事实 del SOP + 拦截回流处置（**回执分叉两分支 SOP：fact 可疑走 evidence 复审；fact 可信走需求/基建复审——基建升级 commit 落地后人工核对并 `fact del` 清理对应 sidecar 条目**）；`bash scripts/quality.sh` + `bash hub/quality.sh` 双绿
