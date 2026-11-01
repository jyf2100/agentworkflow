## 本体论声明

**引用图** = lessons/repo_facts 间的结构关系（有向图：lesson A refs lesson B = A 引用 B 的经验）。**邻域** = 从某节点出发 depth 步内可达的子图。**中心度** = 节点在图中的被引次数（入度）。**Dormant** = 持续未被注入选中的降级态（非删除——同一性保持，只是不在默认注入面）。图的节点同一性 = lesson/fact 的 id（已有）；边的同一性 = (source_id, target_id) 对。

## Decisions

### D1 引用图边来源（机械提取，零 LLM）
- lessons frontmatter 增 `refs: [lesson-id-1, fact-key-2, acN-20261101]`（引用其他 lesson/fact/PRD 验收点）
- repo_facts frontmatter 增 `refs: [lesson-id]`（仓事实支撑哪些经验）
- 图构建 = 启动时全量扫 frontmatter 建邻接表（进程内缓存，lessons 写入时增量更新）

### D2 Top-K 选择算法（机械）
```
score(lesson) = in_degree × 2 + keyword_match(prd, lesson) × 3
top_k = sorted(candidates - dormant, key=score, reverse=True)[:k]
```
keyword_match = PRD 正文含 lesson 的 trigger 词列表中的任一词（机械匹配）。in_degree = 图中被引次数。

### D3 修剪（dormant 降级）
每次 dispatch 后：本轮未被选中的 lesson 的 `miss_count += 1`；`miss_count ≥ 10` → dormant。
被引用图邻域命中的 dormant lesson 自动复活（miss_count 清零，回 active）。

### D4 冲突对（文本相似度机械判据）
两条 lessons 的 refs 含同一验收点 id 且 `action` 字段首 20 字相同但后文不同 → 标记 conflicts_with。注入时冲突对只选中心度高的那条（另一条 stub 里注「与 L-x 矛盾，见 [展开]」）。

### D5 注入面升级
lessons 注入从 `all_lessons` 改为 `graph.top_k(prd_text, k)`——结果仍是 markdown 块（`build_prompt` 消费面不变），只是内容从全量变 Top-K。`PA_LESSONS_TOP_K` 可配（0 = 全量注入 baseline）。

## Rollout
flag `PA_MEMORY_GRAPH`（默认 off=全量注入 byte-identical）；启用 = hub.env PA_* 透传链。

## Open Questions
- 图中心度在小样本（<20 lessons）时区分度低——v1 先跑，数据够后加 PageRank
- repo_facts 与 lessons 的 refs 关系是否双向（fact ref lesson 但 lesson 不 ref fact）——v1 单向
