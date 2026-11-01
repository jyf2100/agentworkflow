## 0. 勘察
- [ ] 0.1 lessons frontmatter 现有 schema（trigger/action/refs 字段面）；repo_facts sidecar schema；
        catalog 存储形态（文件 vs db）

## 1. 实现
- [ ] 1.1 hub/memory_graph.py：图构建（frontmatter 扫描）+ neighborhood + top_k + dormant 降级/复活 + 冲突对
- [ ] 1.2 lessons 注入面升级：all → top_k（PA_LESSONS_TOP_K 可配 0=全量）
- [ ] 1.3 flag PA_MEMORY_GRAPH（默认 off）+ dispatch 接线 + RUNBOOK 节
- [ ] 1.4 单测：图构建/邻域/top_k/修剪/冲突对/flag 关 byte-identical

## 2. 验证
- [ ] 2.1 真机：flag 开 → lessons 注入仅 Top-K → dev prompt 确认非全量
