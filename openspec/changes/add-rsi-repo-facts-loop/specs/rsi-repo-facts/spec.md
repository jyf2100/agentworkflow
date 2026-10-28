## ADDED Requirements

### Requirement: 教训分轨与仓事实候选提取

终态教训提取 SHALL 分轨：失败模式候选走既有 ≥2 PRD equivalence 晋升轨（零变化）；仓事实候选（repo_fact_candidate）SHALL 一次即候选——依据 curated evidence bundle 中可机械复核的证据（log 行/文件存在性/依赖清单），经新 schema 类产出；不可机械复核的候选 SHALL 转既有轨道。

#### Scenario: #76 形态 run 产出仓事实候选

- **WHEN** dev 因 PRD 指标与仓测试基建冲突 off_track 止损（evidence：dev 独白 log + off_track 判定 + PRD 指标节）
- **THEN** 提取产出 repo_fact_candidate（fact_class=test_infra，fact 引 package.json 与测试文件证据），进入人审，不等待第二次复现

#### Scenario: 轨道 1 零变化

- **WHEN** 终态提取产出的是偶发失败模式候选
- **THEN** 既有 equivalence 晋升/dev prompt 注入路径逐字节不变

### Requirement: 仓事实人审环

hub SHALL 经 `repo_fact_proposed` 事件将仓事实候选 post 进项目绑定频道（@admin 用法行：`/pa fact ok|no|merge`），admin 确认后写入 profile `repo_facts` 节（含 confirmed_by/confirmed_at/evidence_run 溯源，上限 12 条/项目）。人审前候选 SHALL NOT 进入任何 prompt 输入面。确认/拒绝幂等，72h 无决过期。

#### Scenario: 确认即沉淀

- **WHEN** admin 发 `/pa fact ok #91`
- **THEN** profile repo_facts 增对应条目（带溯源），下一次 PRD 生成的输入面即含该事实

#### Scenario: 人审前零注入

- **WHEN** 候选已提取但未确认
- **THEN** prd/critic/test-author/spec-author 的 prompt 面不含该事实

### Requirement: 上游语义链注入

已确认 repo_facts SHALL 机械拼接（零 LLM）进四个上游输入面：PRD 生成（MM wrap 与 radar 路径，节名「仓事实（验收指标须贴此现实）」）、critic 判据 prompt、test-author prompt、spec-author prompt。critic 判据 SHALL 增机械理由：PRD 验收指标与 repo_facts 直接冲突 → drop（理由引 fact key；B 环落地后可判 needs_metrics）。repo_facts SHALL NOT 改测试门/verify/发布/重试任何谓词。

#### Scenario: 冲突指标在闸口被拦

- **WHEN** repo_facts 含「无 DOM 测试库（test_infra）」而新 PRD 验收要求 jsdom computed style 断言
- **THEN** critic 以「指标与仓事实冲突（fact key）」drop/转 needs_metrics，run 不进 dispatch

#### Scenario: PRD 生成即贴现实

- **WHEN** 项目 repo_facts 非空且新需求入队
- **THEN** wrap 后的 PRD 输入含仓事实节，后续 critic/test-author 同节可见
