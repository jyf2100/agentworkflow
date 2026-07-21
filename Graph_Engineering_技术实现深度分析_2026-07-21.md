# Graph Engineering 技术实现深度分析

> 分析来源：Peter Steinberger (@steipete) Twitter 讨论
> 分析时间：2026-07-21
> 分析人：roc

---

## 一、核心架构组件

### 1. 状态管理（State Management）

#### LangGraph 实现

```python
from typing import TypedDict, Annotated
import operator

# 状态定义：每个字段是一个 Channel
class AgentState(TypedDict):
    messages: Annotated[list, operator.add]  # Append reducer
    iterations: int                          # Overwrite reducer
    draft: str                               # Simple overwrite

# 状态图构建
workflow = StateGraph(AgentState)
```

**关键机制**：
- **Channels（通道）**：每个状态字段对应一个通道，带可选 Reducer 函数
- **Reducers**：定义状态如何更新（`operator.add` 追加、`lambda x,y: y` 覆盖）
- **Partial Updates**：节点返回部分状态，自动合并

#### Claude Code 实现

```yaml
# .claude/agents/code-reviewer.yaml
---
name: code-reviewer
description: Reviews code for quality
tools: Read, Glob, Grep
model: sonnet
isolation: worktree        # 文件系统隔离
memory: project            # 跨会话记忆
---
# 系统提示词（隔离的上下文）
```

#### GraphBit 三层内存架构

```
┌─────────────────────────────────────┐
│  Tier 1: Ephemeral Scratch Space    │
│  临时计算空间，节点内部使用           │
│  生命周期：单次节点执行               │
├─────────────────────────────────────┤
│  Tier 2: Structured State           │
│  结构化工作流状态                     │
│  生命周期：整个图执行                 │
├─────────────────────────────────────┤
│  Tier 3: External Connectors        │
│  外部数据源连接                       │
│  生命周期：持久化                     │
└─────────────────────────────────────┘
```

---

### 2. 路由机制（Routing）

#### LangGraph 三种边类型

| 边类型 | 实现方式 | 适用场景 |
|--------|----------|----------|
| **静态边** | `add_edge("a", "b")` | 确定性顺序执行 |
| **条件边** | `add_conditional_edges("node", router_fn)` | 动态分支 |
| **命令路由** | `Command(goto="target", update=...)` | 节点内决定下一跳 |

#### 条件边示例（ReAct 模式核心）

```python
def should_continue(state: AgentState) -> str:
    last_msg = state["messages"][-1]
    # LLM 决定是否调用工具
    if last_msg.tool_calls:
        return "tools"      # → 工具节点
    return END              # → 结束

workflow.add_conditional_edges("agent", should_continue, {
    "tools": "tool_node",
    END: END
})
```

#### Claude Code 子代理路由

```typescript
// AgentTool.ts 核心逻辑
interface AgentToolInput {
  prompt: string;
  agent_type?: "Explore" | "Plan" | "general-purpose";
  isolation?: "worktree" | "remote" | "in-process";
  allowed_tools?: string[];
  run_in_background?: boolean;
}

// 三层路由轴：
// 1. Teammate Routing → 选择子代理类型
// 2. Isolation → 选择隔离模式
// 3. Lifecycle → 同步/异步/后台
```

#### GraphBit 引擎编排路由

```rust
// Rust 引擎确定性路由
// 优势：零幻觉路由，无无限循环
dag.execute(|state| {
    if state.predicate("review_passed") {
        Route::To("deploy")
    } else {
        Route::To("revise")
    }
})
```

---

### 3. 执行模型（Execution）

#### LangGraph Pregel 消息传递

编译后生成 Pregel Graph：
- **节点触发**：当输入通道收到写入时触发
- **消息传递**：节点产生状态更新和路由写入
- **并行执行**：独立分支同时运行

执行流程：
```
START → Channel Write → Node Fire → State Update → Route Write → Next Node
```

#### Claude Code 执行模型

