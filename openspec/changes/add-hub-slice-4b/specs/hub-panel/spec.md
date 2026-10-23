# hub-panel（add-hub-slice-4b 新 capability）

## ADDED Requirements

### Requirement: 面板提交入口（会话强制 + evidence 机械必填）
hub SHALL 提供浏览器面板的 PRD 提交页：PRD 内容（文本粘贴）+ project 选择 + evidence 字段，提交即落盘入队走既有队列。面板 SHALL 强制有效会话（复用 2b OAuth 会话；auth 未配置态面板整体 503 对齐 auth 群）。**evidence 机械必填（F12）**：入口 SHALL 拒绝空 evidence 的提交（422 + 明确提示）——把空依据挡在 critic 闸外，省一轮 critic；hub 不判定 evidence 内容质量（机械非空检查 only）。发起人身份 SHALL 取会话用户（enqueued_by = mm_user_id 形态，通知走直发路径）；无会话提交不可达（面板强制会话，无 env 兜底形态）。project 清单 SHALL 机械扫描 data_root 下具备启动必读面（profiles 目录 + sources.yaml）的子目录生成——hub 不维护独立 project 注册表。

#### Scenario: 会话提交闭环
- **WHEN** 已登录同事在提交页填 PRD 内容、选 project、填 evidence 链接并提交
- **THEN** PRD 落盘 `data_root/<project>/prd/`、队列项入队（enqueued_by = 该会话 mm_user_id），页面显示队列项标识

#### Scenario: evidence 空被拒
- **WHEN** 提交时 evidence 为空或纯空白
- **THEN** 422 拒绝入队，提示「evidence 必填（链接或出处说明）」；不产生队列项、不落盘

#### Scenario: 未登录不可提交
- **WHEN** 无有效会话 cookie 访问面板提交端点
- **THEN** 401/302 到登录；auth 未配置态面板端点 503（对齐 auth 群行为）

#### Scenario: project 清单来自必读面扫描
- **WHEN** 渲染提交页 project 选择器
- **THEN** 候选 = data_root 下有 profiles 目录与 sources.yaml 的子目录（与派发 fail fast 同判据）；空清单时提示部署侧先建 project 必读面

### Requirement: 面板只读面（队列 / run / 报告，零语义渲染）
hub SHALL 提供面板只读页：队列状态列表、run 列表（含终态与 exit code）、run 详情（终态事实 + journal 尾部行机械展示）。run 报告 SHALL 在报告落卷存在时只读渲染（T2a 裁决：pa 侧 report 双写 state 卷，见 spec: pa-event-outlet；roc 侧报告可见性 = 面板渲染 + bot 摘要）。面板渲染 MUST 限于机械展示 hub DB 自有行与 state 卷落盘文件——不解析 pa journal 内部结构做语义判定（journal 尾部行原样展示除外——展示非判定）、不聚合统计语义结论。只读面同样强制会话（与提交页同守卫口径）。

#### Scenario: run 列表与详情
- **WHEN** 已登录同事打开 run 列表 / 某 run 详情
- **THEN** 见队列项状态、run 终态、exit code、last_error、journal 尾部若干行（原样文本）

#### Scenario: 报告渲染
- **WHEN** 某 run 的报告文件已落卷（report 双写生效）
- **THEN** run 详情页渲染报告内容（markdown 原文展示）；报告缺失时显示「无报告」（非错误）

#### Scenario: 只读面不产生语义判定
- **WHEN** 任何只读页渲染
- **THEN** 页面内容为 DB 行与卷文件的事实展示，无 hub 侧生成的质量/价值/结论性判断

### Requirement: 面板形态（服务端渲染，零前端构建链）
面板 SHALL 服务端渲染纯 HTML（单 HTMLResponse 渲染函数族），MUST NOT 引入前端构建链、JS 框架或额外模板引擎依赖（hub 最小依赖面不动；样式内联极简）。面板端点群挂在既有 FastAPI app（与 REST 面同进程同端口）。CLI 与 REST 端点行为 MUST NOT 因面板引入而变化。

#### Scenario: 零新依赖
- **WHEN** 面板落地后检查 hub 依赖面
- **THEN** 无新增 Python 包依赖（渲染用标准库 + 既有 FastAPI 栈）
