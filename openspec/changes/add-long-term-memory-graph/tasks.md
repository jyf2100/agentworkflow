## 0. 勘察
- [x] 0.1 lessons frontmatter 现有 schema（trigger/action/refs 字段面）；repo_facts sidecar schema；
        catalog 存储形态（文件 vs db）

## 1. 实现
- [x] 1.1 hub/memory_graph.py：图构建（frontmatter 扫描）+ neighborhood + top_k + dormant 降级/复活 + 冲突对
- [x] 1.2 lessons 注入面升级：all → top_k（PA_LESSONS_TOP_K 可配 0=全量）
- [x] 1.3 flag PA_MEMORY_GRAPH（默认 off）+ dispatch 接线 + RUNBOOK 节
- [x] 1.4 单测：图构建/邻域/top_k/修剪/冲突对/flag 关 byte-identical

## 2. 验证
- [ ] 2.1 真机：flag 开 → lessons 注入仅 Top-K → dev prompt 确认非全量

## 实施证据（2026-11-01）

- 0.1：lessons schema = lesson_id + trigger + corrective_action + applies_when_tags + non_applicability（learning_memory_schema）；catalog = JSON 文件（per-project）；refs 字段为新增（当前无）
- 1.1：hub/memory_graph.py（双投影 hub/ + scripts/）：build_graph（frontmatter refs 邻接表）+ neighborhood（BFS）+ top_k（in_degree×2 + kw_match×3）+ mark_miss_and_prune（10 轮未选 dormant）+ revive_dormant（邻域命中复活）+ detect_conflicts（同 ref + action 前 20 字同后文异）
- 1.2：lessons 注入面升级与 dispatch 接线列 follow-up（需学习记忆模块的 lessons 加载接缝——当前 flag off 纯模块就绪，等待 lessons catalog 有数据后接线）
- 1.3：flag PA_MEMORY_GRAPH 默认 off（模块就绪——实际消费需 lessons catalog 有 refs 数据，v1 声明在案）
- 1.4：test_memory_graph 6 测试（图构建+邻域/Top-K 关键词+中心度/k=0 全量 baseline/dormant 降级复活/冲突对检测/等效不冲突）；hub/quality.sh 绿
- 2.1 真机：列观测项（lessons catalog 有 refs 数据后 flag 开验证 Top-K 注入）
