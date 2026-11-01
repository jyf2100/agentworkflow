# P1/P2 观测数据回收报告（2026-11-01）

## 一、已确认项（全部有真机证据）

| # | 观测项 | 证据 | 状态 |
|---|---|---|---|
| 1 | 影子提案产出 | sp-79f43019（CI 连红 10 次，锚=streak 首 run） | ✅ |
| 2 | 影子提案采纳追踪 | PRD frontmatter `shadow-proposal:sp-79f43019` → digest 报「已采纳 1」 | ✅ |
| 3 | 订阅直通落盘 | fake 人话 → `state/human_notes/qi-112/1793534746255_testpost001.md`（谱系目录 qi-112 ✓） | ✅ |
| 4 | 谱系化（重派不断链） | notes 在 `qi-112/` 而非 run 特定目录——换 run_dir 不丢 | ✅ |
| 5 | 直通回执 | 频道回帖「已记入 run #125 下一轮上下文：1 条」 | ✅ |
| 6 | Router persona 回答 | 宿主机验证：正确回答「#112 状态 running，project cc-web-control」 | ✅ |
| 7 | L5 探活（确定性死链） | roc 面 qwen3.6-35b 名册在+8896 死 → 连续 3 轮 UNKNOWN → 第 3 轮升级漂移 | ✅ |
| 8 | L5 探活（健康面） | 正常消费面 R3=ok（qwen3.8-27b-fast 探活通过） | ✅ |
| 9 | 漂移看门狗四规则 | 心跳 `R1=ok｜R2=ok｜R3=ok｜R4=无 running`——全绿 | ✅ |
| 10 | 运维日报 | 队列/池/影子提案/漂移四节全出（含采纳计数） | ✅ |
| 11 | 注意力视图 | `/panel/attention` 四节渲染（提案节含 sp-79f43019，徽标带数） | ✅ |
| 12 | 镜像自动分发 | v0.1.38 探针红→自动 save|load→复测绿→183 就位（~6 分钟） | ✅ |
| 13 | CI 修复（sp-79f43019 人审采纳实践） | 7302d34 push 后 CI `completed/success`（10 连败→绿） | ✅ |

## 二、设计缺口（1 项）

| # | 缺口 | 影响 | 处置 |
|---|---|---|---|
| G1 | Router 在 hub thin 容器内无 claude CLI | `router_relay.answer_question` 的 subprocess 调 claude 超时 | 需改直连 API（httpx 到 roc 代理）或 hub 镜像装 CLI——列 follow-up change `fix-router-api-direct`，不阻塞 P3 |

## 三、待真实流量验证（2 项——单测已覆盖，真机等自然触发）

| # | 观测项 | 触发条件 | 单测状态 |
|---|---|---|---|
| 1 | auto-triage 首秀 | dev 绿测后又发生候选写（test_stale） | 4 测试全绿（重验=新证据 fresh 构造成立） |
| 2 | Router 从 hub 容器回答 | 修 G1 后 | 5 测试全绿（预筛/注入块/预算闸/互斥/失败静默） |

## 四、#112（adopt-sp79）run 行为

入队 → dispatch 到 183 → **PRD 改名**（hub wrap 后 slug 变 `xiufu-ci-resume-from-sp-79f43019`）→ critic pass → test-author 3 文件 0 uncovered → **dispatch 超额跳过**（在途 1≥1 single-flight）→ run #125 终态 `succeeded`（容器 exit 0）。**注**：PRD evidence 的 `shadow-proposal:sp-79f43019` 在 wrap 后进入 PRD frontmatter `source:` 字段——采纳追踪链路闭合（digest 已报 1）。

## 总裁定

**13 项确认 + 1 设计缺口（router CLI）+ 2 项待自然触发**——P1/P2 全链路行为与设计一致，观测数据足以支撑 P3 决策（影子命中率已有首个数据点 1/1=100%——样本太小，但链路已闭合可采集）。

**P3 可以开工。**

---

## 附录：P3 实测报告（2026-11-01 补）

### 模块级验证（全部通过）

| # | 项 | 证据 | 状态 |
|---|---|---|---|
| P3-①-1 | context_loader stub 渲染 | `flag on: True` + `含 [展开: True` + `含 ## 📋: True`（hub 容器直调） | ✅ |
| P3-①-2 | 预算截断 | `PA_CONTEXT_BUDGET=50` → 仅 PRD 存活（单测 + hub 容器验证） | ✅ |
| P3-①-3 | flag 关 byte-identical | `flag off → render_stub 返回空串` → dev-agent 走全量路径 | ✅ |
| P3-②-1 | effects 表迁移（旧库自动建表） | `effects 表: True`（真机 db 打开即建） | ✅ |
| P3-②-2 | mm_notify Effect | `mm_notify done payload={"action": "run_finished", "run_id": 126}` | ✅ |
| P3-③-1 | memory_graph 图构建 + Top-K | `graph nodes: 3 | top_k: ['L1', 'L2']`，关键词 `readme` 命中 L1 | ✅ |
| P3-③-2 | 邻域 + 冲突对 | `L1 邻域: {'L2', 'L1', 'L3'}` + `conflicts: []` | ✅ |

### 未完成验证（被 dispatch 面阻断）

| # | 项 | 阻断原因 | 状态 |
|---|---|---|---|
| P3-①-4 | dev prompt 含 stub 标记（真实会话） | 两次入队均被 single-flight 跳过（在途 1≥1——dispatch 面问题与 P3 无关） | ⏸ 模块级已过 |

### 新发现

| 级别 | 发现 | 处置 |
|---|---|---|
| P2-8 | effects 缺 state_write（容器启动）包裹——编辑时在途文件文本不匹配导致断言静默失败 | 列 follow-up `add-effects-state-write-wrap` |
| P2-9 | dispatch 面连续 skip「跳过-超额」——无在飞 run 仍判超额（single-flight 锁误判？） | 独立问题与 P3 无关，列 follow-up 排查 |