```python
# AsyncGenerator 流式执行
async def query_loop():
    while True:
        # 1. 构建请求（state → message）
        request = build_request(state)

        # 2. LLM 推理（可能产生 tool_use）
        response = await llm.generate(request)

        # 3. 执行工具
        if response.tool_calls:
            results = await execute_tools(response.tool_calls)
            state = update_state(state, results)
        else:
            # 无工具调用 = 完成
            break

        yield StreamEvent(state)
```

#### GraphBit DAG 执行

```
拓扑排序 → 并行分支识别 → 独立路径并发执行
           ↓
    ┌──────┴──────┐
  Branch A     Branch B
    │              │
    └──────┬──────┘
           ↓
      Merge Results
```

---

### 4. 持久化与检查点（Checkpointing）

#### LangGraph 检查点系统

```python
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.sqlite import SqliteSaver

# 开发环境：内存检查点
memory = MemorySaver()

# 生产环境：Postgres 持久化
checkpointer = PostgresSaver.from_conn_string(
    "postgresql://user:pass@localhost/db"
)

# 编译时注入
app = workflow.compile(checkpointer=checkpointer)

# 多轮对话：thread_id 关联状态
config = {"configurable": {"thread_id": "session-001"}}
result = app.invoke(state, config=config)  # 自动加载/保存
```

#### Claude Code 持久化机制

| 机制 | 实现 | 用途 |
|------|------|------|
| **Worktree** | `git worktree add` | 文件系统级隔离 |
| **Transcript** | 独立日志文件 | 审计追踪 |
| **Memory** | `CLAUDE.md` + 技能 | 跨会话知识 |
| **Channels** | 事件总线 | 运行时通信 |

---

### 5. 隔离机制（Isolation）

#### Claude Code 三层隔离

| Isolation Level | Filesystem | Context | Cost |
|-----------------|------------|---------|------|
| In-process      | Shared     | Isolated | Low   |
| Worktree        | Copy       | Isolated | Med   |
| Remote          | Remote     | Isolated | High  |

#### 权限覆盖规则（Cascading）

```
Parent: bypassPermissions → 子代理无法覆盖
Parent: acceptEdits     → 子代理继承
Parent: auto            → 子代理继承
Subagent: explicit mode → 仅在 Parent 未显式设置时生效
```

---

## 二、多代理编排模式实现

### 1. Supervisor 模式（Claude Code）

```
用户请求
    │
    ▼
┌─────────────┐
│  Supervisor │ ← 主会话，保持全局上下文
│  (Claude)   │
└──────┬──────┘
       │ Task 工具
   ┌───┴───┐
   ▼       ▼
Agent A  Agent B
(Explore) (Write)
   │       │
   └───┬───┘
       │ 返回摘要
       ▼
┌─────────────┐
│  Supervisor │ ← 合并结果，决定下一步
│  (继续)     │
└─────────────┘
```

**实现细节**：
- 最多 **10 个并行子代理**
- 子代理仅返回**摘要**，不污染父上下文
- Worktree 隔离防止代码冲突

### 2. LangGraph 工作流图

```python
# 研究 → 写作 → 评审 流水线
workflow = StateGraph(State)

# 节点定义
workflow.add_node("research", research_node)
workflow.add_node("write", write_node)
workflow.add_node("review", review_node)

# 边定义
workflow.add_edge(START, "research")
workflow.add_edge("research", "write")
workflow.add_edge("write", "review")

# 条件循环：评审不通过返回重写
workflow.add_conditional_edges(
    "review",
    lambda s: "approve" if s["score"] > 8 else "revise",
    {"approve": END, "revise": "write"}
)

app = workflow.compile()
```

### 3. 嵌套图（Subgraph）

```python
# 外层图
outer = StateGraph(OuterState)
outer.add_node("plan", plan_node)

# 内层图作为节点
inner = StateGraph(InnerState)
inner.add_node("research", research_node)
inner.add_node("write", write_node)
inner.add_edge("research", "write")

# 内层图编译后作为外层节点
outer.add_node("execute", inner.compile())
outer.add_edge("plan", "execute")
```

---

## 三、关键技术对比

