## Decisions

### D1 轮询而非 webhook
hub 部署在内网无公网入口；stale_check 已证 gh api 轮询模式可靠（三态分流）。webhook 需公网面 + 新凭据，列 follow-up。

### D2 事件即语料增量（编排零改动）
事件文件落 `data_root/<project>/inbox/events/<stamp>_<sha8>.md`（frontmatter：源仓/事件类型/作者/URL + 正文摘要——机械拼接零语义），fetch 既有 discovery 纳入当日新内容；radar 语义筛天然消费。**不建独立事件队列/状态机**——dsh 的 Trigger→Thread→Mandate 领域模型是长期参照，v1 以最小形态（文件）接入，零新调度语义。

### D3 Fast Path 判据全部机械
作者 ∈ 项目授权面（成员三态既有数据）/类型白名单/同 sha 去重（inbox 已有 dedup 惯例可复用勘察定）；零 LLM 零模型调用。Slow Path 即 radar（不重复建设）。

### D4 前置勘察（tasks 1.0）
fetch 段 inbox/discovery 机制现状、去重惯例、gh api 事件端点限流面——实装前必须勘察对齐（本 change 起草时未勘察）。

## 依赖
LangGraph 迁移收尾后实施（路线 P1 注记：事件入口挂 graph_pa 自然形态；不阻塞起草）。

### D5 声明位置 = sources.yaml 顶层 `events:` 键（实装定案）
pa 侧 load_sources 只读 `data["sources"]`——顶层 events 键对 pa 零影响（实装核验 run_daily.py:215-259）；比独立 events.yaml 少一个文件。hub 侧自读自容（容错：无键/坏 yaml = 零声明零行为）。

### D6 作者授权面 = events.authors（GitHub login 集）——不做跨信道映射
MM 成员三态是 MM 身份域，GitHub 事件作者是 login——两域映射被显式拒绝（Non-Goals + dsh rejected-concepts 同款）；映射由配置人在 authors 声明处完成（人工一次、机械永久）。authors 空 = 拒绝全部（零误报取向 + 一次性 warn）。

### D7 容器消费接缝 = events 目录自动挂载（ro）
RunSpec 增 `events_root`（目录存在才挂）→ 容器 `/opt/pa/.project-auto/events`（ro——hub 写、容器读）；项目 sources.yaml 配 directory 源（root: .project-auto/events/）即被 fetch date-marker 机械发现。去重 = 文件名含 event id（幂等、重启安全、零 db 面）。
