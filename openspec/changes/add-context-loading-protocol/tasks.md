## 0. 勘察
- [x] 0.1 build_prompt 五通道注入点精确定位；lessons/handoff/feedback 的 frontmatter schema 确认（引用图提取源）

## 1. 实现
- [x] 1.1 hub/context_loader.py：stub 渲染（D1 规则）+ 预算管理（D2 优先级截断）+ 引用图（D3 机械标注）
- [x] 1.2 flag PA_CONTEXT_STUB（feature_flags 域切割，默认 off）+ dispatch 侧接线（_dev_cmd artifact 装配层）
- [x] 1.3 单测：stub 渲染/预算截断/引用链/flag 关 byte-identical

## 2. 验证
- [ ] 2.1 真机：flag 开 → dev prompt 含 stub+展开标记 → dev Read 展开 → run_log 有展开路径记录

## 实施证据（2026-11-01）

- 0.1：build_prompt 五通道定位（dev-agent.py:671-830）：PRD 全文 L784 / source L785 / feedback L686+ / human_notes L695+ / handoff L702+ / acceptance L716+ / lessons L756+；lessons schema = trigger/corrective_action/non_applicability/lesson_id（learning_memory_schema.py）
- 1.1：hub/context_loader.py（双投影：hub/ + scripts/ 同步拷贝）：stub 渲染（≤80 字+[展开: path]+引用链）+ 预算截断（PRIORITY 高→低分配，PRD 永不截）+ 引用链机械提取（D3 正则）；flag PA_CONTEXT_STUB 默认 off
- 1.2：feature_flags context_stub 键 + dev-agent build_prompt 接线（flag 开 → prd_text 替换为 stub 块 + 各注入 block 清空；acceptance 通道不截断——考卷不 stub 化 spec scenario）
- 1.3：test_context_loader 5 测试（flag 关空返回/stub 渲染含展开标记/极小预算仅 PRD 存活/80 字截断/引用链提取）；hub/quality.sh 绿 + pa quality.sh 绿
- 2.1 真机：列观测项（下次真实 run flag 开后 dev prompt 含 [展开:] 标记 + run_log 有展开路径）