| 维度 | LangGraph | Claude Code | GraphBit |
|------|-----------|-------------|----------|
| **图定义** | Python DSL | YAML + 提示词 | DAG 配置 |
| **执行引擎** | Pregel (Python) | Node.js AsyncGenerator | Rust |
| **状态持久化** | Checkpointer (内置) | Worktree + 文件 | 三层内存 |
| **路由控制** | 条件函数 | LLM 自主决定 | 引擎确定性 |
| **并行度** | 图分支并行 | 10 子代理 | DAG 分支并行 |
| **隔离级别** | 无（共享进程） | Worktree/Remote | 内存隔离 |
| **延迟开销** | ~100ms | 网络延迟 | 11.9ms |
| **适用场景** | 通用工作流 | 代码开发 | 企业合规 |

---

## 四、生产级实现要点

### 1. 状态设计原则

```python
# 好的状态设计：分离关注点
class GoodState(TypedDict):
    # 1. 业务数据
    user_query: str
    research_results: list

    # 2. 控制数据（路由用）
    current_step: Literal["research", "write", "review"]
    retry_count: int

    # 3. 审计数据
    execution_trace: Annotated[list, operator.add]
    timestamps: dict
```

### 2. 错误恢复模式

```python
# LangGraph 错误恢复
workflow.add_node("try", try_node)
workflow.add_node("fallback", fallback_node)
workflow.add_node("retry", retry_node)

workflow.add_conditional_edges(
    "try",
    lambda s: "success" if not s["error"] else "fail",
    {"success": "next", "fail": "fallback"}
)

# 重试计数器防止无限循环
workflow.add_conditional_edges(
    "retry",
    lambda s: "continue" if s["retry_count"] < 3 else "giveup",
    {"continue": "try", "giveup": END}
)
```

### 3. 人在回路（HITL）

```python
# LangGraph 原生支持
app = workflow.compile(
    checkpointer=checkpointer,
    interrupt_before=["approval_node"]  # 暂停等待人工
)

# 恢复执行
app.invoke(None, config=config)  # 从检查点继续
```

---

## 五、性能优化

| 优化点 | 方法 | 效果 |
|--------|------|------|
| **上下文裁剪** | 仅传递必要状态字段 | 减少 token 消耗 |
| **并行执行** | 独立分支同时运行 | 线性加速 |
| **检查点策略** | 关键节点才保存 | 降低 I/O 开销 |
| **子代理摘要** | 返回压缩结果 | 防止上下文膨胀 |
| **缓存** | 工具结果缓存 | 避免重复调用 |

---

## 六、选型决策树

```
是否需要多 Agent 协作？
    │
    ├─ 否 → 单 Loop（Claude Code /goal, Codex goal）
    │
    └─ 是 → 工作流是否复杂？
              │
              ├─ 简单（2-3 步）→ Claude Code Skills 链式调用
              │
              └─ 复杂 → 需要状态持久化？
                          │
                          ├─ 否 → CrewAI / AutoGen
                          │
                          └─ 是 → 需要审计合规？
                                      │
                                      ├─ 否 → LangGraph
                                      │
                                      └─ 是 → GraphBit
```

---

## 七、未来趋势

1. **技能组合（Deeper Skill Composition）**
   - `/release` → `/test` → `/commit` → `/push` 确定性流水线

2. **更强隔离**
   - 从 Worktree → 进程级 → 网络级隔离（Cloudflare Sandbox）

3. **Agent 间协议**
   - A2A 标准 + MCP-over-network，跨厂商协作

4. **动态工作流**
   - Claude Code Workflows：JavaScript 脚本编排数十个子代理

---

## 相关阅读

- [Loop Engineering vs Graph Engineering](https://www.aibuilderclub.com/blog/graph-engineering-vs-loop-engineering)
- [Graph Engineering: After Loops, This Is How You Wire Multi-Agent Orgs](https://explainx.ai/blog/graph-engineering-ai-agents-multi-agent-organizations-2026)
- [LangGraph Tutorial: Build Stateful AI Agents in Python](https://realpython.com/langgraph-python/)
- [Claude Code Subagent Docs](https://code.claude.com/docs/en/sub-agents)
- [GraphBit Paper](https://arxiv.org/abs/2605.13848)
