## MODIFIED Requirements

### Requirement: 上下文按需加载（stub + 展开 + 预算截断）

dev prompt 的注入面（PRD / lessons / acceptance bundle / handoff / feedback）SHALL 支持 stub 化加载：每注入物在 prompt 中以 ≤80 字摘要 + 引用链标注 + `[展开: <path>]` 标记出现，dev 可按需 Read 展开全文。stub 总量 SHALL 受 `PA_CONTEXT_BUDGET`（缺省 4000 token 近似）约束，超限按优先级（PRD > 验收 > feedback > handoff > lessons）从低到高截断。引用关系 SHALL 从既有 frontmatter 机械提取（零 LLM）。flag `PA_CONTEXT_STUB` 默认 off（off = 全量注入 byte-identical）。verify 面注入 MUST NOT stub 化（考卷不可截断）。

#### Scenario: dev prompt 含 stub 与展开标记
- **WHEN** flag 开且有 lessons + acceptance 注入
- **THEN** prompt 含各注入物 stub（摘要+引用链+[展开: path]）而非全文；dev Read 展开路径可在 run_log 追溯

#### Scenario: 预算截断从低优先级开始
- **WHEN** stub 总量超预算
- **THEN** lessons 先截（stub 变一行截断标记+路径），PRD 最后截；截断项展开标记保留

#### Scenario: flag 关全量注入
- **WHEN** PA_CONTEXT_STUB 未设或 falsy
- **THEN** 注入面与现状 byte-identical（全量拼贴）
