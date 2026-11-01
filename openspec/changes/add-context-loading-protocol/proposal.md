# Proposal: add-context-loading-protocol

## Why

dev prompt 的上下文注入面当前是「整块拼贴」：PRD 全文 + lessons 全文 + 验收测试 bundle + handoff 全文 + feedback 全文——每次 dev 会话的 prompt 可达数千行。演进路线 P3-⑪（dsh「上下文当 Cache 管理」的 pa 翻译）：常用直接内联、长文截断留续读入口、超预算 Spill 出窗口。与 HelixHarness 的 loading-protocol 合流（整合第一步已落地）。

**与既有注入面的关系**：不是新注入通道——是既有五通道（PRD/lessons/acceptance/handoff/feedback）的**加载策略升级**，从「全量注入」到「stub + 按需展开」（链式召回的 pa 形态）。

## What Changes

1. **stub 渲染层**（`hub/context_loader.py`）：每注入物在 prompt 中以 ≤80 字 stub 出现（标题+摘要+`[展开: <path>]` 标记），dev 可按需 Read 展开全文。
2. **预算管理**（`HUB_CONTEXT_BUDGET_TOKENS` 缺省 4000）：五通道 stub 总量超预算时，按优先级截断（PRD > 验收 > feedback > handoff > lessons）。
3. **引用图**：注入物间的引用关系（lessons 引用 PRD 验收点、handoff 引用上轮 diff）以 `→` 链式标注在 stub 头——dev 顺链展开。
4. **与 HelixHarness 合流**：helix skills 的 `loading-protocol.md` 思想（按需加载协议）反向吸收为 pa 原生能力——不依赖 helix 运行时。

## Non-Goals

- 不改 dev-agent 的工具白名单（dev 已有 Read 可展开）。
- 不做向量检索/语义排序——v1 按优先级+预算的机械截断。
- 不动 verify 面注入（verify 的 bundle 注入保持全量——考卷不可截断）。
- 不做跨仓 Workspace 级共享缓存（dsh Inline&Spill 的完整形态——先做 dev 会话内的按需展开）。
