# Proposal: add-router-persona

## Why

hub↔人的交互面全是硬编码：`/pa` 子命令集（run/confirm/reject/metrics/bind/unbind）+
事件单向转发。人问「#110 为什么阻断」「今天跑了几单」「sp-a1b2 是什么」只能去翻面板/日志——
run 的结构化事实（dispatch json / gate_reason / 影子提案）已经全在 hub 里，缺一个**语义问答层**。
演进路线 P2-⑨（dsh Router 的 pa 翻译）：轻模型 persona 挂在 MM 交互点，把「人问 → 查数据 →
答事实」自动化。

**与 dsh Router 的关键分野（本体红线）**：dsh Router 是**调度者**（分派任务给 Worker）；
pa Router 是**解释者**（回答关于调度的问题）。调度权恒在确定性 hub——「不引入常驻 LLM
Router 接管调度」是路线图不采纳清单第一条，本 change 不越线一毫米。

## What Changes

1. **第 12 个 persona `.claude/agents/pa-router.md`**：只读问答角色——tools 仅 `Read`（限定
   hub 数据面路径），无 Bash 无 Write 无 Agent；模型走 `PA_PERSONA_MODEL_PA_ROUTER`
   （per-agent routing 既有面，建议 haiku 档）。
2. **hub 交互挂点**：MM 绑定频道的**非 slash 自然语言消息**（现被忽略）→ 机械预筛 →
   headless 调 router → 回帖频道。预筛全机械（D3）：消息含 `#数字`（run 引用）/ `sp-`（提案）/
   问句关键词（为什么/怎么/多少/状态）——零 LLM 判定是否调 router。
3. **上下文注入（机械组装）**：router prompt 头部带结构化事实块（hub 查询拼装）：被引用 run
   的 dispatch 摘要（status/gate_reason/skip_reason/dev_cost/turns）、活跃影子提案列表、
   今日队列计数——persona 只 Read 补细节文件（dispatch json 路径给它）。
4. **回答纪律**：persona 契约——只引事实（「dispatch 记录说…」），无事实就说「查不到 X」；
   行动建议只呈现**命令提示**（「可执行：/pa run …」）绝不代执行。回答 ≤ 200 词（频道礼仪，
   dsh 多信道差异化输出同款）。
5. **fail-open 全承**：router 调用失败/超时（wall-clock 60s）→ 静默跳过（无回帖）+ stderr——
   交互面绝不阻塞主链。

## Non-Goals

- 无行动权：不 enqueue/不 abandon/不改任何状态/不发确认——行动建议仅文字。
- 不做多轮对话记忆（每问独立会话——上下文由注入块供给）。
- 不替代 /pa 命令（slash 路径零变化——router 是旁路解释层）。
- 不做跨项目漫游（只答绑定频道对应项目 + 全局计数）。
- 不引入常驻会话池（每问起停，与 persona 面同形态）。
