## 0. 勘察
- [ ] 0.1 build_prompt 五通道注入点精确定位；lessons/handoff/feedback 的 frontmatter schema 确认（引用图提取源）

## 1. 实现
- [ ] 1.1 hub/context_loader.py：stub 渲染（D1 规则）+ 预算管理（D2 优先级截断）+ 引用图（D3 机械标注）
- [ ] 1.2 flag PA_CONTEXT_STUB（feature_flags 域切割，默认 off）+ dispatch 侧接线（_dev_cmd artifact 装配层）
- [ ] 1.3 单测：stub 渲染/预算截断/引用链/flag 关 byte-identical

## 2. 验证
- [ ] 2.1 真机：flag 开 → dev prompt 含 stub+展开标记 → dev Read 展开 → run_log 有展开路径记录
