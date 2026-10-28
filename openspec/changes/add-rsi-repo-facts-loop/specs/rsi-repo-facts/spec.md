## ADDED Requirements

### Requirement: 教训分轨与仓事实候选提取

终态教训提取 SHALL 分轨：失败模式候选（含外部库/工具的通用行为）走既有 ≥2 PRD equivalence 晋升轨（零变化）；仓事实候选（repo_fact_candidate）SHALL 一次即候选——仅限**项目作用域内**的结构性事实（仓测试基建/依赖清单/风格约定），依据 curated evidence bundle 中可机械复核的证据（仓内 log 行/文件存在性/依赖清单），经新 schema 类产出；外部作用域或不可机械复核的候选 SHALL 转既有轨道。

#### Scenario: #76 形态 run 产出仓事实候选

- **WHEN** dev 因 PRD 指标与仓测试基建冲突 off_track 止损（evidence：dev 独白 log + off_track 判定 + PRD 指标节）
- **THEN** 提取产出 repo_fact_candidate（fact_class=test_infra，fact 断言「仓无 DOM 测试库」，evidence 引 package.json devDeps 与测试文件），进入人审，不等待第二次复现

#### Scenario: 外部库行为不入仓事实轨

- **WHEN** 终态证据中的教训是外部库/工具的通用行为（如「jsdom 不解析 CSS var()」），其成立不依赖本仓任何清单/文件/风格
- **THEN** 该候选 MUST NOT 产出 repo_fact_candidate，转轨道 1 走 ≥2 PRD 复现晋升

#### Scenario: 轨道 1 零变化

- **WHEN** 终态提取产出的是失败模式候选
- **THEN** 既有 equivalence 晋升/dev prompt 注入路径逐字节不变

### Requirement: 仓事实人审环与拦截审计

hub SHALL 经 `repo_fact_proposed` 事件将仓事实候选 post 进项目绑定频道（@admin 用法行：`/pa fact ok|no|merge|del`），admin 确认后原子写入 facts sidecar（`profiles/<name>.facts.yaml`，独立于 profile 主文件；含 confirmed_by/confirmed_at/evidence_run 溯源，上限 12 条/项目）。人审前候选 SHALL NOT 进入任何 prompt 输入面。确认/拒绝幂等，72h 无决过期。fact 条目 SHALL 维护拦截审计（blocked_count/last_blocked_run）；同一 fact_key 连续拦截达 2 次时 hub SHALL 自动 post @admin 复审提示。

#### Scenario: 确认即沉淀

- **WHEN** admin 发 `/pa fact ok #91`
- **THEN** facts sidecar 原子增对应条目（带溯源），下一次上游 prompt 构建即含该事实

#### Scenario: 人审前零注入

- **WHEN** 候选已提取但未确认（facts sidecar 无该条）
- **THEN** radar-PRD/critic/test-author 的 prompt 面与现状 byte-identical，不含该事实

#### Scenario: 拦截回流复审

- **WHEN** 同一 fact_key 关联的冲突拦截累计达 2 次
- **THEN** 频道收到 @admin 复审提示（附 blocked_count 与最近拦截 run 链），check-intake 同步可见

### Requirement: 上游注入与软出口判据

已确认 repo_facts SHALL 机械拼接（零 LLM）进三个上游输入面：radar-PRD 生成 prompt、critic 判据 prompt、test-author prompt（各尾部「## 仓事实（验收指标须贴此现实）」节）；spec-author prompt 为 B 环落地后的扩展点。critic 判据 SHALL 取软出口：PRD 验收指标与 repo_facts 直接冲突 → revise 打回（反馈附冲突 fact key 与仓事实原文；B 环落地后为 needs_metrics），SHALL NOT 因事实冲突终局 drop。MM wrap 与 panel wrap SHALL 保持纯机械零注入。repo_facts SHALL NOT 改测试门/verify/发布/重试任何谓词。

#### Scenario: 冲突指标被打回而非击毙

- **WHEN** repo_facts 含「无 DOM 测试库（test_infra）」而新 PRD 验收要求 jsdom computed style 断言
- **THEN** critic 以冲突反馈打回修订（引 fact key），PRD 重写后可再来，run 不死于终局 drop

#### Scenario: PRD 生成即贴现实

- **WHEN** 项目 repo_facts 非空且 radar 路径新 PRD 生成
- **THEN** PRD prompt 含仓事实节，生成的验收指标贴仓现实

#### Scenario: wrap 零污染

- **WHEN** 用户经 MM 提交人写 PRD
- **THEN** wrap 后正文为用户原文全文（零注入零增写），仓事实仅经 critic/test-author prompt 面生效
