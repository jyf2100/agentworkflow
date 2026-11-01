# Proposal: add-long-term-memory-graph

## Why

cross-prd-learning-memory 已有 8 模块（lessons 晋升 ≥2 PRD 复现 + 注入 dev prompt），但注入面是**平铺列表**——lessons 之间、lessons 与 repo_facts 之间没有结构关系。演进路线 P3-⑬（dsh「跨月长程记忆」的 pa 翻译）：把 lessons + repo_facts 升级为**引用图**（谁引用谁、冲突对、时效性修剪），使 dev prompt 的 lessons 注入从「全部候选」到「图邻域的 Top-K」。

**与 add-context-loading-protocol 的关系**：loading-protocol 管「怎么注入」（stub/预算）；本 change 管「注入什么」（图邻域选择）。两者正交可独立启用。

## What Changes

1. **引用图存储**：lessons + repo_facts 的 frontmatter 增 `refs:` 字段（引用其他 lesson/fact/PRD 验收点的 id 列表）——图边从 frontmatter 机械提取。
2. **图查询面**（`hub/memory_graph.py`）：`neighborhood(lesson_id, depth=1)` 返回引用邻域；`top_k(prd_keywords, k=3)` 按图中心度 + PRD 关键词匹配选 Top-K lessons。
3. **注入升级**：lessons 注入从「全部晋升 lessons」到「图邻域 Top-K」（k 可配 `PA_LESSONS_TOP_K` 缺省 3）。
4. **修剪策略**：连续 N 个 PRD（缺省 10）未被选中的 lesson 降级为 `dormant`（不删——保留在 catalog，可被引用图重新激活）。
5. **冲突对检测**：两条 lessons 的 refs 指向同一验收点但建议矛盾（文本相似度机械判据）→ 标记 `conflicts_with` 互引。

## Non-Goals

- 不做向量嵌入/语义检索——v1 用关键词匹配 + 图中心度（机械）。
- 不删 lessons（dormant 只是降级，不是删除——审计链不断）。
- 不改 lessons 晋升门槛（≥2 PRD 复现——已有 spec 不动）。
- 不做跨项目 lessons 共享（v1 项目作用域内）。
