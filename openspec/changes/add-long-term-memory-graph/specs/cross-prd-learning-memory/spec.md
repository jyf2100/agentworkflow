## ADDED Requirements

### Requirement: 长程记忆引用图（邻域 Top-K + 修剪 + 冲突对）

lessons 与 repo_facts SHALL 构成引用图（frontmatter `refs` 字段为边，机械提取零 LLM），支持：邻域查询（depth 步可达子图）、Top-K 选择（中心度×2 + 关键词匹配×3，`PA_LESSONS_TOP_K` 缺省 3、0=全量注入 baseline）、修剪（连续 10 个 PRD 未选中 → dormant 降级，被邻域命中自动复活）、冲突对检测（同验收点引用 + action 前 20 字同但后文异 → 标记互引，注入时选中心度高者）。dormant 项 MUST NOT 删除（审计链不断，只是不在默认注入面）。flag `PA_MEMORY_GRAPH` 默认 off（byte-identical）。

#### Scenario: Top-K 注入按图选
- **WHEN** flag 开且 lessons 池有 10 条、PRD 含某 lesson 的 trigger 词
- **THEN** 注入仅 Top-K 条（该词匹配的 lesson 优先 + 高中心度补充），非全量

#### Scenario: dormant 降级与复活
- **WHEN** 某 lesson 连续 10 个 PRD 未被 top_k 选中
- **THEN** 降级 dormant（不注入）；后续 PRD 的引用图邻域命中它时自动复活

#### Scenario: 冲突对选高中心度
- **WHEN** 两条 lessons 引用同一验收点但建议矛盾
- **THEN** 注入选中心度高的那条，另一条以「与 L-x 矛盾」注记

#### Scenario: flag 关全量注入
- **WHEN** PA_MEMORY_GRAPH 未设
- **THEN** lessons 注入与现状 byte-identical（全部晋升 lessons）
