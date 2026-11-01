## 本体论声明

**注入物（injection artifact）** = 已落盘的、可被 dev prompt 引用的持续物（PRD/lessons bundle/acceptance bundle/handoff/feedback artifact）。**Stub** = 注入物的「前 80 字 + 引用链 + 展开标记」的 prompt 内替身。**预算** = stub 总量的 token 上限（机械截断，非语义判断）。三者范畴分明：注入物是源实体（continuant），stub 是其在 prompt 内的投影（派生），预算是投影面的机械约束——绝不改源实体。

## Decisions

### D1 stub 渲染规则（≤80 字 + 引用链 + 展开标记）
```
## 📋 PRD（完整验收标准）
→ 验收点 acN × 3 ｜ ← lessons L2 L5
[展开: /opt/pa/.project-auto/state/prd/proj/20261101_x.md]
前 80 字摘要……
```
标题行 + 引用链行 + 展开行 + 摘要。引用链标注上下游（`→` 引出 / `←` 被引）。

### D2 预算管理（优先级截断）
优先级：PRD > 验收 bundle > feedback > handoff > lessons（与 build_prompt 现有顺序对齐）。
超预算时从最低优先级截断（截断项 stub 变 `[预算截断: <name>——全文在 <path>]`，展开标记保留）。

### D3 引用图（机械标注，非语义推理）
引用关系来源：
- lessons bundle 的 `criterion_ids` 字段 → PRD 验收点 id
- handoff 的 `prior_iteration` 字段 → 上轮 diff stat
- feedback 的 `round` 字段 → PRD 的 revise 历史
全部从既有 frontmatter/schema 机械提取——零 LLM 推理。

### D4 与 HelixHarness 合流点
helix `reference/loading-protocol.md` 的核心思想（上下文按需加载 + 索引驱动）已在 P0 落为 pa 原生的 stub+展开。v1 不装 helix 运行时（纯 skills 快照），v2 若装则两条协议合流为一条。

### D5 dev-agent 零改动
stub 渲染在 dispatch 侧（run_daily `_dev_cmd` 的 artifact 装配层）完成——`--human-notes`/`--lessons-artifact` 等参数照传全文路径，但 prompt 构造层（`build_prompt`）消费的是 stub 化后的内容。dev-agent 只看到 stub+`[展开: path]`，按需 Read。

## Rollout
flag `PA_CONTEXT_STUB`（feature_flags，默认 off=全量注入 byte-identical）；启用 = hub.env PA_* 透传链。

## Open Questions
- token 计数用什么（v1 用字符数/4 近似——中文 token 比率不同，列 v2 校准）
- dev 实际展开率数据怎么采集（dev Read 展开时的 path 进 run_log 已有——分析即可）
